from smtplib import SMTPException
from datetime import datetime
from dateutil.relativedelta import relativedelta

from django.utils.translation import gettext_lazy as _
from django.shortcuts import render, get_object_or_404, redirect
from django.core.mail import EmailMessage
from django.conf import settings
from django.contrib import messages
from django.template.loader import render_to_string

from breeding.models import Animal, AnimalFile, Breed

from .models import FrequentlyAskedQuestion
from .forms import ContactForm


def home(request):
    parent_breeds = Breed.objects.filter(parent__isnull=False).values_list('parent', flat=True)
    breeds = Breed.objects.exclude(id__in=parent_breeds)
    return render(request, 'home/index.html', {'breeds': breeds})


def about_us(request):
    gallery = AnimalFile.objects.filter(content_type__startswith='image').order_by('?')[:20]
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
        from_email=email,
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


def our_dogs(request):
    # Fetch breeds for the filter dropdown
    parent_breeds = Breed.objects.filter(parent__isnull=False).values_list('parent', flat=True)
    breeds = Breed.objects.exclude(id__in=parent_breeds)

    # Fetch dogs with optional filters
    dogs = Animal.objects.filter(active=True, sold_at__isnull=True)

    # Get filter values from the request
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
    offset = (page - 1) * per_page

    # Get the paginated dogs
    total_dogs = dogs.count()
    paginated_dogs = dogs[offset:offset + per_page]

    # Calculate total pages
    total_pages = (total_dogs + per_page - 1) // per_page

    context = {
        'breeds': breeds,
        'dogs': paginated_dogs,
        'pagination': {
            'has_more': page < total_pages,  # Show "Load More" if there are more pages
            'current_page': page,
            'next_page': page + 1,
            'total_pages': total_pages,
        },
        'filter_values': {
            'breed': breed_filter,
            'gender': gender_filter,
            'age': age_filter,
            'has_training': has_training,
            'has_certifications': has_certifications,
        },
    }

    if request.headers.get('X-Load-More'):
        return render(request, 'our_dogs/dogs_cards.html', context)

    return render(request, 'our_dogs/index.html', context)


def dog_detail(request, dog_id):
    dog = get_object_or_404(Animal, id=dog_id, active=True)
    return render(request, 'dog_detail/index.html', {'dog': dog})


def faqs(request):
    faqs = FrequentlyAskedQuestion.objects.filter(active=True).order_by('order')
    return render(request, 'faqs.html', {'faqs': faqs})
