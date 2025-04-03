from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from django.utils.translation import gettext_lazy as _

from colorfield.fields import ColorField


class Tag(models.Model):
    tag = models.CharField(
        max_length=100,
        null=False,
        unique=True,
        verbose_name=_('tag')
    )

    color_light = ColorField(
        format="hexa",
        null=True,
        blank=True,
        verbose_name=_('light theme color')
    )

    color_dark = ColorField(
        format="hexa",
        null=True,
        blank=True,
        verbose_name=_('dark theme color')
    )

    content_type = models.ForeignKey(
        ContentType,
        null=False,
        on_delete=models.CASCADE,
        verbose_name=_('content_type')
    )

    object_id = models.PositiveIntegerField()

    content_object = GenericForeignKey(
        "content_type",
        "object_id"
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["tag"]
            ),
            models.Index(
                fields=["content_type", "object_id"]
            )
        ]

        unique_together = ("tag", "content_type", "object_id")

        verbose_name = _('tag')
        verbose_name_plural = _('tags')

    def __str__(self):
        return f"{self.tag}"
