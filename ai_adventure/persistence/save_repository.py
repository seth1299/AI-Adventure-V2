from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_adventure.calendar_system import (
    DEFAULT_CALENDAR_SETTINGS,
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    normalize_calendar_settings,
)
from ai_adventure.currency import (
    DEFAULT_CURRENCY_DENOMINATIONS,
    normalize_currency_denominations,
)
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.skills.rules import bonus_for_level, clamp_skill_level, level_for_xp


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SaveSummary:
    """
    Lightweight save-game summary shown on the Main Menu.

    Args:
        title: Player-facing save title.
        db_path: Path to the save's SQLite database.
        last_modified: Last modified timestamp.
    """

    title: str
    db_path: Path
    last_modified: datetime


class SaveRepository:
    """
    SQLite-backed repository for one adventure save.

    The repository is the only layer allowed to directly read/write save data.
    The UI should call repository methods instead of touching files directly.
    """

    DATABASE_NAME = "adventure.db"

    def __init__(self, db_path: Path) -> None:
        """
        Args:
            db_path: Path to this save's SQLite database.
        """

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @classmethod
    def create_new_save(
        cls,
        saves_dir: Path,
        title: str,
        setup: dict[str, Any] | None = None,
    ) -> "SaveRepository":
        """
        Creates a new save directory and SQLite database.

        Args:
            saves_dir: Directory where save folders are stored.
            title: Player-facing adventure title.

        Returns:
            Repository for the newly created save.
        """

        safe_title = _slugify(title.strip() or "New Adventure")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_dir = saves_dir / f"{safe_title}_{timestamp}"
        db_path = save_dir / cls.DATABASE_NAME

        repository = cls(db_path)
        repository.set_meta("title", title.strip() or "New Adventure")

        if setup is not None:
            repository.apply_new_game_setup(setup)
            LOGGER.info("Created new configured save at %s.", db_path)
            return repository

        repository.set_setting("player_name", "Player Name")
        repository.set_setting("player.appearance", "")
        repository.set_setting("player.backstory", "")
        repository.set_setting("player.notes", "")
        repository.set_setting("ai.additional_context", "")
        repository.set_setting("audio.music_enabled", True)
        repository.set_setting("audio.narrator_enabled", True)
        repository.set_setting("audio.music_volume", 25)
        repository.set_setting("audio.tts_volume", 90)
        repository.set_setting("audio.current_music", "")
        repository.set_journal_notes("")
        repository.set_currency_denominations(DEFAULT_CURRENCY_DENOMINATIONS)
        repository.set_calendar_settings(DEFAULT_CALENDAR_SETTINGS)
        repository.set_state_value("elapsed_minutes", str(DEFAULT_START_ELAPSED_MINUTES))
        calendar_snapshot = build_calendar_snapshot(
            DEFAULT_START_ELAPSED_MINUTES,
            DEFAULT_CALENDAR_SETTINGS,
        )
        repository.set_state_value("location", "Tavern")
        repository.set_state_value("time", calendar_snapshot["display_label"])
        repository.set_state_value("weather", "Clear")
        repository.set_state_value("condition", "Healthy")
        repository.add_inventory_item(
            "Healing Draught",
            "Potion",
            1,
            "A mild red draught meant to steady minor wounds and fatigue.",
        )
        repository.add_inventory_item(
            "Iron Dagger",
            "Weapon",
            1,
            "A plain iron dagger with a worn leather grip.",
        )
        repository.add_inventory_item(
            "Lantern",
            "Tool",
            1,
            "A brass lantern with a shuttered flame chamber.",
        )
        repository.add_inventory_item(
            "Trail Ration",
            "Food",
            3,
            "Dried bread, hard cheese, and smoked fruit wrapped for travel.",
        )
        repository.add_inventory_item(
            "Waterskin",
            "Tool",
            1,
            "A sealed waterskin suitable for a day's travel.",
        )
        repository.upsert_skill(
            "Alchemy",
            "Preparing reagents, identifying virtues, and brewing simple preparations.",
            1,
        )
        repository.upsert_skill(
            "Athletics",
            "Climbing, lifting, jumping, and other physical effort.",
            1,
        )
        repository.upsert_skill(
            "Awareness",
            "Noticing danger, details, tracks, and hidden movement.",
            1,
        )
        repository.upsert_skill(
            "Melee",
            "Using hand weapons in close combat.",
            1,
        )
        repository.upsert_skill(
            "Persuasion",
            "Influencing others through charm, reason, or presence.",
            1,
        )
        repository.append_history("system", "New adventure created.")

        LOGGER.info("Created new save at %s.", db_path)

        return repository

    def apply_new_game_setup(self, setup: dict[str, Any]) -> None:
        """
        Applies player-authored new-game wizard setup to a fresh save.

        Args:
            setup: Raw setup dictionary from the New Game Wizard.
        """

        clean_setup = normalize_new_game_setup(setup)
        character = clean_setup["character"]
        title = clean_setup["title"]
        start_location = clean_setup["start_location"]
        calendar_settings = clean_setup["calendar"]
        calendar_snapshot = build_calendar_snapshot(
            DEFAULT_START_ELAPSED_MINUTES,
            calendar_settings,
        )

        self.set_meta("title", title)
        self.set_setting("player_name", character["name"])
        self.set_setting("player.appearance", character["appearance"])
        self.set_setting("player.backstory", character["backstory"])
        self.set_setting("player.notes", character["notes"])
        self.set_setting("ai.additional_context", clean_setup["ai_additional_context"])
        self.set_setting("audio.music_enabled", True)
        self.set_setting("audio.narrator_enabled", True)
        self.set_setting("audio.music_volume", 25)
        self.set_setting("audio.tts_volume", 90)
        self.set_setting("audio.current_music", "")
        self.set_setting("new_game.setup", clean_setup)
        self.set_setting("world.setup_context", clean_setup["world_context"])
        self.set_setting("world.genre", clean_setup["specified_genre"])
        self.set_setting("world.game_style", clean_setup["game_style"])
        self.set_setting("currency.description", clean_setup["currency_description"])
        self.set_journal_notes("")
        self.set_currency_denominations(clean_setup["currency_denominations"])
        self.set_calendar_settings(calendar_settings)
        self.set_state_value("elapsed_minutes", str(DEFAULT_START_ELAPSED_MINUTES))
        self.set_state_value("location", start_location)
        self.set_state_value("time", calendar_snapshot["display_label"])
        self.set_state_value("weather", "Clear")
        self.set_state_value("condition", "Healthy")

        for item in clean_setup["starter_items"]:
            self.add_inventory_item(
                name=item["name"],
                category=item["category"],
                quantity=int(item["quantity"]),
                description=item["description"],
                value_base_units=int(item["value_base_units"]),
            )

        for skill in clean_setup["skills"]:
            if str(skill.get("name", "")).strip():
                self.upsert_skill(
                    skill["name"],
                    skill["description"],
                    int(skill["level"]),
                )

        self.append_history("system", "New adventure created from wizard setup.")

    @classmethod
    def list_saves(cls, saves_dir: Path) -> list[SaveSummary]:
        """
        Lists available saves.

        Args:
            saves_dir: Directory where save folders are stored.

        Returns:
            Save summaries sorted by most recently modified first.
        """

        if not saves_dir.exists():
            LOGGER.warning("Saves directory does not exist: %s", saves_dir)
            return []

        summaries: list[SaveSummary] = []

        for db_path in saves_dir.glob(f"*/{cls.DATABASE_NAME}"):
            try:
                repository = cls(db_path)
                title = repository.get_meta("title", default=db_path.parent.name)
                modified = datetime.fromtimestamp(db_path.stat().st_mtime)
                summaries.append(
                    SaveSummary(
                        title=title,
                        db_path=db_path,
                        last_modified=modified,
                    )
                )
            except Exception:
                LOGGER.exception("Failed to read save summary from %s.", db_path)

        summaries.sort(key=lambda summary: summary.last_modified, reverse=True)
        return summaries

    def set_meta(self, key: str, value: str) -> None:
        """
        Stores metadata for this save.

        Args:
            key: Metadata key.
            value: Metadata value.
        """

        if not key.strip():
            LOGGER.error("Attempted to write blank metadata key.")
            return

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        """
        Reads metadata from this save.

        Args:
            key: Metadata key.
            default: Fallback value if the key does not exist.

        Returns:
            Stored metadata value or default.
        """

        if not key.strip():
            LOGGER.error("Attempted to read blank metadata key.")
            return default

        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = ?",
                (key,),
            ).fetchone()

        if row is None:
            return default

        return str(row["value"])

    def set_state_value(self, key: str, value: str) -> None:
        """
        Stores a simple game-state value.

        Args:
            key: State key.
            value: State value.
        """

        if not key.strip():
            LOGGER.error("Attempted to write blank state key.")
            return

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO game_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_state_snapshot(self) -> dict[str, str]:
        """
        Reads the current state snapshot.

        Returns:
            Dictionary of game-state key/value pairs.
        """

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value FROM game_state ORDER BY key"
            ).fetchall()

        return {str(row["key"]): str(row["value"]) for row in rows}

    def get_state_value(self, key: str, default: str = "") -> str:
        """
        Reads one simple game-state value.

        Args:
            key: State key.
            default: Fallback when key does not exist.

        Returns:
            Stored state value or default.
        """

        if not key.strip():
            LOGGER.error("Attempted to read blank state key.")
            return default

        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM game_state WHERE key = ?",
                (key,),
            ).fetchone()

        if row is None:
            return default

        return str(row["value"])

    def add_inventory_item(
        self,
        name: str,
        category: str,
        quantity: int,
        description: str,
        value_base_units: int = 0,
    ) -> None:
        """
        Adds an inventory item.

        Args:
            name: Item name.
            category: Item category.
            quantity: Quantity to add.
            description: Short item description.
            value_base_units: Item value in baseline currency units.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to add inventory item with blank name.")
            return

        if quantity <= 0:
            LOGGER.warning(
                "Invalid inventory quantity '%s' for item '%s'. Defaulting to 1.",
                quantity,
                clean_name,
            )
            quantity = 1

        clean_value = max(0, _safe_int(value_base_units, default=0) or 0)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO inventory_items (
                    name,
                    category,
                    quantity,
                    description,
                    value_base_units
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    clean_name,
                    category.strip(),
                    quantity,
                    description.strip(),
                    clean_value,
                ),
            )

        self.append_history("inventory", f"Added {quantity} x {clean_name}.")

    def list_inventory_items(self) -> list[dict[str, Any]]:
        """
        Reads all inventory items.

        Returns:
            List of inventory item dictionaries.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name, category, quantity, description, value_base_units
                FROM inventory_items
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def replace_inventory_items(self, items: list[dict[str, Any]]) -> None:
        """
        Replaces the player's starting inventory list.

        This is intended for new-game synthesis, where AI-finalized starter items
        should replace blank/default setup inventory instead of being appended.
        """

        clean_items: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue

            name = str(raw_item.get("name", raw_item.get("item_name", ""))).strip()

            if not name or name.casefold() in seen_names:
                continue

            try:
                quantity = int(raw_item.get("quantity", raw_item.get("amount", 1)))
            except (TypeError, ValueError):
                quantity = 1

            try:
                value_base_units = int(
                    raw_item.get(
                        "value_base_units",
                        raw_item.get("base_unit_value", raw_item.get("value", 0)),
                    )
                )
            except (TypeError, ValueError):
                value_base_units = 0

            clean_items.append(
                {
                    "name": name,
                    "category": str(raw_item.get("category", "Item")).strip() or "Item",
                    "quantity": max(1, quantity),
                    "description": str(raw_item.get("description", "")).strip(),
                    "value_base_units": max(0, value_base_units),
                }
            )
            seen_names.add(name.casefold())

        if not clean_items:
            LOGGER.warning("Skipped replace_inventory_items because no valid items were provided.")
            return

        with self._connect() as connection:
            connection.execute("DELETE FROM inventory_items")
            connection.executemany(
                """
                INSERT INTO inventory_items (
                    name,
                    category,
                    quantity,
                    description,
                    value_base_units
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["name"],
                        item["category"],
                        item["quantity"],
                        item["description"],
                        item["value_base_units"],
                    )
                    for item in clean_items
                ],
            )

        self.append_history("inventory", "Starting inventory finalized.")

    def add_alchemy_note(self, title: str, body: str) -> None:
        """
        Adds an alchemy notebook entry.

        Args:
            title: Note title.
            body: Note body.
        """

        clean_title = title.strip()

        if not clean_title:
            LOGGER.error("Attempted to add alchemy note with blank title.")
            return

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO alchemy_notes (title, body, created_at)
                VALUES (?, ?, ?)
                """,
                (clean_title, body.strip(), datetime.now().isoformat(timespec="seconds")),
            )

        self.append_history("alchemy", f"Added alchemy note: {clean_title}.")

    def list_alchemy_notes(self) -> list[dict[str, Any]]:
        """
        Reads all alchemy notes.

        Returns:
            List of alchemy note dictionaries.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, body, created_at
                FROM alchemy_notes
                ORDER BY id DESC
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def remove_inventory_item(self, name: str, quantity: int) -> None:
        """
        Removes or decreases inventory items by name.

        Args:
            name: Item name.
            quantity: Quantity to remove.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to remove inventory item with blank name.")
            return

        if quantity <= 0:
            LOGGER.warning("Invalid remove quantity '%s' for '%s'.", quantity, clean_name)
            return

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, quantity
                FROM inventory_items
                WHERE name = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (clean_name,),
            ).fetchone()

            if row is None:
                LOGGER.warning("Attempted to remove missing inventory item: %s", clean_name)
                return

            current_quantity = int(row["quantity"])
            new_quantity = current_quantity - quantity

            if new_quantity > 0:
                connection.execute(
                    "UPDATE inventory_items SET quantity = ? WHERE id = ?",
                    (new_quantity, row["id"]),
                )
            else:
                connection.execute(
                    "DELETE FROM inventory_items WHERE id = ?",
                    (row["id"],),
                )

        self.append_history("inventory", f"Removed {quantity} x {clean_name}.")

    def modify_inventory_item(
        self,
        *,
        target_name: str,
        new_name: str | None = None,
        description: str | None = None,
        quantity: int | None = None,
        value_base_units: int | None = None,
    ) -> None:
        """
        Modifies one inventory item by name.

        Args:
            target_name: Existing item name.
            new_name: Optional replacement name.
            description: Optional replacement description.
            quantity: Optional replacement quantity.
            value_base_units: Optional replacement value in baseline currency units.
        """

        clean_target = target_name.strip()

        if not clean_target:
            LOGGER.error("Attempted to modify inventory item with blank target name.")
            return

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, description, quantity, value_base_units
                FROM inventory_items
                WHERE name = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (clean_target,),
            ).fetchone()

            if row is None:
                LOGGER.warning("Attempted to modify missing inventory item: %s", clean_target)
                return

            updated_name = new_name.strip() if new_name and new_name.strip() else row["name"]
            updated_description = (
                description.strip()
                if description is not None and description.strip()
                else row["description"]
            )
            updated_quantity = int(row["quantity"])
            updated_value = int(row["value_base_units"])

            if quantity is not None:
                if quantity <= 0:
                    LOGGER.warning(
                        "Invalid modified quantity '%s' for '%s'. Keeping previous quantity.",
                        quantity,
                        clean_target,
                    )
                else:
                    updated_quantity = quantity

            if value_base_units is not None:
                if value_base_units < 0:
                    LOGGER.warning(
                        "Invalid modified value '%s' for '%s'. Keeping previous value.",
                        value_base_units,
                        clean_target,
                    )
                else:
                    updated_value = value_base_units

            connection.execute(
                """
                UPDATE inventory_items
                SET name = ?, description = ?, quantity = ?, value_base_units = ?
                WHERE id = ?
                """,
                (
                    updated_name,
                    updated_description,
                    updated_quantity,
                    updated_value,
                    row["id"],
                ),
            )

        self.append_history("inventory", f"Modified inventory item: {clean_target}.")

    def add_alchemy_reagent(
        self,
        *,
        name: str,
        qualities: list[str],
        motions: list[str],
        virtues: list[str],
        uses: list[str],
        notes: str,
    ) -> None:
        """
        Adds or updates a discovered alchemical reagent.

        Args:
            name: Reagent name.
            qualities: Discovered reagent qualities.
            motions: Discovered alchemical motions.
            virtues: Discovered alchemical virtues.
            uses: Known uses or experimentation hints.
            notes: Freeform notes.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to add alchemy reagent with blank name.")
            return

        discovered_at = datetime.now().isoformat(timespec="seconds")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO alchemy_reagents (
                    name,
                    qualities_json,
                    motions_json,
                    virtues_json,
                    uses_json,
                    notes,
                    discovered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    qualities_json = excluded.qualities_json,
                    motions_json = excluded.motions_json,
                    virtues_json = excluded.virtues_json,
                    uses_json = excluded.uses_json,
                    notes = excluded.notes
                """,
                (
                    clean_name,
                    _encode_string_list(qualities),
                    _encode_string_list(motions),
                    _encode_string_list(virtues),
                    _encode_string_list(uses),
                    notes.strip(),
                    discovered_at,
                ),
            )

        self.append_history("alchemy", f"Discovered reagent: {clean_name}.")

    def list_alchemy_reagents(self) -> list[dict[str, Any]]:
        """
        Reads discovered alchemical reagents.

        Returns:
            List of reagent dictionaries.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    name,
                    qualities_json,
                    motions_json,
                    virtues_json,
                    uses_json,
                    notes,
                    discovered_at
                FROM alchemy_reagents
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        reagents: list[dict[str, Any]] = []

        for row in rows:
            reagents.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "qualities": _decode_string_list(row["qualities_json"], "qualities"),
                    "motions": _decode_string_list(row["motions_json"], "motions"),
                    "virtues": _decode_string_list(row["virtues_json"], "virtues"),
                    "uses": _decode_string_list(row["uses_json"], "uses"),
                    "notes": row["notes"],
                    "discovered_at": row["discovered_at"],
                }
            )

        return reagents

    def add_alchemy_recipe(
        self,
        *,
        name: str,
        ingredients: list[str],
        result: str,
        motions: list[str],
        virtues: list[str],
        notes: str,
    ) -> None:
        """
        Adds or updates a discovered alchemical recipe.

        Args:
            name: Recipe name.
            ingredients: Known ingredients.
            result: Recipe result.
            motions: Required or observed motions.
            virtues: Required or produced virtues.
            notes: Freeform notes.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to add alchemy recipe with blank name.")
            return

        discovered_at = datetime.now().isoformat(timespec="seconds")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO alchemy_recipes (
                    name,
                    ingredients_json,
                    result,
                    motions_json,
                    virtues_json,
                    notes,
                    discovered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    ingredients_json = excluded.ingredients_json,
                    result = excluded.result,
                    motions_json = excluded.motions_json,
                    virtues_json = excluded.virtues_json,
                    notes = excluded.notes
                """,
                (
                    clean_name,
                    _encode_string_list(ingredients),
                    result.strip(),
                    _encode_string_list(motions),
                    _encode_string_list(virtues),
                    notes.strip(),
                    discovered_at,
                ),
            )

        self.append_history("alchemy", f"Discovered recipe: {clean_name}.")

    def list_alchemy_recipes(self) -> list[dict[str, Any]]:
        """
        Reads discovered alchemical recipes.

        Returns:
            List of recipe dictionaries.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    name,
                    ingredients_json,
                    result,
                    motions_json,
                    virtues_json,
                    notes,
                    discovered_at
                FROM alchemy_recipes
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        recipes: list[dict[str, Any]] = []

        for row in rows:
            recipes.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "ingredients": _decode_string_list(
                        row["ingredients_json"],
                        "ingredients",
                    ),
                    "result": row["result"],
                    "motions": _decode_string_list(row["motions_json"], "motions"),
                    "virtues": _decode_string_list(row["virtues_json"], "virtues"),
                    "notes": row["notes"],
                    "discovered_at": row["discovered_at"],
                }
            )

        return recipes

    def upsert_skill(self, name: str, description: str, level: int) -> None:
        """
        Creates or updates a player skill.

        Args:
            name: Skill name.
            description: Skill description.
            level: Skill level from 1 to 5.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to upsert skill with blank name.")
            return

        clean_level = clamp_skill_level(level)
        bonus = bonus_for_level(clean_level)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO skills (name, description, level, xp, bonus)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    level = MAX(skills.level, excluded.level),
                    bonus = MAX(skills.bonus, excluded.bonus)
                """,
                (clean_name, description.strip(), clean_level, bonus),
            )

        self.append_history("skill", f"Skill updated: {clean_name} Level {clean_level}.")

    def replace_skills(self, skills: list[dict[str, Any]]) -> None:
        """
        Replaces the player's starting skill list.

        This is intended for new-game synthesis, where AI-finalized skill names and
        descriptions should replace wizard placeholders instead of being appended.
        """

        clean_skills: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for raw_skill in skills:
            if not isinstance(raw_skill, dict):
                continue

            name = str(raw_skill.get("name", "")).strip()
            description = str(raw_skill.get("description", "")).strip()

            try:
                level = int(raw_skill.get("level", 0))
            except (TypeError, ValueError):
                level = 0

            if not name or not description or level <= 0:
                continue

            if name.casefold() in seen_names:
                LOGGER.warning("Skipped duplicate skill during replace_skills: %s", name)
                continue

            clean_level = clamp_skill_level(level)
            clean_skills.append(
                {
                    "name": name,
                    "description": description,
                    "level": clean_level,
                    "bonus": bonus_for_level(clean_level),
                }
            )
            seen_names.add(name.casefold())

        if not clean_skills:
            LOGGER.warning("Skipped replace_skills because no valid skills were provided.")
            return

        with self._connect() as connection:
            connection.execute("DELETE FROM skills")
            connection.executemany(
                """
                INSERT INTO skills (name, description, level, xp, bonus)
                VALUES (?, ?, ?, 0, ?)
                """,
                [
                    (
                        skill["name"],
                        skill["description"],
                        skill["level"],
                        skill["bonus"],
                    )
                    for skill in clean_skills
                ],
            )

        self.append_history("skill", "Starting skills finalized.")

    def add_skill_xp(self, name: str, xp_amount: int) -> dict[str, Any] | None:
        """
        Adds XP to a skill and levels it up if thresholds are met.

        Args:
            name: Skill name.
            xp_amount: XP to add.

        Returns:
            Updated skill dictionary, or None when the skill does not exist.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to add XP to blank skill name.")
            return None

        if xp_amount <= 0:
            LOGGER.warning("Ignored non-positive XP amount '%s' for %s.", xp_amount, clean_name)
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT name, description, level, xp
                FROM skills
                WHERE name = ?
                """,
                (clean_name,),
            ).fetchone()

            if row is None:
                LOGGER.warning("Attempted to add XP to missing skill: %s", clean_name)
                return None

            current_level = int(row["level"])
            new_xp = int(row["xp"]) + xp_amount
            new_level = level_for_xp(current_level, new_xp)
            new_bonus = bonus_for_level(new_level)

            connection.execute(
                """
                UPDATE skills
                SET xp = ?, level = ?, bonus = ?
                WHERE name = ?
                """,
                (new_xp, new_level, new_bonus, clean_name),
            )

        self.append_history(
            "skill",
            f"Added {xp_amount} XP to {clean_name}. Level {new_level}, bonus +{new_bonus}.",
        )
        return self.get_skill(clean_name)

    def get_skill(self, name: str) -> dict[str, Any] | None:
        """
        Reads one skill by name.

        Args:
            name: Skill name.

        Returns:
            Skill dictionary or None.
        """

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, description, level, xp, bonus
                FROM skills
                WHERE name = ?
                """,
                (name.strip(),),
            ).fetchone()

        if row is None:
            return None

        return dict(row)

    def list_skills(self) -> list[dict[str, Any]]:
        """
        Reads all player skills.

        Returns:
            List of skill dictionaries.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name, description, level, xp, bonus
                FROM skills
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def record_skill_check(
        self,
        *,
        skill_name: str,
        level: int,
        bonus: int,
        roll: int,
        total: int,
        dc: int,
        outcome: str,
    ) -> None:
        """
        Records a resolved skill check.

        Args:
            skill_name: Checked skill.
            level: Skill level used.
            bonus: Skill bonus used.
            roll: Raw d20 roll.
            total: Roll plus bonus.
            dc: Difficulty class.
            outcome: success or failure.
        """

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO skill_checks (
                    skill_name,
                    level,
                    bonus,
                    roll,
                    total,
                    dc,
                    outcome,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    skill_name.strip(),
                    level,
                    bonus,
                    roll,
                    total,
                    dc,
                    outcome,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def list_skill_checks(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Reads recent skill checks.

        Args:
            limit: Maximum checks to return.

        Returns:
            Recent skill check dictionaries, oldest first within the returned set.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, skill_name, level, bonus, roll, total, dc, outcome, created_at
                FROM skill_checks
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        checks = [dict(row) for row in rows]
        checks.reverse()
        return checks

    def upsert_npc(
        self,
        *,
        name: str,
        npc_id: str = "",
        display_name: str = "",
        role: str = "",
        location: str = "",
        public_description: str = "",
        player_facing_information: str = "",
        knowledge_scope: list[str] | None = None,
        known_facts: list[str] | None = None,
        disposition: str = "",
    ) -> dict[str, Any] | None:
        """
        Creates or updates an NPC memory profile.

        Args:
            name: Internal canonical NPC name. This may be hidden from the player.
            npc_id: Stable NPC identifier. Generated from name/role/location if blank.
            display_name: Player-visible name label for the NPCs tab.
            role: Internal role, job, or scene function. This may be hidden from the player.
            location: Usual or last-known location.
            public_description: Observable public description.
            player_facing_information: Player-safe information for the NPCs tab.
            knowledge_scope: Plain-language topics this NPC can plausibly know.
            known_facts: Specific facts this NPC has learned.
            disposition: Current broad attitude toward the player.

        Returns:
            Stored NPC dictionary, or None when name is blank.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.warning("Skipped NPC upsert with blank name.")
            return None

        clean_role = role.strip()
        clean_location = location.strip()
        clean_display_name = display_name.strip() or clean_name or "Unknown NPC"
        clean_public_description = public_description.strip()
        clean_player_facing_information = (
            player_facing_information.strip()
            or clean_public_description
            or clean_role
        )
        clean_npc_id = npc_id.strip() or _npc_id_from_parts(
            clean_name,
            clean_role,
            clean_location,
        )
        timestamp = datetime.now().isoformat(timespec="seconds")

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT knowledge_scope_json, known_facts_json
                FROM npcs
                WHERE npc_id = ?
                """,
                (clean_npc_id,),
            ).fetchone()

            if existing is None:
                merged_knowledge_scope = _clean_string_list(knowledge_scope or [])
                merged_known_facts = _clean_string_list(known_facts or [])
                created_at = timestamp
            else:
                merged_knowledge_scope = _merge_string_lists(
                    _decode_string_list(existing["knowledge_scope_json"], "npc knowledge scope"),
                    knowledge_scope or [],
                )
                merged_known_facts = _merge_string_lists(
                    _decode_string_list(existing["known_facts_json"], "npc known facts"),
                    known_facts or [],
                )
                created_at = connection.execute(
                    "SELECT created_at FROM npcs WHERE npc_id = ?",
                    (clean_npc_id,),
                ).fetchone()["created_at"]

            connection.execute(
                """
                INSERT INTO npcs (
                    npc_id,
                    name,
                    display_name,
                    role,
                    location,
                    public_description,
                    player_facing_information,
                    knowledge_scope_json,
                    known_facts_json,
                    disposition,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(npc_id) DO UPDATE SET
                    name = excluded.name,
                    display_name = CASE
                        WHEN excluded.display_name != '' THEN excluded.display_name
                        ELSE npcs.display_name
                    END,
                    role = CASE
                        WHEN excluded.role != '' THEN excluded.role
                        ELSE npcs.role
                    END,
                    location = CASE
                        WHEN excluded.location != '' THEN excluded.location
                        ELSE npcs.location
                    END,
                    public_description = CASE
                        WHEN excluded.public_description != '' THEN excluded.public_description
                        ELSE npcs.public_description
                    END,
                    player_facing_information = CASE
                        WHEN excluded.player_facing_information != '' THEN excluded.player_facing_information
                        ELSE npcs.player_facing_information
                    END,
                    knowledge_scope_json = excluded.knowledge_scope_json,
                    known_facts_json = excluded.known_facts_json,
                    disposition = CASE
                        WHEN excluded.disposition != '' THEN excluded.disposition
                        ELSE npcs.disposition
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_npc_id,
                    clean_name,
                    clean_display_name,
                    clean_role,
                    clean_location,
                    clean_public_description,
                    clean_player_facing_information,
                    _encode_string_list(merged_knowledge_scope),
                    _encode_string_list(merged_known_facts),
                    disposition.strip(),
                    created_at,
                    timestamp,
                ),
            )

        return self.get_npc(clean_npc_id)

    def add_npc_knowledge(
        self,
        *,
        npc_id: str = "",
        name: str = "",
        facts: list[str],
        role: str = "",
        location: str = "",
    ) -> dict[str, Any] | None:
        """
        Adds one or more known facts to an NPC profile.

        Args:
            npc_id: Stable NPC identifier.
            name: NPC display name, used to create a minimal profile if needed.
            facts: Facts the NPC plausibly learned.
            role: Optional role when creating a minimal profile.
            location: Optional location when creating a minimal profile.

        Returns:
            Updated NPC dictionary, or None when the NPC cannot be resolved.
        """

        clean_facts = _clean_string_list(facts)

        if not clean_facts:
            LOGGER.warning("Skipped NPC knowledge update with no facts.")
            return None

        clean_npc_id = npc_id.strip()
        npc = self.get_npc(clean_npc_id) if clean_npc_id else None

        if npc is None and name.strip():
            npc = self.get_npc_by_name(name.strip())

        if npc is None and name.strip():
            npc = self.upsert_npc(
                npc_id=clean_npc_id,
                name=name.strip(),
                role=role,
                location=location,
            )

        if npc is None:
            LOGGER.warning("Skipped NPC knowledge update for unknown NPC.")
            return None

        updated_facts = _merge_string_lists(npc.get("known_facts", []), clean_facts)
        return self.upsert_npc(
            npc_id=str(npc["npc_id"]),
            name=str(npc["name"]),
            display_name=str(npc.get("display_name", "")),
            role=str(npc.get("role", "")),
            location=str(npc.get("location", "")),
            public_description=str(npc.get("public_description", "")),
            player_facing_information=str(npc.get("player_facing_information", "")),
            knowledge_scope=list(npc.get("knowledge_scope", [])),
            known_facts=updated_facts,
            disposition=str(npc.get("disposition", "")),
        )

    def get_npc(self, npc_id: str) -> dict[str, Any] | None:
        """
        Reads one NPC by stable identifier.

        Args:
            npc_id: Stable NPC identifier.

        Returns:
            NPC dictionary, or None when not found.
        """

        clean_npc_id = npc_id.strip()

        if not clean_npc_id:
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    npc_id,
                    name,
                    display_name,
                    role,
                    location,
                    public_description,
                    player_facing_information,
                    knowledge_scope_json,
                    known_facts_json,
                    disposition,
                    created_at,
                    updated_at
                FROM npcs
                WHERE npc_id = ?
                """,
                (clean_npc_id,),
            ).fetchone()

        if row is None:
            return None

        return _npc_row_to_dict(row)

    def get_npc_by_name(self, name: str) -> dict[str, Any] | None:
        """
        Reads one NPC by exact display name.

        Args:
            name: NPC display name.

        Returns:
            NPC dictionary, or None when not found.
        """

        clean_name = name.strip()

        if not clean_name:
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    npc_id,
                    name,
                    display_name,
                    role,
                    location,
                    public_description,
                    player_facing_information,
                    knowledge_scope_json,
                    known_facts_json,
                    disposition,
                    created_at,
                    updated_at
                FROM npcs
                WHERE name = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (clean_name,),
            ).fetchone()

        if row is None:
            return None

        return _npc_row_to_dict(row)

    def list_npcs(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Lists stored NPC profiles.

        Args:
            limit: Maximum NPCs to return.

        Returns:
            NPC dictionaries ordered by most recently updated first.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    npc_id,
                    name,
                    display_name,
                    role,
                    location,
                    public_description,
                    player_facing_information,
                    knowledge_scope_json,
                    known_facts_json,
                    disposition,
                    created_at,
                    updated_at
                FROM npcs
                ORDER BY updated_at DESC, name COLLATE NOCASE
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()

        return [_npc_row_to_dict(row) for row in rows]

    def list_player_visible_npcs(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Lists only NPC fields safe to display directly to the player.

        Args:
            limit: Maximum NPCs to return.

        Returns:
            Player-visible NPC dictionaries with no private knowledge fields.
        """

        visible_npcs: list[dict[str, Any]] = []

        for npc in self.list_npcs(limit=limit):
            description = str(npc.get("public_description") or "").strip()
            notes = str(
                npc.get("player_facing_information")
                or npc.get("public_description")
                or npc.get("role")
                or ""
            ).strip()

            visible_npcs.append(
                {
                    "npc_id": npc["npc_id"],
                    "display_name": (
                        npc.get("display_name")
                        or npc.get("name")
                        or "Unknown NPC"
                    ),
                    "description": description,
                    "location": npc["location"],
                    "notes": notes,
                }
            )

        return visible_npcs

    def list_relevant_npcs(
        self,
        *,
        location: str = "",
        query_text: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """
        Lists NPCs likely relevant to the current story turn.

        Args:
            location: Current player location.
            query_text: Player command text.
            limit: Maximum NPCs to return.

        Returns:
            Relevant NPC dictionaries.
        """

        clean_location = location.strip().casefold()
        clean_query = query_text.strip().casefold()
        query_tokens = {
            token
            for token in re.split(r"[^a-zA-Z0-9']+", clean_query)
            if len(token) >= 3
        }
        scored_npcs: list[tuple[int, dict[str, Any]]] = []

        for npc in self.list_npcs(limit=100):
            score = 0
            name = str(npc.get("name", "")).casefold()
            role = str(npc.get("role", "")).casefold()
            npc_location = str(npc.get("location", "")).casefold()

            if clean_location and npc_location == clean_location:
                score += 4
            if name and name in clean_query:
                score += 4
            if role and role in clean_query:
                score += 3
            if query_tokens and query_tokens.intersection(
                set(re.split(r"[^a-zA-Z0-9']+", " ".join([name, role, npc_location])))
            ):
                score += 1

            if score > 0:
                scored_npcs.append((score, npc))

        scored_npcs.sort(
            key=lambda item: (
                item[0],
                str(item[1].get("updated_at", "")),
                str(item[1].get("name", "")),
            ),
            reverse=True,
        )
        return [npc for _, npc in scored_npcs[: max(1, limit)]]

    def append_history(self, kind: str, content: str) -> None:
        """
        Appends an entry to the adventure history.

        Args:
            kind: Entry category, such as player, story, system, inventory, or alchemy.
            content: Entry text.
        """

        clean_content = content.strip()

        if not clean_content:
            LOGGER.warning("Skipped blank history entry of kind '%s'.", kind)
            return

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO history_entries (kind, content, created_at)
                VALUES (?, ?, ?)
                """,
                (kind.strip() or "misc", clean_content, datetime.now().isoformat(timespec="seconds")),
            )

    def append_mechanical_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        status: str,
        message: str,
    ) -> None:
        """
        Stores a mechanical event application result.

        Args:
            event_type: Event type name.
            payload: Event payload.
            status: applied, skipped, or failed.
            message: Short status message.
        """

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mechanical_events (
                    event_type,
                    payload_json,
                    status,
                    message,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_type.strip() or "UnknownEvent",
                    json.dumps(payload),
                    status,
                    message.strip(),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def list_mechanical_events(self) -> list[dict[str, Any]]:
        """
        Reads mechanical event history.

        Returns:
            List of mechanical event dictionaries.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, event_type, payload_json, status, message, created_at
                FROM mechanical_events
                ORDER BY id ASC
                """
            ).fetchall()

        events: list[dict[str, Any]] = []

        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                LOGGER.exception("Mechanical event payload contained invalid JSON.")
                payload = {}

            events.append(
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "payload": payload,
                    "status": row["status"],
                    "message": row["message"],
                    "created_at": row["created_at"],
                }
            )

        return events

    def list_history(self) -> list[dict[str, Any]]:
        """
        Reads the full adventure history.

        Returns:
            List of history entry dictionaries.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, kind, content, created_at
                FROM history_entries
                ORDER BY id ASC
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def upsert_active_task(
        self,
        *,
        name: str,
        category: str = "Task",
        status: str = "Active",
        description: str = "",
        requester: str = "",
        location: str = "",
        reward: str = "",
        due_date: str = "",
        notes: str = "",
    ) -> dict[str, Any] | None:
        """
        Creates or updates a visible active task.

        Args:
            name: Task name.
            category: Task category such as Quest, Commission, or Order.
            status: Current task status.
            description: What needs to happen.
            requester: Person or faction associated with the task.
            location: Relevant location.
            reward: Expected reward, cost, or exchange.
            due_date: In-world due date or timing note.
            notes: Additional player-visible task notes.

        Returns:
            Stored task dictionary, or None if the task name is blank.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to upsert active task with blank name.")
            return None

        clean_category = category.strip() or "Task"
        clean_status = status.strip() or "Active"
        timestamp = datetime.now().isoformat(timespec="seconds")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO active_tasks (
                    name,
                    category,
                    status,
                    description,
                    requester,
                    location,
                    reward,
                    due_date,
                    notes,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    category = excluded.category,
                    status = excluded.status,
                    description = CASE
                        WHEN excluded.description != '' THEN excluded.description
                        ELSE active_tasks.description
                    END,
                    requester = CASE
                        WHEN excluded.requester != '' THEN excluded.requester
                        ELSE active_tasks.requester
                    END,
                    location = CASE
                        WHEN excluded.location != '' THEN excluded.location
                        ELSE active_tasks.location
                    END,
                    reward = CASE
                        WHEN excluded.reward != '' THEN excluded.reward
                        ELSE active_tasks.reward
                    END,
                    due_date = CASE
                        WHEN excluded.due_date != '' THEN excluded.due_date
                        ELSE active_tasks.due_date
                    END,
                    notes = CASE
                        WHEN excluded.notes != '' THEN excluded.notes
                        ELSE active_tasks.notes
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_name,
                    clean_category,
                    clean_status,
                    description.strip(),
                    requester.strip(),
                    location.strip(),
                    reward.strip(),
                    due_date.strip(),
                    notes.strip(),
                    timestamp,
                    timestamp,
                ),
            )

        return self.get_active_task(clean_name)

    def complete_active_task(self, name: str, notes: str = "") -> dict[str, Any] | None:
        """
        Marks an active task as completed.

        Args:
            name: Task name.
            notes: Optional completion notes.

        Returns:
            Updated task dictionary, or None when no matching task exists.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to complete active task with blank name.")
            return None

        timestamp = datetime.now().isoformat(timespec="seconds")

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT notes
                FROM active_tasks
                WHERE name = ?
                """,
                (clean_name,),
            ).fetchone()

            if row is None:
                LOGGER.warning("Attempted to complete missing active task: %s", clean_name)
                return None

            updated_notes = notes.strip() or str(row["notes"])
            connection.execute(
                """
                UPDATE active_tasks
                SET status = 'Completed',
                    notes = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE name = ?
                """,
                (updated_notes, timestamp, timestamp, clean_name),
            )

        return self.get_active_task(clean_name)

    def get_active_task(self, name: str) -> dict[str, Any] | None:
        """
        Reads one task by exact name.

        Args:
            name: Task name.

        Returns:
            Task dictionary, or None when missing.
        """

        clean_name = name.strip()

        if not clean_name:
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    name,
                    category,
                    status,
                    description,
                    requester,
                    location,
                    reward,
                    due_date,
                    notes,
                    created_at,
                    updated_at,
                    completed_at
                FROM active_tasks
                WHERE name = ?
                """,
                (clean_name,),
            ).fetchone()

        if row is None:
            return None

        return dict(row)

    def list_active_tasks(
        self,
        *,
        include_completed: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Lists current active tasks.

        Args:
            include_completed: Whether completed tasks should be included.
            limit: Maximum tasks to return.

        Returns:
            Task dictionaries.
        """

        where_clause = "" if include_completed else "WHERE status != 'Completed'"

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    id,
                    name,
                    category,
                    status,
                    description,
                    requester,
                    location,
                    reward,
                    due_date,
                    notes,
                    created_at,
                    updated_at,
                    completed_at
                FROM active_tasks
                {where_clause}
                ORDER BY updated_at DESC, name COLLATE NOCASE
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()

        return [dict(row) for row in rows]

    def set_journal_notes(self, notes: str) -> None:
        """
        Stores the player's private journal notes.

        These notes are intentionally not included in AdventureState or AI context.
        """

        self.set_setting("journal.private_notes", str(notes))

    def get_journal_notes(self) -> str:
        """
        Reads the player's private journal notes.

        Returns:
            Private journal text.
        """

        return str(self.get_setting("journal.private_notes", ""))

    def set_world_summary(self, summary: str) -> None:
        """
        Stores the AI-synthesized world summary for this save.

        Args:
            summary: Player-known world summary.
        """

        self.set_setting("world.summary", str(summary).strip())

    def get_world_summary(self) -> str:
        """
        Reads the stored world summary.

        Returns:
            Player-known world summary.
        """

        return str(self.get_setting("world.summary", ""))

    def set_world_lore(self, lore: Any) -> None:
        """
        Stores grouped player-facing world lore.

        Args:
            lore: Mapping of category names to lore entry strings.
        """

        self.set_setting("world.lore", _normalize_world_lore(lore))

    def get_world_lore(self) -> dict[str, dict[str, str]]:
        """
        Reads grouped player-facing world lore.

        Returns:
            Mapping of category names to keyed lore entry strings.
        """

        return _normalize_world_lore(self.get_setting("world.lore", {}))

    def add_world_lore_entry(self, category: str, key: str, text: str) -> None:
        """
        Adds a player-facing world lore entry to a category.

        Args:
            category: Player-facing lore category.
            key: Stable entry key, such as a location/faction/religion name.
            text: Lore text.
        """

        clean_category = str(category or "World").strip() or "World"
        clean_key = str(key or "").strip() or _derive_world_lore_key(text)
        clean_text = str(text or "").strip()

        if not clean_key or not clean_text:
            LOGGER.warning("Skipped incomplete world lore entry.")
            return

        lore = self.get_world_lore()
        entries = lore.setdefault(clean_category, {})
        entries.setdefault(clean_key, clean_text)
        self.set_world_lore(lore)

    def change_world_lore_entry(self, category: str, key: str, text: str) -> None:
        """
        Changes one existing keyed player-facing world lore entry.

        Args:
            category: Player-facing lore category.
            key: Existing entry key to change.
            text: Full replacement lore text.
        """

        clean_category = str(category or "World").strip() or "World"
        clean_key = str(key or "").strip()
        clean_text = str(text or "").strip()

        if not clean_key or not clean_text:
            LOGGER.warning("Skipped incomplete changed world lore entry.")
            return

        lore = self.get_world_lore()
        entries = lore.setdefault(clean_category, {})
        entries[clean_key] = clean_text

        self.set_world_lore(lore)

    def set_setting(self, key: str, value: Any) -> None:
        """
        Stores a user setting as JSON.

        Args:
            key: Setting key.
            value: JSON-serializable setting value.
        """

        if not key.strip():
            LOGGER.error("Attempted to write blank setting key.")
            return

        try:
            encoded = json.dumps(value)
        except TypeError:
            LOGGER.exception("Setting '%s' could not be JSON encoded.", key)
            return

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO settings (key, value_json)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (key, encoded),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        """
        Reads a user setting.

        Args:
            key: Setting key.
            default: Fallback if setting does not exist or cannot be decoded.

        Returns:
            Decoded setting value or default.
        """

        if not key.strip():
            LOGGER.error("Attempted to read blank setting key.")
            return default

        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM settings WHERE key = ?",
                (key,),
            ).fetchone()

        if row is None:
            return default

        try:
            return json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            LOGGER.exception("Setting '%s' contained invalid JSON.", key)
            return default

    def list_settings(self) -> dict[str, Any]:
        """
        Reads all user settings.

        Returns:
            Dictionary of decoded setting values keyed by setting name.
        """

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value_json FROM settings ORDER BY key"
            ).fetchall()

        settings: dict[str, Any] = {}

        for row in rows:
            key = str(row["key"])

            try:
                settings[key] = json.loads(str(row["value_json"]))
            except json.JSONDecodeError:
                LOGGER.exception("Setting '%s' contained invalid JSON.", key)

        return settings

    def set_currency_denominations(self, denominations: Any) -> None:
        """
        Stores player-provided currency denominations.

        Args:
            denominations: Denomination dictionaries with name, plural_name, and value.
        """

        self.set_setting(
            "currency.denominations",
            normalize_currency_denominations(
                denominations,
                fallback_denominations=[],
            ),
        )

    def get_currency_denominations(self) -> list[dict[str, Any]]:
        """
        Reads currency denominations for this save.

        Returns:
            Clean denomination dictionaries sorted from smallest to largest.
        """

        stored_denominations = self.get_setting("currency.denominations", None)

        if stored_denominations is None:
            return normalize_currency_denominations(DEFAULT_CURRENCY_DENOMINATIONS)

        return normalize_currency_denominations(
            stored_denominations,
            fallback_denominations=[],
        )

    def set_calendar_settings(self, settings: Any) -> None:
        """
        Stores player-provided calendar and time-display settings.

        Args:
            settings: Calendar settings dictionary.
        """

        self.set_setting(
            "calendar.settings",
            normalize_calendar_settings(settings),
        )

    def get_calendar_settings(self) -> dict[str, Any]:
        """
        Reads calendar settings for this save.

        Returns:
            Clean calendar settings dictionary.
        """

        return normalize_calendar_settings(
            self.get_setting(
                "calendar.settings",
                DEFAULT_CALENDAR_SETTINGS,
            )
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """
        Opens a SQLite connection and closes it after use.

        Yields:
            SQLite connection configured with row dictionaries.
        """

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row

        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        """Creates database tables if they do not already exist."""

        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS game_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inventory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL DEFAULT 1,
                    description TEXT NOT NULL DEFAULT '',
                    value_base_units INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS alchemy_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alchemy_reagents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    qualities_json TEXT NOT NULL DEFAULT '[]',
                    motions_json TEXT NOT NULL DEFAULT '[]',
                    virtues_json TEXT NOT NULL DEFAULT '[]',
                    uses_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    discovered_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alchemy_recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    ingredients_json TEXT NOT NULL DEFAULT '[]',
                    result TEXT NOT NULL DEFAULT '',
                    motions_json TEXT NOT NULL DEFAULT '[]',
                    virtues_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    discovered_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    level INTEGER NOT NULL DEFAULT 1,
                    xp INTEGER NOT NULL DEFAULT 0,
                    bonus INTEGER NOT NULL DEFAULT 2
                );

                CREATE TABLE IF NOT EXISTS skill_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    bonus INTEGER NOT NULL,
                    roll INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    dc INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS active_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL DEFAULT 'Task',
                    status TEXT NOT NULL DEFAULT 'Active',
                    description TEXT NOT NULL DEFAULT '',
                    requester TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    reward TEXT NOT NULL DEFAULT '',
                    due_date TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS npcs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    npc_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    public_description TEXT NOT NULL DEFAULT '',
                    player_facing_information TEXT NOT NULL DEFAULT '',
                    knowledge_scope_json TEXT NOT NULL DEFAULT '[]',
                    known_facts_json TEXT NOT NULL DEFAULT '[]',
                    disposition TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS history_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mechanical_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                """
            )
            _ensure_column(
                connection,
                "inventory_items",
                "value_base_units",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "npcs",
                "display_name",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "npcs",
                "player_facing_information",
                "TEXT NOT NULL DEFAULT ''",
            )


def _slugify(value: str) -> str:
    """
    Converts text into a filesystem-safe slug.

    Args:
        value: Input text.

    Returns:
        Filesystem-safe slug.
    """

    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    cleaned = cleaned.strip("_")

    if not cleaned:
        LOGGER.warning("Blank save title slugified to default name.")
        return "New_Adventure"

    return cleaned[:40]


def _npc_id_from_parts(name: str, role: str, location: str) -> str:
    """Builds a stable NPC id from visible NPC information."""

    raw_value = "_".join(
        part.strip()
        for part in [name, role, location]
        if part.strip()
    )
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", raw_value.casefold()).strip("_")

    if not cleaned:
        return "unknown_npc"

    return cleaned[:80]


def _clean_string_list(values: list[str]) -> list[str]:
    """Returns a clean, de-duplicated string list."""

    clean_values: list[str] = []
    seen: set[str] = set()

    for value in values:
        clean_value = str(value).strip()
        lookup = clean_value.casefold()

        if clean_value and lookup not in seen:
            clean_values.append(clean_value)
            seen.add(lookup)

    return clean_values


def _merge_string_lists(existing: list[str], additions: list[str]) -> list[str]:
    """Merges existing and new string lists without case-insensitive duplicates."""

    return _clean_string_list([*existing, *additions])


def _safe_int(value: Any, *, default: int | None) -> int | None:
    """Safely converts a value to int."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    """Adds a SQLite column when an existing save predates it."""

    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing_columns = {str(row["name"]) for row in rows}

    if column_name in existing_columns:
        return

    connection.execute(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
    )


def _npc_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Converts an NPC database row to a plain dictionary."""

    return {
        "id": row["id"],
        "npc_id": row["npc_id"],
        "name": row["name"],
        "display_name": row["display_name"],
        "role": row["role"],
        "location": row["location"],
        "public_description": row["public_description"],
        "player_facing_information": row["player_facing_information"],
        "knowledge_scope": _decode_string_list(
            row["knowledge_scope_json"],
            "npc knowledge scope",
        ),
        "known_facts": _decode_string_list(row["known_facts_json"], "npc known facts"),
        "disposition": row["disposition"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _encode_string_list(values: list[str]) -> str:
    """Encodes a clean string list as JSON."""

    clean_values = [
        str(value).strip()
        for value in values
        if str(value).strip()
    ]

    return json.dumps(clean_values)


def _decode_string_list(raw_json: Any, label: str) -> list[str]:
    """Decodes a JSON string list, logging and recovering from invalid data."""

    try:
        values = json.loads(str(raw_json))
    except json.JSONDecodeError:
        LOGGER.exception("Invalid JSON list for alchemy %s.", label)
        return []

    if not isinstance(values, list):
        LOGGER.warning("Alchemy %s JSON was not a list.", label)
        return []

    return [
        str(value).strip()
        for value in values
        if str(value).strip()
    ]


def _normalize_world_lore(raw_lore: Any) -> dict[str, dict[str, str]]:
    """Normalizes grouped player-facing world lore."""

    if not isinstance(raw_lore, dict):
        return {}

    lore: dict[str, dict[str, str]] = {}

    for raw_category, raw_entries in raw_lore.items():
        category = str(raw_category).strip()

        if not category:
            continue

        if isinstance(raw_entries, dict):
            entries = {
                str(key).strip(): str(value).strip()
                for key, value in raw_entries.items()
                if str(key).strip() and str(value).strip()
            }
        elif isinstance(raw_entries, str):
            clean_entry = raw_entries.strip()
            entries = (
                {_derive_world_lore_key(clean_entry): clean_entry}
                if clean_entry
                else {}
            )
        elif isinstance(raw_entries, list):
            entries = {}

            for entry in raw_entries:
                clean_entry = str(entry).strip()

                if clean_entry:
                    entries[_derive_world_lore_key(clean_entry)] = clean_entry
        else:
            entries = {}

        if entries:
            lore[category] = entries

    return lore


def _derive_world_lore_key(text: Any) -> str:
    """Derives a stable-ish lore key from text when older data lacks one."""

    clean_text = str(text or "").strip()

    if not clean_text:
        return ""

    key = clean_text.split(":", 1)[0].strip()

    if key:
        return key[:80]

    return clean_text[:80]
