from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel

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

    def test_generation_button_is_omitted_when_generation_is_disabled(self) -> None:
        request = VisualAssetRequest(
            subject_type="npc",
            subject_key="npc_1",
            display_name="Mara",
            description="A harbor pilot.",
        )
        dialog = NewGameImageSourceDialog(
            [request],
            choose_file=lambda _request, _path: (True, ""),
            create_image=None,
            skip_image=lambda _request: None,
        )

        row = dialog._rows[request.asset_id]
        self.assertIsNone(row[2])
        self.assertIn("Choose a local image", dialog.findChildren(QLabel)[1].text())
        row[3].click()
        self.assertTrue(dialog.continue_button.isEnabled())
        dialog.deleteLater()

    def test_choose_file_button_opens_picker_and_forwards_selected_path(self) -> None:
        request = VisualAssetRequest(
            subject_type="player",
            subject_key="player_1",
            display_name="Kit Vale",
            description="A scout in pale armor.",
        )
        selected_path = Path("C:/Images/kit.png")
        chosen: list[tuple[VisualAssetRequest, Path]] = []

        class FakeFileDialog:
            FileMode = QFileDialog.FileMode
            Option = QFileDialog.Option

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def setFileMode(self, _mode: object) -> None:
                pass

            def setNameFilters(self, _filters: object) -> None:
                pass

            def setOption(self, _option: object, _enabled: bool) -> None:
                pass

            def setWindowModality(self, _modality: object) -> None:
                pass

            def setModal(self, _modal: bool) -> None:
                pass

            def show(self) -> None:
                pass

            def raise_(self) -> None:
                pass

            def activateWindow(self) -> None:
                pass

            def exec(self) -> int:
                return int(QDialog.DialogCode.Accepted)

            def selectedFiles(self) -> list[str]:
                return [str(selected_path)]

        dialog = NewGameImageSourceDialog(
            [request],
            choose_file=lambda current_request, path: (
                chosen.append((current_request, path)) or (True, "")
            ),
            create_image=None,
            skip_image=lambda _request: None,
        )

        with patch("ai_adventure.ui.image_sources.QFileDialog", FakeFileDialog):
            dialog._rows[request.asset_id][1].click()

        self.assertEqual(chosen, [(request, selected_path)])
        self.assertEqual(dialog._rows[request.asset_id][0].text(), "Using uploaded image")
        self.assertTrue(dialog.continue_button.isEnabled())
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
