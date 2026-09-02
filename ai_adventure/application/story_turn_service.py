"""Application workflow for one story turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_adventure.infrastructure.gemini import GeminiNarrationService
from ai_adventure.audio.tts_settings import active_voice_spec_from_audio, normalize_tts_audio_fields
from ai_adventure.audio.voices import assign_speaker_voices
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.core.state_manager import StateManager
from ai_adventure.infrastructure.sqlite import SaveRepository
from ai_adventure.events.event_applier import AppliedEventResult, EventApplier
from ai_adventure.audio.pronunciation import (
    merge_pronunciation_maps,
    set_authoritative_pronunciation,
)


@dataclass(frozen=True)
class StoryTurnCommitResult:
    """UI-neutral result of persisting one generated narration."""

    message_id: str
    speaker_cues: list[dict[str, str]]
    event_results: list[AppliedEventResult]


class StoryTurnService:
    """Coordinates provider generation and authoritative event application."""

    def __init__(
        self,
        *,
        api_key_path: Path | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key_path = api_key_path
        self.model = model

    @staticmethod
    def build_context_packet(
        repository: SaveRepository,
        player_text: str,
        *,
        conversation_mode: str = "live_game",
        resolved_skill_checks: list[dict[str, Any]] | None = None,
        planner_context_tags: list[str] | None = None,
        sound_manager: Any = None,
    ) -> dict[str, Any]:
        """Builds the complete provider context for a story turn."""

        state = StateManager(repository).load_state()
        relevant_npcs = repository.list_relevant_npcs(
            location=state.world.location,
            query_text=player_text,
        )
        valid_music_tracks = (
            sound_manager.get_valid_track_names()
            if sound_manager is not None
            else []
        )
        valid_sound_effect_tracks = (
            sound_manager.get_valid_sound_effect_names()
            if sound_manager is not None
            else []
        )
        valid_background_ambience_tracks = (
            getattr(
                sound_manager,
                "get_valid_background_ambience_names",
                lambda: [],
            )()
            if sound_manager is not None
            else []
        )
        return AiContextBuilder.from_default_library().build_story_context(
            state,
            player_command=player_text,
            conversation_mode=conversation_mode,
            relevant_npcs=relevant_npcs,
            party_members=repository.list_party_members(),
            gm_secrets=repository.list_gm_secrets(active_only=True),
            miscellaneous=repository.list_miscellaneous(),
            bestiary=repository.list_bestiary_entries(),
            valid_music_tracks=valid_music_tracks,
            current_music=str(repository.get_setting("audio.current_music", "")),
            valid_sound_effect_tracks=valid_sound_effect_tracks,
            valid_background_ambience_tracks=valid_background_ambience_tracks,
            current_background_ambience=str(
                repository.get_setting("audio.current_background_ambience", "")
            ),
            resolved_skill_checks=resolved_skill_checks,
            planner_context_tags=planner_context_tags,
        )

    @staticmethod
    def record_player_action(
        repository: SaveRepository,
        player_text: str,
        *,
        message_id: str,
        conversation_mode: str = "live_game",
    ) -> None:
        """Persists a submitted player action and captures rollback scope."""

        clean_mode = (
            "out_of_game" if conversation_mode == "out_of_game" else "live_game"
        )
        repository.append_history(
            "player_oog" if clean_mode == "out_of_game" else "player",
            player_text,
            message_id=repository.create_message_id(),
        )
        if clean_mode == "live_game":
            repository.capture_message_snapshot(message_id)

    @staticmethod
    def record_failure(
        repository: SaveRepository,
        *,
        message_id: str,
        conversation_mode: str,
        message: str,
    ) -> None:
        """Persists a provider failure without changing game state."""

        clean_mode = (
            "out_of_game" if conversation_mode == "out_of_game" else "live_game"
        )
        repository.append_history(
            "story_oog" if clean_mode == "out_of_game" else "story",
            message,
            message_id=message_id,
        )

    @staticmethod
    def skill_plan_events(plan_result: Any) -> list[dict[str, Any]]:
        """Converts a provider skill plan into event-applier input."""

        return [
            {
                "type": "SkillCheckRequestedEvent",
                "payload": check,
            }
            for check in getattr(plan_result, "checks", [])
            if isinstance(check, dict) and str(check.get("skill_name", "")).strip()
        ]

    def generate_response(self, context_packet: dict[str, Any]) -> Any:
        service = GeminiNarrationService(
            api_key_path=self.api_key_path,
            **({"model": self.model} if self.model else {}),
        )
        return service.generate_story_response(context_packet)

    def plan_skill_checks(self, context_packet: dict[str, Any]) -> Any:
        """Generates the pre-narration skill-check plan."""

        service = GeminiNarrationService(
            api_key_path=self.api_key_path,
            **({"model": self.model} if self.model else {}),
        )
        return service.plan_story_skill_checks(context_packet)

    @staticmethod
    def apply_suggested_events(
        repository: SaveRepository,
        *,
        message_id: str,
        suggested_events: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        prior_results: list[Any] | None = None,
    ) -> list[Any]:
        return EventApplier(repository, message_id=message_id).apply_events(
            list(suggested_events),
            prior_results=prior_results,
        )

    @staticmethod
    def commit_response(
        repository: SaveRepository,
        result: Any,
        *,
        message_id: str | None,
        conversation_mode: str = "live_game",
        prior_event_results: list[AppliedEventResult] | None = None,
        available_voice_ids: list[str] | None = None,
    ) -> StoryTurnCommitResult:
        """Persists narration metadata and applies authorized game events."""

        is_out_of_game = conversation_mode == "out_of_game"
        pronunciation_map = merge_pronunciation_maps(
            repository.get_setting("tts.pronunciation_map", {}),
            getattr(result, "pronunciation_map", {}),
        )
        player_name_pronunciation = repository.get_setting(
            "player.name_pronunciation",
            "",
        )
        if player_name_pronunciation:
            pronunciation_map = set_authoritative_pronunciation(
                pronunciation_map,
                repository.get_setting("player_name", ""),
                player_name_pronunciation,
            )
        repository.set_setting("tts.pronunciation_map", pronunciation_map)

        clean_message_id = message_id or repository.create_message_id()
        speaker_cues: list[dict[str, str]] = []
        if not is_out_of_game:
            audio = normalize_tts_audio_fields(
                {
                    "tts_voice": repository.get_setting("audio.tts_voice", ""),
                    "tts_voice_mode": repository.get_setting(
                        "audio.tts_voice_mode", "preset"
                    ),
                    "tts_voice_blend": repository.get_setting(
                        "audio.tts_voice_blend", {}
                    ),
                }
            )
            speaker_cues, assignments = assign_speaker_voices(
                getattr(result, "speaker_cues", []),
                narrator_voice=active_voice_spec_from_audio(audio),
                available_voice_ids=available_voice_ids or [],
                existing_assignments=repository.get_setting(
                    "audio.speaker_voice_assignments",
                    {},
                ),
            )
            repository.set_setting("audio.speaker_voice_assignments", assignments)

        repository.append_history(
            "story_oog" if is_out_of_game else "story",
            result.narrative_text,
            message_id=clean_message_id,
            sound_effect_cues=result.sound_effect_cues,
            speaker_cues=speaker_cues,
        )

        event_results: list[AppliedEventResult] = []
        if not is_out_of_game and result.suggested_events:
            event_results = StoryTurnService.apply_suggested_events(
                repository,
                message_id=clean_message_id,
                suggested_events=result.suggested_events,
                prior_results=prior_event_results,
            )
        return StoryTurnCommitResult(
            message_id=clean_message_id,
            speaker_cues=speaker_cues,
            event_results=event_results,
        )
