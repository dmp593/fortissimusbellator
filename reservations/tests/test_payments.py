import decimal
from datetime import timedelta
from unittest.mock import patch

import stripe
from django.test import TestCase, override_settings
from django.utils import timezone

from reservations.exceptions import (
    PaymentConfigurationError,
    PaymentError,
    PaymentValidationError,
)
from reservations.models import (
    ERPDocument,
    Payment,
    PaymentRefund,
    PreReservation,
    ProcessedStripeEvent,
    Reservation,
)
from reservations.services.erp import ensure_sale_erp_document
from reservations.services.payment import (
    fulfill_checkout_session,
    initialize_checkout,
    prepare_failed_checkout_retry,
    process_refund,
    process_stripe_webhook,
    reconcile_refund_webhook,
    release_failed_or_expired_checkout,
    request_refund,
)
from reservations.services.reservation import (
    accept_pre_reservation,
    reopen_failed_reservation,
    start_reservation_payment,
)
from reservations.tests.base import ReservationTestMixin, TEST_STORAGES


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BUSINESS_NOTIFICATION_RECIPIENTS=['staff@example.com'],
    STATIC_ROOT=None,
    STORAGES=TEST_STORAGES,
    STRIPE_SECRET_KEY='sk_test_example',
    TOCONLINE_ENABLED=True,
)
class StripePaymentWorkflowTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()

    def initialize(self, purchase):
        payment = purchase.payment
        expires_at = int((timezone.now() + timedelta(minutes=30)).timestamp())
        session = {
            'id': f'cs_{payment.pk}_{payment.checkout_attempt_number}',
            'url': 'https://checkout.stripe.test/session',
            'expires_at': expires_at,
        }
        with patch(
            'reservations.stripe_gateway.create_checkout_session',
            return_value=session,
        ):
            checkout_url = initialize_checkout(
                purchase=purchase,
                success_url='https://example.test/success',
                cancel_url='https://example.test/cancel',
            )
        purchase.refresh_from_db()
        self.assertEqual(checkout_url, session['url'])
        return purchase

    @staticmethod
    def paid_session(purchase):
        payment = Payment.objects.get(pk=purchase.payment.pk)
        return {
            'id': payment.stripe_checkout_session_id,
            'payment_status': 'paid',
            'client_reference_id': str(purchase.public_id),
            'currency': payment.currency.lower(),
            'amount_total': int(payment.amount * 100),
            'payment_intent': {'id': f'pi_{payment.pk}'},
            'metadata': {
                'local_payment_id': str(payment.pk),
                'purchase_public_id': str(purchase.public_id),
                'checkout_attempt_number': str(
                    payment.checkout_attempt_number,
                ),
            },
        }

    def fulfill(self, purchase):
        session = self.paid_session(purchase)
        with patch(
            'reservations.stripe_gateway.retrieve_checkout_session',
            return_value=session,
        ), patch(
            'reservations.stripe_gateway.retrieve_payment_financials',
            return_value={
                'charge_id': f'ch_{purchase.payment.pk}',
                'fee_amount': decimal.Decimal('2.00'),
                'net_amount': purchase.payment.amount - decimal.Decimal('2.00'),
            },
        ):
            return fulfill_checkout_session(session['id'])

    def accepted_reservation(self):
        pre_reservation = self.fulfill(
            self.initialize(self.reserve(self.dog)),
        )
        reservation = accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.user,
        )
        return start_reservation_payment(
            reservation_id=reservation.pk,
            user=self.user,
            accepted_terms=self.reservation_terms,
        )

    def settled_pre_payment(self):
        pre_reservation = self.reserve(self.dog)
        payment = pre_reservation.payment
        payment.status = Payment.Status.PAID
        payment.stripe_payment_intent_id = f'pi_refund_{payment.pk}'
        payment.provider_fee_amount = decimal.Decimal('2.00')
        payment.provider_net_amount = decimal.Decimal('48.00')
        payment.paid_at = timezone.now()
        payment.save()
        pre_reservation.status = PreReservation.Status.AWAITING_REVIEW
        pre_reservation.confirmed_at = timezone.now()
        pre_reservation.save(update_fields=['status', 'confirmed_at'])
        ensure_sale_erp_document(payment)
        return pre_reservation, payment

    def test_pre_reservation_payment_awaits_review_and_creates_durable_sale(self):
        pre_reservation = self.fulfill(
            self.initialize(self.reserve(self.dog)),
        )

        pre_reservation.refresh_from_db()
        pre_reservation.payment.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.AWAITING_REVIEW,
        )
        self.assertIsNone(pre_reservation.hold_expires_at)
        self.assertEqual(pre_reservation.payment.status, Payment.Status.PAID)
        self.assertEqual(pre_reservation.payment.stripe_charge_id, f'ch_{pre_reservation.payment.pk}')
        document = pre_reservation.payment.erp_documents.get(
            kind=ERPDocument.Kind.SALE,
        )
        self.assertEqual(document.status, ERPDocument.Status.PENDING)

    @override_settings(TOCONLINE_ENABLED=False)
    def test_disabled_erp_is_deferred_without_customer_attention_state(self):
        pre_reservation = self.fulfill(
            self.initialize(self.reserve(self.dog)),
        )
        document = pre_reservation.payment.erp_documents.get(
            kind=ERPDocument.Kind.SALE,
        )

        self.assertEqual(document.status, ERPDocument.Status.DEFERRED)
        self.client.force_login(self.user)
        response = self.client.get('/en/my-reservations/')
        self.assertNotContains(response, 'Requires attention')
        self.assertNotContains(response, 'Fiscal documents')
        self.assertNotContains(response, 'Integration deferred')
        self.assertNotContains(
            response,
            'Our team has been notified to complete the accounting integration.',
        )

    @override_settings(TOCONLINE_ENABLED=False)
    def test_integrated_document_remains_visible_when_integration_is_disabled(self):
        pre_reservation = self.fulfill(
            self.initialize(self.reserve(self.dog)),
        )
        document = pre_reservation.payment.erp_documents.get(
            kind=ERPDocument.Kind.SALE,
        )
        document.status = ERPDocument.Status.INTEGRATED
        document.erp_document_id = 'erp-existing'
        document.pdf_status = ERPDocument.PDFStatus.AVAILABLE
        document.pdf_data = b'%PDF-1.7\nexisting'
        document.pdf_filename = 'existing.pdf'
        document.save()
        self.client.force_login(self.user)

        response = self.client.get('/en/my-reservations/')

        self.assertContains(response, 'Fiscal documents')
        self.assertContains(response, 'Download PDF')

    def test_fulfillment_is_idempotent(self):
        pre_reservation = self.initialize(self.reserve(self.dog))
        self.fulfill(pre_reservation)
        self.fulfill(pre_reservation)

        self.assertEqual(
            pre_reservation.payment.erp_documents.filter(
                kind=ERPDocument.Kind.SALE,
            ).count(),
            1,
        )

    def test_ambiguous_checkout_creation_keeps_dog_held(self):
        pre_reservation = self.reserve(self.dog)

        with patch(
            'reservations.stripe_gateway.create_checkout_session',
            side_effect=stripe.APIConnectionError('connection lost'),
        ), self.assertRaises(PaymentError):
            initialize_checkout(
                purchase=pre_reservation,
                success_url='https://example.test/success',
                cancel_url='https://example.test/cancel',
            )

        pre_reservation.refresh_from_db()
        pre_reservation.payment.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.PENDING_PAYMENT,
        )
        self.assertEqual(
            pre_reservation.payment.status,
            Payment.Status.INITIALIZING,
        )

    def test_definite_checkout_configuration_failure_releases_dog(self):
        pre_reservation = self.reserve(self.dog)

        with patch(
            'reservations.stripe_gateway.create_checkout_session',
            side_effect=PaymentConfigurationError('Stripe is not configured.'),
        ), self.assertRaises(PaymentError):
            initialize_checkout(
                purchase=pre_reservation,
                success_url='https://example.test/success',
                cancel_url='https://example.test/cancel',
            )

        pre_reservation.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.PAYMENT_FAILED,
        )
        replacement = self.reserve(self.dog, user=self.other_user)
        self.assertEqual(replacement.status, PreReservation.Status.PENDING_PAYMENT)

    def test_failed_retry_reuses_payment_after_closing_old_checkout(self):
        pre_reservation = self.initialize(self.reserve(self.dog))
        old_session_id = pre_reservation.payment.stripe_checkout_session_id
        release_failed_or_expired_checkout(
            session_id=old_session_id,
            expired=False,
        )
        pre_reservation.refresh_from_db()
        pre_reservation.payment.refresh_from_db()

        with patch(
            'reservations.stripe_gateway.retrieve_checkout_session',
            return_value={
                'id': old_session_id,
                'status': 'open',
                'payment_status': 'unpaid',
            },
        ), patch(
            'reservations.stripe_gateway.expire_checkout_session',
        ) as expire:
            prepare_failed_checkout_retry(pre_reservation)

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

        expire.assert_called_once_with(old_session_id)
        self.assertEqual(retried.pk, pre_reservation.pk)
        self.assertEqual(retried.payment.checkout_attempt_number, 2)
        self.assertIsNone(retried.payment.stripe_checkout_session_id)

    def test_paid_session_from_previous_attempt_is_rejected(self):
        pre_reservation = self.reserve(self.dog)
        pre_reservation.status = PreReservation.Status.PAYMENT_FAILED
        pre_reservation.save(update_fields=['status'])
        payment = pre_reservation.payment
        payment.status = Payment.Status.FAILED
        payment.save(update_fields=['status'])
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
        old_session = self.paid_session(retried)
        old_session['id'] = 'cs_old'
        old_session['metadata']['checkout_attempt_number'] = '1'

        with patch(
            'reservations.stripe_gateway.retrieve_checkout_session',
            return_value=old_session,
        ), self.assertRaisesMessage(
            PaymentValidationError,
            'earlier payment attempt',
        ):
            fulfill_checkout_session(old_session['id'])

    def test_reservation_deposit_is_a_second_payment_and_final_label(self):
        reservation = self.initialize(self.accepted_reservation())

        fulfilled = self.fulfill(reservation)

        fulfilled.refresh_from_db()
        fulfilled.pre_reservation.refresh_from_db()
        self.assertEqual(fulfilled.status, Reservation.Status.CONFIRMED)
        self.assertEqual(
            fulfilled.pre_reservation.status,
            PreReservation.Status.CONVERTED_TO_RESERVATION,
        )
        self.assertEqual(fulfilled.payment.status, Payment.Status.PAID)
        self.assertEqual(fulfilled.payment.amount, decimal.Decimal('700.00'))
        self.assertEqual(
            fulfilled.payment.erp_documents.filter(
                kind=ERPDocument.Kind.SALE,
            ).count(),
            1,
        )

    def test_late_payment_queues_full_safety_refund_without_rebooking(self):
        pre_reservation = self.initialize(self.reserve(self.dog))
        session_id = pre_reservation.payment.stripe_checkout_session_id
        release_failed_or_expired_checkout(
            session_id=session_id,
            expired=True,
        )

        with self.captureOnCommitCallbacks(execute=False):
            self.fulfill(pre_reservation)

        pre_reservation.refresh_from_db()
        self.assertEqual(pre_reservation.status, PreReservation.Status.EXPIRED)
        safety_refund = pre_reservation.payment.refunds.get()
        self.assertEqual(
            safety_refund.calculation_type,
            PaymentRefund.CalculationType.FULL_REMAINING,
        )
        self.assertEqual(safety_refund.amount, pre_reservation.payment.amount)
        replacement = self.reserve(self.dog, user=self.other_user)
        self.assertEqual(replacement.status, PreReservation.Status.PENDING_PAYMENT)

    def test_partial_and_cumulative_percentage_refunds_never_exceed_payment(self):
        _, payment = self.settled_pre_payment()
        first = request_refund(
            payment_id=payment.pk,
            calculation_type=PaymentRefund.CalculationType.FIXED,
            fixed_amount=decimal.Decimal('10.00'),
            target_percentage=None,
            reason='First partial refund.',
            requested_by=self.user,
        )
        with patch(
            'reservations.stripe_gateway.find_refund',
            return_value=None,
        ), patch(
            'reservations.stripe_gateway.create_refund',
            return_value={'id': 're_10', 'status': 'succeeded'},
        ):
            process_refund(first.pk)

        second = request_refund(
            payment_id=payment.pk,
            calculation_type=(
                PaymentRefund.CalculationType.TARGET_PERCENTAGE
            ),
            fixed_amount=None,
            target_percentage=decimal.Decimal('50.00'),
            reason='Reach fifty percent.',
            requested_by=self.user,
        )
        self.assertEqual(second.amount, decimal.Decimal('15.00'))
        with patch(
            'reservations.stripe_gateway.find_refund',
            return_value=None,
        ), patch(
            'reservations.stripe_gateway.create_refund',
            return_value={'id': 're_25', 'status': 'succeeded'},
        ):
            process_refund(second.pk)

        final = request_refund(
            payment_id=payment.pk,
            calculation_type=PaymentRefund.CalculationType.FULL_REMAINING,
            fixed_amount=None,
            target_percentage=None,
            reason='Full remaining refund.',
            requested_by=self.user,
            provider_loss_acknowledged=True,
        )
        self.assertEqual(final.amount, decimal.Decimal('25.00'))
        with patch(
            'reservations.stripe_gateway.find_refund',
            return_value=None,
        ), patch(
            'reservations.stripe_gateway.create_refund',
            return_value={'id': 're_50', 'status': 'succeeded'},
        ):
            process_refund(final.pk)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REFUNDED)
        self.assertEqual(
            payment.refunds.filter(
                status=PaymentRefund.Status.SUCCEEDED,
            ).count(),
            3,
        )
        self.assertEqual(
            payment.erp_documents.filter(
                kind=ERPDocument.Kind.CREDIT_NOTE,
            ).count(),
            3,
        )

    def test_refund_requires_loss_acknowledgement_when_net_is_unknown(self):
        _, payment = self.settled_pre_payment()
        payment.provider_net_amount = None
        payment.save(update_fields=['provider_net_amount'])

        with self.assertRaisesMessage(PaymentError, 'Explicitly acknowledge'):
            request_refund(
                payment_id=payment.pk,
                calculation_type=PaymentRefund.CalculationType.FIXED,
                fixed_amount=decimal.Decimal('1.00'),
                target_percentage=None,
                reason='Courtesy.',
                requested_by=self.user,
            )

    def test_failed_refund_cannot_retry_after_balance_is_reallocated(self):
        _, payment = self.settled_pre_payment()
        old = request_refund(
            payment_id=payment.pk,
            calculation_type=PaymentRefund.CalculationType.FULL_REMAINING,
            fixed_amount=None,
            target_percentage=None,
            reason='Old request.',
            requested_by=self.user,
            provider_loss_acknowledged=True,
        )
        old.status = PaymentRefund.Status.FAILED
        old.next_retry_at = None
        old.save(update_fields=['status', 'next_retry_at'])
        replacement = request_refund(
            payment_id=payment.pk,
            calculation_type=PaymentRefund.CalculationType.FULL_REMAINING,
            fixed_amount=None,
            target_percentage=None,
            reason='Replacement request.',
            requested_by=self.user,
            provider_loss_acknowledged=True,
        )

        with patch(
            'reservations.stripe_gateway.create_refund',
        ) as create_refund:
            result = process_refund(old.pk)

        self.assertEqual(result.status, PaymentRefund.Status.FAILED)
        self.assertIn('available payment balance', result.last_error)
        create_refund.assert_not_called()
        self.assertEqual(replacement.status, PaymentRefund.Status.PENDING)

    def test_ambiguous_refund_reuses_same_logical_request(self):
        _, payment = self.settled_pre_payment()
        payment_refund = request_refund(
            payment_id=payment.pk,
            calculation_type=PaymentRefund.CalculationType.FIXED,
            fixed_amount=decimal.Decimal('10.00'),
            target_percentage=None,
            reason='Ambiguous request.',
            requested_by=self.user,
        )
        with patch(
            'reservations.stripe_gateway.find_refund',
            return_value=None,
        ), patch(
            'reservations.stripe_gateway.create_refund',
            side_effect=stripe.APIConnectionError('connection lost'),
        ):
            result = process_refund(payment_refund.pk)

        self.assertEqual(result.status, PaymentRefund.Status.PENDING)
        self.assertIn('uncertain', result.last_error)

        with patch(
            'reservations.stripe_gateway.find_refund',
            return_value={'id': 're_existing', 'status': 'succeeded'},
        ), patch(
            'reservations.stripe_gateway.create_refund',
        ) as create_refund:
            result = process_refund(payment_refund.pk)

        self.assertEqual(result.status, PaymentRefund.Status.SUCCEEDED)
        self.assertEqual(result.stripe_refund_id, 're_existing')
        create_refund.assert_not_called()

    def test_duplicate_refund_update_does_not_notify_twice(self):
        _, payment = self.settled_pre_payment()
        payment_refund = payment.refunds.create(
            calculation_type=PaymentRefund.CalculationType.FIXED,
            amount=decimal.Decimal('10.00'),
            reason='Already complete.',
            status=PaymentRefund.Status.SUCCEEDED,
            stripe_refund_id='re_complete',
            succeeded_at=timezone.now(),
        )

        with patch(
            'reservations.services.payment.notify_refund_succeeded',
        ) as notify:
            reconcile_refund_webhook(
                {
                    'id': 're_complete',
                    'status': 'succeeded',
                    'metadata': {
                        'payment_refund_id': str(payment_refund.public_id),
                    },
                }
            )

        notify.assert_not_called()

    def test_unpaid_completed_webhook_is_recorded_without_fulfillment(self):
        pre_reservation = self.initialize(self.reserve(self.dog))
        event = {
            'id': 'evt_unpaid_complete',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': pre_reservation.payment.stripe_checkout_session_id,
                    'payment_status': 'unpaid',
                }
            },
        }

        process_stripe_webhook(event)

        pre_reservation.refresh_from_db()
        self.assertEqual(
            pre_reservation.status,
            PreReservation.Status.PENDING_PAYMENT,
        )
        self.assertTrue(
            ProcessedStripeEvent.objects.filter(
                event_id='evt_unpaid_complete',
            ).exists()
        )
