from __future__ import annotations

from typing import Any


COMMON_MEASUREMENT_UNITS: tuple[str, ...] = (
    "each",
    "mL",
    "L",
    "drops",
    "tsp",
    "tbsp",
    "cups",
    "grams",
    "kg",
    "oz",
    "lbs",
)

DEFAULT_MEASUREMENT_UNIT = "each"
CRAFTING_INGREDIENT_CATEGORIES: tuple[str, ...] = (
    "Material",
    "Ingredient",
    "Reagent",
    "Crafting Item",
    "Container",
)
CRAFTING_INGREDIENT_CATEGORY_NAMES = ", ".join(CRAFTING_INGREDIENT_CATEGORIES)
CRAFTING_ITEM_RARITIES: tuple[str, ...] = (
    "Common",
    "Uncommon",
    "Rare",
    "Very Rare",
)


def normalize_crafting_item_rarity(value: Any) -> str:
    """Returns the canonical crafting-item rarity label."""

    clean_value = str(value or "").strip().casefold()
    for rarity in CRAFTING_ITEM_RARITIES:
        if clean_value == rarity.casefold():
            return rarity
    return "Common"


def is_crafting_ingredient_category(value: Any) -> bool:
    """Returns true when an item category is usable as a recipe ingredient."""

    clean_value = str(value or "").strip().casefold()
    return any(
        clean_value == category.casefold()
        for category in CRAFTING_INGREDIENT_CATEGORIES
    )


def normalize_recipe_ingredients(value: Any) -> list[dict[str, Any]]:
    """Normalizes recipe ingredients into structured reagent measurements."""

    ingredients: list[dict[str, Any]] = []

    if isinstance(value, dict) and not _looks_like_ingredient_object(value):
        for reagent_name, quantity in value.items():
            ingredient = normalize_recipe_ingredient(
                {
                    "reagent_name": reagent_name,
                    "quantity": quantity,
                    "measure_amount": 1,
                    "measure_unit": DEFAULT_MEASUREMENT_UNIT,
                }
            )
            if ingredient is not None:
                ingredients.append(ingredient)
        return ingredients

    if isinstance(value, list):
        for item in value:
            ingredient = normalize_recipe_ingredient(item)
            if ingredient is not None:
                ingredients.append(ingredient)
        return ingredients

    ingredient = normalize_recipe_ingredient(value)
    return [ingredient] if ingredient is not None else []


def normalize_recipe_ingredient(value: Any) -> dict[str, Any] | None:
    """Normalizes one recipe ingredient."""

    if isinstance(value, str):
        name = value.strip()
        if not name:
            return None
        return {
            "reagent_name": name,
            "quantity": 1,
            "measure_amount": 1,
            "measure_unit": DEFAULT_MEASUREMENT_UNIT,
        }

    if not isinstance(value, dict):
        return None

    name = str(
        value.get(
            "reagent_name",
            value.get("name", value.get("ingredient", value.get("item_name", ""))),
        )
    ).strip()

    if not name:
        return None

    quantity = _safe_positive_int(
        value.get("quantity", value.get("count", value.get("amount", 1))),
        default=1,
    )
    measure_amount = _safe_positive_int(
        value.get("measure_amount", value.get("measurement_amount", 1)),
        default=1,
    )
    measure_unit = normalize_measurement_unit(
        value.get(
            "measure_unit",
            value.get("measurement_unit", value.get("unit", DEFAULT_MEASUREMENT_UNIT)),
        )
    )

    normalized = {
        "reagent_name": name,
        "quantity": quantity,
        "measure_amount": measure_amount,
        "measure_unit": measure_unit,
    }
    item_uuid = str(value.get("item_uuid", "") or "").strip()
    if item_uuid:
        normalized["item_uuid"] = item_uuid
    return normalized


def normalize_measurement_unit(value: Any) -> str:
    """Returns a supported measurement unit, falling back to each."""

    clean_value = str(value).strip()

    for unit in COMMON_MEASUREMENT_UNITS:
        if clean_value.casefold() == unit.casefold():
            return unit

    return DEFAULT_MEASUREMENT_UNIT


def format_recipe_ingredient(ingredient: dict[str, Any]) -> str:
    """Formats a structured ingredient for compact table display."""

    normalized = normalize_recipe_ingredient(ingredient)

    if normalized is None:
        return ""

    name = normalized["reagent_name"]
    quantity = normalized["quantity"]
    measure_amount = normalized["measure_amount"]
    measure_unit = normalized["measure_unit"]

    total_amount = quantity * measure_amount
    return f"{name} ({_format_measurement(total_amount, measure_unit)})"


def format_recipe_ingredients(ingredients: Any) -> str:
    """Formats a recipe ingredient list for display."""

    return ", ".join(
        formatted
        for formatted in (
            format_recipe_ingredient(ingredient)
            for ingredient in normalize_recipe_ingredients(ingredients)
        )
        if formatted
    )


def _looks_like_ingredient_object(value: dict[Any, Any]) -> bool:
    """Returns true when a dictionary is already one ingredient object."""

    keys = {str(key) for key in value}
    return bool(
        keys.intersection(
            {
                "reagent_name",
                "item_uuid",
                "name",
                "ingredient",
                "item_name",
                "quantity",
                "count",
                "measure_amount",
                "measurement_amount",
                "measure_unit",
                "measurement_unit",
                "unit",
            }
        )
    )


def _safe_positive_int(value: Any, *, default: int) -> int:
    """Converts a value to a positive integer."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    return max(1, parsed)


def _format_measurement(amount: int, unit: str) -> str:
    """Formats an amount and unit with readable spacing."""

    if unit in {"mL", "L", "kg", "oz", "lbs"}:
        return f"{amount}{unit}"

    return f"{amount} {unit}"
