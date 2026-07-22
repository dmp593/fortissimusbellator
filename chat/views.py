"""HTTP endpoint for the session-only chat widget."""

import hashlib
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .assistant import (
    ModelBusy,
    ModelPreparing,
    ModelUnavailable,
)
from .domain import ChatRequest, ConversationState, EntityKind
from .intents import QUICK_INTENTS
from .service import chat_service


logger = logging.getLogger(__name__)

ALLOWED_CONTEXT_FIELDS = {
    "page_title", "page_name", "page_path", "page_type",
    "dog_id", "dog_name", "litter_id", "litter_name",
    "breed_id", "breed_name",
}


@require_POST
def message(request):
    if _rate_limited(request):
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
        reply = chat_service.reply(
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
                    "The assistant is being prepared for first use. "
                    "Please try again in a few minutes."
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
                    "Please contact us at +351 924 454 382."
                )
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
        kind = EntityKind(value.get("entity_kind"))
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
