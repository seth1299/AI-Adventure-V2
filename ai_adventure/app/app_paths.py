from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    """
    Centralized application paths.

    Args:
        app_data_dir: Root directory for saves, logs, and user settings.
        saves_dir: Directory containing save folders.
        logs_dir: Directory containing log files.
        log_file: Main application log file.
    """

    app_data_dir: Path
    saves_dir: Path
    logs_dir: Path
    log_file: Path

    @property
    def repo_root(self) -> Path:
        """Returns the local project root when running from source."""

        return Path(__file__).resolve().parents[2]

    @property
    def legacy_app_dir(self) -> Path:
        """Returns the sibling legacy app directory used for local asset migration."""

        return self.repo_root.parent / "AI-Adventure"

    @property
    def sounds_dir(self) -> Path:
        """Returns the app-managed sound asset directory."""

        return self.app_data_dir / "sounds"

    @property
    def package_audio_dir(self) -> Path:
        """Returns the packaged source-tree audio asset directory."""

        return self.repo_root / "ai_adventure" / "audio"

    @property
    def package_data_dir(self) -> Path:
        """Returns the packaged source-tree data asset directory."""

        return self.repo_root / "ai_adventure" / "data"

    @property
    def app_icon_path(self) -> Path:
        """Returns the packaged application icon path."""

        return self.package_data_dir / "app_icon.ico"

    @property
    def package_music_tracks_dir(self) -> Path:
        """Returns the packaged music-track directory."""

        return self.package_audio_dir / "music_tracks"

    @property
    def package_tts_dir(self) -> Path:
        """Returns the packaged TTS asset directory."""

        return self.package_audio_dir / "tts"

    @property
    def tts_output_dir(self) -> Path:
        """Returns the temporary app-managed narration output directory."""

        return self.app_data_dir / "tts_cache"

    @property
    def tts_models_dir(self) -> Path:
        """Returns the app-managed TTS model directory."""

        return self.app_data_dir / "models" / "tts"

    @property
    def new_game_templates_path(self) -> Path:
        """Returns the reusable new-game wizard templates path."""

        return self.app_data_dir / "new_game_templates.json"

    @property
    def legacy_new_game_template_path(self) -> Path:
        """Returns the old single-template new-game wizard path."""

        return self.app_data_dir / "new_game_template.json"

    @property
    def kokoro_model_path(self) -> Path:
        """Returns the best known Kokoro ONNX model path."""

        return self._first_existing_path(
            os.getenv("AI_ADVENTURE_KOKORO_MODEL_PATH"),
            self.package_tts_dir / "kokoro-v1.0.onnx",
            self.tts_models_dir / "kokoro" / "kokoro-v1.0.onnx",
            self.repo_root / "models" / "tts" / "kokoro" / "kokoro-v1.0.onnx",
            self.legacy_app_dir / "models" / "tts" / "kokoro" / "kokoro-v1.0.onnx",
            fallback=self.tts_models_dir / "kokoro" / "kokoro-v1.0.onnx",
        )

    @property
    def kokoro_voices_path(self) -> Path:
        """Returns the best known Kokoro voices file path."""

        return self._first_existing_path(
            os.getenv("AI_ADVENTURE_KOKORO_VOICES_PATH"),
            self.package_tts_dir / "voices-v1.0.bin",
            self.tts_models_dir / "kokoro" / "voices-v1.0.bin",
            self.repo_root / "models" / "tts" / "kokoro" / "voices-v1.0.bin",
            self.legacy_app_dir / "models" / "tts" / "kokoro" / "voices-v1.0.bin",
            fallback=self.tts_models_dir / "kokoro" / "voices-v1.0.bin",
        )

    @classmethod
    def create(cls) -> "AppPaths":
        """
        Creates platform-appropriate application paths.

        Returns:
            AppPaths with all required directories created.
        """

        app_data_env = os.getenv("APPDATA")

        if app_data_env is not None and app_data_env.strip():
            app_data_dir = Path(app_data_env) / "AI Adventure"
        else:
            app_data_dir = Path.home() / ".ai_adventure"

        saves_dir = app_data_dir / "saves"
        logs_dir = app_data_dir / "logs"
        sounds_dir = app_data_dir / "sounds"
        tts_output_dir = app_data_dir / "tts_cache"
        log_file = logs_dir / "ai_adventure.log"

        saves_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        sounds_dir.mkdir(parents=True, exist_ok=True)
        tts_output_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            app_data_dir=app_data_dir,
            saves_dir=saves_dir,
            logs_dir=logs_dir,
            log_file=log_file,
        )

    def _first_existing_path(
        self,
        *candidates: str | Path | None,
        fallback: Path,
    ) -> Path:
        """Returns the first existing candidate path, otherwise a fallback."""

        for candidate in candidates:
            if candidate is None:
                continue

            candidate_path = Path(candidate).expanduser()

            if candidate_path.exists():
                return candidate_path

        return fallback
