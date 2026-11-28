import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.stats import StatsScreen
from ai_adventure.ui.screens.travel import TravelScreen
from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator
from ai_adventure.visual_assets import build_visual_asset_requests, save_relative_image_filename
from ai_adventure.ui.screens.alchemy import AlchemyNotebookScreen
from ai_adventure.ui.crafting_details import CraftingDetailsDialog
from ai_adventure.ui.skill_details import SkillDetailsDialog
from tests.qt_test_case import QtTestCase


class BundledImageUiTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_travel_and_initial_preview_use_local_matches_without_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / "images"
            art = root / "bundle"
            cottage = art / "locations/game_genres/fantasy/art_styles/digital_art/cottage.png"
            cottage.parent.mkdir(parents=True)
            Image.new("RGB", (40, 40)).save(cottage)
            source = SaveRepository.create_new_save(root / "saves", "Source")
            target = SaveRepository.create_new_save(root / "saves", "Target")
            for repo in (source, target):
                repo.set_setting("world.genre", "Fantasy")
                repo.set_setting("images.enabled", False)
                repo.add_inventory_item("Leather Backpack", "Container", 1, "A plain backpack with leather straps.")

            request = next(request for request in build_visual_asset_requests(source) if request.display_name == "Leather Backpack")
            filename = save_relative_image_filename(source, request)
            image = images / filename
            image.parent.mkdir(parents=True)
            Image.new("RGB", (1024, 1024)).save(image)
            source.ensure_visual_asset(asset_id=request.asset_id, subject_type="inventory", subject_key=request.subject_key,
                                       display_name=request.display_name, descriptor_hash=request.descriptor_hash,
                                       filename=filename, prompt="", model="test", visual_description=request.description,
                                       image_style=request.image_style, resolution_tier=request.image_size)
            source.set_visual_asset_status(request.asset_id, "ready")
            target.set_travel_locations([{"name": "Hearthstone", "description": "A thatched cottage."}])
            coordinator = _VisualAssetCoordinator(images_dir=images, api_key_path=root / "missing-key", enabled=True)
            with patch("ai_adventure.bundled_images.bundled_images_root", return_value=art):
                screen = TravelScreen()
                screen.set_repository(target)
                screen.location_selector.setCurrentIndex(screen.location_selector.findText("Hearthstone"))
                self.assertEqual(Path(screen.location_image_label.property("imageViewerPath")), cottage)
                requests = coordinator.prepare_initial_batch(target)
                coordinator.reuse_initial_images(target, requests)
                request = next(request for request in requests if request.display_name == "Leather Backpack")
                self.assertEqual(target.get_visual_asset_by_id(request.asset_id)["status"], "ready")
                self.assertEqual((images / save_relative_image_filename(target, request)).read_bytes(), image.read_bytes())
                self.assertEqual(target.visual_asset_generation_count(), 0)

    def test_defaults_display_with_generation_disabled_and_cached_images_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("items/Flask.png", "skills/Alchemy.png"):
                path = root / "art" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGBA", (32, 32), (250, 0, 0, 100)).save(path)
            with patch("ai_adventure.bundled_images.bundled_images_root", return_value=root / "art"):
                repo = SaveRepository.create_new_save(root / "saves", "Defaults")
                repo.set_setting("images.enabled", False)
                repo.upsert_skill("Alchemy", "Brew potions.", 1, default_image="skills/Alchemy.png")
                repo.add_crafting_item(name="Flask", description="A flask.", default_image="items/Flask.png")
                stats = StatsScreen()
                stats.set_repository(repo)
                key = repo.get_skill("Alchemy")["id"]
                asset = repo.get_visual_asset("skill", key)
                self.assertEqual(stats.visual_asset_path(asset), root / "art/skills/Alchemy.png")
                self.assertFalse(stats.skills_table.item(0, 0).icon().isNull())

                def inspect_skill(dialog):
                    self.assertFalse(dialog.icon_preview.pixmap().isNull())
                    return 0

                with patch.object(SkillDetailsDialog, "exec", inspect_skill):
                    stats._open_skill_details(0, 0)
                crafting = AlchemyNotebookScreen()
                crafting.set_repository(repo)
                with patch.object(CraftingDetailsDialog, "exec", lambda dialog: self.assertFalse(dialog.image_preview.pixmap().isNull())):
                    crafting._open_reagent_details(0, 0)

                cache = root / "cache"
                cache.mkdir()
                stats.set_visual_assets_dir(cache)
                repo.ensure_visual_asset(asset_id="uploaded", subject_type="skill", subject_key=key,
                    display_name="Alchemy", descriptor_hash="uploaded", filename="preferred.png", prompt="", model="upload", ready=True)
                # A missing cache file still falls back to bundled artwork.
                self.assertEqual(stats.visual_asset_path(repo.get_visual_asset("skill", key)), root / "art/skills/Alchemy.png")
                Image.new("RGB", (64, 64), (0, 255, 0)).save(cache / "preferred.png")
                self.assertEqual(stats.visual_asset_path(repo.get_visual_asset("skill", key)), cache / "preferred.png")
