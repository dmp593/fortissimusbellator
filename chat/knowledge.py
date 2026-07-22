"""Build a compact knowledge snapshot for the one LLM fallback."""

import logging

from django.conf import settings
from django.db import DatabaseError

from .business import (
    ADDRESS,
    BUSINESS_NAME,
    CONTACT_EMAIL,
    CONTACT_PHONES,
    WEBSITE,
)
from .catalog import public_breeds
from .matching import phrase_score, same_word, search_aliases, words


logger = logging.getLogger(__name__)

BUSINESS_FACTS = f"""Business:
- Name: {BUSINESS_NAME}
- Professional dog breeder in Leiria, Portugal
- Contact phones: {CONTACT_PHONES}
- Email: {CONTACT_EMAIL}
- Address: {ADDRESS}
- Website: {WEBSITE}"""

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "can", "do", "for",
    "how", "i", "in", "is", "it", "of", "on", "or", "the", "to",
    "what", "when", "where", "which", "who", "with", "you", "your",
    "as", "como", "de", "do", "e", "em", "o", "os", "para", "por",
    "que", "sao", "um", "uma", "qual", "quanto", "voces", "vosso",
    "vossos", "vossa", "vossas",
}


def build_knowledge(query, page_context):
    """Return only business, page, and strongly relevant FAQ facts."""
    sections = [_business_facts()]
    page_section = _page_context(page_context)
    if page_section:
        sections.append(page_section)

    try:
        faq_section = _faqs(query)
    except DatabaseError:
        logger.warning("Chat knowledge database query failed", exc_info=True)
    else:
        if faq_section:
            sections.append(faq_section)

    return "\n\n".join(sections)[:settings.CHAT_KNOWLEDGE_MAX_CHARS]


def _business_facts():
    """Add the current public breed catalogue without hard-coded names."""
    try:
        breed_names = [
            str(breed)
            for breed in public_breeds()[:settings.CHAT_MAX_KENNEL_ITEMS]
        ]
    except DatabaseError:
        logger.warning("Chat breed catalogue query failed", exc_info=True)
        breed_names = []
    if not breed_names:
        return BUSINESS_FACTS
    return BUSINESS_FACTS + "\n- Public breeds: " + ", ".join(breed_names)


def _page_context(context):
    if not context:
        return ""

    labels = {
        "page_title": "title",
        "page_name": "route",
        "page_type": "page type",
        "dog_name": "dog",
        "litter_name": "litter",
        "breed_name": "breed",
    }
    facts = [
        f"- {labels[key]}: {value}"
        for key, value in context.items()
        if key in labels and value
    ]
    return "Current page:\n" + "\n".join(facts) if facts else ""


def _faqs(query):
    faqs = relevant_faqs(query)
    if not faqs:
        return ""

    lines = ["Relevant FAQs:"]
    for faq in faqs:
        lines.append(
            f"- Q: {faq.question}\n  A: {_one_line(faq.answer, 400)}"
        )
    return "\n".join(lines)


def relevant_faqs(query):
    """Return FAQs whose meaningful words match, including small typos."""
    from frontoffice.models import FrequentlyAskedQuestion

    query_words = _meaningful_words(query)
    if not query_words:
        return []

    ranked = []
    for faq in FrequentlyAskedQuestion.objects.filter(active=True).order_by("order"):
        aliases = search_aliases(faq.chat_search_aliases)
        question_words = _meaningful_words(" ".join((faq.question, *aliases)))
        matched_words = [
            query_word for query_word in query_words
            if any(same_word(query_word, question_word)
                   for question_word in question_words)
        ]
        score = len(matched_words)
        has_distinctive_match = any(len(word) >= 5 for word in matched_words)
        if score >= 2 or has_distinctive_match:
            ranked.append((score, faq))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [faq for _score, faq in ranked[:settings.CHAT_MAX_FAQS]]


def matching_faq(query):
    """Return only a near-verbatim FAQ match safe for a direct answer."""
    faqs = relevant_faqs(query)
    ranked = sorted(
        (
            (
                max(
                    phrase_score(query, candidate)
                    for candidate in (
                        faq.question,
                        *search_aliases(faq.chat_search_aliases),
                    )
                ),
                faq,
            )
            for faq in faqs
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.82:
        return None
    return ranked[0][1]


def _meaningful_words(value):
    return {
        word for word in words(value)
        if len(word) > 1 and word not in STOP_WORDS
    }


def _one_line(value, limit):
    return " ".join(str(value).split())[:limit]
