import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from tests.qt_test_case import QtTestCase
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.crafting_details import CraftingDetailsDialog
from ai_adventure.ui.screens.alchemy import AlchemyNotebookScreen


class CraftingDetailsTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_rows_open_details_after_sorting_and_keep_craft_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Crafting details")
            repository.add_crafting_item(name="Zinc", description="A useful metal")
            repository.add_crafting_item(name="Apple", description="A fresh fruit")
            repository.add_crafting_recipe(name="Fruit juice", ingredients=[], result="Juice", notes="Press gently")
            screen = AlchemyNotebookScreen()
            screen.set_repository(repository)
            seen = []

            def capture(dialog):
                seen.append((dialog.windowTitle(), [label.text() for label in dialog.findChildren(QLabel)]))
                self.assertTrue(dialog.isSizeGripEnabled())
                self.assertTrue(dialog.findChild(QScrollArea).widgetResizable())
                return 0

            with patch.object(CraftingDetailsDialog, "exec", capture):
                screen.reagent_table.cellClicked.emit(0, 2)
                screen.recipe_table.selectRow(0)
                screen.recipe_table.cellClicked.emit(0, 1)
            self.assertEqual(seen[0][0], "Apple")
            self.assertIn("A fresh fruit", seen[0][1])
            self.assertEqual(seen[1][0], "Fruit juice")
            self.assertIn("Press gently", seen[1][1])
            self.assertEqual(screen.recipe_table.currentRow(), 0)
