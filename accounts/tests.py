import re
from urllib.parse import urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Profile


TEST_STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


@override_settings(
    DEFAULT_FROM_EMAIL='noreply@example.com',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    STATIC_ROOT=None,
    STORAGES=TEST_STORAGES,
)
class AccountLifecycleTests(TestCase):
    password = 'Strong-Test-Password-593!'

    def registration_data(self, **changes):
        data = {
            'username': 'new-customer',
            'first_name': 'New',
            'last_name': 'Customer',
            'email': 'customer@example.com',
            'phone': '+351912345678',
            'password1': self.password,
            'password2': self.password,
        }
        data.update(changes)
        return data

    @staticmethod
    def email_link(message, path_fragment):
        match = re.search(
            rf'https?://[^\s]+{re.escape(path_fragment)}[^\s]*',
            message.body,
        )
        if match is None:
            raise AssertionError(
                f'No email link containing {path_fragment!r} was found.'
            )
        return match.group(0)

    @staticmethod
    def request_path(absolute_url):
        parsed = urlsplit(absolute_url)
        return parsed.path + (f'?{parsed.query}' if parsed.query else '')

    def test_registration_sends_branded_email_and_activation_resumes_next_url(self):
        next_url = reverse('reservations:dashboard')

        response = self.client.post(
            reverse('register'),
            self.registration_data(next=next_url),
        )

        self.assertRedirects(response, reverse('email_confirmation_sent'))
        user = get_user_model().objects.get(username='new-customer')
        self.assertFalse(user.is_active)
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(
            message.subject,
            'Activate your Fortissimus Bellator account',
        )
        self.assertEqual(message.from_email, 'noreply@example.com')
        self.assertIn('http://testserver/en/activate/', message.body)
        self.assertIn(f'next={next_url.replace("/", "%2F")}', message.body)
        self.assertEqual(message.alternatives[0][1], 'text/html')
        self.assertIn('email-brand', message.alternatives[0][0])

        activation_url = self.email_link(message, '/en/activate/')
        activation_response = self.client.get(
            self.request_path(activation_url),
        )

        self.assertRedirects(
            activation_response,
            next_url,
            fetch_redirect_response=False,
        )
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(
            str(self.client.session.get('_auth_user_id')),
            str(user.pk),
        )

    def test_registration_rejects_case_insensitive_duplicate_email(self):
        get_user_model().objects.create_user(
            username='existing',
            email='Customer@Example.com',
            password=self.password,
        )

        response = self.client.post(
            reverse('register'),
            self.registration_data(email='customer@example.com'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    @patch(
        'accounts.views.send_activation_email',
        side_effect=RuntimeError('mail outage'),
    )
    @patch('accounts.views.logger.exception')
    def test_registration_survives_activation_email_outage(
        self,
        log_exception,
        send_email,
    ):
        response = self.client.post(
            reverse('register'),
            self.registration_data(),
        )

        self.assertRedirects(response, reverse('resend_activation_email'))
        user = get_user_model().objects.get(username='new-customer')
        self.assertFalse(user.is_active)
        self.assertTrue(Profile.objects.filter(user=user).exists())
        send_email.assert_called_once()
        log_exception.assert_called_once()

    def test_registration_rejects_an_external_post_activation_redirect(self):
        response = self.client.post(
            reverse('register'),
            self.registration_data(next='https://malicious.example/steal'),
        )

        self.assertRedirects(response, reverse('email_confirmation_sent'))
        message = mail.outbox[0]
        self.assertNotIn('malicious.example', message.body)
        activation_url = self.email_link(message, '/en/activate/')
        activation_response = self.client.get(
            self.request_path(activation_url),
        )
        self.assertRedirects(
            activation_response,
            reverse('welcome'),
            fetch_redirect_response=False,
        )

    def test_resend_activation_does_not_disclose_unknown_email(self):
        response = self.client.post(
            reverse('resend_activation_email'),
            {'email': 'unknown@example.com'},
            follow=True,
        )

        self.assertRedirects(
            response,
            reverse('email_confirmation_sent'),
        )
        self.assertContains(response, 'If the address belongs')
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_activation_token_uses_the_site_theme(self):
        response = self.client.get(
            reverse(
                'activate',
                kwargs={'uidb64': 'invalid', 'token': 'invalid'},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'ui-card', status_code=400)
        self.assertContains(
            response,
            'invalid or has expired',
            status_code=400,
        )

    def test_password_reset_sends_branded_email_and_changes_password(self):
        user = get_user_model().objects.create_user(
            username='customer',
            first_name='Test',
            email='customer@example.com',
            password=self.password,
        )

        response = self.client.post(
            reverse('password_reset'),
            {'email': user.email},
        )

        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(
            message.subject,
            'Reset your Fortissimus Bellator password',
        )
        self.assertIn(
            'http://testserver/en/password-reset-confirm/',
            message.body,
        )
        self.assertEqual(message.alternatives[0][1], 'text/html')
        self.assertIn('email-brand', message.alternatives[0][0])

        reset_url = self.email_link(
            message,
            '/en/password-reset-confirm/',
        )
        token_response = self.client.get(self.request_path(reset_url))
        self.assertEqual(token_response.status_code, 302)
        set_password_url = token_response['Location']
        reset_response = self.client.post(
            set_password_url,
            {
                'new_password1': 'New-Strong-Password-941!',
                'new_password2': 'New-Strong-Password-941!',
            },
        )

        self.assertRedirects(
            reset_response,
            reverse('password_reset_complete'),
        )
        user.refresh_from_db()
        self.assertTrue(user.check_password('New-Strong-Password-941!'))

    def test_password_reset_does_not_email_inactive_accounts(self):
        get_user_model().objects.create_user(
            username='inactive',
            email='inactive@example.com',
            password=self.password,
            is_active=False,
        )

        response = self.client.post(
            reverse('password_reset'),
            {'email': 'inactive@example.com'},
        )

        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_authenticated_password_change_preserves_the_session(self):
        user = get_user_model().objects.create_user(
            username='customer',
            email='customer@example.com',
            password=self.password,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse('change_password'),
            {
                'old_password': self.password,
                'new_password1': 'Changed-Strong-Password-337!',
                'new_password2': 'Changed-Strong-Password-337!',
            },
        )

        self.assertRedirects(response, reverse('change_password'))
        user.refresh_from_db()
        self.assertTrue(user.check_password('Changed-Strong-Password-337!'))
        self.assertEqual(
            str(self.client.session.get('_auth_user_id')),
            str(user.pk),
        )
        self.assertEqual(len(mail.outbox), 0)
