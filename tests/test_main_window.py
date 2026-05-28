from __future__ import annotations

import logging
import os
import tempfile
import unittest
import importlib.util
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if importlib.util.find_spec("PySide6") is None:
    raise unittest.SkipTest("PySide6 is not installed in this test environment.")

from PySide6.QtWidgets import QApplication

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.ui.main_window import MainWindow


class MainWindowTests(unittest.TestCase):
    def test_startup_without_loaded_save_does_not_log_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            app_paths = AppPaths(
                app_data_dir=temp_path,
                saves_dir=temp_path / "saves",
                logs_dir=temp_path / "logs",
                log_file=temp_path / "logs" / "ai_adventure.log",
            )
            app_paths.saves_dir.mkdir(parents=True, exist_ok=True)
            app_paths.logs_dir.mkdir(parents=True, exist_ok=True)

            QApplication.instance() or QApplication([])

            logger = logging.getLogger("ai_adventure.ui.main_window")

            with self.assertNoLogs(logger, level="ERROR"):
                window = MainWindow(app_paths=app_paths)
                window.return_to_menu()
                window.close()


if __name__ == "__main__":
    unittest.main()
