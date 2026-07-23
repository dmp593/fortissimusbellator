from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from reservations.stripe_gateway import (
    CHECKOUT_INTEGRATION_IDENTIFIER,
    create_checkout_session,
)


@override_settings(STRIPE_SECRET_KEY='rk_test_example')
class CheckoutGatewayTests(SimpleTestCase):
    @patch('reservations.stripe_gateway.stripe.checkout.Session.create')
    def test_checkout_uses_dynamic_payment_methods(self, create_session):
        reservation = SimpleNamespace(
            public_id=uuid4(),
            customer_email='customer@example.com',
            currency='EUR',
            total_amount=Decimal('50.00'),
            target_name='Bella',
        )

        create_checkout_session(
            reservation=reservation,
            success_url='https://example.test/success',
            cancel_url='https://example.test/cancel',
        )

        parameters = create_session.call_args.kwargs
        self.assertNotIn('payment_method_types', parameters)
        self.assertEqual(
            parameters['integration_identifier'],
            CHECKOUT_INTEGRATION_IDENTIFIER,
        )
        self.assertRegex(
            CHECKOUT_INTEGRATION_IDENTIFIER,
            r'^fortissimusbellator-pre-reservation-[a-z]{8}$',
        )
