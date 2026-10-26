"""Durable survival death choices, recovery, and protection of spectator saves."""

from __future__ import annotations

import uuid
from typing import Any, TYPE_CHECKING, ContextManager

from ai_adventure.skills.rules import MAX_SKILL_LEVEL, XP_THRESHOLDS_BY_LEVEL
from ai_adventure.locations import clean_player_location_name
from ai_adventure.survival import SURVIVAL_METERS, survival_percentage, survival_roll_modifier

if TYPE_CHECKING:
    import sqlite3


class AdventureNotPlayableError(RuntimeError):
    """The player must resolve death, finish recovery, or keep spectating."""


class SurvivalRepository:
    """Mixin for SaveRepository; all fate transitions use its durable connection."""

    if TYPE_CHECKING:
        def transaction(self) -> ContextManager[sqlite3.Connection]: ...
        def get_setting(self, key: str, default: Any = None) -> Any: ...
        def set_setting(self, key: str, value: Any) -> None: ...
        def get_state_value(self, key: str, default: str = "") -> str: ...
        def set_state_value(self, key: str, value: str) -> None: ...
        def append_history(self, kind: str, content: str, **kwargs: Any) -> Any: ...
        def ensure_travel_locations(self) -> list[dict[str, Any]]: ...
        def get_player_id(self) -> str: ...
        def event_receipt(self, message_id: str, event_key: str) -> dict[str, Any] | None: ...
        def record_event_receipt(self, message_id: str, event_key: str, result: dict[str, Any]) -> None: ...
        def list_skills(self) -> list[dict[str, Any]]: ...
        def player_stats(self) -> dict[str, Any]: ...
        def upsert_travel_location(self, raw_location: Any) -> bool: ...
        def append_mechanical_event(self, event_type: str, payload: dict[str, Any], status: str, message: str, **kwargs: Any) -> Any: ...

    def survival_status(self) -> dict[str, Any]:
        enabled = self.get_setting("survival.enabled", False) is True
        values = {meter: survival_percentage(self.get_state_value(f"player.{meter}", "100"))
                  for meter in SURVIVAL_METERS} if enabled else {}
        fate = str(self.get_setting("survival.fate", "alive"))
        if enabled and fate == "alive" and 0 in values.values():
            fate = "pending"
        return {"enabled": enabled, **values, "fate": fate,
                "roll_modifier": survival_roll_modifier(values, enabled=enabled)}

    def require_active_adventure(self) -> None:
        if self.survival_status()["fate"] != "alive":
            raise AdventureNotPlayableError("This adventure is paused after death or is read-only.")

    def mark_survival_death(self) -> dict[str, Any] | None:
        with self.transaction():
            status = self.survival_status()
            if not status["enabled"] or not any(status[meter] == 0 for meter in SURVIVAL_METERS):
                return None
            if self.get_setting("survival.fate", "alive") != "alive":
                return self.get_setting("survival.death", {})
            death = {"incident_id": uuid.uuid4().hex, "location": self.get_state_value("location"),
                     "meters": {meter: status[meter] for meter in SURVIVAL_METERS}}
            self.set_setting("survival.death", death)
            self.set_setting("survival.fate", "pending")
            self.set_state_value("condition", "Dead")
            return death

    def choose_survival_fate(self, choice: str) -> dict[str, Any]:
        if choice not in {"accepted", "recovering"}:
            raise ValueError("Select one of the two death choices.")
        with self.transaction():
            self.mark_survival_death()
            if self.survival_status()["fate"] != "pending":
                raise AdventureNotPlayableError("This death already has a fate decision.")
            death = self.get_setting("survival.death", {})
            self.append_history("system", "You accepted your fate. This adventure is now read-only."
                                if choice == "accepted" else "You chose: It is not my time yet.")
            if choice == "accepted":
                # Finish read-time normalization before freezing gameplay tables.
                self.ensure_travel_locations()
                self.get_player_id()
                self._install_spectator_guards()
            self.set_setting("survival.fate", choice)
            return death

    def commit_survival_recovery(self, incident_id: str, recovery: dict[str, str]) -> bool:
        with self.transaction() as connection:
            if self.event_receipt(incident_id, "survival_recovery"):
                return False
            death = self.get_setting("survival.death", {})
            if (self.survival_status()["fate"] != "recovering"
                    or not incident_id or death.get("incident_id") != incident_id):
                raise AdventureNotPlayableError("No matching survival recovery is pending.")
            location = clean_player_location_name(str(recovery.get("location", "")))
            narrative = str(recovery.get("response", "")).strip()
            if (not location or not narrative or location.casefold() == str(death["location"]).strip().casefold()
                    or location.upper() in {"AUTO", "SAME", "SKIP"}):
                raise ValueError("Recovery must describe waking alive at a different location.")
            penalties = []
            for skill in self.list_skills():
                level = int(skill["level"])
                if level >= MAX_SKILL_LEVEL:
                    continue
                floor = XP_THRESHOLDS_BY_LEVEL[level]
                progress = max(0, int(skill["xp"]) - floor)
                xp = floor + progress // 2
                connection.execute("UPDATE skills SET xp = ? WHERE id = ?", (xp, skill["id"]))
                penalties.append({"skill_id": skill["id"], "level": level,
                                  "previous_xp": skill["xp"], "xp": xp})
            for meter in SURVIVAL_METERS:
                self.set_state_value(f"player.{meter}", "20")
            self.set_state_value("location", location)
            self.set_state_value("weather", str(recovery.get("weather", "")).strip() or "Clear")
            self.set_state_value("condition", "Recovering")
            self.upsert_travel_location({"name": location, "location_scope": "specific",
                                        "description": str(recovery.get("location_description", ""))})
            if self.player_stats()["health_current"] == 0:
                self.set_setting("player.health_current", 1)
            self.append_history("story", narrative, message_id=f"recovery:{incident_id}")
            self.append_mechanical_event("SurvivalRecoveredEvent", {"incident_id": incident_id,
                "location": location, "meters": dict.fromkeys(SURVIVAL_METERS, 20),
                "skill_xp": penalties}, "applied", "Survived; halved in-progress skill XP.")
            self.record_event_receipt(incident_id, "survival_recovery", {"location": location})
            # Preserve old snapshots for inspection, but keep them from undoing
            # the player's recovery choice and its XP penalty.
            snapshots = [row[0] for row in connection.execute("SELECT message_id FROM message_snapshots")]
            protected = self.get_setting("survival.protected_snapshots", [])
            self.set_setting("survival.protected_snapshots", sorted(set(protected) | set(snapshots)))
            self.set_setting("survival.fate", "alive")
            return True

    def _install_spectator_guards(self) -> None:
        """Freeze gameplay even for stale workers or an attempted snapshot restore.

        Media cache and presentation/audio/image preferences remain writable.
        Triggers live in the save, so the protection survives reopening it.
        """
        condition = "EXISTS (SELECT 1 FROM settings WHERE key='survival.fate' AND value_json='\"accepted\"')"
        with self.transaction() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                if table in {"meta", "visual_assets", "message_visual_assets"}:
                    continue
                quoted = '"' + table.replace('"', '""') + '"'
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    predicate = condition
                    if table == "settings":
                        def protected(row: str) -> str:
                            return (f"({row}.key NOT LIKE 'ui.%' AND {row}.key NOT LIKE 'audio.%' "
                                    f"AND {row}.key NOT LIKE 'images.%' AND {row}.key != 'theme')")
                        checks = [protected("OLD")] if operation == "DELETE" else [protected("NEW")]
                        if operation == "UPDATE":
                            checks.append(protected("OLD"))
                        predicate += " AND (" + " OR ".join(checks) + ")"
                    trigger = f'"spectator_{table}_{operation.lower()}"'
                    connection.execute(f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {operation} ON {quoted} "
                        f"WHEN {predicate} BEGIN SELECT RAISE(ABORT, 'This adventure is read-only after death.'); END")
