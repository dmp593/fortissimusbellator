import mimetypes
import pathlib

from uuid import uuid4

from django.db import models
from django.core.validators import MinValueValidator
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericRelation
from django.utils.translation import gettext_lazy as _

from attachments.models import Attachment
from tags.models import Tag
from . import managers


GENDER_CHOICES = (
    ('M', 'Male'),
    ('F', 'Female'),
    ('?', 'Unknown')
)

User = get_user_model()


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

    active = models.BooleanField(
        default=True,
        verbose_name=_('active'),
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    objects = managers.BreedManager()
    specific = managers.SpecificBreedManager()

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

    sold_at = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('sold at')
    )

    sold_to = models.ForeignKey(
        to=User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name=_('sold to'),
        related_name='animals',
        related_query_name='animal'
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
    def cover(self):
        return self.files.filter(mime_type__startswith='image/').order_by('order').first()

    objects = models.Manager()
    
    animals_active = managers.Manager(
        active=True
    )

    animals_for_breeding = managers.Manager(
        active=True, for_breeding=True
    )

    animals_for_sale = managers.AnimalsForSaleManager()

    class Meta:
        verbose_name = _('animal')
        verbose_name_plural = _('animals')
        ordering = ['order',]

    def __str__(self):
        return f"{self.name}"


class Litter(models.Model):
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

    expected_delivery_date = models.DateField(
        verbose_name=_('expected delivery date'),
        null=True,
        blank=True
    )

    expected_babies = models.PositiveIntegerField(
        verbose_name=_('expected number of babies'),
        null=True,
        blank=True,
    )

    active = models.BooleanField(
        default=True,
        verbose_name=_('active')
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
        return self.files.filter(mime_type__startswith='image/').order_by('order').first()

    objects = models.Manager()
    litters_for_sale = managers.Manager(active=True)

    class Meta:
        verbose_name = _('litter')
        verbose_name_plural = _('litters')
        ordering = ['order',]

    def __str__(self):
        return f"{self.name}"