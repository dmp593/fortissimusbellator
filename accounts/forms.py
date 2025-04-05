from django import forms
from django.contrib.auth.forms import UserCreationForm as AuthUserCreationForm
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from .models import Profile


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


class UserProfileForm(forms.ModelForm):
    first_name = forms.CharField(
        required=True,
        max_length=30,
        help_text=_('Required. Enter your first name.')
    )

    last_name = forms.CharField(
        required=True,
        max_length=30,
        help_text=_('Required. Enter your last name.')
    )

    email = forms.EmailField(
        max_length=254,
        required=True,
        help_text=_('Required. Enter a valid email address.')
    )

    username = forms.CharField(
        required=True,
        max_length=40,
        help_text=_('Required. Enter a unique username.')
    )

    class Meta:
        model = Profile
        fields = [
            'birthdate',
            'fiscal_number',
            'phone',
            'profile_picture'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.user:
            self.fields['first_name'].initial = self.instance.user.first_name
            self.fields['last_name'].initial = self.instance.user.last_name
            self.fields['email'].initial = self.instance.user.email

    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user

        # Update user fields
        user.username = self.cleaned_data['username']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.email = self.cleaned_data['email']

        if commit:
            user.save()
            profile.save()

        return profile
