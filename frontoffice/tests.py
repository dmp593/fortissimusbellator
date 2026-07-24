from types import SimpleNamespace

from django.core import mail
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)

from fortissimusbellator.business import CONTACT_EMAIL
from frontoffice.models import FrequentlyAskedQuestion
from frontoffice.views import send_contact_email


class SecurityHeadersTests(SimpleTestCase):
    def test_allows_openstreetmap_to_receive_the_site_origin(self):
        request = RequestFactory().get('/contact-us/')
        response = SecurityMiddleware(lambda _request: HttpResponse())(request)

        self.assertEqual(
            response.headers['Referrer-Policy'],
            'strict-origin-when-cross-origin',
        )


class PreReservationFAQTests(TestCase):
    def test_non_refundable_and_failure_recovery_faqs_are_installed(self):
        faqs = FrequentlyAskedQuestion.objects.filter(
            order__gte=200,
            order__lte=204,
            active=True,
        )

        self.assertEqual(faqs.count(), 5)
        combined_answers = ' '.join(faqs.values_list('answer_en', flat=True))
        self.assertIn('non-refundable', combined_answers)
        self.assertIn(
            'deducted in full from the final price of the dog',
            combined_answers,
        )
        self.assertIn('payment remains confirmed', combined_answers)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BUSINESS_NOTIFICATION_RECIPIENTS=['staff@example.com'],
    PUBLIC_SITE_URL='https://fortissimusbellator.test',
)
class ContactEmailTests(SimpleTestCase):
    def test_contact_notification_uses_branded_html_and_reply_action(self):
        form = SimpleNamespace(
            cleaned_data={
                'name': 'Customer Example',
                'email': 'customer@example.com',
                'phone': '+351 900 000 000',
                'message': 'I would like more information.',
            },
        )

        send_contact_email(form)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        html = message.alternatives[0][0]
        self.assertEqual(message.to, ['staff@example.com'])
        self.assertEqual(message.reply_to, [CONTACT_EMAIL])
        self.assertIn('Fortissimus Bellator', html)
        self.assertIn('mailto:customer@example.com', html)
        self.assertIn('I would like more information.', html)
