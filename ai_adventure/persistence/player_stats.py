"""Transactional character progression and auditable narrative health changes."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from ai_adventure.stats import (ATTRIBUTES, MAX_PLAYER_LEVEL, PLAYER_XP_AWARDS,
                                attribute_modifier, carrying_capacity, health_max, normalize_attributes)


class PlayerStatsRepository:
    def event_receipt(self, message_id: str, event_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT result_json FROM event_receipts WHERE message_id = ? AND event_key = ?", (message_id, event_key)).fetchone()
        return json.loads(row[0]) if row else None

    def record_event_receipt(self, message_id: str, event_key: str, result: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO event_receipts (message_id, event_key, result_json) VALUES (?, ?, ?)",
                               (message_id, event_key, json.dumps(result)))

    def record_d20_test(self, **test: Any) -> dict[str, Any]:
        with self._connect() as connection:
            previous = connection.execute("SELECT * FROM d20_tests WHERE request_id = ?", (test["request_id"],)).fetchone()
            if previous is not None:
                return {**dict(previous), "rolls": json.loads(previous["rolls_json"])}
            keys = ("attribute", "test_kind", "attribute_modifier", "skill_bonus", "reason", "message_id", "request_id", "skill_name", "level", "bonus", "roll", "total", "dc", "outcome")
            connection.execute(
                f"INSERT INTO d20_tests ({', '.join(keys)}, rolls_json, created_at) VALUES ({', '.join('?' for _ in keys)}, ?, ?)",
                tuple(test[k] for k in keys) + (json.dumps(test["rolls"]), datetime.now().isoformat(timespec="seconds")),
            )
        return test

    def find_d20_test(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM d20_tests WHERE request_id = ?", (request_id,)).fetchone()
        return {**dict(row), "rolls": json.loads(row["rolls_json"])} if row else None

    def list_d20_tests(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM d20_tests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "rolls": json.loads(row["rolls_json"])} for row in rows]

    def initialize_player_stats(self, attributes: dict[str, int], level: int = 1) -> None:
        if type(level) is not int or not 1 <= level <= MAX_PLAYER_LEVEL:
            raise ValueError("Starting Player Level must be from 1 to 20.")
        attributes = normalize_attributes(attributes, maximum=18)
        with self.transaction():
            self.set_setting("player.attributes", attributes)
            self.set_setting("player.xp", 100 * (level - 1))
            self.set_setting("player.level", level)
            self.set_setting("player.reward_choices", 0)
            self.set_setting("player.skill_advances", 0)
            self.set_setting("player.health_current", health_max(attributes))

    def player_stats(self) -> dict[str, Any]:
        attributes = normalize_attributes(self.get_setting("player.attributes", {}))
        xp = max(0, int(self.get_setting("player.xp", 0)))
        level = min(MAX_PLAYER_LEVEL, 1 + xp // 100)
        maximum = health_max(attributes)
        return {"attributes": attributes, "modifiers": {a: attribute_modifier(v) for a, v in attributes.items()},
                "level": level, "xp": xp, "xp_to_next_level": max(0, level * 100 - xp) if level < MAX_PLAYER_LEVEL else 0,
                "reward_choices": int(self.get_setting("player.reward_choices", 0)),
                "skill_advances": int(self.get_setting("player.skill_advances", 0)),
                "health_current": max(0, min(maximum, int(self.get_setting("player.health_current", maximum)))),
                "health_max": maximum, "carrying_capacity_lb": carrying_capacity(attributes)}

    def _has_progression_record(self, kind: str, source_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM progression_records WHERE kind = ? AND source_id = ?", (kind, source_id)).fetchone() is not None

    def _progression_record(self, kind: str, source_id: str, details: dict[str, Any]) -> bool:
        if not source_id.strip():
            raise ValueError("A persistent source_id is required.")
        with self._connect() as connection:
            row = connection.execute(
                "INSERT OR IGNORE INTO progression_records (kind, source_id, details_json, created_at) VALUES (?, ?, ?, ?)",
                (kind, source_id, json.dumps(details), datetime.now().isoformat(timespec="seconds")),
            )
            return bool(row.rowcount)

    def list_progression_records(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM progression_records ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]

    def record_player_achievement(self, source_id: str, significance: str, reason: str, *, source_kind: str = "milestone") -> dict[str, Any]:
        if significance not in PLAYER_XP_AWARDS or not reason.strip() or source_kind not in {"objective", "milestone"}:
            raise ValueError("An objective/milestone, significance, and reason are required.")
        with self.transaction():
            if source_kind == "objective":
                task = next((t for t in self.list_active_tasks(include_completed=True) if str(t.get("id", "")) == source_id), None)
                if task is None or str(task.get("status", "")).casefold() not in {"completed", "complete"}:
                    raise ValueError("Objective XP requires that completed objective's persistent ID.")
            amount = PLAYER_XP_AWARDS[significance]
            if not self._progression_record("achievement", source_id, {"reason": reason, "significance": significance, "xp": amount, "source_kind": source_kind}):
                return {"status": "duplicate", "xp_awarded": 0}
            before = self.player_stats()
            xp = min(1900, before["xp"] + amount)
            level = min(MAX_PLAYER_LEVEL, 1 + xp // 100)
            self.set_setting("player.xp", xp)
            self.set_setting("player.level", level)
            self.set_setting("player.reward_choices", before["reward_choices"] + level - before["level"])
            return {"status": "applied", "xp_awarded": xp - before["xp"], "levels_gained": level - before["level"]}

    def spend_player_reward(self, kind: str, *, attribute: str = "", request_id: str | None = None) -> dict[str, Any]:
        request_id = request_id or uuid.uuid4().hex
        with self.transaction():
            if self._has_progression_record("reward", request_id):
                return {"status": "duplicate"}
            before = self.player_stats()
            if before["reward_choices"] <= 0:
                raise ValueError("No unused level-up choices.")
            if kind == "attribute":
                if attribute not in ATTRIBUTES or before["attributes"][attribute] >= 20:
                    raise ValueError("Select an attribute below 20.")
            elif kind != "skills":
                raise ValueError("Choose an attribute or two skill advances.")
            if not self._progression_record("reward", request_id, {"kind": kind, "attribute": attribute}):
                return {"status": "duplicate"}
            if kind == "attribute":
                attributes = dict(before["attributes"])
                attributes[attribute] += 1
                self.set_setting("player.attributes", attributes)
                current = before["health_current"] + health_max(attributes) - before["health_max"]
                self.set_setting("player.health_current", current)
                if current > 0 and self.get_state_value("condition", "") == "Incapacitated":
                    self.set_state_value("condition", "Recovering")
            else:
                self.set_setting("player.skill_advances", before["skill_advances"] + 2)
            self.set_setting("player.reward_choices", before["reward_choices"] - 1)
            return {"status": "applied", **self.player_stats()}

    def spend_skill_advance(self, name: str, description: str = "", *, request_id: str | None = None) -> dict[str, Any]:
        request_id = request_id or uuid.uuid4().hex
        with self.transaction():
            if self._has_progression_record("skill_advance", request_id):
                return {"status": "duplicate"}
            points = self.player_stats()["skill_advances"]
            skill = self.get_skill(name)
            if points <= 0 or not name.strip() or (skill and skill["level"] >= 5) or (not skill and not description.strip()):
                raise ValueError("Select a skill below level 5, or supply a new skill's name and scope.")
            if not self._progression_record("skill_advance", request_id, {"skill_name": name, "learned": skill is None, "previous_level": skill["level"] if skill else 0, "new_level": skill["level"] + 1 if skill else 1, "xp_offset": 8 if skill else 0, "previous_xp": skill["xp"] if skill else 0}):
                return {"status": "duplicate"}
            if skill is None:
                self.upsert_skill(name, description, 1)
            else:
                level = skill["level"] + 1
                xp = skill["xp"] + 8
                with self._connect() as connection:
                    connection.execute("UPDATE skills SET level = ?, bonus = ?, xp = ? WHERE id = ?", (level, level, xp, skill["id"]))
            self.set_setting("player.skill_advances", points - 1)
            return {"status": "applied", "skill": self.get_skill(name)}

    def change_player_health(self, delta: int, reason: str, source_id: str) -> dict[str, Any]:
        if type(delta) is not int or not reason.strip():
            raise ValueError("Health delta must be an integer with a reason.")
        with self.transaction():
            if not self._progression_record("health", source_id, {"delta": delta, "reason": reason}):
                return {"status": "duplicate"}
            stats = self.player_stats()
            current = max(0, min(stats["health_max"], stats["health_current"] + delta))
            self.set_setting("player.health_current", current)
            if current == 0:
                self.set_state_value("condition", "Incapacitated")
            elif self.get_state_value("condition", "") == "Incapacitated":
                self.set_state_value("condition", "Recovering")
            return {"status": "applied", "health_current": current, "health_max": stats["health_max"]}
