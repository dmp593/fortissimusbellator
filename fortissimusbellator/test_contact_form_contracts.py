from django import forms
from django.contrib.auth.forms import PasswordResetForm
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from accounts.forms import (
    ResendActivationEmailForm,
    UserCreationForm,
    UserProfileForm,
)
from fortissimusbellator.contact_details import (
    PhoneParts,
    normalize_international_phone,
    split_international_phone,
)
from fortissimusbellator.form_fields import InternationalPhoneField
from frontoffice.forms import ContactForm
from reservations.forms import (
    AdminSaleProcessForm,
    PreReservationCheckoutForm,
    ResendDocumentForm,
)


class ContactFormContractTests(SimpleTestCase):
    def test_every_contact_email_uses_django_email_validation(self):
        email_fields = (
            ContactForm.base_fields['email'],
            UserCreationForm.base_fields['email'],
            UserProfileForm.base_fields['email'],
            ResendActivationEmailForm.base_fields['email'],
            PasswordResetForm.base_fields['email'],
            PreReservationCheckoutForm.base_fields['email'],
            AdminSaleProcessForm.base_fields['customer_email'],
            ResendDocumentForm.base_fields['recipient'],
        )

        for field in email_fields:
            with self.subTest(field=field.label):
                self.assertIsInstance(field, forms.EmailField)
                self.assertEqual(field.widget.input_type, 'email')
                with self.assertRaises(ValidationError):
                    field.clean('not-an-email')

    def test_every_contact_phone_uses_the_shared_international_field(self):
        phone_fields = (
            ContactForm.base_fields['phone'],
            UserCreationForm.base_fields['phone'],
            UserProfileForm.base_fields['phone'],
            PreReservationCheckoutForm.base_fields['phone'],
            AdminSaleProcessForm.base_fields['customer_phone'],
        )

        for field in phone_fields:
            with self.subTest(field=field.label):
                self.assertIsInstance(field, InternationalPhoneField)
                self.assertEqual(
                    field.clean(['+44', '7911 123456']),
                    '+447911123456',
                )
                with self.assertRaises(ValidationError):
                    field.clean(['+999', '7911 123456'])

    def test_phone_widget_keeps_legacy_single_input_posts_compatible(self):
        field = UserCreationForm.base_fields['phone']

        values = field.widget.value_from_datadict(
            {'phone': '+351912345678'},
            {},
            'phone',
        )

        self.assertEqual(values, ['+351', '912345678'])
        self.assertEqual(field.clean(values), '+351912345678')


class InternationalPhoneTests(SimpleTestCase):
    def test_normalizes_valid_portuguese_and_foreign_numbers(self):
        self.assertEqual(
            normalize_international_phone('+351', '912 345 678'),
            '+351912345678',
        )
        self.assertEqual(
            normalize_international_phone('+44', '7911 123456'),
            '+447911123456',
        )

    def test_rejects_a_number_that_does_not_match_the_calling_code(self):
        with self.assertRaises(ValidationError):
            normalize_international_phone('+351', '123')

    def test_splits_stored_international_number_for_the_two_inputs(self):
        self.assertEqual(
            split_international_phone('+447911123456'),
            PhoneParts('+44', '7911123456'),
        )

    def test_legacy_local_number_is_presented_with_portuguese_default(self):
        self.assertEqual(
            split_international_phone('912345678'),
            PhoneParts('+351', '912345678'),
        )
