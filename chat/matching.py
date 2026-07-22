"""Accent-insensitive lexical and fuzzy matching without extra models."""

import re
import unicodedata
from difflib import SequenceMatcher


WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
DEFAULT_FUZZY_THRESHOLD = 0.84
AMBIGUITY_MARGIN = 0.03


def normalize_text(value):
    value = unicodedata.normalize("NFKD", str(value).casefold())
    value = "".join(
        character for character in value
        if not unicodedata.combining(character)
    )
    return " ".join(WORD_RE.findall(value))


def words(value):
    return normalize_text(value).split()


def similarity(first, second):
    first = normalize_text(first)
    second = normalize_text(second)
    if not first or not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


def phrase_score(query, candidate):
    """Score a candidate against similarly sized windows in the query."""
    query_words = words(query)
    candidate_words = words(candidate)
    if not query_words or not candidate_words:
        return 0.0

    if query_words == candidate_words:
        return 1.0

    # A one-letter name may be valid in the catalogue, but it must only match
    # when the complete query is that name. Otherwise articles such as "a"
    # would select it accidentally.
    candidate_text = " ".join(candidate_words)
    if len(candidate_text) < 4:
        return 0.0

    candidate_length = len(candidate_words)
    exact_windows = [
        query_words[index:index + candidate_length]
        for index in range(len(query_words) - candidate_length + 1)
    ]
    if candidate_words in exact_windows:
        return 0.99

    window_sizes = {
        max(1, candidate_length - 1),
        candidate_length,
        candidate_length + 1,
    }
    scores = []
    for size in window_sizes:
        for index in range(len(query_words) - size + 1):
            window = " ".join(query_words[index:index + size])
            scores.append(similarity(window, candidate_text))
    return max(scores, default=0.0)


def contains_phrase(query, phrases, threshold=0.9):
    return any(phrase_score(query, phrase) >= threshold for phrase in phrases)


def same_word(first, second, threshold=DEFAULT_FUZZY_THRESHOLD):
    first = normalize_text(first)
    second = normalize_text(second)
    if first == second:
        return True
    if min(len(first), len(second)) < 4:
        return False
    if similarity(first, second) >= threshold:
        return True

    # Also recognise close grammatical forms such as reserve/reservations and
    # available/availability without introducing a stemming dependency.
    common_prefix = 0
    for first_character, second_character in zip(first, second):
        if first_character != second_character:
            break
        common_prefix += 1
    return (
        common_prefix >= 5
        and common_prefix / min(len(first), len(second)) >= 0.75
    )
