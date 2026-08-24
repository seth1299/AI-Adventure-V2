from __future__ import annotations

from typing import Any


MAGIC_CASTING_MODES = ("narrative", "mana", "tiered")
MAGIC_ADVANCEMENT_CATEGORIES = (
    "meaningful_cast",
    "training",
    "study",
    "discovery",
    "story_milestone",
)
MAGIC_ADVANCEMENT_SIGNIFICANCE = ("meaningful", "major", "milestone")
MAGIC_CASTING_MODE_LABELS = {
    "narrative": "Narrative",
    "mana": "Mana",
    "tiered": "Tiered Slots",
}
DEFAULT_TIER_SLOTS = {1: 2, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0, 8: 0, 9: 0}


def normalize_magic_setup(raw_magic: Any) -> dict[str, Any]:
    """Returns the canonical per-save magic configuration."""

    source = raw_magic if isinstance(raw_magic, dict) else {}
    casting_mode = str(source.get("casting_mode", "narrative")).strip().casefold()
    if casting_mode not in MAGIC_CASTING_MODES:
        casting_mode = "narrative"

    raw_slots = source.get("tier_slots", {})
    if not isinstance(raw_slots, dict):
        raw_slots = {}
    tier_slots = {
        tier: _bounded_int(
            raw_slots.get(str(tier), raw_slots.get(tier, DEFAULT_TIER_SLOTS[tier])),
            0,
            99,
        )
        for tier in range(1, 10)
    }

    starting_spells_mode = (
        "advanced"
        if str(source.get("starting_spells_mode", "basic")).strip().casefold()
        == "advanced"
        else "basic"
    )
    raw_spell_requests = source.get("starting_spell_requests", [])
    if not isinstance(raw_spell_requests, list):
        raw_spell_requests = []
    starting_spell_requests = [
        normalized
        for request in raw_spell_requests
        for normalized in [_normalize_starting_spell_request(request)]
        if normalized is not None
    ]

    raw_spells = source.get("starting_spells", [])
    if not isinstance(raw_spells, list):
        raw_spells = []
    starting_spells = [
        normalized
        for spell in raw_spells
        if isinstance(spell, dict)
        for normalized in [_normalize_starting_spell(spell)]
        if normalized["name"]
    ]
    if starting_spells_mode == "advanced":
        starting_spell_requests = []
    else:
        starting_spells = []

    world_contains_magic = bool(source.get("world_contains_magic", True))
    player_magic_enabled = bool(
        source.get("player_magic_enabled", source.get("enabled", False))
    )
    enabled = world_contains_magic and player_magic_enabled
    if not enabled:
        starting_spell_requests = []
        starting_spells = []
    return {
        "world_contains_magic": world_contains_magic,
        "player_magic_enabled": player_magic_enabled,
        "enabled": enabled,
        "casting_mode": casting_mode,
        "tradition": str(source.get("tradition", "")).strip(),
        "mana_maximum": _bounded_int(source.get("mana_maximum", 10), 1, 9999),
        "tier_slots": tier_slots,
        "starting_spells_mode": starting_spells_mode,
        "starting_spell_requests": starting_spell_requests,
        "starting_spells": starting_spells,
    }


def magic_resource_specs(raw_magic: Any) -> list[dict[str, Any]]:
    """Builds initial resource-pool rows for a normalized magic setup."""

    magic = normalize_magic_setup(raw_magic)
    if not magic["enabled"] or magic["casting_mode"] == "narrative":
        return []
    if magic["casting_mode"] == "mana":
        maximum = int(magic["mana_maximum"])
        return [
            {
                "pool_id": "mana",
                "name": "Mana",
                "resource_type": "mana",
                "tier": 0,
                "current_amount": maximum,
                "maximum_amount": maximum,
                "recovery_rule": "Restore according to the adventure's rest rules.",
            }
        ]

    return [
        {
            "pool_id": f"tier_{tier}",
            "name": f"Tier {tier} Slots",
            "resource_type": "slot",
            "tier": tier,
            "current_amount": maximum,
            "maximum_amount": maximum,
            "recovery_rule": "Restore according to the adventure's rest rules.",
        }
        for tier, maximum in magic["tier_slots"].items()
        if maximum > 0
    ]


def normalize_magic_advancement_category(value: Any) -> str:
    """Returns a supported magic-advancement category or an empty string."""

    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return normalized if normalized in MAGIC_ADVANCEMENT_CATEGORIES else ""


def normalize_magic_advancement_significance(value: Any) -> str:
    """Returns a supported advancement significance, defaulting to meaningful."""

    normalized = str(value or "").strip().casefold()
    return normalized if normalized in MAGIC_ADVANCEMENT_SIGNIFICANCE else "meaningful"


def _normalize_starting_spell(spell: dict[str, Any]) -> dict[str, Any]:
    return {
        "spell_id": str(spell.get("spell_id", "")).strip(),
        "name": str(spell.get("name", "")).strip(),
        "tier": _bounded_int(spell.get("tier", spell.get("level", 0)), 0, 9),
        "school": str(spell.get("school", "")).strip(),
        "description": str(spell.get("description", "")).strip(),
        "casting_time": str(spell.get("casting_time", "Action")).strip() or "Action",
        "range": str(spell.get("range", "")).strip(),
        "duration": str(spell.get("duration", "")).strip(),
        "requirements": str(spell.get("requirements", "")).strip(),
        "mana_cost": _bounded_int(spell.get("mana_cost", 0), 0, 9999),
        "prepared": bool(spell.get("prepared", True)),
    }


def _normalize_starting_spell_request(raw_request: Any) -> dict[str, Any] | None:
    """Returns one player-authored Basic-mode spell concept."""

    if isinstance(raw_request, dict):
        request = str(
            raw_request.get(
                "spell_request",
                raw_request.get("request", raw_request.get("description", "")),
            )
        ).strip()
    else:
        request = str(raw_request).strip()
    if not request:
        return None
    return {
        "spell_request": request,
        "requires_ai_invention": True,
    }


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
