"""Tests for admin-reviewed chat alias suggestions."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from breeding.models import Animal, AnimalKind, Breed, Certification, Litter
from frontoffice.models import FrequentlyAskedQuestion

from .alias_suggestions import (
    AliasSuggestionContext,
    AliasSuggestionError,
    AliasSuggestionService,
    LocalAliasGenerator,
    _parse_suggestions,
    build_alias_context,
)
from .assistant import ModelPreparing


class StaticAliasGenerator:
    def __init__(self, suggestions):
        self.raw_response = json.dumps(suggestions, ensure_ascii=False)
        self.context = None

    def generate(self, context):
        self.context = context
        return self.raw_response


class AliasParsingTests(SimpleTestCase):
    def test_parser_accepts_json_code_fence_but_requires_string_list(self):
        self.assertEqual(
            _parse_suggestions('```json\n["Bela", "Bellinha"]\n```'),
            ["Bela", "Bellinha"],
        )

        for invalid in ("not json", '{"aliases": []}', '["valid", 2]'):
            with self.subTest(invalid=invalid), self.assertRaises(
                AliasSuggestionError
            ):
                _parse_suggestions(invalid)

    @override_settings(
        LANGUAGES=(("en", "English"), ("pt", "Portuguese")),
        CHAT_MAX_OUTPUT_TOKENS=192,
    )
    @patch("chat.alias_suggestions.local_model.inference")
    def test_generator_uses_shared_inference_slot_and_bounded_context(
        self,
        inference,
    ):
        model = inference.return_value.__enter__.return_value
        model.create_chat_completion.return_value = {
            "choices": [{"message": {"content": '["Bela"]'}}]
        }
        context = AliasSuggestionContext(
            entity_type="animal",
            public_context={"name": "Bella"},
            canonical_terms=("Bella",),
            existing_aliases=(),
            generation_goal="Suggest name variants.",
        )

        result = LocalAliasGenerator().generate(context)

        self.assertEqual(result, '["Bela"]')
        inference.assert_called_once_with()
        call = model.create_chat_completion.call_args
        self.assertEqual(call.kwargs["max_tokens"], 192)
        self.assertNotIn("history", call.kwargs["messages"][1]["content"])
        self.assertIn('"name": "Bella"', call.kwargs["messages"][1]["content"])


class AliasSuggestionServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kind = AnimalKind.objects.create(name="Dog")
        cls.breed = Breed.objects.create(
            kind=cls.kind,
            name="German Shepherd",
            name_en="German Shepherd",
            name_pt="Pastor Alemão",
            cover="breeds/test.jpg",
        )
        cls.dog = Animal.objects.create(
            breed=cls.breed,
            name="Bella",
            description="Private implementation detail",
            chat_search_aliases="Bela",
            birth_date=date(2024, 1, 1),
            price_in_euros="1300.00",
        )

    def test_animal_context_contains_only_public_alias_facts(self):
        context = build_alias_context(self.dog)

        self.assertEqual(context.entity_type, "animal")
        self.assertEqual(context.public_context["name"], "Bella")
        self.assertIn("Pastor Alemão", context.public_context["breed_names"].values())
        serialized = json.dumps(context.public_context)
        self.assertNotIn("Private implementation detail", serialized)
        self.assertNotIn("1300", serialized)
        self.assertNotIn("birth", serialized)

    def test_service_filters_existing_canonical_generic_and_ambiguous_aliases(self):
        Animal.objects.create(
            breed=self.breed,
            name="Luna",
            chat_search_aliases="Luninha",
            birth_date=date(2024, 2, 1),
        )
        generator = StaticAliasGenerator([
            "Bella",
            "Bela",
            "Available dog",
            "Luna",
            "Bellinha",
            "  Bella da casa  ",
            "x" * 121,
        ])

        suggestions = AliasSuggestionService(generator).suggest(self.dog)

        self.assertEqual(suggestions, ("Bellinha", "Bella da casa"))
        self.assertEqual(generator.context.entity_type, "animal")

    def test_animal_kind_context_generates_dynamic_vocabulary(self):
        self.kind.chat_search_aliases = "Dogs\nPuppies"
        generator = StaticAliasGenerator([
            "Dog",
            "Dogs",
            "Puppies",
            "Cães",
            "Chiens",
        ])

        suggestions = AliasSuggestionService(generator).suggest(self.kind)

        self.assertEqual(suggestions, ("Cães", "Chiens"))
        self.assertEqual(generator.context.entity_type, "animal_kind")

    def test_litter_alias_must_keep_a_distinctive_name_or_parent_term(self):
        litter = Litter.objects.create(
            breed=self.breed,
            name="Bella and Max 2026",
            chat_search_aliases="",
            expected_birth_date=date(2026, 10, 1),
        )
        generator = StaticAliasGenerator([
            "German Shepherd litter",
            "Ninhada Bella e Max",
            "Camada 2026 de Pastor Alemão",
        ])

        suggestions = AliasSuggestionService(generator).suggest(litter)

        self.assertEqual(
            suggestions,
            ("Ninhada Bella e Max", "Camada 2026 de Pastor Alemão"),
        )

    def test_faq_suggestions_are_paraphrases_not_existing_search_terms(self):
        faq = FrequentlyAskedQuestion.objects.create(
            question="Can I reserve a puppy?",
            question_en="Can I reserve a puppy?",
            question_pt="Posso reservar um cachorro?",
            answer="Contact us to arrange a reservation.",
            answer_en="Contact us to arrange a reservation.",
            answer_pt="Contacte-nos para organizar uma reserva.",
            chat_search_aliases="How do reservations work?",
        )
        FrequentlyAskedQuestion.objects.create(
            question="Is delivery available?",
            answer="Contact us about delivery.",
            chat_search_aliases="Do you deliver puppies?",
        )
        generator = StaticAliasGenerator([
            "Can I reserve a puppy?",
            "How do reservations work?",
            "Do you deliver puppies?",
            "Can I book a dog?",
            "Posso reservar um cão?",
        ])

        suggestions = AliasSuggestionService(generator).suggest(faq)

        self.assertEqual(
            suggestions,
            ("Can I book a dog?", "Posso reservar um cão?"),
        )

    def test_certification_suggestions_keep_the_code_or_distinctive_name(self):
        certification = Certification.objects.create(
            code="WB",
            name="Wesensbeurteilung",
            description="A public description that aliases do not need.",
        )
        generator = StaticAliasGenerator([
            "Temperament test",
            "O que é o WB?",
            "Exame Wesensbeurteilng",
        ])

        suggestions = AliasSuggestionService(generator).suggest(certification)

        self.assertEqual(
            suggestions,
            ("O que é o WB?", "Exame Wesensbeurteilng"),
        )
        self.assertEqual(generator.context.entity_type, "certification")
        self.assertNotIn(
            "description",
            json.dumps(generator.context.public_context),
        )

    def test_unsupported_model_is_rejected_explicitly(self):
        with self.assertRaises(TypeError):
            build_alias_context(object())


@override_settings(
    STATIC_ROOT=Path(__file__).parent / "migrations",
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    },
)
class AliasSuggestionAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = get_user_model().objects.create_user(
            username="alias-admin",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        cls.staff_user = get_user_model().objects.create_user(
            username="alias-staff",
            password="password",
            is_staff=True,
        )
        kind = AnimalKind.objects.create(name="Dog")
        breed = Breed.objects.create(
            kind=kind,
            name="German Shepherd",
            cover="breeds/test.jpg",
        )
        cls.dog = Animal.objects.create(
            breed=breed,
            name="Bella",
            chat_search_aliases="Bela",
            birth_date=date(2024, 1, 1),
        )
        cls.litter = Litter.objects.create(
            breed=breed,
            name="Bella and Max 2026",
            expected_birth_date=date(2026, 10, 1),
        )
        cls.faq = FrequentlyAskedQuestion.objects.create(
            question="Can I reserve a puppy?",
            answer="Contact us to arrange a reservation.",
        )
        cls.certification = Certification.objects.create(
            code="WB",
            name="Wesensbeurteilung",
        )

    @property
    def suggestion_url(self):
        return reverse(
            "admin:breeding_animal_chat_alias_suggestions",
            args=(self.dog.pk,),
        )

    def test_change_form_shows_reviewed_suggestion_control(self):
        self.client.force_login(self.admin_user)

        change_views = (
            (
                "admin:breeding_animalkind_change",
                "admin:breeding_animalkind_chat_alias_suggestions",
                self.dog.breed.kind_id,
            ),
            (
                "admin:breeding_animal_change",
                "admin:breeding_animal_chat_alias_suggestions",
                self.dog.pk,
            ),
            (
                "admin:breeding_litter_change",
                "admin:breeding_litter_chat_alias_suggestions",
                self.litter.pk,
            ),
            (
                "admin:breeding_breed_change",
                "admin:breeding_breed_chat_alias_suggestions",
                self.dog.breed_id,
            ),
            (
                "admin:breeding_certification_change",
                "admin:breeding_certification_chat_alias_suggestions",
                self.certification.pk,
            ),
            (
                "admin:frontoffice_frequentlyaskedquestion_change",
                (
                    "admin:frontoffice_frequentlyaskedquestion_"
                    "chat_alias_suggestions"
                ),
                self.faq.pk,
            ),
        )
        for view_name, suggestion_view_name, object_id in change_views:
            with self.subTest(view_name=view_name):
                response = self.client.get(
                    reverse(view_name, args=(object_id,))
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Generate with AI")
                self.assertContains(response, "js/admin/chat_aliases.js")
                self.assertContains(
                    response,
                    reverse(suggestion_view_name, args=(object_id,)),
                )

        self.assertContains(response, "data-chat-alias-generate")

    def test_add_form_requires_saving_before_generation(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("admin:breeding_animal_add"))

        self.assertContains(response, 'name="chat_search_aliases"')
        self.assertNotContains(response, "data-chat-alias-generate")

    def test_suggestion_control_is_translated_in_portuguese_admin(self):
        self.client.force_login(self.admin_user)

        with translation.override("pt"):
            response = self.client.get(
                reverse(
                    "admin:breeding_animal_change",
                    args=(self.dog.pk,),
                )
            )

        self.assertContains(response, "Gerar por IA")
        self.assertContains(response, "Aliases de pesquisa do chat")

    def test_post_returns_suggestions_without_persisting_them(self):
        self.client.force_login(self.admin_user)

        with patch(
            "chat.admin_aliases.alias_suggestion_service.suggest",
            return_value=("Bellinha",),
        ):
            response = self.client.post(self.suggestion_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["suggestions"], ["Bellinha"])
        self.dog.refresh_from_db()
        self.assertEqual(self.dog.chat_search_aliases, "Bela")

    def test_endpoint_requires_post_and_object_change_permission(self):
        self.client.force_login(self.admin_user)
        self.assertEqual(self.client.get(self.suggestion_url).status_code, 405)

        self.client.force_login(self.staff_user)
        self.assertEqual(self.client.post(self.suggestion_url).status_code, 403)

    def test_endpoint_is_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin_user)

        response = client.post(self.suggestion_url)

        self.assertEqual(response.status_code, 403)

    def test_model_preparation_failure_is_safe_and_actionable(self):
        self.client.force_login(self.admin_user)

        with patch(
            "chat.admin_aliases.alias_suggestion_service.suggest",
            side_effect=ModelPreparing("loading"),
        ):
            response = self.client.post(self.suggestion_url)

        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.json())
        self.dog.refresh_from_db()
        self.assertEqual(self.dog.chat_search_aliases, "Bela")
