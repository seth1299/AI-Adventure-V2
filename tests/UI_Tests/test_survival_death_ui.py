"""Real Qt interaction coverage for death choices and spectator restrictions."""
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QVBoxLayout

from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.game_shell import GameShell
from ai_adventure.ui.screens.story import StoryScreen
from ai_adventure.ui.survival import SurvivalDeathDialog
from tests.qt_test_case import QtTestCase


class SurvivalDeathUiTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = SaveRepository.create_new_save(self.root,"Survival",{
            "survival":{"enabled":True}, "character":{"name":"Mara","appearance":"A traveler in a rough coat."}})

    def die(self):
        EventApplier(self.repo).apply_events([{"type":"StatusUpdatedEvent","payload":{
            "location":"AUTO","minutes_passed":0,"weather":"AUTO","energy":0}}])

    def shell(self, repo):
        shell = GameShell(lambda:None,tts_enabled=False,ai_enabled=False)
        shell.set_repository(repo,defer_music_playback=True,defer_visual_scan=True)
        self.addCleanup(lambda:shell.set_repository(None,defer_music_playback=True,defer_visual_scan=True))
        return shell

    def test_dialog_requires_one_of_the_two_choices(self):
        for choice,attribute in (("accepted","accept_fate_button"),("recovering","recover_button")):
            dialog = SurvivalDeathDialog(["Energy"])
            dialog.show()
            self.app.processEvents()
            QTest.keyClick(dialog,Qt.Key.Key_Escape)
            dialog.reject()
            dialog.accept()
            self.assertFalse(dialog.close())
            self.assertTrue(dialog.isVisible())
            self.assertIsNone(dialog.choice)
            QTest.mouseClick(getattr(dialog,attribute),Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.choice,choice)
            self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)

    def test_reopened_pending_death_prompts_and_accepts_without_deleting_save(self):
        self.die()
        reopened = SaveRepository(self.repo.db_path)
        shell = self.shell(reopened)
        choices = []
        def choose(dialog):
            choices.append(dialog)
            dialog.choice="accepted"
            return QDialog.DialogCode.Accepted
        with patch.object(SurvivalDeathDialog,"exec",choose):
            shell.show()
            self.app.processEvents()
        self.assertEqual(len(choices),1)
        self.assertEqual(reopened.survival_status()["fate"],"accepted")
        self.assertTrue(reopened.db_path.is_file())
        self.assertFalse(shell.story_screen.submit_button.isEnabled())
        self.assertFalse(shell.notes_screen.add_entry_button.isEnabled())
        self.assertFalse(shell.travel_screen.travel_button.isEnabled())
        self.assertFalse(shell.calendar_screen.add_event_button.isEnabled())
        self.assertFalse(shell.magic_screen.cast_spell_button.isEnabled())
        self.assertTrue(shell.survival_controller.banner.isVisible())

    def test_spectator_keeps_narration_and_images_and_blocks_dynamic_write_controls(self):
        self.repo.append_history("story","The camp is quiet.")
        self.die()
        self.repo.choose_survival_fate("accepted")
        narrator = Mock()
        narrator.narrate.return_value=True
        story = StoryScreen(narration_player=narrator)
        story.set_repository(self.repo)
        story.show()
        self.app.processEvents()
        read = next(b for b in story.findChildren(QPushButton) if b.text()=="Read Aloud")
        self.assertTrue(read.isEnabled())
        read.click()
        narrator.play_sample.assert_called()
        self.assertFalse(story._submit_player_command("I return to life"))
        self.assertFalse(story.submit_button.isEnabled())
        self.assertTrue(story.player_input.isReadOnly())
        story.submit_button.setEnabled(True)
        self.assertFalse(story.submit_button.isEnabled())
        # Details dialogs created after death inherit the same restriction.
        dialog = QDialog(story)
        layout = QVBoxLayout(dialog)
        move = QPushButton("Move")
        regenerate = QPushButton("Regenerate")
        image = QPushButton("Create new image for me")
        editor = QLineEdit("State")
        for widget in (move,regenerate,image,editor):
            layout.addWidget(widget)
        generated = Mock()
        image.clicked.connect(generated)
        dialog.show()
        self.app.processEvents()
        self.assertFalse(move.isEnabled())
        self.assertFalse(regenerate.isEnabled())
        self.assertTrue(editor.isReadOnly())
        self.assertTrue(image.isEnabled())
        image.click()
        generated.assert_called_once()

    def test_all_tabs_can_refresh_in_spectator_mode_and_notes_cannot_autosave(self):
        self.repo.set_note_entries([{"entry_id":"note","heading":"Before death","body":"Keep this"}])
        shell = self.shell(self.repo)
        self.die()
        self.repo.choose_survival_fate("accepted")
        shell.refresh_screens(scan_visual_assets=False)
        for index in range(shell.tabs.count()):
            shell.tabs.setCurrentIndex(index)
            shell.tabs.widget(index).refresh()
        self.assertFalse(shell.character_screen.edit_profile_button.isEnabled())
        self.assertFalse(shell.stats_screen.spend_rewards_button.isEnabled())
        self.assertTrue(shell.travel_screen.location_selector.isEnabled())
        self.assertFalse(shell.notes_screen.edit_note_button.isEnabled())
        shell.notes_screen._notes_dirty=True
        shell.notes_screen._note_entries=[]
        shell.notes_screen._persist_notes()
        self.assertEqual(self.repo.get_note_entries()[0]["body"],"Keep this")
        # A real image generation entry point still dispatches its callback.
        shell.character_screen.on_visual_asset_generate=Mock(return_value=(True,""))
        with patch("ai_adventure.ui.primitives.QMessageBox.information"):
            shell.character_screen.create_portrait_button.click()
        shell.character_screen.on_visual_asset_generate.assert_called_once()

    def test_loading_live_save_restores_controls(self):
        self.die()
        self.repo.choose_survival_fate("accepted")
        story = StoryScreen()
        story.set_repository(self.repo)
        live = SaveRepository.create_new_save(self.root,"Live")
        story.set_repository(live)
        self.assertTrue(story.submit_button.isEnabled())
        self.assertFalse(story.player_input.isReadOnly())

    def test_recovery_failure_exposes_retry_without_reducing_xp(self):
        self.repo.upsert_skill("Crafting","Make things",1)
        self.repo.add_skill_xp("Crafting",4)
        self.die()
        self.repo.choose_survival_fate("recovering")
        shell = self.shell(self.repo)
        shell.survival_controller._failed(self.repo,"offline")
        shell.show()
        self.app.processEvents()
        self.assertTrue(shell.survival_controller.retry_button.isVisible())
        self.assertFalse(shell.story_screen.submit_button.isEnabled())
        self.assertEqual(self.repo.get_skill("Crafting")["xp"],4)
        with patch.object(shell.survival_controller,"_start_recovery") as start:
            shell.survival_controller.retry_button.click()
            self.app.processEvents()
        start.assert_called_once_with(self.repo)

    def test_recovery_choice_generates_in_background_and_commits_once(self):
        self.repo.upsert_skill("Crafting","Make things",1)
        self.repo.add_skill_xp("Crafting",4)
        self.die()
        shell = self.shell(self.repo)
        requests = []
        def generate(packet):
            requests.append(packet)
            self.assertIsNot(QThread.currentThread(),self.app.thread())
            self.assertIsNone(getattr(self.repo._transaction_state,"connection",None))
            return {"response":"You black out and wake alive in a roadside shelter.",
                    "location":"Roadside Shelter","weather":"Clear","location_description":"A rough shelter."}
        service = Mock()
        service.generate_survival_recovery.side_effect=generate
        def choose(dialog):
            dialog.choice="recovering"
            return QDialog.DialogCode.Accepted
        with patch.object(SurvivalDeathDialog,"exec",choose), patch(
            "ai_adventure.ui.workers.gemini.GeminiNarrationService",return_value=service):
            shell.show()
            for _ in range(300):
                QTest.qWait(10)
                if self.repo.survival_status()["fate"]=="alive" and shell.survival_controller.thread is None:
                    break
        self.assertEqual(len(requests),1)
        self.assertEqual(self.repo.survival_status()["fate"],"alive")
        self.assertEqual(self.repo.get_skill("Crafting")["xp"],2)
        self.assertEqual(self.repo.get_state_value("location"),"Roadside Shelter")
        self.assertTrue(shell.story_screen.submit_button.isEnabled())
        self.assertIsNone(shell.survival_controller.thread)
