"""Build a compact knowledge snapshot for the one LLM fallback."""

import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError

from .business import (
    ADDRESS,
    BUSINESS_NAME,
    CONTACT_EMAIL,
    CONTACT_PHONES,
    WEBSITE,
)
from .catalog import (
    public_breeds,
    public_certifications,
    published_posts,
)
from .intents import is_blog_query, is_certification_query
from .matching import (
    normalize_text,
    phrase_score,
    same_word,
    words,
)
from .search_index import alias_terms_by_id


logger = logging.getLogger(__name__)

BUSINESS_FACTS = f"""Business:
- Name: {BUSINESS_NAME}
- Professional animal breeder in Leiria, Portugal
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


@dataclass(frozen=True)
class KnowledgeSnapshot:
    """Prompt content plus whether it contains facts related to the question."""

    text: str
    has_query_facts: bool


def build_knowledge(query, page_context):
    """Return bounded public facts relevant to the current question."""
    return build_knowledge_snapshot(query, page_context).text


def build_knowledge_snapshot(query, page_context):
    """Build model context without treating general business facts as evidence."""
    sections = [_business_facts()]
    page_section = _page_context(page_context)
    if page_section:
        sections.append(page_section)

    blog_section = _blog_posts(query)
    if blog_section:
        sections.append(blog_section)

    try:
        certification_section = _certifications(query)
    except DatabaseError:
        logger.warning(
            "Chat certification knowledge query failed",
            exc_info=True,
        )
        certification_section = ""
    if certification_section:
        sections.append(certification_section)

    try:
        faq_section = _faqs(query)
    except DatabaseError:
        logger.warning("Chat knowledge database query failed", exc_info=True)
    else:
        if faq_section:
            sections.append(faq_section)

    return KnowledgeSnapshot(
        text="\n\n".join(sections)[:settings.CHAT_KNOWLEDGE_MAX_CHARS],
        has_query_facts=bool(
            blog_section or certification_section or faq_section
        ),
    )


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
        "animal_name": "animal",
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


def _blog_posts(query):
    """Add real post titles only when the visitor asks about the blog."""
    if not is_blog_query(query):
        return ""

    try:
        posts = list(published_posts()[:settings.CHAT_MAX_BLOG_POSTS])
    except DatabaseError:
        logger.warning("Chat blog query failed", exc_info=True)
        return ""
    if not posts:
        return ""

    lines = ["Published blog posts:"]
    lines.extend(f"- {_one_line(post.title, 180)}" for post in posts)
    return "\n".join(lines)


def _certifications(query):
    """Add one matched certification or the bounded public catalogue."""
    certifications = list(
        public_certifications()[:settings.CHAT_MAX_CERTIFICATIONS]
    )
    if not certifications:
        return ""

    query_words = set(words(query))
    exact_matches = [
        certification
        for certification in certifications
        if normalize_text(certification.code) in query_words
    ]
    if exact_matches:
        selected = exact_matches
        description_limit = 900
    else:
        ranked = _rank_certifications(query, certifications)
        strong_matches = [
            certification
            for score, certification in ranked
            if score >= 0.86
        ]
        if strong_matches:
            selected = strong_matches[:3]
            description_limit = 900
        elif is_certification_query(query):
            selected = certifications
            description_limit = 240
        else:
            return ""

    lines = ["Published certifications:"]
    for certification in selected:
        details = [
            f"- Code: {certification.code}",
            f"  Name: {certification.name}",
        ]
        if certification.parent:
            details.append(
                "  Parent certification: "
                f"{certification.parent.code} — {certification.parent.name}"
            )
        if certification.description:
            details.append(
                "  Description: "
                f"{_one_line(certification.description, description_limit)}"
            )
        lines.extend(details)
    return "\n".join(lines)


def _rank_certifications(query, certifications):
    aliases_by_id = (
        alias_terms_by_id(
            certifications[0].__class__,
            (certification.pk for certification in certifications),
        )
        if certifications else {}
    )
    ranked = []
    for certification in certifications:
        candidates = (
            certification.name,
            *aliases_by_id.get(certification.pk, ()),
        )
        score = max(
            (phrase_score(query, candidate) for candidate in candidates),
            default=0.0,
        )
        if score and _has_distinctive_certification_match(query, candidates):
            ranked.append((score, certification))
    return sorted(ranked, key=lambda item: item[0], reverse=True)


def _has_distinctive_certification_match(query, candidates):
    query_words = words(query)
    return any(
        len(candidate_word) >= 4
        and same_word(query_word, candidate_word)
        for candidate in candidates
        for candidate_word in words(candidate)
        for query_word in query_words
    )


def relevant_faqs(query):
    """Return FAQs whose meaningful words match, including small typos."""
    from frontoffice.models import FrequentlyAskedQuestion

    query_words = _meaningful_words(query)
    if not query_words:
        return []

    faqs = list(
        FrequentlyAskedQuestion.objects.filter(active=True).order_by("order")
    )
    aliases_by_id = alias_terms_by_id(
        FrequentlyAskedQuestion,
        (faq.pk for faq in faqs),
    )
    ranked = []
    for faq in faqs:
        aliases = aliases_by_id.get(faq.pk, ())
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
    aliases_by_id = (
        alias_terms_by_id(
            faqs[0].__class__,
            (faq.pk for faq in faqs),
        )
        if faqs else {}
    )
    ranked = sorted(
        (
            (
                max(
                    phrase_score(query, candidate)
                    for candidate in (
                        faq.question,
                        *aliases_by_id.get(faq.pk, ()),
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
