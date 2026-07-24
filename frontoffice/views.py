import logging

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from attachments.models import Attachment
from fortissimusbellator.emails import (
    BrandedEmailContent,
    EmailAction,
    EmailDetail,
    send_branded_email,
)
from .models import FrequentlyAskedQuestion
from .forms import ContactForm


logger = logging.getLogger(__name__)


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

    content = BrandedEmailContent(
        subject=_('Contact form submission from %(name)s') % {'name': name},
        title=_('New contact request'),
        preheader=_(
            'A visitor sent a new message through the website contact form.'
        ),
        eyebrow=_('Website contact'),
        intro=_(
            'A visitor sent a new message through the Fortissimus Bellator '
            'website.'
        ),
        status_label=_('Reply required'),
        tone='warning',
        details=(
            EmailDetail(_('Name'), name),
            EmailDetail(_('Email'), email),
            EmailDetail(_('Phone'), phone),
        ),
        notice_title=_('Message'),
        notice=message,
        primary_action=EmailAction(
            _('Reply to customer'),
            f'mailto:{email}',
        ),
        internal=True,
    )
    send_branded_email(
        content=content,
        language_code=get_language() or settings.LANGUAGE_CODE,
        recipients=settings.BUSINESS_NOTIFICATION_RECIPIENTS,
    )


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
    except Exception:
        logger.exception('Unable to send contact form email')
        messages.error(request, _('Ups... Try again later.'))

    return redirect('contact_us')
