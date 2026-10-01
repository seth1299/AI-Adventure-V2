from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.persistence.save_repository import SaveRepository


class RepositoryTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = SaveRepository.create_new_save(Path(self.temp.name), "Transactions")
        self.initial_history = self.repository.list_history()

    def response(self, events=None):
        return SimpleNamespace(
            narrative_text="A messenger arrives.",
            pronunciation_map={},
            speaker_cues=[],
            sound_effect_cues=[],
            suggested_events=events or [],
        )

    def test_nested_writes_share_connection_and_are_invisible_until_commit(self):
        repository = self.repository
        with patch("ai_adventure.persistence.save_repository.sqlite3.connect", wraps=sqlite3.connect) as connect:
            with repository.transaction() as outer:
                repository.set_setting("transaction.test", "committed")
                with repository.transaction() as inner:
                    self.assertIs(inner, outer)
                    repository.append_history("story", "Atomic history")
                with closing(connect(repository.db_path)) as observer:
                    self.assertIsNone(observer.execute(
                        "SELECT value_json FROM settings WHERE key = 'transaction.test'"
                    ).fetchone())
            self.assertEqual(connect.call_count, 2)  # transaction plus independent observer
        self.assertEqual(repository.get_setting("transaction.test"), "committed")

    def test_caught_nested_failure_still_rolls_back_and_connection_can_be_reused(self):
        repository = self.repository
        with self.assertRaisesRegex(RuntimeError, "nested failure"):
            with repository.transaction():
                repository.set_setting("transaction.test", "discarded")
                try:
                    with repository.transaction():
                        raise ValueError("caught by caller")
                except ValueError:
                    pass
                repository.append_history("story", "Also discarded")
        self.assertIsNone(repository.get_setting("transaction.test"))
        self.assertEqual(repository.list_history(), self.initial_history)
        repository.set_setting("transaction.test", "recovered")
        self.assertEqual(repository.get_setting("transaction.test"), "recovered")

    def test_response_failure_rolls_back_metadata_narration_events_and_audit(self):
        repository = self.repository
        events = [
            {"type": "InventoryItemAddedEvent", "payload": {"item_name": "Atomic Letter", "amount": 1}},
            {"type": "InventoryItemAddedEvent", "payload": {"item_name": "Broken Letter", "amount": 1}},
        ]
        original = repository.add_inventory_item

        def failing_add(*args, **kwargs):
            if (args[0] if args else kwargs.get("name")) == "Broken Letter":
                raise sqlite3.OperationalError("injected failure")
            return original(*args, **kwargs)

        before_settings = repository.get_setting("tts.pronunciation_map")
        before_inventory = repository.list_inventory_items()
        with patch.object(repository, "add_inventory_item", side_effect=failing_add):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                StoryTurnService.commit_response(repository, self.response(events), message_id="atomic")
        self.assertEqual(repository.list_inventory_items(), before_inventory)
        self.assertEqual(repository.list_history(), self.initial_history)
        self.assertEqual(repository.list_mechanical_events(), [])
        self.assertEqual(repository.get_setting("tts.pronunciation_map"), before_settings)

    def test_response_commits_once_and_preserves_skipped_events(self):
        events = [{"type": "UnsupportedEvent", "payload": {}}]
        with patch("ai_adventure.persistence.save_repository.sqlite3.connect", wraps=sqlite3.connect) as connect:
            result = StoryTurnService.commit_response(
                self.repository, self.response(events), message_id="complete"
            )
            self.assertEqual(connect.call_count, 1)
        self.assertEqual(result.event_results[0].status, "skipped")
        self.assertEqual(self.repository.list_history()[-1]["message_id"], "complete")
        self.assertEqual(len(self.repository.list_mechanical_events()), 1)

    def test_snapshot_failure_rolls_back_player_action(self):
        with patch.object(self.repository, "capture_message_snapshot", side_effect=RuntimeError("snapshot failed")):
            with self.assertRaisesRegex(RuntimeError, "snapshot failed"):
                StoryTurnService.record_player_action(self.repository, "Look", message_id="action")
        self.assertEqual(self.repository.list_history(), self.initial_history)

    def test_generated_world_failure_rolls_back_all_changes(self):
        def write_then_fail(repository, setup, result):
            repository.set_setting("world.partial", "discarded")
            raise RuntimeError("world failed")

        with patch.object(NewGameService, "_apply_generated_state", side_effect=write_then_fail):
            with self.assertRaisesRegex(RuntimeError, "world failed"):
                NewGameService.commit_generated_world(self.repository, {}, self.response())
        self.assertIsNone(self.repository.get_setting("world.partial"))

    def test_other_thread_does_not_join_active_connection(self):
        repository = self.repository
        with ThreadPoolExecutor(max_workers=1) as executor:
            with repository.transaction() as outer:
                repository.set_setting("transaction.thread", "uncommitted")

                def observe():
                    with repository.transaction() as other:
                        return other is outer, repository.get_setting("transaction.thread")

                self.assertEqual(executor.submit(observe).result(timeout=5), (False, None))
        self.assertEqual(repository.get_setting("transaction.thread"), "uncommitted")

    def test_late_response_failure_rolls_back_merchant_and_voice_metadata(self):
        repository = self.repository
        before_voices = repository.get_setting("audio.speaker_voice_assignments")
        before_merchant = repository.get_active_merchant_npc_id()
        original = repository.set_active_merchant_npc

        def fail_after_write(npc_id):
            original(npc_id)
            raise RuntimeError("merchant failed")

        with patch.object(repository, "set_active_merchant_npc", side_effect=fail_after_write):
            with self.assertRaisesRegex(RuntimeError, "merchant failed"):
                StoryTurnService.commit_response(repository, self.response(), message_id="late")
        self.assertEqual(repository.list_history(), self.initial_history)
        self.assertEqual(repository.get_setting("audio.speaker_voice_assignments"), before_voices)
        self.assertEqual(repository.get_active_merchant_npc_id(), before_merchant)

    def test_failed_ui_commit_does_not_start_narration_or_refresh_assets(self):
        from ai_adventure.ui.screens.story import StoryScreen

        screen = SimpleNamespace(
            repository=lambda: self.repository,
            _pending_conversation_mode="live_game",
            _pending_message_id="ui-failure",
            _pending_skill_check_event_results=[],
            narration_player=None,
            _handle_persistence_failure=Mock(),
            _reveal_story_with_narration=Mock(),
            notify_repository_changed=Mock(),
        )
        with patch.object(StoryTurnService, "commit_response", side_effect=RuntimeError("commit failed")):
            StoryScreen._handle_gemini_story_result(screen, self.response())
        screen._handle_persistence_failure.assert_called_once()
        screen._reveal_story_with_narration.assert_not_called()
        screen.notify_repository_changed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
