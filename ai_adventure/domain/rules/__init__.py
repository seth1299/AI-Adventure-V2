"""Game rules exposed through the canonical domain namespace."""

from ai_adventure.domain.rules.values import (
    bool_setting,
    clamped_int,
    safe_int,
)

__all__ = ["bool_setting", "clamped_int", "safe_int"]
