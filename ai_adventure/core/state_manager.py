from __future__ import annotations

import logging
from typing import Any

from ai_adventure.calendar_system import (
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
)
from ai_adventure.core.models import (
    AdventureMetadata,
    AdventureState,
    AlchemyNote,
    AlchemyNotebookState,
    CalendarState,
    CurrencyState,
    HistoryEntry,
    HistoryState,
    InventoryItem,
    InventoryState,
    PlayerState,
    ReagentKnowledge,
    RecipeKnowledge,
    SettingsState,
    Skill,
    SkillCheck,
    SkillsState,
    WorldState,
)
from ai_adventure.persistence.save_repository import SaveRepository


LOGGER = logging.getLogger(__name__)


class StateManager:
    """
    Loads and commits the composed adventure state for one save.

    This is the bridge between the SQLite repository and the typed state models.
    Event reducers can target these models in Phase 3 without needing to know
    the database layout.
    """

    def __init__(self, repository: SaveRepository) -> None:
        """
        Args:
            repository: Active save repository.
        """

        self.repository = repository

    def load_state(self) -> AdventureState:
        """
        Loads the complete adventure state from the active save.

        Returns:
            Composed adventure state.
        """

        state_snapshot = self.repository.get_state_snapshot()
        settings = self._load_settings()
        calendar_snapshot = build_calendar_snapshot(
            _read_int(state_snapshot, "elapsed_minutes", DEFAULT_START_ELAPSED_MINUTES),
            self.repository.get_calendar_settings(),
        )

        return AdventureState(
            metadata=AdventureMetadata(
                title=self.repository.get_meta("title", default="Untitled Adventure")
            ),
            player=PlayerState(
                name=str(settings.values.get("player_name", "")),
                condition=_read_string(state_snapshot, "condition", "Healthy"),
            ),
            world=WorldState(
                location=_read_string(state_snapshot, "location", "Tavern"),
                time=_read_string(state_snapshot, "time", calendar_snapshot["display_label"]),
                weather=_read_string(state_snapshot, "weather", "Clear"),
                flags=self._load_flags(state_snapshot),
            ),
            inventory=self._load_inventory(),
            currency=CurrencyState(
                balance_base_units=_read_int(state_snapshot, "currency.balance", 0),
                denominations=self.repository.get_currency_denominations(),
            ),
            calendar=CalendarState(**calendar_snapshot),
            alchemy=self._load_alchemy(),
            skills=self._load_skills(),
            history=self._load_history(),
            settings=settings,
        )

    def update_core_fields(
        self,
        *,
        location: str,
        time: str,
        weather: str,
        condition: str,
    ) -> AdventureState:
        """
        Commits the editable core state fields and reloads the composed state.

        Args:
            location: Current player location.
            time: Current in-world time.
            weather: Current weather.
            condition: Current player condition.

        Returns:
            Reloaded adventure state after committing the fields.
        """

        self.repository.set_state_value("location", location.strip())
        self.repository.set_state_value("time", time.strip())
        self.repository.set_state_value("weather", weather.strip())
        self.repository.set_state_value("condition", condition.strip())
        self.repository.append_history("system", "Core state fields updated.")

        LOGGER.info("Committed core state fields for %s.", self.repository.db_path)

        return self.load_state()

    def _load_inventory(self) -> InventoryState:
        """Loads typed inventory state."""

        items: list[InventoryItem] = []

        for row in self.repository.list_inventory_items():
            items.append(
                InventoryItem(
                    id=_read_optional_int(row, "id"),
                    name=_read_string(row, "name", ""),
                    category=_read_string(row, "category", ""),
                    quantity=_read_int(row, "quantity", 1),
                    description=_read_string(row, "description", ""),
                    value_base_units=_read_int(row, "value_base_units", 0),
                )
            )

        return InventoryState(items=items)

    def _load_alchemy(self) -> AlchemyNotebookState:
        """Loads typed alchemy notebook state."""

        notes: list[AlchemyNote] = []
        known_reagents: list[ReagentKnowledge] = []
        known_recipes: list[RecipeKnowledge] = []

        for row in self.repository.list_alchemy_notes():
            notes.append(
                AlchemyNote(
                    id=_read_optional_int(row, "id"),
                    title=_read_string(row, "title", ""),
                    body=_read_string(row, "body", ""),
                    created_at=_read_string(row, "created_at", ""),
                )
            )

        for row in self.repository.list_alchemy_reagents():
            known_reagents.append(
                ReagentKnowledge(
                    id=_read_optional_int(row, "id"),
                    name=_read_string(row, "name", ""),
                    qualities=_read_string_list(row, "qualities"),
                    motions=_read_string_list(row, "motions"),
                    virtues=_read_string_list(row, "virtues"),
                    uses=_read_string_list(row, "uses"),
                    notes=_read_string(row, "notes", ""),
                    discovered_at=_read_string(row, "discovered_at", ""),
                )
            )

        for row in self.repository.list_alchemy_recipes():
            known_recipes.append(
                RecipeKnowledge(
                    id=_read_optional_int(row, "id"),
                    name=_read_string(row, "name", ""),
                    ingredients=_read_string_list(row, "ingredients"),
                    result=_read_string(row, "result", ""),
                    motions=_read_string_list(row, "motions"),
                    virtues=_read_string_list(row, "virtues"),
                    notes=_read_string(row, "notes", ""),
                    discovered_at=_read_string(row, "discovered_at", ""),
                )
            )

        return AlchemyNotebookState(
            notes=notes,
            known_reagents=known_reagents,
            known_recipes=known_recipes,
        )

    def _load_skills(self) -> SkillsState:
        """Loads typed skill state."""

        skills: list[Skill] = []
        recent_checks: list[SkillCheck] = []

        for row in self.repository.list_skills():
            skills.append(
                Skill(
                    id=_read_optional_int(row, "id"),
                    name=_read_string(row, "name", ""),
                    description=_read_string(row, "description", ""),
                    level=_read_int(row, "level", 1),
                    xp=_read_int(row, "xp", 0),
                    bonus=_read_int(row, "bonus", 2),
                )
            )

        for row in self.repository.list_skill_checks():
            recent_checks.append(
                SkillCheck(
                    id=_read_optional_int(row, "id"),
                    skill_name=_read_string(row, "skill_name", ""),
                    level=_read_int(row, "level", 1),
                    bonus=_read_int(row, "bonus", 2),
                    roll=_read_int(row, "roll", 0),
                    total=_read_int(row, "total", 0),
                    dc=_read_int(row, "dc", 14),
                    outcome=_read_string(row, "outcome", "failure"),
                    created_at=_read_string(row, "created_at", ""),
                )
            )

        return SkillsState(skills=skills, recent_checks=recent_checks)

    def _load_history(self) -> HistoryState:
        """Loads typed history state."""

        entries: list[HistoryEntry] = []

        for row in self.repository.list_history():
            entries.append(
                HistoryEntry(
                    id=_read_optional_int(row, "id"),
                    kind=_read_string(row, "kind", "misc"),
                    content=_read_string(row, "content", ""),
                    created_at=_read_string(row, "created_at", ""),
                )
            )

        return HistoryState(entries=entries)

    def _load_settings(self) -> SettingsState:
        """Loads typed settings state."""

        values = self.repository.list_settings()
        player_name = str(values.get("player_name", ""))
        theme = str(values.get("theme", "System"))

        return SettingsState(player_name=player_name, theme=theme, values=values)

    def _load_flags(self, state_snapshot: dict[str, str]) -> dict[str, Any]:
        """Collects namespaced state keys into world flags."""

        flags: dict[str, Any] = {}

        for key, value in state_snapshot.items():
            if key.startswith("flag."):
                flags[key.removeprefix("flag.")] = value

        return flags


def _read_string(row: dict[str, Any], key: str, default: str) -> str:
    """Reads a string value from a row-like dictionary."""

    value = row.get(key, default)

    if value is None:
        return default

    return str(value)


def _read_int(row: dict[str, Any], key: str, default: int) -> int:
    """Reads an integer value from a row-like dictionary."""

    value = row.get(key, default)

    try:
        return int(value)
    except (TypeError, ValueError):
        LOGGER.warning("Expected integer for '%s', got %r. Using %s.", key, value, default)
        return default


def _read_optional_int(row: dict[str, Any], key: str) -> int | None:
    """Reads an optional integer value from a row-like dictionary."""

    value = row.get(key)

    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        LOGGER.warning("Expected optional integer for '%s', got %r.", key, value)
        return None


def _read_string_list(row: dict[str, Any], key: str) -> list[str]:
    """Reads a clean string list from a row-like dictionary."""

    value = row.get(key, [])

    if not isinstance(value, list):
        LOGGER.warning("Expected list for '%s', got %r.", key, value)
        return []

    return [
        str(item).strip()
        for item in value
        if str(item).strip()
    ]
