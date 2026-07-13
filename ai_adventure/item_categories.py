from __future__ import annotations

from typing import Any


_CRAFTING_CATEGORIES = {"material", "ingredient", "reagent", "crafting item"}
_POISON_TERMS = ("poison", "toxin", "venom")
_RAW_POISON_SOURCE_TERMS = (
    "gland", "sac", "fang", "root", "leaf", "leaves", "herb", "mushroom",
    "spore", "extract ingredient",
)


def normalize_inventory_category(
    category: Any,
    *,
    name: Any = "",
    description: Any = "",
    item_type: Any = "",
) -> str:
    """Returns a concrete category based on an inventory item's finished function."""

    clean_category = str(category or "Item").strip() or "Item"
    folded_category = clean_category.casefold()
    clean_name = str(name or "").strip()
    text = " ".join((clean_name, str(description or ""), str(item_type or ""))).casefold()

    if folded_category in {"information", "info", "knowledge"}:
        if any(word in text for word in ("book", "journal", "notebook", "manual", "ledger", "tome")):
            return "Book"
        return "Document"

    if folded_category in _CRAFTING_CATEGORIES and any(term in text for term in _POISON_TERMS):
        name_text = clean_name.casefold()
        looks_like_raw_source = any(term in name_text for term in _RAW_POISON_SOURCE_TERMS)
        looks_like_finished_poison = (
            any(term in name_text for term in _POISON_TERMS)
            or any(
                phrase in text
                for phrase in (
                    "vial of", "bottle of", "dose of", "applied poison",
                    "contact poison", "ingested poison", "injury poison",
                )
            )
        )
        if looks_like_finished_poison and not looks_like_raw_source:
            return "Poison"

    return clean_category
