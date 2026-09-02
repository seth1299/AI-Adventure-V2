"""Theme, audio-manager, and narrator preference helpers."""

from __future__ import annotations

import importlib
import logging
import re
from pathlib import Path
from typing import Any, Callable, Protocol

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox,
    QWidget,
)

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.features import is_playtesting_build, is_tts_enabled
from ai_adventure.application.audio_preferences_service import AudioPreferencesService
from ai_adventure.application.save_game_service import SaveGameService
from ai_adventure.audio.pronunciation import apply_pronunciation_map
from ai_adventure.audio.tts_settings import (
    DEFAULT_TTS_SPEED_PERCENT,
    active_voice_spec_from_audio,
    merge_custom_voices,
    normalize_custom_voices,
    normalize_narrator_voice_spec,
    normalize_tts_audio_fields,
    normalize_tts_speed_percent,
    normalize_tts_voice_mode,
    normalize_voice_blend,
    voice_display_name,
)
from ai_adventure.audio.voices import (
    DEFAULT_NARRATOR_VOICE,
    assign_speaker_voices,
    available_narrator_voices,
    normalize_narrator_voice,
)
from ai_adventure.domain.services.state_projection import refresh_calendar_time_projection
from ai_adventure.infrastructure.sqlite import SaveRepository
from ai_adventure.ui.primitives import _set_combo_to_data

LOGGER = logging.getLogger(__name__)
THEME_NAMES = {"Light", "Dark"}
SampleVoiceCallback = Callable[[str, int, int], bool]

class SoundManagerProtocol(Protocol):
    def get_valid_track_names(self) -> list[str]: ...
    def get_valid_sound_effect_names(self) -> list[str]: ...
    def get_valid_background_ambience_names(self) -> list[str]: ...
    def set_music_enabled(self, enabled: bool) -> None: ...
    def set_music_volume(self, volume: float | int | None) -> None: ...
    def set_sound_effects_enabled(self, enabled: bool) -> None: ...
    def set_sound_effects_volume(self, volume: float | int | None) -> None: ...
    def set_background_ambience_enabled(self, enabled: bool) -> None: ...
    def set_background_ambience_volume(self, volume: float | int | None) -> None: ...
    def play_music(self, track_name_or_path: str | Path | None) -> None: ...
    def play_music_preview(self, track_name_or_path: str | Path | None) -> None: ...
    def play_sound_effect(self, track_name_or_path: str | Path | None) -> None: ...
    def play_background_ambience(self, track_name_or_path: str | Path | None) -> None: ...
    def stop_music(self, *, clear_current: bool = True) -> None: ...
    def stop_sound_effect(self, *, clear_current: bool = True) -> None: ...
    def stop_background_ambience(self, *, clear_current: bool = True) -> None: ...

class NarrationPlayerProtocol(Protocol):
    def set_enabled(self, enabled: bool) -> None: ...
    def set_volume(self, volume: float | int | None) -> None: ...
    def set_voice(self, voice: str | None) -> None: ...
    def set_speed(self, speed: float | int | None) -> None: ...
    def play_sample(self, *, voice: str | None = None, volume: float | int | None = None, speed: float | int | None = None, text: str = ..., sound_effect_cues: list[dict[str, str]] | None = None, speaker_cues: list[dict[str, str]] | None = None, tts_text_transform: Callable[[str], str] | None = None, on_sound_effect: Callable[[str], None] | None = None) -> bool: ...
    def narrate(self, text: str, *, voice: str | None = None, sound_effect_cues: list[dict[str, str]] | None = None, speaker_cues: list[dict[str, str]] | None = None, tts_text_transform: Callable[[str], str] | None = None, on_chunk_start: Callable[[str], None] | None = None, on_sound_effect: Callable[[str], None] | None = None, on_complete: Callable[[], None] | None = None) -> bool: ...
    def stop(self) -> None: ...
    def get_available_voices(self) -> dict[str, str]: ...

if not is_playtesting_build():
    try:
        from ai_adventure.infrastructure.audio import NarrationPlayer as _NarrationPlayerClass
    except Exception:
        _NarrationPlayerClass = None
    try:
        from ai_adventure.infrastructure.audio import (
            SoundManager as _SoundManagerClass,
            prepare_background_ambience_directory,
            prepare_sound_directory,
            prepare_sound_effect_directory,
        )
    except Exception:
        _SoundManagerClass = None
else:
    _NarrationPlayerClass = None
    _SoundManagerClass = None

    def prepare_sound_directory(app_paths: Any) -> Path:
        """Returns a harmless placeholder path in the audio-free build."""

        return Path(app_paths.sounds_dir)

    def prepare_sound_effect_directory(app_paths: Any) -> Path:
        """Returns a harmless placeholder effect path in the audio-free build."""

        return Path(app_paths.sound_effects_dir)

    def prepare_background_ambience_directory(app_paths: Any) -> Path:
        """Returns a harmless placeholder ambience path in the audio-free build."""

        return Path(app_paths.background_ambience_dir)

def apply_application_theme(theme: str) -> None:
    """Applies the selected app-wide theme to the active QApplication."""

    app = QApplication.instance()

    if not isinstance(app, QApplication):
        return

    clean_theme = _normalize_theme_name(theme)

    if clean_theme == "Dark":
        app.setPalette(_dark_theme_palette())
        app.setStyleSheet(_dark_theme_stylesheet())
        return

    if clean_theme == "Light":
        app.setPalette(_light_theme_palette())
        app.setStyleSheet(_light_theme_stylesheet())
        return

    app.setPalette(_light_theme_palette())
    app.setStyleSheet(_light_theme_stylesheet())



def _create_narration_player(app_paths: AppPaths) -> NarrationPlayerProtocol | None:
    """Creates the narration player only when TTS support is enabled."""

    if not is_tts_enabled():
        LOGGER.info("TTS narration disabled by application configuration.")
        return None

    try:
        tts_module = importlib.import_module("ai_adventure.audio.tts.tts_manager")
        create_tts_manager = getattr(tts_module, "create_tts_manager")
    except Exception as error:
        LOGGER.warning("Narrator is unavailable because TTS could not be loaded: %s", error)
        return None

    if _NarrationPlayerClass is None:
        return None

    return _NarrationPlayerClass(
        create_tts_manager(
            model_path=app_paths.kokoro_model_path,
            voices_path=app_paths.kokoro_voices_path,
            output_directory=app_paths.tts_output_dir,
        )
    )


def _refresh_repository_calendar_time(repository: SaveRepository) -> None:
    """Recomputes the saved display time from current calendar settings."""

    refresh_calendar_time_projection(repository)


def _apply_audio_settings_to_managers(
    repository: SaveRepository,
    *,
    sound_manager: SoundManagerProtocol | None,
    narration_player: NarrationPlayerProtocol | None,
) -> None:
    """Applies saved music, one-shot effect, and narrator settings to managers."""

    AudioPreferencesService.apply(
        repository,
        sound_manager=sound_manager,
        narration_player=narration_player,
    )
    return

    music_enabled = _bool_setting(repository.get_setting("audio.music_enabled", True), True)
    sound_effects_enabled = _bool_setting(
        repository.get_setting("audio.sound_effects_enabled", True),
        True,
    )
    background_ambience_enabled = _bool_setting(
        repository.get_setting("audio.background_ambience_enabled", True),
        True,
    )
    narrator_enabled = _bool_setting(
        repository.get_setting("audio.narrator_enabled", True),
        True,
    )
    music_volume = _clamped_int(repository.get_setting("audio.music_volume", 25), 25, 0, 100)
    sound_effects_volume = _clamped_int(
        repository.get_setting("audio.sound_effects_volume", 35),
        35,
        0,
        100,
    )
    background_ambience_volume = _clamped_int(
        repository.get_setting("audio.background_ambience_volume", 15),
        15,
        0,
        100,
    )
    tts_audio = normalize_tts_audio_fields(
        {
            "narrator_enabled": narrator_enabled,
            "tts_volume": repository.get_setting("audio.tts_volume", 90),
            "tts_voice": repository.get_setting("audio.tts_voice", DEFAULT_NARRATOR_VOICE),
            "tts_speed": repository.get_setting("audio.tts_speed", DEFAULT_TTS_SPEED_PERCENT),
            "tts_voice_mode": repository.get_setting("audio.tts_voice_mode", "preset"),
            "tts_voice_blend": repository.get_setting("audio.tts_voice_blend", {}),
            "tts_custom_voices": repository.get_setting("audio.tts_custom_voices", []),
        }
    )
    tts_volume = int(tts_audio["tts_volume"])
    tts_voice = active_voice_spec_from_audio(tts_audio)
    tts_speed = int(tts_audio["tts_speed"])

    if sound_manager is not None:
        sound_manager.set_music_volume(music_volume)
        sound_manager.set_music_enabled(music_enabled)
        sound_manager.set_sound_effects_volume(sound_effects_volume)
        sound_manager.set_sound_effects_enabled(sound_effects_enabled)
        if hasattr(sound_manager, "set_background_ambience_volume"):
            sound_manager.set_background_ambience_volume(background_ambience_volume)
        if hasattr(sound_manager, "set_background_ambience_enabled"):
            sound_manager.set_background_ambience_enabled(background_ambience_enabled)

        current_music = str(repository.get_setting("audio.current_music", "") or "").strip()

        if music_enabled and current_music:
            sound_manager.play_music(current_music)
        else:
            sound_manager.stop_music(clear_current=False)

        if not sound_effects_enabled:
            sound_manager.stop_sound_effect(clear_current=False)

        current_background_ambience = str(
            repository.get_setting("audio.current_background_ambience", "") or ""
        ).strip()
        if (
            background_ambience_enabled
            and current_background_ambience
            and hasattr(sound_manager, "play_background_ambience")
        ):
            sound_manager.play_background_ambience(current_background_ambience)
        elif hasattr(sound_manager, "stop_background_ambience"):
            sound_manager.stop_background_ambience(clear_current=False)

    if narration_player is not None and hasattr(narration_player, "set_volume"):
        narration_player.set_volume(tts_volume)
    if narration_player is not None and hasattr(narration_player, "set_speed"):
        narration_player.set_speed(tts_speed)
    if narration_player is not None and hasattr(narration_player, "set_voice"):
        narration_player.set_voice(tts_voice)
    if narration_player is not None and hasattr(narration_player, "set_enabled"):
        narration_player.set_enabled(narrator_enabled)


def _resolve_speaker_cues_for_repository(
    repository: SaveRepository,
    narration_player: NarrationPlayerProtocol | None,
    speaker_cues: Any,
) -> list[dict[str, str]]:
    """Assigns installed voices and persists stable speaker-to-voice IDs."""

    tts_audio = normalize_tts_audio_fields(
        {
            "tts_voice": repository.get_setting(
                "audio.tts_voice", DEFAULT_NARRATOR_VOICE
            ),
            "tts_voice_mode": repository.get_setting(
                "audio.tts_voice_mode", "preset"
            ),
            "tts_voice_blend": repository.get_setting(
                "audio.tts_voice_blend", {}
            ),
        }
    )
    voice_options = _narrator_voice_options(narration_player)
    existing_assignments = repository.get_setting(
        "audio.speaker_voice_assignments",
        {},
    )
    resolved, assignments = assign_speaker_voices(
        speaker_cues,
        narrator_voice=active_voice_spec_from_audio(tts_audio),
        available_voice_ids=list(voice_options.values()),
        existing_assignments=existing_assignments,
    )
    if assignments != existing_assignments:
        repository.set_setting("audio.speaker_voice_assignments", assignments)
    return resolved


def _normalize_theme_name(theme: str) -> str:
    """Returns a supported theme name."""

    clean_theme = str(theme or "Light").strip()
    return clean_theme if clean_theme in THEME_NAMES else "Light"


def _application_uses_dark_theme() -> bool:
    """Returns True when the active Qt application palette is dark."""

    app = QApplication.instance()

    if not isinstance(app, QApplication):
        return False

    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def _next_available_save_title(saves_dir: Path, requested_title: str) -> str:
    """Returns the first non-conflicting player-facing save title."""

    return SaveGameService(saves_dir).next_available_title(requested_title)


def _split_save_title_suffix(title: str) -> tuple[str, int]:
    """Splits a trailing numeric save suffix from a title."""

    match = re.match(r"^(?P<base>.*?)(?:\s+(?P<suffix>\d+))?$", title.strip())

    if match is None:
        return title.strip() or "New Adventure", 1

    base_title = str(match.group("base") or "").strip() or "New Adventure"
    suffix_text = match.group("suffix")

    if suffix_text is None:
        return base_title, 1

    return base_title, int(suffix_text)


def _light_theme_palette() -> QPalette:
    """Builds a light, high-contrast application palette."""

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f5f7fb"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#eef2f7"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#4b5563"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#e5e7eb"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#1d4ed8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#111827"))
    return palette


def _dark_theme_palette() -> QPalette:
    """Builds a dark, high-contrast application palette."""

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#121416"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2a2d30"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#a9b0b6"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#303437"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4c8fcb"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#303437"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f1f3f4"))
    return palette


def _light_theme_stylesheet() -> str:
    """Returns stylesheet rules that make the light theme visible on all platforms."""

    return """
        QWidget {
            background-color: #f5f7fb;
            color: #111827;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {
            background-color: #ffffff;
            color: #111827;
            border: 1px solid #64748b;
            selection-background-color: #1d4ed8;
            selection-color: #ffffff;
        }
        QComboBox, QSpinBox {
            min-height: 24px;
            padding: 3px 6px;
        }
        QComboBox {
            padding-right: 40px;
        }
        QSpinBox {
            padding-right: 36px;
        }
        QComboBox::drop-down {
            background-color: #e2e8f0;
            border-left: 1px solid #94a3b8;
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 32px;
        }
        QComboBox::down-arrow {
            image: none;
            width: 0;
            height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #111827;
        }
        QComboBox QAbstractItemView {
            background-color: #ffffff;
            color: #111827;
            selection-background-color: #1d4ed8;
            selection-color: #ffffff;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            background-color: #e2e8f0;
            border-left: 1px solid #94a3b8;
            subcontrol-origin: border;
            width: 28px;
        }
        QSpinBox::up-button {
            border-bottom: 1px solid #94a3b8;
            subcontrol-position: top right;
            height: 14px;
        }
        QSpinBox::down-button {
            subcontrol-position: bottom right;
            height: 14px;
        }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
            border-color: #1d4ed8;
        }
        QLabel, QCheckBox {
            background-color: transparent;
            color: #111827;
        }
        QCheckBox::indicator {
            background-color: #ffffff;
            border: 1px solid #64748b;
            border-radius: 3px;
            height: 16px;
            width: 16px;
        }
        QCheckBox::indicator:hover {
            border-color: #1d4ed8;
        }
        QCheckBox::indicator:checked {
            background-color: #2563eb;
            border-color: #1d4ed8;
        }
        QSlider::groove:horizontal {
            background-color: #d1d9e6;
            border: 1px solid #94a3b8;
            border-radius: 4px;
            height: 8px;
        }
        QSlider::handle:horizontal {
            background-color: #2563eb;
            border: 1px solid #1e40af;
            border-radius: 7px;
            margin: -4px 0;
            width: 14px;
        }
        QPushButton {
            background-color: #e5e7eb;
            color: #111827;
            border: 1px solid #64748b;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #dbe4ee;
            border-color: #475569;
        }
        QPushButton:default {
            background-color: #2563eb;
            border-color: #1d4ed8;
            color: #ffffff;
        }
        QTabWidget::pane {
            border: 1px solid #94a3b8;
        }
        QTabBar::tab {
            background-color: #e5e7eb;
            color: #111827;
            border: 1px solid #94a3b8;
            padding: 6px 10px;
        }
        QTabBar::tab:selected {
            background-color: #ffffff;
            border-bottom-color: #ffffff;
        }
        QHeaderView::section {
            background-color: #e2e8f0;
            color: #111827;
            border: 1px solid #cbd5e1;
            padding: 4px;
        }
        QTableWidget#calendarGrid QLabel#currentCalendarDay {
            background-color: transparent;
            color: #111827;
            border: 2px solid #1d4ed8;
        }
    """


def _dark_theme_stylesheet() -> str:
    """Returns stylesheet rules that make the dark theme visible on all platforms."""

    return """
        QWidget {
            background-color: #202124;
            color: #f1f3f4;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {
            background-color: #121416;
            color: #f1f3f4;
            border: 1px solid #4b5258;
            selection-background-color: #4c8fcb;
            selection-color: #ffffff;
        }
        QComboBox, QSpinBox {
            min-height: 24px;
            padding: 3px 6px;
        }
        QComboBox {
            padding-right: 40px;
        }
        QSpinBox {
            padding-right: 36px;
        }
        QComboBox::drop-down {
            background-color: #303437;
            border-left: 1px solid #5b6268;
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 32px;
        }
        QComboBox::down-arrow {
            image: none;
            width: 0;
            height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #f1f3f4;
        }
        QComboBox QAbstractItemView {
            background-color: #121416;
            color: #f1f3f4;
            selection-background-color: #4c8fcb;
            selection-color: #ffffff;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            background-color: #303437;
            border-left: 1px solid #5b6268;
            subcontrol-origin: border;
            width: 28px;
        }
        QSpinBox::up-button {
            border-bottom: 1px solid #5b6268;
            subcontrol-position: top right;
            height: 14px;
        }
        QSpinBox::down-button {
            subcontrol-position: bottom right;
            height: 14px;
        }
        QLabel, QCheckBox {
            background-color: transparent;
            color: #f1f3f4;
        }
        QCheckBox::indicator {
            background-color: #121416;
            border: 1px solid #5b6268;
            border-radius: 3px;
            height: 16px;
            width: 16px;
        }
        QCheckBox::indicator:hover {
            border-color: #4c8fcb;
        }
        QCheckBox::indicator:checked {
            background-color: #4c8fcb;
            border-color: #2f6fb0;
        }
        QPushButton {
            background-color: #303437;
            color: #f1f3f4;
            border: 1px solid #5b6268;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #3c4247;
        }
        QTabWidget::pane {
            border: 1px solid #4b5258;
        }
        QTabBar::tab {
            background-color: #303437;
            color: #f1f3f4;
            border: 1px solid #4b5258;
            padding: 6px 10px;
        }
        QTabBar::tab:selected {
            background-color: #202124;
            border-bottom-color: #202124;
        }
        QHeaderView::section {
            background-color: #303437;
            color: #f1f3f4;
            border: 1px solid #4b5258;
            padding: 4px;
        }
        QTableWidget#calendarGrid QLabel#currentCalendarDay {
            background-color: transparent;
            color: #f1f3f4;
            border: 2px solid #6b7280;
        }
    """


def _slider_row(slider: QSlider, value_label: QLabel) -> QWidget:
    """Builds a compact slider row with a fixed-width value label."""

    value_label.setFixedWidth(42)
    row = QHBoxLayout()
    row.addWidget(slider)
    row.addWidget(value_label)

    widget = QWidget()
    widget.setLayout(row)
    return widget


def _spin_pair_row(current_spin: QSpinBox, max_spin: QSpinBox) -> QWidget:
    """Builds a compact current/max spin-box row."""

    row = QHBoxLayout()
    row.addWidget(current_spin)
    row.addWidget(QLabel("/"))
    row.addWidget(max_spin)
    row.addStretch()
    widget = QWidget()
    widget.setLayout(row)
    return widget


def _button_row(*widgets: QWidget) -> QWidget:
    """Builds a compact horizontal control row."""

    row = QHBoxLayout()

    for widget in widgets:
        row.addWidget(widget)

    row.addStretch()

    container = QWidget()
    container.setLayout(row)
    return container


def _invoke_sample_voice_callback(
    callback: SampleVoiceCallback | None,
    voice: str,
    volume: int,
    speed: int,
) -> bool:
    """Calls a sample-voice callback with its complete voice settings."""

    if callback is None:
        return False

    return bool(callback(voice, volume, speed))


def _narrator_voice_options(narration_player: Any | None = None) -> dict[str, str]:
    """Returns known narrator voice display options."""

    if narration_player is not None and hasattr(narration_player, "get_available_voices"):
        try:
            voices = narration_player.get_available_voices()
        except Exception as error:
            LOGGER.warning("Could not read narrator voice options: %s", error)
        else:
            if isinstance(voices, dict) and voices:
                return {
                    str(label): str(voice_id)
                    for label, voice_id in voices.items()
                    if str(label).strip() and str(voice_id).strip()
                }

    return available_narrator_voices()


def _custom_voice_display_text(blend: dict[str, Any]) -> str:
    """Returns a compact custom voice summary."""

    clean_blend = normalize_voice_blend(blend)
    return (
        f"{clean_blend['name']} ({voice_display_name(clean_blend['voice_a'])} "
        f"{clean_blend['voice_a_weight']}% / "
        f"{voice_display_name(clean_blend['voice_b'])} "
        f"{clean_blend['voice_b_weight']}%)"
    )


def _populate_narrator_voice_combo(
    combo: QComboBox,
    selected_voice: Any,
    *,
    voice_options: dict[str, str] | None = None,
) -> None:
    """Fills a narrator voice combo and selects a supported voice id."""

    combo.clear()
    combo.setMinimumWidth(220)
    voices = voice_options or available_narrator_voices()

    for label, voice_id in voices.items():
        combo.addItem(label, voice_id)

    _set_combo_to_data(combo, normalize_narrator_voice(selected_voice))




__all__ = [
    "SoundManagerProtocol",
    "NarrationPlayerProtocol",
    "apply_application_theme",
    "_create_narration_player",
    "_refresh_repository_calendar_time",
    "_apply_audio_settings_to_managers",
    "_resolve_speaker_cues_for_repository",
    "_normalize_theme_name",
    "_application_uses_dark_theme",
    "_next_available_save_title",
    "_split_save_title_suffix",
    "_light_theme_palette",
    "_dark_theme_palette",
    "_light_theme_stylesheet",
    "_dark_theme_stylesheet",
    "_slider_row",
    "_spin_pair_row",
    "_button_row",
    "_invoke_sample_voice_callback",
    "_narrator_voice_options",
    "_custom_voice_display_text",
    "_populate_narrator_voice_combo",
    "_NarrationPlayerClass",
    "_SoundManagerClass",
    "prepare_background_ambience_directory","prepare_sound_directory",
    "prepare_sound_effect_directory"
]
