import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.stats import StatsScreen
from ai_adventure.ui.screens.alchemy import AlchemyNotebookScreen
from ai_adventure.ui.crafting_details import CraftingDetailsDialog
from ai_adventure.ui.skill_details import SkillDetailsDialog
from tests.qt_test_case import QtTestCase


class BundledImageUiTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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
