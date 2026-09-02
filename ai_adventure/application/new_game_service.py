"""Application workflow for new-game setup and world generation."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any

from ai_adventure.alchemy.ingredients import (
    is_crafting_ingredient_category,
    normalize_recipe_ingredients,
)
from ai_adventure.audio.tts_settings import normalize_tts_audio_fields
from ai_adventure.audio.tts_settings import active_voice_spec_from_audio
from ai_adventure.audio.voices import DEFAULT_NARRATOR_VOICE
from ai_adventure.audio.voices import assign_speaker_voices
from ai_adventure.audio.pronunciation import (
    merge_pronunciation_maps,
    set_authoritative_pronunciation,
)
from ai_adventure.calendar_system import (
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    resolve_starting_calendar_minute,
)
from ai_adventure.infrastructure.gemini import GeminiNarrationService
from ai_adventure.infrastructure.sqlite import SaveRepository
from ai_adventure.locations import normalize_known_locations
from ai_adventure.new_game_setup import build_new_game_setup_packet
from ai_adventure.new_game_setup import (
    STARTER_INVENTORY_MIN_ITEMS,
    ai_generated_calendar_settings_or_fallback,
    fallback_introductory_message,
    fallback_world_summary,
    merge_authoritative_starting_calendar,
    normalize_new_game_setup,
)
from ai_adventure.currency import (
    FALLBACK_CURRENCY_DENOMINATIONS,
    describe_currency_denominations,
)
from ai_adventure.domain.rules.values import safe_int
from ai_adventure.events.event_applier import AppliedEventResult, EventApplier


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewGameCommitResult:
    """UI-neutral result of committing one generated world."""

    message_id: str
    speaker_cues: list[dict[str, str]]
    event_results: list[AppliedEventResult]


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
            valid_music_tracks=list(valid_music_tracks),
            valid_sound_effect_tracks=list(valid_sound_effect_tracks),
            valid_background_ambience_tracks=list(valid_background_ambience_tracks),
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
            **({"model": model} if model else {}),  # type: ignore[call-arg]
        )
        return service.generate_new_game_world(setup_packet)

    @staticmethod
    def commit_generated_world(
        repository: SaveRepository,
        setup: dict[str, Any],
        result: Any,
        *,
        available_voice_ids: list[str] | None = None,
    ) -> NewGameCommitResult:
        """Persists a generated world and applies its authorized opening events."""

        LOGGER.debug("INITIAL NEW GAME GEMINI PROMPT: \n\n%s", result)
        NewGameService._apply_generated_state(repository, setup, result)

        finalized_character = getattr(result, "finalized_character", {})
        repository.set_world_summary(
            _preserve_player_character_text(
                getattr(result, "world_summary", ""),
                setup,
                finalized_character,
            )
        )
        introductory_message = _preserve_player_character_text(
            _introductory_message_for_save(setup, result),
            setup,
            finalized_character,
        )

        audio = normalize_tts_audio_fields(
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
        speaker_cues, assignments = assign_speaker_voices(
            getattr(result, "speaker_cues", []),
            narrator_voice=active_voice_spec_from_audio(audio),
            available_voice_ids=available_voice_ids or [],
            existing_assignments=repository.get_setting(
                "audio.speaker_voice_assignments", {}
            ),
        )
        repository.set_setting("audio.speaker_voice_assignments", assignments)

        message_id = repository.append_history(
            "story",
            introductory_message,
            sound_effect_cues=getattr(result, "sound_effect_cues", []),
            speaker_cues=speaker_cues,
        )
        event_results: list[AppliedEventResult] = []
        suggested_events = getattr(result, "suggested_events", [])
        if suggested_events:
            event_results = EventApplier(
                repository,
                message_id=message_id,
            ).apply_events(suggested_events)
            applied_count = sum(
                1 for event_result in event_results
                if event_result.status == "applied"
            )
            LOGGER.info(
                "Applied %s new-game event(s); skipped %s.",
                applied_count,
                len(event_results) - applied_count,
            )

        return NewGameCommitResult(
            message_id=message_id,
            speaker_cues=speaker_cues,
            event_results=event_results,
        )

    @staticmethod
    def _apply_generated_state(
        repository: SaveRepository,
        setup: dict[str, Any],
        result: Any,
    ) -> None:
        """Persists AI-finalized setup state before the opening narration."""

        start_location = _final_start_location_for_save(setup, result)
        if start_location:
            repository.set_state_value("location", start_location)

        finalized_location_aliases: dict[str, str] = {}
        result_locations = getattr(result, "locations", [])
        if result_locations:
            travel_locations = _travel_locations_for_save(
                result_locations,
                setup,
                result,
            )
            finalized_location_aliases = _finalized_location_aliases(
                travel_locations,
                setup,
            )
            repository.set_travel_locations(
                _replace_location_aliases_in_travel_locations(
                    travel_locations,
                    finalized_location_aliases,
                )
            )
        repository.ensure_travel_locations()

        for secret in getattr(result, "gm_secrets", []):
            repository.upsert_gm_secret(
                secret_id=str(secret.get("secret_id", "")),
                title=str(secret.get("title", "")),
                details=str(secret.get("details", "")),
                reveal_condition=str(secret.get("reveal_condition", "")),
                related_npc_ids=list(secret.get("related_npc_ids", [])),
                related_locations=list(secret.get("related_locations", [])),
                status=str(secret.get("status", "active")),
            )

        for entry in getattr(result, "miscellaneous", []):
            repository.upsert_miscellaneous(
                misc_id=str(entry.get("misc_id", "")),
                name=str(entry.get("name", "")),
                category=str(entry.get("category", "")),
                details=str(entry.get("details", "")),
            )

        for entry in getattr(result, "bestiary", []):
            repository.upsert_bestiary_entry(
                creature_id=str(entry.get("creature_id", "")),
                name=str(entry.get("name", "")),
                details=str(entry.get("details", "")),
            )

        setup_calendar = setup.get("calendar", {})
        if isinstance(setup_calendar, dict) and bool(
            setup_calendar.get("ai_generated", False)
        ):
            repository.set_calendar_settings(
                ai_generated_calendar_settings_or_fallback(
                    getattr(result, "calendar_settings", {}),
                    genre_hint=_new_game_calendar_genre_hint(setup, result),
                )
            )

        result_starting_calendar = merge_authoritative_starting_calendar(
            getattr(result, "starting_calendar", {}),
            setup.get("starting_calendar", {}),
        )
        if result_starting_calendar:
            current_minute = resolve_starting_calendar_minute(
                result_starting_calendar,
                repository.get_calendar_settings(),
                default_current_minute=DEFAULT_START_ELAPSED_MINUTES,
            )
            calendar_snapshot = build_calendar_snapshot(
                current_minute,
                repository.get_calendar_settings(),
            )
            repository.set_current_calendar_minute(current_minute)
            repository.set_state_value("time", calendar_snapshot["display_label"])

        authoritative_starting_weather = str(
            setup.get("starting_weather", "") or ""
        ).strip()
        result_start_weather = str(
            getattr(result, "start_weather", "") or ""
        ).strip()
        if authoritative_starting_weather:
            repository.set_state_value("weather", authoritative_starting_weather)
        elif result_start_weather:
            repository.set_state_value("weather", result_start_weather)

        starting_wealth = setup.get("starting_wealth", {})
        starting_wealth_mode = (
            str(starting_wealth.get("mode", "basic")).strip().casefold()
            if isinstance(starting_wealth, dict)
            else "basic"
        )
        balance = getattr(
            result,
            "finalized_starting_currency_balance_base_units",
            None,
        )
        if starting_wealth_mode == "basic" and balance is not None:
            repository.set_state_value("currency.balance", str(balance))

        finalized_denominations = getattr(
            result, "finalized_currency_denominations", []
        )
        if not setup.get("currency_denominations"):
            if finalized_denominations:
                repository.set_currency_denominations(finalized_denominations)
                repository.set_setting(
                    "currency.description",
                    getattr(result, "finalized_currency_description", "")
                    or describe_currency_denominations(
                        finalized_denominations,
                        fallback_denominations=[],
                    ),
                )
            else:
                LOGGER.warning(
                    "AI new-game setup omitted generated currency denominations."
                )
                NewGameService.apply_fallback_currency(repository, setup)

        selected_genre = str(getattr(result, "selected_genre", "") or "").strip()
        if selected_genre:
            repository.set_setting("world.genre", selected_genre)
            repository.set_setting(
                "ai.additional_context",
                _append_ai_context_line(
                    str(repository.get_setting("ai.additional_context", "")),
                    f"Selected genre: {selected_genre}",
                ),
            )

        character = _preserved_player_character_fields(
            setup,
            getattr(result, "finalized_character", {}),
        )
        character_setting_map = {
            "name": "player_name",
            "name_pronunciation": "player.name_pronunciation",
            "pronouns": "player.pronouns",
            "appearance": "player.appearance",
            "backstory": "player.backstory",
            "notes": "player.notes",
        }
        for field_name, setting_key in character_setting_map.items():
            value = character.get(field_name, "")
            if value:
                repository.set_setting(setting_key, value)

        pronunciation_map = merge_pronunciation_maps(
            setup.get("pronunciation_map", {}),
            getattr(result, "pronunciation_map", {}),
        )
        setup_character = setup.get("character", {})
        if isinstance(setup_character, dict) and setup_character.get(
            "name_pronunciation"
        ):
            pronunciation_map = set_authoritative_pronunciation(
                pronunciation_map,
                setup_character.get("name", ""),
                setup_character.get("name_pronunciation", ""),
            )
        repository.set_setting("tts.pronunciation_map", pronunciation_map)

        finalized_skills = (
            []
            if setup.get("skill_preset") == "blank"
            else _finalized_skills_for_save(
                getattr(result, "finalized_skills", []),
                setup.get("skills", []),
            )
        )
        if finalized_skills:
            repository.replace_skills(finalized_skills)
        elif getattr(result, "finalized_skills", []):
            LOGGER.warning(
                "Skipped AI-finalized skills because they did not match the "
                "starting skill plan."
            )

        magic_setup = setup.get("magic", {})
        if not isinstance(magic_setup, dict):
            magic_setup = {}
        starting_spell_requests = magic_setup.get("starting_spell_requests", [])
        if not isinstance(starting_spell_requests, list):
            starting_spell_requests = []
        if (
            bool(magic_setup.get("enabled", False))
            and str(magic_setup.get("starting_spells_mode", "basic")).casefold()
            == "basic"
            and starting_spell_requests
        ):
            finalized_starting_spells = [
                spell
                for spell in getattr(result, "finalized_starting_spells", [])
                if isinstance(spell, dict)
                and 0 <= safe_int(spell.get("source_index"), -1)
                < len(starting_spell_requests)
            ]
            learned_spells = repository.learn_starting_spells(
                finalized_starting_spells,
                source="Gemini New Game",
            )
            if len(learned_spells) != len(starting_spell_requests):
                LOGGER.warning(
                    "Gemini finalized %s of %s requested starting spell(s).",
                    len(learned_spells),
                    len(starting_spell_requests),
                )

        finalized_starter_items = _starter_items_for_save(
            getattr(result, "finalized_starter_items", []),
            setup,
        )
        if finalized_starter_items:
            repository.replace_inventory_items(finalized_starter_items)

        _apply_new_game_crafting_knowledge(
            repository,
            result,
            location_aliases=finalized_location_aliases,
        )


def _safe_int(value: Any, default: int) -> int:
    """Returns a domain-safe integer for provider data."""

    return safe_int(value, default)


def _optional_int(value: Any) -> int | None:
    """Parses an optional integer."""

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _final_start_location_for_save(setup: dict[str, Any], result: Any) -> str:
    """Returns the location that should be persisted as the current scene."""

    requested_location = str(setup.get("start_location", "") or "").strip()
    if (
        str(setup.get("start_location_mode", "suggestion")).casefold() == "exact"
        and requested_location
    ):
        return requested_location
    return str(getattr(result, "start_location", "") or "").strip()


def _introductory_message_for_save(setup: dict[str, Any], result: Any) -> str:
    """Returns opening narration corrected for exact start-location requests."""

    message = str(getattr(result, "introductory_message", "") or "")
    requested_location = str(setup.get("start_location", "") or "").strip()
    ai_location = str(getattr(result, "start_location", "") or "").strip()
    if (
        str(setup.get("start_location_mode", "suggestion")).casefold() == "exact"
        and requested_location
        and ai_location
        and ai_location.casefold() != requested_location.casefold()
    ):
        return message.replace(ai_location, requested_location)
    return message


def _travel_locations_for_save(
    raw_locations: Any,
    setup: dict[str, Any],
    result: Any,
) -> list[dict[str, Any]]:
    """Merges AI locations with every structured player-requested location."""

    locations = [
        location.to_dict() for location in normalize_known_locations(raw_locations)
    ]
    source_indexes_by_name: dict[str, int] = {}
    if isinstance(raw_locations, list):
        for raw_location in raw_locations:
            if not isinstance(raw_location, dict):
                continue
            name = str(raw_location.get("name", "") or "").strip().casefold()
            if name:
                source_indexes_by_name[name] = _safe_int(
                    raw_location.get("source_index", -1), -1
                )
    for location in locations:
        location["source_index"] = source_indexes_by_name.get(
            str(location.get("name", "")).casefold(), -1
        )

    requested_locations = setup.get("starting_locations", [])
    if isinstance(requested_locations, list):
        for source_index, raw_requested_location in enumerate(requested_locations):
            if not isinstance(raw_requested_location, dict):
                continue
            requested_name = str(raw_requested_location.get("name", "") or "").strip()
            requested_description = str(
                raw_requested_location.get("description", "") or ""
            ).strip()
            mode = str(
                raw_requested_location.get("location_mode", "suggestion")
                or "suggestion"
            ).casefold()
            matched_location = next(
                (
                    location
                    for location in locations
                    if _safe_int(location.get("source_index", -1), -1)
                    == source_index
                ),
                None,
            )
            if matched_location is None and requested_name:
                matched_location = next(
                    (
                        location
                        for location in locations
                        if str(location.get("name", "")).strip().casefold()
                        == requested_name.casefold()
                    ),
                    None,
                )
            if matched_location is None:
                if not requested_name:
                    continue
                if mode != "exact":
                    LOGGER.warning(
                        "Gemini omitted suggested location %r; not persisting its "
                        "unfinalized placeholder name.",
                        requested_name,
                    )
                    continue
                matched_location = {
                    "name": requested_name,
                    "description": requested_description,
                    "x_miles": None,
                    "y_miles": None,
                    "terrain": "",
                    "travel_multiplier": 1.0,
                    "travel_notes": "",
                    "source_index": source_index,
                }
                locations.append(matched_location)

            if mode == "exact":
                if requested_name:
                    matched_location["name"] = requested_name
                matched_location["description"] = requested_description
            elif requested_description and str(
                matched_location.get("description", "")
            ).strip().casefold() in {"", "starting location."}:
                matched_location["description"] = requested_description

            parent_location = str(
                raw_requested_location.get("parent_location", "") or ""
            ).strip()
            if bool(raw_requested_location.get("is_sublocation")) and parent_location:
                relationship_note = f"Located within {parent_location}."
                existing_notes = str(
                    matched_location.get("travel_notes", "") or ""
                ).strip()
                if (
                    "located within " not in existing_notes.casefold()
                    and relationship_note.casefold() not in existing_notes.casefold()
                ):
                    matched_location["travel_notes"] = " ".join(
                        value for value in [existing_notes, relationship_note] if value
                    )

    requested_location = str(setup.get("start_location", "") or "").strip()
    ai_location = str(getattr(result, "start_location", "") or "").strip()
    if (
        str(setup.get("start_location_mode", "suggestion")).casefold() != "exact"
        or not requested_location
    ):
        return locations

    for location in locations:
        name = str(location.get("name", "") or "").strip()
        is_ai_start = bool(ai_location) and name.casefold() == ai_location.casefold()
        is_origin = (
            _coerce_float(location.get("x_miles")) == 0.0
            and _coerce_float(location.get("y_miles")) == 0.0
        )
        if is_ai_start or is_origin:
            location["name"] = requested_location
            if not str(location.get("description", "") or "").strip():
                location["description"] = "Starting location."
            location["x_miles"] = 0.0
            location["y_miles"] = 0.0
            return locations

    return [
        {
            "name": requested_location,
            "description": "Starting location.",
            "x_miles": 0.0,
            "y_miles": 0.0,
            "terrain": "",
            "travel_multiplier": 1.0,
            "travel_notes": "",
        },
        *locations,
    ]


def _finalized_location_aliases(
    locations: list[dict[str, Any]], setup: dict[str, Any]
) -> dict[str, str]:
    """Maps wizard suggestion names to their finalized AI location names."""

    requested_locations = setup.get("starting_locations", [])
    if not isinstance(requested_locations, list):
        return {}
    finalized_names_by_source_index = {
        _safe_int(location.get("source_index", -1), -1): str(
            location.get("name", "") or ""
        ).strip()
        for location in locations
        if _safe_int(location.get("source_index", -1), -1) >= 0
        and str(location.get("name", "") or "").strip()
    }
    aliases: dict[str, str] = {}
    for source_index, raw_requested_location in enumerate(requested_locations):
        if not isinstance(raw_requested_location, dict):
            continue
        requested_name = str(raw_requested_location.get("name", "") or "").strip()
        finalized_name = finalized_names_by_source_index.get(source_index, "")
        if (
            requested_name
            and finalized_name
            and requested_name.casefold() != finalized_name.casefold()
        ):
            aliases[requested_name] = finalized_name
    return aliases


def _replace_location_aliases(text: Any, aliases: dict[str, str]) -> str:
    """Reconciles free-text setup location references with finalized names."""

    clean_text = str(text or "")
    for old_name, finalized_name in sorted(
        aliases.items(), key=lambda item: len(item[0]), reverse=True
    ):
        clean_text = re.sub(
            rf"(?<!\w){re.escape(old_name)}(?!\w)",
            lambda _match, replacement=finalized_name: replacement,
            clean_text,
            flags=re.IGNORECASE,
        )
    return clean_text


def _replace_location_aliases_in_travel_locations(
    locations: list[dict[str, Any]], aliases: dict[str, str]
) -> list[dict[str, Any]]:
    """Updates player-facing location prose after suggestion names are finalized."""

    if not aliases:
        return locations
    for location in locations:
        for field_name in ("description", "travel_notes"):
            location[field_name] = _replace_location_aliases(
                location.get(field_name, ""), aliases
            )
    return locations


def _apply_new_game_crafting_knowledge(
    repository: SaveRepository,
    result: Any,
    *,
    location_aliases: dict[str, str] | None = None,
) -> None:
    """Persists AI-finalized starting Crafting tab knowledge."""

    for raw_item in getattr(result, "known_crafting_items", []):
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("name", "") or "").strip()
        if not name:
            continue
        description = str(raw_item.get("description", "") or "").strip()
        category = str(raw_item.get("category", "Material") or "Material").strip()
        if not is_crafting_ingredient_category(category):
            category = "Material"
        uses = (
            [str(value).strip() for value in raw_item.get("uses", []) if str(value).strip()]
            if isinstance(raw_item.get("uses"), list)
            else []
        )
        location = str(raw_item.get("location", "") or "").strip()
        if location_aliases:
            location = _replace_location_aliases(location, location_aliases)
        repository.add_crafting_item(
            name=name,
            category=category,
            description=description,
            location=location,
            uses=uses,
            rarity=str(raw_item.get("rarity", "Common") or "Common"),
            notes=str(raw_item.get("notes", "") or "").strip(),
            value_base_units=max(0, _safe_int(raw_item.get("value_base_units", 0), 0)),
            item_uuid=str(raw_item.get("item_uuid", "") or "").strip(),
        )
        repository.upsert_item_catalog_entry(
            name=name,
            category=category,
            description=description,
            value_base_units=max(0, _safe_int(raw_item.get("value_base_units", 0), 0)),
            metadata={"item_uuid": raw_item.get("item_uuid", "")},
        )

    allowed_ingredient_names = {
        str(item.get("name", "") or "").casefold()
        for item in repository.list_item_catalog()
        if str(item.get("name", "") or "").strip()
        and is_crafting_ingredient_category(item.get("category", ""))
    }
    for raw_recipe in getattr(result, "known_crafting_recipes", []):
        if not isinstance(raw_recipe, dict):
            continue
        name = str(raw_recipe.get("name", "") or "").strip()
        ingredients = normalize_recipe_ingredients(raw_recipe.get("ingredients", []))
        result_text = str(raw_recipe.get("result", "") or "").strip()
        if not name or not ingredients or not result_text:
            continue
        unknown_ingredients = [
            ingredient["reagent_name"]
            for ingredient in ingredients
            if ingredient["reagent_name"].casefold() not in allowed_ingredient_names
        ]
        if unknown_ingredients:
            LOGGER.warning(
                "Skipped new-game crafting recipe %s because ingredient knowledge is missing: %s",
                name,
                ", ".join(unknown_ingredients),
            )
            continue
        repository.add_crafting_recipe(
            name=name,
            ingredients=ingredients,
            result=result_text,
            notes=str(raw_recipe.get("notes", "") or "").strip(),
            value_base_units=max(0, _safe_int(raw_recipe.get("value_base_units", 0), 0)),
        )


def _new_game_calendar_genre_hint(setup: dict[str, Any], result: Any) -> str:
    """Combines setup and AI-selected genre text for calendar fallback checks."""

    parts = [
        str(setup.get("specified_genre", "") or ""),
        str(setup.get("game_style", "") or ""),
        str(setup.get("world_context", "") or ""),
        str(setup.get("ai_additional_context", "") or ""),
        str(getattr(result, "selected_genre", "") or ""),
    ]
    return "\n".join(part for part in parts if part.strip())


def _finalized_skills_for_save(
    ai_skills: list[dict[str, Any]], setup_skills: Any
) -> list[dict[str, Any]]:
    """Merges AI skill descriptions while preserving player-provided skill names."""

    if not isinstance(setup_skills, list) or not setup_skills:
        return _deduplicated_ai_skills(ai_skills) if ai_skills else []
    merged_skills: list[dict[str, Any]] = []
    ai_by_name = {
        str(skill.get("name", "")).strip().casefold(): skill
        for skill in ai_skills
        if isinstance(skill, dict)
    }
    for index, raw_setup_skill in enumerate(setup_skills):
        if not isinstance(raw_setup_skill, dict):
            raw_setup_skill = {"name": str(raw_setup_skill)}
        setup_name = str(raw_setup_skill.get("name", "") or "").strip()
        if not setup_name:
            ai_skill = ai_skills[index] if index < len(ai_skills) else {}
            merged_skills.append(dict(ai_skill) if isinstance(ai_skill, dict) else {})
            continue
        ai_skill = ai_by_name.get(setup_name.casefold())
        if ai_skill is None and index < len(ai_skills) and isinstance(ai_skills[index], dict):
            ai_skill = ai_skills[index]
        if ai_skill is None:
            ai_skill = {}
        description = str(
            ai_skill.get("description")
            or raw_setup_skill.get("description")
            or f"Player-selected {setup_name} skill."
        ).strip()
        merged_skills.append(
            {
                **dict(ai_skill),
                "name": setup_name,
                "description": description,
                "level": _safe_int(
                    raw_setup_skill.get("level"),
                    _safe_int(ai_skill.get("level"), 1),
                ),
            }
        )
    return _deduplicated_ai_skills(
        [skill for skill in merged_skills if str(skill.get("name", "")).strip()]
    )


def _coerce_float(value: Any) -> float | None:
    """Returns a float or None when the value is not numeric."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _deduplicated_ai_skills(ai_skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns AI-finalized skills with unique names suitable for persistence."""

    deduplicated_skills: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for raw_skill in ai_skills:
        skill = dict(raw_skill)
        name = str(skill.get("name", "")).strip()
        if name.casefold() in seen_names:
            try:
                level = int(skill.get("level", 0))
            except (TypeError, ValueError):
                level = 0
            name = _unique_ai_skill_name(
                name,
                seen_names,
                suffix=_duplicate_skill_suffix(level),
            )
            skill["name"] = name
            LOGGER.info("Renamed duplicate AI-finalized skill to %s.", name)
        seen_names.add(name.casefold())
        deduplicated_skills.append(skill)
    return deduplicated_skills


def _unique_ai_skill_name(base_name: str, seen_names: set[str], *, suffix: str) -> str:
    """Builds a unique generated skill name from an AI duplicate."""

    clean_base = base_name.strip() or "Generated Skill"
    clean_suffix = suffix.strip() or "Alternate"
    candidate = f"{clean_base} ({clean_suffix})"
    if candidate.casefold() not in seen_names:
        return candidate
    index = 2
    while True:
        candidate = f"{clean_base} ({clean_suffix} {index})"
        if candidate.casefold() not in seen_names:
            return candidate
        index += 1


def _duplicate_skill_suffix(level: int) -> str:
    """Returns a compact descriptor for duplicate generated skill names."""

    return {
        5: "Signature",
        4: "Expert",
        3: "Skilled",
        2: "Trained",
        1: "Familiar",
    }.get(level, "Alternate")


def _starter_items_for_save(
    ai_items: list[dict[str, Any]], setup: dict[str, Any]
) -> list[dict[str, Any]]:
    """Returns AI starter items while preserving explicit named setup items."""

    setup_items = setup.get("starter_items", [])
    if not isinstance(setup_items, list):
        setup_items = []
    completed_items = [dict(item) for item in ai_items if isinstance(item, dict)]
    for item in completed_items:
        source_index = _optional_int(item.get("source_index"))
        if source_index is None or not (0 <= source_index < len(setup_items)):
            continue
        setup_item = setup_items[source_index]
        if not isinstance(setup_item, dict) or bool(
            setup_item.get("requires_ai_invention")
        ):
            continue
        item["storage_location"] = (
            " ".join(
                str(setup_item.get("storage_location", "actively_carried") or "actively_carried")
                .strip()
                .split()
            )[:120]
            or "actively_carried"
        )

    original_completed_count = len(completed_items)
    used_source_indexes = {
        source_index
        for source_index in (
            _optional_int(item.get("source_index")) for item in completed_items
        )
        if source_index is not None and source_index >= 0
    }
    seen_names = {
        str(item.get("name", "")).strip().casefold()
        for item in completed_items
        if str(item.get("name", "")).strip()
    }
    for index, setup_item in enumerate(setup_items):
        if index in used_source_indexes or not isinstance(setup_item, dict):
            continue
        if bool(setup_item.get("requires_ai_invention")) and len(
            completed_items
        ) >= STARTER_INVENTORY_MIN_ITEMS:
            continue
        fallback_item = _fallback_starter_item_from_setup(
            setup_item, source_index=index
        )
        if not fallback_item or fallback_item["name"].casefold() in seen_names:
            continue
        completed_items.append(fallback_item)
        seen_names.add(fallback_item["name"].casefold())

    while len(completed_items) < STARTER_INVENTORY_MIN_ITEMS:
        fallback_item = _starter_inventory_top_up_item(seen_names)
        if fallback_item is None:
            break
        completed_items.append(fallback_item)
        seen_names.add(fallback_item["name"].casefold())

    if len(completed_items) > original_completed_count:
        added_count = len(completed_items) - original_completed_count
        if original_completed_count < STARTER_INVENTORY_MIN_ITEMS:
            LOGGER.warning(
                "Gemini returned fewer than %s complete starter item(s); added %s "
                "fallback item(s) so the new save starts with enough inventory.",
                STARTER_INVENTORY_MIN_ITEMS,
                added_count,
            )
        else:
            LOGGER.warning(
                "Gemini omitted %s explicit starter item(s); preserved named setup item(s).",
                added_count,
            )
    return completed_items


def _fallback_starter_item_from_setup(
    raw_item: Any, *, source_index: int = -1
) -> dict[str, Any] | None:
    """Builds a structured starter item from a wizard entry when AI is partial."""

    if not isinstance(raw_item, dict):
        return None
    name = str(raw_item.get("name", "")).strip()
    item_request = str(raw_item.get("item_request", "")).strip()
    description = str(raw_item.get("description", "")).strip()
    if not name:
        name = _starter_item_name_from_request(item_request)
    if not name:
        return None
    item = {
        "name": name,
        "category": str(raw_item.get("category", "Item")).strip() or "Item",
        "quantity": max(1, _safe_int(raw_item.get("quantity"), 1)),
        "description": description
        or item_request
        or "Player-requested starter item awaiting AI detail.",
        "value_base_units": max(0, _safe_int(raw_item.get("value_base_units"), 0)),
        "storage_location": (
            " ".join(
                str(raw_item.get("storage_location", "actively_carried") or "actively_carried")
                .strip()
                .split()
            )[:120]
            or "actively_carried"
        ),
        "source_index": source_index,
    }
    for field_name in (
        "item_type",
        "weapon_hands",
        "damage",
        "damage_type",
        "attack_skill",
        "attack_range_feet",
        "ammunition_type_required",
        "clip_size",
        "bullets_per_attack",
        "ammunition_type",
        "covers_body_parts",
        "armor_rating",
    ):
        if field_name in raw_item:
            item[field_name] = raw_item[field_name]
    return item


def _starter_inventory_top_up_item(
    seen_names: set[str],
) -> dict[str, Any] | None:
    """Returns a neutral fallback item for short AI starter inventories."""

    fallback_items = [
        ("Personal Pack", "Container", "A sturdy pack for keeping essential belongings close.", 5),
        ("Packed Meal", "Supply", "Simple food set aside for the first stretch of travel.", 2),
        ("Water Flask", "Supply", "A refillable flask of clean drinking water.", 2),
        ("Utility Tool", "Tool", "A compact everyday tool for small repairs and practical tasks.", 4),
        ("Weather-Ready Clothes", "Clothing", "Durable clothing suitable for uncertain conditions.", 6),
        ("Personal Keepsake", "Personal", "A small memento connecting the character to their past.", 1),
    ]
    for name, category, description, value_base_units in fallback_items:
        if name.casefold() in seen_names:
            continue
        return {
            "name": name,
            "category": category,
            "description": description,
            "value_base_units": value_base_units,
            "quantity": 1,
            "source_index": -1,
        }
    return None


def _starter_item_name_from_request(item_request: str) -> str:
    """Derives a compact item name from a natural-language item request."""

    candidate = str(item_request or "").strip()
    if not candidate:
        return ""
    for separator in [
        " that ", " which ", " with ", " used ", " for ", ".", ",", ";", ":"
    ]:
        before_separator = candidate.split(separator, 1)[0].strip()
        if before_separator:
            candidate = before_separator
    words = [
        word.strip("'\"()[]{}")
        for word in candidate.split()
        if word.strip("'\"()[]{}")
    ]
    while words and words[0].casefold() in {
        "a", "an", "the", "my", "his", "her", "their", "our"
    }:
        words.pop(0)
    return " ".join(words[:5]).title() if words else ""


def _preserved_player_character_fields(
    setup: dict[str, Any], ai_character: Any
) -> dict[str, str]:
    """Returns character fields while preserving explicit player setup values."""

    clean_setup = normalize_new_game_setup(setup)
    setup_character = clean_setup["character"]
    ai_character = ai_character if isinstance(ai_character, dict) else {}
    preserved: dict[str, str] = {}
    for key in (
        "name",
        "name_pronunciation",
        "pronouns",
        "appearance",
        "backstory",
        "notes",
    ):
        setup_value = str(setup_character.get(key, "")).strip()
        ai_value = str(ai_character.get(key, "")).strip()
        if _is_player_provided_character_field(key, setup_value):
            preserved[key] = setup_value
        elif ai_value:
            preserved[key] = ai_value
        elif setup_value:
            preserved[key] = setup_value
    return preserved


def _preserve_player_character_text(
    value: Any, setup: dict[str, Any], ai_character: Any
) -> Any:
    """Repairs generated text that renamed an explicitly supplied character."""

    replacements = _player_character_name_replacements(setup, ai_character)
    if not replacements:
        return value
    if isinstance(value, str):
        clean_value = value
        for source, target in replacements:
            clean_value = _replace_whole_name(clean_value, source, target)
        return clean_value
    if isinstance(value, list):
        return [_preserve_player_character_text(item, setup, ai_character) for item in value]
    if isinstance(value, dict):
        return {
            _preserve_player_character_text(key, setup, ai_character)
            if isinstance(key, str)
            else key: _preserve_player_character_text(item, setup, ai_character)
            for key, item in value.items()
        }
    return value


def _player_character_name_replacements(
    setup: dict[str, Any], ai_character: Any
) -> list[tuple[str, str]]:
    """Builds safe character-name replacements for AI-renamed setup text."""

    clean_setup = normalize_new_game_setup(setup)
    player_name = str(clean_setup["character"].get("name", "")).strip()
    if not _is_player_provided_character_field("name", player_name):
        return []
    if not isinstance(ai_character, dict):
        return []
    ai_name = str(ai_character.get("name", "")).strip()
    if not ai_name or ai_name.casefold() == player_name.casefold():
        return []
    replacements = [(ai_name, player_name)]
    ai_first = ai_name.split()[0] if ai_name.split() else ""
    player_first = player_name.split()[0] if player_name.split() else player_name
    if ai_first and player_first and ai_first.casefold() != player_first.casefold():
        replacements.append((ai_first, player_first))
    return replacements


def _is_player_provided_character_field(key: str, value: str) -> bool:
    """Returns True when a character field is a custom player value."""

    clean_value = str(value or "").strip()
    if not clean_value:
        return False
    return not (key == "name" and clean_value == "Player Name")


def _replace_whole_name(text: str, source: str, target: str) -> str:
    """Replaces a generated name without touching substrings inside words."""

    if not source:
        return text
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(source)}(?![A-Za-z0-9])",
        flags=re.IGNORECASE,
    )
    return pattern.sub(target, text)


def _append_ai_context_line(existing_context: str, line: str) -> str:
    """Appends an AI-facing setup context line when it is not already present."""

    clean_existing = str(existing_context or "").strip()
    clean_line = str(line or "").strip()
    if not clean_line:
        return clean_existing
    if clean_line in clean_existing.splitlines():
        return clean_existing
    return f"{clean_existing}\n\n{clean_line}" if clean_existing else clean_line
