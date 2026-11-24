import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QPushButton

from tests.qt_test_case import QtTestCase
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.calendar_system import build_calendar_snapshot, format_time_of_day
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
                screen.recipe_table.cellClicked.emit(0, 2)
                self.assertEqual(len(seen), 1)
                screen.recipe_table.cellWidget(0, 1).click()
            self.assertEqual(seen[0][0], "Apple")
            self.assertIn("A fresh fruit", seen[0][1])
            self.assertEqual(seen[1][0], "Fruit juice")
            self.assertIn("Press gently", seen[1][1])
            self.assertEqual(screen.recipe_table.currentRow(), 0)

    def test_queue_survives_sort_and_reload_and_checks_shared_ingredients(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Queue")
            repository.add_inventory_item("Flax", "Material", 2, "Fiber", metadata={"quantity_unit": "stalks"})
            for name in ("Yarn A", "Yarn Z"):
                repository.add_crafting_recipe(
                    name=name, ingredients=[{"reagent_name": "Flax", "quantity": 2}],
                    result=name, stages=[{"kind": "active", "work_amount": 1}],
                )
            screen = AlchemyNotebookScreen()
            screen.set_repository(repository)
            screen.recipe_table.cellWidget(0, 0).setChecked(True)
            screen.recipe_table.cellWidget(1, 0).setChecked(True)
            queued = list(screen._crafting_queue)
            screen._sort_recipes_by_column(2)
            self.assertEqual(screen._crafting_queue, queued)
            self.assertTrue(screen.recipe_table.cellWidget(0, 0).isChecked())
            self.assertEqual(screen._recipe_rows[0]["name"], "Yarn Z")
            with patch.object(CraftingDetailsDialog, "exec", lambda dialog: self.assertEqual(dialog.windowTitle(), "Yarn Z")):
                screen.recipe_table.cellWidget(0, 1).click()
            screen.recipe_craft_button.click()
            self.assertEqual(screen._crafting_queue, [queued[1]])
            self.assertIn("Yarn A", {item["name"] for item in repository.list_inventory_items()})
            self.assertNotIn("Yarn Z", {item["name"] for item in repository.list_inventory_items()})
            self.assertIn("Yarn Z:", screen.recipe_craftability_label.text())
            screen.set_repository(repository)
            self.assertEqual(screen._crafting_queue, [queued[1]])
            screen.recipe_table.cellWidget(0, 0).setChecked(False)
            self.assertFalse(screen.recipe_craft_button.isEnabled())

    def test_queue_advances_multiple_processes_without_consuming_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Processes")
            repository.add_inventory_item("Flax", "Material", 4, "Fiber", metadata={"quantity_unit": "stalks"})
            for name in ("Yarn A", "Yarn B"):
                repository.add_crafting_recipe(
                    name=name, ingredients=[{"reagent_name": "Flax", "quantity": 2}],
                    result=name, stages=[{"kind": "active", "work_amount": 2}],
                )
            screen = AlchemyNotebookScreen()
            screen.set_repository(repository)
            for row in range(2):
                screen.recipe_table.cellWidget(row, 0).setChecked(True)
            screen.recipe_craft_button.click()
            self.assertEqual(len(repository.get_setting("crafting.processes", [])), 2)
            self.assertEqual(len(screen._crafting_queue), 2)
            screen.refresh()
            screen.recipe_craft_button.click()
            self.assertEqual(repository.get_setting("crafting.processes", []), [])
            self.assertEqual(screen._crafting_queue, [])
            names = {item["name"] for item in repository.list_inventory_items()}
            self.assertTrue({"Yarn A", "Yarn B"}.issubset(names))
            self.assertNotIn("Flax", names)

    def test_continuation_status_and_feedback_show_saved_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Visible progress")
            repository.add_inventory_item("Flax", "Material", 1, "Fiber")
            repository.add_crafting_recipe(
                name="Yarn", ingredients=[{"reagent_name": "Flax"}], result="Yarn",
                stages=[{"kind": "active", "work_amount": 4, "estimated_minutes": 20}],
            )
            screen = AlchemyNotebookScreen()
            screen.set_repository(repository)
            self.assertEqual(screen.recipe_table.item(0, 7).text(), "Start new")
            screen.recipe_table.cellWidget(0, 0).setChecked(True)
            self.assertIn("Start Queue", screen.recipe_craft_button.text())
            screen.recipe_craft_button.click()
            self.assertIn("Continue", screen.recipe_craft_button.text())
            self.assertIn("25%", screen.recipe_table.item(0, 7).text())
            self.assertIn("Continue existing batch", screen.recipe_craftability_label.text())
            self.assertIn("15 min active", screen.recipe_craftability_label.text())
            self.assertNotIn("Missing: Flax", screen.recipe_craftability_label.text())
            screen.recipe_craft_button.click()
            self.assertIn("25% → 50%", screen.recipe_craftability_label.text())
            self.assertIn("Remaining:", screen.recipe_table.item(0, 6).text())
            screen.set_repository(repository)
            self.assertEqual(screen.recipe_table.currentRow(), 0)
            self.assertIn("50%", screen.recipe_craftability_label.text())
            self.assertIn("10 min active", screen.recipe_craftability_label.text())
            finish_minute = repository.get_current_calendar_minute() + 10
            finish = build_calendar_snapshot(finish_minute, repository.get_calendar_settings())
            exact_time = format_time_of_day(finish["time_of_day_minutes"], "24_hour")
            self.assertIn(f"Estimated finish if worked next: {finish['display_label']} ({exact_time})",
                          screen.recipe_craftability_label.text())
            screen.recipe_finish_button.click()
            self.assertEqual(repository.get_current_calendar_minute(), finish_minute)
            self.assertIn("Elapsed: 10 min", screen.recipe_craftability_label.text())
            self.assertIn("Crafted", screen.recipe_craftability_label.text())
            self.assertFalse(screen.recipe_finish_button.isEnabled())
