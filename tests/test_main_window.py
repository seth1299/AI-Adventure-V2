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
                tab_names = [
                    window.game_shell.tabs.tabText(index)
                    for index in range(window.game_shell.tabs.count())
                ]
                self.assertIn("Character", tab_names)
                self.assertIn("World", tab_names)
                self.assertIn("Active Tasks", tab_names)
                npc_headers = [
                    window.game_shell.npcs_screen.table.horizontalHeaderItem(index).text()
                    for index in range(window.game_shell.npcs_screen.table.columnCount())
                ]
                task_headers = [
                    window.game_shell.active_tasks_screen.table.horizontalHeaderItem(index).text()
                    for index in range(window.game_shell.active_tasks_screen.table.columnCount())
                ]
                alchemy_tabs = [
                    window.game_shell.alchemy_screen.tabs.tabText(index)
                    for index in range(window.game_shell.alchemy_screen.tabs.count())
                ]
                self.assertEqual(npc_headers, ["Name", "Location", "Notes"])

                sortable_tables = [
                    window.game_shell.inventory_screen.table,
                    window.game_shell.npcs_screen.table,
                    window.game_shell.active_tasks_screen.table,
                    window.game_shell.skills_screen.skills_table,
                ]

                for table in sortable_tables:
                    self.assertFalse(table.isSortingEnabled())
                    self.assertTrue(table.horizontalHeader().sectionsClickable())
                    self.assertTrue(table.horizontalHeader().isSortIndicatorShown())

                self.assertFalse(window.game_shell.calendar_screen.table.isSortingEnabled())
                self.assertEqual(
                    task_headers,
                    [
                        "Task",
                        "Type",
                        "Status",
                        "Details",
                        "Contact",
                        "Location",
                        "Reward",
                        "Due",
                    ],
                )
                self.assertEqual(alchemy_tabs, ["Reagents", "Recipes"])
                window.close()


if __name__ == "__main__":
    unittest.main()
