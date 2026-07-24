from django import forms
from django.contrib.auth.forms import UserCreationForm as AuthUserCreationForm
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from breeding.models import Breed, LitterAlertPreference

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

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                _('An account with this email address already exists.')
            )
        return email


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
            self.fields['username'].initial = self.instance.user.username
            self.fields['first_name'].initial = self.instance.user.first_name
            self.fields['last_name'].initial = self.instance.user.last_name
            self.fields['email'].initial = self.instance.user.email

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        users = get_user_model().objects.filter(email__iexact=email)
        if self.instance.pk:
            users = users.exclude(pk=self.instance.user_id)
        if users.exists():
            raise forms.ValidationError(
                _('An account with this email address already exists.')
            )
        return email

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        users = get_user_model().objects.filter(username__iexact=username)
        if self.instance.pk:
            users = users.exclude(pk=self.instance.user_id)
        if users.exists():
            raise forms.ValidationError(
                _('An account with this username already exists.')
            )
        return username

    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user

        # Update user fields
        user.username = self.cleaned_data['username']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.email = self.cleaned_data['email']

        if commit:
            with transaction.atomic():
                user.save()
                profile.save()

        return profile


class LitterAlertPreferenceForm(forms.ModelForm):
    breeds = forms.ModelMultipleChoiceField(
        queryset=Breed.objects_specific.filter(active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_('Breeds'),
    )

    class Meta:
        model = LitterAlertPreference
        fields = ('scope', 'breeds')
        widgets = {
            'scope': forms.RadioSelect,
        }

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get('scope')
            == LitterAlertPreference.Scope.SELECTED_BREEDS
            and not cleaned_data.get('breeds')
        ):
            self.add_error(
                'breeds',
                _('Choose at least one breed for selected-breed alerts.'),
            )
        return cleaned_data
