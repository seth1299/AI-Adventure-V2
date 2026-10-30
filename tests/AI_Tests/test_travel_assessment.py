from html import unescape
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_adventure.ai.gemini_service import (
    GeminiNarrationService, GeminiSettings, STORY_RESPONSE_JSON_SCHEMA,
    NEW_GAME_RESPONSE_JSON_SCHEMA, build_gemini_story_prompt,
    _d20_test_planning_packet, _player_command_is_routine_no_check,
)
from ai_adventure.ai.request_metrics import message_type_for_operation
from ai_adventure.ai.travel_assessment import assessment_prompt, assessment_response_schema
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.core.models import AdventureState, TravelState
from ai_adventure.locations import KnownLocation, TRAVEL_SAFETY_RULE
from ai_adventure.new_game_setup import build_new_game_setup_packet


class TravelAssessmentContractTests(unittest.TestCase):
    def packet(self):
        return {
            'origin': 'Home', 'locations': [
                {'location_id': 'home', 'name': 'Home'},
                {'location_id': 'town', 'name': 'Town'},
            ], 'state': {},
        }

    def test_private_request_uses_existing_interactions_transport_with_store_false(self):
        result = {'routes': [{'location_id': 'town', 'safe': True, 'weather': 'Rain'}]}
        client = Mock()
        client.interactions.create.return_value = SimpleNamespace(
            output_text=json.dumps(result), status='completed', usage=None,
        )
        with (patch('ai_adventure.ai.gemini_service.create_gemini_client', return_value=client),
              patch('ai_adventure.ai.request_metrics._append_benchmark_record')):
            service = GeminiNarrationService(GeminiSettings(api_key='test-key', model='gemini-3.8-flash'))
            actual = service.assess_travel_routes(self.packet())
        self.assertEqual(actual, result)
        call = client.interactions.create.call_args.kwargs
        self.assertFalse(call['store'])
        self.assertEqual(call['response_format'][0]['schema'], assessment_response_schema(self.packet()))
        self.assertEqual(message_type_for_operation('assess_travel_routes'), 'Travel Assessments')
        self.assertIn('no narration', call['input'])

    def test_schema_requires_actual_boolean_safety_and_one_record_per_destination(self):
        schema = assessment_response_schema(self.packet())['properties']['routes']
        self.assertEqual((schema['minItems'], schema['maxItems']), (1, 1))
        self.assertEqual(schema['items']['properties']['location_id']['enum'], ['town'])
        self.assertEqual(schema['items']['properties']['safe']['type'], 'boolean')
        self.assertIn(TRAVEL_SAFETY_RULE, assessment_prompt(self.packet()))

    def test_story_context_serialization_and_prompt_preserve_weather_and_private_rule(self):
        state = AdventureState(travel=TravelState(locations=[KnownLocation(name='Town', weather='Rain')]))
        self.assertEqual(state.to_dict()['travel']['locations'][0]['weather'], 'Rain')
        packet = AiContextBuilder.from_default_library().build_story_context(state, player_command='Travel to Town.')
        self.assertEqual(packet['state']['travel']['locations'][0]['weather'], 'Rain')
        self.assertEqual(packet['response_contract']['travel_safety'], TRAVEL_SAFETY_RULE)
        prompt = unescape(build_gemini_story_prompt(packet))
        self.assertEqual(prompt.count(TRAVEL_SAFETY_RULE), 1)

    def test_location_schemas_and_new_game_guidance_support_weather(self):
        branches = STORY_RESPONSE_JSON_SCHEMA['properties']['events']['items']['anyOf']
        location = next(branch for branch in branches
                        if branch['properties']['type']['enum'] == ['LocationUpsertedEvent'])
        self.assertIn('weather', location['properties']['payload']['properties'])
        self.assertIn('weather', NEW_GAME_RESPONSE_JSON_SCHEMA['properties']['locations']['items']['properties'])
        packet = build_new_game_setup_packet({})
        self.assertIn('current weather', packet['requirements']['travel_locations'])
        defaults = json.loads(Path('ai_adventure/data/context/default_rules.json').read_text(encoding='utf-8'))
        self.assertIn('assessed-safe Travel-tab trips locally', json.dumps(defaults))
        context = json.loads(Path('ai_adventure/data/context/default_context.json').read_text(encoding='utf-8'))
        self.assertIn('Route safety is private and directional', json.dumps(context))

    def test_unsafe_travel_logistics_reach_planner_without_routine_verb_suppression(self):
        request = {
            'active': True, 'origin': {'name': 'Home'}, 'destination': {'name': 'Town'},
            'estimate': {'estimated_minutes': 60}, 'route_safety': {'safe': False, 'weather': 'Rain'},
        }
        packet = {
            'packet_type': 'story_turn', 'player_command': 'Travel to Town.',
            'travel_request': request, 'state': {},
        }
        self.assertEqual(_d20_test_planning_packet(packet)['travel_request'], request)
        self.assertFalse(_player_command_is_routine_no_check(packet))

    def test_destination_music_is_required_and_constrained_to_catalog(self):
        packet = self.packet()
        packet['valid_music_tracks'] = ['Fantasy/Town.mp3', 'Fantasy/Forest.mp3']
        route = assessment_response_schema(packet)['properties']['routes']['items']
        self.assertIn('music_filename', route['required'])
        self.assertEqual(route['properties']['music_filename']['enum'], packet['valid_music_tracks'])
        self.assertIn('do not change the music playing at the origin', assessment_prompt(packet))
