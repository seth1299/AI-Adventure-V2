from pathlib import Path
import tempfile

from PySide6.QtWidgets import QApplication

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.inventory import InventoryScreen
from tests.qt_test_case import QtTestCase


class InventoryCapacitySummaryTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_only_positive_capacity_carried_or_local_containers_are_summarized(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Cargo summary")
            repo.set_state_value("location", "Forest Trail")
            for name, capacity, location in (
                ("Carried Pack", 30, "actively_carried"),
                ("Wooden Steeping Cabinet", 100, "Apothecary's Nook"),
                ("Empty Flask", 0, "actively_carried"),
                ("Local Empty Jar", 0, "Forest Trail"),
                ("Local Chest", 20, "Forest Trail"),
            ):
                repo.add_inventory_item(name, "Container", 1, "Storage vessel.", metadata={
                    "weight_lb": 0, "carrying_capacity_lb": capacity, "storage_location": location,
                })
            screen = InventoryScreen()
            screen.set_repository(repo)
            text = screen.carrying_label.text()
            self.assertIn("Carried Pack:", text)
            self.assertIn("Local Chest:", text)
            self.assertNotIn("Wooden Steeping Cabinet:", text)
            self.assertNotIn("Empty Flask:", text)
            self.assertNotIn("Local Empty Jar:", text)
            repo.set_state_value("location", "Apothecary's Nook")
            screen.refresh()
            self.assertIn("Wooden Steeping Cabinet:", screen.carrying_label.text())
            self.assertIn("Carried Pack:", screen.carrying_label.text())
            self.assertNotIn("Local Chest:", screen.carrying_label.text())
            self.assertEqual(len(repo.list_inventory_items()), len(screen._inventory_items))
