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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.main_window import (
    AlchemyNotebookScreen,
    MainWindow,
    NewGameWizard,
    StoryScreen,
)


class FakeNarrationPlayer:
    def __init__(self) -> None:
        self.on_chunk_start = None
        self.on_complete = None

    def narrate(self, text, *, on_chunk_start=None, on_complete=None):
        self.on_chunk_start = on_chunk_start
        self.on_complete = on_complete
        return True

    def play_chunk(self, text: str) -> None:
        self.on_chunk_start(text)

    def complete(self) -> None:
        self.on_complete()


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

    def test_story_screen_reveals_latest_story_by_narration_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Reveal Test")
            story_text = "First sentence. Second sentence.\n\n- Take action."
            repository.append_history("story", story_text)
            latest_story = repository.list_history()[-1]
            narration_player = FakeNarrationPlayer()
            screen = StoryScreen(narration_player=narration_player)
            screen.set_repository(repository)

            started = screen._reveal_story_with_narration(
                int(latest_story["id"]),
                story_text,
            )

            self.assertTrue(started)
            self.assertNotIn("First sentence.", screen.story_output.toPlainText())

            narration_player.play_chunk("First sentence.")
            QApplication.processEvents()

            self.assertIn("First sentence.", screen.story_output.toPlainText())
            self.assertNotIn("Second sentence.", screen.story_output.toPlainText())

            narration_player.play_chunk("Second sentence.")
            QApplication.processEvents()

            self.assertIn("Second sentence.", screen.story_output.toPlainText())
            self.assertNotIn("- Take action.", screen.story_output.toPlainText())

            narration_player.complete()
            QApplication.processEvents()

            self.assertIn("- Take action.", screen.story_output.toPlainText())
            screen.close()

    def test_alchemy_reagent_selection_populates_form_without_table_editing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alchemy UI Test")
            repository.add_alchemy_reagent(
                name="Moon Salt",
                qualities=["cold", "silver"],
                motions=["settling"],
                virtues=["clarity"],
                uses=["cooling draughts"],
                notes="Crystals hum softly.",
            )
            screen = AlchemyNotebookScreen()
            screen.set_repository(repository)

            self.assertEqual(
                screen.reagent_table.editTriggers(),
                QTableWidget.EditTrigger.NoEditTriggers,
            )
            self.assertEqual(
                screen.reagent_table.itemDelegate().__class__.__name__,
                "_NoCellFocusDelegate",
            )
            self.assertEqual(
                screen.recipe_table.itemDelegate().__class__.__name__,
                "_NoCellFocusDelegate",
            )
            self.assertEqual(
                screen.recipe_table.editTriggers(),
                QTableWidget.EditTrigger.NoEditTriggers,
            )
            self.assertEqual(
                screen.reagent_table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable,
                Qt.ItemFlag.NoItemFlags,
            )

            screen.reagent_table.selectRow(0)
            QApplication.processEvents()

            self.assertEqual(screen.reagent_name_input.text(), "Moon Salt")
            self.assertEqual(screen.reagent_qualities_input.text(), "cold, silver")
            self.assertEqual(screen.reagent_motions_input.text(), "settling")
            self.assertEqual(screen.reagent_virtues_input.text(), "clarity")
            self.assertEqual(screen.reagent_uses_input.text(), "cooling draughts")
            self.assertEqual(screen.reagent_notes_input.toPlainText(), "Crystals hum softly.")

            screen.reagent_uses_input.setText("mirror inks")
            screen._save_reagent()

            reagent = repository.list_alchemy_reagents()[0]
            self.assertEqual(reagent["uses"], ["mirror inks"])
            self.assertEqual(screen.reagent_name_input.text(), "")
            screen.close()

    def test_new_game_wizard_loads_template_fields(self) -> None:
        QApplication.instance() or QApplication([])
        wizard = NewGameWizard(
            template_setup={
                "title": "Template Adventure",
                "character": {
                    "name": "Iris Vale",
                    "appearance": "Rain-dark coat.",
                    "backstory": "Raised near the station.",
                    "notes": "Careful and observant.",
                },
                "skills": [{"name": f"Skill {index}"} for index in range(15)],
                "starter_items": [
                    {
                        "name": "Notebook",
                        "category": "Tool",
                        "quantity": 1,
                        "description": "Case notes.",
                        "value_base_units": 4,
                    }
                ],
                "calendar": {"calendar_type": "gregorian", "time_display": "24_hour"},
                "currency_denominations": [
                    {"name": "Bit", "plural_name": "Bits", "value": 1},
                    {"name": "Crown", "plural_name": "Crowns", "value": 12},
                ],
                "currency_description": "Crowns dominate city trade.",
                "specified_genre": "Realistic detective mystery",
                "game_style": "Quiet investigation.",
                "start_location": "Rainmarket Station",
                "world_context": "Canal guilds control the docks.",
            }
        )

        self.assertEqual(wizard.title_input.text(), "Template Adventure")
        self.assertEqual(wizard.character_name_input.text(), "Iris Vale")
        self.assertEqual(wizard.skill_inputs[0][1].text(), "Skill 0")
        self.assertIn("Notebook | Tool | 1 | Case notes. | 4", wizard.starter_items_input.toPlainText())
        self.assertEqual(wizard.currency_table.rowCount(), 2)
        self.assertEqual(wizard.currency_table.item(1, 0).text(), "Crown")
        self.assertEqual(wizard.time_format_combo.currentData(), "24_hour")
        self.assertEqual(wizard.calendar_type_combo.currentData(), "gregorian")
        wizard.close()


if __name__ == "__main__":
    unittest.main()
