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
from PySide6.QtWidgets import QApplication, QDialog, QGroupBox, QTableWidget

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.main_window import (
    AlchemyNotebookScreen,
    GameShell,
    HistoryScreen,
    MainWindow,
    NewGameWizard,
    SettingsScreen,
    StoryScreen,
    apply_application_theme,
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

            self.assertIn("- Take action.", screen.story_output.toPlainText())
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
            self.assertIn("> Look under the counter.", screen.story_output.toPlainText())
            self.assertEqual(screen.player_input.text(), "")
            self.assertFalse(screen.player_input.isEnabled())
            self.assertFalse(screen.submit_button.isEnabled())
            self.assertEqual(screen.player_input.placeholderText(), "GM is thinking...")
            self.assertEqual(screen.player_input.toolTip(), "GM is thinking...")
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

            self.assertEqual(len(item_names), 1)
            self.assertIn("Blue-Wick Warning Lantern", item_names)
            self.assertNotIn("Sealed Map Case", item_names)
            self.assertNotIn(
                "A sealed map case with routes to the coast.",
                item_names,
            )
            window.close()

    def test_alchemy_reagent_selection_populates_form_without_table_editing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Crafting UI Test")
            repository.add_alchemy_reagent(
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

            reagent = repository.list_alchemy_reagents()[0]
            self.assertEqual(reagent["location"], "Silver mine walls")
            self.assertEqual(reagent["uses"], ["mirror inks"])
            self.assertEqual(screen.reagent_name_input.text(), "")
            screen.close()

    def test_recipe_ingredients_use_known_reagent_dropdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            QApplication.instance() or QApplication([])
            repository = SaveRepository.create_new_save(Path(temp_dir), "Recipe UI Test")
            repository.add_alchemy_reagent(
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

            recipe = repository.list_alchemy_recipes()[0]
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
            shell.settings_screen.days_per_week_input.setValue(8)
            QApplication.processEvents()

            self.assertEqual(repository.get_setting("audio.music_volume"), 42)
            self.assertEqual(repository.get_calendar_settings()["days_per_week"], 8)
            self.assertEqual(shell.calendar_screen.table.columnCount(), 8)
            shell.close()

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
            self.assertIn("QSpinBox::up-button", app.styleSheet())
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
            self.assertIn("QSpinBox::up-button", app.styleSheet())
            self.assertIn("QSpinBox::down-button", app.styleSheet())
        finally:
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
        self.assertIn("Notebook | Tool | 1 | Case notes. | 4", wizard.starter_items_input.toPlainText())
        self.assertEqual(wizard.currency_table.rowCount(), 2)
        self.assertEqual(wizard.currency_table.item(1, 0).text(), "Crown")
        self.assertEqual(wizard.time_format_combo.currentData(), "24_hour")
        self.assertEqual(wizard.calendar_type_combo.currentData(), "gregorian")
        self.assertFalse(wizard.music_enabled_checkbox.isChecked())
        self.assertFalse(wizard.narrator_enabled_checkbox.isChecked())
        self.assertEqual(wizard.music_volume_slider.value(), 10)
        self.assertEqual(wizard.tts_volume_slider.value(), 30)
        setup = wizard.build_setup()
        self.assertEqual(setup["skills"][0]["description"], "Skill 0 description.")
        self.assertFalse(setup["skills"][0]["requires_ai_invention"])
        self.assertFalse(setup["audio"]["music_enabled"])
        self.assertFalse(setup["audio"]["narrator_enabled"])
        self.assertEqual(setup["audio"]["music_volume"], 10)
        self.assertEqual(setup["audio"]["tts_volume"], 30)
        wizard.close()

    def test_new_game_wizard_light_theme_uses_readable_contrast(self) -> None:
        QApplication.instance() or QApplication([])
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
            self.assertIn("color: #111827", wizard.styleSheet())
            self.assertIn("background-color: #ffffff", wizard.styleSheet())
            self.assertNotIn("color: #f3f4f6", wizard.styleSheet())
        finally:
            wizard.close()

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
