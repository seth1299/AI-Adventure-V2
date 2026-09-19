from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication

from ai_adventure.app.user_settings import (
    DEFAULT_UI_FONT_SIZE,
    MAX_UI_FONT_SIZE,
    MIN_UI_FONT_SIZE,
    normalize_app_settings,
)
from ai_adventure.ui.dialogues import MainMenuSettingsDialog
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
