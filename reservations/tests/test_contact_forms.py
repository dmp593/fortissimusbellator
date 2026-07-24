from django.test import SimpleTestCase
from django.utils import timezone

from reservations.forms import AdminSaleProcessForm, PreReservationCheckoutForm
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
            'billing_address': 'Example Street 1',
            'billing_postcode': 'SW1A 1AA',
            'billing_city': 'London',
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

    def test_contact_and_billing_details_are_required_for_sale_workflows(self):
        required_fields = {
            'email': 'customer_email',
            'phone': 'customer_phone',
            'billing_address': 'billing_address',
            'billing_postcode': 'billing_postcode',
            'billing_city': 'billing_city',
            'billing_country': 'billing_country',
        }

        for field_name, admin_field_name in required_fields.items():
            with self.subTest(form='pre-reservation', field=field_name):
                self.assertTrue(
                    PreReservationCheckoutForm.base_fields[field_name].required
                )
            with self.subTest(form='admin sale', field=field_name):
                self.assertTrue(
                    AdminSaleProcessForm.base_fields[
                        admin_field_name
                    ].required
                )

        self.assertFalse(
            PreReservationCheckoutForm.base_fields['tax_number'].required
        )
        self.assertFalse(
            AdminSaleProcessForm.base_fields['customer_tax_number'].required
        )

    def test_admin_sale_rejects_invalid_country_code(self):
        form = AdminSaleProcessForm(
            data={
                'billing_country': 'Portugal',
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn('billing_country', form.errors)
