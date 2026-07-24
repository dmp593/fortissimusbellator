from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase

from frontoffice.management.commands.prepare_production import (
    default_fixture_labels,
    fixture_labels,
)


class PrepareProductionCommandTests(SimpleTestCase):
    @patch(
        'frontoffice.management.commands.prepare_production.call_command',
    )
    def test_does_not_load_data_without_flag(self, nested_command):
        call_command('prepare_production', verbosity=0)

        self.assertEqual(
            [call.args[0] for call in nested_command.call_args_list],
            ['migrate', 'compilemessages', 'collectstatic'],
        )
        compile_call = nested_command.call_args_list[1]
        self.assertEqual(
            compile_call.kwargs['locale'],
            ['en', 'pt', 'es', 'fr', 'de', 'it'],
        )
        self.assertIn('.venv', compile_call.kwargs['ignore_patterns'])

    @patch(
        'frontoffice.management.commands.prepare_production.call_command',
    )
    def test_flag_without_labels_loads_safe_defaults(self, nested_command):
        call_command('prepare_production', '--loaddata', verbosity=0)

        loaddata_call = nested_command.call_args_list[3]
        self.assertEqual(loaddata_call.args[0], 'loaddata')
        self.assertEqual(loaddata_call.args[1:], default_fixture_labels())
        self.assertEqual(
            nested_command.call_args_list[4].args[0],
            'rebuild_chat_search_index',
        )
        self.assertNotIn('animals', loaddata_call.args)
        self.assertNotIn('litters', loaddata_call.args)

    @patch(
        'frontoffice.management.commands.prepare_production.call_command',
    )
    def test_accepts_space_and_comma_separated_fixture_labels(
        self,
        nested_command,
    ):
        call_command(
            'prepare_production',
            '--loaddata',
            'faqs,chat_models',
            'quiz/quiz',
            verbosity=0,
        )

        loaddata_call = nested_command.call_args_list[3]
        self.assertEqual(
            loaddata_call.args,
            ('loaddata', 'faqs', 'chat_models', 'quiz/quiz'),
        )

    def test_fixture_parser_distinguishes_absent_and_empty_flag(self):
        self.assertIsNone(fixture_labels(None))
        self.assertEqual(fixture_labels([]), default_fixture_labels())
