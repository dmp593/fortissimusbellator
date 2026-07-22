from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory, SimpleTestCase


class SecurityHeadersTests(SimpleTestCase):
    def test_allows_openstreetmap_to_receive_the_site_origin(self):
        request = RequestFactory().get('/contact-us/')
        response = SecurityMiddleware(lambda _request: HttpResponse())(request)

        self.assertEqual(
            response.headers['Referrer-Policy'],
            'strict-origin-when-cross-origin',
        )
