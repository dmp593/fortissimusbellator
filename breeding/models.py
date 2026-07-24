import decimal
import mimetypes
import pathlib

from uuid import uuid4

from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericRelation
from django.utils.translation import gettext_lazy as _

from attachments.models import Attachment
from tags.models import Tag
from . import managers


GENDER_CHOICES = (
    ('M', _('Male')),
    ('F', _('Female')),
    ('?', _('Unknown'))
)

HAIR_TYPE_CHOICES = (
    ('short', _('Short')),
    ('medium', _('Medium')),
    ('long', _('Long')),
    ('?', _('Unknown')),
)


User = get_user_model()


def _cover_attachment(instance):
    prefetched_files = getattr(
        instance,
        '_prefetched_objects_cache',
        {},
    ).get('files')
    if prefetched_files is not None:
        return next(
            (
                attachment
                for attachment in prefetched_files
                if attachment.mime_type.startswith('image/')
            ),
            None,
        )
    return (
        instance.files.filter(mime_type__startswith='image/')
        .order_by('order')
        .first()
    )


def breed_cover_upload_to(instance: 'Breed', filename: str) -> str:
    extension = mimetypes.guess_extension(filename)

    if not extension:
        extension = pathlib.Path(filename).suffix

    return f"breeds/{uuid4().hex}{extension}"


class AnimalKind(models.Model):
    name = models.CharField(
        max_length=50,
        unique=True,
        verbose_name=_('name')
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    objects = managers.AnimalKindManager()

    class Meta:
        verbose_name = _('animal kind')
        verbose_name_plural = _('animals kinds')
        ordering = ['order']

    def __str__(self):
        return f"{self.name}"


class Breed(models.Model):
    kind = models.ForeignKey(
        AnimalKind,
        on_delete=models.CASCADE,
        related_name='breeds',
        related_query_name='breed',
        verbose_name=_('kind'),
    )

    name = models.CharField(
        max_length=50,
        verbose_name=_('name'),
    )

    cover = models.ImageField(
        upload_to=breed_cover_upload_to,
        null=False,
        blank=False,
        verbose_name=_('cover'),
    )

    description = models.TextField(
        blank=True,
        verbose_name=_('description'),
    )

    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_('parent'),
        related_name='children',
        related_query_name='child',
    )

    featured = models.BooleanField(
        default=True,
        verbose_name=_('featured'),
        help_text=_('Only featured breeds appear outside filters, such as on the homepage and navigation')
    )

    active = models.BooleanField(
        default=True,
        verbose_name=_('active'),
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    objects = managers.BreedManager()
    objects_specific = managers.SpecificBreedManager(active=True)
    objects_specific_featured = managers.SpecificBreedManager(featured=True, active=True)

    class Meta:
        unique_together = ['kind', 'name']
        verbose_name = _('breed')
        verbose_name_plural = _('breeds')
        ordering = ['order']

    def __str__(self):
        if self.parent and self.parent.name not in self.name:
            return f"{self.parent.name} ({self.name})"

        return f"{self.name}"


class Certification(models.Model):
    code = models.CharField(
        max_length=10,
        unique=True,
        verbose_name=_('code'),
    )

    name = models.CharField(
        max_length=150,
        verbose_name=_('name'),
    )

    description = models.TextField(
        blank=True,
        verbose_name=_('description'),
    )

    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_('parent'),
        related_name='children',
        related_query_name='child',
    )

    breeds = models.ManyToManyField(
        Breed,
        related_name='certifications',
        related_query_name='certification',
        verbose_name=_('breeds'),
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    objects = managers.CertificationManager()

    class Meta:
        verbose_name = _('certification')
        verbose_name_plural = _('certifications')
        ordering = ['order']

    def __str__(self):
        return f"{self.code}"


class AnimalCertification(models.Model):
    animal = models.ForeignKey(
        'Animal',
        on_delete=models.CASCADE,
        verbose_name=_('animal'),
        related_name='animal_certifications',
        related_query_name='animal_certification',
    )

    certification = models.ForeignKey(
        Certification,
        on_delete=models.CASCADE,
        verbose_name=_('certification'),
        related_name='animal_certifications',
        related_query_name='animal_certification',
    )

    date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('date'),
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    class Meta:
        verbose_name = _('animal certification')
        verbose_name_plural = _('animal certifications')
        ordering = ['order']

    def __str__(self):
        return f"{self.animal.name} - {self.certification.code}"


class Animal(models.Model):
    breed = models.ForeignKey(
        Breed,
        on_delete=models.PROTECT,
        verbose_name=_('breed'),
        related_name='animals',
        related_query_name='animal',
    )

    name = models.CharField(
        max_length=150,
        verbose_name=_('name'),
    )

    description = models.TextField(
        blank=True,
        verbose_name=_('description'),
    )

    birth_date = models.DateField(
        verbose_name=_('birth date'),
    )

    gender = models.CharField(
        choices=GENDER_CHOICES,
        max_length=1,
        default='?',
        verbose_name=_('gender')
    )

    hair_type = models.CharField(
        max_length=30,
        blank=True,
        choices=HAIR_TYPE_CHOICES,
        verbose_name=_('hair type'),
    )

    father = models.ForeignKey(
        'self', on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_('father'),
        related_name='children_father',
        related_query_name='child_father',
    )

    mother = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_('mother'),
        related_name='children_mother',
        related_query_name='child_mother',
    )

    litter = models.ForeignKey(
        "breeding.Litter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_('litter'),
        related_name='animals',
        related_query_name='animal',
    )

    certifications = models.ManyToManyField(
        Certification,
        through=AnimalCertification,
        related_name='animals',
        related_query_name='animal',
        verbose_name=_('certifications'),
    )

    price_in_euros = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(500)
        ],
        verbose_name=_('price in euros'),
    )

    discount_in_euros = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(0)
        ],
        verbose_name=_('discount in euros'),
    )

    active = models.BooleanField(
        default=True,
        verbose_name=_('active')
    )

    has_training = models.BooleanField(
        default=False,
        verbose_name=_('has training'),
    )

    for_breeding = models.BooleanField(
        default=False,
        verbose_name=_('for breeding'),
    )

    for_sale = models.BooleanField(
        default=False,
        verbose_name=_('for sale'),
    )

    pre_reservation_enabled = models.BooleanField(
        default=True,
        verbose_name=_('available for pre-reservation'),
    )

    pre_reservation_fee = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=decimal.Decimal('50.00'),
        validators=[MinValueValidator(decimal.Decimal('0.50'))],
        verbose_name=_('pre-reservation fee'),
        help_text=_('Non-refundable pre-reservation fee in euros.'),
    )

    reservation_deposit_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=decimal.Decimal('50.00'),
        validators=[
            MinValueValidator(decimal.Decimal('0.01')),
            MaxValueValidator(decimal.Decimal('100.00')),
        ],
        verbose_name=_('reservation deposit percentage'),
        help_text=_(
            'Percentage of the dog price that must have been paid when the '
            'reservation is confirmed.'
        ),
    )

    reservation_offer_hours = models.PositiveSmallIntegerField(
        default=72,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(7 * 24),
        ],
        verbose_name=_('reservation offer validity in hours'),
        help_text=_(
            'Hours available to pay the reservation deposit after the breeder '
            'accepts the pre-reservation. Minimum 1 hour, maximum 7 days.'
        ),
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    files = GenericRelation(
        Attachment,
        verbose_name=_('files'),
    )

    tags = GenericRelation(
        Tag,
        verbose_name=_('tags')
    )

    @property
    def current_price_in_euros(self):
        if not self.for_sale:
            return None

        if not self.discount_in_euros:
            return self.price_in_euros

        return self.price_in_euros - self.discount_in_euros

    @property
    def is_sold(self):
        annotated_value = getattr(self, 'has_completed_sale', None)
        if annotated_value is not None:
            return annotated_value
        if not self.pk:
            return False
        return self.sale_cases.filter(
            sale__isnull=False,
            sale__voided_at__isnull=True,
        ).exists()

    @property
    def cover(self):
        return _cover_attachment(self)

    objects = managers.GetByNameManager()

    animals_active = managers.GetByNameManager(
        active=True
    )

    animals_for_breeding = managers.GetByNameManager(
        active=True, for_breeding=True
    )

    animals_for_sale = managers.AnimalsForSaleManager()

    class Meta:
        verbose_name = _('animal')
        verbose_name_plural = _('animals')
        ordering = ['order',]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(discount_in_euros__isnull=True)
                    | models.Q(
                        price_in_euros__isnull=False,
                        discount_in_euros__lte=models.F('price_in_euros'),
                    )
                ),
                name='animal_discount_lte_price',
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.discount_in_euros is not None
            and self.price_in_euros is None
        ):
            raise ValidationError(
                {
                    'discount_in_euros': _(
                        'A discount requires a published price.'
                    )
                }
            )
        if (
            self.discount_in_euros is not None
            and self.price_in_euros is not None
            and self.discount_in_euros > self.price_in_euros
        ):
            raise ValidationError(
                {
                    'discount_in_euros': _(
                        'The discount cannot exceed the published price.'
                    )
                }
            )

    def __str__(self):
        return f"{self.name}"


class Litter(models.Model):
    class LitterStatus(models.TextChoices):
        EXPECTING = 'expecting', _('Expecting')
        BORN = 'born', _('Born')
        READY = 'ready', _('Ready for new homes')
        COMPLETED = 'completed', _('All babies placed')

    breed = models.ForeignKey(
        Breed,
        on_delete=models.PROTECT,
        verbose_name=_('breed'),
        related_name='litters',
        related_query_name='litter',
    )

    name = models.CharField(
        max_length=150,
        verbose_name=_('name'),
    )

    description = models.TextField(
        blank=True,
        verbose_name=_('description'),
    )

    father = models.ForeignKey(
        Animal, on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_('father'),
        related_name='litter_father',
        related_query_name='litter_father',
    )

    mother = models.ForeignKey(
        Animal, on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_('mother'),
        related_name='litter_mother',
        related_query_name='litter_mother',
    )

    expected_birth_date = models.DateField(
        verbose_name=_('expected birth date'),
        null=True,
        blank=True
    )

    expected_ready_date = models.DateField(
        verbose_name=_('expected ready date'),
        null=True,
        blank=True,
        help_text=_('Expected date when babies will be ready for new homes')
    )

    expected_babies = models.PositiveIntegerField(
        verbose_name=_('expected babies'),
        null=True,
        blank=True,
        help_text=_('Expected number of babies in this litter')
    )

    birth_date = models.DateField(
        verbose_name=_('birth date'),
        null=True,
        blank=True,
        help_text=_('Actual date when the litter was born')
    )

    ready_date = models.DateField(
        verbose_name=_('ready date'),
        null=True,
        blank=True,
        help_text=_('Actual date when babies were ready for new homes')
    )

    babies = models.PositiveIntegerField(
        verbose_name=_('babies'),
        null=True,
        blank=True,
        help_text=_('Actual number of babies born')
    )

    status = models.CharField(
        max_length=20,
        choices=LitterStatus,
        default=LitterStatus.EXPECTING,
        verbose_name=_('status'),
        help_text=_('Current stage of the litter lifecycle')
    )

    active = models.BooleanField(
        default=True,
        verbose_name=_('active')
    )

    offspring_pre_reservation_enabled = models.BooleanField(
        default=True,
        verbose_name=_('generated dogs available for pre-reservation'),
    )

    offspring_pre_reservation_fee = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=decimal.Decimal('50.00'),
        validators=[MinValueValidator(decimal.Decimal('0.50'))],
        verbose_name=_('generated dogs pre-reservation fee'),
        help_text=_(
            'Non-refundable pre-reservation fee copied to generated dogs.'
        ),
    )

    offspring_reservation_deposit_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=decimal.Decimal('50.00'),
        validators=[
            MinValueValidator(decimal.Decimal('0.01')),
            MaxValueValidator(decimal.Decimal('100.00')),
        ],
        verbose_name=_('generated dogs reservation deposit percentage'),
        help_text=_(
            'Reservation deposit percentage copied to generated dogs.'
        ),
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    files = GenericRelation(
        Attachment,
        verbose_name=_('files'),
    )

    tags = GenericRelation(
        Tag,
        verbose_name=_('tags')
    )

    @property
    def cover(self):
        return _cover_attachment(self)

    objects = managers.GetByNameManager()
    litters_for_sale = managers.GetByNameManager(active=True)

    class Meta:
        verbose_name = _('litter')
        verbose_name_plural = _('litters')
        ordering = ['order',]

    def __str__(self):
        return f"{self.name}"


class LitterAlertPreference(models.Model):
    class Scope(models.TextChoices):
        NONE = 'none', _('No general alerts')
        ALL = 'all', _('All breeds')
        SELECTED_BREEDS = 'selected_breeds', _('Selected breeds')

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='litter_alert_preference',
        verbose_name=_('user'),
    )
    scope = models.CharField(
        max_length=20,
        choices=Scope,
        default=Scope.NONE,
        verbose_name=_('general litter alerts'),
    )
    breeds = models.ManyToManyField(
        Breed,
        blank=True,
        related_name='alert_preferences',
        verbose_name=_('breeds'),
    )
    language_code = models.CharField(max_length=10, default='en')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('litter alert preference')
        verbose_name_plural = _('litter alert preferences')

    def __str__(self):
        return f'{self.user} - {self.get_scope_display()}'


class LitterAlertOverride(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='litter_alert_overrides',
        verbose_name=_('user'),
    )
    litter = models.ForeignKey(
        Litter,
        on_delete=models.CASCADE,
        related_name='alert_overrides',
        verbose_name=_('litter'),
    )
    enabled = models.BooleanField(
        default=True,
        verbose_name=_('subscribed'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('litter alert override')
        verbose_name_plural = _('litter alert overrides')
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'litter'],
                name='one_litter_alert_override_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.user} - {self.litter} - {self.enabled}'


class LitterBirthAnnouncement(models.Model):
    litter = models.OneToOneField(
        Litter,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='birth_announcement',
        verbose_name=_('litter'),
    )
    litter_name = models.CharField(max_length=150)
    breed_name = models.CharField(max_length=150)
    babies = models.PositiveIntegerField()
    birth_date = models.DateField()
    announced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-announced_at']
        verbose_name = _('litter birth announcement')
        verbose_name_plural = _('litter birth announcements')

    def __str__(self):
        return f'{self.litter_name} - {self.babies}'


class LitterBirthNotification(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending')
        PROCESSING = 'processing', _('Processing')
        SENT = 'sent', _('Sent')
        FAILED = 'failed', _('Failed')
        CANCELLED = 'cancelled', _('Cancelled')

    announcement = models.ForeignKey(
        LitterBirthAnnouncement,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='litter_birth_notifications',
    )
    recipient = models.EmailField()
    language_code = models.CharField(max_length=10, default='en')
    status = models.CharField(
        max_length=20,
        choices=Status,
        default=Status.PENDING,
        db_index=True,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    processing_started_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    next_retry_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = _('litter birth notification')
        verbose_name_plural = _('litter birth notifications')
        constraints = [
            models.UniqueConstraint(
                fields=['announcement', 'user'],
                name='one_birth_notification_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.announcement} - {self.recipient}'
