from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    """
    Centralized application paths.

    Args:
        app_data_dir: Root directory for saves, logs, and user settings.
        saves_dir: Directory containing save folders.
        logs_dir: Directory containing log files.
        log_file: Main application log file.
    """

    app_data_dir: Path
    saves_dir: Path
    logs_dir: Path
    log_file: Path

    @classmethod
    def create(cls) -> "AppPaths":
        """
        Creates platform-appropriate application paths.

        Returns:
            AppPaths with all required directories created.
        """

        app_data_env = os.getenv("APPDATA")

        if app_data_env is not None and app_data_env.strip():
            app_data_dir = Path(app_data_env) / "AI Adventure"
        else:
            app_data_dir = Path.home() / ".ai_adventure"

        saves_dir = app_data_dir / "saves"
        logs_dir = app_data_dir / "logs"
        log_file = logs_dir / "ai_adventure.log"

        saves_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            app_data_dir=app_data_dir,
            saves_dir=saves_dir,
            logs_dir=logs_dir,
            log_file=log_file,
        )