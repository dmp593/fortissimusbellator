from django.test import SimpleTestCase

from frontoffice.forms import ContactForm


class PublicContactFormTests(SimpleTestCase):
    def contact_form(self, **changes):
        data = {
            'name': 'Customer Example',
            'email': 'customer@example.com',
            'phone_0': '+351',
            'phone_1': '912 345 678',
            'message': 'I would like more information.',
        }
        data.update(changes)
        form = ContactForm(data)
        form.fields.pop('captcha')
        return form

    def test_normalizes_phone_and_accepts_valid_email(self):
        form = self.contact_form()

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone'], '+351912345678')

    def test_rejects_invalid_email_format(self):
        form = self.contact_form(email='invalid-email')

        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_rejects_invalid_phone_for_calling_code(self):
        form = self.contact_form(phone_1='123')

        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)
