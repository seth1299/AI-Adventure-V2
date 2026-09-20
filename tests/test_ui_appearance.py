from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QFileDialog, QFrame

from ai_adventure.app.user_settings import (
    DEFAULT_UI_FONT_SIZE,
    MAX_UI_FONT_SIZE,
    MIN_UI_FONT_SIZE,
    normalize_app_settings,
)
from ai_adventure.ui.dialogues import MainMenuSettingsDialog
from ai_adventure.ui.main_window import MainWindow
from ai_adventure.ui.themes_audio import apply_application_theme


class UiAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_app_settings_adds_backward_compatible_appearance_defaults(self) -> None:
        settings = normalize_app_settings({"theme": "Dark"}, tts_enabled=False)

        self.assertEqual(
            settings["appearance"],
            {"font_family": "", "font_size": DEFAULT_UI_FONT_SIZE},
        )

    def test_app_settings_clamps_appearance_values(self) -> None:
        settings = normalize_app_settings(
            {
                "appearance": {
                    "font_family": "  Aptos  ",
                    "font_size": 999,
                }
            },
            tts_enabled=False,
        )

        self.assertEqual(settings["appearance"]["font_family"], "Aptos")
        self.assertEqual(settings["appearance"]["font_size"], MAX_UI_FONT_SIZE)

        settings = normalize_app_settings(
            {"appearance": {"font_size": -10}},
            tts_enabled=False,
        )
        self.assertEqual(settings["appearance"]["font_size"], MIN_UI_FONT_SIZE)

    def test_settings_dialog_round_trips_appearance(self) -> None:
        dialog = MainMenuSettingsDialog(
            settings={
                "appearance": {"font_family": "System Default", "font_size": 16}
            },
            tts_enabled=False,
            music_enabled=False,
        )

        dialog.font_size_spin.setValue(16)
        selected_index = dialog.font_family_combo.findData("")
        self.assertGreaterEqual(selected_index, 0)
        dialog.font_family_combo.setCurrentIndex(selected_index)

        settings = dialog.build_settings()
        self.assertEqual(settings["appearance"], {"font_family": "", "font_size": 16})
        dialog.deleteLater()

    def test_font_family_choices_use_their_own_font_faces(self) -> None:
        dialog = MainMenuSettingsDialog(settings={}, tts_enabled=False)
        for index in range(dialog.font_family_combo.count()):
            family = str(dialog.font_family_combo.itemData(index) or "").strip()
            item_font = dialog.font_family_combo.itemData(index, Qt.ItemDataRole.FontRole)
            self.assertIsNotNone(item_font)
            if family:
                self.assertEqual(item_font.family(), family)
        dialog.close()

    def test_settings_dialog_hides_and_restores_audio_volume_rows(self) -> None:
        dialog = MainMenuSettingsDialog(
            settings={},
            tts_enabled=False,
            music_enabled=True,
        )

        self.assertTrue(
            dialog._audio_form.isRowVisible(dialog.music_volume_control)
        )
        dialog.music_enabled_checkbox.setChecked(False)
        self.assertFalse(
            dialog._audio_form.isRowVisible(dialog.music_volume_control)
        )
        dialog.music_enabled_checkbox.setChecked(True)
        self.assertTrue(
            dialog._audio_form.isRowVisible(dialog.music_volume_control)
        )

        dialog.sound_effects_enabled_checkbox.setChecked(False)
        self.assertFalse(
            dialog._audio_form.isRowVisible(dialog.sound_effects_volume_control)
        )
        dialog.background_ambience_enabled_checkbox.setChecked(False)
        self.assertFalse(
            dialog._audio_form.isRowVisible(dialog.background_ambience_volume_control)
        )
        dialog.close()

    def test_in_game_settings_hides_audio_children_with_parent_toggles(self) -> None:
        from ai_adventure.ui.screens.settings import SettingsScreen

        screen = SettingsScreen(music_enabled=True, tts_enabled=False)
        self.assertTrue(screen._settings_form.isRowVisible(screen.music_track_control))
        screen.music_enabled_checkbox.setChecked(False)
        self.assertFalse(screen._settings_form.isRowVisible(screen.music_track_control))
        self.assertFalse(screen._settings_form.isRowVisible(screen.music_volume_control))
        screen.music_enabled_checkbox.setChecked(True)
        self.assertTrue(screen._settings_form.isRowVisible(screen.music_track_control))
        screen.sound_effects_enabled_checkbox.setChecked(False)
        self.assertFalse(screen._settings_form.isRowVisible(screen.sound_effects_library_control))
        screen.background_ambience_enabled_checkbox.setChecked(False)
        self.assertFalse(screen._settings_form.isRowVisible(screen.background_ambience_track_control))
        screen.deleteLater()

    def test_in_game_settings_hides_image_children_and_exposes_model_combo(self) -> None:
        from ai_adventure.ui.screens.settings import SettingsScreen

        screen = SettingsScreen(music_enabled=False, tts_enabled=False)
        self.assertIsNotNone(screen.generated_image_model_combo)
        self.assertGreater(screen.generated_image_model_combo.count(), 0)
        self.assertTrue(
            screen._settings_form.isRowVisible(screen.generated_image_model_control)
        )
        screen.generated_images_enabled_checkbox.setChecked(False)
        self.assertFalse(
            screen._settings_form.isRowVisible(screen.generated_image_model_control)
        )
        self.assertFalse(
            screen._settings_form.isRowVisible(screen.maximum_generated_images_control)
        )
        self.assertFalse(
            screen._settings_form.isRowVisible(screen.failed_images_control)
        )
        screen.generated_images_enabled_checkbox.setChecked(True)
        self.assertTrue(
            screen._settings_form.isRowVisible(screen.generated_image_model_control)
        )
        screen.deleteLater()

    def test_in_game_settings_separates_parent_sections_only(self) -> None:
        from ai_adventure.ui.screens.settings import SettingsScreen

        screen = SettingsScreen(music_enabled=True, tts_enabled=False)
        separators = screen.findChildren(QFrame, "settingsSectionSeparator")
        self.assertEqual(len(separators), 4)
        screen.deleteLater()

    def test_settings_dialog_exposes_shared_audio_upload_buttons(self) -> None:
        imported: list[tuple[Path, str]] = []

        class FakeSoundManager:
            def import_audio_file(self, path: Path, category: str) -> tuple[bool, str]:
                imported.append((path, category))
                return True, f"Imported {category}"

        dialog = MainMenuSettingsDialog(
            settings={},
            tts_enabled=False,
            music_enabled=True,
            sound_manager=FakeSoundManager(),
        )
        with (
            patch.object(QFileDialog, "getOpenFileName", return_value=("C:/x.wav", "")),
            patch.object(QMessageBox, "information"),
        ):
            for button, category in (
                (dialog.music_upload_button, "music"),
                (dialog.sound_effects_upload_button, "sound_effects"),
                (dialog.background_ambience_upload_button, "background_ambience"),
            ):
                button.click()

        self.assertEqual([category for _path, category in imported], [
            "music",
            "sound_effects",
            "background_ambience",
        ])
        dialog.close()

    def test_main_menu_settings_cancel_reapplies_original_appearance(self) -> None:
        original_settings = {
            "theme": "Dark",
            "appearance": {"font_family": "", "font_size": 14},
            "audio": {},
        }
        apply_settings = Mock()

        class FakeDialog:
            def exec(self) -> QDialog.DialogCode:
                return QDialog.DialogCode.Rejected

        window = type("Window", (), {})()
        window.app_settings = original_settings
        window.tts_enabled = False
        window.playtesting_build = True
        window.sound_manager = None
        window.narration_player = None
        window._play_narrator_sample = Mock()
        window.app_paths = type("Paths", (), {"app_settings_path": Path("settings.json")})()
        window._apply_app_settings = apply_settings

        with patch("ai_adventure.ui.main_window.MainMenuSettingsDialog", return_value=FakeDialog()):
            MainWindow.open_main_menu_settings(window)  # type: ignore[arg-type]

        apply_settings.assert_called_once_with(original_settings, persist=False)

    def test_application_theme_applies_and_resets_font(self) -> None:
        old_font = QFont(self.app.font())
        old_palette = QPalette(self.app.palette())
        old_stylesheet = self.app.styleSheet()
        old_properties = {
            name: self.app.property(name)
            for name in (
                "ai_adventure_base_font_family",
                "ai_adventure_base_font_size",
                "ai_adventure_ui_font_family",
                "ai_adventure_ui_font_size",
            )
        }

        try:
            apply_application_theme(
                "Light",
                {"font_family": "DejaVu Sans", "font_size": 16},
            )
            self.assertEqual(self.app.font().pointSize(), 16)
            self.assertEqual(self.app.property("ai_adventure_ui_font_size"), 16)

            apply_application_theme("Light", {"font_family": "", "font_size": 10})
            self.assertEqual(self.app.font().pointSize(), 10)
            self.assertEqual(self.app.property("ai_adventure_ui_font_family"), "")
        finally:
            self.app.setFont(old_font)
            self.app.setPalette(old_palette)
            self.app.setStyleSheet(old_stylesheet)
            for name, value in old_properties.items():
                self.app.setProperty(name, value)


if __name__ == "__main__":
    unittest.main()
