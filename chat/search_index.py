"""Read and maintain the polymorphic search projection used by chat."""

from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from .matching import normalize_text, search_aliases
from .models import ChatSearchEntry
from .search_registry import (
    SEARCHABLE_ENTITIES,
    definition_for_instance,
)


_ALIASES_UNSET = object()


@dataclass(frozen=True)
class IndexRebuildResult:
    indexed: int
    removed: int


def aliases_for(instance):
    """Return the stored aliases for one registered object."""
    if instance.pk is None:
        return ""
    content_type = ContentType.objects.get_for_model(instance)
    return (
        ChatSearchEntry.objects
        .filter(content_type=content_type, object_id=instance.pk)
        .values_list("aliases", flat=True)
        .first()
        or ""
    )


def alias_terms_by_id(model, object_ids):
    """Bulk-read parsed aliases for objects belonging to one model."""
    object_ids = tuple(object_ids)
    if not object_ids:
        return {}
    content_type = ContentType.objects.get_for_model(model)
    rows = ChatSearchEntry.objects.filter(
        content_type=content_type,
        object_id__in=object_ids,
    ).values_list("object_id", "aliases")
    return {
        object_id: search_aliases(aliases)
        for object_id, aliases in rows
    }


def save_aliases(instance, value):
    """Persist reviewed aliases and refresh the object's search metadata."""
    return sync_search_entry(instance, aliases=value)


def sync_search_entry(instance, *, aliases=_ALIASES_UNSET):
    """Create or refresh one projection while preserving reviewed aliases."""
    definition = definition_for_instance(instance)
    if definition is None:
        raise TypeError(
            f"{instance._meta.label} is not registered for chat search."
        )
    if instance.pk is None:
        raise ValueError("A saved object is required for chat search indexing.")

    defaults = {
        "label": str(instance)[:255],
        "canonical_terms": list(definition.canonical_terms(instance)),
        "updated_at": timezone.now(),
    }
    if aliases is not _ALIASES_UNSET:
        defaults["aliases"] = _normalize_aliases(aliases)

    content_type = ContentType.objects.get_for_model(instance)
    entry, _created = ChatSearchEntry.objects.update_or_create(
        content_type=content_type,
        object_id=instance.pk,
        defaults=defaults,
    )
    return entry


def delete_search_entry(instance):
    """Remove the projection belonging to a deleted registered object."""
    if instance.pk is None:
        return
    content_type = ContentType.objects.get_for_model(instance)
    ChatSearchEntry.objects.filter(
        content_type=content_type,
        object_id=instance.pk,
    ).delete()


def search_terms(entry):
    """Return canonical terms and aliases once, in stable order."""
    return tuple(dict.fromkeys((
        *(str(term).strip() for term in entry.canonical_terms if str(term).strip()),
        *search_aliases(entry.aliases),
    )))


def other_search_terms(instance):
    """Return normalized terms belonging to every other indexed object."""
    content_type = ContentType.objects.get_for_model(instance)
    entries = ChatSearchEntry.objects.exclude(
        content_type=content_type,
        object_id=instance.pk,
    )
    return {
        normalize_text(term)
        for entry in entries
        for term in search_terms(entry)
        if term
    }


def rebuild_search_index():
    """Refresh all registered objects and remove their orphaned projections."""
    live_objects = set()
    indexed = 0
    supported_content_type_ids = []

    for definition in SEARCHABLE_ENTITIES:
        content_type = ContentType.objects.get_for_model(definition.model)
        supported_content_type_ids.append(content_type.pk)
        for instance in definition.model._default_manager.all().iterator():
            sync_search_entry(instance)
            live_objects.add((content_type.pk, instance.pk))
            indexed += 1

    removed = 0
    entries = ChatSearchEntry.objects.filter(
        content_type_id__in=supported_content_type_ids,
    ).only("pk", "content_type_id", "object_id")
    orphan_ids = [
        entry.pk
        for entry in entries.iterator()
        if (entry.content_type_id, entry.object_id) not in live_objects
    ]
    if orphan_ids:
        removed, _details = ChatSearchEntry.objects.filter(
            pk__in=orphan_ids,
        ).delete()

    return IndexRebuildResult(indexed=indexed, removed=removed)


def _normalize_aliases(value):
    return "\n".join(search_aliases(value))
