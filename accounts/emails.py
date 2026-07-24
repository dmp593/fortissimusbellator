from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _
from django.utils.translation import override


def send_activation_email(*, request, user, next_url=''):
    language_code = getattr(request, 'LANGUAGE_CODE', settings.LANGUAGE_CODE)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    with override(language_code):
        activation_path = reverse(
            'activate',
            kwargs={'uidb64': uid, 'token': token},
        )
        if next_url:
            activation_path = f'{activation_path}?{urlencode({"next": next_url})}'
        context = {
            'activation_url': request.build_absolute_uri(activation_path),
            'language_code': language_code,
            'user': user,
        }
        subject = _('Activate your Fortissimus Bellator account')
        text_body = render_to_string(
            'accounts/emails/account_activation.txt',
            context,
        )
        html_body = render_to_string(
            'email_activate_account.html',
            context,
        )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    email.attach_alternative(html_body, 'text/html')
    email.send(fail_silently=False)
