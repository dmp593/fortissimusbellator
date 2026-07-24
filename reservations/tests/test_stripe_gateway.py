from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from reservations.stripe_gateway import (
    CHECKOUT_INTEGRATION_IDENTIFIER,
    create_checkout_session,
    create_refund,
)


@override_settings(STRIPE_SECRET_KEY='rk_test_example')
class StripeGatewayTests(SimpleTestCase):
    def payment(self):
        purchase = SimpleNamespace(
            public_id=uuid4(),
            customer_email='customer@example.com',
            target_name='Bella',
        )
        return SimpleNamespace(
            pk=7,
            purchase=purchase,
            pre_reservation=purchase,
            pre_reservation_id=11,
            stripe_payment_intent_id='pi_example',
            currency='EUR',
            amount=Decimal('50.00'),
            checkout_attempt_number=2,
        )

    def direct_reservation_payment(self):
        purchase = SimpleNamespace(
            public_id=uuid4(),
            customer_email='customer@example.com',
            target_name='Bella',
            pre_reservation_id=None,
        )
        return SimpleNamespace(
            pk=8,
            purchase=purchase,
            pre_reservation=None,
            pre_reservation_id=None,
            animal_reservation=purchase,
            animal_reservation_id=12,
            stripe_payment_intent_id='pi_direct_reservation',
            currency='EUR',
            amount=Decimal('500.00'),
            checkout_attempt_number=1,
        )

    @patch('reservations.stripe_gateway.stripe.checkout.Session.create')
    def test_checkout_uses_dynamic_payment_methods_and_attempt_idempotency(
        self,
        create_session,
    ):
        payment = self.payment()

        create_checkout_session(
            payment=payment,
            success_url='https://example.test/success',
            cancel_url='https://example.test/cancel',
        )

        parameters = create_session.call_args.kwargs
        self.assertNotIn('payment_method_types', parameters)
        self.assertEqual(
            parameters['integration_identifier'],
            CHECKOUT_INTEGRATION_IDENTIFIER,
        )
        self.assertEqual(
            parameters['metadata']['checkout_attempt_number'],
            '2',
        )
        self.assertEqual(
            parameters['idempotency_key'],
            f'checkout:pre_reservation:{payment.purchase.public_id}:2',
        )
        self.assertEqual(
            CHECKOUT_INTEGRATION_IDENTIFIER,
            'fortissimusbellator-reservations-v2',
        )

    @patch('reservations.stripe_gateway.stripe.Refund.create')
    def test_refund_idempotency_key_is_stable_across_worker_retries(
        self,
        create,
    ):
        payment = self.payment()
        payment_refund = SimpleNamespace(
            public_id=uuid4(),
            amount=Decimal('10.00'),
            attempt_count=1,
        )

        create_refund(payment=payment, payment_refund=payment_refund)
        first_key = create.call_args.kwargs['idempotency_key']
        payment_refund.attempt_count = 2
        create_refund(payment=payment, payment_refund=payment_refund)
        second_key = create.call_args.kwargs['idempotency_key']

        self.assertEqual(first_key, second_key)
        self.assertEqual(
            first_key,
            f'payment-refund:{payment_refund.public_id}',
        )

    @patch('reservations.stripe_gateway.stripe.checkout.Session.create')
    def test_direct_reservation_checkout_does_not_require_a_pre_reservation(
        self,
        create_session,
    ):
        payment = self.direct_reservation_payment()

        create_checkout_session(
            payment=payment,
            success_url='https://example.test/success',
            cancel_url='https://example.test/cancel',
        )

        parameters = create_session.call_args.kwargs
        product = parameters['line_items'][0]['price_data']['product_data']
        self.assertEqual(product['name'], 'Reservation deposit: Bella')
        self.assertIn('directly by the breeder', product['description'])
        self.assertEqual(
            parameters['idempotency_key'],
            f'checkout:reservation:{payment.purchase.public_id}:1',
        )
