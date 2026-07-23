from datetime import timedelta
from unittest.mock import patch

import stripe
from django.test import TestCase, override_settings
from django.utils import timezone

from reservations.exceptions import PaymentError, ReservationUnavailable
from reservations.models import (
    ERPDocument,
    Payment,
    PreReservation,
    ProcessedStripeEvent,
)
from reservations.services.payment import (
    cancel_customer_reservation,
    fulfill_checkout_session,
    initialize_checkout,
    process_refund,
    process_stripe_webhook,
    reconcile_pending_payment,
    release_failed_or_expired_checkout,
)
from reservations.policies import checkout_duration_minutes
from reservations.tests.base import ReservationTestMixin


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BUSINESS_NOTIFICATION_RECIPIENTS=['staff@example.com'],
    STRIPE_SECRET_KEY='sk_test_example',
)
class StripePaymentWorkflowTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()

    def initialize(self, reservation):
        expires_at = int((timezone.now() + timedelta(minutes=30)).timestamp())
        session = {
            'id': f'cs_{reservation.pk}',
            'url': 'https://checkout.stripe.test/session',
            'expires_at': expires_at,
        }
        with patch(
            'reservations.stripe_gateway.create_checkout_session',
            return_value=session,
        ):
            initialize_checkout(
                reservation=reservation,
                success_url='https://example.test/success',
                cancel_url='https://example.test/cancel',
            )
        reservation.refresh_from_db()
        return reservation

    @staticmethod
    def paid_session(reservation):
        return {
            'id': reservation.payment.stripe_checkout_session_id,
            'payment_status': 'paid',
            'client_reference_id': str(reservation.public_id),
            'currency': 'eur',
            'amount_total': int(reservation.total_amount * 100),
            'payment_intent': {'id': f'pi_{reservation.pk}'},
            'metadata': {'pre_reservation_id': str(reservation.public_id)},
        }

    def test_paid_transition_and_durable_erp_task_commit_together(self):
        reservation = self.initialize(self.reserve(self.dog))

        with patch(
            'reservations.stripe_gateway.retrieve_checkout_session',
            return_value=self.paid_session(reservation),
        ), patch(
            'reservations.services.payment.notify_payment_confirmed'
        ) as notify, self.captureOnCommitCallbacks(execute=True):
            fulfilled = fulfill_checkout_session(
                reservation.payment.stripe_checkout_session_id
            )

        fulfilled.refresh_from_db()
        fulfilled.payment.refresh_from_db()
        self.assertEqual(fulfilled.status, PreReservation.Status.CONFIRMED)
        self.assertEqual(fulfilled.payment.status, Payment.Status.PAID)
        document = fulfilled.erp_documents.get(kind=ERPDocument.Kind.SALE)
        self.assertEqual(document.status, ERPDocument.Status.PENDING)
        notify.assert_called_once()

    def test_fulfillment_is_idempotent(self):
        reservation = self.initialize(self.reserve(self.dog))
        session = self.paid_session(reservation)

        with patch(
            'reservations.stripe_gateway.retrieve_checkout_session',
            return_value=session,
        ):
            fulfill_checkout_session(session['id'])
            fulfill_checkout_session(session['id'])

        self.assertEqual(reservation.erp_documents.count(), 1)
        self.assertEqual(
            Payment.objects.get(reservation=reservation).status,
            Payment.Status.PAID,
        )

    def test_ambiguous_checkout_error_keeps_the_place_held(self):
        reservation = self.reserve(self.dog)

        with patch(
            'reservations.stripe_gateway.create_checkout_session',
            side_effect=stripe.APIConnectionError('connection lost'),
        ), self.assertRaises(PaymentError):
            initialize_checkout(
                reservation=reservation,
                success_url='https://example.test/success',
                cancel_url='https://example.test/cancel',
            )

        reservation.refresh_from_db()
        reservation.payment.refresh_from_db()
        self.assertEqual(reservation.status, PreReservation.Status.PENDING_PAYMENT)
        self.assertEqual(reservation.payment.status, Payment.Status.INITIALIZING)
        with self.assertRaises(ReservationUnavailable):
            self.reserve(self.dog, user=self.other_user)

    def test_pending_refund_is_retrieved_until_succeeded(self):
        reservation = self.reserve(self.dog)
        payment = reservation.payment
        payment.status = Payment.Status.REFUND_PENDING
        payment.stripe_payment_intent_id = 'pi_refund'
        payment.save(update_fields=['status', 'stripe_payment_intent_id'])

        with patch(
            'reservations.stripe_gateway.find_reservation_refund',
            return_value=None,
        ), patch(
            'reservations.stripe_gateway.create_refund',
            return_value={'id': 're_pending', 'status': 'pending'},
        ):
            payment = process_refund(payment.pk)

        self.assertEqual(payment.status, Payment.Status.REFUND_PENDING)
        self.assertEqual(payment.stripe_refund_id, 're_pending')
        self.assertFalse(
            reservation.erp_documents.filter(
                kind=ERPDocument.Kind.CREDIT_NOTE
            ).exists()
        )

        with patch(
            'reservations.stripe_gateway.retrieve_refund',
            return_value={'id': 're_pending', 'status': 'succeeded'},
        ):
            payment = process_refund(payment.pk)

        self.assertEqual(payment.status, Payment.Status.REFUNDED)
        self.assertTrue(
            reservation.erp_documents.filter(
                kind=ERPDocument.Kind.CREDIT_NOTE
            ).exists()
        )

    def test_refund_retry_reconciles_an_ambiguous_creation_response(self):
        reservation = self.reserve(self.dog)
        payment = reservation.payment
        payment.status = Payment.Status.REFUND_FAILED
        payment.stripe_payment_intent_id = 'pi_ambiguous_refund'
        payment.save(update_fields=['status', 'stripe_payment_intent_id'])

        existing_refund = {'id': 're_existing', 'status': 'succeeded'}
        with patch(
            'reservations.stripe_gateway.find_reservation_refund',
            return_value=existing_refund,
        ), patch(
            'reservations.stripe_gateway.create_refund'
        ) as create:
            payment = process_refund(payment.pk)

        self.assertEqual(payment.status, Payment.Status.REFUNDED)
        self.assertEqual(payment.stripe_refund_id, 're_existing')
        create.assert_not_called()

    def test_expired_local_hold_finds_lost_paid_checkout_before_release(self):
        reservation = self.reserve(self.dog)
        reservation.hold_expires_at = timezone.now() - timedelta(seconds=1)
        reservation.save(update_fields=['hold_expires_at'])
        session = {
            'id': 'cs_discovered',
            'url': '',
            'expires_at': int((timezone.now() - timedelta(minutes=1)).timestamp()),
            'status': 'complete',
            'payment_status': 'paid',
            'client_reference_id': str(reservation.public_id),
            'currency': 'eur',
            'amount_total': 5000,
            'payment_intent': {'id': 'pi_discovered'},
            'metadata': {'pre_reservation_id': str(reservation.public_id)},
        }

        with patch(
            'reservations.stripe_gateway.find_reservation_checkout_session',
            return_value=session,
        ), patch(
            'reservations.stripe_gateway.retrieve_checkout_session',
            return_value=session,
        ):
            reconcile_pending_payment(reservation.payment.pk)

        reservation.refresh_from_db()
        reservation.payment.refresh_from_db()
        self.assertEqual(reservation.status, PreReservation.Status.CONFIRMED)
        self.assertEqual(reservation.payment.status, Payment.Status.PAID)
        self.assertEqual(
            reservation.payment.stripe_checkout_session_id,
            'cs_discovered',
        )

    @override_settings(RESERVATION_CHECKOUT_MINUTES=5)
    def test_checkout_duration_respects_stripe_minimum(self):
        self.assertEqual(checkout_duration_minutes(), 30)

    @override_settings(RESERVATION_CHECKOUT_MINUTES=2000)
    def test_checkout_duration_respects_stripe_maximum(self):
        self.assertEqual(checkout_duration_minutes(), 1440)

    def test_unpaid_completed_webhook_waits_for_async_success(self):
        reservation = self.initialize(self.reserve(self.dog))
        event = {
            'id': 'evt_unpaid_complete',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': reservation.payment.stripe_checkout_session_id,
                    'payment_status': 'unpaid',
                }
            },
        }

        process_stripe_webhook(event)

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, PreReservation.Status.PENDING_PAYMENT)
        self.assertTrue(
            ProcessedStripeEvent.objects.filter(
                event_id='evt_unpaid_complete'
            ).exists()
        )

    def test_customer_cancellation_expires_checkout_and_closes_payment(self):
        reservation = self.initialize(self.reserve(self.dog))
        open_session = {
            'id': reservation.payment.stripe_checkout_session_id,
            'status': 'open',
            'payment_status': 'unpaid',
        }

        with patch(
            'reservations.stripe_gateway.retrieve_checkout_session',
            return_value=open_session,
        ), patch(
            'reservations.stripe_gateway.expire_checkout_session'
        ) as expire:
            cancel_customer_reservation(
                reservation=reservation,
                user=self.user,
            )

        reservation.refresh_from_db()
        reservation.payment.refresh_from_db()
        expire.assert_called_once_with(
            reservation.payment.stripe_checkout_session_id
        )
        self.assertEqual(
            reservation.status,
            PreReservation.Status.CANCELLED_BY_USER,
        )
        self.assertEqual(reservation.payment.status, Payment.Status.FAILED)

        release_failed_or_expired_checkout(
            session_id=reservation.payment.stripe_checkout_session_id,
            expired=True,
        )
        reservation.refresh_from_db()
        self.assertEqual(
            reservation.status,
            PreReservation.Status.CANCELLED_BY_USER,
        )

    def test_late_payment_after_expiry_is_queued_for_refund_without_rebooking(self):
        reservation = self.initialize(self.reserve(self.dog))
        session_id = reservation.payment.stripe_checkout_session_id
        release_failed_or_expired_checkout(
            session_id=session_id,
            expired=True,
        )
        paid_session = self.paid_session(reservation)

        with patch(
            'reservations.stripe_gateway.retrieve_checkout_session',
            return_value=paid_session,
        ):
            fulfill_checkout_session(session_id)

        reservation.refresh_from_db()
        reservation.payment.refresh_from_db()
        self.assertEqual(reservation.status, PreReservation.Status.EXPIRED)
        self.assertEqual(
            reservation.payment.status,
            Payment.Status.REFUND_PENDING,
        )
        self.assertTrue(
            reservation.erp_documents.filter(kind=ERPDocument.Kind.SALE).exists()
        )
        replacement = self.reserve(self.dog, user=self.other_user)
        self.assertEqual(replacement.status, PreReservation.Status.PENDING_PAYMENT)
