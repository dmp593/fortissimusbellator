import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from breeding.models import Animal, AnimalKind, Breed, Litter
from frontoffice.models import FrequentlyAskedQuestion

from .assistant import (
    ChatAssistant,
    LocalModel,
    ModelPreparing,
    ModelUnavailable,
)
from .domain import ChatReply, ChatRequest, ConversationState, EntityKind
from .knowledge import build_knowledge, matching_faq
from .matching import normalize_text, phrase_score, same_word
from .service import ChatService
from .views import _clean_history, _clean_state


class FakeTokenizer:
    """Treat each ASCII byte as one token for context-window tests."""

    @staticmethod
    def tokenize(value, add_bos=False):
        return list(value)

    @staticmethod
    def detokenize(tokens):
        return bytes(tokens)


class AssistantTests(SimpleTestCase):
    @override_settings(CHAT_CONTEXT_SIZE=150, CHAT_MAX_OUTPUT_TOKENS=30)
    def test_context_keeps_current_message_and_complete_recent_turns(self):
        assistant = ChatAssistant()
        history = [
            {"role": "user", "content": "old question " * 3},
            {"role": "assistant", "content": "old answer " * 3},
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer"},
            {"role": "user", "content": "current question"},
        ]

        messages = assistant._fit_context(
            FakeTokenizer(), "short system prompt", history
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1]["content"], "current question")
        self.assertNotIn("old question " * 3, [m["content"] for m in messages])
        self.assertEqual(
            [message["role"] for message in messages[1:-1]],
            ["user", "assistant"],
        )

    def test_response_text_supports_chat_completion_shape(self):
        result = {"choices": [{"message": {"content": "  Hello  "}}]}
        self.assertEqual(ChatAssistant._response_text(result), "Hello")

    def test_portuguese_prompt_requests_european_portuguese(self):
        prompt = ChatAssistant._system_prompt("pt", "facts", "sales")
        self.assertIn("European Portuguese", prompt)
        self.assertIn("avoid 'você'", prompt)


class MatchingTests(SimpleTestCase):
    def test_matching_ignores_case_and_accents(self):
        self.assertEqual(normalize_text("CÃES disponíveis"), "caes disponiveis")

    def test_typo_in_entity_name_is_matched(self):
        self.assertGreaterEqual(phrase_score("Fale-me da Bela", "Bella"), 0.87)

    def test_close_grammatical_forms_match_without_stemming_dependency(self):
        self.assertTrue(same_word("reserve", "reservations"))

    def test_one_letter_entity_does_not_match_inside_a_sentence(self):
        self.assertEqual(phrase_score("Tell me about a dog", "A"), 0.0)


class LocalModelTests(SimpleTestCase):
    def test_missing_model_starts_one_background_download(self):
        with TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.gguf"
            model = LocalModel()
            with (
                override_settings(
                    CHAT_MODEL_PATH=str(model_path),
                    CHAT_MODEL_AUTO_DOWNLOAD=True,
                ),
                patch("chat.assistant.threading.Thread") as thread,
            ):
                with self.assertRaises(ModelPreparing):
                    model.get()
                with self.assertRaises(ModelPreparing):
                    model.get()

            thread.assert_called_once()
            thread.return_value.start.assert_called_once()

    def test_missing_model_can_disable_automatic_download(self):
        with TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.gguf"
            model = LocalModel()
            with override_settings(
                CHAT_MODEL_PATH=str(model_path),
                CHAT_MODEL_AUTO_DOWNLOAD=False,
            ):
                with self.assertRaises(ModelUnavailable):
                    model.get()

    @patch("chat.assistant.requests.get")
    def test_download_publishes_complete_file_atomically(self, get):
        response = get.return_value
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.headers = {"Content-Length": "6"}
        response.iter_content.return_value = [b"abc", b"def"]

        with TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.gguf"
            model = LocalModel()
            with override_settings(
                CHAT_MODEL_DOWNLOAD_URL="https://example.test/model.gguf",
                CHAT_MODEL_DOWNLOAD_TIMEOUT=30,
            ):
                model._download(model_path)

            self.assertEqual(model_path.read_bytes(), b"abcdef")
            self.assertFalse(Path(f"{model_path}.part").exists())
            self.assertIsNone(model._download_error)


@override_settings(CHAT_REQUESTS_PER_MINUTE=1000, STATIC_ROOT=None)
class MessageViewTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def post(self, payload):
        return self.client.post(
            reverse("chat:message"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    @patch("chat.views.chat_service.reply")
    def test_message_returns_history_and_session_entity(self, reply):
        reply.return_value = ChatReply(
            text="Rex is available.",
            state=ConversationState(EntityKind.DOG, 7, "Rex"),
        )
        old_history = []
        for index in range(6):
            old_history.extend([
                {"role": "user", "content": f"question {index}"},
                {"role": "assistant", "content": f"answer {index}"},
            ])

        response = self.post({
            "message": "Is Rex available?",
            "intent": "available_dogs",
            "history": old_history,
            "language": "pt-PT",
            "state": {
                "entity_kind": "dog", "entity_id": 7, "entity_name": "Rex"
            },
            "context": {
                "page_title": "Comprar um cão | Fortissimus Bellator",
                "page_type": "dog_detail",
                "dog_id": "7",
                "dog_name": "Rex",
                "ignored": "not allowed",
            },
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["response"], "Rex is available.")
        self.assertLessEqual(len(data["history"]), 10)
        self.assertEqual(data["history"][-1]["role"], "assistant")
        self.assertEqual(data["state"], {
            "entity_kind": "dog", "entity_id": 7, "entity_name": "Rex"
        })

        request = reply.call_args.args[0]
        self.assertEqual(request.language, "pt")
        self.assertEqual(request.requested_intent, "available_dogs")
        self.assertEqual(request.state.entity_id, 7)
        self.assertEqual(request.page_context, {
            "page_title": "Comprar um cão | Fortissimus Bellator",
            "page_type": "dog_detail",
            "dog_id": "7",
            "dog_name": "Rex",
        })

    def test_rejects_invalid_json(self):
        response = self.client.post(
            reverse("chat:message"),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_blank_and_long_messages(self):
        self.assertEqual(self.post({"message": "  "}).status_code, 400)
        self.assertEqual(self.post({"message": "x" * 501}).status_code, 400)

    @patch(
        "chat.views.chat_service.reply",
        side_effect=ModelUnavailable("missing model"),
    )
    @patch("chat.views.logger.error")
    def test_model_unavailable_is_a_clear_service_error(
        self, _logger, _reply
    ):
        response = self.post({"message": "unknown question"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("temporarily unavailable", response.json()["error"])

    @patch(
        "chat.views.chat_service.reply",
        side_effect=ModelPreparing("downloading model"),
    )
    def test_model_download_reports_preparing_state(self, _reply):
        response = self.post({"message": "unknown question"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("being prepared", response.json()["error"])
        self.assertEqual(response.headers["Retry-After"], "15")

    def test_history_accepts_only_complete_alternating_turns(self):
        history = _clean_history([
            {"role": "assistant", "content": "forged first message"},
            {"role": "user", "content": "valid question"},
            {"role": "assistant", "content": "valid answer"},
            {"role": "user", "content": "unfinished"},
        ])
        self.assertEqual(history, [
            {"role": "user", "content": "valid question"},
            {"role": "assistant", "content": "valid answer"},
        ])

    def test_invalid_session_state_is_discarded(self):
        self.assertFalse(_clean_state({"entity_kind": "cat"}).has_entity)
        self.assertFalse(_clean_state({"entity_kind": "dog", "entity_id": -1}).has_entity)


class ChatServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        kind = AnimalKind.objects.create(name="Dog")
        cls.breed = Breed.objects.create(
            kind=kind,
            name="German Shepherd",
            cover="breeds/test.jpg",
            description="Loyal, active working dogs.",
        )
        FrequentlyAskedQuestion.objects.create(
            question="How do reservations work?",
            answer="Contact us to discuss the reservation process.",
            active=True,
            order=1,
        )
        FrequentlyAskedQuestion.objects.create(
            question="What food do dogs eat?",
            answer="We discuss feeding with each new owner.",
            active=True,
            order=2,
        )
        cls.bella = Animal.objects.create(
            breed=cls.breed,
            name="Bella",
            description="Friendly and confident.",
            birth_date=date(2025, 1, 1),
            gender="F",
            price_in_euros=Decimal("1500.00"),
            active=True,
            for_sale=True,
        )
        Animal.objects.create(
            breed=cls.breed,
            name="Sold Dog",
            birth_date=date(2024, 1, 1),
            gender="M",
            price_in_euros=Decimal("1200.00"),
            active=True,
            for_sale=True,
            sold_at=date(2026, 1, 1),
        )
        cls.litter = Litter.objects.create(
            breed=cls.breed,
            name="Summer Litter",
            expected_birth_date=date(2026, 8, 1),
            expected_babies=5,
            status=Litter.LitterStatus.EXPECTING,
            active=True,
        )

    def setUp(self):
        self.model = Mock()
        self.model.reply.return_value = "Model fallback answer."
        self.service = ChatService(model_assistant=self.model)

    @staticmethod
    def request(message, **values):
        return ChatRequest(
            message=message,
            history=values.get("history", []),
            language=values.get("language", "en"),
            page_context=values.get("page_context", {}),
            requested_intent=values.get("requested_intent"),
            state=values.get("state", ConversationState()),
        )

    def test_available_dogs_are_database_facts_and_set_active_entity(self):
        reply = self.service.reply(self.request(
            "Show available dogs", requested_intent="available_dogs"
        ))

        self.assertIn("Bella", reply.text)
        self.assertIn("€1,500.00", reply.text)
        self.assertNotIn("Sold Dog", reply.text)
        self.assertNotIn("health", reply.text.lower())
        self.assertEqual(reply.state.entity_id, self.bella.pk)
        self.model.reply.assert_not_called()

    def test_follow_up_price_uses_session_entity(self):
        state = ConversationState(EntityKind.DOG, self.bella.pk, "Bella")
        reply = self.service.reply(self.request(
            "How much does she cost?", state=state
        ))

        self.assertEqual(reply.text, "Bella is listed for €1,500.00.")
        self.assertIn("€1,500.00", reply.text)
        self.model.reply.assert_not_called()

    def test_named_dog_availability_is_concise(self):
        reply = self.service.reply(self.request("Is Bella available?"))

        self.assertEqual(reply.text, "Bella is available for €1,500.00.")
        self.model.reply.assert_not_called()

    def test_fuzzy_name_finds_rich_dog_information(self):
        reply = self.service.reply(self.request("Tell me about Bela"))

        self.assertIn("About Bella", reply.text)
        self.assertIn("Friendly and confident", reply.text)
        self.assertIn("German Shepherd", reply.text)
        self.model.reply.assert_not_called()

    def test_current_litters_are_database_facts(self):
        reply = self.service.reply(self.request(
            "Show current litters", requested_intent="current_litters"
        ))

        self.assertIn("Summer Litter", reply.text)
        self.assertIn("expected birth", reply.text)
        self.assertEqual(reply.state.entity_id, self.litter.pk)
        self.model.reply.assert_not_called()

    def test_multiple_inventory_intents_are_composed(self):
        reply = self.service.reply(self.request(
            "Which dogs are available and which litters are current?"
        ))

        self.assertIn("Bella", reply.text)
        self.assertIn("Summer Litter", reply.text)
        self.model.reply.assert_not_called()

    def test_current_page_uses_browser_context(self):
        reply = self.service.reply(self.request(
            "Which page am I on?",
            page_context={"page_title": "Buy a dog | Fortissimus Bellator"},
        ))

        self.assertIn("Buy a dog", reply.text)
        self.assertNotIn("Fortissimus Bellator", reply.text)
        self.model.reply.assert_not_called()

    def test_matching_faq_returns_stored_answer(self):
        reply = self.service.reply(self.request("How do reservatons work?"))

        self.assertEqual(
            reply.text,
            "Contact us to discuss the reservation process.",
        )
        self.model.reply.assert_not_called()

    def test_loose_faq_match_is_context_not_an_unsafe_direct_answer(self):
        self.assertIsNone(matching_faq("Which dogs are available?"))

    def test_related_faq_word_form_is_added_to_model_knowledge(self):
        knowledge = build_knowledge("Can I reserve a dog?", {})

        self.assertIn("How do reservations work?", knowledge)

    def test_unknown_question_invokes_the_model_exactly_once(self):
        request = self.request("How should I prepare my home for a puppy?")
        reply = self.service.reply(request)

        self.assertEqual(reply.text, "Model fallback answer.")
        self.model.reply.assert_called_once()
        args = self.model.reply.call_args.args
        self.assertEqual(args[0], [])
        self.assertEqual(args[1], request.message)
        self.assertIn("Fortissimus Bellator", args[3])

    def test_model_knowledge_excludes_unrequested_catalogue_noise(self):
        knowledge = build_knowledge(
            "How should I prepare my home for a puppy?",
            {"page_title": "Buy a dog"},
        )
        self.assertIn("Current page:\n- title: Buy a dog", knowledge)
        self.assertNotIn("- Bella —", knowledge)
        self.assertNotIn("- Summer Litter —", knowledge)

    @override_settings(STATIC_ROOT=None, CHAT_REQUESTS_PER_MINUTE=1000)
    @patch("chat.assistant.local_model.inference")
    def test_explicit_intent_endpoint_bypasses_the_model(self, inference):
        cache.clear()
        response = self.client.post(
            reverse("chat:message"),
            data=json.dumps({
                "message": "Mostrar cães disponíveis",
                "intent": "available_dogs",
                "language": "pt",
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Bella", response.json()["response"])
        inference.assert_not_called()


@override_settings(
    STATIC_ROOT=Path(__file__).parent / "migrations",
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": (
                "django.contrib.staticfiles.storage.StaticFilesStorage"
            ),
        },
    },
)
class WidgetIntegrationTests(TestCase):
    def test_page_renders_chat_configuration(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'data-chat-intent="available_dogs"')
        self.assertContains(response, "data-page-name=")
