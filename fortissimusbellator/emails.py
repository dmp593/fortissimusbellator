from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import override

from fortissimusbellator.business import (
    ADDRESS,
    BUSINESS_NAME,
    CONTACT_EMAIL,
    FACEBOOK_URL,
    INSTAGRAM_URL,
    PRIMARY_PHONE,
    SECONDARY_PHONE,
    WHATSAPP_URL,
)


@dataclass(frozen=True)
class EmailDetail:
    label: str
    value: str
    highlight: bool = False


@dataclass(frozen=True)
class EmailAction:
    label: str
    url: str


@dataclass(frozen=True)
class BrandedEmailContent:
    subject: str
    title: str
    preheader: str
    eyebrow: str
    intro: str
    recipient_name: str = ''
    status_label: str = ''
    tone: str = 'neutral'
    details: tuple[EmailDetail, ...] = field(default_factory=tuple)
    notice_title: str = ''
    notice: str = ''
    primary_action: EmailAction | None = None
    secondary_action: EmailAction | None = None
    reference: str = ''
    target_name: str = ''
    target_breed: str = ''
    target_image_url: str = ''
    target_url: str = ''
    footer_note: str = ''
    internal: bool = False


def send_branded_email(
    *,
    content: BrandedEmailContent,
    language_code: str,
    recipients,
    attachments=(),
):
    recipients = [recipient for recipient in recipients if recipient]
    if not recipients:
        return None

    with override(language_code):
        context = {
            'content': content,
            'language_code': language_code,
            **brand_email_context(language_code),
        }
        text_body = render_to_string('emails/branded.txt', context)
        html_body = render_to_string('emails/branded.html', context)

    subject = (
        str(content.subject)
        .replace('\r', ' ')
        .replace('\n', ' ')
        .strip()
    )
    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
        reply_to=[CONTACT_EMAIL],
    )
    email.attach_alternative(html_body, 'text/html')
    for filename, attachment, mime_type in attachments:
        email.attach(filename, attachment, mime_type)
    email.send(fail_silently=False)
    return email


def brand_email_context(language_code: str):
    return {
        'business_name': BUSINESS_NAME,
        'primary_phone': PRIMARY_PHONE,
        'primary_phone_url': _telephone_url(PRIMARY_PHONE),
        'secondary_phone': SECONDARY_PHONE,
        'secondary_phone_url': _telephone_url(SECONDARY_PHONE),
        'contact_email': CONTACT_EMAIL,
        'contact_url': absolute_reverse(
            'contact_us',
            language_code=language_code,
        ),
        'faq_url': absolute_reverse('faqs', language_code=language_code),
        'site_url': settings.PUBLIC_SITE_URL,
        'whatsapp_url': WHATSAPP_URL,
        'facebook_url': FACEBOOK_URL,
        'instagram_url': INSTAGRAM_URL,
        'address': ADDRESS,
        'current_year': timezone.localdate().year,
    }


def absolute_reverse(
    view_name: str,
    *,
    args=None,
    kwargs=None,
    language_code: str | None = None,
):
    with override(language_code or settings.LANGUAGE_CODE):
        path = reverse(view_name, args=args, kwargs=kwargs)
    return absolute_url(path)


def absolute_url(path: str):
    if path.startswith(('https://', 'http://')):
        return path
    return urljoin(f'{settings.PUBLIC_SITE_URL}/', path.lstrip('/'))


def format_email_money(amount, currency: str):
    value = Decimal(str(amount or 0))
    return (
        f'{formats.number_format(value, decimal_pos=2, use_l10n=True)} '
        f'{currency}'
    )


def format_email_date(value: date | datetime | None):
    if value is None:
        return ''
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return formats.date_format(
            value,
            format='SHORT_DATETIME_FORMAT',
            use_l10n=True,
        )
    return formats.date_format(
        value,
        format='SHORT_DATE_FORMAT',
        use_l10n=True,
    )


def _telephone_url(phone: str):
    return f'tel:{phone.replace(" ", "")}'
