"""Public, read-only catalogue queries used by chat experts."""

from django.db.models import F, Q


def public_animals():
    from breeding.models import Animal
    from reservations.availability import annotate_dog_availability

    queryset = (
        Animal.objects.filter(active=True)
        .filter(
            Q(for_sale=True)
            | Q(for_breeding=True)
            | Q(litter_father__active=True)
            | Q(litter_mother__active=True)
        )
        .select_related("breed", "breed__kind", "father", "mother", "litter")
        .prefetch_related("animal_certifications__certification")
        .distinct()
    )
    return annotate_dog_availability(queryset)


def available_animals(*, animal_kind_id=None):
    from breeding.models import Animal
    from reservations.availability import annotate_dog_availability

    queryset = (
        Animal.animals_for_sale.filter(sold_at__isnull=True)
        .select_related("breed", "breed__kind")
        .prefetch_related("animal_certifications__certification")
    )
    if animal_kind_id is not None:
        queryset = queryset.filter(breed__kind_id=animal_kind_id)
    return annotate_dog_availability(queryset).filter(
        has_blocking_pre_reservation=False
    )


def current_litters(*, animal_kind_id=None):
    from breeding.models import Litter
    from reservations.availability import annotate_litter_availability

    queryset = (
        Litter.litters_for_sale
        .exclude(status=Litter.LitterStatus.COMPLETED)
        .select_related("breed", "breed__kind", "father", "mother")
    )
    if animal_kind_id is not None:
        queryset = queryset.filter(breed__kind_id=animal_kind_id)
    return annotate_litter_availability(queryset)


def reservable_litters(*, animal_kind_id=None):
    from breeding.models import Litter

    return (
        current_litters(animal_kind_id=animal_kind_id)
        .select_related(None)
        .select_related("breed", "breed__kind")
        .filter(
            pre_reservation_enabled=True,
            status__in=(
                Litter.LitterStatus.BORN,
                Litter.LitterStatus.READY,
            ),
            babies__gt=0,
            pre_reservation_capacity__gt=0,
            pre_reserved_count__lt=F("pre_reservation_capacity"),
        )
    )


def public_breeds():
    from breeding.models import Breed

    return Breed.objects.filter(active=True).select_related("parent", "kind")


def public_animal_kinds():
    from breeding.models import AnimalKind

    return AnimalKind.objects.filter(breed__active=True).distinct()


def public_certifications():
    from breeding.models import Certification

    return (
        Certification.objects
        .select_related("parent")
        .order_by("order")
    )


def published_posts():
    """Return only titles from posts that are public on the website."""
    from blog.models import Post

    return Post.posts_published.only("id", "title")
