"""Durable, save-local requests for an opening that has not been delivered yet."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def opening_request_path(db_path: Path) -> Path:
    return Path(db_path).with_suffix(".new_game_prompt.txt")


class OpeningRequest:
    """Keep exact phase prompts and approved replies, never credentials."""

    def __init__(self, path: Path, setup_packet: dict[str, Any] | None = None):
        self.path = Path(path)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if (self.data.get("version") != 1
                    or not isinstance(self.data.get("setup_packet"), dict)
                    or not isinstance(self.data.get("phases"), dict)):
                raise ValueError("The saved opening request has an unsupported format.")
        else:
            if setup_packet is None:
                raise ValueError("No saved opening request was found.")
            self.data = {"version": 1, "setup_packet": setup_packet, "phases": {}}
            self.save()

    @property
    def setup_packet(self) -> dict[str, Any]:
        return self.data["setup_packet"]

    def save(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)

    def prepare_phase(self, phase: str, **request: Any) -> dict[str, Any]:
        if phase not in self.data["phases"]:
            self.data["phases"][phase] = request
            self.save()
        return self.data["phases"][phase]

    def approve_phase(self, phase: str, response: str) -> None:
        self.data["phases"][phase]["response"] = response
        self.save()

    def approve_starting_inventory(self, items: list[dict[str, Any]]) -> None:
        """Persist player edits and invalidate prose written for different possessions."""
        if self.data.get("starting_inventory") != items:
            self.data["starting_inventory"] = items
            self.data["phases"].pop("opening_prose", None)
            self.save()
