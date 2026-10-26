"""Survival roll bands, mandatory death decisions, and atomic recovery."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ai_adventure.ai import gemini_service as gemini
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.application.survival_service import build_recovery_context
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.persistence.survival import AdventureNotPlayableError
from ai_adventure.survival import SURVIVAL_METERS, SURVIVAL_RECOVERY_RULE, SURVIVAL_ROLL_RULE, survival_roll_modifier


class FixedDice:
    def randint(self, minimum, maximum, /):
        return 10


class SurvivalDeathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SaveRepository.create_new_save(Path(self.temp.name), "Survival death", {"survival": {"enabled": True}})

    def status(self, **values):
        return {"type": "StatusUpdatedEvent", "payload": {
            "location": "AUTO", "weather": "AUTO", "minutes_passed": 0, **values}}

    def die(self, meter="hunger"):
        EventApplier(self.repo).apply_events([self.status(**{meter: 0})])
        return self.repo.get_setting("survival.death")

    def recovery(self):
        return {"response": "You black out and wake in a roadside shelter, alive but weak.",
                "location": "Roadside Shelter", "location_description": "A rough wooden shelter.", "weather": "Rain"}

    def test_all_band_edges_and_weakest_meter_without_stacking(self):
        for value, expected in ((1,-3),(29,-3),(30,-1),(49,-1),(50,0),(79,0),(80,1),(100,1)):
            for meter in SURVIVAL_METERS:
                with self.subTest(value=value, meter=meter):
                    values = {**dict.fromkeys(SURVIVAL_METERS, 100), meter:value}
                    self.assertEqual(survival_roll_modifier(values, enabled=True), expected)
                    self.assertEqual(survival_roll_modifier(values, enabled=False), 0)

    def test_check_attack_and_save_record_and_reuse_authoritative_modifier(self):
        for kind in ("check", "attack", "save"):
            for value, expected in ((100,1),(60,0),(40,-1),(20,-3)):
                EventApplier(self.repo).apply_events([self.status(energy=value)])
                event = {"type":"D20TestRequestedEvent", "payload":{
                    "attribute":"Wisdom", "test_kind":kind, "reason":"Avoid a falling branch", "dc":12,
                    "request_id":f"{kind}-{value}"}}
                result = EventApplier(self.repo, rng=FixedDice()).apply_event(event)
                self.assertEqual(result.status, "applied")
                self.assertEqual(result.payload["survival_modifier"], expected)
                self.assertEqual(result.payload["total"], 10 + self.repo.player_stats()["modifiers"]["Wisdom"] + expected)
                EventApplier(self.repo).apply_events([self.status(energy=75)])
                repeat = EventApplier(self.repo, rng=FixedDice()).apply_event(event)
                self.assertEqual(repeat.payload["total"], result.payload["total"])
                self.assertEqual(repeat.payload["survival_values"]["energy"], value)
                reopened = SaveRepository(self.repo.db_path)
                self.assertEqual(reopened.find_d20_test(event["payload"]["request_id"])["survival_modifier"], expected)

    def test_any_zero_or_negative_clamp_creates_persistent_pending_death(self):
        for meter in SURVIVAL_METERS:
            repo = SaveRepository.create_new_save(Path(self.temp.name), meter, {"survival":{"enabled":True}})
            EventApplier(repo).apply_events([self.status(**{meter:-10})])
            reopened = SaveRepository(repo.db_path)
            self.assertEqual(reopened.survival_status()["fate"], "pending")
            self.assertEqual(reopened.get_setting("survival.death")["meters"][meter], 0)
            self.assertEqual(StateManager(reopened).load_state().player.condition, "Dead")
            with self.assertRaises(AdventureNotPlayableError):
                StoryTurnService.record_player_action(reopened,"Continue",message_id="blocked")
            with self.assertRaises(AdventureNotPlayableError):
                EventApplier(reopened).apply_events([self.status(**{meter:100})])

    def test_health_incapacitation_and_survival_death_have_distinct_conditions(self):
        self.repo.set_setting("player.health_current",0)
        self.assertEqual(self.repo.survival_status()["fate"],"alive")
        self.assertEqual(StateManager(self.repo).load_state().player.condition,"Incapacitated")
        self.die("energy")
        self.assertEqual(StateManager(self.repo).load_state().player.condition,"Dead")

    def test_accepting_death_freezes_gameplay_but_allows_media_and_browsing(self):
        self.repo.upsert_skill("Survival", "Endure outdoors", 2)
        self.repo.set_note_entries([{"entry_id":"note", "heading":"Before", "body":"Keep me"}])
        self.repo.capture_message_snapshot("before-death")
        self.die()
        self.repo.choose_survival_fate("accepted")
        repo = SaveRepository(self.repo.db_path)
        before = repo.get_state_snapshot()
        mutations = [lambda:repo.set_state_value("location","Elsewhere"),
            lambda:repo.set_note_entries([]), lambda:repo.add_skill_xp("Survival",1),
            lambda:repo.append_history("player","I continue"),
            lambda:repo.set_setting("survival.fate","alive"),
            lambda:repo.set_travel_locations([]),
            lambda:repo.replace_inventory_items([],allow_empty=True),
            lambda:repo.rollback_message("before-death")]
        for mutation in mutations:
            with self.assertRaises((sqlite3.IntegrityError, AdventureNotPlayableError)):
                mutation()
        self.assertEqual(repo.get_state_snapshot(), before)
        self.assertEqual(repo.get_note_entries()[0]["body"], "Keep me")
        self.assertTrue(repo.db_path.is_file())
        repo.set_setting("audio.narrator_enabled",True)
        repo.set_setting("images.enabled",True)
        repo.set_setting("ui.tab_layout", {"version":1})
        # Same tables used by separately billed image generation remain writable.
        with repo.transaction() as connection:
            connection.execute("UPDATE visual_assets SET error_message='retry' WHERE 1=0")
        StateManager(repo).load_state()
        with self.assertRaises(AdventureNotPlayableError):
            repo.choose_survival_fate("recovering")

    def test_recovery_halves_only_within_level_progress_once(self):
        cases = [(1,4,2),(2,13,10),(3,16,16),(4,31,27),(5,32,32)]
        for level,xp,expected in cases:
            self.repo.upsert_skill(f"Skill {level}","Training scope",level)
            with self.repo.transaction() as connection:
                connection.execute("UPDATE skills SET xp=? WHERE name=?",(xp,f"Skill {level}"))
        inventory = self.repo.list_inventory_items()
        money = self.repo.get_state_value("currency.balance")
        self.repo.capture_message_snapshot("pre-death")
        death = self.die("thirst")
        self.repo.choose_survival_fate("recovering")
        for level,xp,expected in cases:
            self.assertEqual(self.repo.get_skill(f"Skill {level}")["xp"],xp)
        self.assertTrue(self.repo.commit_survival_recovery(death["incident_id"],self.recovery()))
        self.assertFalse(self.repo.commit_survival_recovery(death["incident_id"],self.recovery()))
        for level,xp,expected in cases:
            skill = self.repo.get_skill(f"Skill {level}")
            self.assertEqual((skill["level"],skill["xp"]),(level,expected))
        self.assertEqual(self.repo.list_inventory_items(),inventory)
        self.assertEqual(self.repo.get_state_value("currency.balance"),money)
        self.assertEqual(self.repo.survival_status()["fate"],"alive")
        self.assertEqual([self.repo.survival_status()[m] for m in SURVIVAL_METERS],[20,20,20])
        self.assertEqual(self.repo.get_state_value("location"),"Roadside Shelter")
        self.assertTrue(self.repo.has_message_snapshot("pre-death"))
        with self.assertRaisesRegex(RuntimeError, "pre-recovery snapshot"):
            self.repo.rollback_message("pre-death")
        self.assertEqual(len([h for h in self.repo.list_history() if h["kind"]=="story"]),1)

    def test_recovery_transaction_failure_rolls_back_xp_meters_and_scene(self):
        self.repo.upsert_skill("Crafting","Build and repair",1)
        self.repo.add_skill_xp("Crafting",4)
        death = self.die()
        self.repo.choose_survival_fate("recovering")
        with patch.object(self.repo,"append_history",side_effect=RuntimeError("write failure")):
            with self.assertRaises(RuntimeError):
                self.repo.commit_survival_recovery(death["incident_id"],self.recovery())
        self.assertEqual(self.repo.get_skill("Crafting")["xp"],4)
        self.assertEqual(self.repo.survival_status()["hunger"],0)
        self.assertEqual(self.repo.survival_status()["fate"],"recovering")
        self.assertNotEqual(self.repo.get_state_value("location"),"Roadside Shelter")
        self.assertIsNone(self.repo.event_receipt(death["incident_id"],"survival_recovery"))

    def test_recovery_rejects_stale_incident_and_same_location(self):
        death = self.die()
        self.repo.choose_survival_fate("recovering")
        with self.assertRaises(AdventureNotPlayableError):
            self.repo.commit_survival_recovery("stale",self.recovery())
        with self.assertRaises(ValueError):
            self.repo.commit_survival_recovery(death["incident_id"],{**self.recovery(),"location":death["location"]})
        self.assertEqual(self.repo.survival_status()["fate"],"recovering")

    def test_recovery_prompt_calls_existing_interactions_transport_and_not_normal_events(self):
        self.die()
        self.repo.choose_survival_fate("recovering")
        packet = build_recovery_context(self.repo)
        service = gemini.GeminiNarrationService(settings=gemini.GeminiSettings(api_key="test"))
        from types import SimpleNamespace
        with patch.object(gemini,"create_gemini_client"), patch.object(gemini,"_create_interaction_with_retry",return_value=SimpleNamespace(output_text=json.dumps(self.recovery()))) as request:
            result = service.generate_survival_recovery(packet)
        self.assertEqual(result,self.recovery())
        self.assertIn(SURVIVAL_RECOVERY_RULE,request.call_args.kwargs["contents"])
        self.assertNotIn("events",request.call_args.kwargs["response_contract"]["properties"])
        with patch.object(gemini,"create_gemini_client"), patch.object(gemini,"_create_interaction_with_retry",side_effect=gemini.GeminiRequestError("offline")):
            with self.assertRaises(gemini.GeminiRequestError):
                service.generate_survival_recovery(packet)
        self.assertEqual(self.repo.survival_status()["fate"],"recovering")
