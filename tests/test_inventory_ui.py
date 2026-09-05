from __future__ import annotations

from copy import deepcopy
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QThread, QTime, QTimer, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLayoutItem,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.ui.screens.notes import NotesScreen
from ai_adventure.new_game_templates import (
    load_new_game_templates,
    save_new_game_template,
)
from ai_adventure.ui.main_window import (
    _DetachedTabWindow,
    _GeminiNewGameWorker,
    AISettingsDialog,
    AlchemyNotebookScreen,
    BestiaryScreen,
    CalendarPlayerEventDialog,
    CalendarScreen,
    CharacterScreen,
    CombatScreen,
    GameShell,
    InventoryItemDetailsDialog,
    InventoryLocationPanel,
    InventoryScreen,
    MagicScreen,
    MainWindow,
    NewGameTemplateManagerDialog,
    NewGameWizard,
    NpcsScreen,
    PartyScreen,
    StoryScreen,
    _NoWheelSpinBox,
    _append_starter_suggestion_table_row,
    _apply_audio_settings_to_managers,
    _inventory_item_display_name,
    _inventory_quantity_display,
)


class InventoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_data_temp_dir = tempfile.TemporaryDirectory(
            prefix="ai_adventure_qt_tests_"
        )
        cls.app_data_env_patcher = patch.dict(
            os.environ,
            {
                "APPDATA": cls.app_data_temp_dir.name,
                "LOCALAPPDATA": cls.app_data_temp_dir.name,
            },
            clear=False,
        )
        cls.app_data_env_patcher.start()
        cls.addClassCleanup(cls.app_data_env_patcher.stop)
        cls.addClassCleanup(cls.app_data_temp_dir.cleanup)
        cls.app = QApplication.instance() or QApplication([])

    def test_new_game_gemini_worker_keeps_qt_event_loop_responsive(self) -> None:
        test_case = self
        release_request = threading.Event()
        heartbeat_seen: list[bool] = []
        request_threads: list[QThread] = []
        result_marker = object()
        results: list[object] = []
        service_models: list[object] = []
        setup_packet = {
            "title": "Threaded New Game",
            "player_ai_preferences": {"text_model": "gemini-3.7-flash"},
        }

        class FakeGeminiService:
            def __init__(self, **kwargs: object) -> None:
                service_models.append(kwargs.get("model"))

            def generate_new_game_world(self, packet: dict[str, Any]) -> object:
                request_threads.append(QThread.currentThread())
                if not release_request.wait(timeout=2):
                    raise TimeoutError("Qt event loop did not remain responsive.")
                test_case.assertEqual(packet, setup_packet)
                return result_marker

        thread = QThread()
        worker = _GeminiNewGameWorker(setup_packet)
        worker.moveToThread(thread)
        event_loop = QEventLoop()

        thread.started.connect(worker.run)
        worker.completed.connect(results.append)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(event_loop.quit)

        def record_heartbeat() -> None:
            heartbeat_seen.append(True)
            release_request.set()

        with patch(
            "ai_adventure.ui.workers.gemini.GeminiNarrationService",
            FakeGeminiService,
        ):
            thread.start()
            QTimer.singleShot(0, record_heartbeat)
            QTimer.singleShot(3000, event_loop.quit)
            event_loop.exec()

        self.assertTrue(thread.wait(1000))
        self.assertEqual(heartbeat_seen, [True])
        self.assertEqual(results, [result_marker])
        self.assertEqual(service_models, ["gemini-3.7-flash"])
        self.assertEqual(len(request_threads), 1)
        self.assertIsNot(request_threads[0], self.app.thread())

    def test_new_game_saves_unused_template_before_gemini_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            template_path = temp_path / "new_game_templates.json"
            repository = SimpleNamespace(set_setting=Mock())
            observed_template_names: list[str] = []

            window = SimpleNamespace(
                app_paths=SimpleNamespace(
                    saves_dir=temp_path / "saves",
                    new_game_templates_path=template_path,
                    legacy_new_game_template_path=temp_path / "new_game_template.json",
                ),
                menu_theme="dark",
                ai_enabled=True,
                game_shell=SimpleNamespace(
                    story_screen=SimpleNamespace(
                        set_initial_generation_pending=Mock()
                    ),
                    menu_button=SimpleNamespace(setEnabled=Mock()),
                ),
            )
            window._normalize_new_game_setup_for_runtime = normalize_new_game_setup
            window.open_repository = Mock()

            def start_generation(_repository: object, _setup: object) -> None:
                observed_template_names.extend(
                    template.name
                    for template in load_new_game_templates(
                        template_path,
                        normalize_setups=False,
                    )
                )

            window._start_new_game_generation = start_generation

            with patch.object(
                SaveRepository,
                "create_new_save",
                return_value=repository,
            ):
                MainWindow._create_new_game_from_setup(
                    cast(MainWindow, window),
                    {
                        "title": "Gun Jam Online",
                        "character": {"name": "Kit"},
                        "specified_genre": "PvP, Combat, Tactical",
                    },
                    auto_save_template_if_available=True,
                )

            self.assertEqual(observed_template_names, ["Gun Jam Online"])
            stored = load_new_game_templates(template_path, normalize_setups=False)
            self.assertEqual(stored[0].setup["character"]["name"], "Kit")
            self.assertEqual(
                stored[0].setup["specified_genre"],
                "PvP, Combat, Tactical",
            )

    def test_new_game_wizard_supports_maximize_and_quest_guidance(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        wizard.show()
        self.app.processEvents()

        self.assertTrue(
            wizard.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
        )
        self.assertTrue(
            wizard.windowFlags() & Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.assertEqual(
            wizard.windowFlags() & Qt.WindowType.WindowType_Mask,
            Qt.WindowType.Window,
        )
        self.assertGreater(wizard.maximumWidth(), wizard.width())
        stylesheet = wizard.styleSheet()
        self.assertIn("QLabel#newGameWizardPageTitle", stylesheet)
        self.assertIn("font-size: 26px", stylesheet)
        self.assertIn("QLabel#newGameWizardPageSubtitle", stylesheet)
        self.assertIn("font-size: 17px", stylesheet)
        title_label = wizard.findChild(QLabel, "newGameWizardPageTitle")
        subtitle_label = wizard.findChild(QLabel, "newGameWizardPageSubtitle")
        self.assertIsNotNone(title_label)
        self.assertIsNotNone(subtitle_label)
        assert title_label is not None
        assert subtitle_label is not None
        self.assertEqual(
            title_label.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Fixed,
        )
        self.assertEqual(
            subtitle_label.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Fixed,
        )
        self.assertEqual(title_label.minimumHeight(), title_label.maximumHeight())
        self.assertEqual(subtitle_label.minimumHeight(), subtitle_label.maximumHeight())
        self.assertLess(title_label.maximumHeight(), 100)
        self.assertLess(subtitle_label.maximumHeight(), 100)
        ai_index = wizard.starting_task_mode_combo.findData("ai")
        wizard.starting_task_mode_combo.setCurrentIndex(ai_index)
        wizard.starting_task_guidance_input.setPlainText(
            "Begin with a mystery involving a missing courier."
        )
        self.app.processEvents()

        self.assertFalse(wizard.starting_task_guidance_group.isHidden())
        self.assertTrue(wizard.starting_task_custom_group.isHidden())
        self.assertEqual(
            wizard._starting_task_from_controls()["guidance"],
            "Begin with a mystery involving a missing courier.",
        )
        wizard.close()

    def test_new_game_wizard_has_dedicated_ga_model_settings_page(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        page_titles = [wizard.page(page_id).title() for page_id in wizard.pageIds()]

        self.assertEqual(page_titles[:2], ["Adventure", "A.I. Settings"])
        self.assertFalse(hasattr(wizard, "ai_settings_button"))
        self.assertEqual(wizard.text_model_combo.count(), 8)
        self.assertEqual(wizard.image_model_combo.count(), 4)
        self.assertEqual(wizard.image_style_combo.count(), 12)

        text_index = wizard.text_model_combo.findData("gemini-3.7-flash")
        image_index = wizard.image_model_combo.findData("gemini-3-pro-image")
        style_index = wizard.image_style_combo.findData("oil_painting")
        wizard.text_model_combo.setCurrentIndex(text_index)
        wizard.image_model_combo.setCurrentIndex(image_index)
        wizard.image_style_combo.setCurrentIndex(style_index)
        wizard.smarter_ai_checkbox.setChecked(True)
        wizard.generated_images_enabled_checkbox.setChecked(False)
        wizard.additional_ai_context_input.setPlainText("Keep the pacing tense.")
        self.app.processEvents()

        self.assertIn("next iteration", wizard.text_model_description.text())
        self.assertIn("professional-grade", wizard.image_model_description.text())
        self.assertIn("visible brushwork", wizard.image_style_description.text())
        text_bars = wizard.text_model_ratings.findChildren(QProgressBar)
        image_bars = wizard.image_model_ratings.findChildren(QProgressBar)
        self.assertEqual(
            [bar.format() for bar in text_bars],
            ["Cost: 4/5", "Intelligence: 5/5", "Speed: 3/5"],
        )
        self.assertEqual(
            [bar.format() for bar in image_bars],
            ["Cost: 5/5", "Quality: 5/5", "Speed: 2/5"],
        )
        self.assertFalse(wizard.image_model_combo.isEnabled())
        self.assertFalse(wizard.image_style_combo.isEnabled())
        setup = wizard.build_setup()
        self.assertEqual(setup["ai_settings"]["text_model"], "gemini-3.7-flash")
        self.assertEqual(setup["ai_settings"]["model_intelligence"], "smarter")
        self.assertEqual(
            setup["ai_settings"]["additional_context"],
            "Keep the pacing tense.",
        )
        self.assertEqual(
            setup["images"],
            {
                "enabled": False,
                "model": "gemini-3-pro-image",
                "style": "oil_painting",
            },
        )
        wizard.close()

    def test_in_game_ai_settings_dialog_keeps_existing_mode_controls(self) -> None:
        dialog = AISettingsDialog()

        self.assertEqual(dialog.model_intelligence_combo.count(), 2)
        self.assertEqual(
            [
                dialog.model_intelligence_combo.itemText(index)
                for index in range(dialog.model_intelligence_combo.count())
            ],
            ["Faster", "Smarter"],
        )
        self.assertFalse(hasattr(dialog, "text_model_combo"))
        dialog.close()

    def test_template_selection_reuses_widgets_without_mutating_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "new_game_templates.json"
            first_setup = {
                "specified_genre": "Mystery",
                "starting_locations": [
                    {"name": "Office", "description": "A cramped office."},
                    {"name": "Street", "description": "A rain-soaked street."},
                ],
                "starting_npcs": [
                    {"name": "Client", "location": "Office", "description": "Nervous."}
                ],
                "starter_items": [
                    {"name": "Notebook", "category": "Item", "quantity": 1},
                    {
                        "name": "Revolver",
                        "category": "Weapon",
                        "item_type": "Weapon",
                        "quantity": 1,
                    },
                ],
                "currency_denominations": [
                    {"name": "dollar", "plural_name": "dollars", "value": 1}
                ],
                "economy_examples": [
                    {"name": "Coffee", "value_base_units": 1}
                ],
            }
            second_setup = {
                "specified_genre": "Fantasy",
                "starting_locations": [
                    {"name": "Keep", "description": "A stone keep."}
                ],
                "starting_npcs": [],
                "no_starting_npcs": True,
                "starter_items": [],
                "currency_denominations": [],
                "economy_examples": [],
            }
            self.assertTrue(
                save_new_game_template(
                    template_path,
                    first_setup,
                    template_name="A Mystery",
                    normalize_setup=False,
                )
            )
            self.assertTrue(
                save_new_game_template(
                    template_path,
                    second_setup,
                    template_name="B Fantasy",
                    normalize_setup=False,
                )
            )
            original_file = template_path.read_bytes()
            dialog = NewGameTemplateManagerDialog(template_path=template_path)
            original_setups = [deepcopy(template.setup) for template in dialog.templates]
            pooled_widget_ids = {
                id(widget)
                for table in (
                    dialog.starting_locations_table,
                    dialog.starting_npcs_table,
                    dialog.starter_items_table,
                    dialog.starter_weapons_table,
                    dialog.starter_armor_table,
                    dialog.currency_table,
                    dialog.economy_examples_table,
                    *dialog.skill_tables.values(),
                )
                for row in range(table.rowCount())
                for column in range(table.columnCount())
                if (widget := table.cellWidget(row, column)) is not None
            }

            with patch("ai_adventure.ui.main_window.NewGameWizard") as wizard_type:
                for row in (1, 0, 1, 0):
                    dialog.template_list.setCurrentRow(row)
                    self.app.processEvents()

            current_widget_ids = {
                id(widget)
                for table in (
                    dialog.starting_locations_table,
                    dialog.starting_npcs_table,
                    dialog.starter_items_table,
                    dialog.starter_weapons_table,
                    dialog.starter_armor_table,
                    dialog.currency_table,
                    dialog.economy_examples_table,
                    *dialog.skill_tables.values(),
                )
                for row in range(table.rowCount())
                for column in range(table.columnCount())
                if (widget := table.cellWidget(row, column)) is not None
            }
            self.assertEqual(current_widget_ids, pooled_widget_ids)
            self.assertEqual(
                [template.setup for template in dialog.templates],
                original_setups,
            )
            self.assertEqual(template_path.read_bytes(), original_file)
            wizard_type.assert_not_called()
            self.assertEqual(dialog.genre_input.text(), "Mystery")
            self.assertEqual(
                len(dialog._starting_locations_from_table()),
                2,
            )
            self.assertEqual(len(dialog._starting_npcs_from_table()), 1)
            dialog.genre_input.setText("Thriller")
            dialog._save_template()
            saved_templates = load_new_game_templates(
                template_path,
                normalize_setups=False,
            )
            saved_mystery = next(
                template
                for template in saved_templates
                if template.name == "A Mystery"
            )
            self.assertEqual(saved_mystery.setup["specified_genre"], "Thriller")
            dialog.close()

    def test_new_game_wizard_calendar_settings_button_opens_dialog(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        custom_index = wizard.calendar_type_combo.findData("custom")
        wizard.calendar_type_combo.setCurrentIndex(custom_index)
        self.app.processEvents()

        with patch("ai_adventure.ui.main_window.CalendarSettingsDialog") as dialog_type:
            dialog_type.return_value.exec.return_value = 0
            wizard.calendar_settings_button.click()

        dialog_type.assert_called_once_with(
            wizard._custom_calendar_settings,
            wizard,
        )
        wizard.close()

    def test_new_game_wizard_builds_authoritative_start_conditions(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        wizard.calendar_start_year_input.setValue(3)
        wizard.calendar_start_month_input.setValue(2)
        wizard.calendar_start_day_input.setValue(6)
        wizard.calendar_start_time_checkbox.setChecked(True)
        wizard.calendar_start_time_input.setTime(QTime(21, 15))
        wizard.calendar_start_weather_input.setText("Heavy Snow")
        wizard.background_ambience_enabled_checkbox.setChecked(True)
        wizard.background_ambience_volume_slider.setValue(12)

        setup = wizard.build_setup()

        self.assertEqual(
            setup["starting_calendar"],
            {
                "year": 3,
                "month_number": 2,
                "day_of_month": 6,
                "time_of_day_minutes": 1275,
            },
        )
        self.assertEqual(setup["starting_weather"], "Heavy Snow")
        self.assertTrue(setup["audio"]["background_ambience_enabled"])
        self.assertEqual(setup["audio"]["background_ambience_volume"], 12)
        wizard.close()

    def test_new_game_wizard_can_preview_a_sound_effect(self) -> None:
        class FakeSoundManager:
            def __init__(self) -> None:
                self.effect_volume: float | int | None = None
                self.effect_played = ""

            def get_valid_sound_effect_names(self) -> list[str]:
                return ["Rain.wav"]

            def set_sound_effects_volume(self, volume: float | int | None) -> None:
                self.effect_volume = volume

            def play_sound_effect(
                self,
                track_name_or_path: str | Path | None,
            ) -> None:
                self.effect_played = str(track_name_or_path or "")

            def stop_music(self, *, clear_current: bool = True) -> None:
                pass

        manager = FakeSoundManager()
        wizard = NewGameWizard(
            tts_enabled=False,
            sound_manager=cast(Any, manager),
        )
        wizard.sound_effects_volume_slider.setValue(42)
        wizard.sound_effects_test_button.click()

        self.assertEqual(manager.effect_volume, 42)
        self.assertEqual(manager.effect_played, "Rain.wav")
        wizard.close()

    def test_new_game_wizard_can_preview_background_ambience(self) -> None:
        class FakeSoundManager:
            def __init__(self) -> None:
                self.ambience_volume: float | int | None = None
                self.ambience_played = ""

            def get_valid_background_ambience_names(self) -> list[str]:
                return ["Quiet Rain.ogg"]

            def set_background_ambience_volume(
                self,
                volume: float | int | None,
            ) -> None:
                self.ambience_volume = volume

            def play_background_ambience(
                self,
                track_name_or_path: str | Path | None,
            ) -> None:
                self.ambience_played = str(track_name_or_path or "")

        manager = FakeSoundManager()
        wizard = NewGameWizard(
            tts_enabled=False,
            sound_manager=cast(Any, manager),
        )
        wizard.background_ambience_volume_slider.setValue(18)
        wizard.background_ambience_test_button.click()

        self.assertEqual(manager.ambience_volume, 18)
        self.assertEqual(manager.ambience_played, "Quiet Rain.ogg")
        wizard.close()

    def test_new_game_wizard_round_trips_magic_configuration(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        wizard.load_setup(
            {
                "magic": {
                    "enabled": True,
                    "casting_mode": "tiered",
                    "tradition": "Starlight",
                    "tier_slots": {1: 3, 2: 1},
                    "starting_spells_mode": "advanced",
                    "starting_spells": [
                        {
                            "name": "Star Spark",
                            "tier": 0,
                            "school": "Astral",
                            "description": "Creates a bright stellar spark.",
                            "mana_cost": 0,
                            "prepared": True,
                        }
                    ],
                }
            }
        )

        magic = wizard.build_setup()["magic"]

        self.assertTrue(magic["enabled"])
        self.assertEqual(magic["casting_mode"], "tiered")
        self.assertEqual(magic["tradition"], "Starlight")
        self.assertEqual(magic["tier_slots"][1], 3)
        self.assertEqual(magic["starting_spells"][0]["name"], "Star Spark")
        wizard.close()

    def test_new_game_wizard_no_magic_checkbox_hides_and_restores_options(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        self.assertEqual(
            wizard.no_world_magic_checkbox.text(),
            "This world does not contain magic.",
        )
        wizard.magic_enabled_checkbox.setChecked(True)
        wizard.magic_tradition_input.setText("Starlight")
        wizard._set_starting_spells_mode("advanced")
        wizard._append_starting_spell_row(
            {"name": "Star Spark", "description": "Creates a stellar spark."}
        )

        wizard.no_world_magic_checkbox.setChecked(True)
        self.app.processEvents()
        disabled_magic = wizard.build_setup()["magic"]

        self.assertTrue(wizard.magic_options_container.isHidden())
        self.assertFalse(disabled_magic["world_contains_magic"])
        self.assertFalse(disabled_magic["player_magic_enabled"])
        self.assertFalse(disabled_magic["enabled"])
        self.assertEqual(disabled_magic["starting_spells"], [])

        wizard.no_world_magic_checkbox.setChecked(False)
        self.app.processEvents()
        restored_magic = wizard.build_setup()["magic"]

        self.assertFalse(wizard.magic_options_container.isHidden())
        self.assertTrue(wizard.magic_options_container.isEnabled())
        self.assertTrue(wizard.magic_enabled_checkbox.isChecked())
        self.assertEqual(wizard.magic_tradition_input.text(), "Starlight")
        self.assertEqual(wizard.starting_spells_table.rowCount(), 1)
        self.assertTrue(restored_magic["world_contains_magic"])
        self.assertTrue(restored_magic["enabled"])
        wizard.close()

    def test_new_game_wizard_keeps_world_magic_details_when_player_casting_is_off(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        wizard.magic_enabled_checkbox.setChecked(True)
        wizard.magic_casting_mode_combo.setCurrentIndex(
            wizard.magic_casting_mode_combo.findData("mana")
        )
        wizard.magic_tradition_input.setText("Starlight")
        self.app.processEvents()

        self.assertFalse(wizard.magic_player_options_scroll.isHidden())
        wizard.magic_enabled_checkbox.setChecked(False)
        self.app.processEvents()
        magic = wizard.build_setup()["magic"]

        self.assertFalse(wizard.magic_options_container.isHidden())
        self.assertFalse(wizard.magic_player_options_scroll.isHidden())
        self.assertTrue(wizard.magic_player_casting_controls_container.isHidden())
        self.assertTrue(wizard.magic_mana_group.isHidden())
        self.assertTrue(wizard.starting_spells_group.isHidden())
        self.assertFalse(magic["player_magic_enabled"])
        self.assertEqual(magic["casting_mode"], "mana")
        self.assertEqual(magic["tradition"], "Starlight")
        self.assertEqual(magic["starting_spells"], [])
        wizard.close()

    def test_new_game_wizard_casting_resources_do_not_capture_wheel_scroll(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)

        self.assertIsInstance(wizard.magic_mana_maximum_input, _NoWheelSpinBox)
        self.assertTrue(
            all(
                isinstance(slot_input, _NoWheelSpinBox)
                for slot_input in wizard.magic_tier_slot_inputs.values()
            )
        )
        wizard.close()

    def test_new_game_wizard_round_trips_standard_and_custom_pronouns(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)

        self.assertEqual(wizard.build_setup()["character"]["pronouns"], "They/Them")
        self.assertTrue(wizard.character_custom_pronouns_input.isHidden())

        wizard.load_setup({"character": {"pronouns": "She/Her"}})
        self.assertEqual(wizard.character_pronouns_combo.currentData(), "She/Her")
        self.assertEqual(wizard.build_setup()["character"]["pronouns"], "She/Her")

        wizard.load_setup({"character": {"pronouns": "Xe/Xem"}})
        self.app.processEvents()
        self.assertEqual(wizard.character_pronouns_combo.currentData(), "other")
        self.assertFalse(wizard.character_custom_pronouns_input.isHidden())
        self.assertEqual(wizard.character_custom_pronouns_input.text(), "Xe/Xem")
        self.assertEqual(wizard.build_setup()["character"]["pronouns"], "Xe/Xem")
        wizard.close()

    def test_new_game_wizard_basic_starting_spells_are_gemini_requests(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        wizard.magic_enabled_checkbox.setChecked(True)
        wizard._append_starting_spell_request_row(
            {"spell_request": "A spell that reveals fresh footprints"}
        )

        magic = wizard.build_setup()["magic"]

        self.assertEqual(magic["starting_spells_mode"], "basic")
        self.assertEqual(magic["starting_spells"], [])
        self.assertEqual(
            magic["starting_spell_requests"][0]["spell_request"],
            "A spell that reveals fresh footprints",
        )
        wizard.close()

    def test_new_game_wizard_round_trips_combat_preferences(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        wizard.load_setup(
            {"combat": {"resolution_mode": "narrative", "focus": "low"}}
        )

        combat = wizard.build_setup()["combat"]

        self.assertEqual(combat["resolution_mode"], "narrative")
        self.assertEqual(combat["focus"], "low")
        self.assertIn("Gemini narrates", wizard.combat_resolution_explanation.text())
        wizard.close()

    def test_new_game_wizard_party_tracks_the_live_starting_npc_list(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        wizard._append_starting_npc_row(
            {"npc_id": "npc_mira", "name": "Mira", "location": "Old Road"}
        )
        wizard._append_starting_npc_row(
            {"npc_id": "npc_orin", "name": "Orin", "location": "West Gate"}
        )
        self.app.processEvents()

        self.assertEqual(wizard.starting_party_npc_combo.count(), 2)
        mira_index = wizard.starting_party_npc_combo.findData("npc_mira")
        wizard.starting_party_npc_combo.setCurrentIndex(mira_index)
        wizard._add_selected_starting_party_member()
        self.assertEqual(
            wizard.build_setup()["starting_party_npc_ids"],
            ["npc_mira"],
        )

        remove_button = wizard.starting_npcs_table.cellWidget(0, 4)
        self.assertIsInstance(remove_button, QPushButton)
        assert isinstance(remove_button, QPushButton)
        self.assertEqual(remove_button.text(), "Remove")

        with patch(
            "ai_adventure.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            remove_button.click()
        self.assertEqual(wizard.starting_npcs_table.rowCount(), 2)

        with patch(
            "ai_adventure.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            remove_button.click()
        self.app.processEvents()

        self.assertEqual(wizard.starting_party_table.rowCount(), 0)
        self.assertEqual(wizard.starting_party_npc_combo.findData("npc_mira"), -1)
        self.assertEqual(
            wizard.build_setup()["starting_party_npc_ids"],
            [],
        )
        wizard.close()

    def test_new_game_wizard_hides_inactive_npc_and_party_controls(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)

        self.assertTrue(wizard.starting_party_selection_container.isHidden())
        self.assertTrue(wizard.starting_party_table.isHidden())
        self.assertFalse(wizard.starting_party_empty_label.isHidden())

        wizard.no_starting_npcs_checkbox.setChecked(True)
        self.app.processEvents()
        self.assertTrue(wizard.starting_npcs_editor_container.isHidden())

        wizard.no_starting_npcs_checkbox.setChecked(False)
        wizard._append_starting_npc_row({"name": "Mira"})
        self.app.processEvents()
        self.assertFalse(wizard.starting_npcs_editor_container.isHidden())
        self.assertFalse(wizard.starting_party_selection_container.isHidden())
        self.assertFalse(wizard.add_starting_party_member_button.isHidden())
        wizard.close()

    def test_new_game_wizard_party_page_skip_reacts_to_npc_checkbox(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        wizard.show()
        self.app.processEvents()
        wizard.setCurrentId(wizard.starting_npcs_page_id)

        wizard.no_starting_npcs_checkbox.setChecked(True)
        self.assertEqual(wizard.nextId(), wizard.character_page_id)

        wizard.no_starting_npcs_checkbox.setChecked(False)
        self.assertEqual(wizard.nextId(), wizard.starting_party_page_id)
        wizard.close()

    def test_new_game_wizard_npc_location_dropdown_tracks_locations_page(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        wizard._append_starting_location_row(
            {"name": "Old Road", "description": "A muddy trade road."}
        )
        wizard._append_starting_location_row(
            {"name": "West Gate", "description": "The city gate."}
        )
        wizard._append_starting_npc_row(
            {"npc_id": "npc_mira", "name": "Mira", "location": "Old Road"}
        )
        self.app.processEvents()

        location_combo = wizard.starting_npcs_table.cellWidget(0, 1)
        self.assertIsInstance(location_combo, QComboBox)
        assert isinstance(location_combo, QComboBox)
        self.assertEqual(
            [location_combo.itemText(index) for index in range(location_combo.count())],
            ["Select a location", "Old Road", "West Gate"],
        )
        self.assertEqual(location_combo.currentText(), "Old Road")

        old_road_name = wizard.starting_locations_table.cellWidget(0, 0)
        self.assertIsInstance(old_road_name, QLineEdit)
        assert isinstance(old_road_name, QLineEdit)
        old_road_name.setText("North Road")
        self.app.processEvents()

        self.assertEqual(location_combo.currentText(), "North Road")
        self.assertEqual(
            wizard.build_setup()["starting_npcs"][0]["location"],
            "North Road",
        )
        self.assertEqual(
            wizard.build_setup()["starting_npcs"][0]["location_source_index"],
            0,
        )

        wizard.starting_locations_table.removeRow(0)
        wizard._refresh_starting_location_dropdowns()
        self.app.processEvents()

        self.assertEqual(location_combo.currentData(), "")
        self.assertNotIn(
            "North Road",
            [location_combo.itemText(index) for index in range(location_combo.count())],
        )
        self.assertEqual(wizard.build_setup()["starting_npcs"][0]["location"], "")
        self.assertEqual(
            wizard.build_setup()["starting_npcs"][0]["location_source_index"],
            -1,
        )
        wizard.close()

    def test_combat_screen_disables_manual_start_for_narrative_combat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Narrative Combat UI"
            )
            repository.set_setting(
                "combat.preferences",
                {"resolution_mode": "narrative", "focus": "balanced"},
            )
            screen = CombatScreen(playtesting_tools=True)
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertIn("Gemini resolves fights", screen.status_label.text())
            self.assertFalse(screen.start_button.isEnabled())
            self.assertFalse(screen.add_combatant_button.isEnabled())
            screen.close()

    def test_character_identity_pronouns_and_contextual_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Character Smart Controls"
            )
            repository.set_setting("player.pronouns", "Xe/Xem")
            repository.set_setting("audio.narrator_enabled", False)
            repository.set_setting(
                "combat.preferences",
                {"resolution_mode": "narrative", "focus": "balanced"},
            )
            repository.set_state_value("condition", "Wounded")
            screen = CharacterScreen(tts_enabled=True)
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertEqual(screen.pronouns_combo.currentData(), "other")
            self.assertEqual(screen.custom_pronouns_input.text(), "Xe/Xem")
            self.assertFalse(screen.custom_pronouns_input.isHidden())
            self.assertTrue(screen.name_pronunciation_input.isHidden())
            self.assertTrue(screen.stats_group.isHidden())
            self.assertTrue(screen.equipment_group.isHidden())
            self.assertFalse(screen.condition_group.isHidden())
            self.assertEqual(screen.condition_label.text(), "Wounded")

            she_index = screen.pronouns_combo.findData("She/Her")
            screen.pronouns_combo.setCurrentIndex(she_index)
            self.app.processEvents()
            self.assertEqual(repository.get_setting("player.pronouns"), "She/Her")
            screen.close()

    def test_magic_screen_displays_known_spells_and_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Magic UI")
            repository.set_magic_configuration(
                {"enabled": True, "casting_mode": "mana", "mana_maximum": 14}
            )
            spell = repository.upsert_spell_catalog(
                name="River Shield",
                tier=1,
                school="Water",
                description="Raises a flowing defensive veil.",
                mana_cost=3,
            )
            assert spell is not None
            repository.learn_character_spell(spell["spell_id"])
            screen = MagicScreen()
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertIn("Mana Casting", screen.summary_label.text())
            self.assertIn("14/14", screen.resources_label.text())
            self.assertEqual(screen.spells_table.rowCount(), 1)
            spell_item = screen.spells_table.item(0, 0)
            self.assertIsNotNone(spell_item)
            assert spell_item is not None
            self.assertEqual(spell_item.text(), "River Shield")
            screen.close()

    def test_new_game_wizard_starter_inventory_uses_one_basic_advanced_mode(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        self.assertEqual(wizard._starter_inventory_mode(), "basic")
        self.assertEqual(wizard.starter_inventory_mode_stack.currentIndex(), 0)

        _append_starter_suggestion_table_row(
            wizard.starter_item_suggestions_table,
            "Item",
            "A compact field medicine kit",
        )
        wizard._append_starter_item_row({"name": "Exact Lantern"})
        basic_items = wizard._starter_items_from_table()
        self.assertEqual(len(basic_items), 1)
        self.assertEqual(basic_items[0]["item_request"], "A compact field medicine kit")

        wizard._set_starter_inventory_mode("advanced")
        advanced_items = wizard._starter_items_from_table()
        self.assertEqual(wizard.starter_inventory_mode_stack.currentIndex(), 1)
        self.assertEqual(len(advanced_items), 1)
        self.assertEqual(advanced_items[0]["name"], "Exact Lantern")
        wizard.close()

    def test_new_game_wizard_starting_wealth_supports_guidance_and_exact_amounts(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)
        self.assertEqual(wizard._starting_wealth_mode(), "basic")
        self.assertIn("few meals", wizard.starting_wealth_guidance_input.toPlainText())

        wizard._append_currency_row(
            {"name": "Bit", "plural_name": "Bits", "value": 1}
        )
        wizard._append_currency_row(
            {"name": "Crown", "plural_name": "Crowns", "value": 12}
        )
        wizard._set_starting_wealth_mode("advanced")
        wizard._append_starting_wealth_amount_row(
            {"denomination_value": 12, "quantity": 3}
        )
        wizard._append_starting_wealth_amount_row(
            {"denomination_value": 1, "quantity": 4}
        )

        first_combo = wizard.starting_wealth_amounts_table.cellWidget(0, 0)
        first_amount = wizard.starting_wealth_amounts_table.cellWidget(0, 1)
        self.assertIsInstance(first_combo, QComboBox)
        self.assertIsInstance(first_amount, _NoWheelSpinBox)
        assert isinstance(first_combo, QComboBox)
        self.assertEqual(
            [first_combo.itemText(index) for index in range(first_combo.count())],
            ["Bit", "Crown"],
        )

        setup = wizard.build_setup()
        self.assertEqual(setup["starting_wealth"]["mode"], "advanced")
        self.assertEqual(setup["starting_wealth"]["balance_base_units"], 40)
        self.assertIn("40 base units", wizard.starting_wealth_summary_label.text())
        wizard.close()

    def test_new_game_wizard_controls_resize_with_available_space(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)

        self.assertGreaterEqual(wizard.width(), 780)
        self.assertGreaterEqual(wizard.height(), 620)
        self.assertEqual(
            wizard.starting_locations_table.horizontalHeader().sectionResizeMode(0),
            QHeaderView.ResizeMode.Stretch,
        )
        self.assertEqual(
            wizard.starting_locations_table.horizontalHeader().sectionResizeMode(5),
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.assertEqual(
            wizard.starter_item_suggestions_table.horizontalHeader().sectionResizeMode(0),
            QHeaderView.ResizeMode.Stretch,
        )
        self.app.processEvents()
        empty_height = wizard.starter_item_suggestions_table.maximumHeight()
        self.assertLess(empty_height, 190)
        _append_starter_suggestion_table_row(
            wizard.starter_item_suggestions_table,
            "Item",
            "Field kit",
        )
        _append_starter_suggestion_table_row(
            wizard.starter_item_suggestions_table,
            "Item",
            "Weatherproof cloak",
        )
        self.app.processEvents()
        self.assertGreater(
            wizard.starter_item_suggestions_table.maximumHeight(),
            empty_height,
        )
        wizard.starter_item_suggestions_table.setRowCount(0)
        self.app.processEvents()
        self.assertEqual(
            wizard.starter_item_suggestions_table.maximumHeight(),
            empty_height,
        )
        self.assertTrue(
            hasattr(
                wizard.starter_item_suggestions_table,
                "_wheel_passthrough_filter",
            )
        )
        self.assertGreater(wizard.starting_task_guidance_input.maximumHeight(), 120)
        wizard.close()

    def test_detached_tab_window_reshows_page_hidden_by_tab_removal(self) -> None:
        host = QWidget()
        tabs = QTabWidget(host)
        screen = QWidget()
        screen_layout = QVBoxLayout(screen)
        content = QLabel("Detached inventory content", screen)
        screen_layout.addWidget(content)
        tabs.addTab(screen, "Inventory")
        host.show()
        self.app.processEvents()

        self.assertTrue(screen.isVisible())
        tabs.removeTab(0)
        self.assertTrue(screen.isHidden())

        window = _DetachedTabWindow(
            "inventory",
            "Inventory - Test Adventure",
            screen,
            host,
        )
        window.show()
        self.app.processEvents()

        self.assertIs(window.centralWidget(), screen)
        self.assertTrue(screen.isVisible())
        self.assertTrue(content.isVisible())

        window.close()
        self.app.processEvents()
        screen.close()
        host.close()

    def test_conversation_bubbles_fill_available_width_and_wrap_text(self) -> None:
        screen = StoryScreen()
        screen.resize(1200, 700)
        screen._render_conversation(
            [
                (
                    "ai",
                    "live_game",
                    "A deliberately long sentence that should use the available "
                    "conversation width before wrapping naturally at a word boundary.",
                    1,
                )
            ]
        )
        screen.show()
        self.app.processEvents()

        bubble = screen.findChild(QWidget, "conversationBubble")
        self.assertIsNotNone(bubble)
        assert bubble is not None
        message = bubble.findChild(QTextEdit)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertGreater(bubble.width(), 1000)
        self.assertEqual(
            message.lineWrapMode(),
            QTextEdit.LineWrapMode.WidgetWidth,
        )

        screen.close()

    def test_live_game_ai_headers_number_turns_without_counting_out_of_game(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Turn Header Test")
            repository.append_history("story", "The first live response.")
            repository.append_history("story_oog", "The out-of-game answer.")
            repository.append_history("story", "The second live response.")
            screen = StoryScreen()
            screen.set_repository(repository)
            screen.show()
            self.app.processEvents()

            headers = {
                label.text()
                for label in screen.findChildren(QLabel)
                if label.text().startswith("AI Game Master")
            }

            self.assertIn("AI Game Master  |  Live Game  |  Turn #1", headers)
            self.assertIn("AI Game Master  |  Out-of-Game", headers)
            self.assertIn("AI Game Master  |  Live Game  |  Turn #2", headers)
            self.assertNotIn("AI Game Master  |  Out-of-Game  |  Turn #2", headers)
            screen.close()

    def test_story_speaker_cues_render_as_same_turn_named_bubbles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Speaker Bubble Test"
            )
            message_id = repository.create_message_id()
            repository.capture_message_snapshot(message_id)
            repository.append_history(
                "story",
                (
                    'Rain taps the glass. "Stay close." The hooded figure points '
                    'east. "Not that door." Silence returns.'
                ),
                message_id=message_id,
                speaker_cues=[
                    {
                        "anchor_text": '"Stay close."',
                        "speaker_id": "mira_coppercup",
                        "speaker_name": "Mira",
                        "voice_profile": "feminine",
                        "voice_id": "af_sarah",
                    },
                    {
                        "anchor_text": '"Not that door."',
                        "speaker_id": "hooded_figure",
                        "speaker_name": "Hooded Figure",
                        "voice_profile": "neutral",
                        "voice_id": "am_echo",
                    },
                ],
            )
            screen = StoryScreen()
            screen.set_repository(repository)
            screen.show()
            self.app.processEvents()

            headers = [
                label.text()
                for label in screen.findChildren(QLabel)
                if "  |  Live Game  |  Turn #" in label.text()
            ]

            self.assertEqual(
                headers,
                [
                    "AI Game Master  |  Live Game  |  Turn #1",
                    "Mira  |  Live Game  |  Turn #1",
                    "AI Game Master  |  Live Game  |  Turn #1",
                    "Hooded Figure  |  Live Game  |  Turn #1",
                    "AI Game Master  |  Live Game  |  Turn #1",
                ],
            )
            self.assertEqual(
                len(
                    [
                        button
                        for button in screen.findChildren(QPushButton)
                        if button.text() == "Regenerate"
                    ]
                ),
                5,
            )
            screen.close()

    def test_opening_message_has_no_image_grid_and_current_location_image_is_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = SaveRepository.create_new_save(root / "saves", "Opening Image Test")
            repository.set_state_value("location", "Glass Market")
            repository.set_travel_locations(
                [
                    {
                        "location_id": "loc_glass_market",
                        "name": "Glass Market",
                        "description": "Blue awnings over wet stone.",
                    }
                ]
            )
            opening_id = repository.create_message_id()
            opening_speaker_cue = {
                "anchor_text": '"Welcome."',
                "speaker_id": "market_keeper",
                "speaker_name": "Market Keeper",
                "voice_profile": "neutral",
                "voice_id": "am_echo",
            }
            repository.append_history(
                "story",
                'Rain gathers beneath the awnings. "Welcome."',
                message_id=opening_id,
                speaker_cues=[opening_speaker_cue],
            )

            images_dir = root / "images"
            image_path = images_dir / "location.jpg"
            image_path.parent.mkdir(parents=True)
            image = QImage(320, 180, QImage.Format.Format_RGB32)
            image.fill(QColor(20, 80, 120))
            self.assertTrue(image.save(str(image_path)))
            npc_image = QImage(100, 125, QImage.Format.Format_RGB32)
            npc_image.fill(QColor(120, 40, 40))
            self.assertTrue(npc_image.save(str(images_dir / "market_keeper.jpg")))
            repository.ensure_visual_asset(
                asset_id="img_location_glass_market",
                subject_type="location",
                subject_key="loc_glass_market",
                display_name="Glass Market",
                descriptor_hash="location-hash",
                filename="location.jpg",
                prompt="location",
                model="test",
                message_ids=(opening_id,),
                ready=True,
            )
            repository.ensure_visual_asset(
                asset_id="img_npc_market_keeper",
                subject_type="npc",
                subject_key="market_keeper",
                display_name="Market Keeper",
                descriptor_hash="npc-hash",
                filename="market_keeper.jpg",
                prompt="portrait",
                model="test",
                message_ids=(opening_id,),
                ready=True,
            )

            screen = StoryScreen()
            screen.set_visual_assets_dir(images_dir)
            screen.set_repository(repository)
            screen.show()
            self.app.processEvents()

            self.assertTrue(screen.location_image_label.isVisible())
            self.assertIsNotNone(screen.location_image_label.pixmap())
            self.assertEqual(
                len(screen.findChildren(QLabel, "conversationGeneratedImage")),
                0,
            )
            portraits = screen.findChildren(QLabel, "conversationSpeakerPortrait")
            self.assertEqual(len(portraits), 1)
            self.assertEqual(
                portraits[0].accessibleName(),
                "Profile picture of Market Keeper",
            )
            screen.close()

    def test_player_and_named_npc_messages_show_profile_portraits_but_narrator_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = SaveRepository.create_new_save(root / "saves", "Portrait Test")
            repository.set_setting("player.id", "player_test")
            repository.append_history("story", "The scene opens.")
            npc_message_id = repository.create_message_id()
            speaker_cue = {
                "anchor_text": '"Keep moving."',
                "speaker_id": "mira_coppercup",
                "speaker_name": "Mira",
                "voice_profile": "feminine",
                "voice_id": "af_sarah",
            }
            repository.append_history(
                "story",
                'Mira whispers, "Keep moving."',
                message_id=npc_message_id,
                speaker_cues=[speaker_cue],
            )
            repository.append_history("player", "I follow.")

            images_dir = root / "images"
            images_dir.mkdir(parents=True)
            mira_image = QImage(100, 125, QImage.Format.Format_RGB32)
            mira_image.fill(QColor(120, 40, 40))
            self.assertTrue(mira_image.save(str(images_dir / "mira.jpg")))
            player_image = QImage(100, 125, QImage.Format.Format_RGB32)
            player_image.fill(QColor(40, 120, 40))
            self.assertTrue(player_image.save(str(images_dir / "player.jpg")))
            for asset_id, subject_type, subject_key, display_name, filename in (
                ("img_mira", "npc", "mira_coppercup", "Mira", "mira.jpg"),
                ("img_player", "player", "player_test", "Player", "player.jpg"),
            ):
                repository.ensure_visual_asset(
                    asset_id=asset_id,
                    subject_type=subject_type,
                    subject_key=subject_key,
                    display_name=display_name,
                    descriptor_hash=asset_id,
                    filename=filename,
                    prompt="portrait",
                    model="test",
                    ready=True,
                )

            screen = StoryScreen()
            screen.set_visual_assets_dir(images_dir)
            screen.set_repository(repository)
            self.app.processEvents()

            portraits = screen.findChildren(QLabel, "conversationSpeakerPortrait")
            self.assertEqual(len(portraits), 2)
            self.assertEqual(
                {portrait.accessibleName() for portrait in portraits},
                {"Profile picture of Mira", "Profile picture of You"},
            )
            screen.close()

    def test_named_speaker_bubble_reads_only_its_saved_voice_passage(self) -> None:
        class FakeNarrationPlayer:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def set_volume(self, _volume: float | int | None) -> None:
                pass

            def set_speed(self, _speed: float | int | None) -> None:
                pass

            def set_voice(self, _voice: str | None) -> None:
                pass

            def set_enabled(self, _enabled: bool) -> None:
                pass

            def play_sample(self, **kwargs: Any) -> bool:
                self.calls.append(kwargs)
                return True

        speaker_cue = {
            "anchor_text": '"Keep moving."',
            "speaker_id": "mira_coppercup",
            "speaker_name": "Mira",
            "voice_profile": "feminine",
            "voice_id": "af_sarah",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Speaker Bubble Replay Test"
            )
            repository.append_history(
                "story",
                'Mira leans close. "Keep moving." The footsteps grow louder.',
                speaker_cues=[speaker_cue],
            )
            narrator = FakeNarrationPlayer()
            screen = StoryScreen(narration_player=cast(Any, narrator))
            screen.set_repository(repository)
            self.app.processEvents()

            speaker_header = next(
                label
                for label in screen.findChildren(QLabel)
                if label.text() == "Mira  |  Live Game  |  Turn #1"
            )
            bubble = speaker_header.parentWidget()
            self.assertIsNotNone(bubble)
            assert bubble is not None
            read_button = next(
                button
                for button in bubble.findChildren(QPushButton)
                if button.text() == "Read Aloud"
            )
            read_button.click()

            self.assertEqual(len(narrator.calls), 1)
            self.assertEqual(narrator.calls[0]["text"], '"Keep moving."')
            self.assertEqual(narrator.calls[0]["speaker_cues"], [speaker_cue])
            screen.close()

    def test_initial_generation_uses_neutral_status_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Pending Opening Test"
            )
            screen = StoryScreen()
            screen.set_repository(repository)

            screen.set_initial_generation_pending(True)

            self.assertEqual(screen.location_value.text(), "---")
            self.assertEqual(screen.day_value.text(), "---")
            self.assertEqual(screen.time_value.text(), "---")
            self.assertEqual(screen.weather_value.text(), "---")

            screen.set_initial_generation_pending(False)

            self.assertNotEqual(screen.location_value.text(), "---")
            self.assertNotEqual(screen.day_value.text(), "---")
            self.assertNotEqual(screen.time_value.text(), "---")
            self.assertNotEqual(screen.weather_value.text(), "---")
            screen.close()

    def test_saved_music_starts_without_replaying_a_sound_effect(self) -> None:
        class FakeSoundManager:
            def __init__(self) -> None:
                self.music_played = ""
                self.effect_played = ""
                self.ambience_played = ""

            def get_valid_track_names(self) -> list[str]:
                return []

            def get_valid_sound_effect_names(self) -> list[str]:
                return []

            def get_valid_background_ambience_names(self) -> list[str]:
                return []

            def set_music_volume(self, volume: float | int | None) -> None:
                pass

            def set_music_enabled(self, enabled: bool) -> None:
                pass

            def set_sound_effects_volume(self, volume: float | int | None) -> None:
                pass

            def set_sound_effects_enabled(self, enabled: bool) -> None:
                pass

            def set_background_ambience_volume(
                self,
                volume: float | int | None,
            ) -> None:
                pass

            def set_background_ambience_enabled(self, enabled: bool) -> None:
                pass

            def play_music(self, track_name_or_path: str | Path | None) -> None:
                self.music_played = str(track_name_or_path or "")

            def play_music_preview(
                self,
                track_name_or_path: str | Path | None,
            ) -> None:
                pass

            def play_sound_effect(
                self,
                track_name_or_path: str | Path | None,
            ) -> None:
                self.effect_played = str(track_name_or_path or "")

            def play_background_ambience(
                self,
                track_name_or_path: str | Path | None,
            ) -> None:
                self.ambience_played = str(track_name_or_path or "")

            def stop_background_ambience(
                self,
                *,
                clear_current: bool = True,
            ) -> None:
                pass

            def stop_music(self, *, clear_current: bool = True) -> None:
                pass

            def stop_sound_effect(self, *, clear_current: bool = True) -> None:
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Audio Sync Test")
            repository.set_setting("audio.current_music", "Slow Jazz.mp3")
            repository.set_setting(
                "audio.current_background_ambience",
                "Quiet Rain.ogg",
            )
            manager = FakeSoundManager()

            _apply_audio_settings_to_managers(
                repository,
                sound_manager=manager,
                narration_player=None,
            )

            self.assertEqual(manager.music_played, "Slow Jazz.mp3")
            self.assertEqual(manager.effect_played, "")
            self.assertEqual(manager.ambience_played, "Quiet Rain.ogg")

    def test_latest_story_can_use_progressive_narration_with_pronunciations(self) -> None:
        class FakeNarrationPlayer:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def set_volume(self, _volume: float | int | None) -> None:
                pass

            def set_speed(self, _speed: float | int | None) -> None:
                pass

            def set_voice(self, _voice: str | None) -> None:
                pass

            def set_enabled(self, _enabled: bool) -> None:
                pass

            def narrate(self, text: str, **kwargs: Any) -> bool:
                self.calls.append((text, kwargs))
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Opening Test")
            repository.set_setting(
                "tts.pronunciation_map",
                {"Ironpeak City": {"ipa": "ˈaɪɚnˌpik ˈsɪti"}},
            )
            repository.append_history(
                "story",
                "Ironpeak City wakes.\n\nThe market opens.",
                sound_effect_cues=[
                    {
                        "filename": "Market Bell.wav",
                        "anchor_text": "market",
                        "position": "before",
                    }
                ],
                speaker_cues=[
                    {
                        "anchor_text": "The market opens.",
                        "speaker_id": "market_crier",
                        "speaker_name": "Market Crier",
                        "voice_profile": "deep_masculine",
                        "voice_id": "am_onyx",
                    }
                ],
            )
            story_id = int(repository.list_history()[-1]["id"])
            narrator = FakeNarrationPlayer()
            screen = StoryScreen(narration_player=cast(Any, narrator))
            screen.set_repository(repository)

            with patch("ai_adventure.ui.main_window.QTimer.singleShot"):
                started = screen.narrate_latest_story(reveal_progressively=True)

            self.assertTrue(started)
            self.assertEqual(screen._revealing_story_id, story_id)
            self.assertEqual(screen._revealed_story_chunks, [])
            self.assertEqual(len(narrator.calls), 1)
            narration_text, kwargs = narrator.calls[0]
            self.assertEqual(
                narration_text,
                "Ironpeak City wakes.\n\nThe market opens.",
            )
            self.assertIsNotNone(kwargs.get("on_chunk_start"))
            self.assertIsNotNone(kwargs.get("on_complete"))
            self.assertEqual(
                kwargs.get("sound_effect_cues"),
                [
                    {
                        "filename": "Market Bell.wav",
                        "anchor_text": "market",
                        "position": "before",
                    }
                ],
            )
            self.assertEqual(
                kwargs.get("speaker_cues"),
                [
                    {
                        "anchor_text": "The market opens.",
                        "speaker_id": "market_crier",
                        "speaker_name": "Market Crier",
                        "voice_profile": "deep_masculine",
                        "voice_id": "am_onyx",
                    }
                ],
            )
            transform = cast(Any, kwargs["tts_text_transform"])
            self.assertEqual(transform("Ironpeak City wakes."), "Ironpeak City wakes.")
            screen.close()

    def test_progressive_narration_updates_one_bubble_without_rebuilding_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Progressive Render Test"
            )
            repository.append_history(
                "story",
                "First paragraph.\n\nSecond paragraph.",
            )
            story_id = int(repository.list_history()[-1]["id"])
            screen = StoryScreen()
            screen.set_repository(repository)
            screen._revealing_story_id = story_id
            screen._revealed_story_chunks = ["First paragraph."]
            screen.refresh()
            self.app.processEvents()

            message = screen._progressive_story_message
            self.assertIsNotNone(message)
            assert message is not None

            with patch.object(screen, "refresh", wraps=screen.refresh) as refresh:
                screen._append_revealed_story_chunk(
                    story_id,
                    "\n\nSecond paragraph.",
                )

            self.assertFalse(refresh.called)
            self.assertIs(screen._progressive_story_message, message)
            self.assertIn("Second paragraph.", message.toPlainText())
            screen.close()

    def test_read_aloud_replays_saved_passage_sound_effect_cues(self) -> None:
        class FakeNarrationPlayer:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def set_volume(self, _volume: float | int | None) -> None:
                pass

            def set_speed(self, _speed: float | int | None) -> None:
                pass

            def set_voice(self, _voice: str | None) -> None:
                pass

            def set_enabled(self, _enabled: bool) -> None:
                pass

            def play_sample(self, **kwargs: Any) -> bool:
                self.calls.append(kwargs)
                return True

        class FakeSoundManager:
            def __init__(self) -> None:
                self.effects: list[str] = []

            def set_music_volume(self, _volume: float | int | None) -> None:
                pass

            def set_music_enabled(self, _enabled: bool) -> None:
                pass

            def set_sound_effects_volume(self, _volume: float | int | None) -> None:
                pass

            def set_sound_effects_enabled(self, _enabled: bool) -> None:
                pass

            def play_music(self, _track: str | Path | None) -> None:
                pass

            def stop_music(self, *, clear_current: bool = True) -> None:
                pass

            def stop_sound_effect(self, *, clear_current: bool = True) -> None:
                pass

            def stop_background_ambience(
                self,
                *,
                clear_current: bool = True,
            ) -> None:
                pass

            def play_sound_effect(self, track: str | Path | None) -> None:
                self.effects.append(str(track or ""))

        cue = {
            "filename": "Market Bell.wav",
            "anchor_text": "market",
            "position": "before",
        }
        speaker_cue = {
            "anchor_text": '"The market opens."',
            "speaker_id": "town_crier",
            "speaker_name": "Town Crier",
            "voice_profile": "deep_masculine",
            "voice_id": "am_onyx",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Replay Test")
            repository.set_setting(
                "tts.pronunciation_map",
                {"Ironpeak City": {"ipa": "ˈaɪɚnˌpik ˈsɪti"}},
            )
            repository.append_history(
                "story",
                'Ironpeak City wakes. "The market opens."',
                sound_effect_cues=[cue],
                speaker_cues=[speaker_cue],
            )
            history_entry_id = int(repository.list_history()[-1]["id"])
            narrator = FakeNarrationPlayer()
            sound_manager = FakeSoundManager()
            screen = StoryScreen(
                narration_player=cast(Any, narrator),
                sound_manager=cast(Any, sound_manager),
            )
            screen.set_repository(repository)

            started = screen._read_conversation_message_aloud(
                "Formatted display text",
                history_entry_id=history_entry_id,
            )

            self.assertTrue(started)
            self.assertEqual(len(narrator.calls), 1)
            call = narrator.calls[0]
            self.assertEqual(
                call["text"],
                'Ironpeak City wakes. "The market opens."',
            )
            self.assertEqual(call["sound_effect_cues"], [cue])
            self.assertEqual(call["speaker_cues"], [speaker_cue])
            transform = cast(Any, call["tts_text_transform"])
            self.assertEqual(transform("Ironpeak City wakes."), "Ironpeak City wakes.")
            on_sound_effect = cast(Any, call["on_sound_effect"])
            on_sound_effect("Market Bell.wav")
            self.assertEqual(sound_manager.effects, ["Market Bell.wav"])
            screen.close()

    def test_conversation_refresh_preserves_reader_position_and_adds_bottom_buffer(self) -> None:
        screen = StoryScreen()
        screen.resize(900, 600)
        entries = [
            (
                "ai",
                "live_game",
                f"Message {index}: " + ("A long readable sentence. " * 12),
                index,
            )
            for index in range(8)
        ]
        screen._render_conversation(entries)
        screen.show()
        self.app.processEvents()

        bar = screen.conversation_scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertGreater(screen.conversation_bottom_padding.height(), 0)

        bar.setValue(max(0, bar.maximum() - 140))
        old_value = bar.value()
        screen._render_conversation(entries)
        self.app.processEvents()

        self.assertLessEqual(abs(bar.value() - old_value), 2)
        screen.close()

    def test_recipe_table_uses_notes_instead_of_redundant_result_column(self) -> None:
        screen = AlchemyNotebookScreen()
        headers = [
            cast(
                QTableWidgetItem,
                screen.recipe_table.horizontalHeaderItem(column),
            ).text()
            for column in range(screen.recipe_table.columnCount())
        ]

        self.assertEqual(
            headers,
            ["Name", "Ingredients", "Estimated Value", "Notes"],
        )
        self.assertNotIn("Result", headers)

        screen.close()

    def test_crafting_items_show_typical_areas_value_and_rarity_notes(self) -> None:
        screen = AlchemyNotebookScreen()
        headers = [
            cast(
                QTableWidgetItem,
                screen.reagent_table.horizontalHeaderItem(column),
            ).text()
            for column in range(screen.reagent_table.columnCount())
        ]

        self.assertEqual(
            headers,
            [
                "Name",
                "Category",
                "Description",
                "Typical Areas",
                "Uses",
                "Estimated Value",
                "Notes",
            ],
        )
        self.assertNotIn("Location", headers)

        screen.close()

    def test_calendar_month_title_shares_navigation_row_and_player_events_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Calendar UI Test")
            screen = CalendarScreen()
            screen.set_repository(repository)
            screen.resize(1200, 700)
            screen.show()
            self.app.processEvents()

            buttons = {
                button.text(): button
                for button in screen.findChildren(QPushButton)
            }
            self.assertIn("Previous", buttons)
            self.assertIn("Today", buttons)
            self.assertIn("Next", buttons)
            self.assertIn("+ Event", buttons)
            self.assertLessEqual(
                abs(screen.month_label.geometry().center().y() - buttons["Today"].geometry().center().y()),
                9,
            )

            dialog = CalendarPlayerEventDialog(
                calendar_settings=repository.get_calendar_settings(),
                default_year=1,
                default_month=2,
                default_day=7,
                parent=screen,
            )
            dialog.title_input.setText("Watch the eclipse")
            dialog.all_day_checkbox.setChecked(False)
            dialog.hour_input.setValue(21)
            dialog.minute_input.setValue(10)
            event = dialog.build_event()

            self.assertEqual(event["origin"], "player")
            self.assertEqual(event["time_of_day_minutes"], 1270)
            self.assertTrue(str(event["event_id"]).startswith("player_"))

            dialog.close()

    def test_calendar_tasks_table_displays_wrapped_quest_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Task Description UI",
            )
            repository.upsert_active_task(
                name="The Kestrel Street Homicide",
                category="Quest",
                description=(
                    "Question witnesses in Kestrel Street Alleyway, identify the "
                    "victim, and bring enough evidence to Inspector Vale to name "
                    "the killer."
                ),
                requester="Inspector Vale",
                location="Kestrel Street Alleyway",
                reward="$200",
                due_date="N/A",
            )
            screen = CalendarScreen()
            screen.set_repository(repository)
            self.app.processEvents()

            headers = [
                cast(QTableWidgetItem, screen.tasks_table.horizontalHeaderItem(column)).text()
                for column in range(screen.tasks_table.columnCount())
            ]
            self.assertEqual(
                headers,
                ["Task", "Description", "Category", "Due", "Location", "Reward"],
            )
            self.assertEqual(screen.tasks_table.rowCount(), 1)
            description_item = screen.tasks_table.item(0, 1)
            self.assertIsNotNone(description_item)
            assert description_item is not None
            self.assertIn("Question witnesses", description_item.text())
            self.assertIn("Inspector Vale", description_item.text())
            self.assertTrue(screen.tasks_table.wordWrap())
            screen.close()
            screen.close()

    def test_calendar_day_double_click_prefills_event_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Calendar Double Click"
            )
            screen = CalendarScreen()
            screen.set_repository(repository)
            self.app.processEvents()
            item = screen.table.item(0, 2)
            self.assertIsNotNone(item)
            assert item is not None
            date = item.data(int(Qt.ItemDataRole.UserRole) + 1)
            self.assertIsInstance(date, dict)

            with patch(
                "ai_adventure.ui.main_window.CalendarPlayerEventDialog"
            ) as dialog_type:
                dialog_type.return_value.exec.return_value = QDialog.DialogCode.Rejected
                screen._schedule_open_day_events(0, 2)
                screen._add_player_event_for_day(0, 2)

            self.assertFalse(screen._day_click_timer.isActive())
            self.assertEqual(dialog_type.call_args.kwargs["default_year"], date["year"])
            self.assertEqual(dialog_type.call_args.kwargs["default_month"], date["month"])
            self.assertEqual(dialog_type.call_args.kwargs["default_day"], date["day"])
            screen.close()

    def test_new_game_empty_tabs_reveal_with_unread_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Smart Starting Tabs",
                setup={"title": "Smart Starting Tabs"},
            )
            shell = GameShell(lambda: None, tts_enabled=False, ai_enabled=False)
            shell.set_repository(repository, initially_hide_empty_tabs=True)
            self.app.processEvents()

            self.assertEqual(shell._tab_index_for_key("npcs"), -1)
            self.assertEqual(shell._tab_index_for_key("party"), -1)
            self.assertEqual(shell._tab_index_for_key("magic"), -1)

            repository.upsert_npc(
                npc_id="mira_coppercup",
                name="Mira Coppercup",
                display_name="Mira",
                role="Guide",
                player_facing_information="A guide the player has just met.",
            )
            shell.story_screen.notify_repository_changed()
            self.app.processEvents()

            npc_index = shell._tab_index_for_key("npcs")
            self.assertGreaterEqual(npc_index, 0)
            self.assertEqual(shell.tabs.tabText(npc_index), "NPCs •")
            self.assertEqual(shell._tab_index_for_key("party"), -1)
            self.assertEqual(shell._tab_index_for_key("magic"), -1)

            shell.tabs.setCurrentIndex(npc_index)
            self.app.processEvents()
            self.assertEqual(shell.tabs.tabText(npc_index), "NPCs")
            shell.close()

    def test_bestiary_screen_matches_travel_layout_without_action_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Bestiary UI")
            repository.upsert_bestiary_entry(
                creature_id="mist_strider",
                name="Mist-Strider",
                details="A towering animal seen moving between the fog banks.",
            )
            repository.upsert_miscellaneous(
                misc_id="reed_covenant",
                name="Reed Covenant",
                category="Faction",
                details="A marshland alliance.",
            )
            repository.upsert_gm_secret(
                secret_id="mist_strider_origin",
                title="Mist-Strider Origin",
                details="It was secretly built beneath the old archive.",
            )

            screen = BestiaryScreen()
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertEqual(screen.creature_list.count(), 1)
            self.assertEqual(screen.creature_list.item(0).text(), "Mist-Strider")
            visible_details = screen.details_output.toPlainText()
            self.assertIn("towering animal", visible_details)
            self.assertNotIn("built beneath", visible_details)
            self.assertEqual(screen.findChildren(QPushButton), [])
            screen.close()

    def test_notes_default_to_markdown_preview_and_edit_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Notes UI")
            repository.set_note_entries(
                [
                    {
                        "entry_id": "note-1",
                        "heading": "Field clue",
                        "body": "**A marked door**\n\n- Check the hinges",
                        "tags": ["Clues"],
                    }
                ]
            )

            screen = NotesScreen()
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertIs(screen.entry_pages.currentWidget(), screen.entry_preview)
            self.assertTrue(screen.entry_preview.isReadOnly())
            self.assertIn("A marked door", screen.entry_preview.toPlainText())
            self.assertEqual(screen.edit_note_button.text(), "Edit note")

            screen.edit_note_button.click()
            self.assertIs(screen.entry_pages.currentWidget(), screen.entry_editor)
            self.assertEqual(screen.entry_body_input.toPlainText(), "**A marked door**\n\n- Check the hinges")
            self.assertEqual(screen.edit_note_button.text(), "View note")

            screen.entry_body_input.setPlainText("**Updated clue**")
            screen.edit_note_button.click()
            self.assertIs(screen.entry_pages.currentWidget(), screen.entry_preview)
            self.assertIn("Updated clue", screen.entry_preview.toPlainText())
            screen._autosave_timer.stop()
            screen.close()

    def test_game_shell_registers_bestiary_tab(self) -> None:
        shell = GameShell(lambda: None, tts_enabled=False, ai_enabled=False)

        index = shell._tab_index_for_key("bestiary")
        self.assertGreaterEqual(index, 0)
        self.assertEqual(shell.tabs.tabText(index), "Bestiary")
        self.assertIs(shell.tabs.widget(index), shell.bestiary_screen)
        shell.close()

    def test_npcs_auto_refresh_after_repository_change_without_refresh_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "NPC UI Test")
            shell = GameShell(
                lambda: None,
                tts_enabled=False,
                ai_enabled=False,
            )
            shell.set_repository(repository)
            self.app.processEvents()

            self.assertEqual(shell.npcs_screen.table.rowCount(), 0)
            refresh_buttons = [
                button
                for button in shell.npcs_screen.findChildren(QPushButton)
                if button.text() == "Refresh"
            ]
            self.assertEqual(refresh_buttons, [])

            repository.upsert_npc(
                npc_id="mira_coppercup",
                name="Mira Coppercup",
                display_name="Bartender",
                role="Tavern keeper",
                location="Copper Kettle Tavern",
                player_facing_information="A friendly bartender who remembers regulars.",
            )
            shell.story_screen.notify_repository_changed()
            self.app.processEvents()

            self.assertEqual(shell.npcs_screen.table.rowCount(), 1)
            name_item = shell.npcs_screen.table.item(0, 0)
            location_item = shell.npcs_screen.table.item(0, 1)
            self.assertIsNotNone(name_item)
            self.assertIsNotNone(location_item)
            assert name_item is not None
            assert location_item is not None
            self.assertEqual(name_item.text(), "Bartender")
            self.assertEqual(
                location_item.text(),
                "Copper Kettle Tavern",
            )

            shell.close()

    def test_npcs_screen_contains_only_the_live_table(self) -> None:
        screen = NpcsScreen()

        self.assertEqual(
            [button.text() for button in screen.findChildren(QPushButton)],
            [],
        )

        screen.close()

    def test_party_screen_shows_party_data_with_shared_npc_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Party UI Test")
            repository.upsert_npc(
                npc_id="mira_coppercup",
                name="Mira Coppercup",
                display_name="Mira",
                role="Scout",
                location="Old Road",
                public_description="A keen-eyed traveler in a green cloak.",
                player_facing_information="A trusted traveling companion.",
            )
            repository.upsert_party_member(
                "mira_coppercup",
                status="Wounded",
                health_current=8,
                health_max=18,
                armor_class=13,
                combat_style="Mobile archer",
                skills=["Archery", "Tracking"],
            )
            shell = GameShell(lambda: None, tts_enabled=False, ai_enabled=False)
            shell.set_repository(repository)
            screen = shell.party_screen
            self.app.processEvents()

            self.assertIn(
                "Party",
                [shell.tabs.tabText(index) for index in range(shell.tabs.count())],
            )
            self.assertEqual(screen.table.rowCount(), 1)
            header_texts = []
            for index in range(screen.table.columnCount()):
                header_item = screen.table.horizontalHeaderItem(index)
                self.assertIsNotNone(header_item)
                assert header_item is not None
                header_texts.append(header_item.text())
            self.assertEqual(
                header_texts,
                [
                    "Name",
                    "Status",
                    "Health",
                    "Armor Class",
                    "Combat Style",
                    "Skills",
                    "Description",
                    "Equipment",
                    "Portrait",
                ],
            )
            name_item = screen.table.item(0, 0)
            health_item = screen.table.item(0, 2)
            armor_item = screen.table.item(0, 3)
            self.assertIsNotNone(name_item)
            self.assertIsNotNone(health_item)
            self.assertIsNotNone(armor_item)
            assert name_item is not None
            assert health_item is not None
            assert armor_item is not None
            self.assertEqual(name_item.text(), "Mira")
            self.assertEqual(health_item.text(), "8/18")
            self.assertEqual(armor_item.text(), "13")
            self.assertEqual(
                name_item.data(Qt.ItemDataRole.UserRole),
                "mira_coppercup",
            )
            shell.close()

    def test_inventory_uses_location_panels_and_modal_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Inventory UI Test")
            repository.add_inventory_item(
                "Brass Compass",
                "Tool",
                1,
                "A pocket compass with a scratched lid.",
                value_base_units=8,
                metadata={
                    "quantity_unit": "each",
                    "storage_location": "Detective's Car",
                    "ascii_art": "  .--.\\n ( N  )\\n  '--'",
                },
            )
            repository.add_inventory_item(
                "Notebook",
                "Book",
                2,
                "Two clothbound notebooks.",
                value_base_units=2,
                metadata={
                    "quantity_unit": "each",
                    "storage_location": "actively_carried",
                    "ascii_art": " _____\n|_____|\n|_____|",
                },
            )

            screen = InventoryScreen()
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertFalse(hasattr(screen, "table"))
            self.assertEqual(len(screen.location_panels), 2)
            self.assertTrue(screen.location_panels[0].title().startswith("Actively Carried ("))
            self.assertEqual(screen.location_panels[1].title(), "Detective's Car (1)")
            self.assertTrue(all(panel.item_buttons for panel in screen.location_panels))
            panel_layout = cast(QGridLayout, screen.inventory_panel_layout)
            self.assertEqual(panel_layout.getItemPosition(0), (0, 0, 1, 2))
            self.assertEqual(panel_layout.getItemPosition(1), (0, 2, 1, 2))

            compass = screen._inventory_items["brass compass"]
            catalog_entry = compass["catalog_entry"]
            dialog = InventoryItemDetailsDialog(
                item=compass,
                catalog_entry=catalog_entry,
                denominations=screen._denominations,
                parent=screen,
            )
            self.assertTrue(dialog.isModal())
            self.assertEqual(
                dialog.windowModality(),
                Qt.WindowModality.ApplicationModal,
            )
            self.assertIsNone(dialog.findChild(QPlainTextEdit, "inventoryAsciiArt"))
            dialog_labels = [label.text() for label in dialog.findChildren(QLabel)]
            self.assertNotIn("Item Art", dialog_labels)
            self.assertNotIn("Equipped:", dialog_labels)
            self.assertIsNone(
                dialog.findChild(QPlainTextEdit, "inventoryStructuredDetails")
            )
            self.assertIsNone(
                dialog.findChild(QLabel, "inventoryStructuredDetailsLabel")
            )
            playtesting_dialog = InventoryItemDetailsDialog(
                item=compass,
                catalog_entry=catalog_entry,
                denominations=screen._denominations,
                show_structured_details=True,
                parent=screen,
            )
            structured_details = playtesting_dialog.findChild(
                QPlainTextEdit,
                "inventoryStructuredDetails",
            )
            self.assertIsNotNone(structured_details)
            structured_details = cast(QPlainTextEdit, structured_details)
            self.assertIn("item_uuid", structured_details.toPlainText())
            playtesting_dialog.close()
            dialog.close()
            screen.close()

    def test_npc_rows_open_resizable_player_visible_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "NPC UI Test")
            repository.upsert_npc(
                npc_id="dock_warden",
                name="Dock Warden",
                display_name="Dock Warden",
                role="Harbor guard",
                location="Glass Market",
                public_description="A broad woman in an orange rain cape.",
                player_facing_information="Keeps order at the piers.",
            )

            screen = NpcsScreen()
            screen.set_repository(repository)
            self.app.processEvents()
            self.assertEqual(screen.table.rowCount(), 1)
            name_item = screen.table.item(0, 0)
            self.assertIsNotNone(name_item)
            assert name_item is not None
            self.assertEqual(
                name_item.data(Qt.ItemDataRole.UserRole),
                "dock_warden",
            )

            with patch(
                "ai_adventure.ui.main_window.NpcDetailsDialog.exec",
                return_value=0,
                autospec=True,
            ) as exec_dialog:
                screen._open_npc_details(0, 0)

            exec_dialog.assert_called_once_with()
            screen.close()

    def test_inventory_quantities_use_x_format_and_natural_plurals(self) -> None:
        self.assertEqual(_inventory_quantity_display(1, "each"), "x1")
        self.assertEqual(_inventory_quantity_display(3, "day"), "x3 days")
        self.assertEqual(_inventory_quantity_display(2, "flask"), "x2 flasks")
        self.assertEqual(_inventory_quantity_display(2, "oz"), "x2 oz")
        self.assertEqual(
            _inventory_item_display_name("Healing Potion", 3, "each"),
            "Healing Potions",
        )
        self.assertEqual(
            _inventory_item_display_name("Food Rations", 3, "day"),
            "Food Rations",
        )

        panel = InventoryLocationPanel(
            "actively_carried",
            [
                {
                    "name": "Steel Dagger",
                    "quantity": 1,
                    "quantity_unit": "each",
                    "category": "Weapon",
                },
                {
                    "name": "Healing Potion",
                    "quantity": 3,
                    "quantity_unit": "each",
                    "category": "Consumable",
                },
                {
                    "name": "Food Rations",
                    "quantity": 3,
                    "quantity_unit": "day",
                    "category": "Consumable",
                },
            ],
            lambda _item: None,
        )
        button_texts = [button.text() for button in panel.item_buttons]

        self.assertIn("Steel Dagger\nx1  ·  Weapon", button_texts)
        self.assertIn("Healing Potions\nx3  ·  Consumable", button_texts)
        self.assertIn("Food Rations\nx3 days  ·  Consumable", button_texts)

        panel.close()

    def test_each_inventory_location_sorts_independently_and_retains_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Sorted Inventory")
            repository.add_inventory_item(
                "Copper Buckle",
                "Accessory",
                5,
                "A plain buckle.",
                value_base_units=2,
                metadata={"storage_location": "actively_carried"},
            )
            repository.add_inventory_item(
                "Amber Lens",
                "Tool",
                1,
                "A polished lens.",
                value_base_units=12,
                metadata={"storage_location": "actively_carried"},
            )
            repository.add_inventory_item(
                "Zinc Plate",
                "Material",
                2,
                "A thin metal plate.",
                value_base_units=4,
                metadata={"storage_location": "home"},
            )
            repository.add_inventory_item(
                "Brass Tongs",
                "Tool",
                1,
                "Small workshop tongs.",
                value_base_units=9,
                metadata={"storage_location": "home"},
            )

            screen = InventoryScreen()
            screen.set_repository(repository)
            self.app.processEvents()
            carried_panel, home_panel = screen.location_panels

            carried_panel.sort_field_combo.setCurrentIndex(
                carried_panel.sort_field_combo.findData("price")
            )
            carried_panel.sort_direction_combo.setCurrentIndex(
                carried_panel.sort_direction_combo.findData(True)
            )
            carried_panel.secondary_sort_field_combo.setCurrentIndex(
                carried_panel.secondary_sort_field_combo.findData("name")
            )
            carried_panel.secondary_sort_direction_combo.setCurrentIndex(
                carried_panel.secondary_sort_direction_combo.findData(True)
            )
            home_panel.sort_field_combo.setCurrentIndex(
                home_panel.sort_field_combo.findData("category")
            )
            home_panel.secondary_sort_field_combo.setCurrentIndex(
                home_panel.secondary_sort_field_combo.findData("quantity")
            )
            self.app.processEvents()

            carried_names = [
                button.text().splitlines()[0]
                for button in carried_panel.item_buttons
            ]
            self.assertLess(
                carried_names.index("Amber Lens"),
                carried_names.index("Copper Buckles"),
            )
            self.assertTrue(home_panel.item_buttons[0].text().startswith("Zinc Plate"))
            self.assertEqual(home_panel.sort_direction_combo.currentData(), False)
            self.assertEqual(len(carried_panel.group_separators), 2)
            self.assertEqual(len(home_panel.group_separators), 1)

            carried_panel.sort_field_combo.setCurrentIndex(
                carried_panel.sort_field_combo.findData("name")
            )
            self.app.processEvents()
            self.assertEqual(len(carried_panel.group_separators), 6)

            carried_panel.sort_field_combo.setCurrentIndex(
                carried_panel.sort_field_combo.findData("quantity")
            )
            self.app.processEvents()
            self.assertEqual(len(carried_panel.group_separators), 2)

            carried_panel.sort_field_combo.setCurrentIndex(
                carried_panel.sort_field_combo.findData("price")
            )
            carried_panel.sort_direction_combo.setCurrentIndex(
                carried_panel.sort_direction_combo.findData(True)
            )
            self.app.processEvents()

            screen.refresh()
            self.app.processEvents()
            carried_panel, home_panel = screen.location_panels
            self.assertEqual(carried_panel.sort_field_combo.currentData(), "price")
            self.assertEqual(carried_panel.sort_direction_combo.currentData(), True)
            self.assertEqual(
                carried_panel.secondary_sort_field_combo.currentData(),
                "name",
            )
            self.assertEqual(
                carried_panel.secondary_sort_direction_combo.currentData(),
                True,
            )
            self.assertEqual(home_panel.sort_field_combo.currentData(), "category")
            self.assertEqual(home_panel.sort_direction_combo.currentData(), False)
            self.assertEqual(
                home_panel.secondary_sort_field_combo.currentData(),
                "quantity",
            )
            self.assertEqual(
                home_panel.secondary_sort_direction_combo.currentData(),
                False,
            )
            screen.close()

    def test_primary_and_secondary_inventory_sort_directions_are_independent(self) -> None:
        items = [
            {
                "name": "Sickle",
                "category": "Weapon",
                "quantity": 1,
                "value_base_units": 6,
            },
            {
                "name": "Tweezers",
                "category": "Tool",
                "quantity": 1,
                "value_base_units": 6,
            },
            {
                "name": "Small Scissors",
                "category": "Tool",
                "quantity": 1,
                "value_base_units": 6,
            },
            {
                "name": "Pruning Shears",
                "category": "Tool",
                "quantity": 1,
                "value_base_units": 6,
            },
        ]
        panel = InventoryLocationPanel(
            "actively_carried",
            items,
            lambda _item: None,
            sort_field="category",
            sort_descending=True,
        )

        self.assertFalse(panel.secondary_sort_direction_combo.isEnabled())
        self.assertEqual(
            [button.text().splitlines()[0] for button in panel.item_buttons],
            ["Sickle", "Pruning Shears", "Small Scissors", "Tweezers"],
        )

        panel.secondary_sort_field_combo.setCurrentIndex(
            panel.secondary_sort_field_combo.findData("name")
        )
        panel.secondary_sort_direction_combo.setCurrentIndex(
            panel.secondary_sort_direction_combo.findData(True)
        )
        self.app.processEvents()

        self.assertTrue(panel.secondary_sort_direction_combo.isEnabled())
        self.assertEqual(
            [button.text().splitlines()[0] for button in panel.item_buttons],
            ["Sickle", "Tweezers", "Small Scissors", "Pruning Shears"],
        )
        panel.close()

    def test_single_location_panel_is_centered_at_half_width(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Centered Inventory")
            screen = InventoryScreen()
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertEqual(len(screen.location_panels), 1)
            panel_layout = cast(QGridLayout, screen.inventory_panel_layout)
            self.assertEqual(panel_layout.getItemPosition(0), (0, 1, 1, 2))
            screen.close()


if __name__ == "__main__":
    unittest.main()
