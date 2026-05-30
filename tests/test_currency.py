from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.currency import (
    DEFAULT_CURRENCY_DENOMINATIONS,
    describe_currency_denominations,
    format_currency_amount,
    normalize_currency_denominations,
)
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository


class CurrencyTests(unittest.TestCase):
    def test_formats_largest_denominations_first(self) -> None:
        self.assertEqual(
            format_currency_amount(45, DEFAULT_CURRENCY_DENOMINATIONS),
            "4 Silver Pieces and 5 Copper Pieces",
        )
        self.assertEqual(
            format_currency_amount(125, DEFAULT_CURRENCY_DENOMINATIONS),
            "1 Gold Piece, 2 Silver Pieces, and 5 Copper Pieces",
        )

    def test_describes_currency_denominations(self) -> None:
        description = describe_currency_denominations(
            [
                {"name": "Bit", "plural_name": "Bits", "value": 1},
                {"name": "Crown", "plural_name": "Crowns", "value": 12},
            ]
        )

        self.assertIn("Bit (1 base units)", description)
        self.assertIn("Crown (12 base units)", description)

    def test_custom_currency_does_not_insert_copper_baseline(self) -> None:
        denominations = normalize_currency_denominations(
            [
                {"name": "Dollar", "plural_name": "Dollars", "value": 100},
                {"name": "Quarter", "plural_name": "Quarters", "value": 25},
            ],
            fallback_denominations=[],
        )

        self.assertEqual(denominations[0]["name"], "Quarter")
        self.assertEqual(denominations[0]["value"], 25)
        self.assertNotIn(
            "Copper Piece",
            {denomination["name"] for denomination in denominations},
        )

    def test_inventory_items_store_baseline_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Currency Test")
            repository.add_inventory_item(
                "Silvered Dagger",
                "Weapon",
                1,
                "A dagger with a silvered edge.",
                value_base_units=125,
            )

            items = [
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Silvered Dagger"
            ]

            self.assertEqual(items[0]["value_base_units"], 125)

    def test_inventory_events_accept_item_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Currency Test")

            EventApplier(repository).apply_events(
                [
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_name": "Amber Ring",
                            "item_type": "Jewelry",
                            "value_base_units": 45,
                        },
                    },
                    {
                        "type": "InventoryItemModifiedEvent",
                        "payload": {
                            "target_name": "Amber Ring",
                            "new_value_base_units": 125,
                        },
                    },
                ]
            )

            items = [
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Amber Ring"
            ]

            self.assertEqual(items[0]["value_base_units"], 125)


if __name__ == "__main__":
    unittest.main()
