"""Pronunciation glossary normalization and TTS-only text substitution."""

from __future__ import annotations

import re
from typing import Any


MAX_PRONUNCIATION_ENTRIES = 200
MAX_PRONUNCIATION_TERM_LENGTH = 160
MAX_PRONUNCIATION_VALUE_LENGTH = 240


def normalize_pronunciation_map(raw_map: Any) -> dict[str, str]:
    """Returns a bounded, clean visible-term to phonetic-value mapping."""

    if isinstance(raw_map, list):
        raw_map = {
            str(entry.get("term", "")): entry.get(
                "phonetic", entry.get("pronunciation", "")
            )
            for entry in raw_map
            if isinstance(entry, dict)
        }
    if not isinstance(raw_map, dict):
        return {}

    normalized: dict[str, str] = {}
    seen_terms: set[str] = set()
    for raw_term, raw_value in raw_map.items():
        term = str(raw_term or "").strip()[:MAX_PRONUNCIATION_TERM_LENGTH]
        value = str(raw_value or "").strip()[:MAX_PRONUNCIATION_VALUE_LENGTH]
        if not term or not value or term.casefold() in seen_terms:
            continue
        normalized[term] = value
        seen_terms.add(term.casefold())
        if len(normalized) >= MAX_PRONUNCIATION_ENTRIES:
            break

    return normalized


def merge_pronunciation_maps(*raw_maps: Any) -> dict[str, str]:
    """Merges maps with first-seen spellings taking precedence."""

    merged: dict[str, str] = {}
    seen_terms: set[str] = set()
    for raw_map in raw_maps:
        for term, value in normalize_pronunciation_map(raw_map).items():
            key = term.casefold()
            if key in seen_terms:
                continue
            merged[term] = value
            seen_terms.add(key)
            if len(merged) >= MAX_PRONUNCIATION_ENTRIES:
                return merged
    return merged


def apply_pronunciation_map(text: str, raw_map: Any) -> str:
    """Replaces mapped visible terms in a TTS-only copy of text."""

    clean_text = str(text or "")
    pronunciation_map = normalize_pronunciation_map(raw_map)
    if not clean_text or not pronunciation_map:
        return clean_text

    terms = sorted(pronunciation_map, key=len, reverse=True)
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(term) for term in terms) + r")(?!\w)",
        re.IGNORECASE,
    )
    by_casefold = {term.casefold(): value for term, value in pronunciation_map.items()}
    return pattern.sub(lambda match: by_casefold[match.group(0).casefold()], clean_text)
