import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.application.opening_request import OpeningRequest, opening_request_path
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.image_sources import NewGameImageSourceDialog
from ai_adventure.ui.main_window import MainWindow
from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator
from ai_adventure.visual_assets import VisualAssetRequest
from tests.qt_test_case import QtTestCase


class NewGameOpeningTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        paths = AppPaths(app_data_dir=self.root, saves_dir=self.root / "saves",
                         logs_dir=self.root / "logs", log_file=self.root / "logs" / "app.log",
                         local_app_data_dir=self.root)
        paths.saves_dir.mkdir()
        for name, value in (("is_playtesting_build", True), ("_create_narration_player", None)):
            patcher = patch("ai_adventure.ui.main_window." + name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = MainWindow(paths)
        self.addCleanup(self.window.close)
        self.setup = normalize_new_game_setup({"title": "Opening recovery"})
        self.repository = NewGameService.create_repository(paths.saves_dir, self.setup)
        self.repository.set_setting("new_game.opening_pending", True)
        self.window.open_repository(self.repository, new_game=True)
        self.checkpoint = opening_request_path(self.repository.db_path)
        self.packet = {"packet_type": "new_game_setup", "setup": {"title": "Exact saved setup"}}
        OpeningRequest(self.checkpoint, self.packet)
        self.window._pending_new_game_repository = self.repository
        self.window._pending_new_game_setup = self.setup

    def test_failed_opening_preserves_request_and_retry_after_reopening_save(self):
        coordinator = self.window.game_shell.visual_asset_coordinator
        history = self.repository.list_history()
        with patch.object(coordinator, "prepare_initial_batch") as prepare, \
                patch.object(self.window.new_game_service, "apply_fallback") as fallback:
            self.window._finish_failed_new_game_generation(temporary_failure=True)
        prepare.assert_not_called()
        fallback.assert_not_called()
        self.assertEqual(self.repository.list_history(), history)
        self.assertEqual(OpeningRequest(self.checkpoint).setup_packet, self.packet)
        story = self.window.game_shell.story_screen
        self.assertFalse(story.opening_retry_box.isHidden())
        self.assertFalse(story.submit_button.isEnabled())

        self.window.return_to_menu()
        reopened = SaveRepository(self.repository.db_path)
        with patch.object(coordinator, "scan") as scan:
            self.window.open_repository(reopened)
        scan.assert_not_called()
        self.assertFalse(story.opening_retry_box.isHidden())
        self.assertTrue(story._initial_generation_pending)
        self.assertFalse(story.submit_button.isEnabled())

    def test_opening_failure_displays_provider_reason_without_spending_on_images(self):
        message = "Gemini could not complete the request (HTTP 400): invalid safety setting."
        with patch.object(self.window.game_shell.visual_asset_coordinator, "prepare_initial_batch") as prepare:
            self.window._handle_new_game_generation_request_failure(message)
        prepare.assert_not_called()
        self.assertIn(message, self.window.game_shell.story_screen.opening_retry_message.text())
        self.assertTrue(self.checkpoint.exists())

    def test_worker_waits_for_inventory_approval_before_writing_opening(self):
        from ai_adventure.ui.starting_inventory import StartingInventoryReviewDialog
        received = []
        class ReviewService:
            def __init__(self, **_kwargs):
                pass

            def generate_new_game_world_staged(self, packet, *, inventory_review_callback, **_kwargs):
                items = inventory_review_callback({"starting_items": [
                    {"name": "Anvil", "category": "Tool", "quantity": 1, "weight_lb": 200,
                     "source_index": -1, "storage_location": "actively_carried"},
                ]}, packet, None)
                received.append(items)
                return SimpleNamespace(world_summary="A workshop.", introductory_message="You begin empty handed.",
                    approved_starter_items=items, finalized_starter_items=[], finalized_skills=[], suggested_events=[])

        def approve(dialog):
            self.assertFalse(dialog.continue_button.isEnabled())
            self.assertTrue(self.window._new_game_progress_dialog.isHidden())
            self.assertEqual(received, [])
            self.assertEqual(len(self.repository.list_history(kinds=("story",))), 0)
            dialog.quantity_inputs[0].setValue(0)
            dialog.accept()
            return dialog.result()

        with patch("ai_adventure.ui.workers.gemini.GeminiNarrationService", ReviewService), \
                patch.object(StartingInventoryReviewDialog, "exec", approve), \
                patch.object(self.window, "_complete_new_game_generation") as complete:
            self.window._show_new_game_progress_dialog()
            self.window._start_new_game_generation(self.repository, self.setup)
            thread = self.window._new_game_thread
            loop = QEventLoop()
            thread.finished.connect(loop.quit)
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            self.assertTrue(thread.wait(1000))
            self.app.processEvents()
            self.window._release_new_game_progress()
        self.assertEqual(received, [[]])
        self.assertTrue(self.repository.get_setting("new_game.opening_ready"))
        self.assertEqual(self.repository.list_inventory_items(), [])
        complete.assert_called_once_with(self.repository)

    def test_retry_reads_saved_packet_without_rebuilding_it(self):
        received = []

        class FailedWorker(QObject):
            completed = Signal(object)
            phase_changed = Signal(str)
            configuration_error = Signal(str)
            request_failed = Signal(str)
            failed = Signal()
            finished = Signal()

            def __init__(self, packet, api_key_path, *, opening_request_path):
                super().__init__()
                received.append((packet, opening_request_path))

            def run(self):
                self.request_failed.emit("Unavailable")
                self.finished.emit()

        with patch("ai_adventure.ui.main_window._GeminiNewGameWorker", FailedWorker), \
                patch.object(self.window, "_build_new_game_setup_packet", side_effect=AssertionError("rebuilt")):
            self.window._retry_new_game_opening()
            thread = self.window._new_game_thread
            loop = QEventLoop()
            thread.finished.connect(loop.quit)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            self.assertTrue(thread.wait(1000))
        self.assertEqual(received, [(self.packet, self.checkpoint)])
        self.assertTrue(self.checkpoint.is_file())

    def test_valid_opening_is_hidden_and_silent_until_selected_images_finish(self):
        requests = [VisualAssetRequest(subject_type="location", subject_key=str(i),
                    display_name=f"Place {i}", description="A forest.") for i in range(2)]
        coordinator = self.window.game_shell.visual_asset_coordinator
        story = self.window.game_shell.story_screen
        result = SimpleNamespace(world_summary="A quiet beginning.",
            introductory_message="Morning light reaches the forest.", finalized_character={},
            finalized_starter_items=[], finalized_skills=[], suggested_events=[],
            speaker_cues=[], sound_effect_cues=[])
        original_exec = NewGameImageSourceDialog.exec

        def choose(dialog):
            self.assertTrue(self.repository.get_setting("new_game.opening_ready"))
            self.assertTrue(story._initial_generation_pending)
            self.assertTrue(self.checkpoint.is_file())
            narrate.assert_not_called()
            dialog._rows[requests[0].asset_id][2].click()
            create.assert_not_called()
            dialog.continue_button.click()
            narrate.assert_not_called()
            # Even a coordinator completion must not reveal behind the modal.
            self.window._handle_initial_visuals_ready(self.repository)
            narrate.assert_not_called()
            QTimer.singleShot(0, lambda: coordinator.asset_status_changed.emit(
                requests[0].asset_id, "ready", ""))
            return original_exec(dialog)

        with patch.object(coordinator, "prepare_initial_batch", return_value=requests), \
                patch.object(coordinator, "restore_initial_template_images", return_value=requests), \
                patch.object(coordinator, "create_initial_image", return_value=(True, "")) as create, \
                patch.object(coordinator, "skip_initial_image") as skip, \
                patch.object(story, "narrate_latest_story", return_value=False) as narrate, \
                patch.object(NewGameImageSourceDialog, "exec", choose):
            self.window._handle_new_game_generation_result(result)
        create.assert_called_once_with(self.repository, requests[0])
        skip.assert_called_once_with(self.repository, requests[1])
        narrate.assert_called_once_with(reveal_progressively=True)
        self.assertFalse(story._initial_generation_pending)
        self.assertFalse(self.checkpoint.exists())
        self.assertFalse(self.repository.get_setting("new_game.opening_pending"))
        self.assertEqual(len(self.repository.list_history(kinds=("story",))), 1)

    def test_empty_opening_never_starts_images_or_commits_world(self):
        coordinator = self.window.game_shell.visual_asset_coordinator
        with patch.object(coordinator, "prepare_initial_batch") as prepare, \
                patch.object(self.window.new_game_service, "commit_generated_world") as commit, \
                self.assertLogs("ai_adventure.ui.main_window", level="ERROR"):
            self.window._handle_new_game_generation_result(SimpleNamespace(introductory_message=" "))
        commit.assert_not_called()
        prepare.assert_not_called()
        self.assertTrue(self.checkpoint.exists())

    def test_delivery_failure_retains_request_and_retry_uses_committed_opening(self):
        self.repository.append_history("story", "An already validated opening.")
        self.repository.set_setting("new_game.opening_ready", True)
        coordinator = self.window.game_shell.visual_asset_coordinator
        story = self.window.game_shell.story_screen
        with patch.object(story, "narrate_latest_story", side_effect=RuntimeError("Display failed")), \
                self.assertLogs("ai_adventure.ui.main_window", level="ERROR"):
            self.window._finish_initial_visual_generation(self.repository)
        self.assertTrue(self.checkpoint.exists())
        self.assertTrue(self.repository.get_setting("new_game.opening_ready"))
        self.assertTrue(self.repository.get_setting("new_game.opening_pending"))
        with patch.object(self.window, "_start_new_game_generation") as generate, \
                patch.object(coordinator, "prepare_initial_batch", return_value=[]), \
                patch.object(story, "narrate_latest_story", return_value=False):
            self.window._retry_new_game_opening()
        generate.assert_not_called()
        self.assertEqual(len(self.repository.list_history(kinds=("story",))), 1)
        self.assertFalse(self.checkpoint.exists())

    def test_prepared_assets_and_worker_drain_do_not_queue_unselected_images(self):
        coordinator = _VisualAssetCoordinator(images_dir=self.root / "images",
                                              api_key_path=self.root / "key", enabled=True)
        self.repository.set_setting("new_game.opening_pending", False)
        request_builder = patch("ai_adventure.ui.workers.visual_assets.AssetGenerationService.requests_for",
            return_value=[VisualAssetRequest(subject_type="location", subject_key=str(i),
                    display_name=f"Place {i}", description="A forest.") for i in range(3)])
        request_builder.start()
        self.addCleanup(request_builder.stop)
        requests = coordinator.prepare_initial_batch(self.repository)
        self.assertGreater(len(requests), 1)
        for request in requests:
            self.assertEqual(self.repository.get_visual_asset_by_id(request.asset_id)["status"], "skipped")
        with patch("ai_adventure.ui.workers.visual_assets.read_api_key", return_value="test-key"), \
                patch.object(coordinator, "_start_next"):
            self.assertTrue(coordinator.create_initial_image(self.repository, requests[0])[0])
            self.assertEqual([entry[1] for entry in coordinator._queue], [requests[0]])
            coordinator._queue.clear()
            coordinator._queued_asset_ids.clear()
            self.repository.set_visual_asset_status(requests[0].asset_id, "ready")
            coordinator._clear_worker()
            self.assertEqual(coordinator._queue, [])
            coordinator.scan(self.repository)
            self.assertEqual(coordinator._queue, [])
