from pathlib import Path
import tempfile
import unittest

from ai_adventure.item_names import singularize_item_name
from ai_adventure.application.new_game_service import _starter_items_for_save
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository


class ItemNameTests(unittest.TestCase):
    def test_countable_stacks_and_nouns_that_should_not_change(self):
        for name, expected in {
            "Glass Alchemy Flasks": "Glass Alchemy Flask", "Glass Alchemy Flask": "Glass Alchemy Flask",
            "Bandages": "Bandage", "Berries": "Berry", "Knives": "Knife", "Gloves": "Glove",
            "Salves": "Salve", "Cookies": "Cookie", "Pants": "Pants", "Fish": "Fish",
            "Scissors": "Scissors", "Brass Compass": "Brass Compass", "Glass Lens": "Glass Lens",
            "Oiled Canvas": "Oiled Canvas", "Bag of Herbs": "Bag of Herbs",
            "Bottles of Water": "Bottle of Water", "Glass Flasks (Empty)": "Glass Flask (Empty)",
            "Foraging Trowel and Pruners": "Foraging Trowel and Pruners",
        }.items():
            with self.subTest(name=name):
                self.assertEqual(singularize_item_name(name), expected)

    def test_generated_starter_names_normalize_without_explicit_quantity_requests(self):
        for amount in (1, 10):
            items = _starter_items_for_save([{"name": "Glass Alchemy Flasks", "basic_name": "Flasks", "quantity": amount}], {})
            self.assertEqual(items[0]["name"], "Glass Alchemy Flask")
            self.assertEqual(items[0]["basic_name"], "Flask")
            self.assertEqual(items[0]["quantity"], amount)

    def test_added_plural_stack_keeps_singular_name_when_one_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Singular stacks")
            for message, name, amount in (("plural", "Glass Alchemy Flasks", 10),):
                results = EventApplier(repo, message_id=message).apply_events([{
                    "type": "InventoryItemAddedEvent", "payload": {"item_name": name, "item_type": "Container",
                        "amount": amount, "description": "A small glass flask.", "weight_lb": 0.3,
                        "carrying_capacity_lb": 0, "storage_location": "actively_carried"},
                }])
                self.assertEqual(results[0].status, "applied")
            stack = next(item for item in repo.list_inventory_items() if item["name"] == "Glass Alchemy Flask")
            original_id = stack["id"]
            self.assertEqual(stack["quantity"], 10)
            repo.remove_inventory_item("Glass Alchemy Flask", 9)
            saved = next(item for item in SaveRepository(repo.db_path).list_inventory_items() if item["id"] == original_id)
            self.assertEqual((saved["name"], saved["quantity"]), ("Glass Alchemy Flask", 1))
