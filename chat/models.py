"""Database configuration for locally downloadable chat models."""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .model_catalog import ChatModelSpec, validate_model_spec


class ChatModel(models.Model):
    """One GGUF file that administrators may download and activate."""

    model_id = models.SlugField(max_length=64, primary_key=True)
    name = models.CharField(max_length=120)
    repository = models.CharField(
        max_length=180,
        help_text=_("Hugging Face repository in owner/name format."),
    )
    filename = models.CharField(max_length=255, unique=True)
    revision = models.CharField(
        max_length=100,
        default="main",
        help_text=_(
            "Branch, tag, or commit. Use main to allow upstream updates."
        ),
    )
    sha256 = models.CharField(
        max_length=64,
        blank=True,
        help_text=_(
            "Optional. When present, downloaded bytes must match exactly."
        ),
    )
    download_size = models.PositiveBigIntegerField(
        default=0,
        help_text=_("Optional estimated size in bytes; zero means unknown."),
    )
    summary = models.CharField(max_length=255, blank=True)
    recommended = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("-recommended", "name")
        verbose_name = _("local chat model")
        verbose_name_plural = _("local chat models")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        validate_model_spec(self.to_spec())

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def to_spec(self):
        return ChatModelSpec(
            model_id=self.model_id,
            name=self.name,
            repository=self.repository,
            filename=self.filename,
            revision=self.revision,
            sha256=self.sha256.lower(),
            download_size=self.download_size,
            summary=self.summary,
            recommended=self.recommended,
        )


class ChatModelConfiguration(models.Model):
    """Singleton containing the model selected for the website."""

    active_model = models.ForeignKey(
        ChatModel,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = _("chat model configuration")
        verbose_name_plural = _("chat model configuration")

    def __str__(self):
        return str(self.active_model or _("No active model"))


class ChatSearchEntry(models.Model):
    """Search projection for one chat-visible Django object."""

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="chat_search_entries",
    )
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    label = models.CharField(max_length=255)
    canonical_terms = models.JSONField(default=list)
    aliases = models.TextField(
        blank=True,
        verbose_name=_("chat search aliases"),
        help_text=_(
            "Optional alternative names or questions, one per line. "
            "Used only by chat search."
        ),
    )
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("label",)
        constraints = (
            models.UniqueConstraint(
                fields=("content_type", "object_id"),
                name="unique_chat_search_object",
            ),
        )
        verbose_name = _("chat search entry")
        verbose_name_plural = _("chat search entries")

    def __str__(self):
        return self.label
