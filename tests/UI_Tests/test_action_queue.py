from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QModelIndex, QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QPlainTextEdit

from ai_adventure.ai.gemini_service import (
    AiNarrationResult, build_d20_test_plan_prompt, build_gemini_story_prompt,
    parse_d20_test_plan_response,
)
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.turn_actions import TurnAction
from ai_adventure.ui.screens.story import StoryScreen
from tests.qt_test_case import QtTestCase


def status(index, *, location="SAME", minutes=0, **metadata):
    return {"type": "StatusUpdatedEvent", "action_index": index,
            "payload": {"location": location, "minutes_passed": minutes}, **metadata}


class ActionQueueTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = SaveRepository.create_new_save(Path(directory.name), "Typed actions")
        self.repo.set_state_value("location", "Office")
        self.screen = StoryScreen()
        self.screen.set_repository(self.repo)

    def select(self, action_type):
        self.screen.action_combo.setCurrentIndex(self.screen.action_combo.findData(action_type))

    def test_queue_edits_drag_order_and_submits_one_typed_turn(self):
        self.select("say")
        self.screen.player_input.setText("I'll be right back.")
        QTimer.singleShot(0, lambda: self.app.activeModalWidget().reject())
        self.screen.queue_button.click()
        self.assertEqual(self.screen.queue_button.text(), "Queue (1)")
        self.assertFalse(self.repo.list_history(kinds=("player",)))
        self.select("do")
        self.screen.player_input.setText("Search the desk.")

        def edit():
            editor = self.app.activeModalWidget()
            combo = editor.findChild(QComboBox)
            combo.setCurrentIndex(combo.findData("retcon"))
            editor.findChild(QPlainTextEdit).setPlainText("I would have checked the desk earlier.")
            editor.accept()

        def arrange():
            dialog = self.app.activeModalWidget()
            QTimer.singleShot(0, edit)
            dialog._edit_entry(dialog.entries.item(1))
            QTimer.singleShot(0, edit)
            dialog._edit_entry(None)
            dialog.remove.click()
            dialog.entries.model().moveRow(QModelIndex(), 1, QModelIndex(), 0)
            dialog.submit_all.click()

        with patch.object(self.screen, "_start_d20_test_planning_request") as plan:
            QTimer.singleShot(0, arrange)
            self.screen.queue_button.click()
        plan.assert_called_once()
        packet = plan.call_args.args[0]
        self.assertEqual([action["action_type"] for action in packet["player_actions"]], ["retcon", "say"])
        self.assertIn("I would have checked the desk earlier.", build_d20_test_plan_prompt(packet))
        self.assertIn("I would have checked the desk earlier.", build_gemini_story_prompt(packet))
        self.assertEqual(self.screen.queue_button.text(), "Queue")
        [submitted] = self.repo.list_history(kinds=("player",))
        self.assertIn("2. Say:", submitted["content"])
        self.assertEqual(self.repo.event_receipt(submitted["turn_id"], "player_actions")["entries"], packet["player_actions"])

    def test_conditional_checks_and_events_follow_authoritative_outcome(self):
        actions = [TurnAction("do", "Open the stuck door."),
                   TurnAction("do", "If action 1 fails, try the side window."),
                   TurnAction("say", "If action 1 succeeds, call out."),
                   TurnAction("ask_gm", "What would I remember about this house?")]
        with patch.object(self.screen, "_start_d20_test_planning_request"):
            self.assertTrue(self.screen._submit_player_actions(actions))
        parse = parse_d20_test_plan_response('''{
            "checks":[
                {"action_index":1,"attribute":"Strength","test_kind":"check","reason":"Open the door","dc":99},
                {"action_index":2,"attribute":"Dexterity","test_kind":"check","reason":"Reach the window","dc":1},
                {"action_index":3,"attribute":"Charisma","test_kind":"check","reason":"Call out","dc":1},
                {"action_index":4,"attribute":"Wisdom","test_kind":"check","reason":"Unsolicited","dc":1}],
            "relevant_tags":[],"conditions":[
                {"action_index":2,"depends_on":1,"outcome":"failure"}]}''')
        with patch.object(self.screen, "_start_gemini_story_request") as narrate:
            self.screen._handle_d20_test_plan_result(parse)
        packet = narrate.call_args.args[0]
        self.assertEqual(set(packet["inactive_actions"]), {3})
        self.assertEqual([(check["action_index"], check["outcome"]) for check in packet["state"]["skills"]["resolved_checks_this_turn"]],
                         [(1, "failure"), (2, "success")])
        minute = self.repo.get_current_calendar_minute()
        self.screen._handle_gemini_story_result(AiNarrationResult("The window gives way.", suggested_events=[
            status(2, minutes=2), status(3, minutes=50), status(4, minutes=50),
        ]))
        self.assertEqual(self.repo.get_current_calendar_minute(), minute + 2)

    def test_retcon_and_ask_the_gm_preserve_current_scene(self):
        self.repo.append_history("story", "The speaker gave her account earlier.", message_id="earlier-scene")
        self.select("retcon")
        self.screen.player_input.setText("I would have watched for signs of dishonesty.")
        with patch.object(self.screen, "_start_d20_test_planning_request") as plan:
            self.screen.submit_button.click()
        plan.assert_called_once()
        minute = self.repo.get_current_calendar_minute()
        self.screen._handle_gemini_story_result(AiNarrationResult("In hindsight, you recall a pause.", suggested_events=[
            status(1, minutes=50, location="Road"),
        ]))
        self.assertEqual(self.repo.get_state_value("location"), "Office")
        self.assertEqual(self.repo.get_current_calendar_minute(), minute)
        self.assertEqual(self.repo.list_history(kinds=("story",))[0]["message_id"], "earlier-scene")

        self.select("ask_gm")
        self.screen.player_input.setText("What might I remember about the speaker?")
        with patch.object(self.screen, "_start_d20_test_planning_request") as plan, patch.object(self.screen, "_start_gemini_story_request") as answer:
            self.screen.submit_button.click()
        plan.assert_not_called()
        self.assertEqual(answer.call_args.args[0]["conversation_mode"], "out_of_game")
        self.screen._handle_gemini_story_result(AiNarrationResult("You remember their earlier visit."))
        self.assertEqual(self.repo.list_history(limit=1)[0]["kind"], "story_oog")

    def test_justified_retcon_correction_preserves_clock(self):
        with patch.object(self.screen, "_start_d20_test_planning_request"):
            self.screen._submit_player_actions([TurnAction("retcon", "The scene was at the station. Correct where I am.")])
        minute = self.repo.get_current_calendar_minute()
        event = status(1, location="Station", minutes=45,
                       retcon_correction_reason="The player explicitly corrected the current scene's location.")
        self.screen._handle_gemini_story_result(AiNarrationResult("The scene was at the station.", suggested_events=[event]))
        self.assertEqual(self.repo.get_state_value("location"), "Station")
        self.assertEqual(self.repo.get_current_calendar_minute(), minute)
