from django.test import SimpleTestCase
from django.utils import timezone

from reservations.forms import PreReservationCheckoutForm
from reservations.models import PreReservationTerms


class PreReservationContactFormTests(SimpleTestCase):
    def setUp(self):
        self.terms = PreReservationTerms(
            pk=1,
            version='contact-tests',
            description='Test terms.',
            published_at=timezone.now(),
        )

    def form_data(self, **changes):
        data = {
            'terms': str(self.terms.pk),
            'full_name': 'Foreign Customer',
            'email': 'customer@example.com',
            'phone_0': '+44',
            'phone_1': '7911 123456',
            'billing_country': 'GB',
            'accept_non_refundable': 'on',
        }
        data.update(changes)
        return data

    def test_checkout_normalizes_foreign_phone(self):
        form = PreReservationCheckoutForm(
            self.form_data(),
            terms=self.terms,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone'], '+447911123456')

    def test_checkout_rejects_invalid_email_format(self):
        form = PreReservationCheckoutForm(
            self.form_data(email='invalid-email'),
            terms=self.terms,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_checkout_requires_valid_country_calling_code(self):
        form = PreReservationCheckoutForm(
            self.form_data(phone_0='+999'),
            terms=self.terms,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)
