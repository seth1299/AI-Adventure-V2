import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QPushButton

from tests.qt_test_case import QtTestCase
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.crafting_details import CraftingDetailsDialog
from ai_adventure.ui.screens.alchemy import AlchemyNotebookScreen
from ai_adventure.visual_assets import build_visual_asset_requests
from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator
from ai_adventure.application.asset_generation_service import AssetGenerationService
from PIL import Image


class CraftingDetailsTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_item_image_controls_use_shared_inventory_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Crafting images")
            repository.add_crafting_item(name="Glass Phial", description="A glass bottle.")
            screen = AlchemyNotebookScreen()
            screen.set_repository(repository)
            key = repository.list_crafting_items()[0]["item_uuid"]
            request = next(r for r in build_visual_asset_requests(repository) if r.subject_key == key)
            self.assertEqual(request.subject_type, "inventory")

            def inspect(dialog):
                dialog.findChild(QPushButton, "craftingSelectImageButton").click()
                dialog.findChild(QPushButton, "craftingGenerateImageButton").click()
                return 0

            with patch.object(CraftingDetailsDialog, "exec", inspect), \
                 patch.object(screen, "choose_visual_asset", return_value=False) as upload, \
                 patch.object(screen, "create_visual_asset") as generate:
                screen._open_reagent_details(0, 0)

            upload.assert_called_once_with("inventory", key)
            generate.assert_called_once_with("inventory", key)
            repository.set_setting("images.enabled", False)
            with patch.object(CraftingDetailsDialog, "exec", lambda dialog: (
                self.assertIsNotNone(dialog.findChild(QPushButton, "craftingSelectImageButton")),
                self.assertIsNone(dialog.findChild(QPushButton, "craftingGenerateImageButton")),
            )):
                screen._open_reagent_details(0, 0)

    def test_upload_saves_separate_copy_without_enlarging_32_pixel_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SaveRepository.create_new_save(root, "Upload")
            repository.add_crafting_item(name="Phial", description="A glass bottle.")
            coordinator = _VisualAssetCoordinator(
                images_dir=root / "images", api_key_path=root / "key.json", enabled=True,
            )
            request = next(r for r in coordinator.prepare_initial_batch(repository) if r.display_name == "Phial")
            source = root / "original.png"
            Image.new("RGBA", (32, 32), (255, 0, 0, 100)).save(source)
            original_bytes = source.read_bytes()
            success, error = coordinator.upload_initial_image(repository, request, source)
            self.assertTrue(success, error)
            self.assertEqual(source.read_bytes(), original_bytes)
            cached = AssetGenerationService.target_path(repository, request, coordinator.images_dir)
            self.assertNotEqual(cached.resolve(), source.resolve())
            with Image.open(cached) as saved:
                self.assertEqual(saved.size, (32, 32))
                self.assertEqual(saved.getpixel((0, 0)), (255, 0, 0, 100))
            self.assertEqual(repository.get_visual_asset("inventory", request.subject_key)["width"], 32)

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
