from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.game_shell import GameShell
from ai_adventure.ui.primitives import _set_generated_image
from ai_adventure.ui.screens.notes import NotesScreen
from tests.qt_test_case import QtTestCase


class NotesAutosaveTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = SaveRepository.create_new_save(self.root, "Notes")
        self.repo.set_note_entries([
            {"entry_id": "first", "heading": "First", "body": "Original", "tags": []},
            {"entry_id": "second", "heading": "Second", "body": "Another", "tags": []},
        ])

    def editor(self):
        notes = NotesScreen()
        notes.set_repository(self.repo)
        notes.show()
        notes.edit_note_button.click()
        self.addCleanup(notes.deleteLater)
        return notes

    def test_body_typing_is_debounced_and_autosave_has_no_other_tab_side_effects(self):
        shell = GameShell(lambda: None, tts_enabled=False, ai_enabled=False)
        self.addCleanup(shell.deleteLater)
        shell.set_repository(self.repo, defer_visual_scan=True)
        shell.show()
        shell.tabs.setCurrentWidget(shell.notes_screen)
        notes = shell.notes_screen
        notes.edit_note_button.click()
        with patch.object(shell, "_apply_audio_settings") as audio, \
                patch.object(shell, "refresh_screens") as refresh, \
                patch.object(shell.visual_asset_coordinator, "scan") as scan:
            QTest.keyClicks(notes.entry_body_input, " abc")
            self.assertTrue(notes._autosave_timer.isActive())
            self.assertEqual(self.repo.get_note_entries()[0]["body"], "Original")
            QTest.qWait(1100)
            self.assertEqual(self.repo.get_note_entries()[0]["body"], notes.entry_body_input.toPlainText())
            self.assertTrue(notes._editing_note)
            audio.assert_not_called()
            refresh.assert_not_called()
            scan.assert_not_called()

    def test_background_refresh_preserves_unsaved_body_cursor_and_edit_mode(self):
        notes = self.editor()
        QTest.keyClicks(notes.entry_body_input, " draft")
        body = notes.entry_body_input.toPlainText()
        position = notes.entry_body_input.textCursor().position()
        notes.refresh()
        self.assertEqual(notes.entry_body_input.toPlainText(), body)
        self.assertEqual(notes.entry_body_input.textCursor().position(), position)
        self.assertTrue(notes._editing_note)
        self.assertTrue(notes._autosave_timer.isActive())
        notes.flush_pending_notes()
        notes.refresh()
        self.assertTrue(notes._editing_note)
        self.assertEqual(self.repo.get_note_entries()[0]["body"], body)

    def test_leaving_notes_flushes_pending_edits_and_sharing_preference(self):
        notes = self.editor()
        notes.entry_body_input.setPlainText("New text")
        notes.share_with_ai_checkbox.setChecked(True)
        notes.hide()
        self.assertEqual(self.repo.get_note_entries()[0]["body"], "New text")
        self.assertTrue(self.repo.get_notes_share_with_ai())
        self.assertFalse(notes._autosave_timer.isActive())

    def test_switching_saves_flushes_old_note_without_copying_it_to_new_save(self):
        notes = self.editor()
        notes.entry_body_input.setPlainText("Belongs to the first save")
        other = SaveRepository.create_new_save(self.root, "Other")
        notes.set_repository(other)
        self.assertEqual(self.repo.get_note_entries()[0]["body"], "Belongs to the first save")
        self.assertEqual(other.get_note_entries(), [])
        self.assertFalse(notes._editing_note)

    def test_parentless_image_preview_never_opens_as_a_transient_window(self):
        image_path = self.root / "portrait.png"
        image = QImage(32, 32, QImage.Format.Format_RGB32)
        image.fill(QColor("green"))
        image.save(str(image_path))
        events = []
        class Watch(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.Show and isinstance(obj, QLabel) and obj.isWindow():
                    events.append(obj)
                return False
        watch = Watch()
        self.app.installEventFilter(watch)
        self.addCleanup(self.app.removeEventFilter, watch)
        label = QLabel()
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        self.assertTrue(_set_generated_image(label, image_path, maximum_width=96, maximum_height=96))
        self.assertFalse(label.isVisible())
        QVBoxLayout(parent).addWidget(label)
        parent.show()
        self.app.processEvents()
        self.assertTrue(label.isVisible())
        self.assertEqual(events, [])

    def test_preview_refresh_keeps_selected_note(self):
        notes = NotesScreen()
        self.addCleanup(notes.deleteLater)
        notes.set_repository(self.repo)
        notes.entry_tree.setCurrentItem(notes.entry_tree.topLevelItem(0).child(1))
        notes.refresh()
        self.assertEqual(notes._selected_entry()["entry_id"], "second")

    def test_individual_sharing_works_in_preview_and_editor_and_retains_choices(self):
        notes = self.editor()
        self.assertEqual(notes.share_with_ai_checkbox.text(), "Send all notes to AI")
        individual = notes.entry_share_with_ai_checkbox
        self.assertTrue(individual.isVisible())
        individual.click()
        notes.edit_note_button.click()
        self.assertTrue(individual.isVisible())
        self.assertTrue(self.repo.get_note_entries()[0]["share_with_ai"])
        notes.entry_tree.setCurrentItem(notes.entry_tree.topLevelItem(0).child(1))
        self.assertFalse(individual.isChecked())
        notes.share_with_ai_checkbox.click()
        self.assertTrue(individual.isHidden())
        notes.share_with_ai_checkbox.click()
        self.assertTrue(individual.isVisible())
        self.assertFalse(individual.isChecked())
        notes.entry_tree.setCurrentItem(notes.entry_tree.topLevelItem(0).child(0))
        self.assertTrue(individual.isChecked())
        notes.hide()
        reloaded = NotesScreen()
        self.addCleanup(reloaded.deleteLater)
        reloaded.set_repository(self.repo)
        self.assertTrue(reloaded.entry_share_with_ai_checkbox.isChecked())
        self.assertFalse(reloaded.share_with_ai_checkbox.isChecked())
