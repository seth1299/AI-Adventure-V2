from __future__ import annotations

import re
from typing import Any
from ai_adventure.inventory_storage import pounds


BODY_PARTS = ["Head", "Torso", "Arms", "Hands", "Legs", "Feet"]


HAND_SLOTS = ["Main Hand", "Off Hand"]


EQUIPMENT_SLOTS = [*HAND_SLOTS, *BODY_PARTS, "Back"]


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
    """Returns clean item metadata for equipment and containers."""

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
        clean_metadata["weapon_hands"] = _normalize_weapon_hands(metadata.get("weapon_hands"), folded)
        return clean_metadata
    if item_type == "Armor":
        clean_metadata["covers_body_parts"] = normalize_body_parts(
            metadata.get("covers_body_parts", metadata.get("body_parts")),
            name=clean_name, category=clean_category, description=description,
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
        if item_type == "Container" and (metadata.get("equipment_slot") == "Back" or re.search(r"\b(backpack|satchel|knapsack)\b", clean_name, re.I)):
            clean_metadata["equipment_slot"] = "Back"
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
        "lockpick_attribute": str(container.get("lockpick_attribute", "Dexterity")),
        "trap_disarm_attribute": str(container.get("trap_disarm_attribute", "Dexterity")),
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

    if not metadata.get("moveable") or not metadata.get("storable"):
        return False

    if slot == "Back":
        return item_type == "container" and metadata.get("equipment_slot") == "Back"

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
