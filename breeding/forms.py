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
