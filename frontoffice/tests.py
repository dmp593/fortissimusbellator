from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory, SimpleTestCase, TestCase

from frontoffice.models import FrequentlyAskedQuestion


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
