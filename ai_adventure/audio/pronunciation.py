"""English-only pronunciation glossary normalization for local TTS."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, TypedDict

from ai_adventure.text_sanitization import sanitize_english_text


MAX_PRONUNCIATION_ENTRIES = 200
MAX_PRONUNCIATION_TERM_LENGTH = 160
MAX_PRONUNCIATION_VALUE_LENGTH = 240

# Kokoro v1.0's accepted phoneme vocabulary. Whitespace is allowed so one
# override can cover a multi-word visible term.
KOKORO_V1_PHONEME_CHARACTERS = frozenset(
    " ;:,.!?—…()“”̃ʣʥʦʨᵝꭧAIOQSTWYᵊ"
    "abcdefhijklmnopqrstuvwxyz"
    "ɑɐɒæβɔɕçɖðʤəɚɛɜɟɡɥɨɪʝɯɰŋɳɲɴøɸθœɹɾɻʁɽʂʃʈʧʊʋʌɣɤχʎʒʔ"
    "ˈˌːʰʲ↓→↗↘ᵻ"
)
_PHONETIC_SEPARATOR_RE = re.compile(r"(?<=[^\W\d_])[-\u2010-\u2015](?=[^\W\d_])")
_VISIBLE_WORD_RE = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)*")
_NON_LETTER_RE = re.compile(r"[^a-z]+")
_REPEATED_LETTER_RE = re.compile(r"([a-z])\1+")
_PHONEME_OVERRIDE_RE = re.compile(
    r'\[([^\]]+)\]\{\s*(?:ph|phonemes)\s*=\s*"([^"]+)"\s*\}',
    re.IGNORECASE,
)


class PronunciationEntry(TypedDict, total=False):
    """Canonical saved pronunciation data for one exact visible term."""

    ipa: str
    respelling: str


PronunciationMap = dict[str, PronunciationEntry]


def invalid_kokoro_ipa_characters(raw_value: Any) -> tuple[str, ...]:
    """Returns unsupported characters from a proposed Kokoro phoneme string."""

    value = _strip_ipa_delimiters(str(raw_value or "").strip())
    return tuple(
        sorted(
            {
                character
                for character in value
                if character not in KOKORO_V1_PHONEME_CHARACTERS
                and not character.isspace()
            }
        )
    )


def normalize_kokoro_ipa(raw_value: Any) -> str:
    """Rejects IPA overrides so TTS always uses its English grapheme path."""

    del raw_value
    return ""


def normalize_pronunciation_value(term: str, raw_value: Any) -> str:
    """Returns a pause-safe legacy respelling for one visible term."""

    value = sanitize_english_text(raw_value)[:MAX_PRONUNCIATION_VALUE_LENGTH]
    if not value:
        return ""

    # Legacy values remain useful for player-authored "sound it out" input, but
    # punctuation between syllables makes Kokoro pause. Join syllables into one
    # continuous visible word before the value is sent through normal G2P.
    value = _PHONETIC_SEPARATOR_RE.sub("-", value).casefold()
    value = re.sub(r"\s+", " ", value).strip()

    # A glossary entry that merely changes case or cosmetically respells an
    # already pronounceable term cannot improve TTS. It can, however, make a
    # voice spell the word letter by letter, so omit it deterministically.
    term_literal_key = _literal_pronunciation_key(term)
    value_literal_key = _literal_pronunciation_key(value)
    term_key = _rough_pronunciation_key(term)
    value_key = _rough_pronunciation_key(value)
    if term_literal_key and term_literal_key == value_literal_key:
        visible_word_count = len(_VISIBLE_WORD_RE.findall(term))
        phonetic_chunks = _VISIBLE_WORD_RE.findall(value)
        is_letter_spelling = len(phonetic_chunks) > 1 and all(
            len(chunk) == 1 for chunk in phonetic_chunks
        )
        has_useful_syllable_boundaries = len(phonetic_chunks) > visible_word_count
        if is_letter_spelling or not has_useful_syllable_boundaries:
            return ""
    elif term_key and value_key and term_key == value_key:
        return ""

    partitioned = _join_syllables_within_visible_words(term, value)
    return _PHONETIC_SEPARATOR_RE.sub("", partitioned)


def _normalize_pronunciation_entry(term: str, raw_value: Any) -> PronunciationEntry:
    entry: PronunciationEntry = {}
    if isinstance(raw_value, dict):
        raw_respelling = raw_value.get(
            "respelling",
            raw_value.get("phonetic", raw_value.get("pronunciation", "")),
        )
        respelling = normalize_pronunciation_value(term, raw_respelling)
        if respelling:
            entry["respelling"] = respelling
        return entry

    value = str(raw_value or "").strip()
    if _has_explicit_ipa_delimiters(value):
        return entry

    respelling = normalize_pronunciation_value(term, value)
    if respelling:
        entry["respelling"] = respelling
    return entry


def _join_syllables_within_visible_words(term: str, value: str) -> str:
    """Infers which spaced phonetic syllables belong to each visible word."""

    visible_words = _VISIBLE_WORD_RE.findall(str(term or ""))
    phonetic_chunks = str(value or "").split()
    if not visible_words or len(phonetic_chunks) <= len(visible_words):
        return value

    @lru_cache(maxsize=None)
    def best_partition(
        visible_index: int,
        chunk_index: int,
    ) -> tuple[float, tuple[str, ...]] | None:
        if visible_index == len(visible_words):
            return (0.0, ()) if chunk_index == len(phonetic_chunks) else None

        visible_remaining = len(visible_words) - visible_index - 1
        last_end = len(phonetic_chunks) - visible_remaining
        best: tuple[float, tuple[str, ...]] | None = None
        source_key = _rough_pronunciation_key(visible_words[visible_index])

        for end in range(chunk_index + 1, last_end + 1):
            remainder = best_partition(visible_index + 1, end)
            if remainder is None:
                continue
            joined = "-".join(phonetic_chunks[chunk_index:end])
            joined_key = _rough_pronunciation_key(joined)
            similarity = SequenceMatcher(None, source_key, joined_key).ratio()
            candidate = (similarity + remainder[0], (joined, *remainder[1]))
            if best is None or candidate[0] > best[0]:
                best = candidate

        return best

    partition = best_partition(0, 0)
    if partition is None:
        return value
    return " ".join(partition[1])


def _rough_pronunciation_key(value: str) -> str:
    """Builds a conservative key for detecting no-op phonetic respellings."""

    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    replacements = (
        ("eigh", "a"),
        ("igh", "i"),
        ("ph", "f"),
        ("qu", "kw"),
        ("ck", "k"),
        ("ee", "i"),
        ("ea", "i"),
        ("ai", "a"),
        ("ay", "a"),
        ("oo", "u"),
    )
    for source, replacement in replacements:
        text = text.replace(source, replacement)
    text = text.replace("c", "k").replace("q", "k").replace("x", "ks")
    words = [
        word[:-1] if word.endswith("e") and len(word) > 2 else word
        for word in re.findall(r"[a-z]+", text)
    ]
    return _REPEATED_LETTER_RE.sub(
        r"\1",
        _NON_LETTER_RE.sub("", "".join(words)),
    )


def _literal_pronunciation_key(value: str) -> str:
    """Returns case-neutral letters without applying phonetic equivalences."""

    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return _NON_LETTER_RE.sub("", text)


def normalize_pronunciation_map(raw_map: Any) -> PronunciationMap:
    """Returns a bounded exact-term map with validated IPA or legacy fallbacks."""

    raw_entries: list[tuple[Any, Any]]
    if isinstance(raw_map, list):
        raw_entries = [
            (
                entry.get("term", ""),
                (
                    {"ipa": entry.get("ipa", "")}
                    if "ipa" in entry
                    else {
                        "respelling": entry.get(
                            "phonetic",
                            entry.get("pronunciation", entry.get("respelling", "")),
                        )
                    }
                ),
            )
            for entry in raw_map
            if isinstance(entry, dict)
        ]
    elif isinstance(raw_map, dict):
        raw_entries = list(raw_map.items())
    else:
        return {}

    normalized: PronunciationMap = {}
    seen_terms: set[str] = set()
    for raw_term, raw_value in raw_entries:
        term = sanitize_english_text(raw_term)[:MAX_PRONUNCIATION_TERM_LENGTH]
        entry = _normalize_pronunciation_entry(term, raw_value)
        key = term.casefold()
        if not term or not entry or key in seen_terms:
            continue
        normalized[term] = entry
        seen_terms.add(key)
        if len(normalized) >= MAX_PRONUNCIATION_ENTRIES:
            break

    return normalized


def merge_pronunciation_maps(*raw_maps: Any) -> PronunciationMap:
    """Merges maps with first-seen values taking precedence per entry field."""

    merged: PronunciationMap = {}
    spelling_by_key: dict[str, str] = {}
    for raw_map in raw_maps:
        for term, entry in normalize_pronunciation_map(raw_map).items():
            key = term.casefold()
            existing_term = spelling_by_key.get(key)
            if existing_term is None:
                copied_entry: PronunciationEntry = {}
                entry_ipa = entry.get("ipa", "")
                entry_respelling = entry.get("respelling", "")
                if entry_ipa:
                    copied_entry["ipa"] = entry_ipa
                if entry_respelling:
                    copied_entry["respelling"] = entry_respelling
                merged[term] = copied_entry
                spelling_by_key[key] = term
            else:
                existing = merged[existing_term]
                entry_ipa = entry.get("ipa", "")
                entry_respelling = entry.get("respelling", "")
                if "ipa" not in existing and entry_ipa:
                    existing["ipa"] = entry_ipa
                if "respelling" not in existing and entry_respelling:
                    existing["respelling"] = entry_respelling
            if len(merged) >= MAX_PRONUNCIATION_ENTRIES:
                return merged
    return merged


def set_authoritative_pronunciation(
    raw_map: Any,
    term: Any,
    raw_value: Any,
) -> PronunciationMap:
    """Sets or clears one player-authored term without retaining stale AI data."""

    clean_term = str(term or "").strip()[:MAX_PRONUNCIATION_TERM_LENGTH]
    pronunciation_map = normalize_pronunciation_map(raw_map)
    if not clean_term:
        return pronunciation_map

    key = clean_term.casefold()
    pronunciation_map = {
        existing_term: entry
        for existing_term, entry in pronunciation_map.items()
        if existing_term.casefold() != key
    }
    authoritative_entry = _normalize_pronunciation_entry(clean_term, raw_value)
    if not authoritative_entry:
        return pronunciation_map

    return merge_pronunciation_maps(
        {clean_term: authoritative_entry},
        pronunciation_map,
    )


def apply_pronunciation_map(text: str, raw_map: Any) -> str:
    """Applies only ASCII English respellings to a TTS-only copy."""

    clean_text = sanitize_english_text(text)
    pronunciation_map = normalize_pronunciation_map(raw_map)
    if not clean_text or not pronunciation_map:
        return clean_text

    terms = sorted(pronunciation_map, key=len, reverse=True)
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(term) for term in terms) + r")(?!\w)",
        re.IGNORECASE,
    )
    by_casefold = {
        term.casefold(): entry for term, entry in pronunciation_map.items()
    }

    def replace_term(match: re.Match[str]) -> str:
        entry = by_casefold[match.group(0).casefold()]
        return sanitize_english_text(entry.get("respelling", match.group(0)))

    return pattern.sub(replace_term, clean_text)


def strip_phoneme_overrides(text: str) -> str:
    """Replaces any legacy inline phoneme annotation with visible text."""

    return _PHONEME_OVERRIDE_RE.sub(lambda match: match.group(1), str(text or ""))


def compile_kokoro_phoneme_overrides(
    text: str,
    phonemize: Callable[[str], str],
) -> str | None:
    """Ignores legacy IPA markup so Kokoro uses normal English G2P."""

    del text, phonemize
    return None


def _has_explicit_ipa_delimiters(value: str) -> bool:
    value = str(value or "").strip()
    return (
        len(value) >= 2
        and ((value.startswith("/") and value.endswith("/")) or (value.startswith("[") and value.endswith("]")))
    )


def _strip_ipa_delimiters(value: str) -> str:
    value = str(value or "").strip()
    if _has_explicit_ipa_delimiters(value):
        return value[1:-1].strip()
    return value
