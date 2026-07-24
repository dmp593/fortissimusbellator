import decimal

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from breeding.models import Animal
from fortissimusbellator.form_fields import InternationalPhoneField
from reservations.availability import available_dogs_for_new_sale_process
from reservations.models import (
    AnimalSaleCase,
    Charge,
    ChargeAdjustment,
    CustomerCredit,
    Payment,
    PaymentRefund,
    PreReservationTerms,
    ReservationTerms,
)


class PreReservationCheckoutForm(forms.Form):
    terms = forms.IntegerField(widget=forms.HiddenInput())
    full_name = forms.CharField(max_length=150, label=_('Full name'))
    email = forms.EmailField(label=_('Email'))
    phone = InternationalPhoneField(
        label=_('Phone'),
        help_text=_(
            'Enter the country calling code separately, for example '
            '+351 and 912 345 678.'
        ),
    )
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
            elif isinstance(field.widget, forms.MultiWidget):
                for widget in field.widget.widgets:
                    widget.attrs['class'] = 'ui-input'
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


class ReservationCheckoutForm(forms.Form):
    terms = forms.IntegerField(widget=forms.HiddenInput())
    promotion_code = forms.CharField(
        max_length=50,
        required=False,
        label=_('Promotion code'),
    )
    accept_terms = forms.BooleanField(
        required=True,
        label=_(
            'I have read and accept the reservation terms and the stated '
            'deposit amount.'
        ),
    )

    def __init__(self, *args, terms: ReservationTerms, **kwargs):
        self.terms = terms
        super().__init__(*args, **kwargs)
        self.initial['terms'] = terms.pk
        self.fields['promotion_code'].widget.attrs['class'] = 'ui-input'
        self.fields['accept_terms'].widget.attrs['class'] = (
            'size-5 rounded border-stone-300 text-stone-700 '
            'focus:ring-stone-500'
        )

    def clean_promotion_code(self):
        return self.cleaned_data['promotion_code'].strip().upper()

    def clean_terms(self):
        submitted_terms_id = self.cleaned_data['terms']
        if submitted_terms_id != self.terms.pk:
            raise forms.ValidationError(
                _('The reservation terms were updated. Review them again.')
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


class AdminAcceptanceForm(forms.Form):
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'vLargeTextField'}),
        label=_('Internal review note'),
    )
    confirm = forms.BooleanField(
        label=_(
            'I confirm that this pre-reservation is accepted and the '
            'customer may pay the reservation deposit.'
        ),
    )


class AdminClosureRefundForm(forms.Form):
    class RefundChoice:
        NONE = 'none'

    reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4, 'class': 'vLargeTextField'}),
        label=_('Reason shown to the customer'),
    )
    refund_calculation = forms.ChoiceField(
        choices=(
            (RefundChoice.NONE, _('No refund')),
            (
                PaymentRefund.CalculationType.FIXED,
                _('Refund an additional fixed amount'),
            ),
            (
                PaymentRefund.CalculationType.TARGET_PERCENTAGE,
                _('Refund up to a total percentage of the payment'),
            ),
            (
                PaymentRefund.CalculationType.FULL_REMAINING,
                _('Refund the full remaining amount'),
            ),
        ),
        initial=RefundChoice.NONE,
        label=_('Refund decision'),
        help_text=_(
            'No refund is the default. Percentage means the desired total '
            'refunded percentage, including earlier partial refunds.'
        ),
    )
    fixed_amount = forms.DecimalField(
        required=False,
        min_value=0.01,
        max_digits=9,
        decimal_places=2,
        label=_('Additional refund amount'),
    )
    target_percentage = forms.DecimalField(
        required=False,
        min_value=0.01,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        label=_('Target total refund percentage'),
    )
    credit_amount = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=9,
        decimal_places=2,
        initial=0,
        label=_('Convert to customer credit'),
        help_text=_(
            'Any available value not refunded or converted to credit is '
            'retained by the business.'
        ),
    )
    assume_processing_costs = forms.BooleanField(
        required=False,
        label=_(
            'I acknowledge that this refund may exceed the Stripe net amount '
            'retained and that the business will absorb processing costs.'
        ),
    )
    confirm = forms.BooleanField(
        label=_('I confirm this closure and refund decision.'),
    )

    def clean(self):
        cleaned_data = super().clean()
        calculation = cleaned_data.get('refund_calculation')
        if (
            calculation == PaymentRefund.CalculationType.FIXED
            and cleaned_data.get('fixed_amount') is None
        ):
            self.add_error('fixed_amount', _('Enter the refund amount.'))
        if (
            calculation == PaymentRefund.CalculationType.TARGET_PERCENTAGE
            and cleaned_data.get('target_percentage') is None
        ):
            self.add_error(
                'target_percentage',
                _('Enter the target refund percentage.'),
            )
        return cleaned_data


class AdminReservationCancellationForm(AdminClosureRefundForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['refund_calculation'].label = _(
            'Refund decision for both reservation payments'
        )
        self.fields['refund_calculation'].help_text = _(
            'No refund is the default. A percentage is applied to both the '
            'pre-reservation payment and the reservation deposit payment. A '
            'fixed amount is refunded from the reservation deposit first, '
            'then from the pre-reservation payment.'
        )
        self.fields['fixed_amount'].label = _(
            'Additional refund amount across both payments'
        )
        self.fields['target_percentage'].label = _(
            'Target refunded percentage of both payments'
        )
        self.fields['credit_amount'].label = _(
            'Convert value from both stages to customer credit'
        )


class AdminSaleCancellationForm(AdminClosureRefundForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['refund_calculation'].label = _(
            'Refund decision for the complete sale'
        )
        self.fields['refund_calculation'].help_text = _(
            'No refund is the default. The calculation considers every '
            'payment recorded in the pre-reservation, reservation and final '
            'sale stages.'
        )
        self.fields['fixed_amount'].label = _(
            'Additional refund amount across the complete sale'
        )
        self.fields['target_percentage'].label = _(
            'Target refunded percentage of the complete sale'
        )
        self.fields['credit_amount'].label = _(
            'Convert value from the complete sale to customer credit'
        )
        self.fields['confirm'].label = _(
            'I confirm that this completed sale must be cancelled.'
        )


class ResendDocumentForm(forms.Form):
    recipient = forms.EmailField(label=_('Recipient email'))
    confirm = forms.BooleanField(
        label=_('I confirm that this fiscal document may be emailed.'),
    )


class AdminRetryForm(forms.Form):
    confirm = forms.BooleanField(
        label=_('I confirm that this operation should be retried now.'),
    )


MANUAL_PAYMENT_CHOICES = (
    (Payment.Provider.CASH, _('Cash')),
    (Payment.Provider.BANK_TRANSFER, _('Bank transfer')),
    (Payment.Provider.CARD_TERMINAL, _('Card terminal')),
    (Payment.Provider.OTHER, _('Other')),
)
ADMIN_PAYMENT_CHOICES = (
    (Payment.Provider.STRIPE, _('Customer pays online with Stripe')),
    *MANUAL_PAYMENT_CHOICES,
    (
        Payment.Provider.COMPLIMENTARY,
        _('No payment required (complimentary)'),
    ),
)
ADMIN_OFFLINE_PAYMENT_CHOICES = (
    *MANUAL_PAYMENT_CHOICES,
    (
        Payment.Provider.COMPLIMENTARY,
        _('No payment required (complimentary)'),
    ),
)


class AdminSaleProcessForm(forms.Form):
    class Stage:
        PRE_RESERVATION = 'pre_reservation'
        RESERVATION = 'reservation'
        SALE = 'sale'

    start_stage = forms.ChoiceField(
        choices=(
            (Stage.PRE_RESERVATION, _('Pre-reservation')),
            (Stage.RESERVATION, _('Direct reservation')),
            (Stage.SALE, _('Direct final sale')),
        ),
        label=_('Start at'),
    )
    animal = forms.ModelChoiceField(
        queryset=Animal.objects.none(),
        label=_('Dog'),
    )
    user = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        label=_('Registered customer'),
        help_text=_('Required when the customer will pay online.'),
    )
    customer_name = forms.CharField(max_length=150, label=_('Customer name'))
    customer_email = forms.EmailField(
        required=False,
        label=_('Customer email'),
    )
    customer_phone = InternationalPhoneField(
        required=False,
        label=_('Customer phone'),
    )
    customer_tax_number = forms.CharField(
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
    )
    language_code = forms.CharField(
        max_length=10,
        initial='pt',
        label=_('Language code'),
    )
    amount = forms.DecimalField(
        min_value=0,
        max_digits=9,
        decimal_places=2,
        label=_('Commercial amount'),
        help_text=_(
            'Pre-reservation fee, reservation deposit, or final sale price. '
            'Enter zero and choose the complimentary option when no payment '
            'is due.'
        ),
    )
    payment_provider = forms.ChoiceField(
        choices=ADMIN_PAYMENT_CHOICES,
        label=_('Payment method'),
    )
    terms_accepted_in_person = forms.BooleanField(
        required=False,
        label=_(
            'I confirm that the customer accepted the current terms outside '
            'the website.'
        ),
    )
    offer_hours = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=168,
        initial=72,
        label=_('Online payment validity in hours'),
    )
    sold_at = forms.DateField(
        required=False,
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}),
        label=_('Sold at'),
    )
    credit = forms.ModelChoiceField(
        queryset=CustomerCredit.objects.none(),
        required=False,
        label=_('Customer credit'),
    )
    credit_amount = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=9,
        decimal_places=2,
        initial=0,
        label=_('Credit to apply'),
    )
    payment_reference = forms.CharField(
        required=False,
        max_length=150,
        label=_('Payment reference'),
    )
    payment_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label=_('Payment note'),
        help_text=_(
            'Required for complimentary and other settlement methods so the '
            'commercial decision remains auditable.'
        ),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label=_('Sale notes'),
    )
    confirm = forms.BooleanField(
        label=_('I confirm this administrative sale workflow.'),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['animal'].queryset = (
            available_dogs_for_new_sale_process(
                Animal.objects.select_related('breed'),
            )
            .order_by('name', 'pk')
        )
        self.fields['user'].queryset = (
            get_user_model().objects.order_by('email', 'username', 'pk')
        )
        self.fields['credit'].queryset = CustomerCredit.objects.filter(
            status=CustomerCredit.Status.ACTIVE,
        ).select_related('user')

    def clean(self):
        data = super().clean()
        stage = data.get('start_stage')
        provider = data.get('payment_provider')
        user = data.get('user')
        customer_email = (data.get('customer_email') or '').strip()
        credit = data.get('credit')
        credit_amount = data.get('credit_amount') or 0
        amount = data.get('amount')
        payment_note = (data.get('payment_note') or '').strip()
        if provider == Payment.Provider.STRIPE and user is None:
            self.add_error(
                'user',
                _('A registered customer is required for online payment.'),
            )
        if user is None and not customer_email:
            self.add_error(
                'customer_email',
                _('Enter an email for an unregistered customer.'),
            )
        if (
            provider != Payment.Provider.STRIPE
            and stage != self.Stage.SALE
            and not data.get('terms_accepted_in_person')
        ):
            self.add_error(
                'terms_accepted_in_person',
                _('Offline pre-reservations and reservations require terms acceptance.'),
            )
        if stage == self.Stage.SALE:
            if provider == Payment.Provider.STRIPE:
                self.add_error(
                    'payment_provider',
                    _(
                        'Use a reservation when the customer still needs to '
                        'pay online.'
                    ),
                )
            if not data.get('sold_at'):
                self.add_error('sold_at', _('Enter the sale date.'))
        remaining_amount = amount
        if (
            amount is not None
            and stage in {self.Stage.RESERVATION, self.Stage.SALE}
        ):
            remaining_amount = max(
                amount - decimal.Decimal(credit_amount),
                decimal.Decimal('0.00'),
            )
        if (
            provider == Payment.Provider.COMPLIMENTARY
            and remaining_amount != 0
        ):
            self.add_error(
                'amount',
                _(
                    'A complimentary process cannot leave an amount due. '
                    'Enter zero, apply enough customer credit, or use an '
                    'audited adjustment on an existing charge.'
                ),
            )
        if (
            amount == 0
            and provider not in {
                Payment.Provider.STRIPE,
                Payment.Provider.COMPLIMENTARY,
            }
        ):
            self.add_error(
                'payment_provider',
                _(
                    'Choose the complimentary option when no payment was '
                    'received.'
                ),
            )
        if (
            provider in {
                Payment.Provider.COMPLIMENTARY,
                Payment.Provider.OTHER,
            }
            and not payment_note
        ):
            self.add_error(
                'payment_note',
                _(
                    'Explain this settlement method so the decision remains '
                    'auditable.'
                ),
            )
        if (
            stage == self.Stage.PRE_RESERVATION
            and data.get('animal')
            and data['animal'].current_price_in_euros is None
        ):
            self.add_error(
                'start_stage',
                _(
                    'A pre-reservation requires a published dog price so its '
                    'future reservation deposit can be calculated. Start a '
                    'direct reservation instead and enter the agreed amount.'
                ),
            )
        if credit_amount and credit is None:
            self.add_error('credit', _('Choose the credit to apply.'))
        if credit and user and credit.user_id and credit.user_id != user.pk:
            self.add_error('credit', _('This credit belongs to another customer.'))
        return data

    @property
    def customer_data(self):
        return {
            key: self.cleaned_data.get(key, '')
            for key in (
                'customer_name',
                'customer_email',
                'customer_phone',
                'customer_tax_number',
                'billing_address',
                'billing_postcode',
                'billing_city',
                'billing_country',
                'language_code',
            )
        }


class AdminManualPaymentForm(forms.Form):
    amount = forms.DecimalField(
        min_value=0.01,
        max_digits=9,
        decimal_places=2,
        label=_('Amount received'),
    )
    provider = forms.ChoiceField(
        choices=MANUAL_PAYMENT_CHOICES,
        label=_('Payment method'),
    )
    external_reference = forms.CharField(
        required=False,
        max_length=150,
        label=_('Payment reference'),
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label=_('Note'),
    )
    terms_accepted_in_person = forms.BooleanField(
        required=False,
        label=_(
            'I confirm that the customer accepted the current stage terms '
            'outside the website.'
        ),
    )
    confirm = forms.BooleanField(
        label=_('I confirm that this payment was received.'),
    )

    def __init__(
        self,
        *args,
        terms_acceptance_required=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.fields['terms_accepted_in_person'].required = (
            terms_acceptance_required
        )


class AdminChargeAdjustmentForm(forms.Form):
    kind = forms.ChoiceField(
        choices=ChargeAdjustment.Kind.choices,
        label=_('Adjustment type'),
    )
    amount = forms.DecimalField(
        max_digits=9,
        decimal_places=2,
        label=_('Signed amount'),
        help_text=_('Use a negative value for a discount or waiver.'),
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        label=_('Reason'),
    )
    terms_accepted_in_person = forms.BooleanField(
        required=False,
        label=_(
            'I confirm that the customer accepted the current stage terms '
            'outside the website.'
        ),
        help_text=_(
            'Required when this adjustment settles a stage whose terms are '
            'still awaiting customer acceptance.'
        ),
    )
    confirm = forms.BooleanField(
        label=_('I confirm this immutable financial adjustment.'),
    )


class AdminWorkflowTransferForm(forms.Form):
    target_animal = forms.ModelChoiceField(
        queryset=Animal.objects.none(),
        label=_('Target dog'),
    )
    target_charge_amount = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=9,
        decimal_places=2,
        label=_('Target stage amount override'),
    )
    transferred_amount = forms.DecimalField(
        min_value=0,
        max_digits=9,
        decimal_places=2,
        label=_('Convert to transferable credit'),
    )
    refund_amount = forms.DecimalField(
        min_value=0,
        max_digits=9,
        decimal_places=2,
        label=_('Refund'),
    )
    retained_amount = forms.DecimalField(
        min_value=0,
        max_digits=9,
        decimal_places=2,
        label=_('Retain'),
    )
    difference_payment_provider = forms.ChoiceField(
        choices=ADMIN_PAYMENT_CHOICES,
        label=_('How to settle a target difference'),
    )
    payment_reference = forms.CharField(
        required=False,
        max_length=150,
        label=_('Payment reference'),
    )
    payment_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label=_('Payment note'),
    )
    terms_accepted_in_person = forms.BooleanField(
        required=False,
        label=_(
            'I confirm that the customer accepted the current target-stage '
            'terms outside the website.'
        ),
        help_text=_(
            'This is only needed when the source process has no valid terms '
            'acceptance to carry to the target dog.'
        ),
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        label=_('Reason shown in the history'),
    )
    assume_processing_costs = forms.BooleanField(
        required=False,
        label=_(
            'I acknowledge that a Stripe refund may make the business absorb '
            'processing costs.'
        ),
    )
    confirm = forms.BooleanField(
        label=_('I confirm this animal transfer and financial split.'),
    )

    def __init__(self, *args, source_case, **kwargs):
        self.source_case = source_case
        super().__init__(*args, **kwargs)
        self.fields['target_animal'].queryset = (
            available_dogs_for_new_sale_process(
                Animal.objects.select_related('breed'),
            )
            .exclude(pk=source_case.animal_id)
            .order_by('name', 'pk')
        )


class AdminCompleteSaleForm(forms.Form):
    final_price = forms.DecimalField(
        min_value=0,
        max_digits=9,
        decimal_places=2,
        label=_('Final sale price'),
    )
    payment_provider = forms.ChoiceField(
        choices=ADMIN_OFFLINE_PAYMENT_CHOICES,
        label=_('Final balance payment method'),
    )
    sold_at = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}),
        label=_('Sold at'),
    )
    credit = forms.ModelChoiceField(
        queryset=CustomerCredit.objects.none(),
        required=False,
        label=_('Additional customer credit'),
    )
    credit_amount = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=9,
        decimal_places=2,
        initial=0,
        label=_('Additional credit to apply'),
    )
    payment_reference = forms.CharField(
        required=False,
        max_length=150,
        label=_('Payment reference'),
    )
    payment_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label=_('Payment note'),
        help_text=_(
            'Required for complimentary and other settlement methods.'
        ),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        label=_('Sale notes'),
    )
    confirm = forms.BooleanField(label=_('I confirm the final animal sale.'))

    def __init__(self, *args, sale_case: AnimalSaleCase, **kwargs):
        self.sale_case = sale_case
        super().__init__(*args, **kwargs)
        credits = CustomerCredit.objects.filter(
            status=CustomerCredit.Status.ACTIVE,
            currency=sale_case.currency,
        )
        if sale_case.user_id:
            credits = credits.filter(user_id=sale_case.user_id)
        else:
            credits = credits.filter(
                user__isnull=True,
                customer_email__iexact=sale_case.customer_email,
            )
        self.fields['credit'].queryset = credits

    def clean(self):
        data = super().clean()
        provider = data.get('payment_provider')
        final_price = data.get('final_price')
        payment_note = (data.get('payment_note') or '').strip()
        earlier_value = sum(
            (
                charge.settled_amount
                for charge in self.sale_case.charges.exclude(
                    stage=Charge.Stage.SALE,
                )
            ),
            decimal.Decimal('0.00'),
        )
        remaining_amount = (
            max(
                final_price - earlier_value - decimal.Decimal(
                    data.get('credit_amount') or 0
                ),
                decimal.Decimal('0.00'),
            )
            if final_price is not None
            else None
        )
        if (
            provider == Payment.Provider.COMPLIMENTARY
            and remaining_amount != 0
        ):
            self.add_error(
                'final_price',
                _(
                    'A complimentary completion cannot leave a final balance '
                    'due. Enter a matching final price, apply enough customer '
                    'credit, or resolve the balance first.'
                ),
            )
        if (
            provider in {
                Payment.Provider.COMPLIMENTARY,
                Payment.Provider.OTHER,
            }
            and not payment_note
        ):
            self.add_error(
                'payment_note',
                _(
                    'Explain this settlement method so the decision remains '
                    'auditable.'
                ),
            )
        return data
