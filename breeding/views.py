from datetime import datetime
from dateutil.relativedelta import relativedelta

from django.utils.translation import gettext_lazy as _
from django.shortcuts import render, redirect
from django.core.paginator import Paginator

from breeding.models import Animal, Litter


def our_dogs(request, breed_id: int | None = None):
    animals = Animal.animals_for_breeding.all()

    if breed_id:
        animals = animals.filter(breed=breed_id)

    animals = animals.order_by('order')
    return render(request, 'our_dogs/index.html', {'dogs': animals})


def buy_a_dog(request):
    dogs = Animal.animals_for_sale.all()

    # Filters
    breed_filter = request.GET.get('breed')
    gender_filter = request.GET.get('gender')
    age_filter = request.GET.get('age')
    has_training = request.GET.get('has_training')
    has_certifications = request.GET.get('has_certifications')

    if breed_filter:
        dogs = dogs.filter(breed_id=breed_filter)

    if gender_filter:
        dogs = dogs.filter(gender=gender_filter)

    if age_filter:
        today = datetime.today()
        today_minus_6_months = today - relativedelta(months=6)
        today_minus_12_months = today - relativedelta(months=12)

        if age_filter == 'puppy':
            dogs = dogs.filter(birth_date__gte=today_minus_6_months)
        elif age_filter == 'junior':
            dogs = dogs.filter(birth_date__range=(today_minus_6_months, today_minus_12_months))
        elif age_filter == 'adult':
            dogs = dogs.filter(birth_date__lte=today_minus_12_months)

    if has_training == 'on':
        dogs = dogs.filter(has_training=True)

    if has_certifications == 'on':
        dogs = dogs.filter(certifications__isnull=False)

    # Pagination
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 12))

    if per_page <= 0:
        per_page = 1

    paginator = Paginator(dogs, per_page)
    paginated_dogs = paginator.get_page(page)

    context = {
        'dogs': paginated_dogs,
        'pagination': {
            'has_more': paginated_dogs.has_next(),  # Show "Load More" if there are more pages
            'next_page': paginated_dogs.next_page_number() if paginated_dogs.has_next() else None,
            'total_pages': paginator.num_pages,
        },
        'filters': {
            'breed': breed_filter,
            'gender': gender_filter,
            'age': age_filter,
            'has_training': has_training,
            'has_certifications': has_certifications,
        },
    }

    if request.headers.get('X-Load-More'):
        return render(request, 'buy_a_dog/partials/cards.html', context)

    return render(request, 'buy_a_dog/index.html', context)


def dog_detail(request, dog_id: int):
    try:
        dog = Animal.animals_for_sale.get(pk=dog_id)
        return render(request, 'buy_a_dog/detail.html', {'dog': dog})
    except Animal.DoesNotExist:
        return redirect('buy_a_dog')


def upcoming_litters(request):
    litters = Litter.litters_for_sale.all()

    # Filters
    breed_filter = request.GET.get('breed')

    if breed_filter:
        litters = litters.filter(breed_id=breed_filter)

    # Pagination
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 12))

    if per_page <= 0:
        per_page = 1

    paginator = Paginator(litters, per_page)
    paginated_litters = paginator.get_page(page)

    # Check if it's an HTMX request (Load More button clicked)
    if request.headers.get('X-Load-More'):
        return render(request, 'upcoming_litters/partials/cards.html', {'litters': paginated_litters})

    context = {
        'litters': paginated_litters,
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
        litter = Litter.litters_for_sale.get(pk=litter_id)
        return render(request, 'upcoming_litters/detail.html', {'litter': litter})
    except Litter.DoesNotExist:
        return redirect('upcoming_litters')

