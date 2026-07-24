from datetime import date
from dateutil.relativedelta import relativedelta

from django.utils.translation import gettext_lazy as _
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator

from fortissimusbellator.parsers import page_size, to_int
from breeding.models import Animal, Breed, Litter
from reservations.availability import (
    annotate_dog_availability,
)
from reservations.models import PreReservation
from reservations.views import reservation_checkout
from breeding.services.litter_alerts import (
    is_subscribed_to_litter,
    set_litter_subscription,
)
from django.contrib import messages
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST


def our_dogs(request, breed_id: int | None = None):
    animals = (
        Animal.animals_for_breeding
        .select_related("breed", "breed__kind")
        .prefetch_related("animal_certifications__certification")
    )

    if breed_id:
        animals = animals.filter(breed=breed_id)

    animals = animals.order_by('order')
    return render(request, 'our_dogs/index.html', {'dogs': animals})


def buy_a_dog(request):
    dogs = annotate_dog_availability(
        Animal.animals_for_sale
        .select_related("breed", "breed__kind")
        .prefetch_related("animal_certifications__certification")
    )
    filters = {
        name: request.GET.get(name)
        for name in (
            'breed',
            'gender',
            'hair_type',
            'age',
            'has_training',
            'has_certifications',
        )
    }
    dogs = _filter_sale_dogs(dogs, filters)
    page = to_int(request.GET.get('page'), or_default=1)
    per_page = page_size(request.GET.get('per_page'))
    paginator = Paginator(dogs, per_page)
    paginated_dogs = paginator.get_page(page)
    context = {
        'dogs': paginated_dogs,
        'breeds': Breed.objects_specific.all(),
        'pagination': _pagination_context(paginated_dogs, paginator),
        'filters': filters,
    }

    if request.headers.get('X-Load-More'):
        return render(request, 'buy_a_dog/partials/cards.html', context)

    return render(request, 'buy_a_dog/index.html', context)


def _filter_sale_dogs(dogs, filters):
    direct_filters = {
        'breed_id': filters['breed'],
        'gender': filters['gender'],
        'hair_type': filters['hair_type'],
    }
    for field_name, value in direct_filters.items():
        if value:
            dogs = dogs.filter(**{field_name: value})

    dogs = _filter_dogs_by_age(dogs, filters['age'])
    if filters['has_training'] == 'on':
        dogs = dogs.filter(has_training=True)
    if filters['has_certifications'] == 'on':
        dogs = dogs.filter(certifications__isnull=False).distinct()
    return dogs


def _filter_dogs_by_age(dogs, age_filter):
    if not age_filter:
        return dogs

    today = date.today()
    six_months_ago = today - relativedelta(months=6)
    twelve_months_ago = today - relativedelta(months=12)
    if age_filter == 'puppy':
        return dogs.filter(birth_date__gte=six_months_ago)
    if age_filter == 'junior':
        return dogs.filter(
            birth_date__range=(twelve_months_ago, six_months_ago)
        )
    if age_filter == 'adult':
        return dogs.filter(birth_date__lte=twelve_months_ago)
    return dogs


def _pagination_context(page, paginator):
    has_more = page.has_next()
    return {
        'has_more': has_more,
        'next_page': page.next_page_number() if has_more else None,
        'total_pages': paginator.num_pages,
    }


def dog_detail(request, dog_id: int):
    try:
        dog = annotate_dog_availability(
            Animal.animals_for_sale
            .select_related("breed", "breed__kind", "litter")
            .prefetch_related("animal_certifications__certification")
        ).get(pk=dog_id)
        return render(request, 'buy_a_dog/detail.html', {'dog': dog})
    except Animal.DoesNotExist:
        return redirect('breeding:buy_a_dog')


def upcoming_litters(request):
    litters = Litter.litters_for_sale.all()

    # Filters
    breed_filter = request.GET.get('breed')

    if breed_filter:
        litters = litters.filter(breed=breed_filter)

    # Pagination
    page = to_int(request.GET.get('page'), or_default=1)
    per_page = page_size(request.GET.get('per_page'))

    paginator = Paginator(litters, per_page)
    paginated_litters = paginator.get_page(page)

    # Check if it's an HTMX request (Load More button clicked)
    if request.headers.get('X-Load-More'):
        return render(request, 'upcoming_litters/partials/cards.html', {'litters': paginated_litters})

    context = {
        'litters': paginated_litters,
        'breeds': Breed.objects_specific.all(),  # overriding. see: context_processors.py
        'pagination': {
            'has_more': paginated_litters.has_next(),  # Show "Load More" if there are more pages
            'next_page': paginated_litters.next_page_number() if paginated_litters.has_next() else None,
            'total_pages': paginator.num_pages,
        },
        'filters': {
            'breed': breed_filter,
        },
    }

    return render(request, 'upcoming_litters/index.html', context)


def litter_detail(request, litter_id: int):
    try:
        litter = Litter.litters_for_sale.select_related('breed').get(
            pk=litter_id,
        )
        subscribed = (
            request.user.is_authenticated
            and is_subscribed_to_litter(
                user=request.user,
                litter=litter,
            )
        )
        return render(
            request,
            'upcoming_litters/detail.html',
            {
                'litter': litter,
                'birth_alert_subscribed': subscribed,
            },
        )
    except Litter.DoesNotExist:
        return redirect('breeding:upcoming_litters')


@login_required
def pre_reserve_dog(request, dog_id: int):
    return reservation_checkout(
        request,
        target_type=PreReservation.TargetType.DOG,
        target_id=dog_id,
    )


@login_required
@require_POST
def subscribe_litter_alert(request, litter_id: int):
    litter = get_object_or_404(Litter.litters_for_sale, pk=litter_id)
    set_litter_subscription(
        user=request.user,
        litter=litter,
        enabled=True,
        language_code=request.LANGUAGE_CODE,
    )
    messages.success(
        request,
        _('You will receive an email when this litter is born.'),
    )
    return redirect('breeding:litter_detail', litter_id)


@login_required
@require_POST
def unsubscribe_litter_alert(request, litter_id: int):
    litter = get_object_or_404(Litter.litters_for_sale, pk=litter_id)
    set_litter_subscription(
        user=request.user,
        litter=litter,
        enabled=False,
        language_code=request.LANGUAGE_CODE,
    )
    messages.success(request, _('Birth alerts for this litter were disabled.'))
    return redirect('breeding:litter_detail', litter_id)
