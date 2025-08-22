from django.core.management.base import BaseCommand, CommandError
import argostranslate.package


class Command(BaseCommand):
    help = "Install an Argos Translate language model (e.g., en → pt)."

    def add_arguments(self, parser):
        parser.add_argument(
            "source_lang", type=str, help="Source language code (e.g., en)"
        )

        parser.add_argument(
            "target_lang", type=str, help="Target language code (e.g., pt)"
        )

    def handle(self, *args, **options):
        source_lang = options["source_lang"]
        target_lang = options["target_lang"]

        self.stdout.write(
            self.style.NOTICE("Updating package index…")
        )

        argostranslate.package.update_package_index()

        available_packages = argostranslate.package.get_available_packages()

        package = next(
            (
                p for p in available_packages
                if p.from_code == source_lang and p.to_code == target_lang
            ),
            None,
        )

        if not package:
            raise CommandError(
                f"No Argos model found for {source_lang} → {target_lang}"
            )

        self.stdout.write(
            self.style.NOTICE(
                f"Downloading and installing {source_lang} → {target_lang}…"
            )
        )

        argostranslate.package.install_from_path(package.download())

        self.stdout.write(
            self.style.SUCCESS(
                f"Installed Argos model: {source_lang} → {target_lang}"
            )
        )
