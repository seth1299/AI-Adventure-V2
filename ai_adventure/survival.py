"""Shared rules for optional player survival meters."""

from __future__ import annotations

from typing import Any


SURVIVAL_METERS = ("hunger", "thirst", "energy")
SURVIVAL_ROLL_RULE = (
    "When survival tracking is enabled, Python applies one survival modifier to every "
    "player check, attack, and save, using the lowest of Hunger, Thirst, and Energy: "
    "80-100% gives +1; 50-79% gives 0; 30-49% gives -1; 1-29% gives -3. "
    "These modifiers do not stack. Do not alter DCs, invent extra bonuses or penalties, "
    "or apply this modifier again: resolved totals already include it. At 0% in any "
    "meter the player dies and Python pauses play for the player's fate decision. "
    "Do not revive them, choose their fate, or continue play in an ordinary response."
)
SURVIVAL_RECOVERY_RULE = (
    "The player chose 'It is not my time yet' after survival depletion. Narrate the "
    "character blacking out and waking alive at a different, plausible location. "
    "Return that new location and its current weather with a concise player-visible "
    "description. Do not return events or roll checks. Python alone restores all "
    "three survival meters to 20%, halves each skill's in-progress XP (rounding down), "
    "and preserves all earned skill levels, inventory, currency, and other progress. "
    "Do not invent player dialogue or decisions, new loot, lost belongings, extra "
    "penalties, healed injuries, elapsed-time amounts, or NPC-private secrets."
)
SURVIVAL_RULE = (
    "Only when state.survival.enabled is true, consider the current Hunger, Thirst, "
    "and Energy on every live-game turn. In the final StatusUpdatedEvent, optionally "
    "include hunger, thirst, and/or energy as absolute integer percentages from 0 to "
    "100, never deltas. Higher is better: 100 means well-fed, fully hydrated, or "
    "fully rested; 0 means depleted. Omit unchanged values; they need not change "
    "every turn. Eating must increase Hunger, drinking must increase Thirst, and "
    "rest or sleep must restore Energy, up to 100. Scale restoration to the amount "
    "and quality consumed or the duration and quality of rest; better food/drink "
    "restores more than poor food/drink. Let meaningful elapsed time drain needs "
    "plausibly, with greater drain during strenuous building, running, fighting, "
    "or similar exertion than during quiet activity. Do not reduce the corresponding "
    "meter for an action devoted to eating, drinking, or resting. Keep narration "
    "consistent with the resulting values and established supplies. Never update "
    "these meters when tracking is disabled or in out-of-game mode. "
    + SURVIVAL_ROLL_RULE
)


def normalize_survival_setup(value: Any, *, genre: str = "") -> dict[str, bool]:
    """Default new games to Survival genre, retaining explicit Yes/No choices."""

    raw = value.get("enabled") if isinstance(value, dict) else None
    if isinstance(raw, bool):
        enabled = raw
    elif isinstance(raw, str) and raw.strip().casefold() in {"yes", "no", "true", "false"}:
        enabled = raw.strip().casefold() in {"yes", "true"}
    else:
        enabled = "survival" in {part.strip().casefold() for part in genre.split(",")}
    return {"enabled": enabled}


def survival_percentage(value: Any) -> int:
    """Read a stored percentage, with a full meter for missing legacy values."""

    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 100


def survival_roll_modifier(values: dict[str, Any], *, enabled: bool) -> int:
    """One non-stacking modifier; the most depleted need determines the band."""

    if not enabled:
        return 0
    lowest = min(survival_percentage(values.get(meter)) for meter in SURVIVAL_METERS)
    if lowest >= 80:
        return 1
    if lowest >= 50:
        return 0
    return -1 if lowest >= 30 else -3
