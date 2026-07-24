import decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Promotion(models.Model):
    class DiscountType(models.TextChoices):
        FIXED = 'fixed', _('Fixed amount')
        PERCENTAGE = 'percentage', _('Percentage')

    class PurchaseStage(models.TextChoices):
        PRE_RESERVATION = 'pre_reservation', _('Pre-reservation')
        RESERVATION = 'reservation', _('Reservation')
        BOTH = 'both', _('Pre-reservation and reservation')

    class Scope(models.TextChoices):
        ANY = 'any', _('Any dog (all breeds)')
        BREEDS = 'breeds', _('Selected breeds')
        SPECIFIC_DOGS = 'specific_dogs', _('Selected dogs')

    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('promotion code'),
        help_text=_('Codes are stored and matched without letter case.'),
    )
    discount_type = models.CharField(
        max_length=20,
        choices=DiscountType,
        verbose_name=_('discount type'),
    )
    purchase_stage = models.CharField(
        max_length=20,
        choices=PurchaseStage,
        default=PurchaseStage.PRE_RESERVATION,
        verbose_name=_('purchase stage'),
        help_text=_(
            'Choose whether the code can discount the pre-reservation fee, '
            'the reservation deposit, or either purchase.'
        ),
    )
    value = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        validators=[MinValueValidator(decimal.Decimal('0.01'))],
        verbose_name=_('discount value'),
    )
    scope = models.CharField(
        max_length=30,
        choices=Scope,
        default=Scope.ANY,
        verbose_name=_('applies to'),
    )
    breeds = models.ManyToManyField(
        'breeding.Breed',
        blank=True,
        related_name='pre_reservation_promotions',
        verbose_name=_('breeds'),
    )
    dogs = models.ManyToManyField(
        'breeding.Animal',
        blank=True,
        related_name='pre_reservation_promotions',
        verbose_name=_('dogs'),
    )
    active = models.BooleanField(default=True, verbose_name=_('active'))
    starts_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('starts at'),
    )
    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('ends at'),
    )
    max_redemptions = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_('maximum redemptions'),
        help_text=_('Leave empty for unlimited redemptions.'),
    )
    max_redemptions_per_user = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_('maximum redemptions per user'),
        help_text=_('Leave empty for unlimited redemptions per user.'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        verbose_name = _('promotion')
        verbose_name_plural = _('promotions')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gt=0),
                name='promotion_value_gt_zero',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(discount_type='fixed')
                    | models.Q(
                        discount_type='percentage',
                        value__lte=100,
                    )
                ),
                name='promotion_percentage_lte_100',
            ),
        ]

    def clean(self):
        super().clean()
        self.code = self.normalize_code(self.code)

        errors = {}
        if (
            self.discount_type == self.DiscountType.PERCENTAGE
            and self.value is not None
            and self.value > 100
        ):
            errors['value'] = _('A percentage discount cannot exceed 100%.')

        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors['ends_at'] = _('The end date must be after the start date.')

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.code = self.normalize_code(self.code)
        super().save(*args, **kwargs)

    @staticmethod
    def normalize_code(code: str) -> str:
        return (code or '').strip().upper()

    def __str__(self):
        return self.code
