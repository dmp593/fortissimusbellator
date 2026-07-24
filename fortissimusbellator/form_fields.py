from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .contact_details import (
    E164_MAX_LENGTH,
    normalize_international_phone,
    split_international_phone,
)


class TelephoneInput(forms.TextInput):
    input_type = 'tel'


class InternationalPhoneWidget(forms.MultiWidget):
    def __init__(self, attrs=None):
        widgets = (
            TelephoneInput(
                attrs={
                    'aria-label': _('Country calling code'),
                    'autocomplete': 'tel-country-code',
                    'class': 'ui-input',
                    'inputmode': 'tel',
                    'maxlength': 4,
                    'pattern': r'\+[1-9][0-9]{0,2}',
                    'placeholder': '+351',
                }
            ),
            TelephoneInput(
                attrs={
                    'aria-label': _('Phone number'),
                    'autocomplete': 'tel-national',
                    'class': 'ui-input',
                    'inputmode': 'tel',
                    'maxlength': 20,
                    'placeholder': _('912 345 678'),
                }
            ),
        )
        super().__init__(widgets, attrs)

    def decompress(self, value):
        parts = split_international_phone(value)
        return [parts.calling_code, parts.national_number]

    def value_from_datadict(self, data, files, name):
        values = super().value_from_datadict(data, files, name)
        subwidget_names = (
            f'{name}_{index}' for index in range(len(self.widgets))
        )
        if any(subwidget_name in data for subwidget_name in subwidget_names):
            return values

        # Accept the former single-input payload during the transition.
        return self.decompress(data.get(name, ''))


class InternationalPhoneField(forms.MultiValueField):
    widget = InternationalPhoneWidget

    def __init__(self, *args, **kwargs):
        required = kwargs.get('required', True)
        kwargs.setdefault(
            'help_text',
            _(
                'Enter the country calling code separately, for example '
                '+351 and 912 345 678.'
            ),
        )
        fields = (
            forms.CharField(max_length=4, required=required),
            forms.CharField(max_length=20, required=required),
        )
        super().__init__(
            fields,
            *args,
            require_all_fields=required,
            **kwargs,
        )

    def compress(self, data_list):
        if not data_list:
            return ''

        calling_code, national_number = data_list
        if not national_number:
            if self.required:
                raise ValidationError(self.error_messages['required'])
            return ''
        if not calling_code:
            raise ValidationError(
                _('Enter the country calling code, for example +351.')
            )

        normalized_phone = normalize_international_phone(
            calling_code,
            national_number,
        )
        if len(normalized_phone) > E164_MAX_LENGTH:
            raise ValidationError(_('Enter a valid phone number.'))
        return normalized_phone
