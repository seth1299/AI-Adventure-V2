import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_adventure.ai.gemini_service import (
    AiNarrationResult, GeminiRequestError, _repair_missing_deadlines, _story_prompt_packet,
    build_story_response_schema,
)
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.deadlines import DEADLINE_RULE, missing_deadline_commitments
from ai_adventure.persistence.save_repository import SaveRepository


LOAN = "You sign your name in the register, committing to return the borrowed volumes within two fortnights."
PAYLOAD = {"name": "Return borrowed folios", "description": LOAN + " Return both folios to Archivist Vanja at The Scribe's Hall.",
           "category": "Loan", "status": "Active", "requester": "Archivist Vanja",
           "location": "The Scribe's Hall", "reward": "N/A", "due_date": "Day 29, 10:54 A.M.",
           "due_elapsed_minutes": 654 + 28 * 1440}
TASK = {"type": "ActiveTaskUpsertedEvent", "payload": PAYLOAD}
STATUS = {"type": "StatusUpdatedEvent", "payload": {"location": "AUTO", "minutes_passed": 5, "weather": "Clear"}}


class DeadlineCommitmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = SaveRepository.create_new_save(Path(temporary.name), "Loan")
        self.repo.set_state_value("location", "The Scribe's Hall")
        self.repo.set_current_calendar_minute(654)
        self.packet = StoryTurnService.build_context_packet(self.repo, "I would love to borrow both, please.")
        self.packet["selection"]["tags"] = []

    def test_calendar_task_context_and_schema_survive_empty_tags(self):
        packet = _story_prompt_packet(self.packet)
        for key in ("calendar", "active_tasks", "calendar_events"):
            self.assertIn(key, packet["state"])
        self.assertEqual(packet["state"]["calendar"]["current"]["current_minute"], 654)
        self.assertIn("settings", packet["state"]["calendar"]["current"])
        self.assertEqual(packet["response_contract"]["deadlines"], DEADLINE_RULE)
        schema = build_story_response_schema(self.packet, for_api=False)
        types = {branch["properties"]["type"]["enum"][0]
                 for branch in schema["properties"]["events"]["items"]["anyOf"]}
        self.assertTrue({"ActiveTaskUpsertedEvent", "ActiveTaskCompletedEvent", "CalendarEventUpsertedEvent"} <= types)

    def test_deadline_detected_and_related_saved_or_new_task_satisfies_it(self):
        self.assertEqual(missing_deadline_commitments(LOAN, [STATUS], []), [LOAN])
        self.assertEqual(missing_deadline_commitments(LOAN, [TASK], []), [])
        self.assertEqual(missing_deadline_commitments(LOAN, [], [PAYLOAD]), [])
        self.assertEqual(missing_deadline_commitments(LOAN, [], [{"name": "Flax", "due_elapsed_minutes": 100}]), [LOAN])

    def test_ordinary_durations_offers_and_hypotheticals_do_not_create_obligations(self):
        for text in ("You return home in two hours.", "The flax will mature in two days.",
                     "If you borrow these books, return them within two fortnights.",
                     "You are welcome to borrow both books for two fortnights.",
                     "You no longer need to return the books by tomorrow."):
            self.assertEqual(missing_deadline_commitments(text, [], []), [], text)

    def test_explicit_dates_and_relative_days_are_detected(self):
        for text in ("The books are due tomorrow.", "You must return the books by Blossomwithe 1.",
                     "You promise to deliver the order by next Gilday."):
            self.assertEqual(missing_deadline_commitments(text, [], []), [text])

    def test_completed_loan_can_reference_original_deadline(self):
        StoryTurnService.commit_response(self.repo, AiNarrationResult(LOAN, suggested_events=[TASK, STATUS]), message_id="loan")
        result = AiNarrationResult("You return the borrowed volumes before their deadline in two fortnights.",
                                  suggested_events=[{"type": "ActiveTaskCompletedEvent", "payload": {"name": PAYLOAD["name"]}}])
        StoryTurnService.commit_response(self.repo, result, message_id="returned")
        self.assertEqual(self.repo.list_active_tasks(), [])

    def test_event_only_repair_preserves_narrative_items_and_final_status(self):
        item = {"type": "InventoryItemAddedEvent", "payload": {"item_name": "Folio", "amount": 1}}
        result = AiNarrationResult(LOAN, suggested_events=[item, STATUS], raw_text="original")
        with patch("ai_adventure.ai.gemini_service._create_interaction_with_retry",
                   return_value=SimpleNamespace(output_text=json.dumps({"events": [TASK]}))) as request:
            repaired = _repair_missing_deadlines(None, "test", result, self.packet, ai_preferences={})
        self.assertEqual(repaired.narrative_text, LOAN)
        self.assertEqual(repaired.raw_text, "original")
        self.assertEqual(repaired.suggested_events, [item, TASK, STATUS])
        self.assertIn("current_minute", request.call_args.kwargs["contents"])
        request.assert_called_once()

    def test_no_extra_request_when_recorded_or_out_of_game(self):
        with patch("ai_adventure.ai.gemini_service._create_interaction_with_retry") as request:
            for result in (AiNarrationResult(LOAN, suggested_events=[TASK]),
                           AiNarrationResult(LOAN, out_of_game=True)):
                self.assertIs(_repair_missing_deadlines(None, "test", result, self.packet, ai_preferences={}), result)
            request.assert_not_called()

    def test_repair_failure_is_bounded_and_does_not_save(self):
        unrelated = {"type": "ActiveTaskUpsertedEvent", "payload": {**PAYLOAD, "name": "Grow flax", "description": "Tend flax."}}
        with patch("ai_adventure.ai.gemini_service._create_interaction_with_retry",
                   return_value=SimpleNamespace(output_text=json.dumps({"events": [unrelated]}))) as request:
            with self.assertRaises(GeminiRequestError):
                _repair_missing_deadlines(None, "test", AiNarrationResult(LOAN), self.packet, ai_preferences={})
            request.assert_called_once()

    def test_omitted_deadline_cannot_be_committed_as_prose_only(self):
        before = self.repo.list_history()
        with self.assertRaisesRegex(ValueError, "no dated task"):
            StoryTurnService.commit_response(self.repo, AiNarrationResult(LOAN, suggested_events=[STATUS]), message_id="missing")
        self.assertEqual(self.repo.list_history(), before)
        self.assertEqual(self.repo.get_current_calendar_minute(), 654)

    def test_loan_deadline_survives_reload_and_commit_replay_without_reset(self):
        result = AiNarrationResult(LOAN, suggested_events=[TASK, STATUS])
        StoryTurnService.commit_response(self.repo, result, message_id="loan")
        reopened = SaveRepository(self.repo.db_path)
        self.assertEqual(reopened.list_active_tasks()[0]["due_elapsed_minutes"], PAYLOAD["due_elapsed_minutes"])
        StoryTurnService.commit_response(reopened, result, message_id="loan")
        self.assertEqual(reopened.get_current_calendar_minute(), 659)
        self.assertEqual(len(reopened.list_active_tasks()), 1)

    def test_invalid_deadline_record_rolls_back_inventory_time_and_narration(self):
        broken = {"type": "ActiveTaskUpsertedEvent", "payload": {**PAYLOAD, "description": ""}}
        before = self.repo.list_history()
        with self.assertRaisesRegex(ValueError, "could not be persisted"):
            StoryTurnService.commit_response(self.repo, AiNarrationResult(LOAN, suggested_events=[broken, STATUS]), message_id="invalid")
        self.assertEqual(self.repo.list_history(), before)
        self.assertEqual(self.repo.get_current_calendar_minute(), 654)

    def test_packaged_defaults_carry_the_shared_rule(self):
        for name in ("default_context.json", "default_rules.json"):
            text = (Path(__file__).resolve().parents[2] / "ai_adventure/data/context" / name).read_text(encoding="utf-8")
            self.assertIn(DEADLINE_RULE, json.dumps(json.loads(text), ensure_ascii=False))
