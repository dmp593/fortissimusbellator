import mimetypes
import pathlib
import logging

from io import BytesIO
from uuid import uuid4

import cv2
from PIL import Image

from django.db import models
from django.core.files.base import ContentFile
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _


logger = logging.getLogger(__name__)


def attachment_file_upload_to(instance: 'Attachment', filename: str) -> str:
    instance.filename = filename

    extension = mimetypes.guess_extension(filename)
    if not extension:
        extension = pathlib.Path(filename).suffix

    mime_type, _ = mimetypes.guess_type(filename)
    instance.mime_type = mime_type or 'application/octet-stream'

    return f"attachments/{uuid4().hex}{extension}"


def attachment_thumbnail_upload_to(instance: 'Attachment', filename: str) -> str:
    extension = mimetypes.guess_extension(filename)

    if not extension:
        extension = pathlib.Path(filename).suffix

    return f"attachments/thumbnails/{uuid4().hex}{extension}"


class Attachment(models.Model):
    file = models.FileField(
        upload_to=attachment_file_upload_to,
        null=False,
        verbose_name=_('file'),
    )

    thumbnail = models.ImageField(
        upload_to=attachment_thumbnail_upload_to,
        null=True,
        blank=True,
        verbose_name=_('thumbnail'),
    )

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=False,
        related_name='attachments',
        related_query_name='attachment'
    )

    object_id = models.PositiveIntegerField()

    content_object = GenericForeignKey(
        "content_type",
        "object_id"
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

    # Renamed from `content_type` to avoid confusion
    # with the ForeignKey field of the contenttypes framework.
    mime_type = models.CharField(
        max_length=50,
        editable=False,
        verbose_name=_('MIME type'),
    )

    order = models.IntegerField(
        default=999,
        verbose_name=_('order'),
    )

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "content_type",
                    "object_id"
                ]
            ),
            models.Index(
                fields=[
                    "mime_type"
                ]
            )
        ]

        ordering = [
            'order',
        ]

        verbose_name = _('attachment')
        verbose_name_plural = _('attachments')

    def save(self, *args, **kwargs):
        # Save the file first to get the path
        super().save(*args, **kwargs)

        # Generate thumbnail for videos if not provided
        if self.mime_type and self.mime_type.startswith('video') and not self.thumbnail:
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
                    img.save(thumb_io, format='WebP')
                    thumb_file = ContentFile(thumb_io.getvalue(), name=f"{uuid4().hex}.webp")

                    # Save the thumbnail
                    self.thumbnail.save(thumb_file.name, thumb_file, save=False)
                    super().save(*args, **kwargs)  # Save again to store the thumbnail

            except Exception as e:
                logger.error(f"Error generating thumbnail for {self.file.name}: {e}")

    def __str__(self):
        return f"{self.filename}"
