from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
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
from ai_adventure.combat import (
    DEFAULT_BASE_ARMOR_RATING,
    DEFAULT_PLAYER_MAX_HEALTH,
    attack_bonus_from_skills,
    armor_rating_from_equipment,
    equipped_weapon_attack_skill,
    equipped_weapon_combat_profile,
    equipped_weapon_damage,
    normalize_damage_expression,
    normalize_item_metadata,
    roll_combat_initiative,
)
from ai_adventure.currency import format_currency_amount
from ai_adventure.locations import clean_player_location_name
from ai_adventure.persistence.save_repository import GM_SECRET_STATUSES, SaveRepository
from ai_adventure.skills.rules import bonus_for_level, dc_for_difficulty


LOGGER = logging.getLogger(__name__)
_SKILL_CHECK_GATED_EVENT_TYPES = {
    "ActiveTaskCompletedEvent",
    "CurrencyChangedEvent",
    "ContainerContentsTakenEvent",
    "ContainerOpenedEvent",
    "InventoryItemAddedEvent",
    "InventoryItemModifiedEvent",
    "ItemAddedEvent",
    "ReagentDiscoveredEvent",
    "RecipeDiscoveredEvent",
    "SkillXpAddedEvent",
    "SpellLearnedEvent",
}
_BAD_LUCK_HISTORY_LIMIT = 8
_BAD_LUCK_MIN_HISTORY = 5
_BAD_LUCK_LOW_ROLL_MAX = 10
_BAD_LUCK_LOW_ROLL_RATIO = 0.70
_BAD_LUCK_MAX_NUDGE = 3


class RandomNumberGenerator(Protocol):
    """Minimal random interface needed to resolve a skill check."""

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
                command, such as pre-narration skill checks.

        Returns:
            Application results for every attempted event.
        """

        with self.repository.message_context(self.message_id):
            return self._apply_events(raw_events, prior_results=prior_results)

    def _apply_events(
        self,
        raw_events: list[dict[str, Any]],
        *,
        prior_results: list[AppliedEventResult] | None = None,
    ) -> list[AppliedEventResult]:
        """Applies events inside the message-associated repository scope."""

        results: list[AppliedEventResult] = []
        blocking_failure = _blocking_skill_check_failure(prior_results or [])
        protects_container_contents = any(
            _raw_event_protects_container_contents(raw_event)
            for raw_event in raw_events
        )

        for raw_event in raw_events:
            event_type, payload = normalize_event(raw_event)

            if protects_container_contents and _is_direct_container_reward(
                event_type,
                payload,
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
                and event_type in _SKILL_CHECK_GATED_EVENT_TYPES
            ):
                result = AppliedEventResult(
                    event_type,
                    "skipped",
                    (
                        "Skipped because a previous skill check failed: "
                        f"{blocking_failure.message}"
                    ),
                    payload,
                )
            else:
                result = self.apply_event(
                    {"type": event_type, "payload": payload},
                    skill_check_results=[*(prior_results or []), *results],
                )

            if (
                result.event_type == "SkillCheckRequestedEvent"
                and result.status == "applied"
                and str(result.payload.get("outcome", "")).casefold() == "failure"
            ):
                blocking_failure = result

            self.repository.append_mechanical_event(
                result.event_type,
                result.payload,
                result.status,
                result.message,
            )
            results.append(result)

        return results

    def apply_event(
        self,
        raw_event: dict[str, Any],
        *,
        skill_check_results: list[AppliedEventResult] | None = None,
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

            if event_type == "InventoryItemModifiedEvent":
                return self._apply_inventory_item_modified(event_type, payload)

            if event_type == "ContainerOpenedEvent":
                return self._apply_container_opened(
                    event_type,
                    payload,
                    skill_check_results or [],
                )

            if event_type == "ContainerContentsTakenEvent":
                return self._apply_container_contents_taken(event_type, payload)

            if event_type == "CombatStartedEvent":
                return self._apply_combat_started(event_type, payload)

            if event_type == "SkillUpsertedEvent":
                return self._apply_skill_upserted(event_type, payload)

            if event_type == "SkillXpAddedEvent":
                return self._apply_skill_xp_added(event_type, payload)

            if event_type == "SkillCheckRequestedEvent":
                return self._apply_skill_check_requested(event_type, payload)

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

            if event_type == "SpellLearnedEvent":
                return self._apply_spell_learned(event_type, payload)

            if event_type == "NpcUpsertedEvent":
                return self._apply_npc_upserted(event_type, payload)

            if event_type == "NpcKnowledgeAddedEvent":
                return self._apply_npc_knowledge_added(event_type, payload)

            if event_type == "SecretUpsertedEvent":
                return self._apply_secret_upserted(event_type, payload)

            if event_type == "MusicChangedEvent":
                return self._apply_music_changed(event_type, payload)

            if event_type == "SoundEffectChangedEvent":
                return self._apply_sound_effect_changed(event_type, payload)

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
            merged_metadata["ascii_art"] = str(
                catalog_entry.get("ascii_art", payload.get("ascii_art", "")) or ""
            ).strip("\r\n")

        self.repository.add_inventory_item(
            name=name,
            category=category,
            quantity=quantity,
            description=description,
            value_base_units=value_base_units,
            metadata={
                **merged_metadata,
                "quantity_unit": quantity_unit,
                "storage_location": storage_location,
            },
        )

        return AppliedEventResult(
            event_type,
            "applied",
            f"Added inventory item: {quantity} x {name}.",
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

        quantity = _first_int(payload, 1, "amount", "quantity")
        self.repository.remove_inventory_item(name, quantity)

        return AppliedEventResult(
            event_type,
            "applied",
            f"Removed inventory item: {quantity} x {name}.",
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

        if _find_inventory_container(self.repository, target_name) is not None:
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

        self.repository.modify_inventory_item(
            target_name=target_name,
            new_name=_first_text(payload, "new_name"),
            category=_first_text(payload, "new_category", "category"),
            description=_first_text(payload, "new_description", "description"),
            quantity=quantity,
            value_base_units=value_base_units,
            metadata=payload,
        )

        return AppliedEventResult(
            event_type,
            "applied",
            f"Modified inventory item: {target_name}.",
            payload,
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
        skill_check_results: list[AppliedEventResult],
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

        metadata = dict(item.get("metadata", {}))
        container = dict(metadata.get("container", {}))

        if container.get("is_open") is True:
            return AppliedEventResult(
                event_type,
                "skipped",
                f"{item['name']} is already open.",
                payload,
            )

        if container.get("is_locked") is True:
            required_skill = str(container.get("lockpick_skill", "Lockpicking"))
            required_dc = max(1, _safe_int(container.get("lockpick_dc"), default=10) or 10)

            if not _has_successful_skill_check(
                skill_check_results,
                skill_name=required_skill,
                minimum_total=required_dc,
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
                        + (f" Failure consequence: {consequence}" if consequence else "")
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

            if not _has_successful_skill_check(
                skill_check_results,
                skill_name=required_skill,
                minimum_total=required_dc,
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

        container["is_open"] = True
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

        contents = dict(container.get("contents", {}))
        currency_amount = max(
            0,
            _safe_int(contents.get("currency_base_units"), default=0) or 0,
        )
        transferred_items: list[dict[str, Any]] = []

        if currency_amount:
            current_balance = _safe_int(
                self.repository.get_state_value("currency.balance", "0"),
                default=0,
            ) or 0
            self.repository.set_state_value(
                "currency.balance",
                str(current_balance + currency_amount),
            )

        for raw_item in contents.get("items", []):
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
            self.repository.add_inventory_item(
                name=name,
                category=category,
                quantity=quantity,
                description=description,
                value_base_units=value_base_units,
                metadata=item_metadata,
            )
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

        container["contents_taken"] = True
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

    def _apply_combat_started(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies CombatStartedEvent."""

        if self.repository.is_combat_active():
            return AppliedEventResult(
                event_type,
                "skipped",
                "Combat is already active.",
                payload,
            )

        enemies = _combatants_from_payload(payload.get("enemies", []), team="enemy")

        if not enemies:
            enemy_name = _first_text(payload, "enemy_name", "opponent_name") or "Enemy"
            enemies = [
                _combatant_from_payload(
                    {
                        "name": enemy_name,
                        "health": _first_int(payload, 8, "enemy_health", "health"),
                        "armor_rating": _first_int(
                            payload,
                            10,
                            "enemy_armor_rating",
                            "armor_rating",
                        ),
                        "to_hit_bonus": _first_int(
                            payload,
                            0,
                            "enemy_to_hit_bonus",
                            "to_hit_bonus",
                        ),
                        "initiative_bonus": _first_int(
                            payload,
                            0,
                            "enemy_initiative_bonus",
                            "initiative_bonus",
                        ),
                        "personality": _first_text(
                            payload,
                            "enemy_personality",
                            "personality",
                        )
                        or "balanced",
                        "damage": _first_text(payload, "enemy_damage", "damage") or "1d6",
                        "loot": payload.get("loot", []),
                    },
                    team="enemy",
                    index=1,
                )
            ]

        allies = _combatants_from_payload(payload.get("allies", []), team="party", start_index=2)
        inventory_items = self.repository.list_inventory_items()
        equipment = self.repository.get_player_equipment()
        attack_skill = equipped_weapon_attack_skill(equipment, inventory_items)
        weapon_profile = equipped_weapon_combat_profile(
            equipment,
            inventory_items,
        )
        stored_clips = self.repository.get_setting(
            "player.weapon_clip_ammo",
            {},
        )
        weapon_name_key = str(
            weapon_profile.get("weapon_name", "")
        ).casefold()
        clip_size = int(weapon_profile.get("clip_size", 0))
        stored_clip_ammo = (
            _safe_int(stored_clips.get(weapon_name_key), default=clip_size)
            if isinstance(stored_clips, dict) and weapon_name_key
            else clip_size
        )
        player_health_max = _safe_positive_int(
            self.repository.get_setting("player.health_max", DEFAULT_PLAYER_MAX_HEALTH),
            DEFAULT_PLAYER_MAX_HEALTH,
        )
        player_health_current = max(
            0,
            min(
                _safe_positive_int(
                    self.repository.get_setting("player.health_current", player_health_max),
                    player_health_max,
                ),
                player_health_max,
            ),
        )
        player = {
            "id": "player",
            "name": str(self.repository.get_setting("player_name", "Player")).strip() or "Player",
            "team": "party",
            "current_health": player_health_current,
            "max_health": player_health_max,
            "armor_rating": armor_rating_from_equipment(
                equipment,
                inventory_items,
                base_armor_rating=DEFAULT_BASE_ARMOR_RATING,
            ),
            "to_hit_bonus": attack_bonus_from_skills(
                attack_skill,
                self.repository.list_skills(),
            ),
            "initiative_bonus": _safe_int(
                self.repository.get_setting("player.initiative_bonus", 0),
                default=0,
            )
            or 0,
            "personality": "balanced",
            **weapon_profile,
            "clip_ammo": max(
                0,
                min(clip_size, stored_clip_ammo or 0),
            ),
            "reserve_ammo": 0,
            "damage": equipped_weapon_damage(equipment, inventory_items),
            "status_effects": [],
            "loot": [],
            "defeated": player_health_current <= 0,
        }
        combatants = roll_combat_initiative(
            [player, *allies, *enemies],
            rng=self.rng,
        )
        combat_state = {
            "active": True,
            "round": 1,
            "turn_index": 0,
            "combatants": combatants,
            "log": [
                str(payload.get("description", "") or "Combat begins.").strip(),
                "Initiative order: "
                + ", ".join(
                    (
                        f"{combatant.get('display_name', combatant['name'])} "
                        f"({combatant['initiative_total']})"
                    )
                    for combatant in combatants
                )
                + ".",
            ],
        }
        self.repository.set_combat_state(combat_state)
        self.repository.append_history("system", "Combat started.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Started combat with {len(enemies)} enemy combatant(s).",
            payload,
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

        level = _first_int(payload, 1, "level")
        description = _first_text(payload, "description", "skill_description")
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

        skill = self.repository.add_skill_xp(name, xp_amount)

        if skill is None:
            return _invalid(event_type, payload, f"Skill does not exist: {name}.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Added {xp_amount} XP to {name}. Level {skill['level']}, bonus +{skill['bonus']}.",
            payload,
        )

    def _apply_skill_check_requested(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies SkillCheckRequestedEvent by rolling d20 + skill bonus."""

        name = _first_text(payload, "skill_name", "name")

        if not name:
            return _invalid(event_type, payload, "Skill name is required.")

        skill = self.repository.get_skill(name)

        if skill is None:
            description = _first_text(payload, "skill_description", "description")
            if not description:
                return _invalid(
                    event_type,
                    payload,
                    "skill_description is required when checking a new skill.",
                )
            self.repository.upsert_skill(name, description, 1)
            skill = self.repository.get_skill(name)

        if skill is None:
            return _invalid(event_type, payload, f"Could not create skill: {name}.")

        level = int(skill["level"])
        bonus = int(skill["bonus"])
        dc = _optional_int(payload, "dc")

        if dc is None:
            dc = dc_for_difficulty(payload.get("difficulty"))

        raw_roll = self.rng.randint(1, 20)
        luck_nudge = _bad_luck_roll_nudge(
            self.repository.list_skill_checks(_BAD_LUCK_HISTORY_LIMIT)
        )
        roll = min(20, raw_roll + luck_nudge)
        total = roll + bonus
        outcome = "success" if total >= dc else "failure"

        self.repository.record_skill_check(
            skill_name=name,
            level=level,
            bonus=bonus,
            roll=roll,
            total=total,
            dc=dc,
            outcome=outcome,
        )
        LOGGER.info(
            "Resolved hidden %s check: total %s vs DC %s (%s).",
            name,
            total,
            dc,
            outcome,
        )
        if luck_nudge:
            LOGGER.info(
                "Applied bad-luck nudge to %s check: raw d20 %s + %s = %s.",
                name,
                raw_roll,
                luck_nudge,
                roll,
            )

        return AppliedEventResult(
            event_type,
            "applied",
            f"{name} check {outcome}: {total} vs DC {dc}.",
            {
                **payload,
                "skill_name": name,
                "level": level,
                "bonus": bonus,
                "roll": roll,
                "raw_roll": raw_roll,
                "bad_luck_nudge": luck_nudge,
                "total": total,
                "dc": dc,
                "outcome": outcome,
            },
        )

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

    def _apply_sound_effect_changed(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies SoundEffectChangedEvent to the independent ambient channel."""

        filename = _first_text(
            payload,
            "filename",
            "file_name",
            "track",
            "track_name",
            "sound_effect",
            "ambient_sound",
        )
        if not filename:
            return _invalid(event_type, payload, "Sound-effect filename is required.")

        if filename.upper() in {"STOP", "NONE", "OFF", "SILENCE"}:
            self.repository.set_setting("audio.current_sound_effect", "")
            message = "Stopped the ambient sound effect."
        else:
            self.repository.set_setting("audio.current_sound_effect", filename)
            message = f"Changed ambient sound effect to: {filename}."

        return AppliedEventResult(event_type, "applied", message, payload)

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
        ascii_art = str(payload.get("ascii_art", "") or "").strip("\r\n")
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
            ascii_art=ascii_art,
            item_uuid=_first_text(payload, "item_uuid"),
        )
        self.repository.upsert_item_catalog_entry(
            name=name,
            category=category,
            description=description,
            value_base_units=value_base_units,
            metadata={
                "ascii_art": ascii_art,
                "item_uuid": _first_text(payload, "item_uuid"),
            },
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

    def _apply_spell_learned(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies SpellLearnedEvent as durable state/history."""

        name = _first_text(payload, "name")

        if not name:
            return _invalid(event_type, payload, "Spell name is required.")

        self.repository.set_state_value(f"spell.{name}.known", "true")
        self.repository.append_history("spell", f"Learned spell: {name}.")
        return AppliedEventResult(event_type, "applied", f"Learned spell: {name}.", payload)

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
            knowledge_scope=knowledge_scope,
            known_facts=known_facts,
        )

        if npc is None:
            return _invalid(event_type, payload, "NPC could not be stored.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Stored NPC profile: {npc['name']}.",
            {**payload, "npc_id": npc["npc_id"]},
        )

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

    if event_type == "ContainerContentsTakenEvent":
        return True

    if event_type != "InventoryItemAddedEvent":
        return False

    container = payload.get("container", {})
    return (
        str(payload.get("item_type", "")).strip().casefold() == "container"
        and isinstance(container, dict)
        and container.get("is_open") is not True
    )


def _is_direct_container_reward(
    event_type: str,
    payload: dict[str, Any],
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

    return (
        event_type in {"InventoryItemAddedEvent", "ItemAddedEvent"}
        and str(payload.get("item_type", payload.get("category", "")))
        .strip()
        .casefold()
        != "container"
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


def _combatants_from_payload(
    raw_combatants: Any,
    *,
    team: str,
    start_index: int = 1,
) -> list[dict[str, Any]]:
    """Normalizes a list of combatants from an event payload."""

    if not isinstance(raw_combatants, list):
        return []

    combatants: list[dict[str, Any]] = []

    for offset, raw_combatant in enumerate(raw_combatants):
        if not isinstance(raw_combatant, dict):
            continue

        combatants.append(
            _combatant_from_payload(
                raw_combatant,
                team=team,
                index=start_index + offset,
            )
        )

    return combatants


def _combatant_from_payload(
    raw_combatant: dict[str, Any],
    *,
    team: str,
    index: int,
) -> dict[str, Any]:
    """Normalizes one combatant from an event payload."""

    name = str(raw_combatant.get("name", raw_combatant.get("enemy_name", ""))).strip()
    name = name or ("Enemy" if team == "enemy" else "Ally")
    max_health = _safe_positive_int(
        raw_combatant.get("max_health", raw_combatant.get("health")),
        8 if team == "enemy" else 12,
    )
    current_health = _safe_positive_int(
        raw_combatant.get("current_health", max_health),
        max_health,
    )
    return {
        "id": f"{team}-{index}-{_slug_for_event_id(name)}",
        "name": name,
        "team": team,
        "current_health": max(0, min(current_health, max_health)),
        "max_health": max_health,
        "armor_rating": _safe_positive_int(raw_combatant.get("armor_rating"), 10),
        "to_hit_bonus": max(
            -99,
            min(99, _safe_int(raw_combatant.get("to_hit_bonus"), default=0) or 0),
        ),
        "initiative_bonus": max(
            -99,
            min(
                99,
                _safe_int(raw_combatant.get("initiative_bonus"), default=0)
                or 0,
            ),
        ),
        "personality": str(
            raw_combatant.get("personality", "balanced") or "balanced"
        ),
        "weapon_name": str(raw_combatant.get("weapon_name", "") or ""),
        "ammunition_type_required": str(
            raw_combatant.get("ammunition_type_required", "") or ""
        ),
        "clip_size": max(
            0,
            _safe_int(raw_combatant.get("clip_size"), default=0) or 0,
        ),
        "clip_ammo": max(
            0,
            _safe_int(raw_combatant.get("clip_ammo"), default=0) or 0,
        ),
        "bullets_per_attack": max(
            0,
            _safe_int(raw_combatant.get("bullets_per_attack"), default=0)
            or 0,
        ),
        "reserve_ammo": max(
            0,
            _safe_int(raw_combatant.get("reserve_ammo"), default=0) or 0,
        ),
        "damage": normalize_damage_expression(raw_combatant.get("damage"), default="1d6"),
        "status_effects": _text_list(raw_combatant.get("status_effects", [])),
        "loot": _text_list(raw_combatant.get("loot", [])),
        "defeated": False,
    }


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


def _slug_for_event_id(value: str) -> str:
    """Returns a compact event-combatant id fragment."""

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "combatant"


def _blocking_skill_check_failure(
    results: list[AppliedEventResult],
) -> AppliedEventResult | None:
    """Returns the first failed skill check result from this player command."""

    for result in results:
        if (
            result.event_type == "SkillCheckRequestedEvent"
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
            and str(metadata.get("item_type", "")).casefold() == "container"
            and isinstance(metadata.get("container"), dict)
        ):
            return item

    return None


def _has_successful_skill_check(
    results: list[AppliedEventResult],
    *,
    skill_name: str,
    minimum_total: int,
) -> bool:
    """Returns whether this command resolved the required check successfully."""

    folded_skill = str(skill_name or "").strip().casefold()

    return any(
        result.event_type == "SkillCheckRequestedEvent"
        and result.status == "applied"
        and str(result.payload.get("outcome", "")).casefold() == "success"
        and str(result.payload.get("skill_name", "")).strip().casefold() == folded_skill
        and (_safe_int(result.payload.get("total"), default=0) or 0) >= minimum_total
        for result in results
    )


def _bad_luck_roll_nudge(recent_checks: list[dict[str, Any]]) -> int:
    """Returns a small d20 nudge when recent skill rolls are unusually cold."""

    if len(recent_checks) < _BAD_LUCK_MIN_HISTORY:
        return 0

    recent_rolls = [
        roll
        for roll in (
            _safe_int(check.get("roll"), default=0)
            for check in recent_checks[-_BAD_LUCK_HISTORY_LIMIT:]
        )
        if roll is not None and 1 <= roll <= 20
    ]

    if len(recent_rolls) < _BAD_LUCK_MIN_HISTORY:
        return 0

    low_roll_count = sum(
        1 for roll in recent_rolls if roll <= _BAD_LUCK_LOW_ROLL_MAX
    )
    low_roll_ratio = low_roll_count / len(recent_rolls)

    if low_roll_ratio < _BAD_LUCK_LOW_ROLL_RATIO:
        return 0

    extra_low_rolls = low_roll_count - (len(recent_rolls) // 2)
    return max(1, min(_BAD_LUCK_MAX_NUDGE, extra_low_rolls))


def _current_player_location(repository: SaveRepository) -> str:
    """Returns the current player location for event defaulting."""

    return clean_player_location_name(repository.get_state_value("location", "")) or "Unknown"


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
