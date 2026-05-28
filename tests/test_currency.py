from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.currency import (
    DEFAULT_CURRENCY_DENOMINATIONS,
    format_currency_amount,
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
