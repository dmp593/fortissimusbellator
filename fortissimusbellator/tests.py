from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from django.db.utils import OperationalError
from django.test import RequestFactory, SimpleTestCase, override_settings

from fortissimusbellator.admin import FieldTranslatorAdmin
from fortissimusbellator.health import liveness, readiness
from fortissimusbellator.parsers import page_size
from fortissimusbellator.translator import translate


class HealthCheckTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get('/health/live/')

    def test_liveness_does_not_depend_on_external_services(self):
        response = liveness(self.request)

        self.assertEqual(response.status_code, 200)

    def test_readiness_reports_database_failure(self):
        with patch('fortissimusbellator.health.connection') as connection:
            connection.cursor.side_effect = OperationalError(
                'database unavailable'
            )
            response = readiness(self.request)

        self.assertEqual(response.status_code, 503)


class ParserTests(SimpleTestCase):
    def test_page_size_is_bounded(self):
        self.assertEqual(page_size(None), 12)
        self.assertEqual(page_size('0'), 1)
        self.assertEqual(page_size('-1'), 1)
        self.assertEqual(page_size('24'), 24)
        self.assertEqual(page_size('100000'), 48)


class TranslationAdapterTests(SimpleTestCase):
    @patch("googletrans.Translator")
    def test_google_client_is_scoped_and_closed(self, translator_factory):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.translate = AsyncMock(
            return_value=SimpleNamespace(text="Olá")
        )
        translator_factory.return_value = client

        result = translate(
            "Hello",
            source_lang="en",
            target_lang="pt",
            provider="google",
        )

        self.assertEqual(result, "Olá")
        client.translate.assert_awaited_once_with(
            "Hello",
            src="en",
            dest="pt",
        )
        client.__aexit__.assert_awaited_once()

    @override_settings(DEEPL_AUTH_KEY="test-key")
    @patch("deepl.DeepLClient")
    def test_deepl_result_is_returned_and_client_is_closed(
        self,
        client_class,
    ):
        client = client_class.return_value
        client.translate_text.return_value = SimpleNamespace(text="Olá")

        result = translate(
            "Hello",
            source_lang="en",
            target_lang="pt-pt",
            provider="deepl",
        )

        self.assertEqual(result, "Olá")
        client.translate_text.assert_called_once_with(
            "Hello",
            source_lang="EN",
            target_lang="PT-PT",
        )
        client.close.assert_called_once_with()

    def test_unsupported_provider_fails_without_external_call(self):
        with self.assertLogs(
            "fortissimusbellator.translator",
            level="ERROR",
        ):
            result = translate("Hello", provider="unknown")

        self.assertIsNone(result)


class FieldTranslatorAdminTests(SimpleTestCase):
    def setUp(self):
        self.model_admin = object.__new__(FieldTranslatorAdmin)
        self.cleaned_data = {
            "description_pt": "",
            "description_en": "English",
            "description_es": "",
            "description_fr": "",
            "description_de": "",
            "description_it": "",
        }

    def test_missing_fields_are_filled_without_overwriting_source(self):
        obj = SimpleNamespace()
        translations = {
            "pt-pt": "Português",
            "es": "Español",
            "fr": "Français",
            "de": "Deutsch",
            "it": "Italiano",
        }

        with patch.object(
            self.model_admin,
            "_translate",
            side_effect=lambda _text, **values: translations[
                values["target_lang"]
            ],
        ):
            self.model_admin._fill_missing_translations(
                obj,
                "description",
                self.cleaned_data,
            )

        self.assertEqual(obj.description_pt, "Português")
        self.assertEqual(obj.description_es, "Español")
        self.assertEqual(obj.description_fr, "Français")
        self.assertEqual(obj.description_de, "Deutsch")
        self.assertEqual(obj.description_it, "Italiano")
        self.assertFalse(hasattr(obj, "description_en"))

    def test_portuguese_falls_back_to_google_when_deepl_fails(self):
        obj = SimpleNamespace()

        def translate_text(_text, **values):
            if values.get("provider", "google") == "deepl":
                return None
            return "Português" if values["target_lang"] == "pt" else "value"

        with patch.object(
            self.model_admin,
            "_translate",
            side_effect=translate_text,
        ) as translator:
            self.model_admin._fill_missing_translations(
                obj,
                "description",
                self.cleaned_data,
            )

        self.assertEqual(obj.description_pt, "Português")
        portuguese_calls = [
            call.kwargs
            for call in translator.call_args_list
            if call.kwargs["target_lang"].startswith("pt")
        ]
        self.assertEqual(
            [call.get("provider", "google") for call in portuguese_calls],
            ["deepl", "google"],
        )

    def test_existing_translation_is_not_overwritten(self):
        obj = SimpleNamespace()
        self.cleaned_data["description_es"] = "Ya existe"

        with patch.object(
            self.model_admin,
            "_translate",
            return_value="translated",
        ):
            self.model_admin._fill_missing_translations(
                obj,
                "description",
                self.cleaned_data,
            )

        self.assertFalse(hasattr(obj, "description_es"))
