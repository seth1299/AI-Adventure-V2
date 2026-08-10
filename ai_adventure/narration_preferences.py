from __future__ import annotations

from collections import OrderedDict
from typing import Any


DEFAULT_NARRATION_TENSE = "present"
DEFAULT_NARRATION_STYLE = "second_person_limited"

NARRATION_TENSE_OPTIONS: "OrderedDict[str, str]" = OrderedDict(
    [
        ("past", "Past Tense"),
        ("present", "Present Tense"),
        ("future", "Future Tense"),
    ]
)

NARRATION_STYLE_OPTIONS: "OrderedDict[str, str]" = OrderedDict(
    [
        ("first_person_limited", "First-Person Limited"),
        ("first_person_omniscient", "First-Person Omniscient"),
        ("second_person_limited", "Second-Person Limited"),
        ("second_person_omniscient", "Second-Person Omniscient"),
        ("third_person_limited", "Third-Person Limited"),
        ("third_person_omniscient", "Third-Person Omniscient"),
    ]
)


def normalize_narration_preferences(raw_preferences: Any) -> dict[str, str]:
    """Returns safe narration preference keys and player-facing labels."""

    if not isinstance(raw_preferences, dict):
        raw_preferences = {}

    tense = _normalize_option(
        raw_preferences.get("tense", raw_preferences.get("narration_tense")),
        NARRATION_TENSE_OPTIONS,
        DEFAULT_NARRATION_TENSE,
    )
    style = _normalize_option(
        raw_preferences.get("style", raw_preferences.get("narration_style")),
        NARRATION_STYLE_OPTIONS,
        DEFAULT_NARRATION_STYLE,
    )

    return {
        "tense": tense,
        "tense_label": NARRATION_TENSE_OPTIONS[tense],
        "style": style,
        "style_label": NARRATION_STYLE_OPTIONS[style],
    }


def _normalize_option(
    raw_value: Any,
    options: "OrderedDict[str, str]",
    default: str,
) -> str:
    """Normalizes either an option key or its display label."""

    clean_value = str(raw_value or "").strip()

    if clean_value in options:
        return clean_value

    clean_label = clean_value.casefold()

    for key, label in options.items():
        if clean_label == label.casefold():
            return key

    return default
