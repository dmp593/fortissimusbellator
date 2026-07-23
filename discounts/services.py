import decimal
from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from breeding.models import Animal, Litter

from .models import Promotion


class PromotionUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PromotionQuote:
    promotion: Promotion | None
    discount_amount: decimal.Decimal


def quote_promotion(
    *,
    code: str,
    target: Animal | Litter,
    user,
    fee: decimal.Decimal,
    lock: bool = False,
) -> PromotionQuote:
    normalized_code = Promotion.normalize_code(code)
    if not normalized_code:
        return PromotionQuote(None, decimal.Decimal('0.00'))

    promotions = Promotion.objects
    if lock:
        promotions = promotions.select_for_update()

    try:
        promotion = promotions.get(code=normalized_code)
    except Promotion.DoesNotExist as exc:
        raise PromotionUnavailable(_('This promotion code is not valid.')) from exc

    _validate_schedule(promotion)
    _validate_scope(promotion, target)
    _validate_usage_limits(promotion, user)

    if promotion.discount_type == Promotion.DiscountType.PERCENTAGE:
        discount = fee * promotion.value / decimal.Decimal('100')
    else:
        discount = promotion.value

    discount = discount.quantize(
        decimal.Decimal('0.01'),
        rounding=decimal.ROUND_HALF_UP,
    )
    return PromotionQuote(promotion, min(fee, discount))


def _validate_schedule(promotion: Promotion):
    now = timezone.now()
    if not promotion.active:
        raise PromotionUnavailable(_('This promotion code is not active.'))
    if promotion.starts_at and now < promotion.starts_at:
        raise PromotionUnavailable(_('This promotion has not started yet.'))
    if promotion.ends_at and now >= promotion.ends_at:
        raise PromotionUnavailable(_('This promotion has expired.'))


def _validate_scope(promotion: Promotion, target: Animal | Litter):
    scope = promotion.scope
    applies = scope == Promotion.Scope.ANY

    if scope == Promotion.Scope.DOGS:
        applies = isinstance(target, Animal)
    elif scope == Promotion.Scope.LITTERS:
        applies = isinstance(target, Litter)
    elif scope == Promotion.Scope.BREEDS:
        applies = promotion.breeds.filter(pk=target.breed_id).exists()
    elif scope == Promotion.Scope.SPECIFIC_DOGS:
        applies = (
            isinstance(target, Animal)
            and promotion.dogs.filter(pk=target.pk).exists()
        )
    elif scope == Promotion.Scope.SPECIFIC_LITTERS:
        applies = (
            isinstance(target, Litter)
            and promotion.litters.filter(pk=target.pk).exists()
        )

    if not applies:
        raise PromotionUnavailable(
            _('This promotion does not apply to this pre-reservation.')
        )


def _validate_usage_limits(promotion: Promotion, user):
    from reservations.models import Payment, PreReservation

    paid_payment_statuses = (
        Payment.Status.PAID,
        Payment.Status.REFUND_PENDING,
        Payment.Status.REFUND_FAILED,
        Payment.Status.REFUNDED,
    )
    reservations = promotion.pre_reservations.filter(
        Q(
            status__in=(
                PreReservation.Status.PENDING_PAYMENT,
                PreReservation.Status.CONFIRMED,
                PreReservation.Status.FULFILLED,
            )
        )
        | Q(payment__status__in=paid_payment_statuses)
    ).distinct()

    if (
        promotion.max_redemptions is not None
        and reservations.count() >= promotion.max_redemptions
    ):
        raise PromotionUnavailable(_('This promotion has reached its limit.'))

    if (
        promotion.max_redemptions_per_user is not None
        and reservations.filter(user=user).count()
        >= promotion.max_redemptions_per_user
    ):
        raise PromotionUnavailable(
            _('You have already used this promotion the maximum number of times.')
        )

