import decimal
import threading
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import (
    TestCase,
    TransactionTestCase,
    override_settings,
    skipUnlessDBFeature,
)
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from breeding.forms import AnimalForm
from breeding.models import Animal
from reservations.exceptions import ReservationUnavailable
from reservations.models import Payment, PreReservation, Reservation
from reservations.services.admin_workflows import complete_existing_sale_case
from reservations.services.reservation import (
    accept_pre_reservation,
    cancel_pre_reservation_by_user,
    create_pending_reservation,
    mark_pre_reservation_payment_setup_failed,
    reject_pre_reservation,
    reopen_failed_reservation,
    start_reservation_payment,
)
from reservations.tests.base import ReservationTestMixin, TEST_STORAGES


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BUSINESS_NOTIFICATION_RECIPIENTS=['staff@example.com'],
    STATIC_ROOT=None,
    STORAGES=TEST_STORAGES,
)
class ReservationRulesTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()
        self.admin = get_user_model().objects.create_user(
            username='breeder',
            email='breeder@example.com',
            is_staff=True,
        )

    def mark_pre_reservation_paid(self, pre_reservation):
        now = timezone.now()
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            status=Payment.Status.PAID,
            stripe_payment_intent_id=f'pi_{pre_reservation.pk}',
            paid_at=now,
        )
        PreReservation.objects.filter(pk=pre_reservation.pk).update(
            status=PreReservation.Status.AWAITING_REVIEW,
            confirmed_at=now,
        )
        pre_reservation.refresh_from_db()
        return pre_reservation

    def test_pending_pre_reservation_blocks_dog_without_changing_for_sale(self):
        pre_reservation = self.reserve(self.dog)

        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.PENDING_PAYMENT,
        )
        self.assertEqual(
            pre_reservation.payment.status,
            Payment.Status.INITIALIZING,
        )
        with self.assertRaisesMessage(
            ReservationUnavailable,
            'already pre-reserved',
        ):
            self.reserve(self.dog, user=self.other_user)

        self.dog.refresh_from_db()
        self.assertTrue(self.dog.for_sale)

    def test_offer_duration_cannot_change_during_an_active_workflow(self):
        self.reserve(self.dog)
        form = AnimalForm(
            instance=self.dog,
            data={
                'breed': self.breed.pk,
                'name': self.dog.name,
                'description': self.dog.description,
                'birth_date': self.dog.birth_date.isoformat(),
                'gender': self.dog.gender,
                'hair_type': self.dog.hair_type,
                'father': '',
                'mother': '',
                'litter': '',
                'price_in_euros': '1500',
                'discount_in_euros': '',
                'active': 'on',
                'has_training': '',
                'for_breeding': '',
                'for_sale': 'on',
                'pre_reservation_enabled': 'on',
                'pre_reservation_fee': '50.00',
                'reservation_deposit_percentage': '50.00',
                'reservation_offer_hours': '48',
                'order': '999',
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn('reservation_offer_hours', form.errors)

    def test_definite_payment_failure_releases_dog_and_preserves_history(self):
        first = self.reserve(self.dog)

        mark_pre_reservation_payment_setup_failed(
            first.pk,
            'Definite Stripe failure',
        )
        second = self.reserve(self.dog, user=self.other_user)

        first.refresh_from_db()
        self.dog.refresh_from_db()
        self.assertEqual(first.status, PreReservation.Status.PAYMENT_FAILED)
        self.assertEqual(first.payment.status, Payment.Status.FAILED)
        self.assertEqual(second.status, PreReservation.Status.PENDING_PAYMENT)
        self.assertTrue(self.dog.for_sale)
        self.assertEqual(PreReservation.objects.filter(animal=self.dog).count(), 2)

    def test_failed_retry_reuses_same_pre_reservation_and_payment(self):
        pre_reservation = self.reserve(self.dog)
        payment_id = pre_reservation.payment.pk
        mark_pre_reservation_payment_setup_failed(
            pre_reservation.pk,
            'Definite Stripe failure',
        )
        self.dog.pre_reservation_fee = decimal.Decimal('65.00')
        self.dog.save(update_fields=['pre_reservation_fee'])
        checkout_data = self.checkout_data()
        checkout_data['terms'] = self.terms

        retried = reopen_failed_reservation(
            reservation_id=pre_reservation.pk,
            user=self.user,
            target_type=PreReservation.TargetType.DOG,
            target_id=self.dog.pk,
            checkout_data=checkout_data,
            language_code='en',
        )

        self.assertEqual(PreReservation.objects.count(), 1)
        self.assertEqual(retried.pk, pre_reservation.pk)
        self.assertEqual(retried.payment.pk, payment_id)
        self.assertEqual(retried.payment.checkout_attempt_number, 2)
        self.assertEqual(retried.total_amount, decimal.Decimal('65.00'))
        self.assertEqual(retried.status, PreReservation.Status.PENDING_PAYMENT)

    def test_retry_cannot_reclaim_dog_held_by_another_customer(self):
        failed = self.reserve(self.dog)
        mark_pre_reservation_payment_setup_failed(
            failed.pk,
            'Definite Stripe failure',
        )
        self.reserve(self.dog, user=self.other_user)
        checkout_data = self.checkout_data()
        checkout_data['terms'] = self.terms

        with self.assertRaisesMessage(
            ReservationUnavailable,
            'already pre-reserved',
        ):
            reopen_failed_reservation(
                reservation_id=failed.pk,
                user=self.user,
                target_type=PreReservation.TargetType.DOG,
                target_id=self.dog.pk,
                checkout_data=checkout_data,
                language_code='en',
            )

    def test_litters_cannot_be_pre_reserved(self):
        with self.assertRaisesMessage(
            ReservationUnavailable,
            'Litters cannot be pre-reserved',
        ):
            self.reserve(self.litter)

    def test_dog_without_published_price_cannot_be_pre_reserved(self):
        self.dog.price_in_euros = None
        self.dog.save(update_fields=['price_in_euros'])

        with self.assertRaisesMessage(
            ReservationUnavailable,
            'without a published price',
        ):
            self.reserve(self.dog)

    def test_acceptance_creates_separate_deposit_offer_with_exact_formula(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )

        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.admin,
        )

        pre_reservation.refresh_from_db()
        self.assertEqual(pre_reservation.status, PreReservation.Status.ACCEPTED)
        self.assertEqual(reservation.status, Reservation.Status.OFFERED)
        self.assertEqual(
            reservation.deposit_target_amount,
            decimal.Decimal('750.00'),
        )
        self.assertEqual(
            reservation.pre_reservation_credit_amount,
            decimal.Decimal('50.00'),
        )
        self.assertEqual(
            reservation.payment_amount,
            decimal.Decimal('700.00'),
        )
        self.assertAlmostEqual(
            (reservation.offer_expires_at - timezone.now()).total_seconds(),
            72 * 60 * 60,
            delta=2,
        )

    def test_reservation_offer_uses_the_dog_specific_duration(self):
        self.dog.reservation_offer_hours = 24
        self.dog.save(update_fields=['reservation_offer_hours'])
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )

        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.admin,
        )

        self.assertAlmostEqual(
            (reservation.offer_expires_at - timezone.now()).total_seconds(),
            24 * 60 * 60,
            delta=2,
        )

    def test_reservation_offer_duration_is_between_one_hour_and_seven_days(self):
        self.assertEqual(self.dog.reservation_offer_hours, 72)

        self.dog.reservation_offer_hours = 0
        with self.assertRaises(ValidationError):
            self.dog.full_clean()

        self.dog.reservation_offer_hours = 169
        with self.assertRaises(ValidationError):
            self.dog.full_clean()

    def test_paid_pre_reservation_has_no_review_deadline(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )
        pre_reservation.hold_expires_at = timezone.now() - timedelta(days=30)
        pre_reservation.save(update_fields=['hold_expires_at'])
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            provider_fee_amount=decimal.Decimal('2.00'),
            provider_net_amount=decimal.Decimal('48.00'),
        )

        call_command(
            'process_reservation_workflows',
            limit=100,
            stdout=StringIO(),
        )

        pre_reservation.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.AWAITING_REVIEW,
        )
        self.assertFalse(
            Reservation.objects.filter(
                pre_reservation=pre_reservation,
            ).exists()
        )

    def test_expired_offer_closes_pre_reservation_and_releases_dog(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )
        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.admin,
        )
        reservation.offer_expires_at = timezone.now() - timedelta(seconds=1)
        reservation.save(update_fields=['offer_expires_at'])
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            provider_fee_amount=decimal.Decimal('2.00'),
            provider_net_amount=decimal.Decimal('48.00'),
        )

        call_command(
            'process_reservation_workflows',
            limit=100,
            stdout=StringIO(),
        )

        reservation.refresh_from_db()
        pre_reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.EXPIRED)
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.RESERVATION_OFFER_EXPIRED,
        )
        replacement = self.reserve(self.dog, user=self.other_user)
        self.assertEqual(
            replacement.status,
            PreReservation.Status.PENDING_PAYMENT,
        )

    def test_checkout_of_expired_offer_persists_expiry_and_releases_dog(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )
        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.admin,
        )
        reservation.offer_expires_at = timezone.now() - timedelta(seconds=1)
        reservation.save(update_fields=['offer_expires_at'])

        with self.assertRaisesMessage(
            ReservationUnavailable,
            'offer has expired',
        ):
            start_reservation_payment(
                reservation_id=reservation.pk,
                user=self.user,
                accepted_terms=self.reservation_terms,
            )

        reservation.refresh_from_db()
        pre_reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.EXPIRED)
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.RESERVATION_OFFER_EXPIRED,
        )
        replacement = self.reserve(self.dog, user=self.other_user)
        self.assertEqual(
            replacement.status,
            PreReservation.Status.PENDING_PAYMENT,
        )

    def test_acceptance_uses_amount_retained_after_partial_refund(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )
        payment = pre_reservation.payment
        payment.status = Payment.Status.PARTIALLY_REFUNDED
        payment.save(update_fields=['status'])
        payment.refunds.create(
            calculation_type='fixed',
            amount=decimal.Decimal('10.00'),
            reason='Courtesy',
            status='succeeded',
        )

        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.admin,
        )

        self.assertEqual(
            reservation.pre_reservation_credit_amount,
            decimal.Decimal('40.00'),
        )
        self.assertEqual(
            reservation.payment_amount,
            decimal.Decimal('710.00'),
        )

    def test_pending_refund_blocks_acceptance(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )
        pre_reservation.payment.refunds.create(
            calculation_type='fixed',
            amount=decimal.Decimal('10.00'),
            reason='Pending decision',
            status='pending',
        )

        with self.assertRaisesMessage(
            ReservationUnavailable,
            'pending refund',
        ):
            accept_pre_reservation(
                pre_reservation_id=pre_reservation.pk,
                admin_user=self.admin,
            )

    def test_price_change_after_payment_blocks_acceptance(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )
        self.dog.price_in_euros = decimal.Decimal('1600.00')
        self.dog.save(update_fields=['price_in_euros'])

        with self.assertRaisesMessage(
            ReservationUnavailable,
            'price changed',
        ):
            accept_pre_reservation(
                pre_reservation_id=pre_reservation.pk,
                admin_user=self.admin,
            )

    def test_only_accepted_pre_reservation_can_reach_deposit_checkout(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('reservations:dashboard'))

        self.assertContains(response, 'Awaiting breeder review')
        self.assertNotContains(response, 'Continue to reservation deposit')
        self.assertFalse(
            Reservation.objects.filter(
                pre_reservation=pre_reservation,
            ).exists()
        )

    def test_dashboard_query_count_is_bounded_across_multiple_processes(self):
        self.reserve(self.dog)
        for index in range(3):
            dog = Animal.objects.create(
                breed=self.breed,
                name=f'Dashboard dog {index}',
                birth_date=timezone.localdate() - timedelta(days=300),
                gender='F',
                active=True,
                for_sale=True,
                price_in_euros='1500.00',
            )
            self.reserve(dog)
        self.client.force_login(self.user)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('reservations:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 18)

    def test_zero_balance_deposit_confirms_only_after_terms_acceptance(self):
        self.dog.reservation_deposit_percentage = decimal.Decimal('3.00')
        self.dog.save(update_fields=['reservation_deposit_percentage'])
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )
        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.admin,
        )

        reservation = start_reservation_payment(
            reservation_id=reservation.pk,
            user=self.user,
            accepted_terms=self.reservation_terms,
        )

        pre_reservation.refresh_from_db()
        self.assertEqual(reservation.payment_amount, decimal.Decimal('0.00'))
        self.assertEqual(reservation.status, Reservation.Status.CONFIRMED)
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.CONVERTED_TO_RESERVATION,
        )
        self.assertEqual(reservation.payment.provider, Payment.Provider.COMPLIMENTARY)

    def test_rejection_releases_dog_without_automatic_refund(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )

        reject_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.admin,
            reason='Not a suitable placement.',
        )

        pre_reservation.refresh_from_db()
        self.assertEqual(pre_reservation.status, PreReservation.Status.NOT_ACCEPTED)
        self.assertFalse(pre_reservation.payment.refunds.exists())
        replacement = self.reserve(self.dog, user=self.other_user)
        self.assertEqual(replacement.status, PreReservation.Status.PENDING_PAYMENT)

    def test_customer_cancellation_never_creates_automatic_refund(self):
        pre_reservation = self.mark_pre_reservation_paid(
            self.reserve(self.dog),
        )

        cancel_pre_reservation_by_user(
            pre_reservation_id=pre_reservation.pk,
            user=self.user,
        )

        pre_reservation.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.CANCELLED_BY_USER,
        )
        self.assertEqual(pre_reservation.payment.status, Payment.Status.PAID)
        self.assertFalse(pre_reservation.payment.refunds.exists())

    def test_public_site_distinguishes_pre_reserved_from_reserved(self):
        pre_reservation = self.reserve(self.dog)

        response = self.client.get(
            reverse('breeding:dog_detail', args=[self.dog.pk]),
        )
        self.assertContains(response, 'Pre-reserved')
        self.assertNotContains(response, '>Call Us<', html=True)

        self.mark_pre_reservation_paid(pre_reservation)
        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.admin,
        )
        reservation.status = Reservation.Status.CONFIRMED
        reservation.save(update_fields=['status'])
        pre_reservation.status = PreReservation.Status.CONVERTED_TO_RESERVATION
        pre_reservation.save(update_fields=['status'])

        response = self.client.get(
            reverse('breeding:dog_detail', args=[self.dog.pk]),
        )
        self.assertContains(response, 'Reserved')
        self.assertNotContains(response, 'Pre-reserved')
        self.assertNotContains(response, '>Call Us<', html=True)

        Payment.objects.filter(
            charge__sale_case=reservation.sale_case,
            status__in=(
                Payment.Status.INITIALIZING,
                Payment.Status.PENDING,
            ),
        ).update(status=Payment.Status.FAILED)
        complete_existing_sale_case(
            sale_case_id=reservation.sale_case_id,
            final_price=decimal.Decimal('1500.00'),
            payment_provider=Payment.Provider.BANK_TRANSFER,
            sold_at=timezone.localdate(),
            completed_by=self.admin,
            payment_reference='public-state-test',
        )
        response = self.client.get(
            reverse('breeding:dog_detail', args=[self.dog.pk]),
        )
        self.assertContains(response, 'Sold', count=1)
        self.assertNotContains(response, 'Reserved')
        self.assertNotContains(response, 'Pre-reserved')

    def test_deleted_dog_remains_in_customer_history(self):
        pre_reservation = self.reserve(self.dog)
        dog_name = self.dog.name
        self.dog.delete()
        pre_reservation.refresh_from_db()

        self.assertIsNone(pre_reservation.animal_id)
        self.assertIsNotNone(pre_reservation.target_deleted_at)
        self.client.force_login(self.user)
        response = self.client.get(reverse('reservations:dashboard'))
        self.assertContains(response, dog_name)
        self.assertContains(response, 'purchase history is preserved')


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentDogPreReservationTests(
    ReservationTestMixin,
    TransactionTestCase,
):
    reset_sequences = True

    def setUp(self):
        self.create_domain_data()

    def test_two_customers_cannot_hold_the_same_dog(self):
        barrier = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def reserve(user_id):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_id)
                data = self.checkout_data()
                data['terms'] = self.terms
                barrier.wait(timeout=5)
                create_pending_reservation(
                    user=user,
                    target_type=PreReservation.TargetType.DOG,
                    target_id=self.dog.pk,
                    checkout_data=data,
                    language_code='en',
                )
            except ReservationUnavailable:
                outcome = 'unavailable'
            else:
                outcome = 'held'
            finally:
                close_old_connections()
            with outcome_lock:
                outcomes.append(outcome)

        threads = [
            threading.Thread(target=reserve, args=(self.user.pk,)),
            threading.Thread(target=reserve, args=(self.other_user.pk,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(sorted(outcomes), ['held', 'unavailable'])
        self.assertEqual(
            PreReservation.objects.capacity_consuming().count(),
            1,
        )
