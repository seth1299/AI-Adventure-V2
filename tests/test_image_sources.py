from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ai_adventure.ui.image_sources import NewGameImageSourceDialog
from ai_adventure.visual_assets import VisualAssetRequest


class NewGameImageSourceDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_each_subject_requires_a_source_disposition(self) -> None:
        request = VisualAssetRequest(
            subject_type="location",
            subject_key="harbor",
            display_name="Harbor",
            description="Rain-darkened docks.",
        )
        dialog = NewGameImageSourceDialog(
            [request],
            choose_file=lambda _request, _path: (True, ""),
            create_image=lambda _request: (True, ""),
            skip_image=lambda _request: None,
        )

        self.assertFalse(dialog.continue_button.isEnabled())
        row = dialog._rows[request.asset_id]
        self.assertEqual(row[1].text(), "Choose File to Upload")
        self.assertEqual(row[2].text(), "Create an Image for me")
        row[3].click()
        self.assertTrue(dialog.continue_button.isEnabled())
        self.assertEqual(row[0].text(), "Skipped for now")
        dialog.deleteLater()

    def test_generation_resolves_only_after_worker_status(self) -> None:
        request = VisualAssetRequest(
            subject_type="player",
            subject_key="player_1",
            display_name="Kit Vale",
            description="A scout in pale armor.",
        )
        dialog = NewGameImageSourceDialog(
            [request],
            choose_file=lambda _request, _path: (True, ""),
            create_image=lambda _request: (True, ""),
            skip_image=lambda _request: None,
        )

        row = dialog._rows[request.asset_id]
        row[2].click()
        self.assertFalse(dialog.continue_button.isEnabled())
        dialog.set_asset_status(request.asset_id, "ready")
        self.assertTrue(dialog.continue_button.isEnabled())
        self.assertEqual(row[0].text(), "Image ready")
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
