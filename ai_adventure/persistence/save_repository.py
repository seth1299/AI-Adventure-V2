from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


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
    def create_new_save(cls, saves_dir: Path, title: str) -> "SaveRepository":
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
        repository.set_state_value("location", "Starting Room")
        repository.set_state_value("time", "Day 1, Morning")
        repository.set_state_value("weather", "Clear")
        repository.set_state_value("condition", "Healthy")
        repository.append_history("system", "New adventure created.")

        LOGGER.info("Created new save at %s.", db_path)

        return repository

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

    def add_inventory_item(
        self,
        name: str,
        category: str,
        quantity: int,
        description: str,
    ) -> None:
        """
        Adds an inventory item.

        Args:
            name: Item name.
            category: Item category.
            quantity: Quantity to add.
            description: Short item description.
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

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO inventory_items (name, category, quantity, description)
                VALUES (?, ?, ?, ?)
                """,
                (clean_name, category.strip(), quantity, description.strip()),
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
                SELECT id, name, category, quantity, description
                FROM inventory_items
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()

        return [dict(row) for row in rows]

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

    def _connect(self) -> sqlite3.Connection:
        """
        Opens a SQLite connection.

        Returns:
            SQLite connection configured with row dictionaries.
        """

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

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
                    description TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS alchemy_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS history_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
                """
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