from django.core.management.base import BaseCommand

from chat.search_index import rebuild_search_index


class Command(BaseCommand):
    help = "Refresh the polymorphic chat search index without changing aliases."

    def handle(self, *args, **options):
        result = rebuild_search_index()
        self.stdout.write(self.style.SUCCESS(
            "Chat search index rebuilt: "
            f"{result.indexed} indexed, {result.removed} removed."
        ))
