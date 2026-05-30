from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.audio.narration import chunk_tts_text, sanitize_tts_text
from ai_adventure.audio.sound_manager import prepare_sound_directory


class AudioTests(unittest.TestCase):
    def test_sanitize_tts_text_removes_tags_and_action_suggestions(self) -> None:
        text = sanitize_tts_text(
            "The room falls quiet. [[MUSIC: Boss_Fight.mp3]]\n\n"
            "- Search the desk.\n"
            "- Leave the room."
        )

        self.assertEqual(text, "The room falls quiet.")

    def test_chunk_tts_text_splits_long_narration(self) -> None:
        chunks = chunk_tts_text(
            "First sentence. Second sentence. Third sentence.",
            max_length=25,
        )

        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0], "First sentence.")

    def test_packaged_audio_paths_resolve_to_current_folder_layout(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = AppPaths(
                app_data_dir=root,
                saves_dir=root / "saves",
                logs_dir=root / "logs",
                log_file=root / "logs" / "ai_adventure.log",
            )
            sound_directory = prepare_sound_directory(paths)

            self.assertEqual(sound_directory, paths.package_music_tracks_dir)
            self.assertTrue((sound_directory / "Boss_Fight.mp3").exists())
            self.assertEqual(paths.kokoro_model_path, paths.package_tts_dir / "kokoro-v1.0.onnx")
            self.assertEqual(paths.kokoro_voices_path, paths.package_tts_dir / "voices-v1.0.bin")
            self.assertTrue(paths.kokoro_model_path.exists())
            self.assertTrue(paths.kokoro_voices_path.exists())


if __name__ == "__main__":
    unittest.main()
