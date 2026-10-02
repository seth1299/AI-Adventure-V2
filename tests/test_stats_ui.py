from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QPushButton

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.stats import RANK_STATS
from ai_adventure.ui.screens.stats import StatsScreen
from ai_adventure.ui.screens.character import CharacterScreen
from ai_adventure.ui.screens.inventory import InventoryScreen
from ai_adventure.ui.wizards.new_game import NewGameWizard


class StatsUiTests(unittest.TestCase):
    @staticmethod
    def capture(widget, name):
        directory = os.environ.get("AI_ADVENTURE_UI_CAPTURE_DIR")
        if directory:
            QTest.qWait(150)
            QApplication.processEvents()
            Path(directory).mkdir(parents=True, exist_ok=True)
            widget.grab().save(str(Path(directory) / (name + ".png")))

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_rank_point_buy_previews_and_incomplete_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            wizard = NewGameWizard(tts_enabled=False, api_key_path=Path(directory) / "key.json",
                                   terms_acceptance_path=Path(directory) / "terms.json",
                                   custom_voice_storage_path=Path(directory) / "voices.json")
            for rank, (budget, level) in RANK_STATS.items():
                wizard.skill_preset_combo.setCurrentIndex(wizard.skill_preset_combo.findData(rank))
                self.assertEqual(wizard._point_buy_remaining(), 0)
                self.assertTrue(wizard.stats_page.isComplete())
                setup = wizard.build_setup()
                self.assertEqual((setup["point_buy_budget"], setup["starting_player_level"]), (budget, level))
            wizard._reset_point_buy(blank=True)
            self.assertFalse(wizard.stats_page.isComplete())
            self.assertIn("Remaining 45", wizard.point_buy_summary.text())
            wizard._reset_point_buy()
            wizard.show()
            wizard.setCurrentId(next(page_id for page_id in wizard.pageIds() if wizard.page(page_id) is wizard.stats_page))
            self.app.processEvents()
            self.capture(wizard, "point_buy")
            wizard.close()

    def test_spend_rewards_via_visible_dialog_persists_mixed_advances(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Rewards")
            repository.upsert_skill("Climbing", "Climb dangerous terrain.", 1)
            repository.add_skill_xp("Climbing", 3)
            for number in range(2):
                repository.record_player_achievement(str(number), "major", "Completed a distinct objective.")
            screen = StatsScreen()
            screen.set_repository(repository)
            screen.resize(1000, 650)
            screen.show()
            errors = []

            def spend():
                try:
                    dialog = next(child for child in screen.findChildren(QDialog) if child.isVisible())
                    combos = dialog.findChildren(QComboBox)
                    combos[0].setCurrentIndex(combos[0].findData("skills"))
                    buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
                    QTest.mouseClick(buttons["Spend one reward choice"], Qt.MouseButton.LeftButton)
                    combos[2].setCurrentIndex(combos[2].findData("Climbing"))
                    QTest.mouseClick(buttons["Spend one skill advance"], Qt.MouseButton.LeftButton)
                    self.capture(dialog, "spend_rewards")
                    QTest.mouseClick(buttons["Close"], Qt.MouseButton.LeftButton)
                except BaseException as exc:
                    errors.append(exc)
                    for dialog in screen.findChildren(QDialog):
                        dialog.reject()

            QTimer.singleShot(50, spend)
            QTest.mouseClick(screen.spend_rewards_button, Qt.MouseButton.LeftButton)
            if errors:
                raise errors[0]
            reopened = SaveRepository(repository.db_path)
            self.assertEqual(reopened.player_stats()["skill_advances"], 1)
            self.assertEqual((reopened.get_skill("Climbing")["level"], reopened.get_skill("Climbing")["xp"]), (2, 11))
            self.assertIn("Level 2", screen.summary_label.text())
            self.capture(screen, "stats")
            screen.close()

    def test_equipment_pickup_and_item_modal_move(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Equipment")
            repository.set_state_value("location", "Store")
            repository.replace_inventory_items([
                {"name": "Backpack", "quantity": 1, "category": "Container", "description": "A wearable pack", "storage_location": "Store", "weight_lb": 2, "carrying_capacity_lb": 20,
                 "container": {"is_open": True, "contents_initialized": True, "contents": {"items": [], "currency_base_units": 0}}},
                {"name": "Lantern", "quantity": 1, "category": "Tool", "description": "A portable light", "storage_location": "Store", "weight_lb": 1},
            ])
            character = CharacterScreen(tts_enabled=False)
            character.set_repository(repository)
            character.resize(1000, 750)
            character.show()
            combo = character.equipment_combos["Back"]
            combo.setCurrentIndex(combo.findData("Backpack"))
            self.app.processEvents()
            self.assertEqual(repository.inventory_load()["capacity_lb"], 70)
            self.capture(character, "equipment")
            inventory = InventoryScreen()
            inventory.set_repository(repository)
            inventory.resize(1000, 650)
            inventory.show()
            lantern = next(item for item in repository.list_inventory_items() if item["name"] == "Lantern")
            pack = next(item for item in repository.list_inventory_items() if item["name"] == "Backpack")
            inventory._open_item_details(inventory._inventory_items["lantern"])
            details = inventory._item_detail_dialogs[str(lantern["id"])]
            errors = []

            def move():
                try:
                    dialog = next(child for child in inventory.findChildren(QDialog) if child.windowTitle() == "Move Item")
                    destination = dialog.destination_combo
                    destination.setCurrentIndex(destination.findData(str(pack["id"])))
                    self.capture(dialog, "inventory_move")
                    dialog.accept()
                except BaseException as exc:
                    errors.append(exc)
                    for dialog in inventory.findChildren(QDialog):
                        dialog.reject()

            QTimer.singleShot(50, move)
            QTest.mouseClick(details.move_button, Qt.MouseButton.LeftButton)
            if errors:
                raise errors[0]
            moved = next(item for item in repository.list_inventory_items() if item["name"] == "Lantern")
            self.assertEqual(moved["metadata"]["container_id"], str(pack["id"]))
            self.assertEqual(repository.inventory_load()["weight_lb"], 3)
            self.capture(inventory, "inventory")
            inventory.close()
            character.close()
