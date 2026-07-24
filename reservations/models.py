import decimal
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from fortissimusbellator.contact_details import validate_international_phone


def _prefetched_objects(instance, relation_name):
    return getattr(instance, '_prefetched_objects_cache', {}).get(
        relation_name,
    )


class PreReservationTermsQuerySet(models.QuerySet):
    def published(self):
        return self.filter(published_at__isnull=False, published_at__lte=timezone.now())

    def current(self):
        return self.published().order_by('-published_at', '-pk').first()


class PreReservationTerms(models.Model):
    version = models.CharField(max_length=50, unique=True, verbose_name=_('version'))
    description = models.TextField(verbose_name=_('description'))
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_('published at'),
    )

    objects = PreReservationTermsQuerySet.as_manager()

    class Meta:
        ordering = ('-published_at', '-pk')
        verbose_name = _('pre-reservation terms')
        verbose_name_plural = _('pre-reservation terms')

    def __str__(self):
        return self.version


class ReservationTermsQuerySet(models.QuerySet):
    def published(self):
        return self.filter(
            published_at__isnull=False,
            published_at__lte=timezone.now(),
        )

    def current(self):
        return self.published().order_by('-published_at', '-pk').first()


class ReservationTerms(models.Model):
    version = models.CharField(max_length=50, unique=True, verbose_name=_('version'))
    description = models.TextField(verbose_name=_('description'))
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_('published at'),
    )

    objects = ReservationTermsQuerySet.as_manager()

    class Meta:
        ordering = ('-published_at', '-pk')
        verbose_name = _('reservation terms')
        verbose_name_plural = _('reservation terms')

    def __str__(self):
        return self.version


class AnimalSaleCase(models.Model):
    class Origin(models.TextChoices):
        ONLINE = 'online', _('Online')
        ADMIN = 'admin', _('Created by staff')
        TRANSFER = 'transfer', _('Transferred')
        LEGACY = 'legacy', _('Imported legacy sale')

    class Status(models.TextChoices):
        PRE_RESERVATION = 'pre_reservation', _('Pre-reservation')
        RESERVATION = 'reservation', _('Reservation')
        SOLD = 'sold', _('Sold')
        CLOSED = 'closed', _('Closed')
        TRANSFERRED = 'transferred', _('Transferred')

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='animal_sale_cases',
        verbose_name=_('customer'),
    )
    animal = models.ForeignKey(
        'breeding.Animal',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='sale_cases',
        verbose_name=_('dog'),
    )
    origin = models.CharField(
        max_length=20,
        choices=Origin,
        default=Origin.ONLINE,
        db_index=True,
        verbose_name=_('origin'),
    )
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.PRE_RESERVATION,
        db_index=True,
        verbose_name=_('status'),
    )
    blocking_animal_key = models.GeneratedField(
        expression=models.Case(
            models.When(
                status__in=(
                    Status.PRE_RESERVATION,
                    Status.RESERVATION,
                    Status.SOLD,
                ),
                then=models.F('animal'),
            ),
            default=models.Value(None),
        ),
        output_field=models.BigIntegerField(null=True),
        db_persist=False,
        null=True,
    )
    target_name = models.CharField(max_length=150, verbose_name=_('target name'))
    target_breed = models.CharField(max_length=150, verbose_name=_('breed'))
    target_birth_date = models.DateField(null=True, blank=True)
    target_deleted_at = models.DateTimeField(null=True, blank=True)
    animal_price_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('dog price snapshot'),
    )
    reservation_deposit_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('reservation deposit percentage snapshot'),
    )
    reservation_deposit_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('reservation deposit target snapshot'),
    )

    customer_name = models.CharField(max_length=150)
    customer_email = models.EmailField(blank=True)
    customer_phone = models.CharField(
        max_length=30,
        blank=True,
        validators=[validate_international_phone],
    )
    customer_tax_number = models.CharField(max_length=30, blank=True)
    billing_address = models.CharField(max_length=255, blank=True)
    billing_postcode = models.CharField(max_length=20, blank=True)
    billing_city = models.CharField(max_length=100, blank=True)
    billing_country = models.CharField(max_length=2, default='PT')
    language_code = models.CharField(max_length=10, default='en')
    currency = models.CharField(max_length=3, default='EUR')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_animal_sale_cases',
        verbose_name=_('created by'),
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('animal sale process')
        verbose_name_plural = _('animal sale processes')
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['animal', 'status']),
            models.Index(fields=['user', 'created_at']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(animal__isnull=False)
                    | Q(target_deleted_at__isnull=False)
                ),
                name='sale_case_has_animal_or_deleted_snapshot',
            ),
            models.UniqueConstraint(
                fields=['blocking_animal_key'],
                name='one_blocking_sale_case_per_animal',
            ),
        ]

    @property
    def target_is_public(self):
        return bool(
            self.animal
            and self.animal.active
            and self.animal.for_sale
            and not self.animal.is_sold
        )

    @property
    def is_active(self):
        return self.status in {
            self.Status.PRE_RESERVATION,
            self.Status.RESERVATION,
        }

    def __str__(self):
        return f'{self.public_id} - {self.target_name}'


class Charge(models.Model):
    class Stage(models.TextChoices):
        PRE_RESERVATION = 'pre_reservation', _('Pre-reservation')
        RESERVATION = 'reservation', _('Reservation')
        SALE = 'sale', _('Final sale')

    class Status(models.TextChoices):
        OPEN = 'open', _('Open')
        PARTIALLY_PAID = 'partially_paid', _('Partially paid')
        PAID = 'paid', _('Paid')
        VOID = 'void', _('Void')

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sale_case = models.ForeignKey(
        AnimalSaleCase,
        on_delete=models.PROTECT,
        related_name='charges',
        verbose_name=_('sale process'),
    )
    stage = models.CharField(
        max_length=20,
        choices=Stage,
        db_index=True,
        verbose_name=_('stage'),
    )
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.OPEN,
        db_index=True,
        verbose_name=_('status'),
    )
    subtotal_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        verbose_name=_('subtotal'),
    )
    promotion = models.ForeignKey(
        'discounts.Promotion',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='charges',
        verbose_name=_('promotion'),
    )
    promotion_discount_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
        verbose_name=_('promotion discount'),
    )
    promotion_code = models.CharField(max_length=50, blank=True)
    promotion_discount_type = models.CharField(max_length=20, blank=True)
    promotion_value = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
    )
    currency = models.CharField(max_length=3, default='EUR')
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_sale_charges',
        verbose_name=_('created by'),
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name = _('charge')
        verbose_name_plural = _('charges')
        constraints = [
            models.UniqueConstraint(
                fields=['sale_case', 'stage'],
                name='one_charge_per_sale_case_stage',
            ),
            models.CheckConstraint(
                condition=Q(subtotal_amount__gte=0),
                name='charge_subtotal_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(promotion_discount_amount__gte=0),
                name='charge_promotion_discount_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(
                    promotion_discount_amount__lte=F('subtotal_amount')
                ),
                name='charge_promotion_discount_lte_subtotal',
            ),
        ]

    @property
    def adjustment_amount(self):
        adjustments = _prefetched_objects(self, 'adjustments')
        if adjustments is not None:
            return sum(
                (adjustment.amount for adjustment in adjustments),
                decimal.Decimal('0.00'),
            )
        value = self.adjustments.aggregate(
            total=models.Sum('amount'),
        )['total']
        return value or decimal.Decimal('0.00')

    @property
    def total_amount(self):
        return max(
            self.subtotal_amount
            - self.promotion_discount_amount
            + self.adjustment_amount,
            decimal.Decimal('0.00'),
        )

    @property
    def gross_payment_amount(self):
        total = decimal.Decimal('0.00')
        for payment in self.payments.all():
            if payment.status in {
                Payment.Status.PAID,
                Payment.Status.PARTIALLY_REFUNDED,
                Payment.Status.REFUNDED,
            }:
                total += payment.amount
        return total

    @property
    def paid_amount(self):
        refunded = decimal.Decimal('0.00')
        for payment in self.payments.all():
            if payment.status in {
                Payment.Status.PAID,
                Payment.Status.PARTIALLY_REFUNDED,
                Payment.Status.REFUNDED,
            }:
                refunded += payment.succeeded_refund_amount
        total = self.gross_payment_amount - refunded
        return max(total, decimal.Decimal('0.00'))

    @property
    def credit_amount(self):
        allocations = _prefetched_objects(self, 'credit_allocations')
        if allocations is not None:
            return sum(
                (
                    allocation.amount
                    for allocation in allocations
                    if allocation.reversed_at is None
                ),
                decimal.Decimal('0.00'),
            )
        value = self.credit_allocations.filter(
            reversed_at__isnull=True,
        ).aggregate(total=models.Sum('amount'))['total']
        return value or decimal.Decimal('0.00')

    @property
    def settled_amount(self):
        return self.paid_amount + self.credit_amount

    @property
    def amount_due(self):
        return max(
            self.total_amount - self.settled_amount,
            decimal.Decimal('0.00'),
        )

    @property
    def purchase(self):
        if self.stage == self.Stage.PRE_RESERVATION:
            return getattr(self, 'pre_reservation_stage', None)
        if self.stage == self.Stage.RESERVATION:
            return getattr(self, 'reservation_stage', None)
        return getattr(self, 'sale_stage', None)

    def __str__(self):
        return (
            f'{self.sale_case.target_name} - '
            f'{self.get_stage_display()} - {self.get_status_display()}'
        )


class ChargeAdjustment(models.Model):
    class Kind(models.TextChoices):
        MANUAL_DISCOUNT = 'manual_discount', _('Manual discount')
        SURCHARGE = 'surcharge', _('Surcharge')
        WAIVER = 'waiver', _('Waiver')
        CORRECTION = 'correction', _('Correction')

    charge = models.ForeignKey(
        Charge,
        on_delete=models.PROTECT,
        related_name='adjustments',
    )
    kind = models.CharField(max_length=30, choices=Kind)
    amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        help_text=_('Use a negative value to reduce the amount due.'),
    )
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_charge_adjustments',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name = _('charge adjustment')
        verbose_name_plural = _('charge adjustments')
        constraints = [
            models.CheckConstraint(
                condition=~Q(amount=0),
                name='charge_adjustment_amount_nonzero',
            ),
        ]

    def __str__(self):
        return f'{self.get_kind_display()}: {self.amount}'


class PreReservationQuerySet(models.QuerySet):
    def capacity_consuming(self):
        return self.filter(
            status__in=(
                PreReservation.Status.PENDING_PAYMENT,
                PreReservation.Status.AWAITING_REVIEW,
                PreReservation.Status.ACCEPTED,
            )
        )

    def paid(self):
        paid_statuses = (
            Payment.Status.PAID,
            Payment.Status.PARTIALLY_REFUNDED,
            Payment.Status.REFUNDED,
        )
        return self.filter(
            Q(charge__payments__status__in=paid_statuses)
            | Q(payment__status__in=paid_statuses)
        ).distinct()


class PreReservation(models.Model):
    class TargetType(models.TextChoices):
        DOG = 'dog', _('Dog')
        LITTER = 'litter', _('Litter')

    class Status(models.TextChoices):
        PENDING_PAYMENT = 'pending_payment', _('Awaiting payment')
        AWAITING_REVIEW = 'awaiting_review', _('Awaiting review')
        ACCEPTED = 'accepted', _('Accepted')
        NOT_ACCEPTED = 'not_accepted', _('Not accepted')
        PAYMENT_FAILED = 'payment_failed', _('Payment failed')
        EXPIRED = 'expired', _('Expired')
        CANCELLED_BY_USER = 'cancelled_by_user', _('Cancelled by customer')
        CANCELLED_BY_ADMIN = 'cancelled_by_admin', _('Cancelled by staff')
        RESERVATION_OFFER_EXPIRED = (
            'reservation_offer_expired',
            _('Reservation offer expired'),
        )
        CONVERTED_TO_RESERVATION = (
            'converted_to_reservation',
            _('Converted to reservation'),
        )
        TRANSFERRED = 'transferred', _('Transferred')

    class TermsAcceptanceSource(models.TextChoices):
        CUSTOMER_ONLINE = 'customer_online', _('Accepted online by customer')
        STAFF_RECORDED = 'staff_recorded', _('Acceptance recorded by staff')
        PENDING_CUSTOMER = 'pending_customer', _('Awaiting customer acceptance')

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sale_case = models.OneToOneField(
        AnimalSaleCase,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='pre_reservation',
        verbose_name=_('sale process'),
    )
    charge = models.OneToOneField(
        Charge,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='pre_reservation_stage',
        verbose_name=_('charge'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='pre_reservations',
        verbose_name=_('customer'),
    )
    target_type = models.CharField(
        max_length=10,
        choices=TargetType,
        verbose_name=_('target type'),
    )
    animal = models.ForeignKey(
        'breeding.Animal',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pre_reservations',
        verbose_name=_('dog'),
    )
    litter = models.ForeignKey(
        'breeding.Litter',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pre_reservations',
        verbose_name=_('litter'),
    )
    promotion = models.ForeignKey(
        'discounts.Promotion',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='pre_reservations',
        verbose_name=_('promotion'),
    )
    status = models.CharField(
        max_length=30,
        choices=Status,
        default=Status.PENDING_PAYMENT,
        db_index=True,
        verbose_name=_('status'),
    )
    active_animal_key = models.GeneratedField(
        expression=models.Case(
            models.When(
                target_type=TargetType.DOG,
                status__in=(
                    Status.PENDING_PAYMENT,
                    Status.AWAITING_REVIEW,
                    Status.ACCEPTED,
                ),
                then=models.F('animal'),
            ),
            default=models.Value(None),
        ),
        output_field=models.BigIntegerField(null=True),
        db_persist=False,
        null=True,
    )

    target_name = models.CharField(max_length=150, verbose_name=_('target name'))
    target_breed = models.CharField(max_length=150, verbose_name=_('breed'))
    target_birth_date = models.DateField(null=True, blank=True)
    target_deleted_at = models.DateTimeField(null=True, blank=True)

    customer_name = models.CharField(max_length=150)
    customer_email = models.EmailField()
    customer_phone = models.CharField(
        max_length=30,
        validators=[validate_international_phone],
    )
    customer_tax_number = models.CharField(max_length=30, blank=True)
    billing_address = models.CharField(max_length=255, blank=True)
    billing_postcode = models.CharField(max_length=20, blank=True)
    billing_city = models.CharField(max_length=100, blank=True)
    billing_country = models.CharField(max_length=2, default='PT')
    language_code = models.CharField(max_length=10, default='en')

    fee_amount = models.DecimalField(max_digits=9, decimal_places=2)
    discount_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
    )
    total_amount = models.DecimalField(max_digits=9, decimal_places=2)
    currency = models.CharField(max_length=3, default='EUR')
    promotion_code = models.CharField(max_length=50, blank=True)
    promotion_discount_type = models.CharField(max_length=20, blank=True)
    promotion_value = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
    )
    animal_price_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('dog price snapshot'),
    )
    reservation_deposit_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('reservation deposit percentage snapshot'),
    )
    reservation_deposit_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('reservation deposit target snapshot'),
    )

    hold_expires_at = models.DateTimeField(null=True, blank=True)
    terms = models.ForeignKey(
        PreReservationTerms,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='reservations',
        verbose_name=_('accepted terms'),
    )
    terms_acceptance_source = models.CharField(
        max_length=30,
        choices=TermsAcceptanceSource,
        default=TermsAcceptanceSource.CUSTOMER_ONLINE,
        verbose_name=_('terms acceptance source'),
    )
    non_refundable_accepted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_pre_reservations',
    )
    review_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='cancelled_pre_reservations',
    )
    cancellation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PreReservationQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('pre-reservation')
        verbose_name_plural = _('pre-reservations')
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['animal', 'status']),
            models.Index(fields=['litter', 'status']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        target_type='dog',
                        animal__isnull=False,
                        litter__isnull=True,
                    )
                    | Q(
                        target_type='litter',
                        animal__isnull=True,
                        litter__isnull=False,
                    )
                    | Q(
                        animal__isnull=True,
                        litter__isnull=True,
                        target_deleted_at__isnull=False,
                    )
                ),
                name='pre_reservation_target_matches_type',
            ),
            models.CheckConstraint(
                condition=Q(fee_amount__gte=0),
                name='pre_reservation_fee_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(discount_amount__gte=0),
                name='pre_reservation_discount_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(discount_amount__lte=F('fee_amount')),
                name='pre_reservation_discount_lte_fee',
            ),
            models.CheckConstraint(
                condition=Q(total_amount__gte=0),
                name='pre_reservation_total_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(
                    total_amount=F('fee_amount') - F('discount_amount')
                ),
                name='pre_reservation_total_matches_snapshot',
            ),
            models.CheckConstraint(
                condition=(
                    Q(target_type='litter')
                    | Q(
                        animal_price_amount__isnull=False,
                        animal_price_amount__gte=0,
                        reservation_deposit_percentage__isnull=False,
                        reservation_deposit_percentage__gt=0,
                        reservation_deposit_percentage__lte=100,
                        reservation_deposit_amount__isnull=False,
                        reservation_deposit_amount__gte=0,
                    )
                    | Q(target_deleted_at__isnull=False)
                ),
                name='dog_pre_reservation_has_price_snapshot',
            ),
            models.UniqueConstraint(
                fields=['active_animal_key'],
                name='one_active_pre_reservation_per_dog',
            ),
        ]

    def clean(self):
        super().clean()
        if self.pk is None:
            has_animal = self.animal_id is not None
            has_litter = self.litter_id is not None
            if has_animal == has_litter:
                raise ValidationError(
                    _('A pre-reservation must target exactly one dog or litter.')
                )

        expected_total = self.fee_amount - self.discount_amount
        if self.total_amount != expected_total:
            raise ValidationError(
                {'total_amount': _('Total must equal fee minus discount.')}
            )
        if (
            self.terms_acceptance_source
            == self.TermsAcceptanceSource.PENDING_CUSTOMER
            and (
                self.terms_id is not None
                or self.non_refundable_accepted_at is not None
            )
        ):
            raise ValidationError(
                _(
                    'Pending customer terms cannot already have an accepted '
                    'version or acceptance date.'
                )
            )
        if (
            self.terms_acceptance_source
            != self.TermsAcceptanceSource.PENDING_CUSTOMER
            and (
                self.terms_id is None
                or self.non_refundable_accepted_at is None
            )
        ):
            raise ValidationError(
                _('Accepted pre-reservation terms require a version and date.')
            )

    @property
    def target(self):
        return self.animal or self.litter

    @property
    def target_is_public(self):
        if self.animal:
            return (
                self.animal.active
                and self.animal.for_sale
                and not self.animal.is_sold
            )
        if self.litter:
            return self.litter.active
        return False

    @property
    def is_capacity_consuming(self):
        return self.status in {
            self.Status.PENDING_PAYMENT,
            self.Status.AWAITING_REVIEW,
            self.Status.ACCEPTED,
        }

    @property
    def can_user_cancel(self):
        return self.status in {
            self.Status.PENDING_PAYMENT,
            self.Status.AWAITING_REVIEW,
        }

    @property
    def refundable_amount(self):
        if self.charge_id:
            return sum(
                (
                    payment.refundable_amount
                    for payment in self.charge.payments.filter(
                        status__in=(
                            Payment.Status.PAID,
                            Payment.Status.PARTIALLY_REFUNDED,
                        )
                    )
                ),
                decimal.Decimal('0.00'),
            )
        if hasattr(self, 'payment'):
            return self.payment.refundable_amount
        return decimal.Decimal('0.00')

    def __str__(self):
        return f'{self.public_id} - {self.target_name}'


class Reservation(models.Model):
    class Status(models.TextChoices):
        OFFERED = 'offered', _('Awaiting customer')
        PENDING_PAYMENT = 'pending_payment', _('Awaiting payment')
        PAYMENT_FAILED = 'payment_failed', _('Payment failed')
        CONFIRMED = 'confirmed', _('Reserved')
        EXPIRED = 'expired', _('Expired')
        CANCELLED_BY_ADMIN = 'cancelled_by_admin', _('Cancelled by staff')
        TRANSFERRED = 'transferred', _('Transferred')

    class TermsAcceptanceSource(models.TextChoices):
        CUSTOMER_ONLINE = 'customer_online', _('Accepted online by customer')
        STAFF_RECORDED = 'staff_recorded', _('Acceptance recorded by staff')
        PENDING_CUSTOMER = 'pending_customer', _('Awaiting customer acceptance')

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sale_case = models.OneToOneField(
        AnimalSaleCase,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='reservation',
        verbose_name=_('sale process'),
    )
    charge = models.OneToOneField(
        Charge,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='reservation_stage',
        verbose_name=_('charge'),
    )
    pre_reservation = models.OneToOneField(
        PreReservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='reservation',
        verbose_name=_('pre-reservation'),
    )
    promotion = models.ForeignKey(
        'discounts.Promotion',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='reservations',
        verbose_name=_('promotion'),
    )
    status = models.CharField(
        max_length=30,
        choices=Status,
        default=Status.OFFERED,
        db_index=True,
        verbose_name=_('status'),
    )
    pre_reservation_credit_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
        verbose_name=_('pre-reservation credit'),
    )
    customer_credit_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
        verbose_name=_('customer credit'),
    )
    deposit_target_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        verbose_name=_('reservation deposit target'),
    )
    discount_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
        verbose_name=_('reservation discount'),
    )
    payment_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        verbose_name=_('reservation payment amount'),
    )
    promotion_code = models.CharField(max_length=50, blank=True)
    promotion_discount_type = models.CharField(max_length=20, blank=True)
    promotion_value = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
    )
    currency = models.CharField(max_length=3, default='EUR')
    offer_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    terms = models.ForeignKey(
        ReservationTerms,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='reservations',
        verbose_name=_('accepted terms'),
    )
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_acceptance_source = models.CharField(
        max_length=30,
        choices=TermsAcceptanceSource,
        default=TermsAcceptanceSource.PENDING_CUSTOMER,
        verbose_name=_('terms acceptance source'),
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='cancelled_reservations',
    )
    cancellation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('reservation')
        verbose_name_plural = _('reservations')
        indexes = [
            models.Index(fields=['status', 'offer_expires_at']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(pre_reservation_credit_amount__gte=0),
                name='reservation_credit_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(deposit_target_amount__gte=0),
                name='reservation_deposit_target_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(customer_credit_amount__gte=0),
                name='reservation_customer_credit_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(payment_amount__gte=0),
                name='animal_reservation_payment_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(discount_amount__gte=0),
                name='reservation_discount_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(
                    pre_reservation_credit_amount__lte=(
                        F('deposit_target_amount')
                        - F('customer_credit_amount')
                    )
                ),
                name='reservation_credit_lte_deposit_target',
            ),
            models.CheckConstraint(
                condition=Q(
                    discount_amount__lte=(
                        F('deposit_target_amount')
                        - F('pre_reservation_credit_amount')
                        - F('customer_credit_amount')
                    )
                ),
                name='reservation_discount_lte_amount_due',
            ),
            models.CheckConstraint(
                condition=Q(
                    payment_amount=(
                        F('deposit_target_amount')
                        - F('pre_reservation_credit_amount')
                        - F('customer_credit_amount')
                        - F('discount_amount')
                    )
                ),
                name='reservation_payment_matches_amount_due',
            ),
            models.CheckConstraint(
                condition=(
                    Q(pre_reservation__isnull=False)
                    | Q(sale_case__isnull=False)
                ),
                name='reservation_has_pre_reservation_or_sale_case',
            ),
        ]

    def clean(self):
        super().clean()
        expected = self.amount_before_discount - self.discount_amount
        if self.payment_amount != expected:
            raise ValidationError(
                {
                    'payment_amount': _(
                        'Payment must equal the deposit target minus the '
                        'pre-reservation credit and reservation discount.'
                    )
                }
            )

    @property
    def amount_before_discount(self):
        return (
            self.deposit_target_amount
            - self.pre_reservation_credit_amount
            - self.customer_credit_amount
        )

    @property
    def workflow(self):
        if self.sale_case_id:
            return self.sale_case
        if self.pre_reservation_id:
            return self.pre_reservation.sale_case
        return None

    @property
    def user(self):
        if self.workflow:
            return self.workflow.user
        return self.pre_reservation.user if self.pre_reservation_id else None

    @property
    def animal(self):
        if self.workflow:
            return self.workflow.animal
        return self.pre_reservation.animal if self.pre_reservation_id else None

    @property
    def target_name(self):
        if self.workflow:
            return self.workflow.target_name
        return self.pre_reservation.target_name if self.pre_reservation_id else ''

    @property
    def target_breed(self):
        if self.workflow:
            return self.workflow.target_breed
        return self.pre_reservation.target_breed if self.pre_reservation_id else ''

    @property
    def language_code(self):
        if self.workflow:
            return self.workflow.language_code
        return self.pre_reservation.language_code if self.pre_reservation_id else 'en'

    @property
    def customer_email(self):
        if self.workflow:
            return self.workflow.customer_email
        return self.pre_reservation.customer_email if self.pre_reservation_id else ''

    @property
    def target_is_public(self):
        if self.workflow:
            return self.workflow.target_is_public
        return (
            self.pre_reservation.target_is_public
            if self.pre_reservation_id
            else False
        )

    @property
    def is_inventory_blocking(self):
        return self.status in {
            self.Status.OFFERED,
            self.Status.PENDING_PAYMENT,
            self.Status.PAYMENT_FAILED,
            self.Status.CONFIRMED,
        }

    def __str__(self):
        return f'{self.public_id} - {self.target_name}'


class Payment(models.Model):
    class Provider(models.TextChoices):
        STRIPE = 'stripe', 'Stripe'
        CASH = 'cash', _('Cash')
        BANK_TRANSFER = 'bank_transfer', _('Bank transfer')
        CARD_TERMINAL = 'card_terminal', _('Card terminal')
        OTHER = 'other', _('Other')
        COMPLIMENTARY = 'complimentary', _('Complimentary')

    class Status(models.TextChoices):
        INITIALIZING = 'initializing', _('Initializing')
        PENDING = 'pending', _('Pending')
        PAID = 'paid', _('Paid')
        FAILED = 'failed', _('Failed')
        PARTIALLY_REFUNDED = 'partially_refunded', _('Partially refunded')
        REFUNDED = 'refunded', _('Refunded')

    charge = models.ForeignKey(
        Charge,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='payments',
        verbose_name=_('charge'),
    )
    pre_reservation = models.OneToOneField(
        PreReservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='payment',
        verbose_name=_('pre-reservation'),
    )
    animal_reservation = models.OneToOneField(
        Reservation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='payment',
        verbose_name=_('reservation'),
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider,
        default=Provider.STRIPE,
    )
    status = models.CharField(
        max_length=30,
        choices=Status,
        default=Status.INITIALIZING,
        db_index=True,
    )
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    currency = models.CharField(max_length=3, default='EUR')
    stripe_checkout_session_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    checkout_started_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
    )
    stripe_payment_intent_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    stripe_charge_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    stripe_checkout_url = models.TextField(blank=True)
    stripe_checkout_expires_at = models.DateTimeField(null=True, blank=True)
    provider_fee_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
    )
    provider_net_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
    )
    financials_attempt_count = models.PositiveIntegerField(default=0)
    financials_next_retry_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    financials_last_error = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    checkout_attempt_number = models.PositiveIntegerField(default=1)
    external_reference = models.CharField(max_length=150, blank=True)
    note = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='recorded_sale_payments',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('payment')
        verbose_name_plural = _('payments')
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name='reservation_payment_amount_gte_zero',
            ),
            models.CheckConstraint(
                condition=(
                    Q(charge__isnull=False)
                    | Q(pre_reservation__isnull=False)
                    | Q(animal_reservation__isnull=False)
                ),
                name='payment_has_charge_or_legacy_purchase',
            ),
            models.CheckConstraint(
                condition=~Q(
                    pre_reservation__isnull=False,
                    animal_reservation__isnull=False,
                ),
                name='payment_has_at_most_one_legacy_purchase',
            ),
        ]

    @property
    def purchase(self):
        purchase = self.pre_reservation or self.animal_reservation
        if purchase is not None:
            return purchase
        return self.charge.purchase if self.charge_id else None

    @property
    def succeeded_refund_amount(self):
        refunds = _prefetched_objects(self, 'refunds')
        if refunds is not None:
            return sum(
                (
                    refund.amount
                    for refund in refunds
                    if refund.status == PaymentRefund.Status.SUCCEEDED
                ),
                decimal.Decimal('0.00'),
            )
        value = self.refunds.filter(
            status=PaymentRefund.Status.SUCCEEDED
        ).aggregate(total=models.Sum('amount'))['total']
        return value or decimal.Decimal('0.00')

    @property
    def committed_refund_amount(self):
        refunds = _prefetched_objects(self, 'refunds')
        if refunds is not None:
            committed_statuses = {
                PaymentRefund.Status.PENDING,
                PaymentRefund.Status.PROCESSING,
                PaymentRefund.Status.SUCCEEDED,
            }
            return sum(
                (
                    refund.amount
                    for refund in refunds
                    if refund.status in committed_statuses
                ),
                decimal.Decimal('0.00'),
            )
        value = self.refunds.filter(
            status__in=(
                PaymentRefund.Status.PENDING,
                PaymentRefund.Status.PROCESSING,
                PaymentRefund.Status.SUCCEEDED,
            )
        ).aggregate(total=models.Sum('amount'))['total']
        return value or decimal.Decimal('0.00')

    @property
    def refundable_amount(self):
        return max(
            self.amount - self.committed_refund_amount,
            decimal.Decimal('0.00'),
        )

    def __str__(self):
        purchase = self.purchase
        reference = purchase.public_id if purchase else self.pk
        return f'{reference} - {self.get_status_display()}'


class PaymentRefund(models.Model):
    class CalculationType(models.TextChoices):
        FIXED = 'fixed', _('Fixed amount')
        TARGET_PERCENTAGE = 'target_percentage', _('Target percentage')
        FULL_REMAINING = 'full_remaining', _('Full remaining amount')

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        PROCESSING = 'processing', _('Processing')
        SUCCEEDED = 'succeeded', _('Succeeded')
        FAILED = 'failed', _('Failed')

    class ProcessingMethod(models.TextChoices):
        STRIPE = 'stripe', _('Stripe')
        MANUAL = 'manual', _('Recorded manually')

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name='refunds',
    )
    closure = models.ForeignKey(
        'WorkflowClosure',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='refunds',
    )
    transfer = models.ForeignKey(
        'AnimalWorkflowTransfer',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='refunds',
    )
    processing_method = models.CharField(
        max_length=20,
        choices=ProcessingMethod,
        default=ProcessingMethod.STRIPE,
    )
    calculation_type = models.CharField(
        max_length=30,
        choices=CalculationType,
    )
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    target_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    provider_fee_amount_snapshot = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('Stripe fee snapshot'),
    )
    provider_net_amount_snapshot = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('Stripe net amount snapshot'),
    )
    provider_loss_acknowledged = models.BooleanField(
        default=False,
        verbose_name=_('processing cost loss acknowledged'),
    )
    reason = models.TextField()
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='requested_payment_refunds',
    )
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.PENDING,
        db_index=True,
    )
    stripe_refund_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-requested_at']
        verbose_name = _('payment refund')
        verbose_name_plural = _('payment refunds')
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name='payment_refund_amount_gt_zero',
            ),
            models.CheckConstraint(
                condition=(
                    Q(target_percentage__isnull=True)
                    | Q(
                        target_percentage__gt=0,
                        target_percentage__lte=100,
                    )
                ),
                name='payment_refund_percentage_valid',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        calculation_type='target_percentage',
                        target_percentage__isnull=False,
                    )
                    | Q(
                        ~Q(calculation_type='target_percentage'),
                        target_percentage__isnull=True,
                    )
                ),
                name='payment_refund_percentage_matches_type',
            ),
        ]

    def __str__(self):
        return f'{self.public_id} - {self.amount} {self.payment.currency}'


class WorkflowClosure(models.Model):
    class Kind(models.TextChoices):
        CANCELLED = 'cancelled', _('Cancelled')
        SALE_CANCELLED = 'sale_cancelled', _('Sale cancelled')
        REJECTED = 'rejected', _('Not accepted')
        EXPIRED = 'expired', _('Expired')
        TRANSFERRED = 'transferred', _('Transferred')

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sale_case = models.ForeignKey(
        AnimalSaleCase,
        on_delete=models.PROTECT,
        related_name='closures',
    )
    stage = models.CharField(max_length=20, choices=Charge.Stage)
    kind = models.CharField(max_length=20, choices=Kind)
    paid_value_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        verbose_name=_('available paid value'),
    )
    refund_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
    )
    credit_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
    )
    retained_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
    )
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_workflow_closures',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('workflow closure')
        verbose_name_plural = _('workflow closures')
        constraints = [
            models.CheckConstraint(
                condition=Q(paid_value_amount__gte=0),
                name='workflow_closure_paid_value_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(refund_amount__gte=0),
                name='workflow_closure_refund_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(credit_amount__gte=0),
                name='workflow_closure_credit_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(retained_amount__gte=0),
                name='workflow_closure_retained_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(
                    paid_value_amount=(
                        F('refund_amount')
                        + F('credit_amount')
                        + F('retained_amount')
                    )
                ),
                name='workflow_closure_split_matches_paid_value',
            ),
        ]

    def __str__(self):
        return f'{self.sale_case.target_name} - {self.get_kind_display()}'


class CustomerCredit(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', _('Active')
        EXHAUSTED = 'exhausted', _('Exhausted')
        VOID = 'void', _('Void')

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='animal_purchase_credits',
        verbose_name=_('customer'),
    )
    customer_name = models.CharField(max_length=150, blank=True)
    customer_email = models.EmailField(blank=True)
    source_sale_case = models.ForeignKey(
        AnimalSaleCase,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='issued_credits',
    )
    source_closure = models.ForeignKey(
        WorkflowClosure,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='credits',
    )
    source_transfer = models.ForeignKey(
        'AnimalWorkflowTransfer',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='credits',
    )
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    currency = models.CharField(max_length=3, default='EUR')
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.ACTIVE,
        db_index=True,
    )
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_customer_credits',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name = _('customer credit')
        verbose_name_plural = _('customer credits')
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name='customer_credit_amount_gt_zero',
            ),
        ]

    @property
    def allocated_amount(self):
        allocations = _prefetched_objects(self, 'allocations')
        if allocations is not None:
            return sum(
                (
                    allocation.amount
                    for allocation in allocations
                    if allocation.reversed_at is None
                ),
                decimal.Decimal('0.00'),
            )
        value = self.allocations.filter(
            reversed_at__isnull=True,
        ).aggregate(total=models.Sum('amount'))['total']
        return value or decimal.Decimal('0.00')

    @property
    def available_amount(self):
        if self.status == self.Status.VOID:
            return decimal.Decimal('0.00')
        return max(
            self.amount - self.allocated_amount,
            decimal.Decimal('0.00'),
        )

    def __str__(self):
        customer = self.user or self.customer_email or self.customer_name
        return f'{customer} - {self.available_amount} {self.currency}'


class CreditAllocation(models.Model):
    credit = models.ForeignKey(
        CustomerCredit,
        on_delete=models.PROTECT,
        related_name='allocations',
    )
    charge = models.ForeignKey(
        Charge,
        on_delete=models.PROTECT,
        related_name='credit_allocations',
    )
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_credit_allocations',
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reversed_credit_allocations',
    )
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['created_at', 'pk']
        verbose_name = _('credit allocation')
        verbose_name_plural = _('credit allocations')
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name='credit_allocation_amount_gt_zero',
            ),
        ]

    def __str__(self):
        return f'{self.credit.public_id} -> {self.charge.public_id}'


class AnimalWorkflowTransfer(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source_case = models.ForeignKey(
        AnimalSaleCase,
        on_delete=models.PROTECT,
        related_name='outgoing_transfers',
    )
    target_case = models.OneToOneField(
        AnimalSaleCase,
        on_delete=models.PROTECT,
        related_name='incoming_transfer',
    )
    source_stage = models.CharField(max_length=20, choices=Charge.Stage)
    target_stage = models.CharField(max_length=20, choices=Charge.Stage)
    available_value_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
    )
    transferred_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
    )
    refund_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
    )
    retained_amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
    )
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_animal_workflow_transfers',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('animal workflow transfer')
        verbose_name_plural = _('animal workflow transfers')
        constraints = [
            models.CheckConstraint(
                condition=Q(available_value_amount__gte=0),
                name='workflow_transfer_available_value_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(transferred_amount__gte=0),
                name='workflow_transfer_amount_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(refund_amount__gte=0),
                name='workflow_transfer_refund_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(retained_amount__gte=0),
                name='workflow_transfer_retained_gte_zero',
            ),
            models.CheckConstraint(
                condition=Q(
                    available_value_amount=(
                        F('transferred_amount')
                        + F('refund_amount')
                        + F('retained_amount')
                    )
                ),
                name='workflow_transfer_split_matches_available_value',
            ),
        ]

    def __str__(self):
        return (
            f'{self.source_case.target_name} -> '
            f'{self.target_case.target_name}'
        )


class AnimalSale(models.Model):
    class Source(models.TextChoices):
        WORKFLOW = 'workflow', _('Commercial workflow')
        LEGACY = 'legacy', _('Imported legacy record')

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source = models.CharField(
        max_length=20,
        choices=Source,
        default=Source.WORKFLOW,
        db_index=True,
    )
    sale_case = models.OneToOneField(
        AnimalSaleCase,
        on_delete=models.PROTECT,
        related_name='sale',
    )
    charge = models.OneToOneField(
        Charge,
        on_delete=models.PROTECT,
        related_name='sale_stage',
        null=True,
        blank=True,
    )
    final_price = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_('final sale price'),
    )
    sold_at = models.DateField(verbose_name=_('sold at'))
    notes = models.TextField(blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='completed_animal_sales',
    )
    voided_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_('voided at'),
    )
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='voided_animal_sales',
        verbose_name=_('voided by'),
    )
    void_reason = models.TextField(blank=True, verbose_name=_('void reason'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sold_at', '-created_at']
        verbose_name = _('animal sale')
        verbose_name_plural = _('animal sales')
        constraints = [
            models.CheckConstraint(
                condition=Q(final_price__isnull=True) | Q(final_price__gte=0),
                name='animal_sale_final_price_gte_zero',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        source='workflow',
                        charge__isnull=False,
                        final_price__isnull=False,
                    )
                    | Q(
                        source='legacy',
                        charge__isnull=True,
                        final_price__isnull=True,
                    )
                ),
                name='animal_sale_source_matches_financial_data',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        voided_at__isnull=True,
                        void_reason='',
                    )
                    | (
                        Q(voided_at__isnull=False)
                        & ~Q(void_reason='')
                    )
                ),
                name='animal_sale_void_metadata_consistent',
            ),
        ]

    @property
    def is_voided(self):
        return self.voided_at is not None

    def __str__(self):
        price = self.final_price if self.final_price is not None else _('unknown')
        return f'{self.sale_case.target_name} - {price}'


class ERPDocument(models.Model):
    class Kind(models.TextChoices):
        SALE = 'sale', _('Sale document')
        CREDIT_NOTE = 'credit_note', _('Credit note')

    class Status(models.TextChoices):
        DEFERRED = 'deferred', _('Integration deferred')
        PENDING = 'pending', _('Pending')
        PROCESSING = 'processing', _('Processing')
        INTEGRATED = 'integrated', _('Integrated')
        RETRYABLE_FAILURE = 'retryable_failure', _('Retryable failure')
        NEEDS_ATTENTION = 'needs_attention', _('Needs attention')

    class PDFStatus(models.TextChoices):
        NOT_REQUESTED = 'not_requested', _('Not requested')
        PENDING = 'pending', _('Pending')
        AVAILABLE = 'available', _('Available')
        FAILED = 'failed', _('Failed')

    charge = models.ForeignKey(
        Charge,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='erp_documents',
    )
    payment = models.ForeignKey(
        Payment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='erp_documents',
    )
    refund = models.OneToOneField(
        PaymentRefund,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='erp_document',
    )
    kind = models.CharField(max_length=20, choices=Kind)
    sale_payment_key = models.GeneratedField(
        expression=models.Case(
            models.When(kind=Kind.SALE, then=models.F('payment')),
            default=models.Value(None),
        ),
        output_field=models.BigIntegerField(null=True),
        db_persist=False,
        null=True,
    )
    sale_charge_key = models.GeneratedField(
        expression=models.Case(
            models.When(kind=Kind.SALE, then=models.F('charge')),
            default=models.Value(None),
        ),
        output_field=models.BigIntegerField(null=True),
        db_persist=False,
        null=True,
    )
    amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=decimal.Decimal('0.00'),
        editable=False,
        verbose_name=_('fiscal document amount'),
    )
    currency = models.CharField(
        max_length=3,
        default='EUR',
        editable=False,
    )
    status = models.CharField(
        max_length=30,
        choices=Status,
        default=Status.PENDING,
        db_index=True,
    )
    external_reference = models.CharField(max_length=100, unique=True)
    erp_document_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    erp_document_number = models.CharField(max_length=100, blank=True)
    creation_uncertain = models.BooleanField(default=False, editable=False)
    creation_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    integrated_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    pdf_status = models.CharField(
        max_length=20,
        choices=PDFStatus,
        default=PDFStatus.NOT_REQUESTED,
    )
    pdf_data = models.BinaryField(null=True, blank=True, editable=False)
    pdf_filename = models.CharField(max_length=255, blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    pdf_attempt_count = models.PositiveIntegerField(default=0)
    pdf_last_error = models.TextField(blank=True)
    pdf_downloaded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('ERP document')
        verbose_name_plural = _('ERP documents')
        constraints = [
            models.UniqueConstraint(
                fields=['sale_payment_key'],
                name='one_sale_erp_document_per_payment',
            ),
            models.UniqueConstraint(
                fields=['sale_charge_key'],
                name='one_sale_erp_document_per_charge',
            ),
            models.CheckConstraint(
                condition=(
                    Q(kind='sale', refund__isnull=True)
                    | Q(kind='credit_note', refund__isnull=False)
                ),
                name='erp_document_refund_matches_kind',
            ),
            models.CheckConstraint(
                condition=(
                    Q(charge__isnull=False)
                    | Q(payment__isnull=False)
                    | Q(refund__isnull=False)
                ),
                name='erp_document_has_financial_source',
            ),
        ]

    @property
    def purchase(self):
        if self.charge_id:
            return self.charge.purchase
        return self.payment.purchase if self.payment_id else None

    def __str__(self):
        return f'{self.external_reference} - {self.get_status_display()}'


class ERPIntegrationAttempt(models.Model):
    class Trigger(models.TextChoices):
        AUTOMATIC = 'automatic', _('Automatic')
        SUCCESS_PAGE = 'success_page', _('Payment success page')
        ADMIN = 'admin', _('Administrator')

    class Result(models.TextChoices):
        SUCCESS = 'success', _('Success')
        RECONCILED = 'reconciled', _('Reconciled existing document')
        FAILED = 'failed', _('Failed')

    document = models.ForeignKey(
        ERPDocument,
        on_delete=models.CASCADE,
        related_name='integration_attempts',
    )
    trigger = models.CharField(max_length=20, choices=Trigger)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    result = models.CharField(max_length=20, choices=Result)
    error_type = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField()

    class Meta:
        ordering = ['-started_at']
        verbose_name = _('ERP integration attempt')
        verbose_name_plural = _('ERP integration attempts')


class DocumentEmailAttempt(models.Model):
    class Status(models.TextChoices):
        SENT = 'sent', _('Sent')
        FAILED = 'failed', _('Failed')

    document = models.ForeignKey(
        ERPDocument,
        on_delete=models.CASCADE,
        related_name='email_attempts',
    )
    recipient = models.EmailField()
    status = models.CharField(max_length=10, choices=Status)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('document email attempt')
        verbose_name_plural = _('document email attempts')


class ProcessedStripeEvent(models.Model):
    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    payment = models.ForeignKey(
        Payment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='stripe_events',
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-processed_at']
        verbose_name = _('processed Stripe event')
        verbose_name_plural = _('processed Stripe events')

    def __str__(self):
        return f'{self.event_type}: {self.event_id}'
