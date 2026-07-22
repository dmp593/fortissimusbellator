import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from breeding.models import Animal, AnimalKind, Breed, Litter
from frontoffice.models import FrequentlyAskedQuestion

from .assistant import (
    ChatAssistant,
    LocalModel,
    ModelPreparing,
    ModelSnapshot,
    ModelState,
    ModelUnavailable,
)
from .domain import ChatReply, ChatRequest, ConversationState, EntityKind
from .knowledge import build_knowledge, matching_faq
from .matching import normalize_text, phrase_score, same_word
from .model_catalog import ChatModelSpec, validate_model_spec
from .model_selection import ModelSelectionError
from .models import ChatModel, ChatModelConfiguration
from .service import ChatService
from .views import _clean_history, _clean_state


def _test_model_spec(**changes):
    values = {
        "model_id": "test-model",
        "name": "Test model",
        "repository": "example/models",
        "filename": "model.gguf",
        "revision": "main",
        "sha256": "",
        "download_size": 400_000_000,
        "summary": "Test model",
        "recommended": False,
    }
    values.update(changes)
    return ChatModelSpec(**values)


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

    def test_three_letter_name_or_alias_matches_as_a_complete_word(self):
        self.assertEqual(phrase_score("Tell me about GSD", "GSD"), 0.99)


class ModelCatalogTests(SimpleTestCase):
    def test_spec_builds_safe_hugging_face_url_and_storage_path(self):
        model = _test_model_spec(
            repository="owner/model-name",
            filename="small model.gguf",
            revision="refs/pr/2",
        )

        with override_settings(CHAT_MODEL_DIR="/models"):
            self.assertEqual(model.path, Path("/models/small model.gguf"))
        self.assertEqual(
            model.download_url,
            "https://huggingface.co/owner/model-name/resolve/"
            "refs%2Fpr%2F2/small%20model.gguf",
        )

    def test_checksum_is_optional(self):
        validate_model_spec(_test_model_spec(sha256=""))
        validate_model_spec(_test_model_spec(sha256="a" * 64))

    def test_invalid_or_unsafe_values_are_rejected(self):
        invalid_models = (
            _test_model_spec(repository="https://example.com/model"),
            _test_model_spec(filename="../model.gguf"),
            _test_model_spec(sha256="not-a-checksum"),
            _test_model_spec(download_size=850_000_001),
        )

        for model in invalid_models:
            with self.subTest(model=model), self.assertRaises(ValidationError):
                validate_model_spec(model)


class LocalModelTests(SimpleTestCase):
    def test_missing_model_starts_one_background_download(self):
        with TemporaryDirectory() as directory:
            model = LocalModel(lambda: _test_model_spec())
            with (
                override_settings(
                    CHAT_MODEL_DIR=directory,
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
            model = LocalModel(lambda: _test_model_spec())
            with override_settings(
                CHAT_MODEL_DIR=directory,
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
            model = LocalModel(lambda: _test_model_spec(
                sha256=hashlib.sha256(b"abcdef").hexdigest(),
            ))
            with override_settings(
                CHAT_MODEL_DOWNLOAD_TIMEOUT=30,
                CHAT_MODEL_DIR=directory,
            ):
                model._download(model_path)

            self.assertEqual(model_path.read_bytes(), b"abcdef")
            self.assertFalse(Path(f"{model_path}.part").exists())
            self.assertEqual(model.snapshot().downloaded_bytes, 6)

    def test_existing_model_is_verified_and_loaded_in_background(self):
        with TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.gguf"
            model_path.write_bytes(b"valid model")
            model = LocalModel(lambda: _test_model_spec(
                sha256=hashlib.sha256(b"valid model").hexdigest(),
            ))
            loaded_model = object()
            with (
                override_settings(
                    CHAT_MODEL_DIR=directory,
                ),
                patch.object(model, "_load", return_value=loaded_model),
            ):
                model._prepare(model_path)

            self.assertIs(model.get(), loaded_model)
            self.assertEqual(model.snapshot().state, ModelState.READY)

    def test_close_releases_loaded_model_once(self):
        model = LocalModel(lambda: _test_model_spec())
        loaded_model = Mock()
        model._model = loaded_model
        model._state = ModelState.READY

        model.close()
        model.close()

        loaded_model.close.assert_called_once_with()
        self.assertIsNone(model._model)
        self.assertNotEqual(model.snapshot().state, ModelState.READY)

    def test_activate_unloads_current_model_before_preparing_replacement(self):
        with TemporaryDirectory() as directory:
            current = _test_model_spec(model_id="current")
            replacement = _test_model_spec(
                model_id="replacement",
                name="Replacement",
                filename="replacement.gguf",
            )
            model = LocalModel(lambda: current)
            loaded_model = Mock()
            model._model_spec = current
            model._model = loaded_model
            model._state = ModelState.READY

            with (
                override_settings(
                    CHAT_MODEL_DIR=directory,
                ),
                patch("chat.assistant.threading.Thread") as thread,
            ):
                snapshot = model.activate(replacement)

            loaded_model.close.assert_called_once_with()
            self.assertEqual(snapshot.model_id, replacement.model_id)
            self.assertEqual(model._model_spec, replacement)
            thread.assert_called_once()
            thread.return_value.start.assert_called_once_with()

    @patch("chat.assistant.requests.get")
    def test_bad_checksum_never_publishes_download(self, get):
        response = get.return_value
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.headers = {"Content-Length": "6"}
        response.iter_content.return_value = [b"abcdef"]

        with TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.gguf"
            model = LocalModel(lambda: _test_model_spec(
                sha256="0" * 64,
            ))
            with override_settings(CHAT_MODEL_DIR=directory):
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    model._download(model_path)

            self.assertFalse(model_path.exists())
            self.assertFalse(Path(f"{model_path}.part").exists())

    @patch("chat.assistant.requests.get")
    def test_stream_limit_applies_when_content_length_is_invalid(self, get):
        response = get.return_value
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.headers = {"Content-Length": "not-a-number"}
        response.iter_content.return_value = [b"abc"]

        with TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.gguf"
            model = LocalModel(lambda: _test_model_spec())
            with override_settings(CHAT_MODEL_MAX_DOWNLOAD_BYTES=2):
                with self.assertRaisesRegex(RuntimeError, "size limit"):
                    model._download(model_path)

            self.assertFalse(model_path.exists())
            self.assertFalse(Path(f"{model_path}.part").exists())

    @patch("chat.assistant.requests.get")
    def test_unpinned_download_is_allowed_and_hash_is_logged(self, get):
        response = get.return_value
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.headers = {"Content-Length": "3"}
        response.iter_content.return_value = [b"abc"]

        with TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.gguf"
            model = LocalModel(lambda: _test_model_spec(sha256=""))
            with (
                override_settings(CHAT_MODEL_DIR=directory),
                self.assertLogs("chat.assistant", level="INFO") as logs,
            ):
                model._download(model_path)
                self.assertEqual(model_path.read_bytes(), b"abc")

        self.assertIn("chat_model_checksum_observed", " ".join(logs.output))

    def test_download_latest_reads_fresh_database_spec(self):
        original = _test_model_spec(revision="old")
        updated = _test_model_spec(revision="main")
        provider = Mock(return_value=updated)
        model = LocalModel(provider)
        model._model_spec = original

        with patch.object(model, "activate", return_value="snapshot") as activate:
            result = model.download_latest()

        self.assertEqual(result, "snapshot")
        activate.assert_called_once_with(updated, force_download=True)


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
            chat_search_aliases="Can I book a puppy?",
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
            chat_search_aliases="Bibi\nBellinha",
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

    def test_admin_managed_alias_finds_dog(self):
        reply = self.service.reply(self.request("Tell me about Bibi"))

        self.assertIn("About Bella", reply.text)
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

    def test_admin_managed_alias_finds_faq(self):
        reply = self.service.reply(self.request("Can I book a puppy?"))

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
        self.assertIn("German Shepherd", args[3])

    def test_route_log_contains_metadata_but_not_the_message(self):
        secret_message = "A private but unknown customer question"
        with self.assertLogs("chat.service", level="INFO") as logs:
            self.service.reply(self.request(secret_message))

        output = " ".join(logs.output)
        self.assertIn("route=model", output)
        self.assertNotIn(secret_message, output)

    def test_golden_sales_conversation_in_every_supported_language(self):
        conversations = {
            "en": ("Which dogs are available?", "How much does she cost?", "Dogs currently available:"),
            "pt": ("Que cães estão disponíveis?", "Quanto custa ela?", "Cães atualmente disponíveis:"),
            "es": ("¿Qué perros están disponibles?", "¿Cuánto cuesta ella?", "Perros disponibles actualmente:"),
            "fr": ("Quels chiens sont disponibles ?", "Combien coûte-t-elle ?", "Chiens actuellement disponibles:"),
            "de": ("Welche Hunde sind verfügbar?", "Wie viel kostet sie?", "Derzeit verfügbare Hunde:"),
            "it": ("Quali cani sono disponibili?", "Quanto costa lei?", "Cani attualmente disponibili:"),
        }

        for language, (question, follow_up, heading) in conversations.items():
            with self.subTest(language=language):
                first = self.service.reply(self.request(
                    question, language=language
                ))
                second = self.service.reply(self.request(
                    follow_up, language=language, state=first.state
                ))
                self.assertIn(heading, first.text)
                self.assertIn("Bella", first.text)
                self.assertIn("Bella", second.text)
                self.assertTrue(second.state.has_entity)

        self.model.reply.assert_not_called()

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


class ChatFixtureAliasTests(TestCase):
    fixtures = (
        "animalskinds",
        "breeds",
        "certifications",
        "animals",
        "litters",
        "faqs",
    )

    def test_searchable_fixture_models_have_aliases(self):
        self.assertFalse(
            Breed.objects.filter(chat_search_aliases="").exists()
        )
        self.assertFalse(
            Animal.objects.filter(chat_search_aliases="").exists()
        )
        self.assertFalse(
            Litter.objects.filter(chat_search_aliases="").exists()
        )
        self.assertFalse(
            FrequentlyAskedQuestion.objects.filter(
                chat_search_aliases=""
            ).exists()
        )

    def test_fixture_aliases_are_used_by_chat_matching(self):
        service = ChatService(model_assistant=Mock())
        request = ChatRequest(
            message="Fala-me do GSD",
            history=[],
            language="pt",
            page_context={},
        )

        reply = service.reply(request)

        self.assertIn("Pastor Alemão", reply.text)
        self.assertEqual(
            matching_faq("Como funcionam as reservas?").pk,
            6,
        )


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
class ModelStatusViewTests(TestCase):
    fixtures = ("chat_models",)

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="admin",
            password="password",
            is_staff=True,
            is_superuser=True,
        )

    def test_status_page_requires_staff(self):
        response = self.client.get(reverse("chat_model_status"))
        self.assertEqual(response.status_code, 302)

    def test_admin_header_links_to_model_status(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:index"))

        self.assertContains(response, reverse("chat_model_status"))
        self.assertContains(response, "Local chat model status")

    def test_admin_can_manage_dynamic_model_catalogue(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("admin:chat_chatmodel_changelist")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Qwen3.5 0.8B Instruct")
        self.assertContains(response, "Qwen2.5 0.5B Instruct")

    @patch(
        "chat.views.local_model.snapshot",
        side_effect=ModelSelectionError("No model"),
    )
    def test_empty_catalogue_links_to_add_model(self, _snapshot):
        ChatModel.objects.update(enabled=False)
        self.client.force_login(self.user)

        response = self.client.get(reverse("chat_model_status"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No enabled models")
        self.assertContains(response, reverse("admin:chat_chatmodel_add"))

    @patch("chat.views.local_model")
    def test_staff_can_view_and_retry_model(self, model):
        model.snapshot.return_value = ModelSnapshot(
            state=ModelState.FAILED,
            model_path="/models/chat.gguf",
            file_size=0,
            downloaded_bytes=10,
            total_bytes=100,
            error="download failed",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("chat_model_status"), {"action": "retry"}
        )

        self.assertRedirects(
            response,
            reverse("chat_model_status"),
            fetch_redirect_response=False,
        )
        model.prepare.assert_called_once_with(retry=True)

        response = self.client.get(reverse("chat_model_status"))
        self.assertContains(response, "download failed")
        self.assertContains(response, "Fortissimus Bellator Administration")
        self.assertContains(response, "model-status--failed")
        self.assertContains(response, "model-status__alert")

    @patch("chat.views.local_model")
    def test_preparing_status_refreshes_without_duplicate_action(self, model):
        model.snapshot.return_value = ModelSnapshot(
            state=ModelState.DOWNLOADING,
            model_path="/models/chat.gguf",
            file_size=0,
            downloaded_bytes=25,
            total_bytes=100,
            error="",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("chat_model_status"))

        self.assertContains(response, 'http-equiv="refresh"')
        self.assertContains(response, "25%")
        self.assertContains(response, "Qwen3.5 0.8B Instruct")
        self.assertContains(response, "Qwen2.5 0.5B Instruct")
        self.assertContains(response, 'id="chat-model-id"')
        self.assertNotContains(response, 'value="prepare"')

    @patch("chat.views.local_model")
    def test_staff_can_select_only_an_approved_model(self, model):
        current = ChatModel.objects.get(
            pk="qwen2.5-0.5b-q4-k-m"
        ).to_spec()
        model.snapshot.return_value = ModelSnapshot(
            state=ModelState.READY,
            model_path=str(current.path),
            file_size=0,
            downloaded_bytes=0,
            total_bytes=0,
            error="",
            model_id=current.model_id,
            model_name=current.name,
        )
        self.client.force_login(self.user)
        selected = ChatModel.objects.get(
            pk="qwen3.5-0.8b-q4-k-m"
        ).to_spec()

        response = self.client.post(
            reverse("chat_model_status"),
            {"action": "activate", "model_id": selected.model_id},
        )

        self.assertEqual(response.status_code, 302)
        model.activate.assert_called_once_with(selected)
        self.assertEqual(
            ChatModelConfiguration.objects.get(pk=1).active_model_id,
            selected.model_id,
        )

        model.reset_mock()
        response = self.client.post(
            reverse("chat_model_status"),
            {"action": "activate", "model_id": "untrusted-model"},
        )

        self.assertEqual(response.status_code, 302)
        model.activate.assert_not_called()

    @patch("chat.views.local_model")
    def test_staff_can_download_latest_active_revision(self, model):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("chat_model_status"),
            {"action": "download_latest"},
        )

        self.assertEqual(response.status_code, 302)
        model.download_latest.assert_called_once_with()


class ChatModelFixtureTests(TestCase):
    fixtures = ("chat_models",)

    def test_seed_models_allow_upstream_updates_without_required_checksum(self):
        models = ChatModel.objects.order_by("model_id")

        self.assertEqual(models.count(), 5)
        self.assertFalse(models.exclude(revision="main").exists())
        self.assertFalse(models.exclude(sha256="").exists())
        for model in models:
            validate_model_spec(model)
