"""Process-scoped composition root for the local chat runtime."""

import logging

from django.apps import apps

from .alias_suggestions import AliasSuggestionService, LocalAliasGenerator
from .assistant import ChatAssistant, LocalModel
from .model_selection import ModelSelectionError
from .service import ChatService


logger = logging.getLogger(__name__)


class ChatRuntime:
    """Own the model and services for exactly one Django web process."""

    def __init__(self, model_provider):
        self.local_model = LocalModel(model_provider)
        model_assistant = ChatAssistant(self.local_model)
        self.chat_service = ChatService(model_assistant=model_assistant)
        self.alias_suggestion_service = AliasSuggestionService(
            LocalAliasGenerator(self.local_model)
        )

    def warm_up(self):
        """Start model preparation without delaying process startup."""
        try:
            snapshot = self.local_model.prepare()
        except ModelSelectionError as exc:
            logger.warning("chat_model_warmup_skipped reason=%s", exc)
            return None
        except Exception:
            logger.exception("chat_model_warmup_failed")
            return None

        logger.info(
            "chat_model_warmup state=%s model_id=%s",
            snapshot.state.value,
            snapshot.model_id or "none",
        )
        return snapshot

    def close(self):
        self.local_model.close()


def get_chat_runtime():
    """Resolve the runtime owned by Django's chat AppConfig instance."""
    app_config = apps.get_app_config("chat")
    runtime = getattr(app_config, "runtime", None)
    if not isinstance(runtime, ChatRuntime):
        raise RuntimeError("The chat runtime has not been initialized.")
    return runtime
