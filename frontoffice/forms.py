from django import forms
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV3
from django.utils.translation import gettext_lazy as _

from fortissimusbellator.form_fields import InternationalPhoneField


class ContactForm(forms.Form):
    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(
            attrs={
                'placeholder': _('Your Name')
            }
        )
    )

    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                'autocomplete': 'email',
                'placeholder': _('Your Email')
            }
        )
    )

    phone = InternationalPhoneField(
        label=_('Phone'),
    )

    message = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'placeholder': _('Your Message'),
                'rows': 5
            }
        )
    )

    captcha = ReCaptchaField(
        widget=ReCaptchaV3()
    )
