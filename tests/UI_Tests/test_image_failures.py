import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.game_shell import GameShell
from ai_adventure.visual_assets import VisualAssetRequest, save_relative_image_filename
from tests.qt_test_case import QtTestCase


class ImageFailureTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = SaveRepository.create_new_save(self.root / "saves", "Image failures")
        self.shell = GameShell(lambda: None, tts_enabled=False, ai_enabled=False,
                               generated_images_dir=self.root / "images")
        self.addCleanup(self.shell.deleteLater)
        self.addCleanup(self.shell.close)
        self.shell.repository = self.repo
        refresh = patch.object(self.shell, "refresh_screens")
        refresh.start()
        self.addCleanup(refresh.stop)
        self.request = VisualAssetRequest("skill", "cooking", "Cooking", "Prepare meals.")
        self.repo.ensure_visual_asset(
            asset_id=self.request.asset_id, subject_type="skill", subject_key="cooking",
            display_name="Cooking", descriptor_hash=self.request.descriptor_hash,
            filename=save_relative_image_filename(self.repo, self.request),
            prompt=self.request.prompt, model="test",
        )

    def test_provider_failure_shows_above_modal_and_groups_further_failures(self):
        modal = QDialog(self.shell)
        self.addCleanup(modal.deleteLater)
        self.addCleanup(modal.close)
        modal.setModal(True)
        modal.show()
        self.app.processEvents()
        coordinator = self.shell.visual_asset_coordinator
        coordinator._handle_failed(self.repo, self.request, "Provider quota exceeded")
        popup = self.shell._image_failure_popup
        self.assertIsNotNone(popup)
        self.assertIs(popup.parentWidget(), modal)
        self.assertTrue(popup.isVisible())
        self.assertIn("Cooking", popup.text())
        self.assertIn("Provider quota exceeded", popup.informativeText())
        self.assertEqual(self.repo.get_visual_asset_by_id(self.request.asset_id)["status"], "failed")
        coordinator._handle_failed(self.repo, self.request, "Service unavailable")
        self.assertIs(self.shell._image_failure_popup, popup)
        self.assertIn("2 images", popup.text())
        self.assertIn("Provider quota exceeded", popup.detailedText())
        self.assertIn("Service unavailable", popup.detailedText())
        popup.accept()
        self.assertIsNone(self.shell._image_failure_popup)
        self.assertEqual(self.shell._image_failures, [])
        coordinator._handle_failed(self.repo, self.request, "Another attempt failed")
        self.assertIsNot(self.shell._image_failure_popup, popup)
        self.shell._image_failure_popup.accept()

    def test_image_processing_failure_also_notifies(self):
        data = BytesIO()
        Image.new("RGB", (64, 64), (70, 55, 40)).save(data, format="PNG")
        with self.assertLogs("ai_adventure.ui.common", level="WARNING"):
            self.shell.visual_asset_coordinator._handle_completed(
                self.repo, self.request, data.getvalue(), "image/png",
            )
        popup = self.shell._image_failure_popup
        self.assertIsNotNone(popup)
        self.assertIn("uniform magenta background", popup.informativeText())
        popup.accept()

    def test_success_and_other_save_failures_do_not_show_popup(self):
        request = VisualAssetRequest("location", "forest", "Forest", "A green forest.")
        self.repo.ensure_visual_asset(
            asset_id=request.asset_id, subject_type="location", subject_key="forest",
            display_name="Forest", descriptor_hash=request.descriptor_hash,
            filename=save_relative_image_filename(self.repo, request), prompt=request.prompt, model="test",
        )
        data = BytesIO()
        Image.new("RGB", (64, 64), (20, 80, 40)).save(data, format="PNG")
        self.shell.visual_asset_coordinator._handle_completed(self.repo, request, data.getvalue(), "image/png")
        self.assertIsNone(self.shell._image_failure_popup)
        other = SaveRepository.create_new_save(self.root / "saves", "Other save")
        self.shell.visual_asset_coordinator.generation_failed.emit(other, request, "Unrelated failure")
        self.assertIsNone(self.shell._image_failure_popup)
