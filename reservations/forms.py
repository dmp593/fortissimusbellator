from django import forms
from django.utils.translation import gettext_lazy as _

from reservations.models import PreReservationTerms


class PreReservationCheckoutForm(forms.Form):
    terms = forms.IntegerField(widget=forms.HiddenInput())
    full_name = forms.CharField(max_length=150, label=_('Full name'))
    email = forms.EmailField(label=_('Email'))
    phone = forms.CharField(max_length=30, label=_('Phone'))
    tax_number = forms.CharField(
        max_length=30,
        required=False,
        label=_('Tax number'),
    )
    billing_address = forms.CharField(
        max_length=255,
        required=False,
        label=_('Billing address'),
    )
    billing_postcode = forms.CharField(
        max_length=20,
        required=False,
        label=_('Postcode'),
    )
    billing_city = forms.CharField(
        max_length=100,
        required=False,
        label=_('City'),
    )
    billing_country = forms.CharField(
        max_length=2,
        initial='PT',
        label=_('Country code'),
        help_text=_('Two-letter country code, for example PT.'),
    )
    promotion_code = forms.CharField(
        max_length=50,
        required=False,
        label=_('Promotion code'),
    )
    accept_non_refundable = forms.BooleanField(
        required=True,
        label=_(
            'I have read and accept the pre-reservation terms, including that '
            'the fee is non-refundable if I cancel.'
        ),
    )

    def __init__(self, *args, terms: PreReservationTerms, **kwargs):
        self.terms = terms
        super().__init__(*args, **kwargs)
        self.initial['terms'] = terms.pk
        for name, field in self.fields.items():
            if name == 'accept_non_refundable':
                field.widget.attrs['class'] = (
                    'size-5 rounded border-stone-300 text-stone-700 '
                    'focus:ring-stone-500'
                )
            else:
                field.widget.attrs['class'] = 'ui-input'

    def clean_billing_country(self):
        country = self.cleaned_data['billing_country'].strip().upper()
        if len(country) != 2 or not country.isalpha():
            raise forms.ValidationError(
                _('Enter a valid two-letter country code.')
            )
        return country

    def clean_promotion_code(self):
        return self.cleaned_data['promotion_code'].strip().upper()

    def clean_terms(self):
        submitted_terms_id = self.cleaned_data['terms']
        if submitted_terms_id != self.terms.pk:
            raise forms.ValidationError(
                _('The pre-reservation terms were updated. Review them again.')
            )
        return self.terms


class AdminCancellationForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4, 'class': 'vLargeTextField'}),
        label=_('Cancellation reason'),
    )
    confirm = forms.BooleanField(
        label=_('I confirm that this pre-reservation must be cancelled.'),
    )


class ResendDocumentForm(forms.Form):
    recipient = forms.EmailField(label=_('Recipient email'))
    confirm = forms.BooleanField(
        label=_('I confirm that this fiscal document may be emailed.'),
    )
