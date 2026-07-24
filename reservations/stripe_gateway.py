import decimal
from datetime import datetime, timedelta, timezone as datetime_timezone

import stripe
from django.conf import settings

from .exceptions import PaymentConfigurationError
from .policies import checkout_duration_minutes


CHECKOUT_INTEGRATION_IDENTIFIER = 'fortissimusbellator-reservations-v2'


def _configure():
    if not settings.STRIPE_SECRET_KEY:
        raise PaymentConfigurationError('Stripe is not configured.')
    stripe.api_key = settings.STRIPE_SECRET_KEY


def create_checkout_session(*, payment, success_url: str, cancel_url: str):
    _configure()
    purchase = payment.purchase
    purchase_type, title, description, customer_email = _purchase_details(
        payment,
    )
    expires_at = datetime.now(tz=datetime_timezone.utc) + timedelta(
        minutes=checkout_duration_minutes(),
    )
    metadata = {
        'local_payment_id': str(payment.pk),
        'purchase_type': purchase_type,
        'purchase_public_id': str(purchase.public_id),
        'checkout_attempt_number': str(payment.checkout_attempt_number),
    }
    return stripe.checkout.Session.create(
        mode='payment',
        integration_identifier=CHECKOUT_INTEGRATION_IDENTIFIER,
        customer_email=customer_email,
        client_reference_id=str(purchase.public_id),
        metadata=metadata,
        payment_intent_data={'metadata': metadata},
        line_items=[
            {
                'price_data': {
                    'currency': payment.currency.lower(),
                    'unit_amount': int(payment.amount * 100),
                    'product_data': {
                        'name': title,
                        'description': description,
                    },
                },
                'quantity': 1,
            }
        ],
        success_url=success_url,
        cancel_url=cancel_url,
        expires_at=int(expires_at.timestamp()),
        idempotency_key=(
            f'checkout:{purchase_type}:{purchase.public_id}:'
            f'{payment.checkout_attempt_number}'
        ),
    )


def retrieve_checkout_session(session_id: str):
    _configure()
    return stripe.checkout.Session.retrieve(session_id)


def find_checkout_session(payment):
    _configure()
    purchase = payment.purchase
    sessions = stripe.checkout.Session.list(
        created={
            'gte': int(payment.created_at.timestamp()) - 60,
            'lte': int(
                (
                    payment.created_at + timedelta(days=1, minutes=10)
                ).timestamp()
            ),
        },
        limit=100,
    )
    expected_payment_id = str(payment.pk)
    expected_reference = str(purchase.public_id)
    expected_attempt = str(payment.checkout_attempt_number)
    iterator = (
        sessions.auto_paging_iter()
        if hasattr(sessions, 'auto_paging_iter')
        else value(sessions, 'data', sessions) or ()
    )
    for session in iterator:
        metadata = value(session, 'metadata', {}) or {}
        attempt = value(metadata, 'checkout_attempt_number')
        attempt_matches = attempt == expected_attempt
        if expected_attempt == '1' and not attempt:
            attempt_matches = True
        reference_matches = (
            value(metadata, 'local_payment_id') == expected_payment_id
            or (
                value(session, 'client_reference_id') == expected_reference
                and (
                    value(metadata, 'purchase_public_id') == expected_reference
                    or value(metadata, 'pre_reservation_id')
                    == expected_reference
                )
            )
        )
        if attempt_matches and reference_matches:
            return session
    return None


def expire_checkout_session(session_id: str):
    _configure()
    return stripe.checkout.Session.expire(session_id)


def create_refund(*, payment, payment_refund):
    _configure()
    purchase = payment.purchase
    return stripe.Refund.create(
        payment_intent=payment.stripe_payment_intent_id,
        amount=int(payment_refund.amount * 100),
        metadata={
            'local_payment_id': str(payment.pk),
            'payment_refund_id': str(payment_refund.public_id),
            'purchase_public_id': str(purchase.public_id),
        },
        # One logical refund keeps one key across retries. An ambiguous network
        # response must never create a second Stripe refund.
        idempotency_key=f'payment-refund:{payment_refund.public_id}',
    )


def retrieve_refund(refund_id: str):
    _configure()
    return stripe.Refund.retrieve(refund_id)


def find_refund(*, payment, payment_refund):
    _configure()
    refunds = stripe.Refund.list(
        payment_intent=payment.stripe_payment_intent_id,
        limit=100,
    )
    expected_id = str(payment_refund.public_id)
    for refund in value(refunds, 'data', refunds) or ():
        metadata = value(refund, 'metadata', {}) or {}
        if (
            value(metadata, 'payment_refund_id') == expected_id
            and value(refund, 'status') not in {'failed', 'canceled'}
        ):
            return refund
    return None


def retrieve_payment_financials(payment_intent_id: str) -> dict:
    _configure()
    payment_intent = stripe.PaymentIntent.retrieve(
        payment_intent_id,
        expand=['latest_charge.balance_transaction'],
    )
    charge = value(payment_intent, 'latest_charge')
    if isinstance(charge, str):
        charge = stripe.Charge.retrieve(
            charge,
            expand=['balance_transaction'],
        )
    if not charge:
        return {}

    balance_transaction = value(charge, 'balance_transaction')
    if isinstance(balance_transaction, str):
        balance_transaction = stripe.BalanceTransaction.retrieve(
            balance_transaction,
        )
    result = {'charge_id': value(charge, 'id')}
    if balance_transaction:
        result.update(
            {
                'fee_amount': _from_minor_units(
                    value(balance_transaction, 'fee'),
                ),
                'net_amount': _from_minor_units(
                    value(balance_transaction, 'net'),
                ),
            }
        )
    return result


def construct_webhook_event(payload: bytes, signature: str):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise PaymentConfigurationError(
            'Stripe webhook signing is not configured.'
        )
    return stripe.Webhook.construct_event(
        payload,
        signature,
        settings.STRIPE_WEBHOOK_SECRET,
    )


def value(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _purchase_details(payment):
    if payment.pre_reservation_id:
        purchase = payment.pre_reservation
        return (
            'pre_reservation',
            f'Pre-reservation: {purchase.target_name}',
            'Pre-reservation fee. Refunds are discretionary under the terms.',
            purchase.customer_email,
        )

    purchase = payment.animal_reservation
    return (
        'reservation',
        f'Reservation deposit: {purchase.target_name}',
        (
            'Reservation deposit after an accepted pre-reservation.'
            if purchase.pre_reservation_id
            else 'Reservation deposit recorded directly by the breeder.'
        ),
        purchase.customer_email,
    )


def _from_minor_units(value):
    if value is None:
        return None
    return (
        decimal.Decimal(value) / decimal.Decimal('100')
    ).quantize(decimal.Decimal('0.01'))
