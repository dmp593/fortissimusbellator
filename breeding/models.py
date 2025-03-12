import mimetypes
import pathlib
import cv2

from io import BytesIO
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.validators import MinValueValidator
from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _
from PIL import Image

from . import managers


GENDER_CHOICES = (
    ('M', 'Male'),
    ('F', 'Female'),
    ('?', 'Unknown')
)

User = get_user_model()


def animal_file_upload_to(instance: 'AnimalFile', filename: str) -> str:
    instance.filename = filename

    extension = mimetypes.guess_extension(filename)
    if not extension:
        extension = pathlib.Path(filename).suffix

    content_type, _ = mimetypes.guess_type(filename)
    if content_type:
        instance.content_type = content_type

    return f"animals/{instance.animal.breed.kind.name}/{instance.animal.breed.name}/{instance.animal.name}/{uuid4().hex}{extension}"


def animal_file_thumbnail_upload_to(instance: 'AnimalFile', filename: str) -> str:
    extension = mimetypes.guess_extension(filename)

    if not extension:
        extension = pathlib.Path(filename).suffix

    return f"animals/{instance.animal.breed.kind.name}/{instance.animal.breed.name}/{instance.animal.name}/thumbnails/{uuid4().hex}{extension}"


def breed_cover_upload_to(instance: 'Breed', filename: str) -> str:
    extension = mimetypes.guess_extension(filename)

    if not extension:
        extension = pathlib.Path(filename).suffix

    return f"breeds/{instance.kind.name}/{instance.name}/{uuid4().hex}{extension}"


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

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    objects = managers.BreedManager()

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
        related_name='father_children',
    )

    mother = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name=_('mother'),
        related_name='mother_children',
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
        related_name='dogs',
    )

    active = models.BooleanField(
        default=True,
        verbose_name=_('active')
    )

    has_training = models.BooleanField(
        default=False,
        verbose_name=_('has training'),
    )

    for_sale = models.BooleanField(
        default=False,
        verbose_name=_('for sale'),
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
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
        return self.files.filter(content_type__startswith='image/').order_by('order').first()

    class Meta:
        ordering = ['order',]

    def __str__(self):
        return f"{self.name}"


class AnimalFile(models.Model):
    animal = models.ForeignKey(
        to=Animal,
        on_delete=models.CASCADE,
        null=False,
        verbose_name=_('animal'),
        related_name='files',
        related_query_name='file'
    )

    file = models.FileField(
        upload_to=animal_file_upload_to,
        null=False,
        verbose_name=_('file'),
    )

    thumbnail = models.ImageField(
        upload_to=animal_file_thumbnail_upload_to,
        null=True,
        blank=True,
        verbose_name=_('thumbnail'),
    )

    description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('description'),
    )

    filename = models.CharField(
        max_length=255,
        editable=False,
        verbose_name=_('filename'),
    )

    content_type = models.CharField(
        max_length=50,
        editable=False,
        verbose_name=_('content type'),
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    class Meta:
        verbose_name = _('animal file')
        verbose_name_plural = _('animal files')
        ordering = ['order',]

    def save(self, *args, **kwargs):
        # Save the file first to get the path
        super().save(*args, **kwargs)

        # Generate thumbnail for videos if not provided
        if self.content_type and self.content_type.startswith('video') and not self.thumbnail:
            try:
                # Open the video file
                video = cv2.VideoCapture(self.file.path)
                success, frame = video.read()  # Read the first frame
                video.release()

                if success:
                    # Convert the frame from BGR (OpenCV) to RGB (PIL)
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame_rgb)

                    # Save the frame as a thumbnail
                    thumb_io = BytesIO()
                    img.save(thumb_io, format='JPEG')
                    thumb_file = ContentFile(thumb_io.getvalue(), name=f"{uuid4().hex}.jpg")

                    # Save the thumbnail
                    self.thumbnail.save(thumb_file.name, thumb_file, save=False)
                    super().save(*args, **kwargs)  # Save again to store the thumbnail
            except Exception as e:
                print(f"Error generating thumbnail: {e}")

    def __str__(self):
        return f"{self.filename}"
