from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

from ai_adventure.context.creative_ideas import CreativeIdeasLibrary


LOGGER = logging.getLogger(__name__)
DEFAULT_BANNED_TERM_REPLACEMENT = "the city"
_TERM_SEPARATOR_PATTERN = r"[\s\-_']*"


@lru_cache(maxsize=1)
def default_banned_creative_terms() -> tuple[str, ...]:
    """Returns the packaged banned creative terms list."""

    try:
        return CreativeIdeasLibrary.load_default().banned_terms
    except Exception:
        LOGGER.exception("Could not load banned creative terms.")
        return ()


def find_banned_creative_terms(
    value: Any,
    *,
    terms: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    """Returns banned generated-name terms found in text or nested data."""

    banned_terms = tuple(terms) if terms is not None else default_banned_creative_terms()
    found: list[str] = []
    seen: set[str] = set()

    for text in _iter_text_values(value):
        for term in banned_terms:
            if not term:
                continue

            if _contains_banned_creative_term(text, term):
                folded = term.casefold()

                if folded not in seen:
                    found.append(term)
                    seen.add(folded)

    return found


def contains_banned_creative_term(
    value: Any,
    *,
    terms: tuple[str, ...] | list[str] | None = None,
) -> bool:
    """Returns True when value contains any banned creative term."""

    return bool(find_banned_creative_terms(value, terms=terms))


def sanitize_banned_creative_terms(
    text: Any,
    *,
    terms: tuple[str, ...] | list[str] | None = None,
    replacement: str = DEFAULT_BANNED_TERM_REPLACEMENT,
) -> str:
    """Replaces banned generated-name terms in one text value."""

    clean_text = str(text)
    banned_terms = tuple(terms) if terms is not None else default_banned_creative_terms()

    for term in sorted(banned_terms, key=len, reverse=True):
        if not term:
            continue

        pattern = _banned_term_pattern(term)
        clean_text = pattern.sub(
            lambda match: (
                _replacement_for_match(match.group(0), replacement)
                if _looks_like_generated_proper_noun(match.group(0))
                else match.group(0)
            ),
            clean_text,
        )

    return _clean_replacement_artifacts(clean_text, replacement=replacement)


def sanitize_banned_creative_terms_in_data(
    value: Any,
    *,
    terms: tuple[str, ...] | list[str] | None = None,
    replacement: str = DEFAULT_BANNED_TERM_REPLACEMENT,
    _path: tuple[str, ...] = (),
) -> Any:
    """Recursively replaces banned generated-name terms in text-like data."""

    banned_terms = tuple(terms) if terms is not None else default_banned_creative_terms()

    if isinstance(value, str):
        return sanitize_banned_creative_terms(
            value,
            terms=banned_terms,
            replacement=_replacement_for_path(_path, replacement),
        )

    if isinstance(value, list):
        return [
            sanitize_banned_creative_terms_in_data(
                item,
                terms=banned_terms,
                replacement=replacement,
                _path=(*_path, "[]"),
            )
            for item in value
        ]

    if isinstance(value, tuple):
        return tuple(
            sanitize_banned_creative_terms_in_data(
                item,
                terms=banned_terms,
                replacement=replacement,
                _path=(*_path, "[]"),
            )
            for item in value
        )

    if isinstance(value, dict):
        return {
            sanitize_banned_creative_terms(
                key,
                terms=banned_terms,
                replacement=_replacement_for_path((*_path, "<key>"), replacement),
            )
            if isinstance(key, str)
            else key: sanitize_banned_creative_terms_in_data(
                item,
                terms=banned_terms,
                replacement=replacement,
                _path=(*_path, str(key)),
            )
            for key, item in value.items()
        }

    return value


def _iter_text_values(value: Any) -> list[str]:
    """Returns all string leaves from a nested value."""

    if isinstance(value, str):
        return [value]

    if isinstance(value, dict):
        values: list[str] = []

        for key, item in value.items():
            if isinstance(key, str):
                values.append(key)

            values.extend(_iter_text_values(item))

        return values

    if isinstance(value, (list, tuple)):
        values: list[str] = []

        for item in value:
            values.extend(_iter_text_values(item))

        return values

    return []


def _contains_banned_creative_term(text: str, term: str) -> bool:
    """Returns True when text contains a proper-noun-looking banned term."""

    pattern = _banned_term_pattern(term)
    return any(
        _looks_like_generated_proper_noun(match.group(0))
        for match in pattern.finditer(text)
    )


def _banned_term_pattern(term: str) -> re.Pattern[str]:
    """Builds a case-insensitive pattern with spelling/hyphenation slack."""

    chars = [re.escape(char) for char in term if char.isalnum()]

    if not chars:
        return re.compile(r"(?!x)x")

    pattern = (
        r"(?<![A-Za-z0-9])"
        + _TERM_SEPARATOR_PATTERN.join(chars)
        + r"(?:'s)?"
        + r"(?![A-Za-z0-9])"
    )
    return re.compile(pattern, flags=re.IGNORECASE)


def _replacement_for_match(match_text: str, replacement: str) -> str:
    """Preserves simple possessive grammar around a replacement."""

    if re.search(r"'s$", match_text):
        return f"{replacement}'s"

    return replacement


def _looks_like_generated_proper_noun(match_text: str) -> bool:
    """Avoids replacing ordinary lowercase words such as 'verdant'."""

    return any(character.isupper() for character in match_text)


def _clean_replacement_artifacts(text: str, *, replacement: str) -> str:
    """Cleans common awkward phrases created by replacing generated names."""

    escaped_replacement = re.escape(replacement)
    text = re.sub(
        r"\b(the\s+city\s+of\s+)the\s+city\s+([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)*)",
        r"\1the \2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bthe city\s+([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)*)",
        r"the \1",
        text,
    )
    text = re.sub(
        rf"\bNew\s+{escaped_replacement}\b",
        "the city",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"\bOld\s+{escaped_replacement}\b",
        "the old city",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bthe city,\s+((?:my|the|a|an)\s+[^,]+),",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bthe city,\s+((?:my|the|a|an)\s+[a-z])",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _replacement_for_path(path: tuple[str, ...], default: str) -> str:
    """Chooses a less awkward fallback by structured response field."""

    folded_path = tuple(part.casefold() for part in path)
    last = folded_path[-1] if folded_path else ""

    if last in {
        "display_name",
        "visible_name",
        "requester",
        "giver",
        "quest_giver",
        "client",
        "speaker",
    }:
        return "the person"

    if last in {"npc_id", "internal_name"}:
        return "local_contact"

    if last in {"location", "start_location", "turn_in"}:
        return "the city"

    if last == "name" and "character" in folded_path:
        return "the character"

    if last == "name" and any(part.startswith("npc") for part in folded_path):
        return "the person"

    if last in {"skill_name"} or "skills" in folded_path:
        return "Local Skill"

    if last in {"item_name", "target_name", "new_name"} or "starting_items" in folded_path:
        return "Local Item"

    return default
