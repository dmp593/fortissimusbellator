"""Django admin configuration for downloadable local chat models."""

from django.contrib import admin
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext_lazy as _

from .models import ChatModel


@admin.register(ChatModel)
class ChatModelAdmin(admin.ModelAdmin):
    """Manage the bounded model catalogue; activation lives on the status page."""

    list_display = (
        "name",
        "repository",
        "revision",
        "estimated_size",
        "recommended",
        "enabled",
    )
    list_filter = ("enabled", "recommended")
    search_fields = ("model_id", "name", "repository", "filename")
    ordering = ("-recommended", "name")
    fieldsets = (
        (
            None,
            {"fields": ("model_id", "name", "summary")},
        ),
        (
            _("Download source"),
            {
                "fields": (
                    "repository",
                    "filename",
                    "revision",
                    "sha256",
                    "download_size",
                ),
            },
        ),
        (
            _("Availability"),
            {"fields": ("enabled", "recommended")},
        ),
    )

    @admin.display(description=_("Estimated size"), ordering="download_size")
    def estimated_size(self, model):
        if not model.download_size:
            return _("Unknown")
        return filesizeformat(model.download_size)
