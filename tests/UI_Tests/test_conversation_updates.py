import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QPixmap, QTextCursor
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTextEdit

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.story import StoryScreen
from tests.qt_test_case import QtTestCase


class ConversationUpdateTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.screen = StoryScreen()
        self.screen.resize(1000, 700)
        self.screen.show()
        self.entries = [("ai", "live_game", f"Message {i}", i) for i in range(1, 4)]
        self.screen._render_conversation(self.entries)
        self.app.processEvents()

    def rows(self):
        return [item[1] for item in self.screen._conversation_rows.values()]

    def test_append_keeps_existing_rows_documents_and_selection(self):
        before = self.rows()
        message = before[0].findChild(QTextEdit)
        cursor = message.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        message.setTextCursor(cursor)
        with patch.object(self.screen, "_conversation_bubble", wraps=self.screen._conversation_bubble) as build:
            self.screen._render_conversation(self.entries + [("player", "live_game", "Look", 4)])
        self.assertEqual(build.call_count, 1)
        self.assertEqual(self.rows()[:3], before)
        self.assertIs(self.rows()[0].findChild(QTextEdit), message)
        self.assertEqual(message.textCursor().selectedText(), "Message 1")

    def test_unchanged_refresh_does_not_rebuild_or_move_scroll(self):
        before = self.rows()
        with patch.object(self.screen, "_conversation_bubble") as build:
            self.screen._render_conversation(self.entries)
        build.assert_not_called()
        self.assertEqual(self.rows(), before)

    def test_changed_content_replaces_only_affected_row(self):
        before = self.rows()
        entries = list(self.entries)
        entries[1] = ("ai", "live_game", "A revised message", 2)
        self.screen._render_conversation(entries)
        after = self.rows()
        self.assertIs(after[0], before[0])
        self.assertIsNot(after[1], before[1])
        self.assertIs(after[2], before[2])
        self.assertTrue(before[1].isHidden())

    def test_prepend_hide_and_restore_preserve_unaffected_rows(self):
        before = self.rows()
        older = ("player", "live_game", "Earlier", 0)
        self.screen._render_conversation([older] + self.entries)
        self.assertEqual(self.rows()[1:], before)
        self.screen._render_conversation([older, self.entries[0], self.entries[2]])
        self.assertEqual(self.rows()[1:], [before[0], before[2]])
        self.screen._render_conversation([older] + self.entries)
        self.assertIs(self.rows()[1], before[0])
        self.assertIs(self.rows()[3], before[2])

    def test_speaker_segments_with_same_history_id_have_separate_stable_rows(self):
        entries = [("ai", "live_game", text, 1) for text in ("Narration", "Dialogue")]
        self.screen._render_conversation(entries)
        before = self.rows()
        self.assertIsNot(before[0], before[1])
        self.screen._render_conversation(entries + [("player", "live_game", "Reply", 2)])
        self.assertEqual(self.rows()[:2], before)

    def test_player_dialogue_inside_saved_turn_uses_you_and_player_portrait(self):
        with TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Dialogue")
            repository.set_setting("player_name", "Ghum Schue")
            player_id = repository.get_player_id()
            repository.append_history("player", '"Come in." I wait. "Who is after you?"')
            repository.append_history(
                "story", '"Come in." He takes a seat. "Who is after you?" "I do not know."',
                message_id="turn-1",
                speaker_cues=[
                    {"anchor_text": '"Come in."', "speaker_name": "Ghum Schue",
                     "speaker_id": "player_character", "speaker_role": "player", "voice_id": "am_adam"},
                    {"anchor_text": '"Who is after you?"', "speaker_name": "Ghum Schue",
                     "speaker_id": "ghum_schue", "voice_id": "am_adam"},
                    {"anchor_text": '"I do not know."', "speaker_name": "Mandel Cole",
                     "speaker_id": "mandel", "voice_id": "am_michael"},
                ],
            )
            portrait = Path(directory) / "portrait.png"
            pixmap = QPixmap(10, 10)
            pixmap.fill()
            self.assertTrue(pixmap.save(str(portrait)))
            assets = {("player", player_id): {"filename": "player.png"},
                      ("npc", "mandel"): {"filename": "mandel.png"}}
            with (
                patch.object(repository, "get_visual_asset", side_effect=lambda kind, key: assets.get((kind, key))),
                patch.object(self.screen, "visual_asset_path", side_effect=lambda asset: portrait if asset else None),
                patch.object(repository, "has_message_snapshot", return_value=True),
            ):
                self.screen.set_repository(repository)
            rows = self.rows()
            self.assertEqual(len(rows), 5)
            for row in (rows[1], rows[3]):
                self.assertTrue(any(label.text().startswith("You  |  Live Game  |  Turn #1")
                                    for label in row.findChildren(QLabel)))
                self.assertEqual(row.findChild(QLabel, "conversationSpeakerPortrait").accessibleName(),
                                 "Profile picture of You")
                self.assertIsNotNone(row.findChild(QPushButton, "conversationRegenerate"))
            self.assertEqual(rows[2].findChild(QTextEdit).toPlainText(), "He takes a seat.")
            self.assertEqual(rows[4].findChild(QLabel, "conversationSpeakerPortrait").accessibleName(),
                             "Profile picture of Mandel Cole")

    def test_regeneration_action_changes_without_replacing_message(self):
        row = self.rows()[0]
        message = row.findChild(QTextEdit)
        self.screen._sync_bubble_regeneration(row, True, "turn-1")
        button = row.findChild(QPushButton, "conversationRegenerate")
        with patch.object(self.screen, "_request_regeneration") as regenerate:
            button.click()
        regenerate.assert_called_once_with("turn-1")
        self.screen._sync_bubble_regeneration(row, False, "turn-1")
        self.assertIsNone(row.findChild(QPushButton, "conversationRegenerate"))
        self.assertIs(row.findChild(QTextEdit), message)

    def test_switching_repository_clears_cached_rows_even_with_matching_ids(self):
        before = self.rows()
        self.screen.set_repository(None)
        self.screen._render_conversation(self.entries)
        self.assertTrue(all(new is not old for new, old in zip(self.rows(), before)))

    def test_new_turn_moves_regenerate_action_and_preserves_previous_row(self):
        repository = Mock()
        repository.has_message_snapshot.return_value = True
        repository.list_visual_assets_for_message.return_value = []
        first = ("ai", "live_game", "First turn", 1, "turn-1", 1)
        second = ("ai", "live_game", "Second turn", 2, "turn-2", 2)
        with patch.object(self.screen, "repository", return_value=repository), patch.object(
            self.screen, "_latest_ai_message_entry", return_value={"id": 1}
        ) as latest:
            self.screen._render_conversation([first])
            row = self.rows()[0]
            self.assertIsNotNone(row.findChild(QPushButton, "conversationRegenerate"))
            latest.return_value = {"id": 2}
            self.screen._render_conversation([first, second])
            self.assertIs(self.rows()[0], row)
            self.assertIsNone(row.findChild(QPushButton, "conversationRegenerate"))
            button = self.rows()[1].findChild(QPushButton, "conversationRegenerate")
            with patch.object(self.screen, "_request_regeneration") as regenerate:
                button.click()
            regenerate.assert_called_once_with("turn-2")

    def test_image_change_refreshes_only_its_own_message(self):
        asset = {"subject_type": "npc", "filename": "portrait.png"}
        def speaker_asset(role, cues):
            return asset if cues else None
        entries = [("ai", "live_game", "Hello", 1, "turn-1", 1, "Mira", "Hello", [],
                    [{"speaker_id": "mira"}]), self.entries[1]]
        with patch.object(self.screen, "_conversation_speaker_asset", side_effect=speaker_asset):
            self.screen._render_conversation(entries)
            before = self.rows()
            asset["filename"] = "new_portrait.png"
            self.screen._render_conversation(entries)
        self.assertIsNot(self.rows()[0], before[0])
        self.assertIs(self.rows()[1], before[1])

    def test_read_aloud_never_shows_as_a_top_level_window(self):
        class Probe(QObject):
            def __init__(self):
                super().__init__()
                self.windows = []

            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.Show and isinstance(obj, QPushButton) and obj.isWindow():
                    self.windows.append(obj.text())
                return False

        probe = Probe()
        self.app.installEventFilter(probe)
        try:
            self.screen.narration_player = object()
            self.screen._render_conversation(self.entries)
            self.app.processEvents()
            self.assertEqual(probe.windows, [])
        finally:
            self.app.removeEventFilter(probe)
