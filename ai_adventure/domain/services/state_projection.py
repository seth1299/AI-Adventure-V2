"""Durable-state projection helpers."""

from __future__ import annotations

from typing import Any

from ai_adventure.calendar_system import build_calendar_snapshot
from ai_adventure.persistence.save_repository import SaveRepository


def refresh_calendar_time_projection(repository: SaveRepository) -> dict[str, Any]:
    """Synchronizes the persisted display-time projection from calendar state."""

    snapshot = build_calendar_snapshot(
        repository.get_current_calendar_minute(),
        repository.get_calendar_settings(),
    )
    repository.set_state_value("time", snapshot["display_label"])
    return snapshot
