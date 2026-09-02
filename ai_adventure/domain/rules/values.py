"""Small persistence-value rules shared by application and UI layers."""

from __future__ import annotations

from typing import Any


def bool_setting(value: Any, default: bool = False) -> bool:
    """Converts persisted boolean values without treating false as true."""

    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in {"false", "0", "no", "off"}:
            return False
        if folded in {"true", "1", "yes", "on"}:
            return True
    return bool(value)


def clamped_int(
    value: Any,
    default: int = 0,
    minimum: int = 0,
    maximum: int = 100,
) -> int:
    """Reads an integer and clamps it to a caller-provided range."""

    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """Converts an optional value to an integer with a fallback."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default
