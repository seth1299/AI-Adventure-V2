import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from PySide6.QtWidgets import QApplication

from ai_adventure.application.asset_generation_service import AssetGenerationService
from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.new_game_setup import normalize_new_game_setup, build_new_game_setup_packet
from ai_adventure.new_game_templates import save_new_game_template, load_new_game_templates
from ai_adventure.template_images import template_image_bytes
from ai_adventure.ui.main_window import MainWindow
from ai_adventure.ui.wizards.new_game import NewGameWizard
from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator
from tests.qt_test_case import QtTestCase


class TemplateImageTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_final_upload_is_portable_and_restores_with_new_runtime_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "templates.json"
            setup = normalize_new_game_setup({"title": "Workshop", "skill_preset": "custom", "skill_level_plan": [2],
                "starter_items": [{"name": "Glass Flask", "description": "A glass flask.", "category": "Tool", "quantity": 1}],
                "skills": [{"name": "Alchemy", "description": "Brew potions.", "level": 2, "attribute": "Intelligence"}]})
            self.assertTrue(save_new_game_template(path, setup, template_name="Workshop"))
            repository = NewGameService.create_repository(root / "saves", setup)
            repository.set_setting("new_game.template_name", "Workshop")
            coordinator = _VisualAssetCoordinator(images_dir=root / "cache", api_key_path=root / "key.json", enabled=True)
            requests = coordinator.prepare_initial_batch(repository)
            request = next(r for r in requests if r.subject_type == "skill")
            item_request = next(r for r in requests if r.subject_type == "inventory")
            source = root / "original.png"
            Image.new("RGBA", (32, 32), (220, 10, 20, 100)).save(source)
            original = source.read_bytes()
            window = SimpleNamespace(game_shell=SimpleNamespace(visual_asset_coordinator=coordinator),
                                     app_paths=SimpleNamespace(new_game_templates_path=path))
            success, message = MainWindow._upload_new_game_image(window, repository, request, source)
            self.assertTrue(success, message)
            self.assertTrue(MainWindow._upload_new_game_image(window, repository, item_request, source)[0])
            self.assertEqual(source.read_bytes(), original)
            loaded = load_new_game_templates(path)[0].setup
            attachments = loaded["template_images"]
            self.assertEqual(len(attachments), 2)
            self.assertEqual(attachments[0]["display_name"], "Alchemy")
            self.assertNotIn(str(source), json.dumps(attachments))
            self.assertNotIn(attachments[0]["png_base64"], json.dumps(build_new_game_setup_packet(loaded)))
            # Neither the original file nor the previous save's cache is required.
            source.unlink()
            AssetGenerationService.target_path(repository, request, coordinator.images_dir).unlink()
            AssetGenerationService.target_path(repository, item_request, coordinator.images_dir).unlink()
            loaded["title"] = "Workshop reused"
            second = NewGameService.create_repository(root / "saves", loaded)
            second.upsert_skill("Rocket Science", "Design rockets.", 1)
            requests = coordinator.prepare_initial_batch(second)
            new_request = next(r for r in requests if r.display_name == "Alchemy")
            self.assertNotEqual(new_request.subject_key, request.subject_key)
            remaining = coordinator.restore_initial_template_images(second, requests, loaded["template_images"])
            self.assertNotIn(new_request, remaining)
            self.assertNotIn("Glass Flask", [r.display_name for r in remaining])
            self.assertIn("Rocket Science", [r.display_name for r in remaining])
            saved = AssetGenerationService.target_path(second, new_request, coordinator.images_dir)
            with Image.open(saved) as image:
                self.assertEqual(image.size, (32, 32))
                self.assertEqual(image.getpixel((0, 0)), (220, 10, 20, 100))
            self.assertIsNotNone(second.get_visual_asset("skill", new_request.subject_key))

            wizard = NewGameWizard(tts_enabled=False, api_key_path=root / "key.json",
                terms_acceptance_path=root / "terms.json", custom_voice_storage_path=root / "voices.json",
                template_setup=loaded)
            self.assertEqual(wizard.build_template_setup()["template_images"], attachments)
            self.assertEqual(wizard.build_setup()["template_images"], attachments)

    def test_invalid_or_wrong_subject_upload_falls_back_to_source_selection(self):
        request = SimpleNamespace(subject_type="skill", display_name="Alchemy")
        self.assertIsNone(template_image_bytes([{"subject_type": "skill", "display_name": "Alchemy", "png_base64": "invalid!"}], request))
        self.assertIsNone(template_image_bytes([{"subject_type": "inventory", "display_name": "Alchemy", "png_base64": "invalid!"}], request))
