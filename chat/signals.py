"""Keep the chat search projection synchronized with registered entities."""

from django.db.models.signals import post_delete, post_save

from .search_index import delete_search_entry, sync_search_entry
from .search_registry import SEARCHABLE_ENTITIES


def connect_search_index_signals():
    """Connect explicit signals once for every registered model."""
    for definition in SEARCHABLE_ENTITIES:
        model = definition.model
        label = model._meta.label_lower
        post_save.connect(
            _sync_after_save,
            sender=model,
            dispatch_uid=f"chat.search_index.save.{label}",
            weak=False,
        )
        post_delete.connect(
            _delete_after_delete,
            sender=model,
            dispatch_uid=f"chat.search_index.delete.{label}",
            weak=False,
        )


def _sync_after_save(sender, instance, raw=False, **_kwargs):
    if not raw:
        sync_search_entry(instance)


def _delete_after_delete(sender, instance, **_kwargs):
    delete_search_entry(instance)
