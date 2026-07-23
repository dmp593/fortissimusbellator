from datetime import datetime, timedelta, timezone as datetime_timezone
from secrets import choice
from string import ascii_lowercase

import stripe
from django.conf import settings

from .exceptions import PaymentConfigurationError
from .policies import checkout_duration_minutes


CHECKOUT_INTEGRATION_IDENTIFIER = (
    'fortissimusbellator-pre-reservation-'
    + ''.join(choice(ascii_lowercase) for _ in range(8))
)


def _configure():
    if not settings.STRIPE_SECRET_KEY:
        raise PaymentConfigurationError('Stripe is not configured.')
    stripe.api_key = settings.STRIPE_SECRET_KEY


def create_checkout_session(
    *,
    reservation,
    success_url: str,
    cancel_url: str,
):
    _configure()
    checkout_minutes = checkout_duration_minutes()
    expires_at = datetime.now(tz=datetime_timezone.utc) + timedelta(
        minutes=checkout_minutes
    )
    metadata = {'pre_reservation_id': str(reservation.public_id)}
    return stripe.checkout.Session.create(
        mode='payment',
        integration_identifier=CHECKOUT_INTEGRATION_IDENTIFIER,
        customer_email=reservation.customer_email,
        client_reference_id=str(reservation.public_id),
        metadata=metadata,
        payment_intent_data={'metadata': metadata},
        line_items=[
            {
                'price_data': {
                    'currency': reservation.currency.lower(),
                    'unit_amount': int(reservation.total_amount * 100),
                    'product_data': {
                        'name': f'Pre-reservation: {reservation.target_name}',
                        'description': (
                            'Non-refundable pre-reservation fee when '
                            'cancelled by the customer.'
                        ),
                    },
                },
                'quantity': 1,
            }
        ],
        success_url=success_url,
        cancel_url=cancel_url,
        expires_at=int(expires_at.timestamp()),
        idempotency_key=f'pre-reservation-checkout:{reservation.public_id}',
    )


def retrieve_checkout_session(session_id: str):
    _configure()
    return stripe.checkout.Session.retrieve(session_id)


def find_reservation_checkout_session(reservation):
    _configure()
    created_at = int(reservation.created_at.timestamp())
    sessions = stripe.checkout.Session.list(
        created={
            'gte': created_at - 60,
            'lte': int(reservation.hold_expires_at.timestamp()),
        },
        limit=100,
    )
    expected_reference = str(reservation.public_id)
    iterator = (
        sessions.auto_paging_iter()
        if hasattr(sessions, 'auto_paging_iter')
        else value(sessions, 'data', sessions) or ()
    )
    for session in iterator:
        metadata = value(session, 'metadata', {}) or {}
        if (
            value(session, 'client_reference_id') == expected_reference
            or value(metadata, 'pre_reservation_id') == expected_reference
        ):
            return session
    return None


def expire_checkout_session(session_id: str):
    _configure()
    return stripe.checkout.Session.expire(session_id)


def create_refund(
    *, payment_intent_id: str, reservation_public_id, attempt_number: int
):
    _configure()
    return stripe.Refund.create(
        payment_intent=payment_intent_id,
        metadata={'pre_reservation_id': str(reservation_public_id)},
        idempotency_key=(
            f'pre-reservation-refund:{reservation_public_id}:{attempt_number}'
        ),
    )


def retrieve_refund(refund_id: str):
    _configure()
    return stripe.Refund.retrieve(refund_id)


def find_reservation_refund(*, payment_intent_id: str, reservation_public_id):
    _configure()
    refunds = stripe.Refund.list(
        payment_intent=payment_intent_id,
        limit=100,
    )
    expected_reference = str(reservation_public_id)
    for refund in value(refunds, 'data', refunds) or ():
        metadata = value(refund, 'metadata', {}) or {}
        if (
            value(metadata, 'pre_reservation_id') == expected_reference
            and value(refund, 'status') not in {'failed', 'canceled'}
        ):
            return refund
    return None


def construct_webhook_event(payload: bytes, signature: str):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise PaymentConfigurationError('Stripe webhook signing is not configured.')
    return stripe.Webhook.construct_event(
        payload,
        signature,
        settings.STRIPE_WEBHOOK_SECRET,
    )


def value(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
