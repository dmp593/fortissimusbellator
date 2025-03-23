from smtplib import SMTPException

from django.utils.translation import gettext_lazy as _
from django.shortcuts import render, redirect
from django.core.mail import EmailMessage
from django.conf import settings
from django.contrib import messages
from django.template.loader import render_to_string

from attachments.models import Attachment
from .models import FrequentlyAskedQuestion
from .forms import ContactForm


def home(request):
    return render(request, 'home/index.html')


def about_us(request):
    gallery = Attachment.objects.filter(mime_type__startswith='image').order_by('?')[:50]
    return render(request, 'about_us.html', { 'gallery': gallery })


def faqs(request):
    faqs = FrequentlyAskedQuestion.objects.filter(active=True).order_by('order')
    return render(request, 'faqs.html', {'faqs': faqs})


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
