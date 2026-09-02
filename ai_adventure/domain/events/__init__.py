"""Domain event normalization and application."""

from ai_adventure.events.event_applier import (
    AppliedEventResult,
    EventApplier,
    normalize_event,
)

__all__ = ["AppliedEventResult", "EventApplier", "normalize_event"]
