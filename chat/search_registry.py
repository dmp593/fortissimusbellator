"""Explicit registry of Django models exposed to global chat search."""

from dataclasses import dataclass
from typing import Callable

from django.apps import apps
from django.conf import settings

from .catalog import (
    current_litters,
    public_animal_kinds,
    public_animals,
    public_breeds,
    public_certifications,
)
from .domain import EntityKind
from .matching import normalize_text


CanonicalTermsFactory = Callable[[object], tuple[str, ...]]
QuerySetFactory = Callable[[], object]


@dataclass(frozen=True)
class SearchEntityDefinition:
    """Configuration required to index and safely reload one model type."""

    kind: EntityKind
    model_label: str
    public_queryset_factory: QuerySetFactory
    canonical_terms_factory: CanonicalTermsFactory
    page_context_fields: tuple[tuple[str, str], ...] = ()

    @property
    def model(self):
        return apps.get_model(self.model_label)

    def public_queryset(self):
        return self.public_queryset_factory()

    def canonical_terms(self, instance):
        return self.canonical_terms_factory(instance)


def _public_faqs():
    from frontoffice.models import FrequentlyAskedQuestion

    return FrequentlyAskedQuestion.objects.filter(active=True).order_by("order")


def _animal_terms(instance):
    return _unique_terms((instance.name,))


def _animal_kind_terms(instance):
    return _translated_terms(instance, "name")


def _breed_terms(instance):
    return _unique_terms((*_translated_terms(instance, "name"), str(instance)))


def _litter_terms(instance):
    return _unique_terms((instance.name,))


def _certification_terms(instance):
    return _unique_terms((instance.code, instance.name))


def _faq_terms(instance):
    return _translated_terms(instance, "question")


SEARCHABLE_ENTITIES = (
    SearchEntityDefinition(
        kind=EntityKind.ANIMAL,
        model_label="breeding.Animal",
        public_queryset_factory=public_animals,
        canonical_terms_factory=_animal_terms,
        page_context_fields=(
            ("animal_id", "animal_name"),
            # Compatibility with pages cached before Animal replaced Dog.
            ("dog_id", "dog_name"),
        ),
    ),
    SearchEntityDefinition(
        kind=EntityKind.ANIMAL_KIND,
        model_label="breeding.AnimalKind",
        public_queryset_factory=public_animal_kinds,
        canonical_terms_factory=_animal_kind_terms,
    ),
    SearchEntityDefinition(
        kind=EntityKind.LITTER,
        model_label="breeding.Litter",
        public_queryset_factory=current_litters,
        canonical_terms_factory=_litter_terms,
        page_context_fields=(("litter_id", "litter_name"),),
    ),
    SearchEntityDefinition(
        kind=EntityKind.BREED,
        model_label="breeding.Breed",
        public_queryset_factory=public_breeds,
        canonical_terms_factory=_breed_terms,
        page_context_fields=(("breed_id", "breed_name"),),
    ),
    SearchEntityDefinition(
        kind=EntityKind.CERTIFICATION,
        model_label="breeding.Certification",
        public_queryset_factory=public_certifications,
        canonical_terms_factory=_certification_terms,
        page_context_fields=(
            ("certification_id", "certification_name"),
        ),
    ),
    SearchEntityDefinition(
        kind=EntityKind.FAQ,
        model_label="frontoffice.FrequentlyAskedQuestion",
        public_queryset_factory=_public_faqs,
        canonical_terms_factory=_faq_terms,
    ),
)

_BY_KIND = {definition.kind: definition for definition in SEARCHABLE_ENTITIES}
_BY_MODEL_LABEL = {
    definition.model_label.lower(): definition
    for definition in SEARCHABLE_ENTITIES
}


def definition_for_kind(kind):
    return _BY_KIND.get(kind)


def definition_for_model_label(model_label):
    return _BY_MODEL_LABEL.get(str(model_label).lower())


def definition_for_instance(instance):
    return definition_for_model_label(instance._meta.label_lower)


def _translated_terms(instance, field_name):
    values = []
    for language_code, _name in settings.LANGUAGES:
        translated_name = f"{field_name}_{language_code}"
        value = getattr(instance, translated_name, "")
        if value:
            values.append(value)
    values.append(getattr(instance, field_name, ""))
    return _unique_terms(values)


def _unique_terms(values):
    terms = []
    seen = set()
    for value in values:
        value = str(value or "").strip()
        normalized = normalize_text(value)
        if value and normalized not in seen:
            terms.append(value)
            seen.add(normalized)
    return tuple(terms)
