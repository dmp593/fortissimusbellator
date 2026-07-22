"""Facade that coordinates analysis, experts, and the local model fallback."""

import logging
import time

from django.utils import translation

from .assistant import assistant
from .domain import ChatReply
from .entities import EntityResolver
from .experts import (
    ContactExpert,
    EntityExpert,
    ExpertContext,
    FaqExpert,
    GreetingExpert,
    InventoryExpert,
    LocalModelExpert,
    PageExpert,
    ResponseComposer,
)
from .intents import QueryAnalyzer


logger = logging.getLogger(__name__)


class ExpertRouter:
    """Ask cheap deterministic experts before the single model expert."""

    def __init__(self, analyzer, deterministic_experts, model_expert):
        self.analyzer = analyzer
        self.deterministic_experts = deterministic_experts
        self.model_expert = model_expert

    def route(self, request):
        started_at = time.monotonic()
        analysis = self.analyzer.analyze(request)
        context = ExpertContext(request=request, analysis=analysis)
        replies = []
        selected_experts = []
        for expert in self.deterministic_experts:
            reply = expert.answer(context)
            if reply is not None:
                replies.append(reply)
                selected_experts.append(expert.__class__.__name__)
        if replies:
            response = ResponseComposer.compose(replies, request.state)
            self._log_route(
                "deterministic", selected_experts, analysis, started_at
            )
            return response

        try:
            response = self.model_expert.answer(context)
        except Exception:
            self._log_route("model_error", [], analysis, started_at)
            raise
        self._log_route("model", [], analysis, started_at)
        return response

    @staticmethod
    def _log_route(route, experts, analysis, started_at):
        entities = ",".join(
            f"{match.kind.value}:{match.instance.pk}:{match.score:.2f}"
            for match in analysis.entities.matches
        ) or "none"
        logger.info(
            "chat_route route=%s experts=%s intents=%s entities=%s duration_ms=%d",
            route,
            ",".join(experts) or "none",
            ",".join(sorted(analysis.intents)) or "none",
            entities,
            round((time.monotonic() - started_at) * 1000),
        )


class ChatService:
    """Stable application-level entry point for one chat request."""

    def __init__(self, router=None, model_assistant=None):
        if router is not None:
            self.router = router
            return

        resolver = EntityResolver()
        analyzer = QueryAnalyzer(resolver)
        model_expert = LocalModelExpert(model_assistant or assistant)
        self.router = ExpertRouter(
            analyzer=analyzer,
            deterministic_experts=(
                GreetingExpert(),
                PageExpert(),
                EntityExpert(),
                InventoryExpert(),
                ContactExpert(),
                FaqExpert(),
            ),
            model_expert=model_expert,
        )

    def reply(self, request):
        with translation.override(request.language):
            response = self.router.route(request)
        if not isinstance(response, ChatReply):
            raise TypeError("Chat experts must return a ChatReply.")
        return response


chat_service = ChatService()
