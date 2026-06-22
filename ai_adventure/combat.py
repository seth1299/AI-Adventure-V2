from __future__ import annotations

import random
import re
from typing import Any


BODY_PARTS = ["Head", "Torso", "Arms", "Hands", "Legs", "Feet"]
HAND_SLOTS = ["Main Hand", "Off Hand"]
EQUIPMENT_SLOTS = [*HAND_SLOTS, *BODY_PARTS]
DEFAULT_PLAYER_MAX_HEALTH = 20
DEFAULT_BASE_ARMOR_RATING = 10
DEFAULT_UNARMED_DAMAGE = "1d4"
DEFAULT_WEAPON_DAMAGE = "1d6"
DEFAULT_TWO_HANDED_DAMAGE = "1d10"


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
    """Returns clean item metadata for equipment and combat."""

    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    clean_category = str(category or "").strip()
    clean_name = str(name or "").strip()
    folded = f"{clean_category} {clean_name} {description}".casefold()
    item_type = str(metadata.get("item_type", "") or "").strip().title()

    if not item_type:
        if "weapon" in folded or any(word in folded for word in _WEAPON_HINTS):
            item_type = "Weapon"
        elif "armor" in folded or "armour" in folded or "shield" in folded:
            item_type = "Armor"
        else:
            item_type = clean_category or "Item"

    clean_metadata: dict[str, Any] = {"item_type": item_type}

    if item_type == "Weapon":
        hands = _normalize_weapon_hands(metadata.get("weapon_hands"), folded)
        clean_metadata["weapon_hands"] = hands
        clean_metadata["damage"] = normalize_damage_expression(
            metadata.get("damage", metadata.get("damage_expression")),
            default=DEFAULT_TWO_HANDED_DAMAGE if hands == "two-handed" else DEFAULT_WEAPON_DAMAGE,
        )
        clean_metadata["damage_type"] = str(metadata.get("damage_type", "") or "").strip()
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

    return clean_metadata


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

    for slot in EQUIPMENT_SLOTS:
        item_name = str(raw_equipment.get(slot, "") or "").strip()

        if not item_name:
            continue

        item = inventory_by_name.get(item_name.casefold())

        if item is None or not item_is_valid_for_slot(item, slot):
            continue

        equipment[slot] = str(item.get("name", item_name))

    main_item = inventory_by_name.get(equipment["Main Hand"].casefold())

    if main_item is not None and item_weapon_hands(main_item) == "two-handed":
        equipment["Off Hand"] = ""

    off_item = inventory_by_name.get(equipment["Off Hand"].casefold())

    if off_item is not None and item_weapon_hands(off_item) == "two-handed":
        equipment["Off Hand"] = ""

    return equipment


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

    return {
        "id": str(raw_combatant.get("id", f"combatant-{index + 1}")),
        "name": str(raw_combatant.get("name", f"Combatant {index + 1}")).strip()
        or f"Combatant {index + 1}",
        "team": team,
        "current_health": current_health,
        "max_health": max_health,
        "armor_rating": max(1, _safe_int(raw_combatant.get("armor_rating"), 10)),
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
