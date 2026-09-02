"""Canonical infrastructure boundary for audio playback."""

from ai_adventure.audio.narration import NarrationPlayer
from ai_adventure.audio.sound_manager import (
    SoundManager,
    prepare_background_ambience_directory,
    prepare_sound_directory,
    prepare_sound_effect_directory,
)

__all__ = [
    "NarrationPlayer",
    "SoundManager",
    "prepare_background_ambience_directory",
    "prepare_sound_directory",
    "prepare_sound_effect_directory",
]
