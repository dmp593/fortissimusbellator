import decimal
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from breeding.models import Animal, Litter
from discounts.services import PromotionUnavailable, quote_promotion

from reservations.availability import (
    ensure_dog_is_available,
    ensure_litter_has_capacity,
)
from reservations.exceptions import ReservationUnavailable
from reservations.models import (
    ERPDocument,
    Payment,
    PreReservation,
    PreReservationTerms,
)
from reservations.policies import checkout_duration_minutes
from reservations.services.notifications import (
    notify_payment_confirmed,
    notify_reservation_cancelled,
)


@transaction.atomic
def create_pending_reservation(
    *,
    user,
    target_type: str,
    target_id: int,
    checkout_data: dict,
    language_code: str,
) -> PreReservation:
    accepted_terms = checkout_data.get('terms')
    current_terms = PreReservationTerms.objects.current()
    if current_terms is None:
        raise ReservationUnavailable(
            _('Pre-reservation terms are not available.')
        )
    if getattr(accepted_terms, 'pk', None) != current_terms.pk:
        raise ReservationUnavailable(
            _('The pre-reservation terms were updated. Review them again.')
        )

    target = _lock_and_validate_target(
        target_type=target_type,
        target_id=target_id,
        user=user,
    )
    fee = decimal.Decimal(target.pre_reservation_fee).quantize(
        decimal.Decimal('0.01')
    )

    try:
        quote = quote_promotion(
            code=checkout_data.get('promotion_code', ''),
            target=target,
            user=user,
            fee=fee,
            lock=True,
        )
    except PromotionUnavailable as exc:
        raise ReservationUnavailable(str(exc)) from exc

    total = fee - quote.discount_amount
    now = timezone.now()
    hold_expires_at = now + timedelta(
        minutes=checkout_duration_minutes() + 10
    )

    if isinstance(target, Animal):
        animal = target
        litter = None
        target_name = target.name
        target_birth_date = target.birth_date
    else:
        animal = None
        litter = target
        target_name = target.name
        target_birth_date = target.birth_date

    promotion = quote.promotion
    reservation = PreReservation(
        user=user,
        target_type=target_type,
        animal=animal,
        litter=litter,
        promotion=promotion,
        target_name=target_name,
        target_breed=target.breed.name,
        target_birth_date=target_birth_date,
        customer_name=checkout_data['full_name'].strip(),
        customer_email=checkout_data['email'].strip(),
        customer_phone=checkout_data['phone'].strip(),
        customer_tax_number=checkout_data.get('tax_number', '').strip(),
        billing_address=checkout_data.get('billing_address', '').strip(),
        billing_postcode=checkout_data.get('billing_postcode', '').strip(),
        billing_city=checkout_data.get('billing_city', '').strip(),
        billing_country=checkout_data.get('billing_country', 'PT'),
        language_code=language_code,
        fee_amount=fee,
        discount_amount=quote.discount_amount,
        total_amount=total,
        promotion_code=promotion.code if promotion else '',
        promotion_discount_type=promotion.discount_type if promotion else '',
        promotion_value=promotion.value if promotion else None,
        hold_expires_at=hold_expires_at,
        terms=current_terms,
        non_refundable_accepted_at=now,
    )
    reservation.full_clean()
    try:
        # The conditional unique constraints are a final guard if a database
        # backend cannot honor the target row lock as expected.
        with transaction.atomic():
            reservation.save()
    except IntegrityError as exc:
        raise ReservationUnavailable(
            'This dog or litter place was reserved by another customer.'
        ) from exc

    if total == 0:
        reservation.status = PreReservation.Status.CONFIRMED
        reservation.confirmed_at = now
        reservation.save(update_fields=['status', 'confirmed_at', 'updated_at'])
        Payment.objects.create(
            reservation=reservation,
            provider=Payment.Provider.COMPLIMENTARY,
            status=Payment.Status.PAID,
            amount=total,
            paid_at=now,
        )
        transaction.on_commit(
            lambda reservation_id=reservation.pk: notify_payment_confirmed(
                PreReservation.objects.get(pk=reservation_id)
            )
        )
    else:
        Payment.objects.create(
            reservation=reservation,
            provider=Payment.Provider.STRIPE,
            status=Payment.Status.INITIALIZING,
            amount=total,
        )

    return reservation


def _lock_and_validate_target(*, target_type: str, target_id: int, user):
    if target_type == PreReservation.TargetType.DOG:
        try:
            target = Animal.objects.select_for_update().get(pk=target_id)
        except Animal.DoesNotExist as exc:
            raise ReservationUnavailable('This dog is no longer available.') from exc
        ensure_dog_is_available(target)
        return target

    if target_type == PreReservation.TargetType.LITTER:
        try:
            target = Litter.objects.select_for_update().get(pk=target_id)
        except Litter.DoesNotExist as exc:
            raise ReservationUnavailable(
                'This litter is no longer available.'
            ) from exc
        ensure_litter_has_capacity(target, user=user)
        return target

    raise ReservationUnavailable('Invalid pre-reservation target.')


@transaction.atomic
def mark_payment_setup_failed(reservation_id: int, error_message: str):
    reservation = PreReservation.objects.select_for_update().get(
        pk=reservation_id
    )
    if reservation.status != PreReservation.Status.PENDING_PAYMENT:
        return reservation

    reservation.status = PreReservation.Status.PAYMENT_FAILED
    reservation.save(update_fields=['status', 'updated_at'])
    payment = Payment.objects.select_for_update().get(
        reservation=reservation
    )
    payment.status = Payment.Status.FAILED
    payment.failed_at = timezone.now()
    payment.last_error = error_message[:2000]
    payment.save(
        update_fields=['status', 'failed_at', 'last_error', 'updated_at']
    )
    return reservation


@transaction.atomic
def cancel_by_user(*, reservation_id: int, user) -> PreReservation:
    reservation = PreReservation.objects.select_for_update().get(
        pk=reservation_id,
        user=user,
    )
    if not reservation.can_user_cancel:
        raise ReservationUnavailable(
            'This pre-reservation can no longer be cancelled.'
        )

    reservation.status = PreReservation.Status.CANCELLED_BY_USER
    reservation.cancelled_at = timezone.now()
    reservation.cancelled_by = user
    reservation.cancellation_reason = 'Cancelled by customer.'
    reservation.save(
        update_fields=[
            'status',
            'cancelled_at',
            'cancelled_by',
            'cancellation_reason',
            'updated_at',
        ]
    )
    payment = Payment.objects.select_for_update().get(
        reservation=reservation
    )
    if payment.status in {Payment.Status.INITIALIZING, Payment.Status.PENDING}:
        payment.status = Payment.Status.FAILED
        payment.failed_at = timezone.now()
        payment.stripe_checkout_url = ''
        payment.save(
            update_fields=[
                'status',
                'failed_at',
                'stripe_checkout_url',
                'updated_at',
            ]
        )
    transaction.on_commit(
        lambda reservation_id=reservation.pk: notify_reservation_cancelled(
            PreReservation.objects.get(pk=reservation_id),
            cancelled_by_staff=False,
        )
    )
    return reservation


@transaction.atomic
def cancel_by_admin(*, reservation_id: int, admin_user, reason: str):
    reservation = PreReservation.objects.select_for_update().get(
        pk=reservation_id
    )
    if reservation.status in {
        PreReservation.Status.CANCELLED_BY_USER,
        PreReservation.Status.CANCELLED_BY_ADMIN,
        PreReservation.Status.EXPIRED,
        PreReservation.Status.PAYMENT_FAILED,
    }:
        raise ReservationUnavailable('This pre-reservation is already closed.')

    reservation.status = PreReservation.Status.CANCELLED_BY_ADMIN
    reservation.cancelled_at = timezone.now()
    reservation.cancelled_by = admin_user
    reservation.cancellation_reason = reason
    reservation.save(
        update_fields=[
            'status',
            'cancelled_at',
            'cancelled_by',
            'cancellation_reason',
            'updated_at',
        ]
    )

    payment = Payment.objects.select_for_update().get(
        reservation=reservation
    )
    should_refund = payment.status == Payment.Status.PAID and payment.amount > 0
    if should_refund:
        payment.status = Payment.Status.REFUND_PENDING
        payment.refund_next_retry_at = timezone.now()
        payment.save(
            update_fields=['status', 'refund_next_retry_at', 'updated_at']
        )
    elif payment.status in {Payment.Status.INITIALIZING, Payment.Status.PENDING}:
        payment.status = Payment.Status.FAILED
        payment.failed_at = timezone.now()
        payment.stripe_checkout_url = ''
        payment.save(
            update_fields=[
                'status',
                'failed_at',
                'stripe_checkout_url',
                'updated_at',
            ]
        )

    transaction.on_commit(
        lambda reservation_id=reservation.pk: notify_reservation_cancelled(
            PreReservation.objects.get(pk=reservation_id),
            cancelled_by_staff=True,
        )
    )

    return reservation, should_refund


@transaction.atomic
def mark_fulfilled(*, reservation_id: int):
    reservation = PreReservation.objects.select_for_update().get(
        pk=reservation_id
    )
    if reservation.status != PreReservation.Status.CONFIRMED:
        raise ReservationUnavailable('Only confirmed reservations can be fulfilled.')
    reservation.status = PreReservation.Status.FULFILLED
    reservation.fulfilled_at = timezone.now()
    reservation.save(update_fields=['status', 'fulfilled_at', 'updated_at'])
    return reservation


def ensure_sale_erp_document(reservation: PreReservation) -> ERPDocument:
    return ERPDocument.objects.get_or_create(
        reservation=reservation,
        kind=ERPDocument.Kind.SALE,
        defaults={
            'external_reference': f'PRE-{reservation.public_id}',
        },
    )[0]
