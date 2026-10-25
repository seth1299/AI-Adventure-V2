"""Player-known regional map geometry and an optional terrain-art request."""
from __future__ import annotations

import json
from typing import Any

from ai_adventure.locations import KnownLocation, normalize_known_locations
from ai_adventure.ai.image_styles import DEFAULT_IMAGE_STYLE
from ai_adventure.visual_assets import VisualAssetRepository, VisualAssetRequest


def regional_map_layout(raw_locations: Any) -> tuple[
    list[tuple[KnownLocation, float, float]], list[KnownLocation], float | None
]:
    """Project known coordinates onto a square without stretching either axis."""
    locations = normalize_known_locations(raw_locations)
    positioned: list[tuple[KnownLocation, float, float]] = []
    unmapped: list[KnownLocation] = []
    for place in locations:
        x, y = place.x_miles, place.y_miles
        if x is None or y is None:
            unmapped.append(place)
        else:
            positioned.append((place, x, y))
    if not positioned:
        return [], locations, None
    min_x = min(x for _, x, _ in positioned)
    max_x = max(x for _, x, _ in positioned)
    min_y = min(y for _, _, y in positioned)
    max_y = max(y for _, _, y in positioned)
    span = max(max_x - min_x, max_y - min_y, 1.0)
    center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    points = [(p, 500 + (x - center_x) * 780 / span,
               500 - (y - center_y) * 780 / span) for p, x, y in positioned]
    return points, unmapped, span


def regional_map_request(repository: VisualAssetRepository) -> VisualAssetRequest:
    """Describe only known geography for explicitly requested map backgrounds."""
    raw = repository.ensure_travel_locations()
    points, unmapped, span = regional_map_layout(raw)
    # Stable ordering makes the artwork reusable when only the player moves.
    places = [{"name": p.name, "terrain": p.terrain,
               "description": p.description, "parent": p.parent_location,
               "route_notes": p.travel_notes,
               "x_percent": round(x / 10, 2), "y_percent": round(y / 10, 2)}
              for p, x, y in sorted(points, key=lambda point: point[0].name.casefold())]
    description = json.dumps({"known_places": places, "map_width_miles":
                              round(span * 1000 / 780, 2) if span else None,
                              "unmapped_places": sorted(p.name for p in unmapped)},
                             ensure_ascii=False, sort_keys=True)
    return VisualAssetRequest(
        subject_type="region_map", subject_key="region", display_name="Regional Map",
        description=description,
        image_style=repository.get_setting("images.style", DEFAULT_IMAGE_STYLE),
    )
