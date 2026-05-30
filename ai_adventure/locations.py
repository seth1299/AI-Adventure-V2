from __future__ import annotations

import re
from typing import Any


_DETAIL_SEPARATORS = [",", ";", " - ", " -- ", " – ", " — "]
_DETAIL_PHRASE_RE = re.compile(
    r"\s+(?:high up|overlooking|with|near|beside|under|above|below|inside|outside|"
    r"atop|beneath|facing)\b",
    flags=re.IGNORECASE,
)


def clean_player_location_name(raw_location: Any) -> str:
    """Returns a short, broad player location name suitable for UI state."""

    location = re.sub(r"\s+", " ", str(raw_location or "")).strip()

    if not location:
        return ""

    split_indexes = [
        index
        for separator in _DETAIL_SEPARATORS
        if (index := location.find(separator)) > 0
    ]
    phrase_match = _DETAIL_PHRASE_RE.search(location)

    if phrase_match is not None and phrase_match.start() > 0:
        split_indexes.append(phrase_match.start())

    if split_indexes:
        location = location[: min(split_indexes)].strip()

    return location.strip(" ,;:-")
