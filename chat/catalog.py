"""Public, read-only catalogue queries used by chat experts."""

from django.db.models import Q


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
        Animal.animals_for_sale
        .select_related("breed", "breed__kind")
        .prefetch_related("animal_certifications__certification")
    )
    if animal_kind_id is not None:
        queryset = queryset.filter(breed__kind_id=animal_kind_id)
    return annotate_dog_availability(queryset).filter(
        has_completed_sale=False,
        has_blocking_pre_reservation=False,
        has_confirmed_reservation=False,
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
    # Kept as a stable catalogue API for intent routing. Litter places are no
    # longer sold; customers subscribe to birth alerts instead.
    return current_litters(animal_kind_id=animal_kind_id).none()


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


def public_faqs():
    from frontoffice.models import FrequentlyAskedQuestion

    return FrequentlyAskedQuestion.objects.filter(active=True).order_by("order")


def published_posts():
    """Return only titles from posts that are public on the website."""
    from blog.models import Post

    return Post.posts_published.only("id", "title")
