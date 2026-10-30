import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_adventure.application.travel_service import (
    assessment_key, build_assessment_context, commit_assessment, complete_safe_travel, current_assessment,
    sync_location_music,
)
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository


class TravelServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = SaveRepository.create_new_save(Path(self.temporary.name), 'Travel')
        self.repository.set_state_value('location', 'Home')
        self.repository.set_state_value('weather', 'Clear')
        self.repository.set_travel_locations([
            {'name': 'Home', 'location_id': 'home', 'x_miles': 0, 'y_miles': 0},
            {'name': 'Town', 'location_id': 'town', 'x_miles': 3, 'y_miles': 0},
            {'name': 'Forest', 'location_id': 'forest', 'x_miles': 0, 'y_miles': 6},
        ])

    def assess(self):
        packet = build_assessment_context(self.repository)
        result = {'routes': [
            {'location_id': 'town', 'safe': True, 'weather': 'Rain'},
            {'location_id': 'forest', 'safe': False, 'weather': 'Fog'},
        ]}
        self.assertTrue(commit_assessment(self.repository, packet, result))
        return packet, result

    def test_safe_travel_status_time_weather_history_and_idempotency(self):
        self.assess()
        before = self.repository.get_current_calendar_minute()
        self.assertTrue(complete_safe_travel(self.repository, 'Town', message_id='journey'))
        reopened = SaveRepository(self.repository.db_path)
        self.assertEqual(reopened.get_state_value('location'), 'Town')
        self.assertEqual(reopened.get_state_value('weather'), 'Rain')
        self.assertEqual(reopened.get_current_calendar_minute(), before + 60)
        self.assertEqual(reopened.find_travel_location('Home')['weather'], 'Clear')
        self.assertEqual(reopened.find_travel_location('Town')['weather'], 'Rain')
        events = reopened.list_mechanical_events(limit=10)
        status = next(event for event in events if event['event_type'] == 'StatusUpdatedEvent')
        self.assertEqual(status['payload']['minutes_passed'], 60)
        self.assertEqual(status['status'], 'applied')
        history = reopened.list_history(kinds=('player', 'story'))
        self.assertIn('Arrived at **Town**', history[-1]['content'])
        self.assertNotIn('safe', history[-1]['content'].casefold())
        self.assertFalse(current_assessment(reopened))  # No reverse-route assumption.
        self.assertTrue(complete_safe_travel(reopened, 'Town', message_id='journey'))
        self.assertEqual(reopened.get_current_calendar_minute(), before + 60)
        self.assertEqual(reopened.list_history(kinds=('player', 'story')), history)

    def test_unsafe_unknown_and_current_destinations_do_not_mutate_game(self):
        before = self.repository.get_current_calendar_minute()
        self.assertFalse(complete_safe_travel(self.repository, 'Town'))
        self.assess()
        for name in ('Forest', 'Missing', 'Home'):
            self.assertFalse(complete_safe_travel(self.repository, name))
        self.assertEqual(self.repository.get_state_value('location'), 'Home')
        self.assertEqual(self.repository.get_current_calendar_minute(), before)

    def test_weather_and_route_changes_invalidate_cached_safety(self):
        self.assess()
        self.repository.upsert_travel_location({'name': 'Town', 'travel_notes': 'Road washed out.'})
        self.assertFalse(current_assessment(self.repository))
        self.assertFalse(complete_safe_travel(self.repository, 'Town'))
        self.assess()
        self.repository.set_state_value('weather', 'Thunderstorm')
        self.assertFalse(current_assessment(self.repository))

    def test_rejected_incomplete_duplicate_or_non_boolean_assessment_is_atomic(self):
        packet = build_assessment_context(self.repository)
        valid = {'routes': [
            {'location_id': 'town', 'safe': True, 'weather': 'Rain'},
            {'location_id': 'forest', 'safe': False, 'weather': 'Fog'},
        ]}
        bad_type = copy.deepcopy(valid)
        bad_type['routes'][0]['safe'] = 'true'
        bad_weather = copy.deepcopy(valid)
        bad_weather['routes'][0]['weather'] = 'AUTO'
        for result in (
            {'routes': valid['routes'][:1]},
            {'routes': [valid['routes'][0], valid['routes'][0]]}, bad_type, bad_weather,
        ):
            with self.subTest(result=result), self.assertRaises(ValueError):
                commit_assessment(self.repository, packet, result)
            self.assertEqual(self.repository.find_travel_location('Town')['weather'], '')
            self.assertFalse(current_assessment(self.repository))

    def test_arrival_discards_late_results_even_after_returning_to_same_origin(self):
        packet, result = self.assess()
        for name in ('Town', 'Home'):
            EventApplier(self.repository).apply_events([{'type': 'StatusUpdatedEvent', 'payload': {
                'location': name, 'minutes_passed': 0, 'weather': 'Clear',
            }}])
        self.assertNotEqual(assessment_key(self.repository), packet['assessment_key'])
        self.assertFalse(commit_assessment(self.repository, packet, result))

    def test_local_arrival_rolls_back_status_and_history_on_write_failure(self):
        self.assess()
        before = self.repository.get_current_calendar_minute()
        append = self.repository.append_history

        def fail_arrival(kind, *args, **kwargs):
            if kind == 'story':
                raise OSError('Simulated arrival write failure')
            return append(kind, *args, **kwargs)

        history = self.repository.list_history()
        with patch.object(self.repository, 'append_history', side_effect=fail_arrival):
            with self.assertRaises(OSError):
                complete_safe_travel(self.repository, 'Town', message_id='failed')
        reopened = SaveRepository(self.repository.db_path)
        self.assertEqual(reopened.get_state_value('location'), 'Home')
        self.assertEqual(reopened.get_state_value('weather'), 'Clear')
        self.assertEqual(reopened.get_current_calendar_minute(), before)
        self.assertEqual(reopened.list_history(), history)
        self.assertTrue(current_assessment(reopened))
        self.assertIsNone(reopened.event_receipt('failed', 'local_travel'))

    def test_background_packet_contains_only_travel_context(self):
        self.repository.append_history('story', 'PRIVATE_HISTORY_MARKER')
        self.repository.set_setting('ai.additional_context', 'PRIVATE_ADDITIONAL_CONTEXT')
        self.repository.set_setting('notes.entries', [{'text': 'PRIVATE_NOTE_MARKER', 'share_with_ai': False}])
        packet = build_assessment_context(self.repository)
        text = json.dumps(packet)
        self.assertNotIn('PRIVATE_', text)
        self.assertNotIn('gm_secrets', text)
        self.assertEqual(set(packet['state']), {
            'player_ai_preferences', 'scene', 'calendar', 'movement', 'player_condition', 'genre',
        })

    def test_location_metadata_updates_preserve_weather_and_status_tracks_current_site(self):
        self.assess()
        EventApplier(self.repository).apply_events([
            {'type': 'LocationUpsertedEvent', 'payload': {
                'name': 'Town', 'description': 'A peaceful town.', 'x_miles': 3, 'y_miles': 0,
            }},
            {'type': 'StatusUpdatedEvent', 'payload': {
                'location': 'AUTO', 'minutes_passed': 5, 'weather': 'Snow',
            }},
        ])
        reopened = SaveRepository(self.repository.db_path)
        self.assertEqual(reopened.find_travel_location('Town')['weather'], 'Rain')
        self.assertEqual(reopened.find_travel_location('Home')['weather'], 'Snow')

    def test_safe_assessment_cannot_bypass_missing_route_estimate(self):
        self.repository.upsert_travel_location({'name': 'Town', 'x_miles': None, 'y_miles': None})
        self.assess()
        self.assertFalse(complete_safe_travel(self.repository, 'Town'))
        self.assertEqual(self.repository.get_state_value('location'), 'Home')

    def test_location_music_is_saved_and_safe_arrival_applies_music_event(self):
        self.repository.set_setting('audio.current_music', 'Home.mp3')
        sync_location_music(self.repository, ['Home.mp3', 'Town.mp3', 'Forest.mp3'])
        packet = build_assessment_context(self.repository)
        self.assertEqual(self.repository.find_travel_location('Home')['music_filename'], 'Home.mp3')
        commit_assessment(self.repository, packet, {'routes': [
            {'location_id': 'town', 'safe': True, 'weather': 'Rain', 'music_filename': 'Town.mp3'},
            {'location_id': 'forest', 'safe': False, 'weather': 'Fog', 'music_filename': 'Forest.mp3'},
        ]})
        self.assertEqual(self.repository.get_setting('audio.current_music'), 'Home.mp3')
        self.assertTrue(complete_safe_travel(self.repository, 'Town', message_id='music-trip'))
        reopened = SaveRepository(self.repository.db_path)
        self.assertEqual(reopened.find_travel_location('Town')['music_filename'], 'Town.mp3')
        self.assertEqual(reopened.get_setting('audio.current_music'), 'Town.mp3')
        events = reopened.list_mechanical_events(limit=10)
        self.assertTrue(any(event['event_type'] == 'MusicChangedEvent'
                            and event['payload']['filename'] == 'Town.mp3' for event in events))

    def test_assessment_rejects_invented_music_atomically(self):
        sync_location_music(self.repository, ['Town.mp3'])
        packet = build_assessment_context(self.repository)
        with self.assertRaises(ValueError):
            commit_assessment(self.repository, packet, {'routes': [
                {'location_id': 'town', 'safe': True, 'weather': 'Rain', 'music_filename': 'Invented.mp3'},
                {'location_id': 'forest', 'safe': False, 'weather': 'Fog', 'music_filename': 'Town.mp3'},
            ]})
        self.assertEqual(self.repository.find_travel_location('Town')['weather'], '')
        self.assertEqual(self.repository.find_travel_location('Town')['music_filename'], '')
