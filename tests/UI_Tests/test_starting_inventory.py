import tempfile
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QDialog

from ai_adventure.application.starting_inventory import StartingInventoryReview
from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.starting_inventory import StartingInventoryReviewDialog
from tests.qt_test_case import QtTestCase


class StartingInventoryTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = SaveRepository.create_new_save(Path(directory.name), "Inventory review")
        self.repo.replace_inventory_items([], allow_empty=True)
        self.sites = [{"name": "Kit's Shop", "location_scope": "specific"}]

    def test_only_carried_container_tree_counts_and_preview_rolls_back(self):
        self.repo.add_inventory_item("Existing", "Item", 1, "Old possession")
        before = self.repo.list_inventory_items()
        history = self.repo.list_history()
        catalog = self.repo.list_item_catalog()
        proposal = StartingInventoryReview([
            {"name": "Cabinet", "category": "Container", "weight_lb": 80,
             "storage_location": "Kit's Shop"},
            {"name": "Cauldron", "weight_lb": 30, "storage_location": "Cabinet"},
            {"name": "Satchel", "category": "Container", "weight_lb": 2,
             "carrying_capacity_lb": 10, "storage_location": "actively_carried"},
            {"name": "Tools", "quantity": 2, "weight_lb": 3, "storage_location": "Satchel"},
        ], self.sites, 10)
        preview = proposal.preview(self.repo)
        self.assertEqual(preview["load"]["weight_lb"], 8)
        self.assertEqual(preview["load"]["capacity_lb"], 20)
        self.assertFalse(preview["error"])
        self.assertEqual(self.repo.list_inventory_items(), before)
        self.assertEqual(self.repo.list_history(), history)
        self.assertEqual(self.repo.list_item_catalog(), catalog)

    def test_review_updates_quantities_and_storage_before_enabling_continue(self):
        proposal = StartingInventoryReview([
            {"name": "Cauldron", "quantity": 1, "weight_lb": 30, "storage_location": "actively_carried"},
            {"name": "Flask", "quantity": 10, "weight_lb": 2, "storage_location": "actively_carried"},
        ], self.sites, 20)
        dialog = StartingInventoryReviewDialog(self.repo, proposal)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.continue_button.isEnabled())
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog.storage_inputs[0].setCurrentText("Kit's Shop")
        self.assertTrue(dialog.continue_button.isEnabled())
        self.assertEqual(dialog.preview["load"]["weight_lb"], 20)
        dialog.quantity_inputs[1].setValue(4)
        self.assertEqual(dialog.preview["load"]["weight_lb"], 8)
        dialog.quantity_inputs[0].setValue(0)
        self.assertEqual([item["name"] for item in dialog.selected_items()], ["Flask"])
        self.assertEqual(self.repo.list_inventory_items(), [])

    def test_removing_carried_bag_recalculates_bonus_and_unloads_children_explicitly(self):
        proposal = StartingInventoryReview([
            {"name": "Bag", "category": "Container", "quantity": 1, "weight_lb": 1,
             "carrying_capacity_lb": 10, "storage_location": "actively_carried"},
            {"name": "Tools", "quantity": 1, "weight_lb": 8, "storage_location": "Bag"},
        ], self.sites, 5)
        dialog = StartingInventoryReviewDialog(self.repo, proposal)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.continue_button.isEnabled())
        dialog.quantity_inputs[0].setValue(0)
        self.assertEqual(dialog.storage_inputs[1].currentData(), "actively_carried")
        self.assertFalse(dialog.continue_button.isEnabled())
        dialog.storage_inputs[1].setCurrentText("Kit's Shop")
        self.assertTrue(dialog.continue_button.isEnabled())

    def test_basic_explicit_storage_uses_finalized_shop_name(self):
        setup = {"character": {"carrying_capacity_lb": 5},
                 "starter_items": [{"item_request": "A cauldron", "requires_ai_invention": True,
                                    "storage_location": "My Shop", "moveable": False}],
                 "starting_locations": [{"name": "My Shop", "location_mode": "suggestion"}]}
        proposal = StartingInventoryReview.from_generated_state({
            "locations": [{"name": "Kit's Shop", "source_index": 0, "location_scope": "specific"}],
            "starting_items": [{"name": "Cauldron", "quantity": 1, "weight_lb": 30,
                                "source_index": 0, "storage_location": "actively_carried"}],
        }, {"setup": setup})
        self.assertEqual(proposal.items[0]["storage_location"], "Kit's Shop")
        self.assertFalse(proposal.items[0]["moveable"])
        self.assertFalse(proposal.preview(self.repo)["error"])

    def test_player_approved_empty_inventory_does_not_restore_requested_items(self):
        setup = normalize_new_game_setup({"title": "Approved removals", "starter_inventory_mode": "advanced",
            "starter_items": [{"name": "Heavy Anvil", "category": "Tool", "weight_lb": 200}]})
        result = SimpleNamespace(world_summary="A workshop.", introductory_message="You begin empty handed.",
            approved_starter_items=[], finalized_starter_items=[{"name": "Heavy Anvil", "weight_lb": 200}],
            finalized_skills=[], suggested_events=[])
        NewGameService.commit_generated_world(self.repo, setup, result)
        self.assertEqual(self.repo.list_inventory_items(), [])
        self.assertEqual(len(self.repo.list_history(kinds=("story",))), 1)

    def test_overweight_authored_inventory_can_wait_for_review_during_generation(self):
        setup = normalize_new_game_setup({"title": "Authored overload", "starter_inventory_mode": "advanced",
            "starter_items": [{"name": "Anvil", "category": "Tool", "description": "A heavy anvil.",
                               "weight_lb": 200, "requires_ai_invention": False}]})
        repo = NewGameService.create_repository(self.repo.db_path.parent.parent, setup,
                                               defer_starting_inventory=True)
        self.assertEqual(repo.list_inventory_items(), [])
        self.assertEqual(repo.get_setting("new_game.setup")["starter_items"][0]["name"], "Anvil")

    def test_cargo_limit_is_checked_and_top_level_storage_overrides_metadata(self):
        proposal = StartingInventoryReview([
            {"name": "Cabinet", "category": "Container", "quantity": 1, "weight_lb": 80,
             "storage_location": "Kit's Shop", "metadata": {"storage_location": "actively_carried",
                "carrying_capacity_lb": 2}},
            {"name": "Tools", "quantity": 1, "weight_lb": 8, "storage_location": "Cabinet"},
        ], self.sites, 5)
        preview = proposal.preview(self.repo)
        self.assertEqual(preview["load"]["weight_lb"], 0)
        self.assertIn("cargo capacity exceeded", preview["error"])
