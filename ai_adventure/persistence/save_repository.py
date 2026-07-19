from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_adventure.alchemy.ingredients import normalize_recipe_ingredients
from ai_adventure.ai.modes import (
    default_ai_mode_settings,
    normalize_ai_mode_preferences,
)
from ai_adventure.calendar_system import (
    DEFAULT_CALENDAR_SETTINGS,
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    normalize_calendar_settings,
)
from ai_adventure.audio.tts_settings import normalize_tts_audio_fields
from ai_adventure.audio.voices import DEFAULT_NARRATOR_VOICE
from ai_adventure.combat import normalize_combat_state, normalize_equipment, normalize_item_metadata
from ai_adventure.currency import (
    DEFAULT_CURRENCY_DENOMINATIONS,
    normalize_currency_denominations,
)
from ai_adventure.item_categories import normalize_inventory_category
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.narration_preferences import (
    DEFAULT_NARRATION_STYLE,
    DEFAULT_NARRATION_TENSE,
)
from ai_adventure.locations import (
    DEFAULT_MOVE_SPEED_MPH,
    DEFAULT_TRAVEL_MODE,
    DEFAULT_TRAVEL_SPEED_MULTIPLIER,
    KnownLocation,
    clean_player_location_name,
    normalize_known_location,
    normalize_known_locations,
)
from ai_adventure.skills.rules import bonus_for_level, clamp_skill_level, level_for_xp


LOGGER = logging.getLogger(__name__)
GM_SECRET_STATUSES = frozenset({"active", "revealed", "retired"})


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


class DuplicateSaveTitleError(ValueError):
    """Raised when a new save title already exists."""


class SaveFileOperationError(ValueError):
    """Raised when a save file operation cannot be performed safely."""


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
        self.set_player_equipment(self.get_setting("player.equipment", {}))

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

        clean_title = title.strip() or "New Adventure"
        cls._raise_for_duplicate_save_title(saves_dir, clean_title)

        safe_title = _slugify(clean_title)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        save_dir = _unique_save_dir(saves_dir, safe_title, timestamp)
        db_path = save_dir / cls.DATABASE_NAME

        repository = cls(db_path)
        repository.set_meta("title", clean_title)

        if setup is not None:
            repository.apply_new_game_setup(setup)
            LOGGER.info("Created new configured save at %s.", db_path)
            return repository

        repository.set_setting("player_name", "Player Name")
        repository.set_setting("player.appearance", "")
        repository.set_setting("player.backstory", "")
        repository.set_setting("player.notes", "")
        repository.set_setting("ai.additional_context", "")
        repository.set_setting("ai.narration_tense", DEFAULT_NARRATION_TENSE)
        repository.set_setting("ai.narration_style", DEFAULT_NARRATION_STYLE)
        repository._set_default_ai_mode_settings()
        repository.set_setting("audio.music_enabled", True)
        repository.set_setting("audio.narrator_enabled", True)
        repository.set_setting("audio.music_volume", 25)
        repository.set_setting("audio.tts_volume", 90)
        repository.set_setting("audio.tts_voice", DEFAULT_NARRATOR_VOICE)
        repository.set_setting("audio.tts_speed", 100)
        repository.set_setting("audio.tts_voice_mode", "preset")
        repository.set_setting(
            "audio.tts_voice_blend",
            normalize_tts_audio_fields({})["tts_voice_blend"],
        )
        repository.set_setting("audio.tts_custom_voices", [])
        repository.set_setting("audio.current_music", "")
        repository.set_journal_notes("")
        repository.set_journal_share_with_ai(False)
        repository.set_currency_denominations(DEFAULT_CURRENCY_DENOMINATIONS)
        repository.set_calendar_settings(DEFAULT_CALENDAR_SETTINGS)
        repository.set_current_calendar_minute(DEFAULT_START_ELAPSED_MINUTES)
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
            "Crafting",
            "Identifying useful materials, preparing components, repairing items, and making simple preparations.",
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
        audio_settings = clean_setup["audio"]
        narration_preferences = clean_setup["narration"]
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
        self.set_setting("ai.narration_tense", narration_preferences["tense"])
        self.set_setting("ai.narration_style", narration_preferences["style"])
        self._set_ai_mode_settings(clean_setup["ai_settings"])
        self.set_setting("audio.music_enabled", bool(audio_settings["music_enabled"]))
        self.set_setting("audio.narrator_enabled", bool(audio_settings["narrator_enabled"]))
        self.set_setting("audio.music_volume", int(audio_settings["music_volume"]))
        self.set_setting("audio.tts_volume", int(audio_settings["tts_volume"]))
        self.set_setting("audio.tts_voice", audio_settings["tts_voice"])
        self.set_setting("audio.tts_speed", int(audio_settings["tts_speed"]))
        self.set_setting("audio.tts_voice_mode", audio_settings["tts_voice_mode"])
        self.set_setting("audio.tts_voice_blend", audio_settings["tts_voice_blend"])
        self.set_setting("audio.tts_custom_voices", audio_settings["tts_custom_voices"])
        self.set_setting("audio.current_music", "")
        self.set_setting("new_game.setup", clean_setup)
        self.set_setting("world.setup_context", clean_setup["world_context"])
        self.set_setting("world.genre", clean_setup["specified_genre"])
        self.set_setting("world.game_style", clean_setup["game_style"])
        self.set_setting("currency.description", clean_setup["currency_description"])
        self.set_journal_notes("")
        self.set_journal_share_with_ai(False)
        self.set_currency_denominations(clean_setup["currency_denominations"])
        self.set_calendar_settings(calendar_settings)
        self.set_current_calendar_minute(DEFAULT_START_ELAPSED_MINUTES)
        self.set_state_value("location", start_location)
        self.set_state_value("time", calendar_snapshot["display_label"])
        self.set_state_value("weather", "Clear")
        self.set_state_value("condition", "Healthy")

        for item in clean_setup["starter_items"]:
            if bool(item.get("requires_ai_invention")) or not str(item.get("name", "")).strip():
                continue

            self.add_inventory_item(
                name=item["name"],
                category=item["category"],
                quantity=int(item["quantity"]),
                description=item["description"],
                value_base_units=int(item["value_base_units"]),
                metadata=item,
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

    @classmethod
    def save_title_exists(cls, saves_dir: Path, title: str) -> bool:
        """Returns True when a save already uses this player-facing title."""

        return cls._save_title_exists(saves_dir, title)

    @classmethod
    def rename_save(cls, saves_dir: Path, db_path: Path, new_title: str) -> None:
        """Renames an existing save's player-facing title."""

        save_dir = cls._save_dir_for_db_path(saves_dir, db_path)
        clean_title = new_title.strip() or "New Adventure"
        resolved_db_path = save_dir / cls.DATABASE_NAME

        if cls._save_title_exists(
            saves_dir,
            clean_title,
            exclude_db_path=resolved_db_path,
        ):
            raise DuplicateSaveTitleError(
                f"A save named '{clean_title}' already exists."
            )

        repository = cls(resolved_db_path)
        repository.set_meta("title", clean_title)

    @classmethod
    def delete_save(cls, saves_dir: Path, db_path: Path) -> None:
        """Deletes one save directory after validating it belongs to saves_dir."""

        save_dir = cls._save_dir_for_db_path(saves_dir, db_path)
        shutil.rmtree(save_dir)

    @classmethod
    def _save_title_exists(
        cls,
        saves_dir: Path,
        title: str,
        *,
        exclude_db_path: Path | None = None,
    ) -> bool:
        """Returns True when another save already uses this title."""

        clean_title = _normalize_save_title(title)

        if not clean_title:
            clean_title = _normalize_save_title("New Adventure")

        resolved_excluded = exclude_db_path.resolve() if exclude_db_path is not None else None

        return any(
            _normalize_save_title(summary.title) == clean_title
            and (
                resolved_excluded is None
                or summary.db_path.resolve() != resolved_excluded
            )
            for summary in cls.list_saves(saves_dir)
        )

    @classmethod
    def _save_dir_for_db_path(cls, saves_dir: Path, db_path: Path) -> Path:
        """Returns the validated save directory for db_path."""

        resolved_saves_dir = saves_dir.resolve()
        resolved_db_path = db_path.resolve()

        if resolved_db_path.name != cls.DATABASE_NAME:
            raise SaveFileOperationError("Selected path is not an adventure save database.")

        save_dir = resolved_db_path.parent

        if save_dir.parent != resolved_saves_dir:
            raise SaveFileOperationError("Selected save is not inside the configured saves directory.")

        if not resolved_db_path.exists():
            raise SaveFileOperationError("Selected save no longer exists.")

        return save_dir

    @classmethod
    def _raise_for_duplicate_save_title(cls, saves_dir: Path, title: str) -> None:
        """Rejects duplicate player-facing save titles."""

        clean_title = title.strip() or "New Adventure"

        if cls.save_title_exists(saves_dir, clean_title):
            raise DuplicateSaveTitleError(
                f"A save named '{clean_title}' already exists."
            )

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
        metadata: Any | None = None,
    ) -> None:
        """
        Adds an inventory item.

        Args:
            name: Item name.
            category: Item category.
            quantity: Quantity to add.
            description: Short item description.
            value_base_units: Item value in baseline currency units.
            metadata: Optional structured equipment/combat metadata.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to add inventory item with blank name.")
            return

        category = normalize_inventory_category(
            category,
            name=clean_name,
            description=description,
            item_type=(metadata or {}).get("item_type", "")
            if isinstance(metadata, dict)
            else "",
        )

        if quantity <= 0:
            LOGGER.warning(
                "Invalid inventory quantity '%s' for item '%s'. Defaulting to 1.",
                quantity,
                clean_name,
            )
            quantity = 1

        clean_value = max(0, _safe_int(value_base_units, default=0) or 0)
        raw_metadata = metadata if isinstance(metadata, dict) else {}
        clean_metadata = normalize_item_metadata(
            metadata,
            name=clean_name,
            category=category,
            description=description,
        )
        clean_metadata["quantity_unit"] = _inventory_quantity_unit(raw_metadata)
        clean_metadata["storage_location"] = _inventory_storage_location(raw_metadata)
        clean_metadata["item_uuid"] = str(raw_metadata.get("item_uuid", "")).strip() or str(uuid.uuid4())
        metadata_json = _encode_json_dict(clean_metadata)

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, category, quantity, description, value_base_units, metadata_json
                FROM inventory_items
                WHERE name = ? COLLATE NOCASE
                ORDER BY id ASC
                """,
                (clean_name,),
            ).fetchall()

            if rows:
                primary_row = rows[0]
                duplicate_ids = [row["id"] for row in rows[1:]]
                existing_quantity = sum(int(row["quantity"]) for row in rows)
                existing_value = max(int(row["value_base_units"]) for row in rows)
                updated_category = category.strip() or str(primary_row["category"])
                updated_description = description.strip() or str(primary_row["description"])
                updated_quantity = existing_quantity + quantity
                updated_value = clean_value if clean_value > 0 else existing_value
                updated_metadata = (
                    metadata_json
                    if clean_metadata.get("item_type") != "Item"
                    else str(primary_row["metadata_json"])
                )
                connection.execute(
                    """
                    UPDATE inventory_items
                    SET category = ?,
                        quantity = ?,
                        description = ?,
                        value_base_units = ?,
                        metadata_json = ?
                    WHERE id = ?
                    """,
                    (
                        updated_category,
                        updated_quantity,
                        updated_description,
                        updated_value,
                        updated_metadata,
                        primary_row["id"],
                    ),
                )

                if duplicate_ids:
                    placeholders = ", ".join("?" for _ in duplicate_ids)
                    connection.execute(
                        f"DELETE FROM inventory_items WHERE id IN ({placeholders})",
                        duplicate_ids,
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO inventory_items (
                        name,
                        category,
                        quantity,
                        description,
                        value_base_units,
                        metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_name,
                        category.strip(),
                        quantity,
                        description.strip(),
                        clean_value,
                        metadata_json,
                    ),
                )

            _upsert_item_catalog_entry(
                connection,
                name=clean_name,
                category=category,
                description=description,
                value_base_units=clean_value,
                metadata=clean_metadata,
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
                SELECT
                    id,
                    name,
                    category,
                    quantity,
                    equipped,
                    description,
                    value_base_units,
                    metadata_json
                FROM inventory_items
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        return [_inventory_row_to_dict(row) for row in rows]

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
                    "metadata": normalize_item_metadata(
                        raw_item.get("metadata", raw_item),
                        name=name,
                        category=str(raw_item.get("category", "Item")),
                        description=str(raw_item.get("description", "")),
                    ),
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
                    value_base_units,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["name"],
                        item["category"],
                        item["quantity"],
                        item["description"],
                        item["value_base_units"],
                        _encode_json_dict(item["metadata"]),
                    )
                    for item in clean_items
                ],
            )
            for item in clean_items:
                _upsert_item_catalog_entry(
                    connection,
                    name=item["name"],
                    category=item["category"],
                    description=item["description"],
                    value_base_units=item["value_base_units"],
                    metadata=item["metadata"],
                )

        self.set_player_equipment(self.get_setting("player.equipment", {}))
        self.append_history("inventory", "Starting inventory finalized.")

    def upsert_item_catalog_entry(
        self,
        *,
        name: str,
        category: str = "",
        description: str = "",
        value_base_units: int = 0,
        metadata: Any | None = None,
    ) -> None:
        """Adds or updates one remembered item definition."""

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to upsert item catalog entry with blank name.")
            return

        clean_value = max(0, _safe_int(value_base_units, default=0) or 0)

        with self._connect() as connection:
            _upsert_item_catalog_entry(
                connection,
                name=clean_name,
                category=category,
                description=description,
                value_base_units=clean_value,
                metadata=metadata,
            )

    def list_item_catalog(self) -> list[dict[str, Any]]:
        """Reads all remembered item definitions without inventory quantities."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    name,
                    category,
                    description,
                    value_base_units,
                    metadata_json,
                    first_seen_at,
                    updated_at
                FROM item_catalog
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        return [_item_catalog_row_to_dict(row) for row in rows]

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

        self.set_player_equipment(self.get_setting("player.equipment", {}))
        self.append_history("inventory", f"Removed {quantity} x {clean_name}.")

    def modify_inventory_item(
        self,
        *,
        target_name: str,
        new_name: str | None = None,
        category: str | None = None,
        description: str | None = None,
        quantity: int | None = None,
        value_base_units: int | None = None,
        metadata: Any | None = None,
    ) -> None:
        """
        Modifies one inventory item by name.

        Args:
            target_name: Existing item name.
            new_name: Optional replacement name.
            category: Optional replacement category.
            description: Optional replacement description.
            quantity: Optional replacement quantity.
            value_base_units: Optional replacement value in baseline currency units.
            metadata: Optional replacement metadata.
        """

        clean_target = target_name.strip()

        if not clean_target:
            LOGGER.error("Attempted to modify inventory item with blank target name.")
            return

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, category, description, quantity, value_base_units, metadata_json
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
            updated_category = (
                category.strip()
                if category is not None and category.strip()
                else row["category"]
            )
            updated_description = (
                description.strip()
                if description is not None and description.strip()
                else row["description"]
            )
            updated_quantity = int(row["quantity"])
            updated_value = int(row["value_base_units"])
            existing_metadata = _decode_json_dict(
                row["metadata_json"], "inventory item metadata"
            )
            metadata_updates = metadata if isinstance(metadata, dict) else {}
            updated_metadata = normalize_item_metadata(
                {**existing_metadata, **metadata_updates},
                name=str(updated_name),
                category=str(updated_category),
                description=str(updated_description),
            )
            merged_metadata = {**existing_metadata, **metadata_updates}
            updated_metadata["quantity_unit"] = _inventory_quantity_unit(merged_metadata)
            updated_metadata["storage_location"] = _inventory_storage_location(merged_metadata)
            updated_metadata["item_uuid"] = str(merged_metadata.get("item_uuid", "")).strip() or str(uuid.uuid4())

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
                SET name = ?,
                    category = ?,
                    description = ?,
                    quantity = ?,
                    value_base_units = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    updated_name,
                    updated_category,
                    updated_description,
                    updated_quantity,
                    updated_value,
                    _encode_json_dict(updated_metadata),
                    row["id"],
                ),
            )
            _upsert_item_catalog_entry(
                connection,
                name=updated_name,
                category=str(updated_category),
                description=updated_description,
                value_base_units=updated_value,
                metadata=updated_metadata,
            )

        self.set_player_equipment(self.get_setting("player.equipment", {}))
        self.append_history("inventory", f"Modified inventory item: {clean_target}.")

    def add_crafting_item(
        self,
        *,
        name: str,
        category: str = "Material",
        description: str = "",
        location: str = "",
        uses: list[str] | None = None,
        notes: str = "",
    ) -> None:
        """
        Adds or updates a discovered useful crafting item/material.

        Args:
            name: Item/material name.
            description: Short player-facing description.
            location: Where the reagent is commonly found.
            uses: Known uses or experimentation hints.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to add crafting item/material with blank name.")
            return

        discovered_at = datetime.now().isoformat(timespec="seconds")
        clean_category = category.strip() or "Material"
        clean_description = description.strip() or notes.strip()
        clean_location = location.strip()
        clean_uses = uses or []

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO crafting_items (
                    name,
                    category,
                    description,
                    location,
                    uses_json,
                    discovered_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    category = excluded.category,
                    description = excluded.description,
                    location = excluded.location,
                    uses_json = excluded.uses_json
                """,
                (
                    clean_name,
                    clean_category,
                    clean_description,
                    clean_location,
                    _encode_string_list(clean_uses),
                    discovered_at,
                ),
            )
            _upsert_item_catalog_entry(
                connection,
                name=clean_name,
                category=clean_category,
                description=clean_description,
                value_base_units=0,
            )

        self.append_history("crafting", f"Discovered crafting item/material: {clean_name}.")

    def list_crafting_items(self) -> list[dict[str, Any]]:
        """
        Reads discovered useful crafting items/materials.

        Returns:
            List of crafting item/material dictionaries.
        """

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    name,
                    category,
                    description,
                    location,
                    uses_json,
                    discovered_at
                FROM crafting_items
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        reagents: list[dict[str, Any]] = []

        for row in rows:
            reagents.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "category": row["category"],
                    "description": row["description"],
                    "location": row["location"],
                    "uses": _decode_string_list(row["uses_json"], "uses"),
                    "discovered_at": row["discovered_at"],
                }
            )

        return reagents

    def add_crafting_recipe(
        self,
        *,
        name: str,
        ingredients: list[dict[str, Any]] | list[str],
        result: str,
        notes: str = "",
        value_base_units: int = 0,
    ) -> None:
        """
        Adds or updates a discovered crafting recipe.

        Args:
            name: Recipe name.
            ingredients: Known item/material ingredients.
            result: Recipe result.
            notes: Freeform notes.
        """

        clean_name = name.strip()

        if not clean_name:
            LOGGER.error("Attempted to add crafting recipe with blank name.")
            return

        discovered_at = datetime.now().isoformat(timespec="seconds")
        clean_ingredients = normalize_recipe_ingredients(ingredients)
        clean_value = max(0, _safe_int(value_base_units, default=0) or 0)

        with self._connect() as connection:
            catalog_rows = connection.execute(
                "SELECT name, metadata_json FROM item_catalog"
            ).fetchall()
            catalog_uuids = {
                str(row["name"]).casefold(): str(
                    _decode_json_dict(row["metadata_json"], "item catalog metadata").get("item_uuid", "")
                ).strip()
                for row in catalog_rows
            }
            for ingredient in clean_ingredients:
                item_uuid = catalog_uuids.get(
                    str(ingredient.get("reagent_name", "")).casefold(),
                    str(ingredient.get("item_uuid", "")),
                )
                if item_uuid:
                    ingredient["item_uuid"] = item_uuid
            connection.execute(
                """
                INSERT INTO crafting_recipes (
                    name,
                    ingredients_json,
                    result,
                    notes,
                    value_base_units,
                    discovered_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    ingredients_json = excluded.ingredients_json,
                    result = excluded.result,
                    notes = excluded.notes,
                    value_base_units = excluded.value_base_units
                """,
                (
                    clean_name,
                    json.dumps(clean_ingredients, ensure_ascii=False),
                    result.strip(),
                    notes.strip(),
                    clean_value,
                    discovered_at,
                ),
            )

        self.append_history("crafting", f"Discovered recipe: {clean_name}.")

    def list_crafting_recipes(self) -> list[dict[str, Any]]:
        """
        Reads discovered crafting recipes.

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
                    notes,
                    value_base_units,
                    discovered_at
                FROM crafting_recipes
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        recipes: list[dict[str, Any]] = []

        for row in rows:
            recipes.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "ingredients": normalize_recipe_ingredients(
                        _decode_json_list(row["ingredients_json"], "ingredients")
                    ),
                    "result": row["result"],
                    "notes": row["notes"],
                    "value_base_units": row["value_base_units"],
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
        requested_display_name = display_name.strip()
        clean_display_name = requested_display_name or _fallback_npc_display_name(
            clean_name,
            clean_role,
        )
        clean_public_description = public_description.strip()
        clean_player_facing_information = (
            player_facing_information.strip()
            or clean_public_description
            or clean_role
        )
        explicit_npc_id = npc_id.strip()
        clean_npc_id = explicit_npc_id or _npc_id_from_parts(
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
                matching_npc = self._find_matching_npc_for_upsert(
                    connection,
                    name=clean_name,
                    display_name=clean_display_name,
                    role=clean_role,
                    location=clean_location,
                )

                if matching_npc is not None:
                    clean_npc_id = str(matching_npc["npc_id"])
                    clean_name = str(matching_npc["name"]) or clean_name
                    existing = matching_npc

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
                        WHEN ? != '' THEN excluded.display_name
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
                    requested_display_name,
                ),
            )

        return self.get_npc(clean_npc_id)

    def _find_matching_npc_for_upsert(
        self,
        connection: sqlite3.Connection,
        *,
        name: str,
        display_name: str,
        role: str,
        location: str,
    ) -> sqlite3.Row | None:
        """Finds an existing NPC when Gemini changes its internal identifier."""

        clean_location = location.casefold()

        if not clean_location:
            return None

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
            WHERE lower(location) = lower(?)
            """,
            (location,),
        ).fetchall()

        scored_rows: list[tuple[int, sqlite3.Row]] = []

        for row in rows:
            score = _npc_match_score(
                name=name,
                display_name=display_name,
                role=role,
                location=location,
                existing_name=str(row["name"]),
                existing_display_name=str(row["display_name"]),
                existing_role=str(row["role"]),
                existing_location=str(row["location"]),
            )

            if score >= 8:
                scored_rows.append((score, row))

        if not scored_rows:
            return None

        scored_rows.sort(
            key=lambda item: (
                item[0],
                str(item[1]["updated_at"]),
                str(item[1]["npc_id"]),
            ),
            reverse=True,
        )

        if len(scored_rows) > 1 and scored_rows[0][0] == scored_rows[1][0]:
            LOGGER.info(
                "Skipped ambiguous NPC identity merge for name=%r role=%r location=%r.",
                name,
                role,
                location,
            )
            return None

        LOGGER.info(
            "Resolved NPC upsert %r at %r to existing npc_id %r.",
            name,
            location,
            scored_rows[0][1]["npc_id"],
        )
        return scored_rows[0][1]

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
        Reads one NPC by exact internal or display name.

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
                WHERE name = ? OR display_name = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (clean_name, clean_name),
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

        for npc in _coalesce_npc_profiles(self.list_npcs(limit=limit)):
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

        for npc in _coalesce_npc_profiles(self.list_npcs(limit=100)):
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

    def upsert_gm_secret(
        self,
        *,
        title: str,
        details: str,
        secret_id: str = "",
        reveal_condition: str = "",
        related_npc_ids: list[str] | None = None,
        related_locations: list[str] | None = None,
        status: str = "active",
    ) -> dict[str, Any] | None:
        """
        Creates or updates one AI-only secret-memory record.

        Args:
            title: Concise internal label for the hidden fact.
            details: Canonical GM-only truth the AI must remember.
            secret_id: Stable identifier used for later updates.
            reveal_condition: Circumstances under which the player could learn it.
            related_npc_ids: Stable NPC ids connected to the secret.
            related_locations: Locations connected to the secret.
            status: active, revealed, or retired.

        Returns:
            Stored secret dictionary, or None when required content is blank.
        """

        clean_title = title.strip()
        clean_details = details.strip()

        if not clean_title or not clean_details:
            LOGGER.warning("Skipped GM secret upsert without title and details.")
            return None

        clean_secret_id = _gm_secret_id(secret_id or clean_title)
        clean_status = status.strip().casefold() or "active"

        if clean_status not in GM_SECRET_STATUSES:
            LOGGER.warning(
                "Invalid GM secret status %r; defaulting to active.",
                status,
            )
            clean_status = "active"

        timestamp = datetime.now().isoformat(timespec="seconds")

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT
                    title,
                    details,
                    reveal_condition,
                    related_npc_ids_json,
                    related_locations_json,
                    created_at
                FROM gm_secrets
                WHERE secret_id = ?
                """,
                (clean_secret_id,),
            ).fetchone()
            created_at = timestamp if existing is None else str(existing["created_at"])
            merged_npc_ids = _merge_string_lists(
                (
                    []
                    if existing is None
                    else _decode_string_list(
                        existing["related_npc_ids_json"],
                        "GM secret related NPC ids",
                    )
                ),
                related_npc_ids or [],
            )
            merged_locations = _merge_string_lists(
                (
                    []
                    if existing is None
                    else _decode_string_list(
                        existing["related_locations_json"],
                        "GM secret related locations",
                    )
                ),
                related_locations or [],
            )

            connection.execute(
                """
                INSERT INTO gm_secrets (
                    secret_id,
                    title,
                    details,
                    reveal_condition,
                    related_npc_ids_json,
                    related_locations_json,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(secret_id) DO UPDATE SET
                    title = excluded.title,
                    details = excluded.details,
                    reveal_condition = excluded.reveal_condition,
                    related_npc_ids_json = excluded.related_npc_ids_json,
                    related_locations_json = excluded.related_locations_json,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_secret_id,
                    clean_title,
                    clean_details,
                    reveal_condition.strip(),
                    _encode_string_list(merged_npc_ids),
                    _encode_string_list(merged_locations),
                    clean_status,
                    created_at,
                    timestamp,
                ),
            )

        return self.get_gm_secret(clean_secret_id)

    def get_gm_secret(self, secret_id: str) -> dict[str, Any] | None:
        """Reads one AI-only secret-memory record by stable id."""

        clean_secret_id = _gm_secret_id(secret_id)

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    secret_id,
                    title,
                    details,
                    reveal_condition,
                    related_npc_ids_json,
                    related_locations_json,
                    status,
                    created_at,
                    updated_at
                FROM gm_secrets
                WHERE secret_id = ?
                """,
                (clean_secret_id,),
            ).fetchone()

        return None if row is None else _gm_secret_row_to_dict(row)

    def list_gm_secrets(
        self,
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Lists AI-only secret-memory records.

        Args:
            active_only: Excludes revealed and retired records when true.
        """

        where_clause = "WHERE status = 'active'" if active_only else ""

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    secret_id,
                    title,
                    details,
                    reveal_condition,
                    related_npc_ids_json,
                    related_locations_json,
                    status,
                    created_at,
                    updated_at
                FROM gm_secrets
                {where_clause}
                ORDER BY created_at ASC, secret_id COLLATE NOCASE
                """
            ).fetchall()

        return [_gm_secret_row_to_dict(row) for row in rows]

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
        due_elapsed_minutes: int | None = None,
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
            due_date: In-world due date label or N/A.
            due_elapsed_minutes: Absolute due minute, or -1 for no exact deadline.
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
        parsed_due_elapsed = (
            None
            if due_elapsed_minutes is None
            else _safe_int(due_elapsed_minutes, default=-1)
        )
        clean_due_elapsed = (
            -1
            if parsed_due_elapsed is None
            else max(-1, parsed_due_elapsed)
        )
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
                    due_elapsed_minutes,
                    notes,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    due_elapsed_minutes = CASE
                        WHEN excluded.due_elapsed_minutes >= 0 THEN excluded.due_elapsed_minutes
                        WHEN excluded.due_date != '' THEN excluded.due_elapsed_minutes
                        ELSE active_tasks.due_elapsed_minutes
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
                    clean_due_elapsed,
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
                    due_elapsed_minutes,
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

        return self._active_task_row_dict(dict(row))

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
                    due_elapsed_minutes,
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

        calendar_settings = self.get_calendar_settings()
        return [
            self._active_task_row_dict(dict(row), calendar_settings=calendar_settings)
            for row in rows
        ]

    def _active_task_row_dict(
        self,
        row: dict[str, Any],
        *,
        calendar_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Returns an active-task row with a display label for exact deadlines."""

        due_elapsed_minutes = _safe_int(row.get("due_elapsed_minutes"), default=-1)
        row["due_elapsed_minutes"] = due_elapsed_minutes

        if due_elapsed_minutes >= 0:
            row["due_date"] = build_calendar_snapshot(
                due_elapsed_minutes,
                calendar_settings or self.get_calendar_settings(),
            )["display_label"]

        return row

    def set_journal_notes(self, notes: str) -> None:
        """Stores the player's journal notes."""

        self.set_setting("journal.private_notes", str(notes))

    def get_journal_notes(self) -> str:
        """Reads the player's journal notes."""

        return str(self.get_setting("journal.private_notes", ""))

    def set_journal_share_with_ai(self, share_with_ai: bool) -> None:
        """Stores whether journal notes should be included in AI context."""

        self.set_setting("journal.share_with_ai", bool(share_with_ai))

    def get_journal_share_with_ai(self) -> bool:
        """Reads whether journal notes should be included in AI context."""

        return _safe_bool(self.get_setting("journal.share_with_ai", False), default=False)

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

    def get_travel_locations(self) -> list[dict[str, Any]]:
        """Reads structured player-known locations used by the Travel screen."""

        return [
            location.to_dict()
            for location in normalize_known_locations(
                self.get_setting("travel.locations", [])
            )
        ]

    def set_travel_locations(self, locations: Any) -> None:
        """Stores normalized structured player-known travel locations."""

        self.set_setting(
            "travel.locations",
            [location.to_dict() for location in normalize_known_locations(locations)],
        )

    def ensure_travel_locations(self) -> list[dict[str, Any]]:
        """Bootstraps travel data from the current scene when needed."""

        locations = normalize_known_locations(self.get_setting("travel.locations", []))
        indexes_by_name = {
            location.name.casefold(): index for index, location in enumerate(locations)
        }
        changed = False
        current_location = clean_player_location_name(
            self.get_state_value("location", "")
        )

        if current_location and current_location.casefold() not in indexes_by_name:
            indexes_by_name[current_location.casefold()] = len(locations)
            locations.append(
                KnownLocation(
                    name=current_location,
                    description="Current player location.",
                    x_miles=0.0 if not locations else None,
                    y_miles=0.0 if not locations else None,
                )
            )
            changed = True

        if changed:
            self.set_travel_locations([location.to_dict() for location in locations])

        return [location.to_dict() for location in locations]

    def find_travel_location(self, name: str) -> dict[str, Any] | None:
        """Finds one structured location by its player-visible name."""

        clean_name = clean_player_location_name(name)

        if not clean_name:
            return None

        for location in self.ensure_travel_locations():
            if str(location.get("name", "")).casefold() == clean_name.casefold():
                return location

        return None

    def upsert_travel_location(self, raw_location: Any) -> bool:
        """Adds or updates player-known location metadata without duplicating names."""

        incoming = normalize_known_location(raw_location)

        if incoming is None:
            return False

        locations = normalize_known_locations(self.get_setting("travel.locations", []))
        incoming_data = incoming.to_dict()
        raw_data = raw_location if isinstance(raw_location, dict) else {}

        for index, existing in enumerate(locations):
            if existing.name.casefold() != incoming.name.casefold():
                continue

            merged_data = existing.to_dict()
            merged_data["name"] = incoming.name

            for key in (
                "description",
                "x_miles",
                "y_miles",
                "terrain",
                "travel_multiplier",
                "travel_notes",
            ):
                aliases = {
                    "x_miles": ("x_miles", "x"),
                    "y_miles": ("y_miles", "y"),
                }.get(key, (key,))

                if any(alias in raw_data for alias in aliases):
                    merged_data[key] = incoming_data[key]

            locations[index] = normalize_known_location(merged_data) or existing
            self.set_travel_locations([location.to_dict() for location in locations])
            return True

        locations.append(incoming)
        self.set_travel_locations([location.to_dict() for location in locations])
        return True

    def get_travel_profile(self) -> dict[str, Any]:
        """Reads hidden movement values used for mathematically calculated travel."""

        return {
            "move_speed_mph": _bounded_float(
                self.get_setting("travel.move_speed_mph", DEFAULT_MOVE_SPEED_MPH),
                default=DEFAULT_MOVE_SPEED_MPH,
                minimum=0.1,
                maximum=100.0,
            ),
            "travel_mode": str(
                self.get_setting("travel.mode", DEFAULT_TRAVEL_MODE)
            ).strip()
            or DEFAULT_TRAVEL_MODE,
            "speed_multiplier": _bounded_float(
                self.get_setting(
                    "travel.speed_multiplier",
                    DEFAULT_TRAVEL_SPEED_MULTIPLIER,
                ),
                default=DEFAULT_TRAVEL_SPEED_MULTIPLIER,
                minimum=0.1,
                maximum=20.0,
            ),
        }

    def set_travel_mode(self, mode: str, speed_multiplier: Any) -> bool:
        """Updates the hidden current travel mode after a validated story event."""

        clean_mode = str(mode or "").strip()

        if not clean_mode:
            return False

        self.set_setting("travel.mode", clean_mode)
        self.set_setting(
            "travel.speed_multiplier",
            _bounded_float(
                speed_multiplier,
                default=DEFAULT_TRAVEL_SPEED_MULTIPLIER,
                minimum=0.1,
                maximum=20.0,
            ),
        )
        return True

    def _set_default_ai_mode_settings(self) -> None:
        """Stores the default save-specific AI behavior modes."""

        self._set_ai_mode_settings(default_ai_mode_settings())

    def _set_ai_mode_settings(self, raw_settings: dict[str, Any]) -> None:
        """Stores normalized save-specific AI behavior modes."""

        settings = normalize_ai_mode_preferences(raw_settings)
        self.set_setting(
            "ai.model_intelligence",
            settings["model_intelligence"],
        )
        self.set_setting("ai.model_tone", settings["model_tone"])
        self.set_setting("ai.response_length", settings["response_length"])
        self.set_setting(
            "ai.allowed_content_categories",
            settings["allowed_content_categories"],
        )

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

    def set_current_calendar_minute(self, current_minute: int) -> None:
        """Stores the current absolute in-world minute with calendar state."""

        self.set_setting(
            "calendar.current_minute",
            max(0, _safe_int(current_minute, default=DEFAULT_START_ELAPSED_MINUTES)),
        )

    def get_current_calendar_minute(self) -> int:
        """Reads the current absolute in-world minute for this save."""

        stored_minute = self.get_setting(
            "calendar.current_minute",
            DEFAULT_START_ELAPSED_MINUTES,
        )
        return max(0, _safe_int(stored_minute, default=DEFAULT_START_ELAPSED_MINUTES))

    def list_calendar_events(self) -> list[dict[str, Any]]:
        """Returns normalized persistent calendar events for this save."""

        raw_events = self.get_setting("calendar.events", [])
        if not isinstance(raw_events, list):
            return []

        events: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw_event in raw_events:
            event = _normalize_calendar_event(raw_event)
            event_id = event.get("event_id", "")
            if not event_id or event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            events.append(event)
        return sorted(
            events,
            key=lambda event: (
                int(event["month"]),
                int(event["day"]),
                str(event["title"]).casefold(),
            ),
        )

    def upsert_calendar_event(self, event: Any) -> dict[str, Any] | None:
        """Creates or updates one persistent calendar event."""

        clean_event = _normalize_calendar_event(event)
        if not clean_event.get("event_id") or not clean_event.get("title"):
            LOGGER.warning("Skipped invalid calendar event: %r", event)
            return None

        events = self.list_calendar_events()
        replaced = False
        for index, existing in enumerate(events):
            if existing["event_id"] == clean_event["event_id"]:
                events[index] = clean_event
                replaced = True
                break
        if not replaced:
            events.append(clean_event)
        self.set_setting("calendar.events", events)
        self.append_history("calendar", f"Saved calendar event: {clean_event['title']}.")
        return clean_event

    def delete_calendar_event(self, event_id: str) -> bool:
        """Deletes one persistent calendar event by stable identifier."""

        clean_id = str(event_id or "").strip()
        events = self.list_calendar_events()
        remaining = [event for event in events if event["event_id"] != clean_id]
        if len(remaining) == len(events):
            return False
        self.set_setting("calendar.events", remaining)
        self.append_history("calendar", f"Deleted calendar event: {clean_id}.")
        return True

    def get_player_equipment(self) -> dict[str, str]:
        """Reads current player equipment."""

        return normalize_equipment(
            self.get_setting("player.equipment", {}),
            self.list_inventory_items(),
        )

    def set_player_equipment(self, equipment: Any) -> dict[str, str]:
        """Stores current player equipment and returns the normalized result."""

        clean_equipment = normalize_equipment(equipment, self.list_inventory_items())
        self.set_setting("player.equipment", clean_equipment)
        self._sync_inventory_equipped_flags(clean_equipment)
        return clean_equipment

    def _sync_inventory_equipped_flags(self, equipment: dict[str, str]) -> None:
        """Keeps inventory flags aligned with the canonical equipment map."""

        equipped_names = {
            str(item_name).strip().casefold()
            for item_name in equipment.values()
            if str(item_name).strip()
        }

        with self._connect() as connection:
            connection.execute("UPDATE inventory_items SET equipped = 0")

            for item_name in equipped_names:
                connection.execute(
                    """
                    UPDATE inventory_items
                    SET equipped = 1
                    WHERE name = ? COLLATE NOCASE
                    """,
                    (item_name,),
                )

    def get_combat_state(self) -> dict[str, Any]:
        """Reads the saved deterministic combat state."""

        return normalize_combat_state(self.get_setting("combat.state", {}))

    def set_combat_state(self, combat_state: Any) -> dict[str, Any]:
        """Stores the deterministic combat state."""

        clean_state = normalize_combat_state(combat_state)
        self.set_setting("combat.state", clean_state)
        return clean_state

    def is_combat_active(self) -> bool:
        """Returns whether an unresolved deterministic combat is active."""

        return bool(self.get_combat_state().get("active", False))

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
                    equipped INTEGER NOT NULL DEFAULT 0,
                    description TEXT NOT NULL DEFAULT '',
                    value_base_units INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS item_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    category TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    value_base_units INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crafting_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL DEFAULT 'Material',
                    description TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    uses_json TEXT NOT NULL DEFAULT '[]',
                    discovered_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crafting_recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    ingredients_json TEXT NOT NULL DEFAULT '[]',
                    result TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    value_base_units INTEGER NOT NULL DEFAULT 0,
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
                    due_elapsed_minutes INTEGER NOT NULL DEFAULT -1,
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

                CREATE TABLE IF NOT EXISTS gm_secrets (
                    secret_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    details TEXT NOT NULL,
                    reveal_condition TEXT NOT NULL DEFAULT '',
                    related_npc_ids_json TEXT NOT NULL DEFAULT '[]',
                    related_locations_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
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

                DROP TABLE IF EXISTS alchemy_notes;
                DROP TABLE IF EXISTS alchemy_reagents;
                DROP TABLE IF EXISTS alchemy_recipes;
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
                "crafting_items",
                "category",
                "TEXT NOT NULL DEFAULT 'Material'",
            )
            _ensure_column(
                connection,
                "crafting_recipes",
                "value_base_units",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "inventory_items",
                "metadata_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            _ensure_column(
                connection,
                "inventory_items",
                "equipped",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "item_catalog",
                "metadata_json",
                "TEXT NOT NULL DEFAULT '{}'",
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
            _ensure_column(
                connection,
                "active_tasks",
                "due_elapsed_minutes",
                "INTEGER NOT NULL DEFAULT -1",
            )
            self._migrate_calendar_minute_from_game_state(connection)
            self._coalesce_inventory_stacks(connection)
            self._seed_item_catalog_from_inventory(connection)
            self._synchronize_item_identity_metadata(connection)

    def _migrate_calendar_minute_from_game_state(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Moves legacy current-time storage out of game_state."""

        legacy_row = connection.execute(
            "SELECT value FROM game_state WHERE key = ?",
            ("elapsed_minutes",),
        ).fetchone()

        if legacy_row is not None:
            existing_row = connection.execute(
                "SELECT value_json FROM settings WHERE key = ?",
                ("calendar.current_minute",),
            ).fetchone()
            if existing_row is None:
                current_minute = max(
                    0,
                    _safe_int(
                        legacy_row["value"],
                        default=DEFAULT_START_ELAPSED_MINUTES,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO settings (key, value_json)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                    """,
                    ("calendar.current_minute", json.dumps(current_minute)),
                )

        connection.execute(
            "DELETE FROM game_state WHERE key = ?",
            ("elapsed_minutes",),
        )

    def _coalesce_inventory_stacks(self, connection: sqlite3.Connection) -> None:
        """Merges duplicate inventory rows by case-insensitive item name."""

        rows = connection.execute(
            """
            SELECT
                id,
                name,
                category,
                quantity,
                description,
                value_base_units,
                metadata_json
            FROM inventory_items
            ORDER BY name COLLATE NOCASE, id ASC
            """
        ).fetchall()
        groups: dict[str, list[sqlite3.Row]] = {}

        for row in rows:
            groups.setdefault(str(row["name"]).casefold(), []).append(row)

        for matching_rows in groups.values():
            if len(matching_rows) <= 1:
                continue

            primary_row = matching_rows[0]
            duplicate_ids = [row["id"] for row in matching_rows[1:]]
            merged_quantity = sum(int(row["quantity"]) for row in matching_rows)
            merged_category = next(
                (
                    str(row["category"]).strip()
                    for row in matching_rows
                    if str(row["category"]).strip()
                ),
                "",
            )
            merged_description = next(
                (
                    str(row["description"]).strip()
                    for row in matching_rows
                    if str(row["description"]).strip()
                ),
                "",
            )
            merged_value = max(int(row["value_base_units"]) for row in matching_rows)
            merged_metadata = next(
                (
                    str(row["metadata_json"])
                    for row in matching_rows
                    if str(row["metadata_json"]).strip()
                    and str(row["metadata_json"]).strip() != "{}"
                ),
                "{}",
            )
            connection.execute(
                """
                UPDATE inventory_items
                SET category = ?,
                    quantity = ?,
                    description = ?,
                    value_base_units = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    merged_category,
                    merged_quantity,
                    merged_description,
                    merged_value,
                    merged_metadata,
                    primary_row["id"],
                ),
            )
            placeholders = ", ".join("?" for _ in duplicate_ids)
            connection.execute(
                f"DELETE FROM inventory_items WHERE id IN ({placeholders})",
                duplicate_ids,
            )

    def _seed_item_catalog_from_inventory(self, connection: sqlite3.Connection) -> None:
        """Ensures existing inventory rows are represented in the item catalog."""

        rows = connection.execute(
            """
            SELECT name, category, description, value_base_units, metadata_json
            FROM inventory_items
            ORDER BY id ASC
            """
        ).fetchall()

        for row in rows:
            _upsert_item_catalog_entry(
                connection,
                name=str(row["name"]),
                category=str(row["category"]),
                description=str(row["description"]),
                value_base_units=int(row["value_base_units"]),
                metadata=_decode_json_dict(row["metadata_json"], "inventory item metadata"),
            )

    def _synchronize_item_identity_metadata(self, connection: sqlite3.Connection) -> None:
        """Backfills one stable UUID across catalog and matching inventory rows."""

        catalog_rows = connection.execute(
            "SELECT id, name, metadata_json FROM item_catalog ORDER BY id ASC"
        ).fetchall()
        for row in catalog_rows:
            catalog_metadata = _decode_json_dict(row["metadata_json"], "item catalog metadata")
            item_uuid = str(catalog_metadata.get("item_uuid", "")).strip() or str(uuid.uuid4())
            catalog_metadata["item_uuid"] = item_uuid
            connection.execute(
                "UPDATE item_catalog SET metadata_json = ? WHERE id = ?",
                (_encode_json_dict(catalog_metadata), row["id"]),
            )
            inventory_rows = connection.execute(
                "SELECT id, metadata_json FROM inventory_items WHERE name = ? COLLATE NOCASE",
                (row["name"],),
            ).fetchall()
            for inventory_row in inventory_rows:
                inventory_metadata = _decode_json_dict(
                    inventory_row["metadata_json"], "inventory item metadata"
                )
                inventory_metadata["item_uuid"] = item_uuid
                connection.execute(
                    "UPDATE inventory_items SET metadata_json = ? WHERE id = ?",
                    (_encode_json_dict(inventory_metadata), inventory_row["id"]),
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


def _normalize_save_title(value: str) -> str:
    """Normalizes player-facing save titles for uniqueness checks."""

    return re.sub(r"\s+", " ", value.strip()).casefold()


def _unique_save_dir(saves_dir: Path, safe_title: str, timestamp: str) -> Path:
    """Returns a save directory path that does not collide on disk."""

    save_dir = saves_dir / f"{safe_title}_{timestamp}"

    if not save_dir.exists():
        return save_dir

    suffix = 2
    while True:
        candidate = saves_dir / f"{safe_title}_{timestamp}_{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


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


def _gm_secret_id(value: str) -> str:
    """Builds a stable internal id for AI-only secret memory."""

    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().casefold()).strip("_")
    return (cleaned or "unnamed_secret")[:100]


def _fallback_npc_display_name(name: str, role: str) -> str:
    """Chooses a player-visible fallback when the model did not provide one."""

    clean_name = name.strip()
    clean_role = role.strip()

    if clean_name and not _looks_like_internal_npc_name(clean_name):
        return clean_name

    return clean_role or clean_name or "Unknown NPC"


def _looks_like_internal_npc_name(value: str) -> bool:
    """Returns True for slug-like model identifiers such as copper_kettle_bartender."""

    clean_value = value.strip()

    if "_" not in clean_value:
        return False

    return bool(re.fullmatch(r"[a-z0-9_]+", clean_value))


def _coalesce_npc_profiles(npcs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapses obvious duplicate NPC rows for player-facing and AI context lists."""

    coalesced: list[dict[str, Any]] = []

    for npc in npcs:
        match_index = next(
            (
                index
                for index, existing in enumerate(coalesced)
                if _npc_match_score(
                    name=str(npc.get("name", "")),
                    display_name=str(npc.get("display_name", "")),
                    role=str(npc.get("role", "")),
                    location=str(npc.get("location", "")),
                    existing_name=str(existing.get("name", "")),
                    existing_display_name=str(existing.get("display_name", "")),
                    existing_role=str(existing.get("role", "")),
                    existing_location=str(existing.get("location", "")),
                )
                >= 8
            ),
            None,
        )

        if match_index is None:
            coalesced.append(dict(npc))
        else:
            coalesced[match_index] = _merge_npc_profiles(coalesced[match_index], npc)

    return coalesced


def _merge_npc_profiles(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    """Merges two likely duplicate NPC profiles without mutating the database."""

    primary, secondary = _preferred_npc_profile(first, second)
    merged = dict(primary)

    for key in ["name", "display_name", "role", "location", "disposition"]:
        if not str(merged.get(key, "")).strip() and str(secondary.get(key, "")).strip():
            merged[key] = secondary[key]

    for key in ["public_description", "player_facing_information"]:
        primary_text = str(merged.get(key, "")).strip()
        secondary_text = str(secondary.get(key, "")).strip()

        if len(secondary_text) > len(primary_text):
            merged[key] = secondary_text

    merged["knowledge_scope"] = _merge_string_lists(
        list(first.get("knowledge_scope", [])),
        list(second.get("knowledge_scope", [])),
    )
    merged["known_facts"] = _merge_string_lists(
        list(first.get("known_facts", [])),
        list(second.get("known_facts", [])),
    )
    merged["updated_at"] = max(
        str(first.get("updated_at", "")),
        str(second.get("updated_at", "")),
    )

    return merged


def _preferred_npc_profile(
    first: dict[str, Any],
    second: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Picks the profile whose identifier looks more stable."""

    first_id = str(first.get("npc_id", ""))
    second_id = str(second.get("npc_id", ""))

    if len(second_id) < len(first_id):
        return second, first

    return first, second


def _npc_match_score(
    *,
    name: str,
    display_name: str,
    role: str,
    location: str,
    existing_name: str,
    existing_display_name: str,
    existing_role: str,
    existing_location: str,
) -> int:
    """Scores whether two NPC descriptions appear to be the same person."""

    score = 0

    if location.strip().casefold() == existing_location.strip().casefold():
        score += 4

    if role.strip() and role.strip().casefold() == existing_role.strip().casefold():
        score += 4

    requested_aliases = _npc_aliases(name, display_name)
    existing_aliases = _npc_aliases(existing_name, existing_display_name)

    if requested_aliases and existing_aliases:
        if requested_aliases.intersection(existing_aliases):
            score += 6
        elif _tokens_for_match(" ".join(requested_aliases)).intersection(
            _tokens_for_match(" ".join(existing_aliases))
        ):
            score += 2

    requested_internal_names = _npc_aliases(name)
    existing_internal_names = _npc_aliases(existing_name)

    if _has_alias_containment(requested_internal_names, existing_internal_names):
        score += 5

    return score


def _npc_aliases(*values: str) -> set[str]:
    """Returns clean case-folded NPC aliases."""

    return {
        str(value).strip().casefold()
        for value in values
        if str(value).strip()
    }


def _has_alias_containment(
    requested_aliases: set[str],
    existing_aliases: set[str],
) -> bool:
    """Returns True when one NPC alias is an expanded form of another."""

    for requested_alias in requested_aliases:
        for existing_alias in existing_aliases:
            if (
                len(requested_alias) >= 4
                and len(existing_alias) >= 4
                and (
                    requested_alias in existing_alias
                    or existing_alias in requested_alias
                )
            ):
                return True

    return False


def _tokens_for_match(value: str) -> set[str]:
    """Returns stable-ish tokens useful for loose NPC identity matching."""

    return {
        token
        for token in re.split(r"[^a-zA-Z0-9']+", value.casefold())
        if len(token) >= 4
    }


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


def _safe_int(value: Any, *, default: int = -1) -> int:
    """Safely converts a value to int."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, *, default: bool) -> bool:
    """Safely converts a saved value to bool."""

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().casefold()

        if normalized in {"true", "1", "yes", "on"}:
            return True

        if normalized in {"false", "0", "no", "off", ""}:
            return False

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


def _upsert_item_catalog_entry(
    connection: sqlite3.Connection,
    *,
    name: str,
    category: str = "",
    description: str = "",
    value_base_units: int = 0,
    metadata: Any | None = None,
) -> None:
    """Adds or updates the durable item definition catalog."""

    clean_name = name.strip()

    if not clean_name:
        return

    clean_category = category.strip()
    clean_description = description.strip()
    clean_value = max(0, _safe_int(value_base_units, default=0) or 0)
    raw_metadata = metadata if isinstance(metadata, dict) else {}
    clean_metadata = normalize_item_metadata(
        metadata,
        name=clean_name,
        category=clean_category,
        description=clean_description,
    )
    # Keep a stable, AI-facing identity separate from the player-visible name.
    # Older saves are upgraded lazily the first time an item is touched/read.
    clean_metadata["item_uuid"] = str(raw_metadata.get("item_uuid", "")).strip() or str(uuid.uuid4())
    metadata_json = _encode_json_dict(clean_metadata)
    now = datetime.now().isoformat(timespec="seconds")
    row = connection.execute(
        """
        SELECT id, category, description, value_base_units, metadata_json, first_seen_at
        FROM item_catalog
        WHERE name = ? COLLATE NOCASE
        ORDER BY id ASC
        LIMIT 1
        """,
        (clean_name,),
    ).fetchone()

    if row is None:
        connection.execute(
            """
            INSERT INTO item_catalog (
                name,
                category,
                description,
                value_base_units,
                metadata_json,
                first_seen_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_name,
                clean_category,
                clean_description,
                clean_value,
                metadata_json,
                now,
                now,
            ),
        )
        return

    connection.execute(
        """
        UPDATE item_catalog
        SET name = ?,
            category = ?,
            description = ?,
            value_base_units = ?,
            metadata_json = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            clean_name,
            clean_category or str(row["category"]),
            clean_description or str(row["description"]),
            clean_value if clean_value > 0 else int(row["value_base_units"]),
            metadata_json
            if clean_metadata.get("item_type") != "Item"
            else str(row["metadata_json"]),
            now,
            row["id"],
        ),
    )


def _inventory_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Converts an inventory row to a plain dictionary."""

    metadata = _decode_json_dict(row["metadata_json"], "inventory item metadata")
    metadata["quantity_unit"] = _inventory_quantity_unit(metadata)
    metadata["storage_location"] = _inventory_storage_location(metadata)
    normalized_metadata = normalize_item_metadata(
        metadata,
        name=str(row["name"]),
        category=str(row["category"]),
        description=str(row["description"]),
    )
    normalized_metadata["quantity_unit"] = metadata["quantity_unit"]
    normalized_metadata["storage_location"] = metadata["storage_location"]
    normalized_metadata["item_uuid"] = str(metadata.get("item_uuid", "")).strip()
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "quantity": row["quantity"],
        "quantity_unit": metadata["quantity_unit"],
        "storage_location": metadata["storage_location"],
        "equipped": bool(row["equipped"]),
        "description": row["description"],
        "value_base_units": row["value_base_units"],
        "metadata": normalized_metadata,
    }


def _item_catalog_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Converts an item catalog row to a plain dictionary."""

    metadata = _decode_json_dict(row["metadata_json"], "item catalog metadata")
    metadata.setdefault("item_uuid", str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-adventure:item:{row['id']}")))
    normalized_metadata = normalize_item_metadata(
        metadata,
        name=str(row["name"]),
        category=str(row["category"]),
        description=str(row["description"]),
    )
    normalized_metadata["item_uuid"] = str(metadata["item_uuid"])
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "description": row["description"],
        "value_base_units": row["value_base_units"],
        "metadata": normalized_metadata,
        "first_seen_at": row["first_seen_at"],
        "updated_at": row["updated_at"],
    }


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


def _inventory_quantity_unit(metadata: dict[str, Any]) -> str:
    """Returns the persisted measurement unit for an inventory quantity."""

    value = str(metadata.get("quantity_unit", "each") or "each").strip()
    return value or "each"


def _normalize_calendar_event(raw_event: Any) -> dict[str, Any]:
    """Normalizes a one-time or yearly recurring calendar event."""

    if not isinstance(raw_event, dict):
        return {}
    title = str(raw_event.get("title", raw_event.get("name", "")) or "").strip()
    event_id = str(raw_event.get("event_id", raw_event.get("id", "")) or "").strip()
    if not event_id and title:
        event_id = re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_")
    recurrence = str(raw_event.get("recurrence", "none") or "none").strip().casefold()
    if recurrence not in {"none", "yearly"}:
        recurrence = "none"
    return {
        "event_id": event_id,
        "title": title,
        "description": str(raw_event.get("description", "") or "").strip(),
        "category": str(raw_event.get("category", "Event") or "Event").strip(),
        "month": max(1, _safe_int(raw_event.get("month", 1), default=1)),
        "day": max(1, _safe_int(raw_event.get("day", 1), default=1)),
        "duration_days": max(1, _safe_int(raw_event.get("duration_days", 1), default=1)),
        "recurrence": recurrence,
        "year": max(1, _safe_int(raw_event.get("year", 1), default=1)),
        "importance": str(raw_event.get("importance", "") or "").strip(),
        "details": str(raw_event.get("details", raw_event.get("notes", "")) or "").strip(),
    }


def _inventory_storage_location(metadata: dict[str, Any]) -> str:
    """Returns the persisted free-text inventory storage label."""

    value = " ".join(str(metadata.get("storage_location", "actively_carried") or "actively_carried").strip().split())
    return value[:120] or "actively_carried"


def _gm_secret_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Converts an AI-only secret-memory row to a plain dictionary."""

    return {
        "secret_id": row["secret_id"],
        "title": row["title"],
        "details": row["details"],
        "reveal_condition": row["reveal_condition"],
        "related_npc_ids": _decode_string_list(
            row["related_npc_ids_json"],
            "GM secret related NPC ids",
        ),
        "related_locations": _decode_string_list(
            row["related_locations_json"],
            "GM secret related locations",
        ),
        "status": row["status"],
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


def _encode_json_dict(value: Any) -> str:
    """Encodes a JSON dictionary."""

    return json.dumps(value if isinstance(value, dict) else {}, sort_keys=True)


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


def _decode_json_dict(raw_json: Any, label: str) -> dict[str, Any]:
    """Decodes a JSON dictionary."""

    try:
        value = json.loads(str(raw_json or "{}"))
    except json.JSONDecodeError:
        LOGGER.exception("Invalid JSON dictionary for %s.", label)
        return {}

    if not isinstance(value, dict):
        LOGGER.warning("%s JSON was not a dictionary.", label)
        return {}

    return value


def _decode_json_list(raw_json: Any, label: str) -> list[Any]:
    """Decodes a JSON list, preserving structured entries."""

    try:
        values = json.loads(str(raw_json))
    except json.JSONDecodeError:
        LOGGER.exception("Invalid JSON list for alchemy %s.", label)
        return []

    if not isinstance(values, list):
        LOGGER.warning("Alchemy %s JSON was not a list.", label)
        return []

    return values


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    """Parses a finite bounded float for persisted hidden travel settings."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default

    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        parsed = default

    return round(max(minimum, min(maximum, parsed)), 2)
