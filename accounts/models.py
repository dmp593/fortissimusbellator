import mimetypes
import pathlib

from uuid import uuid4
from datetime import date

from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from django_countries.fields import CountryField


def profile_picture_upload_to(instance, filename):
    extension = mimetypes.guess_extension(filename)

    if not extension:
        extension = pathlib.Path(filename).suffix

    return f"posts/{uuid4().hex}{extension}"


class Profile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
        related_query_name='profile',
        verbose_name=_('user')
    )

    birthdate = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('birthdate')
    )

    fiscal_number = models.CharField(
        null=True,
        blank=True,
        max_length=20,
        verbose_name=_('fiscal number')
    )

    phone = models.CharField(
        max_length=15,
        verbose_name=_('phone number')
    )

    profile_picture = models.ImageField(
        upload_to=profile_picture_upload_to,
        null=True,
        blank=True,
        verbose_name=_('profile picture')
    )

    @property
    def age(self):
        today = date.today()

        if not self.birthdate:
            return None

        return today.year - self.birthdate.year - (
            (today.month, today.day) < (self.birthdate.month, self.birthdate.day)
        )

    def __str__(self):
        return f"{self.user.username}"


class Address(models.Model):
    street_line1 = models.CharField(
        max_length=255,
        verbose_name=_('Street Address 1')
    )

    street_line2 = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_('Street Address 2')
    )

    city = models.CharField(
        max_length=100,
        verbose_name=_('City')
    )

    state_province = models.CharField(
        max_length=100,
        verbose_name=_('State/Province')
    )

    postal_code = models.CharField(
        max_length=20,
        verbose_name=_('Postal Code')
    )

    country = CountryField()

    kind = models.CharField(
        max_length=10,
        choices=[
            ('billing', _('Billing')),
            ('shipping', _('Shipping'))
        ],
        default='billing',
        verbose_name=_('Address Type')
    )

    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='addresses',
        related_query_name='address',
        verbose_name=_('Profile')
    )

    is_default = models.BooleanField(
        default=False,
        verbose_name=_('Is Default')
    )

    class Meta:
        verbose_name = _('Address')
        verbose_name_plural = _('Addresses')

    def __str__(self):
        return f"{self.street_line1}, {self.postal_code} {self.city}"
