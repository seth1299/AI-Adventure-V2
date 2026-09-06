"""Deterministic crafting rules shared by persistence, UI, and Gemini context."""

from __future__ import annotations

import math
from typing import Any

from ai_adventure.alchemy.ingredients import normalize_measurement_unit, normalize_recipe_ingredients
from ai_adventure.skills.rules import clamp_skill_level


DEFAULT_CRAFTING_SKILL = "Crafting"
DEFAULT_ACTIVE_WORK_AMOUNT = 30
DEFAULT_ACTIVE_MINUTES_PER_WORK = 1
MAX_CRAFTING_QUANTITY = 999


def normalize_recipe_stages(value: Any, *, active_work_amount: Any = None) -> list[dict[str, Any]]:
    """Returns a validated active/passive stage list for a recipe."""

    stages: list[dict[str, Any]] = []
    if isinstance(value, list):
        for index, raw_stage in enumerate(value):
            if not isinstance(raw_stage, dict):
                continue
            kind = str(raw_stage.get("kind", raw_stage.get("type", "active"))).strip().casefold()
            if kind not in {"active", "passive"}:
                continue
            stage_id = str(raw_stage.get("stage_id", raw_stage.get("id", f"stage_{index + 1}"))).strip()
            if not stage_id:
                stage_id = f"stage_{index + 1}"
            stage: dict[str, Any] = {"stage_id": stage_id, "kind": kind}
            if kind == "active":
                stage["work_amount"] = _positive_int(
                    raw_stage.get("work_amount", raw_stage.get("work", DEFAULT_ACTIVE_WORK_AMOUNT)),
                    DEFAULT_ACTIVE_WORK_AMOUNT,
                )
                stage["estimated_minutes"] = _positive_int(
                    raw_stage.get("estimated_minutes", raw_stage.get("minutes", 0)),
                    0,
                )
            else:
                stage["duration_minutes"] = _positive_int(
                    raw_stage.get("duration_minutes", raw_stage.get("minutes", 0)),
                    0,
                )
            stage["required_tool_item_uuids"] = _string_list(
                raw_stage.get("required_tool_item_uuids", raw_stage.get("tool_item_uuids", []))
            )
            stage["required_tool_item_names"] = _string_list(
                raw_stage.get("required_tool_item_names", raw_stage.get("tool_names", []))
            )
            stage["label"] = str(raw_stage.get("label", "")).strip()
            stages.append(stage)

    if not stages:
        stages.append(
            {
                "stage_id": "active",
                "kind": "active",
                "work_amount": _positive_int(active_work_amount, DEFAULT_ACTIVE_WORK_AMOUNT),
                "estimated_minutes": 0,
                "required_tool_item_uuids": [],
                "required_tool_item_names": [],
                "label": "",
            }
        )
    return stages


def normalize_recipe_plan(recipe: dict[str, Any]) -> dict[str, Any]:
    """Normalizes the deterministic fields stored with one recipe."""

    stages = normalize_recipe_stages(
        recipe.get("stages", []),
        active_work_amount=recipe.get("active_work_amount", recipe.get("work_amount")),
    )
    tools = _string_list(
        recipe.get("required_tool_item_uuids", recipe.get("required_tools", []))
    )
    tool_names = _string_list(recipe.get("required_tool_item_names", []))
    return {
        "skill_name": str(recipe.get("skill_name", DEFAULT_CRAFTING_SKILL)).strip()
        or DEFAULT_CRAFTING_SKILL,
        "stages": stages,
        "required_tool_item_uuids": tools,
        "required_tool_item_names": tool_names,
        "result_item_uuid": str(recipe.get("result_item_uuid", "")).strip(),
        "result_item_name": str(recipe.get("result_item_name", "")).strip(),
    }


def inventory_item_uuid(item: dict[str, Any]) -> str:
    """Returns the stable identity used for deterministic item matching."""

    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    return str(metadata.get("item_uuid", item.get("item_uuid", ""))).strip()


def evaluate_recipe_craftability(
    recipe: dict[str, Any],
    inventory_items: list[dict[str, Any]],
    *,
    quantity: int = 1,
) -> dict[str, Any]:
    """Evaluates exact ingredient/tool availability without changing state."""

    requested_quantity = min(MAX_CRAFTING_QUANTITY, max(1, _positive_int(quantity, 1)))
    inventory_by_uuid: dict[str, dict[str, Any]] = {}
    inventory_by_name: dict[str, dict[str, Any]] = {}
    for item in inventory_items:
        if not isinstance(item, dict):
            continue
        identity = inventory_item_uuid(item)
        name = str(item.get("name", "")).strip().casefold()
        if identity:
            previous = inventory_by_uuid.setdefault(
                identity,
                {"quantity": 0, "quantity_unit": str(item.get("quantity_unit", "each"))},
            )
            previous["quantity"] += max(0, _positive_int(item.get("quantity"), 0))
        if name:
            previous = inventory_by_name.setdefault(
                name,
                {"quantity": 0, "quantity_unit": str(item.get("quantity_unit", "each"))},
            )
            previous["quantity"] += max(0, _positive_int(item.get("quantity"), 0))

    missing_ingredients: list[dict[str, Any]] = []
    possible_quantity: int | None = None
    for ingredient in normalize_recipe_ingredients(recipe.get("ingredients", [])):
        name = str(ingredient.get("reagent_name", "")).strip()
        identity = str(ingredient.get("item_uuid", "")).strip()
        required_per_result = max(1, _positive_int(ingredient.get("quantity"), 1)) * max(
            1, _positive_int(ingredient.get("measure_amount"), 1)
        )
        required = required_per_result * requested_quantity
        unit = normalize_measurement_unit(ingredient.get("measure_unit", "each"))
        owned_record = inventory_by_uuid.get(identity) if identity else None
        if owned_record is None:
            owned_record = inventory_by_name.get(name.casefold())
        owned = int(owned_record.get("quantity", 0)) if owned_record else 0
        owned_unit = str(owned_record.get("quantity_unit", unit)) if owned_record else unit
        unit_matches = owned_unit.casefold() == unit.casefold()
        if not identity or not unit_matches or owned < required:
            missing_ingredients.append(
                {
                    "item_uuid": identity,
                    "name": name,
                    "owned": owned,
                    "owned_unit": owned_unit,
                    "required": required,
                    "required_unit": unit,
                    "reason": (
                        "missing_item_id" if not identity else
                        "unit_mismatch" if not unit_matches else "insufficient_quantity"
                    ),
                }
            )
        possible = 0 if not identity or not unit_matches else owned // required_per_result
        possible_quantity = possible if possible_quantity is None else min(possible_quantity, possible)

    plan = normalize_recipe_plan(recipe)
    missing_tools = _missing_tools(plan, inventory_by_uuid)
    if missing_tools:
        possible_quantity = 0
    if possible_quantity is None:
        possible_quantity = 0
    return {
        "recipe_id": str(recipe.get("id", "")).strip(),
        "recipe_name": str(recipe.get("name", "")).strip(),
        "requested_quantity": requested_quantity,
        "craftable": not missing_ingredients and not missing_tools,
        "craftable_quantity": max(0, possible_quantity),
        "missing_ingredients": missing_ingredients,
        "missing_tools": missing_tools,
    }


def recipe_estimated_time(recipe: dict[str, Any], skills: list[dict[str, Any]]) -> dict[str, Any]:
    """Returns skill-adjusted active and fixed passive estimates for display."""

    plan = normalize_recipe_plan(recipe)
    skill_name = plan["skill_name"]
    level = 1
    for skill in skills:
        if str(skill.get("name", "")).strip().casefold() == skill_name.casefold():
            level = clamp_skill_level(_positive_int(skill.get("level"), 1))
            break
    active_minutes = 0
    active_work = 0
    for stage in plan["stages"]:
        if stage.get("kind") != "active":
            continue
        work_amount = _positive_int(stage.get("work_amount"), 0)
        active_work += work_amount
        explicit_minutes = _positive_int(stage.get("estimated_minutes"), 0)
        active_minutes += explicit_minutes or math.ceil(work_amount / max(1, level))
    passive_minutes = sum(
        _positive_int(stage.get("duration_minutes"), 0)
        for stage in plan["stages"]
        if stage.get("kind") == "passive"
    )
    return {
        "skill_name": skill_name,
        "skill_level": level,
        "active_minutes": active_minutes,
        "passive_minutes": passive_minutes,
        "active_work_amount": active_work,
    }


def _missing_tools(
    plan: dict[str, Any],
    inventory_by_uuid: dict[str, dict[str, Any]],
) -> list[str]:
    """Returns player-facing names for unavailable required tools."""

    missing: list[str] = []
    scopes = [
        (
            list(plan["required_tool_item_uuids"]),
            list(plan["required_tool_item_names"]),
        )
    ] + [
        (
            list(stage.get("required_tool_item_uuids", [])),
            list(stage.get("required_tool_item_names", [])),
        )
        for stage in plan["stages"]
    ]
    for required_ids, required_names in scopes:
        for index, identity in enumerate(
            dict.fromkeys(item.strip() for item in required_ids if str(item).strip())
        ):
            if not inventory_by_uuid.get(identity, {}).get("quantity", 0):
                missing.append(
                    str(required_names[index]).strip()
                    if index < len(required_names) and str(required_names[index]).strip()
                    else identity
                )

        # A name without a corresponding UUID is intentionally never
        # considered owned. Names are retained only for a useful message;
        # UUIDs are the authoritative identity for craftability.
        for index, name in enumerate(required_names):
            clean_name = str(name).strip()
            if clean_name and index >= len(required_ids):
                missing.append(clean_name)
    return list(dict.fromkeys(missing))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default
