import decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from breeding import models


class DecimalToIntegerField(forms.IntegerField):
    def prepare_value(self, value):
        if isinstance(value, decimal.Decimal):
            return int(value)

        return value


class AnimalForm(forms.ModelForm):
    price_in_euros = DecimalToIntegerField(
        min_value=0,
        required=False,
        label=_('Price (€)')
    )

    discount_in_euros = DecimalToIntegerField(
        min_value=0,
        required=False,
        label=_('Discount (€)')
    )

    class Meta:
        model = models.Animal
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance.pk:
            return cleaned_data

        from reservations.availability import capacity_consuming_reservations

        from reservations.availability import inventory_blocking_reservations

        pre_reservations = capacity_consuming_reservations().filter(
            animal=self.instance
        )
        reservations = inventory_blocking_reservations().filter(
            pre_reservation__animal=self.instance,
        )
        if (
            {
                'price_in_euros',
                'discount_in_euros',
                'pre_reservation_fee',
                'reservation_deposit_percentage',
                'reservation_offer_hours',
            }.intersection(self.changed_data)
            and (pre_reservations.exists() or reservations.exists())
        ):
            for field_name in {
                'price_in_euros',
                'discount_in_euros',
                'pre_reservation_fee',
                'reservation_deposit_percentage',
                'reservation_offer_hours',
            }.intersection(self.changed_data):
                self.add_error(
                    field_name,
                    _(
                        'Price and payment settings cannot change while this '
                        'dog has an active pre-reservation or reservation.'
                    ),
                )

        return cleaned_data


class LitterAdminForm(forms.ModelForm):
    """
    Custom form for Litter admin that shows/hides fields based on status.
    """

    class Meta:
        model = models.Litter
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance or not self.instance.pk:
            return

        if self.instance.status == models.Litter.LitterStatus.EXPECTING:
            self._style_fields(
                (
                    'expected_birth_date',
                    'expected_ready_date',
                    'expected_babies',
                ),
                state_class='expected-field',
                style='background-color: #fff3cd; border: 2px solid #ffc107;',
            )
            self._style_fields(
                ('birth_date', 'ready_date', 'babies'),
                state_class='disabled-field',
                style='background-color: #f8f9fa; color: #6c757d;',
            )
        elif self.instance.status == models.Litter.LitterStatus.BORN:
            self._style_fields(
                ('birth_date', 'ready_date', 'babies'),
                state_class='actual-field',
                style='background-color: #d1ecf1; border: 2px solid #17a2b8;',
            )

    def _style_fields(self, field_names, *, state_class, style):
        for field_name in field_names:
            field = self.fields.get(field_name)
            if field is None:
                continue
            field.widget.attrs.update({
                'class': (
                    f'{self._widget_class(field_name)} {state_class}'
                ),
                'style': style,
            })

    @staticmethod
    def _widget_class(field_name):
        if 'date' in field_name:
            return 'vDateField'
        if 'babies' in field_name:
            return 'vIntegerField'
        return 'vTextField'
