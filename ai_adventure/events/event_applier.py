from __future__ import annotations

import logging
import json
import hashlib
from copy import deepcopy
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from ai_adventure.calendar_system import (
    DEFAULT_START_ELAPSED_MINUTES,
    MINUTES_PER_DAY,
    build_calendar_snapshot,
    month_start_day_index,
    normalize_calendar_settings,
)
from ai_adventure.alchemy.ingredients import (
    CRAFTING_INGREDIENT_CATEGORY_NAMES,
    is_crafting_ingredient_category,
    normalize_crafting_item_rarity,
    normalize_recipe_ingredients,
)
from ai_adventure.context.creative_guardrails import (
    contains_banned_creative_term,
    find_banned_creative_terms,
    sanitize_banned_creative_terms_in_data,
)
from ai_adventure.items import normalize_item_metadata
from ai_adventure.story_preferences import normalize_fighting_preferences
from ai_adventure.stats import DEFAULT_PLAYER_MAX_HEALTH
from ai_adventure.container_access import has_immediate_container_unlock_method
from ai_adventure.container_flow import container_test_succeeded, ContainerFlowError, selected_contents, validate_contents_manifest
from ai_adventure.inventory_storage import move_error, carry_priority
from ai_adventure.currency import format_currency_amount
from ai_adventure.locations import clean_player_location_name
from ai_adventure.locations import calculate_travel_estimate, normalize_known_locations
from ai_adventure.persistence.save_repository import GM_SECRET_STATUSES, SaveRepository
from ai_adventure.skills.rules import MAX_SKILL_LEVEL, bonus_for_level, dc_for_difficulty


LOGGER = logging.getLogger(__name__)
_D20_TEST_GATED_EVENT_TYPES = {
    "PlayerAchievementRecordedEvent",
    "ActiveTaskCompletedEvent",
    "CurrencyChangedEvent",
    "ContainerContentsTakenEvent",
    "ContainerOpenedEvent",
    "InventoryItemAddedEvent",
    "InventoryItemModifiedEvent",
    "ItemAddedEvent",
    "ReagentDiscoveredEvent",
    "RecipeDiscoveredEvent",
    "CharacterSpellLearnedEvent",
    "MagicAdvancementRecordedEvent",
}


class RandomNumberGenerator(Protocol):
    """Minimal random interface needed to resolve a d20 test."""

    def randint(self, minimum: int, maximum: int, /) -> int:
        """Returns an integer within the inclusive range."""
        return random.randint(minimum, maximum)


@dataclass(frozen=True)
class AppliedEventResult:
    """Result of attempting to apply one event."""

    event_type: str
    status: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


class EventApplier:
    """Applies validated AI-suggested events to the save repository."""

    def __init__(
        self,
        repository: SaveRepository,
        rng: RandomNumberGenerator | None = None,
        message_id: str | None = None,
    ) -> None:
        """
        Args:
            repository: Active save repository.
            rng: Optional random generator for deterministic tests.
        """

        self.repository = repository
        self.rng = rng or random.Random()
        self.message_id = str(message_id or "").strip() or None

    def apply_events(
        self,
        raw_events: list[dict[str, Any]],
        *,
        prior_results: list[AppliedEventResult] | None = None,
    ) -> list[AppliedEventResult]:
        """
        Applies a list of raw event dictionaries.

        Args:
            raw_events: Event objects from Gemini's JSON response.
            prior_results: Already-applied event results from the same player
                command, such as pre-narration d20 tests.

        Returns:
            Application results for every attempted event.
        """

        with self.repository.transaction(), self.repository.message_context(self.message_id):
            return self._apply_events(raw_events, prior_results=prior_results)

    def _apply_events(
        self,
        raw_events: list[dict[str, Any]],
        *,
        prior_results: list[AppliedEventResult] | None = None,
    ) -> list[AppliedEventResult]:
        """Applies events inside the message-associated repository scope."""

        results: list[AppliedEventResult] = []
        blocking_failure = _blocking_d20_test_failure(prior_results or [])
        interacting_containers = {
            _first_text(normalize_event(event)[1], "container_name").casefold()
            for event in raw_events
            if normalize_event(event)[0] in {"ContainerOpenedEvent", "ContainerContentsTakenEvent"}
        }
        protects_container_contents = any(
            _raw_event_protects_container_contents(raw_event)
            for raw_event in raw_events
        )
        existing_container_names = {
            str(item["name"]).strip().casefold()
            for item in self.repository.list_inventory_items()
            if isinstance(item.get("metadata", {}).get("container"), dict)
        } if protects_container_contents else set()

        for event_index, raw_event in enumerate(raw_events):
            event_type, payload = normalize_event(raw_event)
            receipt_key = f"event:{event_index}:" + hashlib.sha256(json.dumps([event_type, payload], sort_keys=True).encode()).hexdigest()
            receipt = self.repository.event_receipt(self.message_id, receipt_key) if self.message_id else None
            if receipt:
                result = AppliedEventResult(**receipt)
                results.append(result)
                if result.event_type == "D20TestRequestedEvent" and result.status == "applied" and result.payload.get("outcome") == "failure":
                    blocking_failure = result
                continue

            if protects_container_contents and _is_direct_container_reward(
                event_type,
                payload, interacting_containers=interacting_containers,
                existing_container_names=existing_container_names,
            ):
                result = AppliedEventResult(
                    event_type,
                    "skipped",
                    (
                        "Skipped direct reward because container contents must "
                        "transfer only through ContainerContentsTakenEvent."
                    ),
                    payload,
                )
            elif (
                blocking_failure is not None
                and event_type in _D20_TEST_GATED_EVENT_TYPES
            ):
                result = AppliedEventResult(
                    event_type,
                    "skipped",
                    (
                        "Skipped because a previous d20 test failed: "
                        f"{blocking_failure.message}"
                    ),
                    payload,
                )
            else:
                result = self.apply_event(
                    {"type": event_type, "payload": payload},
                    d20_test_results=[*(prior_results or []), *results],
                )

            if (
                result.event_type == "D20TestRequestedEvent"
                and result.status == "applied"
                and str(result.payload.get("outcome", "")).casefold() == "failure"
            ):
                blocking_failure = result

            if result.status == "failed":
                raise RuntimeError(f"Failed to apply {result.event_type}: {result.message}")

            self.repository.append_mechanical_event(
                result.event_type,
                result.payload,
                result.status,
                result.message,
            )
            if self.message_id:
                self.repository.record_event_receipt(self.message_id, receipt_key, asdict(result))
            results.append(result)

        return results

    def apply_event(
        self,
        raw_event: dict[str, Any],
        *,
        d20_test_results: list[AppliedEventResult] | None = None,
    ) -> AppliedEventResult:
        """
        Applies one raw event dictionary.

        Args:
            raw_event: Event object.

        Returns:
            Application result.
        """

        event_type, payload = normalize_event(raw_event)

        try:
            if event_type in {"InventoryItemAddedEvent", "ItemAddedEvent"}:
                return self._apply_inventory_item_added(event_type, payload)

            if event_type in {"InventoryItemRemovedEvent", "ItemRemovedEvent"}:
                return self._apply_inventory_item_removed(event_type, payload)

            if event_type in {"InventoryItemModifiedEvent", "ItemModifiedEvent"}:
                return self._apply_inventory_item_modified(event_type, payload)

            if event_type == "ContainerOpenedEvent":
                return self._apply_container_opened(
                    event_type,
                    payload,
                    d20_test_results or [],
                )

            if event_type == "ContainerContentsTakenEvent":
                return self._apply_container_contents_taken(event_type, payload)

            if event_type == "SkillUpsertedEvent":
                return self._apply_skill_upserted(event_type, payload)

            if event_type == "SkillXpAddedEvent":
                return self._apply_skill_xp_added(event_type, payload)

            if event_type == "D20TestRequestedEvent":
                return self._apply_d20_test_requested(event_type, payload)

            if event_type == "PlayerAchievementRecordedEvent":
                return self._apply_player_achievement(event_type, payload)
            if event_type == "PlayerHealthChangedEvent":
                return self._apply_player_health(event_type, payload)
            if event_type == "StatusUpdatedEvent":
                return self._apply_status_updated(event_type, payload)

            if event_type == "LocationUpsertedEvent":
                return self._apply_location_upserted(event_type, payload)

            if event_type == "TravelModeChangedEvent":
                return self._apply_travel_mode_changed(event_type, payload)

            if event_type == "FlagSetEvent":
                return self._apply_flag_set(event_type, payload)

            if event_type == "RecipeDiscoveredEvent":
                return self._apply_recipe_discovered(event_type, payload)

            if event_type == "ReagentDiscoveredEvent":
                return self._apply_reagent_discovered(event_type, payload)

            if event_type == "CraftingProcessRequestedEvent":
                return self._apply_crafting_process_requested(event_type, payload)

            if event_type == "CurrencyChangedEvent":
                return self._apply_currency_changed(event_type, payload)

            if event_type == "CurrencyDefinedEvent":
                return self._apply_currency_defined(event_type, payload)

            if event_type == "ActiveTaskUpsertedEvent":
                return self._apply_active_task_upserted(event_type, payload)

            if event_type == "ActiveTaskCompletedEvent":
                return self._apply_active_task_completed(event_type, payload)

            if event_type == "CalendarEventUpsertedEvent":
                return self._apply_calendar_event_upserted(event_type, payload)

            if event_type == "CalendarEventDeletedEvent":
                return self._apply_calendar_event_deleted(event_type, payload)

            if event_type == "SpellCatalogUpsertedEvent":
                return self._apply_spell_catalog_upserted(event_type, payload)

            if event_type == "CharacterSpellLearnedEvent":
                return self._apply_character_spell_learned(event_type, payload)

            if event_type == "PlayerSpellCastEvent":
                return self._apply_player_spell_cast(event_type, payload)

            if event_type == "MagicAdvancementRecordedEvent":
                return self._apply_magic_advancement_recorded(event_type, payload)

            if event_type == "MagicEffectUpsertedEvent":
                return self._apply_magic_effect_upserted(event_type, payload)

            if event_type == "NpcUpsertedEvent":
                return self._apply_npc_upserted(event_type, payload)

            if event_type == "MerchantStockUpsertedEvent":
                return self._apply_merchant_stock_upserted(event_type, payload)

            if event_type == "MerchantBuyOfferUpsertedEvent":
                return self._apply_merchant_buy_offer_upserted(event_type, payload)

            if event_type == "NpcKnowledgeAddedEvent":
                return self._apply_npc_knowledge_added(event_type, payload)

            if event_type == "SecretUpsertedEvent":
                return self._apply_secret_upserted(event_type, payload)

            if event_type == "MiscellaneousUpsertedEvent":
                return self._apply_miscellaneous_upserted(event_type, payload)

            if event_type == "BestiaryEntryUpsertedEvent":
                return self._apply_bestiary_entry_upserted(event_type, payload)

            if event_type == "MusicChangedEvent":
                return self._apply_music_changed(event_type, payload)

            if event_type == "BackgroundAmbienceChangedEvent":
                return self._apply_background_ambience_changed(event_type, payload)

            message = f"Unsupported event type: {event_type}"
            LOGGER.warning(message)
            return AppliedEventResult(event_type, "skipped", message, payload)
        except Exception as error:
            LOGGER.exception("Failed to apply event %s.", event_type)
            return AppliedEventResult(event_type, "failed", str(error), payload)

    def _apply_inventory_item_added(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies InventoryItemAddedEvent."""

        name = _first_text(payload, "item_name", "name")

        if not name:
            return _invalid(event_type, payload, "Inventory item name is required.")

        owner_npc_id = _first_text(payload, "owner_npc_id")
        if owner_npc_id and not self._is_current_party_member(owner_npc_id):
            return _invalid(
                event_type,
                payload,
                f"Party member is not active: {owner_npc_id}.",
            )

        catalog_entry = self._matching_item_catalog_entry(payload, name)
        catalog_metadata = (
            dict(catalog_entry.get("metadata", {}))
            if catalog_entry is not None
            and isinstance(catalog_entry.get("metadata"), dict)
            else {}
        )
        if catalog_entry is not None:
            name = str(catalog_entry.get("name", name)).strip() or name

        quantity = _first_int(payload, 1, "amount", "quantity")
        category = (
            str(catalog_entry.get("category", "")).strip()
            if catalog_entry is not None
            else ""
        ) or _first_text(payload, "item_type", "category")
        description = (
            str(catalog_entry.get("description", "")).strip()
            if catalog_entry is not None
            else ""
        ) or _first_text(payload, "description", "desc")
        quantity_unit = _first_text(payload, "quantity_unit", "unit", "measure_unit") or "each"
        storage_location = _first_text(payload, "storage_location") or "actively_carried"
        if not owner_npc_id:
            storage_location = _canonical_inventory_storage_location(
                self.repository, storage_location
            )
        value_base_units = max(
            1,
            _first_int(
                payload,
                1,
                "value_base_units",
                "base_unit_value",
                "value",
            ),
        )
        if catalog_entry is not None:
            value_base_units = max(
                value_base_units,
                _safe_int(catalog_entry.get("value_base_units"), default=0) or 0,
            )

        merged_metadata = {**catalog_metadata, **payload}
        if catalog_entry is not None:
            merged_metadata["item_uuid"] = str(
                catalog_metadata.get("item_uuid", payload.get("item_uuid", ""))
            ).strip()

        item_metadata = {
            **merged_metadata,
            "quantity_unit": quantity_unit,
            "storage_location": storage_location,
        }
        if not owner_npc_id:
            existing_items = self.repository.list_inventory_items()
            existing = next((item for item in existing_items if item["name"].casefold() == name.casefold()), None)
            if existing is not None and isinstance(existing["metadata"].get("container"), dict):
                return _invalid(event_type, payload, "An existing physical container must be moved or modified, not reacquired/reset.")
            destination = next((item for item in existing_items if item["name"].casefold() == storage_location.casefold() and isinstance(item["metadata"].get("container"), dict)), None)
            if destination is not None:
                access = self.repository.inventory_access()[str(destination["id"])]
                container = destination["metadata"]["container"]
                if not access["available"] or not container["is_open"] or container["is_locked"] or container["is_trapped"] or not container["contents_initialized"]:
                    return _invalid(event_type, payload, "Destination container must be accessible and opened before adding items.")
                clean_metadata = normalize_item_metadata(item_metadata, name=name, category=category, description=description)
                if not clean_metadata["moveable"] or not clean_metadata["storable"]:
                    return _invalid(event_type, payload, "Item cannot be moved/stored.")
            if existing is not None and str(existing["storage_location"]).casefold() != storage_location.casefold():
                return _invalid(event_type, payload, "Move the existing item instead of adding another stack at a different location.")
        if owner_npc_id:
            self.repository.add_party_inventory_item(
                owner_npc_id,
                name=name,
                category=category,
                quantity=quantity,
                description=description,
                value_base_units=value_base_units,
                metadata=item_metadata,
            )
        else:
            self.repository.add_inventory_item(
                name=name,
                category=category,
                quantity=quantity,
                description=description,
                value_base_units=value_base_units,
                metadata=item_metadata,
            )

        return AppliedEventResult(
            event_type,
            "applied",
            (
                f"Added inventory item: {quantity} x {name} to party member "
                f"{owner_npc_id}."
                if owner_npc_id
                else f"Added inventory item: {quantity} x {name}."
            ),
            payload,
        )

    def _apply_inventory_item_removed(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies InventoryItemRemovedEvent."""

        name = _first_text(payload, "item_name", "name")

        if not name:
            return _invalid(event_type, payload, "Inventory item name is required.")

        owner_npc_id = _first_text(payload, "owner_npc_id")
        if owner_npc_id and not self._is_current_party_member(owner_npc_id):
            return _invalid(
                event_type,
                payload,
                f"Party member is not active: {owner_npc_id}.",
            )
        quantity = _first_int(payload, 1, "amount", "quantity")
        if owner_npc_id:
            self.repository.remove_party_inventory_item(owner_npc_id, name, quantity)
        else:
            existing = next((item for item in self.repository.list_inventory_items() if item["name"].casefold() == name.casefold()), None)
            if existing is not None and not self.repository.inventory_access()[str(existing["id"])]["available"]:
                return _invalid(event_type, payload, "Cannot use or remove an item that is not currently accessible.")
            if existing is not None:
                contents = existing["metadata"].get("container", {}).get("contents", {})
                if quantity >= existing["quantity"] and (contents.get("items") or contents.get("currency_base_units")):
                    return _invalid(event_type, payload, "Empty the container first, or move it with its contents.")
            self.repository.remove_inventory_item(name, quantity)

        return AppliedEventResult(
            event_type,
            "applied",
            (
                f"Removed inventory item: {quantity} x {name} from party member "
                f"{owner_npc_id}."
                if owner_npc_id
                else f"Removed inventory item: {quantity} x {name}."
            ),
            payload,
        )

    def _apply_inventory_item_modified(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies InventoryItemModifiedEvent."""

        target_name = _first_text(payload, "target_name", "target", "item_name", "name")

        if not target_name:
            return _invalid(event_type, payload, "Inventory target name is required.")

        owner_npc_id = _first_text(payload, "owner_npc_id")
        if owner_npc_id and not self._is_current_party_member(owner_npc_id):
            return _invalid(
                event_type,
                payload,
                f"Party member is not active: {owner_npc_id}.",
            )

        if _find_inventory_container(self.repository, target_name) is not None and "container" in payload:
            return _invalid(
                event_type,
                payload,
                (
                    "Container state cannot be changed with "
                    "InventoryItemModifiedEvent; use ContainerOpenedEvent or "
                    "ContainerContentsTakenEvent."
                ),
            )

        quantity = _optional_int(payload, "new_amount", "quantity", "new_quantity")
        value_base_units = _optional_int(
            payload,
            "new_value_base_units",
            "value_base_units",
            "base_unit_value",
            "value",
        )
        metadata = dict(payload)
        new_storage_location = _first_text(
            payload, "new_storage_location", "storage_location"
        )
        if new_storage_location:
            if not owner_npc_id:
                new_storage_location = _canonical_inventory_storage_location(
                    self.repository, new_storage_location
                )
            if not owner_npc_id:
                requested_uuid = _first_text(payload, "item_uuid")
                target_item = next(
                    (item for item in self.repository.list_inventory_items()
                     if (requested_uuid and item["metadata"].get("item_uuid") == requested_uuid)
                     or item["name"].casefold() == target_name.casefold()),
                    None,
                )
                if target_item is None:
                    return _invalid(event_type, payload, "The item to move is not in inventory.")
                try:
                    self.repository.move_inventory_item(str(target_item["id"]), new_storage_location)
                except ValueError as error:
                    return _invalid(event_type, payload, str(error))
                target_name = str(target_item["name"])
                metadata.pop("storage_location", None)
            elif not _storage_location_is_accessible(self.repository, new_storage_location):
                return _invalid(
                    event_type,
                    payload,
                    (
                        f"Cannot move {target_name} to storage location "
                        f"{new_storage_location!r} from the player's current location."
                    ),
                )
            if owner_npc_id:
                metadata["storage_location"] = new_storage_location
        new_basic_name = _first_text(payload, "new_basic_name")
        if new_basic_name and new_basic_name.casefold() not in {"same", "skip"}:
            metadata["basic_name"] = new_basic_name

        if owner_npc_id:
            self.repository.modify_party_inventory_item(
                npc_id=owner_npc_id,
                target_name=target_name,
                new_name=_first_text(payload, "new_name"),
                category=_first_text(payload, "new_category", "category"),
                description=_first_text(payload, "new_description", "description"),
                quantity=quantity,
                value_base_units=value_base_units,
                metadata=metadata,
            )
        else:
            self.repository.modify_inventory_item(
                target_name=target_name,
                new_name=_first_text(payload, "new_name"),
                category=_first_text(payload, "new_category", "category"),
                description=_first_text(payload, "new_description", "description"),
                quantity=quantity,
                value_base_units=value_base_units,
                metadata=metadata,
            )

        return AppliedEventResult(
            event_type,
            "applied",
            (
                f"Modified inventory item: {target_name} for party member "
                f"{owner_npc_id}."
                if owner_npc_id
                else f"Modified inventory item: {target_name}."
            ),
            payload,
        )

    def _is_current_party_member(self, npc_id: str) -> bool:
        """Returns whether an NPC currently has party-specific state."""

        return any(
            str(member.get("npc_id", "")).strip() == npc_id.strip()
            for member in self.repository.list_party_members()
        )

    def _matching_item_catalog_entry(
        self,
        payload: dict[str, Any],
        item_name: str,
    ) -> dict[str, Any] | None:
        """Finds the authoritative catalog definition requested by Gemini."""

        requested_uuid = str(payload.get("item_uuid", "") or "").strip()
        folded_name = item_name.casefold()
        name_match: dict[str, Any] | None = None

        for entry in self.repository.list_item_catalog():
            metadata = entry.get("metadata", {})
            entry_uuid = (
                str(metadata.get("item_uuid", "") or "").strip()
                if isinstance(metadata, dict)
                else ""
            )
            if requested_uuid and entry_uuid == requested_uuid:
                return entry
            if str(entry.get("name", "")).casefold() == folded_name:
                name_match = entry

        return name_match

    def _apply_calendar_event_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Creates or updates a persistent calendar event."""

        event_id = _first_text(payload, "event_id", "id")
        player_event = next(
            (
                event
                for event in self.repository.list_calendar_events()
                if str(event.get("event_id", "")) == event_id
                and str(event.get("origin", "game")) == "player"
            ),
            None,
        )
        if player_event is not None:
            return _invalid(
                event_type,
                payload,
                "Player-created calendar events cannot be changed by game events.",
            )

        canonical_payload = dict(payload)
        canonical_payload["origin"] = "game"
        saved = self.repository.upsert_calendar_event(canonical_payload)
        if saved is None:
            return _invalid(event_type, payload, "Calendar event id and title are required.")
        return AppliedEventResult(
            event_type,
            "applied",
            f"Saved calendar event: {saved['title']}.",
            saved,
        )

    def _apply_calendar_event_deleted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Deletes a persistent calendar event."""

        event_id = _first_text(payload, "event_id", "id")
        if not event_id:
            return _invalid(event_type, payload, "Calendar event id is required.")
        stored_event = next(
            (
                event
                for event in self.repository.list_calendar_events()
                if str(event.get("event_id", "")) == event_id
            ),
            None,
        )
        if stored_event is not None and str(stored_event.get("origin", "game")) == "player":
            return _invalid(
                event_type,
                payload,
                "Player-created calendar events cannot be deleted by game events.",
            )
        deleted = self.repository.delete_calendar_event(event_id)
        return AppliedEventResult(
            event_type,
            "applied" if deleted else "skipped",
            f"Deleted calendar event: {event_id}." if deleted else f"Calendar event not found: {event_id}.",
            payload,
        )

    def _apply_container_opened(
        self,
        event_type: str,
        payload: dict[str, Any],
        d20_test_results: list[AppliedEventResult],
    ) -> AppliedEventResult:
        """Opens a container only after its stored requirements are satisfied."""

        container_name = _first_text(payload, "container_name", "item_name", "name")

        if not container_name:
            return _invalid(event_type, payload, "Container name is required.")

        item = _find_inventory_container(self.repository, container_name)

        if item is None:
            return _invalid(
                event_type,
                payload,
                f"Container is not in inventory: {container_name}.",
            )

        if not self.repository.inventory_access()[str(item["id"])]["available"]:
            return _invalid(event_type, payload, "Container is not currently accessible from the player's location.")

        metadata = dict(item.get("metadata", {}))
        container = dict(metadata.get("container", {}))

        if container.get("is_open") is True and container.get("contents_initialized", True) and container.get("contents_known", True):
            return AppliedEventResult(
                event_type,
                "skipped",
                f"{item['name']} is already open.",
                payload,
            )

        if container.get("is_locked") is True:
            has_unlock_method = has_immediate_container_unlock_method(
                self.repository.list_accessible_inventory_items(),
                str(item["name"]),
            )
            if not has_unlock_method:
                required_skill = str(container.get("lockpick_skill", "Lockpicking"))
                required_dc = max(
                    1,
                    _safe_int(container.get("lockpick_dc"), default=10) or 10,
                )

                if not _has_successful_d20_test(
                    d20_test_results,
                    skill_name=required_skill,
                    minimum_total=required_dc,
                    target=str(item["name"]),
                    attribute=str(container.get("lockpick_attribute", "Dexterity")),
                ):
                    consequence = str(
                        container.get("lockpick_failure_consequence", "") or ""
                    ).strip()
                    return _invalid(
                        event_type,
                        payload,
                        (
                            f"{item['name']} remains locked; opening it requires a "
                            f"successful {required_skill} check against DC {required_dc}."
                            + (
                                f" Failure consequence: {consequence}"
                                if consequence
                                else ""
                            )
                        ),
                    )

            container["is_locked"] = False

        if container.get("is_trapped") is True:
            required_skill = str(
                container.get("trap_disarm_skill", "Sleight of Hand")
            )
            required_dc = max(
                1,
                _safe_int(container.get("trap_disarm_dc"), default=10) or 10,
            )

            if not _has_successful_d20_test(
                d20_test_results,
                skill_name=required_skill,
                minimum_total=required_dc,
                target=str(item["name"]),
                attribute=str(container.get("trap_disarm_attribute", "Dexterity")),
            ):
                consequence = str(
                    container.get("trap_failure_consequence", "") or ""
                ).strip()
                return _invalid(
                    event_type,
                    payload,
                    (
                        f"{item['name']} remains trapped; opening it requires a "
                        f"successful {required_skill} check against DC {required_dc}."
                        + (f" Failure consequence: {consequence}" if consequence else "")
                    ),
                )

            container["is_trapped"] = False

        if not container.get("contents_initialized", True):
            try:
                validate_contents_manifest(payload.get("contents"))
            except ContainerFlowError as error:
                return _invalid(event_type, payload, str(error))
            normalized = normalize_item_metadata(
                {**item["metadata"], "container": {**container, "contents": payload["contents"], "contents_initialized": True}},
                name=str(item["name"]), category="Container",
            )
            container = normalized["container"]
        elif "contents" in payload:
            return _invalid(event_type, payload, "Already initialized contents cannot be replaced.")
        container["is_open"] = True
        container["contents_known"] = True
        metadata["container"] = container
        self.repository.modify_inventory_item(
            target_name=str(item["name"]),
            metadata=metadata,
        )
        return AppliedEventResult(
            event_type,
            "applied",
            f"Opened container: {item['name']}.",
            {**payload, "container_name": item["name"]},
        )

    def _apply_container_contents_taken(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Transfers an open container's stored contents exactly once."""

        container_name = _first_text(payload, "container_name", "item_name", "name")

        if not container_name:
            return _invalid(event_type, payload, "Container name is required.")

        item = _find_inventory_container(self.repository, container_name)

        if item is None:
            return _invalid(
                event_type,
                payload,
                f"Container is not in inventory: {container_name}.",
            )

        metadata = dict(item.get("metadata", {}))
        container = dict(metadata.get("container", {}))

        if container.get("is_open") is not True:
            return _invalid(
                event_type,
                payload,
                f"{item['name']} must be opened before its contents can be taken.",
            )

        if container.get("contents_taken") is True:
            return AppliedEventResult(
                event_type,
                "skipped",
                f"The contents of {item['name']} were already taken.",
                payload,
            )

        if not container.get("contents_initialized", True):
            return _invalid(event_type, payload, "Container contents have not been initialized.")
        contents = dict(container.get("contents", {}))
        try:
            currency_amount, selected_items, remaining_contents = selected_contents(contents, payload, self.repository.list_inventory_items())
        except ContainerFlowError as error:
            return _invalid(event_type, payload, str(error))
        transferred_items: list[dict[str, Any]] = []

        prospective = deepcopy(self.repository.list_inventory_items())
        cargo = self.repository.inventory_load()["cargo"]
        selected_items.sort(key=lambda selected: carry_priority(selected, cargo), reverse=True)
        for selected in selected_items:
            error = move_error(str(selected["id"]), "actively_carried", prospective, self.repository.get_state_value("location", ""), self.repository.player_carrying_capacity_lb(), self.repository.get_travel_locations())
            if error:
                return _invalid(event_type, payload, error)
            moved = next(row for row in prospective if str(row["id"]) == str(selected["id"]))
            moved["metadata"].pop("container_id", None)
            moved["metadata"]["storage_location"] = moved["storage_location"] = "actively_carried"
        if not self.repository.inventory_access()[str(item["id"])]["available"]:
            return _invalid(event_type, payload, "Container is not currently accessible from the player's location.")

        if currency_amount:
            current_balance = _safe_int(
                self.repository.get_state_value("currency.balance", "0"),
                default=0,
            ) or 0
            self.repository.set_state_value(
                "currency.balance",
                str(current_balance + currency_amount),
            )

        for raw_item in selected_items:
            if not isinstance(raw_item, dict):
                continue

            name = str(raw_item.get("name", "") or "").strip()

            if not name:
                continue

            category = str(raw_item.get("category", "Item") or "Item").strip()
            description = str(raw_item.get("description", "") or "").strip()
            quantity = max(
                1,
                _safe_int(raw_item.get("quantity"), default=1) or 1,
            )
            value_base_units = max(
                0,
                _safe_int(raw_item.get("value_base_units"), default=0) or 0,
            )
            item_metadata = normalize_item_metadata(
                raw_item.get("metadata", raw_item),
                name=name,
                category=category,
                description=description,
            )
            self.repository.move_inventory_item(str(raw_item["id"]), "actively_carried")
            transferred_items.append(
                {
                    "name": name,
                    "category": category,
                    "quantity": quantity,
                    "description": description,
                    "value_base_units": value_base_units,
                    "metadata": item_metadata,
                }
            )

        container["contents"] = remaining_contents
        container["contents_taken"] = not remaining_contents["currency_base_units"] and not remaining_contents["items"]
        metadata["container"] = container
        self.repository.modify_inventory_item(
            target_name=str(item["name"]),
            metadata=metadata,
        )
        return AppliedEventResult(
            event_type,
            "applied",
            (
                f"Transferred the stored contents of {item['name']}: "
                f"{currency_amount} base currency unit(s) and "
                f"{len(transferred_items)} item record(s)."
            ),
            {
                **payload,
                "container_name": item["name"],
                "currency_base_units": currency_amount,
                "items": transferred_items,
            },
        )


    def _apply_skill_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies SkillUpsertedEvent."""

        name = _first_text(payload, "name", "skill_name")

        if not name:
            return _invalid(event_type, payload, "Skill name is required.")

        existing = self.repository.get_skill(name)
        level = _first_int(payload, 1, "level")
        description = _first_text(payload, "description", "skill_description")
        if (existing is not None and level != existing["level"]) or (existing is None and level != 1):
            return _invalid(event_type, payload, "Skills advance through training XP or player rewards, not upserts.")
        if not existing and (not description or not _first_text(payload, "reason")):
            return _invalid(event_type, payload, "Learning requires a scope description and meaningful training reason.")
        self.repository.upsert_skill(name, description, level)

        skill = self.repository.get_skill(name)
        bonus = skill["bonus"] if skill is not None else bonus_for_level(level)

        return AppliedEventResult(
            event_type,
            "applied",
            f"Skill updated: {name} Level {level}, bonus +{bonus}.",
            payload,
        )

    def _apply_skill_xp_added(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies SkillXpAddedEvent."""

        name = _first_text(payload, "skill_name", "name")

        if not name:
            return _invalid(event_type, payload, "Skill name is required.")

        xp_amount = _optional_int(payload, "xp_amount", "amount", "xp")

        if xp_amount is None:
            xp_amount = 1

        if xp_amount <= 0:
            return _invalid(event_type, payload, "Positive XP amount is required.")

        existing_skill = self.repository.get_skill(name)
        if existing_skill is not None and int(existing_skill["level"]) >= MAX_SKILL_LEVEL:
            return _invalid(event_type, payload, f"{name} is already at Max Level and cannot gain XP.")

        message_source = f"{self.message_id}:{name.casefold()}:training" if self.message_id else ""
        source_id = _first_text(payload, "source_id") or message_source
        if any(self.repository._has_progression_record("skill_training", key)
               for key in {source_id, message_source} if key):
            return AppliedEventResult(event_type, "skipped", "Training XP already recorded for this source.", payload)
        skill = self.repository.add_skill_xp(name, xp_amount)

        if skill is None:
            return _invalid(event_type, payload, f"Skill does not exist: {name}.")

        if source_id:
            self.repository._progression_record("skill_training", source_id, {"skill_name": name, "xp": xp_amount})
        if message_source and message_source != source_id:
            self.repository._progression_record("skill_training", message_source, {"skill_name": name, "xp": xp_amount})
        return AppliedEventResult(
            event_type,
            "applied",
            f"Added {xp_amount} XP to {name}. Level {skill['level']}, bonus +{skill['bonus']}.",
            payload,
        )

    def _apply_d20_test_requested(self, event_type: str, payload: dict[str, Any]) -> AppliedEventResult:
        from ai_adventure.stats import ATTRIBUTES
        import uuid
        attribute = str(payload.get("attribute", "")).title()
        kind = str(payload.get("test_kind", "check")).casefold()
        reason = _first_text(payload, "reason")
        if attribute not in ATTRIBUTES or kind not in {"check", "attack", "save"} or not reason:
            return _invalid(event_type, payload, "A valid attribute, test_kind, and consequential reason are required.")
        if self.repository.player_stats()["health_current"] == 0 and (kind == "attack" or kind == "check" and attribute in {"Strength", "Dexterity", "Constitution"}):
            return _invalid(event_type, payload, "The player is incapacitated and cannot take strenuous actions.")
        name = _first_text(payload, "skill_name")
        skill = self.repository.get_skill(name) if name else None
        if name and skill is None:
            return _invalid(event_type, payload, "Optional skill must be an existing learned skill; omit it for an untrained test.")
        dc = _optional_int(payload, "dc")
        dc = dc_for_difficulty(payload.get("difficulty")) if dc is None else dc
        if dc < 1:
            return _invalid(event_type, payload, "DC must be positive.")
        source = str(payload.get("request_id") or (uuid.uuid5(uuid.NAMESPACE_URL, str(self.message_id) + attribute + kind + name + reason).hex if self.message_id else uuid.uuid4().hex))
        previous = self.repository.find_d20_test(source)
        if previous:
            return AppliedEventResult(event_type, "applied", "Previously resolved d20 test; dice reused.", previous)
        advantage = payload.get("advantage", False)
        disadvantage = payload.get("disadvantage", False)
        if type(advantage) is not bool or type(disadvantage) is not bool:
            return _invalid(event_type, payload, "Advantage/disadvantage must be booleans.")
        rolls = [self.rng.randint(1, 20) for _ in range(2 if advantage != disadvantage else 1)]
        roll = max(rolls) if advantage and not disadvantage else min(rolls)
        modifier = self.repository.player_stats()["modifiers"][attribute]
        level = int(skill["level"]) if skill else 0
        total = roll + modifier + level
        resolved = {**payload, "attribute": attribute, "test_kind": kind, "attribute_modifier": modifier,
                    "skill_bonus": level, "skill_name": skill["name"] if skill else "", "level": level,
                    "bonus": modifier + level, "roll": roll, "rolls": rolls, "total": total, "dc": dc,
                    "outcome": "success" if total >= dc else "failure", "reason": reason,
                    "message_id": self.message_id or "", "request_id": source}
        with self.repository.transaction():
            self.repository.record_d20_test(**resolved)
            if skill and level < MAX_SKILL_LEVEL:
                training = self._apply_skill_xp_added("SkillXpAddedEvent", {
                    "skill_name": skill["name"], "xp_amount": 1,
                    "source_id": (f"{self.message_id}:{skill['name'].casefold()}:training"
                                  if self.message_id else f"d20:{source}:training"),
                })
                if training.status == "applied":
                    LOGGER.info("Awarded 1 training XP to %s for d20 test %s (%s), message_ID=%s.",
                                skill["name"], source, resolved["outcome"], self.message_id or "")
        return AppliedEventResult(event_type, "applied", f"{attribute} {kind}: {total} vs DC {dc} ({resolved['outcome']}).", resolved)

    def _apply_player_achievement(self, event_type: str, payload: dict[str, Any]) -> AppliedEventResult:
        try:
            result = self.repository.record_player_achievement(
                _first_text(payload, "source_id"), _first_text(payload, "significance"),
                _first_text(payload, "reason"), source_kind=_first_text(payload, "source_kind") or "milestone")
            return AppliedEventResult(event_type, "applied" if result["status"] == "applied" else "skipped", "Player achievement recorded.", {**payload, **result})
        except ValueError as exc:
            return _invalid(event_type, payload, str(exc))

    def _apply_player_health(self, event_type: str, payload: dict[str, Any]) -> AppliedEventResult:
        try:
            result = self.repository.change_player_health(payload.get("delta"), _first_text(payload, "reason"), _first_text(payload, "source_id"))
            return AppliedEventResult(event_type, "applied" if result["status"] == "applied" else "skipped", "Player health updated.", {**payload, **result})
        except ValueError as exc:
            return _invalid(event_type, payload, str(exc))


    def _apply_status_updated(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies StatusUpdatedEvent."""

        location = _first_text(payload, "location", "new_location")
        weather = _first_text(payload, "weather")
        minutes_passed = _optional_int(payload, "minutes_passed", "minutes", "time")

        if location and location.upper() not in {"AUTO", "SAME", "SKIP"}:
            clean_location = clean_player_location_name(location)
            self.repository.set_state_value("location", clean_location)

            if payload.get("discover_location") is True:
                self.repository.upsert_travel_location({"name": clean_location})

        if weather and weather.upper() not in {"AUTO", "SAME", "SKIP"}:
            self.repository.set_state_value("weather", weather)

        if minutes_passed is not None and minutes_passed >= 0:
            current_total = self.repository.get_current_calendar_minute()
            new_total = current_total + minutes_passed
            self.repository.set_current_calendar_minute(new_total)
            calendar_snapshot = build_calendar_snapshot(
                new_total,
                self.repository.get_calendar_settings(),
            )
            self.repository.set_state_value(
                "time",
                str(calendar_snapshot["display_label"]),
            )

        return AppliedEventResult(
            event_type,
            "applied",
            "Updated status fields.",
            payload,
        )

    def _apply_location_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Stores one player-known, map-aware location for the Travel screen."""

        name = _first_text(payload, "name", "location", "location_name")
        has_x = "x_miles" in payload or "x" in payload
        has_y = "y_miles" in payload or "y" in payload

        if not name:
            return _invalid(event_type, payload, "Location name is required.")

        if not has_x or not has_y:
            return _invalid(
                event_type,
                payload,
                "Location map coordinates x_miles and y_miles are required.",
            )

        location = {
            "name": name,
            "description": _first_text(payload, "description", "text", "lore"),
            "x_miles": payload.get("x_miles", payload.get("x")),
            "y_miles": payload.get("y_miles", payload.get("y")),
            "terrain": _first_text(payload, "terrain"),
            "travel_multiplier": payload.get("travel_multiplier", 1.0),
            "travel_notes": _first_text(payload, "travel_notes", "route_notes"),
            "is_sublocation": bool(payload.get("is_sublocation")),
            "parent_location": _first_text(payload, "parent_location"),
            "location_scope": payload.get("location_scope", "broad"),
        }

        if not self.repository.upsert_travel_location(location):
            return _invalid(event_type, payload, "Location metadata was invalid.")

        self.repository.append_history(
            "world",
            f"Recorded travel location: {clean_player_location_name(name)}.",
        )
        return AppliedEventResult(
            event_type,
            "applied",
            "Recorded travel location.",
            payload,
        )

    def _apply_travel_mode_changed(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Updates the hidden speed multiplier for a changed travel arrangement."""

        mode = _first_text(payload, "mode", "travel_mode")
        raw_speed_multiplier = payload.get("speed_multiplier")

        if raw_speed_multiplier is None:
            return _invalid(event_type, payload, "Travel speed multiplier is required.")

        try:
            speed_multiplier = float(raw_speed_multiplier)
        except (TypeError, ValueError):
            return _invalid(event_type, payload, "Travel speed multiplier is required.")

        if not 0.1 <= speed_multiplier <= 20.0:
            return _invalid(
                event_type,
                payload,
                "Travel speed multiplier must be between 0.1 and 20.",
            )

        if not self.repository.set_travel_mode(mode, speed_multiplier):
            return _invalid(event_type, payload, "Travel mode is required.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Updated travel mode to {mode}.",
            payload,
        )

    def _apply_flag_set(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies FlagSetEvent."""

        key = _first_text(payload, "key", "name", "flag")

        if not key:
            return _invalid(event_type, payload, "Flag key is required.")

        value = payload.get("value", True)
        self.repository.set_state_value(f"flag.{key}", str(value))

        return AppliedEventResult(event_type, "applied", f"Set flag: {key}.", payload)

    def _apply_music_changed(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies MusicChangedEvent."""

        filename = _first_text(
            payload,
            "filename",
            "file_name",
            "track",
            "track_name",
            "music",
        )

        if not filename:
            return _invalid(event_type, payload, "Music filename is required.")

        self.repository.set_setting("audio.current_music", filename)

        return AppliedEventResult(
            event_type,
            "applied",
            f"Changed background music to: {filename}.",
            payload,
        )

    def _apply_recipe_discovered(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies RecipeDiscoveredEvent."""

        name = _first_text(payload, "name", "item_name", "recipe_name")

        if not name:
            return _invalid(event_type, payload, "Recipe name is required.")

        ingredients = normalize_recipe_ingredients(payload.get("ingredients", []))

        if not ingredients:
            return _invalid(event_type, payload, "Recipe ingredients are required.")

        known_reagent_names = {
            str(item.get("name", "")).casefold()
            for item in self.repository.list_item_catalog()
            if str(item.get("name", "")).strip()
            and is_crafting_ingredient_category(item.get("category", ""))
        }
        unknown_ingredients = [
            ingredient["reagent_name"]
            for ingredient in ingredients
            if ingredient["reagent_name"].casefold() not in known_reagent_names
        ]

        if unknown_ingredients:
            return _invalid(
                event_type,
                payload,
                "Recipe ingredients must be known items with category "
                f"{CRAFTING_INGREDIENT_CATEGORY_NAMES}: "
                + ", ".join(unknown_ingredients),
            )

        self.repository.add_crafting_recipe(
            name=name,
            ingredients=ingredients,
            result=_first_text(payload, "result", "description"),
            result_item_uuid=_first_text(payload, "result_item_uuid"),
            result_item_name=_first_text(payload, "result_item_name", "result"),
            result_weight_lb=payload.get("result_weight_lb"),
            skill_name=_first_text(payload, "skill_name") or "Crafting",
            stages=payload.get("stages", []),
            required_tool_item_uuids=_as_string_list(payload.get("required_tool_item_uuids", [])),
            required_tool_item_names=_as_string_list(payload.get("required_tool_item_names", [])),
            notes=_first_text(payload, "notes"),
            value_base_units=max(
                0,
                _safe_int(payload.get("value_base_units"), default=0) or 0,
            ),
        )

        return AppliedEventResult(
            event_type,
            "applied",
            f"Discovered recipe: {name}.",
            payload,
        )

    def _apply_crafting_process_requested(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Advances a recipe through the deterministic crafting service."""

        recipe_id = _first_text(payload, "recipe_id")
        if not recipe_id:
            return _invalid(event_type, payload, "Recipe database id is required.")
        quantity = max(1, _first_int(payload, 1, "quantity", "amount"))
        result = self.repository.craft_recipe(recipe_id, quantity=quantity)
        status = str(result.get("status", "rejected"))
        return AppliedEventResult(
            event_type,
            "applied" if status in {"active", "passive", "ready", "completed"} else "skipped",
            str(result.get("message", "Crafting request was rejected.")),
            {**payload, "crafting_status": status},
        )

    def _apply_reagent_discovered(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies ReagentDiscoveredEvent."""

        name = _first_text(payload, "name", "reagent_name")

        if not name:
            return _invalid(event_type, payload, "Item name is required.")

        description = _first_text(payload, "description", "notes")
        location = _first_text(payload, "location", "found_at", "source")
        uses = _as_string_list(payload.get("uses", []))
        rarity = normalize_crafting_item_rarity(payload.get("rarity"))
        notes = _first_text(payload, "notes")
        value_base_units = max(
            0,
            _safe_int(payload.get("value_base_units"), default=0) or 0,
        )
        category = _first_text(payload, "category") or "Material"
        if not is_crafting_ingredient_category(category):
            return _invalid(
                event_type,
                payload,
                "Reagent category must be one of "
                f"{CRAFTING_INGREDIENT_CATEGORY_NAMES}.",
            )

        if not description:
            return _invalid(event_type, payload, "Reagent description is required.")

        if not location:
            return _invalid(event_type, payload, "Reagent location is required.")

        if not uses:
            return _invalid(event_type, payload, "Reagent uses are required.")

        self.repository.add_crafting_item(
            name=name,
            category=category,
            description=description,
            location=location,
            uses=uses,
            rarity=rarity,
            notes=notes,
            value_base_units=value_base_units,
            item_uuid=_first_text(payload, "item_uuid"),
        )
        self.repository.upsert_item_catalog_entry(
            name=name,
            category=category,
            description=description,
            value_base_units=value_base_units,
            metadata={"item_uuid": _first_text(payload, "item_uuid")},
        )

        return AppliedEventResult(
            event_type,
            "applied",
            f"Discovered reagent: {name}.",
            payload,
        )

    def _apply_currency_changed(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies CurrencyChangedEvent."""

        amount = _optional_int(
            payload,
            "base_unit_amount",
            "base_units",
            "delta_base_units",
            "amount",
        )

        if amount is None:
            return _invalid(event_type, payload, "Currency amount is required.")

        current_balance = _safe_int(
            self.repository.get_state_value("currency.balance", "0"),
            default=0,
        ) or 0
        new_balance = current_balance + amount
        self.repository.set_state_value("currency.balance", str(new_balance))
        denominations = self.repository.get_currency_denominations()

        return AppliedEventResult(
            event_type,
            "applied",
            (
                "Currency balance changed by "
                f"{format_currency_amount(amount, denominations)}. "
                f"New balance: {format_currency_amount(new_balance, denominations)}."
            ),
            {**payload, "base_unit_amount": amount, "balance_base_units": new_balance},
        )

    def _apply_currency_defined(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies CurrencyDefinedEvent."""

        name = _first_text(payload, "name")
        value = _optional_int(payload, "base_unit_value", "value")

        if not name or value is None or value <= 0:
            return _invalid(event_type, payload, "Currency name and positive value are required.")

        denominations = self.repository.get_currency_denominations()
        matching_index = next(
            (
                index
                for index, denomination in enumerate(denominations)
                if str(denomination["name"]).casefold() == name.casefold()
            ),
            None,
        )

        new_denomination = {
            "name": name,
            "plural_name": _first_text(payload, "plural_name") or f"{name}s",
            "value": value,
        }

        if matching_index is None:
            denominations.append(new_denomination)
        else:
            denominations[matching_index] = new_denomination

        self.repository.set_currency_denominations(denominations)
        return AppliedEventResult(
            event_type,
            "applied",
            f"Defined currency denomination: {name}.",
            payload,
        )

    def _apply_active_task_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies ActiveTaskUpsertedEvent for creates and updates."""

        name = _first_text(payload, "name", "title", "task_name")

        if not name:
            return _invalid(event_type, payload, "Active task name is required.")

        existing_task = self.repository.get_active_task(name)
        category = (
            _first_text(payload, "category", "type")
            or str((existing_task or {}).get("category", "")).strip()
            or "Task"
        )
        description = (
            _first_text(payload, "description", "objective", "summary")
            or str((existing_task or {}).get("description", "")).strip()
        )
        if not description:
            return _invalid(
                event_type,
                payload,
                "An active task requires a player-visible description.",
            )
        status = (
            _first_text(payload, "status")
            or str((existing_task or {}).get("status", "")).strip()
            or "Active"
        )
        default_fields = _active_task_defaults(
            self.repository,
            name=name,
            category=category,
            description=description,
            requester=_first_text(payload, "requester", "giver", "client", "npc"),
            location=_first_text(payload, "location", "turn_in"),
            reward=_first_text(payload, "reward", "payment"),
            due_date="",
            existing_task=existing_task,
        )
        due_fields = _active_task_due_fields(
            self.repository,
            payload,
            due_date=_first_text(payload, "due_date", "deadline", "due"),
        )
        default_fields["due_date"] = _task_field_value(
            provided=due_fields["due_date"],
            existing=existing_task,
            field_name="due_date",
            default="N/A",
        )
        task = self.repository.upsert_active_task(
            name=name,
            category=category,
            status=status,
            description=description,
            requester=default_fields["requester"],
            location=default_fields["location"],
            reward=default_fields["reward"],
            due_date=default_fields["due_date"],
            due_elapsed_minutes=due_fields["due_elapsed_minutes"],
        )

        if task is None:
            return _invalid(event_type, payload, "Active task could not be stored.")

        if category.casefold() == "quest":
            self.repository.set_state_value(f"quest.{name}.status", task["status"].casefold())

        return AppliedEventResult(
            event_type,
            "applied",
            f"Stored active task: {name}.",
            {**payload, "name": name, **default_fields},
        )

    def _apply_active_task_completed(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies ActiveTaskCompletedEvent."""

        name = _first_text(payload, "name", "title", "task_name")

        if not name:
            return _invalid(event_type, payload, "Active task name is required.")

        task = self.repository.complete_active_task(
            name,
            _first_text(payload, "notes", "resolution", "outcome"),
        )

        if task is None:
            return _invalid(event_type, payload, f"Active task does not exist: {name}.")

        if str(task.get("category", "")).casefold() == "quest":
            self.repository.set_state_value(f"quest.{name}.status", "completed")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Completed active task: {name}.",
            payload,
        )

    def _apply_spell_catalog_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Stores one authoritative spell definition without granting it."""

        name = _first_text(payload, "name")
        if not name:
            return _invalid(event_type, payload, "Spell name is required.")
        spell = self.repository.upsert_spell_catalog(
            spell_id=_first_text(payload, "spell_id"),
            name=name,
            tier=int(_safe_int(payload.get("tier", 0), default=0) or 0),
            school=_first_text(payload, "school"),
            description=_first_text(payload, "description"),
            casting_time=_first_text(payload, "casting_time") or "Action",
            range=_first_text(payload, "range"),
            duration=_first_text(payload, "duration"),
            requirements=_first_text(payload, "requirements"),
            mana_cost=int(_safe_int(payload.get("mana_cost", 0), default=0) or 0),
        )
        if spell is None:
            return _invalid(event_type, payload, "Spell could not be stored.")
        payload["spell_id"] = spell["spell_id"]
        return AppliedEventResult(event_type, "applied", f"Cataloged spell: {name}.", payload)

    def _apply_character_spell_learned(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Stores a complete spell definition and grants it to the player."""

        catalog_result = self._apply_spell_catalog_upserted(event_type, payload)
        if catalog_result.status != "applied":
            return catalog_result
        spell_id = str(catalog_result.payload["spell_id"])
        learned = self.repository.learn_character_spell(
            spell_id,
            prepared=bool(payload.get("prepared", True)),
            source=_first_text(payload, "source") or "Story",
        )
        if learned is None:
            return _invalid(event_type, payload, "Spell could not be learned.")
        name = str(learned["name"])
        self.repository.append_history("spell", f"Learned spell: {name}.")
        return AppliedEventResult(event_type, "applied", f"Learned spell: {name}.", payload)

    def _apply_player_spell_cast(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Validates an explicitly player-authorized cast and consumes resources."""

        if not bool(payload.get("player_authorized", False)):
            return _invalid(
                event_type,
                payload,
                "A player spell cast requires explicit player authorization.",
            )
        spell_id = _first_text(payload, "spell_id")
        if not spell_id:
            return _invalid(event_type, payload, "spell_id is required for casting.")
        result = self.repository.cast_character_spell(
            spell_id,
            cast_tier=int(_safe_int(payload.get("cast_tier", 0), default=0) or 0),
            target=_first_text(payload, "target"),
            message_id=self.message_id or "",
        )
        status = str(result.get("status", "rejected"))
        if status != "cast":
            return AppliedEventResult(
                event_type, "skipped", str(result.get("message", "Cast rejected.")), payload
            )
        payload.update(result)
        return AppliedEventResult(event_type, "applied", str(result["message"]), payload)

    def _apply_magic_advancement_recorded(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Records validated evidence of meaningful magical development."""

        result = self.repository.record_magic_advancement(
            category=_first_text(payload, "category"),
            reason=_first_text(payload, "reason"),
            significance=_first_text(payload, "significance") or "meaningful",
            spell_id=_first_text(payload, "spell_id"),
            source=_first_text(payload, "source"),
            message_id=self.message_id,
        )
        status = str(result.get("status", "rejected"))
        if status == "recorded":
            entry = result.get("entry", {})
            if isinstance(entry, dict):
                payload["advancement_id"] = str(entry.get("advancement_id", ""))
            return AppliedEventResult(
                event_type,
                "applied",
                str(result.get("message", "Recorded meaningful magic advancement.")),
                payload,
            )
        return AppliedEventResult(
            event_type,
            "skipped",
            str(result.get("message", "Magic advancement was not recorded.")),
            payload,
        )

    def _apply_background_ambience_changed(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Starts, replaces, or stops persistent looping background ambience."""

        filename = _first_text(
            payload,
            "filename",
            "file_name",
            "track",
            "track_name",
            "ambience",
        )
        if not filename:
            return _invalid(event_type, payload, "Ambience filename or STOP is required.")

        if filename.casefold() in {"stop", "none", "off", "silence"}:
            self.repository.set_setting("audio.current_background_ambience", "")
            message = "Stopped background ambience."
        else:
            self.repository.set_setting("audio.current_background_ambience", filename)
            message = f"Changed background ambience to: {filename}."

        return AppliedEventResult(event_type, "applied", message, payload)

    def _apply_magic_effect_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Tracks one active magical effect after narration establishes it."""

        effect = self.repository.upsert_active_magic_effect(
            effect_id=_first_text(payload, "effect_id"),
            spell_id=_first_text(payload, "spell_id"),
            name=_first_text(payload, "name"),
            target=_first_text(payload, "target"),
            description=_first_text(payload, "description"),
            start_elapsed_minutes=int(
                _safe_int(payload.get("start_elapsed_minutes", -1), default=-1) or 0
            ),
            end_elapsed_minutes=int(
                _safe_int(payload.get("end_elapsed_minutes", -1), default=-1) or 0
            ),
            requires_concentration=bool(payload.get("requires_concentration", False)),
            active=bool(payload.get("active", True)),
        )
        if effect is None:
            return _invalid(event_type, payload, "Magic effect name is required.")
        payload["effect_id"] = effect["effect_id"]
        return AppliedEventResult(
            event_type, "applied", f"Updated magic effect: {effect['name']}.", payload
        )

    def _apply_npc_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies NpcUpsertedEvent."""

        raw_display_name = _first_text(payload, "display_name", "visible_name")
        raw_role = _first_text(payload, "role", "occupation")
        display_name = _safe_generated_npc_display_name(raw_display_name, raw_role)
        internal_name = _first_text(payload, "internal_name")
        npc_id = _first_text(payload, "npc_id", "id") or internal_name
        name = (
            _first_text(payload, "name", "npc_name")
            or internal_name
            or display_name
            or raw_role
            or npc_id
        )

        if not name:
            return _invalid(
                event_type,
                payload,
                "NPC name, display_name, role, or npc_id is required.",
            )

        role = raw_role or _fallback_npc_role(display_name=display_name, name=name)
        location = _first_text(payload, "location") or _current_player_location(self.repository)
        public_description = _first_text(
            payload,
            "public_description",
            "description",
            "appearance",
        )
        gender_identity = _first_text(payload, "gender_identity", "gender")
        age = _first_text(payload, "age")
        species = _first_text(payload, "species", "race")
        player_facing_information = _first_text(
            payload,
            "player_facing_information",
            "player_facing_summary",
            "player_known_information",
        )
        knowledge_scope = _npc_knowledge_scope(
            payload,
            role=role,
            location=location,
        )
        known_facts = _npc_known_facts(
            payload,
            player_facing_information=player_facing_information,
            public_description=public_description,
            role=role,
            location=location,
        )

        npc = self.repository.upsert_npc(
            npc_id=npc_id,
            name=name,
            display_name=display_name,
            role=role,
            location=location,
            public_description=public_description,
            player_facing_information=player_facing_information,
            gender_identity=gender_identity,
            age=age,
            species=species,
            knowledge_scope=knowledge_scope,
            known_facts=known_facts,
        )

        if npc is None:
            return _invalid(event_type, payload, "NPC could not be stored.")

        merchant_profile = payload.get("merchant_profile")
        if isinstance(merchant_profile, dict):
            self.repository.upsert_merchant_profile(
                str(npc["npc_id"]),
                can_sell=bool(merchant_profile.get("can_sell", False)),
                can_buy=bool(merchant_profile.get("can_buy", False)),
            )

        has_party_fields = any(
            key in payload
            for key in (
                "party_status",
                "party_combat_style",
                "party_skills",
            )
        )
        if payload.get("party_member") is False:
            self.repository.remove_party_member(str(npc["npc_id"]))
        elif payload.get("party_member") is True or has_party_fields:
            party_member = self.repository.upsert_party_member(
                str(npc["npc_id"]),
                status=(
                    _first_text(payload, "party_status")
                    if "party_status" in payload
                    else None
                ),
                combat_style=(
                    _first_text(payload, "party_combat_style")
                    if "party_combat_style" in payload
                    else None
                ),
                skills=(
                    _text_list(payload.get("party_skills", []))
                    if "party_skills" in payload
                    else None
                ),
            )
            if party_member is None:
                return _invalid(event_type, payload, "Party member could not be stored.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Stored NPC profile: {npc['name']}.",
            {**payload, "npc_id": npc["npc_id"]},
        )

    def _merchant_catalog_id(self, payload: dict[str, Any]) -> str | None:
        name = _first_text(payload, "item_name", "name")
        if not name:
            return None
        self.repository.upsert_item_catalog_entry(
            name=name,
            category=_first_text(payload, "item_type", "category") or "Item",
            description=_first_text(payload, "description"),
            value_base_units=max(0, _safe_int(payload.get("value_base_units"), default=0) or 0),
            metadata=payload,
        )
        for item in self.repository.list_item_catalog():
            if str(item.get("name", "")).casefold() == name.casefold():
                return str(item.get("id", ""))
        return None

    def _apply_merchant_stock_upserted(self, event_type: str, payload: dict[str, Any]) -> AppliedEventResult:
        npc_id = _first_text(payload, "npc_id")
        item_id = self._merchant_catalog_id(payload)
        if not npc_id or not item_id:
            return _invalid(event_type, payload, "Merchant NPC and item name are required.")
        if self.repository.get_npc(npc_id) is None:
            return _invalid(event_type, payload, "Merchant NPC does not exist.")
        self.repository.upsert_merchant_profile(npc_id, can_sell=True, can_buy=True)
        stock = self.repository.upsert_merchant_stock(
            npc_id=npc_id, item_id=item_id,
            stock_id=_first_text(payload, "stock_id"),
            quantity=max(0, _safe_int(payload.get("quantity"), default=0) or 0),
            unit_price_base_units=max(0, _safe_int(payload.get("unit_price_base_units"), default=0) or 0),
        )
        return AppliedEventResult(event_type, "applied", f"Updated merchant stock: {stock['item_name']}." if stock else "Merchant stock updated.", {**payload, "stock_id": stock["stock_id"] if stock else ""})

    def _apply_merchant_buy_offer_upserted(self, event_type: str, payload: dict[str, Any]) -> AppliedEventResult:
        npc_id = _first_text(payload, "npc_id")
        item_id = self._merchant_catalog_id(payload)
        if not npc_id or not item_id:
            return _invalid(event_type, payload, "Merchant NPC and item name are required.")
        if self.repository.get_npc(npc_id) is None:
            return _invalid(event_type, payload, "Merchant NPC does not exist.")
        self.repository.upsert_merchant_profile(npc_id, can_sell=True, can_buy=True)
        offer = self.repository.upsert_merchant_buy_offer(
            npc_id=npc_id, item_id=item_id,
            offer_id=_first_text(payload, "offer_id"),
            unit_price_base_units=max(0, _safe_int(payload.get("unit_price_base_units"), default=0) or 0),
            max_quantity=max(0, _safe_int(payload.get("max_quantity"), default=0) or 0),
        )
        return AppliedEventResult(event_type, "applied", f"Updated merchant buy offer: {offer['item_name']}." if offer else "Merchant offer updated.", {**payload, "offer_id": offer["offer_id"] if offer else ""})

    def _apply_npc_knowledge_added(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies NpcKnowledgeAddedEvent."""

        facts = _as_string_list(payload.get("facts", payload.get("fact", [])))

        if not facts:
            return _invalid(event_type, payload, "NPC knowledge fact is required.")

        npc = self.repository.add_npc_knowledge(
            npc_id=_first_text(payload, "npc_id", "id"),
            name=_first_text(payload, "name", "npc_name"),
            facts=facts,
            role=_first_text(payload, "role", "occupation"),
            location=_first_text(payload, "location") or _current_player_location(self.repository),
        )

        if npc is None:
            return _invalid(event_type, payload, "NPC could not be resolved.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Updated NPC knowledge: {npc['name']}.",
            {**payload, "npc_id": npc["npc_id"], "facts": facts},
        )

    def _apply_secret_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies SecretUpsertedEvent to private database-backed GM memory."""

        title = _first_text(payload, "title", "name")
        details = _first_text(payload, "details", "hidden_information")
        status = (_first_text(payload, "status") or "active").casefold()

        if not title:
            return _invalid(event_type, payload, "Secret title is required.")

        if not details:
            return _invalid(event_type, payload, "Secret details are required.")

        if status not in GM_SECRET_STATUSES:
            return _invalid(
                event_type,
                payload,
                "Secret status must be active, revealed, or retired.",
            )

        secret = self.repository.upsert_gm_secret(
            secret_id=_first_text(payload, "secret_id", "id"),
            title=title,
            details=details,
            reveal_condition=_first_text(payload, "reveal_condition"),
            related_npc_ids=_as_string_list(payload.get("related_npc_ids", [])),
            related_locations=_as_string_list(payload.get("related_locations", [])),
            status=status,
        )

        if secret is None:
            return _invalid(event_type, payload, "Secret could not be stored.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Stored private GM secret: {secret['title']}.",
            {**payload, "secret_id": secret["secret_id"], "status": secret["status"]},
        )

    def _apply_miscellaneous_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies general canon that does not belong to another state table."""

        name = _first_text(payload, "name", "title")
        details = _first_text(payload, "details", "description")

        if not name:
            return _invalid(event_type, payload, "Miscellaneous entry name is required.")

        if not details:
            return _invalid(event_type, payload, "Miscellaneous entry details are required.")

        if str(payload.get("category", "")).strip().casefold() in {
            "creature", "creatures", "monster", "monsters", "beast", "beasts",
        }:
            return _invalid(
                event_type,
                payload,
                "Creature lore must use BestiaryEntryUpsertedEvent.",
            )

        entry = self.repository.upsert_miscellaneous(
            misc_id=_first_text(payload, "misc_id", "id"),
            name=name,
            category=_first_text(payload, "category") or "Miscellaneous",
            details=details,
        )

        if entry is None:
            return _invalid(event_type, payload, "Miscellaneous entry could not be stored.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Stored miscellaneous world lore: {entry['name']}.",
            {**payload, "misc_id": entry["misc_id"]},
        )

    def _apply_bestiary_entry_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies player-known creature lore to the separate Bestiary table."""

        name = _first_text(payload, "name", "title")
        details = _first_text(payload, "details", "description")
        if not name or not details:
            return _invalid(event_type, payload, "Bestiary name and details are required.")

        entry = self.repository.upsert_bestiary_entry(
            creature_id=_first_text(payload, "creature_id", "id"),
            name=name,
            details=details,
        )
        if entry is None:
            return _invalid(event_type, payload, "Bestiary entry could not be stored.")
        return AppliedEventResult(
            event_type,
            "applied",
            f"Stored Bestiary creature: {entry['name']}.",
            {**payload, "creature_id": entry["creature_id"]},
        )


def normalize_event(raw_event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    Normalizes Gemini event dictionaries.

    Supports both {"type": "...", "payload": {...}} and flat event objects.
    """

    event_type = _event_type_from_raw_event(raw_event)

    if not event_type:
        event_type = "UnknownEvent"

    raw_payload = raw_event.get("payload", {})

    if isinstance(raw_payload, dict):
        payload = dict(raw_payload)
    else:
        payload = {}

    for key, value in raw_event.items():
        if key not in {"type", "event_type", "eventType", "payload"} and key not in payload:
            payload[key] = value

    payload = _sanitize_ai_event_payload(event_type, payload)
    return event_type, payload


def _sanitize_ai_event_payload(
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Removes banned generated-name terms from AI event payloads."""

    clean_payload = dict(payload)

    if event_type == "NpcUpsertedEvent":
        for key in ["display_name", "visible_name"]:
            value = str(clean_payload.get(key, "")).strip()

            if value and contains_banned_creative_term(value):
                LOGGER.warning(
                    "Removed banned generated NPC display_name %r before storage.",
                    value,
                )
                clean_payload[key] = ""

    banned_terms = find_banned_creative_terms(clean_payload)

    if not banned_terms:
        return clean_payload

    LOGGER.warning(
        "Sanitized banned creative term(s) from %s payload before storage: %s.",
        event_type,
        ", ".join(banned_terms),
    )
    return sanitize_banned_creative_terms_in_data(clean_payload)


def _event_type_from_raw_event(raw_event: dict[str, Any]) -> str:
    """Reads a Gemini event type from supported event-type keys."""

    for key in ["type", "event_type", "eventType"]:
        event_type = str(raw_event.get(key, "")).strip()

        if event_type:
            return event_type

    return ""


def _raw_event_protects_container_contents(raw_event: dict[str, Any]) -> bool:
    """Returns whether an event batch invokes the authoritative container flow."""

    event_type, payload = normalize_event(raw_event)

    if event_type in {"ContainerOpenedEvent", "ContainerContentsTakenEvent"}:
        return True

    if event_type != "InventoryItemAddedEvent":
        return False

    container = payload.get("container", {})
    return (
        str(payload.get("item_type", "")).strip().casefold() in {"container", "vehicle"}
        and isinstance(container, dict)
        and container.get("is_open") is not True
    )


def _is_direct_container_reward(
    event_type: str,
    payload: dict[str, Any],
    *, interacting_containers: set[str] | None = None,
    existing_container_names: set[str] | None = None,
) -> bool:
    """Detects reward events that would duplicate or bypass stored contents."""

    if event_type == "CurrencyChangedEvent":
        amount = _optional_int(
            payload,
            "base_unit_amount",
            "base_units",
            "delta_base_units",
            "amount",
        )
        return amount is not None and amount > 0

    if event_type not in {"InventoryItemAddedEvent", "ItemAddedEvent"}:
        return False
    is_container = str(payload.get("item_type", payload.get("category", ""))).strip().casefold() in {"container", "vehicle"}
    if not is_container:
        return True
    name = _first_text(payload, "item_name", "name").casefold()
    return bool(interacting_containers) and (
        name not in interacting_containers or name in (existing_container_names or set())
    )


def _invalid(
    event_type: str,
    payload: dict[str, Any],
    message: str,
) -> AppliedEventResult:
    """Builds an invalid/skipped event result."""

    LOGGER.warning("%s skipped: %s", event_type, message)
    return AppliedEventResult(event_type, "skipped", message, payload)


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    """Reads the first non-empty text value from payload."""

    for key in keys:
        value = payload.get(key)

        if value is None:
            continue

        clean_value = str(value).strip()

        if clean_value and clean_value.upper() not in {"SAME", "SKIP"}:
            return clean_value

    return ""






def _safe_positive_int(value: Any, default: int) -> int:
    """Reads a positive integer with fallback."""

    clean_value = _safe_int(value, default=default)

    if clean_value is None or clean_value <= 0:
        return default

    return clean_value


def _text_list(value: Any) -> list[str]:
    """Normalizes a text or list value into clean text entries."""

    if isinstance(value, str):
        raw_values = re.split(r"[,;]+", value)
    elif isinstance(value, list):
        raw_values = value
    else:
        raw_values = []

    return [str(item).strip() for item in raw_values if str(item).strip()]


def _blocking_d20_test_failure(
    results: list[AppliedEventResult],
) -> AppliedEventResult | None:
    """Returns the first failed d20 test result from this player command."""

    for result in results:
        if (
            result.event_type == "D20TestRequestedEvent"
            and result.status == "applied"
            and str(result.payload.get("outcome", "")).casefold() == "failure"
        ):
            return result

    return None


def _find_inventory_container(
    repository: SaveRepository,
    container_name: str,
) -> dict[str, Any] | None:
    """Finds a named inventory item carrying canonical container metadata."""

    folded_name = str(container_name or "").strip().casefold()

    for item in repository.list_inventory_items():
        metadata = item.get("metadata", {})

        if (
            str(item.get("name", "")).strip().casefold() == folded_name
            and isinstance(metadata, dict)
            and str(metadata.get("item_type", "")).casefold() in {"container", "vehicle"}
            and isinstance(metadata.get("container"), dict)
        ):
            return item

    return None


def _has_successful_d20_test(
    results: list[AppliedEventResult], *, skill_name: str, minimum_total: int,
    target: str, attribute: str = "Dexterity",
) -> bool:
    """A resolved check must permit the selected access method for this container."""
    return any(result.event_type == "D20TestRequestedEvent" and result.status == "applied"
               and container_test_succeeded(result.payload, skill=skill_name, dc=minimum_total,
                                            target=target, attribute=attribute)
               for result in results)



def _current_player_location(repository: SaveRepository) -> str:
    """Returns the current player location for event defaulting."""

    return clean_player_location_name(repository.get_state_value("location", "")) or "Unknown"


def _storage_location_is_accessible(
    repository: SaveRepository,
    destination: str,
) -> bool:
    """Only carried inventory or the exact current specific site is accessible."""
    from ai_adventure.inventory_storage import location_allows_storage
    if destination.casefold() in {"actively_carried", "on_person"}:
        return True
    current = repository.get_state_value("location", "")
    return (destination.strip().casefold() == current.strip().casefold()
            and location_allows_storage(destination, repository.get_travel_locations()))



def _canonical_inventory_storage_location(
    repository: SaveRepository,
    destination: str,
) -> str:
    """Reuses an established container/storage label for unambiguous shorthand."""

    clean = " ".join(str(destination or "").split())
    if not clean:
        return "actively_carried"
    if clean.casefold() in {"actively_carried", "on_person"}:
        return clean.casefold()

    items = repository.list_inventory_items()
    named_containers: list[str] = []
    stored_labels: list[str] = []
    for item in items:
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        location = str(item.get("storage_location", "") or "").strip()
        if location and location.casefold() not in {"actively_carried", "on_person"}:
            stored_labels.append(location)
        category = str(item.get("category", "") or "").strip().casefold()
        item_type = str(metadata.get("item_type", "") or "").strip().casefold()
        if category in {"container", "vehicle"} or item_type in {"container", "vehicle"}:
            name = str(item.get("name", "") or "").strip()
            if name:
                named_containers.append(name)

    target_key = _storage_label_key(clean)
    suffix_matches = {
        label
        for label in named_containers
        if _storage_label_key(label) == target_key
        or _storage_label_key(label).endswith(f" {target_key}")
    }
    if len(suffix_matches) == 1:
        return next(iter(suffix_matches))

    exact_matches = {
        label
        for label in [*named_containers, *stored_labels]
        if label.casefold() == clean.casefold()
    }
    if len(exact_matches) == 1:
        return next(iter(exact_matches))
    return clean


def _storage_label_key(value: str) -> str:
    """Normalizes a storage label for conservative identity matching."""

    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _active_task_defaults(
    repository: SaveRepository,
    *,
    name: str,
    category: str,
    description: str,
    requester: str,
    location: str,
    reward: str,
    due_date: str,
    existing_task: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Fills player-visible active-task fields with meaningful defaults."""

    is_personal = _looks_like_personal_task(
        name=name,
        category=category,
        description=description,
        requester=requester,
    )

    return {
        "requester": _task_field_value(
            provided=requester,
            existing=existing_task,
            field_name="requester",
            default="Self" if is_personal else "Unknown",
        ),
        "location": _task_field_value(
            provided=location,
            existing=existing_task,
            field_name="location",
            default=_default_task_location(
                repository,
                name=name,
                category=category,
                description=description,
                is_personal=is_personal,
            ),
        ),
        "reward": _task_field_value(
            provided=reward,
            existing=existing_task,
            field_name="reward",
            default="N/A" if is_personal else "Unknown",
        ),
        "due_date": _task_field_value(
            provided=due_date,
            existing=existing_task,
            field_name="due_date",
            default="N/A",
        ),
    }


def _task_field_value(
    *,
    provided: str,
    existing: dict[str, Any] | None,
    field_name: str,
    default: str,
) -> str:
    """Returns provided text, preserves existing text, or supplies a default."""

    clean_provided = str(provided or "").strip()

    if clean_provided:
        return clean_provided

    if existing is not None and str(existing.get(field_name, "")).strip():
        return ""

    return default


def _looks_like_personal_task(
    *,
    name: str,
    category: str,
    description: str,
    requester: str,
) -> bool:
    """Returns True when a task appears self-directed."""

    clean_requester = requester.strip().casefold()

    if clean_requester in {"self", "player", "player character", "me"}:
        return True

    if clean_requester:
        return False

    category_text = category.casefold()

    if any(
        marker in category_text
        for marker in ["personal", "goal", "research", "training", "craft"]
    ):
        return True

    task_text = f"{name} {description}".casefold()
    return any(
        marker in task_text
        for marker in [
            "practice",
            "train",
            "research",
            "study",
            "learn",
            "craft",
            "create",
            "make",
            "build",
            "brew",
            "prepare",
            "repair",
            "upgrade",
        ]
    )


def _default_task_location(
    repository: SaveRepository,
    *,
    name: str,
    category: str,
    description: str,
    is_personal: bool,
) -> str:
    """Chooses a reasonable active-task location fallback."""

    task_text = f"{name} {category} {description}".casefold()

    if is_personal and any(
        marker in task_text
        for marker in [
            "alchemy",
            "brew",
            "craft",
            "create",
            "forge",
            "make",
            "repair",
            "workshop",
        ]
    ):
        return "Player's Workshop"

    return _current_player_location(repository)


def _active_task_due_fields(
    repository: SaveRepository,
    payload: dict[str, Any],
    *,
    due_date: str,
) -> dict[str, Any]:
    """Resolves active-task due text to an absolute in-world minute when possible."""

    explicit_elapsed = _active_task_due_elapsed_from_payload(payload)

    if explicit_elapsed is not None:
        if explicit_elapsed < 0:
            return {"due_date": "N/A", "due_elapsed_minutes": -1}

        return {
            "due_date": _format_due_elapsed_minutes(repository, explicit_elapsed),
            "due_elapsed_minutes": explicit_elapsed,
        }

    clean_due_date = str(due_date or "").strip()

    if not clean_due_date:
        return {"due_date": "", "due_elapsed_minutes": None}

    if _is_no_deadline(clean_due_date):
        return {"due_date": "N/A", "due_elapsed_minutes": -1}

    resolved_elapsed = _resolve_due_text_to_elapsed_minutes(
        repository,
        clean_due_date,
            payload,
        )

    if resolved_elapsed is None:
        return {"due_date": clean_due_date, "due_elapsed_minutes": None}

    return {
        "due_date": _format_due_elapsed_minutes(repository, resolved_elapsed),
        "due_elapsed_minutes": resolved_elapsed,
    }


def _active_task_due_elapsed_from_payload(payload: dict[str, Any]) -> int | None:
    """Reads an exact active-task due minute from supported payload fields."""

    for key in ["due_elapsed_minutes", "deadline_elapsed_minutes"]:
        if key not in payload:
            continue

        value = payload.get(key)

        if value is None or str(value).strip().upper() in {"AUTO", "SAME", "SKIP"}:
            continue

        parsed_value = _safe_int(value, default=-1)
        return max(-1, parsed_value if parsed_value is not None else -1)

    return None


def _resolve_due_text_to_elapsed_minutes(
    repository: SaveRepository,
    due_text: str,
    payload: dict[str, Any],
) -> int | None:
    """Resolves common relative or calendar due text to an absolute minute."""

    clean_text = due_text.strip()
    folded_text = clean_text.casefold()
    stored_current_minute = repository.get_current_calendar_minute()
    current_minute = max(
        0,
        stored_current_minute
        if stored_current_minute is not None
        else DEFAULT_START_ELAPSED_MINUTES,
    )
    settings = normalize_calendar_settings(repository.get_calendar_settings())
    current_day_index = current_minute // MINUTES_PER_DAY
    days_per_week = int(settings["days_per_week"])
    due_time = _resolve_due_time_of_day_minutes(payload, clean_text)

    if "end of" in folded_text and "week" in folded_text:
        days_until_due = (days_per_week - 1) - (current_day_index % days_per_week)
        return (current_day_index + days_until_due) * MINUTES_PER_DAY + due_time

    if "tomorrow" in folded_text:
        return (current_day_index + 1) * MINUTES_PER_DAY + due_time

    if "today" in folded_text:
        return current_day_index * MINUTES_PER_DAY + due_time

    relative_days = _relative_due_days(folded_text, days_per_week)

    if relative_days is not None:
        return (current_day_index + relative_days) * MINUTES_PER_DAY + due_time

    exact_day_index = _exact_due_day_index(clean_text, settings)

    if exact_day_index is not None:
        return exact_day_index * MINUTES_PER_DAY + due_time

    return None


def _resolve_due_time_of_day_minutes(payload: dict[str, Any], due_text: str) -> int:
    """Resolves a due time, defaulting to a concrete late-day deadline."""

    explicit_minutes = _optional_int(payload, "due_time_of_day_minutes", "time_of_day_minutes")

    if explicit_minutes is not None:
        return max(0, min(MINUTES_PER_DAY - 1, explicit_minutes))

    for key in ["due_time", "deadline_time", "time"]:
        raw_time = str(payload.get(key, "")).strip()

        if raw_time:
            parsed_time = _parse_time_of_day(raw_time)

            if parsed_time is not None:
                return parsed_time

    parsed_text_time = _parse_time_of_day(due_text)

    if parsed_text_time is not None:
        return parsed_text_time

    folded_text = due_text.casefold()

    if "end of" in folded_text or "by night" in folded_text:
        return MINUTES_PER_DAY - 1

    if "dawn" in folded_text:
        return 6 * 60

    return 17 * 60


def _parse_time_of_day(text: str) -> int | None:
    """Parses common clock and narrative time strings."""

    folded_text = text.strip().casefold()

    if not folded_text:
        return None

    if "midnight" in folded_text:
        return 0

    if "noon" in folded_text:
        return 12 * 60

    clock_match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b",
        folded_text,
    )

    if clock_match is None:
        return None

    hour = int(clock_match.group(1))
    minute = int(clock_match.group(2) or 0)
    suffix = str(clock_match.group(3) or "").replace(".", "")
    matched_text = clock_match.group(0)

    if minute > 59:
        return None

    if not suffix and ":" not in matched_text:
        return None

    if suffix in {"am", "pm"}:
        if hour < 1 or hour > 12:
            return None

        if suffix == "am":
            hour = hour % 12
        else:
            hour = (hour % 12) + 12
    elif hour > 23:
        return None

    return hour * 60 + minute


def _relative_due_days(text: str, days_per_week: int) -> int | None:
    """Parses relative due phrases such as 'in 3 days' or 'in two weeks'."""

    match = re.search(r"\bin\s+([a-z0-9]+)\s+(day|days|week|weeks)\b", text)

    if match is None:
        return None

    amount = _number_word_value(match.group(1))

    if amount is None:
        return None

    unit = match.group(2)
    multiplier = days_per_week if unit.startswith("week") else 1
    return max(0, amount * multiplier)


def _number_word_value(text: str) -> int | None:
    """Parses a small integer or common English number word."""

    clean_text = text.strip().casefold()

    if clean_text.isdigit():
        return int(clean_text)

    return {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }.get(clean_text)


def _exact_due_day_index(text: str, settings: dict[str, Any]) -> int | None:
    """Parses simple month/day due dates using the active calendar settings."""

    folded_text = text.strip().casefold()
    month_names = [
        (index, str(name).strip())
        for index, name in enumerate(settings["month_names"])
        if str(name).strip()
    ]
    month_names.sort(key=lambda item: len(item[1]), reverse=True)

    for month_index, month_name in month_names:
        folded_month = month_name.casefold()

        if not folded_text.startswith(folded_month):
            continue

        remainder = text[len(month_name):].strip(" ,")
        match = re.match(r"(\d{1,2})(?:\D+year\s+(\d+))?", remainder, flags=re.IGNORECASE)

        if match is None:
            continue

        days_per_month = int(settings["days_per_week"]) * int(settings["weeks_per_month"])
        day_of_month = max(1, min(days_per_month, int(match.group(1))))
        year = int(match.group(2) or 1)
        return month_start_day_index(year, month_index, settings) + day_of_month - 1

    return None


def _format_due_elapsed_minutes(repository: SaveRepository, elapsed_minutes: int) -> str:
    """Formats an absolute due minute with the current save calendar settings."""

    return build_calendar_snapshot(
        max(0, elapsed_minutes),
        repository.get_calendar_settings(),
    )["display_label"]


def _is_no_deadline(text: str) -> bool:
    """Returns True for no-deadline task values."""

    return text.strip().casefold() in {
        "",
        "n/a",
        "na",
        "none",
        "no deadline",
        "no known deadline",
        "not applicable",
    }


def _first_int(payload: dict[str, Any], default: int, *keys: str) -> int:
    """Reads the first integer value, with fallback."""

    value = _optional_int(payload, *keys)

    if value is None:
        return default

    return value


def _optional_int(payload: dict[str, Any], *keys: str) -> int | None:
    """Reads the first optional integer from payload."""

    for key in keys:
        value = payload.get(key)

        if value is None or str(value).strip().upper() in {"AUTO", "SAME", "SKIP"}:
            continue

        return _safe_int(value, default=None)

    return None


def _safe_int(value: Any, *, default: int | None) -> int | None:
    """Safely converts a value to int."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_string_list(value: Any) -> list[str]:
    """Converts list-like or comma-separated values into clean strings."""

    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    if isinstance(value, str):
        return [
            item.strip()
            for item in value.split(",")
            if item.strip()
        ]

    return []


def _safe_generated_npc_display_name(display_name: str, role: str) -> str:
    """Avoids storing banned generated names as player-visible NPC names."""

    if not display_name or not _is_banned_creative_term(display_name):
        return display_name

    LOGGER.warning(
        "Removed banned generated NPC display_name %r before storage.",
        display_name,
    )
    return ""


def _fallback_npc_role(*, display_name: str, name: str) -> str:
    """Builds a conservative role when Gemini omits the NPC role field."""

    clean_display_name = display_name.strip()
    clean_name = name.strip()

    if clean_display_name:
        return clean_display_name

    return clean_name or "Unspecified NPC"


def _npc_knowledge_scope(
    payload: dict[str, Any],
    *,
    role: str,
    location: str,
) -> list[str]:
    """Returns an NPC knowledge scope, defaulting to safe observable topics."""

    knowledge_scope = _as_string_list(payload.get("knowledge_scope", []))

    if knowledge_scope:
        return knowledge_scope

    clean_role = role.strip() or "this NPC"
    clean_location = location.strip() or "the current scene"
    return [
        f"Visible behavior and public activity involving {clean_role}.",
        f"Public information around {clean_location}.",
    ]


def _npc_known_facts(
    payload: dict[str, Any],
    *,
    player_facing_information: str,
    public_description: str,
    role: str,
    location: str,
) -> list[str]:
    """Returns known facts without inventing private player information."""

    known_facts = _as_string_list(payload.get("known_facts", []))

    if known_facts:
        return known_facts

    for candidate in [
        player_facing_information,
        public_description,
        f"{role} encountered at {location}.",
    ]:
        clean_candidate = candidate.strip()
        if clean_candidate:
            return [clean_candidate]

    return ["No private facts about the player are established."]


def _is_banned_creative_term(value: str) -> bool:
    """Returns True when a value contains a banned generated term."""

    return contains_banned_creative_term(value)
