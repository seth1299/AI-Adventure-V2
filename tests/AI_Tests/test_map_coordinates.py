"""The regional map origin must not overwrite the player's starting position."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from ai_adventure.ai.gemini_service import _parse_new_game_locations, NEW_GAME_RESPONSE_JSON_SCHEMA
from ai_adventure.application.new_game_service import _travel_locations_for_save
from ai_adventure.locations import MAP_COORDINATE_RULE
from ai_adventure.new_game_setup import build_new_game_setup_packet, normalize_new_game_setup


class MapCoordinateTests(unittest.TestCase):
    def test_parser_preserves_distinct_region_and_start_positions(self):
        locations = _parse_new_game_locations([
            {"name": "Main Region", "x_miles": 0, "y_miles": 0},
            {"name": "Shop", "x_miles": 2.5, "y_miles": 1.5},
        ], "Shop")
        self.assertEqual((locations[0]["x_miles"], locations[0]["y_miles"]), (0, 0))
        self.assertEqual((locations[1]["x_miles"], locations[1]["y_miles"]), (2.5, 1.5))

    def test_missing_start_is_not_given_a_fabricated_origin(self):
        locations = _parse_new_game_locations([
            {"name": "Main Region", "x_miles": 0, "y_miles": 0},
        ], "Shop")
        self.assertIsNone(locations[1]["x_miles"])
        self.assertIsNone(locations[1]["y_miles"])

    def test_exact_start_rename_keeps_global_position_and_region_name(self):
        locations = _travel_locations_for_save([
            {"name": "Main Region", "x_miles": 0, "y_miles": 0},
            {"name": "Generated Shop", "x_miles": 2.5, "y_miles": 1.5},
        ], {"start_location": "My Shop", "start_location_mode": "exact"},
            SimpleNamespace(start_location="Generated Shop"))
        self.assertEqual(locations[0]["name"], "Main Region")
        self.assertEqual(locations[1]["name"], "My Shop")
        self.assertEqual((locations[1]["x_miles"], locations[1]["y_miles"]), (2.5, 1.5))

    def test_missing_ai_start_does_not_rename_region_origin(self):
        locations = _travel_locations_for_save([
            {"name": "Main Region", "x_miles": 0, "y_miles": 0},
        ], {"start_location": "My Shop", "start_location_mode": "exact"},
            SimpleNamespace(start_location=""))
        self.assertEqual(locations[0]["name"], "My Shop")
        self.assertIsNone(locations[0]["x_miles"])
        self.assertEqual(locations[1]["name"], "Main Region")

    def test_setup_schema_and_packaged_rule_share_coordinate_contract(self):
        packet = build_new_game_setup_packet(normalize_new_game_setup({"title": "Coordinates"}))
        self.assertIn(MAP_COORDINATE_RULE, packet["requirements"]["travel_locations"])
        schema = NEW_GAME_RESPONSE_JSON_SCHEMA["properties"]["locations"]["items"]
        self.assertEqual(schema["properties"]["x_miles"]["description"], MAP_COORDINATE_RULE)
        rules = json.loads((Path(__file__).resolve().parents[2] / "ai_adventure/data/context/default_rules.json").read_text(encoding="utf-8"))
        self.assertIn(MAP_COORDINATE_RULE, json.dumps(rules))

    def test_runtime_context_carries_the_same_region_origin_rule(self):
        from ai_adventure.context.context_builder import AiContextBuilder
        from ai_adventure.context.reference_loader import ContextReferenceLoader
        from ai_adventure.core.models import AdventureState
        packet = AiContextBuilder(ContextReferenceLoader().load_default_library()).build_story_context(
            AdventureState(), player_command="Travel to the nearby city.",
        )
        self.assertIn(MAP_COORDINATE_RULE, packet["state"]["travel"]["rules"])
