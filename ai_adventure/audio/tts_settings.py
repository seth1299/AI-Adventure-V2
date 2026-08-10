from __future__ import annotations

import re
from typing import Any

from ai_adventure.audio.voices import DEFAULT_NARRATOR_VOICE, KOKORO_VOICES, normalize_narrator_voice


MIN_TTS_SPEED_PERCENT = 50
MAX_TTS_SPEED_PERCENT = 200
DEFAULT_TTS_SPEED_PERCENT = 100
DEFAULT_TTS_VOICE_MODE = "preset"
TTS_VOICE_MODES = {"preset", "blend"}
DEFAULT_CUSTOM_VOICE_NAME = "Custom Voice"
DEFAULT_BLEND_VOICE_A = DEFAULT_NARRATOR_VOICE
DEFAULT_BLEND_VOICE_B = "am_echo"
DEFAULT_BLEND_WEIGHT_A = 50
VOICE_BLEND_SPEC_RE = re.compile(
    r"^\s*([a-z]{2}_[a-z0-9_]+)\s*:\s*(\d{1,3})\s*,\s*"
    r"([a-z]{2}_[a-z0-9_]+)\s*:\s*(\d{1,3})\s*$",
    re.IGNORECASE,
)


def normalize_tts_speed_percent(value: Any) -> int:
    """Returns a supported TTS speed percentage."""

    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        parsed_value = DEFAULT_TTS_SPEED_PERCENT

    return max(MIN_TTS_SPEED_PERCENT, min(MAX_TTS_SPEED_PERCENT, parsed_value))


def tts_speed_multiplier(value: Any) -> float:
    """Converts a stored speed percentage to the engine multiplier."""

    return normalize_tts_speed_percent(value) / 100.0


def normalize_tts_voice_mode(value: Any) -> str:
    """Returns a supported voice source mode."""

    clean_value = str(value or "").strip().casefold()
    return clean_value if clean_value in TTS_VOICE_MODES else DEFAULT_TTS_VOICE_MODE


def normalize_voice_blend(raw_blend: Any) -> dict[str, Any]:
    """Returns a normalized two-voice blend definition."""

    if not isinstance(raw_blend, dict):
        raw_blend = {}

    name = str(raw_blend.get("name") or "").strip() or DEFAULT_CUSTOM_VOICE_NAME
    voice_a = normalize_narrator_voice(raw_blend.get("voice_a") or DEFAULT_BLEND_VOICE_A)
    voice_b = normalize_narrator_voice(raw_blend.get("voice_b") or DEFAULT_BLEND_VOICE_B)
    weight_a = _clamped_int(
        raw_blend.get("voice_a_weight", raw_blend.get("weight_a")),
        DEFAULT_BLEND_WEIGHT_A,
        0,
        100,
    )

    return {
        "name": name,
        "voice_a": voice_a,
        "voice_b": voice_b,
        "voice_a_weight": weight_a,
        "voice_b_weight": 100 - weight_a,
        "tts_volume": _clamped_int(raw_blend.get("tts_volume"), 90, 0, 100),
        "tts_speed": normalize_tts_speed_percent(raw_blend.get("tts_speed")),
    }


def normalize_custom_voices(raw_voices: Any) -> list[dict[str, Any]]:
    """Returns saved custom voices, de-duplicated by display name."""

    if not isinstance(raw_voices, list):
        return []

    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for raw_voice in raw_voices:
        voice = normalize_voice_blend(raw_voice)
        key = str(voice["name"]).strip().casefold()

        if not key or key in seen_names:
            continue

        normalized.append(voice)
        seen_names.add(key)

    return normalized


def merge_custom_voices(*raw_voice_lists: Any) -> list[dict[str, Any]]:
    """Returns a combined custom-voice library, preserving first-name wins."""

    merged: list[dict[str, Any]] = []

    for raw_voices in raw_voice_lists:
        merged.extend(normalize_custom_voices(raw_voices))

    return normalize_custom_voices(merged)


def normalize_tts_audio_fields(raw_audio: Any, *, tts_enabled: bool = True) -> dict[str, Any]:
    """Returns normalized narrator settings shared by app, setup, and saves."""

    if not isinstance(raw_audio, dict):
        raw_audio = {}

    narrator_enabled = _safe_bool(raw_audio.get("narrator_enabled"), True)
    tts_volume = _clamped_int(raw_audio.get("tts_volume"), 90, 0, 100)

    if not tts_enabled:
        narrator_enabled = False
        tts_volume = 0

    voice_blend = normalize_voice_blend(raw_audio.get("tts_voice_blend"))
    custom_voices = normalize_custom_voices(raw_audio.get("tts_custom_voices"))
    mode = normalize_tts_voice_mode(raw_audio.get("tts_voice_mode"))

    if parse_voice_blend_spec(raw_audio.get("tts_voice")) is not None:
        mode = "blend"
        voice_blend = normalize_voice_blend(parse_voice_blend_spec(raw_audio.get("tts_voice")))

    return {
        "narrator_enabled": narrator_enabled,
        "tts_volume": tts_volume,
        "tts_voice": normalize_narrator_voice(raw_audio.get("tts_voice")),
        "tts_speed": normalize_tts_speed_percent(raw_audio.get("tts_speed")),
        "tts_voice_mode": mode,
        "tts_voice_blend": voice_blend,
        "tts_custom_voices": custom_voices,
    }


def active_voice_spec_from_audio(audio: Any) -> str:
    """Returns the engine voice id or blend spec selected by audio settings."""

    if not isinstance(audio, dict):
        audio = {}

    if normalize_tts_voice_mode(audio.get("tts_voice_mode")) == "blend":
        return build_voice_blend_spec(normalize_voice_blend(audio.get("tts_voice_blend")))

    return normalize_narrator_voice(audio.get("tts_voice"))


def normalize_narrator_voice_spec(value: Any) -> str:
    """Returns either a supported preset voice id or a normalized blend spec."""

    blend = parse_voice_blend_spec(value)

    if blend is not None:
        return build_voice_blend_spec(blend)

    return normalize_narrator_voice(value)


def build_voice_blend_spec(blend: Any) -> str:
    """Builds the compact voice blend string accepted by TTS engines."""

    clean_blend = normalize_voice_blend(blend)
    return (
        f"{clean_blend['voice_a']}:{clean_blend['voice_a_weight']},"
        f"{clean_blend['voice_b']}:{clean_blend['voice_b_weight']}"
    )


def parse_voice_blend_spec(value: Any) -> dict[str, Any] | None:
    """Parses a compact two-voice blend string."""

    match = VOICE_BLEND_SPEC_RE.match(str(value or ""))

    if match is None:
        return None

    voice_a = normalize_narrator_voice(match.group(1))
    voice_b = normalize_narrator_voice(match.group(3))

    if voice_a not in KOKORO_VOICES.values() or voice_b not in KOKORO_VOICES.values():
        return None

    weight_a = _clamped_int(match.group(2), DEFAULT_BLEND_WEIGHT_A, 0, 100)
    weight_b = _clamped_int(match.group(4), 100 - weight_a, 0, 100)
    total = weight_a + weight_b

    if total <= 0:
        weight_a = DEFAULT_BLEND_WEIGHT_A
    elif total != 100:
        weight_a = round((weight_a / total) * 100)

    return normalize_voice_blend(
        {
            "voice_a": voice_a,
            "voice_b": voice_b,
            "voice_a_weight": weight_a,
        }
    )


def voice_display_name(voice_id: Any) -> str:
    """Returns a readable label for a known voice id."""

    clean_voice_id = normalize_narrator_voice(voice_id)

    for label, known_voice_id in KOKORO_VOICES.items():
        if known_voice_id == clean_voice_id:
            return label

    return clean_voice_id


def _safe_bool(value: Any, default: bool) -> bool:
    """Reads flexible boolean values."""

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().casefold()

        if normalized in {"true", "1", "yes", "on", "enabled"}:
            return True

        if normalized in {"false", "0", "no", "off", "disabled"}:
            return False

    return default


def _clamped_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Returns an integer clamped to an inclusive range."""

    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        parsed_value = default

    return max(minimum, min(maximum, parsed_value))
