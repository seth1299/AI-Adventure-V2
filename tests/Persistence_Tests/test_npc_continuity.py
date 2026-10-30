from pathlib import Path
import tempfile
import unittest

from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository


class NpcContinuityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = SaveRepository.create_new_save(Path(temporary.name), 'NPC continuity')
        self.repo.upsert_npc(npc_id='npc_archivist_vanja', name='Vanja', display_name='Archivist Vanja',
                             role='Chief Archivist', location="The Scribe's Hall",
                             public_description='A meticulous and courteous archivist who maintains the municipal records and texts of Fairrest.',
                             age='Middle-aged', gender_identity='Female', species='Human',
                             known_facts=['Manages the municipal archives.'])

    def payload(self):
        return {'npc_id': 'npc_archivist_vanja', 'name': 'Archivist Vanja',
                'display_name': 'Archivist Vanja', 'role': 'Head Archivist', 'age': 'Mid 40s',
                'location': "The Scribe's Hall", 'gender_identity': 'Female', 'species': 'Human',
                'public_description': 'An attentive, bespectacled archivist with an obliging smile, dressed in neat scholarly robes.',
                'player_facing_information': 'Offers two-fortnight book loans.',
                'known_facts': ['Discussed loans with Kit.']}

    def test_recorded_vanja_rewrite_preserves_canon_and_audits_proposal(self):
        before = self.repo.get_npc('npc_archivist_vanja')
        payload = self.payload()
        result = EventApplier(self.repo, message_id='repeat-profile').apply_events([
            {'type': 'NpcUpsertedEvent', 'payload': payload},
        ])[0]
        after = self.repo.get_npc('npc_archivist_vanja')
        self.assertEqual(result.status, 'applied')
        for field in ('name', 'display_name', 'role', 'age', 'gender_identity', 'species', 'public_description'):
            self.assertEqual(after[field], before[field])
        self.assertIn('Discussed loans with Kit.', after['known_facts'])
        self.assertIn('Manages the municipal archives.', after['known_facts'])
        self.assertIn('two-fortnight', after['player_facing_information'])
        event = self.repo.list_mechanical_events(limit=1)[0]
        self.assertIn('public_description', event['payload']['preserved_profile_fields'])
        self.assertEqual(event['payload']['public_description'], payload['public_description'])
        self.assertEqual(event['payload']['effective_profile']['public_description'], before['public_description'])
        self.assertEqual(len(self.repo.list_npcs()), 1)

    def test_explicit_correction_can_update_profile_but_internal_identity_stays_stable(self):
        payload = self.payload()
        payload['profile_change_reason'] = 'Vanja states her precise age and corrects her job title.'
        EventApplier(self.repo).apply_events([{'type': 'NpcUpsertedEvent', 'payload': payload}])
        after = self.repo.get_npc('npc_archivist_vanja')
        self.assertEqual(after['name'], 'Vanja')
        self.assertEqual(after['role'], 'Head Archivist')
        self.assertEqual(after['age'], 'Mid 40s')
        self.assertEqual(after['public_description'], payload['public_description'])

    def test_newly_revealed_name_requires_a_reason_and_preserves_id(self):
        payload = self.payload()
        payload['display_name'] = 'Vanja Reed'
        payload['profile_change_reason'] = 'She introduces herself by her full name, Vanja Reed.'
        EventApplier(self.repo).apply_events([{'type': 'NpcUpsertedEvent', 'payload': payload}])
        self.assertEqual(self.repo.get_npc('npc_archivist_vanja')['display_name'], 'Vanja Reed')
        self.assertEqual(len(self.repo.list_npcs()), 1)

    def test_new_npc_and_blank_fields_can_be_filled_normally(self):
        payload = {'npc_id': 'npc_new', 'name': 'Bren', 'location': 'Harbor'}
        applier = EventApplier(self.repo)
        applier.apply_events([{'type': 'NpcUpsertedEvent', 'payload': payload}])
        payload.update(age='Young adult', species='Human', public_description='A sailor in a blue coat.')
        applier.apply_events([{'type': 'NpcUpsertedEvent', 'payload': payload}])
        npc = self.repo.get_npc('npc_new')
        self.assertEqual(npc['age'], 'Young adult')
        self.assertEqual(npc['public_description'], 'A sailor in a blue coat.')

    def test_redundant_new_id_with_same_visible_identity_reuses_protected_profile(self):
        payload = self.payload()
        payload['npc_id'] = 'npc_another_vanja'
        EventApplier(self.repo).apply_events([{'type': 'NpcUpsertedEvent', 'payload': payload}])
        self.assertEqual(len(self.repo.list_npcs()), 1)
        self.assertEqual(self.repo.get_npc('npc_archivist_vanja')['role'], 'Chief Archivist')
