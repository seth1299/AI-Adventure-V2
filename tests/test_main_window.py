from __future__ import annotations

import logging
import os
import tempfile
import unittest
import importlib.util
from types import SimpleNamespace
from pathlib import Path
from typing import TypeVar, cast
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if importlib.util.find_spec("PySide6") is None:
    raise unittest.SkipTest("PySide6 is not installed in this test environment.")

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QWidget,
)

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.user_settings import load_app_settings
from ai_adventure.audio.voices import DEFAULT_NARRATOR_VOICE
from ai_adventure.audio.narration import NarrationPlayer
from ai_adventure.calendar_system import DEFAULT_CALENDAR_SETTINGS
from ai_adventure.combat import calculate_team_threat_levels
from ai_adventure.new_game_setup import GREGORIAN_CALENDAR_SETTINGS, normalize_new_game_setup
from ai_adventure.new_game_templates import load_new_game_templates
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.main_window import (
    AISettingsDialog,
    AlchemyNotebookScreen,
    CalendarScreen,
    CalendarSettingsDialog,
    CharacterScreen,
    CombatScreen,
    CustomVoiceDialog,
    GameShell,
    HistoryScreen,
    InventoryScreen,
    MainMenuScreen,
    MainMenuSettingsDialog,
    MainWindow,
    NewGameTemplateManagerDialog,
    NewGameWizard,
    NPC_TURN_DELAY_MS,
    SettingsScreen,
    StoryScreen,
    TTSSettingsDialog,
    TravelScreen,
    _next_available_save_title,
    _NoWheelComboBox,
    _NoWheelSpinBox,
    _player_command_markdown,
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
        self.speed = 1.0
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

    def set_speed(self, speed):
        self.speed = speed

    def play_sample(self, *, voice=None, volume=None, speed=None, text=""):
        self.samples.append(
            {"voice": voice, "volume": volume, "speed": speed, "text": text}
        )
        return True

    def play_chunk(self, text: str) -> None:
        assert self.on_chunk_start is not None
        self.on_chunk_start(text)

    def complete(self) -> None:
        assert self.on_complete is not None
        self.on_complete()


WidgetT = TypeVar("WidgetT", bound=QObject)


def _ensure_qt_application() -> QApplication:
    """Returns a typed QApplication instance for Qt widget tests."""

    application = QApplication.instance()

    if isinstance(application, QApplication):
        return application

    return QApplication([])


def _require_widget(widget: QObject | None, widget_type: type[WidgetT]) -> WidgetT:
    """Narrows a nullable table or form widget for a test assertion."""

    assert isinstance(widget, widget_type)
    return widget


ValueT = TypeVar("ValueT")


def _require(value: ValueT | None) -> ValueT:
    """Narrows an optional test fixture lookup after asserting its presence."""

    assert value is not None
    return value


def _table_cell(
    table: QTableWidget,
    row: int,
    column: int,
    widget_type: type[WidgetT],
) -> WidgetT:
    """Returns a typed table-cell editor required by a populated test row."""

    return _require_widget(table.cellWidget(row, column), widget_type)


class MainWindowTests(unittest.TestCase):
    def test_player_command_markdown_uses_normal_speaker_label(self) -> None:
        formatted = _player_command_markdown("Pocket the locket.")

        self.assertEqual(formatted, "**You:** Pocket the locket.")
        self.assertFalse(formatted.startswith(">"))

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

            _ensure_qt_application()

            logger = logging.getLogger("ai_adventure.ui.main_window")

            with self.assertNoLogs(logger, level="ERROR"):
                window = MainWindow(app_paths=app_paths)
                window.return_to_menu()
                tab_names = [
                    window.game_shell.tabs.tabText(index)
                    for index in range(window.game_shell.tabs.count())
                ]
                self.assertIn("Character", tab_names)
                self.assertNotIn("World", tab_names)
                self.assertIn("Travel", tab_names)
                self.assertNotIn("Active Tasks", tab_names)
                self.assertEqual(
                    window.game_shell.calendar_screen.views.tabText(2),
                    "Tasks & Deadlines",
                )
                self.assertIn("Crafting", tab_names)
                self.assertFalse(window.windowIcon().isNull())
                npc_headers = [
                    _require(window.game_shell.npcs_screen.table.horizontalHeaderItem(index)).text()
                    for index in range(window.game_shell.npcs_screen.table.columnCount())
                ]
                task_headers = [
                    _require(window.game_shell.active_tasks_screen.table.horizontalHeaderItem(index)).text()
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
                    self.assertTrue(table.wordWrap())
                    self.assertTrue(table.horizontalHeader().sectionsClickable())
                    self.assertTrue(table.horizontalHeader().isSortIndicatorShown())

                self.assertEqual(
                    window.game_shell.inventory_screen.table.horizontalHeader().sectionResizeMode(4),
                    QHeaderView.ResizeMode.Stretch,
                )
                self.assertEqual(
                    window.game_shell.npcs_screen.table.horizontalHeader().sectionResizeMode(2),
                    QHeaderView.ResizeMode.Stretch,
                )
                self.assertEqual(
                    window.game_shell.active_tasks_screen.table.horizontalHeader().sectionResizeMode(3),
                    QHeaderView.ResizeMode.Stretch,
                )
                self.assertEqual(
                    window.game_shell.skills_screen.skills_table.horizontalHeader().sectionResizeMode(2),
                    QHeaderView.ResizeMode.ResizeToContents,
                )
                self.assertTrue(window.game_shell.alchemy_screen.reagent_table.wordWrap())
                self.assertTrue(window.game_shell.alchemy_screen.recipe_table.wordWrap())

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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Journal Toggle Test")
            screen = HistoryScreen()
            screen.set_repository(repository)

            self.assertFalse(screen.share_with_ai_checkbox.isChecked())

            screen.journal_input.setPlainText("Tell the AI this theory.")
            screen.share_with_ai_checkbox.setChecked(True)

            screen._autosave_journal()

            self.assertEqual(repository.get_journal_notes(), "Tell the AI this theory.")
            self.assertTrue(repository.get_journal_share_with_ai())
            screen.close()

    def test_journal_screen_autosaves_after_typing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Reveal Test")
            story_text = "First sentence. Second sentence.\n\n- Take action."
            repository.append_history("story", story_text)
            latest_story = repository.list_history()[-1]
            narration_player = FakeNarrationPlayer()
            screen = StoryScreen(narration_player=cast(NarrationPlayer, narration_player))
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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Reveal Reset Test")
            story_text = "The opening scene is saved."
            repository.append_history("story", story_text)
            latest_story = repository.list_history()[-1]
            narration_player = FakeNarrationPlayer()
            screen = StoryScreen(narration_player=cast(NarrationPlayer, narration_player))
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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Narrate Saved Test")
            repository.append_history("story", "The introduction is already in history.")
            narration_player = FakeNarrationPlayer()
            screen = StoryScreen(narration_player=cast(NarrationPlayer, narration_player))
            screen.set_repository(repository)

            screen.narrate_latest_story()

            self.assertIn("The introduction is already in history.", screen.story_output.toPlainText())
            screen.close()

    def test_story_submit_displays_player_text_and_busy_state_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Continue Test")
            repository.append_history("player", "Open the book and search for clues.")
            repository.append_history("story", "The brittle cover opens onto a map fragment.")
            repository.upsert_gm_secret(
                secret_id="map_marks_hidden_vault",
                title="Hidden Vault",
                details="The map's faded compass rose marks the concealed vault.",
                reveal_condition="The player restores the missing corner.",
                related_locations=["Old Archive"],
            )
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
            self.assertEqual(
                started_packets[0]["state"]["gm_secrets"]["active"][0]["secret_id"],
                "map_marks_hidden_vault",
            )
            self.assertEqual(repository.list_history(), history_before)
            self.assertFalse(screen.player_input.isEnabled())
            self.assertFalse(screen.continue_button.isEnabled())
            screen.close()

    def test_skill_check_plan_resolves_before_story_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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
                        ],
                        relevant_tags=["exploration", "skill", "uncertainty"],
                    )
                )

            resolved_checks = started_packets[0]["state"]["skills"]["resolved_checks_this_turn"]

            self.assertEqual(applied_events[0]["type"], "SkillCheckRequestedEvent")
            self.assertEqual(applied_events[0]["payload"]["reason"], "Searching a dangerous cliff.")
            self.assertEqual(resolved_checks[0]["skill_name"], "Foraging")
            self.assertEqual(resolved_checks[0]["outcome"], "failure")
            self.assertEqual(resolved_checks[0]["roll"], 2)
            self.assertEqual(screen._pending_skill_check_event_results[0].payload["outcome"], "failure")
            self.assertEqual(
                started_packets[0]["selection"]["tags"],
                ["exploration", "skill", "story", "uncertainty"],
            )
            screen.close()

    def test_story_result_keeps_input_disabled_until_tts_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "TTS Busy Test")
            narration_player = FakeNarrationPlayer()
            screen = StoryScreen(narration_player=cast(NarrationPlayer, narration_player))
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
            _ensure_qt_application()
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

    def test_story_screen_blocks_input_during_active_combat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Combat Lock Test")
            repository.set_combat_state(
                {
                    "active": True,
                    "round": 1,
                    "turn_index": 0,
                    "combatants": [
                        {
                            "id": "player",
                            "name": "Player",
                            "team": "party",
                            "current_health": 20,
                            "max_health": 20,
                            "armor_rating": 10,
                            "damage": "1d4",
                        },
                        {
                            "id": "enemy-1-wolf",
                            "name": "Wolf",
                            "team": "enemy",
                            "current_health": 4,
                            "max_health": 4,
                            "armor_rating": 10,
                            "damage": "1d6",
                        },
                    ],
                }
            )
            screen = StoryScreen()
            screen.set_repository(repository)

            self.assertFalse(screen.player_input.isEnabled())
            self.assertFalse(screen.submit_button.isEnabled())
            self.assertEqual(screen.player_input.placeholderText(), "Combat is active...")
            self.assertIn("Resolve the active combat", screen.player_input.toolTip())

            combat_state = repository.get_combat_state()
            combat_state["active"] = False
            repository.set_combat_state(combat_state)
            screen.refresh()

            self.assertTrue(screen.player_input.isEnabled())
            self.assertTrue(screen.submit_button.isEnabled())
            self.assertEqual(screen.player_input.placeholderText(), "Enter a player action...")
            screen.close()

    def test_character_sheet_equips_weapon_armor_and_saves_stats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Character Sheet Test")
            repository.add_inventory_item(
                "Longsword",
                "Weapon",
                1,
                "A balanced one-handed blade.",
                15,
                metadata={
                    "item_type": "Weapon",
                    "weapon_hands": "one-handed",
                    "damage": "1d8",
                },
            )
            repository.add_inventory_item(
                "Leather Armor",
                "Armor",
                1,
                "Flexible armor that protects the head, torso, arms, and legs.",
                20,
                metadata={
                    "item_type": "Armor",
                    "covers_body_parts": ["Head", "Torso", "Arms", "Legs"],
                    "armor_rating": 2,
                },
            )
            repository.add_inventory_item(
                "Tower Shield",
                "Armor",
                1,
                "A broad shield carried in the off hand.",
                10,
                metadata={
                    "item_type": "Armor",
                    "covers_body_parts": ["Off Hand"],
                    "armor_rating": 2,
                },
            )
            screen = CharacterScreen(playtesting_tools=True)
            screen.set_repository(repository)

            _set_combo_to_data(screen.equipment_combos["Main Hand"], "Longsword")
            _set_combo_to_data(screen.equipment_combos["Off Hand"], "Tower Shield")
            _set_combo_to_data(screen.equipment_combos["Arms"], "Leather Armor")
            screen.health_max_input.setValue(20)
            screen.health_current_input.setValue(15)

            for covered_slot in ["Head", "Torso", "Arms", "Legs"]:
                self.assertEqual(
                    screen.equipment_combos[covered_slot].currentData(),
                    "Leather Armor",
                )

            with patch("ai_adventure.ui.main_window.QMessageBox.information"):
                screen._save_character()

            equipment = repository.get_player_equipment()

            self.assertEqual(equipment["Main Hand"], "Longsword")
            self.assertEqual(equipment["Off Hand"], "Tower Shield")
            self.assertEqual(equipment["Head"], "Leather Armor")
            self.assertEqual(equipment["Torso"], "Leather Armor")
            self.assertEqual(equipment["Arms"], "Leather Armor")
            self.assertEqual(equipment["Legs"], "Leather Armor")
            self.assertEqual(repository.get_setting("player.health_current"), 15)
            self.assertEqual(repository.get_setting("player.health_max"), 20)
            self.assertEqual(repository.get_setting("player.armor_rating"), 14)
            self.assertEqual(_require(screen.armor_rating_label).text(), "14")
            self.assertEqual(_require(screen.weapon_damage_label).text(), "1d8")
            screen.close()

    def test_character_sheet_unequips_multi_slot_armor_from_every_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Multi Slot Armor Test",
            )
            repository.add_inventory_item(
                "Leather Armor",
                "Armor",
                1,
                "Flexible armor covering several body regions.",
                20,
                metadata={
                    "item_type": "Armor",
                    "covers_body_parts": ["Head", "Torso", "Arms", "Legs"],
                    "armor_rating": 2,
                },
            )
            screen = CharacterScreen(playtesting_tools=True)
            screen.set_repository(repository)

            _set_combo_to_data(
                screen.equipment_combos["Torso"],
                "Leather Armor",
            )
            _set_combo_to_data(screen.equipment_combos["Head"], "")

            for covered_slot in ["Head", "Torso", "Arms", "Legs"]:
                self.assertEqual(
                    screen.equipment_combos[covered_slot].currentData(),
                    "",
                )

            screen.close()

    def test_character_sheet_hides_single_equipped_weapon_from_other_hand(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Single Dagger Equipment Test",
            )
            screen = CharacterScreen(playtesting_tools=True)
            screen.set_repository(repository)

            _set_combo_to_data(
                screen.equipment_combos["Main Hand"],
                "Iron Dagger",
            )

            off_hand_values = {
                str(screen.equipment_combos["Off Hand"].itemData(index) or "")
                for index in range(screen.equipment_combos["Off Hand"].count())
            }
            dagger = next(
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Iron Dagger"
            )

            self.assertNotIn("Iron Dagger", off_hand_values)
            self.assertTrue(dagger["equipped"])
            self.assertEqual(
                repository.get_player_equipment()["Main Hand"],
                "Iron Dagger",
            )

            _set_combo_to_data(screen.equipment_combos["Main Hand"], "")

            off_hand_values = {
                str(screen.equipment_combos["Off Hand"].itemData(index) or "")
                for index in range(screen.equipment_combos["Off Hand"].count())
            }
            dagger = next(
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Iron Dagger"
            )

            self.assertIn("Iron Dagger", off_hand_values)
            self.assertFalse(dagger["equipped"])
            screen.close()

    def test_character_sheet_rejects_off_hand_when_main_weapon_is_two_handed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Two Handed Test")
            repository.add_inventory_item(
                "Greatsword",
                "Weapon",
                1,
                "A two-handed weapon.",
                25,
                metadata={
                    "item_type": "Weapon",
                    "weapon_hands": "two-handed",
                    "damage": "2d6",
                },
            )
            repository.add_inventory_item(
                "Round Shield",
                "Armor",
                1,
                "A shield carried in the off hand.",
                8,
                metadata={
                    "item_type": "Armor",
                    "covers_body_parts": ["Off Hand"],
                    "armor_rating": 2,
                },
            )
            screen = CharacterScreen(playtesting_tools=True)
            screen.set_repository(repository)

            _set_combo_to_data(screen.equipment_combos["Off Hand"], "Round Shield")
            _set_combo_to_data(screen.equipment_combos["Main Hand"], "Greatsword")

            with patch("ai_adventure.ui.main_window.QMessageBox.information"):
                screen._save_character()

            equipment = repository.get_player_equipment()

            self.assertEqual(equipment["Main Hand"], "Greatsword")
            self.assertEqual(equipment["Off Hand"], "")
            self.assertEqual(repository.get_setting("player.armor_rating"), 10)
            self.assertEqual(_require(screen.weapon_damage_label).text(), "2d6")
            screen.close()

    def test_combat_screen_resolves_victory_and_grants_loot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Combat UI Test")
            repository.set_setting("player.initiative_bonus", 99)
            screen = CombatScreen(playtesting_tools=True)
            screen.set_repository(repository)
            screen.name_input.setText("Wolf")
            screen.health_input.setValue(1)
            screen.armor_input.setValue(1)
            screen.initiative_input.setValue(-99)
            screen.damage_input.setText("1d1")
            screen.loot_input.setText("Wolf Fang")

            screen._start_combat()

            self.assertTrue(repository.is_combat_active())
            self.assertTrue(screen.attack_button.isEnabled())

            with patch("ai_adventure.ui.main_window.random.randint", return_value=20):
                screen._resolve_current_turn()

            items = repository.list_inventory_items()
            combat_state = repository.get_combat_state()

            self.assertFalse(combat_state["active"])
            self.assertEqual(combat_state["combatants"], [])
            self.assertEqual(screen.combatants_table.rowCount(), 0)
            self.assertIn("Wolf Fang", {item["name"] for item in items})
            self.assertIn("Combat resolved: victory.", "\n".join(combat_state["log"]))
            self.assertEqual(repository.get_setting("player.health_current"), 20)
            screen.close()

    def test_combat_screen_applies_saved_to_hit_bonus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "To Hit Bonus Test",
            )
            repository.set_setting("player.initiative_bonus", 99)
            screen = CombatScreen(playtesting_tools=True)
            screen.set_repository(repository)
            screen.name_input.setText("Guard")
            screen.health_input.setValue(8)
            screen.armor_input.setValue(10)
            screen.initiative_input.setValue(-99)
            screen._start_combat()

            with patch(
                "ai_adventure.ui.main_window.random.randint",
                side_effect=[8, 1],
            ):
                screen._resolve_current_turn()

            combat_state = repository.get_combat_state()
            player = next(
                combatant
                for combatant in combat_state["combatants"]
                if combatant["id"] == "player"
            )
            enemy = next(
                combatant
                for combatant in combat_state["combatants"]
                if combatant["team"] == "enemy"
            )
            self.assertEqual(player["to_hit_bonus"], 2)
            self.assertEqual(enemy["current_health"], 7)
            self.assertIn("8+2=10 vs AR 10", "\n".join(combat_state["log"]))
            screen.close()

    def test_manual_combat_resolution_clears_battlefield_and_keeps_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Clear Battlefield Test",
            )
            screen = CombatScreen(playtesting_tools=True)
            screen.set_repository(repository)
            screen.name_input.setText("Bandit")
            screen._start_combat()
            screen._resolve_combat_manually()

            combat_state = repository.get_combat_state()
            self.assertFalse(combat_state["active"])
            self.assertEqual(combat_state["combatants"], [])
            self.assertEqual(screen.combatants_table.rowCount(), 0)
            self.assertEqual(screen.adjust_target_combo.count(), 0)
            self.assertIn("Combat is marked resolved.", combat_state["log"])
            screen.close()

    def test_duplicate_combatants_have_unique_table_and_target_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Duplicate Combatants Test",
            )
            repository.set_setting("player.initiative_bonus", 99)
            screen = CombatScreen(playtesting_tools=True)
            screen.set_repository(repository)
            screen.name_input.setText("Bandit")
            screen.initiative_input.setValue(-99)
            screen._start_combat()
            screen._add_combatant()

            state = repository.get_combat_state()
            bandits = [
                combatant
                for combatant in state["combatants"]
                if combatant["name"] == "Bandit"
            ]
            table_names = {
                _require(screen.combatants_table.item(row, 1)).text()
                for row in range(screen.combatants_table.rowCount())
            }
            target_names = {
                screen.target_combo.itemText(index)
                for index in range(screen.target_combo.count())
            }

            self.assertEqual(len({combatant["id"] for combatant in bandits}), 2)
            self.assertEqual(
                {combatant["display_name"] for combatant in bandits},
                {"Bandit (1)", "Bandit (2)"},
            )
            self.assertIn("Bandit (1)", table_names)
            self.assertIn("Bandit (2)", table_names)
            self.assertIn("Bandit (1) (enemy)", target_names)
            self.assertIn("Bandit (2) (enemy)", target_names)
            screen.close()

    def test_combat_table_replaces_spatial_columns_with_threat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Threat Table Test",
            )
            repository.set_setting("player.initiative_bonus", 99)
            screen = CombatScreen(playtesting_tools=True)
            screen.set_repository(repository)
            screen.name_input.setText("Bandit")
            screen.initiative_input.setValue(-99)
            screen._start_combat()

            headers = [
                _require(screen.combatants_table.horizontalHeaderItem(column)).text()
                for column in range(screen.combatants_table.columnCount())
            ]
            threats = [
                _require(screen.combatants_table.item(row, 7)).text()
                for row in range(screen.combatants_table.rowCount())
            ]

            self.assertIn("Threat", headers)
            self.assertNotIn("Range", headers)
            self.assertNotIn("Distance", headers)
            self.assertNotIn("Move", headers)
            self.assertEqual(threats, ["100%", "100%"])
            screen.close()

    def test_npc_turns_schedule_automatically_with_reading_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Automatic NPC Turns Test",
            )
            repository.set_combat_state(
                {
                    "active": True,
                    "round": 1,
                    "turn_index": 0,
                    "combatants": [
                        {
                            "id": "enemy-1",
                            "name": "Goblin One",
                            "team": "enemy",
                            "current_health": 8,
                            "max_health": 8,
                            "armor_rating": 10,
                            "to_hit_bonus": 0,
                            "personality": "balanced",
                            "damage": "1d4",
                        },
                        {
                            "id": "enemy-2",
                            "name": "Goblin Two",
                            "team": "enemy",
                            "current_health": 8,
                            "max_health": 8,
                            "armor_rating": 10,
                            "to_hit_bonus": 0,
                            "personality": "balanced",
                            "damage": "1d4",
                        },
                        {
                            "id": "player",
                            "name": "Player",
                            "team": "party",
                            "current_health": 20,
                            "max_health": 20,
                            "armor_rating": 10,
                            "damage": "1d6",
                        },
                    ],
                    "log": ["Combat begins."],
                }
            )
            screen = CombatScreen(playtesting_tools=True)

            try:
                screen.set_repository(repository)

                self.assertTrue(screen.npc_turn_timer.isActive())
                self.assertEqual(
                    screen.npc_turn_timer.interval(),
                    NPC_TURN_DELAY_MS,
                )
                self.assertFalse(screen.attack_button.isEnabled())
                self.assertFalse(screen.reload_button.isEnabled())
                self.assertFalse(screen.end_turn_button.isEnabled())
                self.assertTrue(screen.attack_button.isHidden())
                self.assertTrue(screen.reload_button.isHidden())
                self.assertTrue(screen.end_turn_button.isHidden())
                self.assertEqual(
                    screen.attack_button.text(),
                    "Attack / Resolve Turn",
                )
                self.assertIn(
                    "acting automatically in 2 seconds",
                    screen.status_label.text(),
                )

                with patch(
                    "ai_adventure.ui.main_window.random.randint",
                    side_effect=[1, 1],
                ):
                    screen._resolve_scheduled_npc_turn()

                state = repository.get_combat_state()
                current_actor = state["combatants"][state["turn_index"]]
                self.assertEqual(current_actor["id"], "enemy-2")
                self.assertTrue(screen.npc_turn_timer.isActive())
                self.assertEqual(
                    screen._scheduled_npc_actor_id,
                    "enemy-2",
                )
                self.assertIn(
                    "Goblin One targets Player",
                    "\n".join(state["log"]),
                )
            finally:
                screen._cancel_scheduled_npc_turn()
                screen.close()

    def test_firearm_reload_consumes_inventory_and_attack_consumes_clip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Firearm Combat Test",
            )
            repository.add_inventory_item(
                "Service Pistol",
                "Weapon",
                1,
                "A compact sidearm.",
                metadata={
                    "item_type": "Weapon",
                    "weapon_hands": "one-handed",
                    "damage": "1d4",
                    "attack_skill": "Ranged",
                    "attack_range_feet": 60,
                    "ammunition_type_required": "9mm Round",
                    "clip_size": 6,
                    "bullets_per_attack": 2,
                },
            )
            repository.add_inventory_item(
                "9mm Box",
                "Ammunition",
                10,
                "Loose cartridges.",
                metadata={
                    "item_type": "Ammunition",
                    "ammunition_type": "9mm Round",
                },
            )
            repository.set_player_equipment({"Main Hand": "Service Pistol"})
            repository.set_setting("player.weapon_clip_ammo", {"service pistol": 0})
            repository.set_setting("player.initiative_bonus", 99)
            screen = CombatScreen(playtesting_tools=True)
            screen.set_repository(repository)
            screen.name_input.setText("Bandit")
            screen.initiative_input.setValue(-99)
            screen._start_combat()
            screen._reload_current_weapon()

            reloaded_state = repository.get_combat_state()
            player = next(
                combatant
                for combatant in reloaded_state["combatants"]
                if combatant["id"] == "player"
            )
            ammunition = next(
                item
                for item in repository.list_inventory_items()
                if item["name"] == "9mm Box"
            )
            self.assertEqual(player["clip_ammo"], 6)
            self.assertEqual(ammunition["quantity"], 4)

            player_index = next(
                index
                for index, combatant in enumerate(reloaded_state["combatants"])
                if combatant["id"] == "player"
            )
            reloaded_state["turn_index"] = player_index
            repository.set_combat_state(reloaded_state)
            screen.refresh()

            with patch(
                "ai_adventure.ui.main_window.random.randint",
                side_effect=[10, 1],
            ):
                screen._resolve_current_turn()

            attacked_state = repository.get_combat_state()
            attacked_player = next(
                combatant
                for combatant in attacked_state["combatants"]
                if combatant["id"] == "player"
            )
            self.assertEqual(attacked_player["clip_ammo"], 4)
            self.assertEqual(
                repository.get_setting("player.weapon_clip_ammo")[
                    "service pistol"
                ],
                4,
            )
            screen.close()

    def test_intelligent_npc_targeting_balances_hit_chance_and_wounds(self) -> None:
        _ensure_qt_application()
        screen = CombatScreen(playtesting_tools=True)
        actor = {
            "id": "enemy",
            "team": "enemy",
            "to_hit_bonus": 4,
            "personality": "intelligent",
        }
        healthy_easy_target = {
            "id": "easy",
            "team": "party",
            "current_health": 18,
            "max_health": 20,
            "armor_rating": 10,
            "defeated": False,
        }
        wounded_hard_target = {
            "id": "wounded",
            "team": "party",
            "current_health": 2,
            "max_health": 20,
            "armor_rating": 14,
            "defeated": False,
        }

        try:
            selected = screen._npc_target_for_actor(
                actor,
                [actor, healthy_easy_target, wounded_hard_target],
            )
            self.assertIs(selected, wounded_hard_target)
        finally:
            screen.close()

    def test_non_intelligent_enemies_and_allies_use_opponent_threat(self) -> None:
        _ensure_qt_application()
        screen = CombatScreen(playtesting_tools=True)
        enemy_actor = {
            "id": "enemy-actor",
            "team": "enemy",
            "personality": "aggressive",
            "defeated": False,
        }
        ally_actor = {
            "id": "ally-actor",
            "team": "party",
            "personality": "cautious",
            "defeated": False,
        }
        low_party_threat = {
            "id": "party-low",
            "team": "party",
            "current_health": 8,
            "max_health": 8,
            "armor_rating": 8,
            "damage": "1d4",
            "defeated": False,
        }
        high_party_threat = {
            "id": "party-high",
            "team": "party",
            "current_health": 30,
            "max_health": 30,
            "armor_rating": 18,
            "damage": "2d8",
            "defeated": False,
        }
        low_enemy_threat = {
            "id": "enemy-low",
            "team": "enemy",
            "current_health": 6,
            "max_health": 6,
            "armor_rating": 8,
            "damage": "1d4",
            "defeated": False,
        }
        high_enemy_threat = {
            "id": "enemy-high",
            "team": "enemy",
            "current_health": 28,
            "max_health": 28,
            "armor_rating": 17,
            "damage": "2d10",
            "defeated": False,
        }

        try:
            combatants = [
                enemy_actor,
                ally_actor,
                low_party_threat,
                high_party_threat,
                low_enemy_threat,
                high_enemy_threat,
            ]
            party_low_percent = calculate_team_threat_levels(
                combatants,
                "party",
            )["party-low"]
            enemy_low_percent = calculate_team_threat_levels(
                combatants,
                "enemy",
            )["enemy-low"]

            with patch(
                "ai_adventure.ui.main_window.random.randint",
                return_value=party_low_percent + 1,
            ):
                enemy_target = screen._npc_target_for_actor(
                    enemy_actor,
                    combatants,
                )
            with patch(
                "ai_adventure.ui.main_window.random.randint",
                return_value=enemy_low_percent + 1,
            ):
                ally_target = screen._npc_target_for_actor(
                    ally_actor,
                    combatants,
                )

            self.assertIs(enemy_target, high_party_threat)
            self.assertIs(ally_target, high_enemy_threat)
        finally:
            screen.close()

    def test_manual_character_and_combat_editors_are_playtesting_only(self) -> None:
        _ensure_qt_application()
        character = CharacterScreen(playtesting_tools=False)
        combat = CombatScreen(playtesting_tools=False)

        try:
            self.assertFalse(character.health_current_input.isEnabled())
            self.assertFalse(character.health_max_input.isEnabled())
            self.assertFalse(combat.add_group.isVisible())
            self.assertFalse(combat.adjust_group.isVisible())
            self.assertFalse(combat.resolve_button.isVisible())
        finally:
            character.close()
            combat.close()

    def test_character_screen_autosaves_text_changes_and_omits_body_ascii_art(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Character Autosave Test",
            )
            screen = CharacterScreen(playtesting_tools=True)
            screen.set_repository(repository)

            try:
                self.assertFalse(hasattr(screen, "body_map_label"))

                screen.name_input.setText("Mira Stone")
                screen.name_input.editingFinished.emit()

                screen.notes_input.setPlainText("Keeps a private map.")
                screen.eventFilter(
                    screen.notes_input,
                    QEvent(QEvent.Type.FocusOut),
                )
                QApplication.processEvents()

                self.assertEqual(repository.get_setting("player_name"), "Mira Stone")
                self.assertEqual(
                    repository.get_setting("player.notes"),
                    "Keeps a private map.",
                )
            finally:
                screen.close()

    def test_playtesting_shell_only_exposes_manual_test_tabs(self) -> None:
        _ensure_qt_application()
        shell = GameShell(
            on_return_to_menu=lambda: None,
            playtesting_tools=True,
            ai_enabled=False,
            tts_enabled=False,
        )

        try:
            tab_names = [
                shell.tabs.tabText(index)
                for index in range(shell.tabs.count())
            ]
            self.assertEqual(
                tab_names,
                ["Character", "Calendar", "Inventory", "Combat", "Settings"],
            )
            self.assertFalse(shell.calendar_screen.settings_button.isHidden())
            self.assertTrue(shell.character_screen.health_current_input.isEnabled())
            self.assertFalse(shell.combat_screen.add_group.isHidden())
        finally:
            shell.close()

    def test_playtesting_inventory_editor_adds_weapon_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Inventory Editor Test",
            )
            screen = InventoryScreen(playtesting_tools=True)
            screen.set_repository(repository)
            screen._clear_item_editor()
            screen.item_name_input.setText("Test Greatsword")
            _set_combo_to_data(screen.item_type_combo, "Weapon")
            _set_combo_to_data(screen.weapon_hands_combo, "two-handed")
            screen.weapon_damage_input.setText("2d6")
            screen.weapon_attack_skill_input.setText("Melee")
            screen.weapon_range_input.setValue(10)
            screen._save_playtesting_item()

            item = next(
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Test Greatsword"
            )
            self.assertEqual(item["metadata"]["item_type"], "Weapon")
            self.assertEqual(item["metadata"]["weapon_hands"], "two-handed")
            self.assertEqual(item["metadata"]["damage"], "2d6")
            self.assertEqual(item["metadata"]["attack_skill"], "Melee")
            self.assertEqual(item["metadata"]["attack_range_feet"], 10)
            screen.close()

    def test_playtesting_inventory_editor_adds_ammunition_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Ammunition Editor Test",
            )
            screen = InventoryScreen(playtesting_tools=True)
            screen.set_repository(repository)
            screen._clear_item_editor()
            screen.item_name_input.setText("Rifle Cartridge Box")
            _set_combo_to_data(screen.item_type_combo, "Ammunition")
            screen.item_quantity_input.setValue(30)
            screen.ammunition_type_name_input.setText("Rifle Cartridge")
            screen._save_playtesting_item()

            item = next(
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Rifle Cartridge Box"
            )
            self.assertEqual(item["metadata"]["item_type"], "Ammunition")
            self.assertEqual(
                item["metadata"]["ammunition_type"],
                "Rifle Cartridge",
            )
            self.assertEqual(item["quantity"], 30)
            screen.close()

    def test_travel_screen_displays_calculated_estimate_and_submits_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Travel Screen Test")
            repository.set_state_value("location", "Canal Gate")
            repository.set_travel_locations(
                [
                    {
                        "name": "Canal Gate",
                        "description": "The city's eastern entrance.",
                        "x_miles": 0,
                        "y_miles": 0,
                    },
                    {
                        "name": "North Lock",
                        "description": "A guarded lock beyond the warehouses.",
                        "x_miles": 3,
                        "y_miles": 4,
                        "terrain": "Cobblestone",
                        "travel_notes": "The gate closes after dark.",
                    },
                ]
            )
            submitted: list[tuple[dict[str, object], str]] = []
            screen = TravelScreen(
                on_travel_requested=lambda destination, context: (
                    submitted.append((destination, context)) or True
                )
            )
            screen.set_repository(repository)

            self.assertEqual(
                screen.location_list.currentItem().text(),
                "Canal Gate (Currently here)",
            )
            self.assertFalse(screen.travel_button.isEnabled())

            for row in range(screen.location_list.count()):
                item = screen.location_list.item(row)
                location = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(location, dict) and location.get("name") == "North Lock":
                    screen.location_list.setCurrentRow(row)
                    break

            self.assertIn("5.0 miles", screen.details_output.toPlainText())
            self.assertIn("About 1 hour 40 minutes", screen.details_output.toPlainText())
            self.assertTrue(screen.travel_button.isEnabled())

            screen.travel_context_input.setPlainText("I keep to the lit road.")
            screen._request_travel()

            self.assertEqual(submitted[0][0]["name"], "North Lock")
            self.assertEqual(submitted[0][1], "I keep to the lit road.")
            self.assertEqual(screen.travel_context_input.toPlainText(), "")
            screen.close()

    def test_story_travel_request_sends_calculated_itinerary_to_gemini_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Travel Context Test")
            repository.set_state_value("location", "Canal Gate")
            repository.set_travel_locations(
                [
                    {"name": "Canal Gate", "x_miles": 0, "y_miles": 0},
                    {
                        "name": "North Lock",
                        "description": "A guarded lock.",
                        "x_miles": 3,
                        "y_miles": 4,
                        "terrain": "Cobblestone",
                        "travel_notes": "The gate closes after dark.",
                    },
                ]
            )
            screen = StoryScreen()
            screen.set_repository(repository)

            with patch.object(screen, "_start_skill_check_planning_request") as request:
                submitted = screen.submit_travel_request(
                    _require(repository.find_travel_location("North Lock")),
                    "I keep to the lit road.",
                )

            self.assertTrue(submitted)
            packet = request.call_args.args[0]
            self.assertTrue(packet["travel_request"]["active"])
            self.assertEqual(packet["travel_request"]["destination"]["name"], "North Lock")
            self.assertEqual(packet["travel_request"]["estimate"]["distance_miles"], 5.0)
            self.assertEqual(packet["travel_request"]["estimate"]["estimated_minutes"], 100)
            self.assertEqual(packet["travel_request"]["player_context"], "I keep to the lit road.")
            self.assertIn("Travel toward North Lock.", repository.list_history()[-1]["content"])
            screen.close()

    def test_ai_new_game_state_sets_currency_balance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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
                    gm_secrets=[
                        {
                            "secret_id": "treasurer_forged_ledger",
                            "title": "Forged Ledger",
                            "details": "The treasurer forged the canal tax ledger.",
                            "reveal_condition": "The player compares both seal impressions.",
                            "related_npc_ids": ["treasurer"],
                            "related_locations": ["Counting House"],
                            "status": "active",
                        }
                    ],
                ),
            )

            self.assertEqual(repository.get_state_value("currency.balance"), "37")
            self.assertEqual(
                repository.list_gm_secrets(active_only=True)[0]["secret_id"],
                "treasurer_forged_ledger",
            )
            window.close()

    def test_ai_new_game_state_persists_starting_crafting_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            setup = normalize_new_game_setup(
                {
                    "title": "Crafting Knowledge",
                    "currency_denominations": [
                        {"name": "Credit", "plural_name": "Credits", "value": 1}
                    ],
                }
            )
            repository = SaveRepository.create_new_save(
                temp_path,
                "Crafting Knowledge",
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
                    locations=[],
                    starting_calendar={},
                    start_weather="",
                    finalized_starting_currency_balance_base_units=None,
                    finalized_currency_denominations=[],
                    finalized_currency_description="",
                    selected_genre="",
                    finalized_character={},
                    finalized_skills=[],
                    finalized_starter_items=[],
                    known_crafting_items=[
                        {
                            "name": "Sterile Culture Gel",
                            "category": "Crafting Item",
                            "description": "A clear gel used to stabilize samples.",
                            "location": "Expedition lab stores.",
                            "uses": ["sample preservation", "field cultures"],
                        }
                    ],
                    known_crafting_recipes=[
                        {
                            "name": "Emergency Culture Patch",
                            "ingredients": [
                                {
                                    "reagent_name": "Sterile Culture Gel",
                                    "quantity": 1,
                                    "measure_amount": 30,
                                    "measure_unit": "mL",
                                }
                            ],
                            "result": "A sterile patch for sealing a sample breach.",
                            "notes": "Standard expedition field method.",
                            "value_base_units": 45,
                        }
                    ],
                ),
            )

            crafting_items = repository.list_crafting_items()
            recipes = repository.list_crafting_recipes()
            catalog = repository.list_item_catalog()

            self.assertEqual(crafting_items[0]["name"], "Sterile Culture Gel")
            self.assertEqual(crafting_items[0]["category"], "Crafting Item")
            self.assertEqual(crafting_items[0]["uses"], ["sample preservation", "field cultures"])
            self.assertEqual(recipes[0]["name"], "Emergency Culture Patch")
            self.assertEqual(recipes[0]["ingredients"][0]["measure_unit"], "mL")
            self.assertEqual(recipes[0]["value_base_units"], 45)
            self.assertEqual(
                next(item for item in catalog if item["name"] == "Sterile Culture Gel")[
                    "category"
                ],
                "Crafting Item",
            )
            window.close()

    def test_ai_generated_calendar_settings_replace_bootstrap_calendar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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

    def test_ai_generated_calendar_rejects_default_gregorian_ai_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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
                    calendar_settings=GREGORIAN_CALENDAR_SETTINGS,
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
            self.assertEqual(calendar_settings["day_names"][0], "Dawn")
            self.assertNotEqual(calendar_settings["month_names"][0], "January")
            self.assertIn("Dawn", repository.get_state_value("time"))
            window.close()

    def test_ai_generated_sci_fi_calendar_rejects_artisan_fallback_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            setup = normalize_new_game_setup(
                {
                    "title": "Alien Crash",
                    "specified_genre": "Futuristic sci-fi survival",
                    "world_context": "Crash-landed on an unknown alien planet.",
                    "calendar": {"calendar_type": "ai_generated"},
                }
            )
            repository = SaveRepository.create_new_save(
                temp_path,
                "Alien Crash",
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
                        "seasons_per_year": 5,
                        "day_names": [
                            "Dawn",
                            "Bell",
                            "Hearth",
                            "Market",
                            "Lantern",
                            "Tide",
                            "Star",
                            "Rest",
                        ],
                        "month_names": [
                            "First Rise",
                            "Greenwake",
                            "Highsun",
                            "Goldleaf",
                            "Longshade",
                            "Deepfrost",
                            "Raincall",
                            "Bloomturn",
                            "Redharvest",
                            "Yearsend",
                        ],
                        "seasons": [
                            {"name": "Waking", "weather_hint": "spring"},
                            {"name": "Highlight", "weather_hint": "summer"},
                            {"name": "Harvest", "weather_hint": "autumn"},
                            {"name": "Frost", "weather_hint": "winter"},
                            {"name": "Rainmoot", "weather_hint": "rainy"},
                        ],
                        "time_display": "12_hour",
                    },
                    starting_calendar={"day_of_month": 1, "time_of_day_minutes": 480},
                    start_weather="",
                    finalized_starting_currency_balance_base_units=None,
                    finalized_currency_denominations=[],
                    finalized_currency_description="",
                    selected_genre="Science-fiction crash survival",
                    finalized_character={},
                    finalized_skills=[],
                    finalized_starter_items=[],
                ),
            )

            calendar_settings = repository.get_calendar_settings()

            self.assertEqual(calendar_settings["day_names"][0], "Launch")
            self.assertEqual(calendar_settings["month_names"][0], "Perihelion")
            self.assertNotIn("Hearth", calendar_settings["day_names"])
            self.assertNotIn("Market", calendar_settings["day_names"])
            window.close()

    def test_ai_new_game_state_preserves_player_provided_character_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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
            _ensure_qt_application()
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
            _ensure_qt_application()
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

    def test_ai_new_game_state_preserves_exact_location_and_named_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            setup = normalize_new_game_setup(
                {
                    "title": "Exact Start",
                    "start_location": "Kit's Abandoned Loft",
                    "start_location_mode": "exact",
                    "skills": [
                        {"name": "Stealth", "level": 5},
                        {"name": "Sleight of Hand", "level": 4},
                    ],
                }
            )
            repository = SaveRepository.create_new_save(
                temp_path,
                "Exact Start",
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
            ai_skills = []
            for index, skill in enumerate(setup["skills"]):
                ai_skills.append(
                    {
                        "name": (
                            "Rafter-Shadowing"
                            if index < 2
                            else f"Generated Skill {index}"
                        ),
                        "description": "Moving unseen in cramped urban spaces.",
                        "level": skill["level"],
                    }
                )

            window._apply_new_game_ai_state(
                repository,
                setup,
                SimpleNamespace(
                    start_location="Rafters of Rook's End",
                    locations=[
                        {
                            "name": "Rafters of Rook's End",
                            "description": "A hidden sleeping place.",
                            "x_miles": 0,
                            "y_miles": 0,
                            "terrain": "Urban",
                            "travel_multiplier": 1.0,
                            "travel_notes": "",
                        }
                    ],
                    starting_calendar={},
                    start_weather="",
                    finalized_starting_currency_balance_base_units=None,
                    finalized_currency_denominations=[],
                    finalized_currency_description="",
                    selected_genre="",
                    finalized_character={},
                    finalized_skills=ai_skills,
                    finalized_starter_items=[],
                ),
            )

            self.assertEqual(
                repository.get_state_value("location"),
                "Kit's Abandoned Loft",
            )
            self.assertIsNotNone(repository.find_travel_location("Kit's Abandoned Loft"))
            self.assertIsNone(repository.find_travel_location("Rafters of Rook's End"))
            skill_names = [skill["name"] for skill in repository.list_skills()]
            self.assertEqual(skill_names[:2], ["Stealth", "Sleight of Hand"])
            self.assertNotIn("Rafter-Shadowing", skill_names[:2])
            self.assertEqual(
                repository.list_skills()[0]["description"],
                "Moving unseen in cramped urban spaces.",
            )
            window.close()

    def test_ai_new_game_state_restores_omitted_requested_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            temp_path = Path(temp_dir)
            (temp_path / "saves").mkdir(parents=True, exist_ok=True)
            (temp_path / "logs").mkdir(parents=True, exist_ok=True)
            setup = normalize_new_game_setup(
                {
                    "title": "Structured Locations",
                    "start_location": "Kit's Alchemy",
                    "start_location_mode": "suggestion",
                    "starting_locations": [
                        {
                            "name": "Kit's Alchemy",
                            "description": "Kit's working alchemy shop.",
                            "location_mode": "suggestion",
                            "is_sublocation": True,
                            "parent_location": "Main City",
                        },
                        {
                            "name": "Main City",
                            "description": "The main city where most play occurs.",
                            "location_mode": "suggestion",
                        },
                        {
                            "name": "Nearby Wilderness",
                            "description": "A nearby foraging area.",
                            "location_mode": "suggestion",
                        },
                    ],
                }
            )
            repository = SaveRepository.create_new_save(
                temp_path,
                "Structured Locations",
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
                    start_location="Kit's Alchemy",
                    locations=[
                        {
                            "name": "Kit's Alchemy",
                            "description": "Starting location.",
                            "x_miles": 0,
                            "y_miles": 0,
                            "terrain": "",
                            "travel_multiplier": 1.0,
                            "travel_notes": "",
                            "source_index": 0,
                        },
                        {
                            "name": "Central Expanse",
                            "description": "The settled heartland.",
                            "x_miles": 0.5,
                            "y_miles": 0.5,
                            "terrain": "Plains",
                            "travel_multiplier": 1.0,
                            "travel_notes": "",
                            "source_index": 1,
                        },
                        {
                            "name": "Sun-Dappled Glade",
                            "description": "A bright foraging wood.",
                            "x_miles": 1.0,
                            "y_miles": 1.0,
                            "terrain": "Forest",
                            "travel_multiplier": 0.9,
                            "travel_notes": "Beyond Main City.",
                            "source_index": 2,
                        },
                    ],
                    known_crafting_items=[
                        {
                            "name": "Dried Sage",
                            "category": "Material",
                            "description": "A medicinal herb.",
                            "location": "Nearby Wilderness",
                            "uses": ["Healing salve"],
                        }
                    ],
                    known_crafting_recipes=[],
                    starting_calendar={},
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

            locations = {
                location["name"]: location
                for location in repository.get_travel_locations()
            }
            self.assertEqual(
                set(locations),
                {"Kit's Alchemy", "Central Expanse", "Sun-Dappled Glade"},
            )
            self.assertEqual(
                locations["Kit's Alchemy"]["description"],
                "Kit's working alchemy shop.",
            )
            self.assertIn(
                "Located within Central Expanse.",
                locations["Kit's Alchemy"]["travel_notes"],
            )
            self.assertEqual(
                locations["Sun-Dappled Glade"]["travel_notes"],
                "Beyond Central Expanse.",
            )
            self.assertEqual(
                repository.list_crafting_items()[0]["location"],
                "Sun-Dappled Glade",
            )
            window.close()

    def test_ai_new_game_state_accepts_partial_ai_starter_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Crafting UI Test")
            repository.add_crafting_item(
                name="Moon Salt",
                description="Crystals hum softly.",
                location="Moonlit stone basins",
                uses=["cooling draughts"],
            )
            repository.set_currency_denominations(
                [
                    {"name": "Crown", "plural_name": "Crowns", "value": 12},
                    {"name": "Bit", "plural_name": "Bits", "value": 1},
                ]
            )
            repository.add_crafting_recipe(
                name="Moon Draught",
                ingredients=[
                    {
                        "reagent_name": "Moon Salt",
                        "quantity": 1,
                        "measure_amount": 1,
                        "measure_unit": "pinch",
                    }
                ],
                result="Moon Draught",
                notes="Serve cold.",
                value_base_units=30,
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
                _require(screen.reagent_table.item(0, 0)).flags()
                & Qt.ItemFlag.ItemIsEditable,
                Qt.ItemFlag.NoItemFlags,
            )

            screen.reagent_table.selectRow(0)
            QApplication.processEvents()

            self.assertEqual(screen.reagent_table.columnCount(), 5)
            self.assertEqual(
                _require(screen.reagent_table.horizontalHeaderItem(1)).text(),
                "Category",
            )
            self.assertEqual(screen.tabs.tabText(0), "Items")
            self.assertEqual(screen.reagent_name_input.placeholderText(), "Item or material name")
            self.assertEqual(
                screen.recipe_reagent_combo.placeholderText(),
                "Search the Crafting Items list",
            )
            self.assertEqual(
                _require(screen.recipe_ingredient_table.horizontalHeaderItem(0)).text(),
                "Item",
            )
            self.assertEqual(screen.recipe_table.columnCount(), 5)
            self.assertEqual(
                _require(screen.recipe_table.horizontalHeaderItem(3)).text(),
                "Estimated Value",
            )
            self.assertEqual(
                _require(screen.recipe_table.item(0, 3)).text(),
                "2 Crowns and 6 Bits",
            )
            self.assertEqual(screen.reagent_name_input.text(), "Moon Salt")
            self.assertEqual(screen.reagent_category_combo.currentText(), "Material")
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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Recipe UI Test")
            repository.add_crafting_item(
                name="Alcohol Base",
                description="Purified spirit used to extract active compounds.",
                location="Distilled in an alchemist's workshop",
                uses=["extraction", "preservation"],
            )
            repository.add_inventory_item(
                name="Stirring Rod",
                category="Material",
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
            recipe_reagent_line_edit = _require(screen.recipe_reagent_line_edit)

            with patch("ai_adventure.ui.main_window.QTimer.singleShot") as single_shot:
                handled = screen.eventFilter(
                    recipe_reagent_line_edit,
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
            _ensure_qt_application()
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
            if shell.inventory_screen.table != None:
                item_zero = shell.inventory_screen.table.item(0, 0)
                item_one = shell.inventory_screen.table.item(1, 0)
                item_two = shell.inventory_screen.table.item(2, 0)
                if item_zero != None:
                    self.assertEqual(item_zero.text(), "Rope")
                if item_one != None:
                    self.assertEqual(item_one.text(), "Torch")
                if item_two != None:
                    self.assertEqual(item_two.text(), "Small Stone")
                shell.close()

    def test_inventory_screen_displays_currency_balance_outside_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Settings Test")
            shell = GameShell(on_return_to_menu=lambda: None)
            shell.set_repository(repository)

            shell.settings_screen.music_volume_slider.setValue(42)
            shell.settings_screen.music_volume_slider.sliderReleased.emit()
            shell.settings_screen._save_tts_settings(
                {
                    "narrator_enabled": True,
                    "tts_volume": 88,
                    "tts_voice": "am_echo",
                    "tts_speed": 125,
                    "tts_voice_mode": "preset",
                }
            )
            QApplication.processEvents()

            self.assertEqual(repository.get_setting("audio.music_volume"), 42)
            self.assertEqual(repository.get_setting("audio.tts_voice"), "am_echo")
            self.assertEqual(repository.get_setting("audio.tts_speed"), 125)
            self.assertFalse(hasattr(shell.settings_screen, "days_per_week_input"))
            self.assertEqual(shell.calendar_screen.table.columnCount(), 7)
            shell.close()

    def test_settings_currency_rows_match_active_save_denominations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Currency Settings Test")
            repository.set_currency_denominations(
                [{"name": "Credit", "plural_name": "Credits", "value": 1}]
            )
            screen = SettingsScreen()
            screen.set_repository(repository)

            self.assertEqual(len(screen.currency_name_inputs), 1)
            self.assertEqual(screen.currency_name_inputs[0].text(), "Credit")
            self.assertEqual(screen.currency_plural_inputs[0].text(), "Credits")
            self.assertFalse(screen.currency_value_inputs[0].isEnabled())
            self.assertEqual(screen.currency_value_inputs[0].value(), 1)
            self.assertEqual(
                screen.currency_value_inputs[0].buttonSymbols(),
                QAbstractSpinBox.ButtonSymbols.NoButtons,
            )
            self.assertTrue(screen.currency_remove_buttons[0].isHidden())
            self.assertTrue(screen.add_settings_currency_button.isEnabled())

            screen.add_settings_currency_button.click()
            self.assertEqual(len(screen.currency_name_inputs), 2)
            self.assertEqual(
                screen.currency_value_inputs[1].buttonSymbols(),
                QAbstractSpinBox.ButtonSymbols.UpDownArrows,
            )
            self.assertFalse(screen.currency_remove_buttons[1].isHidden())
            screen.currency_name_inputs[1].setText("Marker")
            screen.currency_plural_inputs[1].setText("Markers")
            screen.currency_value_inputs[1].setValue(5)
            screen._save_settings()

            denominations = repository.get_currency_denominations()
            self.assertEqual([denomination["name"] for denomination in denominations], ["Credit", "Marker"])
            self.assertEqual(denominations[1]["value"], 5)

            _require(screen.currency_remove_buttons[1]).click()
            denominations = repository.get_currency_denominations()
            self.assertEqual([denomination["name"] for denomination in denominations], ["Credit"])
            self.assertEqual(len(screen.currency_name_inputs), 1)
            screen.close()

    def test_settings_custom_voice_dialog_persists_to_save_and_app_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Voice Defaults Test")
            app_tts_settings = []
            screen = SettingsScreen(
                on_app_tts_settings_saved=app_tts_settings.append,
                voice_options={
                    "Sarah (Female, US)": "af_sarah",
                    "Echo (Male, US)": "am_echo",
                },
            )
            screen.set_repository(repository)
            saved_voice_audio = {
                "narrator_enabled": True,
                "tts_volume": 43,
                "tts_voice": "af_sarah",
                "tts_speed": 133,
                "tts_voice_mode": "blend",
                "tts_voice_blend": {
                    "name": "Storm Blend",
                    "voice_a": "af_sarah",
                    "voice_b": "am_echo",
                    "voice_a_weight": 71,
                    "tts_volume": 43,
                    "tts_speed": 133,
                },
                "tts_custom_voices": [
                    {
                        "name": "Storm Blend",
                        "voice_a": "af_sarah",
                        "voice_b": "am_echo",
                        "voice_a_weight": 71,
                        "tts_volume": 43,
                        "tts_speed": 133,
                    }
                ],
            }
            fake_dialog = SimpleNamespace(
                custom_voice_library_changed=True,
                exec=lambda: QDialog.DialogCode.Accepted,
                build_audio_settings=lambda: saved_voice_audio,
            )

            with patch(
                "ai_adventure.ui.main_window.CustomVoiceDialog",
                return_value=fake_dialog,
            ):
                _require(screen.custom_voice_button).click()

            self.assertEqual(
                repository.get_setting("audio.tts_custom_voices")[0]["name"],
                "Storm Blend",
            )
            self.assertEqual(app_tts_settings[0]["tts_custom_voices"][0]["name"], "Storm Blend")
            screen.close()

    def test_calendar_screen_settings_dialog_persists_and_refreshes_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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

    def test_calendar_screen_has_month_year_and_task_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Calendar Layout Test")
            screen = CalendarScreen()
            screen.set_repository(repository)

            try:
                self.assertEqual(
                    [screen.views.tabText(index) for index in range(screen.views.count())],
                    ["Month", "Year Overview", "Tasks & Deadlines"],
                )
                self.assertEqual(
                    screen.month_label.alignment(),
                    Qt.AlignmentFlag.AlignCenter,
                )
                self.assertEqual(
                    screen.summary_label.alignment(),
                    Qt.AlignmentFlag.AlignCenter,
                )
                self.assertEqual(
                    screen.table.horizontalHeader().sectionResizeMode(0),
                    QHeaderView.ResizeMode.Stretch,
                )
                self.assertTrue(screen.settings_button.isHidden())
                self.assertEqual(screen.summary_label.text(), "Season: Spring")
            finally:
                screen.close()

    def test_calendar_settings_dialog_builds_calendar_settings(self) -> None:
        _ensure_qt_application()
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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Theme Test")
            theme_changes = []
            screen = SettingsScreen(on_theme_changed=lambda: theme_changes.append("theme"))
            screen.set_repository(repository)

            screen.theme_combo.setCurrentText("Dark")
            QApplication.processEvents()

            self.assertEqual(repository.get_setting("theme"), "Dark")
            self.assertEqual(theme_changes, ["theme"])
            screen.close()

    def test_ai_settings_dialog_builds_and_persists_all_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Narration Test")
            screen = SettingsScreen()
            screen.set_repository(repository)
            dialog = AISettingsDialog(
                settings=screen._current_ai_settings(repository)
            )

            self.assertEqual(screen.ai_settings_button.text(), "A.I. Settings...")
            self.assertFalse(hasattr(screen, "narration_tense_combo"))
            self.assertEqual(
                dialog.model_intelligence_combo.currentData(),
                "faster",
            )
            self.assertEqual(dialog.model_tone_combo.currentData(), "neutral")
            self.assertEqual(dialog.response_length_combo.currentData(), "normal")
            self.assertEqual(
                dialog.model_content_combo.selected_categories(),
                [
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "HARM_CATEGORY_CIVIC_INTEGRITY",
                ],
            )

            _set_combo_to_data(dialog.model_intelligence_combo, "smarter")
            _set_combo_to_data(dialog.model_tone_combo, "quirky")
            _set_combo_to_data(dialog.response_length_combo, "verbose")
            dialog.model_content_combo.set_selected_categories(
                ["HARM_CATEGORY_DANGEROUS_CONTENT"]
            )
            _set_combo_to_data(dialog.narration_tense_combo, "future")
            _set_combo_to_data(
                dialog.narration_style_combo,
                "first_person_omniscient",
            )
            dialog.additional_ai_context_input.setPlainText("Keep mysteries subtle.")
            screen._save_ai_settings(dialog.build_ai_settings())

            self.assertEqual(
                repository.get_setting("ai.model_intelligence"),
                "smarter",
            )
            self.assertEqual(repository.get_setting("ai.model_tone"), "quirky")
            self.assertEqual(repository.get_setting("ai.response_length"), "verbose")
            self.assertEqual(
                repository.get_setting("ai.allowed_content_categories"),
                ["HARM_CATEGORY_DANGEROUS_CONTENT"],
            )
            self.assertEqual(repository.get_setting("ai.narration_tense"), "future")
            self.assertEqual(
                repository.get_setting("ai.narration_style"),
                "first_person_omniscient",
            )
            self.assertEqual(
                repository.get_setting("ai.additional_context"),
                "Keep mysteries subtle.",
            )
            self.assertIn("highly detailed", dialog.response_length_description.text())
            self.assertIn("Dangerous Content", dialog.model_content_description.text())
            dialog.close()
            screen.close()

    def test_settings_sample_voice_uses_selected_voice_and_volume(self) -> None:
        _ensure_qt_application()
        samples = []
        dialog = TTSSettingsDialog(
            audio_settings={
                "narrator_enabled": True,
                "tts_volume": 90,
                "tts_voice": "af_sarah",
                "tts_speed": 100,
            },
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
            on_sample_voice=lambda voice, volume, speed: samples.append(
                (voice, volume, speed)
            )
            or True,
        )

        try:
            widget = _require(dialog.tts_settings_widget)
            _set_combo_to_data(_require(widget.tts_voice_combo), "am_echo")
            _require(widget.tts_volume_slider).setValue(41)
            _require(widget.tts_speed_slider).setValue(125)
            _require(widget.sample_voice_button).click()

            self.assertEqual(samples, [("am_echo", 41, 125)])
        finally:
            dialog.close()

    def test_tts_settings_widget_hides_options_until_narrator_is_enabled(self) -> None:
        _ensure_qt_application()
        dialog = TTSSettingsDialog(
            audio_settings={
                "narrator_enabled": False,
                "tts_volume": 90,
                "tts_voice": "af_sarah",
                "tts_speed": 100,
                "tts_voice_mode": "preset",
            },
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
        )

        try:
            widget = _require(dialog.tts_settings_widget)
            form = _require_widget(widget.layout(), QFormLayout)

            self.assertEqual(widget.tts_volume_slider.value(), 90)
            self.assertEqual(widget.tts_volume_label.text(), "90%")
            self.assertTrue(widget.tts_volume_row.isHidden())
            self.assertTrue(widget.tts_speed_row.isHidden())
            self.assertTrue(widget.voice_mode_combo.isHidden())
            self.assertTrue(widget.preset_voice_combo.isHidden())
            self.assertTrue(widget.custom_voice_row.isHidden())
            self.assertTrue(widget.voice_button_row.isHidden())
            self.assertEqual(
                _require_widget(form.labelForField(widget.custom_voice_row), QLabel).text(),
                "Custom Voice:",
            )
            self.assertFalse(hasattr(widget, "custom_voice_name_input"))

            widget.narrator_enabled_checkbox.setChecked(True)

            self.assertFalse(widget.tts_volume_row.isHidden())
            self.assertFalse(widget.tts_speed_row.isHidden())
            self.assertFalse(widget.voice_mode_combo.isHidden())
            self.assertFalse(widget.preset_voice_combo.isHidden())
            self.assertTrue(widget.custom_voice_row.isHidden())
            self.assertFalse(widget.voice_button_row.isHidden())

            _set_combo_to_data(widget.voice_mode_combo, "blend")

            self.assertTrue(widget.preset_voice_combo.isHidden())
            self.assertFalse(widget.custom_voice_row.isHidden())
            self.assertTrue(widget.custom_voice_button.isEnabled())
        finally:
            dialog.close()

    def test_main_menu_exposes_settings_button(self) -> None:
        _ensure_qt_application()

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
            self.assertEqual(window.main_menu.templates_button.text(), "New Game Templates")

            window.close()
            apply_application_theme("Light")

    def test_new_game_template_manager_saves_renames_and_deletes_partial_template(self) -> None:
        _ensure_qt_application()

        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "new_game_templates.json"
            dialog = NewGameTemplateManagerDialog(template_path=template_path)

            try:
                dialog.template_name_input.setText("Mystery Shell")
                dialog.genre_input.setText("Cozy mystery")
                dialog._append_starting_location_row(
                    {
                        "name": "Rainy Office",
                        "description": "A cramped office above a rainy street.",
                        "location_mode": "exact",
                    }
                )
                dialog._append_starting_location_row(
                    {
                        "name": "Evidence Locker",
                        "description": "A secure room inside the office.",
                        "location_mode": "suggestion",
                        "is_sublocation": True,
                        "parent_location": "Rainy Office",
                    }
                )
                dialog.start_location_combo.setCurrentIndex(
                    dialog.start_location_combo.findText("Rainy Office")
                )
                dialog._append_starting_npc_row(
                    {
                        "name": "Archivist Pell",
                        "location": "Rainy Office",
                        "description": "Knows the case files.",
                        "description_mode": "exact",
                    }
                )
                dialog.character_name_input.clear()
                dialog.skill_inputs[0][1].setText("Observation")
                dialog.skill_inputs[0][2].setText("Spotting small clues.")
                level_three_index = next(
                    index
                    for index, (level, _skill_input, _description_input) in enumerate(
                        dialog.skill_inputs
                    )
                    if level == 3
                )
                dialog.skill_inputs[level_three_index][1].setText("Deduction")
                dialog.skill_inputs[level_three_index][2].setText("Connecting subtle evidence.")
                _set_combo_to_data(dialog.narration_tense_combo, "past")
                _set_combo_to_data(dialog.narration_style_combo, "third_person_limited")
                dialog._append_starter_item_row(
                    {
                        "name": "Notebook",
                        "category": "Tool",
                        "quantity": 1,
                        "description": "Case notes.",
                        "value_base_units": 4,
                    }
                )
                dialog._append_currency_row({"name": "Coin", "plural_name": "Coins", "value": 1})
                dialog._append_economy_example_row(
                    {"name": "Bread", "value_base_units": 2}
                )
                _set_combo_to_data(dialog.calendar_type_combo, "ai_generated")
                dialog._save_template()

                templates = load_new_game_templates(template_path, normalize_setups=False)

                self.assertEqual([template.name for template in templates], ["Mystery Shell"])
                self.assertEqual(templates[0].setup["title"], "Mystery Shell")
                self.assertEqual(templates[0].setup["character"]["name"], "")
                self.assertEqual(templates[0].setup["specified_genre"], "Cozy mystery")
                self.assertEqual(templates[0].setup["start_location"], "Rainy Office")
                self.assertEqual(templates[0].setup["start_location_mode"], "exact")
                self.assertEqual(
                    templates[0].setup["starting_locations"][0]["name"],
                    "Rainy Office",
                )
                self.assertEqual(
                    templates[0].setup["starting_locations"][1]["parent_location"],
                    "Rainy Office",
                )
                self.assertEqual(
                    templates[0].setup["starting_npcs"][0]["name"],
                    "Archivist Pell",
                )
                self.assertEqual(
                    templates[0].setup["starting_npcs"][0]["description_mode"],
                    "exact",
                )
                self.assertEqual(templates[0].setup["narration"]["tense"], "past")
                self.assertEqual(
                    templates[0].setup["narration"]["style"],
                    "third_person_limited",
                )
                self.assertEqual(templates[0].setup["skills"][0]["name"], "Observation")
                self.assertEqual(templates[0].setup["skills"][0]["level"], 5)
                self.assertEqual(templates[0].setup["skills"][1]["name"], "Deduction")
                self.assertEqual(templates[0].setup["skills"][1]["level"], 3)
                self.assertEqual(dialog.skill_inputs[1][1].text(), "")
                self.assertEqual(dialog.skill_inputs[2][1].text(), "")
                self.assertEqual(dialog.skill_inputs[level_three_index][1].text(), "Deduction")
                self.assertEqual(templates[0].setup["starter_items"][0]["name"], "Notebook")
                self.assertEqual(
                    templates[0].setup["currency_denominations"][0]["name"],
                    "Coin",
                )
                self.assertEqual(templates[0].setup["economy_examples"][0]["name"], "Bread")
                self.assertIn("Bread costs 2 base units", templates[0].setup["currency_description"])
                self.assertEqual(templates[0].setup["calendar"]["calendar_type"], "ai_generated")

                dialog.template_name_input.setText("Rainy Mystery Shell")
                dialog._save_template()

                templates = load_new_game_templates(template_path, normalize_setups=False)
                self.assertEqual([template.name for template in templates], ["Rainy Mystery Shell"])

                with patch(
                    "ai_adventure.ui.main_window.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    dialog._delete_template()

                self.assertEqual(load_new_game_templates(template_path, normalize_setups=False), [])
            finally:
                dialog.close()

    def test_new_game_template_manager_editor_tabs_are_scrollable(self) -> None:
        _ensure_qt_application()

        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "new_game_templates.json"
            dialog = NewGameTemplateManagerDialog(template_path=template_path)

            try:
                tabs = _require_widget(dialog.findChild(QTabWidget), QTabWidget)

                self.assertEqual(tabs.count(), 6)

                for index in range(tabs.count()):
                    scroll_area = _require_widget(tabs.widget(index), QScrollArea)
                    self.assertTrue(scroll_area.widgetResizable())
                    self.assertIsNotNone(scroll_area.widget())

                details_scroll_area = _require_widget(tabs.widget(5), QScrollArea)
                details_content = _require(details_scroll_area.widget())
                self.assertIs(dialog.starter_items_table.parentWidget(), details_content)
            finally:
                dialog.close()

    def test_main_menu_settings_apply_and_persist_without_save(self) -> None:
        app = _ensure_qt_application()

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
                    "tts_speed": 130,
                },
            },
            persist=True,
            )

            saved_settings = load_app_settings(app_paths.app_settings_path)

            self.assertEqual(saved_settings["theme"], "Dark")
            self.assertFalse(saved_settings["audio"]["music_enabled"])
            self.assertEqual(saved_settings["audio"]["music_volume"], 7)
            self.assertEqual(saved_settings["audio"]["tts_voice"], "am_echo")
            self.assertEqual(saved_settings["audio"]["tts_speed"], 130)
            self.assertEqual(window.menu_theme, "Dark")
            sound_manager = _require(window.sound_manager)
            self.assertFalse(sound_manager.music_enabled)
            self.assertEqual(sound_manager.music_volume, 0.07)
            self.assertEqual(
                app.palette().color(QPalette.ColorRole.Window).name(),
                "#202124",
            )

            window.close()
            apply_application_theme("Light")

    def test_main_window_persists_custom_voice_defaults_to_app_settings(self) -> None:
        _ensure_qt_application()

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

            window._persist_app_tts_settings(
                {
                    "narrator_enabled": True,
                    "tts_volume": 41,
                    "tts_voice": "af_sarah",
                    "tts_speed": 132,
                    "tts_voice_mode": "blend",
                    "tts_voice_blend": {
                        "name": "Storm Blend",
                        "voice_a": "af_sarah",
                        "voice_b": "am_echo",
                        "voice_a_weight": 72,
                        "tts_volume": 41,
                        "tts_speed": 132,
                    },
                    "tts_custom_voices": [
                        {
                            "name": "Storm Blend",
                            "voice_a": "af_sarah",
                            "voice_b": "am_echo",
                            "voice_a_weight": 72,
                            "tts_volume": 41,
                            "tts_speed": 132,
                        }
                    ],
                }
            )

            saved_settings = load_app_settings(app_paths.app_settings_path)

            self.assertEqual(
                saved_settings["audio"]["tts_custom_voices"][0]["name"],
                "Storm Blend",
            )
            self.assertEqual(
                saved_settings["audio"]["tts_custom_voices"][0]["tts_volume"],
                41,
            )
            self.assertEqual(
                saved_settings["audio"]["tts_custom_voices"][0]["tts_speed"],
                132,
            )
            self.assertEqual(saved_settings["audio"]["tts_voice_mode"], "blend")

            window.close()
            apply_application_theme("Light")

    def test_main_menu_settings_dialog_builds_audio_and_theme_settings(self) -> None:
        _ensure_qt_application()
        dialog = MainMenuSettingsDialog(
            settings={
                "theme": "Light",
                "audio": {
                    "music_enabled": True,
                    "narrator_enabled": True,
                    "music_volume": 25,
                    "tts_volume": 90,
                    "tts_voice": "af_sarah",
                    "tts_speed": 100,
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
            _require(dialog.music_enabled_checkbox).setChecked(False)
            _require(dialog.music_volume_slider).setValue(12)
            _require(dialog.narrator_enabled_checkbox).setChecked(False)
            _require(dialog.tts_volume_slider).setValue(34)
            _require(_require(dialog.tts_settings_widget).tts_speed_slider).setValue(135)
            _set_combo_to_data(_require(dialog.tts_voice_combo), "am_echo")

            settings = dialog.build_settings()

            self.assertEqual(settings["theme"], "Dark")
            self.assertFalse(settings["audio"]["music_enabled"])
            self.assertEqual(settings["audio"]["music_volume"], 12)
            self.assertFalse(settings["audio"]["narrator_enabled"])
            self.assertEqual(settings["audio"]["tts_volume"], 34)
            self.assertEqual(settings["audio"]["tts_voice"], "am_echo")
            self.assertEqual(settings["audio"]["tts_speed"], 135)
        finally:
            dialog.close()

    def test_main_menu_settings_sample_voice_uses_selected_voice_and_volume(self) -> None:
        _ensure_qt_application()
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
                    "tts_speed": 100,
                },
            },
            tts_enabled=True,
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
            on_sample_voice=lambda voice, volume, _speed: samples.append((voice, volume)) or True,
        )

        try:
            _set_combo_to_data(_require(dialog.tts_voice_combo), "am_echo")
            _require(dialog.tts_volume_slider).setValue(37)
            _require(dialog.sample_voice_button).click()

            self.assertEqual(samples, [("am_echo", 37)])
        finally:
            dialog.close()

    def test_custom_voice_dialog_links_blend_sliders_and_saves_as(self) -> None:
        _ensure_qt_application()
        dialog = CustomVoiceDialog(
            audio_settings={
                "narrator_enabled": True,
                "tts_voice_mode": "blend",
                "tts_voice_blend": {
                    "name": "Rain Voice",
                    "voice_a": "af_sarah",
                    "voice_b": "am_echo",
                    "voice_a_weight": 50,
                },
            },
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
        )

        try:
            dialog.voice_a_weight_slider.setValue(65)
            self.assertEqual(dialog.voice_a_weight_slider.value(), 65)
            self.assertEqual(dialog.voice_b_weight_slider.value(), 35)

            dialog.voice_b_weight_slider.setValue(20)
            self.assertEqual(dialog.voice_a_weight_slider.value(), 80)
            self.assertEqual(dialog.voice_b_weight_slider.value(), 20)

            dialog.tts_volume_slider.setValue(42)
            dialog.tts_speed_slider.setValue(135)
            _set_combo_to_data(dialog.voice_a_combo, "af_sarah")
            _set_combo_to_data(dialog.voice_b_combo, "am_echo")

            with patch(
                "ai_adventure.ui.main_window.QInputDialog.getText",
                return_value=("Storm Blend", True),
            ):
                dialog.save_custom_voice_as_button.click()

            settings = dialog.build_audio_settings()

            self.assertEqual(settings["tts_voice_mode"], "blend")
            self.assertEqual(settings["tts_voice_blend"]["voice_a_weight"], 80)
            self.assertEqual(settings["tts_voice_blend"]["voice_b_weight"], 20)
            self.assertIn("Storm Blend", dialog.custom_voice_combo.currentText())
            self.assertEqual(settings["tts_custom_voices"][0]["name"], "Storm Blend")
            self.assertEqual(settings["tts_custom_voices"][0]["tts_volume"], 42)
            self.assertEqual(settings["tts_custom_voices"][0]["tts_speed"], 135)
            self.assertTrue(dialog.custom_voice_library_changed)
        finally:
            dialog.close()

    def test_custom_voice_dialog_loads_custom_voice_volume_and_speed(self) -> None:
        _ensure_qt_application()
        dialog = CustomVoiceDialog(
            audio_settings={
                "narrator_enabled": True,
                "tts_volume": 90,
                "tts_speed": 100,
                "tts_voice_mode": "blend",
                "tts_custom_voices": [
                    {
                        "name": "Storm Blend",
                        "voice_a": "af_sarah",
                        "voice_b": "am_echo",
                        "voice_a_weight": 65,
                        "tts_volume": 38,
                        "tts_speed": 145,
                    }
                ],
            },
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
        )

        try:
            dialog.custom_voice_combo.setCurrentIndex(1)
            dialog.load_custom_voice_button.click()

            self.assertIn("Storm Blend", dialog.current_voice_label.text())
            self.assertEqual(dialog.voice_a_weight_slider.value(), 65)
            self.assertEqual(dialog.voice_b_weight_slider.value(), 35)
            self.assertEqual(dialog.tts_volume_slider.value(), 38)
            self.assertEqual(dialog.tts_speed_slider.value(), 145)
        finally:
            dialog.close()

    def test_custom_voice_dialog_saves_loaded_voice_and_renames_explicitly(self) -> None:
        _ensure_qt_application()
        dialog = CustomVoiceDialog(
            audio_settings={
                "narrator_enabled": True,
                "tts_voice_mode": "blend",
                "tts_voice_blend": {
                    "name": "Storm Blend",
                    "voice_a": "af_sarah",
                    "voice_b": "am_echo",
                    "voice_a_weight": 65,
                },
                "tts_custom_voices": [
                    {
                        "name": "Storm Blend",
                        "voice_a": "af_sarah",
                        "voice_b": "am_echo",
                        "voice_a_weight": 65,
                    }
                ],
            },
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
        )

        try:
            self.assertTrue(dialog.save_custom_voice_button.isEnabled())
            self.assertFalse(hasattr(dialog, "custom_voice_name_input"))

            dialog.voice_a_weight_slider.setValue(70)
            dialog.save_custom_voice_button.click()

            settings = dialog.build_audio_settings()
            self.assertEqual(settings["tts_custom_voices"][0]["name"], "Storm Blend")
            self.assertEqual(settings["tts_custom_voices"][0]["voice_a_weight"], 70)

            with patch(
                "ai_adventure.ui.main_window.QInputDialog.getText",
                return_value=("Rain Blend", True),
            ):
                dialog.rename_custom_voice_button.click()

            settings = dialog.build_audio_settings()
            self.assertEqual(settings["tts_custom_voices"][0]["name"], "Rain Blend")
            self.assertNotIn("Storm Blend", [voice["name"] for voice in settings["tts_custom_voices"]])
        finally:
            dialog.close()

    def test_main_menu_uses_latest_saved_dark_theme_on_startup(self) -> None:
        app = _ensure_qt_application()

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
        app = _ensure_qt_application()

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

    def test_main_menu_renames_and_deletes_selected_save(self) -> None:
        _ensure_qt_application()

        with tempfile.TemporaryDirectory() as temp_dir:
            saves_dir = Path(temp_dir)
            repository = SaveRepository.create_new_save(saves_dir, "Old Save")
            loaded_paths: list[Path] = []
            menu = MainMenuScreen(
                saves_dir,
                on_new_game=lambda: None,
                on_load_game=loaded_paths.append,
                on_settings=lambda: None,
                on_templates=lambda: None,
            )

            try:
                self.assertTrue(menu.rename_save_button.isEnabled())
                self.assertTrue(menu.delete_save_button.isEnabled())

                with patch(
                    "ai_adventure.ui.main_window.QInputDialog.getText",
                    return_value=("Renamed Save", True),
                ):
                    menu.rename_save_button.click()

                self.assertEqual(SaveRepository(repository.db_path).get_meta("title"), "Renamed Save")
                self.assertIn("Renamed Save", menu.save_combo.itemText(0))

                with patch(
                    "ai_adventure.ui.main_window.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    menu.delete_save_button.click()

                self.assertFalse(repository.db_path.exists())
                self.assertEqual(menu.save_combo.currentData(), None)
                self.assertFalse(menu.load_button.isEnabled())
                self.assertFalse(menu.rename_save_button.isEnabled())
                self.assertFalse(menu.delete_save_button.isEnabled())
            finally:
                menu.close()

    def test_start_new_game_wizard_prompts_for_new_name_after_duplicate(self) -> None:
        _ensure_qt_application()

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
            repository = _require(window.active_repository)
            self.assertEqual(
                repository.get_meta("title"),
                "Duplicate UI Save 2",
            )
            self.assertEqual(len(scheduled_callbacks), 1)
            window.close()
            apply_application_theme("Light")

    def test_create_new_game_opens_blank_shell_before_world_generation(self) -> None:
        app = _ensure_qt_application()

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

            with patch.object(
                window,
                "_finish_new_game_generation",
                side_effect=fake_finish,
            ), patch("ai_adventure.ui.main_window.QTimer.singleShot", fake_single_shot):
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
                self.assertEqual(generated, [])
                self.assertEqual(len(scheduled_callbacks), 1)
                scheduled_callbacks[0]()

            self.assertEqual(window.stack.currentWidget(), window.game_shell)
            repository = _require(window.active_repository)
            self.assertEqual(len(scheduled_callbacks), 1)
            self.assertFalse(window.game_shell.story_screen.player_input.isEnabled())
            self.assertFalse(
                repository.get_setting("audio.music_enabled", True)
            )
            self.assertFalse(
                repository.get_setting("audio.narrator_enabled", True)
            )
            self.assertEqual(repository.get_setting("audio.music_volume"), 0)
            self.assertEqual(repository.get_setting("audio.tts_volume"), 20)
            self.assertEqual(
                repository.get_setting("audio.tts_voice"),
                "am_echo",
            )

            self.assertEqual(len(generated), 1)
            self.assertEqual(generated[0][0], repository)
            window.close()
            apply_application_theme("Light")

    def test_return_to_menu_preserves_active_save_dark_theme(self) -> None:
        app = _ensure_qt_application()

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
            _ensure_qt_application()
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
            _ensure_qt_application()
            repository = SaveRepository.create_new_save(Path(temp_dir), "Theme Test")
            repository.set_setting("theme", "System")
            screen = SettingsScreen()
            screen.set_repository(repository)

            self.assertEqual(screen.theme_combo.currentText(), "Light")
            self.assertEqual(repository.get_setting("theme"), "Light")
            screen.close()

    def test_main_window_lightweight_mode_skips_narration_player(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _ensure_qt_application()
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
        app = _ensure_qt_application()

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
        _ensure_qt_application()

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

            with patch.object(
                window,
                "_finish_new_game_generation",
                side_effect=lambda _repository, _setup: None,
            ), patch("ai_adventure.ui.main_window.QTimer.singleShot", lambda *_args: None):
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

            repository = _require(window.active_repository)
            self.assertFalse(
                repository.get_setting("audio.narrator_enabled", True)
            )
            self.assertEqual(repository.get_setting("audio.tts_volume"), 0)
            self.assertEqual(
                repository.get_setting("audio.tts_voice"),
                DEFAULT_NARRATOR_VOICE,
            )
            window.close()
            apply_application_theme("Light")

    def test_template_setup_uses_next_available_save_title(self) -> None:
        _ensure_qt_application()

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

    def test_next_available_save_title_increments_existing_numeric_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            saves_dir = Path(temp_dir)
            SaveRepository.create_new_save(saves_dir, "A Thief's Tale 2")
            SaveRepository.create_new_save(saves_dir, "A Thief's Tale 3")

            self.assertEqual(
                _next_available_save_title(saves_dir, "A Thief's Tale 2"),
                "A Thief's Tale 4",
            )

    def test_new_game_wizard_loads_template_fields(self) -> None:
        _ensure_qt_application()
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
                "starting_locations": [
                    {
                        "name": "Rainmarket Station",
                        "description": "A canal station under an old clock.",
                        "location_mode": "exact",
                    },
                    {
                        "name": "Blacksmith Shop",
                        "description": "A forge inside the station concourse.",
                        "location_mode": "suggestion",
                        "is_sublocation": True,
                        "parent_location": "Rainmarket Station",
                    }
                ],
                "starting_npcs": [
                    {
                        "name": "Quartermaster Vale",
                        "location": "Rainmarket Station",
                        "description": "Sells travel supplies with exact wording.",
                        "description_mode": "exact",
                    }
                ],
                "starting_task": {
                    "mode": "custom",
                    "task": {
                        "name": "Find the Canal Ledger",
                        "description": "Recover the missing tax ledger.",
                        "requester": "Archivist Pell",
                        "location": "",
                        "reward": "Guild favor",
                        "due_date": "",
                    },
                },
                "calendar": {"calendar_type": "gregorian", "time_display": "24_hour"},
                "audio": {
                    "music_enabled": False,
                    "narrator_enabled": False,
                    "music_volume": 10,
                    "tts_volume": 30,
                    "tts_voice": "am_echo",
                    "tts_speed": 140,
                },
                "narration": {
                    "tense": "past",
                    "style": "third_person_omniscient",
                },
                "ai_settings": {
                    "model_intelligence": "smarter",
                    "model_tone": "professional",
                    "response_length": "descriptive",
                    "allowed_content_categories": [
                        "HARM_CATEGORY_HARASSMENT"
                    ],
                    "additional_context": "Favor investigative tension.",
                },
                "currency_denominations": [
                    {"name": "Bit", "plural_name": "Bits", "value": 1},
                    {"name": "Crown", "plural_name": "Crowns", "value": 12},
                ],
                "economy_examples": [
                    {"name": "Bread", "value_base_units": 2},
                ],
                "specified_genre": "Realistic detective mystery",
                "game_style": "Quiet investigation.",
                "start_location": "Rainmarket Station",
                "start_location_mode": "exact",
                "world_context": "Canal guilds control the docks.",
            }
        )

        self.assertEqual(wizard.title_input.text(), "Template Adventure")
        self.assertTrue(wizard.start_location_input.isHidden())
        self.assertTrue(wizard.start_location_mode_combo.isHidden())
        self.assertEqual(wizard.character_name_input.text(), "Iris Vale")
        self.assertEqual(wizard.skill_inputs[0][1].text(), "Skill 0")
        self.assertEqual(wizard.skill_inputs[0][2].text(), "Skill 0 description.")
        self.assertEqual(wizard.starter_items_table.rowCount(), 1)
        self.assertEqual(_table_cell(wizard.starter_items_table, 0, 0, QLineEdit).text(), "Notebook")
        self.assertEqual(_table_cell(wizard.starter_items_table, 0, 1, QSpinBox).value(), 1)
        self.assertEqual(_table_cell(wizard.starter_items_table, 0, 2, QLineEdit).text(), "Tool")
        self.assertEqual(_table_cell(wizard.starter_items_table, 0, 3, QLineEdit).text(), "Case notes.")
        self.assertEqual(_table_cell(wizard.starter_items_table, 0, 4, QSpinBox).value(), 4)
        self.assertEqual(wizard.starting_locations_table.rowCount(), 2)
        self.assertEqual(wizard.start_location_combo.currentText(), "Rainmarket Station")
        self.assertEqual(
            _table_cell(wizard.starting_locations_table, 0, 0, QLineEdit).text(),
            "Rainmarket Station",
        )
        self.assertEqual(
            _table_cell(wizard.starting_locations_table, 0, 1, QLineEdit).text(),
            "A canal station under an old clock.",
        )
        self.assertEqual(
            _table_cell(wizard.starting_locations_table, 0, 2, QComboBox).currentData(),
            "exact",
        )
        self.assertEqual(
            _table_cell(wizard.starting_locations_table, 1, 0, QLineEdit).text(),
            "Blacksmith Shop",
        )
        self.assertTrue(
            _table_cell(wizard.starting_locations_table, 1, 3, QCheckBox).isChecked()
        )
        self.assertEqual(
            _table_cell(wizard.starting_locations_table, 1, 4, QComboBox).currentText(),
            "Rainmarket Station",
        )
        self.assertEqual(wizard.starting_npcs_table.rowCount(), 1)
        self.assertEqual(
            _table_cell(wizard.starting_npcs_table, 0, 0, QLineEdit).text(),
            "Quartermaster Vale",
        )
        self.assertEqual(
            _table_cell(wizard.starting_npcs_table, 0, 1, QLineEdit).text(),
            "Rainmarket Station",
        )
        self.assertEqual(
            _table_cell(wizard.starting_npcs_table, 0, 2, QLineEdit).text(),
            "Sells travel supplies with exact wording.",
        )
        self.assertEqual(
            _table_cell(wizard.starting_npcs_table, 0, 3, QComboBox).currentData(),
            "exact",
        )
        self.assertEqual(wizard.starting_task_mode_combo.currentData(), "custom")
        self.assertFalse(wizard.starting_task_custom_group.isHidden())
        self.assertEqual(wizard.starting_task_name_input.text(), "Find the Canal Ledger")
        self.assertEqual(
            wizard.starting_task_description_input.toPlainText(),
            "Recover the missing tax ledger.",
        )
        self.assertEqual(wizard.starting_task_requester_input.text(), "Archivist Pell")
        self.assertEqual(wizard.starting_task_reward_input.text(), "Guild favor")
        self.assertEqual(wizard.currency_table.rowCount(), 2)
        self.assertEqual(_table_cell(wizard.currency_table, 1, 0, QLineEdit).text(), "Crown")
        self.assertEqual(wizard.economy_examples_table.rowCount(), 1)
        self.assertEqual(_table_cell(wizard.economy_examples_table, 0, 0, QLineEdit).text(), "Bread")
        self.assertEqual(_table_cell(wizard.economy_examples_table, 0, 1, QSpinBox).value(), 2)
        self.assertEqual(wizard.calendar_type_combo.currentData(), "gregorian")
        self.assertEqual(wizard.start_location_mode_combo.currentData(), "exact")
        self.assertFalse(wizard.calendar_settings_button.isEnabled())
        self.assertEqual(wizard.narration_tense_combo.currentData(), "past")
        self.assertEqual(
            wizard.narration_style_combo.currentData(),
            "third_person_omniscient",
        )
        self.assertEqual(wizard.ai_settings_button.text(), "A.I. Settings...")
        self.assertIn("Smarter", wizard.ai_settings_summary_label.text())
        self.assertIn("Professional", wizard.ai_settings_summary_label.text())
        self.assertIn("Descriptive", wizard.ai_settings_summary_label.text())
        self.assertFalse(_require(wizard.music_enabled_checkbox).isChecked())
        self.assertFalse(_require(wizard.narrator_enabled_checkbox).isChecked())
        self.assertEqual(_require(wizard.music_volume_slider).value(), 10)
        self.assertEqual(_require(wizard.tts_volume_slider).value(), 30)
        self.assertEqual(_require(wizard.tts_speed_slider).value(), 140)
        self.assertEqual(_require(wizard.tts_voice_combo).currentData(), "am_echo")
        setup = wizard.build_setup()
        self.assertEqual(setup["skills"][0]["description"], "Skill 0 description.")
        self.assertFalse(setup["skills"][0]["requires_ai_invention"])
        self.assertEqual(setup["starter_items"][0]["name"], "Notebook")
        self.assertEqual(setup["starter_items"][0]["category"], "Tool")
        self.assertEqual(setup["starter_items"][0]["quantity"], 1)
        self.assertEqual(setup["starter_items"][0]["description"], "Case notes.")
        self.assertEqual(setup["starter_items"][0]["value_base_units"], 4)
        self.assertEqual(setup["starting_locations"][0]["name"], "Rainmarket Station")
        self.assertEqual(
            setup["starting_locations"][0]["description"],
            "A canal station under an old clock.",
        )
        self.assertEqual(setup["starting_locations"][0]["location_mode"], "exact")
        self.assertFalse(setup["starting_locations"][0]["requires_ai_invention"])
        self.assertEqual(setup["starting_locations"][1]["name"], "Blacksmith Shop")
        self.assertTrue(setup["starting_locations"][1]["is_sublocation"])
        self.assertEqual(
            setup["starting_locations"][1]["parent_location"],
            "Rainmarket Station",
        )
        self.assertEqual(setup["start_location"], "Rainmarket Station")
        self.assertEqual(setup["start_location_mode"], "exact")
        self.assertEqual(setup["starting_npcs"][0]["name"], "Quartermaster Vale")
        self.assertEqual(setup["starting_npcs"][0]["location"], "Rainmarket Station")
        self.assertEqual(
            setup["starting_npcs"][0]["description"],
            "Sells travel supplies with exact wording.",
        )
        self.assertEqual(setup["starting_npcs"][0]["description_mode"], "exact")
        self.assertFalse(setup["starting_npcs"][0]["requires_ai_invention"])
        self.assertEqual(setup["starting_task"]["mode"], "custom")
        self.assertEqual(
            setup["starting_task"]["task"]["name"],
            "Find the Canal Ledger",
        )
        self.assertTrue(
            setup["starting_task"]["task"]["requires_ai_invention"],
        )
        self.assertFalse(setup["audio"]["music_enabled"])
        self.assertFalse(setup["audio"]["narrator_enabled"])
        self.assertEqual(setup["audio"]["music_volume"], 10)
        self.assertEqual(setup["audio"]["tts_volume"], 30)
        self.assertEqual(setup["audio"]["tts_voice"], "am_echo")
        self.assertEqual(setup["audio"]["tts_speed"], 140)
        self.assertEqual(setup["narration"]["tense"], "past")
        self.assertEqual(setup["narration"]["style"], "third_person_omniscient")
        self.assertEqual(setup["ai_settings"]["model_intelligence"], "smarter")
        self.assertEqual(setup["ai_settings"]["model_tone"], "professional")
        self.assertEqual(setup["ai_settings"]["response_length"], "descriptive")
        self.assertEqual(
            setup["ai_settings"]["allowed_content_categories"],
            ["HARM_CATEGORY_HARASSMENT"],
        )
        self.assertEqual(
            setup["ai_settings"]["additional_context"],
            "Favor investigative tension.",
        )
        self.assertEqual(setup["economy_examples"][0]["name"], "Bread")
        self.assertEqual(setup["start_location_mode"], "exact")
        self.assertIn("Bread costs 2 base units", setup["currency_description"])
        wizard.close()

    def test_new_game_wizard_uses_no_wheel_value_controls(self) -> None:
        _ensure_qt_application()
        wizard = NewGameWizard()

        try:
            wizard._append_starting_location_row({})
            wizard._append_starter_item_row({})

            for combo in wizard.findChildren(QComboBox):
                self.assertIsInstance(combo, _NoWheelComboBox)

            for spin_box in wizard.findChildren(QSpinBox):
                self.assertIsInstance(spin_box, _NoWheelSpinBox)
        finally:
            wizard.close()

    def test_no_wheel_value_controls_ignore_wheel_events(self) -> None:
        _ensure_qt_application()

        combo = _NoWheelComboBox()
        combo.addItem("First", "first")
        combo.addItem("Second", "second")
        combo.setCurrentIndex(0)
        combo_wheel = QEvent(QEvent.Type.Wheel)

        combo.wheelEvent(combo_wheel)

        self.assertEqual(combo.currentIndex(), 0)
        self.assertFalse(combo_wheel.isAccepted())

        spin_box = _NoWheelSpinBox()
        spin_box.setRange(0, 10)
        spin_box.setValue(5)
        spin_wheel = QEvent(QEvent.Type.Wheel)

        spin_box.wheelEvent(spin_wheel)

        self.assertEqual(spin_box.value(), 5)
        self.assertFalse(spin_wheel.isAccepted())

    def test_new_game_wizard_location_dropdowns_update_live(self) -> None:
        _ensure_qt_application()
        wizard = NewGameWizard()

        try:
            wizard._append_starting_location_row({})
            self.assertTrue(
                _table_cell(wizard.starting_locations_table, 0, 4, QComboBox).isHidden()
            )
            _table_cell(wizard.starting_locations_table, 0, 0, QLineEdit).setText(
                "Broad City"
            )
            _table_cell(wizard.starting_locations_table, 0, 1, QLineEdit).setText(
                "A compact city around a canal."
            )
            wizard._append_starting_location_row({})
            _table_cell(wizard.starting_locations_table, 1, 0, QLineEdit).setText(
                "Blacksmith Shop"
            )
            _table_cell(wizard.starting_locations_table, 1, 1, QLineEdit).setText(
                "A forge tucked into a city arcade."
            )
            _table_cell(wizard.starting_locations_table, 1, 3, QCheckBox).setChecked(
                True
            )
            parent_combo = _table_cell(
                wizard.starting_locations_table,
                1,
                4,
                QComboBox,
            )

            self.assertFalse(parent_combo.isHidden())
            self.assertNotEqual(parent_combo.findText("Broad City"), -1)
            self.assertEqual(parent_combo.findText("Blacksmith Shop"), -1)
            parent_combo.setCurrentIndex(parent_combo.findText("Broad City"))

            start_index = wizard.start_location_combo.findText("Blacksmith Shop")
            self.assertNotEqual(start_index, -1)
            wizard.start_location_combo.setCurrentIndex(start_index)

            setup = wizard.build_setup()

            self.assertEqual(setup["start_location"], "Blacksmith Shop")
            self.assertEqual(setup["starting_locations"][1]["parent_location"], "Broad City")

            _table_cell(wizard.starting_locations_table, 0, 5, QPushButton).click()

            self.assertEqual(parent_combo.findText("Broad City"), -1)
            self.assertNotEqual(wizard.start_location_combo.findText("Blacksmith Shop"), -1)
            self.assertEqual(wizard.start_location_combo.currentText(), "Blacksmith Shop")
        finally:
            wizard.close()

    def test_new_game_wizard_no_starting_npcs_confirms_and_disables_add(self) -> None:
        _ensure_qt_application()
        wizard = NewGameWizard()

        try:
            wizard._append_starting_npc_row({"name": "Guide"})

            with patch(
                "ai_adventure.ui.main_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ):
                wizard.no_starting_npcs_checkbox.setChecked(True)

            self.assertFalse(wizard.no_starting_npcs_checkbox.isChecked())
            self.assertEqual(wizard.starting_npcs_table.rowCount(), 1)
            self.assertTrue(wizard.add_npc_button.isEnabled())

            with patch(
                "ai_adventure.ui.main_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                wizard.no_starting_npcs_checkbox.setChecked(True)

            self.assertTrue(wizard.no_starting_npcs_checkbox.isChecked())
            self.assertEqual(wizard.starting_npcs_table.rowCount(), 0)
            self.assertFalse(wizard.add_npc_button.isEnabled())
            self.assertFalse(wizard.starting_npcs_table.isEnabled())
            self.assertEqual(wizard.build_setup()["starting_npcs"], [])
            self.assertTrue(wizard.build_setup()["no_starting_npcs"])

            wizard.no_starting_npcs_checkbox.setChecked(False)

            self.assertTrue(wizard.add_npc_button.isEnabled())
            self.assertTrue(wizard.starting_npcs_table.isEnabled())
        finally:
            wizard.close()

    def test_new_game_wizard_inventory_currency_page_is_scrollable(self) -> None:
        _ensure_qt_application()
        wizard = NewGameWizard()

        try:
            scroll_areas = wizard.findChildren(QScrollArea)
            inventory_scroll_area = next(
                (
                    scroll_area
                    for scroll_area in scroll_areas
                    if scroll_area.widget() is not None
                    and wizard.starter_items_table.parentWidget() is scroll_area.widget()
                ),
                None,
            )

            self.assertIsNotNone(inventory_scroll_area)
            self.assertTrue(_require(inventory_scroll_area).widgetResizable())
        finally:
            wizard.close()

    def test_new_game_wizard_ai_settings_button_updates_setup(self) -> None:
        _ensure_qt_application()
        wizard = NewGameWizard()
        fake_dialog = SimpleNamespace(
            exec=lambda: QDialog.DialogCode.Accepted,
            build_ai_settings=lambda: {
                "model_intelligence": "smarter",
                "model_tone": "friendly",
                "response_length": "brief",
                "allowed_content_categories": [
                    "HARM_CATEGORY_DANGEROUS_CONTENT"
                ],
                "narration_tense": "future",
                "narration_style": "first_person_limited",
                "additional_context": "Keep the opening hopeful.",
            },
        )

        try:
            with patch(
                "ai_adventure.ui.main_window.AISettingsDialog",
                return_value=fake_dialog,
            ):
                wizard.ai_settings_button.click()

            setup = wizard.build_setup()
            self.assertEqual(
                setup["ai_settings"]["model_intelligence"],
                "smarter",
            )
            self.assertEqual(setup["ai_settings"]["model_tone"], "friendly")
            self.assertEqual(setup["ai_settings"]["response_length"], "brief")
            self.assertEqual(
                setup["ai_settings"]["allowed_content_categories"],
                ["HARM_CATEGORY_DANGEROUS_CONTENT"],
            )
            self.assertEqual(setup["narration"]["tense"], "future")
            self.assertEqual(
                setup["narration"]["style"],
                "first_person_limited",
            )
            self.assertIn("Smarter", wizard.ai_settings_summary_label.text())
            self.assertIn("Friendly", wizard.ai_settings_summary_label.text())
        finally:
            wizard.close()

    def test_new_game_wizard_accepts_partial_template_shell(self) -> None:
        _ensure_qt_application()
        wizard = NewGameWizard(
            template_setup={
                "title": "",
                "character": {"name": ""},
                "specified_genre": "Cozy mystery",
                "starter_items": ["A clue kit with several ordinary investigative tools"],
            }
        )

        try:
            self.assertEqual(wizard.title_input.text(), "New Adventure")
            self.assertEqual(wizard.character_name_input.text(), "Player Name")
            self.assertEqual(wizard.genre_input.text(), "Cozy mystery")
            self.assertEqual(wizard.starter_items_table.rowCount(), 1)
            self.assertEqual(_table_cell(wizard.starter_items_table, 0, 0, QLineEdit).text(), "")
            self.assertEqual(_table_cell(wizard.starter_items_table, 0, 3, QLineEdit).text(), "")
        finally:
            wizard.close()

    def test_new_game_wizard_uses_shared_calendar_settings_dialog_for_custom_calendar(self) -> None:
        _ensure_qt_application()
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
        _ensure_qt_application()
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
        _ensure_qt_application()
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
            self.assertIsInstance(_table_cell(wizard.starter_items_table, 0, 0, QLineEdit), QLineEdit)
            item_quantity = _table_cell(wizard.starter_items_table, 0, 1, QSpinBox)
            item_category = _table_cell(wizard.starter_items_table, 0, 2, QLineEdit)
            item_remove_container = wizard.starter_items_table.cellWidget(0, 5)
            item_remove = item_remove_container.findChild(QCheckBox)
            self.assertEqual(item_quantity.value(), 2)
            self.assertEqual(
                item_quantity.minimumWidth(),
                item_category.minimumWidth(),
            )
            self.assertEqual(
                wizard.starter_items_table.columnWidth(1),
                wizard.starter_items_table.columnWidth(4),
            )
            item_quantity.stepDown()
            self.assertEqual(item_quantity.value(), 1)
            item_quantity.stepUp()
            self.assertEqual(item_quantity.value(), 2)
            item_remove.setChecked(True)
            wizard.starter_items_table.remove_checked_rows()
            self.assertEqual(wizard.starter_items_table.rowCount(), 0)

            wizard._append_starter_weapon_row(
                {
                    "name": "Rail Pistol",
                    "quantity": 1,
                    "weapon_hands": "one-handed",
                    "damage": "1d8",
                    "attack_skill": "Ranged",
                    "attack_range_feet": 80,
                    "ammunition_type_required": "Rail Cells",
                    "clip_size": 6,
                }
            )
            wizard._append_starter_armor_row(
                {
                    "name": "Vac Suit",
                    "quantity": 1,
                    "covers_body_parts": ["Torso", "Arms", "Legs"],
                    "armor_rating": 2,
                    "value_base_units": 30,
                }
            )

            self.assertEqual(wizard.starter_weapons_table.rowCount(), 1)
            self.assertEqual(wizard.starter_armor_table.rowCount(), 1)
            self.assertEqual(
                _table_cell(wizard.starter_weapons_table, 0, 3, QLineEdit).text(),
                "1d8",
            )
            self.assertEqual(
                _table_cell(wizard.starter_armor_table, 0, 2, QLineEdit).text(),
                "Torso, Arms, Legs",
            )

            setup = wizard.build_setup()
            weapon = next(item for item in setup["starter_items"] if item["name"] == "Rail Pistol")
            armor = next(item for item in setup["starter_items"] if item["name"] == "Vac Suit")
            self.assertEqual(weapon["category"], "Weapon")
            self.assertEqual(weapon["item_type"], "Weapon")
            self.assertEqual(weapon["damage"], "1d8")
            self.assertEqual(weapon["attack_skill"], "Ranged")
            self.assertEqual(weapon["attack_range_feet"], 80)
            self.assertEqual(weapon["ammunition_type_required"], "Rail Cells")
            self.assertEqual(weapon["clip_size"], 6)
            self.assertEqual(weapon["bullets_per_attack"], 1)
            self.assertEqual(armor["category"], "Armor")
            self.assertEqual(armor["item_type"], "Armor")
            self.assertEqual(armor["covers_body_parts"], ["Torso", "Arms", "Legs"])
            self.assertEqual(armor["armor_rating"], 2)

            wizard._append_currency_row({"name": "Bit", "plural_name": "Bits", "value": 1})
            wizard._append_currency_row({"name": "Crown", "plural_name": "Crowns", "value": 12})

            self.assertEqual(wizard.currency_table.rowCount(), 2)
            self.assertEqual(
                wizard.currency_table.selectionMode(),
                QTableWidget.SelectionMode.NoSelection,
            )
            self.assertIsInstance(_table_cell(wizard.currency_table, 1, 0, QLineEdit), QLineEdit)
            base_currency_value = _table_cell(wizard.currency_table, 0, 2, QSpinBox)
            crown_currency_value = _table_cell(wizard.currency_table, 1, 2, QSpinBox)
            crown_currency_name = _table_cell(wizard.currency_table, 1, 0, QLineEdit)
            base_currency_remove = wizard.currency_table.cellWidget(0, 3).findChild(QCheckBox)
            crown_currency_remove = wizard.currency_table.cellWidget(1, 3).findChild(QCheckBox)
            self.assertFalse(base_currency_value.isEnabled())
            self.assertEqual(base_currency_value.value(), 1)
            self.assertEqual(
                base_currency_value.buttonSymbols(),
                QAbstractSpinBox.ButtonSymbols.NoButtons,
            )
            self.assertTrue(base_currency_remove.isHidden())
            self.assertTrue(crown_currency_value.isEnabled())
            self.assertEqual(
                crown_currency_value.buttonSymbols(),
                QAbstractSpinBox.ButtonSymbols.UpDownArrows,
            )
            self.assertFalse(crown_currency_remove.isHidden())
            self.assertEqual(
                crown_currency_value.minimumWidth(),
                crown_currency_name.minimumWidth(),
            )
            self.assertEqual(wizard.currency_table.columnWidth(2), 132)
            crown_currency_value.stepDown()
            self.assertEqual(crown_currency_value.value(), 11)
            crown_currency_value.stepUp()
            self.assertEqual(crown_currency_value.value(), 12)
            base_currency_remove.setChecked(True)
            wizard.currency_table.remove_checked_rows(preserve_first_row=True)
            self.assertEqual(wizard.currency_table.rowCount(), 2)
            crown_currency_remove.setChecked(True)
            wizard.currency_table.remove_checked_rows(preserve_first_row=True)
            self.assertEqual(wizard.currency_table.rowCount(), 1)
            self.assertEqual(_table_cell(wizard.currency_table, 0, 0, QLineEdit).text(), "Bit")
            remaining_currency_value = _table_cell(wizard.currency_table, 0, 2, QSpinBox)
            self.assertFalse(remaining_currency_value.isEnabled())
            self.assertEqual(remaining_currency_value.value(), 1)
            self.assertEqual(
                remaining_currency_value.buttonSymbols(),
                QAbstractSpinBox.ButtonSymbols.NoButtons,
            )

            wizard._append_currency_row({})
            self.assertEqual(
                _table_cell(wizard.currency_table, 1, 2, QSpinBox).value(),
                10,
            )

            wizard._append_economy_example_row({"name": "Bread", "value_base_units": 2})
            self.assertEqual(wizard.economy_examples_table.rowCount(), 1)
            self.assertEqual(_table_cell(wizard.economy_examples_table, 0, 0, QLineEdit).text(), "Bread")
            self.assertEqual(_table_cell(wizard.economy_examples_table, 0, 1, QSpinBox).value(), 2)
            economy_remove = wizard.economy_examples_table.cellWidget(0, 2).findChild(QCheckBox)
            economy_remove.setChecked(True)
            wizard.economy_examples_table.remove_checked_rows()
            self.assertEqual(wizard.economy_examples_table.rowCount(), 0)
        finally:
            wizard.close()

    def test_new_game_wizard_lightweight_mode_hides_narrator_controls(self) -> None:
        _ensure_qt_application()
        wizard = NewGameWizard(
            tts_enabled=False,
            template_setup={
                "audio": {
                    "music_enabled": True,
                    "narrator_enabled": True,
                    "music_volume": 25,
                    "tts_volume": 90,
                    "tts_voice": "am_echo",
                    "tts_speed": 120,
                },
            },
        )

        try:
            labels = [label.text() for label in wizard.findChildren(QLabel)]
            setup = wizard.build_setup()

            self.assertIsNone(wizard.narrator_enabled_checkbox)
            self.assertIsNone(wizard.tts_volume_slider)
            self.assertIsNone(wizard.tts_speed_slider)
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
        _ensure_qt_application()
        wizard = NewGameWizard(
            audio_defaults={
                "music_enabled": False,
                "narrator_enabled": False,
                "music_volume": 8,
                "tts_volume": 22,
                "tts_voice": "am_echo",
                "tts_speed": 125,
            }
        )

        try:
            self.assertFalse(_require(wizard.music_enabled_checkbox).isChecked())
            self.assertFalse(_require(wizard.narrator_enabled_checkbox).isChecked())
            self.assertEqual(_require(wizard.music_volume_slider).value(), 8)
            self.assertEqual(_require(wizard.tts_volume_slider).value(), 22)
            self.assertEqual(_require(wizard.tts_speed_slider).value(), 125)
            self.assertEqual(_require(wizard.tts_voice_combo).currentData(), "am_echo")
        finally:
            wizard.close()

    def test_new_game_wizard_persists_saved_custom_voice_for_next_wizard(self) -> None:
        _ensure_qt_application()
        saved_audio_settings = []
        wizard = NewGameWizard(
            audio_defaults={
                "music_enabled": True,
                "narrator_enabled": True,
                "music_volume": 25,
                "tts_volume": 90,
                "tts_speed": 100,
                "tts_voice_mode": "blend",
            },
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
            on_tts_settings_saved=saved_audio_settings.append,
        )

        try:
            widget = _require(wizard.tts_settings_widget)
            saved_voice_audio = {
                "narrator_enabled": True,
                "tts_volume": 41,
                "tts_voice": "af_sarah",
                "tts_speed": 132,
                "tts_voice_mode": "blend",
                "tts_voice_blend": {
                    "name": "Storm Blend",
                    "voice_a": "af_sarah",
                    "voice_b": "am_echo",
                    "voice_a_weight": 72,
                    "tts_volume": 41,
                    "tts_speed": 132,
                },
                "tts_custom_voices": [
                    {
                        "name": "Storm Blend",
                        "voice_a": "af_sarah",
                        "voice_b": "am_echo",
                        "voice_a_weight": 72,
                        "tts_volume": 41,
                        "tts_speed": 132,
                    }
                ],
            }
            fake_dialog = SimpleNamespace(
                custom_voice_library_changed=True,
                exec=lambda: QDialog.DialogCode.Accepted,
                build_audio_settings=lambda: saved_voice_audio,
            )

            with patch(
                "ai_adventure.ui.main_window.CustomVoiceDialog",
                return_value=fake_dialog,
            ):
                _require(widget.custom_voice_button).click()

            self.assertEqual(len(saved_audio_settings), 1)
            self.assertEqual(
                saved_audio_settings[0]["tts_custom_voices"][0]["name"],
                "Storm Blend",
            )
            self.assertEqual(
                saved_audio_settings[0]["tts_custom_voices"][0]["tts_volume"],
                41,
            )
            self.assertEqual(
                saved_audio_settings[0]["tts_custom_voices"][0]["tts_speed"],
                132,
            )
        finally:
            wizard.close()

        next_wizard = NewGameWizard(
            audio_defaults={
                "music_enabled": True,
                "music_volume": 25,
                **saved_audio_settings[0],
            },
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
        )

        try:
            next_widget = _require(next_wizard.tts_settings_widget)
            self.assertEqual(len(next_widget.custom_voices), 1)
            self.assertIn(
                "Storm Blend",
                _require(next_widget.custom_voice_summary_label).text(),
            )

            dialog = CustomVoiceDialog(
                audio_settings=next_widget.build_audio_settings(),
                voice_options={
                    "Sarah (Female, US)": "af_sarah",
                    "Echo (Male, US)": "am_echo",
                },
            )
            dialog.custom_voice_combo.setCurrentIndex(1)
            dialog.load_custom_voice_button.click()

            self.assertIn("Storm Blend", dialog.current_voice_label.text())
            self.assertEqual(dialog.voice_a_weight_slider.value(), 72)
            self.assertEqual(dialog.voice_b_weight_slider.value(), 28)
            self.assertEqual(_require(next_widget.tts_volume_slider).value(), 41)
            self.assertEqual(_require(next_widget.tts_speed_slider).value(), 132)
            dialog.close()
        finally:
            next_wizard.close()

    def test_new_game_wizard_sample_voice_uses_selected_voice_and_volume(self) -> None:
        _ensure_qt_application()
        samples = []
        wizard = NewGameWizard(
            voice_options={
                "Sarah (Female, US)": "af_sarah",
                "Echo (Male, US)": "am_echo",
            },
            on_sample_voice=lambda voice, volume, _speed: samples.append((voice, volume)) or True,
        )

        try:
            _set_combo_to_data(_require(wizard.tts_voice_combo), "am_echo")
            _require(wizard.tts_volume_slider).setValue(44)
            _require(wizard.sample_voice_button).click()

            self.assertEqual(samples, [("am_echo", 44)])
        finally:
            wizard.close()

    def test_new_game_wizard_light_theme_uses_readable_contrast(self) -> None:
        _ensure_qt_application()
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
        _ensure_qt_application()
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
        _ensure_qt_application()
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
            self.assertTrue(
                all(table.isColumnHidden(0) for table in wizard.skill_tables.values())
            )
            wizard.skill_preset_combo.setCurrentIndex(
                wizard.skill_preset_combo.findData("custom")
            )
            self.assertTrue(
                all(not table.isColumnHidden(0) for table in wizard.skill_tables.values())
            )
            self.assertTrue(
                any(
                    button.text() == "Remove Selected Skills" and button.isVisible()
                    for button in wizard.findChildren(QPushButton)
                )
            )
            wizard._add_starting_skill_row(3, "First", "First description.")
            wizard._add_starting_skill_row(3, "Middle", "Middle description.")
            wizard._add_starting_skill_row(3, "Last", "Last description.")
            level_three_table = wizard.skill_tables[3]
            remove_container = level_three_table.cellWidget(1, 0)
            self.assertIsNotNone(remove_container)
            remove_checkbox = remove_container.findChild(QCheckBox)
            self.assertIsNotNone(remove_checkbox)
            remove_checkbox.setChecked(True)
            wizard._remove_selected_starting_skill_rows(3)
            self.assertEqual(level_three_table.rowCount(), 2)
            self.assertEqual(level_three_table.cellWidget(0, 1).text(), "First")
            self.assertEqual(level_three_table.cellWidget(1, 1).text(), "Last")

            wizard.skill_preset_combo.setCurrentIndex(
                wizard.skill_preset_combo.findData("professional")
            )

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
