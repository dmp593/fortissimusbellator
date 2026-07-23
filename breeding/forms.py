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

        reservations = capacity_consuming_reservations().filter(
            animal=self.instance
        )
        if (
            'pre_reservation_fee' in self.changed_data
            and reservations.exists()
        ):
            self.add_error(
                'pre_reservation_fee',
                _(
                    'The pre-reservation fee cannot change while this dog has '
                    'a pending, confirmed, or fulfilled reservation.'
                ),
            )

        sold_to = cleaned_data.get('sold_to')
        if sold_to and reservations.exclude(user=sold_to).exists():
            self.add_error(
                'sold_to',
                _('This dog is reserved by a different customer.'),
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

        if self.instance and self.instance.pk:
            status = self.instance.status

            # Style fields based on workflow stage
            if status == models.Litter.LitterStatus.EXPECTING:
                # Highlight expected fields, fade actual fields
                expected_fields = [
                    'expected_birth_date',
                    'expected_ready_date',
                    'expected_babies'
                ]

                actual_fields = [
                    'birth_date',
                    'ready_date',
                    'babies'
                ]

                for field in expected_fields:
                    if field in self.fields:
                        # Preserve correct widget class for date fields
                        if 'date' in field:
                            widget_class = 'vDateField'
                        elif 'babies' in field:
                            widget_class = 'vIntegerField'
                        else:
                            widget_class = 'vTextField'

                        self.fields[field].widget.attrs.update({
                            'class': f'{widget_class} expected-field',
                            'style': 'background-color: #fff3cd; '
                                     'border: 2px solid #ffc107;'
                        })

                for field in actual_fields:
                    if field in self.fields:
                        # Preserve correct widget class for date fields
                        if 'date' in field:
                            widget_class = 'vDateField'
                        elif 'babies' in field:
                            widget_class = 'vIntegerField'
                        else:
                            widget_class = 'vTextField'

                        self.fields[field].widget.attrs.update({
                            'class': f'{widget_class} disabled-field',
                            'style': 'background-color: #f8f9fa; '
                                     'color: #6c757d;',
                            # 'readonly': True
                        })
                        # self.fields[field].help_text = _(
                        #     _(
                        #         'This field will be available after marking '
                        #         'as born'
                        #     )
                        # )

            elif status in [models.Litter.LitterStatus.BORN]:
                # Highlight actual fields
                actual_fields = ['birth_date', 'ready_date', 'babies']

                for field in actual_fields:
                    if field in self.fields:
                        # Preserve correct widget class for date fields
                        if 'date' in field:
                            widget_class = 'vDateField'
                        elif 'babies' in field:
                            widget_class = 'vIntegerField'
                        else:
                            widget_class = 'vTextField'

                        self.fields[field].widget.attrs.update({
                            'class': f'{widget_class} actual-field',
                            'style': 'background-color: #d1ecf1; '
                                     'border: 2px solid #17a2b8;'
                        })

    def clean(self):
        cleaned_data = super().clean()
        babies = cleaned_data.get('babies')
        capacity = cleaned_data.get('pre_reservation_capacity') or 0
        status = cleaned_data.get('status')

        if capacity and babies is None:
            self.add_error(
                'pre_reservation_capacity',
                _('Enter the actual number of babies born first.'),
            )
        elif babies is not None and capacity > babies:
            self.add_error(
                'pre_reservation_capacity',
                _('Capacity cannot exceed the actual number of babies born.'),
            )

        if status == models.Litter.LitterStatus.EXPECTING and capacity:
            self.add_error(
                'pre_reservation_capacity',
                _('Expecting litters must have a reservation capacity of zero.'),
            )

        if not self.instance.pk:
            return cleaned_data

        from reservations.availability import capacity_consuming_reservations

        reservations = capacity_consuming_reservations().filter(
            litter=self.instance
        )
        reserved_count = reservations.count()
        if capacity < reserved_count:
            self.add_error(
                'pre_reservation_capacity',
                _(
                    'Capacity cannot be lower than the %(count)d places '
                    'already reserved.'
                ) % {'count': reserved_count},
            )
        if babies is not None and babies < reserved_count:
            self.add_error(
                'babies',
                _(
                    'Actual babies cannot be lower than the %(count)d places '
                    'already reserved.'
                ) % {'count': reserved_count},
            )
        if (
            'pre_reservation_fee' in self.changed_data
            and reservations.exists()
        ):
            self.add_error(
                'pre_reservation_fee',
                _(
                    'The pre-reservation fee cannot change while this litter '
                    'has pending, confirmed, or fulfilled reservations.'
                ),
            )
        return cleaned_data
