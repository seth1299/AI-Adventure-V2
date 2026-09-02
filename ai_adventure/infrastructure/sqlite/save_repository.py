"""Canonical infrastructure boundary for SQLite save persistence."""

from ai_adventure.persistence.save_repository import (
    DuplicateSaveTitleError,
    SaveFileOperationError,
    SaveRepository,
    SaveSummary,
)

__all__ = [
    "DuplicateSaveTitleError",
    "SaveFileOperationError",
    "SaveRepository",
    "SaveSummary",
]
