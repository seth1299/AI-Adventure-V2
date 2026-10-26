"""Recovery requests keep external generation outside the save transaction."""

from __future__ import annotations

from typing import Any

from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.persistence.save_repository import SaveRepository


def build_recovery_context(repository: SaveRepository) -> dict[str, Any]:
    packet = StoryTurnService.build_context_packet(repository, "Survival recovery")
    state = packet["state"]
    return {
        "packet_type": "survival_recovery",
        "state": {key: state[key] for key in (
            "player", "player_ai_preferences", "scene", "world_profile", "survival",
        ) if key in state},
        "death": repository.get_setting("survival.death", {}),
        "known_locations": repository.get_travel_locations(),
        "recent_history": packet.get("recent_history", []),
    }
