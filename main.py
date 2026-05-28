from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.logging_setup import configure_logging
from ai_adventure.ui.main_window import MainWindow


def main() -> int:
    """
    Application entry point.

    Returns:
        Process exit code.
    """

    app_paths = AppPaths.create()
    configure_logging(app_paths.log_file)

    logging.info("Starting AI Adventure application.")

    app = QApplication(sys.argv)
    window = MainWindow(app_paths=app_paths)
    window.show()

    exit_code = app.exec()
    logging.info("AI Adventure application exited with code %s.", exit_code)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
