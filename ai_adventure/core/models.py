from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AdventureMetadata:
    """Player-facing metadata for an adventure save."""

    title: str = "New Adventure"

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable metadata dictionary."""

        return asdict(self)


@dataclass
class PlayerState:
    """State that belongs directly to the player character."""

    name: str = ""
    appearance: str = ""
    backstory: str = ""
    condition: str = "Healthy"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable player-state dictionary."""

        return asdict(self)


@dataclass
class WorldState:
    """State for the current scene and broader world."""

    location: str = "Tavern"
    time: str = "Day 1, Morning"
    weather: str = "Clear"
    flags: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable world-state dictionary."""

        return asdict(self)


@dataclass
class InventoryItem:
    """An item owned by the player."""

    id: int | None = None
    name: str = ""
    category: str = ""
    quantity: int = 1
    description: str = ""
    value_base_units: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable inventory-item dictionary."""

        return asdict(self)


@dataclass
class InventoryState:
    """The player's complete inventory."""

    items: list[InventoryItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable inventory-state dictionary."""

        return {"items": [item.to_dict() for item in self.items]}


@dataclass
class CurrencyState:
    """The player's money and denomination definitions."""

    balance_base_units: int = 0
    denominations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable currency-state dictionary."""

        return asdict(self)


@dataclass
class CalendarState:
    """The current custom calendar and derived date/time fields."""

    elapsed_minutes: int = 480
    absolute_day: int = 1
    year: int = 1
    month_index: int = 0
    month_number: int = 1
    month_name: str = "Month 1"
    week_of_month: int = 1
    day_of_month: int = 1
    day_of_week_index: int = 0
    day_of_week_name: str = "Monday"
    season_index: int = 0
    season_name: str = "Spring"
    season_hint: str = "spring"
    time_of_day_minutes: int = 480
    time_label: str = "Morning"
    date_label: str = "Monday, Month 1 1, Year 1"
    display_label: str = "Monday, Month 1 1, Year 1, Morning"
    days_per_month: int = 28
    days_per_year: int = 336
    days_per_week: int = 7
    weeks_per_month: int = 4
    months_per_year: int = 12
    seasons_per_year: int = 4
    time_display: str = "narrative"
    settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable calendar-state dictionary."""

        return asdict(self)


@dataclass
class ReagentKnowledge:
    """A discovered alchemical reagent."""

    id: int | None = None
    name: str = ""
    qualities: list[str] = field(default_factory=list)
    motions: list[str] = field(default_factory=list)
    virtues: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    notes: str = ""
    discovered_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable reagent dictionary."""

        return asdict(self)


@dataclass
class RecipeKnowledge:
    """A discovered alchemical recipe."""

    id: int | None = None
    name: str = ""
    ingredients: list[str] = field(default_factory=list)
    result: str = ""
    motions: list[str] = field(default_factory=list)
    virtues: list[str] = field(default_factory=list)
    notes: str = ""
    discovered_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable recipe dictionary."""

        return asdict(self)


@dataclass
class AlchemyNote:
    """A freeform alchemy notebook note."""

    id: int | None = None
    title: str = ""
    body: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable note dictionary."""

        return asdict(self)


@dataclass
class AlchemyNotebookState:
    """The player's discovered alchemical knowledge."""

    notes: list[AlchemyNote] = field(default_factory=list)
    known_reagents: list[ReagentKnowledge] = field(default_factory=list)
    known_recipes: list[RecipeKnowledge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable alchemy-state dictionary."""

        return {
            "notes": [note.to_dict() for note in self.notes],
            "known_reagents": [reagent.to_dict() for reagent in self.known_reagents],
            "known_recipes": [recipe.to_dict() for recipe in self.known_recipes],
        }


@dataclass
class HistoryEntry:
    """One durable history timeline entry."""

    id: int | None = None
    kind: str = "misc"
    content: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable history-entry dictionary."""

        return asdict(self)


@dataclass
class HistoryState:
    """The full adventure timeline."""

    entries: list[HistoryEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable history-state dictionary."""

        return {"entries": [entry.to_dict() for entry in self.entries]}


@dataclass
class Skill:
    """A player skill with a plain reliability bonus."""

    id: int | None = None
    name: str = ""
    description: str = ""
    level: int = 1
    xp: int = 0
    bonus: int = 2

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable skill dictionary."""

        return asdict(self)


@dataclass
class SkillCheck:
    """One resolved skill check."""

    id: int | None = None
    skill_name: str = ""
    level: int = 1
    bonus: int = 2
    roll: int = 0
    total: int = 0
    dc: int = 14
    outcome: str = "failure"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable skill-check dictionary."""

        return asdict(self)


@dataclass
class SkillsState:
    """Player skills and recent skill checks."""

    skills: list[Skill] = field(default_factory=list)
    recent_checks: list[SkillCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable skills-state dictionary."""

        return {
            "skills": [skill.to_dict() for skill in self.skills],
            "recent_checks": [check.to_dict() for check in self.recent_checks],
        }


@dataclass
class ActiveTask:
    """A visible ongoing quest, commission, order, or other obligation."""

    id: int | None = None
    name: str = ""
    category: str = "Task"
    status: str = "Active"
    description: str = ""
    requester: str = ""
    location: str = ""
    reward: str = ""
    due_date: str = ""
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable active-task dictionary."""

        return asdict(self)


@dataclass
class ActiveTasksState:
    """The player's current active tasks and pending obligations."""

    tasks: list[ActiveTask] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable active-tasks dictionary."""

        return {"tasks": [task.to_dict() for task in self.tasks]}


@dataclass
class SettingsState:
    """Save-specific settings that affect the adventure runtime."""

    player_name: str = ""
    theme: str = "System"
    values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable settings-state dictionary."""

        return asdict(self)


@dataclass
class AdventureState:
    """The composed source-of-truth state for an adventure save."""

    metadata: AdventureMetadata = field(default_factory=AdventureMetadata)
    player: PlayerState = field(default_factory=PlayerState)
    world: WorldState = field(default_factory=WorldState)
    inventory: InventoryState = field(default_factory=InventoryState)
    currency: CurrencyState = field(default_factory=CurrencyState)
    calendar: CalendarState = field(default_factory=CalendarState)
    alchemy: AlchemyNotebookState = field(default_factory=AlchemyNotebookState)
    skills: SkillsState = field(default_factory=SkillsState)
    active_tasks: ActiveTasksState = field(default_factory=ActiveTasksState)
    history: HistoryState = field(default_factory=HistoryState)
    settings: SettingsState = field(default_factory=SettingsState)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable adventure-state dictionary."""

        return {
            "metadata": self.metadata.to_dict(),
            "player": self.player.to_dict(),
            "world": self.world.to_dict(),
            "inventory": self.inventory.to_dict(),
            "currency": self.currency.to_dict(),
            "calendar": self.calendar.to_dict(),
            "alchemy": self.alchemy.to_dict(),
            "skills": self.skills.to_dict(),
            "active_tasks": self.active_tasks.to_dict(),
            "history": self.history.to_dict(),
            "settings": self.settings.to_dict(),
        }
