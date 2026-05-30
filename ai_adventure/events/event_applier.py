from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from ai_adventure.calendar_system import DEFAULT_START_ELAPSED_MINUTES
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.skills.rules import bonus_for_level, dc_for_difficulty


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppliedEventResult:
    """Result of attempting to apply one event."""

    event_type: str
    status: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


class EventApplier:
    """Applies validated AI-suggested events to the save repository."""

    def __init__(self, repository: SaveRepository, rng: random.Random | None = None) -> None:
        """
        Args:
            repository: Active save repository.
            rng: Optional random generator for deterministic tests.
        """

        self.repository = repository
        self.rng = rng or random.Random()

    def apply_events(self, raw_events: list[dict[str, Any]]) -> list[AppliedEventResult]:
        """
        Applies a list of raw event dictionaries.

        Args:
            raw_events: Event objects from Gemini's JSON response.

        Returns:
            Application results for every attempted event.
        """

        results: list[AppliedEventResult] = []

        for raw_event in raw_events:
            result = self.apply_event(raw_event)
            self.repository.append_mechanical_event(
                result.event_type,
                result.payload,
                result.status,
                result.message,
            )
            results.append(result)

        return results

    def apply_event(self, raw_event: dict[str, Any]) -> AppliedEventResult:
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

            if event_type == "SkillUpsertedEvent":
                return self._apply_skill_upserted(event_type, payload)

            if event_type == "SkillXpAddedEvent":
                return self._apply_skill_xp_added(event_type, payload)

            if event_type == "SkillCheckRequestedEvent":
                return self._apply_skill_check_requested(event_type, payload)

            if event_type in {"StatusUpdatedEvent", "LocationChangedEvent"}:
                return self._apply_status_updated(event_type, payload)

            if event_type == "FlagSetEvent":
                return self._apply_flag_set(event_type, payload)

            if event_type == "RecipeDiscoveredEvent":
                return self._apply_recipe_discovered(event_type, payload)

            if event_type == "ReagentDiscoveredEvent":
                return self._apply_reagent_discovered(event_type, payload)

            if event_type == "PlayerNoteAddedEvent":
                return self._apply_player_note_added(event_type, payload)

            if event_type == "CurrencyChangedEvent":
                return self._apply_currency_changed(event_type, payload)

            if event_type == "CurrencyDefinedEvent":
                return self._apply_currency_defined(event_type, payload)

            if event_type in {
                "WorldLoreAddedEvent",
                "WorldLoreChangedEvent",
                "WorldLoreUpdatedEvent",
            }:
                return self._apply_world_lore_event(event_type, payload)

            if event_type in {"QuestAddedEvent", "QuestCompletedEvent"}:
                return self._apply_quest_event(event_type, payload)

            if event_type in {"ActiveTaskUpsertedEvent", "ActiveTaskUpdatedEvent"}:
                return self._apply_active_task_upserted(event_type, payload)

            if event_type == "ActiveTaskCompletedEvent":
                return self._apply_active_task_completed(event_type, payload)

            if event_type == "SpellLearnedEvent":
                return self._apply_spell_learned(event_type, payload)

            if event_type == "NpcUpsertedEvent":
                return self._apply_npc_upserted(event_type, payload)

            if event_type == "NpcKnowledgeAddedEvent":
                return self._apply_npc_knowledge_added(event_type, payload)

            if event_type == "MusicChangedEvent":
                return self._apply_music_changed(event_type, payload)

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

        quantity = _first_int(payload, 1, "amount", "quantity")
        category = _first_text(payload, "item_type", "category")
        description = _first_text(payload, "description", "desc")
        value_base_units = _first_int(
            payload,
            0,
            "value_base_units",
            "base_unit_value",
            "value",
        )

        self.repository.add_inventory_item(
            name=name,
            category=category,
            quantity=quantity,
            description=description,
            value_base_units=value_base_units,
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
            description=_first_text(payload, "new_description", "description"),
            quantity=quantity,
            value_base_units=value_base_units,
        )

        return AppliedEventResult(
            event_type,
            "applied",
            f"Modified inventory item: {target_name}.",
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

        xp_amount = _first_int(payload, 0, "xp_amount", "amount", "xp")

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
            self.repository.upsert_skill(name, "Untrained or newly revealed skill.", 1)
            skill = self.repository.get_skill(name)

        if skill is None:
            return _invalid(event_type, payload, f"Could not create skill: {name}.")

        level = int(skill["level"])
        bonus = int(skill["bonus"])
        dc = _optional_int(payload, "dc")

        if dc is None:
            dc = dc_for_difficulty(payload.get("difficulty"))

        roll = self.rng.randint(1, 20)
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
        """Applies StatusUpdatedEvent or LocationChangedEvent."""

        location = _first_text(payload, "location", "new_location")
        weather = _first_text(payload, "weather")
        minutes_passed = _optional_int(payload, "minutes_passed", "minutes", "time")

        if location and location.upper() not in {"AUTO", "SAME", "SKIP"}:
            self.repository.set_state_value("location", location)

        if weather and weather.upper() not in {"AUTO", "SAME", "SKIP"}:
            self.repository.set_state_value("weather", weather)

        if minutes_passed is not None and minutes_passed >= 0:
            current_total = _safe_int(
                self.repository.get_state_value(
                    "elapsed_minutes",
                    str(DEFAULT_START_ELAPSED_MINUTES),
                ),
                default=DEFAULT_START_ELAPSED_MINUTES,
            ) or DEFAULT_START_ELAPSED_MINUTES
            self.repository.set_state_value(
                "elapsed_minutes",
                str(current_total + minutes_passed),
            )

        return AppliedEventResult(
            event_type,
            "applied",
            "Updated status fields.",
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

        self.repository.add_alchemy_recipe(
            name=name,
            ingredients=_ingredients_to_list(payload.get("ingredients", [])),
            result=_first_text(payload, "result", "description"),
            motions=_as_string_list(payload.get("motions", [])),
            virtues=_as_string_list(payload.get("virtues", [])),
            notes=_first_text(payload, "notes"),
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
            return _invalid(event_type, payload, "Reagent name is required.")

        self.repository.add_alchemy_reagent(
            name=name,
            qualities=_as_string_list(payload.get("qualities", [])),
            motions=_as_string_list(payload.get("motions", [])),
            virtues=_as_string_list(payload.get("virtues", [])),
            uses=_as_string_list(payload.get("uses", [])),
            notes=_first_text(payload, "notes", "description"),
        )

        return AppliedEventResult(
            event_type,
            "applied",
            f"Discovered reagent: {name}.",
            payload,
        )

    def _apply_player_note_added(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies PlayerNoteAddedEvent as a history note for now."""

        content = _first_text(payload, "content", "note", "text")

        if not content:
            return _invalid(event_type, payload, "Player note content is required.")

        self.repository.append_history("note", content)
        return AppliedEventResult(event_type, "applied", "Added player note.", payload)

    def _apply_currency_changed(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies CurrencyChangedEvent."""

        amount = _optional_int(payload, "base_unit_amount", "amount")

        if amount is None:
            return _invalid(event_type, payload, "Currency amount is required.")

        current_balance = _safe_int(
            self.repository.get_state_value("currency.balance", "0"),
            default=0,
        ) or 0
        new_balance = current_balance + amount
        self.repository.set_state_value("currency.balance", str(new_balance))

        return AppliedEventResult(
            event_type,
            "applied",
            f"Currency balance changed by {amount}.",
            payload,
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

    def _apply_world_lore_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies keyed world-lore events."""

        section = _first_text(payload, "section")
        key = _first_text(payload, "key", "anchor", "name", "title")
        text = _first_text(payload, "text", "replacement_lore", "lore")

        if not text:
            return _invalid(event_type, payload, "World lore text is required.")

        if event_type in {"WorldLoreChangedEvent", "WorldLoreUpdatedEvent"}:
            if not key:
                return _invalid(event_type, payload, "World lore key is required.")

            self.repository.change_world_lore_entry(section or "World", key, text)
        else:
            self.repository.add_world_lore_entry(section or "World", key, text)

        label = f"{section}: {key}: {text}" if section and key else f"{section}: {text}" if section else text
        self.repository.append_history("world", label)
        return AppliedEventResult(event_type, "applied", "Recorded world lore.", payload)

    def _apply_quest_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies quest events as durable active tasks."""

        name = _first_text(payload, "name")

        if not name:
            return _invalid(event_type, payload, "Quest name is required.")

        if event_type == "QuestCompletedEvent":
            self.repository.set_state_value(f"quest.{name}.status", "completed")
            task = self.repository.complete_active_task(
                name,
                _first_text(payload, "notes", "resolution", "outcome"),
            )
            self.repository.append_history("quest", f"Completed quest: {name}.")
            if task is None:
                return AppliedEventResult(
                    event_type,
                    "applied",
                    f"Completed quest flag: {name}.",
                    payload,
                )
            return AppliedEventResult(event_type, "applied", f"Completed quest: {name}.", payload)

        self.repository.set_state_value(f"quest.{name}.status", "active")
        self.repository.upsert_active_task(
            name=name,
            category="Quest",
            status="Active",
            description=_first_text(payload, "description"),
            requester=_first_text(payload, "giver", "quest_giver", "requester"),
            location=_first_text(payload, "turn_in", "location"),
            reward=_first_text(payload, "reward"),
            due_date=_first_text(payload, "due_date", "deadline"),
            notes=_first_text(payload, "notes"),
        )
        self.repository.append_history(
            "quest",
            f"Added quest: {name}. {_first_text(payload, 'description')}",
        )
        return AppliedEventResult(event_type, "applied", f"Added quest: {name}.", payload)

    def _apply_active_task_upserted(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> AppliedEventResult:
        """Applies ActiveTaskUpsertedEvent or ActiveTaskUpdatedEvent."""

        name = _first_text(payload, "name", "title", "task_name")

        if not name:
            return _invalid(event_type, payload, "Active task name is required.")

        task = self.repository.upsert_active_task(
            name=name,
            category=_first_text(payload, "category", "type") or "Task",
            status=_first_text(payload, "status") or "Active",
            description=_first_text(payload, "description", "objective", "summary"),
            requester=_first_text(payload, "requester", "giver", "client", "npc"),
            location=_first_text(payload, "location", "turn_in"),
            reward=_first_text(payload, "reward", "payment"),
            due_date=_first_text(payload, "due_date", "deadline", "due"),
            notes=_first_text(payload, "notes"),
        )

        if task is None:
            return _invalid(event_type, payload, "Active task could not be stored.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Stored active task: {name}.",
            payload,
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

        display_name = _first_text(payload, "display_name", "visible_name")
        role = _first_text(payload, "role", "occupation")
        name = (
            _first_text(payload, "name", "npc_name", "internal_name")
            or display_name
            or role
            or _first_text(payload, "npc_id", "id")
        )

        if not name:
            return _invalid(
                event_type,
                payload,
                "NPC name, display_name, role, or npc_id is required.",
            )

        npc = self.repository.upsert_npc(
            npc_id=_first_text(payload, "npc_id", "id"),
            name=name,
            display_name=display_name,
            role=role,
            location=_first_text(payload, "location"),
            public_description=_first_text(
                payload,
                "public_description",
                "description",
                "appearance",
            ),
            player_facing_information=_first_text(
                payload,
                "player_facing_information",
                "player_facing_summary",
                "player_known_information",
            ),
            knowledge_scope=_as_string_list(payload.get("knowledge_scope", [])),
            known_facts=_as_string_list(payload.get("known_facts", [])),
            disposition=_first_text(payload, "disposition"),
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
            location=_first_text(payload, "location"),
        )

        if npc is None:
            return _invalid(event_type, payload, "NPC could not be resolved.")

        return AppliedEventResult(
            event_type,
            "applied",
            f"Updated NPC knowledge: {npc['name']}.",
            {**payload, "npc_id": npc["npc_id"], "facts": facts},
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

    return event_type, payload


def _event_type_from_raw_event(raw_event: dict[str, Any]) -> str:
    """Reads a Gemini event type from supported event-type keys."""

    for key in ["type", "event_type", "eventType"]:
        event_type = str(raw_event.get(key, "")).strip()

        if event_type:
            return event_type

    return ""


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


def _ingredients_to_list(value: Any) -> list[str]:
    """Normalizes recipe ingredients."""

    if isinstance(value, dict):
        return [
            f"{name}: {quantity}"
            for name, quantity in value.items()
            if str(name).strip()
        ]

    return _as_string_list(value)
