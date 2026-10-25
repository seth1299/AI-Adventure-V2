"""Player approval of generated possessions before the opening is written."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


@dataclass
class StartingInventoryReview:
    items: list[dict[str, Any]]
    locations: list[dict[str, Any]]
    capacity_lb: float
    approved_items: list[dict[str, Any]] | None = None

    @classmethod
    def from_generated_state(cls, state, setup_packet, approved_items=None):
        # Deferred imports keep the UI-neutral proposal independent of provider
        # startup and reuse exactly the same setup reconciliation as save commit.
        from ai_adventure.ai.gemini_service import _parse_new_game_starter_items
        from ai_adventure.application.new_game_service import (
            _starter_items_for_save, _travel_locations_for_save,
            _finalized_location_aliases, _replace_location_aliases,
            _replace_location_aliases_in_travel_locations,
        )
        setup = setup_packet.get("setup", {})
        locations = _travel_locations_for_save(state.get("locations", []), setup,
            SimpleNamespace(start_location=state.get("start_location", "")))
        aliases = _finalized_location_aliases(locations, setup)
        locations = _replace_location_aliases_in_travel_locations(locations, aliases)
        items = deepcopy(approved_items) if approved_items is not None else _starter_items_for_save(
            _parse_new_game_starter_items(state.get("starting_items", [])), setup,
        )
        for item in items:
            item["storage_location"] = _replace_location_aliases(
                item.get("storage_location", "actively_carried"), aliases,
            )
        return cls(items, locations, float(setup.get("character", {}).get("carrying_capacity_lb", 50)))

    def preview(self, repository, items=None):
        from ai_adventure.inventory_storage import is_container, storage_location
        candidate = self.items if items is None else items
        preview = repository.preview_starting_inventory(candidate, base_capacity_lb=self.capacity_lb)
        sites = {str(p["name"]).casefold() for p in self.locations if p.get("location_scope") == "specific"}
        containers = {row["name"].casefold() for row in preview["items"] if is_container(row)}
        carried = {"actively_carried", "actively carried", "on_person", "on person"}
        errors = []
        for row in preview["items"]:
            storage = storage_location(row).casefold()
            if storage not in sites | containers | carried and not row["metadata"].get("container_id"):
                errors.append(f"Choose a specific storage site for {row['name']}.")
            if row["metadata"].get("moveable") is False and storage in carried:
                errors.append(f"{row['name']} is fixed in place; choose its storage site.")
        preview["error"] = "\n".join(filter(None, [preview["error"], *errors]))
        return preview
