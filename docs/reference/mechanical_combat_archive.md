# Retired mechanical combat

Archived before the Stats overhaul. Historical reference only: initiative, turns, armor, damage dice, ammunition clips, threats, and enemy stat blocks are retired. Item/container/equipment helpers continue in `ai_adventure/items.py`. Fighting is narrated with application-owned d20 tests and health events.

## ai_adventure/combat.py

```python
from __future__ import annotations

import random
import re

from ai_adventure.inventory_storage import pounds
from typing import Any


BODY_PARTS = ["Head", "Torso", "Arms", "Hands", "Legs", "Feet"]
HAND_SLOTS = ["Main Hand", "Off Hand"]
EQUIPMENT_SLOTS = [*HAND_SLOTS, *BODY_PARTS]
DEFAULT_PLAYER_MAX_HEALTH = 20
DEFAULT_BASE_ARMOR_RATING = 10
DEFAULT_UNARMED_DAMAGE = "1d4"
DEFAULT_WEAPON_DAMAGE = "1d6"
DEFAULT_TWO_HANDED_DAMAGE = "1d10"
DEFAULT_ATTACK_SKILL = "Melee"
DEFAULT_TO_HIT_BONUS = 0
DEFAULT_ATTACK_RANGE_FEET = 5
DEFAULT_RANGED_ATTACK_RANGE_FEET = 100
COMBAT_PERSONALITIES = ("balanced", "aggressive", "cautious", "intelligent")
COMBAT_RESOLUTION_MODES = ("strict", "narrative")
COMBAT_RESOLUTION_MODE_LABELS = {
    "strict": "Strict / App-Managed Combat",
    "narrative": "Narrative / Gemini-Managed Combat",
}
COMBAT_FOCUS_LEVELS = ("low", "balanced", "high")
COMBAT_FOCUS_LABELS = {
    "low": "Low - Combat is uncommon",
    "balanced": "Balanced - Combat when the story calls for it",
    "high": "High - Combat is a major focus",
}
COMBAT_FOCUS_INSTRUCTIONS = {
    "low": (
        "Keep combat uncommon. Prefer negotiation, evasion, investigation, travel, "
        "and other non-combat challenges unless violence follows naturally from "
        "established stakes or the player pursues it."
    ),
    "balanced": (
        "Use combat when it follows naturally from the story and player choices, "
        "without forcing a fight into every conflict or avoiding warranted danger."
    ),
    "high": (
        "Make combat a major recurring part of the adventure, with varied opponents "
        "and meaningful stakes, while still respecting player choices and narrative logic."
    ),
}


def normalize_combat_preferences(raw_preferences: Any) -> dict[str, str]:
    """Returns safe player preferences for combat frequency and resolution."""

    preferences = raw_preferences if isinstance(raw_preferences, dict) else {}
    raw_resolution = str(
        preferences.get("resolution_mode", preferences.get("mode", "strict")) or "strict"
    ).strip().casefold().replace("-", "_").replace(" ", "_")
    resolution_aliases = {
        "app": "strict",
        "app_managed": "strict",
        "deterministic": "strict",
        "mechanical": "strict",
        "gemini": "narrative",
        "gemini_managed": "narrative",
        "narrated": "narrative",
    }
    resolution_mode = resolution_aliases.get(raw_resolution, raw_resolution)
    if resolution_mode not in COMBAT_RESOLUTION_MODES:
        resolution_mode = "strict"

    raw_focus = str(
        preferences.get("focus", preferences.get("frequency", "balanced")) or "balanced"
    ).strip().casefold().replace("-", "_").replace(" ", "_")
    focus_aliases = {
        "rare": "low",
        "minimal": "low",
        "normal": "balanced",
        "standard": "balanced",
        "frequent": "high",
        "combat_heavy": "high",
    }
    focus = focus_aliases.get(raw_focus, raw_focus)
    if focus not in COMBAT_FOCUS_LEVELS:
        focus = "balanced"

    return {"resolution_mode": resolution_mode, "focus": focus}


def empty_equipment() -> dict[str, str]:
    """Returns an empty equipment map for every supported slot."""

    return {slot: "" for slot in EQUIPMENT_SLOTS}


def normalize_item_metadata(
    raw_metadata: Any,
    *,
    name: str = "",
    category: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Returns clean item metadata for equipment, containers, and combat."""

    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    clean_category = str(category or "").strip()
    clean_name = str(name or "").strip()
    folded = f"{clean_category} {clean_name} {description}".casefold()
    item_type = str(metadata.get("item_type", "") or "").strip().title()

    ordinary_container = (
        item_type in {"", "Item", "Tool", "Container"}
        and clean_category.casefold() in {"", "item", "container", "tool"}
        and re.search(
            r"\b(?:bag|box|chest|crate|pouch|purse|satchel|backpack|basket|sack|"
            r"trunk|cabinet|locker|barrel|jar)\b(?:\s+(?:of|with)\b|\s*(?:\([^)]*\))?\s*$)",
            clean_name.casefold(),
        )
    )
    if isinstance(metadata.get("container"), dict) or ordinary_container:
        item_type = "Vehicle" if item_type == "Vehicle" else "Container"
    if not item_type:
        if "weapon" in folded or any(word in folded for word in _WEAPON_HINTS):
            item_type = "Weapon"
        elif "armor" in folded or "armour" in folded or "shield" in folded:
            item_type = "Armor"
        else:
            item_type = clean_category or "Item"

    basic_name = " ".join(str(metadata.get("basic_name", "") or "").split()).strip()
    clean_metadata: dict[str, Any] = {
        "item_type": item_type,
        "basic_name": basic_name[:120],
        "moveable": _item_boolean(metadata.get("moveable", metadata.get("movable", True))),
        "storable": _item_boolean(metadata.get("storable", True)),
    }
    for field in ("weight_lb", "carrying_capacity_lb"):
        if metadata.get(field) is not None:
            clean_metadata[field] = pounds(metadata[field])
    if item_type == "Vehicle":
        clean_metadata["storable"] = False
    if metadata.get("container_id"):
        clean_metadata["container_id"] = str(metadata["container_id"])
    if metadata.get("manifest_name"):
        clean_metadata["manifest_name"] = str(metadata["manifest_name"])

    if item_type == "Weapon":
        hands = _normalize_weapon_hands(metadata.get("weapon_hands"), folded)
        attack_skill = (
            str(_metadata_value(metadata, "attack_skill") or "").strip()
            or _default_attack_skill(folded)
        )
        ammunition_type = str(
            _metadata_value(
                metadata,
                "ammunition_type_required",
                "ammunition_required",
                "ammo_type_required",
            )
            or ""
        ).strip()
        clean_metadata["weapon_hands"] = hands
        clean_metadata["damage"] = weapon_damage_above_unarmed(
            metadata.get("damage", metadata.get("damage_expression")),
            weapon_hands=hands,
        )
        clean_metadata["damage_type"] = str(metadata.get("damage_type", "") or "").strip()
        clean_metadata["attack_skill"] = attack_skill
        clean_metadata["attack_range_feet"] = max(
            0,
            _safe_int(
                _metadata_value(metadata, "attack_range_feet", "range_feet"),
                (
                    DEFAULT_RANGED_ATTACK_RANGE_FEET
                    if attack_skill.casefold() == "ranged"
                    else DEFAULT_ATTACK_RANGE_FEET
                ),
            ),
        )
        clean_metadata["ammunition_type_required"] = ammunition_type

        if ammunition_type:
            clip_size = max(
                1,
                _safe_int(_metadata_value(metadata, "clip_size"), 1),
            )
            clean_metadata["clip_size"] = clip_size
            clean_metadata["bullets_per_attack"] = max(
                1,
                min(
                    clip_size,
                    _safe_int(
                        _metadata_value(
                            metadata,
                            "bullets_per_attack",
                            "amount_of_bullets_fired_per_attack",
                        ),
                        1,
                    ),
                ),
            )
        else:
            clean_metadata["clip_size"] = 0
            clean_metadata["bullets_per_attack"] = 0

        return clean_metadata

    if item_type == "Armor":
        body_parts = normalize_body_parts(
            metadata.get("covers_body_parts", metadata.get("body_parts")),
            name=clean_name,
            category=clean_category,
            description=description,
        )
        clean_metadata["covers_body_parts"] = body_parts
        clean_metadata["armor_rating"] = max(
            0,
            _safe_int(
                metadata.get("armor_rating", metadata.get("armor_bonus")),
                _default_armor_rating(clean_name, folded, body_parts),
            ),
        )
        return clean_metadata

    if item_type in {"Ammunition", "Ammo"}:
        clean_metadata["item_type"] = "Ammunition"
        clean_metadata["ammunition_type"] = (
            str(
                _metadata_value(
                    metadata,
                    "ammunition_type",
                    "ammo_type",
                )
                or ""
            ).strip()
            or clean_name
        )
        return clean_metadata

    if item_type in {"Container", "Vehicle"}:
        clean_metadata["container"] = _normalize_container_metadata(
            metadata.get("container", metadata)
        )
        return clean_metadata

    return clean_metadata


def _normalize_container_metadata(raw_container: Any) -> dict[str, Any]:
    """Returns the durable state and exact hidden contents of one container."""

    container = dict(raw_container) if isinstance(raw_container, dict) else {}
    is_open = bool(
        container.get("is_open", container.get("container_is_open", False))
    )
    contents_taken = bool(
        container.get(
            "contents_taken",
            container.get("container_contents_taken", False),
        )
    )
    is_locked = bool(
        container.get("is_locked", container.get("container_is_locked", False))
    )
    is_trapped = bool(
        container.get("is_trapped", container.get("container_is_trapped", False))
    )
    raw_contents = container.get("contents", {})

    if not isinstance(raw_contents, dict):
        raw_contents = {}

    currency_base_units = max(
        0,
        _safe_int(
            raw_contents.get(
                "currency_base_units",
                container.get("currency_base_units", 0),
            ),
            0,
        ),
    )
    raw_items = raw_contents.get("items", container.get("items", []))

    return {
        "is_open": is_open,
        "contents_known": _item_boolean(container.get("contents_known", is_open)),
        "contents_initialized": bool(container.get(
            "contents_initialized", "contents" in container or "currency_base_units" in container or "items" in container
        ) or currency_base_units or raw_items),
        "contents_taken": bool(is_open and contents_taken),
        "is_locked": is_locked,
        "lockpick_skill": (
            str(container.get("lockpick_skill", "") or "").strip()
            or "Lockpicking"
        ),
        "lockpick_dc": (
            max(1, _safe_int(container.get("lockpick_dc"), 10))
            if is_locked
            else 0
        ),
        "lockpick_failure_consequence": str(
            container.get("lockpick_failure_consequence", "") or ""
        ).strip(),
        "is_trapped": is_trapped,
        "trap_notice_skill": (
            str(container.get("trap_notice_skill", "") or "").strip()
            or "Perception"
        ),
        "trap_notice_dc": (
            max(1, _safe_int(container.get("trap_notice_dc"), 10))
            if is_trapped
            else 0
        ),
        "trap_disarm_skill": (
            str(container.get("trap_disarm_skill", "") or "").strip()
            or "Sleight of Hand"
        ),
        "trap_disarm_dc": (
            max(1, _safe_int(container.get("trap_disarm_dc"), 10))
            if is_trapped
            else 0
        ),
        "trap_failure_consequence": str(
            container.get("trap_failure_consequence", "") or ""
        ).strip(),
        "contents": {
            "currency_base_units": currency_base_units,
            "items": _normalize_container_contents_items(raw_items),
        },
    }


def _item_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in {"false", "no", "0"}:
        return False
    return True


def _normalize_container_contents_items(raw_items: Any) -> list[Any]:
    """Preserve ID references; legacy/proposed definitions are materialized on write."""

    if not isinstance(raw_items, list):
        return []

    items: list[Any] = []

    for raw_item in raw_items:
        if isinstance(raw_item, str) and raw_item.strip():
            items.append(raw_item.strip())
            continue
        if not isinstance(raw_item, dict):
            continue

        name = str(raw_item.get("name", raw_item.get("item_name", "")) or "").strip()

        if not name:
            continue

        category = str(
            raw_item.get("category", raw_item.get("item_type", "Item")) or "Item"
        ).strip() or "Item"
        description = str(raw_item.get("description", "") or "").strip()
        raw_metadata = raw_item.get("metadata", raw_item)
        items.append(
            {
                "name": name,
                "category": category,
                "quantity": max(
                    1,
                    _safe_int(
                        raw_item.get("quantity", raw_item.get("amount", 1)),
                        1,
                    ),
                ),
                "description": description,
                "value_base_units": max(
                    0,
                    _safe_int(raw_item.get("value_base_units"), 0),
                ),
                "metadata": normalize_item_metadata(
                    raw_metadata,
                    name=name,
                    category=category,
                    description=description,
                ),
            }
        )

    return items


def normalize_body_parts(
    raw_body_parts: Any,
    *,
    name: str = "",
    category: str = "",
    description: str = "",
) -> list[str]:
    """Returns supported body parts covered by one armor item."""

    values: list[str] = []

    if isinstance(raw_body_parts, str):
        values = [part.strip() for part in re.split(r"[,;/|]+", raw_body_parts)]
    elif isinstance(raw_body_parts, list):
        values = [str(part).strip() for part in raw_body_parts]

    clean_parts: list[str] = []

    for value in values:
        normalized = _normalize_body_part(value)

        if normalized and normalized not in clean_parts:
            clean_parts.append(normalized)

    if clean_parts:
        return clean_parts

    folded = f"{category} {name} {description}".casefold()

    if "shield" in folded:
        return ["Off Hand"]
    if any(word in folded for word in ["full plate", "plate armor", "plate armour"]):
        return list(BODY_PARTS)
    if any(word in folded for word in ["helmet", "helm", "hat", "hood"]):
        return ["Head"]
    if any(word in folded for word in ["gauntlet", "glove"]):
        return ["Hands"]
    if any(word in folded for word in ["boot", "shoe", "sabatons"]):
        return ["Feet"]
    if any(word in folded for word in ["greave", "leggings", "trouser"]):
        return ["Legs"]
    if "bracer" in folded or "sleeve" in folded:
        return ["Arms"]
    if "leather" in folded:
        return ["Torso", "Arms", "Legs"]

    return ["Torso"]


def normalize_damage_expression(raw_damage: Any, *, default: str = DEFAULT_WEAPON_DAMAGE) -> str:
    """Returns a compact NdM+B damage expression."""

    text = str(raw_damage or "").strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d*)d(\d+)([+-]\d+)?", text)

    if not match:
        return default

    count = int(match.group(1) or "1")
    sides = int(match.group(2))
    bonus = int(match.group(3) or "0")

    if count <= 0 or sides <= 0 or count > 20 or sides > 1000:
        return default

    expression = f"{count}d{sides}"

    if bonus > 0:
        expression += f"+{bonus}"
    elif bonus < 0:
        expression += str(bonus)

    return expression


def weapon_damage_above_unarmed(
    raw_damage: Any,
    *,
    weapon_hands: str = "one-handed",
) -> str:
    """Returns weapon damage that is strictly better than base unarmed damage."""

    default = (
        DEFAULT_TWO_HANDED_DAMAGE
        if str(weapon_hands).casefold() == "two-handed"
        else DEFAULT_WEAPON_DAMAGE
    )
    expression = normalize_damage_expression(raw_damage, default=default)

    if average_damage(expression) <= average_damage(DEFAULT_UNARMED_DAMAGE):
        return default

    return expression


def roll_damage_expression(
    damage_expression: Any,
    *,
    rng: random.Random | None = None,
) -> tuple[int, str]:
    """Rolls an NdM+B damage expression and returns total plus roll detail."""

    expression = normalize_damage_expression(damage_expression, default=DEFAULT_UNARMED_DAMAGE)
    match = re.fullmatch(r"(\d+)d(\d+)([+-]\d+)?", expression)

    if match is None:
        expression = DEFAULT_UNARMED_DAMAGE
        match = re.fullmatch(r"(\d+)d(\d+)([+-]\d+)?", expression)

    assert match is not None
    roller = rng or random
    count = int(match.group(1))
    sides = int(match.group(2))
    bonus = int(match.group(3) or "0")
    rolls = [roller.randint(1, sides) for _ in range(count)]
    total = max(0, sum(rolls) + bonus)
    detail = "+".join(str(roll) for roll in rolls)

    if bonus > 0:
        detail += f"+{bonus}"
    elif bonus < 0:
        detail += str(bonus)

    return total, f"{expression} ({detail})"


def normalize_equipment(raw_equipment: Any, inventory_items: list[dict[str, Any]]) -> dict[str, str]:
    """Returns equipment that is valid for current inventory and slot rules."""

    equipment = empty_equipment()

    if not isinstance(raw_equipment, dict):
        return equipment

    inventory_by_name = {
        str(item.get("name", "")).casefold(): item
        for item in inventory_items
        if str(item.get("name", "")).strip()
    }
    used_counts: dict[str, int] = {}
    equipped_armor: set[str] = set()

    for slot in EQUIPMENT_SLOTS:
        item_name = str(raw_equipment.get(slot, "") or "").strip()

        if not item_name:
            continue

        item = inventory_by_name.get(item_name.casefold())

        if item is None or not item_is_valid_for_slot(item, slot):
            continue

        canonical_name = str(item.get("name", item_name))
        folded_name = canonical_name.casefold()
        metadata = item_metadata(item)
        item_type = str(metadata.get("item_type", "")).casefold()

        if item_type == "armor":
            if folded_name in equipped_armor:
                continue

            covered_slots = [
                str(covered_slot)
                for covered_slot in metadata.get("covers_body_parts", [])
                if str(covered_slot) in EQUIPMENT_SLOTS
            ]

            if (
                not covered_slots
                or used_counts.get(folded_name, 0) >= _inventory_quantity(item)
                or any(equipment[covered_slot] for covered_slot in covered_slots)
            ):
                continue

            for covered_slot in covered_slots:
                equipment[covered_slot] = canonical_name

            used_counts[folded_name] = used_counts.get(folded_name, 0) + 1
            equipped_armor.add(folded_name)
            continue

        if (
            slot == "Off Hand"
            and equipment["Main Hand"]
            and item_weapon_hands(
                inventory_by_name[equipment["Main Hand"].casefold()]
            )
            == "two-handed"
        ):
            continue

        if used_counts.get(folded_name, 0) >= _inventory_quantity(item):
            continue

        equipment[slot] = canonical_name
        used_counts[folded_name] = used_counts.get(folded_name, 0) + 1

    return equipment


def equipment_item_counts(
    equipment: dict[str, str],
    inventory_items: list[dict[str, Any]],
) -> dict[str, int]:
    """Returns how many owned instances are allocated by an equipment map."""

    inventory_by_name = {
        str(item.get("name", "")).casefold(): item
        for item in inventory_items
        if str(item.get("name", "")).strip()
    }
    counts: dict[str, int] = {}
    counted_armor: set[str] = set()

    for slot in EQUIPMENT_SLOTS:
        folded_name = str(equipment.get(slot, "") or "").strip().casefold()

        if not folded_name:
            continue

        item = inventory_by_name.get(folded_name)

        if item is None:
            continue

        if str(item_metadata(item).get("item_type", "")).casefold() == "armor":
            if folded_name in counted_armor:
                continue
            counted_armor.add(folded_name)

        counts[folded_name] = counts.get(folded_name, 0) + 1

    return counts


def item_is_valid_for_slot(item: dict[str, Any], slot: str) -> bool:
    """Returns whether item can be equipped in slot."""

    metadata = item_metadata(item)
    item_type = str(metadata.get("item_type", "")).casefold()

    if slot == "Main Hand":
        return item_type == "weapon"

    if slot == "Off Hand":
        if item_type == "weapon":
            return str(metadata.get("weapon_hands", "")).casefold() == "one-handed"
        if item_type == "armor":
            return "Off Hand" in list(metadata.get("covers_body_parts", []))
        return False

    if item_type != "armor":
        return False

    return slot in list(metadata.get("covers_body_parts", []))


def _inventory_quantity(item: dict[str, Any]) -> int:
    """Returns an inventory stack's usable quantity."""

    return max(0, _safe_int(item.get("quantity"), 1))


def armor_rating_from_equipment(
    equipment: dict[str, str],
    inventory_items: list[dict[str, Any]],
    *,
    base_armor_rating: int = DEFAULT_BASE_ARMOR_RATING,
) -> int:
    """Computes armor rating from unique equipped armor pieces."""

    inventory_by_name = {
        str(item.get("name", "")).casefold(): item
        for item in inventory_items
        if str(item.get("name", "")).strip()
    }
    armor_rating = max(0, int(base_armor_rating))
    counted_items: set[str] = set()

    for slot, item_name in equipment.items():
        if slot not in EQUIPMENT_SLOTS:
            continue

        item = inventory_by_name.get(str(item_name).casefold())

        if item is None or str(item_name).casefold() in counted_items:
            continue

        metadata = item_metadata(item)

        if str(metadata.get("item_type", "")).casefold() != "armor":
            continue

        armor_rating += max(0, _safe_int(metadata.get("armor_rating"), 0))
        counted_items.add(str(item_name).casefold())

    return armor_rating


def equipped_weapon_damage(
    equipment: dict[str, str],
    inventory_items: list[dict[str, Any]],
) -> str:
    """Returns the active main-hand weapon damage expression."""

    inventory_by_name = {
        str(item.get("name", "")).casefold(): item
        for item in inventory_items
        if str(item.get("name", "")).strip()
    }
    main_item = inventory_by_name.get(str(equipment.get("Main Hand", "")).casefold())

    if main_item is None:
        return DEFAULT_UNARMED_DAMAGE

    metadata = item_metadata(main_item)

    if str(metadata.get("item_type", "")).casefold() != "weapon":
        return DEFAULT_UNARMED_DAMAGE

    return normalize_damage_expression(metadata.get("damage"), default=DEFAULT_WEAPON_DAMAGE)


def equipped_weapon_attack_skill(
    equipment: dict[str, str],
    inventory_items: list[dict[str, Any]],
) -> str:
    """Returns the skill used to calculate the player's to-hit bonus."""

    inventory_by_name = {
        str(item.get("name", "")).casefold(): item
        for item in inventory_items
        if str(item.get("name", "")).strip()
    }
    main_item = inventory_by_name.get(str(equipment.get("Main Hand", "")).casefold())

    if main_item is None:
        return DEFAULT_ATTACK_SKILL

    metadata = item_metadata(main_item)

    if str(metadata.get("item_type", "")).casefold() != "weapon":
        return DEFAULT_ATTACK_SKILL

    return str(metadata.get("attack_skill", DEFAULT_ATTACK_SKILL)).strip() or DEFAULT_ATTACK_SKILL


def equipped_weapon_combat_profile(
    equipment: dict[str, str],
    inventory_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Returns range and ammunition metadata for the active weapon."""

    inventory_by_name = {
        str(item.get("name", "")).casefold(): item
        for item in inventory_items
        if str(item.get("name", "")).strip()
    }
    weapon_name = str(equipment.get("Main Hand", "") or "").strip()
    main_item = inventory_by_name.get(weapon_name.casefold())

    if main_item is None:
        return {
            "weapon_name": "",
            "ammunition_type_required": "",
            "clip_size": 0,
            "bullets_per_attack": 0,
        }

    metadata = item_metadata(main_item)

    if str(metadata.get("item_type", "")).casefold() != "weapon":
        return {
            "weapon_name": "",
            "ammunition_type_required": "",
            "clip_size": 0,
            "bullets_per_attack": 0,
        }

    return {
        "weapon_name": str(main_item.get("name", weapon_name)),
        "ammunition_type_required": str(
            metadata.get("ammunition_type_required", "")
        ).strip(),
        "clip_size": max(0, _safe_int(metadata.get("clip_size"), 0)),
        "bullets_per_attack": max(
            0,
            _safe_int(metadata.get("bullets_per_attack"), 0),
        ),
    }


def attack_bonus_from_skills(
    skill_name: str,
    skills: list[dict[str, Any]],
) -> int:
    """Returns the saved bonus for the named combat skill."""

    for skill in skills:
        if str(skill.get("name", "")).casefold() != skill_name.casefold():
            continue

        return max(-99, min(99, _safe_int(skill.get("bonus"), DEFAULT_TO_HIT_BONUS)))

    return DEFAULT_TO_HIT_BONUS


def item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Returns normalized metadata for an inventory row."""

    return normalize_item_metadata(
        item.get("metadata", {}),
        name=str(item.get("name", "")),
        category=str(item.get("category", "")),
        description=str(item.get("description", "")),
    )


def item_weapon_hands(item: dict[str, Any]) -> str:
    """Returns the weapon hand requirement for an item, if any."""

    metadata = item_metadata(item)

    if str(metadata.get("item_type", "")).casefold() != "weapon":
        return ""

    return str(metadata.get("weapon_hands", "one-handed"))


def normalize_combat_state(raw_state: Any) -> dict[str, Any]:
    """Returns a safe saved combat-state dictionary."""

    state = dict(raw_state) if isinstance(raw_state, dict) else {}
    combatants = [
        _normalize_combatant(combatant, index)
        for index, combatant in enumerate(state.get("combatants", []))
        if isinstance(combatant, dict)
    ]
    turn_index = _safe_int(state.get("turn_index"), 0)

    if combatants:
        turn_index = max(0, min(turn_index, len(combatants) - 1))
    else:
        turn_index = 0

    _assign_display_names(combatants)
    assign_combat_threat_levels(combatants)

    return {
        "active": bool(state.get("active", False)) and bool(combatants),
        "round": max(1, _safe_int(state.get("round"), 1)),
        "turn_index": turn_index,
        "combatants": combatants,
        "log": [
            str(entry)
            for entry in state.get("log", [])
            if str(entry).strip()
        ][-80:],
    }


def next_living_index(combatants: list[dict[str, Any]], start_index: int) -> int:
    """Returns the next combatant index that can act."""

    if not combatants:
        return 0

    for offset in range(1, len(combatants) + 1):
        index = (start_index + offset) % len(combatants)

        if not bool(combatants[index].get("defeated", False)):
            return index

    return start_index


def combat_team_defeated(combatants: list[dict[str, Any]], team: str) -> bool:
    """Returns whether every combatant on team is defeated."""

    members = [combatant for combatant in combatants if combatant.get("team") == team]
    return bool(members) and all(bool(member.get("defeated", False)) for member in members)


def roll_combat_initiative(
    combatants: list[dict[str, Any]],
    *,
    rng: Any = None,
) -> list[dict[str, Any]]:
    """Rolls initiative and returns combatants in descending turn order."""

    roller = rng or random

    for combatant in combatants:
        initiative_roll = roller.randint(1, 20)
        initiative_bonus = _bounded_int(
            combatant.get("initiative_bonus"),
            -99,
            99,
            0,
        )
        combatant["initiative_roll"] = initiative_roll
        combatant["initiative_total"] = initiative_roll + initiative_bonus

    combatants.sort(
        key=lambda combatant: (
            -_safe_int(combatant.get("initiative_total"), 0),
            -_safe_int(combatant.get("initiative_bonus"), 0),
            str(combatant.get("id", "")),
        )
    )
    _assign_display_names(combatants)
    return combatants


def combatant_display_name(combatant: dict[str, Any]) -> str:
    """Returns the unique player-facing name for a combatant."""

    return str(
        combatant.get("display_name")
        or combatant.get("name")
        or "Combatant"
    )


def attack_hit_probability(to_hit_bonus: int, armor_rating: int) -> float:
    """Returns the exact d20 hit probability with natural-one/twenty rules."""

    successful_rolls = sum(
        1
        for roll in range(1, 21)
        if roll == 20
        or (roll != 1 and roll + int(to_hit_bonus) >= int(armor_rating))
    )
    return successful_rolls / 20.0


def average_damage(damage_expression: Any) -> float:
    """Returns the mathematical average of a normalized damage expression."""

    expression = normalize_damage_expression(
        damage_expression,
        default=DEFAULT_UNARMED_DAMAGE,
    )
    match = re.fullmatch(r"(\d+)d(\d+)([+-]\d+)?", expression)

    if match is None:
        return 1.0

    count = int(match.group(1))
    sides = int(match.group(2))
    bonus = int(match.group(3) or "0")
    return max(1.0, (count * (sides + 1) / 2.0) + bonus)


def calculate_team_threat_levels(
    combatants: list[dict[str, Any]],
    team: str,
) -> dict[str, int]:
    """
    Returns whole-percent threat for one living team, totaling exactly 100.

    Maximum health, armor rating, and average weapon damage contribute equally:
    each combatant receives the average of its share of those three party totals.
    """

    living_members = [
        combatant
        for combatant in combatants
        if combatant.get("team") == team
        and not combatant.get("defeated")
        and int(combatant.get("current_health", 0)) > 0
    ]

    if not living_members:
        return {}
    if len(living_members) == 1:
        return {str(living_members[0].get("id", "")): 100}

    health_values = [
        max(1, int(combatant.get("max_health", 1)))
        for combatant in living_members
    ]
    armor_values = [
        max(1, int(combatant.get("armor_rating", 1)))
        for combatant in living_members
    ]
    damage_values = [
        average_damage(combatant.get("damage", DEFAULT_UNARMED_DAMAGE))
        for combatant in living_members
    ]
    total_health = sum(health_values)
    total_armor = sum(armor_values)
    total_damage = sum(damage_values)
    scores = [
        (health / total_health)
        + (armor / total_armor)
        + (damage / total_damage)
        for health, armor, damage in zip(
            health_values,
            armor_values,
            damage_values,
            strict=True,
        )
    ]
    percentages = _whole_percentages(scores)
    return {
        str(combatant.get("id", "")): percentage
        for combatant, percentage in zip(
            living_members,
            percentages,
            strict=True,
        )
    }


def assign_combat_threat_levels(combatants: list[dict[str, Any]]) -> None:
    """Writes recalculated threat percentages onto both combat teams."""

    levels = {
        **calculate_team_threat_levels(combatants, "party"),
        **calculate_team_threat_levels(combatants, "enemy"),
    }

    for combatant in combatants:
        combatant["threat_level"] = levels.get(
            str(combatant.get("id", "")),
            0,
        )


def _normalize_combatant(raw_combatant: dict[str, Any], index: int) -> dict[str, Any]:
    """Returns one clean combatant record."""

    max_health = max(1, _safe_int(raw_combatant.get("max_health"), 10))
    current_health = max(
        0,
        min(_safe_int(raw_combatant.get("current_health"), max_health), max_health),
    )
    team = str(raw_combatant.get("team", "enemy")).strip().casefold()

    if team not in {"party", "enemy"}:
        team = "enemy"

    clip_size = max(0, _safe_int(raw_combatant.get("clip_size"), 0))
    ammunition_type = str(
        raw_combatant.get("ammunition_type_required", "") or ""
    ).strip()
    bullets_per_attack = (
        max(
            1,
            min(
                clip_size,
                _safe_int(raw_combatant.get("bullets_per_attack"), 1),
            ),
        )
        if ammunition_type and clip_size > 0
        else 0
    )
    personality = str(
        raw_combatant.get("personality", "balanced") or "balanced"
    ).strip().casefold()

    if personality not in COMBAT_PERSONALITIES:
        personality = "balanced"

    return {
        "id": str(raw_combatant.get("id", f"combatant-{index + 1}")),
        "npc_id": str(raw_combatant.get("npc_id", "") or "").strip(),
        "name": str(raw_combatant.get("name", f"Combatant {index + 1}")).strip()
        or f"Combatant {index + 1}",
        "display_name": str(raw_combatant.get("display_name", "")).strip(),
        "team": team,
        "current_health": current_health,
        "max_health": max_health,
        "armor_rating": max(1, _safe_int(raw_combatant.get("armor_rating"), 10)),
        "to_hit_bonus": max(
            -99,
            min(
                99,
                _safe_int(
                    raw_combatant.get("to_hit_bonus"),
                    DEFAULT_TO_HIT_BONUS,
                ),
            ),
        ),
        "initiative_bonus": _bounded_int(
            raw_combatant.get("initiative_bonus"),
            -99,
            99,
            0,
        ),
        "initiative_roll": _bounded_int(
            raw_combatant.get("initiative_roll"),
            0,
            20,
            0,
        ),
        "initiative_total": _safe_int(
            raw_combatant.get("initiative_total"),
            0,
        ),
        "threat_level": 0,
        "personality": personality,
        "weapon_name": str(raw_combatant.get("weapon_name", "") or "").strip(),
        "ammunition_type_required": ammunition_type,
        "clip_size": clip_size,
        "clip_ammo": _bounded_int(
            raw_combatant.get("clip_ammo"),
            0,
            clip_size,
            clip_size,
        ),
        "bullets_per_attack": bullets_per_attack,
        "reserve_ammo": max(
            0,
            _safe_int(raw_combatant.get("reserve_ammo"), 0),
        ),
        "damage": normalize_damage_expression(
            raw_combatant.get("damage"),
            default=DEFAULT_WEAPON_DAMAGE,
        ),
        "status_effects": [
            str(effect).strip()
            for effect in raw_combatant.get("status_effects", [])
            if str(effect).strip()
        ],
        "loot": [
            str(item).strip()
            for item in raw_combatant.get("loot", [])
            if str(item).strip()
        ],
        "defeated": current_health <= 0 or bool(raw_combatant.get("defeated", False)),
    }


def _assign_display_names(combatants: list[dict[str, Any]]) -> None:
    """Assigns stable numbered labels when base names repeat."""

    totals: dict[str, int] = {}

    for combatant in combatants:
        folded_name = str(combatant.get("name", "")).casefold()
        totals[folded_name] = totals.get(folded_name, 0) + 1

    occurrences: dict[str, int] = {}

    for combatant in combatants:
        name = str(combatant.get("name", "") or "Combatant")
        folded_name = name.casefold()
        occurrences[folded_name] = occurrences.get(folded_name, 0) + 1
        combatant["display_name"] = (
            f"{name} ({occurrences[folded_name]})"
            if totals.get(folded_name, 0) > 1
            else name
        )


def _whole_percentages(scores: list[float]) -> list[int]:
    """Normalizes positive scores to whole percentages totaling exactly 100."""

    if not scores:
        return []

    total = sum(max(0.0, score) for score in scores)

    if total <= 0:
        scores = [1.0 for _score in scores]
        total = float(len(scores))

    raw_percentages = [
        max(0.0, score) * 100.0 / total
        for score in scores
    ]
    percentages = [int(percentage) for percentage in raw_percentages]

    if len(percentages) <= 100:
        for index, percentage in enumerate(percentages):
            if percentage == 0:
                percentages[index] = 1

    remainder = 100 - sum(percentages)

    if remainder > 0:
        order = sorted(
            range(len(scores)),
            key=lambda index: (
                raw_percentages[index] - int(raw_percentages[index]),
                scores[index],
                -index,
            ),
            reverse=True,
        )
        for offset in range(remainder):
            percentages[order[offset % len(order)]] += 1
    elif remainder < 0:
        order = sorted(
            range(len(scores)),
            key=lambda index: (
                percentages[index] > 1,
                percentages[index],
                index,
            ),
            reverse=True,
        )
        for _offset in range(-remainder):
            for index in order:
                if percentages[index] > 1:
                    percentages[index] -= 1
                    break

    return percentages


def _normalize_weapon_hands(raw_hands: Any, folded_text: str) -> str:
    """Returns one-handed or two-handed."""

    hands = str(raw_hands or "").strip().casefold().replace("_", "-")

    if hands in {"two-handed", "2-handed", "two handed", "2h"}:
        return "two-handed"

    if "two-handed" in folded_text or any(word in folded_text for word in _TWO_HANDED_HINTS):
        return "two-handed"

    return "one-handed"


def _normalize_body_part(value: str) -> str:
    """Maps flexible body-part text to a supported slot."""

    folded = value.strip().casefold().replace("_", " ")

    aliases = {
        "head": "Head",
        "helmet": "Head",
        "helm": "Head",
        "torso": "Torso",
        "body": "Torso",
        "chest": "Torso",
        "arms": "Arms",
        "arm": "Arms",
        "hands": "Hands",
        "hand": "Hands",
        "legs": "Legs",
        "leg": "Legs",
        "feet": "Feet",
        "foot": "Feet",
        "boots": "Feet",
        "off hand": "Off Hand",
        "off-hand": "Off Hand",
        "shield": "Off Hand",
    }
    return aliases.get(folded, "")


def _default_armor_rating(name: str, folded_text: str, body_parts: list[str]) -> int:
    """Guesses an armor rating for items without explicit stats."""

    if "shield" in folded_text:
        return 2
    if "full plate" in folded_text or "plate armor" in folded_text or "plate armour" in folded_text:
        return 6
    if "chain" in folded_text or "mail" in folded_text:
        return 4
    if "leather" in folded_text:
        return 2
    if len(body_parts) == 1:
        return 1
    if len(body_parts) >= 4:
        return 4
    return 2


def _default_attack_skill(folded_text: str) -> str:
    """Infers the ordinary attack skill for a weapon."""

    if any(word in folded_text for word in _RANGED_WEAPON_HINTS):
        return "Ranged"

    return DEFAULT_ATTACK_SKILL


def _metadata_value(metadata: dict[str, Any], *names: str) -> Any:
    """Reads a metadata field while tolerating common key casing styles."""

    normalized_metadata = {
        re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_"): value
        for key, value in metadata.items()
    }

    for name in names:
        normalized_name = re.sub(
            r"[^a-z0-9]+",
            "_",
            name.casefold(),
        ).strip("_")

        if normalized_name in normalized_metadata:
            return normalized_metadata[normalized_name]

    return None


def _bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    """Safely converts and clamps an integer."""

    return max(minimum, min(maximum, _safe_int(value, default)))


def _safe_int(value: Any, default: int) -> int:
    """Safely converts value to int."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_WEAPON_HINTS = {
    "sword",
    "dagger",
    "axe",
    "mace",
    "spear",
    "bow",
    "crossbow",
    "staff",
    "hammer",
    "blade",
    "pistol",
    "rifle",
}
_TWO_HANDED_HINTS = {
    "greatsword",
    "greataxe",
    "longbow",
    "shortbow",
    "crossbow",
    "rifle",
    "halberd",
    "pike",
}
_RANGED_WEAPON_HINTS = {
    "bow",
    "crossbow",
    "pistol",
    "rifle",
    "firearm",
    "sling",
}

```

## ai_adventure/ui/screens/combat.py

```python
from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class CombatScreen(RepositoryBackedWidget):
    """Deterministic saved combat manager."""

    def __init__(self, *, playtesting_tools: bool = False) -> None:
        super().__init__()

        self.playtesting_tools = bool(playtesting_tools)
        self._scheduled_npc_actor_id = ""
        self._scheduled_npc_repository: SaveRepository | None = None
        self.npc_turn_timer = QTimer(self)
        self.npc_turn_timer.setSingleShot(True)
        self.npc_turn_timer.setInterval(NPC_TURN_DELAY_MS)
        self.npc_turn_timer.timeout.connect(self._resolve_scheduled_npc_turn)
        self.status_label = QLabel("No active combat.")
        self.combatants_table = _AppTableWidget(0, 11)
        self.combatants_table.setHorizontalHeaderLabels(
            [
                "Turn",
                "Name",
                "Team",
                "Initiative",
                "Health",
                "Armor",
                "To Hit",
                "Threat",
                "Ammo",
                "Damage",
                "Loot/Status",
            ]
        )
        self.combatants_table.horizontalHeader().setStretchLastSection(True)
        self.combatants_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.combatants_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        self.target_combo = QComboBox()
        self.attack_button = QPushButton("Attack / Resolve Turn")
        self.attack_button.clicked.connect(self._resolve_current_turn)
        self.end_turn_button = QPushButton("End Turn")
        self.end_turn_button.clicked.connect(self._end_turn_without_attack)
        self.reload_button = QPushButton("Reload / End Turn")
        self.reload_button.clicked.connect(self._reload_current_weapon)
        self.resolve_button = QPushButton("Mark Combat Resolved")
        self.resolve_button.clicked.connect(self._resolve_combat_manually)
        self.team_combo = QComboBox()
        self.team_combo.addItem("Enemy", "enemy")
        self.team_combo.addItem("Player Party", "party")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Bandit, wolf, guard ally...")
        self.health_input = QSpinBox()
        self.health_input.setRange(1, 9999)
        self.health_input.setValue(8)
        self.armor_input = QSpinBox()
        self.armor_input.setRange(1, 99)
        self.armor_input.setValue(10)
        self.to_hit_input = QSpinBox()
        self.to_hit_input.setRange(-99, 99)
        self.to_hit_input.setValue(0)
        self.initiative_input = QSpinBox()
        self.initiative_input.setRange(-99, 99)
        self.personality_combo = QComboBox()

        for personality in COMBAT_PERSONALITIES:
            self.personality_combo.addItem(personality.title(), personality)

        self.ammunition_type_input = QLineEdit()
        self.ammunition_type_input.setPlaceholderText(
            "Optional, e.g. 9mm Round"
        )
        self.clip_size_input = QSpinBox()
        self.clip_size_input.setRange(0, 9999)
        self.clip_ammo_input = QSpinBox()
        self.clip_ammo_input.setRange(0, 9999)
        self.clip_size_input.valueChanged.connect(self._sync_clip_inputs)
        self.bullets_per_attack_input = QSpinBox()
        self.bullets_per_attack_input.setRange(1, 9999)
        self.reserve_ammo_input = QSpinBox()
        self.reserve_ammo_input.setRange(0, 999999)
        self.damage_input = QLineEdit("1d6")
        self.loot_input = QLineEdit()
        self.loot_input.setPlaceholderText("Optional loot names separated by commas")
        self.add_combatant_button = QPushButton("Add Combatant")
        self.add_combatant_button.clicked.connect(self._add_combatant)
        self.start_button = QPushButton("Start Combat")
        self.start_button.clicked.connect(self._start_combat)

        self.adjust_target_combo = QComboBox()
        self.adjust_amount_input = QSpinBox()
        self.adjust_amount_input.setRange(1, 9999)
        self.adjust_amount_input.setValue(1)
        self.damage_button = QPushButton("Apply Damage")
        self.damage_button.clicked.connect(lambda: self._adjust_health(-self.adjust_amount_input.value()))
        self.heal_button = QPushButton("Heal")
        self.heal_button.clicked.connect(lambda: self._adjust_health(self.adjust_amount_input.value()))

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        action_group = QGroupBox("Current Turn")
        action_layout = QFormLayout()
        action_layout.addRow("Target:", self.target_combo)
        action_layout.addRow(
            _button_row(
                self.attack_button,
                self.reload_button,
                self.end_turn_button,
                self.resolve_button,
            )
        )
        action_group.setLayout(action_layout)

        self.add_group = QGroupBox("Combatants")
        add_layout = QFormLayout()
        add_layout.addRow("Team:", self.team_combo)
        add_layout.addRow("Name:", self.name_input)
        add_layout.addRow("Health:", self.health_input)
        add_layout.addRow("Armor Rating:", self.armor_input)
        add_layout.addRow("To-Hit Bonus:", self.to_hit_input)
        add_layout.addRow("Initiative Bonus:", self.initiative_input)
        add_layout.addRow("Personality:", self.personality_combo)
        add_layout.addRow("Ammunition Type:", self.ammunition_type_input)
        add_layout.addRow("Clip Size:", self.clip_size_input)
        add_layout.addRow("Loaded Ammo:", self.clip_ammo_input)
        add_layout.addRow("Bullets / Attack:", self.bullets_per_attack_input)
        add_layout.addRow("Reserve Ammo:", self.reserve_ammo_input)
        add_layout.addRow("Damage:", self.damage_input)
        add_layout.addRow("Loot:", self.loot_input)
        add_layout.addRow(_button_row(self.start_button, self.add_combatant_button))
        self.add_group.setLayout(add_layout)

        self.adjust_group = QGroupBox("Damage and Recovery")
        adjust_layout = QFormLayout()
        adjust_layout.addRow("Combatant:", self.adjust_target_combo)
        adjust_layout.addRow("Amount:", self.adjust_amount_input)
        adjust_layout.addRow(_button_row(self.damage_button, self.heal_button))
        self.adjust_group.setLayout(adjust_layout)

        self.resolve_button.setVisible(self.playtesting_tools)
        self.add_group.setVisible(self.playtesting_tools)
        self.adjust_group.setVisible(self.playtesting_tools)

        controls = QVBoxLayout()
        controls.addWidget(action_group)
        controls.addWidget(self.add_group)
        controls.addWidget(self.adjust_group)
        controls.addStretch()
        controls_widget = QWidget()
        controls_widget.setLayout(controls)
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setWidget(controls_widget)

        main_row = QHBoxLayout()
        main_row.addWidget(self.combatants_table, stretch=2)
        main_row.addWidget(controls_scroll, stretch=1)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addLayout(main_row)
        layout.addWidget(QLabel("Combat Log"))
        layout.addWidget(self.log_output)
        self.setLayout(layout)

    def set_repository(self, repository: SaveRepository | None) -> None:
        """Cancels delayed actions before changing the active save."""

        self._cancel_scheduled_npc_turn()
        super().set_repository(repository)

    def refresh(self) -> None:
        """Reloads saved combat state."""

        repository = self.repository()

        if repository is None:
            self._cancel_scheduled_npc_turn()
            self.status_label.setText("No active combat.")
            self.combatants_table.setRowCount(0)
            self.target_combo.clear()
            self.adjust_target_combo.clear()
            self.log_output.clear()
            self._sync_buttons(False)
            return

        combat_state = repository.get_combat_state()
        if combat_state.get("active"):
            self._sync_player_loadout(repository, combat_state)
        self._render_combat_state(combat_state)
        if not combat_state.get("active") and self._uses_narrative_combat(repository):
            self.status_label.setText(
                "Narrative combat is enabled. Gemini resolves fights in Story."
            )

    def _schedule_npc_turn(self, combat_state: dict[str, Any]) -> None:
        """Schedules the current NPC to act after the reading delay."""

        repository = self.repository()
        combatants = combat_state.get("combatants", [])

        if (
            repository is None
            or not combat_state.get("active")
            or not combatants
        ):
            self._cancel_scheduled_npc_turn()
            return

        actor = combatants[int(combat_state.get("turn_index", 0))]
        actor_id = str(actor.get("id", ""))

        if actor_id == "player" or actor.get("defeated"):
            self._cancel_scheduled_npc_turn()
            return

        if (
            self.npc_turn_timer.isActive()
            and self._scheduled_npc_actor_id == actor_id
            and self._scheduled_npc_repository is repository
        ):
            return

        self.npc_turn_timer.stop()
        self._scheduled_npc_actor_id = actor_id
        self._scheduled_npc_repository = repository
        self.npc_turn_timer.start(NPC_TURN_DELAY_MS)

    def _cancel_scheduled_npc_turn(self) -> None:
        """Cancels any NPC action waiting on the reading delay."""

        if hasattr(self, "npc_turn_timer"):
            self.npc_turn_timer.stop()
        self._scheduled_npc_actor_id = ""
        self._scheduled_npc_repository = None

    def _resolve_scheduled_npc_turn(self) -> None:
        """Resolves the still-current NPC after its delay expires."""

        self.npc_turn_timer.stop()
        repository = self.repository()
        expected_repository = self._scheduled_npc_repository
        expected_actor_id = self._scheduled_npc_actor_id
        self._scheduled_npc_actor_id = ""
        self._scheduled_npc_repository = None

        if repository is None or repository is not expected_repository:
            return

        combat_state = repository.get_combat_state()
        combatants = combat_state.get("combatants", [])

        if not combat_state.get("active") or not combatants:
            return

        actor = combatants[int(combat_state.get("turn_index", 0))]

        if (
            str(actor.get("id", "")) != expected_actor_id
            or expected_actor_id == "player"
            or actor.get("defeated")
        ):
            self.refresh()
            return

        self._resolve_current_turn()

    def _start_combat(self) -> None:
        """Starts deterministic combat with the player and first opponent."""

        repository = self.repository()

        if repository is None:
            return

        if self._uses_narrative_combat(repository):
            self.status_label.setText(
                "Narrative combat is enabled. Gemini resolves fights in Story."
            )
            return

        state = StateManager(repository).load_state()
        inventory_items = repository.list_accessible_inventory_items()
        equipment = repository.get_player_equipment()
        attack_skill = equipped_weapon_attack_skill(equipment, inventory_items)
        weapon_profile = equipped_weapon_combat_profile(
            equipment,
            inventory_items,
        )
        armor_rating = armor_rating_from_equipment(equipment, inventory_items)
        player = {
            "id": "player",
            "name": state.player.name or "Player",
            "team": "party",
            "current_health": max(0, int(state.player.health_current)),
            "max_health": max(1, int(state.player.health_max)),
            "armor_rating": armor_rating,
            "to_hit_bonus": attack_bonus_from_skills(
                attack_skill,
                repository.list_skills(),
            ),
            "initiative_bonus": _safe_int(
                repository.get_setting("player.initiative_bonus", 0),
                0,
            ),
            "personality": "balanced",
            **weapon_profile,
            "clip_ammo": self._stored_player_clip_ammo(
                repository,
                weapon_profile,
            ),
            "reserve_ammo": 0,
            "damage": equipped_weapon_damage(equipment, inventory_items),
            "status_effects": [],
            "loot": [],
            "defeated": int(state.player.health_current) <= 0,
        }
        enemy = self._combatant_from_inputs(
            default_team="enemy",
            fallback_name="Enemy",
            use_selected_team=False,
        )
        combatants = roll_combat_initiative(
            [player, enemy],
            rng=random,
        )
        initiative_order = ", ".join(
            (
                f"{combatant_display_name(combatant)} "
                f"({combatant['initiative_total']})"
            )
            for combatant in combatants
        )
        combat_state = {
            "active": True,
            "round": 1,
            "turn_index": 0,
            "combatants": combatants,
            "log": [
                f"Combat begins: {player['name']} faces {enemy['name']}.",
                f"Initiative order: {initiative_order}.",
            ],
        }
        repository.set_combat_state(combat_state)
        repository.append_history("system", "Combat started.")
        self.refresh()
        self.notify_repository_changed()

    def _add_combatant(self) -> None:
        """Adds a party member or enemy to active combat."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            self._start_combat()
            return

        current_actor_id = str(
            combat_state["combatants"][int(combat_state["turn_index"])].get(
                "id",
                "",
            )
        )
        combatant = self._combatant_from_inputs(
            default_team=str(self.team_combo.currentData() or "enemy"),
            fallback_name="Combatant",
            index=len(combat_state["combatants"]) + 1,
        )
        roll_combat_initiative([combatant], rng=random)
        combat_state["combatants"].append(combatant)
        combat_state["combatants"].sort(
            key=lambda entry: (
                -int(entry.get("initiative_total", 0)),
                -int(entry.get("initiative_bonus", 0)),
                str(entry.get("id", "")),
            )
        )
        combat_state = normalize_combat_state(combat_state)
        combat_state["turn_index"] = next(
            (
                index
                for index, entry in enumerate(combat_state["combatants"])
                if str(entry.get("id", "")) == current_actor_id
            ),
            0,
        )
        added_combatant = next(
            (
                entry
                for entry in combat_state["combatants"]
                if str(entry.get("id", "")) == str(combatant["id"])
            ),
            combatant,
        )
        combat_state["log"].append(
            f"{combatant_display_name(added_combatant)} joins the fight "
            f"with initiative {added_combatant.get('initiative_total', 0)}."
        )
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_current_turn(self) -> None:
        """Resolves the current combatant's attack."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        self._sync_player_loadout(repository, combat_state)
        self._cancel_scheduled_npc_turn()
        combatants = combat_state["combatants"]
        turn_index = int(combat_state["turn_index"])
        actor = combatants[turn_index]

        if actor.get("defeated"):
            self._advance_turn(combat_state)
            repository.set_combat_state(combat_state)
            self.refresh()
            self.notify_repository_changed()
            return

        if str(actor.get("id", "")) != "player":
            self._resolve_npc_turn(repository, combat_state, actor)
            return

        target = self._target_for_actor(actor, combatants)

        if target is None:
            self._resolve_combat(repository, combat_state)
            return

        if not self._consume_attack_ammunition(repository, actor):
            combat_state["log"].append(
                f"{combatant_display_name(actor)} cannot attack: reload "
                f"{actor.get('ammunition_type_required', 'ammunition')} first."
            )
            repository.set_combat_state(combat_state)
            self.refresh()
            return

        self._perform_attack(combat_state, actor, target)
        self._finish_combat_action(repository, combat_state)

    def _sync_player_loadout(self, repository: SaveRepository, combat_state: dict[str, Any]) -> None:
        """Refresh cached combat gear after storage changes or travel."""
        player = next((actor for actor in combat_state.get("combatants", []) if actor.get("id") == "player"), None)
        if player is None:
            return
        items = repository.list_accessible_inventory_items()
        equipment = repository.get_player_equipment()
        profile = equipped_weapon_combat_profile(equipment, items)
        if player.get("weapon_name", "") != profile.get("weapon_name", ""):
            player["clip_ammo"] = self._stored_player_clip_ammo(repository, profile)
        player.update(profile)
        player["armor_rating"] = armor_rating_from_equipment(equipment, items)
        player["damage"] = equipped_weapon_damage(equipment, items)
        player["to_hit_bonus"] = attack_bonus_from_skills(
            equipped_weapon_attack_skill(equipment, items), repository.list_skills()
        )

    def _perform_attack(
        self,
        combat_state: dict[str, Any],
        actor: dict[str, Any],
        target: dict[str, Any],
    ) -> None:
        """Rolls and applies one attack."""

        attack_roll = random.randint(1, 20)
        to_hit_bonus = int(actor.get("to_hit_bonus", 0))
        attack_total = attack_roll + to_hit_bonus
        target_armor = int(target.get("armor_rating", 10))
        hit = attack_roll == 20 or (
            attack_roll != 1
            and attack_total >= target_armor
        )
        roll_detail = (
            f"{attack_roll}{to_hit_bonus:+d}={attack_total}"
            if to_hit_bonus
            else str(attack_roll)
        )

        if hit:
            damage, damage_detail = roll_damage_expression(actor.get("damage", DEFAULT_UNARMED_DAMAGE))
            target["current_health"] = max(0, int(target["current_health"]) - damage)
            target["defeated"] = target["current_health"] <= 0
            combat_state["log"].append(
                f"{combatant_display_name(actor)} hits "
                f"{combatant_display_name(target)} with {roll_detail} vs AR {target_armor}, "
                f"dealing {damage} damage [{damage_detail}]."
            )

            if target["defeated"]:
                combat_state["log"].append(
                    f"{combatant_display_name(target)} is defeated."
                )
        else:
            combat_state["log"].append(
                f"{combatant_display_name(actor)} misses "
                f"{combatant_display_name(target)} with {roll_detail} "
                f"vs AR {target_armor}."
            )

    def _finish_combat_action(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Persists an action and advances unless combat ended."""

        combatants = combat_state["combatants"]
        self._sync_player_health_from_combat(repository, combat_state)

        if combat_team_defeated(combatants, "enemy") or combat_team_defeated(combatants, "party"):
            self._resolve_combat(repository, combat_state)
            return

        self._advance_turn(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_npc_turn(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """Resolves one NPC turn with deterministic personality rules."""

        target = self._npc_target_for_actor(
            actor,
            combat_state["combatants"],
        )

        if target is None:
            self._resolve_combat(repository, combat_state)
            return

        if actor.get("personality") == "intelligent":
            hit_chance = attack_hit_probability(
                int(actor.get("to_hit_bonus", 0)),
                int(target.get("armor_rating", 10)),
            )
            max_health = max(1, int(target.get("max_health", 1)))
            wounded_percent = round(
                (1.0 - (int(target.get("current_health", 0)) / max_health))
                * 100
            )
            combat_state["log"].append(
                f"{combatant_display_name(actor)} selects "
                f"{combatant_display_name(target)}: "
                f"{round(hit_chance * 100)}% hit chance, "
                f"{wounded_percent}% wounded."
            )
        else:
            combat_state["log"].append(
                f"{combatant_display_name(actor)} targets "
                f"{combatant_display_name(target)} based on its "
                f"{target.get('threat_level', 0)}% Threat Level."
            )

        if not self._consume_attack_ammunition(repository, actor):
            loaded = self._reload_actor_ammunition(repository, actor)

            if loaded > 0:
                combat_state["log"].append(
                    f"{combatant_display_name(actor)} reloads {loaded} "
                    f"{actor.get('ammunition_type_required', 'rounds')}."
                )
            else:
                combat_state["log"].append(
                    f"{combatant_display_name(actor)} is out of "
                    f"{actor.get('ammunition_type_required', 'ammunition')}."
                )

            self._finish_combat_action(repository, combat_state)
            return

        self._perform_attack(combat_state, actor, target)
        self._finish_combat_action(repository, combat_state)

    def _npc_target_for_actor(
        self,
        actor: dict[str, Any],
        combatants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Selects by threat unless the NPC uses intelligent tactical targeting."""

        enemy_team = "party" if actor.get("team") == "enemy" else "enemy"
        candidates = [
            combatant
            for combatant in combatants
            if combatant.get("team") == enemy_team
            and not combatant.get("defeated")
        ]

        if not candidates:
            return None

        if actor.get("personality") != "intelligent":
            threat_levels = calculate_team_threat_levels(
                combatants,
                enemy_team,
            )
            roll = random.randint(1, 100)
            cumulative = 0

            for candidate in candidates:
                threat = threat_levels.get(
                    str(candidate.get("id", "")),
                    0,
                )
                candidate["threat_level"] = threat
                cumulative += threat

                if roll <= cumulative:
                    return candidate

            return candidates[-1]

        def target_score(target: dict[str, Any]) -> tuple[float, float, int]:
            hit_probability = attack_hit_probability(
                int(actor.get("to_hit_bonus", 0)),
                int(target.get("armor_rating", 10)),
            )
            max_health = max(1, int(target.get("max_health", 1)))
            current_health = max(0, int(target.get("current_health", 0)))
            wounded_ratio = 1.0 - (current_health / max_health)
            combined_score = (hit_probability * 0.65) + (wounded_ratio * 0.35)
            return combined_score, wounded_ratio, -current_health

        return max(candidates, key=target_score)

    def _consume_attack_ammunition(
        self,
        repository: SaveRepository,
        actor: dict[str, Any],
    ) -> bool:
        """Consumes loaded rounds for an attack when the weapon requires them."""

        ammunition_type = str(
            actor.get("ammunition_type_required", "")
        ).strip()

        if not ammunition_type:
            return True

        bullets_per_attack = max(1, int(actor.get("bullets_per_attack", 1)))
        clip_ammo = max(0, int(actor.get("clip_ammo", 0)))

        if clip_ammo < bullets_per_attack:
            return False

        actor["clip_ammo"] = clip_ammo - bullets_per_attack

        if str(actor.get("id", "")) == "player":
            self._persist_player_clip_ammo(repository, actor)

        return True

    def _reload_current_weapon(self) -> None:
        """Reloads the current actor and consumes the turn."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        self._sync_player_loadout(repository, combat_state)
        actor = combat_state["combatants"][int(combat_state["turn_index"])]
        loaded = self._reload_actor_ammunition(repository, actor)

        if loaded <= 0:
            combat_state["log"].append(
                f"{combatant_display_name(actor)} cannot reload."
            )
            repository.set_combat_state(combat_state)
            self.refresh()
            return

        combat_state["log"].append(
            f"{combatant_display_name(actor)} reloads {loaded} "
            f"{actor.get('ammunition_type_required', 'rounds')}."
        )
        self._finish_combat_action(repository, combat_state)

    def _reload_actor_ammunition(
        self,
        repository: SaveRepository,
        actor: dict[str, Any],
    ) -> int:
        """Moves reserve ammunition into an actor's clip."""

        ammunition_type = str(
            actor.get("ammunition_type_required", "")
        ).strip()
        clip_size = max(0, int(actor.get("clip_size", 0)))
        clip_ammo = max(0, int(actor.get("clip_ammo", 0)))
        needed = max(0, clip_size - clip_ammo)

        if not ammunition_type or needed <= 0:
            return 0

        if str(actor.get("id", "")) == "player":
            loaded = self._consume_inventory_ammunition(
                repository,
                ammunition_type,
                needed,
            )
        else:
            reserve_ammo = max(0, int(actor.get("reserve_ammo", 0)))
            loaded = min(needed, reserve_ammo)
            actor["reserve_ammo"] = reserve_ammo - loaded

        actor["clip_ammo"] = clip_ammo + loaded

        if str(actor.get("id", "")) == "player":
            self._persist_player_clip_ammo(repository, actor)

        return loaded

    @staticmethod
    def _consume_inventory_ammunition(
        repository: SaveRepository,
        ammunition_type: str,
        amount: int,
    ) -> int:
        """Consumes matching ammunition stacks from inventory."""

        remaining = max(0, amount)
        consumed = 0

        for item in repository.list_accessible_inventory_items():
            metadata = item_metadata(item)

            if str(metadata.get("item_type", "")).casefold() != "ammunition":
                continue
            if (
                str(metadata.get("ammunition_type", "")).casefold()
                != ammunition_type.casefold()
            ):
                continue

            available = max(0, int(item.get("quantity", 0)))
            used = min(remaining, available)

            if used <= 0:
                continue

            repository.remove_inventory_item(str(item.get("name", "")), used)
            consumed += used
            remaining -= used

            if remaining <= 0:
                break

        return consumed

    @staticmethod
    def _stored_player_clip_ammo(
        repository: SaveRepository,
        weapon_profile: dict[str, Any],
    ) -> int:
        """Loads the durable clip count for the equipped player weapon."""

        clip_size = max(0, int(weapon_profile.get("clip_size", 0)))
        weapon_name = str(weapon_profile.get("weapon_name", "")).casefold()
        stored_clips = repository.get_setting("player.weapon_clip_ammo", {})

        if not isinstance(stored_clips, dict) or not weapon_name:
            return clip_size

        return max(
            0,
            min(
                clip_size,
                _safe_int(stored_clips.get(weapon_name, clip_size), clip_size),
            ),
        )

    @staticmethod
    def _persist_player_clip_ammo(
        repository: SaveRepository,
        actor: dict[str, Any],
    ) -> None:
        """Stores the player's loaded rounds by weapon name."""

        weapon_name = str(actor.get("weapon_name", "")).casefold()

        if not weapon_name:
            return

        stored_clips = repository.get_setting("player.weapon_clip_ammo", {})
        clean_clips = dict(stored_clips) if isinstance(stored_clips, dict) else {}
        clean_clips[weapon_name] = max(0, int(actor.get("clip_ammo", 0)))
        repository.set_setting("player.weapon_clip_ammo", clean_clips)

    def _end_turn_without_attack(self) -> None:
        """Skips the active combatant's turn."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        actor = combat_state["combatants"][int(combat_state["turn_index"])]
        combat_state["log"].append(
            f"{combatant_display_name(actor)} holds position."
        )
        self._advance_turn(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_combat_manually(self) -> None:
        """Marks combat resolved without more attacks."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        combat_state["log"].append("Combat is marked resolved.")
        self._sync_player_health_from_combat(repository, combat_state)
        self._clear_resolved_battlefield(combat_state)
        repository.set_combat_state(combat_state)
        repository.append_history("system", "Combat resolved.")
        self.refresh()
        self.notify_repository_changed()

    def _adjust_health(self, delta: int) -> None:
        """Applies direct damage or healing to a combatant."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("combatants"):
            return

        combatant_id = str(self.adjust_target_combo.currentData() or "")

        for combatant in combat_state["combatants"]:
            if combatant.get("id") != combatant_id:
                continue

            old_health = int(combatant["current_health"])
            new_health = max(0, min(old_health + delta, int(combatant["max_health"])))
            combatant["current_health"] = new_health
            combatant["defeated"] = new_health <= 0
            verb = "heals" if delta > 0 else "takes"
            combat_state["log"].append(
                f"{combatant_display_name(combatant)} {verb} {abs(delta)}; "
                f"health is now "
                f"{new_health}/{combatant['max_health']}."
            )
            break

        self._sync_player_health_from_combat(repository, combat_state)

        if combat_state.get("active") and (
            combat_team_defeated(combat_state["combatants"], "enemy")
            or combat_team_defeated(combat_state["combatants"], "party")
        ):
            self._resolve_combat(repository, combat_state)
            return

        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _combatant_from_inputs(
        self,
        *,
        default_team: str,
        fallback_name: str,
        index: int = 1,
        use_selected_team: bool = True,
    ) -> dict[str, Any]:
        """Builds a combatant from the input row."""

        name = self.name_input.text().strip() or fallback_name
        damage = normalize_damage_expression(self.damage_input.text(), default="1d6")
        team = str(self.team_combo.currentData() or default_team) if use_selected_team else default_team

        if team not in {"party", "enemy"}:
            team = default_team

        ammunition_type = self.ammunition_type_input.text().strip()
        clip_size = self.clip_size_input.value() if ammunition_type else 0
        return {
            "id": f"{team}-{index}-{_slug_for_id(name)}",
            "name": name,
            "team": team,
            "current_health": self.health_input.value(),
            "max_health": self.health_input.value(),
            "armor_rating": self.armor_input.value(),
            "to_hit_bonus": self.to_hit_input.value(),
            "initiative_bonus": self.initiative_input.value(),
            "personality": self.personality_combo.currentData() or "balanced",
            "weapon_name": "",
            "ammunition_type_required": ammunition_type,
            "clip_size": clip_size,
            "clip_ammo": min(self.clip_ammo_input.value(), clip_size),
            "bullets_per_attack": (
                min(self.bullets_per_attack_input.value(), clip_size)
                if ammunition_type and clip_size > 0
                else 0
            ),
            "reserve_ammo": self.reserve_ammo_input.value(),
            "damage": damage,
            "status_effects": [],
            "loot": _split_loot_items(self.loot_input.text()) if team == "enemy" else [],
            "defeated": False,
        }

    def _sync_clip_inputs(self, clip_size: int) -> None:
        """Keeps playtesting clip controls inside the selected capacity."""

        self.clip_ammo_input.setMaximum(max(0, clip_size))
        self.bullets_per_attack_input.setMaximum(max(1, clip_size))

        if clip_size > 0 and self.clip_ammo_input.value() == 0:
            self.clip_ammo_input.setValue(clip_size)

    def _target_for_actor(
        self,
        actor: dict[str, Any],
        combatants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Returns the selected or automatic attack target."""

        enemy_team = "party" if actor.get("team") == "enemy" else "enemy"

        if actor.get("team") == "party":
            selected_id = str(self.target_combo.currentData() or "")

            for combatant in combatants:
                if combatant.get("id") == selected_id and not combatant.get("defeated"):
                    return combatant

        for combatant in combatants:
            if combatant.get("team") == enemy_team and not combatant.get("defeated"):
                return combatant

        return None

    def _advance_turn(self, combat_state: dict[str, Any]) -> None:
        """Moves to the next living combatant."""

        old_index = int(combat_state["turn_index"])
        new_index = next_living_index(combat_state["combatants"], old_index)

        if new_index <= old_index:
            combat_state["round"] = int(combat_state.get("round", 1)) + 1

        combat_state["turn_index"] = new_index

    def _resolve_combat(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Finishes combat, stores state, and grants defeated-enemy loot."""

        party_defeated = combat_team_defeated(combat_state["combatants"], "party")
        enemies_defeated = combat_team_defeated(combat_state["combatants"], "enemy")

        if enemies_defeated and not party_defeated:
            granted_loot: list[str] = []

            for combatant in combat_state["combatants"]:
                if combatant.get("team") != "enemy" or not combatant.get("defeated"):
                    continue

                for loot_name in combatant.get("loot", []):
                    repository.add_inventory_item(
                        loot_name,
                        "Loot",
                        1,
                        (
                            "Loot recovered from "
                            f"{combatant_display_name(combatant)}."
                        ),
                        0,
                    )
                    granted_loot.append(loot_name)

            if granted_loot:
                combat_state["log"].append("Recovered loot: " + ", ".join(granted_loot) + ".")

            combat_state["log"].append("Combat resolved: victory.")
            repository.append_history("system", "Combat resolved: victory.")
        elif party_defeated:
            combat_state["log"].append("Combat resolved: party defeated.")
            repository.append_history("system", "Combat resolved: party defeated.")
        else:
            combat_state["log"].append("Combat resolved.")
            repository.append_history("system", "Combat resolved.")

        self._sync_player_health_from_combat(repository, combat_state)
        self._clear_resolved_battlefield(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _sync_player_health_from_combat(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Persists player health from the player combatant."""

        for combatant in combat_state.get("combatants", []):
            if combatant.get("id") == "player":
                repository.set_setting("player.health_current", int(combatant["current_health"]))
                repository.set_setting("player.health_max", int(combatant["max_health"]))
                repository.set_setting("player.armor_rating", int(combatant["armor_rating"]))
                repository.set_state_value(
                    "condition",
                    "Incapacitated" if int(combatant["current_health"]) <= 0 else "Healthy",
                )
                self._persist_player_clip_ammo(repository, combatant)
                continue

            npc_id = str(combatant.get("npc_id", "") or "").strip()
            if npc_id and combatant.get("team") == "party":
                current_health = int(combatant.get("current_health", -1))
                max_health = int(combatant.get("max_health", -1))
                repository.upsert_party_member(
                    npc_id,
                    status=(
                        "Incapacitated"
                        if current_health <= 0
                        else "Wounded"
                        if max_health >= 0 and current_health < max_health
                        else "Active"
                    ),
                    health_current=current_health,
                    health_max=max_health,
                    armor_class=int(combatant.get("armor_rating", -1)),
                )

    @staticmethod
    def _clear_resolved_battlefield(combat_state: dict[str, Any]) -> None:
        """Clears active participants while preserving the completed combat log."""

        combat_state["active"] = False
        combat_state["round"] = 1
        combat_state["turn_index"] = 0
        combat_state["combatants"] = []

    def _render_combat_state(self, combat_state: dict[str, Any]) -> None:
        """Renders saved combat state."""

        active = bool(combat_state.get("active", False))
        combatants = combat_state.get("combatants", []) if active else []
        current_id = ""

        if active and combatants:
            turn_index = int(combat_state.get("turn_index", 0))
            actor = combatants[turn_index]
            current_id = str(actor.get("id", ""))
            status = (
                f"Round {combat_state.get('round', 1)} - "
                f"{combatant_display_name(actor)}'s turn"
            )

            if current_id != "player":
                status += " (acting automatically in 2 seconds...)"

            self.status_label.setText(status)
        else:
            self.status_label.setText("No active combat.")

        self.combatants_table.setRowCount(len(combatants))

        for row_index, combatant in enumerate(combatants):
            current_marker = "->" if combatant.get("id") == current_id else ""
            status_bits = []

            if combatant.get("defeated"):
                status_bits.append("Defeated")

            if combatant.get("status_effects"):
                status_bits.extend(str(effect) for effect in combatant.get("status_effects", []))

            loot_text = ", ".join(str(item) for item in combatant.get("loot", []))

            if loot_text:
                status_bits.append(f"Loot: {loot_text}")

            self.combatants_table.setItem(row_index, 0, _table_item(current_marker))
            self.combatants_table.setItem(
                row_index,
                1,
                _table_item(combatant_display_name(combatant)),
            )
            self.combatants_table.setItem(row_index, 2, _table_item(str(combatant["team"])))
            self.combatants_table.setItem(
                row_index,
                3,
                _table_item(
                    f"{combatant.get('initiative_total', 0)} "
                    f"({combatant.get('initiative_roll', 0)}"
                    f"{int(combatant.get('initiative_bonus', 0)):+d})"
                ),
            )
            self.combatants_table.setItem(
                row_index,
                4,
                _table_item(f"{combatant['current_health']}/{combatant['max_health']}"),
            )
            self.combatants_table.setItem(row_index, 5, _table_item(str(combatant["armor_rating"])))
            to_hit_bonus = int(combatant.get("to_hit_bonus", 0))
            self.combatants_table.setItem(
                row_index,
                6,
                _table_item(f"{to_hit_bonus:+d}"),
            )
            self.combatants_table.setItem(
                row_index,
                7,
                _table_item(f"{combatant.get('threat_level', 0)}%"),
            )
            ammunition_type = str(
                combatant.get("ammunition_type_required", "")
            )
            ammo_text = (
                f"{combatant.get('clip_ammo', 0)}/"
                f"{combatant.get('clip_size', 0)} {ammunition_type}"
                if ammunition_type
                else "-"
            )
            self.combatants_table.setItem(
                row_index,
                8,
                _table_item(ammo_text),
            )
            self.combatants_table.setItem(row_index, 9, _table_item(str(combatant["damage"])))
            self.combatants_table.setItem(row_index, 10, _table_item("; ".join(status_bits)))

        self.combatants_table.resizeColumnsToContents()
        self._populate_target_combos(combat_state)
        self.log_output.setPlainText("\n".join(str(entry) for entry in combat_state.get("log", [])))
        self.log_output.moveCursor(self.log_output.textCursor().MoveOperation.End)
        self._sync_buttons(active)
        self._schedule_npc_turn(combat_state)

    def _populate_target_combos(self, combat_state: dict[str, Any]) -> None:
        """Reloads target dropdowns from combatants."""

        self.target_combo.clear()
        self.adjust_target_combo.clear()
        combatants = (
            combat_state.get("combatants", [])
            if combat_state.get("active")
            else []
        )
        actor = None

        if combat_state.get("active") and combatants:
            actor = combatants[int(combat_state.get("turn_index", 0))]

        for combatant in combatants:
            if combatant.get("defeated"):
                continue

            label = (
                f"{combatant_display_name(combatant)} "
                f"({combatant['team']})"
            )
            self.adjust_target_combo.addItem(label, combatant["id"])

            if actor is None:
                continue

            if combatant.get("team") != actor.get("team"):
                self.target_combo.addItem(label, combatant["id"])

    def _sync_buttons(self, combat_active: bool) -> None:
        """Enables combat controls for the active state."""

        repository = self.repository()
        narrative_combat = bool(
            repository
            and not combat_active
            and self._uses_narrative_combat(repository)
        )
        combat_state = (
            repository.get_combat_state()
            if repository is not None and combat_active
            else {}
        )
        combatants = combat_state.get("combatants", [])
        actor = (
            combatants[int(combat_state.get("turn_index", 0))]
            if combatants
            else None
        )
        player_turn = bool(
            combat_active
            and actor is not None
            and actor.get("id") == "player"
        )
        self.attack_button.setText("Attack / Resolve Turn")
        self.attack_button.setEnabled(player_turn)
        self.end_turn_button.setEnabled(player_turn)
        self.reload_button.setEnabled(player_turn)
        self.target_combo.setEnabled(player_turn)
        manual_action_visible = not combat_active or player_turn
        self.attack_button.setVisible(manual_action_visible)
        self.end_turn_button.setVisible(manual_action_visible)
        self.reload_button.setVisible(manual_action_visible)
        self.resolve_button.setEnabled(combat_active)
        self.add_combatant_button.setEnabled(
            repository is not None and not narrative_combat
        )
        self.start_button.setEnabled(
            repository is not None and not combat_active and not narrative_combat
        )
        self.damage_button.setEnabled(bool(self.adjust_target_combo.count()))
        self.heal_button.setEnabled(bool(self.adjust_target_combo.count()))

    @staticmethod
    def _uses_narrative_combat(repository: SaveRepository) -> bool:
        """Returns whether this save delegates combat resolution to Gemini."""

        preferences = normalize_combat_preferences(
            repository.get_setting(
                "combat.preferences",
                {
                    "resolution_mode": repository.get_setting(
                        "combat.resolution_mode", "strict"
                    ),
                    "focus": repository.get_setting("combat.focus", "balanced"),
                },
            )
        )
        return preferences["resolution_mode"] == "narrative"

```

## ai_adventure/domain/rules/combat.py

```python
"""Canonical import path for combat rules."""

from ai_adventure.combat import *

```


## Former combat event handler

```python
    def _apply_combat_started(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies CombatStartedEvent."""

        combat_preferences = normalize_fighting_preferences(
            self.repository.get_setting(
                "combat.preferences",
                {
                    "resolution_mode": self.repository.get_setting(
                        "combat.resolution_mode", "strict"
                    ),
                    "focus": self.repository.get_setting("combat.focus", "balanced"),
                },
            )
        )
        if combat_preferences["resolution_mode"] == "narrative":
            return AppliedEventResult(
                event_type,
                "skipped",
                "CombatStartedEvent is disabled for narrative combat.",
                payload,
            )

        if self.repository.is_combat_active():
            return AppliedEventResult(
                event_type,
                "skipped",
                "Combat is already active.",
                payload,
            )

        enemies = _combatants_from_payload(payload.get("enemies", []), team="enemy")

        if not enemies:
            enemy_name = _first_text(payload, "enemy_name", "opponent_name") or "Enemy"
            enemies = [
                _combatant_from_payload(
                    {
                        "name": enemy_name,
                        "health": _first_int(payload, 8, "enemy_health", "health"),
                        "armor_rating": _first_int(
                            payload,
                            10,
                            "enemy_armor_rating",
                            "armor_rating",
                        ),
                        "to_hit_bonus": _first_int(
                            payload,
                            0,
                            "enemy_to_hit_bonus",
                            "to_hit_bonus",
                        ),
                        "initiative_bonus": _first_int(
                            payload,
                            0,
                            "enemy_initiative_bonus",
                            "initiative_bonus",
                        ),
                        "personality": _first_text(
                            payload,
                            "enemy_personality",
                            "personality",
                        )
                        or "balanced",
                        "damage": _first_text(payload, "enemy_damage", "damage") or "1d6",
                        "loot": payload.get("loot", []),
                    },
                    team="enemy",
                    index=1,
                )
            ]

        allies = _combatants_from_payload(payload.get("allies", []), team="party", start_index=2)
        for ally in allies:
            ally_npc_id = str(ally.get("npc_id", "") or "").strip()
            if ally_npc_id:
                ally_health = int(ally["current_health"])
                ally_health_max = int(ally["max_health"])
                self.repository.upsert_party_member(
                    ally_npc_id,
                    status=(
                        "Incapacitated"
                        if ally_health <= 0
                        else "Wounded"
                        if ally_health_max >= 0 and ally_health < ally_health_max
                        else "Active"
                    ),
                    health_current=ally_health,
                    health_max=ally_health_max,
                    armor_class=int(ally["armor_rating"]),
                )
        inventory_items = self.repository.list_accessible_inventory_items()
        equipment = self.repository.get_player_equipment()
        attack_skill = equipped_weapon_attack_skill(equipment, inventory_items)
        weapon_profile = equipped_weapon_combat_profile(
            equipment,
            inventory_items,
        )
        stored_clips = self.repository.get_setting(
            "player.weapon_clip_ammo",
            {},
        )
        weapon_name_key = str(
            weapon_profile.get("weapon_name", "")
        ).casefold()
        clip_size = int(weapon_profile.get("clip_size", 0))
        stored_clip_ammo = (
            _safe_int(stored_clips.get(weapon_name_key), default=clip_size)
            if isinstance(stored_clips, dict) and weapon_name_key
            else clip_size
        )
        player_health_max = _safe_positive_int(
            self.repository.get_setting("player.health_max", DEFAULT_PLAYER_MAX_HEALTH),
            DEFAULT_PLAYER_MAX_HEALTH,
        )
        player_health_current = max(
            0,
            min(
                _safe_positive_int(
                    self.repository.get_setting("player.health_current", player_health_max),
                    player_health_max,
                ),
                player_health_max,
            ),
        )
        player = {
            "id": "player",
            "name": str(self.repository.get_setting("player_name", "Player")).strip() or "Player",
            "team": "party",
            "current_health": player_health_current,
            "max_health": player_health_max,
            "armor_rating": armor_rating_from_equipment(
                equipment,
                inventory_items,
                base_armor_rating=DEFAULT_BASE_ARMOR_RATING,
            ),
            "to_hit_bonus": attack_bonus_from_skills(
                attack_skill,
                self.repository.list_skills(),
            ),
            "initiative_bonus": _safe_int(
                self.repository.get_setting("player.initiative_bonus", 0),
                default=0,
            )
            or 0,
            "personality": "balanced",
            **weapon_profile,
            "clip_ammo": max(
                0,
                min(clip_size, stored_clip_ammo or 0),
            ),
            "reserve_ammo": 0,
            "damage": equipped_weapon_damage(equipment, inventory_items),
            "status_effects": [],
            "loot": [],
            "defeated": player_health_current <= 0,
        }
        combatants = roll_combat_initiative(
            [player, *allies, *enemies],
            rng=self.rng,
        )
        combat_state = {
            "active": True,
            "round": 1,
            "turn_index": 0,
            "combatants": combatants,
            "log": [
                str(payload.get("description", "") or "Combat begins.").strip(),
                "Initiative order: "
                + ", ".join(
                    (
                        f"{combatant.get('display_name', combatant['name'])} "
                        f"({combatant['initiative_total']})"
                    )
                    for combatant in combatants
                )
                + ".",
            ],
        }
        self.repository.set_combat_state(combat_state)
        self.repository.append_history("system", "Combat started.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Started combat with {len(enemies)} enemy combatant(s).",
            payload,
        )

```

## Historical combat tests

```python
from __future__ import annotations

import unittest

from ai_adventure.combat import (
    BODY_PARTS,
    DEFAULT_TWO_HANDED_DAMAGE,
    DEFAULT_WEAPON_DAMAGE,
    attack_hit_probability,
    calculate_team_threat_levels,
    normalize_equipment,
    normalize_combat_state,
    normalize_item_metadata,
    roll_combat_initiative,
)


class _SequenceRng:
    def __init__(self, values: list[int]) -> None:
        self.values = iter(values)

    def randint(self, _minimum: int, _maximum: int) -> int:
        return next(self.values)


class CombatRuleTests(unittest.TestCase):
    def test_weapon_metadata_raises_damage_that_is_not_better_than_unarmed(self) -> None:
        weak_one_handed = normalize_item_metadata(
            {
                "item_type": "Weapon",
                "weapon_hands": "one-handed",
                "damage": "1d4",
            },
            name="Rusty Dagger",
            category="Weapon",
        )
        weak_two_handed = normalize_item_metadata(
            {
                "item_type": "Weapon",
                "weapon_hands": "two-handed",
                "damage": "1d4",
            },
            name="Cracked Greatclub",
            category="Weapon",
        )
        stronger_weapon = normalize_item_metadata(
            {
                "item_type": "Weapon",
                "weapon_hands": "one-handed",
                "damage": "1d6+1",
            },
            name="Fine Saber",
            category="Weapon",
        )

        self.assertEqual(weak_one_handed["damage"], DEFAULT_WEAPON_DAMAGE)
        self.assertEqual(weak_two_handed["damage"], DEFAULT_TWO_HANDED_DAMAGE)
        self.assertEqual(stronger_weapon["damage"], "1d6+1")

    def test_container_metadata_preserves_exact_contents_and_security(self) -> None:
        metadata = normalize_item_metadata(
            {
                "item_type": "Container",
                "container": {
                    "is_open": False,
                    "contents_taken": False,
                    "is_locked": True,
                    "lockpick_skill": "Lockpicking",
                    "lockpick_dc": 16,
                    "lockpick_failure_consequence": "The pick snaps in the lock.",
                    "is_trapped": True,
                    "trap_notice_skill": "Perception",
                    "trap_notice_dc": 13,
                    "trap_disarm_skill": "Sleight of Hand",
                    "trap_disarm_dc": 15,
                    "trap_failure_consequence": "A poisoned needle strikes the opener.",
                    "contents": {
                        "currency_base_units": 35,
                        "items": [
                            {
                                "name": "Tarnished Silver Locket",
                                "category": "Valuable",
                                "quantity": 1,
                                "description": "A locket with a worn clasp.",
                                "value_base_units": 12,
                            }
                        ],
                    },
                },
            },
            name="Stolen Coin Pouch",
            category="Container",
        )
        container = metadata["container"]

        self.assertEqual(metadata["item_type"], "Container")
        self.assertFalse(container["is_open"])
        self.assertFalse(container["contents_taken"])
        self.assertTrue(container["is_locked"])
        self.assertEqual(container["lockpick_dc"], 16)
        self.assertTrue(container["is_trapped"])
        self.assertEqual(container["trap_notice_dc"], 13)
        self.assertEqual(container["trap_disarm_dc"], 15)
        self.assertEqual(container["contents"]["currency_base_units"], 35)
        self.assertEqual(
            container["contents"]["items"][0]["name"],
            "Tarnished Silver Locket",
        )

    def test_team_threat_totals_exactly_one_hundred_and_rewards_tank_stats(self) -> None:
        combatants = [
            {
                "id": "tank",
                "team": "party",
                "current_health": 30,
                "max_health": 30,
                "armor_rating": 18,
                "damage": "2d8",
                "defeated": False,
            },
            {
                "id": "scout",
                "team": "party",
                "current_health": 14,
                "max_health": 14,
                "armor_rating": 12,
                "damage": "1d8",
                "defeated": False,
            },
            {
                "id": "mage",
                "team": "party",
                "current_health": 8,
                "max_health": 8,
                "armor_rating": 9,
                "damage": "1d4",
                "defeated": False,
            },
            {
                "id": "enemy",
                "team": "enemy",
                "current_health": 10,
                "max_health": 10,
                "armor_rating": 10,
                "damage": "1d6",
                "defeated": False,
            },
        ]

        party_threat = calculate_team_threat_levels(combatants, "party")
        enemy_threat = calculate_team_threat_levels(combatants, "enemy")

        self.assertEqual(sum(party_threat.values()), 100)
        self.assertGreater(party_threat["tank"], party_threat["scout"])
        self.assertGreater(party_threat["scout"], party_threat["mage"])
        self.assertEqual(enemy_threat, {"enemy": 100})

    def test_combat_normalization_discards_legacy_spatial_state(self) -> None:
        state = normalize_combat_state(
            {
                "active": True,
                "movement_undo": {"actor_id": "player"},
                "combatants": [
                    {
                        "id": "player",
                        "team": "party",
                        "current_health": 20,
                        "max_health": 20,
                        "movement_speed_feet": 30,
                        "movement_remaining_feet": 10,
                        "distance_feet": 25,
                        "attack_range_feet": 5,
                    },
                    {
                        "id": "enemy",
                        "team": "enemy",
                        "current_health": 8,
                        "max_health": 8,
                    },
                ],
            }
        )

        self.assertNotIn("movement_undo", state)
        for combatant in state["combatants"]:
            self.assertNotIn("movement_speed_feet", combatant)
            self.assertNotIn("movement_remaining_feet", combatant)
            self.assertNotIn("distance_feet", combatant)
            self.assertNotIn("attack_range_feet", combatant)
            self.assertEqual(combatant["threat_level"], 100)

    def test_equipment_respects_owned_quantity_for_optional_hand_slots(self) -> None:
        dagger = {
            "name": "Iron Dagger",
            "quantity": 1,
            "category": "Weapon",
            "metadata": {
                "item_type": "Weapon",
                "weapon_hands": "one-handed",
            },
        }

        main_hand = normalize_equipment(
            {
                "Main Hand": "Iron Dagger",
                "Off Hand": "Iron Dagger",
            },
            [dagger],
        )
        off_hand = normalize_equipment(
            {"Off Hand": "Iron Dagger"},
            [dagger],
        )

        self.assertEqual(main_hand["Main Hand"], "Iron Dagger")
        self.assertEqual(main_hand["Off Hand"], "")
        self.assertEqual(off_hand["Main Hand"], "")
        self.assertEqual(off_hand["Off Hand"], "Iron Dagger")

        dagger["quantity"] = 2
        dual_wielded = normalize_equipment(
            {
                "Main Hand": "Iron Dagger",
                "Off Hand": "Iron Dagger",
            },
            [dagger],
        )

        self.assertEqual(dual_wielded["Main Hand"], "Iron Dagger")
        self.assertEqual(dual_wielded["Off Hand"], "Iron Dagger")

    def test_equipment_expands_armor_into_every_forced_slot(self) -> None:
        full_armor = {
            "name": "Iron Armor",
            "quantity": 1,
            "category": "Armor",
            "metadata": {
                "item_type": "Armor",
                "covers_body_parts": list(BODY_PARTS),
            },
        }

        equipment = normalize_equipment(
            {"Legs": "Iron Armor"},
            [full_armor],
        )

        for slot in BODY_PARTS:
            self.assertEqual(equipment[slot], "Iron Armor")

    def test_duplicate_combatant_names_receive_unique_display_names(self) -> None:
        state = normalize_combat_state(
            {
                "active": True,
                "combatants": [
                    {"id": "enemy-1", "name": "Bandit"},
                    {"id": "enemy-2", "name": "Bandit"},
                    {"id": "enemy-3", "name": "Cleric"},
                ],
            }
        )

        self.assertEqual(
            [
                combatant["display_name"]
                for combatant in state["combatants"]
            ],
            ["Bandit (1)", "Bandit (2)", "Cleric"],
        )

    def test_initiative_rolls_and_sorts_highest_total_first(self) -> None:
        combatants = [
            {"id": "player", "name": "Player", "initiative_bonus": 1},
            {"id": "bandit", "name": "Bandit", "initiative_bonus": 4},
            {"id": "cleric", "name": "Cleric", "initiative_bonus": 0},
        ]

        ordered = roll_combat_initiative(
            combatants,
            rng=_SequenceRng([12, 10, 19]),
        )

        self.assertEqual(
            [combatant["id"] for combatant in ordered],
            ["cleric", "bandit", "player"],
        )
        self.assertEqual(
            [combatant["initiative_total"] for combatant in ordered],
            [19, 14, 13],
        )

    def test_firearm_and_ammunition_metadata_are_normalized(self) -> None:
        firearm = normalize_item_metadata(
            {
                "item_type": "Weapon",
                "Ammunition_Type_Required": "9mm Round",
                "Clip Size": 12,
                "Amount of Bullets Fired Per Attack": 3,
                "attack_range_feet": 90,
            },
            name="Burst Pistol",
            category="Weapon",
        )
        ammunition = normalize_item_metadata(
            {
                "item_type": "Ammunition",
                "ammunition_type": "9mm Round",
            },
            name="9mm Box",
            category="Ammunition",
        )

        self.assertEqual(firearm["ammunition_type_required"], "9mm Round")
        self.assertEqual(firearm["clip_size"], 12)
        self.assertEqual(firearm["bullets_per_attack"], 3)
        self.assertEqual(firearm["attack_range_feet"], 90)
        self.assertEqual(ammunition["item_type"], "Ammunition")
        self.assertEqual(ammunition["ammunition_type"], "9mm Round")

    def test_hit_probability_respects_natural_one_and_twenty(self) -> None:
        self.assertEqual(attack_hit_probability(100, 10), 0.95)
        self.assertEqual(attack_hit_probability(-100, 10), 0.05)

```

## Historical inventory combat test

```python
    def test_active_combat_stops_using_equipment_after_it_becomes_remote(self):
        self.repo.add_inventory_item("Shop Sword", "Weapon", 1, "A sword.", metadata={
            "storage_location": "Player Store", "damage": "1d10"})
        self.repo.add_inventory_item("Shop Armor", "Armor", 1, "A breastplate.", metadata={
            "storage_location": "Player Store", "covers_body_parts": ["Torso"], "armor_rating": 5})
        self.repo.set_player_equipment({"Main Hand": "Shop Sword", "Torso": "Shop Armor"})
        screen = CombatScreen()
        self.addCleanup(screen.close)
        player = {"id": "player", "weapon_name": "Shop Sword", "damage": "1d10", "armor_rating": 15}
        combat = {"combatants": [player]}
        self.repo.set_state_value("location", "Road")
        screen._sync_player_loadout(self.repo, combat)
        self.assertEqual(player["weapon_name"], "")
        self.assertEqual(player["damage"], "1d4")
        self.assertEqual(player["armor_rating"], 10)

```

## Retired behavior test: test_legacy_game_state_elapsed_minutes_migrates_to_calendar_state

```python
    def test_legacy_game_state_elapsed_minutes_migrates_to_calendar_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Calendar Migration")
            repository.set_state_value("elapsed_minutes", "1234")
            connection = sqlite3.connect(repository.db_path)
            try:
                connection.execute(
                    "DELETE FROM settings WHERE key = ?",
                    ("calendar.current_minute",),
                )
                connection.commit()
            finally:
                connection.close()

            migrated_repository = SaveRepository(repository.db_path)

            self.assertEqual(migrated_repository.get_current_calendar_minute(), 1234)
            self.assertNotIn("elapsed_minutes", migrated_repository.get_state_snapshot())

```

## Retired behavior test: test_existing_duplicate_inventory_stacks_are_coalesced_on_load

```python
    def test_existing_duplicate_inventory_stacks_are_coalesced_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "old.sqlite3"
            connection = sqlite3.connect(save_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE inventory_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT '',
                        quantity INTEGER NOT NULL DEFAULT 1,
                        description TEXT NOT NULL DEFAULT '',
                        value_base_units INTEGER NOT NULL DEFAULT 0
                    );
                    INSERT INTO inventory_items (
                        name,
                        category,
                        quantity,
                        description,
                        value_base_units
                    )
                    VALUES
                        ('Silver-Spire Fern', 'Botanical', 2, 'Cool-natured fern.', 8),
                        ('Silver-Spire Fern', 'Botanical', 2, 'Cool-natured fern.', 8);
                    """
                )
            finally:
                connection.close()

            repository = SaveRepository(save_path)
            fern_items = [
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Silver-Spire Fern"
            ]

            self.assertEqual(len(fern_items), 1)
            self.assertEqual(fern_items[0]["quantity"], 4)

```

## Retired behavior test: test_normal_roll_history_does_not_nudge_d20_test_roll

```python
    def test_normal_roll_history_does_not_nudge_d20_test_roll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Luck Test")
            repository.upsert_skill("Prospecting", "Reading ore signs and mineral veins.", 3)

            for roll in [3, 11, 9, 12, 13]:
                repository.record_d20_test(
                    skill_name="Prospecting",
                    level=3,
                    bonus=6,
                    roll=roll,
                    total=roll + 6,
                    dc=14,
                    outcome="success" if roll + 6 >= 14 else "failure",
                )

            result = EventApplier(repository, rng=_FixedRollRng(8)).apply_event(
                {
                    "type": "D20TestRequestedEvent",
                    "payload": {"attribute": 'Dexterity', "test_kind": "check", "reason": "A consequential uncertain action", "skill_name": "Prospecting", "dc": 15},
                }
            )

            self.assertEqual(result.status, "applied")
            self.assertEqual(result.payload["raw_roll"], 8)
            self.assertEqual(result.payload["bad_luck_nudge"], 0)
            self.assertEqual(result.payload["roll"], 8)
            self.assertEqual(result.payload["total"], 14)
            self.assertEqual(result.payload["outcome"], "failure")

```

## Retired behavior test: test_bad_luck_streak_nudges_d20_test_roll

```python
    def test_bad_luck_streak_nudges_d20_test_roll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Luck Test")
            repository.upsert_skill("Prospecting", "Reading ore signs and mineral veins.", 3)

            for roll in [3, 11, 9, 2, 13, 5, 4]:
                repository.record_d20_test(
                    skill_name="Prospecting",
                    level=3,
                    bonus=6,
                    roll=roll,
                    total=roll + 6,
                    dc=14,
                    outcome="success" if roll + 6 >= 14 else "failure",
                )

            result = EventApplier(repository, rng=_FixedRollRng(8)).apply_event(
                {
                    "type": "D20TestRequestedEvent",
                    "payload": {"attribute": 'Dexterity', "test_kind": "check", "reason": "A consequential uncertain action", "skill_name": "Prospecting", "dc": 15},
                }
            )
            check = repository.list_d20_tests(limit=1)[0]

            self.assertEqual(result.status, "applied")
            self.assertEqual(result.payload["raw_roll"], 8)
            self.assertEqual(result.payload["bad_luck_nudge"], 2)
            self.assertEqual(result.payload["roll"], 10)
            self.assertEqual(result.payload["total"], 16)
            self.assertEqual(result.payload["outcome"], "success")
            self.assertEqual(check["roll"], 10)
            self.assertEqual(check["total"], 16)

```

## Retired behavior test: test_combat_started_event_is_rejected_for_narrative_combat

```python
    def test_combat_started_event_is_rejected_for_narrative_combat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Narrative Combat Event Test"
            )
            repository.set_setting(
                "combat.preferences",
                {"resolution_mode": "narrative", "focus": "balanced"},
            )

            result = EventApplier(repository).apply_event(
                {
                    "type": "CombatStartedEvent",
                    "payload": {"enemy_name": "Bandit"},
                }
            )

            self.assertEqual(result.status, "skipped")
            self.assertIn("narrative combat", result.message)
            self.assertFalse(repository.is_combat_active())

```

## Retired behavior test: test_combat_started_event_persists_combat_state

```python
    def test_combat_started_event_persists_combat_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Combat Event Test")
            repository.add_inventory_item(
                "Spear",
                "Weapon",
                1,
                "A sturdy one-handed spear.",
                6,
                metadata={
                    "item_type": "Weapon",
                    "weapon_hands": "one-handed",
                    "damage": "1d8",
                },
            )
            repository.set_player_equipment({"Main Hand": "Spear"})
            repository.set_setting("player.health_current", 18)
            repository.set_setting("player.health_max", 24)

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "CombatStartedEvent",
                        "payload": {
                            "description": "Two bandits draw blades.",
                            "enemies": [
                                {
                                    "name": "Bandit",
                                    "health": 7,
                                    "armor_rating": 12,
                                    "to_hit_bonus": 3,
                                    "initiative_bonus": 4,
                                    "personality": "aggressive",
                                    "weapon_name": "Rusty Knife",
                                    "ammunition_type_required": "",
                                    "clip_size": 0,
                                    "clip_ammo": 0,
                                    "bullets_per_attack": 0,
                                    "reserve_ammo": 0,
                                    "damage": "1d6+1",
                                    "loot": ["Rusty Knife", "Copper Ring"],
                                }
                            ],
                            "allies": [
                                {
                                    "name": "Mira",
                                    "health": 10,
                                    "armor_rating": 11,
                                    "to_hit_bonus": 2,
                                    "initiative_bonus": 1,
                                    "personality": "cautious",
                                    "weapon_name": "Shortbow",
                                    "ammunition_type_required": "Arrow",
                                    "clip_size": 1,
                                    "clip_ammo": 1,
                                    "bullets_per_attack": 1,
                                    "reserve_ammo": 12,
                                    "damage": "1d4",
                                }
                            ],
                        },
                    }
                ]
            )
            combat_state = repository.get_combat_state()
            player = next(
                combatant
                for combatant in combat_state["combatants"]
                if combatant["id"] == "player"
            )
            ally = next(
                combatant
                for combatant in combat_state["combatants"]
                if combatant["name"] == "Mira"
            )
            enemy = next(
                combatant
                for combatant in combat_state["combatants"]
                if combatant["name"] == "Bandit"
            )

            self.assertEqual(results[0].status, "applied")
            self.assertTrue(combat_state["active"])
            self.assertEqual(combat_state["round"], 1)
            self.assertEqual(player["name"], "Player Name")
            self.assertEqual(player["team"], "party")
            self.assertEqual(player["current_health"], 18)
            self.assertEqual(player["max_health"], 24)
            self.assertEqual(player["damage"], "1d8")
            self.assertEqual(player["to_hit_bonus"], 2)
            self.assertEqual(ally["name"], "Mira")
            self.assertEqual(ally["team"], "party")
            self.assertEqual(ally["to_hit_bonus"], 2)
            self.assertEqual(enemy["name"], "Bandit")
            self.assertEqual(enemy["team"], "enemy")
            self.assertEqual(enemy["current_health"], 7)
            self.assertEqual(enemy["armor_rating"], 12)
            self.assertEqual(enemy["to_hit_bonus"], 3)
            self.assertEqual(enemy["initiative_bonus"], 4)
            self.assertEqual(enemy["personality"], "aggressive")
            self.assertGreater(enemy["threat_level"], 0)
            self.assertEqual(enemy["damage"], "1d6+1")
            self.assertEqual(enemy["loot"], ["Rusty Knife", "Copper Ring"])
            self.assertEqual(combat_state["log"][0], "Two bandits draw blades.")
            self.assertIn("Initiative order:", combat_state["log"][1])
            self.assertTrue(
                all(
                    1 <= combatant["initiative_roll"] <= 20
                    for combatant in combat_state["combatants"]
                )
            )

            skipped = EventApplier(repository).apply_event(
                {"type": "CombatStartedEvent", "payload": {"enemy_name": "Second Bandit"}}
            )

            self.assertEqual(skipped.status, "skipped")

```

## Retired behavior test: test_inventory_item_added_upgrades_player_weapon_damage_above_unarmed

```python
    def test_inventory_item_added_upgrades_player_weapon_damage_above_unarmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Weapon Floor Test")

            result = EventApplier(repository).apply_events(
                [
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_type": "Weapon",
                            "item_name": "Rusty Dagger",
                            "description": "A small blade that should still matter.",
                            "amount": 1,
                            "value_base_units": 5,
                            "weapon_hands": "one-handed",
                            "damage": "1d4",
                            "attack_skill": "Melee",
                            "attack_range_feet": 5,
                        },
                    }
                ]
            )[0]

            item = _require(
                next(
                    (
                        inventory_item
                        for inventory_item in repository.list_inventory_items()
                        if inventory_item["name"] == "Rusty Dagger"
                    ),
                    None,
                )
            )

            self.assertEqual(result.status, "applied")
            self.assertEqual(item["metadata"]["item_type"], "Weapon")
            self.assertEqual(item["metadata"]["damage"], "1d6")

```

## Retired behavior test: test_combat_ally_preserves_party_npc_id_and_initial_vitals

```python
    def test_combat_ally_preserves_party_npc_id_and_initial_vitals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Party Combat Test")
            repository.upsert_npc(
                npc_id="mira_coppercup",
                name="Mira Coppercup",
                display_name="Mira",
                role="Scout",
                location="Old Road",
            )
            repository.upsert_party_member(
                "mira_coppercup", health_current=18, health_max=18, armor_class=11
            )

            result = EventApplier(repository).apply_event(
                {
                    "type": "CombatStartedEvent",
                    "payload": {
                        "enemies": [{"name": "Bandit", "health": 8}],
                        "allies": [
                            {
                                "npc_id": "mira_coppercup",
                                "name": "Mira",
                                "health": 14,
                                "armor_rating": 13,
                            }
                        ],
                    },
                }
            )

            ally = next(
                combatant
                for combatant in repository.get_combat_state()["combatants"]
                if combatant.get("npc_id") == "mira_coppercup"
            )
            party_member = repository.list_party_members()[0]
            self.assertEqual(result.status, "applied")
            self.assertEqual(ally["npc_id"], "mira_coppercup")
            self.assertEqual(party_member["npc_id"], "mira_coppercup")
            self.assertEqual(party_member["status"], "Active")
            self.assertEqual(party_member["health_current"], 14)
            self.assertEqual(party_member["armor_class"], 13)

```

## Retired behavior test: test_combat_screen_disables_manual_start_for_narrative_combat

```python
    def test_combat_screen_disables_manual_start_for_narrative_combat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Narrative Combat UI"
            )
            repository.set_setting(
                "combat.preferences",
                {"resolution_mode": "narrative", "focus": "balanced"},
            )
            screen = CombatScreen(playtesting_tools=True)
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertIn("Gemini resolves fights", screen.status_label.text())
            self.assertFalse(screen.start_button.isEnabled())
            self.assertFalse(screen.add_combatant_button.isEnabled())
            screen.close()

```
