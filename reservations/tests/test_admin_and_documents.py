from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from reservations.models import ERPDocument, Payment, PreReservation
from reservations.services.reservation import (
    ensure_sale_erp_document,
    mark_payment_setup_failed,
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

    def test_reserved_dog_deletion_requires_second_confirmation(self):
        reservation = self.reserve(self.dog)
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
        reservation.refresh_from_db()
        self.assertIsNone(reservation.animal_id)
        self.assertIsNotNone(reservation.target_deleted_at)

    def test_pre_reservation_admin_change_page_exposes_recovery_state(self):
        reservation = self.reserve(self.dog)
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse(
                'admin:reservations_prereservation_change',
                args=[reservation.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(reservation.public_id))
        self.assertContains(response, 'Operational state')

    def test_admin_can_delete_unpaid_local_payment_setup_failure(self):
        reservation = self.reserve(self.dog)
        mark_payment_setup_failed(reservation.pk, 'Stripe is not configured.')
        delete_url = reverse(
            'admin:reservations_prereservation_delete',
            args=[reservation.pk],
        )
        self.client.force_login(self.superuser)

        confirmation = self.client.get(delete_url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, 'Are you sure')

        response = self.client.post(delete_url, {'post': 'yes'})

        self.assertRedirects(
            response,
            reverse('admin:reservations_prereservation_changelist'),
        )
        self.assertFalse(PreReservation.objects.filter(pk=reservation.pk).exists())
        self.assertFalse(Payment.objects.filter(reservation_id=reservation.pk).exists())

    def test_admin_cannot_delete_reservation_with_a_stripe_checkout(self):
        reservation = self.reserve(self.dog)
        mark_payment_setup_failed(reservation.pk, 'Card payment failed.')
        Payment.objects.filter(reservation=reservation).update(
            stripe_checkout_session_id='cs_failed_checkout',
        )
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse(
                'admin:reservations_prereservation_delete',
                args=[reservation.pk],
            )
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(PreReservation.objects.filter(pk=reservation.pk).exists())

    def test_fiscal_pdf_is_private_to_owner_and_staff(self):
        reservation = self.reserve(self.dog)
        document = ensure_sale_erp_document(reservation)
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
