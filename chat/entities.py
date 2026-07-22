"""Resolve public catalogue entities mentioned in a user message."""

from dataclasses import dataclass

from .catalog import current_litters, public_breeds, public_dogs
from .domain import EntityKind, EntityMatch, EntityResolution
from .matching import AMBIGUITY_MARGIN, phrase_score


@dataclass(frozen=True)
class _Candidate:
    kind: EntityKind
    instance: object
    aliases: tuple[str, ...]


class EntityResolver:
    """Find exact names first, then conservative fuzzy matches."""

    def resolve_explicit(self, message):
        scored = []
        for candidate in self._candidates():
            score = max(
                phrase_score(message, alias) for alias in candidate.aliases
            )
            if score >= self._threshold(candidate.aliases[0]):
                scored.append(EntityMatch(
                    kind=candidate.kind,
                    instance=candidate.instance,
                    score=score,
                ))

        if not scored:
            return EntityResolution()

        scored.sort(key=lambda match: match.score, reverse=True)
        exact = tuple(match for match in scored if match.score >= 0.99)
        if exact:
            return EntityResolution(matches=exact)

        top_score = scored[0].score
        closest = tuple(
            match for match in scored
            if top_score - match.score <= AMBIGUITY_MARGIN
        )
        return EntityResolution(
            matches=closest[:3],
            ambiguous=len(closest) > 1,
        )

    def resolve_state(self, state):
        if not state.has_entity:
            return EntityResolution()
        match = self._get(state.entity_kind, state.entity_id)
        return EntityResolution(matches=(match,)) if match else EntityResolution()

    def resolve_page(self, context):
        for kind, id_field, name_field in (
            (EntityKind.DOG, "dog_id", "dog_name"),
            (EntityKind.LITTER, "litter_id", "litter_name"),
            (EntityKind.BREED, "breed_id", "breed_name"),
        ):
            raw_id = context.get(id_field)
            if raw_id and raw_id.isdigit():
                match = self._get(kind, int(raw_id))
                if match:
                    return EntityResolution(matches=(match,))

            name = context.get(name_field)
            if name:
                resolution = self.resolve_explicit(name)
                matches = tuple(
                    match for match in resolution.matches
                    if match.kind == kind
                )
                if matches:
                    return EntityResolution(matches=matches[:1])
        return EntityResolution()

    @staticmethod
    def _threshold(alias):
        length = len(alias.strip())
        if length <= 4:
            return 0.87
        if length <= 7:
            return 0.84
        return 0.78

    @staticmethod
    def _get(kind, entity_id):
        querysets = {
            EntityKind.DOG: public_dogs,
            EntityKind.LITTER: current_litters,
            EntityKind.BREED: public_breeds,
        }
        instance = querysets[kind]().filter(pk=entity_id).first()
        if instance is None:
            return None
        return EntityMatch(kind=kind, instance=instance, score=1.0)

    @staticmethod
    def _candidates():
        candidates = []
        for dog in public_dogs():
            candidates.append(_Candidate(
                kind=EntityKind.DOG,
                instance=dog,
                aliases=(dog.name,),
            ))
        for litter in current_litters():
            candidates.append(_Candidate(
                kind=EntityKind.LITTER,
                instance=litter,
                aliases=(litter.name,),
            ))
        for breed in public_breeds():
            candidates.append(_Candidate(
                kind=EntityKind.BREED,
                instance=breed,
                aliases=tuple(dict.fromkeys((str(breed), breed.name))),
            ))
        return candidates

