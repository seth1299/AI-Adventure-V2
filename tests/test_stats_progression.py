from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ai_adventure.stats import ATTRIBUTES, POINT_COSTS, RANK_STATS, attribute_modifier, point_buy_cost, starting_attributes
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository, SaveFileOperationError
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.core.state_manager import StateManager
from ai_adventure.ai.gemini_service import parse_d20_test_plan_response, D20_TEST_PLAN_RESPONSE_JSON_SCHEMA


class Dice:
    def __init__(self, *rolls):
        self.rolls = iter(rolls)
        self.calls = 0

    def randint(self, minimum, maximum):
        self.calls += 1
        return next(self.rolls)


class StatsProgressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "adventure.db"
        self.repo = SaveRepository(self.path)
        self.repo.initialize_player_stats(dict.fromkeys(ATTRIBUTES, 10))

    def tearDown(self):
        self.temp.cleanup()

    def event(self, payload, *rolls, message="action"):
        return EventApplier(self.repo, rng=Dice(*rolls), message_id=message).apply_events([
            {"type": "D20TestRequestedEvent", "payload": {"attribute": "Strength", "test_kind": "check", "reason": "Climb a dangerous cliff", **payload}}
        ])[0]

    def test_rank_allocations_and_creation_authority(self):
        for rank, (budget, level) in RANK_STATS.items():
            with self.subTest(rank=rank):
                setup = normalize_new_game_setup({"skill_preset": rank})
                self.assertEqual(setup["point_buy_budget"], budget)
                self.assertEqual(setup["starting_player_level"], level)
                self.assertEqual(point_buy_cost(setup["character"]["attributes"]), budget)
        self.assertEqual(list(POINT_COSTS.values()), [0, 1, 2, 3, 4, 5, 7, 9, 12, 15, 19])
        self.assertEqual(attribute_modifier(8), -1)
        self.assertEqual(attribute_modifier(20), 5)
        with self.assertRaises(ValueError):
            normalize_new_game_setup({"character": {"attributes": dict.fromkeys(ATTRIBUTES, 18)}})

    def test_untrained_and_trained_tests_without_hidden_adjustments(self):
        self.repo.set_setting("player.attributes", {**dict.fromkeys(ATTRIBUTES, 10), "Strength": 8})
        result = self.event({"dc": 10}, 10)
        self.assertEqual(result.payload["total"], 9)
        self.assertEqual(result.payload["outcome"], "failure")
        self.assertEqual(self.repo.list_skills(), [])
        self.repo.upsert_skill("Climbing", "Climb difficult surfaces", 3)
        result = self.event({"dc": 10, "skill_name": "Climbing"}, 10, message="next")
        self.assertEqual(result.payload["total"], 12)
        self.assertEqual(result.payload["skill_bonus"], 3)
        self.assertEqual(StateManager(self.repo).load_state().skills.recent_checks[0].attribute, "Strength")

    def test_natural_roll_totals_for_all_kinds(self):
        for kind in ("check", "attack", "save"):
            result = self.event({"test_kind": kind, "dc": 21}, 20, message=kind)
            self.assertEqual(result.payload["outcome"], "failure")
            result = self.event({"test_kind": kind, "dc": 1}, 1, message=kind + "-low")
            self.assertEqual(result.payload["outcome"], "success")

    def test_advantage_disadvantage_cancel_and_retry(self):
        for advantage, disadvantage, expected, rolls in ((True, False, 17, (2, 17)), (False, True, 2, (2, 17)), (True, True, 9, (9,))):
            result = self.event({"advantage": advantage, "disadvantage": disadvantage}, *rolls, message=str((advantage, disadvantage)))
            self.assertEqual(result.payload["roll"], expected)
        result = self.event({"request_id": "stable-test"}, 13, message="retry")
        result2 = self.event({"request_id": "stable-test"}, message="retry")
        self.assertEqual(result2.payload["roll"], result.payload["roll"])

    def test_xp_bank_spending_and_training_progress(self):
        self.repo.initialize_player_stats(dict.fromkeys(ATTRIBUTES, 10), 3)
        self.assertEqual(self.repo.player_stats()["reward_choices"], 0)
        for index in range(4):
            self.repo.record_player_achievement(f"milestone-{index}", "major", "Complete a distinct milestone")
        self.assertEqual(self.repo.player_stats()["level"], 5)
        self.assertEqual(self.repo.player_stats()["reward_choices"], 2)
        self.assertEqual(self.repo.record_player_achievement("milestone-0", "major", "Same milestone")["status"], "duplicate")
        self.repo.change_player_health(-7, "A fall", "fall")
        self.repo.spend_player_reward("attribute", attribute="Constitution")
        self.assertEqual(self.repo.player_stats()["health_current"], 15)
        self.assertEqual(self.repo.player_stats()["health_max"], 22)
        self.repo.spend_player_reward("skills")
        self.repo.spend_skill_advance("Climbing", "Climb difficult surfaces")
        self.repo.add_skill_xp("Climbing", 3)
        self.repo.spend_skill_advance("Climbing")
        skill = self.repo.get_skill("Climbing")
        self.assertEqual((skill["level"], skill["xp"], skill["bonus"]), (2, 11, 2))
        reopened = SaveRepository(self.path)
        self.assertEqual(reopened.player_stats()["skill_advances"], 0)
        self.assertEqual(reopened.get_skill("Climbing")["xp"], 11)

    def test_caps_invalid_rewards_and_skill_learning(self):
        self.repo.set_setting("player.reward_choices", 2)
        self.repo.set_setting("player.attributes", dict.fromkeys(ATTRIBUTES, 20))
        with self.assertRaises(ValueError):
            self.repo.spend_player_reward("attribute", attribute="Strength")
        self.assertEqual(self.repo.player_stats()["reward_choices"], 2)
        self.repo.spend_player_reward("skills")
        self.repo.upsert_skill("Master", "A mastered scope", 5)
        with self.assertRaises(ValueError):
            self.repo.spend_skill_advance("Master")
        self.assertEqual(self.repo.player_stats()["skill_advances"], 2)
        for level, reason, expected in ((2, "Training", "skipped"), (1, "", "skipped"), (1, "A week of instruction", "applied")):
            result = EventApplier(self.repo).apply_events([{"type": "SkillUpsertedEvent", "payload": {"name": "Sailing", "description": "Operate sailing vessels", "level": level, "reason": reason}}])[0]
            self.assertEqual(result.status, expected)

    def test_health_clamping_incapacitation_and_strength_capacity(self):
        self.repo.change_player_health(-1000, "Severe injury", "injury")
        self.assertEqual(self.repo.player_stats()["health_current"], 0)
        self.assertEqual(self.repo.get_state_value("condition"), "Incapacitated")
        self.assertEqual(self.event({"test_kind": "attack"}).status, "skipped")
        self.repo.change_player_health(1000, "Treatment", "treatment")
        self.assertEqual(self.repo.player_stats()["health_current"], 20)
        self.repo.set_setting("player.reward_choices", 1)
        self.repo.spend_player_reward("attribute", attribute="Strength")
        self.assertEqual(self.repo.player_carrying_capacity_lb(), 55)

    def test_player_level_cap_and_skill_xp_initialization(self):
        self.repo.initialize_player_stats(dict.fromkeys(ATTRIBUTES, 10), 20)
        self.repo.record_player_achievement("final", "major", "An achievement")
        self.assertEqual(self.repo.player_stats()["level"], 20)
        self.assertEqual(self.repo.player_stats()["reward_choices"], 0)
        self.repo.replace_skills([{"name": "Athletics", "description": "Physical effort", "level": 3}])
        self.assertEqual(self.repo.get_skill("Athletics")["xp"], 16)

    def test_transaction_and_snapshot_restore_all_progression(self):
        self.repo.capture_message_snapshot("before")
        self.repo.record_player_achievement("first", "major", "Milestone")
        self.repo.rollback_message("before")
        self.assertEqual(self.repo.list_progression_records(), [])
        self.assertEqual(self.repo.player_stats()["xp"], 0)
        with self.assertRaises(RuntimeError):
            with self.repo.transaction():
                self.repo.record_player_achievement("rolled-back", "major", "Milestone")
                raise RuntimeError("fail batch")
        self.assertEqual(self.repo.list_progression_records(), [])

    def test_legacy_save_rejected_without_writes(self):
        old = Path(self.temp.name) / "old.db"
        with sqlite3.connect(old) as connection:
            connection.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        connection.close()
        original = old.read_bytes()
        with self.assertRaises(SaveFileOperationError):
            SaveRepository(old)
        self.assertEqual(old.read_bytes(), original)

    def test_planner_supports_attribute_only_tests(self):
        result = parse_d20_test_plan_response(json.dumps({"checks": [{"attribute": "Strength", "test_kind": "check", "reason": "Climb dangerous cliff", "dc": 15}], "relevant_tags": []}))
        self.assertEqual(len(result.checks), 1)
        required = D20_TEST_PLAN_RESPONSE_JSON_SCHEMA["properties"]["checks"]["items"]["required"]
        self.assertNotIn("skill_name", required)

    def test_turn_retry_does_not_duplicate_health_xp_or_inventory(self):
        from types import SimpleNamespace
        from ai_adventure.application.story_turn_service import StoryTurnService
        result = SimpleNamespace(narrative_text="The milestone is complete; you find a gem.", sound_effect_cues=[], speaker_cues=[], dropped_events=[], suggested_events=[
            {"type": "PlayerHealthChangedEvent", "payload": {"delta": -3, "reason": "A difficult rescue", "source_id": "rescue-health"}},
            {"type": "PlayerAchievementRecordedEvent", "payload": {"source_id": "rescue", "source_kind": "milestone", "significance": "major", "reason": "Rescue completed"}},
            {"type": "InventoryItemAddedEvent", "payload": {"item_name": "Gem", "item_type": "Item", "description": "A reward gem", "amount": 1, "value_base_units": 1}}
        ])
        first = StoryTurnService.commit_response(self.repo, result, message_id="rescue-turn")
        second = StoryTurnService.commit_response(self.repo, result, message_id="rescue-turn")
        self.assertEqual(first, second)
        self.assertEqual(self.repo.player_stats()["health_current"], 17)
        self.assertEqual(self.repo.player_stats()["xp"], 50)
        self.assertEqual(next(item for item in self.repo.list_inventory_items() if item["name"] == "Gem")["quantity"], 1)
        self.assertEqual(len(self.repo.list_history(kinds=("story",))), 1)

    def test_untrained_container_test_and_equipped_carried_bag(self):
        self.repo.upsert_travel_location({"name": "Store", "location_scope": "specific"})
        self.repo.set_state_value("location", "Store")
        self.repo.replace_inventory_items([
            {"name": "Locked Box", "quantity": 1, "category": "Container", "description": "A locked box.", "storage_location": "Store", "container": {"is_locked": True, "lockpick_dc": 10, "contents_initialized": True, "contents": {"items": [], "currency_base_units": 0}}},
            {"name": "Backpack", "quantity": 1, "category": "Container", "description": "A pack.", "storage_location": "Store", "weight_lb": 2, "carrying_capacity_lb": 20, "container": {"is_open": True, "contents_initialized": True, "contents": {"items": [], "currency_base_units": 0}}}
        ])
        results = EventApplier(self.repo, rng=Dice(15), message_id="box").apply_events([
            {"type": "D20TestRequestedEvent", "payload": {"attribute": "Dexterity", "test_kind": "check", "dc": 10, "reason": "Pick Locked Box's lock without training"}},
            {"type": "ContainerOpenedEvent", "payload": {"container_name": "Locked Box"}}
        ])
        self.assertEqual([result.status for result in results], ["applied", "applied"])
        self.assertFalse(self.repo.list_skills())
        self.repo.set_player_equipment({"Back": "Backpack"})
        self.assertEqual(self.repo.inventory_load()["capacity_lb"], 70)
        pack = next(item for item in self.repo.list_inventory_items() if item["name"] == "Backpack")
        self.assertEqual(pack["storage_location"], "actively_carried")
        self.repo.move_inventory_item(str(pack["id"]), "Store")
        self.assertEqual(self.repo.inventory_load()["capacity_lb"], 50)
        self.assertFalse(self.repo.get_player_equipment()["Back"])

    def test_completed_objective_award_and_message_free_roll_retry(self):
        task = self.repo.upsert_active_task(name="Rescue", category="Quest", description="Rescue the traveler")
        objective_id = task["id"]
        with self.assertRaises(ValueError):
            self.repo.record_player_achievement(str(objective_id), "standard", "Rescue incomplete", source_kind="objective")
        self.assertEqual(self.repo.player_stats()["xp"], 0)
        self.assertFalse(self.repo.list_progression_records())
        self.repo.complete_active_task("Rescue")
        self.repo.record_player_achievement(str(objective_id), "standard", "Rescue completed", source_kind="objective")
        duplicate = self.repo.record_player_achievement(str(objective_id), "standard", "Retell the rescue", source_kind="objective")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(self.repo.player_stats()["xp"], 25)
        first = self.event({"request_id": "without-message"}, 15, message=None)
        retried = self.event({"request_id": "without-message"}, message=None)
        self.assertEqual(first.payload["roll"], retried.payload["roll"])

    def test_objective_completion_and_award_commit_and_roll_back_together(self):
        task = self.repo.upsert_active_task(name="Rescue", category="Quest", description="Rescue the traveler")
        events = [
            {"type": "ActiveTaskCompletedEvent", "payload": {"name": "Rescue"}},
            {"type": "PlayerAchievementRecordedEvent", "payload": {
                "source_id": task["id"], "source_kind": "objective", "significance": "standard",
                "reason": "Rescue the traveler",
            }},
        ]
        with self.assertRaises(RuntimeError):
            with self.repo.transaction():
                results = EventApplier(self.repo, message_id="rescue").apply_events(events)
                self.assertEqual([result.status for result in results], ["applied", "applied"])
                raise RuntimeError("Abort the story commit")
        self.assertEqual(self.repo.player_stats()["xp"], 0)
        self.assertFalse(self.repo.list_progression_records())
        self.assertEqual(self.repo.list_active_tasks()[0]["id"], task["id"])
        self.assertIsNone(self.repo.event_receipt("rescue", "story_commit"))
        results = EventApplier(self.repo, message_id="rescue").apply_events(events)
        self.assertEqual([result.status for result in results], ["applied", "applied"])
        self.assertEqual(self.repo.player_stats()["xp"], 25)
        self.assertFalse(self.repo.list_active_tasks())
        EventApplier(self.repo, message_id="rescue").apply_events(events)
        self.assertEqual(self.repo.player_stats()["xp"], 25)

    def test_training_source_and_message_fallback_deduplication(self):
        self.repo.upsert_skill("Sailing", "Operate sailing vessels", 1)
        training = {"type": "SkillXpAddedEvent", "payload": {"skill_name": "Sailing", "xp_amount": 2}}
        for message, expected in (("lesson", "applied"), ("lesson", "skipped"), ("next-lesson", "applied")):
            result = EventApplier(self.repo, message_id=message).apply_event(training)
            self.assertEqual(result.status, expected)
        training["payload"]["source_id"] = "sailing-instruction"
        for message, expected in (("instruction", "applied"), ("retelling", "skipped")):
            result = EventApplier(self.repo, message_id=message).apply_event(training)
            self.assertEqual(result.status, expected)
        self.assertEqual(self.repo.get_skill("Sailing")["xp"], 6)

    def test_skill_use_awards_xp_on_success_and_failure_and_reuses_retries(self):
        self.repo.upsert_skill("Stealth", "Move unseen", 1)
        first = self.event({"skill_name": "Stealth", "request_id": "first", "dc": 15}, 20, message="success")
        second = self.event({"skill_name": "Stealth", "request_id": "second", "dc": 15}, 1, message="failure")
        self.assertEqual((first.payload["outcome"], second.payload["outcome"]), ("success", "failure"))
        self.assertEqual(self.repo.get_skill("Stealth")["xp"], 2)
        self.event({"skill_name": "Stealth", "request_id": "first", "dc": 15}, message="retelling")
        self.assertEqual(SaveRepository(self.path).get_skill("Stealth")["xp"], 2)
        self.assertEqual(self.repo.player_stats()["xp"], 0)

    def test_skill_use_and_explicit_training_do_not_double_award_same_message(self):
        self.repo.upsert_skill("Stealth", "Move unseen", 1)
        for number in range(2):
            self.event({"skill_name": "Stealth", "request_id": str(number)}, 15, message="one-turn")
        explicit = EventApplier(self.repo, message_id="one-turn").apply_events([
            {"type": "SkillXpAddedEvent", "payload": {"skill_name": "Stealth", "xp_amount": 3, "source_id": "model-award"}}
        ])[0]
        self.assertEqual(explicit.status, "skipped")
        self.assertEqual(self.repo.get_skill("Stealth")["xp"], 1)
        self.event({"skill_name": "Stealth"}, 15, message="next-turn")
        self.assertEqual(self.repo.get_skill("Stealth")["xp"], 2)

    def test_failed_untrained_practice_can_learn_and_train_but_cannot_award_objective(self):
        failed = self.event({"attribute": "Wisdom", "dc": 20}, 1, message="lesson")
        results = EventApplier(self.repo, message_id="lesson").apply_events([
            {"type": "SkillUpsertedEvent", "payload": {"name": "Tracking", "level": 1,
                "description": "Follow animal tracks", "reason": "Practice interpreting tracks and learn from mistakes"}},
            {"type": "SkillXpAddedEvent", "payload": {"skill_name": "Tracking", "xp_amount": 1}},
            {"type": "PlayerAchievementRecordedEvent", "payload": {"source_id": "catch-prey",
                "source_kind": "milestone", "significance": "standard", "reason": "Catch the prey"}},
        ], prior_results=[failed])
        self.assertEqual([r.status for r in results], ["applied", "applied", "skipped"])
        self.assertEqual((self.repo.get_skill("Tracking")["level"], self.repo.get_skill("Tracking")["xp"]), (1, 1))
        self.assertEqual(self.repo.player_stats()["xp"], 0)

    def test_skill_use_levels_up_after_roll_and_stops_at_cap(self):
        self.repo.upsert_skill("Stealth", "Move unseen", 1)
        self.repo.add_skill_xp("Stealth", 7)
        result = self.event({"skill_name": "Stealth"}, 10, message="level-up")
        self.assertEqual(result.payload["skill_bonus"], 1)
        self.assertEqual((self.repo.get_skill("Stealth")["level"], self.repo.get_skill("Stealth")["xp"]), (2, 8))
        self.repo.upsert_skill("Master", "A mastered scope", 5)
        self.event({"skill_name": "Master"}, 10, message="master")
        self.assertEqual(self.repo.get_skill("Master")["xp"], 32)

    def test_skill_use_roll_and_training_rollback_together(self):
        self.repo.upsert_skill("Stealth", "Move unseen", 1)
        with patch.object(self.repo, "add_skill_xp", side_effect=RuntimeError("Training write failed")):
            with self.assertRaisesRegex(RuntimeError, "Training write failed"):
                self.event({"skill_name": "Stealth"}, 15)
        self.assertEqual(self.repo.list_d20_tests(), [])
        self.assertEqual(self.repo.get_skill("Stealth")["xp"], 0)

    def test_training_contract_survives_prompt_projection_and_schema_routing(self):
        from ai_adventure.ai.gemini_service import build_story_response_schema, build_gemini_story_prompt
        from ai_adventure.context.context_builder import AiContextBuilder
        from ai_adventure.context.reference_loader import ContextReferenceLoader
        from ai_adventure.core.models import AdventureState
        from ai_adventure.skills.rules import SKILL_TRAINING_RULE, SKILL_TRAINING_SOURCE_RULE
        packet = AiContextBuilder(ContextReferenceLoader().load_default_library()).build_story_context(
            AdventureState(), player_command="I learn Tracking through instruction and practice.")
        self.assertEqual(build_gemini_story_prompt(packet).count(SKILL_TRAINING_RULE), 1)
        packet["selection"]["tags"] = ["story"]
        schema = build_story_response_schema(packet)
        events = {b["properties"]["type"]["enum"][0] for b in schema["properties"]["events"]["items"]["anyOf"]}
        self.assertTrue({"SkillUpsertedEvent", "SkillXpAddedEvent"} <= events)
        defaults = json.loads((Path(__file__).parents[1]/"ai_adventure/data/context/default_rules.json").read_text(encoding="utf-8"))
        xp = next(s["content"] for s in defaults["sections"] if s["content"].get("event_type") == "SkillXpAddedEvent")
        self.assertIn(SKILL_TRAINING_RULE, xp["rules"])
        self.assertIn(SKILL_TRAINING_SOURCE_RULE, xp["rules"])

    def test_purchased_master_level_preserves_already_earned_partial_training(self):
        self.repo.set_setting("player.skill_advances", 1)
        self.repo.upsert_skill("Sailing", "Operate sailing vessels", 4)
        self.repo.add_skill_xp("Sailing", 7)
        self.repo.spend_skill_advance("Sailing")
        self.assertEqual((self.repo.get_skill("Sailing")["level"], self.repo.get_skill("Sailing")["xp"]), (5, 39))
        self.assertIsNone(self.repo.add_skill_xp("Sailing", 1))
        self.assertEqual(self.repo.get_skill("Sailing")["xp"], 39)

    def test_narrative_healing_requires_real_accessible_supplies(self):
        from types import SimpleNamespace
        from ai_adventure.application.story_turn_service import StoryTurnService
        from ai_adventure.container_flow import ContainerFlowError
        self.repo.change_player_health(-10, "A wound", "wound")
        self.repo.upsert_travel_location({"name": "Store", "location_scope": "specific"})
        self.repo.set_state_value("location", "Store")
        self.repo.replace_inventory_items([{"name": "Potion", "category": "Item", "quantity": 1, "description": "A healing potion", "storage_location": "Store"}])
        for name, amount, location in (("Ghost Potion", 1, "Store"), ("Potion", 2, "Store"), ("Potion", 1, "Road")):
            self.repo.set_state_value("location", location)
            result = SimpleNamespace(narrative_text="You drink a potion and recover.", speaker_cues=[], sound_effect_cues=[], suggested_events=[
                {"type": "PlayerHealthChangedEvent", "payload": {"delta": 5, "reason": "A healing potion", "source_id": "heal"}},
                {"type": "InventoryItemRemovedEvent", "payload": {"item_name": name, "amount": amount}}
            ])
            with self.assertRaises(ContainerFlowError):
                StoryTurnService.commit_response(self.repo, result, message_id="healing")
            self.assertEqual(self.repo.player_stats()["health_current"], 10)
            self.assertEqual(len(self.repo.list_inventory_items()), 1)
            self.assertIsNone(self.repo.event_receipt("healing", "story_commit"))
        self.repo.upsert_travel_location({"name": "Store", "location_scope": "specific"})
        self.repo.set_state_value("location", "Store")
        StoryTurnService.commit_response(self.repo, result, message_id="healing")
        self.assertEqual(self.repo.player_stats()["health_current"], 15)
        self.assertFalse(self.repo.list_inventory_items())


if __name__ == "__main__":
    unittest.main()
