from django import forms
from django_recaptcha.fields import ReCaptchaField
from django.utils.translation import gettext_lazy as _


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
        widget=forms.EmailInput(
            attrs={
                'placeholder': _('Your Email')
            }
        )
    )
    
    phone = forms.CharField(
        widget=forms.EmailInput(
            attrs={
                'placeholder': _('Your Email')
            }
        )
    )
    
    message = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'placeholder': _('Your Message'),
                'rows': 5
            }
        )
    )
    
    captcha = ReCaptchaField()
