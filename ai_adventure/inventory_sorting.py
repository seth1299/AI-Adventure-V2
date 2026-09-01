from __future__ import annotations

from typing import Any, Iterable


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
            key=lambda item: _sort_value(item, secondary_field),
            reverse=secondary_descending,
        )
    return sorted(
        sorted_items,
        key=lambda item: _sort_value(item, primary_field),
        reverse=primary_descending,
    )


def _sort_value(item: dict[str, Any], field: str) -> Any:
    if field == "category":
        return str(item.get("category", "Item") or "Item").casefold()
    if field == "price":
        return _nonnegative_int(item.get("value_base_units", 0))
    if field == "quantity":
        return _nonnegative_int(item.get("quantity", 0))
    return str(item.get("name", "") or "").casefold()


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
