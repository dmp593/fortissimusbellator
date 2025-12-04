import polib

from django.core.management.base import BaseCommand
from django.conf import settings

from fortissimusbellator import translator


class Command(BaseCommand):
    """
        Automatically translate missing entries in .po files
        using Google Translate.
    """

    help = (
        "Populate missing translations in locale .po files "
        "via Google Translate"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--locales",
            nargs="*",
            help=(
                "Specific locale folder names to process. "
                "Defaults to every locale except the source language."
            ),
        )

        parser.add_argument(
            "--source",
            default="en",
            help="Language code of the source strings (default: en).",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Preview the translations without writing any changes to disk."
            ),
        )
        
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help=(
                "Re-translate entries even when a translation already exists."
            ),
        )

        parser.add_argument(
            "--provider",
            default="google",
            help=(
                "Translation provider to use (default: google). "
                "Options: 'deepl', 'google'."
            ),
        )

    def handle(self, *args, **options):
        locales = options["locales"]
        source_lang = options["source"]
        dry_run = options["dry_run"]
        overwrite = options["overwrite"]
        provider = options["provider"]

        for locale in locales or [loc[0] for loc in settings.LANGUAGES if loc[0] != source_lang]:
            po_path = f"locale/{locale}/LC_MESSAGES/django.po"
            po_file = polib.pofile(po_path)

            updated = False

            po_entries = (
                po_file.untranslated_entries()
                if not overwrite
                else po_file
            )

            for entry in po_entries:
                if entry.msgstr and not overwrite:
                    continue

                try:
                    translated_text = translator.translate(
                        text=entry.msgid,
                        source_lang=source_lang,
                        target_lang=locale,
                        provider=provider,
                    )

                    entry.msgstr = translated_text

                    updated = True

                    self.stdout.write(
                        f"Translated [{locale}]: "
                        f"'{entry.msgid}' -> '{translated_text}'"
                    )
                except Exception as e:
                    self.stderr.write(
                        f"Error translating '{entry.msgid}' to '{locale}': {e}"
                    )

            if updated and not dry_run:
                po_file.save()
                self.stdout.write(f"Saved updated translations to {po_path}")
            elif dry_run:
                self.stdout.write(f"Dry run: No changes saved for {po_path}")

        self.stdout.write("Translation process completed.")
