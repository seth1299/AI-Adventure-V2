"""Application workflow for new-game setup and world generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_adventure.audio.tts_settings import normalize_tts_audio_fields
from ai_adventure.audio.voices import DEFAULT_NARRATOR_VOICE
from ai_adventure.infrastructure.gemini import GeminiNarrationService
from ai_adventure.infrastructure.sqlite import SaveRepository
from ai_adventure.new_game_setup import build_new_game_setup_packet
from ai_adventure.new_game_setup import (
    fallback_introductory_message,
    fallback_world_summary,
    normalize_new_game_setup,
)
from ai_adventure.currency import (
    FALLBACK_CURRENCY_DENOMINATIONS,
    describe_currency_denominations,
)


class NewGameService:
    """Builds provider requests and generates a new world outside the UI."""

    def __init__(
        self,
        *,
        tts_enabled: bool = True,
        audio_defaults: dict[str, Any] | None = None,
    ) -> None:
        self.tts_enabled = bool(tts_enabled)
        self.audio_defaults = dict(audio_defaults or {})

    def normalize_setup(self, setup: dict[str, Any]) -> dict[str, Any]:
        """Normalizes wizard data for durable save creation."""

        raw_setup = dict(setup) if isinstance(setup, dict) else {}
        audio = dict(self.audio_defaults)
        if isinstance(raw_setup.get("audio"), dict):
            audio.update(raw_setup["audio"])
        raw_setup["audio"] = audio
        clean_setup = normalize_new_game_setup(raw_setup)
        if self.tts_enabled:
            return clean_setup
        disabled_audio = dict(clean_setup["audio"])
        disabled_audio.update(
            normalize_tts_audio_fields(disabled_audio, tts_enabled=False)
        )
        disabled_audio["tts_voice"] = DEFAULT_NARRATOR_VOICE
        disabled_audio["tts_voice_mode"] = "preset"
        return {**clean_setup, "audio": disabled_audio}

    @staticmethod
    def create_repository(
        saves_dir: Path,
        setup: dict[str, Any],
        *,
        theme: str | None = None,
    ) -> SaveRepository:
        repository = SaveRepository.create_new_save(
            saves_dir,
            str(setup.get("title", "New Adventure")),
            setup,
        )
        if theme is not None:
            repository.set_setting("theme", theme)
        return repository

    @staticmethod
    def apply_fallback(
        repository: SaveRepository,
        setup: dict[str, Any],
        *,
        temporary_failure: bool = False,
    ) -> None:
        """Applies the safe local opening when generation cannot run."""

        if not setup.get("currency_denominations"):
            repository.set_currency_denominations(FALLBACK_CURRENCY_DENOMINATIONS)
            repository.set_setting(
                "currency.description",
                describe_currency_denominations(
                    FALLBACK_CURRENCY_DENOMINATIONS,
                    fallback_denominations=[],
                ),
            )
        repository.set_world_summary(fallback_world_summary(setup))
        repository.append_history(
            "story",
            (
                "Gemini is temporarily unavailable, so this new game opened "
                "with a local fallback. Your save is safe; try another action "
                "shortly."
                if temporary_failure
                else fallback_introductory_message(setup)
            ),
        )

    @staticmethod
    def apply_fallback_currency(
        repository: SaveRepository,
        setup: dict[str, Any],
    ) -> None:
        """Ensures a save has neutral currency when AI supplied none."""

        if setup.get("currency_denominations"):
            return
        repository.set_currency_denominations(FALLBACK_CURRENCY_DENOMINATIONS)
        repository.set_setting(
            "currency.description",
            describe_currency_denominations(
                FALLBACK_CURRENCY_DENOMINATIONS,
                fallback_denominations=[],
            ),
        )

    @staticmethod
    def build_setup_packet(
        setup: dict[str, Any],
        *,
        valid_music_tracks: list[str] | tuple[str, ...] = (),
        valid_sound_effect_tracks: list[str] | tuple[str, ...] = (),
        valid_background_ambience_tracks: list[str] | tuple[str, ...] = (),
    ) -> dict[str, Any]:
        return build_new_game_setup_packet(
            setup,
            valid_music_tracks=valid_music_tracks,
            valid_sound_effect_tracks=valid_sound_effect_tracks,
            valid_background_ambience_tracks=valid_background_ambience_tracks,
        )

    @staticmethod
    def generate_world(
        setup_packet: dict[str, Any],
        *,
        api_key_path: Path | None = None,
        model: str | None = None,
    ) -> Any:
        service = GeminiNarrationService(
            api_key_path=api_key_path,
            **({"model": model} if model else {}),
        )
        return service.generate_new_game_world(setup_packet)
