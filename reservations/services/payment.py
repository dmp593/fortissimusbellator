import decimal
import logging
from datetime import datetime, timedelta, timezone as datetime_timezone

import stripe
from django.db import IntegrityError, transaction
from django.utils import timezone

from reservations.exceptions import PaymentError, PaymentValidationError
from reservations.models import (
    ERPDocument,
    Payment,
    PreReservation,
    ProcessedStripeEvent,
)
from reservations.services.reservation import (
    cancel_by_admin,
    cancel_by_user,
    ensure_sale_erp_document,
    mark_payment_setup_failed,
)
from reservations.services.notifications import (
    notify_late_payment_refund_queued,
    notify_payment_confirmed,
)
from reservations import stripe_gateway


logger = logging.getLogger(__name__)


def initialize_checkout(*, reservation, success_url: str, cancel_url: str):
    payment = reservation.payment
    if payment.status == Payment.Status.PENDING and payment.stripe_checkout_url:
        return payment.stripe_checkout_url
    if reservation.status != PreReservation.Status.PENDING_PAYMENT:
        raise PaymentError('This pre-reservation is not awaiting payment.')

    try:
        session = stripe_gateway.create_checkout_session(
            reservation=reservation,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except (stripe.APIConnectionError, stripe.APIError) as exc:
        _record_ambiguous_checkout_error(payment.pk, exc)
        raise PaymentError(
            'Payment setup is temporarily unavailable. Your place remains held.'
        ) from exc
    except (stripe.StripeError, PaymentError) as exc:
        mark_payment_setup_failed(reservation.pk, _safe_error(exc))
        raise PaymentError('Unable to initialize payment.') from exc

    session_id = stripe_gateway.value(session, 'id')
    checkout_url = stripe_gateway.value(session, 'url')
    expires_at = _from_timestamp(stripe_gateway.value(session, 'expires_at'))
    if not session_id or not checkout_url:
        _record_ambiguous_checkout_error(
            payment.pk,
            PaymentValidationError('Stripe returned an incomplete Checkout Session.'),
        )
        raise PaymentError(
            'Payment setup is temporarily unavailable. Your place remains held.'
        )

    reservation_closed = False
    with transaction.atomic():
        # Always lock the reservation before its payment to keep lifecycle
        # transitions in a consistent order across checkout and webhooks.
        reservation = PreReservation.objects.select_for_update().get(
            pk=reservation.pk
        )
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if reservation.status != PreReservation.Status.PENDING_PAYMENT:
            reservation_closed = True
        else:
            payment.status = Payment.Status.PENDING
            payment.stripe_checkout_session_id = session_id
            payment.stripe_checkout_url = checkout_url
            payment.stripe_checkout_expires_at = expires_at
            payment.last_error = ''
            payment.save(
                update_fields=[
                    'status',
                    'stripe_checkout_session_id',
                    'stripe_checkout_url',
                    'stripe_checkout_expires_at',
                    'last_error',
                    'updated_at',
                ]
            )
            reservation.hold_expires_at = (
                expires_at + timedelta(minutes=10) if expires_at else None
            )
            reservation.save(update_fields=['hold_expires_at', 'updated_at'])

    if reservation_closed:
        try:
            stripe_gateway.expire_checkout_session(session_id)
        except stripe.StripeError:
            logger.exception(
                'Unable to expire checkout for closed reservation',
                extra={'reservation_id': str(reservation.public_id)},
            )
        raise PaymentError('This pre-reservation is no longer payable.')

    return checkout_url


def fulfill_checkout_session(session_id: str) -> PreReservation:
    session = stripe_gateway.retrieve_checkout_session(session_id)
    if stripe_gateway.value(session, 'payment_status') != 'paid':
        raise PaymentValidationError('Stripe has not confirmed this payment.')

    reservation_public_id = (
        stripe_gateway.value(session, 'client_reference_id')
        or _metadata_value(session, 'pre_reservation_id')
    )
    if not reservation_public_id:
        raise PaymentValidationError('Checkout Session has no reservation reference.')

    with transaction.atomic():
        try:
            reservation = (
                PreReservation.objects.select_for_update()
                .get(public_id=reservation_public_id)
            )
        except (PreReservation.DoesNotExist, ValueError) as exc:
            raise PaymentValidationError(
                'Checkout Session references an unknown reservation.'
            ) from exc

        payment = Payment.objects.select_for_update().get(
            reservation=reservation
        )
        _validate_paid_session(session, reservation, payment)

        if payment.status in {
            Payment.Status.PAID,
            Payment.Status.REFUND_PENDING,
            Payment.Status.REFUND_FAILED,
            Payment.Status.REFUNDED,
        }:
            return reservation

        now = timezone.now()
        closed_statuses = {
            PreReservation.Status.PAYMENT_FAILED,
            PreReservation.Status.EXPIRED,
            PreReservation.Status.CANCELLED_BY_USER,
            PreReservation.Status.CANCELLED_BY_ADMIN,
        }
        if reservation.status in closed_statuses:
            payment.status = Payment.Status.REFUND_PENDING
            payment.stripe_checkout_session_id = stripe_gateway.value(session, 'id')
            payment.stripe_payment_intent_id = _object_id(
                stripe_gateway.value(session, 'payment_intent')
            )
            payment.stripe_checkout_url = ''
            payment.paid_at = now
            payment.refund_next_retry_at = now
            payment.last_error = (
                'Payment arrived after reservation capacity was released; '
                'automatic refund queued.'
            )
            payment.save(
                update_fields=[
                    'status',
                    'stripe_checkout_session_id',
                    'stripe_payment_intent_id',
                    'stripe_checkout_url',
                    'paid_at',
                    'refund_next_retry_at',
                    'last_error',
                    'updated_at',
                ]
            )
            ensure_sale_erp_document(reservation)
            transaction.on_commit(
                lambda reservation_id=reservation.pk: notify_late_payment_refund_queued(
                    PreReservation.objects.get(pk=reservation_id)
                )
            )
            return reservation

        payment.status = Payment.Status.PAID
        payment.stripe_checkout_session_id = stripe_gateway.value(session, 'id')
        payment.stripe_payment_intent_id = _object_id(
            stripe_gateway.value(session, 'payment_intent')
        )
        payment.stripe_checkout_url = ''
        payment.paid_at = now
        payment.last_error = ''
        payment.save(
            update_fields=[
                'status',
                'stripe_checkout_session_id',
                'stripe_payment_intent_id',
                'stripe_checkout_url',
                'paid_at',
                'last_error',
                'updated_at',
            ]
        )

        if reservation.status == PreReservation.Status.PENDING_PAYMENT:
            reservation.status = PreReservation.Status.CONFIRMED
            reservation.confirmed_at = now
            reservation.save(
                update_fields=['status', 'confirmed_at', 'updated_at']
            )

        # Payment and the durable pending ERP task commit together.
        ensure_sale_erp_document(reservation)
        transaction.on_commit(
            lambda reservation_id=reservation.pk: notify_payment_confirmed(
                PreReservation.objects.get(pk=reservation_id)
            )
        )

    return reservation


def process_stripe_webhook(event):
    event_id = stripe_gateway.value(event, 'id')
    event_type = stripe_gateway.value(event, 'type')
    if not event_id or not event_type:
        raise PaymentValidationError('Invalid Stripe event.')
    if ProcessedStripeEvent.objects.filter(event_id=event_id).exists():
        return

    event_object = stripe_gateway.value(
        stripe_gateway.value(event, 'data', {}),
        'object',
        {},
    )
    reservation = None
    if event_type == 'checkout.session.completed' and stripe_gateway.value(
        event_object, 'payment_status'
    ) != 'paid':
        # Delayed payment methods confirm through async_payment_succeeded.
        reservation = None
    elif event_type in {
        'checkout.session.completed',
        'checkout.session.async_payment_succeeded',
    }:
        reservation = fulfill_checkout_session(
            stripe_gateway.value(event_object, 'id')
        )
    elif event_type in {
        'checkout.session.expired',
        'checkout.session.async_payment_failed',
    }:
        reservation = release_failed_or_expired_checkout(
            session_id=stripe_gateway.value(event_object, 'id'),
            expired=event_type == 'checkout.session.expired',
        )

    try:
        ProcessedStripeEvent.objects.create(
            event_id=event_id,
            event_type=event_type,
            reservation=reservation,
        )
    except IntegrityError:
        pass


@transaction.atomic
def release_failed_or_expired_checkout(*, session_id: str, expired: bool):
    try:
        payment_id, reservation_id = Payment.objects.values_list(
            'pk',
            'reservation_id',
        ).get(
            stripe_checkout_session_id=session_id,
        )
    except Payment.DoesNotExist:
        return None

    reservation = PreReservation.objects.select_for_update().get(pk=reservation_id)
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if payment.status in {
        Payment.Status.PAID,
        Payment.Status.REFUND_PENDING,
        Payment.Status.REFUND_FAILED,
        Payment.Status.REFUNDED,
    }:
        return reservation
    if (
        reservation.status != PreReservation.Status.PENDING_PAYMENT
        or payment.status
        not in {Payment.Status.INITIALIZING, Payment.Status.PENDING}
    ):
        return reservation

    now = timezone.now()
    payment.status = Payment.Status.FAILED
    payment.failed_at = now
    payment.stripe_checkout_url = ''
    payment.save(
        update_fields=['status', 'failed_at', 'stripe_checkout_url', 'updated_at']
    )
    reservation.status = (
        PreReservation.Status.EXPIRED
        if expired
        else PreReservation.Status.PAYMENT_FAILED
    )
    reservation.save(update_fields=['status', 'updated_at'])
    return reservation


def cancel_customer_reservation(*, reservation: PreReservation, user):
    _close_checkout_before_cancellation(reservation)
    return cancel_by_user(reservation_id=reservation.pk, user=user)


def cancel_staff_reservation(
    *, reservation: PreReservation, admin_user, reason: str
):
    _close_checkout_before_cancellation(reservation)
    return cancel_by_admin(
        reservation_id=reservation.pk,
        admin_user=admin_user,
        reason=reason,
    )


def _close_checkout_before_cancellation(reservation: PreReservation):
    payment = reservation.payment
    if (
        reservation.status == PreReservation.Status.PENDING_PAYMENT
        and payment.status == Payment.Status.INITIALIZING
        and not payment.stripe_checkout_session_id
    ):
        raise PaymentError(
            'Payment setup is still being reconciled. Please try again shortly.'
        )
    if (
        reservation.status == PreReservation.Status.PENDING_PAYMENT
        and payment.stripe_checkout_session_id
    ):
        session = stripe_gateway.retrieve_checkout_session(
            payment.stripe_checkout_session_id
        )
        if stripe_gateway.value(session, 'payment_status') == 'paid':
            fulfill_checkout_session(payment.stripe_checkout_session_id)
        elif stripe_gateway.value(session, 'status') == 'open':
            stripe_gateway.expire_checkout_session(
                payment.stripe_checkout_session_id
            )
        elif stripe_gateway.value(session, 'status') != 'expired':
            raise PaymentError(
                'This payment is still being processed and cannot be cancelled yet.'
            )


def reconcile_pending_payment(payment_id: int):
    payment = Payment.objects.select_related('reservation').get(pk=payment_id)
    if payment.status not in {
        Payment.Status.INITIALIZING,
        Payment.Status.PENDING,
    }:
        return payment.reservation

    if payment.stripe_checkout_session_id:
        session = stripe_gateway.retrieve_checkout_session(
            payment.stripe_checkout_session_id
        )
        if stripe_gateway.value(session, 'payment_status') == 'paid':
            return fulfill_checkout_session(payment.stripe_checkout_session_id)
        if stripe_gateway.value(session, 'status') == 'expired':
            return release_failed_or_expired_checkout(
                session_id=payment.stripe_checkout_session_id,
                expired=True,
            )
        return payment.reservation

    session = stripe_gateway.find_reservation_checkout_session(
        payment.reservation
    )
    if session is not None:
        payment = _attach_discovered_checkout_session(payment.pk, session)
        if stripe_gateway.value(session, 'payment_status') == 'paid':
            return fulfill_checkout_session(payment.stripe_checkout_session_id)
        if stripe_gateway.value(session, 'status') == 'expired':
            return release_failed_or_expired_checkout(
                session_id=payment.stripe_checkout_session_id,
                expired=True,
            )
        if stripe_gateway.value(session, 'status') == 'open':
            stripe_gateway.expire_checkout_session(
                payment.stripe_checkout_session_id
            )
            return release_failed_or_expired_checkout(
                session_id=payment.stripe_checkout_session_id,
                expired=True,
            )
        return payment.reservation

    if (
        payment.reservation.hold_expires_at
        and payment.reservation.hold_expires_at <= timezone.now()
    ):
        return mark_payment_setup_failed(
            payment.reservation_id,
            'Checkout Session was not created before the hold expired.',
        )
    return payment.reservation


@transaction.atomic
def _attach_discovered_checkout_session(payment_id: int, session):
    reservation_id = Payment.objects.values_list(
        'reservation_id',
        flat=True,
    ).get(pk=payment_id)
    reservation = PreReservation.objects.select_for_update().get(pk=reservation_id)
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if payment.stripe_checkout_session_id:
        return payment

    session_id = stripe_gateway.value(session, 'id')
    if not session_id:
        raise PaymentValidationError(
            'A reconciled Checkout Session has no identifier.'
        )
    expires_at = _from_timestamp(stripe_gateway.value(session, 'expires_at'))
    payment.stripe_checkout_session_id = session_id
    payment.stripe_checkout_expires_at = expires_at
    update_fields = [
        'stripe_checkout_session_id',
        'stripe_checkout_expires_at',
        'updated_at',
    ]
    if (
        reservation.status == PreReservation.Status.PENDING_PAYMENT
        and payment.status
        in {Payment.Status.INITIALIZING, Payment.Status.PENDING}
    ):
        payment.status = Payment.Status.PENDING
        payment.stripe_checkout_url = (
            stripe_gateway.value(session, 'url', '') or ''
        )
        payment.last_error = ''
        update_fields.extend(['status', 'stripe_checkout_url', 'last_error'])
    payment.save(update_fields=update_fields)
    if (
        expires_at
        and reservation.status == PreReservation.Status.PENDING_PAYMENT
    ):
        reservation.hold_expires_at = expires_at + timedelta(minutes=10)
        reservation.save(
            update_fields=['hold_expires_at', 'updated_at']
        )
    return payment


def process_refund(payment_id: int):
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        if payment.status == Payment.Status.REFUNDED:
            return payment
        if payment.status not in {
            Payment.Status.REFUND_PENDING,
            Payment.Status.REFUND_FAILED,
        }:
            raise PaymentError('This payment is not awaiting a refund.')
        if not payment.stripe_payment_intent_id:
            payment.status = Payment.Status.REFUND_FAILED
            payment.last_error = 'Missing Stripe PaymentIntent identifier.'
            payment.save(update_fields=['status', 'last_error', 'updated_at'])
            return payment
        payment.refund_attempt_count += 1
        payment.save(update_fields=['refund_attempt_count', 'updated_at'])
        reservation_public_id = PreReservation.objects.values_list(
            'public_id',
            flat=True,
        ).get(pk=payment.reservation_id)
        payment_intent_id = payment.stripe_payment_intent_id

    try:
        if payment.stripe_refund_id:
            refund = stripe_gateway.retrieve_refund(payment.stripe_refund_id)
        else:
            refund = stripe_gateway.find_reservation_refund(
                payment_intent_id=payment_intent_id,
                reservation_public_id=reservation_public_id,
            )
            if refund is None:
                refund = stripe_gateway.create_refund(
                    payment_intent_id=payment_intent_id,
                    reservation_public_id=reservation_public_id,
                    attempt_number=payment.refund_attempt_count,
                )
    except stripe.StripeError as exc:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=payment_id)
            payment.status = Payment.Status.REFUND_FAILED
            payment.last_error = _safe_error(exc)
            payment.refund_next_retry_at = timezone.now() + timedelta(minutes=10)
            payment.save(
                update_fields=[
                    'status',
                    'last_error',
                    'refund_next_retry_at',
                    'updated_at',
                ]
            )
        return payment

    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        refund_id = stripe_gateway.value(refund, 'id')
        refund_status = stripe_gateway.value(refund, 'status')
        if not refund_id:
            payment.status = Payment.Status.REFUND_FAILED
            payment.last_error = 'Stripe returned a refund without an identifier.'
            payment.refund_next_retry_at = timezone.now() + timedelta(minutes=10)
            payment.save(
                update_fields=[
                    'status',
                    'last_error',
                    'refund_next_retry_at',
                    'updated_at',
                ]
            )
            return payment

        payment.stripe_refund_id = refund_id
        if refund_status == 'succeeded':
            payment.status = Payment.Status.REFUNDED
            payment.refunded_at = timezone.now()
            payment.refund_next_retry_at = None
            payment.last_error = ''
        elif refund_status in {'failed', 'canceled'}:
            payment.status = Payment.Status.REFUND_FAILED
            payment.stripe_refund_id = None
            payment.refund_next_retry_at = timezone.now() + timedelta(minutes=10)
            failure_reason = stripe_gateway.value(refund, 'failure_reason')
            payment.last_error = (
                f'Stripe refund {refund_status}: {failure_reason or "unknown reason"}'
            )[:2000]
        else:
            payment.status = Payment.Status.REFUND_PENDING
            payment.refund_next_retry_at = timezone.now() + timedelta(minutes=10)
            payment.last_error = ''
        payment.save(
            update_fields=[
                'status',
                'stripe_refund_id',
                'refunded_at',
                'refund_next_retry_at',
                'last_error',
                'updated_at',
            ]
        )
        if payment.status == Payment.Status.REFUNDED:
            ERPDocument.objects.get_or_create(
                reservation_id=payment.reservation_id,
                kind=ERPDocument.Kind.CREDIT_NOTE,
                defaults={
                    'external_reference': f'PRE-{reservation_public_id}-CN',
                },
            )
    return payment


def _validate_paid_session(session, reservation, payment):
    session_id = stripe_gateway.value(session, 'id')
    if (
        payment.stripe_checkout_session_id
        and payment.stripe_checkout_session_id != session_id
    ):
        raise PaymentValidationError('Checkout Session does not match payment.')
    currency = (stripe_gateway.value(session, 'currency') or '').upper()
    amount_total = stripe_gateway.value(session, 'amount_total')
    expected_cents = int(reservation.total_amount * 100)
    if currency != reservation.currency or amount_total != expected_cents:
        raise PaymentValidationError('Paid amount or currency does not match.')
    metadata_id = _metadata_value(session, 'pre_reservation_id')
    if metadata_id and metadata_id != str(reservation.public_id):
        raise PaymentValidationError('Checkout metadata does not match reservation.')
    if payment.amount != reservation.total_amount:
        raise PaymentValidationError('Stored payment amount does not match reservation.')


def _record_ambiguous_checkout_error(payment_id: int, exc: Exception):
    Payment.objects.filter(pk=payment_id).update(last_error=_safe_error(exc))


def _metadata_value(session, key: str):
    metadata = stripe_gateway.value(session, 'metadata', {}) or {}
    return stripe_gateway.value(metadata, key)


def _object_id(value):
    if not value:
        return None
    return stripe_gateway.value(value, 'id', value)


def _from_timestamp(value):
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=datetime_timezone.utc)


def _safe_error(exc: Exception) -> str:
    return f'{exc.__class__.__name__}: {str(exc)}'[:2000]
