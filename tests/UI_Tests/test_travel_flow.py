from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import Mock, patch

from PySide6.QtCore import QThread, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ai_adventure.application.travel_service import build_assessment_context, commit_assessment, sync_location_music
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.story import StoryScreen
from ai_adventure.ui.screens.travel import TravelScreen
from ai_adventure.ui.travel_controller import TravelAssessmentController
from ai_adventure.ui.game_shell import GameShell
from tests.qt_test_case import QtTestCase


class TravelFlowTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = SaveRepository.create_new_save(Path(self.temporary.name), 'Travel UI')
        self.repository.set_state_value('location', 'Home')
        self.repository.set_travel_locations([
            {'name': 'Home', 'location_id': 'home', 'x_miles': 0, 'y_miles': 0},
            {'name': 'Town', 'location_id': 'town', 'x_miles': 3, 'y_miles': 0},
        ])

    def assess(self, safe=True):
        packet = build_assessment_context(self.repository)
        result = {'routes': [{'location_id': 'town', 'safe': safe, 'weather': 'Rain'}]}
        commit_assessment(self.repository, packet, result)
        return result

    def test_travel_button_uses_local_status_without_story_or_check_request(self):
        self.assess()
        story = StoryScreen()
        story.set_repository(self.repository)
        story.on_repository_changed = Mock()
        screen = TravelScreen(on_travel_requested=story.submit_travel_request)
        screen.set_repository(self.repository)
        screen.location_selector.setCurrentIndex(screen.location_selector.findText('Town'))
        self.assertIn('Weather: Rain', screen.details_output.toPlainText())
        self.assertNotIn('safe', screen.details_output.toPlainText().casefold())
        before = self.repository.get_current_calendar_minute()
        with patch.object(story, '_submit_player_command') as submit:
            screen.travel_button.click()
        submit.assert_not_called()
        self.assertEqual(self.repository.get_state_value('location'), 'Town')
        self.assertEqual(self.repository.get_current_calendar_minute(), before + 60)
        self.assertEqual(story.weather_value.text(), 'Rain')
        story.on_repository_changed.assert_called_once_with(story)

    def test_unsafe_and_unassessed_routes_use_normal_story_flow(self):
        for assessed in (False, True):
            if assessed:
                self.assess(safe=False)
            story = StoryScreen()
            story.set_repository(self.repository)
            with patch.object(story, '_submit_player_command', return_value=True) as submit:
                self.assertTrue(story.submit_travel_request(self.repository.find_travel_location('Town'), 'Carefully.'))
            request = submit.call_args.kwargs['travel_request']
            self.assertEqual(request['destination']['name'], 'Town')
            self.assertEqual(request['route_safety'].get('safe'), False if assessed else None)
            self.assertEqual(self.repository.get_state_value('location'), 'Home')

    def test_stale_widget_safety_or_weather_cannot_authorize_local_travel(self):
        stale = {**self.repository.find_travel_location('Town'), 'safe': True, 'weather': 'Clear'}
        self.assess(safe=False)
        story = StoryScreen()
        story.set_repository(self.repository)
        with patch.object(story, '_submit_player_command', return_value=True) as submit:
            story.submit_travel_request(stale, '')
        self.assertEqual(submit.call_args.kwargs['travel_request']['destination']['weather'], 'Rain')
        self.assertEqual(self.repository.get_state_value('location'), 'Home')

    def shell(self):
        shell = QWidget()
        shell.repository = self.repository
        shell.ai_enabled = True
        shell.gemini_api_key_path = Path('unused-test-key')
        shell.story_screen = SimpleNamespace(_waiting_for_gm=False, _initial_generation_pending=False)
        shell.travel_screen = Mock()
        return shell

    def test_background_assessment_is_not_repeated_or_written_to_another_save(self):
        shell = self.shell()
        controller = TravelAssessmentController(shell)
        result = {'routes': [{'location_id': 'town', 'safe': True, 'weather': 'Rain'}]}
        with patch('ai_adventure.ui.travel_controller.QThreadPool') as pool:
            controller.check()
            controller.check()
            pool.globalInstance().start.assert_called_once()
            worker = controller._worker
            worker.signals.completed.emit(result)
            controller.check()
            pool.globalInstance().start.assert_called_once()
        shell.travel_screen.refresh.assert_called_once()
        self.assertEqual(self.repository.find_travel_location('Town')['weather'], 'Rain')

        self.repository.set_state_value('weather', 'Snow')
        with patch('ai_adventure.ui.travel_controller.QThreadPool') as pool:
            controller.check()
            worker = controller._worker
            shell.repository = None
            worker.signals.completed.emit({'routes': [{'location_id': 'town', 'safe': False, 'weather': 'Blizzard'}]})
        self.assertEqual(self.repository.find_travel_location('Town')['weather'], 'Rain')

    def test_failed_background_request_does_not_loop_or_block_normal_travel(self):
        shell = self.shell()
        controller = TravelAssessmentController(shell)
        with patch('ai_adventure.ui.travel_controller.QThreadPool') as pool:
            controller.check()
            controller._worker.signals.completed.emit(None)
            controller.check()
            pool.globalInstance().start.assert_called_once()
        self.assertEqual(self.repository.get_state_value('location'), 'Home')

    def test_assessment_waits_for_story_then_starts_after_arrival_is_committed(self):
        shell = self.shell()
        controller = TravelAssessmentController(shell)
        shell.story_screen._waiting_for_gm = True
        with (patch('ai_adventure.ui.travel_controller.QTimer.singleShot') as timer,
              patch('ai_adventure.ui.travel_controller.QThreadPool') as pool):
            controller.check()
            pool.globalInstance().start.assert_not_called()
            self.assertEqual(timer.call_args.args[0], 500)
            shell.story_screen._waiting_for_gm = False
            controller.check()
            pool.globalInstance().start.assert_called_once()

    def test_shell_completes_safe_trip_then_queues_assessment_from_new_origin(self):
        shell = GameShell(lambda: None, playtesting_tools=True,
                          gemini_api_key_path='unused-test-key', ai_enabled=True)
        try:
            with patch('ai_adventure.ui.travel_controller.QThreadPool') as pool:
                shell.set_repository(self.repository)
                self.app.processEvents()
                controller = shell.travel_assessment_controller
                self.assertIsNotNone(controller._worker)
                controller._worker.signals.completed.emit({
                    'routes': [{'location_id': 'town', 'safe': True, 'weather': 'Rain'}],
                })
                screen = shell.travel_screen
                screen.location_selector.setCurrentIndex(screen.location_selector.findText('Town'))
                with patch.object(shell.story_screen, '_submit_player_command') as submit:
                    screen.travel_button.click()
                    self.app.processEvents()
                submit.assert_not_called()
                self.assertEqual(self.repository.get_state_value('location'), 'Town')
                self.assertEqual(controller._request[1]['origin'], 'Town')
                self.assertEqual(pool.globalInstance().start.call_count, 2)
        finally:
            shell.set_repository(None)
            shell.close()

    def test_real_background_worker_commits_on_gui_thread(self):
        shell = self.shell()
        controller = TravelAssessmentController(shell)
        result = {'routes': [{'location_id': 'town', 'safe': True, 'weather': 'Rain'}]}
        committing_threads = []

        def commit(repository, packet, response):
            committing_threads.append(QThread.currentThread())
            return commit_assessment(repository, packet, response)

        with (patch('ai_adventure.ui.workers.gemini.GeminiNarrationService') as provider,
              patch('ai_adventure.ui.travel_controller.commit_assessment', side_effect=commit)):
            provider.return_value.assess_travel_routes.return_value = result
            controller.check()
            for _ in range(100):
                QTest.qWait(10)
                if committing_threads:
                    break
            self.assertTrue(QThreadPool.globalInstance().waitForDone(1000))
        self.assertEqual(committing_threads, [self.app.thread()])
        self.assertEqual(self.repository.find_travel_location('Town')['weather'], 'Rain')
        self.assertEqual(self.repository.get_state_value('location'), 'Home')

    def test_safe_arrival_plays_saved_music_and_respects_music_disabled(self):
        for enabled in (True, False):
            with self.subTest(music_enabled=enabled):
                self.repository.set_state_value('location', 'Home')
                self.repository.set_setting('audio.current_music', 'Home.mp3')
                self.repository.set_setting('audio.music_enabled', enabled)
                self.repository.set_setting('audio.music_volume', 37)
                sync_location_music(self.repository, ['Home.mp3', 'Town.mp3'])
                packet = build_assessment_context(self.repository)
                commit_assessment(self.repository, packet, {'routes': [
                    {'location_id': 'town', 'safe': True, 'weather': 'Rain', 'music_filename': 'Town.mp3'},
                ]})
                manager = Mock()
                manager.get_valid_track_names.return_value = ['Home.mp3', 'Town.mp3']
                story = StoryScreen(sound_manager=manager)
                story.set_repository(self.repository)
                manager.reset_mock()
                with patch.object(story, '_submit_player_command') as submit:
                    self.assertTrue(story.submit_travel_request(self.repository.find_travel_location('Town'), ''))
                submit.assert_not_called()
                manager.set_music_volume.assert_called_with(37)
                self.assertEqual(self.repository.get_setting('audio.current_music'), 'Town.mp3')
                if enabled:
                    manager.play_music.assert_called_with('Town.mp3')
                else:
                    manager.play_music.assert_not_called()
                    manager.stop_music.assert_called()
