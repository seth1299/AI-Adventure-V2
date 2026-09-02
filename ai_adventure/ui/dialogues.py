"""Dialog entry points extracted from the main-window composition module."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget


class TTSSettingsDialog(QDialog):
    """Dialog wrapper for the shared advanced narrator controls.

    TTSSettingsWidget remains in the legacy module during this migration.
    The deferred import keeps this dialogue module independent at import time
    and avoids a circular dependency while the widget is extracted next.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        audio_settings: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: Callable[[str, int, int], bool] | None = None,
        on_custom_voice_saved: Callable[[dict[str, Any]], None] | None = None,
        custom_voice_storage_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)
        from ai_adventure.ui.main_window import TTSSettingsWidget

        self.setWindowTitle("TTS Settings")
        self.resize(560, 520)
        self.tts_settings_widget = TTSSettingsWidget(
            audio_settings=audio_settings,
            voice_options=voice_options,
            on_sample_voice=on_sample_voice,
            on_custom_voice_saved=on_custom_voice_saved,
            custom_voice_storage_path=custom_voice_storage_path,
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout()
        layout.addWidget(self.tts_settings_widget)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def build_audio_settings(self) -> dict[str, Any]:
        return self.tts_settings_widget.build_audio_settings()

    @property
    def custom_voice_library_changed(self) -> bool:
        return self.tts_settings_widget.custom_voice_library_changed
