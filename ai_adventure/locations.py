from __future__ import annotations

import math
import re
import uuid
from dataclasses import asdict, dataclass, replace
from typing import Any


_DETAIL_SEPARATORS = [",", ";", " - ", " -- ", " – ", " — "]
_DETAIL_PHRASE_RE = re.compile(
    r"\s+(?:high up|overlooking|with|near|beside|under|above|below|inside|outside|"
    r"atop|beneath|facing)\b",
    flags=re.IGNORECASE,
)

DEFAULT_MOVE_SPEED_MPH = 3.0
DEFAULT_TRAVEL_MODE = "On Foot"
DEFAULT_TRAVEL_SPEED_MULTIPLIER = 1.0


@dataclass(frozen=True)
class KnownLocation:
    """Player-known location data used by the Travel screen and AI context."""

    name: str
    location_id: str = ""
    description: str = ""
    x_miles: float | None = None
    y_miles: float | None = None
    terrain: str = ""
    travel_multiplier: float = 1.0
    travel_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Returns JSON-serializable location data."""

        return asdict(self)


@dataclass(frozen=True)
class TravelEstimate:
    """A mathematical route estimate between two player-known locations."""

    destination_name: str
    distance_miles: float | None
    effective_speed_mph: float
    estimated_minutes: int | None
    travel_mode: str
    terrain: str = ""
    travel_notes: str = ""

    @property
    def is_available(self) -> bool:
        """Whether both map positions were known well enough to calculate a route."""

        return self.distance_miles is not None and self.estimated_minutes is not None

    def to_dict(self) -> dict[str, Any]:
        """Returns JSON-serializable estimate data for AI context packets."""

        data = asdict(self)
        data["distance_label"] = format_distance(self.distance_miles)
        data["time_label"] = format_travel_time(self.estimated_minutes)
        return data


def clean_player_location_name(raw_location: Any) -> str:
    """Returns a short, broad player location name suitable for UI state."""

    location = re.sub(r"\s+", " ", str(raw_location or "")).strip()

    if not location:
        return ""

    split_indexes = [
        index
        for separator in _DETAIL_SEPARATORS
        if (index := location.find(separator)) > 0
    ]
    phrase_match = _DETAIL_PHRASE_RE.search(location)

    if phrase_match is not None and phrase_match.start() > 0:
        split_indexes.append(phrase_match.start())

    if split_indexes:
        location = location[: min(split_indexes)].strip()

    return location.strip(" ,;:-")


def normalize_known_location(raw_location: Any) -> KnownLocation | None:
    """Normalizes player-known location metadata without inventing map data."""

    if not isinstance(raw_location, dict):
        return None

    name = clean_player_location_name(raw_location.get("name", ""))

    if not name:
        return None

    return KnownLocation(
        name=name,
        location_id=_clean_text(raw_location.get("location_id")),
        description=_clean_text(raw_location.get("description")),
        x_miles=_optional_coordinate(raw_location.get("x_miles", raw_location.get("x"))),
        y_miles=_optional_coordinate(raw_location.get("y_miles", raw_location.get("y"))),
        terrain=_clean_text(raw_location.get("terrain")),
        travel_multiplier=_clamped_float(
            raw_location.get("travel_multiplier"),
            default=1.0,
            minimum=0.1,
            maximum=3.0,
        ),
        travel_notes=_clean_text(raw_location.get("travel_notes")),
    )


def normalize_known_locations(raw_locations: Any) -> list[KnownLocation]:
    """Normalizes and de-duplicates persisted travel locations by visible name."""

    if not isinstance(raw_locations, list):
        return []

    locations: list[KnownLocation] = []
    indexes_by_name: dict[str, int] = {}

    for raw_location in raw_locations:
        location = normalize_known_location(raw_location)

        if location is None:
            continue

        key = location.name.casefold()
        existing_index = indexes_by_name.get(key)

        if existing_index is None:
            indexes_by_name[key] = len(locations)
            locations.append(location)
            continue

        locations[existing_index] = _merge_locations(locations[existing_index], location)

    return locations


def calculate_travel_estimate(
    origin: KnownLocation | None,
    destination: KnownLocation | None,
    *,
    move_speed_mph: Any = DEFAULT_MOVE_SPEED_MPH,
    travel_mode: Any = DEFAULT_TRAVEL_MODE,
    speed_multiplier: Any = DEFAULT_TRAVEL_SPEED_MULTIPLIER,
) -> TravelEstimate:
    """Calculates straight-line travel time from coordinates and effective speed."""

    clean_speed = _clamped_float(
        move_speed_mph,
        default=DEFAULT_MOVE_SPEED_MPH,
        minimum=0.1,
        maximum=100.0,
    )
    clean_mode = _clean_text(travel_mode) or DEFAULT_TRAVEL_MODE
    clean_speed_multiplier = _clamped_float(
        speed_multiplier,
        default=DEFAULT_TRAVEL_SPEED_MULTIPLIER,
        minimum=0.1,
        maximum=20.0,
    )
    terrain_multiplier = (
        destination.travel_multiplier if destination is not None else 1.0
    )
    effective_speed = round(
        clean_speed * clean_speed_multiplier * terrain_multiplier,
        2,
    )
    destination_name = destination.name if destination is not None else "Unknown Location"
    terrain = destination.terrain if destination is not None else ""
    travel_notes = destination.travel_notes if destination is not None else ""

    origin_x = origin.x_miles if origin is not None else None
    origin_y = origin.y_miles if origin is not None else None
    destination_x = destination.x_miles if destination is not None else None
    destination_y = destination.y_miles if destination is not None else None

    if (
        origin_x is None
        or origin_y is None
        or destination_x is None
        or destination_y is None
    ):
        return TravelEstimate(
            destination_name=destination_name,
            distance_miles=None,
            effective_speed_mph=effective_speed,
            estimated_minutes=None,
            travel_mode=clean_mode,
            terrain=terrain,
            travel_notes=travel_notes,
        )

    distance_miles = math.hypot(
        destination_x - origin_x,
        destination_y - origin_y,
    )
    estimated_minutes = math.ceil((distance_miles / effective_speed) * 60)

    return TravelEstimate(
        destination_name=destination_name,
        distance_miles=round(distance_miles, 2),
        effective_speed_mph=effective_speed,
        estimated_minutes=estimated_minutes,
        travel_mode=clean_mode,
        terrain=terrain,
        travel_notes=travel_notes,
    )


def format_distance(distance_miles: float | None) -> str:
    """Formats a calculated distance for the player-facing Travel screen."""

    if distance_miles is None:
        return "Map position not yet known"

    if distance_miles < 0.1:
        return "Less than 0.1 miles"

    return f"{distance_miles:.1f} miles"


def format_travel_time(estimated_minutes: int | None) -> str:
    """Formats a calculated duration for the player-facing Travel screen."""

    if estimated_minutes is None:
        return "Estimate unavailable"

    if estimated_minutes == 0:
        return "Already there"

    if estimated_minutes < 60:
        return f"About {estimated_minutes} minutes"

    hours, minutes = divmod(estimated_minutes, 60)

    if hours < 24:
        hour_label = "hour" if hours == 1 else "hours"
        if minutes == 0:
            return f"About {hours} {hour_label}"
        return f"About {hours} {hour_label} {minutes} minutes"

    days, remaining_hours = divmod(hours, 24)
    day_label = "day" if days == 1 else "days"
    if remaining_hours == 0:
        return f"About {days} {day_label}"
    return f"About {days} {day_label} {remaining_hours} hours"


def _merge_locations(existing: KnownLocation, incoming: KnownLocation) -> KnownLocation:
    """Keeps existing metadata when a partial location update arrives."""

    return KnownLocation(
        name=existing.name,
        location_id=existing.location_id or incoming.location_id,
        description=incoming.description or existing.description,
        x_miles=incoming.x_miles if incoming.x_miles is not None else existing.x_miles,
        y_miles=incoming.y_miles if incoming.y_miles is not None else existing.y_miles,
        terrain=incoming.terrain or existing.terrain,
        travel_multiplier=(
            incoming.travel_multiplier
            if incoming.travel_multiplier != 1.0 or existing.travel_multiplier == 1.0
            else existing.travel_multiplier
        ),
        travel_notes=incoming.travel_notes or existing.travel_notes,
    )


def ensure_location_ids(locations: list[KnownLocation]) -> list[KnownLocation]:
    """Assigns stable UUIDs to locations that predate the location-id field."""

    return [
        location
        if location.location_id
        else replace(location, location_id=f"loc_{uuid.uuid4().hex}")
        for location in locations
    ]


def _clean_text(value: Any) -> str:
    """Returns compact safe text for persisted travel metadata."""

    return re.sub(r"\s+", " ", str(value or "")).strip()


def _optional_coordinate(value: Any) -> float | None:
    """Parses an optional map coordinate while rejecting non-finite values."""

    if value is None or str(value).strip() == "":
        return None

    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(coordinate) or not -100000.0 <= coordinate <= 100000.0:
        return None

    return round(coordinate, 2)


def _clamped_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    """Parses a finite bounded float with a default fallback."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default

    if not math.isfinite(parsed):
        parsed = default

    return round(max(minimum, min(maximum, parsed)), 2)
