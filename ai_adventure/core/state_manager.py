from __future__ import annotations

import logging
from typing import Any

from ai_adventure.calendar_system import (
    build_calendar_snapshot,
)
from ai_adventure.combat import (
    DEFAULT_BASE_ARMOR_RATING,
    DEFAULT_PLAYER_MAX_HEALTH,
    normalize_equipment,
)
from ai_adventure.core.models import (
    AdventureMetadata,
    AdventureState,
    ActiveTask,
    ActiveTasksState,
    AlchemyNotebookState,
    CalendarState,
    CharacterSpell,
    CurrencyState,
    HistoryEntry,
    HistoryState,
    ItemCatalogEntry,
    ItemCatalogState,
    InventoryItem,
    InventoryState,
    MagicResourcePool,
    MagicState,
    ActiveMagicEffect,
    PlayerState,
    ReagentKnowledge,
    RecipeIngredient,
    RecipeKnowledge,
    SettingsState,
    Skill,
    SkillCheck,
    SkillsState,
    TravelState,
    WorldState,
)
from ai_adventure.locations import normalize_known_locations
from ai_adventure.persistence.save_repository import SaveRepository


LOGGER = logging.getLogger(__name__)


class StateManager:
    """
    Loads and commits the composed adventure state for one save.

    This is the bridge between the SQLite repository and the typed state models.
    Event application and UI code can use these models without needing to know
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
        inventory = self._load_inventory()
        travel_locations = self.repository.ensure_travel_locations()
        travel_profile = self.repository.get_travel_profile()
        calendar_snapshot = build_calendar_snapshot(
            self.repository.get_current_calendar_minute(),
            self.repository.get_calendar_settings(),
        )
        equipment = normalize_equipment(
            settings.values.get("player.equipment", {}),
            [item.to_dict() for item in inventory.items],
        )
        health_max = _read_int(settings.values, "player.health_max", DEFAULT_PLAYER_MAX_HEALTH)

        return AdventureState(
            metadata=AdventureMetadata(
                title=self.repository.get_meta("title", default="Untitled Adventure")
            ),
            player=PlayerState(
                name=str(settings.values.get("player_name", "")),
                name_pronunciation=str(
                    settings.values.get("player.name_pronunciation", "")
                ),
                pronouns=str(
                    settings.values.get("player.pronouns", "They/Them")
                ),
                appearance=str(settings.values.get("player.appearance", "")),
                backstory=str(settings.values.get("player.backstory", "")),
                condition=_read_string(state_snapshot, "condition", "Healthy"),
                notes=str(settings.values.get("player.notes", "")),
                health_current=max(
                    0,
                    min(
                        _read_int(
                            settings.values,
                            "player.health_current",
                            health_max,
                        ),
                        health_max,
                    ),
                ),
                health_max=health_max,
                armor_rating=_read_int(
                    settings.values,
                    "player.armor_rating",
                    DEFAULT_BASE_ARMOR_RATING,
                ),
                equipment=equipment,
            ),
            world=WorldState(
                location=_read_string(state_snapshot, "location", "Tavern"),
                time=_read_string(state_snapshot, "time", calendar_snapshot["display_label"]),
                weather=_read_string(state_snapshot, "weather", "Clear"),
                flags=self._load_flags(state_snapshot),
            ),
            travel=TravelState(
                locations=normalize_known_locations(travel_locations),
                move_speed_mph=float(travel_profile["move_speed_mph"]),
                travel_mode=str(travel_profile["travel_mode"]),
                speed_multiplier=float(travel_profile["speed_multiplier"]),
            ),
            inventory=inventory,
            item_catalog=self._load_item_catalog(),
            currency=CurrencyState(
                balance_base_units=_read_int(state_snapshot, "currency.balance", 0),
                denominations=self.repository.get_currency_denominations(),
            ),
            calendar=CalendarState(**calendar_snapshot),
            alchemy=self._load_alchemy(),
            skills=self._load_skills(),
            magic=self._load_magic(),
            active_tasks=self._load_active_tasks(),
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
                    id=_read_optional_id(row, "id"),
                    name=_read_string(row, "name", ""),
                    category=_read_string(row, "category", ""),
                    quantity=_read_int(row, "quantity", 1),
                    equipped=bool(row.get("equipped", False)),
                    description=_read_string(row, "description", ""),
                    value_base_units=_read_int(row, "value_base_units", 0),
                    metadata=dict(row.get("metadata", {}))
                    if isinstance(row.get("metadata"), dict)
                    else {},
                )
            )

        return InventoryState(items=items)

    def _load_item_catalog(self) -> ItemCatalogState:
        """Loads remembered item definitions."""

        items: list[ItemCatalogEntry] = []

        for row in self.repository.list_item_catalog():
            items.append(
                ItemCatalogEntry(
                    id=_read_optional_id(row, "id"),
                    name=_read_string(row, "name", ""),
                    category=_read_string(row, "category", ""),
                    description=_read_string(row, "description", ""),
                    value_base_units=_read_int(row, "value_base_units", 0),
                    ascii_art=_read_string(row, "ascii_art", ""),
                    metadata=dict(row.get("metadata", {}))
                    if isinstance(row.get("metadata"), dict)
                    else {},
                    first_seen_at=_read_string(row, "first_seen_at", ""),
                    updated_at=_read_string(row, "updated_at", ""),
                )
            )

        return ItemCatalogState(items=items)

    def _load_alchemy(self) -> AlchemyNotebookState:
        """Loads typed alchemy notebook state."""

        known_reagents: list[ReagentKnowledge] = []
        known_recipes: list[RecipeKnowledge] = []

        for row in self.repository.list_crafting_items():
            known_reagents.append(
                ReagentKnowledge(
                    id=_read_optional_id(row, "id"),
                    name=_read_string(row, "name", ""),
                    category=_read_string(row, "category", "Material"),
                    description=_read_string(row, "description", ""),
                    location=_read_string(row, "location", ""),
                    uses=_read_string_list(row, "uses"),
                    rarity=_read_string(row, "rarity", "Common"),
                    notes=_read_string(row, "notes", ""),
                    value_base_units=_read_int(row, "value_base_units", 0),
                    discovered_at=_read_string(row, "discovered_at", ""),
                )
            )

        for row in self.repository.list_crafting_recipes():
            known_recipes.append(
                RecipeKnowledge(
                    id=_read_optional_id(row, "id"),
                    name=_read_string(row, "name", ""),
                    ingredients=[
                        RecipeIngredient(
                            reagent_name=_read_string(ingredient, "reagent_name", ""),
                            item_uuid=_read_string(ingredient, "item_uuid", ""),
                            quantity=_read_int(ingredient, "quantity", 1),
                            measure_amount=_read_int(ingredient, "measure_amount", 1),
                            measure_unit=_read_string(ingredient, "measure_unit", "each"),
                        )
                        for ingredient in _read_dict_list(row, "ingredients")
                    ],
                    result=_read_string(row, "result", ""),
                    notes=_read_string(row, "notes", ""),
                    value_base_units=_read_int(row, "value_base_units", 0),
                    discovered_at=_read_string(row, "discovered_at", ""),
                )
            )

        return AlchemyNotebookState(
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
                    id=_read_optional_id(row, "id"),
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

    def _load_active_tasks(self) -> ActiveTasksState:
        """Loads visible active tasks."""

        tasks: list[ActiveTask] = []

        for row in self.repository.list_active_tasks():
            tasks.append(
                ActiveTask(
                    id=_read_optional_id(row, "id"),
                    name=_read_string(row, "name", ""),
                    category=_read_string(row, "category", "Task"),
                    status=_read_string(row, "status", "Active"),
                    description=_read_string(row, "description", ""),
                    requester=_read_string(row, "requester", ""),
                    location=_read_string(row, "location", ""),
                    reward=_read_string(row, "reward", ""),
                    due_date=_read_string(row, "due_date", ""),
                    due_elapsed_minutes=_read_int(row, "due_elapsed_minutes", -1),
                    notes=_read_string(row, "notes", ""),
                    created_at=_read_string(row, "created_at", ""),
                    updated_at=_read_string(row, "updated_at", ""),
                )
            )

        return ActiveTasksState(tasks=tasks)

    def _load_magic(self) -> MagicState:
        """Loads the typed player-magic state."""

        return MagicState(
            configuration=self.repository.get_magic_configuration(),
            known_spells=[
                CharacterSpell(
                    spell_id=_read_string(row, "spell_id", ""),
                    name=_read_string(row, "name", ""),
                    tier=_read_int(row, "tier", 0),
                    school=_read_string(row, "school", ""),
                    description=_read_string(row, "description", ""),
                    casting_time=_read_string(row, "casting_time", "Action"),
                    range=_read_string(row, "range", ""),
                    duration=_read_string(row, "duration", ""),
                    requirements=_read_string(row, "requirements", ""),
                    mana_cost=_read_int(row, "mana_cost", 0),
                    prepared=bool(row.get("prepared", True)),
                    favorite=bool(row.get("favorite", False)),
                )
                for row in self.repository.list_character_spells()
            ],
            resource_pools=[
                MagicResourcePool(
                    pool_id=_read_string(row, "pool_id", ""),
                    name=_read_string(row, "name", ""),
                    resource_type=_read_string(row, "resource_type", ""),
                    tier=_read_int(row, "tier", 0),
                    current_amount=_read_int(row, "current_amount", 0),
                    maximum_amount=_read_int(row, "maximum_amount", 0),
                    recovery_rule=_read_string(row, "recovery_rule", ""),
                )
                for row in self.repository.list_magic_resource_pools()
            ],
            active_effects=[
                ActiveMagicEffect(
                    effect_id=_read_string(row, "effect_id", ""),
                    spell_id=_read_string(row, "spell_id", ""),
                    name=_read_string(row, "name", ""),
                    target=_read_string(row, "target", ""),
                    description=_read_string(row, "description", ""),
                    start_elapsed_minutes=_read_int(row, "start_elapsed_minutes", -1),
                    end_elapsed_minutes=_read_int(row, "end_elapsed_minutes", -1),
                    requires_concentration=bool(row.get("requires_concentration", False)),
                )
                for row in self.repository.list_active_magic_effects()
            ],
        )

    def _load_history(self) -> HistoryState:
        """Loads typed history state."""

        entries: list[HistoryEntry] = []

        for row in self.repository.list_history():
            entries.append(
                HistoryEntry(
                    id=_read_optional_int(row, "id"),
                    message_id=_read_string(row, "message_id", ""),
                    kind=_read_string(row, "kind", "misc"),
                    content=_read_string(row, "content", ""),
                    sound_effect_cues=[
                        cue
                        for cue in row.get("sound_effect_cues", [])
                        if isinstance(cue, dict)
                    ],
                    created_at=_read_string(row, "created_at", ""),
                )
            )

        return HistoryState(entries=entries)

    def _load_settings(self) -> SettingsState:
        """Loads typed settings state."""

        values = self.repository.list_settings()
        player_name = str(values.get("player_name", ""))
        theme = str(values.get("theme", "Light"))
        if theme not in {"Light", "Dark"}:
            theme = "Light"

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


def _read_optional_id(row: dict[str, Any], key: str) -> str | None:
    """Reads an optional alphanumeric record ID from a row-like dictionary."""

    value = row.get(key)

    if value is None:
        return None

    clean_value = str(value).strip()
    return clean_value or None


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


def _read_dict_list(row: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Reads a list of dictionaries from a row-like dictionary."""

    value = row.get(key, [])

    if not isinstance(value, list):
        LOGGER.warning("Expected list for '%s', got %r.", key, value)
        return []

    return [dict(item) for item in value if isinstance(item, dict)]
