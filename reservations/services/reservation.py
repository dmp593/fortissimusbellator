import decimal
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from breeding.models import Animal
from discounts.models import Promotion
from discounts.services import PromotionUnavailable, quote_promotion

from reservations.availability import (
    capacity_consuming_reservations,
    dog_unavailability_reason,
    inventory_blocking_reservations,
)
from reservations.exceptions import ReservationUnavailable
from reservations.models import (
    AnimalSaleCase,
    Charge,
    Payment,
    PaymentRefund,
    PreReservation,
    PreReservationTerms,
    Reservation,
    ReservationTerms,
)
from reservations.policies import checkout_duration_minutes
from reservations.services.ledger import (
    create_charge,
    refresh_charge_status,
    void_charge,
)
from reservations.services.notifications import (
    notify_pre_reservation_accepted,
    notify_pre_reservation_closed,
    notify_pre_reservation_paid,
    notify_reservation_cancelled,
    notify_reservation_confirmed,
    notify_reservation_offer_expired,
)


MONEY = decimal.Decimal('0.01')
PAID_PAYMENT_STATUSES = (
    Payment.Status.PAID,
    Payment.Status.PARTIALLY_REFUNDED,
    Payment.Status.REFUNDED,
)
logger = logging.getLogger(__name__)


def money(value) -> decimal.Decimal:
    return decimal.Decimal(value).quantize(
        MONEY,
        rounding=decimal.ROUND_HALF_UP,
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
    if target_type != PreReservation.TargetType.DOG:
        raise ReservationUnavailable(
            _(
                'Litters cannot be pre-reserved. Choose an individual dog '
                'after the litter is born.'
            )
        )

    values = _prepare_checkout_values(
        user=user,
        target_id=target_id,
        checkout_data=checkout_data,
        language_code=language_code,
    )
    try:
        with transaction.atomic():
            sale_case = _create_online_sale_case(
                user=user,
                values=values,
            )
            charge = create_charge(
                sale_case=sale_case,
                stage=Charge.Stage.PRE_RESERVATION,
                subtotal_amount=values['fee_amount'],
                promotion=values['promotion'],
                promotion_discount_amount=values['discount_amount'],
                promotion_code=values['promotion_code'],
                promotion_discount_type=values['promotion_discount_type'],
                promotion_value=values['promotion_value'],
                currency=values['currency'],
                due_at=values['hold_expires_at'],
            )
            pre_reservation = PreReservation(
                sale_case=sale_case,
                charge=charge,
                user=user,
                target_type=PreReservation.TargetType.DOG,
                terms_acceptance_source=(
                    PreReservation.TermsAcceptanceSource.CUSTOMER_ONLINE
                ),
                **values,
            )
            pre_reservation.full_clean()
            pre_reservation.save()
    except IntegrityError as exc:
        raise ReservationUnavailable(
            _('This dog was pre-reserved by another customer.')
        ) from exc

    now = pre_reservation.non_refundable_accepted_at
    if pre_reservation.total_amount == 0:
        pre_reservation.status = PreReservation.Status.AWAITING_REVIEW
        pre_reservation.confirmed_at = now
        pre_reservation.hold_expires_at = None
        pre_reservation.save(
            update_fields=[
                'status',
                'confirmed_at',
                'hold_expires_at',
                'updated_at',
            ],
        )
        Payment.objects.create(
            charge=charge,
            pre_reservation=pre_reservation,
            provider=Payment.Provider.COMPLIMENTARY,
            status=Payment.Status.PAID,
            amount=pre_reservation.total_amount,
            currency=pre_reservation.currency,
            paid_at=now,
        )
        refresh_charge_status(charge.pk)
        transaction.on_commit(
            lambda pk=pre_reservation.pk: notify_pre_reservation_paid(
                PreReservation.objects.get(pk=pk),
            )
        )
    else:
        Payment.objects.create(
            charge=charge,
            pre_reservation=pre_reservation,
            provider=Payment.Provider.STRIPE,
            status=Payment.Status.INITIALIZING,
            amount=pre_reservation.total_amount,
            currency=pre_reservation.currency,
        )

    return pre_reservation


@transaction.atomic
def reopen_failed_reservation(
    *,
    reservation_id: int,
    user,
    target_type: str,
    target_id: int,
    checkout_data: dict,
    language_code: str,
) -> PreReservation:
    if target_type != PreReservation.TargetType.DOG:
        raise ReservationUnavailable(_('This payment can no longer be retried.'))

    try:
        pre_reservation = PreReservation.objects.select_for_update().get(
            pk=reservation_id,
            user=user,
            target_type=PreReservation.TargetType.DOG,
            animal_id=target_id,
        )
    except PreReservation.DoesNotExist as exc:
        raise ReservationUnavailable(
            _('This payment can no longer be retried.')
        ) from exc

    payment = Payment.objects.select_for_update().get(
        pre_reservation=pre_reservation,
    )
    retrying_failure = (
        pre_reservation.status
        in {
            PreReservation.Status.PAYMENT_FAILED,
            PreReservation.Status.EXPIRED,
        }
        and payment.status == Payment.Status.FAILED
    )
    resuming_staff_process = (
        pre_reservation.status == PreReservation.Status.PENDING_PAYMENT
        and pre_reservation.terms_acceptance_source
        == PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER
        and payment.status == Payment.Status.INITIALIZING
        and not payment.stripe_checkout_session_id
        and pre_reservation.sale_case.origin
        in {
            AnimalSaleCase.Origin.ADMIN,
            AnimalSaleCase.Origin.TRANSFER,
        }
    )
    if not retrying_failure and not resuming_staff_process:
        raise ReservationUnavailable(_('This payment can no longer be retried.'))

    values = _prepare_checkout_values(
        user=user,
        target_id=target_id,
        checkout_data=checkout_data,
        language_code=language_code,
        purchase=pre_reservation,
    )
    for field_name, value in values.items():
        setattr(pre_reservation, field_name, value)
    pre_reservation.terms_acceptance_source = (
        PreReservation.TermsAcceptanceSource.CUSTOMER_ONLINE
    )
    pre_reservation.target_deleted_at = None
    pre_reservation.reviewed_at = None
    pre_reservation.reviewed_by = None
    pre_reservation.review_reason = ''
    pre_reservation.cancelled_at = None
    pre_reservation.cancelled_by = None
    pre_reservation.cancellation_reason = ''
    try:
        with transaction.atomic():
            charge = _reopen_sale_case_from_checkout(
                pre_reservation=pre_reservation,
                values=values,
            )
            payable_amount = charge.amount_due
            pre_reservation.status = (
                PreReservation.Status.AWAITING_REVIEW
                if payable_amount == 0
                else PreReservation.Status.PENDING_PAYMENT
            )
            pre_reservation.confirmed_at = (
                pre_reservation.non_refundable_accepted_at
                if payable_amount == 0
                else None
            )
            if payable_amount == 0:
                pre_reservation.hold_expires_at = None
            pre_reservation.full_clean()
            pre_reservation.save()
    except IntegrityError as exc:
        raise ReservationUnavailable(
            _('This dog was pre-reserved by another customer.')
        ) from exc

    if retrying_failure:
        _reset_payment_for_retry(
            payment,
            amount=payable_amount,
            currency=pre_reservation.currency,
            complimentary=payable_amount == 0,
            paid_at=pre_reservation.confirmed_at,
        )
    else:
        payment.amount = payable_amount
        payment.currency = pre_reservation.currency
        payment.provider = (
            Payment.Provider.COMPLIMENTARY
            if payable_amount == 0
            else Payment.Provider.STRIPE
        )
        payment.status = (
            Payment.Status.PAID
            if payable_amount == 0
            else Payment.Status.INITIALIZING
        )
        payment.paid_at = pre_reservation.confirmed_at
        payment.save(
            update_fields=[
                'amount',
                'currency',
                'provider',
                'status',
                'paid_at',
                'updated_at',
            ]
        )
    if payable_amount == 0:
        if (
            pre_reservation.sale_case.origin
            == AnimalSaleCase.Origin.ADMIN
        ):
            transaction.on_commit(
                lambda pk=pre_reservation.pk: (
                    try_accept_staff_created_pre_reservation(pk)
                )
            )
        transaction.on_commit(
            lambda pk=pre_reservation.pk: notify_pre_reservation_paid(
                PreReservation.objects.get(pk=pk),
            )
        )
    return pre_reservation


def _reopen_sale_case_from_checkout(*, pre_reservation, values):
    sale_case = AnimalSaleCase.objects.select_for_update().get(
        pk=pre_reservation.sale_case_id,
    )
    sale_case.status = AnimalSaleCase.Status.PRE_RESERVATION
    sale_case.animal = values['animal']
    sale_case.target_name = values['target_name']
    sale_case.target_breed = values['target_breed']
    sale_case.target_birth_date = values['target_birth_date']
    sale_case.target_deleted_at = None
    sale_case.animal_price_amount = values['animal_price_amount']
    sale_case.reservation_deposit_percentage = (
        values['reservation_deposit_percentage']
    )
    sale_case.reservation_deposit_amount = (
        values['reservation_deposit_amount']
    )
    sale_case.customer_name = values['customer_name']
    sale_case.customer_email = values['customer_email']
    sale_case.customer_phone = values['customer_phone']
    sale_case.customer_tax_number = values['customer_tax_number']
    sale_case.billing_address = values['billing_address']
    sale_case.billing_postcode = values['billing_postcode']
    sale_case.billing_city = values['billing_city']
    sale_case.billing_country = values['billing_country']
    sale_case.language_code = values['language_code']
    sale_case.currency = values['currency']
    sale_case.closed_at = None
    sale_case.save()

    charge = Charge.objects.select_for_update().get(
        pk=pre_reservation.charge_id,
    )
    charge.status = Charge.Status.OPEN
    charge.subtotal_amount = values['fee_amount']
    charge.promotion = values['promotion']
    charge.promotion_discount_amount = values['discount_amount']
    charge.promotion_code = values['promotion_code']
    charge.promotion_discount_type = values['promotion_discount_type']
    charge.promotion_value = values['promotion_value']
    charge.currency = values['currency']
    charge.due_at = values['hold_expires_at']
    charge.voided_at = None
    charge.void_reason = ''
    charge.save()
    return refresh_charge_status(charge.pk)


def _prepare_checkout_values(
    *,
    user,
    target_id: int,
    checkout_data: dict,
    language_code: str,
    purchase=None,
) -> dict:
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

    try:
        animal = (
            Animal.objects.select_for_update()
            .select_related('breed')
            .get(pk=target_id)
        )
    except Animal.DoesNotExist as exc:
        raise ReservationUnavailable(
            _('This dog is no longer available.')
        ) from exc

    reason = dog_unavailability_reason(
        animal,
        exclude_sale_case_id=(
            purchase.sale_case_id if purchase is not None else None
        ),
    )
    if reason:
        raise ReservationUnavailable(reason)

    preserve_staff_amount = (
        purchase is not None
        and purchase.sale_case.origin
        in {
            AnimalSaleCase.Origin.ADMIN,
            AnimalSaleCase.Origin.TRANSFER,
        }
    )
    fee = money(
        purchase.charge.subtotal_amount
        if preserve_staff_amount
        else animal.pre_reservation_fee
    )
    try:
        quote = quote_promotion(
            code=checkout_data.get('promotion_code', ''),
            target=animal,
            user=user,
            fee=fee,
            purchase_stage=Promotion.PurchaseStage.PRE_RESERVATION,
            purchase=purchase,
            lock=True,
        )
    except PromotionUnavailable as exc:
        raise ReservationUnavailable(str(exc)) from exc

    animal_price = money(animal.current_price_in_euros)
    deposit_percentage = money(animal.reservation_deposit_percentage)
    deposit_target = money(
        animal_price * deposit_percentage / decimal.Decimal('100'),
    )
    now = timezone.now()
    promotion = quote.promotion
    return {
        'animal': animal,
        'litter': None,
        'promotion': promotion,
        'target_name': animal.name,
        'target_breed': animal.breed.name,
        'target_birth_date': animal.birth_date,
        'customer_name': checkout_data['full_name'].strip(),
        'customer_email': checkout_data['email'].strip(),
        'customer_phone': checkout_data['phone'].strip(),
        'customer_tax_number': checkout_data.get('tax_number', '').strip(),
        'billing_address': checkout_data.get('billing_address', '').strip(),
        'billing_postcode': checkout_data.get('billing_postcode', '').strip(),
        'billing_city': checkout_data.get('billing_city', '').strip(),
        'billing_country': checkout_data.get('billing_country', 'PT'),
        'language_code': language_code,
        'fee_amount': fee,
        'discount_amount': quote.discount_amount,
        'total_amount': fee - quote.discount_amount,
        'currency': 'EUR',
        'promotion_code': promotion.code if promotion else '',
        'promotion_discount_type': promotion.discount_type if promotion else '',
        'promotion_value': promotion.value if promotion else None,
        'animal_price_amount': animal_price,
        'reservation_deposit_percentage': deposit_percentage,
        'reservation_deposit_amount': deposit_target,
        'hold_expires_at': now + timedelta(
            minutes=checkout_duration_minutes() + 10,
        ),
        'terms': current_terms,
        'non_refundable_accepted_at': now,
    }


def _create_online_sale_case(*, user, values):
    return AnimalSaleCase.objects.create(
        user=user,
        animal=values['animal'],
        origin=AnimalSaleCase.Origin.ONLINE,
        status=AnimalSaleCase.Status.PRE_RESERVATION,
        target_name=values['target_name'],
        target_breed=values['target_breed'],
        target_birth_date=values['target_birth_date'],
        animal_price_amount=values['animal_price_amount'],
        reservation_deposit_percentage=(
            values['reservation_deposit_percentage']
        ),
        reservation_deposit_amount=values['reservation_deposit_amount'],
        customer_name=values['customer_name'],
        customer_email=values['customer_email'],
        customer_phone=values['customer_phone'],
        customer_tax_number=values['customer_tax_number'],
        billing_address=values['billing_address'],
        billing_postcode=values['billing_postcode'],
        billing_city=values['billing_city'],
        billing_country=values['billing_country'],
        language_code=values['language_code'],
        currency=values['currency'],
    )


@transaction.atomic
def mark_pre_reservation_payment_setup_failed(
    pre_reservation_id: int,
    error_message: str,
):
    pre_reservation = PreReservation.objects.select_for_update().get(
        pk=pre_reservation_id,
    )
    if pre_reservation.status != PreReservation.Status.PENDING_PAYMENT:
        return pre_reservation

    pre_reservation.status = PreReservation.Status.PAYMENT_FAILED
    pre_reservation.save(update_fields=['status', 'updated_at'])
    payment = Payment.objects.select_for_update().get(
        pre_reservation=pre_reservation,
    )
    payment.status = Payment.Status.FAILED
    payment.failed_at = timezone.now()
    payment.last_error = error_message[:2000]
    payment.stripe_checkout_url = ''
    payment.save(
        update_fields=[
            'status',
            'failed_at',
            'last_error',
            'stripe_checkout_url',
            'updated_at',
        ]
    )
    _close_sale_case(
        pre_reservation.sale_case,
        reason=error_message,
    )
    if pre_reservation.charge_id:
        void_charge(
            charge=pre_reservation.charge,
            reason=error_message,
        )
    return pre_reservation


@transaction.atomic
def accept_pre_reservation(
    *,
    pre_reservation_id: int,
    admin_user,
    reason: str = '',
) -> Reservation:
    animal_id = PreReservation.objects.values_list(
        'animal_id',
        flat=True,
    ).get(pk=pre_reservation_id)
    if animal_id is None:
        raise ReservationUnavailable(
            _('Only dog pre-reservations can be accepted.')
        )
    animal = Animal.objects.select_for_update().get(pk=animal_id)
    pre_reservation = (
        PreReservation.objects.select_for_update()
        .get(pk=pre_reservation_id)
    )
    charge = Charge.objects.select_for_update().get(
        pk=pre_reservation.charge_id,
    )

    if pre_reservation.status == PreReservation.Status.ACCEPTED:
        try:
            return pre_reservation.reservation
        except Reservation.DoesNotExist:
            pass
    if pre_reservation.status != PreReservation.Status.AWAITING_REVIEW:
        raise ReservationUnavailable(
            _('Only paid pre-reservations awaiting review can be accepted.')
        )
    if (
        pre_reservation.terms_acceptance_source
        == PreReservation.TermsAcceptanceSource.PENDING_CUSTOMER
        or pre_reservation.terms_id is None
        or pre_reservation.non_refundable_accepted_at is None
    ):
        raise ReservationUnavailable(
            _(
                'The current pre-reservation terms must be accepted before '
                'this process can be approved.'
            )
        )
    charge = refresh_charge_status(charge.pk)
    was_fully_settled = (
        charge.gross_payment_amount + charge.credit_amount
        >= charge.total_amount
    )
    has_retained_value = (
        charge.total_amount == 0 or charge.settled_amount > 0
    )
    if not was_fully_settled or not has_retained_value:
        raise ReservationUnavailable(
            _('The pre-reservation payment is not in a payable review state.')
        )
    if PaymentRefund.objects.filter(
        payment__charge=charge,
        status__in=(
            PaymentRefund.Status.PENDING,
            PaymentRefund.Status.PROCESSING,
        ),
    ).exists():
        raise ReservationUnavailable(
            _(
                'Wait for the pending refund to finish before accepting this '
                'pre-reservation.'
            )
        )

    _ensure_animal_still_matches_snapshot(animal, pre_reservation)
    if capacity_consuming_reservations().filter(
        animal=animal,
    ).exclude(pk=pre_reservation.pk).exists():
        raise ReservationUnavailable(
            _('Another active pre-reservation already holds this dog.')
        )
    if inventory_blocking_reservations().filter(
        pre_reservation__animal=animal,
    ).exists():
        raise ReservationUnavailable(
            _('An active reservation already holds this dog.')
        )
    if ReservationTerms.objects.current() is None:
        raise ReservationUnavailable(
            _('Reservation terms are not currently published.')
        )

    paid_credit = charge.settled_amount
    deposit_target = pre_reservation.reservation_deposit_amount
    credit = min(paid_credit, deposit_target)
    offer_expires_at = timezone.now() + timedelta(
        hours=animal.reservation_offer_hours,
    )
    charge = create_charge(
        sale_case=pre_reservation.sale_case,
        stage=Charge.Stage.RESERVATION,
        subtotal_amount=deposit_target - credit,
        currency=pre_reservation.currency,
        due_at=offer_expires_at,
        created_by=admin_user,
    )
    reservation = Reservation(
        sale_case=pre_reservation.sale_case,
        charge=charge,
        pre_reservation=pre_reservation,
        status=Reservation.Status.OFFERED,
        pre_reservation_credit_amount=credit,
        deposit_target_amount=deposit_target,
        payment_amount=deposit_target - credit,
        currency=pre_reservation.currency,
        offer_expires_at=offer_expires_at,
        terms_acceptance_source=(
            Reservation.TermsAcceptanceSource.PENDING_CUSTOMER
        ),
    )
    reservation.full_clean()
    reservation.save()

    pre_reservation.status = PreReservation.Status.ACCEPTED
    pre_reservation.reviewed_at = timezone.now()
    pre_reservation.reviewed_by = admin_user
    pre_reservation.review_reason = reason.strip()
    pre_reservation.save(
        update_fields=[
            'status',
            'reviewed_at',
            'reviewed_by',
            'review_reason',
            'updated_at',
        ],
    )
    pre_reservation.sale_case.status = AnimalSaleCase.Status.RESERVATION
    pre_reservation.sale_case.closed_at = None
    pre_reservation.sale_case.save(
        update_fields=['status', 'closed_at', 'updated_at'],
    )
    transaction.on_commit(
        lambda pk=pre_reservation.pk: notify_pre_reservation_accepted(
            PreReservation.objects.select_related('reservation').get(pk=pk),
        )
    )
    return reservation


def accept_staff_created_pre_reservation(
    pre_reservation_id: int,
    *,
    admin_user=None,
) -> Reservation | None:
    """Accept a settled pre-reservation that staff already approved by creating."""
    pre_reservation = (
        PreReservation.objects.select_related('sale_case__created_by')
        .filter(pk=pre_reservation_id)
        .first()
    )
    if (
        pre_reservation is None
        or pre_reservation.sale_case is None
        or pre_reservation.sale_case.origin != AnimalSaleCase.Origin.ADMIN
    ):
        return None
    if pre_reservation.status == PreReservation.Status.ACCEPTED:
        try:
            return pre_reservation.reservation
        except Reservation.DoesNotExist:
            return None
    if pre_reservation.status != PreReservation.Status.AWAITING_REVIEW:
        return None

    reviewer = admin_user or pre_reservation.sale_case.created_by
    if reviewer is None:
        raise ReservationUnavailable(
            _(
                'A staff-created pre-reservation needs an identified staff '
                'member before it can be accepted automatically.'
            )
        )
    return accept_pre_reservation(
        pre_reservation_id=pre_reservation.pk,
        admin_user=reviewer,
        reason=_('Automatically accepted because staff created this process.'),
    )


def try_accept_staff_created_pre_reservation(
    pre_reservation_id: int,
) -> Reservation | None:
    """Keep a confirmed payment valid if automatic staff acceptance is blocked."""
    try:
        return accept_staff_created_pre_reservation(pre_reservation_id)
    except Exception:
        logger.exception(
            'Unable to auto-accept a paid staff pre-reservation',
            extra={'pre_reservation_id': pre_reservation_id},
        )
        return None


@transaction.atomic
def reject_pre_reservation(
    *,
    pre_reservation_id: int,
    admin_user,
    reason: str,
) -> PreReservation:
    pre_reservation = PreReservation.objects.select_for_update().get(
        pk=pre_reservation_id,
    )
    if pre_reservation.status != PreReservation.Status.AWAITING_REVIEW:
        raise ReservationUnavailable(
            _('Only pre-reservations awaiting review can be rejected.')
        )
    now = timezone.now()
    pre_reservation.status = PreReservation.Status.NOT_ACCEPTED
    pre_reservation.reviewed_at = now
    pre_reservation.reviewed_by = admin_user
    pre_reservation.review_reason = reason.strip()
    pre_reservation.cancelled_at = now
    pre_reservation.cancelled_by = admin_user
    pre_reservation.cancellation_reason = reason.strip()
    pre_reservation.save(
        update_fields=[
            'status',
            'reviewed_at',
            'reviewed_by',
            'review_reason',
            'cancelled_at',
            'cancelled_by',
            'cancellation_reason',
            'updated_at',
        ],
    )
    _close_sale_case(
        pre_reservation.sale_case,
        reason=reason,
    )
    transaction.on_commit(
        lambda pk=pre_reservation.pk: notify_pre_reservation_closed(
            PreReservation.objects.get(pk=pk),
            rejected=True,
            cancelled_by_staff=True,
        )
    )
    return pre_reservation


@transaction.atomic
def cancel_pre_reservation_by_user(
    *,
    pre_reservation_id: int,
    user,
) -> PreReservation:
    pre_reservation = PreReservation.objects.select_for_update().get(
        pk=pre_reservation_id,
        user=user,
    )
    if pre_reservation.status not in {
        PreReservation.Status.PENDING_PAYMENT,
        PreReservation.Status.AWAITING_REVIEW,
    }:
        raise ReservationUnavailable(
            _('This pre-reservation can no longer be cancelled.')
        )

    _close_pre_reservation(
        pre_reservation,
        status=PreReservation.Status.CANCELLED_BY_USER,
        cancelled_by=user,
        reason=_('Cancelled by customer.'),
    )
    transaction.on_commit(
        lambda pk=pre_reservation.pk: notify_pre_reservation_closed(
            PreReservation.objects.get(pk=pk),
            rejected=False,
            cancelled_by_staff=False,
        )
    )
    return pre_reservation


@transaction.atomic
def cancel_pre_reservation_by_admin(
    *,
    pre_reservation_id: int,
    admin_user,
    reason: str,
) -> PreReservation:
    pre_reservation = PreReservation.objects.select_for_update().get(
        pk=pre_reservation_id,
    )
    if pre_reservation.status == PreReservation.Status.ACCEPTED:
        try:
            cancel_reservation_by_admin(
                reservation_id=pre_reservation.reservation.pk,
                admin_user=admin_user,
                reason=reason,
            )
        except Reservation.DoesNotExist:
            pass
        pre_reservation.refresh_from_db()
        return pre_reservation
    if pre_reservation.status not in {
        PreReservation.Status.PENDING_PAYMENT,
        PreReservation.Status.AWAITING_REVIEW,
    }:
        raise ReservationUnavailable(
            _('This pre-reservation is already closed.')
        )

    _close_pre_reservation(
        pre_reservation,
        status=PreReservation.Status.CANCELLED_BY_ADMIN,
        cancelled_by=admin_user,
        reason=reason,
    )
    transaction.on_commit(
        lambda pk=pre_reservation.pk: notify_pre_reservation_closed(
            PreReservation.objects.get(pk=pk),
            rejected=False,
            cancelled_by_staff=True,
        )
    )
    return pre_reservation


def _close_pre_reservation(
    pre_reservation: PreReservation,
    *,
    status: str,
    cancelled_by,
    reason,
):
    now = timezone.now()
    pre_reservation.status = status
    pre_reservation.cancelled_at = now
    pre_reservation.cancelled_by = cancelled_by
    pre_reservation.cancellation_reason = str(reason).strip()
    pre_reservation.save(
        update_fields=[
            'status',
            'cancelled_at',
            'cancelled_by',
            'cancellation_reason',
            'updated_at',
        ],
    )
    _close_sale_case(
        pre_reservation.sale_case,
        reason=str(reason),
    )
    payment = Payment.objects.select_for_update().filter(
        pre_reservation=pre_reservation,
    ).first()
    if payment and payment.status in {
        Payment.Status.INITIALIZING,
        Payment.Status.PENDING,
    }:
        _mark_unpaid_payment_failed(payment)
    if pre_reservation.charge_id:
        void_charge(
            charge=pre_reservation.charge,
            reason=str(reason),
        )


def start_reservation_payment(
    *,
    reservation_id: int,
    user,
    accepted_terms,
    promotion_code: str = '',
) -> Reservation:
    reservation, expired = _start_reservation_payment_locked(
        reservation_id=reservation_id,
        user=user,
        accepted_terms=accepted_terms,
        promotion_code=promotion_code,
    )
    if expired:
        raise ReservationUnavailable(_('This reservation offer has expired.'))
    return reservation


@transaction.atomic
def _start_reservation_payment_locked(
    *,
    reservation_id: int,
    user,
    accepted_terms,
    promotion_code: str,
) -> tuple[Reservation, bool]:
    pre_reservation, reservation = _lock_reservation_workflow(
        reservation_id,
        user=user,
    )
    _validate_reservation_payment_status(reservation)
    if _reservation_offer_has_expired(reservation):
        _expire_reservation_offer_locked(reservation)
        return reservation, True

    current_terms = _validate_reservation_terms(accepted_terms)
    payment = (
        Payment.objects.select_for_update()
        .filter(animal_reservation=reservation)
        .first()
    )
    _apply_reservation_promotion(
        reservation=reservation,
        payment=payment,
        user=user,
        promotion_code=promotion_code,
    )
    _record_reservation_terms(reservation, current_terms)
    charge = _synchronize_reservation_charge(reservation)
    payable_amount = charge.amount_due
    _save_reservation_checkout_state(reservation, payable_amount)
    _prepare_reservation_payment(
        reservation=reservation,
        payment=payment,
        payable_amount=payable_amount,
    )
    if payable_amount == 0:
        _complete_complimentary_reservation(
            reservation=reservation,
            pre_reservation=pre_reservation,
        )
    return reservation, False


def _validate_reservation_payment_status(reservation: Reservation):
    if reservation.status not in {
        Reservation.Status.OFFERED,
        Reservation.Status.PAYMENT_FAILED,
        Reservation.Status.PENDING_PAYMENT,
    }:
        raise ReservationUnavailable(
            _('This reservation offer can no longer be paid.')
        )


def _reservation_offer_has_expired(reservation: Reservation) -> bool:
    return bool(
        reservation.offer_expires_at
        and reservation.offer_expires_at <= timezone.now()
    )


def _validate_reservation_terms(accepted_terms) -> ReservationTerms:
    current_terms = ReservationTerms.objects.current()
    if current_terms is None:
        raise ReservationUnavailable(_('Reservation terms are not available.'))
    if getattr(accepted_terms, 'pk', None) != current_terms.pk:
        raise ReservationUnavailable(
            _('The reservation terms were updated. Review them again.')
        )
    return current_terms


def _apply_reservation_promotion(
    *,
    reservation: Reservation,
    payment,
    user,
    promotion_code: str,
):
    normalized_code = Promotion.normalize_code(promotion_code)
    payment_price_is_locked = payment and payment.status in {
        Payment.Status.INITIALIZING,
        Payment.Status.PENDING,
    }
    if payment_price_is_locked:
        if normalized_code != reservation.promotion_code:
            raise ReservationUnavailable(
                _(
                    'The amount is locked for the current payment attempt. '
                    'Use the same promotion code or continue without changing '
                    'the checkout.'
                )
            )
    else:
        try:
            quote = quote_promotion(
                code=normalized_code,
                target=reservation.animal,
                user=user,
                fee=reservation.amount_before_discount,
                purchase_stage=Promotion.PurchaseStage.RESERVATION,
                purchase=reservation,
                lock=True,
            )
        except PromotionUnavailable as exc:
            raise ReservationUnavailable(str(exc)) from exc
        promotion = quote.promotion
        reservation.promotion = promotion
        reservation.discount_amount = quote.discount_amount
        reservation.payment_amount = quote.total_amount
        reservation.promotion_code = promotion.code if promotion else ''
        reservation.promotion_discount_type = (
            promotion.discount_type if promotion else ''
        )
        reservation.promotion_value = (
            promotion.value if promotion else None
        )


def _record_reservation_terms(
    reservation: Reservation,
    terms: ReservationTerms,
):
    reservation.terms = terms
    reservation.terms_accepted_at = timezone.now()
    reservation.terms_acceptance_source = (
        Reservation.TermsAcceptanceSource.CUSTOMER_ONLINE
    )


def _synchronize_reservation_charge(reservation: Reservation) -> Charge:
    charge = Charge.objects.select_for_update().get(pk=reservation.charge_id)
    charge.promotion = reservation.promotion
    charge.promotion_discount_amount = reservation.discount_amount
    charge.promotion_code = reservation.promotion_code
    charge.promotion_discount_type = reservation.promotion_discount_type
    charge.promotion_value = reservation.promotion_value
    charge.due_at = reservation.offer_expires_at
    charge.save(
        update_fields=[
            'promotion',
            'promotion_discount_amount',
            'promotion_code',
            'promotion_discount_type',
            'promotion_value',
            'due_at',
            'updated_at',
        ]
    )
    return refresh_charge_status(charge.pk)


def _save_reservation_checkout_state(
    reservation: Reservation,
    payable_amount,
):
    reservation.status = (
        Reservation.Status.CONFIRMED
        if payable_amount == 0
        else Reservation.Status.PENDING_PAYMENT
    )
    reservation.confirmed_at = (
        reservation.terms_accepted_at
        if payable_amount == 0
        else None
    )
    reservation.full_clean()
    reservation.save(
        update_fields=[
            'promotion',
            'discount_amount',
            'payment_amount',
            'promotion_code',
            'promotion_discount_type',
            'promotion_value',
            'terms',
            'terms_accepted_at',
            'terms_acceptance_source',
            'status',
            'confirmed_at',
            'updated_at',
        ],
    )


def _prepare_reservation_payment(
    *,
    reservation: Reservation,
    payment,
    payable_amount,
):
    if payment is None:
        Payment.objects.create(
            charge=reservation.charge,
            animal_reservation=reservation,
            provider=(
                Payment.Provider.COMPLIMENTARY
                if payable_amount == 0
                else Payment.Provider.STRIPE
            ),
            status=(
                Payment.Status.PAID
                if payable_amount == 0
                else Payment.Status.INITIALIZING
            ),
            amount=payable_amount,
            currency=reservation.currency,
            paid_at=(
                reservation.terms_accepted_at
                if payable_amount == 0
                else None
            ),
        )
    elif payment.status == Payment.Status.FAILED:
        _reset_payment_for_retry(
            payment,
            amount=payable_amount,
            currency=reservation.currency,
            complimentary=payable_amount == 0,
            paid_at=(
                reservation.terms_accepted_at
                if payable_amount == 0
                else None
            ),
        )



def _complete_complimentary_reservation(
    *,
    reservation: Reservation,
    pre_reservation,
):
    refresh_charge_status(reservation.charge_id)
    if pre_reservation is not None:
        pre_reservation.status = (
            PreReservation.Status.CONVERTED_TO_RESERVATION
        )
        pre_reservation.save(
            update_fields=['status', 'updated_at'],
        )
    transaction.on_commit(
        lambda pk=reservation.pk: notify_reservation_confirmed(
            Reservation.objects.get(pk=pk),
        )
    )


@transaction.atomic
def mark_reservation_payment_setup_failed(
    reservation_id: int,
    error_message: str,
):
    _, reservation = _lock_reservation_workflow(reservation_id)
    if reservation.status != Reservation.Status.PENDING_PAYMENT:
        return reservation
    reservation.status = Reservation.Status.PAYMENT_FAILED
    reservation.save(update_fields=['status', 'updated_at'])
    payment = Payment.objects.select_for_update().get(
        animal_reservation=reservation,
    )
    payment.status = Payment.Status.FAILED
    payment.failed_at = timezone.now()
    payment.last_error = error_message[:2000]
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
    return reservation


@transaction.atomic
def cancel_reservation_by_admin(
    *,
    reservation_id: int,
    admin_user,
    reason: str,
) -> Reservation:
    pre_reservation, reservation = _lock_reservation_workflow(reservation_id)
    if reservation.status not in {
        Reservation.Status.OFFERED,
        Reservation.Status.PENDING_PAYMENT,
        Reservation.Status.PAYMENT_FAILED,
        Reservation.Status.CONFIRMED,
    }:
        raise ReservationUnavailable(_('This reservation is already closed.'))
    _close_reservation(
        reservation,
        status=Reservation.Status.CANCELLED_BY_ADMIN,
        cancelled_by=admin_user,
        reason=reason,
    )
    transaction.on_commit(
        lambda pk=reservation.pk: notify_reservation_cancelled(
            Reservation.objects.select_related('pre_reservation').get(pk=pk),
        )
    )
    return reservation


def _close_reservation(
    reservation: Reservation,
    *,
    status: str,
    cancelled_by,
    reason,
):
    now = timezone.now()
    reservation.status = status
    reservation.cancelled_at = now
    reservation.cancelled_by = cancelled_by
    reservation.cancellation_reason = str(reason).strip()
    reservation.save(
        update_fields=[
            'status',
            'cancelled_at',
            'cancelled_by',
            'cancellation_reason',
            'updated_at',
        ],
    )
    if (
        reservation.pre_reservation_id
        and reservation.pre_reservation.status
        == PreReservation.Status.ACCEPTED
    ):
        reservation.pre_reservation.status = (
            PreReservation.Status.CANCELLED_BY_ADMIN
        )
        reservation.pre_reservation.cancelled_at = now
        reservation.pre_reservation.cancelled_by = cancelled_by
        reservation.pre_reservation.cancellation_reason = str(reason).strip()
        reservation.pre_reservation.save(
            update_fields=[
                'status',
                'cancelled_at',
                'cancelled_by',
                'cancellation_reason',
                'updated_at',
            ],
        )
    _close_sale_case(
        reservation.sale_case,
        reason=str(reason),
    )
    payment = Payment.objects.select_for_update().filter(
        animal_reservation=reservation,
    ).first()
    if payment and payment.status in {
        Payment.Status.INITIALIZING,
        Payment.Status.PENDING,
    }:
        _mark_unpaid_payment_failed(payment)
    if reservation.charge_id:
        void_charge(
            charge=reservation.charge,
            reason=str(reason),
        )


@transaction.atomic
def expire_reservation_offer(reservation_id: int) -> Reservation:
    _, reservation = _lock_reservation_workflow(reservation_id)
    if (
        reservation.status
        not in {
            Reservation.Status.OFFERED,
            Reservation.Status.PENDING_PAYMENT,
            Reservation.Status.PAYMENT_FAILED,
        }
        or reservation.offer_expires_at is None
        or reservation.offer_expires_at > timezone.now()
    ):
        return reservation
    _expire_reservation_offer_locked(reservation)
    return reservation


def _expire_reservation_offer_locked(reservation: Reservation):
    now = timezone.now()
    reservation.status = Reservation.Status.EXPIRED
    reservation.expired_at = now
    reservation.save(
        update_fields=['status', 'expired_at', 'updated_at'],
    )
    if reservation.pre_reservation_id:
        pre_reservation = PreReservation.objects.select_for_update().get(
            pk=reservation.pre_reservation_id,
        )
        if pre_reservation.status == PreReservation.Status.ACCEPTED:
            pre_reservation.status = (
                PreReservation.Status.RESERVATION_OFFER_EXPIRED
            )
            pre_reservation.save(update_fields=['status', 'updated_at'])
    payment = Payment.objects.select_for_update().filter(
        animal_reservation=reservation,
    ).first()
    if payment and payment.status in {
        Payment.Status.INITIALIZING,
        Payment.Status.PENDING,
    }:
        _mark_unpaid_payment_failed(payment)
    _close_sale_case(
        reservation.sale_case,
        reason=str(_('Reservation offer expired.')),
    )
    if reservation.charge_id:
        void_charge(
            charge=reservation.charge,
            reason=str(_('Reservation offer expired.')),
        )
    transaction.on_commit(
        lambda pk=reservation.pk: notify_reservation_offer_expired(
            Reservation.objects.get(pk=pk),
        )
    )


def _ensure_animal_still_matches_snapshot(
    animal: Animal,
    pre_reservation: PreReservation,
):
    reason = None
    if not animal.active or not animal.for_sale or animal.is_sold:
        reason = _('This dog is no longer available.')
    elif (
        pre_reservation.sale_case
        and pre_reservation.sale_case.origin
        in {
            AnimalSaleCase.Origin.ADMIN,
            AnimalSaleCase.Origin.TRANSFER,
        }
    ):
        # Staff workflows preserve the agreed snapshot and do not depend on
        # the public online pre-reservation switch.
        reason = None
    elif not animal.pre_reservation_enabled:
        reason = _('This dog is not available for reservation.')
    elif animal.current_price_in_euros is None:
        reason = _('This dog no longer has a published price.')
    elif money(animal.current_price_in_euros) != pre_reservation.animal_price_amount:
        reason = _(
            'The dog price changed after payment. Review the pre-reservation '
            'before accepting it.'
        )
    elif (
        money(animal.reservation_deposit_percentage)
        != pre_reservation.reservation_deposit_percentage
    ):
        reason = _(
            'The reservation deposit percentage changed after payment. '
            'Review the pre-reservation before accepting it.'
        )
    if reason:
        raise ReservationUnavailable(reason)


def _reset_payment_for_retry(
    payment: Payment,
    *,
    amount: decimal.Decimal,
    currency: str,
    complimentary: bool,
    paid_at,
):
    payment.provider = (
        Payment.Provider.COMPLIMENTARY
        if complimentary
        else Payment.Provider.STRIPE
    )
    payment.status = (
        Payment.Status.PAID
        if complimentary
        else Payment.Status.INITIALIZING
    )
    payment.amount = amount
    payment.currency = currency
    payment.checkout_attempt_number += 1
    payment.stripe_checkout_session_id = None
    payment.checkout_started_at = None
    payment.stripe_payment_intent_id = None
    payment.stripe_charge_id = None
    payment.stripe_checkout_url = ''
    payment.stripe_checkout_expires_at = None
    payment.provider_fee_amount = None
    payment.provider_net_amount = None
    payment.paid_at = paid_at
    payment.failed_at = None
    payment.last_error = ''
    payment.save()


def _mark_unpaid_payment_failed(payment: Payment):
    payment.status = Payment.Status.FAILED
    payment.failed_at = timezone.now()
    payment.stripe_checkout_url = ''
    payment.save(
        update_fields=[
            'status',
            'failed_at',
            'stripe_checkout_url',
            'updated_at',
        ],
    )


def _lock_reservation_workflow(reservation_id: int, *, user=None):
    references = Reservation.objects.filter(pk=reservation_id)
    if user is not None:
        references = references.filter(
            Q(sale_case__user=user) | Q(pre_reservation__user=user),
        )
    reference = references.values(
        'pre_reservation_id',
        'sale_case_id',
    ).get()
    sale_case = AnimalSaleCase.objects.select_for_update().get(
        pk=reference['sale_case_id'],
    )
    pre_reservation = None
    if reference['pre_reservation_id']:
        pre_reservation = PreReservation.objects.select_for_update().get(
            pk=reference['pre_reservation_id'],
        )
    reservation = Reservation.objects.select_for_update().get(
        pk=reservation_id,
    )
    reservation.sale_case = sale_case
    if pre_reservation is not None:
        reservation.pre_reservation = pre_reservation
    return pre_reservation, reservation


def _close_sale_case(sale_case, *, reason):
    if sale_case is None:
        return
    sale_case.status = AnimalSaleCase.Status.CLOSED
    sale_case.closed_at = timezone.now()
    sale_case.save(
        update_fields=['status', 'closed_at', 'updated_at'],
    )
