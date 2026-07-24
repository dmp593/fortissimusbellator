"""HTTP endpoint for the session-only chat widget."""

import hashlib
import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from .assistant import (
    ModelBusy,
    ModelPreparing,
    ModelSnapshot,
    ModelState,
    ModelUnavailable,
)
from .business import CONTACT_PHONES
from .domain import ChatRequest, ConversationState, EntityKind
from .intents import QUICK_INTENTS
from .model_selection import (
    ModelSelectionError,
    available_models,
    save_selected_model,
)
from .models import ChatModel
from .runtime import get_chat_runtime


logger = logging.getLogger(__name__)

ALLOWED_CONTEXT_FIELDS = {
    "page_title", "page_name", "page_path", "page_type",
    "animal_id", "animal_name",
    # Kept temporarily for pages cached before the generic animal context.
    "dog_id", "dog_name", "litter_id", "litter_name",
    "breed_id", "breed_name",
    "certification_id", "certification_name",
}


@staff_member_required
@require_http_methods(["GET", "POST"])
def model_status(request):
    """Show and control model preparation without blocking an admin request."""
    if request.method == "POST":
        _handle_model_action(request)
        return redirect("chat_model_status")

    models = [model.to_spec() for model in available_models()]
    try:
        snapshot = get_chat_runtime().local_model.snapshot()
    except ModelSelectionError:
        snapshot = _unconfigured_snapshot()
    context = admin.site.each_context(request)
    context.update({
        "title": _("Local chat model status"),
        "snapshot": snapshot,
        "model_options": [
            _model_option(model, snapshot.model_id) for model in models
        ],
    })
    return render(
        request,
        "admin/chat/model_status.html",
        context,
    )


def _handle_model_action(request):
    local_model = get_chat_runtime().local_model
    action = request.POST.get("action")
    if action in {"prepare", "retry"}:
        try:
            local_model.prepare(retry=action == "retry")
        except ModelSelectionError:
            messages.error(request, _("Add and enable a chat model first."))
        return
    if action == "download_latest":
        try:
            local_model.download_latest()
        except (ModelBusy, ModelSelectionError):
            messages.error(
                request,
                _("Wait for the current model operation or add a model first."),
            )
        return
    if action != "activate":
        messages.error(request, _("Unknown model action."))
        return

    model_id = request.POST.get("model_id", "")
    try:
        model = available_models().get(pk=model_id).to_spec()
    except (ValueError, TypeError, ChatModel.DoesNotExist):
        messages.error(request, _("Select one of the approved chat models."))
        return

    try:
        local_model.activate(model)
    except ModelBusy:
        messages.error(
            request,
            _("Wait for the current model operation to finish before switching."),
        )
        return

    save_selected_model(model.model_id)
    messages.success(
        request,
        _("%(model)s is now the active chat model.") % {"model": model.name},
    )


def _model_option(model, active_model_id):
    path = model.path
    is_downloaded = path.is_file()
    file_size = path.stat().st_size if is_downloaded else model.download_size
    return {
        "model": model,
        "is_active": model.model_id == active_model_id,
        "is_downloaded": is_downloaded,
        "file_size": file_size,
        "file_size_known": bool(file_size),
    }


def _unconfigured_snapshot():
    return ModelSnapshot(
        state=ModelState.MISSING,
        model_path=str(Path(settings.CHAT_MODEL_DIR)),
        file_size=0,
        downloaded_bytes=0,
        total_bytes=0,
        error=str(_("No enabled local chat model is configured.")),
    )


@require_POST
def message(request):
    if _rate_limited(request):
        logger.warning("chat_request_rejected reason=rate_limit")
        return JsonResponse(
            {"error": _("Too many requests. Please wait a moment.")},
            status=429,
        )

    data = _json_body(request)
    if data is None:
        return JsonResponse({"error": _("Invalid request format.")}, status=400)

    user_message = data.get("message")
    if not isinstance(user_message, str) or not user_message.strip():
        return JsonResponse({"error": _("Message is required.")}, status=400)

    user_message = user_message.strip()
    if len(user_message) > settings.CHAT_MAX_INPUT_CHARS:
        return JsonResponse(
            {
                "error": _("Message too long (max %(limit)s characters).")
                % {"limit": settings.CHAT_MAX_INPUT_CHARS}
            },
            status=400,
        )

    history = _clean_history(data.get("history"))
    language = _language(data.get("language"), request.LANGUAGE_CODE)
    page_context = _clean_page_context(data.get("context"))
    intent = _clean_intent(data.get("intent"))
    state = _clean_state(data.get("state"))

    try:
        reply = get_chat_runtime().chat_service.reply(
            ChatRequest(
                message=user_message,
                history=history,
                language=language,
                page_context=page_context,
                requested_intent=intent,
                state=state,
            )
        )
    except ModelBusy:
        return JsonResponse(
            {"error": _("The assistant is busy. Please try again shortly.")},
            status=503,
        )
    except ModelPreparing:
        response = JsonResponse(
            {
                "error": _(
                    "The assistant is starting. "
                    "Please try again shortly."
                )
            },
            status=503,
        )
        response["Retry-After"] = "15"
        return response
    except ModelUnavailable as exc:
        logger.error("Local chat model is unavailable: %s", exc)
        return JsonResponse(
            {
                "error": _(
                    "The assistant is temporarily unavailable. "
                    "Please contact us at %(phones)s."
                ) % {"phones": CONTACT_PHONES}
            },
            status=503,
        )
    except Exception:
        logger.exception("Unexpected local chat failure")
        return JsonResponse(
            {"error": _("Something went wrong. Please try again.")},
            status=500,
        )

    updated_history = _limit_history(history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": reply.text},
    ])
    return JsonResponse({
        "response": reply.text,
        "history": updated_history,
        "state": reply.state.as_dict(),
    })


def _json_body(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _clean_history(value):
    """Accept only complete user/assistant turns from sessionStorage."""
    if not isinstance(value, list):
        return []

    messages = []
    expected_role = "user"
    for item in value[-settings.CHAT_MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict) or item.get("role") != expected_role:
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue

        content_limit = (
            settings.CHAT_MAX_INPUT_CHARS
            if expected_role == "user"
            else settings.CHAT_MAX_RESPONSE_CHARS
        )
        messages.append({
            "role": expected_role,
            "content": content.strip()[:content_limit],
        })
        expected_role = "assistant" if expected_role == "user" else "user"

    if messages and messages[-1]["role"] == "user":
        messages.pop()
    return _limit_history(messages)


def _limit_history(messages):
    limit = settings.CHAT_MAX_HISTORY_MESSAGES
    if limit % 2:
        limit -= 1
    messages = messages[-max(2, limit):]
    if messages and messages[0]["role"] == "assistant":
        messages = messages[1:]
    return messages


def _clean_page_context(value):
    if not isinstance(value, dict):
        return {}
    return {
        key: field.strip()[:100]
        for key, field in value.items()
        if key in ALLOWED_CONTEXT_FIELDS
        and isinstance(field, str)
        and field.strip()
    }


def _language(requested, fallback):
    supported = {code for code, _name in settings.LANGUAGES}
    if isinstance(requested, str):
        requested = requested.split("-", 1)[0].lower()
    return requested if requested in supported else fallback


def _clean_intent(value):
    return value if value in QUICK_INTENTS else None


def _clean_state(value):
    if not isinstance(value, dict):
        return ConversationState()

    try:
        raw_kind = value.get("entity_kind")
        if raw_kind == "dog":
            raw_kind = EntityKind.ANIMAL.value
        kind = EntityKind(raw_kind)
        entity_id = int(value.get("entity_id"))
    except (TypeError, ValueError):
        return ConversationState()
    if entity_id <= 0:
        return ConversationState()

    name = value.get("entity_name", "")
    if not isinstance(name, str):
        name = ""
    return ConversationState(
        entity_kind=kind,
        entity_id=entity_id,
        entity_name=name.strip()[:100],
    )


def _rate_limited(request):
    address = request.META.get("REMOTE_ADDR", "unknown")
    digest = hashlib.sha256(address.encode("utf-8")).hexdigest()[:16]
    key = f"chat-rate:{digest}"

    if cache.add(key, 1, timeout=60):
        return False
    try:
        return cache.incr(key) > settings.CHAT_REQUESTS_PER_MINUTE
    except ValueError:
        return False
