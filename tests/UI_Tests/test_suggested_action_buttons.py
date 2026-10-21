import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from ai_adventure.ai import gemini_service as g
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.story import StoryScreen
from tests.qt_test_case import QtTestCase


class SuggestedActionButtonTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = SaveRepository.create_new_save(Path(temporary.name), "Action buttons")
        self.repo.set_setting("player_name", "Iris Vale")
        self.screen = StoryScreen()
        self.screen.set_repository(self.repo)
        self.addCleanup(self.screen.deleteLater)

    def response(self):
        return g.parse_gemini_story_response(json.dumps({
            "response": "The camp is quiet.", "out_of_game": False, "events": [],
            "suggested_actions": [{"label": "Rest and eat", "prompt": "I will take some time to rest and eat."},
                                  {"label": "Inspect camp", "prompt": "I will inspect the camp for signs of visitors."}],
        }))

    def show_response(self):
        result = self.response()
        result = g._apply_story_guard(result, {"packet_type": "story_turn"}, g._ensure_status_event_for_in_game_response)
        StoryTurnService.commit_response(self.repo, result, message_id="suggestions")
        self.screen.refresh()
        return self.repo.list_history(limit=1)[0]

    def test_click_composes_editable_draft_and_only_submit_sends_edited_text(self):
        self.show_response()
        buttons = self.screen.suggested_action_buttons
        self.assertEqual([button.text() for button in buttons], ["Rest and eat", "Inspect camp"])
        self.assertEqual(buttons[0].toolTip(), "I will take some time to rest and eat.")
        with patch.object(self.screen, "_start_d20_test_planning_request") as start:
            buttons[0].click()
            start.assert_not_called()
            self.assertEqual(self.screen.player_input.text(), "I will take some time to rest and eat.")
            self.assertEqual(self.screen.player_input.cursorPosition(), len(self.screen.player_input.text()))
            self.assertFalse(self.screen._waiting_for_gm)
            self.assertEqual(self.repo.list_history(kinds=("player",)), [])
            self.screen.player_input.setText("I will rest, but save my food for later.")
            self.screen.submit_button.click()
        self.assertEqual(start.call_count, 1)
        player = self.repo.list_history(limit=1, kinds=("player",))[0]
        self.assertEqual(player["content"], "I will rest, but save my food for later.")
        self.assertTrue(self.screen._waiting_for_gm)
        self.screen._set_waiting_for_gm(False)

    def test_actions_persist_and_are_not_repeated_in_prompt_history(self):
        self.show_response()
        reloaded = SaveRepository(self.repo.db_path)
        self.screen.set_repository(reloaded)
        self.assertEqual(self.screen.suggested_action_buttons[0].text(), "Rest and eat")
        packet = StoryTurnService.build_context_packet(reloaded, "Look around.")
        self.assertTrue(all("suggested_actions" not in entry for entry in packet["recent_history"]))

    def test_stale_hidden_out_of_game_and_revealing_choices_cannot_replace_draft(self):
        entry = self.show_response()
        prompt = entry["suggested_actions"][0]["prompt"]
        self.screen.player_input.setText("My existing draft")
        with patch.object(self.screen, "_submit_player_action") as submit:
            self.screen.mode_button.setChecked(True)
            self.screen._compose_suggested_action(entry["id"], prompt)
            self.assertTrue(self.screen.suggested_actions_box.isHidden())
            self.screen.mode_button.setChecked(False)
            self.screen._revealing_story_id = entry["id"]
            self.screen._compose_suggested_action(entry["id"], prompt)
            self.screen._revealing_story_id = None
            self.repo.set_history_entry_hidden(entry["id"], True)
            self.screen._compose_suggested_action(entry["id"], prompt)
            self.repo.set_history_entry_hidden(entry["id"], False)
            self.repo.append_history("player", "Another action.")
            self.screen._compose_suggested_action(entry["id"], prompt)
        submit.assert_not_called()
        self.assertEqual(self.screen.player_input.text(), "My existing draft")



    def test_third_person_prompt_fills_draft_with_character_name(self):
        self.repo.set_setting("ai.narration_style", "third_person_limited")
        entry = self.show_response()
        self.assertEqual(entry["suggested_actions"][0]["prompt"], "Iris Vale will take some time to rest and eat.")
        with patch.object(self.screen, "_start_d20_test_planning_request") as start:
            self.screen.suggested_action_buttons[0].click()
        start.assert_not_called()
        self.assertEqual(self.screen.player_input.text(),
                         "Iris Vale will take some time to rest and eat.")
        self.assertEqual(self.repo.list_history(kinds=("player",)), [])




if __name__ == "__main__":
    unittest.main()
