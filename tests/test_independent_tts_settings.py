from __future__ import annotations

import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from ai_adventure.application.audio_preferences_service import AudioPreferencesService
from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.audio.narration import NarrationPlayer, build_narration_chunks
from ai_adventure.audio.tts_settings import (
    active_player_voice_spec_from_audio,
    read_tts_audio_settings,
)
from ai_adventure.audio.voices import assign_speaker_voices
from ai_adventure.ui.dialogues import TTSSettingsWidget
from ai_adventure.persistence.save_repository import SaveRepository


class IndependentTtsSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sections_have_independent_visibility_and_preview_preferences(self):
        callback = Mock(return_value=True)
        widget = TTSSettingsWidget(on_sample_voice=callback)
        try:
            widget.tts_volume_slider.setValue(65)
            widget.tts_speed_slider.setValue(130)
            widget.player_tts_volume_slider.setValue(40)
            widget.player_tts_speed_slider.setValue(90)
            widget.player_voice_mode_combo.setCurrentIndex(1)
            widget.current_player_voice_blend = {
                "voice_a": "af_heart", "voice_b": "am_echo", "voice_a_weight": 70,
            }
            widget.sample_player_voice_button.click()
            callback.assert_called_once_with(
                "af_heart:70,am_echo:30", 40, 90,
                text="The Player Character is ready. This is a sample of the selected voice.",
            )
            audio = widget.build_audio_settings()
            self.assertEqual(audio["tts_voice_mode"], "preset")
            self.assertEqual(audio["player_tts_voice_mode"], "blend")
            self.assertEqual((audio["tts_volume"], audio["tts_speed"]), (65, 130))
            self.assertTrue(widget.player_voice_combo.isHidden())
            self.assertFalse(widget.player_custom_voice_row.isHidden())
            widget.narrator_enabled_checkbox.setChecked(False)
            self.assertTrue(widget.tts_volume_row.isHidden())
            self.assertFalse(widget.player_tts_volume_row.isHidden())
            widget.player_enabled_checkbox.setChecked(False)
            self.assertTrue(widget.player_tts_volume_row.isHidden())
            widget.narrator_enabled_checkbox.setChecked(True)
            self.assertFalse(widget.tts_volume_row.isHidden())
            self.assertTrue(widget.player_tts_volume_row.isHidden())
        finally:
            widget.close()

    def test_editing_player_custom_voice_preserves_narrator_preferences(self):
        widget = TTSSettingsWidget(audio_settings={
            "tts_voice": "af_sarah", "tts_volume": 60, "tts_speed": 125,
            "player_tts_volume": 35, "player_tts_speed": 85,
        })
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.custom_voice_library_changed = False
        dialog.build_audio_settings.return_value = {
            "tts_volume": 45, "tts_speed": 95, "tts_voice_mode": "blend",
            "tts_voice_blend": {"voice_a": "af_heart", "voice_b": "am_echo"},
            "tts_custom_voices": [],
        }
        try:
            with patch("ai_adventure.ui.dialogues.CustomVoiceDialog", return_value=dialog) as constructor:
                widget._open_custom_voice_dialog(player=True)
            editor_audio = constructor.call_args.kwargs["audio_settings"]
            self.assertEqual((editor_audio["tts_volume"], editor_audio["tts_speed"]), (35, 85))
            audio = widget.build_audio_settings()
            self.assertEqual((audio["tts_voice"], audio["tts_volume"], audio["tts_speed"]), ("af_sarah", 60, 125))
            self.assertEqual((audio["player_tts_volume"], audio["player_tts_speed"]), (45, 95))
            self.assertEqual(audio["player_tts_voice_mode"], "blend")
        finally:
            widget.close()

    def test_settings_survive_new_game_save_and_apply_to_playback(self):
        audio = {
            "narrator_enabled": False, "player_enabled": True,
            "tts_volume": 60, "tts_speed": 125,
            "player_tts_volume": 35, "player_tts_speed": 85,
            "player_tts_voice_mode": "blend",
            "player_tts_voice_blend": {"voice_a": "af_heart", "voice_b": "am_echo"},
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = NewGameService.create_repository(Path(directory), {"title": "Independent voices", "audio": audio})
            repository = SaveRepository(repository.db_path)
            saved = read_tts_audio_settings(repository.get_setting)
            for key in audio:
                if key != "player_tts_voice_blend":
                    self.assertEqual(saved[key], audio[key])
            self.assertEqual(active_player_voice_spec_from_audio(saved), "af_heart:50,am_echo:50")
            player = Mock()
            AudioPreferencesService.apply(repository, narration_player=player)
            player.set_enabled.assert_called_with(True)
            player.set_speaker_preferences.assert_called_with(saved)

    def test_preview_does_not_overwrite_gameplay_volume_or_speed(self):
        player = NarrationPlayer.__new__(NarrationPlayer)
        player.enabled = True
        player.volume = 0.6
        player.speed = 1.25
        player.player_volume = 0.35
        player.player_speed = 0.85
        with patch.object(player, "narrate", return_value=True) as narrate:
            player.play_sample(voice="af_heart", volume=40, speed=90)
        self.assertEqual(narrate.call_args.kwargs["_sample_preferences"], (0.4, 0.9))
        self.assertEqual((player.volume, player.speed), (0.6, 1.25))
        self.assertEqual((player.player_volume, player.player_speed), (0.35, 0.85))

    def test_named_player_blend_uses_independent_speed_and_volume(self):
        cues, _ = assign_speaker_voices(
            [{"speaker_id": "robin", "anchor_text": "I am ready."}],
            player_speaker_ids={"robin"}, player_voice="af_heart:70,am_echo:30",
            available_voice_ids=["af_heart", "am_echo", "af_sarah"],
            narrator_voice="af_sarah",
        )
        chunks = build_narration_chunks("A new day. I am ready.", speaker_cues=cues)
        self.assertEqual([chunk.is_player for chunk in chunks], [False, True])
        player = NarrationPlayer.__new__(NarrationPlayer)
        player.enabled = True
        player.volume = 0.6
        player.speed = 1.25
        player.set_speaker_preferences({"player_tts_volume": 35, "player_tts_speed": 85})
        player._session_id = 1
        player._state_lock = threading.Lock()
        player._generation_lock = threading.Lock()
        player.tts_manager = Mock()
        player.tts_manager.synthesize_to_file.return_value = Path("unused.wav")
        output = queue.Queue()
        player._produce_chunks(1, chunks, output, "af_sarah")
        requests = [call.args[0] for call in player.tts_manager.synthesize_to_file.call_args_list]
        self.assertEqual([(request.voice, request.speed) for request in requests], [("af_sarah", 1.25), ("af_heart:70,am_echo:30", 0.85)])
        self.assertEqual([output.get().volume, output.get().volume], [0.6, 0.35])
        # Muted narration must still reveal its text before player speech.
        player.set_speaker_preferences({"narrator_enabled": False, "player_tts_volume": 35, "player_tts_speed": 85})
        player.tts_manager.reset_mock()
        output = queue.Queue()
        player._produce_chunks(1, chunks, output, "af_sarah")
        self.assertIsNone(output.get().audio_path)
        self.assertEqual(player.tts_manager.synthesize_to_file.call_count, 1)


if __name__ == "__main__":
    unittest.main()
