"""Public, read-only catalogue queries used by chat experts."""

from django.db.models import Q


def public_dogs():
    from breeding.models import Animal

    return (
        Animal.objects.filter(active=True)
        .filter(
            Q(for_sale=True)
            | Q(for_breeding=True)
            | Q(litter_father__active=True)
            | Q(litter_mother__active=True)
        )
        .select_related("breed", "father", "mother", "litter")
        .prefetch_related("certifications")
        .distinct()
    )


def available_dogs():
    from breeding.models import Animal

    return (
        Animal.animals_for_sale.filter(sold_at__isnull=True)
        .select_related("breed")
        .prefetch_related("certifications")
    )


def current_litters():
    from breeding.models import Litter

    return (
        Litter.litters_for_sale
        .exclude(status=Litter.LitterStatus.COMPLETED)
        .select_related("breed", "father", "mother")
    )


def public_breeds():
    from breeding.models import Breed

    return Breed.objects.filter(active=True).select_related("parent", "kind")

