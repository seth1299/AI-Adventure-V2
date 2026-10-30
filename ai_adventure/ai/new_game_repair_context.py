"""Explicit dependency routing for bounded New Game section repairs."""
import copy
import re
from typing import Any

from ai_adventure.bundled_images import bundled_image_prompt_context

# (setup inputs, previously generated context, applicable requirements)
REPAIR_DEPENDENCIES = {
    "timed_events": (("starting_locations", "starting_calendar", "calendar", "world_context", "character"),
                     ("world_summary", "locations", "calendar_settings", "starting_calendar", "current_calendar"),
                     ("timed_events",)),
    "bestiary": (("magic",), ("world_summary", "selected_genre", "locations"), ("bestiary",)),
    "known_crafting_items": (("skills",), ("world_summary", "locations"), ("crafting_knowledge",)),
    "known_crafting_recipes": (("skills",), ("world_summary", "known_crafting_items", "starting_items", "skills"), ("crafting_knowledge",)),
    "starting_items": (("starter_items", "skills"), ("world_summary", "currency_denominations"), ("starter_inventory", "source_index_rule", "category_rule", "storage_rule", "inventory_storage")),
    "starting_npcs": (("starting_npcs", "starting_party_npc_ids"), ("world_summary", "locations", "start_location"), ("events",)),
    "skills": (("skills", "character"), ("world_summary",), ("skill_generation", "skill_limits")),
    "starting_spells": (("magic", "skills"), ("world_summary", "skills"), ("magic",)),
    "locations": (("start_location", "start_location_mode"), ("world_summary", "start_location"), ("travel_locations", "starting_location")),
    "calendar_settings": (("calendar",), ("world_summary",), ("calendar_generation",)),
    "currency_denominations": (("currency_denominations", "currency_description"), ("world_summary",), ("currency_generation",)),
    "starting_currency_balance_base_units": (("starting_currency", "currency_denominations"), ("currency_denominations",), ("starting_currency_balance",)),
    "weather": (("weather",), ("world_summary", "start_location"), ()),
}


def repair_fields(errors: list[str], properties: dict[str, Any]) -> list[str] | None:
    """Route validator paths, never guess a section from free-form error prose."""
    fields = set()
    for error in errors:
        match = re.match(r"^\$\.([A-Za-z_][A-Za-z_0-9]*)(?:\[|\.|\s)", error)
        if match is None or match[1] not in properties or match[1] not in REPAIR_DEPENDENCIES:
            return None
        fields.add(match[1])
    return sorted(fields) or None


def section_schema(schema: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": {field: copy.deepcopy(schema["properties"][field]) for field in fields},
            "required": list(fields), "additionalProperties": False}


def repair_context(setup_packet, approved_state, candidate, fields, errors, attempt):
    """Build a new prompt packet from a whitelist; never trim a rendered prompt."""
    setup = setup_packet.get("setup", {})
    setup_keys = {"specified_genre", "world_context"}
    context_keys = set()
    rule_keys = {"ai_invention_policy", "creative_ideas"}
    for field in fields:
        inputs, context, rules = REPAIR_DEPENDENCIES[field]
        setup_keys.update(inputs)
        context_keys.update(context)
        rule_keys.update(rules)
    context = {"current_calendar": setup_packet.get("current_calendar"),
               **(approved_state or {}), **candidate}
    selected_setup = {key: setup[key] for key in setup_keys if key in setup}
    # Skill choice can require attributes, but never the character's biography.
    if isinstance(selected_setup.get("character"), dict):
        selected_setup["character"] = {key: value for key, value in selected_setup["character"].items()
                                        if key in {"name", "attributes"}}
    if isinstance(selected_setup.get("magic"), dict) and "starting_spells" not in fields:
        selected_setup["magic"] = {key: value for key, value in selected_setup["magic"].items()
                                   if key == "world_contains_magic"}
    compact_keys = {"id", "item_uuid", "name", "category", "description", "terrain", "level", "bonus"}
    selected_context = {}
    for key in context_keys - set(fields):
        if key not in context:
            continue
        value = context[key]
        if isinstance(value, list):
            value = [{k: v for k, v in record.items() if k in compact_keys} if isinstance(record, dict) else record
                     for record in value]
        selected_context[key] = value
    requirements = setup_packet.get("requirements", {})
    return {
        **(bundled_image_prompt_context() if set(fields) & {"skills", "starting_items", "known_crafting_items"} else {}),
        "attempt": attempt, "repair_fields": fields, "validation_errors": errors,
        "setup": selected_setup, "related_state": selected_context,
        "requirements": {key: requirements[key] for key in rule_keys if key in requirements},
        "creative_ideas": setup_packet.get("creative_ideas", {}),
        "rejected_sections": {field: candidate[field] for field in fields if field in candidate},
    }
