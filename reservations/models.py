import decimal
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


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


class PreReservationQuerySet(models.QuerySet):
    def capacity_consuming(self):
        return self.filter(
            status__in=(
                PreReservation.Status.PENDING_PAYMENT,
                PreReservation.Status.CONFIRMED,
                PreReservation.Status.FULFILLED,
            )
        )

    def paid(self):
        return self.filter(
            payment__status__in=(
                Payment.Status.PAID,
                Payment.Status.REFUND_PENDING,
                Payment.Status.REFUND_FAILED,
                Payment.Status.REFUNDED,
            )
        )


class PreReservation(models.Model):
    class TargetType(models.TextChoices):
        DOG = 'dog', _('Dog')
        LITTER = 'litter', _('Litter')

    class Status(models.TextChoices):
        PENDING_PAYMENT = 'pending_payment', _('Awaiting payment')
        CONFIRMED = 'confirmed', _('Confirmed')
        PAYMENT_FAILED = 'payment_failed', _('Payment failed')
        EXPIRED = 'expired', _('Expired')
        CANCELLED_BY_USER = 'cancelled_by_user', _('Cancelled by customer')
        CANCELLED_BY_ADMIN = 'cancelled_by_admin', _('Cancelled by staff')
        FULFILLED = 'fulfilled', _('Fulfilled')

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
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

    target_name = models.CharField(max_length=150, verbose_name=_('target name'))
    target_breed = models.CharField(max_length=150, verbose_name=_('breed'))
    target_birth_date = models.DateField(null=True, blank=True)
    target_deleted_at = models.DateTimeField(null=True, blank=True)

    customer_name = models.CharField(max_length=150)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=30)
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

    hold_expires_at = models.DateTimeField(null=True, blank=True)
    terms = models.ForeignKey(
        PreReservationTerms,
        on_delete=models.PROTECT,
        related_name='reservations',
        verbose_name=_('accepted terms'),
    )
    non_refundable_accepted_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
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
            models.UniqueConstraint(
                fields=['animal'],
                condition=Q(
                    target_type='dog',
                    status__in=(
                        'pending_payment',
                        'confirmed',
                        'fulfilled',
                    ),
                ),
                name='one_active_pre_reservation_per_dog',
            ),
            models.UniqueConstraint(
                fields=['litter', 'user'],
                condition=Q(
                    target_type='litter',
                    status__in=(
                        'pending_payment',
                        'confirmed',
                        'fulfilled',
                    ),
                ),
                name='one_active_litter_place_per_user',
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

    @property
    def target(self):
        return self.animal or self.litter

    @property
    def target_is_public(self):
        if self.animal:
            return (
                self.animal.active
                and self.animal.for_sale
                and self.animal.sold_at is None
            )
        if self.litter:
            return self.litter.active
        return False

    @property
    def is_capacity_consuming(self):
        return self.status in {
            self.Status.PENDING_PAYMENT,
            self.Status.CONFIRMED,
            self.Status.FULFILLED,
        }

    @property
    def can_user_cancel(self):
        return self.status in {
            self.Status.PENDING_PAYMENT,
            self.Status.CONFIRMED,
        }

    def __str__(self):
        return f'{self.public_id} - {self.target_name}'


class Payment(models.Model):
    class Provider(models.TextChoices):
        STRIPE = 'stripe', 'Stripe'
        COMPLIMENTARY = 'complimentary', _('Complimentary')

    class Status(models.TextChoices):
        INITIALIZING = 'initializing', _('Initializing')
        PENDING = 'pending', _('Pending')
        PAID = 'paid', _('Paid')
        FAILED = 'failed', _('Failed')
        REFUND_PENDING = 'refund_pending', _('Refund pending')
        REFUND_FAILED = 'refund_failed', _('Refund failed')
        REFUNDED = 'refunded', _('Refunded')

    reservation = models.OneToOneField(
        PreReservation,
        on_delete=models.CASCADE,
        related_name='payment',
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
    stripe_payment_intent_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    stripe_checkout_url = models.TextField(blank=True)
    stripe_checkout_expires_at = models.DateTimeField(null=True, blank=True)
    stripe_refund_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    refund_attempt_count = models.PositiveIntegerField(default=0)
    refund_next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name='reservation_payment_amount_gte_zero',
            ),
        ]

    def __str__(self):
        return f'{self.reservation.public_id} - {self.get_status_display()}'


class ERPDocument(models.Model):
    class Kind(models.TextChoices):
        SALE = 'sale', _('Sale document')
        CREDIT_NOTE = 'credit_note', _('Credit note')

    class Status(models.TextChoices):
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

    reservation = models.ForeignKey(
        PreReservation,
        on_delete=models.PROTECT,
        related_name='erp_documents',
    )
    kind = models.CharField(max_length=20, choices=Kind)
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
                fields=['reservation', 'kind'],
                name='one_erp_document_kind_per_reservation',
            ),
        ]

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


class ProcessedStripeEvent(models.Model):
    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    reservation = models.ForeignKey(
        PreReservation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='stripe_events',
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-processed_at']

    def __str__(self):
        return f'{self.event_type}: {self.event_id}'
