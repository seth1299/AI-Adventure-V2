import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from io import BytesIO

from PIL import Image

from PySide6.QtWidgets import QApplication, QGraphicsTextItem, QGraphicsPixmapItem

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.region_map import regional_map_layout, regional_map_request
from ai_adventure.ui.region_map import RegionalMapDialog
from ai_adventure.ui.screens.travel import TravelScreen
from tests.qt_test_case import QtTestCase


class RegionalMapTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_map_layout_uses_shared_scale_and_north_up(self):
        points, unknown, span = regional_map_layout([
            {"name": "West", "x_miles": -3, "y_miles": 0},
            {"name": "North", "x_miles": 0, "y_miles": 4},
            {"name": "Unknown"},
        ])
        self.assertLess(points[0][1], points[1][1])
        self.assertGreater(points[0][2], points[1][2])
        self.assertEqual(span, 4)
        self.assertEqual([p.name for p in unknown], ["Unknown"])

    def test_map_renders_text_without_provider_and_keeps_copy_with_generation_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Map")
            repo.set_setting("images.enabled", False)
            repo.set_state_value("location", "Fern Glen")
            repo.set_travel_locations([
                {"name": "Stonevault", "x_miles": 0, "y_miles": 0},
                {"name": "Deercross Wilds", "x_miles": 2.5, "y_miles": 1.5},
                {"name": "Fern Glen", "is_sublocation": True,
                 "parent_location": "Deercross Wilds"},
            ])
            screen = TravelScreen()
            screen.set_repository(repo)
            screen.on_visual_asset_generate = Mock()
            dialog = RegionalMapDialog(screen)
            try:
                dialog.show()
                self.app.processEvents()
                self.assertTrue(dialog.generate_button.isHidden())
                self.assertIn("Fern Glen — CURRENTLY HERE", dialog.legend.toPlainText())
                self.assertIn("POSITIONS UNKNOWN", dialog.legend.toPlainText())
                self.assertTrue(any(isinstance(item, QGraphicsTextItem) for item in dialog.scene.items()))
                dialog._copy_prompt()
                self.assertIn('Stonevault', QApplication.clipboard().text())
                self.assertIn('never render them as text', QApplication.clipboard().text())
                screen.on_visual_asset_generate.assert_not_called()
                request = regional_map_request(repo)
                repo.set_state_value("location", "Stonevault")
                self.assertEqual(regional_map_request(repo).asset_id, request.asset_id)
                repo.upsert_travel_location({"name": "New Farm", "x_miles": 5, "y_miles": 1})
                self.assertNotEqual(regional_map_request(repo).asset_id, request.asset_id)
            finally:
                dialog.close()
                dialog.deleteLater()
                screen.deleteLater()

    def test_map_uses_existing_generation_and_upload_pipeline_only_on_request(self):
        from ai_adventure.ui.game_shell import GameShell
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Map pipeline")
            owner = SimpleNamespace(repository=repo)
            request = GameShell._visual_asset_request(owner, "region_map", "region")
            self.assertEqual(request.subject_type, "region_map")
            # Maps do not join ordinary entity scans or initial paid batches.
            from ai_adventure.application.asset_generation_service import AssetGenerationService
            self.assertFalse(any(r.subject_type == "region_map" for r in AssetGenerationService.requests_for(repo)))
            record = repo.ensure_visual_asset(
                asset_id=request.asset_id, subject_type=request.subject_type,
                subject_key=request.subject_key, display_name=request.display_name,
                descriptor_hash=request.descriptor_hash, filename="maps/map.png",
                prompt=request.prompt, model="test",
            )
            self.assertEqual(record["subject_type"], "region_map")

    def test_saved_alias_repairs_parent_without_changing_location_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Alias repair")
            repo.set_travel_locations([{"name": "Shop", "is_sublocation": True,
                                        "parent_location": "Main City"}])
            identity = repo.get_travel_locations()[0]["location_id"]
            repo.set_setting("travel.location_aliases", {"Main City": "Stonevault"})
            repaired = repo.get_travel_locations()[0]
            self.assertEqual(repaired["parent_location"], "Stonevault")
            self.assertEqual(repaired["location_id"], identity)
            self.assertEqual(repo.get_setting("travel.locations")[0]["parent_location"], "Stonevault")

    def test_existing_conflicting_coordinates_are_retained_in_an_audit_record(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Position audit")
            repo.set_setting("travel.locations", [
                {"name": "Stonevault", "x_miles": 0, "y_miles": 0},
                {"name": "Forest", "x_miles": 2.5, "y_miles": 1.5},
                {"name": "Fern Glen", "x_miles": 0, "y_miles": 0,
                 "is_sublocation": True, "parent_location": "Forest"},
            ])
            self.assertIsNone(repo.get_travel_locations()[2]["x_miles"])
            audit = repo.get_setting("travel.position_conflicts")
            self.assertEqual(audit[0]["original"]["x_miles"], 0)
            self.assertEqual(audit[0]["original"]["parent_location"], "Forest")
            repo.get_travel_locations()
            self.assertEqual(repo.get_setting("travel.position_conflicts"), audit)

    def test_generated_map_background_is_cached_and_shown_below_vector_labels(self):
        from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator
        from ai_adventure.visual_assets import save_relative_image_filename
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = SaveRepository.create_new_save(root, "Generated map")
            repo.set_state_value("location", "City")
            repo.set_travel_locations([{"name": "City", "x_miles": 0, "y_miles": 0}])
            request = regional_map_request(repo)
            repo.ensure_visual_asset(
                asset_id=request.asset_id, subject_type=request.subject_type,
                subject_key=request.subject_key, display_name=request.display_name,
                descriptor_hash=request.descriptor_hash,
                filename=save_relative_image_filename(repo, request),
                prompt=request.prompt, model="test",
            )
            coordinator = _VisualAssetCoordinator(images_dir=root / "images",
                api_key_path=root / "key", enabled=True)
            coordinator._start_next = Mock()
            with patch("ai_adventure.ui.workers.visual_assets.read_api_key", return_value="test-key"):
                success, _ = coordinator.create_initial_image(repo, request)
            self.assertTrue(success)
            self.assertEqual(len(coordinator._queue), 1)
            buffer = BytesIO()
            Image.new("RGB", (128, 128), "green").save(buffer, format="PNG")
            coordinator._handle_completed(repo, request, buffer.getvalue(), "image/png")
            self.assertEqual(repo.get_visual_asset_by_id(request.asset_id)["status"], "ready")
            screen = TravelScreen()
            screen.set_visual_assets_dir(root / "images")
            screen.set_repository(repo)
            dialog = RegionalMapDialog(screen)
            try:
                self.assertTrue(any(isinstance(item, QGraphicsPixmapItem) for item in dialog.scene.items()))
                labels = [item for item in dialog.scene.items() if isinstance(item, QGraphicsTextItem)]
                self.assertTrue(any(item.toPlainText() == "1" for item in labels))
            finally:
                dialog.deleteLater()
                screen.deleteLater()
                coordinator.deleteLater()
