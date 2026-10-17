from __future__ import annotations

import unittest

from ai_adventure.inventory_sorting import sort_inventory_items


class InventorySortingTests(unittest.TestCase):
    def test_descending_primary_keeps_default_name_ties_ascending(self) -> None:
        items = [
            {"name": "Tweezers", "category": "Tool"},
            {"name": "Sickle", "category": "Weapon"},
            {"name": "Small Scissors", "category": "Tool"},
            {"name": "Pruning Shears", "category": "Tool"},
        ]

        sorted_items = sort_inventory_items(
            items,
            primary_field="category",
            primary_descending=True,
        )

        self.assertEqual(
            [item["name"] for item in sorted_items],
            ["Sickle", "Pruning Shears", "Small Scissors", "Tweezers"],
        )

    def test_price_descending_then_name_descending_matches_requested_groups(self) -> None:
        items = [
            {"name": "Baseball", "value_base_units": 10},
            {"name": "Cantaloupe", "value_base_units": 10},
            {"name": "Lamp", "value_base_units": 10},
            {"name": "Candy Bar Bag", "value_base_units": 8},
            {"name": "Marinated Chicken", "value_base_units": 8},
            {"name": "Xylophone", "value_base_units": 8},
            {"name": "Bag of Rice", "value_base_units": 6},
            {"name": "Lemon Scented Wet Wipes", "value_base_units": 6},
            {"name": "Mop Handle", "value_base_units": 6},
        ]

        sorted_items = sort_inventory_items(
            items,
            primary_field="price",
            primary_descending=True,
            secondary_field="name",
            secondary_descending=True,
        )

        self.assertEqual(
            [item["name"] for item in sorted_items],
            [
                "Lamp",
                "Cantaloupe",
                "Baseball",
                "Xylophone",
                "Marinated Chicken",
                "Candy Bar Bag",
                "Mop Handle",
                "Lemon Scented Wet Wipes",
                "Bag of Rice",
            ],
        )


if __name__ == "__main__":
    unittest.main()
