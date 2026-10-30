from html import unescape
import json
from pathlib import Path
import unittest

from ai_adventure.ai.gemini_service import STORY_RESPONSE_JSON_SCHEMA, build_gemini_story_prompt
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.core.models import AdventureState
from ai_adventure.npc_continuity import NPC_PROFILE_CONTINUITY_RULE


class NpcContinuityContractTests(unittest.TestCase):
    def test_repository_id_is_exported_as_canonical_npc_id(self):
        packet = AiContextBuilder.from_default_library().build_story_context(
            AdventureState(), player_command='"Yes, please."',
            relevant_npcs=[{'id': 'npc_vanja', 'name': 'Vanja', 'display_name': 'Archivist Vanja',
                            'public_description': 'An archivist.', 'private_extra': 'Do not export.'}],
        )
        npc = packet['state']['npcs']['relevant'][0]
        self.assertEqual(npc['npc_id'], 'npc_vanja')
        self.assertNotIn('private_extra', npc)
        prompt = unescape(build_gemini_story_prompt(packet))
        self.assertIn('npc_vanja', prompt)
        self.assertEqual(prompt.count(NPC_PROFILE_CONTINUITY_RULE), 1)

    def test_continuity_rule_survives_minimal_context_selection(self):
        packet = {'state': {}, 'selection': {'tags': ['scene']}, 'response_contract': {}}
        self.assertIn(NPC_PROFILE_CONTINUITY_RULE, unescape(build_gemini_story_prompt(packet)))

    def test_npc_event_supports_explicit_change_reason_without_requiring_it_for_introductions(self):
        branches = STORY_RESPONSE_JSON_SCHEMA['properties']['events']['items']['anyOf']
        event = next(branch for branch in branches if branch['properties']['type']['enum'] == ['NpcUpsertedEvent'])
        payload = event['properties']['payload']
        self.assertEqual(payload['properties']['profile_change_reason']['type'], 'string')
        self.assertNotIn('profile_change_reason', payload['required'])

    def test_packaged_defaults_explain_preserved_profiles_and_player_controlled_portraits(self):
        for name in ('default_context.json', 'default_rules.json'):
            content = json.loads((Path('ai_adventure/data/context') / name).read_text(encoding='utf-8'))
            self.assertIn('profile_change_reason', json.dumps(content))
            self.assertIn('portrait', json.dumps(content))
