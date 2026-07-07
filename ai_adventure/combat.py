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
DEFAULT_ATTACK_SKILL = "Melee"
DEFAULT_TO_HIT_BONUS = 0
DEFAULT_ATTACK_RANGE_FEET = 5
DEFAULT_RANGED_ATTACK_RANGE_FEET = 100
COMBAT_PERSONALITIES = ("balanced", "aggressive", "cautious", "intelligent")


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

    if item_type == "Container":
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


def _normalize_container_contents_items(raw_items: Any) -> list[dict[str, Any]]:
    """Returns canonical inventory records stored inside a container."""

    if not isinstance(raw_items, list):
        return []

    items: list[dict[str, Any]] = []

    for raw_item in raw_items:
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
