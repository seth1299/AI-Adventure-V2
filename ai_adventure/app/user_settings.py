from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ai_adventure.audio.voices import (
    DEFAULT_NARRATOR_VOICE,
)
from ai_adventure.audio.tts_settings import normalize_tts_audio_fields


LOGGER = logging.getLogger(__name__)
THEME_NAMES = {"Light", "Dark"}
DEFAULT_APP_SETTINGS = {
    "theme": "Light",
    "audio": {
        "music_enabled": True,
        "sound_effects_enabled": True,
        "background_ambience_enabled": True,
        "narrator_enabled": True,
        "music_volume": 25,
        "sound_effects_volume": 35,
        "background_ambience_volume": 15,
        "tts_volume": 90,
        "tts_voice": DEFAULT_NARRATOR_VOICE,
        "tts_speed": 100,
        "tts_voice_mode": "preset",
        "tts_voice_blend": {
            "name": "Custom Voice",
            "voice_a": DEFAULT_NARRATOR_VOICE,
            "voice_b": "am_echo",
            "voice_a_weight": 50,
            "voice_b_weight": 50,
        },
        "tts_custom_voices": [],
    },
}


def load_app_settings(
    path: Path,
    *,
    fallback_theme: str = "Light",
    tts_enabled: bool = True,
) -> dict[str, Any]:
    """Loads app-level settings used before a save is active."""

    try:
        raw_settings = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw_settings = {}
    except Exception as error:
        LOGGER.warning("Failed to load app settings from %s: %s", path, error)
        raw_settings = {}

    return normalize_app_settings(
        raw_settings,
        fallback_theme=fallback_theme,
        tts_enabled=tts_enabled,
    )


def save_app_settings(path: Path, settings: dict[str, Any]) -> None:
    """Persists app-level settings."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def normalize_app_settings(
    raw_settings: Any,
    *,
    fallback_theme: str = "Light",
    tts_enabled: bool = True,
) -> dict[str, Any]:
    """Returns a complete app-settings dictionary."""

    if not isinstance(raw_settings, dict):
        raw_settings = {}

    theme = _normalize_theme(raw_settings.get("theme"), fallback_theme=fallback_theme)
    audio = _normalize_audio(raw_settings.get("audio", {}), tts_enabled=tts_enabled)

    return {
        "theme": theme,
        "audio": audio,
    }


def _normalize_theme(value: Any, *, fallback_theme: str) -> str:
    """Returns a supported theme name."""

    clean_value = str(value or "").strip()

    if clean_value in THEME_NAMES:
        return clean_value

    clean_fallback = str(fallback_theme or "").strip()
    return clean_fallback if clean_fallback in THEME_NAMES else "Light"


def _normalize_audio(raw_audio: Any, *, tts_enabled: bool) -> dict[str, Any]:
    """Returns normalized app-level audio defaults."""

    if not isinstance(raw_audio, dict):
        raw_audio = {}

    return {
        "music_enabled": _safe_bool(raw_audio.get("music_enabled"), True),
        "music_volume": _clamped_int(raw_audio.get("music_volume"), 25, 0, 100),
        "sound_effects_enabled": _safe_bool(
            raw_audio.get("sound_effects_enabled"), True
        ),
        "sound_effects_volume": _clamped_int(
            raw_audio.get("sound_effects_volume"), 35, 0, 100
        ),
        "background_ambience_enabled": _safe_bool(
            raw_audio.get("background_ambience_enabled"), True
        ),
        "background_ambience_volume": _clamped_int(
            raw_audio.get("background_ambience_volume"), 15, 0, 100
        ),
        **normalize_tts_audio_fields(raw_audio, tts_enabled=tts_enabled),
    }


def _safe_bool(value: Any, default: bool) -> bool:
    """Reads a flexible boolean value."""

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().casefold()

        if normalized in {"true", "1", "yes", "on"}:
            return True

        if normalized in {"false", "0", "no", "off"}:
            return False

    return default


def _clamped_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Returns an integer clamped to the provided range."""

    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        parsed_value = default

    return max(minimum, min(maximum, parsed_value))
