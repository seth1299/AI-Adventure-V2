from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel

from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.core.state_manager import StateManager
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.story import StoryScreen
from tests.qt_test_case import QtTestCase


class HistoryPaginationTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = SaveRepository.create_new_save(Path(self.temp.name), "Long adventure")
        with self.repository.transaction():
            for number in range(120):
                self.repository.append_history(
                    "story" if number % 2 else "player", f"Message {number}",
                    message_id=f"message-{number}",
                )
                self.repository.append_mechanical_event("TestEvent", {"number": number}, "applied", "")

    def test_recent_history_and_events_match_full_tail_in_chronological_order(self):
        repo = self.repository
        self.assertEqual(repo.list_history(limit=8), repo.list_history()[-8:])
        self.assertEqual(repo.list_mechanical_events(limit=40), repo.list_mechanical_events()[-40:])
        self.assertEqual(repo.list_history(limit=0), [])
        self.assertEqual(repo.list_mechanical_events(limit=0), [])
        with self.assertRaises(ValueError):
            repo.list_history(limit=-1)

    def test_id_cursor_pages_do_not_shift_when_new_messages_arrive(self):
        repo = self.repository
        kinds = StoryScreen.CONVERSATION_KINDS
        original = repo.list_history(kinds=kinds)
        newest = repo.list_history(limit=50, kinds=kinds)
        repo.append_history("story", "New arrival")
        previous = repo.list_history(limit=50, before_id=newest[0]["id"], kinds=kinds)
        oldest = repo.list_history(limit=50, before_id=previous[0]["id"], kinds=kinds)
        self.assertEqual(oldest + previous + newest, original)
        self.assertEqual(repo.list_history(limit=50, before_id=oldest[0]["id"], kinds=kinds), [])

    def test_recent_query_does_not_decode_old_messages(self):
        repo = self.repository
        with repo.transaction() as connection:
            connection.execute("UPDATE history_entries SET sound_effect_cues_json='bad json' WHERE message_id='message-0'")
        with patch("ai_adventure.persistence.save_repository.LOGGER.warning") as warning:
            repo.list_history(limit=8)
        warning.assert_not_called()

    def test_context_fetches_only_existing_recent_windows(self):
        repo = self.repository
        with patch.object(repo, "list_history", wraps=repo.list_history) as history, patch.object(
            repo, "list_mechanical_events", wraps=repo.list_mechanical_events
        ) as events:
            packet = StoryTurnService.build_context_packet(repo, "Look around")
        history.assert_called_once_with(limit=8)
        events.assert_called_once_with(limit=40)
        self.assertEqual(len(packet["recent_history"]), 8)
        self.assertEqual(packet["recent_history"][-1]["content"], "Message 119")
        self.assertEqual(len(packet["state"]["event_audit"]["recent_mechanical_events"]), 40)

    def test_bounded_context_matches_previous_full_history_context(self):
        repo = self.repository
        full_history = repo.list_history()
        full_events = repo.list_mechanical_events()
        # Creative seeds vary per request independently of history retrieval.
        with patch.object(AiContextBuilder, "_build_creative_ideas_context", return_value={}):
            bounded = StoryTurnService.build_context_packet(repo, "Look around")
            with patch.object(repo, "list_history", return_value=full_history), patch.object(
                repo, "list_mechanical_events", return_value=full_events
            ):
                previous = StoryTurnService.build_context_packet(repo, "Look around")
        self.assertEqual(bounded, previous)

    def test_state_can_skip_history_without_querying_it(self):
        with patch.object(self.repository, "list_history", side_effect=AssertionError("Full history read")):
            state = StateManager(self.repository).load_state(history_limit=0)
        self.assertEqual(state.history.entries, [])
        self.assertTrue(state.inventory.items)

    def test_screen_loads_pages_preserves_turn_numbers_and_resets_for_new_save(self):
        screen = StoryScreen()
        self.addCleanup(screen.close)
        self.addCleanup(screen.deleteLater)
        with patch.object(screen, "_render_conversation") as render:
            screen.set_repository(self.repository)
            first = render.call_args.args[0]
            self.assertEqual(len(first), 50)
            self.assertEqual([row[5] for row in first if row[0] == "ai"], list(range(36, 61)))
            self.assertFalse(screen.load_older_messages_button.isHidden())
            screen._load_older_messages()
            self.assertEqual(len(render.call_args.args[0]), 100)
            screen._load_older_messages()
            self.assertEqual(len(render.call_args.args[0]), 120)
            self.assertTrue(screen.load_older_messages_button.isHidden())
            screen.set_repository(self.repository)
            self.assertEqual(len(render.call_args.args[0]), 50)

    def test_latest_helpers_and_hidden_counts_do_not_fetch_full_history(self):
        repo = self.repository
        oldest = repo.list_history(limit=120, kinds=StoryScreen.CONVERSATION_KINDS)[0]
        repo.set_history_entry_hidden(oldest["id"], True)
        self.assertEqual(repo.count_history(kinds=StoryScreen.CONVERSATION_KINDS, hidden=True), 1)
        screen = StoryScreen()
        self.addCleanup(screen.close)
        self.addCleanup(screen.deleteLater)
        screen.set_repository(repo)
        with patch.object(repo, "list_history", wraps=repo.list_history) as history:
            self.assertEqual(screen._latest_story_entry()["content"], "Message 119")
            self.assertEqual(screen._latest_player_command(), "Message 118")
            self.assertEqual(screen._player_command_before_history_id(oldest["id"]), "")
        self.assertTrue(all(call.kwargs["limit"] == 1 for call in history.call_args_list))
        self.assertEqual(screen.view_hidden_messages_button.text(), "View Hidden Messages (1)")

    def test_message_times_survive_clock_changes_reopening_and_pagination(self):
        repo = self.repository
        repo.set_calendar_settings({"time_display": "24_hour"})
        repo.set_current_calendar_minute(23 * 60 + 55)
        repo.append_history("player", "Wait until midnight")
        repo.set_current_calendar_minute(24 * 60 + 5)
        repo.append_history("story", "Midnight has passed.")
        repo.append_history("story_oog", "The clock is now past midnight.")
        repo.set_current_calendar_minute(3 * 24 * 60)
        reopened = SaveRepository(repo.db_path)
        self.assertEqual(
            [entry["game_time"] for entry in reopened.list_history(limit=3)],
            ["Day 1, 23:55", "Day 2, 00:05", "Day 2, 00:05"],
        )
        screen = StoryScreen()
        self.addCleanup(screen.close)
        self.addCleanup(screen.deleteLater)
        screen.set_repository(reopened)
        screen._load_older_messages()
        headers = [label.text() for label in screen.findChildren(QLabel)]
        self.assertIn("You  |  Live Game  |  Day 1, 23:55", headers)
        self.assertIn("AI Game Master  |  Live Game  |  Turn #61  |  Day 2, 00:05", headers)
        self.assertIn("AI Game Master  |  Out-of-Game  |  Day 2, 00:05", headers)

    def test_legacy_message_time_is_unknown(self):
        with self.repository.transaction() as connection:
            connection.execute("ALTER TABLE history_entries DROP COLUMN game_time")
        reopened = SaveRepository(self.repository.db_path)
        screen = StoryScreen()
        self.addCleanup(screen.close)
        self.addCleanup(screen.deleteLater)
        screen.set_repository(reopened)
        headers = [label.text() for label in screen.findChildren(QLabel)]
        self.assertIn("You  |  Live Game  |  Time unknown", headers)

    def test_legacy_spectator_history_can_be_read_without_migration(self):
        self.repository.set_setting("survival.fate", "accepted")
        with self.repository.transaction() as connection:
            connection.execute("ALTER TABLE history_entries DROP COLUMN game_time")
        reopened = SaveRepository(self.repository.db_path)
        self.assertEqual(reopened.list_history(limit=1)[0]["game_time"], "")
        with reopened._connect() as connection:
            self.assertNotIn("game_time", {
                row[1] for row in connection.execute("PRAGMA table_info(history_entries)")
            })


if __name__ == "__main__":
    unittest.main()
