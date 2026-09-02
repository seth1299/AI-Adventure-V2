"""Application service for applying persisted audio preferences."""

from __future__ import annotations

from typing import Any, Protocol

from ai_adventure.audio.tts_settings import (
    DEFAULT_TTS_SPEED_PERCENT,
    active_voice_spec_from_audio,
    normalize_tts_audio_fields,
)
from ai_adventure.audio.voices import DEFAULT_NARRATOR_VOICE
from ai_adventure.infrastructure.sqlite import SaveRepository
from ai_adventure.domain.rules.values import bool_setting, clamped_int


class SoundManager(Protocol):
    def set_music_volume(self, volume: int) -> None: ...
    def set_music_enabled(self, enabled: bool) -> None: ...
    def set_sound_effects_volume(self, volume: int) -> None: ...
    def set_sound_effects_enabled(self, enabled: bool) -> None: ...
    def play_music(self, track_name_or_path: str) -> None: ...
    def stop_music(self, *, clear_current: bool = True) -> None: ...


class NarrationPlayer(Protocol):
    def set_volume(self, volume: int) -> None: ...
    def set_speed(self, speed: int) -> None: ...
    def set_voice(self, voice: str) -> None: ...
    def set_enabled(self, enabled: bool) -> None: ...


class AudioPreferencesService:
    """Reads saved settings and applies them to optional runtime managers."""

    @staticmethod
    def apply(
        repository: SaveRepository,
        *,
        sound_manager: Any = None,
        narration_player: Any = None,
    ) -> None:
        get = repository.get_setting
        music_enabled = bool_setting(get("audio.music_enabled", True), True)
        effects_enabled = bool_setting(get("audio.sound_effects_enabled", True), True)
        music_volume = clamped_int(get("audio.music_volume", 25), 25)
        effects_volume = clamped_int(get("audio.sound_effects_volume", 35), 35)
        ambience_enabled = bool_setting(get("audio.background_ambience_enabled", True), True)
        ambience_volume = clamped_int(get("audio.background_ambience_volume", 15), 15)
        narrator_enabled = bool_setting(get("audio.narrator_enabled", True), True)

        if sound_manager is not None:
            sound_manager.set_music_volume(music_volume)
            sound_manager.set_music_enabled(music_enabled)
            sound_manager.set_sound_effects_volume(effects_volume)
            sound_manager.set_sound_effects_enabled(effects_enabled)
            if hasattr(sound_manager, "set_background_ambience_volume"):
                sound_manager.set_background_ambience_volume(ambience_volume)
            if hasattr(sound_manager, "set_background_ambience_enabled"):
                sound_manager.set_background_ambience_enabled(ambience_enabled)
            current_music = str(get("audio.current_music", "") or "").strip()
            if music_enabled and current_music:
                sound_manager.play_music(current_music)
            else:
                sound_manager.stop_music(clear_current=False)
            if not effects_enabled and hasattr(sound_manager, "stop_sound_effect"):
                sound_manager.stop_sound_effect(clear_current=False)
            current_ambience = str(
                get("audio.current_background_ambience", "") or ""
            ).strip()
            if ambience_enabled and current_ambience and hasattr(
                sound_manager, "play_background_ambience"
            ):
                sound_manager.play_background_ambience(current_ambience)
            elif hasattr(sound_manager, "stop_background_ambience"):
                sound_manager.stop_background_ambience(clear_current=False)

        tts_audio = normalize_tts_audio_fields(
            {
                "narrator_enabled": narrator_enabled,
                "tts_volume": get("audio.tts_volume", 90),
                "tts_voice": get("audio.tts_voice", DEFAULT_NARRATOR_VOICE),
                "tts_speed": get("audio.tts_speed", DEFAULT_TTS_SPEED_PERCENT),
                "tts_voice_mode": get("audio.tts_voice_mode", "preset"),
                "tts_voice_blend": get("audio.tts_voice_blend", {}),
                "tts_custom_voices": get("audio.tts_custom_voices", []),
            }
        )
        if narration_player is not None:
            narration_player.set_volume(int(tts_audio["tts_volume"]))
            narration_player.set_speed(int(tts_audio["tts_speed"]))
            narration_player.set_voice(active_voice_spec_from_audio(tts_audio))
            narration_player.set_enabled(narrator_enabled)
