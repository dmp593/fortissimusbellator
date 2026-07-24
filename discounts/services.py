import decimal
from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from breeding.models import Animal

from .models import Promotion


class PromotionUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PromotionQuote:
    promotion: Promotion | None
    subtotal_amount: decimal.Decimal
    discount_amount: decimal.Decimal

    @property
    def total_amount(self):
        return self.subtotal_amount - self.discount_amount


def quote_promotion(
    *,
    code: str,
    target: Animal,
    user,
    fee: decimal.Decimal,
    purchase_stage: str = Promotion.PurchaseStage.PRE_RESERVATION,
    purchase=None,
    lock: bool = False,
) -> PromotionQuote:
    fee = decimal.Decimal(fee).quantize(decimal.Decimal('0.01'))
    normalized_code = Promotion.normalize_code(code)
    if not normalized_code:
        return PromotionQuote(None, fee, decimal.Decimal('0.00'))

    promotions = Promotion.objects
    if lock:
        promotions = promotions.select_for_update()

    try:
        promotion = promotions.get(code=normalized_code)
    except Promotion.DoesNotExist as exc:
        raise PromotionUnavailable(_('This promotion code is not valid.')) from exc

    _validate_schedule(promotion)
    _validate_purchase_stage(promotion, purchase_stage)
    _validate_scope(promotion, target)
    _validate_usage_limits(promotion, user, purchase=purchase)

    if fee <= 0:
        raise PromotionUnavailable(
            _('There is no remaining amount to discount.')
        )

    if promotion.discount_type == Promotion.DiscountType.PERCENTAGE:
        discount = fee * promotion.value / decimal.Decimal('100')
    else:
        discount = promotion.value

    discount = discount.quantize(
        decimal.Decimal('0.01'),
        rounding=decimal.ROUND_HALF_UP,
    )
    return PromotionQuote(promotion, fee, min(fee, discount))


def _validate_schedule(promotion: Promotion):
    now = timezone.now()
    if not promotion.active:
        raise PromotionUnavailable(_('This promotion code is not active.'))
    if promotion.starts_at and now < promotion.starts_at:
        raise PromotionUnavailable(_('This promotion has not started yet.'))
    if promotion.ends_at and now >= promotion.ends_at:
        raise PromotionUnavailable(_('This promotion has expired.'))


def _validate_purchase_stage(promotion: Promotion, purchase_stage: str):
    valid_stages = {
        Promotion.PurchaseStage.PRE_RESERVATION,
        Promotion.PurchaseStage.RESERVATION,
    }
    if purchase_stage not in valid_stages:
        raise ValueError(f'Unsupported promotion purchase stage: {purchase_stage}')
    if promotion.purchase_stage in {
        Promotion.PurchaseStage.BOTH,
        purchase_stage,
    }:
        return
    if purchase_stage == Promotion.PurchaseStage.PRE_RESERVATION:
        raise PromotionUnavailable(
            _('This promotion cannot be used for a pre-reservation.')
        )
    raise PromotionUnavailable(
        _('This promotion cannot be used for a reservation.')
    )


def _validate_scope(promotion: Promotion, target: Animal):
    scope = promotion.scope
    applies = scope == Promotion.Scope.ANY

    if scope == Promotion.Scope.BREEDS:
        applies = promotion.breeds.filter(pk=target.breed_id).exists()
    elif scope == Promotion.Scope.SPECIFIC_DOGS:
        applies = (
            isinstance(target, Animal)
            and promotion.dogs.filter(pk=target.pk).exists()
        )

    if not applies:
        raise PromotionUnavailable(
            _('This promotion does not apply to this dog.')
        )


def _validate_usage_limits(promotion: Promotion, user, *, purchase=None):
    from reservations.models import Charge, Payment, PreReservation, Reservation

    paid_payment_statuses = (
        Payment.Status.PAID,
        Payment.Status.PARTIALLY_REFUNDED,
        Payment.Status.REFUNDED,
    )
    pre_reservations = promotion.pre_reservations.filter(
        Q(
            status__in=(
                PreReservation.Status.PENDING_PAYMENT,
                PreReservation.Status.AWAITING_REVIEW,
                PreReservation.Status.ACCEPTED,
            )
        )
        | Q(charge__status=Charge.Status.PAID)
        | Q(payment__status__in=paid_payment_statuses)
    ).distinct()
    reservations = promotion.reservations.filter(
        Q(status=Reservation.Status.PENDING_PAYMENT)
        | Q(charge__status=Charge.Status.PAID)
        | Q(payment__status__in=paid_payment_statuses)
    ).distinct()

    if isinstance(purchase, PreReservation):
        pre_reservations = pre_reservations.exclude(pk=purchase.pk)
    elif isinstance(purchase, Reservation):
        reservations = reservations.exclude(pk=purchase.pk)

    redemption_count = pre_reservations.count() + reservations.count()

    if (
        promotion.max_redemptions is not None
        and redemption_count >= promotion.max_redemptions
    ):
        raise PromotionUnavailable(_('This promotion has reached its limit.'))

    user_redemption_count = (
        pre_reservations.filter(user=user).count()
        + reservations.filter(
            Q(pre_reservation__user=user) | Q(sale_case__user=user)
        ).count()
    )
    if (
        promotion.max_redemptions_per_user is not None
        and user_redemption_count >= promotion.max_redemptions_per_user
    ):
        raise PromotionUnavailable(
            _('You have already used this promotion the maximum number of times.')
        )
