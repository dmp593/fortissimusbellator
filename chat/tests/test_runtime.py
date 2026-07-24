"""Tests for process-scoped chat runtime ownership."""

from unittest.mock import Mock, patch

from django.apps import apps
from django.test import SimpleTestCase

from .. import alias_suggestions, assistant, service
from ..assistant import ModelState
from ..runtime import ChatRuntime, get_chat_runtime


class ChatRuntimeTests(SimpleTestCase):
    def test_django_app_config_owns_the_process_runtime(self):
        app_config = apps.get_app_config("chat")

        self.assertIs(get_chat_runtime(), app_config.runtime)

    def test_runtime_starts_model_preparation(self):
        runtime = ChatRuntime(Mock())
        snapshot = Mock(state=ModelState.LOADING, model_id="test-model")

        with patch.object(
            runtime.local_model,
            "prepare",
            return_value=snapshot,
        ) as prepare:
            result = runtime.warm_up()

        self.assertIs(result, snapshot)
        prepare.assert_called_once_with()

    def test_warmup_failure_is_logged_without_stopping_django(self):
        runtime = ChatRuntime(Mock())

        with (
            patch.object(
                runtime.local_model,
                "prepare",
                side_effect=RuntimeError("load failed"),
            ),
            self.assertLogs("chat.runtime", level="ERROR") as logs,
        ):
            result = runtime.warm_up()

        self.assertIsNone(result)
        self.assertIn("chat_model_warmup_failed", " ".join(logs.output))

    def test_runtime_services_are_not_mutable_module_singletons(self):
        forbidden_names = (
            (assistant, "local_model"),
            (assistant, "assistant"),
            (service, "chat_service"),
            (alias_suggestions, "alias_suggestion_service"),
        )

        for module, name in forbidden_names:
            with self.subTest(module=module.__name__, name=name):
                self.assertFalse(hasattr(module, name))
