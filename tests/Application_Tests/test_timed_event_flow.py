import json
import tempfile
import unittest
from pathlib import Path

from ai_adventure.ai.gemini_service import (
    parse_gemini_new_game_response, parse_gemini_story_response,
    build_gemini_new_game_phase_prompt, build_gemini_story_prompt,
    build_new_game_phase_schema, build_story_response_schema,
)
from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.new_game_setup import normalize_new_game_setup, build_new_game_setup_packet
from ai_adventure.persistence.save_repository import SaveRepository


class TimedEventFlowTests(unittest.TestCase):
    def test_conflicting_timer_rolls_back_the_whole_wait_and_visit_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Conflicting timer")
            repo.set_travel_locations([{"name": "Garden", "location_scope": "specific"}])
            repo.set_current_calendar_minute(480)
            repo.upsert_timed_event(name="Flax", location="Garden", ready_at_minute=1920, notes="Original")
            before = repo.list_history()
            result = parse_gemini_story_response(json.dumps({
                "response": "You wait, travel to the garden and inspect the flax.",
                "out_of_game": False, "suggested_actions": [], "events": [
                    {"type": "TimedEventUpsertedEvent", "payload": {"name": "Flax", "location": "Garden", "notes": "Must roll back"}},
                    {"type": "StatusUpdatedEvent", "payload": {"location": "Garden", "minutes_passed": 100, "weather": "Clear"}},
                    {"type": "TimedEventUpsertedEvent", "payload": {"name": "Flax", "location": "Garden", "duration_minutes": 90, "ready_at_minute": 700}},
                ],
            }))
            with self.assertRaisesRegex(ValueError, "Conflicting timer times"):
                StoryTurnService.commit_response(repo, result, message_id="conflicting")
            self.assertEqual(repo.get_current_calendar_minute(), 480)
            self.assertEqual(repo.list_history(), before)
            self.assertEqual(repo.list_timed_events()[0]["notes"], "Original")
            self.assertEqual(repo.list_timed_events()[0]["ready_at_minute"], 1920)
            self.assertEqual(repo.list_mechanical_events(), [])

    def test_gemini_timer_schema_exposes_only_absolute_time(self):
        for api in (False, True):
            schema = build_story_response_schema({"selection": {"tags": []}}, for_api=api)
            branch = next(branch for branch in schema["properties"]["events"]["items"]["anyOf"]
                          if branch["properties"]["type"]["enum"] == ["TimedEventUpsertedEvent"])
            fields = branch["properties"]["payload"]["properties"]
            self.assertIn("ready_at_minute", fields)
            self.assertNotIn("duration_minutes", fields)

    def test_wait_travel_and_crop_update_commit_without_restarting_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Wait then visit garden")
            repo.set_travel_locations([{"name": "Garden", "location_scope": "specific"}])
            repo.set_current_calendar_minute(480)
            repo.upsert_timed_event(name="Flax", location="Garden", ready_at_minute=1920)
            result = parse_gemini_story_response(json.dumps({
                "response": "After resting for ninety minutes, you visit the garden and inspect the flax.",
                "out_of_game": False, "suggested_actions": [], "events": [
                    {"type": "StatusUpdatedEvent", "payload": {"location": "Garden", "minutes_passed": 100, "weather": "Clear"}},
                    {"type": "TimedEventUpsertedEvent", "payload": {"name": "Flax", "location": "Garden", "notes": "Inspected; healthy."}},
                ],
            }))
            StoryTurnService.commit_response(repo, result, message_id="wait-and-visit")
            self.assertEqual(repo.get_current_calendar_minute(), 580)
            self.assertEqual(repo.get_state_value("location"), "Garden")
            self.assertEqual(repo.list_timed_events()[0]["ready_at_minute"], 1920)
            self.assertEqual(repo.list_timed_events()[0]["notes"], "Inspected; healthy.")

    def test_invalid_timed_change_rolls_back_the_turn_and_prior_timer_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Atomic garden")
            repo.set_travel_locations([{"name": "Garden", "location_scope": "specific"}])
            repo.upsert_timed_event(name="Flax", location="Garden", notes="Original notes")
            history = repo.list_history()
            result = parse_gemini_story_response(json.dumps({
                "response": "You tend the garden.", "out_of_game": False, "suggested_actions": [],
                "events": [
                    {"type": "TimedEventUpsertedEvent", "payload": {
                        "name": "Flax", "location": "Garden", "notes": "This must roll back"}},
                    {"type": "TimedEventUpsertedEvent", "payload": {
                        "name": "Hemp", "location": "Garden", "duration_minutes": -10}},
                ],
            }))
            with self.assertRaisesRegex(ValueError, "Time-sensitive event changes"):
                StoryTurnService.commit_response(repo, result, message_id=repo.create_message_id())
            self.assertEqual(repo.list_history(), history)
            self.assertEqual(len(repo.list_timed_events()), 1)
            self.assertEqual(repo.list_timed_events()[0]["notes"], "Original notes")

    def test_setup_prose_reaches_ai_and_generated_plants_persist_then_update_during_play(self):
        setup = normalize_new_game_setup({
            "title": "Garden", "starting_task": {"mode": "none"},
            "starting_locations": [{"name": "Garden: flax (3 shoots), hemp and cotton grow here",
                                    "description": "Young plants in beds outside the cottage.",
                                    "location_mode": "suggestion"}],
        })
        packet = build_new_game_setup_packet(setup)
        prompt = build_gemini_new_game_phase_prompt(packet, "entities_mechanics")
        self.assertIn("flax (3 shoots), hemp and cotton", prompt)
        self.assertIn("TimedEventUpsertedEvent", prompt)
        self.assertIn("each named plant", prompt)
        schema = build_new_game_phase_schema(packet, "entities_mechanics")
        self.assertIn("timed_events", schema["properties"])
        self.assertIn("timed_events", schema["required"])
        raw = {
            "world_summary": "A quiet cottage garden.", "start_location": "Cottage Garden", "weather": "Clear",
            "introductory_message": "Young flax and hemp grow in the garden beds.",
            "locations": [{"name": "Cottage Garden", "description": "A private garden with young crops.",
                           "location_scope": "specific", "source_index": 0, "x_miles": 0, "y_miles": 0}],
            "timed_events": [{
                "name": name, "location": "Cottage Garden", "quantity": count, "quantity_unit": "shoots",
                "current_state": "Growing", "next_state": "Mature", "ready_at_minute": 4800,
                "category": "Growth",
                "is_estimate": True, "quantity_is_estimate": name != "Flax",
                "notes": "Young plants; soil is moist. Unstated counts estimated for a small garden bed.",
            } for name, count in (("Flax", 3), ("Hemp", 6), ("Cotton", 4))],
        }
        result = parse_gemini_new_game_response(json.dumps(raw), setup_packet=packet)
        with tempfile.TemporaryDirectory() as directory:
            repo = NewGameService.create_repository(Path(directory), setup)
            committed = NewGameService.commit_generated_world(repo, setup, result)
            self.assertTrue(all(event.status == "applied" for event in committed.event_results))
            reopened = SaveRepository(repo.db_path)
            self.assertEqual(len(reopened.list_timed_events()), 3)
            flax = next(row for row in reopened.list_timed_events() if row["name"] == "Flax")
            self.assertEqual(flax["quantity"], 3)
            self.assertFalse(flax["quantity_is_estimate"])
            hemp = next(row for row in reopened.list_timed_events() if row["name"] == "Hemp")
            self.assertEqual(hemp["quantity"], 6)
            self.assertTrue(hemp["quantity_is_estimate"])
            turn = StoryTurnService.build_context_packet(reopened, "Loosen the soil and add compost.")
            # Timers and update rules must survive even when task/quest routing was missed.
            turn["selection"]["tags"] = []
            story_prompt = build_gemini_story_prompt(turn)
            self.assertIn(flax["id"], story_prompt)
            self.assertIn("TimedEventUpsertedEvent", story_prompt)
            self.assertIn("must not reset", story_prompt)
            self.assertIn("infer a reasonable positive count", story_prompt)
            self.assertIn("quantity_is_estimate", story_prompt)
            self.assertIn("TimedEventUpsertedEvent", json.dumps(build_story_response_schema(turn)))
            narration = parse_gemini_story_response(json.dumps({
                "response": "You loosen the soil and add compost around the flax.",
                "suggested_actions": [], "out_of_game": False,
                "events": [{"type": "TimedEventUpsertedEvent", "payload": {
                    "event_id": flax["id"], "name": "Flax", "location": "Cottage Garden",
                    "notes": "Soil loosened and compost added. Healthy growth.",
                }}],
            }))
            StoryTurnService.commit_response(reopened, narration, message_id=reopened.create_message_id())
            updated = next(row for row in reopened.list_timed_events() if row["name"] == "Flax")
            self.assertEqual(updated["ready_at_minute"], flax["ready_at_minute"])
            self.assertIn("compost", updated["notes"])
            self.assertEqual(len(reopened.list_timed_events()), 3)
