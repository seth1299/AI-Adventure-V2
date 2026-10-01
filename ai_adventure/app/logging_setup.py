from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4


def _human_timestamp(created: float) -> str:
    """Format a log record timestamp in the compact style used by the log file."""

    local_time = datetime.fromtimestamp(created)
    meridiem = "A.M." if local_time.hour < 12 else "P.M."
    hour = local_time.hour % 12 or 12
    return f"{local_time.month}-{local_time.day}-{local_time:%y}, {hour}:{local_time:%M} {meridiem}"


class HumanReadableJsonFormatter(logging.Formatter):
    """Serialize a record into the human-readable fields stored in the JSON log."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "message_id": uuid4().hex,
            "timestamp": _human_timestamp(record.created),
            "file_location": record.name,
            "log_type": record.levelname,
            "log_message": record.getMessage(),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


class JsonFileHandler(logging.Handler):
    """Write all records to a single, valid, human-readable JSON document."""

    def __init__(self, log_file: Path) -> None:
        super().__init__()
        self.log_file = log_file
        self._records: list[dict[str, object]] = []
        self._write_lock = RLock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            entry = json.loads(formatted)
            with self._write_lock:
                self._records.append(entry)
                self._write_document()
        except Exception:
            self.handleError(record)

    def _write_document(self) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.write_text(
            json.dumps({"messages": self._records}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def close(self) -> None:
        with self._write_lock:
            if self._records:
                self._write_document()
        super().close()


def configure_logging(log_file: Path) -> None:
    """
    Configures application-wide logging.

    Args:
        log_file: File path where logs should be written.
    """

    if log_file.parent is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Avoid duplicate handlers when restarting from an interactive environment.
    for existing_handler in root_logger.handlers[:]:
        root_logger.removeHandler(existing_handler)
        if isinstance(existing_handler, JsonFileHandler):
            existing_handler.close()

    file_handler = JsonFileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)

    formatter = HumanReadableJsonFormatter()

    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    logging.info("Logging configured. Log file: %s", log_file)
