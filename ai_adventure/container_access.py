from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


_CONTAINER_KIND_WORDS = {
    "bag",
    "box",
    "case",
    "chest",
    "container",
    "crate",
    "pouch",
    "purse",
    "sack",
    "satchel",
}
_GENERIC_CONTAINER_WORDS = {
    "a",
    "an",
    "closed",
    "heavy",
    "locked",
    "small",
    "the",
    "wooden",
}
_UNLOCK_METHOD_RE = re.compile(
    r"\b(?:key|keys|keycard|keycards|access card|access cards|passcode|"
    r"passcodes|combination|combinations)\b",
    re.IGNORECASE,
)


def has_immediate_container_unlock_method(
    inventory_items: Iterable[Mapping[str, Any]],
    container_name: str,
) -> bool:
    """Returns whether inventory contains an unambiguous key-like unlock method."""

    clean_container_name = str(container_name or "").strip()
    if not clean_container_name:
        return False

    container_words = _normalized_words(clean_container_name)
    distinctive_words = (
        container_words - _CONTAINER_KIND_WORDS - _GENERIC_CONTAINER_WORDS
    )

    for item in inventory_items:
        if not isinstance(item, Mapping):
            continue

        metadata = item.get("metadata", {})
        metadata = metadata if isinstance(metadata, Mapping) else {}
        explicit_target = _first_text(
            metadata,
            "unlocks_container",
            "unlocks_container_name",
            "key_for",
            "container_name",
        )
        if (
            explicit_target
            and explicit_target.casefold() == clean_container_name.casefold()
        ):
            return True

        item_text = " ".join(
            [
                str(item.get("name", "") or ""),
                str(item.get("category", "") or ""),
                str(item.get("description", "") or ""),
            ]
        )
        if not _UNLOCK_METHOD_RE.search(item_text):
            continue

        item_words = _normalized_words(item_text)
        if distinctive_words and distinctive_words.issubset(item_words):
            return True

    return False


def _normalized_words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(mapping.get(key, "") or "").strip()
        if value:
            return value
    return ""
