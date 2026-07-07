from __future__ import annotations

import re
from typing import Any


DEFAULT_CURRENCY_DENOMINATIONS: list[dict[str, Any]] = [
    {"name": "Copper Piece", "plural_name": "Copper Pieces", "value": 1},
    {"name": "Silver Piece", "plural_name": "Silver Pieces", "value": 10},
    {"name": "Gold Piece", "plural_name": "Gold Pieces", "value": 100},
    {"name": "Platinum Piece", "plural_name": "Platinum Pieces", "value": 1000},
]

FALLBACK_CURRENCY_DENOMINATIONS: list[dict[str, Any]] = [
    {"name": "Coin", "plural_name": "Coins", "value": 1},
]

_BASE_UNIT_PHRASE_RE = re.compile(
    r"\b(?P<amount>\d+)\s*(?:-| )?(?:base|baseline)(?:\s+currency)?\s+units?\b",
    re.IGNORECASE,
)
_MONEY_WORTH_PHRASE_RE = re.compile(
    r"\b(?P<amount>\d+)\s+"
    r"(?:(?:copper|silver|gold|platinum)\s+(?:coins?|pieces?)|"
    r"(?:base|baseline)(?:\s+currency)?\s+units?)"
    r"(?:'s|’s|'|’)?\s+worth\s+of\s+"
    r"(?:copper|silver|gold|platinum)\b",
    re.IGNORECASE,
)


def normalize_currency_denominations(
    raw_denominations: Any,
    *,
    fallback_denominations: list[dict[str, Any]] | None = DEFAULT_CURRENCY_DENOMINATIONS,
    max_denominations: int | None = None,
) -> list[dict[str, Any]]:
    """Returns clean positive currency denominations sorted by value."""

    if not isinstance(raw_denominations, list):
        raw_denominations = fallback_denominations or []

    clean_denominations: list[dict[str, Any]] = []
    seen_values: set[int] = set()

    for raw_denomination in raw_denominations:
        if not isinstance(raw_denomination, dict):
            continue

        name = str(raw_denomination.get("name", "")).strip()
        plural_name = str(raw_denomination.get("plural_name", "")).strip()
        value = _safe_positive_int(raw_denomination.get("value"))

        if not name or value is None or value in seen_values:
            continue

        clean_denominations.append(
            {
                "name": name,
                "plural_name": plural_name or f"{name}s",
                "value": value,
            }
        )
        seen_values.add(value)

    if not clean_denominations:
        return [dict(denomination) for denomination in (fallback_denominations or [])]

    clean_denominations.sort(key=lambda denomination: int(denomination["value"]))

    if max_denominations is not None:
        clean_denominations = clean_denominations[: max(1, max_denominations)]

    return clean_denominations


def describe_currency_denominations(
    denominations: Any,
    *,
    fallback_denominations: list[dict[str, Any]] | None = DEFAULT_CURRENCY_DENOMINATIONS,
) -> str:
    """Returns a readable description of the world's currency denominations."""

    clean_denominations = normalize_currency_denominations(
        denominations,
        fallback_denominations=fallback_denominations,
    )
    if not clean_denominations:
        return ""

    parts = [
        f"{denomination['name']} ({denomination['value']} base units)"
        for denomination in clean_denominations
    ]
    return "Currency denominations: " + "; ".join(parts) + "."


def format_currency_amount(
    amount: int,
    denominations: list[dict[str, Any]] | None = None,
) -> str:
    """Formats a baseline currency amount with largest denominations first."""

    clean_amount = int(amount)
    clean_denominations = normalize_currency_denominations(denominations)

    if clean_amount == 0:
        return f"0 {clean_denominations[0]['plural_name']}"

    sign = "-" if clean_amount < 0 else ""
    remaining = abs(clean_amount)
    parts: list[str] = []

    for denomination in sorted(
        clean_denominations,
        key=lambda item: int(item["value"]),
        reverse=True,
    ):
        value = int(denomination["value"])

        if value <= 0 or remaining < value:
            continue

        count, remaining = divmod(remaining, value)
        unit_name = denomination["name"] if count == 1 else denomination["plural_name"]
        parts.append(f"{count} {unit_name}")

    if not parts:
        return f"0 {clean_denominations[0]['plural_name']}"

    return sign + _join_currency_parts(parts)


def normalize_visible_currency_text(
    text: str,
    denominations: list[dict[str, Any]] | None = None,
) -> str:
    """Rewrites player-visible base-unit money phrases into denominations."""

    clean_denominations = normalize_currency_denominations(denominations)
    normalized = str(text or "")

    def replace_amount(match: re.Match[str]) -> str:
        amount = _safe_int(match.group("amount"), 0)
        return format_currency_amount(amount, clean_denominations)

    normalized = _MONEY_WORTH_PHRASE_RE.sub(replace_amount, normalized)
    normalized = _BASE_UNIT_PHRASE_RE.sub(replace_amount, normalized)

    base_denomination = next(
        (
            denomination
            for denomination in clean_denominations
            if int(denomination.get("value", 0)) == 1
        ),
        None,
    )
    if base_denomination is None:
        return normalized

    larger_values = [
        int(denomination["value"])
        for denomination in clean_denominations
        if int(denomination["value"]) > 1
    ]
    smallest_larger_value = min(larger_values, default=0)
    if smallest_larger_value <= 1:
        return normalized

    base_names = {
        str(base_denomination.get("name", "")).strip(),
        str(base_denomination.get("plural_name", "")).strip(),
    }
    folded_base_name = str(base_denomination.get("name", "")).casefold()
    folded_base_plural = str(base_denomination.get("plural_name", "")).casefold()
    if "copper" in folded_base_name or "copper" in folded_base_plural:
        base_names.update({"Copper Coin", "Copper Coins", "Copper Piece", "Copper Pieces"})

    escaped_names = sorted(
        (re.escape(name) for name in base_names if name),
        key=len,
        reverse=True,
    )
    if not escaped_names:
        return normalized

    base_coin_re = re.compile(
        r"\b(?P<amount>\d+)\s+(?P<name>" + "|".join(escaped_names) + r")\b",
        re.IGNORECASE,
    )

    def replace_base_coin_amount(match: re.Match[str]) -> str:
        amount = _safe_int(match.group("amount"), 0)
        if amount < smallest_larger_value:
            return match.group(0)
        return format_currency_amount(amount, clean_denominations)

    return base_coin_re.sub(replace_base_coin_amount, normalized)


def _safe_positive_int(value: Any) -> int | None:
    """Converts a value to a positive integer."""

    try:
        clean_value = int(value)
    except (TypeError, ValueError):
        return None

    if clean_value <= 0:
        return None

    return clean_value


def _safe_int(value: Any, default: int) -> int:
    """Converts a value to an integer, returning default on failure."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _join_currency_parts(parts: list[str]) -> str:
    """Joins currency parts with natural comma and conjunction placement."""

    if len(parts) <= 1:
        return parts[0] if parts else ""

    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"

    return ", ".join(parts[:-1]) + f", and {parts[-1]}"
