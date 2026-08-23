from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLayoutItem,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.main_window import (
    _DetachedTabWindow,
    AlchemyNotebookScreen,
    CalendarPlayerEventDialog,
    CalendarScreen,
    CombatScreen,
    GameShell,
    InventoryItemDetailsDialog,
    InventoryLocationPanel,
    InventoryScreen,
    MagicScreen,
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
        cls.app = QApplication.instance() or QApplication([])

    def test_new_game_wizard_supports_maximize_and_quest_guidance(self) -> None:
        wizard = NewGameWizard(tts_enabled=False)

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

    def test_saved_music_starts_without_replaying_a_sound_effect(self) -> None:
        class FakeSoundManager:
            def __init__(self) -> None:
                self.music_played = ""
                self.effect_played = ""

            def get_valid_track_names(self) -> list[str]:
                return []

            def get_valid_sound_effect_names(self) -> list[str]:
                return []

            def set_music_volume(self, volume: float | int | None) -> None:
                pass

            def set_music_enabled(self, enabled: bool) -> None:
                pass

            def set_sound_effects_volume(self, volume: float | int | None) -> None:
                pass

            def set_sound_effects_enabled(self, enabled: bool) -> None:
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

            def stop_music(self, *, clear_current: bool = True) -> None:
                pass

            def stop_sound_effect(self, *, clear_current: bool = True) -> None:
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Audio Sync Test")
            repository.set_setting("audio.current_music", "Slow Jazz.mp3")
            manager = FakeSoundManager()

            _apply_audio_settings_to_managers(
                repository,
                sound_manager=manager,
                narration_player=None,
            )

            self.assertEqual(manager.music_played, "Slow Jazz.mp3")
            self.assertEqual(manager.effect_played, "")

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
            transform = cast(Any, kwargs["tts_text_transform"])
            self.assertEqual(
                transform("Ironpeak City wakes."),
                '[Ironpeak City]{ph="ˈaɪɚnˌpik ˈsɪti"} wakes.',
            )
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
            self.assertLess(
                abs(screen.month_label.geometry().center().y() - buttons["Today"].geometry().center().y()),
                8,
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
            screen.close()

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
            art_view = cast(
                QPlainTextEdit,
                dialog.findChild(QPlainTextEdit, "inventoryAsciiArt"),
            )

            self.assertTrue(dialog.isModal())
            self.assertEqual(
                dialog.windowModality(),
                Qt.WindowModality.ApplicationModal,
            )
            self.assertIsNotNone(art_view)
            self.assertIn("( N  )", art_view.toPlainText())
            self.assertNotIn("\\n", art_view.toPlainText())
            self.assertEqual(
                art_view.document().firstBlock().blockFormat().alignment(),
                Qt.AlignmentFlag.AlignCenter,
            )
            art_blocks = art_view.document().begin()
            while art_blocks.isValid():
                self.assertEqual(
                    art_blocks.blockFormat().alignment(),
                    Qt.AlignmentFlag.AlignCenter,
                )
                art_blocks = art_blocks.next()
            self.assertGreater(art_view.viewportMargins().top(), 0)
            dialog_labels = [label.text() for label in dialog.findChildren(QLabel)]
            self.assertNotIn("Item Art", dialog_labels)
            self.assertNotIn("Equipped:", dialog_labels)
            self.assertIsNone(
                dialog.findChild(QPlainTextEdit, "inventoryStructuredDetails")
            )
            self.assertIsNone(
                dialog.findChild(QLabel, "inventoryStructuredDetailsLabel")
            )
            dialog_layout = cast(QVBoxLayout, dialog.layout())
            art_layout_item = cast(
                QLayoutItem,
                dialog_layout.itemAt(dialog_layout.indexOf(art_view)),
            )
            self.assertIsNotNone(art_layout_item)
            self.assertTrue(
                art_layout_item.alignment()
                & Qt.AlignmentFlag.AlignHCenter
            )

            tall_catalog_entry = dict(catalog_entry)
            tall_catalog_entry["ascii_art"] = "\n".join(
                f"line {index}" for index in range(10)
            )
            tall_dialog = InventoryItemDetailsDialog(
                item=compass,
                catalog_entry=tall_catalog_entry,
                denominations=screen._denominations,
                parent=screen,
            )
            tall_art_view = cast(
                QPlainTextEdit,
                tall_dialog.findChild(QPlainTextEdit, "inventoryAsciiArt"),
            )
            self.assertGreater(tall_art_view.height(), art_view.height())

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
            tall_dialog.close()
            dialog.close()
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
            home_panel.sort_field_combo.setCurrentIndex(
                home_panel.sort_field_combo.findData("category")
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
            self.assertEqual(len(carried_panel.group_separators), 1)
            self.assertEqual(len(home_panel.group_separators), 1)

            carried_panel.sort_field_combo.setCurrentIndex(
                carried_panel.sort_field_combo.findData("name")
            )
            self.app.processEvents()
            self.assertEqual(len(carried_panel.group_separators), 1)

            carried_panel.sort_field_combo.setCurrentIndex(
                carried_panel.sort_field_combo.findData("quantity")
            )
            self.app.processEvents()
            self.assertEqual(len(carried_panel.group_separators), 1)

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
            self.assertEqual(home_panel.sort_field_combo.currentData(), "category")
            self.assertEqual(home_panel.sort_direction_combo.currentData(), False)
            screen.close()

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
