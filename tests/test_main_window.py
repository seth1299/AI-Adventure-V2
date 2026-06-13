from __future__ import annotations

import logging
import os
import tempfile
import unittest
import importlib.util
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if importlib.util.find_spec("PySide6") is None:
    raise unittest.SkipTest("PySide6 is not installed in this test environment.")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QDialog, QGroupBox, QLabel, QLineEdit, QTableWidget

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.user_settings import load_app_settings
from ai_adventure.audio.voices import DEFAULT_NARRATOR_VOICE
from ai_adventure.calendar_system import DEFAULT_CALENDAR_SETTINGS
from ai_adventure.new_game_setup import GREGORIAN_CALENDAR_SETTINGS, normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.main_window import (
    AlchemyNotebookScreen,
    CalendarSettingsDialog,
    GameShell,
    HistoryScreen,
    MainMenuSettingsDialog,
    MainWindow,
    NewGameWizard,
    SettingsScreen,
    StoryScreen,
    WorldScreen,
    _preserve_player_character_text,
    _set_combo_to_data,
    apply_application_theme,
)


class FakeNarrationPlayer:
    def __init__(self) -> None:
        self.on_chunk_start = None
        self.on_complete = None
        self.enabled = True
        self.volume = None
        self.voice = DEFAULT_NARRATOR_VOICE
        self.samples = []

    def narrate(self, text, *, voice=None, on_chunk_start=None, on_complete=None):
        self.on_chunk_start = on_chunk_start
        self.on_complete = on_complete
        if voice is not None:
            self.voice = voice
        return True

    def get_available_voices(self):
        return {
            "Sarah (Female, US)": "af_sarah",
            "Echo (Male, US)": "am_echo",
        }

    def get_default_voice(self):
        return DEFAULT_NARRATOR_VOICE

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)

    def set_volume(self, volume):
        self.volume = volume

    def set_voice(self, voice):
        self.voice = voice or DEFAULT_NARRATOR_VOICE

    def play_sample(self, *, voice=None, volume=None, text=""):
        self.samples.append({"voice": voice, "volume": volume, "text": text})
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
                self.assertIn("Crafting", tab_names)
                self.assertFalse(window.windowIcon().isNull())
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
                self.assertEqual(alchemy_tabs, ["Items", "Recipes"])
                window.close()

    def test_journal_screen_saves_ai_sharing_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Journal Toggle Test")
            screen = HistoryScreen()
            screen.set_repository(repository)

            self.assertFalse(screen.share_with_ai_checkbox.isChecked())

            screen.journal_input.setPlainText("Tell the AI this theory.")
            screen.share_with_ai_checkbox.setChecked(True)

            with patch("ai_adventure.ui.main_window.QMessageBox.information"):
                screen._save_journal()

            self.assertEqual(repository.get_journal_notes(), "Tell the AI this theory.")
            self.assertTrue(repository.get_journal_share_with_ai())
            screen.close()

    def test_journal_screen_autosaves_after_typing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Journal Autosave Test")
            screen = HistoryScreen()
            screen.set_repository(repository)

            screen.journal_input.setPlainText("Autosave this note.")
            screen.share_with_ai_checkbox.setChecked(True)

            self.assertTrue(screen._autosave_timer.isActive())

            with patch("ai_adventure.ui.main_window.QMessageBox.information") as information:
                screen._autosave_journal()

            information.assert_not_called()
            self.assertEqual(repository.get_journal_notes(), "Autosave this note.")
            self.assertTrue(repository.get_journal_share_with_ai())
            screen.close()

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

            self.assertIn("Take action.", screen.story_output.toPlainText())
            screen.close()

    def test_story_reveal_state_clears_when_repository_is_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Reveal Reset Test")
            story_text = "The opening scene is saved."
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
            self.assertNotIn("The opening scene is saved.", screen.story_output.toPlainText())

            screen.set_repository(repository)

            self.assertIn("The opening scene is saved.", screen.story_output.toPlainText())
            screen.close()

    def test_narrate_latest_story_keeps_saved_text_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Narrate Saved Test")
            repository.append_history("story", "The introduction is already in history.")
            narration_player = FakeNarrationPlayer()
            screen = StoryScreen(narration_player=narration_player)
            screen.set_repository(repository)

            screen.narrate_latest_story()

            self.assertIn("The introduction is already in history.", screen.story_output.toPlainText())
            screen.close()

    def test_story_submit_displays_player_text_and_busy_state_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Submit Test")
            screen = StoryScreen()
            screen.set_repository(repository)
            started_packets = []

            def fake_start(context_packet):
                started_packets.append(context_packet)

            screen._start_skill_check_planning_request = fake_start
            screen.player_input.setText("Look under the counter.")

            screen._submit_player_action()

            self.assertEqual(len(started_packets), 1)
            self.assertIn("You: Look under the counter.", screen.story_output.toPlainText())
            self.assertEqual(screen.player_input.text(), "")
            self.assertFalse(screen.player_input.isEnabled())
            self.assertFalse(screen.submit_button.isEnabled())
            self.assertEqual(screen.player_input.placeholderText(), "GM is thinking...")
            self.assertEqual(screen.player_input.toolTip(), "GM is thinking...")
            screen.close()

    def test_continue_story_uses_latest_story_without_player_history_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Continue Test")
            repository.append_history("player", "Open the book and search for clues.")
            repository.append_history("story", "The brittle cover opens onto a map fragment.")
            screen = StoryScreen()
            screen.set_repository(repository)
            started_packets = []
            history_before = repository.list_history()

            def fake_start(context_packet):
                started_packets.append(context_packet)

            screen._start_gemini_story_request = fake_start

            self.assertTrue(screen.continue_button.isEnabled())
            screen._continue_story_response()

            self.assertEqual(len(started_packets), 1)
            self.assertTrue(started_packets[0]["continuation_request"]["active"])
            self.assertIn(
                "The brittle cover opens onto a map fragment.",
                started_packets[0]["continuation_request"]["latest_story_response"],
            )
            self.assertEqual(repository.list_history(), history_before)
            self.assertFalse(screen.player_input.isEnabled())
            self.assertFalse(screen.continue_button.isEnabled())
            screen.close()

    def test_skill_check_plan_resolves_before_story_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Plan Test")
            repository.upsert_skill("Foraging", "Finding useful materials.", 1)
            repository.append_history("player", "Search the cliff face for rare herbs.")
            screen = StoryScreen()
            screen.set_repository(repository)
            started_packets = []
            applied_events = []

            class FakeEventApplier:
                def __init__(self, _repository):
                    pass

                def apply_events(self, events, **_kwargs):
                    applied_events.extend(events)
                    return [
                        SimpleNamespace(
                            event_type="SkillCheckRequestedEvent",
                            status="applied",
                            message="Foraging check failure: 6 vs DC 14.",
                            payload={
                                "skill_name": "Foraging",
                                "dc": 14,
                                "roll": 2,
                                "raw_roll": 2,
                                "total": 6,
                                "outcome": "failure",
                            },
                        )
                    ]

            def fake_start(context_packet):
                started_packets.append(context_packet)

            screen._start_gemini_story_request = fake_start

            with patch("ai_adventure.ui.main_window.EventApplier", FakeEventApplier):
                screen._handle_skill_check_plan_result(
                    SimpleNamespace(
                        checks=[
                            {
                                "skill_name": "Foraging",
                                "dc": 14,
                                "reason": "Searching a dangerous cliff.",
                            }
                        ]
                    )
                )

            resolved_checks = started_packets[0]["state"]["skills"]["resolved_checks_this_turn"]

            self.assertEqual(applied_events[0]["type"], "SkillCheckRequestedEvent")
            self.assertEqual(applied_events[0]["payload"]["reason"], "Searching a dangerous cliff.")
            self.assertEqual(resolved_checks[0]["skill_name"], "Foraging")
            self.assertEqual(resolved_checks[0]["outcome"], "failure")
            self.assertEqual(resolved_checks[0]["roll"], 2)
            self.assertEqual(screen._pending_skill_check_event_results[0].payload["outcome"], "failure")
            screen.close()

    def test_story_result_keeps_input_disabled_until_tts_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "TTS Busy Test")
            narration_player = FakeNarrationPlayer()
            screen = StoryScreen(narration_player=narration_player)
            screen.set_repository(repository)
            screen._set_waiting_for_gm(True)

            screen._handle_gemini_story_result(
                SimpleNamespace(
                    narrative_text="First sentence. Second sentence.",
                    suggested_events=[],
                )
            )

            self.assertFalse(screen.player_input.isEnabled())
            self.assertFalse(screen.submit_button.isEnabled())
            self.assertNotIn("First sentence.", screen.story_output.toPlainText())

            narration_player.play_chunk("First sentence.")
            QApplication.processEvents()

            self.assertFalse(screen.player_input.isEnabled())
            self.assertIn("First sentence.", screen.story_output.toPlainText())

            narration_player.complete()
            QApplication.processEvents()

            self.assertTrue(screen.player_input.isEnabled())
            self.assertTrue(screen.submit_button.isEnabled())
            self.assertEqual(screen.player_input.placeholderText(), "Enter a player action...")
            self.assertEqual(screen.player_input.toolTip(), "")
            screen.close()

    def test_story_screen_renders_markdown_story_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Markdown Story Test")
            repository.append_history(
                "story",
                "You meet **Mira Coppercup**.\n\n*Stay calm,* you think.",
            )
            screen = StoryScreen()
            screen.set_repository(repository)

            plain_text = screen.story_output.toPlainText()

            self.assertIn("You meet Mira Coppercup.", plain_text)
            self.assertIn("Stay calm, you think.", plain_text)
            self.assertNotIn("**Mira Coppercup**", plain_text)
            self.assertNotIn("*Stay calm,*", plain_text)
            screen.close()

    def test_world_screen_renders_markdown_summary_and_lore(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Markdown World Test")
            repository.set_world_summary("The city of **Rainmarket** watches the canals.")
            repository.set_world_lore(
                {
                    "Locations": {
                        "Rainmarket Station": "A rail hub with *old brass clocks*."
                    }
                }
            )
            screen = WorldScreen()
            screen.set_repository(repository)

            plain_text = screen.world_output.toPlainText()

            self.assertIn("World Overview", plain_text)
            self.assertIn("The city of Rainmarket watches the canals.", plain_text)
            self.assertIn("Locations", plain_text)
            self.assertIn("Rainmarket Station: A rail hub with old brass clocks.", plain_text)
            self.assertNotIn("**Rainmarket**", plain_text)
            self.assertNotIn("*old brass clocks*", plain_text)
            screen.close()

    def test_ai_new_game_state_sets_currency_balance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            repository = SaveRepository.create_new_save(temp_path, "Currency Setup")
            window = MainWindow(
                app_paths=AppPaths(
                    app_data_dir=temp_path,
                    saves_dir=temp_path / "saves",
                    logs_dir=temp_path / "logs",
                    log_file=temp_path / "logs" / "ai_adventure.log",
                )
            )
            setup = {
                "currency_denominations": [
                    {"name": "Bit", "plural_name": "Bits", "value": 1}
                ]
            }

            window._apply_new_game_ai_state(
                repository,
                setup,
                SimpleNamespace(
                    start_location="",
                    starting_calendar={},
                    start_weather="",
                    finalized_starting_currency_balance_base_units=37,
                    finalized_currency_denominations=[],
                    finalized_currency_description="",
                    selected_genre="",
                    finalized_character={},
                    finalized_skills=[],
                    finalized_starter_items=[],
                ),
            )

            self.assertEqual(repository.get_state_value("currency.balance"), "37")
            window.close()

    def test_ai_generated_calendar_settings_replace_bootstrap_calendar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            setup = normalize_new_game_setup(
                {"title": "AI Calendar", "calendar": {"calendar_type": "ai_generated"}}
            )
            repository = SaveRepository.create_new_save(
                temp_path,
                "AI Calendar",
                setup=setup,
            )
            window = MainWindow(
                app_paths=AppPaths(
                    app_data_dir=temp_path,
                    saves_dir=temp_path / "saves",
                    logs_dir=temp_path / "logs",
                    log_file=temp_path / "logs" / "ai_adventure.log",
                )
            )

            window._apply_new_game_ai_state(
                repository,
                setup,
                SimpleNamespace(
                    start_location="",
                    calendar_settings={
                        "days_per_week": 8,
                        "weeks_per_month": 5,
                        "months_per_year": 10,
                        "seasons_per_year": 2,
                        "day_names": [
                            "Bell",
                            "Canal",
                            "Ledger",
                            "Rain",
                            "Market",
                            "Lantern",
                            "Lock",
                            "Mist",
                        ],
                        "month_names": ["First Rain", "Second Rain"],
                        "seasons": [
                            {"name": "Wet", "weather_hint": "spring"},
                            {"name": "Cold", "weather_hint": "winter"},
                        ],
                        "time_display": "24_hour",
                    },
                    starting_calendar={"day_of_month": 1, "time_of_day_minutes": 480},
                    start_weather="",
                    finalized_starting_currency_balance_base_units=None,
                    finalized_currency_denominations=[],
                    finalized_currency_description="",
                    selected_genre="",
                    finalized_character={},
                    finalized_skills=[],
                    finalized_starter_items=[],
                ),
            )

            calendar_settings = repository.get_calendar_settings()

            self.assertEqual(calendar_settings["days_per_week"], 8)
            self.assertEqual(calendar_settings["day_names"][0], "Bell")
            self.assertEqual(calendar_settings["time_display"], "24_hour")
            self.assertIn("08:00", repository.get_state_value("time"))
            window.close()

    def test_ai_new_game_state_preserves_player_provided_character_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            setup = normalize_new_game_setup(
                {
                    "title": "Character Preservation",
                    "character": {
                        "name": "Ghum Schoo",
                        "appearance": "Ghum is a 30-year-old detective.",
                        "backstory": "Ghum opened a small detective agency.",
                        "notes": "Ghum is sarcastic but serious about crime.",
                    },
                }
            )
            repository = SaveRepository.create_new_save(
                temp_path,
                "Character Preservation",
                setup=setup,
            )
            window = MainWindow(
                app_paths=AppPaths(
                    app_data_dir=temp_path,
                    saves_dir=temp_path / "saves",
                    logs_dir=temp_path / "logs",
                    log_file=temp_path / "logs" / "ai_adventure.log",
                )
            )

            window._apply_new_game_ai_state(
                repository,
                setup,
                SimpleNamespace(
                    start_location="",
                    starting_calendar={},
                    start_weather="",
                    finalized_starting_currency_balance_base_units=None,
                    finalized_currency_denominations=[],
                    finalized_currency_description="",
                    selected_genre="",
                    finalized_character={
                        "name": "Elias Thorne",
                        "appearance": "Elias is a detective in a darker coat.",
                        "backstory": "Elias opened a renamed inquiry bureau.",
                        "notes": "Elias has a different personality.",
                    },
                    finalized_skills=[],
                    finalized_starter_items=[],
                ),
            )

            self.assertEqual(repository.get_setting("player_name"), "Ghum Schoo")
            self.assertEqual(
                repository.get_setting("player.appearance"),
                "Ghum is a 30-year-old detective.",
            )
            self.assertEqual(
                repository.get_setting("player.backstory"),
                "Ghum opened a small detective agency.",
            )
            self.assertEqual(
                repository.get_setting("player.notes"),
                "Ghum is sarcastic but serious about crime.",
            )
            window.close()

    def test_ai_new_game_state_fills_blank_character_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            setup = normalize_new_game_setup(
                {
                    "title": "Generated Character",
                    "character": {
                        "name": "Player Name",
                        "appearance": "",
                        "backstory": "",
                        "notes": "",
                    },
                }
            )
            repository = SaveRepository.create_new_save(
                temp_path,
                "Generated Character",
                setup=setup,
            )
            window = MainWindow(
                app_paths=AppPaths(
                    app_data_dir=temp_path,
                    saves_dir=temp_path / "saves",
                    logs_dir=temp_path / "logs",
                    log_file=temp_path / "logs" / "ai_adventure.log",
                )
            )

            window._apply_new_game_ai_state(
                repository,
                setup,
                SimpleNamespace(
                    start_location="",
                    starting_calendar={},
                    start_weather="",
                    finalized_starting_currency_balance_base_units=None,
                    finalized_currency_denominations=[],
                    finalized_currency_description="",
                    selected_genre="",
                    finalized_character={
                        "name": "Mara Flint",
                        "appearance": "A field medic in a patched coat.",
                        "backstory": "Raised on border roads.",
                        "notes": "Calm under pressure.",
                    },
                    finalized_skills=[],
                    finalized_starter_items=[],
                ),
            )

            self.assertEqual(repository.get_setting("player_name"), "Mara Flint")
            self.assertEqual(
                repository.get_setting("player.appearance"),
                "A field medic in a patched coat.",
            )
            window.close()

    def test_ai_new_game_text_repairs_ai_renamed_player_character(self) -> None:
        setup = normalize_new_game_setup(
            {
                "character": {
                    "name": "Ghum Schoo",
                    "appearance": "Ghum is a detective.",
                }
            }
        )
        ai_character = {"name": "Elias Thorne"}

        repaired_text = _preserve_player_character_text(
            "Elias Thorne checks the window. Elias keeps his coat close.",
            setup,
            ai_character,
        )
        repaired_lore = _preserve_player_character_text(
            {
                "Prominent NPCs": {
                    "Elias Thorne": "Elias is known around the office."
                }
            },
            setup,
            ai_character,
        )

        self.assertEqual(
            repaired_text,
            "Ghum Schoo checks the window. Ghum keeps his coat close.",
        )
        self.assertIn("Ghum Schoo", repaired_lore["Prominent NPCs"])
        self.assertEqual(
            repaired_lore["Prominent NPCs"]["Ghum Schoo"],
            "Ghum is known around the office.",
        )

    def test_ai_new_game_state_accepts_generated_blank_skill_slots_with_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            setup = normalize_new_game_setup({"title": "Generated Skills"})
            repository = SaveRepository.create_new_save(
                temp_path,
                "Generated Skills",
                setup=setup,
            )
            window = MainWindow(
                app_paths=AppPaths(
                    app_data_dir=temp_path,
                    saves_dir=temp_path / "saves",
                    logs_dir=temp_path / "logs",
                    log_file=temp_path / "logs" / "ai_adventure.log",
                )
            )
            generated_skills = [
                {
                    "name": f"Generated Skill {index}",
                    "description": f"Generated skill {index} description.",
                    "level": skill["level"],
                }
                for index, skill in enumerate(setup["skills"])
            ]
            generated_skills[1]["name"] = "Persuasion"
            generated_skills[-1]["name"] = "Persuasion"

            window._apply_new_game_ai_state(
                repository,
                setup,
                SimpleNamespace(
                    start_location="",
                    starting_calendar={},
                    start_weather="",
                    finalized_starting_currency_balance_base_units=None,
                    finalized_currency_denominations=[],
                    finalized_currency_description="",
                    selected_genre="",
                    finalized_character={},
                    finalized_skills=generated_skills,
                    finalized_starter_items=[],
                ),
            )

            skills = repository.list_skills()
            skill_names = [skill["name"] for skill in skills]

            self.assertEqual(len(skills), len(setup["skills"]))
            self.assertIn("Persuasion", skill_names)
            self.assertIn("Persuasion (Familiar)", skill_names)
            self.assertEqual(len(skill_names), len(set(skill_names)))
            window.close()

    def test_ai_new_game_state_accepts_partial_ai_starter_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            setup = {
                "starter_items": [
                    {
                        "name": "",
                        "category": "Item",
                        "quantity": 1,
                        "description": "",
                        "value_base_units": 0,
                        "item_request": "An old brass lantern that burns blue near danger.",
                        "requires_ai_invention": True,
                    },
                    {
                        "name": "",
                        "category": "Item",
                        "quantity": 1,
                        "description": "",
                        "value_base_units": 0,
                        "item_request": "A sealed map case with routes to the coast.",
                        "requires_ai_invention": True,
                    },
                    {
                        "name": "",
                        "category": "Item",
                        "quantity": 1,
                        "description": "",
                        "value_base_units": 0,
                        "item_request": "A field compass that points toward storms.",
                        "requires_ai_invention": True,
                    },
                    {
                        "name": "",
                        "category": "Item",
                        "quantity": 1,
                        "description": "",
                        "value_base_units": 0,
                        "item_request": "A waxed packet of emergency signal flares.",
                        "requires_ai_invention": True,
                    },
                    {
                        "name": "",
                        "category": "Item",
                        "quantity": 1,
                        "description": "",
                        "value_base_units": 0,
                        "item_request": "A compact repair kit for rain-soaked gear.",
                        "requires_ai_invention": True,
                    },
                ],
                "currency_denominations": [
                    {"name": "Bit", "plural_name": "Bits", "value": 1}
                ],
            }
            repository = SaveRepository.create_new_save(
                temp_path,
                "Inventory Completion",
                setup=setup,
            )
            window = MainWindow(
                app_paths=AppPaths(
                    app_data_dir=temp_path,
                    saves_dir=temp_path / "saves",
                    logs_dir=temp_path / "logs",
                    log_file=temp_path / "logs" / "ai_adventure.log",
                )
            )

            window._apply_new_game_ai_state(
                repository,
                setup,
                SimpleNamespace(
                    start_location="",
                    starting_calendar={},
                    start_weather="",
                    finalized_starting_currency_balance_base_units=None,
                    finalized_currency_denominations=[],
                    finalized_currency_description="",
                    selected_genre="",
                    finalized_character={},
                    finalized_skills=[],
                    finalized_starter_items=[
                        {
                            "name": "Blue-Wick Warning Lantern",
                            "category": "Tool",
                            "quantity": 1,
                            "description": "A brass lantern that burns blue near danger.",
                            "value_base_units": 8,
                            "source_index": 0,
                        }
                    ],
                ),
            )

            item_names = {item["name"] for item in repository.list_inventory_items()}

            self.assertEqual(len(item_names), 5)
            self.assertIn("Blue-Wick Warning Lantern", item_names)
            self.assertIn("Sealed Map Case", item_names)
            self.assertIn("Field Compass", item_names)
            self.assertIn("Waxed Packet Of Emergency Signal", item_names)
            self.assertIn("Compact Repair Kit", item_names)
            window.close()

    def test_alchemy_reagent_selection_populates_form_without_table_editing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Crafting UI Test")
            repository.add_crafting_item(
                name="Moon Salt",
                description="Crystals hum softly.",
                location="Moonlit stone basins",
                uses=["cooling draughts"],
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

            self.assertEqual(screen.reagent_table.columnCount(), 4)
            self.assertEqual(screen.reagent_table.horizontalHeaderItem(1).text(), "Description")
            self.assertEqual(screen.tabs.tabText(0), "Items")
            self.assertEqual(screen.reagent_name_input.placeholderText(), "Item or material name")
            self.assertEqual(
                screen.recipe_reagent_combo.placeholderText(),
                "Search material, ingredient, reagent, or crafting item",
            )
            self.assertEqual(screen.recipe_ingredient_table.horizontalHeaderItem(0).text(), "Item")
            self.assertEqual(screen.reagent_name_input.text(), "Moon Salt")
            self.assertEqual(screen.reagent_description_input.text(), "Crystals hum softly.")
            self.assertEqual(screen.reagent_location_input.text(), "Moonlit stone basins")
            self.assertEqual(screen.reagent_uses_input.text(), "cooling draughts")

            screen.reagent_uses_input.setText("mirror inks")
            screen.reagent_location_input.setText("Silver mine walls")
            screen._save_reagent()

            reagent = repository.list_crafting_items()[0]
            self.assertEqual(reagent["location"], "Silver mine walls")
            self.assertEqual(reagent["uses"], ["mirror inks"])
            self.assertEqual(screen.reagent_name_input.text(), "")
            screen.close()

    def test_recipe_ingredients_use_known_reagent_dropdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Recipe UI Test")
            repository.add_crafting_item(
                name="Alcohol Base",
                description="Purified spirit used to extract active compounds.",
                location="Distilled in an alchemist's workshop",
                uses=["extraction", "preservation"],
            )
            repository.add_inventory_item(
                name="Stirring Rod",
                category="Tool",
                quantity=1,
                description="A glass rod for stirring mixtures.",
                value_base_units=3,
            )
            screen = AlchemyNotebookScreen()
            screen.set_repository(repository)

            self.assertFalse(hasattr(screen, "recipe_ingredients_input"))
            self.assertEqual(screen.recipe_reagent_combo.itemText(0), "Alcohol Base (Material)")
            self.assertEqual(screen.recipe_reagent_combo.itemData(0), "Alcohol Base")
            self.assertNotIn(
                "Stirring Rod",
                [
                    screen.recipe_reagent_combo.itemData(index)
                    for index in range(screen.recipe_reagent_combo.count())
                ],
            )
            self.assertEqual(
                screen.recipe_reagent_choice_model.stringList(),
                ["Alcohol Base (Material)"],
            )
            self.assertIsNotNone(screen.recipe_reagent_line_edit)

            with patch("ai_adventure.ui.main_window.QTimer.singleShot") as single_shot:
                handled = screen.eventFilter(
                    screen.recipe_reagent_line_edit,
                    QEvent(QEvent.Type.MouseButtonPress),
                )

            self.assertFalse(handled)
            single_shot.assert_called_once()
            self.assertEqual(single_shot.call_args.args[0], 0)
            self.assertTrue(callable(single_shot.call_args.args[1]))

            screen.recipe_name_input.setText("Cooling Tincture")
            screen.recipe_quantity_input.setValue(1)
            screen.recipe_measure_amount_input.setValue(100)
            screen.recipe_measure_unit_combo.setCurrentText("mL")
            screen._add_recipe_ingredient()
            screen.recipe_result_input.setText("A mild cooling tincture.")
            screen._add_recipe()

            recipe = repository.list_crafting_recipes()[0]
            self.assertEqual(recipe["ingredients"][0]["reagent_name"], "Alcohol Base")
            self.assertEqual(recipe["ingredients"][0]["quantity"], 1)
            self.assertEqual(recipe["ingredients"][0]["measure_amount"], 100)
            self.assertEqual(recipe["ingredients"][0]["measure_unit"], "mL")
            screen.close()

    def test_refresh_keeps_inventory_sorted_after_repository_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository(Path(temp_dir) / "sort.sqlite3")
            repository.set_meta("title", "Refresh Sort Test")
            repository.add_inventory_item(
                name="Small Stone",
                category="Item",
                quantity=1,
                description="A small stone.",
                value_base_units=0,
            )
            repository.add_inventory_item(
                name="Torch",
                category="Tool",
                quantity=2,
                description="A pitch torch.",
                value_base_units=1,
            )
            shell = GameShell(on_return_to_menu=lambda: None)
            shell.set_repository(repository)

            shell.inventory_screen._sort_by_column(2)
            shell.inventory_screen._sort_by_column(2)
            repository.add_inventory_item(
                name="Rope",
                category="Tool",
                quantity=3,
                description="A coil of rope.",
                value_base_units=5,
            )

            shell.refresh_screens()

            self.assertEqual(shell.inventory_screen.table.item(0, 0).text(), "Rope")
            self.assertEqual(shell.inventory_screen.table.item(1, 0).text(), "Torch")
            self.assertEqual(shell.inventory_screen.table.item(2, 0).text(), "Small Stone")
            shell.close()

    def test_inventory_screen_displays_currency_balance_outside_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Currency UI Test")
            repository.set_state_value("currency.balance", "65")
            shell = GameShell(on_return_to_menu=lambda: None)
            shell.set_repository(repository)

            self.assertEqual(
                shell.inventory_screen.currency_label.text(),
                "Currency: 6 Silver Pieces and 5 Copper Pieces",
            )
            shell.close()

    def test_settings_autosave_persists_slider_release_and_refreshes_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Settings Test")
            shell = GameShell(on_return_to_menu=lambda: None)
            shell.set_repository(repository)

            shell.settings_screen.music_volume_slider.setValue(42)
            shell.settings_screen.music_volume_slider.sliderReleased.emit()
            _set_combo_to_data(shell.settings_screen.tts_voice_combo, "am_echo")
            QApplication.processEvents()

            self.assertEqual(repository.get_setting("audio.music_volume"), 42)
            self.assertEqual(repository.get_setting("audio.tts_voice"), "am_echo")
            self.assertFalse(hasattr(shell.settings_screen, "days_per_week_input"))
            self.assertEqual(shell.calendar_screen.table.columnCount(), 7)
            shell.close()

    def test_calendar_screen_settings_dialog_persists_and_refreshes_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Calendar Settings Test")
            shell = GameShell(on_return_to_menu=lambda: None)
            shell.set_repository(repository)
            changed_sources = []
            shell.calendar_screen.on_repository_changed = (
                lambda source: changed_sources.append(source)
            )

            shell.calendar_screen._save_calendar_settings(
                {
                    "days_per_week": 8,
                    "weeks_per_month": 5,
                    "months_per_year": 10,
                    "seasons_per_year": 2,
                    "day_names": [
                        "One",
                        "Two",
                        "Three",
                        "Four",
                        "Five",
                        "Six",
                        "Seven",
                        "Eight",
                    ],
                    "month_names": ["First"],
                    "seasons": [
                        {"name": "Warm", "weather_hint": "summer"},
                        {"name": "Cold", "weather_hint": "winter"},
                    ],
                    "time_display": "24_hour",
                }
            )

            self.assertEqual(repository.get_calendar_settings()["days_per_week"], 8)
            self.assertEqual(repository.get_calendar_settings()["weeks_per_month"], 5)
            self.assertEqual(repository.get_calendar_settings()["time_display"], "24_hour")
            self.assertEqual(shell.calendar_screen.table.columnCount(), 8)
            self.assertEqual(changed_sources, [shell.calendar_screen])
            shell.close()

    def test_calendar_settings_dialog_builds_calendar_settings(self) -> None:
        QApplication.instance() or QApplication([])
        dialog = CalendarSettingsDialog(DEFAULT_CALENDAR_SETTINGS)

        try:
            dialog.days_per_week_input.setValue(9)
            dialog.weeks_per_month_input.setValue(6)
            dialog.months_per_year_input.setValue(11)
            dialog.seasons_per_year_input.setValue(2)
            dialog.day_names_input.setText("A, B, C")
            dialog.month_names_input.setText("M1, M2")
            dialog.season_names_input.setText("Dry, Wet")
            dialog.season_hints_input.setText("summer, spring")
            dialog.time_display_combo.setCurrentIndex(
                dialog.time_display_combo.findData("12_hour")
            )

            settings = dialog.build_settings()

            self.assertEqual(settings["days_per_week"], 9)
            self.assertEqual(settings["weeks_per_month"], 6)
            self.assertEqual(settings["months_per_year"], 11)
            self.assertEqual(settings["day_names"], ["A", "B", "C"])
            self.assertEqual(settings["month_names"], ["M1", "M2"])
            self.assertEqual(settings["seasons"][0]["name"], "Dry")
            self.assertEqual(settings["time_display"], "12_hour")
        finally:
            dialog.close()

    def test_settings_theme_change_persists_and_notifies_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Theme Test")
            theme_changes = []
            screen = SettingsScreen(on_theme_changed=lambda: theme_changes.append("theme"))
            screen.set_repository(repository)

            screen.theme_combo.setCurrentText("Dark")
            QApplication.processEvents()

            self.assertEqual(repository.get_setting("theme"), "Dark")
            self.assertEqual(theme_changes, ["theme"])
            screen.close()

    def test_settings_narration_preferences_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Narration Test")
            screen = SettingsScreen()
            screen.set_repository(repository)

            _set_combo_to_data(screen.narration_tense_combo, "future")
            _set_combo_to_data(screen.narration_style_combo, "first_person_omniscient")
            QApplication.processEvents()

            self.assertEqual(repository.get_setting("ai.narration_tense"), "future")
            self.assertEqual(
                repository.get_setting("ai.narration_style"),
                "first_person_omniscient",
            )
            screen.close()

    def test_settings_sample_voice_uses_selected_voice_and_volume(self) -> None:
        QApplication.instance() or QApplication([])
        samples = []
        screen = SettingsScreen(
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
            on_sample_voice=lambda voice, volume: samples.append((voice, volume)) or True,
        )

        try:
            _set_combo_to_data(screen.tts_voice_combo, "am_echo")
            screen.tts_volume_slider.setValue(41)
            screen.sample_voice_button.click()

            self.assertEqual(samples, [("am_echo", 41)])
        finally:
            screen.close()

    def test_main_menu_exposes_settings_button(self) -> None:
        QApplication.instance() or QApplication([])

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
            window = MainWindow(app_paths=app_paths)

            self.assertEqual(window.main_menu.settings_button.text(), "Settings")

            window.close()
            apply_application_theme("Light")

    def test_main_menu_settings_apply_and_persist_without_save(self) -> None:
        app = QApplication.instance() or QApplication([])

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
            window = MainWindow(app_paths=app_paths)

            window._apply_app_settings(
                {
                    "theme": "Dark",
                    "audio": {
                        "music_enabled": False,
                        "narrator_enabled": False,
                        "music_volume": 7,
                        "tts_volume": 20,
                        "tts_voice": "am_echo",
                    },
                },
                persist=True,
            )

            saved_settings = load_app_settings(app_paths.app_settings_path)

            self.assertEqual(saved_settings["theme"], "Dark")
            self.assertFalse(saved_settings["audio"]["music_enabled"])
            self.assertEqual(saved_settings["audio"]["music_volume"], 7)
            self.assertEqual(saved_settings["audio"]["tts_voice"], "am_echo")
            self.assertEqual(window.menu_theme, "Dark")
            self.assertFalse(window.sound_manager.music_enabled)
            self.assertEqual(window.sound_manager.music_volume, 0.07)
            self.assertEqual(
                app.palette().color(QPalette.ColorRole.Window).name(),
                "#202124",
            )

            window.close()
            apply_application_theme("Light")

    def test_main_menu_settings_dialog_builds_audio_and_theme_settings(self) -> None:
        QApplication.instance() or QApplication([])
        dialog = MainMenuSettingsDialog(
            settings={
                "theme": "Light",
                "audio": {
                    "music_enabled": True,
                    "narrator_enabled": True,
                    "music_volume": 25,
                    "tts_volume": 90,
                    "tts_voice": "af_sarah",
                },
            },
            tts_enabled=True,
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
        )

        try:
            dialog.theme_combo.setCurrentText("Dark")
            dialog.music_enabled_checkbox.setChecked(False)
            dialog.music_volume_slider.setValue(12)
            dialog.narrator_enabled_checkbox.setChecked(False)
            dialog.tts_volume_slider.setValue(34)
            _set_combo_to_data(dialog.tts_voice_combo, "am_echo")

            settings = dialog.build_settings()

            self.assertEqual(settings["theme"], "Dark")
            self.assertFalse(settings["audio"]["music_enabled"])
            self.assertEqual(settings["audio"]["music_volume"], 12)
            self.assertFalse(settings["audio"]["narrator_enabled"])
            self.assertEqual(settings["audio"]["tts_volume"], 34)
            self.assertEqual(settings["audio"]["tts_voice"], "am_echo")
        finally:
            dialog.close()

    def test_main_menu_settings_sample_voice_uses_selected_voice_and_volume(self) -> None:
        QApplication.instance() or QApplication([])
        samples = []
        dialog = MainMenuSettingsDialog(
            settings={
                "theme": "Light",
                "audio": {
                    "music_enabled": True,
                    "narrator_enabled": True,
                    "music_volume": 25,
                    "tts_volume": 90,
                    "tts_voice": "af_sarah",
                },
            },
            tts_enabled=True,
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
            on_sample_voice=lambda voice, volume: samples.append((voice, volume)) or True,
        )

        try:
            _set_combo_to_data(dialog.tts_voice_combo, "am_echo")
            dialog.tts_volume_slider.setValue(37)
            dialog.sample_voice_button.click()

            self.assertEqual(samples, [("am_echo", 37)])
        finally:
            dialog.close()

    def test_main_menu_uses_latest_saved_dark_theme_on_startup(self) -> None:
        app = QApplication.instance() or QApplication([])

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
            repository = SaveRepository.create_new_save(
                app_paths.saves_dir,
                "Dark Menu Test",
            )
            repository.set_setting("theme", "Dark")

            window = MainWindow(app_paths=app_paths)

            self.assertEqual(window.stack.currentWidget(), window.main_menu)
            self.assertEqual(window.menu_theme, "Dark")
            self.assertEqual(
                app.palette().color(QPalette.ColorRole.Window).name(),
                "#202124",
            )
            window.close()
            apply_application_theme("Light")

    def test_create_new_game_warns_when_save_title_already_exists(self) -> None:
        app = QApplication.instance() or QApplication([])

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
            SaveRepository.create_new_save(app_paths.saves_dir, "Duplicate UI Save")
            window = MainWindow(app_paths=app_paths)

            with patch("ai_adventure.ui.main_window.QMessageBox.warning") as warning:
                window.create_new_game({"title": " duplicate ui save "})

            warning.assert_called_once()
            self.assertEqual(window.stack.currentWidget(), window.main_menu)
            window.close()
            apply_application_theme("Light")

    def test_start_new_game_wizard_prompts_for_new_name_after_duplicate(self) -> None:
        QApplication.instance() or QApplication([])

        class FakeTitleInput:
            def __init__(self, value: str) -> None:
                self._value = value

            def text(self) -> str:
                return self._value

            def setText(self, value: str) -> None:
                self._value = value

        class FakeWizard:
            def __init__(self, *_args, **_kwargs) -> None:
                self.title_input = FakeTitleInput("Duplicate UI Save")
                self.exec_calls = 0

            def exec(self):
                self.exec_calls += 1
                return QDialog.DialogCode.Accepted

            def build_setup(self) -> dict:
                return {
                    "title": self.title_input.text(),
                    "game_style": "Keep these fields.",
                    "character": {"name": "Iris"},
                }

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
            SaveRepository.create_new_save(app_paths.saves_dir, "Duplicate UI Save")
            window = MainWindow(app_paths=app_paths)
            scheduled_callbacks = []
            wizard_instances = []

            def fake_new_game_wizard(*args, **kwargs):
                wizard = FakeWizard(*args, **kwargs)
                wizard_instances.append(wizard)
                return wizard

            def fake_single_shot(_interval, callback):
                scheduled_callbacks.append(callback)

            with patch.object(
                window,
                "_choose_new_game_template_setup",
                return_value=(True, None),
            ), patch(
                "ai_adventure.ui.main_window.NewGameWizard",
                fake_new_game_wizard,
            ), patch(
                "ai_adventure.ui.main_window.QInputDialog.getText",
                return_value=("Duplicate UI Save 2", True),
            ) as get_text, patch(
                "ai_adventure.ui.main_window.QTimer.singleShot",
                fake_single_shot,
            ):
                window.start_new_game_wizard()

            self.assertEqual(len(wizard_instances), 1)
            self.assertEqual(wizard_instances[0].exec_calls, 1)
            self.assertEqual(wizard_instances[0].title_input.text(), "Duplicate UI Save 2")
            get_text.assert_called_once()
            self.assertIsNotNone(window.active_repository)
            self.assertEqual(
                window.active_repository.get_meta("title"),
                "Duplicate UI Save 2",
            )
            self.assertEqual(len(scheduled_callbacks), 1)
            window.close()
            apply_application_theme("Light")

    def test_create_new_game_opens_blank_shell_before_world_generation(self) -> None:
        app = QApplication.instance() or QApplication([])

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
            scheduled_callbacks = []
            generated = []
            window = MainWindow(app_paths=app_paths)

            def fake_single_shot(_interval, callback):
                scheduled_callbacks.append(callback)

            def fake_finish(repository, setup):
                generated.append((repository, setup))

            window._finish_new_game_generation = fake_finish

            with patch("ai_adventure.ui.main_window.QTimer.singleShot", fake_single_shot):
                window.create_new_game(
                    {
                        "title": "Immediate Shell Test",
                        "audio": {
                            "music_enabled": False,
                            "narrator_enabled": False,
                            "music_volume": 0,
                            "tts_volume": 20,
                            "tts_voice": "am_echo",
                        },
                    }
                )

            self.assertEqual(window.stack.currentWidget(), window.game_shell)
            self.assertIsNotNone(window.active_repository)
            self.assertEqual(generated, [])
            self.assertEqual(len(scheduled_callbacks), 1)
            self.assertFalse(window.game_shell.story_screen.player_input.isEnabled())
            self.assertFalse(
                window.active_repository.get_setting("audio.music_enabled", True)
            )
            self.assertFalse(
                window.active_repository.get_setting("audio.narrator_enabled", True)
            )
            self.assertEqual(window.active_repository.get_setting("audio.music_volume"), 0)
            self.assertEqual(window.active_repository.get_setting("audio.tts_volume"), 20)
            self.assertEqual(
                window.active_repository.get_setting("audio.tts_voice"),
                "am_echo",
            )

            scheduled_callbacks[0]()

            self.assertEqual(len(generated), 1)
            self.assertEqual(generated[0][0], window.active_repository)
            window.close()
            apply_application_theme("Light")

    def test_return_to_menu_preserves_active_save_dark_theme(self) -> None:
        app = QApplication.instance() or QApplication([])

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
            repository = SaveRepository.create_new_save(
                app_paths.saves_dir,
                "Return Theme Test",
            )
            window = MainWindow(app_paths=app_paths)

            window.open_repository(repository)
            window.game_shell.settings_screen.theme_combo.setCurrentText("Dark")
            QApplication.processEvents()
            window.return_to_menu()

            self.assertEqual(window.stack.currentWidget(), window.main_menu)
            self.assertEqual(window.menu_theme, "Dark")
            self.assertEqual(
                app.palette().color(QPalette.ColorRole.Window).name(),
                "#202124",
            )
            window.close()
            apply_application_theme("Light")

    def test_settings_theme_options_are_light_and_dark_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Theme Test")
            screen = SettingsScreen()
            screen.set_repository(repository)

            options = [
                screen.theme_combo.itemText(index)
                for index in range(screen.theme_combo.count())
            ]

            self.assertEqual(options, ["Light", "Dark"])
            self.assertEqual(screen.theme_combo.currentText(), "Light")
            screen.close()

    def test_settings_theme_migrates_system_to_light(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Theme Test")
            repository.set_setting("theme", "System")
            screen = SettingsScreen()
            screen.set_repository(repository)

            self.assertEqual(screen.theme_combo.currentText(), "Light")
            self.assertEqual(repository.get_setting("theme"), "Light")
            screen.close()

    def test_main_window_lightweight_mode_skips_narration_player(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            temp_path = Path(temp_dir)
            app_paths = AppPaths(
                app_data_dir=temp_path,
                saves_dir=temp_path / "saves",
                logs_dir=temp_path / "logs",
                log_file=temp_path / "logs" / "ai_adventure.log",
            )
            app_paths.saves_dir.mkdir(parents=True, exist_ok=True)
            app_paths.logs_dir.mkdir(parents=True, exist_ok=True)

            with patch.dict(os.environ, {"AI_ADVENTURE_DISABLE_TTS": "1"}):
                window = MainWindow(app_paths=app_paths)

            labels = [
                label.text()
                for label in window.game_shell.settings_screen.findChildren(QLabel)
            ]

            self.assertFalse(window.tts_enabled)
            self.assertIsNone(window.narration_player)
            self.assertIsNone(window.game_shell.settings_screen.narrator_enabled_checkbox)
            self.assertIsNone(window.game_shell.settings_screen.tts_voice_combo)
            self.assertIsNone(window.game_shell.settings_screen.sample_voice_button)
            self.assertNotIn("Narrator:", labels)
            self.assertNotIn("Narrator Volume:", labels)
            self.assertNotIn("Narrator Voice:", labels)
            window.close()

    def test_apply_application_theme_updates_application_palette(self) -> None:
        app = QApplication.instance() or QApplication([])

        try:
            apply_application_theme("Dark")

            self.assertEqual(
                app.palette().color(QPalette.ColorRole.Window).name(),
                "#202124",
            )
            self.assertIn("QWidget", app.styleSheet())
            self.assertIn("QComboBox::drop-down", app.styleSheet())
            self.assertIn("QComboBox::down-arrow", app.styleSheet())
            self.assertIn("QCheckBox::indicator", app.styleSheet())
            self.assertIn("background-color: #121416", app.styleSheet())
            self.assertIn("background-color: #4c8fcb", app.styleSheet())
            self.assertIn("QSpinBox::up-button", app.styleSheet())
            self.assertIn("border-top: 6px solid #f1f3f4", app.styleSheet())
            self.assertIn("height: 14px", app.styleSheet())
            self.assertIn("QSpinBox::down-button", app.styleSheet())

            apply_application_theme("Light")

            self.assertEqual(
                app.palette().color(QPalette.ColorRole.Window).name(),
                "#f5f7fb",
            )
            self.assertEqual(
                app.palette().color(QPalette.ColorRole.WindowText).name(),
                "#111827",
            )
            self.assertIn("QLabel, QCheckBox", app.styleSheet())
            self.assertIn("color: #111827", app.styleSheet())
            self.assertIn("QComboBox::drop-down", app.styleSheet())
            self.assertIn("QComboBox::down-arrow", app.styleSheet())
            self.assertIn("QCheckBox::indicator", app.styleSheet())
            self.assertIn("background-color: #ffffff", app.styleSheet())
            self.assertIn("background-color: #2563eb", app.styleSheet())
            self.assertIn("QSpinBox::up-button", app.styleSheet())
            self.assertIn("border-top: 6px solid #111827", app.styleSheet())
            self.assertIn("height: 14px", app.styleSheet())
            self.assertIn("QSpinBox::down-button", app.styleSheet())
        finally:
            apply_application_theme("Light")

    def test_new_game_lightweight_mode_saves_narrator_off(self) -> None:
        QApplication.instance() or QApplication([])

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

            with patch.dict(os.environ, {"AI_ADVENTURE_DISABLE_TTS": "1"}):
                window = MainWindow(app_paths=app_paths)

            window._finish_new_game_generation = lambda _repository, _setup: None

            with patch("ai_adventure.ui.main_window.QTimer.singleShot", lambda *_args: None):
                self.assertTrue(
                    window.create_new_game(
                        {
                            "title": "Lightweight New Game",
                            "audio": {
                                "music_enabled": True,
                                "narrator_enabled": True,
                                "music_volume": 25,
                                "tts_volume": 90,
                                "tts_voice": "am_echo",
                            },
                        }
                    )
                )

            self.assertIsNotNone(window.active_repository)
            self.assertFalse(
                window.active_repository.get_setting("audio.narrator_enabled", True)
            )
            self.assertEqual(window.active_repository.get_setting("audio.tts_volume"), 0)
            self.assertEqual(
                window.active_repository.get_setting("audio.tts_voice"),
                DEFAULT_NARRATOR_VOICE,
            )
            window.close()
            apply_application_theme("Light")

    def test_template_setup_uses_next_available_save_title(self) -> None:
        QApplication.instance() or QApplication([])

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
            SaveRepository.create_new_save(app_paths.saves_dir, "Adventure Quest")
            SaveRepository.create_new_save(app_paths.saves_dir, "Adventure Quest 2")
            window = MainWindow(app_paths=app_paths)
            template_setup = {"title": "Adventure Quest", "game_style": "Classic."}

            copied_setup = window._template_setup_with_available_title(template_setup)

            self.assertEqual(copied_setup["title"], "Adventure Quest 3")
            self.assertEqual(template_setup["title"], "Adventure Quest")
            window.close()
            apply_application_theme("Light")

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
                "skills": [
                    {
                        "name": f"Skill {index}",
                        "description": f"Skill {index} description.",
                    }
                    for index in range(15)
                ],
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
                "audio": {
                    "music_enabled": False,
                    "narrator_enabled": False,
                    "music_volume": 10,
                    "tts_volume": 30,
                    "tts_voice": "am_echo",
                },
                "narration": {
                    "tense": "past",
                    "style": "third_person_omniscient",
                },
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
        self.assertEqual(wizard.skill_inputs[0][2].text(), "Skill 0 description.")
        self.assertEqual(wizard.starter_items_table.rowCount(), 1)
        self.assertEqual(wizard.starter_items_table.cellWidget(0, 0).text(), "Notebook")
        self.assertEqual(wizard.starter_items_table.cellWidget(0, 1).value(), 1)
        self.assertEqual(wizard.starter_items_table.cellWidget(0, 2).text(), "Tool")
        self.assertEqual(wizard.starter_items_table.cellWidget(0, 3).text(), "Case notes.")
        self.assertEqual(wizard.starter_items_table.cellWidget(0, 4).value(), 4)
        self.assertEqual(wizard.currency_table.rowCount(), 2)
        self.assertEqual(wizard.currency_table.cellWidget(1, 0).text(), "Crown")
        self.assertEqual(wizard.calendar_type_combo.currentData(), "gregorian")
        self.assertFalse(wizard.calendar_settings_button.isEnabled())
        self.assertEqual(wizard.narration_tense_combo.currentData(), "past")
        self.assertEqual(
            wizard.narration_style_combo.currentData(),
            "third_person_omniscient",
        )
        self.assertFalse(wizard.music_enabled_checkbox.isChecked())
        self.assertFalse(wizard.narrator_enabled_checkbox.isChecked())
        self.assertEqual(wizard.music_volume_slider.value(), 10)
        self.assertEqual(wizard.tts_volume_slider.value(), 30)
        self.assertEqual(wizard.tts_voice_combo.currentData(), "am_echo")
        setup = wizard.build_setup()
        self.assertEqual(setup["skills"][0]["description"], "Skill 0 description.")
        self.assertFalse(setup["skills"][0]["requires_ai_invention"])
        self.assertEqual(setup["starter_items"][0]["name"], "Notebook")
        self.assertEqual(setup["starter_items"][0]["category"], "Tool")
        self.assertEqual(setup["starter_items"][0]["quantity"], 1)
        self.assertEqual(setup["starter_items"][0]["description"], "Case notes.")
        self.assertEqual(setup["starter_items"][0]["value_base_units"], 4)
        self.assertFalse(setup["audio"]["music_enabled"])
        self.assertFalse(setup["audio"]["narrator_enabled"])
        self.assertEqual(setup["audio"]["music_volume"], 10)
        self.assertEqual(setup["audio"]["tts_volume"], 30)
        self.assertEqual(setup["audio"]["tts_voice"], "am_echo")
        self.assertEqual(setup["narration"]["tense"], "past")
        self.assertEqual(setup["narration"]["style"], "third_person_omniscient")
        wizard.close()

    def test_new_game_wizard_uses_shared_calendar_settings_dialog_for_custom_calendar(self) -> None:
        QApplication.instance() or QApplication([])
        wizard = NewGameWizard()

        try:
            _set_combo_to_data(wizard.calendar_type_combo, "custom")
            self.assertTrue(wizard.calendar_settings_button.isEnabled())
            wizard._custom_calendar_settings = {
                **GREGORIAN_CALENDAR_SETTINGS,
                "days_per_week": 9,
                "day_names": ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
                "time_display": "24_hour",
            }

            setup = wizard.build_setup()

            self.assertEqual(setup["calendar"]["calendar_type"], "custom")
            self.assertFalse(setup["calendar"]["ai_generated"])
            self.assertEqual(setup["calendar"]["days_per_week"], 9)
            self.assertEqual(setup["calendar"]["day_names"][0], "A")
            self.assertEqual(setup["calendar"]["time_display"], "24_hour")
        finally:
            wizard.close()

    def test_new_game_wizard_allows_ai_generated_calendar(self) -> None:
        QApplication.instance() or QApplication([])
        wizard = NewGameWizard()

        try:
            _set_combo_to_data(wizard.calendar_type_combo, "ai_generated")
            setup = wizard.build_setup()

            self.assertFalse(wizard.calendar_settings_button.isEnabled())
            self.assertEqual(setup["calendar"]["calendar_type"], "ai_generated")
            self.assertTrue(setup["calendar"]["ai_generated"])
        finally:
            wizard.close()

    def test_new_game_wizard_inventory_currency_tables_add_and_remove_rows(self) -> None:
        QApplication.instance() or QApplication([])
        wizard = NewGameWizard()

        try:
            self.assertEqual(wizard.starter_items_table.rowCount(), 0)

            wizard._append_starter_item_row(
                {
                    "name": "Notebook",
                    "category": "Tool",
                    "quantity": 2,
                    "description": "Case notes.",
                    "value_base_units": 4,
                }
            )

            self.assertEqual(wizard.starter_items_table.rowCount(), 1)
            self.assertEqual(
                wizard.starter_items_table.selectionMode(),
                QTableWidget.SelectionMode.NoSelection,
            )
            self.assertIsInstance(wizard.starter_items_table.cellWidget(0, 0), QLineEdit)
            self.assertEqual(wizard.starter_items_table.cellWidget(0, 1).value(), 2)
            self.assertEqual(
                wizard.starter_items_table.cellWidget(0, 1).minimumWidth(),
                wizard.starter_items_table.cellWidget(0, 2).minimumWidth(),
            )
            self.assertEqual(
                wizard.starter_items_table.columnWidth(1),
                wizard.starter_items_table.columnWidth(4),
            )
            wizard.starter_items_table.cellWidget(0, 1).stepDown()
            self.assertEqual(wizard.starter_items_table.cellWidget(0, 1).value(), 1)
            wizard.starter_items_table.cellWidget(0, 1).stepUp()
            self.assertEqual(wizard.starter_items_table.cellWidget(0, 1).value(), 2)
            wizard.starter_items_table.cellWidget(0, 5).click()
            self.assertEqual(wizard.starter_items_table.rowCount(), 0)

            wizard._append_currency_row({"name": "Bit", "plural_name": "Bits", "value": 1})
            wizard._append_currency_row({"name": "Crown", "plural_name": "Crowns", "value": 12})

            self.assertEqual(wizard.currency_table.rowCount(), 2)
            self.assertEqual(
                wizard.currency_table.selectionMode(),
                QTableWidget.SelectionMode.NoSelection,
            )
            self.assertIsInstance(wizard.currency_table.cellWidget(1, 0), QLineEdit)
            self.assertFalse(wizard.currency_table.cellWidget(0, 2).isEnabled())
            self.assertTrue(wizard.currency_table.cellWidget(1, 2).isEnabled())
            self.assertEqual(
                wizard.currency_table.cellWidget(1, 2).minimumWidth(),
                wizard.currency_table.cellWidget(1, 0).minimumWidth(),
            )
            self.assertEqual(wizard.currency_table.columnWidth(2), 132)
            wizard.currency_table.cellWidget(1, 2).stepDown()
            self.assertEqual(wizard.currency_table.cellWidget(1, 2).value(), 11)
            wizard.currency_table.cellWidget(1, 2).stepUp()
            self.assertEqual(wizard.currency_table.cellWidget(1, 2).value(), 12)
            wizard.currency_table.cellWidget(0, 3).click()
            self.assertEqual(wizard.currency_table.rowCount(), 1)
            self.assertEqual(wizard.currency_table.cellWidget(0, 0).text(), "Crown")
            self.assertFalse(wizard.currency_table.cellWidget(0, 2).isEnabled())
            self.assertEqual(wizard.currency_table.cellWidget(0, 2).value(), 1)
        finally:
            wizard.close()

    def test_new_game_wizard_lightweight_mode_hides_narrator_controls(self) -> None:
        QApplication.instance() or QApplication([])
        wizard = NewGameWizard(
            tts_enabled=False,
            template_setup={
                "audio": {
                    "music_enabled": True,
                    "narrator_enabled": True,
                    "music_volume": 25,
                    "tts_volume": 90,
                    "tts_voice": "am_echo",
                },
            },
        )

        try:
            labels = [label.text() for label in wizard.findChildren(QLabel)]
            setup = wizard.build_setup()

            self.assertIsNone(wizard.narrator_enabled_checkbox)
            self.assertIsNone(wizard.tts_volume_slider)
            self.assertIsNone(wizard.tts_voice_combo)
            self.assertIsNone(wizard.sample_voice_button)
            self.assertNotIn("Narrator:", labels)
            self.assertNotIn("Narrator Volume:", labels)
            self.assertNotIn("Narrator Voice:", labels)
            self.assertFalse(setup["audio"]["narrator_enabled"])
            self.assertEqual(setup["audio"]["tts_volume"], 0)
            self.assertEqual(setup["audio"]["tts_voice"], DEFAULT_NARRATOR_VOICE)
        finally:
            wizard.close()

    def test_new_game_wizard_uses_app_audio_defaults(self) -> None:
        QApplication.instance() or QApplication([])
        wizard = NewGameWizard(
            audio_defaults={
                "music_enabled": False,
                "narrator_enabled": False,
                "music_volume": 8,
                "tts_volume": 22,
                "tts_voice": "am_echo",
            }
        )

        try:
            self.assertFalse(wizard.music_enabled_checkbox.isChecked())
            self.assertFalse(wizard.narrator_enabled_checkbox.isChecked())
            self.assertEqual(wizard.music_volume_slider.value(), 8)
            self.assertEqual(wizard.tts_volume_slider.value(), 22)
            self.assertEqual(wizard.tts_voice_combo.currentData(), "am_echo")
        finally:
            wizard.close()

    def test_new_game_wizard_sample_voice_uses_selected_voice_and_volume(self) -> None:
        QApplication.instance() or QApplication([])
        samples = []
        wizard = NewGameWizard(
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
            on_sample_voice=lambda voice, volume: samples.append((voice, volume)) or True,
        )

        try:
            _set_combo_to_data(wizard.tts_voice_combo, "am_echo")
            wizard.tts_volume_slider.setValue(44)
            wizard.sample_voice_button.click()

            self.assertEqual(samples, [("am_echo", 44)])
        finally:
            wizard.close()

    def test_new_game_wizard_light_theme_uses_readable_contrast(self) -> None:
        QApplication.instance() or QApplication([])
        apply_application_theme("Light")
        wizard = NewGameWizard()

        try:
            self.assertEqual(
                wizard.palette().color(QPalette.ColorRole.Window).name(),
                "#f5f7fb",
            )
            self.assertEqual(
                wizard.palette().color(QPalette.ColorRole.WindowText).name(),
                "#111827",
            )
            self.assertEqual(
                wizard.palette().color(QPalette.ColorRole.Text).name(),
                "#111827",
            )
            self.assertEqual(
                wizard.palette().color(QPalette.ColorRole.Base).name(),
                "#ffffff",
            )
            self.assertIn("QWizard#newGameWizard QLabel", wizard.styleSheet())
            self.assertIn("QWizard#newGameWizard QComboBox::down-arrow", wizard.styleSheet())
            self.assertIn("QWizard#newGameWizard QCheckBox::indicator", wizard.styleSheet())
            self.assertIn("QWizard#newGameWizard QSpinBox::up-button", wizard.styleSheet())
            self.assertIn("QWizard#newGameWizard QSpinBox::down-button", wizard.styleSheet())
            self.assertIn("color: #111827", wizard.styleSheet())
            self.assertIn("background-color: #ffffff", wizard.styleSheet())
            self.assertNotIn("color: #f3f4f6", wizard.styleSheet())
        finally:
            wizard.close()
            apply_application_theme("Light")

    def test_new_game_wizard_dark_theme_uses_readable_contrast(self) -> None:
        QApplication.instance() or QApplication([])
        apply_application_theme("Dark")
        wizard = NewGameWizard()

        try:
            self.assertEqual(
                wizard.palette().color(QPalette.ColorRole.Window).name(),
                "#202124",
            )
            self.assertEqual(
                wizard.palette().color(QPalette.ColorRole.WindowText).name(),
                "#f1f3f4",
            )
            self.assertEqual(
                wizard.palette().color(QPalette.ColorRole.Text).name(),
                "#f1f3f4",
            )
            self.assertEqual(
                wizard.palette().color(QPalette.ColorRole.Base).name(),
                "#121416",
            )
            self.assertIn("QWizard#newGameWizard QWidget", wizard.styleSheet())
            self.assertIn("QWizard#newGameWizard QComboBox::down-arrow", wizard.styleSheet())
            self.assertIn("QWizard#newGameWizard QCheckBox::indicator", wizard.styleSheet())
            self.assertIn("QWizard#newGameWizard QSpinBox::up-button", wizard.styleSheet())
            self.assertIn("QWizard#newGameWizard QSpinBox::down-button", wizard.styleSheet())
            self.assertIn("color: #f1f3f4", wizard.styleSheet())
            self.assertIn("background-color: #121416", wizard.styleSheet())
            self.assertNotIn("background-color: #ffffff", wizard.styleSheet())
        finally:
            wizard.close()
            apply_application_theme("Light")

    def test_new_game_wizard_skills_page_groups_levels_with_descriptions(self) -> None:
        QApplication.instance() or QApplication([])
        wizard = NewGameWizard()

        try:
            level_groups = [
                group.title()
                for group in wizard.findChildren(QGroupBox)
                if group.title().startswith("Level ")
            ]

            self.assertEqual(level_groups, ["Level 5", "Level 4", "Level 3", "Level 2", "Level 1"])
            self.assertEqual([entry[0] for entry in wizard.skill_inputs], [5, 4, 4, 3, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1, 1])
            self.assertEqual(len(wizard.skill_inputs[0]), 3)

            wizard.skill_inputs[0][1].setText("Smithing")
            wizard.skill_inputs[0][2].setText("Forge repair, tool-making, and metalwork.")
            setup = wizard.build_setup()

            self.assertEqual(setup["skills"][0]["name"], "Smithing")
            self.assertEqual(
                setup["skills"][0]["description"],
                "Forge repair, tool-making, and metalwork.",
            )
        finally:
            wizard.close()


if __name__ == "__main__":
    unittest.main()
