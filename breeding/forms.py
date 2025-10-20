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
