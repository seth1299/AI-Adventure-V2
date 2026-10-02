from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_adventure.ai import gemini_service as g
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.persistence.save_repository import SaveRepository


class SkillCheckAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = SaveRepository.create_new_save(Path(temporary.name), "Skill check audit")

    def packet(self, command):
        return {"packet_type": "story_turn", "player_command": command,
                "selection": {"tags": ["skill"]}}

    def generate(self, packet):
        from google import genai
        response = {"response": "You observe the square.", "suggested_actions": ["Look around."],
                    "out_of_game": False, "events": [{"type": "SkillCheckRequestedEvent",
                    "payload": {"skill_name": "Perception", "dc": 13, "reason": "Look around."}}]}
        with patch.object(genai, "Client") as client:
            client.return_value.models.generate_content.return_value = SimpleNamespace(text=json.dumps(response))
            return g.GeminiNarrationService(g.GeminiSettings(api_key="test-key")).generate_story_response(packet)

    def test_actual_lift_and_escape_preserves_planned_checks(self):
        packet = self.packet("Once I am sure of a good opportunity, I will make a move to attempt to lift his pouch and silently flee.")
        checks = [{"skill_name": "Pickpocketing", "dc": 15}, {"skill_name": "Stealth", "dc": 13}]
        result = g._filter_unwarranted_planned_skill_checks(g.SkillCheckPlanResult(checks=checks), packet)
        self.assertEqual(result.checks, checks)
        result = self.generate(packet)
        self.assertTrue(any(event["type"] == "SkillCheckRequestedEvent" for event in result.suggested_events))
        self.assertFalse(result.dropped_events)

    def test_compound_or_uncertain_actions_are_not_classified_as_routine(self):
        for command in ("Talk to him and get him to reveal his secret.", "Walk across a narrow bridge.",
                        "Move toward his belt pouch.", "Ask him to let me past the guards.",
                        "I will walk to the market and watch for an opportunity."):
            with self.subTest(command=command):
                self.assertFalse(g._player_command_is_routine_no_check(self.packet(command)))

    def test_risky_check_reason_overrides_simple_movement(self):
        check = {"skill_name": "Stealth", "dc": 15, "reason": "Avoid the watchful guards."}
        result = g._filter_unwarranted_planned_skill_checks(
            g.SkillCheckPlanResult(checks=[check]), self.packet("Walk to the market."))
        self.assertEqual(result.checks, [check])

    def test_routine_planner_drop_is_audited_without_a_roll(self):
        check = {"skill_name": "Navigation", "dc": 8, "reason": "Walking across town."}
        result = g._filter_unwarranted_planned_skill_checks(
            g.SkillCheckPlanResult(checks=[check]), self.packet("Walk to the market."))
        self.assertFalse(result.checks)
        StoryTurnService.record_dropped_events(self.repository, result, message_id="planned-drop")
        event = self.repository.list_mechanical_events()[0]
        self.assertEqual(event["status"], "dropped")
        self.assertEqual(event["payload"], check)
        self.assertEqual(event["message_id"], "planned-drop")
        self.assertIn("skill_check_planning", event["message"])
        self.assertNotIn("roll", event["payload"])

    def test_routine_story_drop_survives_other_guards_and_is_committed(self):
        result = self.generate(self.packet("Walk to the market."))
        self.assertEqual(len(result.dropped_events), 1)
        StoryTurnService.commit_response(self.repository, result, message_id="story-drop")
        event = next(event for event in self.repository.list_mechanical_events() if event["status"] == "dropped")
        self.assertEqual(event["event_type"], "SkillCheckRequestedEvent")
        self.assertEqual(event["payload"]["dc"], 13)
        self.assertIn("drop_unwarranted_skill_check_events", event["message"])
        self.assertEqual(event["message_id"], "story-drop")

    def test_duplicate_check_is_audited_without_a_second_roll(self):
        packet = self.packet("Observe the merchant.")
        packet["state"] = {"skills": {"resolved_checks_this_turn": [
            {"skill_name": "Perception", "outcome": "success", "total": 15, "dc": 13}]}}
        result = self.generate(packet)
        self.assertFalse(any(event["type"] == "SkillCheckRequestedEvent" for event in result.suggested_events))
        StoryTurnService.commit_response(self.repository, result, message_id="duplicate-drop")
        event = next(event for event in self.repository.list_mechanical_events() if event["status"] == "dropped")
        self.assertIn("already resolved", event["message"])
        self.assertNotIn("roll", event["payload"])

    def test_dropped_audit_rolls_back_with_failed_story_commit(self):
        result = self.generate(self.packet("Walk to the market."))
        with patch.object(self.repository, "append_history", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                StoryTurnService.commit_response(self.repository, result, message_id="failed-story")
        self.assertFalse(self.repository.list_mechanical_events())

    def test_payload_correction_is_not_mislabeled_as_a_drop(self):
        original = g.AiNarrationResult("Time passes.", suggested_events=[
            {"type": "StatusUpdatedEvent", "payload": {"weather": "AUTO"}}])
        def correct(result, packet):
            return g.replace(result, suggested_events=[
                {"type": "StatusUpdatedEvent", "payload": {"weather": "Rain"}}])
        corrected = g._apply_story_guard(original, {}, correct)
        self.assertFalse(corrected.dropped_events)

    def test_duplicate_removal_records_the_correct_payload(self):
        packet = self.packet("Observe the merchant.")
        packet["state"] = {"skills": {"resolved_checks_this_turn": [{"skill_name": "Perception"}]}}
        original = g.AiNarrationResult("Time passes.", suggested_events=[
            {"type": "SkillCheckRequestedEvent", "payload": {"skill_name": "Perception", "dc": 13}},
            {"type": "SkillCheckRequestedEvent", "payload": {"skill_name": "Stealth", "dc": 15}}])
        filtered = g._apply_story_guard(original, packet, g._drop_duplicate_resolved_skill_check_events)
        self.assertEqual(filtered.dropped_events[0]["payload"]["skill_name"], "Perception")
        self.assertEqual(filtered.suggested_events[0]["payload"]["skill_name"], "Stealth")
