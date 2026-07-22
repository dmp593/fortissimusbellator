"""Small value objects shared by the chat pipeline."""

from dataclasses import dataclass, field
from enum import StrEnum


class EntityKind(StrEnum):
    DOG = "dog"
    LITTER = "litter"
    BREED = "breed"


@dataclass(frozen=True)
class ConversationState:
    """Session-only reference to the last unambiguous entity."""

    entity_kind: EntityKind | None = None
    entity_id: int | None = None
    entity_name: str = ""

    @property
    def has_entity(self):
        return self.entity_kind is not None and self.entity_id is not None

    def as_dict(self):
        if not self.has_entity:
            return {}
        return {
            "entity_kind": self.entity_kind.value,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
        }


@dataclass(frozen=True)
class ChatRequest:
    message: str
    history: list[dict]
    language: str
    page_context: dict[str, str]
    requested_intent: str | None = None
    state: ConversationState = field(default_factory=ConversationState)


@dataclass(frozen=True)
class ChatReply:
    text: str
    state: ConversationState = field(default_factory=ConversationState)


@dataclass(frozen=True)
class EntityMatch:
    kind: EntityKind
    instance: object
    score: float

    @property
    def name(self):
        return str(self.instance)

    def as_state(self):
        return ConversationState(
            entity_kind=self.kind,
            entity_id=self.instance.pk,
            entity_name=self.name,
        )


@dataclass(frozen=True)
class EntityResolution:
    matches: tuple[EntityMatch, ...] = ()
    ambiguous: bool = False


@dataclass(frozen=True)
class QueryAnalysis:
    intents: frozenset[str]
    entities: EntityResolution = field(default_factory=EntityResolution)
    used_conversation_state: bool = False

