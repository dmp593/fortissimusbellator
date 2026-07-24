import decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from attachments.models import Attachment
from reservations.models import (
    ERPDocument,
    Payment,
    PaymentRefund,
    PreReservation,
    Reservation,
)
from reservations.services.erp import ensure_sale_erp_document
from reservations.services.reservation import (
    accept_pre_reservation,
    mark_pre_reservation_payment_setup_failed,
    start_reservation_payment,
)
from reservations.tests.base import ReservationTestMixin, TEST_STORAGES


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BUSINESS_NOTIFICATION_RECIPIENTS=['staff@example.com'],
    STATIC_ROOT=None,
    STORAGES=TEST_STORAGES,
)
class AdminAndDocumentSecurityTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()
        self.superuser = get_user_model().objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='test-password',
        )

    def create_confirmed_reservation(self):
        now = timezone.now()
        pre_reservation = self.reserve(self.dog)
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            status=Payment.Status.PAID,
            paid_at=now,
            stripe_payment_intent_id=f'pi_pre_{pre_reservation.pk}',
            provider_fee_amount=decimal.Decimal('2.00'),
            provider_net_amount=decimal.Decimal('48.00'),
        )
        PreReservation.objects.filter(pk=pre_reservation.pk).update(
            status=PreReservation.Status.AWAITING_REVIEW,
            confirmed_at=now,
        )
        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.superuser,
        )
        reservation = start_reservation_payment(
            reservation_id=reservation.pk,
            user=self.user,
            accepted_terms=self.reservation_terms,
        )
        Payment.objects.filter(animal_reservation=reservation).update(
            status=Payment.Status.PAID,
            paid_at=now,
            stripe_payment_intent_id=f'pi_reservation_{reservation.pk}',
            provider_fee_amount=decimal.Decimal('20.00'),
            provider_net_amount=decimal.Decimal('680.00'),
        )
        Reservation.objects.filter(pk=reservation.pk).update(
            status=Reservation.Status.CONFIRMED,
        )
        PreReservation.objects.filter(pk=pre_reservation.pk).update(
            status=PreReservation.Status.CONVERTED_TO_RESERVATION,
        )
        pre_reservation.refresh_from_db()
        reservation.refresh_from_db()
        return pre_reservation, reservation

    def test_dog_with_history_requires_second_delete_confirmation(self):
        pre_reservation = self.reserve(self.dog)
        delete_url = reverse('admin:breeding_animal_delete', args=[self.dog.pk])
        self.client.force_login(self.superuser)

        first_confirmation = self.client.get(delete_url)
        self.assertEqual(first_confirmation.status_code, 200)
        self.assertContains(first_confirmation, 'Are you sure')

        second_confirmation = self.client.post(delete_url, {'post': 'yes'})
        self.assertEqual(second_confirmation.status_code, 200)
        self.assertContains(second_confirmation, 'second and final confirmation')
        self.assertTrue(type(self.dog).objects.filter(pk=self.dog.pk).exists())

        response = self.client.post(
            delete_url,
            {'post': 'yes', 'confirm_reservation_history': 'yes'},
        )
        self.assertRedirects(
            response,
            reverse('admin:breeding_animal_changelist'),
        )
        pre_reservation.refresh_from_db()
        self.assertIsNone(pre_reservation.animal_id)
        self.assertIsNotNone(pre_reservation.target_deleted_at)

    def test_admin_can_inspect_full_pre_reservation_lifecycle(self):
        pre_reservation = self.reserve(self.dog)
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse(
                'admin:reservations_prereservation_change',
                args=[pre_reservation.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(pre_reservation.public_id))
        self.assertContains(response, 'Lifecycle')
        self.assertContains(response, 'Dog snapshot')

    def test_parent_fields_use_native_paginated_admin_autocomplete(self):
        self.client.force_login(self.superuser)

        animal_response = self.client.get(
            reverse('admin:breeding_animal_add'),
        )
        litter_response = self.client.get(
            reverse('admin:breeding_litter_add'),
        )

        animal_form = animal_response.context['adminform'].form
        litter_form = litter_response.context['adminform'].form
        self.assertEqual(
            animal_form.fields['father'].widget.widget.__class__.__name__,
            'AutocompleteSelect',
        )
        self.assertEqual(
            animal_form.fields['mother'].widget.widget.__class__.__name__,
            'AutocompleteSelect',
        )
        self.assertEqual(
            litter_form.fields['father'].widget.widget.__class__.__name__,
            'AutocompleteSelect',
        )
        self.assertEqual(
            litter_form.fields['mother'].widget.widget.__class__.__name__,
            'AutocompleteSelect',
        )

    def test_customer_cannot_cancel_an_accepted_or_confirmed_reservation(self):
        _, reservation = self.create_confirmed_reservation()
        self.client.force_login(self.user)
        dashboard_url = reverse('reservations:dashboard')
        old_cancel_url = (
            f'{dashboard_url}reservations/{reservation.public_id}/cancel/'
        )

        dashboard = self.client.get(dashboard_url)

        self.assertEqual(self.client.get(old_cancel_url).status_code, 404)
        self.assertNotContains(dashboard, 'Cancel reservation')

    def test_customer_cards_and_confirmation_show_the_dog_cover(self):
        _, reservation = self.create_confirmed_reservation()
        Attachment.objects.create(
            file='attachments/athena-cover.jpg',
            content_type=ContentType.objects.get_for_model(self.dog),
            object_id=self.dog.pk,
            filename='athena-cover.jpg',
            mime_type='image/jpeg',
            order=1,
        )
        self.client.force_login(self.user)

        dashboard = self.client.get(reverse('reservations:dashboard'))
        confirmation = self.client.get(
            reverse(
                'reservations:reservation_confirmation',
                args=[reservation.public_id],
            )
        )

        self.assertContains(dashboard, '/media/attachments/athena-cover.jpg')
        self.assertContains(
            confirmation,
            '/media/attachments/athena-cover.jpg',
        )

    def test_admin_cancels_reservation_without_refund_and_notifies_customer(self):
        _, reservation = self.create_confirmed_reservation()
        cancel_url = reverse(
            'admin:reservations_reservation_cancel',
            args=[reservation.pk],
        )
        self.client.force_login(self.superuser)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                cancel_url,
                {
                    'reason': 'Placement can no longer proceed.',
                    'refund_calculation': 'none',
                    'confirm': 'on',
                },
            )

        reservation.refresh_from_db()
        self.assertRedirects(
            response,
            reverse(
                'admin:reservations_reservation_change',
                args=[reservation.pk],
            ),
        )
        self.assertEqual(
            reservation.status,
            Reservation.Status.CANCELLED_BY_ADMIN,
        )
        self.assertFalse(PaymentRefund.objects.exists())
        self.assertTrue(
            any(
                'Reservation cancelled' in message.subject
                and self.user.email in message.to
                for message in mail.outbox
            )
        )

        self.client.force_login(self.user)
        dashboard = self.client.get(reverse('reservations:dashboard'))
        self.assertContains(dashboard, 'Cancelled by staff')
        self.assertNotContains(
            dashboard,
            'The pre-reservation is non-refundable by nature',
        )

    @patch('reservations.admin.process_refund')
    def test_admin_partial_refund_uses_reservation_payment_first(
        self,
        process_refund,
    ):
        pre_reservation, reservation = self.create_confirmed_reservation()
        self.client.force_login(self.superuser)

        response = self.client.post(
            reverse(
                'admin:reservations_reservation_cancel',
                args=[reservation.pk],
            ),
            {
                'reason': 'Exceptional partial refund.',
                'refund_calculation': PaymentRefund.CalculationType.FIXED,
                'fixed_amount': '100.00',
                'confirm': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        refund = PaymentRefund.objects.get()
        self.assertEqual(refund.payment, reservation.payment)
        self.assertEqual(refund.amount, decimal.Decimal('100.00'))
        self.assertFalse(
            pre_reservation.payment.refunds.exists(),
        )
        process_refund.assert_called_once_with(refund.pk)

    @patch('reservations.admin.process_refund')
    def test_admin_full_refund_covers_both_reservation_payments(
        self,
        process_refund,
    ):
        pre_reservation, reservation = self.create_confirmed_reservation()
        self.client.force_login(self.superuser)

        response = self.client.post(
            reverse(
                'admin:reservations_reservation_cancel',
                args=[reservation.pk],
            ),
            {
                'reason': 'The dog is no longer available.',
                'refund_calculation': (
                    PaymentRefund.CalculationType.FULL_REMAINING
                ),
                'assume_processing_costs': 'on',
                'confirm': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PaymentRefund.objects.count(), 2)
        self.assertEqual(
            pre_reservation.payment.refunds.get().amount,
            decimal.Decimal('50.00'),
        )
        self.assertEqual(
            reservation.payment.refunds.get().amount,
            decimal.Decimal('700.00'),
        )
        self.assertEqual(process_refund.call_count, 2)

    def test_admin_can_delete_failed_unpaid_attempt_even_with_closed_session(self):
        pre_reservation = self.reserve(self.dog)
        mark_pre_reservation_payment_setup_failed(
            pre_reservation.pk,
            'Card payment failed.',
        )
        charge_id = pre_reservation.charge_id
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            stripe_checkout_session_id='cs_failed_checkout',
        )
        Payment.objects.create(
            charge_id=charge_id,
            provider=Payment.Provider.STRIPE,
            status=Payment.Status.FAILED,
            amount=pre_reservation.total_amount,
            currency=pre_reservation.currency,
            last_error='Second card attempt failed.',
        )
        delete_url = reverse(
            'admin:reservations_prereservation_delete',
            args=[pre_reservation.pk],
        )
        self.client.force_login(self.superuser)

        self.assertEqual(self.client.get(delete_url).status_code, 200)
        response = self.client.post(delete_url, {'post': 'yes'})

        self.assertRedirects(
            response,
            reverse('admin:reservations_prereservation_changelist'),
        )
        self.assertFalse(
            PreReservation.objects.filter(pk=pre_reservation.pk).exists(),
        )
        self.assertFalse(
            Payment.objects.filter(pre_reservation_id=pre_reservation.pk).exists(),
        )
        self.assertFalse(Payment.objects.filter(charge_id=charge_id).exists())

    def test_admin_cannot_delete_paid_financial_history(self):
        pre_reservation = self.reserve(self.dog)
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            status=Payment.Status.PAID,
            paid_at=timezone.now(),
            stripe_payment_intent_id='pi_paid',
        )
        pre_reservation.status = PreReservation.Status.AWAITING_REVIEW
        pre_reservation.save(update_fields=['status'])
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse(
                'admin:reservations_prereservation_delete',
                args=[pre_reservation.pk],
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_delete_when_another_charge_payment_is_paid(self):
        pre_reservation = self.reserve(self.dog)
        mark_pre_reservation_payment_setup_failed(
            pre_reservation.pk,
            'Card payment failed.',
        )
        Payment.objects.create(
            charge=pre_reservation.charge,
            provider=Payment.Provider.CASH,
            status=Payment.Status.PAID,
            amount=pre_reservation.total_amount,
            currency=pre_reservation.currency,
            paid_at=timezone.now(),
            external_reference='receipt-123',
        )
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse(
                'admin:reservations_prereservation_delete',
                args=[pre_reservation.pk],
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_fiscal_pdf_is_private_to_owner_or_authorized_admin(self):
        pre_reservation = self.reserve(self.dog)
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            status=Payment.Status.PAID,
            paid_at=timezone.now(),
        )
        document = ensure_sale_erp_document(pre_reservation.payment)
        document.status = ERPDocument.Status.INTEGRATED
        document.erp_document_id = 'erp-private'
        document.pdf_status = ERPDocument.PDFStatus.AVAILABLE
        document.pdf_data = b'%PDF-1.7\nprivate'
        document.pdf_filename = 'reservation.pdf'
        document.save()
        url = reverse('reservations:download_document', args=[document.pk])

        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.other_user.is_staff = True
        self.other_user.save(update_fields=['is_staff'])
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.user)
        owner_response = self.client.get(url)
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(owner_response['Content-Type'], 'application/pdf')
        self.assertIn('private', owner_response['Cache-Control'])
        self.assertIn('no-store', owner_response['Cache-Control'])

        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(url).status_code, 200)

    @override_settings(TOCONLINE_ENABLED=False)
    def test_admin_erp_retry_requires_confirmed_post(self):
        pre_reservation = self.reserve(self.dog)
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            status=Payment.Status.PAID,
            paid_at=timezone.now(),
        )
        document = ensure_sale_erp_document(pre_reservation.payment)
        document.status = ERPDocument.Status.PENDING
        document.save(update_fields=['status'])
        retry_url = reverse(
            'admin:reservations_erpdocument_retry',
            args=[document.pk],
        )
        self.client.force_login(self.superuser)

        response = self.client.get(retry_url)
        document.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Retry ERP integration')
        self.assertEqual(document.status, ERPDocument.Status.PENDING)

        response = self.client.post(retry_url, {'confirm': 'on'})
        document.refresh_from_db()
        self.assertRedirects(
            response,
            reverse(
                'admin:reservations_erpdocument_change',
                args=[document.pk],
            ),
        )
        self.assertEqual(document.status, ERPDocument.Status.DEFERRED)
