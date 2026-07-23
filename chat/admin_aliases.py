"""Reusable Django admin integration for AI-assisted chat aliases."""

import logging

from django import forms
from django.contrib.admin.utils import quote, unquote
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.urls import path, reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from .admin_widgets import ChatAliasTextareaWidget
from .alias_suggestions import (
    AliasSuggestionError,
    alias_suggestion_service,
)
from .assistant import ModelBusy, ModelPreparing, ModelUnavailable
from .search_index import aliases_for, save_aliases


logger = logging.getLogger(__name__)


class ChatAliasSuggestionsAdminMixin:
    """Add admin-reviewed local-model alias suggestions to a ModelAdmin."""

    alias_field_name = "chat_search_aliases"

    @property
    def alias_suggestion_url_name(self):
        opts = self.model._meta
        return f"{opts.app_label}_{opts.model_name}_chat_alias_suggestions"

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/chat-alias-suggestions/",
                self.admin_site.admin_view(self.alias_suggestions_view),
                name=self.alias_suggestion_url_name,
            ),
        ]
        return custom_urls + super().get_urls()

    def get_form(self, request, obj=None, **kwargs):
        requested_fields = kwargs.get("fields")
        if requested_fields is not None:
            kwargs["fields"] = tuple(
                field
                for field in requested_fields
                if field != self.alias_field_name
            )

        form = super().get_form(request, obj, **kwargs)
        suggestion_url = ""
        if obj is not None and self.has_change_permission(request, obj):
            suggestion_url = reverse(
                f"{self.admin_site.name}:{self.alias_suggestion_url_name}",
                args=(quote(obj.pk),),
            )
        form.base_fields[self.alias_field_name] = forms.CharField(
            required=False,
            label=_("chat search aliases"),
            help_text=_(
                "Optional alternative names or questions, one per line. "
                "Used only by chat search."
            ),
            initial=aliases_for(obj) if obj is not None else "",
            widget=ChatAliasTextareaWidget(
                suggestion_url=suggestion_url,
            ),
        )
        return form

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if self.alias_field_name in form.cleaned_data:
            save_aliases(
                obj,
                form.cleaned_data[self.alias_field_name],
            )

    def alias_suggestions_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        instance = self.get_object(request, unquote(object_id))
        if instance is None:
            raise Http404
        if not self.has_change_permission(request, instance):
            raise PermissionDenied

        try:
            suggestions = self.get_alias_suggestion_service().suggest(instance)
        except (ModelBusy, ModelPreparing, ModelUnavailable):
            logger.info(
                "chat_alias_admin outcome=model_unavailable model=%s object_id=%s",
                self.model._meta.label,
                instance.pk,
            )
            return JsonResponse(
                {
                    "error": _(
                        "The local chat model is not ready. Check its status "
                        "and try again."
                    )
                },
                status=503,
            )
        except AliasSuggestionError:
            logger.warning(
                "chat_alias_admin outcome=invalid_response model=%s object_id=%s",
                self.model._meta.label,
                instance.pk,
            )
            return JsonResponse(
                {
                    "error": _(
                        "The local model did not return usable aliases. "
                        "Please try again."
                    )
                },
                status=502,
            )
        except Exception:
            logger.exception(
                "chat_alias_admin outcome=error model=%s object_id=%s",
                self.model._meta.label,
                instance.pk,
            )
            return JsonResponse(
                {"error": _("Could not generate alias suggestions.")},
                status=500,
            )

        message = ngettext(
            "%(count)d new alias was added to the form. Review and save it.",
            "%(count)d new aliases were added to the form. Review and save them.",
            len(suggestions),
        ) % {"count": len(suggestions)}
        if not suggestions:
            message = _(
                "The model found no useful new aliases. Existing aliases "
                "may already cover its suggestions."
            )

        return JsonResponse({
            "suggestions": suggestions,
            "message": message,
        })

    @staticmethod
    def get_alias_suggestion_service():
        return alias_suggestion_service
