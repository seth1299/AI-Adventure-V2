import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from ai_adventure.ai.gemini_service import (
    build_gemini_story_prompt, build_story_response_schema, parse_gemini_story_response,
)
from ai_adventure.application.new_game_service import _travel_locations_for_save
from ai_adventure.audio.catalog import MUSIC_SELECTION_RULE
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.context.reference_loader import ContextReferenceLoader
from ai_adventure.core.models import AdventureState
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.new_game_form_helpers import _append_starting_location_table_row, _starting_locations_from_table
from ai_adventure.ui.screens.travel import TravelScreen
from ai_adventure.ui.table_helpers import _AppTableWidget
from ai_adventure.ui.dialogues import NewGameTemplateManagerDialog
from ai_adventure.ui.wizards.new_game import NewGameWizard


class SceneAudioScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def packet(self):
        return AiContextBuilder(ContextReferenceLoader().load_default_library()).build_story_context(
            AdventureState(), player_command="I leave town and enter the forest.",
            valid_music_tracks=["Town Village City.mp3", "Forest_Or_Generic_Nighttime.mp3"],
            current_music="Town Village City.mp3",
        )

    def test_music_selection_is_required_and_transmitted_once(self):
        packet = self.packet()
        schema = build_story_response_schema(packet)
        self.assertIn("music_filename", schema["required"])
        self.assertEqual(schema["properties"]["music_filename"]["enum"],
                         packet["state"]["audio"]["valid_music_tracks"])
        prompt = build_gemini_story_prompt(packet)
        self.assertEqual(prompt.count(MUSIC_SELECTION_RULE), 1)
        self.assertIn("Forest_Or_Generic_Nighttime.mp3", prompt)
        self.assertIn("Town Village City.mp3", prompt)
        empty = build_story_response_schema({})
        self.assertNotIn("music_filename", empty["properties"])

    def test_explicit_selection_becomes_one_event_before_final_status(self):
        status = {"type": "StatusUpdatedEvent", "payload": {"location": "Forest", "weather": "Clear", "minutes_passed": 45}}
        result = parse_gemini_story_response(json.dumps({
            "response": "The trees close around the trail.", "suggested_actions": [], "out_of_game": False,
            "music_filename": "Forest_Or_Generic_Nighttime.mp3",
            "events": [{"type": "MusicChangedEvent", "payload": {"filename": "Town Village City.mp3"}}, status],
        }), context_packet=self.packet())
        self.assertEqual(result.suggested_events, [
            {"type": "MusicChangedEvent", "payload": {"filename": "Forest_Or_Generic_Nighttime.mp3"}}, status,
        ])

    def test_unchanged_invalid_and_out_of_game_choices_do_not_change_music(self):
        for filename, out_of_game in [("Town Village City.mp3", False), ("Invented.mp3", False),
                                      ("Forest_Or_Generic_Nighttime.mp3", True)]:
            with self.subTest(filename=filename, out_of_game=out_of_game):
                result = parse_gemini_story_response(json.dumps({
                    "response": "You wait.", "suggested_actions": [], "events": [],
                    "out_of_game": out_of_game, "music_filename": filename,
                }), context_packet=self.packet())
                self.assertEqual(result.suggested_events, [])

    def test_default_table_scope_is_automatic_but_playtesting_is_explicit(self):
        for playtesting in [False, True]:
            with patch.dict(os.environ, {"AI_ADVENTURE_PLAYTESTING_BUILD": str(int(playtesting))}):
                table = _AppTableWidget(0, 7)
                self.addCleanup(table.close)
                _append_starting_location_table_row(table, {"name": "Room"}, 1, lambda _: None)
                self.assertEqual(table.isColumnHidden(6), not playtesting)
                row = _starting_locations_from_table(table)[0]
                self.assertEqual("location_scope" in row, playtesting)
                with tempfile.TemporaryDirectory() as directory:
                    wizard = NewGameWizard(tts_enabled=False, api_key_path=Path(directory)/"key",
                                           terms_acceptance_path=Path(directory)/"terms")
                    manager = NewGameTemplateManagerDialog(tts_enabled=False, template_path=Path(directory)/"templates.json")
                    self.addCleanup(wizard.close)
                    self.addCleanup(manager.close)
                    self.assertEqual(wizard.starting_locations_table.isColumnHidden(6), not playtesting)
                    self.assertEqual(manager.starting_locations_table.isColumnHidden(6), not playtesting)
                    _append_starting_location_table_row(manager.starting_locations_table,
                        {"name": "Room", "location_scope": "specific"}, 1, lambda _: None)
                    manager._update_starting_location_row(0, {"name": "Room"})
                    self.assertFalse(manager.starting_locations_table.cellWidget(0, 6).property("authored_scope"))

    def test_automatic_scope_survives_setup_and_creation(self):
        setup = normalize_new_game_setup({"starting_locations": [{"name": "Room", "description": "A small private room", "location_mode": "exact"}]})
        self.assertNotIn("location_scope", setup["starting_locations"][0])
        rows = _travel_locations_for_save([{"name": "Room", "source_index": 0, "location_scope": "specific"}],
                                         setup, SimpleNamespace(start_location="Room"))
        self.assertEqual(rows[0]["location_scope"], "specific")

    def test_scope_controls_and_details_are_playtesting_only(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Scope")
            repo.set_state_value("location", "City")
            repo.set_travel_locations([{"name": "City", "location_scope": "broad"}])
            for playtesting in [False, True]:
                screen = TravelScreen(playtesting_tools=playtesting)
                self.addCleanup(screen.close)
                screen.set_repository(repo)
                self.assertEqual(screen.scope_selector.parentWidget().isHidden(), not playtesting)
                self.assertEqual("Storage:" in screen.details_output.toPlainText(), playtesting)
                screen.scope_selector.setCurrentIndex(screen.scope_selector.findData("specific"))
                self.assertEqual(repo.find_travel_location("City")["location_scope"],
                                 "specific" if playtesting else "broad")
