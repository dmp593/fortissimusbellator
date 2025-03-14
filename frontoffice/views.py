from smtplib import SMTPException
from datetime import datetime
from dateutil.relativedelta import relativedelta

from django.utils.translation import gettext_lazy as _
from django.shortcuts import render, redirect
from django.core.mail import EmailMessage
from django.core.paginator import Paginator
from django.conf import settings
from django.contrib import messages
from django.template.loader import render_to_string

from breeding.models import Animal, Breed, Litter

from attachments.models import Attachment
from .models import FrequentlyAskedQuestion
from .forms import ContactForm


def home(request):
    parent_breeds = Breed.objects.filter(parent__isnull=False).values_list('parent', flat=True)
    breeds = Breed.objects.exclude(id__in=parent_breeds)
    return render(request, 'home/index.html', {'breeds': breeds})


def about_us(request):
    gallery = Attachment.objects.filter(mime_type__startswith='image').order_by('?')[:20]
    return render(request, 'about_us.html', { 'gallery': gallery })


def send_contact_email(form):
    name = form.cleaned_data['name']
    email = form.cleaned_data['email']
    phone = form.cleaned_data['phone']
    message = form.cleaned_data['message']

    html_message = render_to_string(
        'emails/contact_us.html',
        {
            'name': name,
            'email': email,
            'phone': phone,
            'message': message,
        }
    )

    email = EmailMessage(
        subject=_(f"Contact Form Submission from {name}"),
        body=html_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=settings.RECIPIENT_LIST_ON_CONTACT_US_REQUEST,
    )

    email.content_subtype = "html"
    email.send(fail_silently=False)


def contact_us(request):
    if request.method != 'POST':
        return render(request, 'contact_us.html', {'form': ContactForm()})

    form = ContactForm(request.POST)

    if not form.is_valid():
        messages.error(request, _('Please fill out all fields.'))
        return render(request, 'contact_us.html', {'form': form})

    try:
        send_contact_email(form)
        messages.success(request, _('Thank you! We will get back to you soon.'))
    except SMTPException as e:
        print("Error sending email:", e)
        messages.error(request, _('Ups... Try again later.'))

    return redirect('contact_us')


def get_breeds():
    # Fetch breeds for the filter dropdown
    parent_breeds = Breed.objects.filter(parent__isnull=False).values_list('parent', flat=True)
    return Breed.objects.exclude(id__in=parent_breeds)


def buy_a_dog(request):
    breeds = get_breeds()
    dogs = Animal.dogs_for_sale.all()

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
        'breeds': breeds,
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


def dog_detail(request, dog_id):
    try:
        dog = Animal.dogs_for_sale.get(pk=dog_id)
        return render(request, 'buy_a_dog/detail.html', {'dog': dog})
    except Animal.DoesNotExist:
        return redirect('buy_a_dog')


def upcoming_litters(request):
    breeds = get_breeds()
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
        'breeds': breeds,
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


def litter_detail(request, litter_id):
    try:
        litter = Litter.litters_for_sale.get(pk=litter_id)
        return render(request, 'upcoming_litters/detail.html', {'litter': litter})
    except Litter.DoesNotExist:
        return redirect('upcoming_litters')


def faqs(request):
    faqs = FrequentlyAskedQuestion.objects.filter(active=True).order_by('order')
    return render(request, 'faqs.html', {'faqs': faqs})
