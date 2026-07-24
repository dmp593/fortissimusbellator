from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.forms import UserProfileForm
from accounts.models import Profile


TEST_STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class AccountContactFormTests(TestCase):
    password = 'Strong-Test-Password-593!'

    def registration_data(self, **changes):
        data = {
            'username': 'international-customer',
            'first_name': 'International',
            'last_name': 'Customer',
            'email': 'customer@example.com',
            'phone_0': '+44',
            'phone_1': '7911 123456',
            'password1': self.password,
            'password2': self.password,
        }
        data.update(changes)
        return data

    def test_registration_normalizes_foreign_phone_number(self):
        response = self.client.post(
            reverse('register'),
            self.registration_data(),
        )

        self.assertRedirects(response, reverse('email_confirmation_sent'))
        profile = Profile.objects.get(
            user__username='international-customer',
        )
        self.assertEqual(profile.phone, '+447911123456')

    def test_registration_requires_valid_email_format(self):
        response = self.client.post(
            reverse('register'),
            self.registration_data(email='invalid-email'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter a valid email address')
        self.assertFalse(
            get_user_model().objects.filter(
                username='international-customer',
            ).exists()
        )

    def test_registration_requires_country_calling_code(self):
        response = self.client.post(
            reverse('register'),
            self.registration_data(phone_0=''),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('phone', response.context['form'].errors)

    def test_profile_form_splits_stored_foreign_phone_for_display(self):
        user = get_user_model().objects.create_user(
            username='profile-customer',
            email='profile@example.com',
        )
        profile = Profile.objects.create(
            user=user,
            phone='+447911123456',
        )

        form = UserProfileForm(instance=profile)

        self.assertEqual(
            form.fields['phone'].widget.decompress(form['phone'].value()),
            ['+44', '7911123456'],
        )

    def test_resend_activation_rejects_invalid_email_on_the_server(self):
        response = self.client.post(
            reverse('resend_activation_email'),
            {'email': 'invalid-email'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter a valid email address')
