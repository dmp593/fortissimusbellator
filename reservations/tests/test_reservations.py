import decimal
import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import (
    TestCase,
    TransactionTestCase,
    override_settings,
    skipUnlessDBFeature,
)
from django.urls import reverse

from breeding.models import Litter
from reservations.exceptions import ReservationUnavailable
from reservations.models import Payment, PreReservation
from reservations.services.reservation import (
    cancel_by_user,
    mark_payment_setup_failed,
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

    def test_pending_reservation_blocks_the_same_dog_before_payment(self):
        reservation = self.reserve(self.dog)

        self.assertEqual(reservation.status, PreReservation.Status.PENDING_PAYMENT)
        self.assertEqual(reservation.payment.status, Payment.Status.INITIALIZING)
        with self.assertRaisesMessage(
            ReservationUnavailable,
            'already reserved',
        ):
            self.reserve(self.dog, user=self.other_user)
        self.assertEqual(
            PreReservation.objects.capacity_consuming().filter(
                animal=self.dog
            ).count(),
            1,
        )

    def test_reservation_never_changes_the_dog_sale_listing_flag(self):
        reservation = self.reserve(self.dog)
        self.dog.refresh_from_db()

        self.assertTrue(self.dog.for_sale)

        mark_payment_setup_failed(reservation.pk, 'Definite Stripe failure')
        self.dog.refresh_from_db()

        self.assertTrue(self.dog.for_sale)

    def test_failed_payment_releases_the_dog_but_preserves_history(self):
        first = self.reserve(self.dog)

        mark_payment_setup_failed(first.pk, 'Definite Stripe failure')
        second = self.reserve(self.dog, user=self.other_user)

        first.refresh_from_db()
        self.assertEqual(first.status, PreReservation.Status.PAYMENT_FAILED)
        self.assertEqual(second.status, PreReservation.Status.PENDING_PAYMENT)
        self.assertEqual(PreReservation.objects.filter(animal=self.dog).count(), 2)

    def test_failed_payment_retry_uses_a_fresh_reservation_and_current_price(self):
        failed_reservation = self.reserve(self.dog)
        mark_payment_setup_failed(
            failed_reservation.pk,
            'Definite Stripe failure',
        )
        self.dog.pre_reservation_fee = decimal.Decimal('65.00')
        self.dog.save(update_fields=['pre_reservation_fee'])
        self.client.force_login(self.user)

        dashboard_response = self.client.get(reverse('reservations:dashboard'))
        self.assertContains(dashboard_response, 'Try again')

        retry_response = self.client.post(
            reverse(
                'reservations:retry_payment',
                args=[failed_reservation.public_id],
            )
        )
        checkout_url = reverse(
            'breeding:pre_reserve_dog',
            args=[self.dog.pk],
        )
        self.assertRedirects(
            retry_response,
            f'{checkout_url}?retry={failed_reservation.public_id}',
            fetch_redirect_response=False,
        )

        checkout_response = self.client.get(retry_response.url)
        self.assertEqual(
            checkout_response.context['form'].initial['email'],
            failed_reservation.customer_email,
        )
        self.assertContains(checkout_response, '65.00 EUR')

        with patch(
            'reservations.views.initialize_checkout',
            return_value='https://checkout.stripe.test/retry',
        ):
            payment_response = self.client.post(
                retry_response.url,
                data=self.checkout_data(),
            )

        self.assertRedirects(
            payment_response,
            'https://checkout.stripe.test/retry',
            fetch_redirect_response=False,
        )
        retried_reservation = PreReservation.objects.exclude(
            pk=failed_reservation.pk
        ).get()
        self.assertEqual(
            retried_reservation.total_amount,
            decimal.Decimal('65.00'),
        )
        failed_reservation.refresh_from_db()
        self.assertEqual(
            failed_reservation.status,
            PreReservation.Status.PAYMENT_FAILED,
        )

    def test_failed_payment_retry_cannot_reclaim_a_reserved_dog(self):
        failed_reservation = self.reserve(self.dog)
        mark_payment_setup_failed(
            failed_reservation.pk,
            'Definite Stripe failure',
        )
        self.reserve(self.dog, user=self.other_user)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                'reservations:retry_payment',
                args=[failed_reservation.public_id],
            ),
            follow=True,
        )

        self.assertContains(response, 'This dog is already reserved.')
        self.assertEqual(PreReservation.objects.filter(animal=self.dog).count(), 2)

    def test_retry_reference_never_prefills_another_users_details(self):
        failed_reservation = self.reserve(self.dog)
        mark_payment_setup_failed(
            failed_reservation.pk,
            'Definite Stripe failure',
        )
        self.client.force_login(self.other_user)

        checkout_url = reverse(
            'breeding:pre_reserve_dog',
            args=[self.dog.pk],
        )
        response = self.client.get(
            f'{checkout_url}?retry={failed_reservation.public_id}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['form'].initial['email'],
            self.other_user.email,
        )
        self.assertNotContains(response, failed_reservation.customer_phone)

    def test_failed_payment_explains_when_released_dog_is_not_for_sale(self):
        failed_reservation = self.reserve(self.dog)
        mark_payment_setup_failed(
            failed_reservation.pk,
            'Definite Stripe failure',
        )
        self.dog.for_sale = False
        self.dog.save(update_fields=['for_sale'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('reservations:dashboard'))

        self.assertContains(
            response,
            'The failed payment no longer holds this dog or litter place.',
        )
        self.assertNotContains(response, 'Try again')

    def test_litter_capacity_uses_offered_places_not_total_babies(self):
        self.litter.pre_reservation_capacity = 2
        self.litter.save(update_fields=['pre_reservation_capacity'])
        third_user = get_user_model().objects.create_user(username='third')

        self.reserve(self.litter)
        self.reserve(self.litter, user=self.other_user)
        with self.assertRaisesMessage(ReservationUnavailable, 'fully reserved'):
            self.reserve(self.litter, user=third_user)

        self.assertEqual(self.litter.babies, 5)
        self.assertEqual(
            PreReservation.objects.capacity_consuming().filter(
                litter=self.litter
            ).count(),
            2,
        )

    def test_expecting_litter_cannot_be_reserved(self):
        self.litter.status = Litter.LitterStatus.EXPECTING
        self.litter.pre_reservation_capacity = 0
        self.litter.save(update_fields=['status', 'pre_reservation_capacity'])

        with self.assertRaisesMessage(ReservationUnavailable, 'only after'):
            self.reserve(self.litter)

    def test_customer_can_hold_only_one_active_place_in_a_litter(self):
        self.reserve(self.litter)

        with self.assertRaisesMessage(
            ReservationUnavailable,
            'already have an active',
        ):
            self.reserve(self.litter)

    def test_user_cancellation_is_non_refundable_and_releases_capacity(self):
        reservation = self.reserve(self.dog)
        reservation.status = PreReservation.Status.CONFIRMED
        reservation.confirmed_at = reservation.created_at
        reservation.save(update_fields=['status', 'confirmed_at'])
        Payment.objects.filter(reservation=reservation).update(
            status=Payment.Status.PAID,
            stripe_payment_intent_id='pi_customer_cancelled',
        )

        with self.captureOnCommitCallbacks(execute=True):
            cancel_by_user(reservation_id=reservation.pk, user=self.user)

        reservation.refresh_from_db()
        reservation.payment.refresh_from_db()
        self.assertEqual(
            reservation.status,
            PreReservation.Status.CANCELLED_BY_USER,
        )
        self.assertEqual(reservation.payment.status, Payment.Status.PAID)
        self.reserve(self.dog, user=self.other_user)

    def test_deleted_target_remains_visible_in_customer_history(self):
        reservation = self.reserve(self.dog)
        dog_name = self.dog.name
        self.dog.delete()
        reservation.refresh_from_db()

        self.assertIsNone(reservation.animal_id)
        self.assertIsNotNone(reservation.target_deleted_at)
        self.client.force_login(self.user)
        response = self.client.get(reverse('reservations:dashboard'))
        self.assertContains(response, dog_name)
        self.assertContains(response, 'reservation history is preserved')

    def test_deactivated_target_remains_visible_in_customer_dashboard(self):
        reservation = self.reserve(self.dog)
        self.dog.for_sale = False
        self.dog.save(update_fields=['for_sale'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('reservations:dashboard'))

        self.assertContains(response, reservation.target_name)
        self.assertContains(response, 'listing is no longer public')

    def test_cancellation_requires_a_confirmation_page(self):
        reservation = self.reserve(self.dog)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('reservations:cancel', args=[reservation.public_id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'The fee will not be refunded')
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, PreReservation.Status.PENDING_PAYMENT)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentLitterReservationTests(ReservationTestMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.create_domain_data()
        self.litter.pre_reservation_capacity = 1
        self.litter.save(update_fields=['pre_reservation_capacity'])

    def test_two_simultaneous_customers_cannot_take_one_litter_place(self):
        barrier = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def reserve(user_id):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_id)
                barrier.wait(timeout=5)
                self.reserve(self.litter, user=user)
            except ReservationUnavailable:
                outcome = 'unavailable'
            else:
                outcome = 'reserved'
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

        self.assertEqual(sorted(outcomes), ['reserved', 'unavailable'])
        self.assertEqual(
            PreReservation.objects.capacity_consuming().filter(
                litter=self.litter
            ).count(),
            1,
        )
