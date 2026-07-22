"""Facade that coordinates analysis, experts, and the local model fallback."""

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


class ExpertRouter:
    """Ask cheap deterministic experts before the single model expert."""

    def __init__(self, analyzer, deterministic_experts, model_expert):
        self.analyzer = analyzer
        self.deterministic_experts = deterministic_experts
        self.model_expert = model_expert

    def route(self, request):
        analysis = self.analyzer.analyze(request)
        context = ExpertContext(request=request, analysis=analysis)
        replies = [
            reply
            for expert in self.deterministic_experts
            if (reply := expert.answer(context)) is not None
        ]
        if replies:
            return ResponseComposer.compose(replies, request.state)
        return self.model_expert.answer(context)


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

