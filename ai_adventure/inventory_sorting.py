from __future__ import annotations

from typing import Any, Iterable

from ai_adventure.inventory_storage import metadata, pounds


def sort_inventory_items(
    items: Iterable[dict[str, Any]],
    *,
    primary_field: str,
    primary_descending: bool,
    secondary_field: str = "",
    secondary_descending: bool = False,
) -> list[dict[str, Any]]:
    """Sorts inventory with independent primary and optional secondary directions."""

    sorted_items = sorted(items, key=lambda item: _sort_value(item, "name"))
    if secondary_field:
        sorted_items = sorted(
            sorted_items,
            key=lambda item: _sort_value(item, secondary_field, secondary_descending),
            reverse=secondary_descending,
        )
    return sorted(
        sorted_items,
        key=lambda item: _sort_value(item, primary_field, primary_descending),
        reverse=primary_descending,
    )


def inventory_row_weight(item: dict[str, Any]) -> float | None:
    """Return the total weight of the displayed stack, if known."""
    unit_weight = pounds(metadata(item).get("weight_lb"), default=-1)
    return None if unit_weight < 0 else unit_weight * _nonnegative_int(item.get("quantity", 0))


def _sort_value(item: dict[str, Any], field: str, descending: bool = False) -> Any:
    if field == "category":
        return str(item.get("category", "Item") or "Item").casefold()
    if field == "price":
        return _nonnegative_int(item.get("value_base_units", 0))
    if field == "quantity":
        return _nonnegative_int(item.get("quantity", 0))
    if field == "weight":
        weight = inventory_row_weight(item)
        return weight if weight is not None else float("-inf" if descending else "inf")
    return str(item.get("name", "") or "").casefold()


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
