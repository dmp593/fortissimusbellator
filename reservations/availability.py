from django.db.models import Exists, OuterRef, Q, Value
from django.db.models.fields import BooleanField
from django.utils.translation import gettext_lazy as _

from breeding.models import Animal

from .exceptions import ReservationUnavailable
from .models import AnimalSale, AnimalSaleCase, PreReservation, Reservation


PRE_RESERVATION_BLOCKING_STATUSES = (
    PreReservation.Status.PENDING_PAYMENT,
    PreReservation.Status.AWAITING_REVIEW,
    PreReservation.Status.ACCEPTED,
)
RESERVATION_BLOCKING_STATUSES = (
    Reservation.Status.OFFERED,
    Reservation.Status.PENDING_PAYMENT,
    Reservation.Status.PAYMENT_FAILED,
    Reservation.Status.CONFIRMED,
)


def capacity_consuming_reservations():
    """Return pre-reservations that currently hold a dog."""
    return PreReservation.objects.filter(
        status__in=PRE_RESERVATION_BLOCKING_STATUSES,
    )


def inventory_blocking_reservations():
    return Reservation.objects.filter(
        status__in=RESERVATION_BLOCKING_STATUSES,
    )


def completed_animal_sales():
    return AnimalSale.objects.filter(
        voided_at__isnull=True,
    )


def annotate_dog_availability(queryset):
    completed_sales = completed_animal_sales().filter(
        sale_case__animal_id=OuterRef('pk'),
    )
    blocking_cases = AnimalSaleCase.objects.filter(
        animal_id=OuterRef('pk'),
        status__in=(
            AnimalSaleCase.Status.PRE_RESERVATION,
            AnimalSaleCase.Status.RESERVATION,
        ),
    )
    confirmed_reservations = inventory_blocking_reservations().filter(
        Q(sale_case__animal_id=OuterRef('pk'))
        | Q(pre_reservation__animal_id=OuterRef('pk')),
        status=Reservation.Status.CONFIRMED,
    )
    pre_reserved_cases = blocking_cases.filter(
        Q(reservation__isnull=True)
        | ~Q(reservation__status=Reservation.Status.CONFIRMED),
    )
    blocking_legacy_pre_reservations = (
        capacity_consuming_reservations()
        .filter(
            animal_id=OuterRef('pk'),
            sale_case__isnull=True,
        )
        .exclude(reservation__status=Reservation.Status.CONFIRMED)
    )
    return queryset.annotate(
        has_completed_sale=Exists(completed_sales),
        has_blocking_sale_case=Exists(blocking_cases),
        has_blocking_pre_reservation=Exists(
            pre_reserved_cases,
        )
        | Exists(blocking_legacy_pre_reservations),
        has_confirmed_reservation=Exists(confirmed_reservations),
    )


def available_dogs_for_new_sale_process(queryset=None):
    """Return dogs that can safely start a new commercial workflow."""
    queryset = queryset if queryset is not None else Animal.objects.all()
    return annotate_dog_availability(queryset).filter(
        active=True,
        for_sale=True,
        has_completed_sale=False,
        has_blocking_sale_case=False,
        has_blocking_pre_reservation=False,
        has_confirmed_reservation=False,
    )


def annotate_litter_availability(queryset):
    """Compatibility annotation for pages that no longer sell litter places."""
    return queryset.annotate(
        has_blocking_pre_reservation=Value(
            False,
            output_field=BooleanField(),
        ),
    )


def dog_has_blocking_pre_reservation(
    animal: Animal,
    *,
    exclude_sale_case_id=None,
) -> bool:
    annotated = getattr(animal, 'has_blocking_pre_reservation', None)
    if annotated is not None and exclude_sale_case_id is None:
        return annotated
    cases = AnimalSaleCase.objects.filter(
        animal=animal,
        status__in=(
            AnimalSaleCase.Status.PRE_RESERVATION,
            AnimalSaleCase.Status.RESERVATION,
        ),
    ).filter(
        Q(reservation__isnull=True)
        | ~Q(reservation__status=Reservation.Status.CONFIRMED),
    )
    if exclude_sale_case_id:
        cases = cases.exclude(pk=exclude_sale_case_id)
    if cases.exists():
        return True
    return (
        capacity_consuming_reservations()
        .filter(
            animal=animal,
            sale_case__isnull=True,
        )
        .exclude(reservation__status=Reservation.Status.CONFIRMED)
        .exists()
    )


def dog_has_confirmed_reservation(
    animal: Animal,
    *,
    exclude_sale_case_id=None,
) -> bool:
    annotated = getattr(animal, 'has_confirmed_reservation', None)
    if annotated is not None and exclude_sale_case_id is None:
        return annotated
    reservations = inventory_blocking_reservations().filter(
        Q(sale_case__animal=animal) | Q(pre_reservation__animal=animal),
        status=Reservation.Status.CONFIRMED,
    )
    if exclude_sale_case_id:
        reservations = reservations.exclude(sale_case_id=exclude_sale_case_id)
    return reservations.exists()


def dog_has_blocking_reservation(
    animal: Animal,
    *,
    exclude_sale_case_id=None,
) -> bool:
    annotated = getattr(animal, 'has_blocking_sale_case', None)
    if annotated is not None and exclude_sale_case_id is None:
        return annotated
    cases = AnimalSaleCase.objects.filter(
        animal=animal,
        status__in=(
            AnimalSaleCase.Status.PRE_RESERVATION,
            AnimalSaleCase.Status.RESERVATION,
        ),
    )
    if exclude_sale_case_id:
        cases = cases.exclude(pk=exclude_sale_case_id)
    if cases.exists():
        return True
    return capacity_consuming_reservations().filter(
        animal=animal,
        sale_case__isnull=True,
    ).exists()


def ensure_dog_is_available(animal: Animal, *, exclude_sale_case_id=None):
    reason = dog_unavailability_reason(
        animal,
        exclude_sale_case_id=exclude_sale_case_id,
    )
    if reason:
        raise ReservationUnavailable(reason)


def dog_unavailability_reason(animal: Animal, *, exclude_sale_case_id=None):
    inventory_reason = dog_inventory_unavailability_reason(
        animal,
        exclude_sale_case_id=exclude_sale_case_id,
    )
    if inventory_reason:
        return inventory_reason
    if not animal.pre_reservation_enabled:
        return _('This dog is not available for pre-reservation.')
    if (
        animal.price_in_euros is None
        or animal.current_price_in_euros is None
        or animal.current_price_in_euros <= 0
    ):
        return _('Dogs without a published price cannot be pre-reserved.')
    return None


def dog_inventory_unavailability_reason(
    animal: Animal,
    *,
    exclude_sale_case_id=None,
):
    if not animal.active or not animal.for_sale or animal.is_sold:
        return _('This dog is no longer available.')
    if dog_has_confirmed_reservation(
        animal,
        exclude_sale_case_id=exclude_sale_case_id,
    ):
        return _('This dog is already reserved.')
    if dog_has_blocking_reservation(
        animal,
        exclude_sale_case_id=exclude_sale_case_id,
    ):
        return _('This dog is already pre-reserved.')
    return None


def ensure_litter_has_capacity(*args, **kwargs):
    raise ReservationUnavailable(
        _(
            'Litters cannot be pre-reserved. Subscribe to birth updates and '
            'pre-reserve an individual dog after it is published.'
        )
    )


def litter_reserved_count(*args, **kwargs):
    return 0
