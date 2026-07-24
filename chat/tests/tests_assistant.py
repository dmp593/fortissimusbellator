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
from django.utils import timezone

from blog.models import Post
from breeding.models import (
    Animal,
    AnimalCertification,
    AnimalKind,
    Breed,
    Certification,
    Litter,
)
from frontoffice.models import FrequentlyAskedQuestion
from reservations.models import (
    Payment,
    PreReservation,
    PreReservationTerms,
    Reservation,
    ReservationTerms,
)
from reservations.services.reservation import (
    accept_pre_reservation,
    create_pending_reservation,
    mark_pre_reservation_payment_setup_failed,
)
from reservations.services.admin_workflows import create_admin_sale

from ..assistant import (
    ChatAssistant,
    LocalModel,
    ModelPreparing,
    ModelSnapshot,
    ModelState,
    ModelUnavailable,
)
from ..catalog import available_animals, reservable_litters
from ..domain import ChatReply, ChatRequest, ConversationState, EntityKind
from ..entities import EntityResolver
from ..knowledge import (
    KnowledgeSnapshot,
    build_knowledge,
    matching_faq,
)
from ..matching import normalize_text, phrase_score, same_word
from ..model_catalog import ChatModelSpec, validate_model_spec
from ..model_selection import ModelSelectionError
from ..models import ChatModel, ChatModelConfiguration, ChatSearchEntry
from ..runtime import get_chat_runtime
from ..search_index import (
    aliases_for,
    rebuild_search_index,
    save_aliases,
)
from ..service import ChatService
from ..views import _clean_history, _clean_state


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
        assistant = ChatAssistant(Mock())
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
        self.assertIn("Do not use\ngeneral or external knowledge", prompt)


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

    @override_settings(CHAT_MODEL_WAIT_SECONDS=1)
    def test_inference_waits_for_background_loading(self):
        with TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.gguf"
            model_path.write_bytes(b"valid model")
            model = LocalModel(lambda: _test_model_spec())
            loaded_model = object()

            with (
                override_settings(CHAT_MODEL_DIR=directory),
                patch.object(model, "_load", return_value=loaded_model),
                model.inference() as ready_model,
            ):
                self.assertIs(ready_model, loaded_model)

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

    @patch("chat.views.get_chat_runtime")
    def test_message_returns_history_and_session_entity(self, runtime):
        reply = runtime.return_value.chat_service.reply
        reply.return_value = ChatReply(
            text="Rex is available.",
            state=ConversationState(EntityKind.ANIMAL, 7, "Rex"),
        )
        old_history = []
        for index in range(6):
            old_history.extend([
                {"role": "user", "content": f"question {index}"},
                {"role": "assistant", "content": f"answer {index}"},
            ])

        response = self.post({
            "message": "Is Rex available?",
            "intent": "available_animals",
            "history": old_history,
            "language": "pt-PT",
            "state": {
                "entity_kind": "dog", "entity_id": 7, "entity_name": "Rex"
            },
            "context": {
                "page_title": "Comprar um cão | Fortissimus Bellator",
                "page_type": "animal_detail",
                "animal_id": "7",
                "animal_name": "Rex",
                "ignored": "not allowed",
            },
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["response"], "Rex is available.")
        self.assertLessEqual(len(data["history"]), 10)
        self.assertEqual(data["history"][-1]["role"], "assistant")
        self.assertEqual(data["state"], {
            "entity_kind": "animal", "entity_id": 7, "entity_name": "Rex"
        })

        request = reply.call_args.args[0]
        self.assertEqual(request.language, "pt")
        self.assertEqual(request.requested_intent, "available_animals")
        self.assertEqual(request.state.entity_kind, EntityKind.ANIMAL)
        self.assertEqual(request.state.entity_id, 7)
        self.assertEqual(request.page_context, {
            "page_title": "Comprar um cão | Fortissimus Bellator",
            "page_type": "animal_detail",
            "animal_id": "7",
            "animal_name": "Rex",
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

    @patch("chat.views.get_chat_runtime")
    @patch("chat.views.logger.error")
    def test_model_unavailable_is_a_clear_service_error(
        self, _logger, runtime
    ):
        runtime.return_value.chat_service.reply.side_effect = ModelUnavailable(
            "missing model"
        )
        response = self.post({"message": "unknown question"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("temporarily unavailable", response.json()["error"])

    @patch("chat.views.get_chat_runtime")
    def test_model_download_reports_preparing_state(self, runtime):
        runtime.return_value.chat_service.reply.side_effect = ModelPreparing(
            "downloading model"
        )
        response = self.post({"message": "unknown question"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("starting", response.json()["error"])
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


class ChatSearchIndexTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kind = AnimalKind.objects.create(name="Dog")
        cls.breed = Breed.objects.create(
            kind=cls.kind,
            name="German Shepherd",
            cover="breeds/test.jpg",
            active=True,
        )
        cls.animal = Animal.objects.create(
            breed=cls.breed,
            name="Atlas",
            birth_date=date(2025, 1, 1),
            active=True,
            for_sale=True,
        )

    def test_save_signal_refreshes_metadata_without_overwriting_aliases(self):
        save_aliases(self.animal, "Atlaz\nAtlas dog")

        self.animal.name = "Atlas Prime"
        self.animal.save(update_fields=["name"])

        entry = ChatSearchEntry.objects.get(
            object_id=self.animal.pk,
            content_type__app_label="breeding",
            content_type__model="animal",
        )
        self.assertEqual(entry.label, "Atlas Prime")
        self.assertEqual(entry.canonical_terms, ["Atlas Prime"])
        self.assertEqual(entry.aliases, "Atlaz\nAtlas dog")

    def test_delete_signal_removes_the_polymorphic_entry(self):
        entry_pk = ChatSearchEntry.objects.get(
            label=self.animal.name,
        ).pk

        self.animal.delete()

        self.assertFalse(ChatSearchEntry.objects.filter(pk=entry_pk).exists())

    def test_global_resolution_can_return_different_model_types(self):
        faq = FrequentlyAskedQuestion.objects.create(
            question="Atlas",
            answer="A deliberately shared test term.",
            active=True,
        )

        resolution = EntityResolver().resolve_explicit("Atlas")

        self.assertTrue(resolution.ambiguous)
        self.assertEqual(
            {match.kind for match in resolution.matches},
            {EntityKind.ANIMAL, EntityKind.FAQ},
        )
        self.assertEqual(len(resolution.matches), 2)
        self.assertIn(faq, [match.instance for match in resolution.matches])

    def test_global_ambiguity_only_asks_the_visitor_to_disambiguate(self):
        faq = FrequentlyAskedQuestion.objects.create(
            question="Atlas",
            answer="This answer must wait until the visitor clarifies.",
            active=True,
        )

        reply = ChatService(model_assistant=Mock()).reply(ChatRequest(
            message="Atlas",
            history=[],
            language="en",
            page_context={},
        ))

        self.assertIn("Did you mean:", reply.text)
        self.assertNotIn(faq.answer, reply.text)

    def test_short_certification_code_matches_inside_a_question(self):
        certification = Certification.objects.create(
            code="BH",
            name="Companion dog examination",
        )

        resolution = EntityResolver().resolve_explicit("What is BH?")

        self.assertFalse(resolution.ambiguous)
        self.assertEqual(len(resolution.matches), 1)
        self.assertEqual(
            resolution.matches[0].kind,
            EntityKind.CERTIFICATION,
        )
        self.assertEqual(resolution.matches[0].instance, certification)

    def test_index_never_bypasses_a_models_public_queryset(self):
        faq = FrequentlyAskedQuestion.objects.create(
            question="Private staff answer",
            answer="This must not be exposed.",
            active=False,
        )
        save_aliases(faq, "Secret kennel policy")

        resolution = EntityResolver().resolve_explicit(
            "Secret kennel policy"
        )

        self.assertFalse(resolution.matches)

    def test_rebuild_repairs_metadata_and_preserves_reviewed_aliases(self):
        save_aliases(self.animal, "Atlaz")
        ChatSearchEntry.objects.filter(
            label=self.animal.name,
        ).update(label="stale", canonical_terms=[])

        result = rebuild_search_index()

        entry = ChatSearchEntry.objects.get(
            object_id=self.animal.pk,
            content_type__model="animal",
        )
        self.assertGreaterEqual(result.indexed, 3)
        self.assertEqual(entry.label, "Atlas")
        self.assertEqual(entry.canonical_terms, ["Atlas"])
        self.assertEqual(entry.aliases, "Atlaz")


class ChatServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.blog_author = get_user_model().objects.create_user(
            username="chat-blog-author",
            password="password",
        )
        cls.pre_reservation_terms = PreReservationTerms.objects.create(
            version="chat-tests-v1",
            description="The pre-reservation fee is non-refundable.",
            published_at=timezone.now(),
        )
        ReservationTerms.objects.create(
            version="chat-reservation-tests-v1",
            description="Reservation deposit terms.",
            published_at=timezone.now(),
        )
        kind = AnimalKind.objects.create(
            name="Dog",
        )
        save_aliases(
            kind,
            "Dogs\nPuppy\nPuppies\nCães\nCachorros\nPerros\n"
            "Chiens\nHunde\nCani",
        )
        cls.breed = Breed.objects.create(
            kind=kind,
            name="German Shepherd",
            cover="breeds/test.jpg",
            description="Loyal, active working dogs.",
        )
        cls.cat_kind = AnimalKind.objects.create(name="Cat")
        save_aliases(
            cls.cat_kind,
            "Cats\nKitten\nKittens\nGato\nGatos",
        )
        cls.cat_breed = Breed.objects.create(
            kind=cls.cat_kind,
            name="Maine Coon",
            cover="breeds/cat-test.jpg",
        )
        reservation_faq = FrequentlyAskedQuestion.objects.create(
            question="How do reservations work?",
            answer="Contact us to discuss the reservation process.",
            active=True,
            order=1,
        )
        save_aliases(reservation_faq, "Can I book a puppy?")
        FrequentlyAskedQuestion.objects.create(
            question="What food do dogs eat?",
            answer="We discuss feeding with each new owner.",
            active=True,
            order=2,
        )
        cls.return_faq = FrequentlyAskedQuestion.objects.create(
            question="Can I return a dog if there are issues?",
            answer=(
                "We have a return policy in case of health issues. "
                "Please contact us within 14 days of purchase."
            ),
            active=True,
            order=3,
        )
        health_faq = FrequentlyAskedQuestion.objects.create(
            question="Do you provide a health warranty?",
            answer="Please contact us to discuss the health warranty.",
            active=True,
            order=4,
        )
        save_aliases(health_faq, "Existe garantia de saúde?")
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
        save_aliases(cls.bella, "Bibi\nBellinha")
        cls.certification = Certification.objects.create(
            code="WB",
            name="Wesensbeurteilung",
            description=(
                "The WB examines an animal's character and temperament "
                "before training can significantly influence it."
            ),
            description_pt=(
                "O WB avalia o caráter e o temperamento do animal antes de "
                "o treino os poder influenciar significativamente."
            ),
        )
        save_aliases(cls.certification, "What is WB?\nO que é o WB?")
        AnimalCertification.objects.create(
            animal=cls.bella,
            certification=cls.certification,
        )
        sold_dog = Animal.objects.create(
            breed=cls.breed,
            name="Sold Dog",
            birth_date=date(2024, 1, 1),
            gender="M",
            price_in_euros=Decimal("1200.00"),
            active=True,
            for_sale=True,
        )
        create_admin_sale(
            animal_id=sold_dog.pk,
            user=cls.blog_author,
            customer_data={
                'customer_name': 'Chat Test Customer',
                'customer_email': 'chat-customer@example.com',
            },
            final_price=Decimal('1200.00'),
            payment_provider=Payment.Provider.CASH,
            sold_at=date(2026, 1, 1),
            created_by=cls.blog_author,
            payment_reference='chat-test-sale',
        )
        cls.litter = Litter.objects.create(
            breed=cls.breed,
            name="Summer Litter",
            expected_birth_date=date(2026, 8, 1),
            expected_babies=5,
            status=Litter.LitterStatus.EXPECTING,
            active=True,
        )
        cls.blog_post = Post.posts.create(
            author=cls.blog_author,
            title="Preparing your home for a puppy",
            cover="posts/puppy-home.jpg",
            content={"type": "doc"},
            published_at=timezone.now(),
            active=True,
        )
        Post.posts.create(
            author=cls.blog_author,
            title="Unpublished staff notes",
            cover="posts/staff-notes.jpg",
            content={"type": "doc"},
            published_at=timezone.now(),
            active=False,
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

    def reserve(self, target):
        return create_pending_reservation(
            user=self.blog_author,
            target_type=PreReservation.TargetType.DOG,
            target_id=target.pk,
            checkout_data={
                "full_name": "Chat Test Customer",
                "email": "chat-customer@example.com",
                "phone": "+351912345678",
                "tax_number": "999999990",
                "billing_address": "Test Street 1",
                "billing_postcode": "1000-001",
                "billing_city": "Lisbon",
                "billing_country": "PT",
                "promotion_code": "",
                "terms": self.pre_reservation_terms,
                "accept_non_refundable": True,
            },
            language_code="en",
        )

    def accept(self, target):
        pre_reservation = self.reserve(target)
        now = timezone.now()
        PreReservation.objects.filter(pk=pre_reservation.pk).update(
            status=PreReservation.Status.AWAITING_REVIEW,
            confirmed_at=now,
        )
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            status=Payment.Status.PAID,
            paid_at=now,
        )
        return accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.blog_author,
        )

    def test_available_animals_are_database_facts_and_set_active_entity(self):
        reply = self.service.reply(self.request(
            "Show available animals", requested_intent="available_animals"
        ))

        self.assertIn("Bella", reply.text)
        self.assertIn("€1,500.00", reply.text)
        self.assertNotIn("Sold Dog", reply.text)
        self.assertNotIn("health", reply.text.lower())
        self.assertIn("WB", reply.text)
        self.assertNotIn(self.certification.description, reply.text)
        self.assertEqual(reply.state.entity_id, self.bella.pk)
        self.model.reply.assert_not_called()

    def test_database_animal_kind_filters_inventory_without_new_python_enum(self):
        luna = Animal.objects.create(
            breed=self.cat_breed,
            name="Luna",
            birth_date=date(2025, 2, 1),
            gender="F",
            price_in_euros=Decimal("900.00"),
            active=True,
            for_sale=True,
        )

        cats_reply = self.service.reply(self.request("Which cats can I buy?"))
        dogs_reply = self.service.reply(self.request("Which dogs can I buy?"))

        self.assertIn(luna.name, cats_reply.text)
        self.assertNotIn(self.bella.name, cats_reply.text)
        self.assertIn(self.bella.name, dogs_reply.text)
        self.assertNotIn(luna.name, dogs_reply.text)
        self.assertEqual(cats_reply.state.entity_kind, EntityKind.ANIMAL)
        self.model.reply.assert_not_called()

    def test_follow_up_price_uses_session_entity(self):
        state = ConversationState(EntityKind.ANIMAL, self.bella.pk, "Bella")
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

    def test_reserved_dog_is_excluded_from_available_inventory(self):
        self.reserve(self.bella)

        reply = self.service.reply(self.request("Which dogs can I buy?"))

        self.assertNotIn("Bella", reply.text)
        self.assertIn("No animals are currently listed for sale", reply.text)
        self.model.reply.assert_not_called()

    def test_portuguese_acquisition_query_excludes_reserved_dog(self):
        self.reserve(self.bella)

        reply = self.service.reply(self.request(
            "Que cães posso adquirir?",
            language="pt",
        ))

        self.assertNotIn("Bella", reply.text)
        self.assertIn("não existem animais anunciados para venda", reply.text)
        self.model.reply.assert_not_called()

    def test_named_reserved_dog_is_never_reported_as_available(self):
        self.reserve(self.bella)

        reply = self.service.reply(self.request("Can I buy Bella?"))

        self.assertIn("Bella is not currently available", reply.text)
        self.assertIn("already pre-reserved", reply.text)
        self.model.reply.assert_not_called()

    def test_accepted_pre_reservation_remains_pre_reserved_in_chat(self):
        self.accept(self.bella)

        reply = self.service.reply(self.request("Can I buy Bella?"))

        self.assertIn("already pre-reserved", reply.text)
        self.assertNotIn("already reserved", reply.text)
        self.model.reply.assert_not_called()

    def test_paid_reservation_is_reported_as_reserved_in_chat(self):
        reservation = self.accept(self.bella)
        reservation.status = Reservation.Status.CONFIRMED
        reservation.save(update_fields=["status", "updated_at"])

        reply = self.service.reply(self.request("Can I buy Bella?"))

        self.assertIn("already reserved", reply.text)
        self.assertNotIn("already pre-reserved", reply.text)
        self.model.reply.assert_not_called()

    def test_reserved_dog_price_includes_the_reservation_warning(self):
        self.reserve(self.bella)

        reply = self.service.reply(self.request("How much does Bella cost?"))

        self.assertIn("€1,500.00", reply.text)
        self.assertIn("cannot currently be pre-reserved", reply.text)
        self.assertIn("already pre-reserved", reply.text)
        self.model.reply.assert_not_called()

    def test_failed_payment_returns_dog_to_available_chat_inventory(self):
        reservation = self.reserve(self.bella)
        mark_pre_reservation_payment_setup_failed(
            reservation.pk,
            "Definite payment failure",
        )

        reply = self.service.reply(self.request("Which dogs can I acquire?"))

        self.assertIn("Bella", reply.text)
        self.assertIn("€1,500.00", reply.text)
        self.model.reply.assert_not_called()

    def test_reservation_aware_catalogues_execute_one_query_each(self):
        self.reserve(self.bella)

        with self.assertNumQueries(1):
            animals = list(available_animals())
        with self.assertNumQueries(0):
            litters = list(reservable_litters())

        self.assertEqual(animals, [])
        self.assertEqual(litters, [])

    def test_page_context_reports_reserved_dog_as_unavailable(self):
        self.reserve(self.bella)

        reply = self.service.reply(self.request(
            "Can I reserve this?",
            page_context={
                "animal_id": str(self.bella.pk),
                "animal_name": self.bella.name,
            },
        ))

        self.assertIn("already pre-reserved", reply.text)
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
        self.assertIn("individual dogs can be pre-reserved", reply.text)
        self.assertNotIn(self.return_faq.answer, reply.text)
        self.assertEqual(reply.state.entity_id, self.litter.pk)
        self.model.reply.assert_not_called()

    def test_born_litter_is_never_reported_as_reservable(self):
        litter = Litter.objects.get(pk=self.litter.pk)
        litter.birth_date = timezone.localdate()
        litter.babies = 1
        litter.status = Litter.LitterStatus.BORN
        litter.save(
            update_fields=[
                "birth_date",
                "babies",
                "status",
            ]
        )

        inventory_reply = self.service.reply(
            self.request("Which litters can I reserve?")
        )
        named_reply = self.service.reply(
            self.request("Can I reserve Summer Litter?")
        )

        self.assertIn(
            "Litters cannot be pre-reserved",
            inventory_reply.text,
        )
        self.assertIn("cannot be pre-reserved", named_reply.text)
        self.assertIn("birth alert", named_reply.text)
        self.model.reply.assert_not_called()

    def test_multiple_inventory_intents_are_composed(self):
        reply = self.service.reply(self.request(
            "Which dogs are available and which litters are current?"
        ))

        self.assertIn("Bella", reply.text)
        self.assertIn("Summer Litter", reply.text)
        self.assertNotIn(self.return_faq.answer, reply.text)
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

    def test_short_return_question_returns_only_the_published_faq(self):
        reply = self.service.reply(self.request("how to return"))

        self.assertEqual(reply.text, self.return_faq.answer)
        self.assertNotIn("/returns", reply.text)
        self.assertNotIn("full refund", reply.text)
        self.model.reply.assert_not_called()

    def test_blog_lists_only_published_post_titles(self):
        reply = self.service.reply(self.request("Which blog posts do you have?"))

        self.assertIn("Published blog posts:", reply.text)
        self.assertIn(self.blog_post.title, reply.text)
        self.assertNotIn("Unpublished staff notes", reply.text)
        self.model.reply.assert_not_called()

    def test_blog_titles_use_the_active_language(self):
        reply = self.service.reply(self.request(
            "Que posts tens?", language="pt"
        ))

        self.assertIn("Publicações do blogue:", reply.text)
        self.assertIn(self.blog_post.title, reply.text)
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

    def test_faq_answers_are_not_added_to_model_knowledge(self):
        knowledge = build_knowledge("Can I reserve a dog?", {})

        self.assertNotIn("How do reservations work?", knowledge)
        self.assertNotIn(
            "Contact us to discuss the reservation process.",
            knowledge,
        )

    def test_blog_titles_are_added_to_blog_knowledge(self):
        knowledge = build_knowledge("Which blog posts do you have?", {})

        self.assertIn("Published blog posts:", knowledge)
        self.assertIn(self.blog_post.title, knowledge)
        self.assertNotIn("Unpublished staff notes", knowledge)

    def test_specific_certification_uses_detailed_database_knowledge(self):
        reply = self.service.reply(self.request("What is WB?"))

        self.assertIn("About certification WB:", reply.text)
        self.assertIn("Wesensbeurteilung", reply.text)
        self.assertIn(self.certification.description, reply.text)
        self.model.reply.assert_not_called()

    def test_generic_certification_query_uses_published_certification_catalogue(self):
        reply = self.service.reply(self.request(
            "Que certificações existem?",
            language="pt",
        ))

        self.assertIn("Certificações publicadas:", reply.text)
        self.assertIn("WB", reply.text)
        self.assertIn("Wesensbeurteilung", reply.text)
        self.assertNotIn(self.certification.description_pt, reply.text)
        self.model.reply.assert_not_called()

    def test_single_related_faq_returns_stored_answer_without_model(self):
        request = self.request("What food should a dog eat?")
        reply = self.service.reply(request)

        self.assertEqual(
            reply.text,
            "We discuss feeding with each new owner.",
        )
        self.model.reply.assert_not_called()

    def test_multiple_related_faqs_return_only_published_answers(self):
        second_return_faq = FrequentlyAskedQuestion.objects.create(
            question="Can I return training equipment?",
            answer="Contact us before returning training equipment.",
            active=True,
            order=5,
        )

        reply = self.service.reply(self.request("return issues equipment"))

        self.assertIn(self.return_faq.answer, reply.text)
        self.assertIn(second_return_faq.answer, reply.text)
        self.assertNotIn("/returns", reply.text)
        self.model.reply.assert_not_called()

    def test_model_response_with_unpublished_url_is_rejected(self):
        self.model.reply.return_value = (
            "Read https://fortissimusbellator.pt/returns"
        )
        snapshot = KnowledgeSnapshot(
            text="Website: https://fortissimusbellator.pt",
            has_query_facts=True,
        )

        with patch(
            "chat.experts.build_knowledge_snapshot",
            return_value=snapshot,
        ):
            reply = self.service.reply(self.request("Explain this topic"))

        self.assertIn("I do not have information about that.", reply.text)
        self.assertNotIn("/returns", reply.text)
        self.model.reply.assert_called_once()

    def test_model_can_repeat_an_exact_published_url(self):
        self.model.reply.return_value = (
            "Website: https://fortissimusbellator.pt/"
        )
        snapshot = KnowledgeSnapshot(
            text="Website: https://fortissimusbellator.pt",
            has_query_facts=True,
        )

        with patch(
            "chat.experts.build_knowledge_snapshot",
            return_value=snapshot,
        ):
            reply = self.service.reply(self.request("Explain this topic"))

        self.assertEqual(
            reply.text,
            "Website: https://fortissimusbellator.pt/",
        )

    def test_link_correction_does_not_match_a_foreign_language_alias(self):
        history = [
            {"role": "user", "content": "how to return"},
            {
                "role": "assistant",
                "content": "See https://fortissimusbellator.pt/returns",
            },
        ]

        reply = self.service.reply(self.request(
            "that link does not exist",
            history=history,
        ))

        self.assertIn("I do not have information about that.", reply.text)
        self.assertNotIn("/returns", reply.text)
        self.assertNotIn("health warranty", reply.text)
        self.model.reply.assert_not_called()

    def test_general_knowledge_question_does_not_invoke_model(self):
        reply = self.service.reply(self.request("Who is the current Pope?"))

        self.assertIn("I do not have information about that.", reply.text)
        self.model.reply.assert_not_called()

    def test_route_log_contains_metadata_but_not_the_message(self):
        secret_message = "A private but unknown customer question"
        with self.assertLogs("chat.service", level="INFO") as logs:
            self.service.reply(self.request(secret_message))

        output = " ".join(logs.output)
        self.assertIn("route=knowledge_boundary", output)
        self.assertNotIn(secret_message, output)

    def test_golden_sales_conversation_in_every_supported_language(self):
        conversations = {
            "en": ("Which dogs are available?", "How much does she cost?", "Animals currently available:"),
            "pt": ("Que cães estão disponíveis?", "Quanto custa ela?", "Animais atualmente disponíveis:"),
            "es": ("¿Qué perros están disponibles?", "¿Cuánto cuesta ella?", "Animales disponibles actualmente:"),
            "fr": ("Quels chiens sont disponibles ?", "Combien coûte-t-elle ?", "Animaux actuellement disponibles :"),
            "de": ("Welche Hunde sind verfügbar?", "Wie viel kostet sie?", "Derzeit verfügbare Tiere:"),
            "it": ("Quali cani sono disponibili?", "Quanto costa lei?", "Animali attualmente disponibili:"),
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
    def test_explicit_intent_endpoint_bypasses_the_model(self):
        cache.clear()
        with patch.object(
            get_chat_runtime().local_model,
            "inference",
        ) as inference:
            response = self.client.post(
                reverse("chat:message"),
                data=json.dumps({
                    "message": "Mostrar cães disponíveis",
                    "intent": "available_animals",
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
        "chat_search_entries",
    )

    def test_searchable_fixture_models_have_aliases(self):
        for model in (
            AnimalKind,
            Breed,
            Animal,
            Litter,
            Certification,
            FrequentlyAskedQuestion,
        ):
            with self.subTest(model=model._meta.label):
                self.assertTrue(model.objects.exists())
                self.assertTrue(all(
                    aliases_for(instance)
                    for instance in model.objects.all()
                ))

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

    def test_all_fixture_certifications_fit_in_generic_model_knowledge(self):
        knowledge = build_knowledge("Que certificações existem?", {})

        for code in Certification.objects.values_list("code", flat=True):
            with self.subTest(code=code):
                self.assertIn(f"Code: {code}", knowledge)


@override_settings(
    STATIC_ROOT=Path(__file__).parent.parent / "migrations",
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
        self.assertContains(response, 'data-chat-intent="available_animals"')
        self.assertContains(response, 'data-chat-intent="certifications"')
        self.assertContains(response, "data-page-name=")

    def test_mobile_chat_uses_full_screen_overlay_without_small_input_zoom(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, 'class="chat-panel ')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, "text-base md:text-sm")
        self.assertNotContains(response, "calc(100vh - 7rem)")


@override_settings(
    STATIC_ROOT=Path(__file__).parent.parent / "migrations",
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

    @patch("chat.views.get_chat_runtime")
    def test_empty_catalogue_links_to_add_model(self, runtime):
        runtime.return_value.local_model.snapshot.side_effect = (
            ModelSelectionError("No model")
        )
        ChatModel.objects.update(enabled=False)
        self.client.force_login(self.user)

        response = self.client.get(reverse("chat_model_status"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No enabled models")
        self.assertContains(response, reverse("admin:chat_chatmodel_add"))

    @patch("chat.views.get_chat_runtime")
    def test_staff_can_view_and_retry_model(self, runtime):
        model = runtime.return_value.local_model
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

    @patch("chat.views.get_chat_runtime")
    def test_preparing_status_refreshes_without_duplicate_action(self, runtime):
        model = runtime.return_value.local_model
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

    @patch("chat.views.get_chat_runtime")
    def test_staff_can_select_only_an_approved_model(self, runtime):
        model = runtime.return_value.local_model
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

    @patch("chat.views.get_chat_runtime")
    def test_staff_can_download_latest_active_revision(self, runtime):
        model = runtime.return_value.local_model
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

        self.assertEqual(models.count(), 6)
        self.assertFalse(models.exclude(revision="main").exists())
        self.assertFalse(models.exclude(sha256="").exists())
        for model in models:
            validate_model_spec(model)

        qwen3 = models.get(model_id="qwen3-1.7b-ud-q4-k-xl")
        self.assertEqual(qwen3.repository, "unsloth/Qwen3-1.7B-GGUF")
        self.assertEqual(qwen3.filename, "Qwen3-1.7B-UD-Q4_K_XL.gguf")
        self.assertTrue(qwen3.enabled)
