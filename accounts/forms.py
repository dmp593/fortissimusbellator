from django import forms
from django.contrib.auth.forms import UserCreationForm as AuthUserCreationForm
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _


class UserCreationForm(AuthUserCreationForm):
    first_name = forms.CharField(
        max_length=30,
        required=True,
        help_text=_('Required.')
    )

    last_name = forms.CharField(
        max_length=30,
        required=True,
        help_text=_('Required.')
    )

    email = forms.EmailField(
        max_length=254,
        required=True,
        help_text=_('Required. Enter a valid email address.')
    )

    phone = forms.CharField(
        max_length=15,
        required=True,
        help_text=_('Required. Enter your phone number.')
    )

    class Meta:
        model = get_user_model()
        fields = (
            'username',
            'first_name',
            'last_name',
            'email',
            'phone',
            'password1',
            'password2'
        )
