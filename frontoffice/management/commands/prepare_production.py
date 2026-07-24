from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


def default_fixture_labels() -> tuple[str, ...]:
    return (
        'animalskinds',
        'breeds',
        'certifications',
        'faqs',
        'blog/categories',
        'quiz/quiz',
        'chat_models',
    )


def fixture_labels(raw_labels: list[str] | None) -> tuple[str, ...] | None:
    if raw_labels is None:
        return None

    labels = tuple(
        label.strip()
        for raw_label in raw_labels
        for label in raw_label.split(',')
        if label.strip()
    )
    return labels or default_fixture_labels()


class Command(BaseCommand):
    help = (
        'Apply migrations, compile translations, collect static files, and '
        'optionally load production-safe fixtures.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--loaddata',
            nargs='*',
            default=None,
            metavar='FIXTURE',
            help=(
                'Load the specified fixture labels. Separate labels with '
                'spaces or commas. When passed without labels, load the '
                'production-safe defaults.'
            ),
        )

    def handle(self, *args, **options):
        verbosity = options['verbosity']
        fixtures = fixture_labels(options['loaddata'])

        self._run_command(
            'migrate',
            interactive=False,
            verbosity=verbosity,
        )
        self._run_command(
            'compilemessages',
            locale=[code for code, _name in settings.LANGUAGES],
            ignore_patterns=[
                '.git',
                '.models',
                '.venv',
                'media',
                'node_modules',
                'static',
            ],
            verbosity=verbosity,
        )
        self._run_command(
            'collectstatic',
            interactive=False,
            verbosity=verbosity,
        )

        if fixtures is not None:
            self._run_command(
                'loaddata',
                *fixtures,
                verbosity=verbosity,
            )
            self._run_command(
                'rebuild_chat_search_index',
                verbosity=verbosity,
            )

        self.stdout.write(
            self.style.SUCCESS('Production preparation completed.'),
        )

    def _run_command(self, name, *args, **options):
        self.stdout.write(f'Running {name}...')
        call_command(
            name,
            *args,
            stdout=self.stdout,
            stderr=self.stderr,
            **options,
        )
