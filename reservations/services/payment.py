import decimal
import logging
from datetime import datetime, timedelta, timezone as datetime_timezone

import stripe
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from reservations import stripe_gateway
from reservations.exceptions import PaymentError, PaymentValidationError
from reservations.models import (
    AnimalSaleCase,
    Charge,
    Payment,
    PaymentRefund,
    PreReservation,
    ProcessedStripeEvent,
    Reservation,
)
from reservations.policies import checkout_duration_minutes
from reservations.services.notifications import (
    notify_late_payment_refund_queued,
    notify_payment_failed,
    notify_pre_reservation_paid,
    notify_refund_succeeded,
    notify_reservation_confirmed,
)
from reservations.services.reservation import (
    cancel_pre_reservation_by_admin,
    cancel_pre_reservation_by_user,
    cancel_reservation_by_admin,
    mark_pre_reservation_payment_setup_failed,
    mark_reservation_payment_setup_failed,
    try_accept_staff_created_pre_reservation,
)
from reservations.services.ledger import refresh_charge_status, void_charge


logger = logging.getLogger(__name__)
MONEY = decimal.Decimal('0.01')
SETTLED_PAYMENT_STATUSES = (
    Payment.Status.PAID,
    Payment.Status.PARTIALLY_REFUNDED,
    Payment.Status.REFUNDED,
)


def initialize_checkout(*, purchase, success_url: str, cancel_url: str):
    payment = purchase.payment
    if payment.status == Payment.Status.PENDING and payment.stripe_checkout_url:
        return payment.stripe_checkout_url
    if not _purchase_is_awaiting_payment(purchase):
        raise PaymentError(_('This purchase is not awaiting payment.'))
    payment = _mark_checkout_started(payment.pk)

    try:
        session = stripe_gateway.create_checkout_session(
            payment=payment,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except (stripe.APIConnectionError, stripe.APIError) as exc:
        _record_ambiguous_checkout_error(payment.pk, exc)
        raise PaymentError(
            _(
                'Payment setup is temporarily unavailable. Your place '
                'remains held.'
            )
        ) from exc
    except (stripe.StripeError, PaymentError) as exc:
        _mark_payment_setup_failed(payment, _safe_error(exc))
        raise PaymentError(_('Unable to initialize payment.')) from exc

    session_id = stripe_gateway.value(session, 'id')
    checkout_url = stripe_gateway.value(session, 'url')
    expires_at = _from_timestamp(stripe_gateway.value(session, 'expires_at'))
    if not session_id or not checkout_url:
        _record_ambiguous_checkout_error(
            payment.pk,
            PaymentValidationError(
                'Stripe returned an incomplete Checkout Session.'
            ),
        )
        raise PaymentError(
            _(
                'Payment setup is temporarily unavailable. Your place '
                'remains held.'
            )
        )

    purchase_closed = False
    with transaction.atomic():
        payment, purchase = _lock_payment_workflow(payment.pk)
        if not _purchase_is_awaiting_payment(purchase):
            purchase_closed = True
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
                ],
            )
            if isinstance(purchase, PreReservation):
                purchase.hold_expires_at = (
                    expires_at + timedelta(minutes=10)
                    if expires_at
                    else None
                )
                purchase.save(
                    update_fields=['hold_expires_at', 'updated_at'],
                )

    if purchase_closed:
        try:
            stripe_gateway.expire_checkout_session(session_id)
        except stripe.StripeError:
            logger.exception(
                'Unable to expire checkout for a closed purchase',
                extra={'payment_id': payment.pk},
            )
        raise PaymentError(_('This purchase is no longer payable.'))
    return checkout_url


def prepare_failed_checkout_retry(purchase):
    payment = purchase.payment
    if not _purchase_can_retry_payment(purchase, payment):
        raise PaymentError(_('This payment cannot be retried.'))
    if not payment.stripe_checkout_session_id:
        return

    session_id = payment.stripe_checkout_session_id
    try:
        session = stripe_gateway.retrieve_checkout_session(session_id)
        if stripe_gateway.value(session, 'payment_status') == 'paid':
            fulfill_checkout_session(session_id)
            raise PaymentError(
                _('Payment was already received and is being processed.')
            )

        session_status = stripe_gateway.value(session, 'status')
        if session_status == 'open':
            stripe_gateway.expire_checkout_session(session_id)
            return
        if session_status not in {'expired', 'complete'}:
            raise PaymentError(
                _(
                    'The previous payment is still being processed. '
                    'Please try again shortly.'
                )
            )
    except PaymentError:
        raise
    except stripe.StripeError as exc:
        raise PaymentError(
            _(
                'We could not safely close the previous payment attempt. '
                'Please try again shortly.'
            )
        ) from exc


def reconcile_sale_case_checkouts_for_admin(sale_case_id: int):
    payment_ids = list(
        Payment.objects.filter(
            charge__sale_case_id=sale_case_id,
            provider=Payment.Provider.STRIPE,
            status__in=(
                Payment.Status.INITIALIZING,
                Payment.Status.PENDING,
            ),
        ).values_list('pk', flat=True)
    )
    for payment_id in payment_ids:
        _reconcile_case_checkout_for_admin(payment_id)

    if Payment.objects.filter(
        charge__sale_case_id=sale_case_id,
        provider=Payment.Provider.STRIPE,
        status__in=(
            Payment.Status.INITIALIZING,
            Payment.Status.PENDING,
        ),
    ).exists():
        raise PaymentError(
            _(
                'An online payment is still being processed. '
                'Try this administrative operation again shortly.'
            )
        )


def _reconcile_case_checkout_for_admin(payment_id: int):
    payment = Payment.objects.select_related(
        'pre_reservation',
        'animal_reservation',
    ).get(pk=payment_id)
    if (
        not payment.stripe_checkout_session_id
        and payment.checkout_started_at is None
    ):
        _fail_checkout_without_releasing_case(
            payment_id,
            reason='Administrative operation replaced an unstarted checkout.',
        )
        return

    session = _retrieve_admin_checkout(payment)
    if session is None:
        _handle_missing_admin_checkout(payment)
        return

    session_id = stripe_gateway.value(session, 'id')
    if not session_id:
        raise PaymentError(
            _('Stripe returned a checkout without an identifier.')
        )
    if not payment.stripe_checkout_session_id:
        payment = _attach_discovered_checkout_session(payment.pk, session)

    if stripe_gateway.value(session, 'payment_status') == 'paid':
        fulfill_checkout_session(session_id)
        return

    _close_admin_checkout(
        session_id,
        stripe_gateway.value(session, 'status'),
    )
    _fail_checkout_without_releasing_case(
        payment.pk,
        reason='Checkout closed before an administrative workflow change.',
    )


def _retrieve_admin_checkout(payment):
    try:
        return (
            stripe_gateway.retrieve_checkout_session(
                payment.stripe_checkout_session_id,
            )
            if payment.stripe_checkout_session_id
            else stripe_gateway.find_checkout_session(payment)
        )
    except stripe.StripeError as exc:
        raise PaymentError(
            _(
                'The online payment could not be reconciled safely. '
                'Try again before changing this process.'
            )
        ) from exc



def _handle_missing_admin_checkout(payment):
    checkout_may_be_in_progress = (
        payment.checkout_started_at
        and payment.checkout_started_at
        > timezone.now() - timedelta(minutes=2)
    )
    if checkout_may_be_in_progress:
        raise PaymentError(
            _(
                'Payment setup may still be in progress. '
                'Try this administrative operation again shortly.'
            )
        )
    _fail_checkout_without_releasing_case(
        payment.pk,
        reason='No Stripe checkout exists for the recorded attempt.',
    )


def _close_admin_checkout(session_id, session_status):
    if session_status == 'open':
        try:
            stripe_gateway.expire_checkout_session(session_id)
        except stripe.StripeError as exc:
            raise PaymentError(
                _(
                    'The open online payment could not be closed safely. '
                    'Try again before changing this process.'
                )
            ) from exc
    elif session_status != 'expired':
        raise PaymentError(
            _(
                'The online payment is still being processed. '
                'Try this administrative operation again shortly.'
            )
        )


@transaction.atomic
def _fail_checkout_without_releasing_case(payment_id: int, *, reason: str):
    payment, purchase = _lock_payment_workflow(payment_id)
    if payment.status in SETTLED_PAYMENT_STATUSES:
        return purchase
    payment.status = Payment.Status.FAILED
    payment.failed_at = timezone.now()
    payment.last_error = reason[:2000]
    payment.stripe_checkout_url = ''
    payment.save(
        update_fields=[
            'status',
            'failed_at',
            'last_error',
            'stripe_checkout_url',
            'updated_at',
        ],
    )
    if isinstance(purchase, PreReservation):
        if purchase.status == PreReservation.Status.PENDING_PAYMENT:
            purchase.status = PreReservation.Status.PAYMENT_FAILED
    elif purchase.status == Reservation.Status.PENDING_PAYMENT:
        purchase.status = Reservation.Status.PAYMENT_FAILED
    purchase.save(update_fields=['status', 'updated_at'])
    return purchase


@transaction.atomic
def _mark_checkout_started(payment_id: int) -> Payment:
    payment, purchase = _lock_payment_workflow(payment_id)
    if not _purchase_is_awaiting_payment(purchase):
        raise PaymentError(_('This purchase is not awaiting payment.'))
    if payment.checkout_started_at is None:
        payment.checkout_started_at = timezone.now()
        payment.save(
            update_fields=['checkout_started_at', 'updated_at'],
        )
    return payment


def fulfill_checkout_session(session_id: str):
    session = stripe_gateway.retrieve_checkout_session(session_id)
    if stripe_gateway.value(session, 'payment_status') != 'paid':
        raise PaymentValidationError('Stripe has not confirmed this payment.')

    payment_id = _resolve_session_payment_id(session)
    with transaction.atomic():
        try:
            payment, purchase = _lock_payment_workflow(payment_id)
        except (Payment.DoesNotExist, ValueError, TypeError) as exc:
            raise PaymentValidationError(
                'Checkout Session references an unknown payment.'
            ) from exc
        _validate_paid_session(session, payment, purchase)

        if payment.status in SETTLED_PAYMENT_STATUSES:
            if (
                isinstance(purchase, PreReservation)
                and purchase.status == PreReservation.Status.AWAITING_REVIEW
                and purchase.sale_case_id
                and purchase.sale_case.origin
                == AnimalSaleCase.Origin.ADMIN
            ):
                transaction.on_commit(
                    lambda pk=purchase.pk: (
                        try_accept_staff_created_pre_reservation(pk)
                    )
                )
            return purchase

        now = timezone.now()
        payment.status = Payment.Status.PAID
        payment.stripe_checkout_session_id = stripe_gateway.value(session, 'id')
        payment.stripe_payment_intent_id = _object_id(
            stripe_gateway.value(session, 'payment_intent'),
        )
        payment.stripe_checkout_url = ''
        payment.paid_at = now
        payment.failed_at = None
        payment.last_error = ''
        payment.financials_next_retry_at = now
        payment.save(
            update_fields=[
                'status',
                'stripe_checkout_session_id',
                'stripe_payment_intent_id',
                'stripe_checkout_url',
                'paid_at',
                'failed_at',
                'last_error',
                'financials_next_retry_at',
                'updated_at',
            ],
        )
        if payment.charge_id:
            refresh_charge_status(payment.charge_id)

        if not _purchase_is_awaiting_payment(purchase):
            payment.last_error = (
                'Payment arrived after the purchase was closed; a full '
                'safety refund was queued.'
            )
            payment.save(update_fields=['last_error', 'updated_at'])
            payment_refund = _create_refund_request_locked(
                payment=payment,
                calculation_type=PaymentRefund.CalculationType.FULL_REMAINING,
                amount=payment.amount,
                target_percentage=None,
                reason='Automatic safety refund for a late Stripe payment.',
                requested_by=None,
                provider_loss_acknowledged=True,
            )
            _ensure_sale_document(payment)
            transaction.on_commit(
                lambda purchase_pk=purchase.pk,
                is_pre=isinstance(purchase, PreReservation): (
                    notify_late_payment_refund_queued(
                        _get_purchase(purchase_pk, is_pre=is_pre),
                    )
                )
            )
            transaction.on_commit(
                lambda refund_id=payment_refund.pk: process_refund(refund_id)
            )
            return purchase

        if isinstance(purchase, PreReservation):
            purchase.status = PreReservation.Status.AWAITING_REVIEW
            purchase.confirmed_at = now
            purchase.hold_expires_at = None
            purchase.save(
                update_fields=[
                    'status',
                    'confirmed_at',
                    'hold_expires_at',
                    'updated_at',
                ],
            )
            if purchase.sale_case_id:
                AnimalSaleCase.objects.filter(pk=purchase.sale_case_id).update(
                    status=AnimalSaleCase.Status.PRE_RESERVATION,
                    closed_at=None,
                    updated_at=now,
                )
            if (
                purchase.sale_case_id
                and purchase.sale_case.origin
                == AnimalSaleCase.Origin.ADMIN
            ):
                transaction.on_commit(
                    lambda pk=purchase.pk: (
                        try_accept_staff_created_pre_reservation(pk)
                    )
                )
            transaction.on_commit(
                lambda pk=purchase.pk: notify_pre_reservation_paid(
                    PreReservation.objects.get(pk=pk),
                )
            )
        else:
            purchase.status = Reservation.Status.CONFIRMED
            purchase.confirmed_at = now
            purchase.save(
                update_fields=['status', 'confirmed_at', 'updated_at'],
            )
            if purchase.pre_reservation_id:
                pre_reservation = (
                    PreReservation.objects.select_for_update().get(
                        pk=purchase.pre_reservation_id,
                    )
                )
                pre_reservation.status = (
                    PreReservation.Status.CONVERTED_TO_RESERVATION
                )
                pre_reservation.save(update_fields=['status', 'updated_at'])
            if purchase.sale_case_id:
                AnimalSaleCase.objects.filter(pk=purchase.sale_case_id).update(
                    status=AnimalSaleCase.Status.RESERVATION,
                    closed_at=None,
                    updated_at=now,
                )
            transaction.on_commit(
                lambda pk=purchase.pk: notify_reservation_confirmed(
                    Reservation.objects.get(pk=pk),
                )
            )
        _ensure_sale_document(payment)

    refresh_payment_financials(payment.pk)
    return _get_purchase(
        purchase.pk,
        is_pre=isinstance(purchase, PreReservation),
    )


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
    payment = None
    if event_type == 'checkout.session.completed' and stripe_gateway.value(
        event_object,
        'payment_status',
    ) != 'paid':
        pass
    elif event_type in {
        'checkout.session.completed',
        'checkout.session.async_payment_succeeded',
    }:
        purchase = fulfill_checkout_session(
            stripe_gateway.value(event_object, 'id'),
        )
        payment = purchase.payment
    elif event_type in {
        'checkout.session.expired',
        'checkout.session.async_payment_failed',
    }:
        purchase = release_failed_or_expired_checkout(
            session_id=stripe_gateway.value(event_object, 'id'),
            expired=event_type == 'checkout.session.expired',
        )
        payment = purchase.payment if purchase else None
    elif event_type in {'refund.created', 'refund.updated'}:
        payment = reconcile_refund_webhook(event_object)

    try:
        ProcessedStripeEvent.objects.create(
            event_id=event_id,
            event_type=event_type,
            payment=payment,
        )
    except IntegrityError:
        pass


@transaction.atomic
def release_failed_or_expired_checkout(*, session_id: str, expired: bool):
    payment_id = Payment.objects.filter(
        stripe_checkout_session_id=session_id,
    ).values_list('pk', flat=True).first()
    if payment_id is None:
        return None
    payment, purchase = _lock_payment_workflow(payment_id)
    if payment.status in SETTLED_PAYMENT_STATUSES:
        return purchase
    if (
        not _purchase_is_awaiting_payment(purchase)
        or payment.status
        not in {Payment.Status.INITIALIZING, Payment.Status.PENDING}
    ):
        return purchase

    now = timezone.now()
    payment.status = Payment.Status.FAILED
    payment.failed_at = now
    payment.stripe_checkout_url = ''
    payment.save(
        update_fields=[
            'status',
            'failed_at',
            'stripe_checkout_url',
            'updated_at',
        ],
    )
    if isinstance(purchase, PreReservation):
        purchase.status = (
            PreReservation.Status.EXPIRED
            if expired
            else PreReservation.Status.PAYMENT_FAILED
        )
        if purchase.sale_case_id:
            AnimalSaleCase.objects.filter(pk=purchase.sale_case_id).update(
                status=AnimalSaleCase.Status.CLOSED,
                closed_at=now,
                updated_at=now,
            )
        if purchase.charge_id:
            void_charge(
                charge=purchase.charge,
                reason=(
                    'Stripe Checkout Session expired.'
                    if expired
                    else 'Stripe reported a failed payment.'
                ),
            )
    else:
        purchase.status = Reservation.Status.PAYMENT_FAILED
    purchase.save(update_fields=['status', 'updated_at'])
    transaction.on_commit(
        lambda purchase_pk=purchase.pk,
        is_pre=isinstance(purchase, PreReservation),
        did_expire=expired: notify_payment_failed(
            _get_purchase(purchase_pk, is_pre=is_pre),
            expired=did_expire,
        )
    )
    return purchase


def cancel_customer_pre_reservation(*, pre_reservation, user):
    _close_checkout_before_cancellation(pre_reservation)
    from reservations.models import WorkflowClosure
    from reservations.services.closures import record_workflow_closure

    with transaction.atomic():
        result = cancel_pre_reservation_by_user(
            pre_reservation_id=pre_reservation.pk,
            user=user,
        )
        if not result.sale_case.closures.exists():
            record_workflow_closure(
                sale_case=result.sale_case,
                stage=Charge.Stage.PRE_RESERVATION,
                kind=WorkflowClosure.Kind.CANCELLED,
                reason=str(_('Cancelled by customer.')),
                refund_amount=decimal.Decimal('0.00'),
                credit_amount=decimal.Decimal('0.00'),
                created_by=user,
            )
    return result


def cancel_staff_pre_reservation(*, pre_reservation, admin_user, reason: str):
    _close_checkout_before_cancellation(pre_reservation)
    return cancel_pre_reservation_by_admin(
        pre_reservation_id=pre_reservation.pk,
        admin_user=admin_user,
        reason=reason,
    )


def cancel_staff_reservation(*, reservation, admin_user, reason: str):
    _close_checkout_before_cancellation(reservation)
    return cancel_reservation_by_admin(
        reservation_id=reservation.pk,
        admin_user=admin_user,
        reason=reason,
    )


def _close_checkout_before_cancellation(purchase):
    try:
        payment = purchase.payment
    except Payment.DoesNotExist:
        return
    if (
        _purchase_is_awaiting_payment(purchase)
        and payment.status == Payment.Status.INITIALIZING
        and not payment.stripe_checkout_session_id
    ):
        raise PaymentError(
            _(
                'Payment setup is still being reconciled. '
                'Please try again shortly.'
            )
        )
    if (
        _purchase_is_awaiting_payment(purchase)
        and payment.stripe_checkout_session_id
    ):
        session = stripe_gateway.retrieve_checkout_session(
            payment.stripe_checkout_session_id,
        )
        if stripe_gateway.value(session, 'payment_status') == 'paid':
            fulfill_checkout_session(payment.stripe_checkout_session_id)
            raise PaymentError(
                _('Payment was received before the cancellation completed.')
            )
        session_status = stripe_gateway.value(session, 'status')
        if session_status == 'open':
            stripe_gateway.expire_checkout_session(
                payment.stripe_checkout_session_id,
            )
        elif session_status != 'expired':
            raise PaymentError(
                _(
                    'This payment is still being processed and cannot be '
                    'cancelled yet.'
                )
            )


def reconcile_pending_payment(payment_id: int):
    payment = Payment.objects.select_related(
        'pre_reservation',
        'animal_reservation__pre_reservation',
    ).get(pk=payment_id)
    purchase = payment.purchase
    if payment.status not in {
        Payment.Status.INITIALIZING,
        Payment.Status.PENDING,
    }:
        return purchase

    if payment.stripe_checkout_session_id:
        session = stripe_gateway.retrieve_checkout_session(
            payment.stripe_checkout_session_id,
        )
        if stripe_gateway.value(session, 'payment_status') == 'paid':
            return fulfill_checkout_session(payment.stripe_checkout_session_id)
        if stripe_gateway.value(session, 'status') == 'expired':
            return release_failed_or_expired_checkout(
                session_id=payment.stripe_checkout_session_id,
                expired=True,
            )
        return purchase

    session = stripe_gateway.find_checkout_session(payment)
    if session is not None:
        payment = _attach_discovered_checkout_session(payment.pk, session)
        if stripe_gateway.value(session, 'payment_status') == 'paid':
            return fulfill_checkout_session(payment.stripe_checkout_session_id)
        session_status = stripe_gateway.value(session, 'status')
        if session_status == 'expired':
            return release_failed_or_expired_checkout(
                session_id=payment.stripe_checkout_session_id,
                expired=True,
            )
        if session_status == 'open':
            stripe_gateway.expire_checkout_session(
                payment.stripe_checkout_session_id,
            )
            return release_failed_or_expired_checkout(
                session_id=payment.stripe_checkout_session_id,
                expired=True,
            )
        return purchase

    if _payment_reconciliation_deadline(payment) <= timezone.now():
        _mark_payment_setup_failed(
            payment,
            'Checkout Session was not created before the payment hold expired.',
        )
        return _get_purchase(
            purchase.pk,
            is_pre=isinstance(purchase, PreReservation),
        )
    return purchase


@transaction.atomic
def _attach_discovered_checkout_session(payment_id: int, session):
    payment, purchase = _lock_payment_workflow(payment_id)
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
        _purchase_is_awaiting_payment(purchase)
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
        isinstance(purchase, PreReservation)
        and expires_at
        and _purchase_is_awaiting_payment(purchase)
    ):
        purchase.hold_expires_at = expires_at + timedelta(minutes=10)
        purchase.save(update_fields=['hold_expires_at', 'updated_at'])
    return payment


@transaction.atomic
def request_refund(
    *,
    payment_id: int,
    calculation_type: str,
    reason: str,
    requested_by,
    fixed_amount=None,
    target_percentage=None,
    provider_loss_acknowledged: bool = False,
    closure=None,
    transfer=None,
) -> PaymentRefund:
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if payment.status not in {
        Payment.Status.PAID,
        Payment.Status.PARTIALLY_REFUNDED,
    }:
        raise PaymentError(_('This payment cannot be refunded.'))
    if (
        payment.provider == Payment.Provider.STRIPE
        and not payment.stripe_payment_intent_id
    ):
        raise PaymentError(_('The Stripe payment identifier is missing.'))
    if payment.provider == Payment.Provider.COMPLIMENTARY:
        raise PaymentError(_('A zero-value payment cannot be refunded.'))

    amount, percentage = _calculate_refund_amount(
        payment=payment,
        calculation_type=calculation_type,
        fixed_amount=fixed_amount,
        target_percentage=target_percentage,
    )
    payment_refund = _create_refund_request_locked(
        payment=payment,
        calculation_type=calculation_type,
        amount=amount,
        target_percentage=percentage,
        reason=reason,
        requested_by=requested_by,
        provider_loss_acknowledged=provider_loss_acknowledged,
        closure=closure,
        transfer=transfer,
    )
    if payment.provider != Payment.Provider.STRIPE:
        _complete_manual_refund_locked(payment, payment_refund)
    return payment_refund


def _calculate_refund_amount(
    *,
    payment,
    calculation_type,
    fixed_amount,
    target_percentage,
):
    committed = payment.committed_refund_amount
    if calculation_type == PaymentRefund.CalculationType.FIXED:
        if fixed_amount is None:
            raise PaymentError(_('Enter the refund amount.'))
        amount = _money(fixed_amount)
        percentage = None
    elif calculation_type == PaymentRefund.CalculationType.TARGET_PERCENTAGE:
        if target_percentage is None:
            raise PaymentError(_('Enter the target refund percentage.'))
        percentage = _money(target_percentage)
        if percentage <= 0 or percentage > 100:
            raise PaymentError(
                _('The target refund percentage must be between 0 and 100.')
            )
        target = _money(
            payment.amount * percentage / decimal.Decimal('100'),
        )
        amount = target - committed
    elif calculation_type == PaymentRefund.CalculationType.FULL_REMAINING:
        amount = payment.amount - committed
        percentage = None
    else:
        raise PaymentError(_('Choose a valid refund calculation.'))

    if amount <= 0:
        raise PaymentError(
            _('The selected refund does not leave an amount to return.')
        )
    if amount > payment.refundable_amount:
        raise PaymentError(
            _('The refund cannot exceed the uncommitted payment amount.')
        )
    return amount, percentage


def _create_refund_request_locked(
    *,
    payment,
    calculation_type,
    amount,
    target_percentage,
    reason,
    requested_by,
    provider_loss_acknowledged,
    closure=None,
    transfer=None,
):
    projected_total = payment.committed_refund_amount + amount
    provider_net = payment.provider_net_amount
    loss_possible = payment.provider == Payment.Provider.STRIPE and (
        provider_net is None or projected_total > provider_net
    )
    if loss_possible and not provider_loss_acknowledged:
        raise PaymentError(
            _(
                'This refund may exceed the Stripe net amount retained. '
                'Explicitly acknowledge the processing-cost loss.'
            )
        )
    return PaymentRefund.objects.create(
        payment=payment,
        calculation_type=calculation_type,
        amount=amount,
        target_percentage=target_percentage,
        provider_fee_amount_snapshot=payment.provider_fee_amount,
        provider_net_amount_snapshot=payment.provider_net_amount,
        provider_loss_acknowledged=provider_loss_acknowledged,
        reason=reason.strip(),
        requested_by=requested_by,
        closure=closure,
        transfer=transfer,
        processing_method=(
            PaymentRefund.ProcessingMethod.STRIPE
            if payment.provider == Payment.Provider.STRIPE
            else PaymentRefund.ProcessingMethod.MANUAL
        ),
        status=(
            PaymentRefund.Status.PENDING
            if payment.provider == Payment.Provider.STRIPE
            else PaymentRefund.Status.SUCCEEDED
        ),
        next_retry_at=(
            timezone.now()
            if payment.provider == Payment.Provider.STRIPE
            else None
        ),
    )


def _complete_manual_refund_locked(payment, payment_refund):
    now = timezone.now()
    payment_refund.succeeded_at = now
    payment_refund.save(update_fields=['succeeded_at', 'updated_at'])
    succeeded_total = payment.refunds.filter(
        status=PaymentRefund.Status.SUCCEEDED,
    ).aggregate(total=Sum('amount'))['total'] or decimal.Decimal('0.00')
    payment.status = (
        Payment.Status.REFUNDED
        if succeeded_total >= payment.amount
        else Payment.Status.PARTIALLY_REFUNDED
    )
    payment.save(update_fields=['status', 'updated_at'])
    if payment.charge_id:
        refresh_charge_status(payment.charge_id)
    _ensure_refund_document(payment_refund)
    transaction.on_commit(
        lambda pk=payment_refund.pk: notify_refund_succeeded(
            PaymentRefund.objects.select_related(
                'payment__pre_reservation',
                'payment__animal_reservation__pre_reservation',
                'payment__charge__sale_case',
            ).get(pk=pk),
        )
    )


def process_refund(refund_id: int):
    reference = PaymentRefund.objects.only('payment_id').get(pk=refund_id)
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(
            pk=reference.payment_id,
        )
        payment_refund = (
            PaymentRefund.objects.select_for_update()
            .select_related(
                'payment__pre_reservation',
                'payment__animal_reservation__pre_reservation',
            )
            .get(pk=refund_id)
        )
        if payment_refund.status == PaymentRefund.Status.SUCCEEDED:
            return payment_refund
        if (
            payment_refund.status == PaymentRefund.Status.PROCESSING
            and payment_refund.processing_started_at
            and payment_refund.processing_started_at
            > timezone.now() - timedelta(minutes=10)
        ):
            return payment_refund
        if payment_refund.status not in {
            PaymentRefund.Status.PENDING,
            PaymentRefund.Status.PROCESSING,
            PaymentRefund.Status.FAILED,
        }:
            raise PaymentError(_('This refund cannot be processed.'))
        other_committed = payment.refunds.exclude(
            pk=payment_refund.pk,
        ).filter(
            status__in=(
                PaymentRefund.Status.PENDING,
                PaymentRefund.Status.PROCESSING,
                PaymentRefund.Status.SUCCEEDED,
            )
        ).aggregate(total=Sum('amount'))['total'] or decimal.Decimal('0.00')
        if payment_refund.amount > payment.amount - other_committed:
            return _record_refund_failure_locked(
                payment_refund,
                'Another refund now consumes the available payment balance.',
            )
        if not payment.stripe_payment_intent_id:
            return _record_refund_failure_locked(
                payment_refund,
                'Missing Stripe PaymentIntent identifier.',
            )
        payment_refund.status = PaymentRefund.Status.PROCESSING
        payment_refund.processing_started_at = timezone.now()
        payment_refund.attempt_count += 1
        payment_refund.next_retry_at = None
        payment_refund.last_error = ''
        payment_refund.save(
            update_fields=[
                'status',
                'processing_started_at',
                'attempt_count',
                'next_retry_at',
                'last_error',
                'updated_at',
            ],
        )

    try:
        if payment_refund.stripe_refund_id:
            stripe_refund = stripe_gateway.retrieve_refund(
                payment_refund.stripe_refund_id,
            )
        else:
            stripe_refund = stripe_gateway.find_refund(
                payment=payment,
                payment_refund=payment_refund,
            )
            if stripe_refund is None:
                stripe_refund = stripe_gateway.create_refund(
                    payment=payment,
                    payment_refund=payment_refund,
                )
    except (stripe.APIConnectionError, stripe.APIError) as exc:
        with transaction.atomic():
            locked = PaymentRefund.objects.select_for_update().get(pk=refund_id)
            return _record_refund_uncertain_locked(locked, _safe_error(exc))
    except (stripe.StripeError, PaymentError) as exc:
        with transaction.atomic():
            locked = PaymentRefund.objects.select_for_update().get(pk=refund_id)
            return _record_refund_failure_locked(locked, _safe_error(exc))

    return _record_refund_result(refund_id, stripe_refund)


def reconcile_refund_webhook(stripe_refund):
    refund_id = stripe_gateway.value(stripe_refund, 'id')
    metadata = stripe_gateway.value(stripe_refund, 'metadata', {}) or {}
    local_refund_id = stripe_gateway.value(metadata, 'payment_refund_id')
    payment_refund = None
    if refund_id:
        payment_refund = PaymentRefund.objects.filter(
            stripe_refund_id=refund_id,
        ).first()
    if payment_refund is None and local_refund_id:
        try:
            payment_refund = PaymentRefund.objects.get(
                public_id=local_refund_id,
            )
        except (PaymentRefund.DoesNotExist, ValueError):
            return None
    if payment_refund is None:
        return None
    _record_refund_result(payment_refund.pk, stripe_refund)
    return Payment.objects.get(pk=payment_refund.payment_id)


@transaction.atomic
def _record_refund_result(refund_id: int, stripe_refund):
    reference = PaymentRefund.objects.only('payment_id').get(pk=refund_id)
    payment = Payment.objects.select_for_update().get(
        pk=reference.payment_id,
    )
    payment_refund = PaymentRefund.objects.select_for_update().get(
        pk=refund_id,
    )
    stripe_refund_id = stripe_gateway.value(stripe_refund, 'id')
    stripe_status = stripe_gateway.value(stripe_refund, 'status')
    if not stripe_refund_id:
        return _record_refund_failure_locked(
            payment_refund,
            'Stripe returned a refund without an identifier.',
        )
    if payment_refund.status == PaymentRefund.Status.SUCCEEDED:
        return payment_refund

    payment_refund.stripe_refund_id = stripe_refund_id
    payment_refund.processing_started_at = None
    if stripe_status == 'succeeded':
        payment_refund.status = PaymentRefund.Status.SUCCEEDED
        payment_refund.succeeded_at = timezone.now()
        payment_refund.failed_at = None
        payment_refund.next_retry_at = None
        payment_refund.last_error = ''
    elif stripe_status in {'failed', 'canceled'}:
        payment_refund.status = PaymentRefund.Status.FAILED
        payment_refund.failed_at = timezone.now()
        payment_refund.next_retry_at = timezone.now() + timedelta(minutes=10)
        failure_reason = stripe_gateway.value(
            stripe_refund,
            'failure_reason',
        )
        payment_refund.last_error = (
            f'Stripe refund {stripe_status}: '
            f'{failure_reason or "unknown reason"}'
        )[:2000]
    else:
        payment_refund.status = PaymentRefund.Status.PROCESSING
        payment_refund.next_retry_at = timezone.now() + timedelta(minutes=10)
        payment_refund.last_error = ''
    payment_refund.save(
        update_fields=[
            'status',
            'stripe_refund_id',
            'processing_started_at',
            'succeeded_at',
            'failed_at',
            'next_retry_at',
            'last_error',
            'updated_at',
        ],
    )

    if payment_refund.status == PaymentRefund.Status.SUCCEEDED:
        succeeded_total = payment.refunds.filter(
            status=PaymentRefund.Status.SUCCEEDED,
        ).aggregate(total=Sum('amount'))['total'] or decimal.Decimal('0.00')
        payment.status = (
            Payment.Status.REFUNDED
            if succeeded_total >= payment.amount
            else Payment.Status.PARTIALLY_REFUNDED
        )
        payment.save(update_fields=['status', 'updated_at'])
        if payment.charge_id:
            refresh_charge_status(payment.charge_id)
        _ensure_refund_document(payment_refund)
        transaction.on_commit(
            lambda pk=payment_refund.pk: notify_refund_succeeded(
                PaymentRefund.objects.select_related(
                    'payment__pre_reservation',
                    'payment__animal_reservation__pre_reservation',
                ).get(pk=pk),
            )
        )
    return payment_refund


def _record_refund_failure_locked(payment_refund, error_message):
    payment_refund.status = PaymentRefund.Status.FAILED
    payment_refund.processing_started_at = None
    payment_refund.failed_at = timezone.now()
    payment_refund.next_retry_at = timezone.now() + timedelta(minutes=10)
    payment_refund.last_error = str(error_message)[:2000]
    payment_refund.save(
        update_fields=[
            'status',
            'processing_started_at',
            'failed_at',
            'next_retry_at',
            'last_error',
            'updated_at',
        ],
    )
    return payment_refund


def _record_refund_uncertain_locked(payment_refund, error_message):
    payment_refund.status = PaymentRefund.Status.PENDING
    payment_refund.processing_started_at = None
    payment_refund.next_retry_at = timezone.now() + timedelta(minutes=10)
    payment_refund.last_error = (
        'Stripe response was uncertain; the same idempotent refund will be '
        f'reconciled: {error_message}'
    )[:2000]
    payment_refund.save(
        update_fields=[
            'status',
            'processing_started_at',
            'next_retry_at',
            'last_error',
            'updated_at',
        ],
    )
    return payment_refund


def refresh_payment_financials(payment_id: int):
    payment = Payment.objects.get(pk=payment_id)
    if (
        payment.provider != Payment.Provider.STRIPE
        or not payment.stripe_payment_intent_id
        or payment.status not in SETTLED_PAYMENT_STATUSES
    ):
        return payment

    Payment.objects.filter(pk=payment_id).update(
        financials_attempt_count=payment.financials_attempt_count + 1,
        financials_next_retry_at=None,
        financials_last_error='',
    )
    try:
        financials = stripe_gateway.retrieve_payment_financials(
            payment.stripe_payment_intent_id,
        )
    except (stripe.StripeError, PaymentError) as exc:
        Payment.objects.filter(pk=payment_id).update(
            financials_next_retry_at=timezone.now() + timedelta(minutes=10),
            financials_last_error=_safe_error(exc),
        )
        return Payment.objects.get(pk=payment_id)

    if not financials.get('charge_id'):
        Payment.objects.filter(pk=payment_id).update(
            financials_next_retry_at=timezone.now() + timedelta(minutes=10),
            financials_last_error='Stripe charge financials are not ready.',
        )
        return Payment.objects.get(pk=payment_id)
    Payment.objects.filter(pk=payment_id).update(
        stripe_charge_id=financials.get('charge_id'),
        provider_fee_amount=financials.get('fee_amount'),
        provider_net_amount=financials.get('net_amount'),
        financials_next_retry_at=None,
        financials_last_error='',
    )
    return Payment.objects.get(pk=payment_id)


def _validate_paid_session(session, payment, purchase):
    session_id = stripe_gateway.value(session, 'id')
    if (
        payment.stripe_checkout_session_id
        and payment.stripe_checkout_session_id != session_id
    ):
        raise PaymentValidationError('Checkout Session does not match payment.')
    currency = (stripe_gateway.value(session, 'currency') or '').upper()
    amount_total = stripe_gateway.value(session, 'amount_total')
    expected_cents = int(payment.amount * 100)
    if currency != payment.currency or amount_total != expected_cents:
        raise PaymentValidationError('Paid amount or currency does not match.')
    metadata = stripe_gateway.value(session, 'metadata', {}) or {}
    metadata_payment_id = stripe_gateway.value(metadata, 'local_payment_id')
    if metadata_payment_id and metadata_payment_id != str(payment.pk):
        raise PaymentValidationError(
            'Checkout metadata does not match the payment.'
        )
    metadata_reference = (
        stripe_gateway.value(metadata, 'purchase_public_id')
        or stripe_gateway.value(metadata, 'pre_reservation_id')
    )
    if (
        metadata_reference
        and metadata_reference != str(purchase.public_id)
    ):
        raise PaymentValidationError(
            'Checkout metadata does not match the purchase.'
        )
    metadata_attempt = stripe_gateway.value(
        metadata,
        'checkout_attempt_number',
    )
    if metadata_attempt:
        try:
            attempt_matches = (
                int(metadata_attempt) == payment.checkout_attempt_number
            )
        except (TypeError, ValueError):
            attempt_matches = False
    else:
        attempt_matches = payment.checkout_attempt_number == 1
    if not attempt_matches:
        raise PaymentValidationError(
            'Checkout Session belongs to an earlier payment attempt.'
        )


def _resolve_session_payment_id(session):
    metadata = stripe_gateway.value(session, 'metadata', {}) or {}
    payment_id = stripe_gateway.value(metadata, 'local_payment_id')
    if payment_id:
        return payment_id

    session_id = stripe_gateway.value(session, 'id')
    payment_id = Payment.objects.filter(
        stripe_checkout_session_id=session_id,
    ).values_list('pk', flat=True).first()
    if payment_id:
        return payment_id

    reference = (
        stripe_gateway.value(session, 'client_reference_id')
        or stripe_gateway.value(metadata, 'pre_reservation_id')
    )
    if reference:
        payment_id = Payment.objects.filter(
            pre_reservation__public_id=reference,
        ).values_list('pk', flat=True).first()
    if not payment_id:
        raise PaymentValidationError(
            'Checkout Session has no local payment reference.'
        )
    return payment_id


def _lock_payment_workflow(payment_id):
    reference = Payment.objects.only(
        'charge_id',
        'pre_reservation_id',
        'animal_reservation_id',
    ).get(pk=payment_id)
    if reference.pre_reservation_id:
        purchase = PreReservation.objects.select_for_update().get(
            pk=reference.pre_reservation_id,
        )
        if purchase.sale_case_id:
            AnimalSaleCase.objects.select_for_update().get(
                pk=purchase.sale_case_id,
            )
    elif reference.animal_reservation_id:
        reservation_reference = Reservation.objects.only(
            'pre_reservation_id',
            'sale_case_id',
        ).get(pk=reference.animal_reservation_id)
        if reservation_reference.sale_case_id:
            AnimalSaleCase.objects.select_for_update().get(
                pk=reservation_reference.sale_case_id,
            )
        if reservation_reference.pre_reservation_id:
            PreReservation.objects.select_for_update().get(
                pk=reservation_reference.pre_reservation_id,
            )
        purchase = Reservation.objects.select_for_update().get(
            pk=reference.animal_reservation_id,
        )
    elif reference.charge_id:
        charge = Charge.objects.select_for_update().get(
            pk=reference.charge_id,
        )
        AnimalSaleCase.objects.select_for_update().get(
            pk=charge.sale_case_id,
        )
        purchase = charge.purchase
        if purchase is None:
            raise Payment.DoesNotExist
        purchase = purchase.__class__.objects.select_for_update().get(
            pk=purchase.pk,
        )
    else:
        raise Payment.DoesNotExist
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    return payment, purchase


def _get_purchase(pk, *, is_pre):
    model = PreReservation if is_pre else Reservation
    return model.objects.get(pk=pk)


def _purchase_is_awaiting_payment(purchase):
    if isinstance(purchase, PreReservation):
        return purchase.status == PreReservation.Status.PENDING_PAYMENT
    return purchase.status == Reservation.Status.PENDING_PAYMENT


def _purchase_can_retry_payment(purchase, payment):
    if payment.status != Payment.Status.FAILED:
        return False
    if isinstance(purchase, PreReservation):
        return purchase.status in {
            PreReservation.Status.PAYMENT_FAILED,
            PreReservation.Status.EXPIRED,
        }
    return (
        purchase.status == Reservation.Status.PAYMENT_FAILED
        and (
            purchase.offer_expires_at is None
            or purchase.offer_expires_at > timezone.now()
        )
    )


def _payment_reconciliation_deadline(payment):
    purchase = payment.purchase
    if isinstance(purchase, PreReservation) and purchase.hold_expires_at:
        return purchase.hold_expires_at
    if purchase.offer_expires_at is None:
        return payment.created_at + timedelta(
            minutes=checkout_duration_minutes() + 10,
        )
    return min(
        purchase.offer_expires_at,
        payment.created_at
        + timedelta(minutes=checkout_duration_minutes() + 10),
    )


def _mark_payment_setup_failed(payment, error_message):
    if payment.pre_reservation_id:
        return mark_pre_reservation_payment_setup_failed(
            payment.pre_reservation_id,
            error_message,
        )
    return mark_reservation_payment_setup_failed(
        payment.animal_reservation_id,
        error_message,
    )


def _ensure_sale_document(payment):
    if payment.amount <= 0:
        return None
    from reservations.services.erp import ensure_sale_erp_document

    return ensure_sale_erp_document(payment)


def _ensure_refund_document(payment_refund):
    from reservations.services.erp import ensure_refund_erp_document

    return ensure_refund_erp_document(payment_refund)


def _record_ambiguous_checkout_error(payment_id: int, exc: Exception):
    Payment.objects.filter(pk=payment_id).update(last_error=_safe_error(exc))


def _object_id(value):
    if not value:
        return None
    return stripe_gateway.value(value, 'id', value)


def _from_timestamp(value):
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=datetime_timezone.utc)


def _money(value):
    return decimal.Decimal(value).quantize(
        MONEY,
        rounding=decimal.ROUND_HALF_UP,
    )


def _safe_error(exc: Exception) -> str:
    return f'{exc.__class__.__name__}: {str(exc)}'[:2000]
