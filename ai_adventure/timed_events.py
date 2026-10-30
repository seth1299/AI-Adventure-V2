"""Contracts and presentation for durable processes driven by in-game time."""
from ai_adventure.calendar_system import MINUTES_PER_DAY

TIMED_EVENT_RULE = (
    "Time-sensitive events are durable player-visible processes, not just quests. "
    "Use TimedEventUpsertedEvent for growth, incubation, fermentation, recovery, "
    "construction, deliveries, and other established changes that take time. Read "
    "location names AND descriptions and other player-authored setup prose for "
    "existing gardens, crops, animals, projects and their present condition. Track "
    "each named plant or distinct batch separately at its actual finalized location; "
    "do not collapse flax, hemp, cotton and dye plants into one garden task. Preserve "
    "explicit counts and units with quantity_is_estimate=false. When counts are "
    "unstated, infer a reasonable positive count and suitable unit from the described "
    "scale, location, species and context; mark quantity_is_estimate=true and explain "
    "the assumption in notes. Prefer modest plausible counts; do not silently treat "
    "an inferred count as an exact observation. Reuse established estimates on later "
    "turns unless new evidence or player actions change them. Use quantity=-1 only "
    "when no reasonable estimate is possible and explain why. Count estimates are "
    "independent of timing estimates. Infer a reasonable stage and estimated duration when "
    "the fiction supports it, marking estimates with is_estimate=true; use "
    "ready_at_minute=-1 and explain uncertainty in notes when timing cannot be inferred. "
    "Use current_state (e.g. Growing) and next_state (e.g. Mature), plus either an "
    "absolute ready_at_minute or duration_minutes from the current in-game minute. "
    "The clock derives remaining time and the reached next_state automatically. "
    "Copy state.timed_events.entries[].id into event_id, or reuse the same name and "
    "location, to update an existing record; "
    "use distinct names for separate beds/batches. Tending a garden must update "
    "its durable notes/condition, not exist only in narration, and must not reset "
    "growth timers unless the action actually changes them. A mature crop remains "
    "active. Estimated readiness is a forecast, not proof of actual condition; "
    "confirm it when inspected. If conditions invalidate the expected transition, "
    "clear ready_at_minute to -1 and update current_state and notes. It remains "
    "active until harvested: mark lifecycle=completed only when consumed, harvested, "
    "or otherwise resolved; use cancelled for abandoned processes. A new cycle "
    "requires a new state and time estimate. Never grant inventory/rewards merely "
    "because a timer reaches zero. Do not duplicate authoritative crafting.processes "
    "timers: those already track active/passive recipe stages."
)


def remaining_time_label(due_minute: int, current_minute: int, *, estimated: bool = False) -> str:
    if due_minute < 0:
        return "Unknown"
    remaining = max(0, due_minute - current_minute)
    if not remaining:
        return "Expected ready" if estimated else "Ready"
    days, remainder = divmod(remaining, MINUTES_PER_DAY)
    hours, minutes = divmod(remainder, 60)
    parts = [f"{value} {unit}" for value, unit in ((days, "day" if days == 1 else "days"), (hours, "hr"), (minutes, "min")) if value]
    return ("~" if estimated else "") + " ".join(parts)
