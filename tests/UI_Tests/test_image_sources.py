from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QMessageBox

from ai_adventure.ui.image_sources import NewGameImageSourceDialog
from ai_adventure.visual_assets import VisualAssetRequest
from tests.qt_test_case import QtTestCase


class NewGameImageSourceDialogTests(QtTestCase):
    def test_preview_refreshes_when_a_generated_image_replaces_bundled_art(self):
        from PIL import Image

        request = VisualAssetRequest("skill", "alchemy", "Alchemy", "Brew potions.")
        with tempfile.TemporaryDirectory() as directory:
            bundled = Path(directory) / "bundled.png"
            generated = Path(directory) / "generated.png"
            Image.new("RGB", (64, 64), (100, 80, 40)).save(bundled)
            Image.new("RGBA", (64, 64), (30, 90, 120, 128)).save(generated)
            path = Mock(return_value=bundled)
            dialog = NewGameImageSourceDialog(
                [request], choose_file=Mock(), create_image=Mock(), skip_image=Mock(),
                preview_path=path,
            )
            self.addCleanup(dialog.deleteLater)
            preview = dialog._preview_labels[request.asset_id]
            self.assertEqual(preview.property("imageViewerPath"), str(bundled))
            path.return_value = generated
            dialog.set_asset_status(request.asset_id, "ready")
            self.assertEqual(preview.property("imageViewerPath"), str(generated))
            self.assertTrue(preview.pixmap().hasAlphaChannel())

    def test_invalid_preview_is_hidden_and_leaves_image_choices_available(self):
        request = VisualAssetRequest("skill", "alchemy", "Alchemy", "Brew potions.")
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.png"
            invalid.write_bytes(b"not an image")
            dialog = NewGameImageSourceDialog(
                [request], choose_file=Mock(), create_image=None, skip_image=Mock(),
                preview_path=lambda _request: invalid,
            )
            self.addCleanup(dialog.deleteLater)
            self.assertTrue(dialog._preview_labels[request.asset_id].isHidden())
            self.assertEqual(dialog._rows[request.asset_id][0].text(), "No image source selected")
            self.assertTrue(dialog._rows[request.asset_id][1].isEnabled())
            self.assertTrue(dialog.continue_button.isEnabled())

    def test_ingame_buttons_copy_the_same_brief_without_generation(self):
        from ai_adventure.ui.primitives import RepositoryBackedWidget
        from PySide6.QtWidgets import QPushButton
        screen = RepositoryBackedWidget()
        self.addCleanup(screen.deleteLater)
        repository = Mock()
        repository.get_setting.return_value = False
        clipboard = QApplication.clipboard()
        previous = clipboard.text()
        self.addCleanup(clipboard.setText, previous)
        for kind in ("player", "npc", "inventory", "skill", "location", "bestiary"):
            request = VisualAssetRequest(subject_type=kind, subject_key=kind,
                display_name="Example", description="Weathered copper and green glass.",
                image_style="oil_painting", world_context="Medieval technology. Goblins live nearby.")
            dialog = QDialog()
            from PySide6.QtWidgets import QVBoxLayout
            QVBoxLayout(dialog)
            self.addCleanup(dialog.deleteLater)
            with patch.object(screen, "repository", return_value=repository), patch(
                "ai_adventure.application.asset_generation_service.AssetGenerationService.requests_for",
                return_value=[request]), patch.object(screen, "create_visual_asset") as generate:
                screen.add_copy_image_prompt_button(dialog, kind, kind)
                dialog.findChild(QPushButton, "copyImagePromptButton").click()
                self.assertEqual(clipboard.text(), request.external_prompt)
                self.assertNotIn("Goblins", clipboard.text())
                generate.assert_not_called()

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_copy_prompt_preserves_style_and_subject_without_resolving_source(self):
        requests = [VisualAssetRequest(
            subject_type=kind, subject_key=kind, display_name=name,
            description=description, image_style="oil_painting",
            world_context="A medieval harbor without modern technology.",
        ) for kind, name, description in (
            ("location", "Harbor", "Rain-darkened stone docks."),
            ("inventory", "Cauldron", "A dented copper cauldron."),
        )]
        clipboard = QApplication.clipboard()
        previous_text = clipboard.text()
        self.addCleanup(clipboard.setText, previous_text)
        for generation_enabled in (True, False):
            upload, generate, skip = Mock(), Mock(), Mock()
            dialog = NewGameImageSourceDialog(
                requests, choose_file=upload,
                create_image=generate if generation_enabled else None, skip_image=skip,
            )
            self.addCleanup(dialog.deleteLater)
            for request in requests:
                dialog.copy_prompt_buttons[request.asset_id].click()
                self.assertEqual(clipboard.text(), request.external_prompt)
                self.assertIn(request.display_name, clipboard.text())
                self.assertIn(request.description, clipboard.text())
                self.assertNotIn(request.world_context, clipboard.text())
                self.assertIn("medieval", clipboard.text())
                self.assertIn("Avoid modern", clipboard.text())
                self.assertIn("oil", clipboard.text().lower())
                self.assertEqual(dialog._rows[request.asset_id][0].text(), "No image source selected")
                self.assertTrue(dialog.continue_button.isEnabled())
            self.assertFalse(dialog._resolved)
            upload.assert_not_called()
            generate.assert_not_called()
            skip.assert_not_called()

    def test_unselected_subject_is_skipped_on_next(self) -> None:
        request = VisualAssetRequest(
            subject_type="location",
            subject_key="harbor",
            display_name="Harbor",
            description="Rain-darkened docks.",
        )
        skip = Mock()
        dialog = NewGameImageSourceDialog(
            [request],
            choose_file=lambda _request, _path: (True, ""),
            create_image=lambda _request: (True, ""),
            skip_image=skip,
        )

        self.assertTrue(dialog.continue_button.isEnabled())
        row = dialog._rows[request.asset_id]
        self.assertEqual(row[1].text(), "Choose File to Upload")
        self.assertEqual(row[2].text(), "Create an Image for me")
        skip.assert_not_called()
        dialog.continue_button.click()
        skip.assert_called_once_with(request)
        self.assertEqual(row[0].text(), "Skipped for now")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog.deleteLater()

    def test_generation_resolves_only_after_worker_status(self) -> None:
        request = VisualAssetRequest(
            subject_type="player",
            subject_key="player_1",
            display_name="Kit Vale",
            description="A scout in pale armor.",
        )
        generate = Mock(return_value=(True, ""))
        dialog = NewGameImageSourceDialog(
            [request],
            choose_file=lambda _request, _path: (True, ""),
            create_image=generate,
            skip_image=lambda _request: None,
        )

        row = dialog._rows[request.asset_id]
        row[2].click()
        generate.assert_not_called()
        self.assertTrue(dialog.continue_button.isEnabled())
        dialog.continue_button.click()
        generate.assert_called_once_with(request)
        self.assertFalse(dialog.continue_button.isEnabled())
        dialog.set_asset_status(request.asset_id, "ready")
        self.assertTrue(dialog.continue_button.isEnabled())
        self.assertEqual(row[0].text(), "Image ready")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog.deleteLater()

    def test_only_checked_images_are_queued_after_all_other_subjects_are_skipped(self):
        requests = [VisualAssetRequest(subject_type="location", subject_key=str(i),
                    display_name=f"Place {i}", description="A forest.") for i in range(3)]
        actions = []
        dialog = NewGameImageSourceDialog(requests,
            choose_file=Mock(),
            create_image=lambda r: (actions.append(("create", r.asset_id)) or (True, "")),
            skip_image=lambda r: actions.append(("skip", r.asset_id)))
        self.addCleanup(dialog.deleteLater)
        dialog._rows[requests[1].asset_id][2].click()
        dialog._rows[requests[2].asset_id][2].click()
        dialog._rows[requests[2].asset_id][2].click()
        self.assertEqual(actions, [])
        dialog.continue_button.click()
        self.assertEqual(actions, [("skip", requests[0].asset_id),
                                  ("skip", requests[2].asset_id), ("create", requests[1].asset_id)])
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        dialog.set_asset_status(requests[1].asset_id, "ready")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_bulk_selection_requires_confirmation_and_waits_for_next(self):
        request = VisualAssetRequest(subject_type="location", subject_key="harbor",
                    display_name="Harbor", description="Docks.")
        generate, skip = Mock(return_value=(True, "")), Mock()
        dialog = NewGameImageSourceDialog([request], choose_file=Mock(),
                                          create_image=generate, skip_image=skip)
        self.addCleanup(dialog.deleteLater)
        with patch("ai_adventure.ui.image_sources.QMessageBox.warning",
                   side_effect=[QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]) as warning:
            dialog.create_all_button.click()
            self.assertFalse(dialog._rows[request.asset_id][2].isChecked())
            dialog.create_all_button.click()
            self.assertTrue(dialog._rows[request.asset_id][2].isChecked())
            self.assertIn("API requests", warning.call_args.args[2])
        generate.assert_not_called()
        skip.assert_not_called()
        dialog.skip_all_button.click()
        self.assertFalse(dialog._rows[request.asset_id][2].isChecked())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        dialog.continue_button.click()
        skip.assert_called_once_with(request)
        generate.assert_not_called()

    def test_failed_selected_image_can_be_retried_or_skipped_without_releasing_opening(self):
        request = VisualAssetRequest(subject_type="location", subject_key="harbor",
                    display_name="Harbor", description="Docks.")
        generate = Mock(side_effect=[(False, "Limit reached"), (True, "")])
        skip = Mock()
        dialog = NewGameImageSourceDialog([request], choose_file=Mock(),
                                          create_image=generate, skip_image=skip)
        self.addCleanup(dialog.deleteLater)
        row = dialog._rows[request.asset_id]
        row[2].click()
        dialog.continue_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertTrue(dialog.continue_button.isEnabled())
        self.assertIn("Limit reached", row[0].text())
        row[2].click()
        dialog.continue_button.click()
        self.assertEqual(generate.call_count, 2)
        dialog.set_asset_status(request.asset_id, "failed", "Unavailable")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        dialog.continue_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        skip.assert_called_once_with(request)

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
