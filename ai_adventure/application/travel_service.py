"""Private route assessments and transactional, provider-free safe travel."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ai_adventure.ai.modes import ai_mode_preferences_from_settings
from ai_adventure.audio.catalog import music_track_context
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.locations import calculate_travel_estimate, format_travel_time, normalize_known_location
from ai_adventure.persistence.save_repository import SaveRepository


def assessment_key(repository: SaveRepository) -> str:
    """Invalidate directional judgments on arrival or changed route conditions."""
    data = {
        "origin": repository.get_state_value("location", "").casefold(),
        "arrival_id": repository.get_setting("travel.arrival_id", ""),
        "weather": repository.get_state_value("weather", ""),
        "condition": repository.get_state_value("condition", ""),
        "movement": repository.get_travel_profile(),
        "music_tracks": repository.get_setting("travel.music_tracks", []),
        "current_music": repository.get_setting("audio.current_music", ""),
        "locations": sorted(repository.ensure_travel_locations(), key=lambda row: row["location_id"]),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def sync_location_music(repository: SaveRepository, tracks: list[str]) -> None:
    """Keep the available catalog and the current scene's association authoritative."""
    filenames = [track["filename"] for track in music_track_context(tracks)]
    if repository.get_setting("travel.music_tracks", []) != filenames:
        repository.set_setting("travel.music_tracks", filenames)
    current = str(repository.get_setting("audio.current_music", "") or "").strip()
    location = repository.find_travel_location(repository.get_state_value("location", ""))
    if current in filenames and location and location.get("music_filename") != current:
        repository.upsert_travel_location({"name": location["name"], "music_filename": current})


def current_assessment(repository: SaveRepository) -> dict[str, Any]:
    record = repository.get_setting("travel.assessment", {})
    return record if isinstance(record, dict) and record.get("key") == assessment_key(repository) else {}


def build_assessment_context(repository: SaveRepository) -> dict[str, Any]:
    # This background operation needs only route logistics and current conditions.
    # Do not export secrets, notes, conversation history or unrelated saved state.
    state = StateManager(repository).load_state(history_limit=0)
    return {
        "packet_type": "private_travel_assessment",
        "assessment_key": assessment_key(repository),
        "origin": repository.get_state_value("location", ""),
        "locations": repository.ensure_travel_locations(),
        "valid_music_tracks": repository.get_setting("travel.music_tracks", []),
        "music_track_context": music_track_context(repository.get_setting("travel.music_tracks", [])),
        "state": {
            "player_ai_preferences": ai_mode_preferences_from_settings(state.settings.values),
            "scene": {"location": state.world.location, "weather": state.world.weather},
            "calendar": state.calendar.to_dict(),
            "movement": repository.get_travel_profile(),
            "player_condition": state.player.condition,
            "genre": repository.get_setting("world.genre", ""),
        },
    }


def commit_assessment(repository: SaveRepository, packet: dict[str, Any], result: Any) -> bool:
    """Accept one complete assessment only while its origin and inputs still match."""
    with repository.transaction():
        if (repository.survival_status()["fate"] != "alive"
                or packet.get("assessment_key") != assessment_key(repository)):
            return False
        expected = {row["location_id"]: row for row in packet["locations"]
                    if row["name"].casefold() != packet["origin"].casefold()}
        routes = result.get("routes") if isinstance(result, dict) else None
        if not isinstance(routes, list) or len(routes) != len(expected):
            raise ValueError("Travel assessment must cover every other known location exactly once.")
        validated = {}
        tracks = {track.casefold(): track for track in packet.get("valid_music_tracks", [])}
        for route in routes:
            if not isinstance(route, dict):
                raise ValueError("Invalid travel route assessment.")
            location_id = route.get("location_id")
            weather = route.get("weather")
            if (not isinstance(location_id, str) or location_id not in expected or location_id in validated
                    or type(route.get("safe")) is not bool or not isinstance(weather, str)
                    or not weather.strip() or weather.strip().upper() in {"AUTO", "SAME", "SKIP"}):
                raise ValueError("Travel assessments need known unique IDs, boolean safety and actual weather.")
            music = route.get("music_filename", "")
            if not isinstance(music, str) or (tracks and music.casefold() not in tracks) or (not tracks and music):
                raise ValueError("Location music must be an exact available catalog track, or blank when no music is available.")
            validated[location_id] = {"safe": route["safe"], "weather": weather.strip(),
                                      "music_filename": tracks.get(music.casefold(), "")}
        for location_id, route in validated.items():
            repository.upsert_travel_location({"name": expected[location_id]["name"], "weather": route["weather"],
                                              "music_filename": route["music_filename"]})
        repository.set_setting("travel.assessment", {
            "key": assessment_key(repository), "origin": packet["origin"], "routes": validated,
            "assessed_minute": repository.get_current_calendar_minute(),
        })
        return True


def complete_safe_travel(
    repository: SaveRepository, destination_name: str, player_context: str = "", *, message_id: str | None = None,
    music_tracks: list[str] | None = None,
) -> bool:
    """Recheck persisted authority, then commit the action, status and arrival together."""
    with repository.transaction():
        repository.require_active_adventure()
        message_id = message_id or repository.create_message_id()
        if repository.event_receipt(message_id, "local_travel"):
            return True
        if repository.survival_status()["fate"] != "alive":
            return False
        # Never trust safety, weather or coordinates supplied by a stale UI item.
        destination = normalize_known_location(repository.find_travel_location(destination_name))
        state = StateManager(repository).load_state(history_limit=0)
        origin = normalize_known_location(repository.find_travel_location(state.world.location))
        if destination is None or origin is None or origin.name.casefold() == destination.name.casefold():
            return False
        assessment = current_assessment(repository)
        route = assessment.get("routes", {}).get(destination.location_id, {})
        if route.get("safe") is not True or not destination.weather:
            return False
        estimate = calculate_travel_estimate(
            origin, destination, move_speed_mph=state.travel.move_speed_mph,
            travel_mode=state.travel.travel_mode, speed_multiplier=state.travel.speed_multiplier,
        )
        if not estimate.is_available:
            return False
        StoryTurnService.record_player_action(
            repository, f"Travel toward {destination.name}. {player_context}".strip(), message_id=message_id,
        )
        events = [{
            "type": "StatusUpdatedEvent", "payload": {
                "location": destination.name, "minutes_passed": estimate.estimated_minutes,
                "weather": destination.weather,
            },
        }]
        music = destination.music_filename
        if music_tracks is not None:
            music = {track.casefold(): track for track in music_tracks}.get(music.casefold(), "")
        if music:
            events.append({"type": "MusicChangedEvent", "payload": {"filename": music}})
        else:
            repository.set_setting("audio.current_music", "")
        results = EventApplier(repository, message_id=message_id).apply_events(events)
        if not results or any(result.status != "applied" for result in results):
            raise ValueError("Could not apply the travel status update.")
        repository.append_history(
            "story", f"Arrived at **{destination.name}** after {format_travel_time(estimate.estimated_minutes)}. "
            f"Weather: {destination.weather}.", message_id=message_id, turn_id=message_id,
        )
        repository.record_event_receipt(message_id, "local_travel", {"destination": destination.name})
        return True
