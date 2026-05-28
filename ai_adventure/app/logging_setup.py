from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_file: Path) -> None:
    """
    Configures application-wide logging.

    Args:
        log_file: File path where logs should be written.
    """

    if log_file.parent is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers when restarting from an interactive environment.
    root_logger.handlers.clear()

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    logging.info("Logging configured. Log file: %s", log_file)
