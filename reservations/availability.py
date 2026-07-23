from django.db.models import Count, Exists, OuterRef, Q
from django.utils.translation import gettext_lazy as _

from breeding.models import Animal, Litter

from .exceptions import ReservationUnavailable
from .models import PreReservation


CAPACITY_CONSUMING_STATUSES = (
    PreReservation.Status.PENDING_PAYMENT,
    PreReservation.Status.CONFIRMED,
    PreReservation.Status.FULFILLED,
)


def capacity_consuming_reservations():
    return PreReservation.objects.filter(status__in=CAPACITY_CONSUMING_STATUSES)


def annotate_dog_availability(queryset):
    blocking = capacity_consuming_reservations().filter(animal_id=OuterRef('pk'))
    return queryset.annotate(
        has_blocking_pre_reservation=Exists(blocking),
    )


def annotate_litter_availability(queryset):
    return queryset.annotate(
        pre_reserved_count=Count(
            'pre_reservations',
            filter=Q(pre_reservations__status__in=CAPACITY_CONSUMING_STATUSES),
            distinct=True,
        )
    )


def dog_has_blocking_reservation(animal: Animal) -> bool:
    annotated = getattr(animal, 'has_blocking_pre_reservation', None)
    if annotated is not None:
        return annotated
    return capacity_consuming_reservations().filter(animal=animal).exists()


def litter_reserved_count(litter: Litter) -> int:
    annotated = getattr(litter, 'pre_reserved_count', None)
    if annotated is not None:
        return annotated
    return capacity_consuming_reservations().filter(litter=litter).count()


def ensure_dog_is_available(animal: Animal):
    reason = dog_unavailability_reason(animal)
    if reason:
        raise ReservationUnavailable(reason)


def dog_unavailability_reason(animal: Animal):
    if not animal.active or not animal.for_sale or animal.sold_at:
        return _('This dog is no longer available.')
    if not animal.pre_reservation_enabled:
        return _('This dog is not available for pre-reservation.')
    if dog_has_blocking_reservation(animal):
        return _('This dog is already reserved.')
    return None


def ensure_litter_has_capacity(litter: Litter, *, user=None):
    reason = litter_unavailability_reason(litter, user=user)
    if reason:
        raise ReservationUnavailable(reason)


def litter_unavailability_reason(litter: Litter, *, user=None):
    if not litter.active or not litter.pre_reservation_enabled:
        return _('This litter is not available for pre-reservation.')
    if litter.status not in {
        Litter.LitterStatus.BORN,
        Litter.LitterStatus.READY,
    }:
        return _('Pre-reservations open only after the babies are born.')
    if not litter.babies or litter.pre_reservation_capacity <= 0:
        return _('This litter has no places available for pre-reservation.')

    if litter_reserved_count(litter) >= litter.pre_reservation_capacity:
        return _('This litter is fully reserved.')
    if (
        user is not None
        and capacity_consuming_reservations().filter(
            litter=litter,
            user=user,
        ).exists()
    ):
        return _('You already have an active pre-reservation for this litter.')
    return None
