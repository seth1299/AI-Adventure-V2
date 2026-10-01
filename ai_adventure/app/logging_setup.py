from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ai_adventure.app.features import is_playtesting_build


_initialized_log_files: set[Path] = set()


class HumanReadableLogFormatter(logging.Formatter):
    """Format one record as labeled lines, followed by one blank line."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds")
        lines = [
            f"Severity: {record.levelname}",
            f"File: {record.name} ({record.filename}:{record.lineno})",
            f"Function: {record.funcName}",
            f"Date/Time: {timestamp}",
            f"Message ID: {uuid4().hex}",
            f"Message: {record.getMessage().rstrip()}",
        ]
        if record.exc_info:
            lines.append("Exception:\n" + self.formatException(record.exc_info))
        if record.stack_info:
            lines.append("Stack:\n" + self.formatStack(record.stack_info))
        return "\n".join(lines).rstrip() + "\n"


class ApplicationFileHandler(logging.FileHandler):
    """Append records with standard logging locking and per-record flushing."""

    def _open(self):
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()


def configure_logging(log_file: Path) -> None:
    """Reset the log once per process/path, then append throughout the run.

    Reconfiguration replaces our handler without erasing the session.
    DEBUG records are written only by the playtesting build.
    """
    log_file = Path(log_file).resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    level = logging.DEBUG if is_playtesting_build() else logging.INFO
    root_logger.setLevel(level)
    for handler in root_logger.handlers[:]:
        if isinstance(handler, ApplicationFileHandler):
            root_logger.removeHandler(handler)
            handler.close()

    if log_file not in _initialized_log_files:
        with log_file.open("w", encoding="utf-8"):
            pass
        _initialized_log_files.add(log_file)

    handler = ApplicationFileHandler(log_file, mode="a", encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(HumanReadableLogFormatter())
    root_logger.addHandler(handler)
    logging.info("Logging configured. Log file: %s", log_file)
