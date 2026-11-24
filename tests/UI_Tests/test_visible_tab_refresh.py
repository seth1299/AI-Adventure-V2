import os
from contextlib import ExitStack
from pathlib import Path
import tempfile
from unittest.mock import patch

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.game_shell import GameShell
from tests.qt_test_case import QtTestCase


class VisibleTabRefreshTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = SaveRepository.create_new_save(Path(temporary.name), "Lazy tabs")
        self.shell = GameShell(
            lambda: None, tts_enabled=False, ai_enabled=False,
            generated_images_dir=Path(temporary.name) / "images",
        )
        self.shell.set_repository(self.repo, defer_visual_scan=True)
        self.shell.resize(1200, 800)
        self.shell.show()
        self.settle()

    def settle(self):
        for _ in range(4):
            self.app.processEvents()

    def watch_refreshes(self, stack):
        return {screen: stack.enter_context(patch.object(screen, "refresh", wraps=screen.refresh))
                for screen in self.shell.screens}

    def test_only_current_tab_refreshes_and_hidden_changes_coalesce_until_opened(self):
        shell = self.shell
        with ExitStack() as stack:
            calls = self.watch_refreshes(stack)
            shell.refresh_screens(scan_visual_assets=False)
            shell.refresh_screens(scan_visual_assets=False)
            self.settle()
            self.assertEqual(calls[shell.story_screen].call_count, 2)
            self.assertTrue(all(call.call_count == 0 for screen, call in calls.items()
                                if screen is not shell.story_screen))
            self.repo.upsert_npc(name="Mira", npc_id="mira")
            shell.tabs.setCurrentWidget(shell.npcs_screen)
            self.assertEqual(calls[shell.npcs_screen].call_count, 1)
            self.assertEqual(shell.npcs_screen.table.rowCount(), 1)
            shell.tabs.setCurrentWidget(shell.story_screen)
            shell.tabs.setCurrentWidget(shell.npcs_screen)
            self.assertEqual(calls[shell.npcs_screen].call_count, 1)

    def test_source_tab_is_not_refreshed_twice(self):
        shell = self.shell
        shell.tabs.setCurrentWidget(shell.character_screen)
        with ExitStack() as stack:
            calls = self.watch_refreshes(stack)
            shell._handle_screen_repository_changed(shell.character_screen)
            self.settle()
            self.assertTrue(all(call.call_count == 0 for call in calls.values()))
            shell.tabs.setCurrentWidget(shell.inventory_screen)
            calls[shell.inventory_screen].assert_called_once()

    def test_ordinary_update_does_not_rescan_hidden_widget_locks(self):
        shell = self.shell
        with patch.object(shell.character_screen, "sync_adventure_lock") as sync, patch(
            "ai_adventure.ui.survival.adventure_ui_guard"
        ) as guard:
            # Mock the visible refresh too, so only the invalidation path runs.
            with patch.object(shell.story_screen, "refresh"), patch.object(
                shell.story_screen, "sync_adventure_lock"
            ):
                shell.refresh_screens(scan_visual_assets=False)
        sync.assert_not_called()
        guard.assert_not_called()

    def test_closed_tab_catches_up_when_restored(self):
        shell = self.shell
        shell._close_tab(shell._tab_index_for_key("npcs"))
        with patch.object(shell.npcs_screen, "refresh", wraps=shell.npcs_screen.refresh) as refresh:
            self.repo.upsert_npc(name="Mira", npc_id="mira")
            shell.refresh_screens(scan_visual_assets=False)
            refresh.assert_not_called()
            shell._restore_tab("npcs")
            self.settle()
            refresh.assert_called_once()
            self.assertEqual(shell.npcs_screen.table.rowCount(), 1)

    def test_detached_visible_tab_refreshes_but_minimized_tab_waits(self):
        shell = self.shell
        shell._detach_tab("npcs", QPoint(100, 100))
        self.settle()
        window = shell._detached_windows["npcs"]
        with patch.object(shell.npcs_screen, "refresh", wraps=shell.npcs_screen.refresh) as refresh:
            shell.refresh_screens(scan_visual_assets=False)
            refresh.assert_called_once()
            refresh.reset_mock()
            window.showMinimized()
            self.settle()
            self.repo.upsert_npc(name="Mira", npc_id="mira")
            shell.refresh_screens(scan_visual_assets=False)
            refresh.assert_not_called()
            window.showNormal()
            self.settle()
            refresh.assert_called_once()
            self.assertEqual(shell.npcs_screen.table.rowCount(), 1)

    def test_hidden_game_shell_waits_until_shown(self):
        shell = self.shell
        shell.hide()
        with patch.object(shell.story_screen, "refresh", wraps=shell.story_screen.refresh) as refresh:
            shell.refresh_screens(scan_visual_assets=False)
            self.settle()
            refresh.assert_not_called()
            shell.show()
            self.settle()
            refresh.assert_called_once()

    def test_background_results_wait_for_hidden_travel_tab(self):
        shell = self.shell
        with patch.object(shell.travel_screen, "refresh", wraps=shell.travel_screen.refresh) as refresh:
            shell.request_screen_refresh(shell.travel_screen)
            refresh.assert_not_called()
            shell.tabs.setCurrentWidget(shell.travel_screen)
            self.settle()
            refresh.assert_called_once()

    def test_image_result_does_not_refresh_hidden_tabs(self):
        shell = self.shell
        with ExitStack() as stack:
            calls = self.watch_refreshes(stack)
            shell._handle_visual_assets_changed()
            self.settle()
            calls[shell.story_screen].assert_called_once()
            self.assertTrue(all(call.call_count == 0 for screen, call in calls.items()
                                if screen is not shell.story_screen))

    def test_npc_picker_updates_open_dialog_and_table_after_each_upload(self):
        self.repo.upsert_npc(name="Mira", npc_id="mira", public_description="A detective in a raincoat.")
        shell = self.shell
        screen = shell.npcs_screen
        shell.refresh_screens(scan_visual_assets=False)
        shell.tabs.setCurrentWidget(screen)

        inputs = self.repo.db_path.parent
        custom_path = inputs / "custom.png"
        external_path = inputs / "external.png"
        Image.new("RGB", (64, 80), (20, 40, 60)).save(custom_path)
        external = Image.new("RGB", (64, 80), (246, 4, 248))
        external.paste((80, 110, 140), (12, 10, 52, 80))
        # The real external PNG had a few noisy corner pixels.
        external.putpixel((0, 79), (216, 18, 229))
        external.putpixel((63, 79), (224, 14, 240))
        external.save(external_path)

        def exercise_open_dialog(dialog):
            preview = dialog.findChild(QLabel, "npcGeneratedDetailImage")
            button = dialog.findChild(QPushButton, "npcSelectImageButton")
            with (
                patch("ai_adventure.ui.primitives.ImageFileDialog") as picker,
                patch("ai_adventure.ui.primitives.QMessageBox.warning") as warning,
            ):
                picker.return_value.exec.return_value = QDialog.DialogCode.Accepted
                picker.return_value.selectedFiles.return_value = [str(custom_path)]
                button.click()
                warning.assert_not_called()
                thumbnail = screen.table.cellWidget(0, 3)
                for label in (preview, thumbnail):
                    pixel = label.pixmap().toImage().pixelColor(0, 0)
                    self.assertEqual(pixel.getRgb(), (20, 40, 60, 255))

                picker.return_value.selectedFiles.return_value = [str(external_path)]
                button.click()
                warning.assert_not_called()
                self.assertIs(screen.table.cellWidget(0, 3), thumbnail)
                for label in (preview, thumbnail):
                    image = label.pixmap().toImage()
                    self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                    self.assertEqual(image.pixelColor(32, 40).getRgb(), (80, 110, 140, 255))

        outcomes = []

        def during_modal_loop():
            dialog = self.app.activeModalWidget()
            try:
                self.assertIsInstance(dialog, QDialog)
                exercise_open_dialog(dialog)
            except Exception as error:
                outcomes.append(error)
            else:
                outcomes.append(None)
            finally:
                if dialog is not None:
                    dialog.reject()

        QTimer.singleShot(0, during_modal_loop)
        screen._open_npc_details(0, 0)
        self.assertTrue(outcomes)
        if outcomes[0] is not None:
            raise outcomes[0]

    def test_new_save_discards_pending_refreshes_from_previous_save(self):
        shell = self.shell
        shell.refresh_screens(scan_visual_assets=False)
        self.assertIn(shell.npcs_screen, shell._dirty_screens)
        other = SaveRepository.create_new_save(self.repo.db_path.parent.parent, "Other adventure")
        other.upsert_npc(name="Other NPC", npc_id="other_npc")
        shell.set_repository(other, defer_visual_scan=True)
        self.assertNotIn(shell.npcs_screen, shell._dirty_screens)
        shell.tabs.setCurrentWidget(shell.npcs_screen)
        self.assertEqual(shell.npcs_screen.table.item(0, 0).text(), "Other NPC")
