"""Survival setup, durable state, and Gemini contract integration."""

import json
from pathlib import Path
import tempfile
import unittest

from ai_adventure.ai import gemini_service as gemini
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.core.models import AdventureState
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.new_game_setup import build_new_game_setup_packet, normalize_new_game_setup
from ai_adventure.new_game_templates import load_new_game_template, save_new_game_template
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.survival import SURVIVAL_METERS, SURVIVAL_RULE


class SurvivalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = SaveRepository.create_new_save(
            self.root, "Survival", {"survival": {"enabled": True}},
        )

    def status(self, **values):
        return {"type": "StatusUpdatedEvent", "payload": {
            "location": "AUTO", "weather": "AUTO", "minutes_passed": 0, **values,
        }}

    def meters(self, repo=None):
        player = StateManager(repo or self.repo).load_state().player
        return tuple(getattr(player, meter) for meter in SURVIVAL_METERS)

    def test_setup_defaults_and_explicit_overrides_survive_templates(self):
        for genre, explicit, expected in (
            ("", None, False), ("Fantasy", None, False),
            ("Horror, Survival", None, True), ("survival", False, False),
            ("Fantasy", True, True),
        ):
            with self.subTest(genre=genre, explicit=explicit):
                raw = {"specified_genre": genre}
                if explicit is not None:
                    raw["survival"] = {"enabled": explicit}
                setup = normalize_new_game_setup(raw)
                self.assertEqual(setup["survival"], {"enabled": expected})
                path = self.root / "templates.json"
                save_new_game_template(path, setup, template_name="test")
                self.assertEqual(load_new_game_template(path)["survival"], setup["survival"])
                packet = build_new_game_setup_packet(setup)
                self.assertEqual("survival_contract" in packet, expected)
                if expected:
                    self.assertIn(SURVIVAL_RULE, gemini.build_gemini_new_game_prompt(packet))

    def test_values_initialize_change_and_reload_with_optional_omission(self):
        self.assertEqual(self.meters(), (100, 100, 100))
        applier = EventApplier(self.repo)
        applier.apply_events([self.status(hunger=45, thirst=30, energy=20)])
        self.assertEqual(self.meters(), (45, 30, 20))
        # Eating/drinking/restoring are positive absolute updates, not deltas.
        applier.apply_events([self.status(hunger=80, thirst=90, energy=70)])
        applier.apply_events([self.status(hunger=85)])
        reopened = SaveRepository(self.repo.db_path)
        self.assertEqual(self.meters(reopened), (85, 90, 70))
        self.assertEqual(self.repo.list_mechanical_events()[-1]["payload"]["hunger"], 85)

    def test_disabled_and_legacy_saves_ignore_survival_fields(self):
        for setup in ({}, {"survival": {"enabled": False}, "specified_genre": "Survival"}):
            repo = SaveRepository.create_new_save(self.root, str(len(list(self.root.iterdir()))), setup)
            EventApplier(repo).apply_events([self.status(hunger=0, thirst=0, energy=0, weather="Rain")])
            self.assertEqual(self.meters(repo), (None, None, None))
            self.assertNotIn("player.hunger", repo.get_state_snapshot())
            self.assertEqual(repo.get_state_value("weather"), "Rain")
        # Legacy genre selection alone does not opt an existing save in.
        repo.set_setting("world.genre", "Survival")
        self.assertEqual(self.meters(repo), (None, None, None))

    def test_bounds_invalid_types_and_retry_receipts(self):
        event = self.status(hunger=10, thirst=125, energy=5)
        EventApplier(self.repo, message_id="turn-1").apply_events([event])
        self.assertEqual(self.meters(), (10, 100, 5))
        EventApplier(self.repo, message_id="turn-2").apply_events([self.status(hunger=50)])
        EventApplier(self.repo, message_id="turn-1").apply_events([event])
        self.assertEqual(self.meters(), (50, 100, 5))
        for value in (True, 12.5, "12", None):
            with self.subTest(value=value), self.assertLogs("ai_adventure.events.event_applier", level="WARNING"):
                result = EventApplier(self.repo).apply_event(self.status(hunger=value, weather="Snow"))
                self.assertEqual(result.status, "skipped")
                self.assertEqual(self.meters(), (50, 100, 5))
                self.assertEqual(self.repo.get_state_value("weather"), "Clear")

    def test_status_updates_roll_back_with_the_rest_of_the_turn(self):
        with self.assertRaises(RuntimeError):
            with self.repo.transaction():
                EventApplier(self.repo).apply_events([self.status(hunger=5, energy=10)])
                raise RuntimeError("abort turn")
        self.assertEqual(self.meters(), (100, 100, 100))

    def test_out_of_game_commit_cannot_change_meters(self):
        result = gemini.AiNarrationResult(
            narrative_text="These are your survival meters.",
            raw_text="", suggested_events=[self.status(hunger=0, thirst=0, energy=0)],
        )
        StoryTurnService.commit_response(
            self.repo, result, message_id="out-of-game", conversation_mode="out_of_game",
        )
        self.assertEqual(self.meters(), (100, 100, 100))

    def test_context_prompt_schema_and_parser_keep_optional_values(self):
        EventApplier(self.repo).apply_events([self.status(hunger=63, thirst=42, energy=21)])
        packet = StoryTurnService.build_context_packet(self.repo, "Look around")
        # No relevance tag is needed: all live turns consider these needs.
        packet["selection"]["tags"] = []
        self.assertEqual(gemini._story_prompt_packet(packet)["state"]["survival"], {
            "enabled": True, "hunger": 63, "thirst": 42, "energy": 21,
            "roll_modifier": -3, "fate": "alive",
        })
        prompt = gemini.build_gemini_story_prompt(packet)
        self.assertEqual(prompt.count(SURVIVAL_RULE), 1)
        response = {"response": "You drink the water.", "suggested_actions": [],
                    "out_of_game": False, "events": [self.status(thirst=75)]}
        for for_api in (False, True):
            self.assertEqual(gemini._json_schema_shape_errors(
                response, gemini.build_story_response_schema(packet, for_api=for_api)), [])
            unchanged = {**response, "events": [self.status()]}
            self.assertEqual(gemini._json_schema_shape_errors(
                unchanged, gemini.build_story_response_schema(packet, for_api=for_api)), [])
        parsed = gemini.parse_gemini_story_response(json.dumps(response), context_packet=packet)
        self.assertEqual(parsed.suggested_events[-1]["payload"]["thirst"], 75)
        EventApplier(self.repo).apply_events(list(parsed.suggested_events))
        self.assertEqual(self.meters(), (63, 75, 21))

    def test_disabled_prompts_and_schemas_omit_survival_updates_without_mutating_base(self):
        state = AdventureState()
        packet = AiContextBuilder.from_default_library().build_story_context(state, player_command="Look around")
        self.assertNotIn(SURVIVAL_RULE, gemini.build_gemini_story_prompt(packet))
        schema = gemini.build_story_response_schema(packet, for_api=False)
        branches = schema["properties"]["events"]["items"]["anyOf"]
        status = next(b for b in branches if b["properties"]["type"]["enum"] == ["StatusUpdatedEvent"])
        self.assertNotIn("hunger", status["properties"]["payload"]["properties"])
        base = gemini.EVENT_RESPONSE_SCHEMA["anyOf"][0]
        self.assertIn("hunger", base["properties"]["payload"]["properties"])

    def test_packaged_defaults_describe_all_three_optional_meters(self):
        root = Path(__file__).resolve().parents[2] / "ai_adventure/data/context"
        for name in ("default_rules.json", "default_context.json"):
            data = json.loads((root / name).read_text(encoding="utf-8"))
            rules = json.dumps(data)
            for phrase in ("state.survival.enabled", "absolute integer percentages", "Better food/drink", "Energy"):
                self.assertIn(phrase, rules)
