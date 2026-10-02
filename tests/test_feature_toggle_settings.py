import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFormLayout

from ai_adventure.app.user_settings import normalize_app_settings
from ai_adventure.audio.tts_settings import normalize_tts_audio_fields, normalize_voice_blend
from ai_adventure.ui.dialogues import MainMenuSettingsDialog, NewGameTemplateManagerDialog
from ai_adventure.ui.dialogues import TTSSettingsWidget
from ai_adventure.ui.screens.settings import SettingsScreen
from ai_adventure.ui.widgets.inputs import FeatureToggleCheckBox
from ai_adventure.ui.wizards.new_game import NewGameWizard


class FeatureToggleSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_labels_follow_mouse_and_signal_blocked_restoration(self):
        widget = FeatureToggleCheckBox("Music")
        self.assertEqual(widget.text(), "Music disabled")
        widget.show()
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.assertEqual(widget.text(), "Music enabled")
        widget.blockSignals(True)
        widget.setChecked(False)
        self.assertEqual(widget.text(), "Music disabled")
        widget.close()

    def test_feature_labels_across_settings_wizard_and_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            widgets = [
                MainMenuSettingsDialog(settings={}),
                SettingsScreen(),
                NewGameWizard(api_key_path=Path(directory)/"key", terms_acceptance_path=Path(directory)/"terms"),
                NewGameTemplateManagerDialog(template_path=Path(directory)/"templates.json"),
            ]
            try:
                for widget in widgets:
                    toggles = widget.findChildren(FeatureToggleCheckBox)
                    self.assertGreaterEqual(len(toggles), 3)
                    for toggle in toggles:
                        for checked in (False, True, False):
                            toggle.setChecked(checked)
                            self.assertTrue(toggle.text().endswith(" enabled" if checked else " disabled"))
                self.assertEqual(widgets[0].tts_volume_slider.value(), 80)
            finally:
                for widget in widgets:
                    widget.close()
                    widget.deleteLater()
                self.app.processEvents()

    def test_volume_defaults_and_explicit_preferences(self):
        self.assertEqual(normalize_tts_audio_fields({})["tts_volume"], 80)
        self.assertEqual(normalize_voice_blend({})["tts_volume"], 80)
        for volume in (0, 35, 100):
            self.assertEqual(normalize_app_settings({"audio": {"tts_volume": volume}})["audio"]["tts_volume"], volume)
        disabled = normalize_app_settings({}, tts_enabled=False)
        self.assertFalse(disabled["audio"]["narrator_enabled"])
        self.assertEqual(disabled["audio"]["tts_volume"], 80)
        self.assertEqual(normalize_app_settings(disabled)["audio"]["tts_volume"], 80)

    def test_distinct_narrator_and_player_voice_samples(self):
        from unittest.mock import Mock
        callback = Mock(return_value=True)
        widget = TTSSettingsWidget(on_sample_voice=callback, audio_settings={"tts_voice": "af_alloy", "player_tts_voice": "af_heart", "tts_volume": 70, "tts_speed": 120, "player_tts_volume": 45, "player_tts_speed": 85})
        widget.sample_voice_button.click()
        callback.assert_called_once_with("af_alloy", 70, 120)
        callback.reset_mock()
        widget.sample_player_voice_button.click()
        callback.assert_called_once_with("af_heart", 45, 85, text="The Player Character is ready. This is a sample of the selected voice.")
        widget.narrator_enabled_checkbox.setChecked(False)
        self.assertTrue(widget.sample_player_voice_button.isEnabled())
        widget.player_enabled_checkbox.setChecked(False)
        self.assertTrue(widget.player_voice_button_row.isHidden())
        self.assertTrue(widget.voice_button_row.isHidden())
        widget.close()

    def test_wizard_player_sample_resolves_automatic_voice_and_forwards_text(self):
        from unittest.mock import Mock
        from ai_adventure.audio.voices import assign_speaker_voices
        callback = Mock(return_value=True)
        with tempfile.TemporaryDirectory() as directory:
            wizard = NewGameWizard(on_sample_voice=callback, api_key_path=Path(directory)/"key", terms_acceptance_path=Path(directory)/"terms")
            wizard._set_character_pronouns("he/him")
            widget = wizard.tts_settings_widget
            widget.player_voice_combo.setCurrentIndex(widget.player_voice_combo.findData("ai"))
            widget.sample_player_voice_button.click()
            _cues, expected = assign_speaker_voices(
                [{"speaker_id": "player", "anchor_text": "sample"}],
                narrator_voice=widget.active_voice_spec(), available_voice_ids=list(widget.voice_options.values()),
                player_pronouns="he/him", player_voice="ai",
            )
            self.assertEqual(callback.call_args.args[0], expected["player"])
            self.assertIn("Player Character", callback.call_args.kwargs["text"])
            wizard.close()

    def test_player_sample_text_reaches_audio_player(self):
        from unittest.mock import Mock
        from ai_adventure.ui.main_window import MainWindow
        window = type("Window", (), {"narration_player": Mock()})()
        MainWindow._play_narrator_sample(window, "af_heart", 70, 120, text="Player sample")
        window.narration_player.play_sample.assert_called_once_with(voice="af_heart", volume=70, speed=120, text="Player sample")

    def test_audio_children_collapse_and_restore_across_forms(self):
        disabled_audio = {"music_enabled": False, "sound_effects_enabled": False, "background_ambience_enabled": False}
        with tempfile.TemporaryDirectory() as directory:
            widgets = [
                MainMenuSettingsDialog(settings={"audio": disabled_audio}),
                NewGameWizard(audio_defaults=disabled_audio, api_key_path=Path(directory)/"key", terms_acceptance_path=Path(directory)/"terms"),
                NewGameTemplateManagerDialog(audio_defaults=disabled_audio, template_path=Path(directory)/"templates.json"),
                SettingsScreen(),
            ]
            try:
                for widget in widgets:
                    for prefix in ("music", "sound_effects", "background_ambience"):
                        toggle = getattr(widget, prefix+"_enabled_checkbox")
                        slider = getattr(widget, prefix+"_volume_slider")
                        slider.setValue(37)
                        controls = [slider]
                        for suffix in ("_test_button", "_upload_button"):
                            control = getattr(widget, prefix+suffix, None)
                            if control is not None:
                                controls.append(control)
                        for checked in (False, True, False, True):
                            with self.subTest(form=type(widget).__name__, feature=prefix, checked=checked):
                                toggle.setChecked(checked)
                                for control in controls:
                                    # Locate the form row's field, including wrapper
                                    # widgets around sliders and upload/status groups.
                                    field = control
                                    while field.parentWidget() is not None:
                                        parent = field.parentWidget()
                                        layout = parent.layout()
                                        if isinstance(layout, QFormLayout) and layout.getWidgetPosition(field)[0] >= 0:
                                            self.assertEqual(layout.isRowVisible(field), checked)
                                            label = layout.labelForField(field)
                                            if label is not None:
                                                self.assertEqual(label.isHidden(), not checked)
                                            break
                                        field = parent
                                    else:
                                        self.fail("Audio control did not belong to a form row")
                                self.assertEqual(slider.value(), 37)
                        # Bindings also work while a template loader blocks signals.
                        if isinstance(widget, (NewGameWizard, NewGameTemplateManagerDialog, MainMenuSettingsDialog)):
                            toggle.blockSignals(True)
                            toggle.setChecked(False)
                            self.assertTrue(slider.parentWidget().isHidden())
                            toggle.blockSignals(False)
                    tts = getattr(widget, "tts_settings_widget", None) or getattr(widget, "template_tts_settings_widget", None)
                    if tts is not None:
                        narrator = tts.narrator_enabled_checkbox
                        narrator.setChecked(False)
                        self.assertTrue(tts.tts_volume_row.isHidden())
                        narrator.setChecked(True)
                        self.assertFalse(tts.tts_volume_row.isHidden())
            finally:
                for widget in widgets:
                    widget.close()
                    widget.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
