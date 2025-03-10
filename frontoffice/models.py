import pathlib
import mimetypes

from uuid import uuid4

from django.db import models
from django.utils.translation import gettext_lazy as _


def faq_image_upload_to(instance: 'FrequentlyAskedQuestion', filename: str) -> str:
    extension = mimetypes.guess_extension(filename)

    if not extension:
        extension = pathlib.Path(filename).suffix

    return f"faqs/{instance.pk}/{uuid4().hex}{extension}"


class FrequentlyAskedQuestion(models.Model):
    question = models.CharField(
        max_length=255,
        null=False,
        blank=False,
        verbose_name=_('question')
    )

    answer = models.TextField(
        null=False,
        blank=False,
        verbose_name=_('answer')
    )

    image = models.ImageField(
        upload_to=faq_image_upload_to,
        null=True,
        verbose_name=_('image'),
    )

    active = models.BooleanField(
        default=True,
        verbose_name=_('active')
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    def __str__(self):
        return f"{self.question}"
