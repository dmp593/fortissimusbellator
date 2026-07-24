import decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from breeding.models import Animal
from chat.catalog import available_animals
from reservations.availability import (
    annotate_dog_availability,
    dog_inventory_unavailability_reason,
)
from reservations.exceptions import PaymentError, ReservationUnavailable
from reservations.forms import AdminSaleProcessForm, AdminWorkflowTransferForm
from reservations.models import (
    AnimalSale,
    AnimalSaleCase,
    AnimalWorkflowTransfer,
    Charge,
    CustomerCredit,
    ERPDocument,
    Payment,
    PreReservation,
    Reservation,
    WorkflowClosure,
)
from reservations.services.admin_workflows import (
    cancel_animal_sale,
    complete_existing_sale_case,
    create_admin_pre_reservation,
    create_admin_reservation,
    create_admin_sale,
    record_staff_terms_acceptance,
    synchronize_paid_charge,
)
from reservations.services.closures import record_workflow_closure
from reservations.services.ledger import (
    add_charge_adjustment,
    record_manual_payment,
)
from reservations.services.payment import (
    cancel_staff_reservation,
    fulfill_checkout_session,
    initialize_checkout,
    reconcile_sale_case_checkouts_for_admin,
)
from reservations.services.reservation import (
    accept_pre_reservation,
    reopen_failed_reservation,
    start_reservation_payment,
)
from reservations.services.transfers import transfer_animal_workflow
from reservations.tests.base import ReservationTestMixin, TEST_STORAGES


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BUSINESS_NOTIFICATION_RECIPIENTS=['staff@example.com'],
    STATIC_ROOT=None,
    STORAGES=TEST_STORAGES,
    TOCONLINE_ENABLED=False,
)
class AdminSaleWorkflowTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()
        self.superuser = get_user_model().objects.create_superuser(
            username='admin-sale-workflows',
            email='admin-sale-workflows@example.com',
            password='test-password',
        )

    def customer_data(self):
        return {
            'customer_name': 'Customer Example',
            'customer_email': self.user.email,
            'customer_phone': '+351912345678',
            'customer_tax_number': '999999990',
            'billing_address': 'Example Street 1',
            'billing_postcode': '1000-001',
            'billing_city': 'Lisbon',
            'billing_country': 'PT',
            'language_code': 'en',
        }

    def create_direct_reservation(
        self,
        *,
        animal=None,
        amount=decimal.Decimal('500.00'),
        provider=Payment.Provider.CASH,
    ):
        return create_admin_reservation(
            animal_id=(animal or self.dog).pk,
            user=self.user,
            customer_data=self.customer_data(),
            deposit_amount=amount,
            payment_provider=provider,
            created_by=self.superuser,
            terms_accepted_in_person=provider != Payment.Provider.STRIPE,
            payment_reference='admin-reference',
            payment_note='Received by staff.',
        )

    def assert_new_processes_are_rejected(
        self,
        *,
        expected_message,
    ):
        customer_data = self.customer_data()
        customer_data.update(
            customer_name='Other Customer',
            customer_email=self.other_user.email,
        )
        initial_counts = {
            AnimalSaleCase: AnimalSaleCase.objects.count(),
            PreReservation: PreReservation.objects.count(),
            Reservation: Reservation.objects.count(),
            AnimalSale: AnimalSale.objects.count(),
            AnimalWorkflowTransfer: AnimalWorkflowTransfer.objects.count(),
        }
        attempts = (
            (
                'pre-reservation',
                lambda: create_admin_pre_reservation(
                    animal_id=self.dog.pk,
                    user=self.other_user,
                    customer_data=customer_data,
                    fee_amount=decimal.Decimal('50.00'),
                    payment_provider=Payment.Provider.STRIPE,
                    created_by=self.superuser,
                ),
            ),
            (
                'reservation',
                lambda: create_admin_reservation(
                    animal_id=self.dog.pk,
                    user=self.other_user,
                    customer_data=customer_data,
                    deposit_amount=decimal.Decimal('500.00'),
                    payment_provider=Payment.Provider.CASH,
                    created_by=self.superuser,
                    terms_accepted_in_person=True,
                    payment_reference='conflicting-reservation',
                    payment_note='Must never be recorded.',
                ),
            ),
            (
                'final sale',
                lambda: create_admin_sale(
                    animal_id=self.dog.pk,
                    user=self.other_user,
                    customer_data=customer_data,
                    final_price=decimal.Decimal('1500.00'),
                    payment_provider=Payment.Provider.CASH,
                    sold_at=timezone.localdate(),
                    created_by=self.superuser,
                    payment_reference='conflicting-sale',
                    payment_note='Must never be recorded.',
                ),
            ),
        )

        for stage, attempt in attempts:
            with self.subTest(stage=stage):
                with self.assertRaisesMessage(
                    ReservationUnavailable,
                    expected_message,
                ):
                    attempt()
                for model, expected_count in initial_counts.items():
                    self.assertEqual(model.objects.count(), expected_count)

    def test_direct_manual_reservation_has_no_fake_pre_reservation(self):
        reservation = self.create_direct_reservation()

        self.assertIsNone(reservation.pre_reservation_id)
        self.assertEqual(reservation.status, Reservation.Status.CONFIRMED)
        self.assertEqual(
            reservation.sale_case.status,
            AnimalSaleCase.Status.RESERVATION,
        )
        self.assertFalse(
            PreReservation.objects.filter(
                sale_case=reservation.sale_case,
            ).exists()
        )
        charge = Charge.objects.get(pk=reservation.charge_id)
        self.assertEqual(charge.status, Charge.Status.PAID)
        payment = charge.payments.get()
        self.assertEqual(payment.provider, Payment.Provider.CASH)
        self.assertEqual(payment.status, Payment.Status.PAID)

        dog = annotate_dog_availability(
            Animal.objects.filter(pk=self.dog.pk),
        ).get()
        self.assertFalse(dog.has_blocking_pre_reservation)
        self.assertTrue(dog.has_confirmed_reservation)
        self.assertNotIn(self.dog, available_animals())
        self.dog.refresh_from_db()
        self.assertTrue(self.dog.for_sale)

        self.client.force_login(self.user)
        dashboard = self.client.get(reverse('reservations:dashboard'))
        self.assertContains(dashboard, self.dog.name)
        self.assertContains(dashboard, 'Reserved')

    def test_paid_staff_pre_reservation_is_accepted_automatically(self):
        with self.captureOnCommitCallbacks(execute=True):
            pre_reservation = create_admin_pre_reservation(
                animal_id=self.dog.pk,
                user=self.user,
                customer_data=self.customer_data(),
                fee_amount=decimal.Decimal('50.00'),
                payment_provider=Payment.Provider.CASH,
                created_by=self.superuser,
                terms_accepted_in_person=True,
                payment_reference='cash-receipt-1',
                payment_note='Paid at the kennel.',
            )

        pre_reservation.refresh_from_db()
        reservation = pre_reservation.reservation
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.ACCEPTED,
        )
        self.assertEqual(pre_reservation.reviewed_by, self.superuser)
        self.assertIn(
            'Automatically accepted',
            pre_reservation.review_reason,
        )
        self.assertEqual(reservation.status, Reservation.Status.OFFERED)
        self.assertEqual(
            pre_reservation.sale_case.status,
            AnimalSaleCase.Status.RESERVATION,
        )
        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(
            all(
                'Pre-reservation accepted' in message.subject
                for message in mail.outbox
            )
        )

    def test_staff_stripe_pre_reservation_invites_customer_to_same_process(self):
        with self.captureOnCommitCallbacks(execute=True):
            pre_reservation = create_admin_pre_reservation(
                animal_id=self.dog.pk,
                user=self.user,
                customer_data=self.customer_data(),
                fee_amount=decimal.Decimal('65.00'),
                payment_provider=Payment.Provider.STRIPE,
                created_by=self.superuser,
            )

        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.PENDING_PAYMENT,
        )
        self.assertEqual(
            pre_reservation.payment.status,
            Payment.Status.INITIALIZING,
        )
        self.assertEqual(len(mail.outbox), 2)
        customer_html = mail.outbox[0].alternatives[0][0]
        self.assertIn(
            f'retry={pre_reservation.public_id}',
            customer_html,
        )
        self.assertIn('/en/buy-a-dog/', customer_html)

    def test_paid_staff_stripe_pre_reservation_auto_accepts_after_callback(self):
        pre_reservation = create_admin_pre_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            fee_amount=decimal.Decimal('50.00'),
            payment_provider=Payment.Provider.STRIPE,
            created_by=self.superuser,
        )
        checkout_data = self.checkout_data()
        checkout_data['terms'] = self.terms
        pre_reservation = reopen_failed_reservation(
            reservation_id=pre_reservation.pk,
            user=self.user,
            target_type=PreReservation.TargetType.DOG,
            target_id=self.dog.pk,
            checkout_data=checkout_data,
            language_code='en',
        )
        expires_at = int(
            (timezone.now() + timedelta(minutes=30)).timestamp()
        )
        checkout_session = {
            'id': f'cs_staff_{pre_reservation.payment.pk}',
            'url': 'https://checkout.stripe.test/staff',
            'expires_at': expires_at,
        }
        with patch(
            'reservations.stripe_gateway.create_checkout_session',
            return_value=checkout_session,
        ):
            initialize_checkout(
                purchase=pre_reservation,
                success_url='https://example.test/success',
                cancel_url='https://example.test/cancel',
            )
        pre_reservation.refresh_from_db()
        payment = pre_reservation.payment
        paid_session = {
            'id': payment.stripe_checkout_session_id,
            'payment_status': 'paid',
            'client_reference_id': str(pre_reservation.public_id),
            'currency': payment.currency.lower(),
            'amount_total': int(payment.amount * 100),
            'payment_intent': {'id': f'pi_staff_{payment.pk}'},
            'metadata': {
                'local_payment_id': str(payment.pk),
                'purchase_public_id': str(pre_reservation.public_id),
                'checkout_attempt_number': str(
                    payment.checkout_attempt_number,
                ),
            },
        }
        with self.captureOnCommitCallbacks(execute=True), patch(
            'reservations.stripe_gateway.retrieve_checkout_session',
            return_value=paid_session,
        ), patch(
            'reservations.stripe_gateway.retrieve_payment_financials',
            return_value={
                'charge_id': f'ch_staff_{payment.pk}',
                'fee_amount': decimal.Decimal('1.50'),
                'net_amount': decimal.Decimal('48.50'),
            },
        ):
            fulfill_checkout_session(paid_session['id'])

        pre_reservation.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.ACCEPTED,
        )
        self.assertEqual(
            pre_reservation.reservation.status,
            Reservation.Status.OFFERED,
        )

    def test_complimentary_direct_reservation_is_confirmed_and_audited(self):
        reservation = create_admin_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            deposit_amount=decimal.Decimal('0.00'),
            payment_provider=Payment.Provider.COMPLIMENTARY,
            created_by=self.superuser,
            terms_accepted_in_person=True,
            payment_note='Commercial courtesy approved by the breeder.',
        )

        self.assertEqual(reservation.status, Reservation.Status.CONFIRMED)
        payment = reservation.charge.payments.get()
        self.assertEqual(payment.provider, Payment.Provider.COMPLIMENTARY)
        self.assertEqual(payment.amount, decimal.Decimal('0.00'))
        self.assertEqual(payment.recorded_by, self.superuser)
        self.assertIn('courtesy', payment.note)

    def test_complimentary_staff_pre_reservation_is_autoaccepted_and_audited(self):
        pre_reservation = create_admin_pre_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            fee_amount=decimal.Decimal('0.00'),
            payment_provider=Payment.Provider.COMPLIMENTARY,
            created_by=self.superuser,
            terms_accepted_in_person=True,
            payment_note='Pre-reservation fee waived by the breeder.',
        )

        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.ACCEPTED,
        )
        payment = pre_reservation.charge.payments.get()
        self.assertEqual(payment.provider, Payment.Provider.COMPLIMENTARY)
        self.assertEqual(payment.amount, decimal.Decimal('0.00'))
        self.assertEqual(
            pre_reservation.reservation.status,
            Reservation.Status.OFFERED,
        )

    def test_staff_direct_reservation_invites_customer_to_pay_online(self):
        with self.captureOnCommitCallbacks(execute=True):
            reservation = self.create_direct_reservation(
                provider=Payment.Provider.STRIPE,
            )

        self.assertEqual(reservation.status, Reservation.Status.OFFERED)
        self.assertEqual(len(mail.outbox), 2)
        customer_html = mail.outbox[0].alternatives[0][0]
        self.assertIn(
            reverse(
                'reservations:reservation_checkout',
                args=[reservation.public_id],
            ),
            customer_html,
        )
        self.assertIn('Complete your reservation', mail.outbox[0].subject)

    def test_zero_balance_adjustment_auto_accepts_staff_pre_reservation(self):
        pre_reservation = create_admin_pre_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            fee_amount=decimal.Decimal('50.00'),
            payment_provider=Payment.Provider.STRIPE,
            created_by=self.superuser,
        )
        reconcile_sale_case_checkouts_for_admin(
            pre_reservation.sale_case_id,
        )
        with transaction.atomic():
            record_staff_terms_acceptance(pre_reservation.charge_id)
            add_charge_adjustment(
                charge_id=pre_reservation.charge_id,
                amount=decimal.Decimal('-50.00'),
                kind='waiver',
                reason='Documented full courtesy waiver.',
                created_by=self.superuser,
            )
            synchronize_paid_charge(
                pre_reservation.charge_id,
                admin_user=self.superuser,
            )

        pre_reservation.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.ACCEPTED,
        )
        self.assertEqual(
            pre_reservation.charge.total_amount,
            decimal.Decimal('0.00'),
        )

    def test_admin_can_replace_unstarted_stripe_with_manual_payment(self):
        pre_reservation = create_admin_pre_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            fee_amount=decimal.Decimal('50.00'),
            payment_provider=Payment.Provider.STRIPE,
            created_by=self.superuser,
        )
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse(
                'admin:reservations_charge_record_payment',
                args=[pre_reservation.charge_id],
            ),
            {
                'amount': '50.00',
                'provider': Payment.Provider.BANK_TRANSFER,
                'external_reference': 'TRX-ADMIN-1',
                'note': 'Received after the process was created.',
                'terms_accepted_in_person': 'on',
                'confirm': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        pre_reservation.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.ACCEPTED,
        )
        self.assertEqual(
            pre_reservation.terms_acceptance_source,
            PreReservation.TermsAcceptanceSource.STAFF_RECORDED,
        )
        self.assertTrue(
            pre_reservation.charge.payments.filter(
                provider=Payment.Provider.BANK_TRANSFER,
                status=Payment.Status.PAID,
            ).exists()
        )

    def test_admin_add_page_exposes_every_supported_starting_stage(self):
        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('admin:reservations_animalsalecase_add'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pre-reservation')
        self.assertContains(response, 'Direct reservation')
        self.assertContains(response, 'Direct final sale')

    def test_animal_admin_prefills_direct_sale_without_legacy_fields(self):
        self.client.force_login(self.superuser)
        animal_page = self.client.get(
            reverse('admin:breeding_animal_change', args=[self.dog.pk]),
        )

        self.assertEqual(animal_page.status_code, 200)
        animal_form = animal_page.context['adminform'].form
        self.assertNotIn('sold_at', animal_form.fields)
        self.assertNotIn('sold_to', animal_form.fields)
        self.assertContains(animal_page, 'Register direct final sale')

        sale_page = self.client.get(
            reverse('admin:reservations_animalsalecase_add'),
            {
                'animal': self.dog.pk,
                'start_stage': 'sale',
            },
        )
        sale_form = sale_page.context['form']
        self.assertEqual(sale_form['animal'].value(), self.dog.pk)
        self.assertEqual(sale_form['start_stage'].value(), 'sale')
        self.assertEqual(
            sale_form['payment_provider'].value(),
            Payment.Provider.CASH,
        )
        self.assertEqual(
            sale_form['amount'].value(),
            decimal.Decimal('1500.00'),
        )
        self.assertContains(sale_page, 'No payment required (complimentary)')
        self.assertContains(sale_page, 'admin/css/forms.css')
        self.assertContains(
            sale_page,
            'class="form-row field-start_stage"',
        )
        self.assertContains(sale_page, 'class="module aligned ', count=4)

    def test_custom_admin_actions_use_the_native_admin_form_layout(self):
        reservation = self.create_direct_reservation()
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse(
                'admin:reservations_reservation_cancel',
                args=[reservation.pk],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'admin/css/forms.css')
        self.assertContains(response, 'class="module aligned"')
        self.assertContains(response, 'class="form-row field-reason"')
        self.assertContains(response, 'class="submit-row"')
        self.assertNotContains(response, 'Stripe · Stripe')

    def test_unpaid_direct_reservation_is_pre_reserved_everywhere(self):
        reservation = self.create_direct_reservation(
            provider=Payment.Provider.STRIPE,
        )

        dog = annotate_dog_availability(
            Animal.objects.filter(pk=self.dog.pk),
        ).get()
        self.assertEqual(reservation.status, Reservation.Status.OFFERED)
        self.assertTrue(dog.has_blocking_pre_reservation)
        self.assertFalse(dog.has_confirmed_reservation)
        self.assertNotIn(self.dog, available_animals())
        self.assertEqual(
            str(dog_inventory_unavailability_reason(self.dog)),
            'This dog is already pre-reserved.',
        )

    def test_pending_pre_reservation_blocks_every_new_admin_starting_stage(self):
        original = create_admin_pre_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            fee_amount=decimal.Decimal('50.00'),
            payment_provider=Payment.Provider.STRIPE,
            created_by=self.superuser,
        )

        self.assert_new_processes_are_rejected(
            expected_message='This dog is already pre-reserved.',
        )

        original.refresh_from_db()
        original.sale_case.refresh_from_db()
        self.assertEqual(
            original.status,
            PreReservation.Status.PENDING_PAYMENT,
        )
        self.assertEqual(original.sale_case.user, self.user)
        self.assertEqual(
            original.sale_case.status,
            AnimalSaleCase.Status.PRE_RESERVATION,
        )

    def test_offered_reservation_blocks_every_new_admin_starting_stage(self):
        original = self.create_direct_reservation(
            provider=Payment.Provider.STRIPE,
        )

        self.assert_new_processes_are_rejected(
            expected_message='This dog is already pre-reserved.',
        )

        original.refresh_from_db()
        original.sale_case.refresh_from_db()
        self.assertEqual(original.status, Reservation.Status.OFFERED)
        self.assertEqual(original.sale_case.user, self.user)
        self.assertEqual(
            original.sale_case.status,
            AnimalSaleCase.Status.RESERVATION,
        )

    def test_confirmed_reservation_blocks_every_new_admin_starting_stage(self):
        original = self.create_direct_reservation()

        self.assert_new_processes_are_rejected(
            expected_message='This dog is already reserved.',
        )

        original.refresh_from_db()
        original.sale_case.refresh_from_db()
        self.assertEqual(original.status, Reservation.Status.CONFIRMED)
        self.assertEqual(original.sale_case.user, self.user)
        self.assertEqual(
            original.sale_case.status,
            AnimalSaleCase.Status.RESERVATION,
        )

    def test_completed_sale_blocks_every_new_admin_starting_stage(self):
        original = create_admin_sale(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            final_price=decimal.Decimal('1500.00'),
            payment_provider=Payment.Provider.CASH,
            sold_at=timezone.localdate(),
            created_by=self.superuser,
            payment_reference='original-sale',
            payment_note='Paid at the kennel.',
        )

        self.assert_new_processes_are_rejected(
            expected_message='This dog is no longer available.',
        )

        original.refresh_from_db()
        original.sale_case.refresh_from_db()
        self.assertIsNone(original.voided_at)
        self.assertEqual(original.sale_case.user, self.user)
        self.assertEqual(
            original.sale_case.status,
            AnimalSaleCase.Status.SOLD,
        )

    def test_admin_selectors_and_shortcut_hide_a_dog_with_an_active_process(self):
        original = create_admin_pre_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            fee_amount=decimal.Decimal('50.00'),
            payment_provider=Payment.Provider.STRIPE,
            created_by=self.superuser,
        )
        sale_form = AdminSaleProcessForm()
        self.assertFalse(
            sale_form.fields['animal'].queryset.filter(pk=self.dog.pk).exists()
        )

        source_dog = Animal.objects.create(
            breed=self.breed,
            name='Transfer Source',
            birth_date=self.dog.birth_date,
            gender='M',
            active=True,
            for_sale=True,
            price_in_euros='1500.00',
        )
        source_reservation = self.create_direct_reservation(
            animal=source_dog,
            provider=Payment.Provider.STRIPE,
        )
        transfer_form = AdminWorkflowTransferForm(
            source_case=source_reservation.sale_case,
        )
        self.assertFalse(
            transfer_form.fields['target_animal']
            .queryset.filter(pk=self.dog.pk)
            .exists()
        )

        self.client.force_login(self.superuser)
        animal_page = self.client.get(
            reverse('admin:breeding_animal_change', args=[self.dog.pk]),
        )
        self.assertNotContains(animal_page, 'Register direct final sale')
        self.assertContains(animal_page, 'Open sale process')

        add_page = self.client.get(
            reverse('admin:reservations_animalsalecase_add'),
            {
                'animal': self.dog.pk,
                'start_stage': AdminSaleProcessForm.Stage.SALE,
            },
        )
        self.assertIsNone(add_page.context['form']['animal'].value())
        original.refresh_from_db()
        self.assertEqual(
            original.status,
            PreReservation.Status.PENDING_PAYMENT,
        )

    def test_unstarted_admin_checkout_can_be_closed_without_stripe(self):
        pre_reservation = create_admin_pre_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            fee_amount=decimal.Decimal('50.00'),
            payment_provider=Payment.Provider.STRIPE,
            created_by=self.superuser,
        )

        reconcile_sale_case_checkouts_for_admin(
            pre_reservation.sale_case_id,
        )

        pre_reservation.refresh_from_db()
        pre_reservation.payment.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.PAYMENT_FAILED,
        )
        self.assertEqual(
            pre_reservation.payment.status,
            Payment.Status.FAILED,
        )
        self.assertEqual(
            pre_reservation.sale_case.status,
            AnimalSaleCase.Status.PRE_RESERVATION,
        )

    def test_manual_payment_after_failed_stripe_attempt_can_be_accepted(self):
        pre_reservation = create_admin_pre_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            fee_amount=decimal.Decimal('75.00'),
            payment_provider=Payment.Provider.STRIPE,
            created_by=self.superuser,
            terms_accepted_in_person=True,
        )
        reconcile_sale_case_checkouts_for_admin(pre_reservation.sale_case_id)
        manual_payment = record_manual_payment(
            charge_id=pre_reservation.charge_id,
            amount=decimal.Decimal('75.00'),
            provider=Payment.Provider.BANK_TRANSFER,
            recorded_by=self.superuser,
            external_reference='manual-after-stripe',
            purchase=pre_reservation,
        )
        synchronize_paid_charge(pre_reservation.charge_id)

        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.superuser,
        )

        pre_reservation.refresh_from_db()
        pre_reservation.payment.refresh_from_db()
        self.assertEqual(
            pre_reservation.payment.status,
            Payment.Status.FAILED,
        )
        self.assertEqual(manual_payment.status, Payment.Status.PAID)
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.ACCEPTED,
        )
        self.assertEqual(
            reservation.pre_reservation_credit_amount,
            decimal.Decimal('75.00'),
        )

    def test_admin_pre_reservation_custom_amount_and_adjustment_reach_stripe(self):
        pre_reservation = create_admin_pre_reservation(
            animal_id=self.dog.pk,
            user=self.user,
            customer_data=self.customer_data(),
            fee_amount=decimal.Decimal('75.00'),
            payment_provider=Payment.Provider.STRIPE,
            created_by=self.superuser,
        )
        reconcile_sale_case_checkouts_for_admin(pre_reservation.sale_case_id)
        add_charge_adjustment(
            charge_id=pre_reservation.charge_id,
            amount=decimal.Decimal('25.00'),
            kind='surcharge',
            reason='Documented administrative adjustment.',
            created_by=self.superuser,
        )
        checkout_data = self.checkout_data()
        checkout_data['terms'] = self.terms

        reopened = reopen_failed_reservation(
            reservation_id=pre_reservation.pk,
            user=self.user,
            target_type=PreReservation.TargetType.DOG,
            target_id=self.dog.pk,
            checkout_data=checkout_data,
            language_code='en',
        )

        reopened.payment.refresh_from_db()
        reopened.charge.refresh_from_db()
        self.assertEqual(
            reopened.charge.subtotal_amount,
            decimal.Decimal('75.00'),
        )
        self.assertEqual(
            reopened.charge.total_amount,
            decimal.Decimal('100.00'),
        )
        self.assertEqual(reopened.payment.amount, decimal.Decimal('100.00'))

    def test_admin_direct_reservation_adjustment_reaches_stripe(self):
        reservation = self.create_direct_reservation(
            provider=Payment.Provider.STRIPE,
        )
        add_charge_adjustment(
            charge_id=reservation.charge_id,
            amount=decimal.Decimal('25.00'),
            kind='surcharge',
            reason='Documented administrative adjustment.',
            created_by=self.superuser,
        )

        reservation = start_reservation_payment(
            reservation_id=reservation.pk,
            user=self.user,
            accepted_terms=self.reservation_terms,
        )

        reservation.payment.refresh_from_db()
        reservation.charge.refresh_from_db()
        self.assertEqual(
            reservation.charge.total_amount,
            decimal.Decimal('525.00'),
        )
        self.assertEqual(
            reservation.payment.amount,
            decimal.Decimal('525.00'),
        )

    def test_admin_cancellation_splits_refund_credit_and_retained_value(self):
        reservation = self.create_direct_reservation()
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                cancel_staff_reservation(
                    reservation=reservation,
                    admin_user=self.superuser,
                    reason='Customer placement cannot proceed.',
                )
                closure, refunds = record_workflow_closure(
                    sale_case=reservation.sale_case,
                    stage=Charge.Stage.RESERVATION,
                    kind=WorkflowClosure.Kind.CANCELLED,
                    reason='Customer placement cannot proceed.',
                    refund_amount=decimal.Decimal('200.00'),
                    credit_amount=decimal.Decimal('250.00'),
                    created_by=self.superuser,
                )

        reservation.refresh_from_db()
        reservation.sale_case.refresh_from_db()
        payment = reservation.charge.payments.get()
        payment.refresh_from_db()
        self.assertEqual(
            reservation.status,
            Reservation.Status.CANCELLED_BY_ADMIN,
        )
        self.assertEqual(
            reservation.sale_case.status,
            AnimalSaleCase.Status.CLOSED,
        )
        self.assertEqual(closure.refund_amount, decimal.Decimal('200.00'))
        self.assertEqual(closure.credit_amount, decimal.Decimal('250.00'))
        self.assertEqual(closure.retained_amount, decimal.Decimal('50.00'))
        self.assertEqual(len(refunds), 1)
        self.assertEqual(payment.status, Payment.Status.PARTIALLY_REFUNDED)
        self.assertEqual(
            CustomerCredit.objects.get(
                source_closure=closure,
            ).amount,
            decimal.Decimal('250.00'),
        )
        self.assertEqual(
            refunds[0].erp_document.status,
            ERPDocument.Status.DEFERRED,
        )
        self.assertIsNone(dog_inventory_unavailability_reason(self.dog))

        self.client.force_login(self.user)
        dashboard = self.client.get(reverse('reservations:dashboard'))
        self.assertContains(dashboard, 'Customer credit')
        self.assertContains(dashboard, '250.00 EUR')

        with self.assertRaises(ReservationUnavailable):
            cancel_staff_reservation(
                reservation=reservation,
                admin_user=self.superuser,
                reason='Duplicate cancellation.',
            )

    def test_transfer_moves_value_and_settles_target_difference(self):
        source_reservation = self.create_direct_reservation()
        target = Animal.objects.create(
            breed=self.breed,
            name='Bruna',
            birth_date=self.dog.birth_date,
            gender='F',
            active=True,
            for_sale=True,
            price_in_euros='2000.00',
        )

        with self.captureOnCommitCallbacks(execute=True):
            transfer, target_reservation, refunds = transfer_animal_workflow(
                source_case_id=source_reservation.sale_case_id,
                target_animal_id=target.pk,
                transferred_amount=decimal.Decimal('500.00'),
                refund_amount=decimal.Decimal('0.00'),
                retained_amount=decimal.Decimal('0.00'),
                reason='Customer chose another dog.',
                created_by=self.superuser,
                difference_payment_provider=Payment.Provider.BANK_TRANSFER,
                payment_reference='difference-transfer',
            )

        source_reservation.refresh_from_db()
        source_reservation.sale_case.refresh_from_db()
        target_reservation.refresh_from_db()
        target_charge = target_reservation.charge
        self.assertEqual(refunds, [])
        self.assertEqual(
            source_reservation.status,
            Reservation.Status.TRANSFERRED,
        )
        self.assertEqual(
            source_reservation.sale_case.status,
            AnimalSaleCase.Status.TRANSFERRED,
        )
        self.assertIsNone(target_reservation.pre_reservation_id)
        self.assertEqual(
            target_reservation.status,
            Reservation.Status.CONFIRMED,
        )
        self.assertEqual(
            target_reservation.terms_id,
            source_reservation.terms_id,
        )
        self.assertEqual(
            target_reservation.terms_accepted_at,
            source_reservation.terms_accepted_at,
        )
        self.assertEqual(target_charge.total_amount, decimal.Decimal('1000.00'))
        self.assertEqual(target_charge.credit_amount, decimal.Decimal('500.00'))
        self.assertEqual(target_charge.paid_amount, decimal.Decimal('500.00'))
        self.assertEqual(target_charge.amount_due, decimal.Decimal('0.00'))
        self.assertEqual(transfer.target_case_id, target_reservation.sale_case_id)
        credit = CustomerCredit.objects.get(source_transfer=transfer)
        self.assertEqual(credit.status, CustomerCredit.Status.EXHAUSTED)
        fiscal_document = target_charge.erp_documents.get(
            kind=ERPDocument.Kind.SALE,
        )
        self.assertEqual(
            fiscal_document.amount,
            decimal.Decimal('500.00'),
        )
        self.assertEqual(
            fiscal_document.status,
            ERPDocument.Status.DEFERRED,
        )
        self.assertNotIn(target, available_animals())
        self.assertIn(self.dog, available_animals())

    def test_transfer_refund_is_linked_to_the_transfer_audit_record(self):
        source_reservation = self.create_direct_reservation()
        target = Animal.objects.create(
            breed=self.breed,
            name='Cora',
            birth_date=self.dog.birth_date,
            gender='F',
            active=True,
            for_sale=True,
            price_in_euros='1800.00',
        )

        with self.captureOnCommitCallbacks(execute=True):
            transfer, target_reservation, refunds = transfer_animal_workflow(
                source_case_id=source_reservation.sale_case_id,
                target_animal_id=target.pk,
                target_charge_amount=decimal.Decimal('300.00'),
                transferred_amount=decimal.Decimal('300.00'),
                refund_amount=decimal.Decimal('100.00'),
                retained_amount=decimal.Decimal('100.00'),
                reason='Customer chose another dog with a different deposit.',
                created_by=self.superuser,
                difference_payment_provider=Payment.Provider.STRIPE,
            )

        self.assertEqual(len(refunds), 1)
        refunds[0].refresh_from_db()
        self.assertEqual(refunds[0].transfer_id, transfer.pk)
        self.assertFalse(target_reservation.charge.erp_documents.exists())

    def test_final_sale_preserves_asking_price_and_records_final_price(self):
        reservation = self.create_direct_reservation()
        sale = complete_existing_sale_case(
            sale_case_id=reservation.sale_case_id,
            final_price=decimal.Decimal('1450.00'),
            payment_provider=Payment.Provider.BANK_TRANSFER,
            sold_at=timezone.localdate(),
            completed_by=self.superuser,
            payment_reference='final-balance',
            notes='Final price agreed in person.',
        )

        self.dog.refresh_from_db()
        sale.sale_case.refresh_from_db()
        self.assertEqual(sale.final_price, decimal.Decimal('1450.00'))
        self.assertEqual(sale.charge.total_amount, decimal.Decimal('950.00'))
        self.assertEqual(sale.charge.amount_due, decimal.Decimal('0.00'))
        self.assertEqual(sale.sale_case.status, AnimalSaleCase.Status.SOLD)
        self.assertEqual(self.dog.price_in_euros, decimal.Decimal('1500.00'))
        self.assertTrue(self.dog.is_sold)
        self.assertEqual(sale.sale_case.user, self.user)
        self.assertTrue(self.dog.for_sale)
        self.assertNotIn(self.dog, available_animals())

        self.client.force_login(self.user)
        dashboard = self.client.get(reverse('reservations:dashboard'))
        self.assertContains(dashboard, 'Sold')
        self.assertContains(dashboard, '1450.00')
        self.assertContains(dashboard, self.dog.name)
        self.assertEqual(dashboard.context['active_reservations'], [])
        self.assertEqual(
            dashboard.context['reservation_history'],
            [sale.sale_case],
        )

    def test_animal_sold_to_another_customer_is_not_labelled_as_users_dog(self):
        old_reservation = self.create_direct_reservation()
        cancel_staff_reservation(
            reservation=old_reservation,
            admin_user=self.superuser,
            reason='Customer withdrew before the final sale.',
        )
        other_user = get_user_model().objects.create_user(
            username='other-sale-customer',
            email='other-sale-customer@example.com',
            password='test-password',
        )
        customer_data = self.customer_data()
        customer_data.update(
            customer_name='Other Customer',
            customer_email=other_user.email,
        )
        create_admin_sale(
            animal_id=self.dog.pk,
            user=other_user,
            customer_data=customer_data,
            final_price=decimal.Decimal('1500.00'),
            payment_provider=Payment.Provider.CASH,
            sold_at=timezone.localdate(),
            created_by=self.superuser,
            payment_reference='cash-final-sale',
            payment_note='Paid at the kennel.',
        )

        self.client.force_login(self.user)
        dashboard = self.client.get(reverse('reservations:dashboard'))

        self.assertContains(dashboard, self.dog.name)
        self.assertContains(dashboard, 'Closed')
        self.assertNotContains(dashboard, 'Sold')

    def test_cancelling_sale_without_refund_releases_the_dog(self):
        reservation = self.create_direct_reservation()
        sale = complete_existing_sale_case(
            sale_case_id=reservation.sale_case_id,
            final_price=decimal.Decimal('1450.00'),
            payment_provider=Payment.Provider.BANK_TRANSFER,
            sold_at=timezone.localdate(),
            completed_by=self.superuser,
            payment_reference='final-balance',
        )

        sale, closure, refunds = cancel_animal_sale(
            animal_sale_id=sale.pk,
            reason='Commercial sale cancelled by agreement.',
            refund_amount=decimal.Decimal('0.00'),
            credit_amount=decimal.Decimal('0.00'),
            cancelled_by=self.superuser,
        )

        self.dog.refresh_from_db()
        reservation.refresh_from_db()
        sale.sale_case.refresh_from_db()
        self.assertIsNotNone(sale.voided_at)
        self.assertEqual(
            sale.sale_case.status,
            AnimalSaleCase.Status.CLOSED,
        )
        self.assertEqual(
            reservation.status,
            Reservation.Status.CANCELLED_BY_ADMIN,
        )
        self.assertEqual(closure.paid_value_amount, decimal.Decimal('1450.00'))
        self.assertEqual(closure.retained_amount, decimal.Decimal('1450.00'))
        self.assertEqual(refunds, [])
        self.assertFalse(self.dog.is_sold)
        self.assertIn(self.dog, available_animals())

    def test_admin_can_cancel_completed_sale_from_dedicated_page(self):
        reservation = self.create_direct_reservation()
        sale = complete_existing_sale_case(
            sale_case_id=reservation.sale_case_id,
            final_price=decimal.Decimal('1450.00'),
            payment_provider=Payment.Provider.BANK_TRANSFER,
            sold_at=timezone.localdate(),
            completed_by=self.superuser,
            payment_reference='final-balance',
        )
        self.client.force_login(self.superuser)
        cancel_url = reverse(
            'admin:reservations_animalsale_cancel',
            args=[sale.pk],
        )

        response = self.client.get(cancel_url)
        self.assertContains(response, 'Cancel completed sale')
        self.assertNotContains(
            response,
            'pre-reservation is non-refundable',
        )

        response = self.client.post(
            cancel_url,
            {
                'reason': 'Sale cancelled by agreement.',
                'refund_calculation': 'none',
                'fixed_amount': '',
                'target_percentage': '',
                'credit_amount': '0.00',
                'confirm': 'on',
            },
        )

        self.assertRedirects(
            response,
            reverse(
                'admin:reservations_animalsale_change',
                args=[sale.pk],
            ),
        )
        sale.refresh_from_db()
        self.assertIsNotNone(sale.voided_at)

    def test_cancelling_sale_can_issue_partial_refund(self):
        reservation = self.create_direct_reservation()
        sale = complete_existing_sale_case(
            sale_case_id=reservation.sale_case_id,
            final_price=decimal.Decimal('1450.00'),
            payment_provider=Payment.Provider.BANK_TRANSFER,
            sold_at=timezone.localdate(),
            completed_by=self.superuser,
            payment_reference='final-balance',
        )

        sale, closure, refunds = cancel_animal_sale(
            animal_sale_id=sale.pk,
            reason='Partial refund agreed with the customer.',
            refund_amount=decimal.Decimal('300.00'),
            credit_amount=decimal.Decimal('0.00'),
            cancelled_by=self.superuser,
        )

        self.assertIsNotNone(sale.voided_at)
        self.assertEqual(closure.refund_amount, decimal.Decimal('300.00'))
        self.assertEqual(closure.retained_amount, decimal.Decimal('1150.00'))
        self.assertEqual(
            sum((refund.amount for refund in refunds), decimal.Decimal('0.00')),
            decimal.Decimal('300.00'),
        )

    def test_cancelling_sale_can_refund_every_payment(self):
        reservation = self.create_direct_reservation()
        sale = complete_existing_sale_case(
            sale_case_id=reservation.sale_case_id,
            final_price=decimal.Decimal('1450.00'),
            payment_provider=Payment.Provider.BANK_TRANSFER,
            sold_at=timezone.localdate(),
            completed_by=self.superuser,
            payment_reference='final-balance',
        )

        sale, closure, refunds = cancel_animal_sale(
            animal_sale_id=sale.pk,
            reason='Full refund agreed with the customer.',
            refund_amount=decimal.Decimal('1450.00'),
            credit_amount=decimal.Decimal('0.00'),
            cancelled_by=self.superuser,
        )

        self.assertIsNotNone(sale.voided_at)
        self.assertEqual(closure.refund_amount, decimal.Decimal('1450.00'))
        self.assertEqual(closure.retained_amount, decimal.Decimal('0.00'))
        self.assertEqual(
            sum((refund.amount for refund in refunds), decimal.Decimal('0.00')),
            decimal.Decimal('1450.00'),
        )

    def test_cancelling_sale_can_convert_value_to_customer_credit(self):
        reservation = self.create_direct_reservation()
        sale = complete_existing_sale_case(
            sale_case_id=reservation.sale_case_id,
            final_price=decimal.Decimal('1450.00'),
            payment_provider=Payment.Provider.BANK_TRANSFER,
            sold_at=timezone.localdate(),
            completed_by=self.superuser,
            payment_reference='final-balance',
        )

        sale, closure, refunds = cancel_animal_sale(
            animal_sale_id=sale.pk,
            reason='Value retained as customer credit.',
            refund_amount=decimal.Decimal('0.00'),
            credit_amount=decimal.Decimal('1450.00'),
            cancelled_by=self.superuser,
        )

        credit = CustomerCredit.objects.get(source_closure=closure)
        self.assertIsNotNone(sale.voided_at)
        self.assertEqual(refunds, [])
        self.assertEqual(closure.credit_amount, decimal.Decimal('1450.00'))
        self.assertEqual(closure.retained_amount, decimal.Decimal('0.00'))
        self.assertEqual(credit.amount, decimal.Decimal('1450.00'))
        self.assertEqual(credit.user, self.user)

    def test_settled_charge_is_changed_only_through_audited_adjustment(self):
        reservation = self.create_direct_reservation()

        with self.assertRaises(PaymentError):
            add_charge_adjustment(
                charge_id=reservation.charge_id,
                amount=decimal.Decimal('-10.00'),
                kind='manual_discount',
                reason='Would rewrite settled value.',
                created_by=self.superuser,
            )

        adjustment = add_charge_adjustment(
            charge_id=reservation.charge_id,
            amount=decimal.Decimal('25.00'),
            kind='surcharge',
            reason='Documented additional service.',
            created_by=self.superuser,
        )
        reservation.charge.refresh_from_db()
        self.assertEqual(adjustment.amount, decimal.Decimal('25.00'))
        self.assertEqual(
            reservation.charge.status,
            Charge.Status.PARTIALLY_PAID,
        )
