"""Application service for save-file lifecycle operations."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from ai_adventure.infrastructure.sqlite import SaveRepository, SaveSummary


class SaveGameService:
    """Keeps save-file operations out of window and screen classes."""

    def __init__(self, saves_dir: Path) -> None:
        self.saves_dir = Path(saves_dir)

    def create(self, title: str, setup: dict[str, Any] | None = None) -> SaveRepository:
        return SaveRepository.create_new_save(self.saves_dir, title, setup)

    def load(self, db_path: Path) -> SaveRepository:
        return SaveRepository(db_path)

    def list(self) -> list[SaveSummary]:
        return SaveRepository.list_saves(self.saves_dir)

    def rename(self, db_path: Path, new_title: str) -> Path:
        return SaveRepository.rename_save(self.saves_dir, db_path, new_title)

    def delete(self, db_path: Path) -> None:
        SaveRepository.delete_save(self.saves_dir, db_path)

    def latest_theme(self, default: str = "Light") -> str:
        for summary in self.list():
            try:
                value = SaveRepository.read_save_setting(summary.db_path, "theme", default)
            except Exception:
                continue
            clean_value = str(value or default).strip()
            return clean_value or default
        return default

    def next_available_title(self, requested_title: str) -> str:
        base_title = str(requested_title or "").strip() or "New Adventure"
        if not SaveRepository.save_title_exists(self.saves_dir, base_title):
            return base_title
        match = re.match(r"^(?P<base>.*?)(?:\s+(?P<suffix>\d+))?$", base_title)
        base = str(match.group("base") or "").strip() if match else base_title
        suffix = (
            max(2, int(match.group("suffix")) + 1)
            if match and match.group("suffix")
            else 2
        )
        while SaveRepository.save_title_exists(self.saves_dir, f"{base} {suffix}"):
            suffix += 1
        return f"{base} {suffix}"
