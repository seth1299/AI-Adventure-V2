"""Storage for timed processes; compatible with the repository's transactions."""
from datetime import datetime
from uuid import uuid4


class TimedEventsRepository:
    def upsert_timed_event(self, *, name: str = "", location: str = "", event_id: str = "", **changes):
        allowed = {"category", "quantity", "quantity_unit", "current_state", "next_state",
                   "ready_at_minute", "notes", "lifecycle", "is_estimate", "quantity_is_estimate"}
        if set(changes) - allowed:
            raise ValueError("Unsupported timed-event fields.")
        place = self.find_travel_location(location) if location else None
        if location and place is None:
            raise ValueError("Timed events require a known related location.")
        with self._connect() as connection:
            if event_id:
                existing = connection.execute("SELECT * FROM timed_events WHERE id=?", (event_id,)).fetchone()
                if existing is None:
                    raise ValueError("Unknown timed event_id; omit it when creating a record.")
            else:
                existing = connection.execute(
                    "SELECT * FROM timed_events WHERE name=? COLLATE NOCASE AND location_id=?",
                    (name.strip(), place["location_id"] if place else ""),
                ).fetchone()
            row = dict(existing) if existing else {
                "id": "timed_" + uuid4().hex, "name": "", "location": "", "location_id": "", "category": "Process",
                "quantity": -1, "quantity_unit": "", "quantity_is_estimate": 0,
                "current_state": "In progress", "next_state": "Ready",
                "ready_at_minute": -1, "notes": "", "lifecycle": "active", "is_estimate": 1,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            row.update(changes)
            row["name"] = name.strip() or row["name"]
            row["location"] = location.strip() or row["location"]
            if place:
                row["location"] = place["name"]
                row["location_id"] = place["location_id"]
            if not row["name"] or not row["location"]:
                raise ValueError("Timed events require a name and a related location.")
            for field in ("quantity", "ready_at_minute"):
                value = row[field]
                if isinstance(value, bool) or not isinstance(value, int) or value < -1:
                    raise ValueError(f"{field} must be an integer of -1 or greater.")
            if row["lifecycle"] not in {"active", "completed", "cancelled"}:
                raise ValueError("Invalid timed-event lifecycle.")
            for field in ("category", "quantity_unit", "current_state", "next_state", "notes"):
                if not isinstance(row[field], str):
                    raise ValueError(f"{field} must be text.")
                row[field] = row[field].strip()
            if not row["current_state"] or not row["next_state"]:
                raise ValueError("Timed events require current_state and next_state.")
            for field in ("is_estimate", "quantity_is_estimate"):
                if not isinstance(row[field], (bool, int)) or row[field] not in (0, 1):
                    raise ValueError(f"{field} must be a boolean.")
                row[field] = int(row[field])
            row["updated_at"] = datetime.now().isoformat(timespec="seconds")
            fields = list(row)
            connection.execute(
                "INSERT INTO timed_events (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields)
                + ") ON CONFLICT(id) DO UPDATE SET " + ",".join(field + "=excluded." + field for field in fields if field != "id"),
                [row[field] for field in fields],
            )
        return self._timed_event_view(row)

    def _timed_event_view(self, row, current=None, locations=None):
        row = dict(row)
        due = row["ready_at_minute"]
        if current is None:
            current = self.get_current_calendar_minute()
        if locations is None:
            locations = self.get_travel_locations()
        place = next((place for place in locations
                      if place["location_id"] == row["location_id"]), None)
        if place:
            row["location"] = place["name"]
        row["remaining_minutes"] = max(0, due - current) if due >= 0 else None
        row["state"] = (row["next_state"] if row["lifecycle"] == "active" and due >= 0 and current >= due
                        else row["current_state"])
        row["is_estimate"] = bool(row["is_estimate"])
        row["quantity_is_estimate"] = bool(row["quantity_is_estimate"])
        return row

    def list_timed_events(self, *, include_inactive: bool = False):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM timed_events"
                                      + ("" if include_inactive else " WHERE lifecycle='active'")
                                      + " ORDER BY location COLLATE NOCASE, name COLLATE NOCASE").fetchall()
        current = self.get_current_calendar_minute()
        locations = self.get_travel_locations()
        return [self._timed_event_view(row, current, locations) for row in rows]
