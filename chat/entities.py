"""Resolve public entities through the central polymorphic search index."""

from dataclasses import dataclass

from .domain import EntityKind, EntityMatch, EntityResolution
from .intents import ANIMAL_WORDS
from .matching import AMBIGUITY_MARGIN, phrase_score, same_word, words
from .models import ChatSearchEntry
from .search_index import search_terms
from .search_registry import (
    SEARCHABLE_ENTITIES,
    SearchEntityDefinition,
    definition_for_kind,
    definition_for_model_label,
)


GENERIC_ENTITY_WORDS = frozenset(
    word for phrase in ANIMAL_WORDS for word in words(phrase)
)
ANIMAL_KIND_MATCH_THRESHOLD = 0.95
MAX_AMBIGUOUS_MATCHES = 3


@dataclass(frozen=True)
class _ScoredEntry:
    definition: SearchEntityDefinition
    entry: ChatSearchEntry
    score: float


class EntityResolver:
    """Find exact names first, then conservative fuzzy matches."""

    def resolve_explicit(self, message):
        scored_entries = []
        entries = ChatSearchEntry.objects.select_related("content_type")
        for entry in entries:
            definition = definition_for_model_label(
                f"{entry.content_type.app_label}.{entry.content_type.model}"
            )
            if definition is None:
                continue

            terms = search_terms(entry)
            if not terms:
                continue
            score = self._score(message, definition, entry, terms)
            threshold = (
                ANIMAL_KIND_MATCH_THRESHOLD
                if definition.kind == EntityKind.ANIMAL_KIND
                else self._threshold(terms[0])
            )
            if score < threshold:
                continue
            if (
                definition.kind != EntityKind.ANIMAL_KIND
                and not self._has_distinctive_match(message, terms)
            ):
                continue
            scored_entries.append(_ScoredEntry(
                definition=definition,
                entry=entry,
                score=score,
            ))

        matches = self._public_matches(scored_entries)
        if not matches:
            return EntityResolution()

        matches.sort(key=lambda match: match.score, reverse=True)
        exact = tuple(match for match in matches if match.score == 1.0)
        if exact:
            exact = exact[:MAX_AMBIGUOUS_MATCHES]
            return EntityResolution(
                matches=exact,
                ambiguous=len(exact) > 1,
            )

        top_score = matches[0].score
        closest = tuple(
            match
            for match in matches
            if top_score - match.score <= AMBIGUITY_MARGIN
        )[:MAX_AMBIGUOUS_MATCHES]
        return EntityResolution(
            matches=closest,
            ambiguous=len(closest) > 1,
        )

    def resolve_state(self, state):
        if not state.has_entity:
            return EntityResolution()
        match = self._get(state.entity_kind, state.entity_id)
        return EntityResolution(matches=(match,)) if match else EntityResolution()

    def resolve_page(self, context):
        for definition in SEARCHABLE_ENTITIES:
            for id_field, name_field in definition.page_context_fields:
                raw_id = context.get(id_field)
                if raw_id and raw_id.isdigit():
                    match = self._get(definition.kind, int(raw_id))
                    if match:
                        return EntityResolution(matches=(match,))

                name = context.get(name_field)
                if name:
                    resolution = self.resolve_explicit(name)
                    matches = tuple(
                        match
                        for match in resolution.matches
                        if match.kind == definition.kind
                    )
                    if matches:
                        return EntityResolution(matches=matches[:1])
        return EntityResolution()

    @staticmethod
    def _threshold(term):
        length = len(term.strip())
        if length <= 4:
            return 0.87
        if length <= 7:
            return 0.84
        return 0.78

    @staticmethod
    def _score(message, definition, entry, terms):
        score = max(phrase_score(message, term) for term in terms)
        if definition.kind != EntityKind.CERTIFICATION:
            return score

        # Certification codes are commonly only two characters long. The
        # generic matcher deliberately ignores such short words inside longer
        # sentences, so accept an exact code token without weakening matching
        # for every other entity type.
        query_words = set(words(message))
        has_exact_short_code = False
        for term in entry.canonical_terms:
            term_words = words(term)
            if (
                len(term_words) == 1
                and len(term_words[0]) < 3
                and term_words[0] in query_words
            ):
                has_exact_short_code = True
                break
        return max(score, 0.99) if has_exact_short_code else score

    @staticmethod
    def _has_distinctive_match(message, terms):
        """Require a real name token rather than only a generic animal word."""
        query_words = words(message)
        for term in terms:
            distinctive_words = [
                word for word in words(term)
                if word not in GENERIC_ENTITY_WORDS
            ]
            if any(
                same_word(query_word, candidate_word)
                for query_word in query_words
                for candidate_word in distinctive_words
            ):
                return True
        return False

    @staticmethod
    def _get(kind, entity_id):
        definition = definition_for_kind(kind)
        if definition is None:
            return None
        instance = definition.public_queryset().filter(pk=entity_id).first()
        if instance is None:
            return None
        return EntityMatch(kind=kind, instance=instance, score=1.0)

    @staticmethod
    def _public_matches(scored_entries):
        """Bulk-load only candidates allowed by each public catalogue query."""
        entries_by_definition = {}
        for scored in scored_entries:
            entries_by_definition.setdefault(scored.definition, []).append(scored)

        matches = []
        for definition, definition_entries in entries_by_definition.items():
            object_ids = [scored.entry.object_id for scored in definition_entries]
            instances = definition.public_queryset().filter(
                pk__in=object_ids,
            ).in_bulk()
            for scored in definition_entries:
                instance = instances.get(scored.entry.object_id)
                if instance is not None:
                    matches.append(EntityMatch(
                        kind=definition.kind,
                        instance=instance,
                        score=scored.score,
                    ))
        return matches
