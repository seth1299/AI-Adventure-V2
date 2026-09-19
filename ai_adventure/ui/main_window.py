from __future__ import annotations

import logging
from math import ceil
from time import monotonic

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403
from ai_adventure.ui.image_sources import NewGameImageSourceDialog
from ai_adventure.ui.game_shell import *  # noqa: F401,F403
from ai_adventure.ui.game_shell import _DetachedTabWindow
from ai_adventure.ui.screens.main_menu import MainMenuScreen
from ai_adventure.ui.screens.story import StoryScreen
from ai_adventure.ui.screens.character import CharacterScreen
from ai_adventure.ui.screens.combat import CombatScreen
from ai_adventure.ui.screens.bestiary import BestiaryScreen
from ai_adventure.ui.screens.travel import TravelScreen
from ai_adventure.ui.screens.calendar import CalendarScreen
from ai_adventure.ui.screens.inventory import *  # noqa: F401,F403
from ai_adventure.ui.common import _inventory_item_display_name, _inventory_quantity_display
from ai_adventure.ui.screens.party import PartyScreen
from ai_adventure.ui.screens.npcs import NpcsScreen
from ai_adventure.ui.screens.tasks import ActiveTasksScreen
from ai_adventure.ui.screens.skills import SkillsScreen
from ai_adventure.ui.screens.magic import MagicScreen
from ai_adventure.ui.screens.alchemy import AlchemyNotebookScreen
from ai_adventure.ui.screens.notes import NotesScreen
from ai_adventure.ui.screens.settings import SettingsScreen
from ai_adventure.ui.wizards.new_game import NewGameWizard
from ai_adventure.ui.workers.gemini import (
    GeminiNewGameWorker as _GeminiNewGameWorker,
    GeminiSkillCheckPlanWorker as _GeminiSkillCheckPlanWorker,
    GeminiStoryWorker as _GeminiStoryWorker,
    GeminiVisualAssetWorker as _GeminiVisualAssetWorker,
)
from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator

_ExtractedMainMenuScreen = MainMenuScreen

LOGGER = logging.getLogger(__name__)


class _NewGameGenerationProgressDialog(QDialog):
    """Application-modal progress display for staged new-game generation."""

    _SEQUENCE = (
        "setup_compilation",
        "world_skeleton",
        "world_repair",
        "entities_mechanics",
        "entities_repair",
        "opening_prose",
        "opening_repair",
        "final_commit",
        "initial_visuals",
    )
    _PROGRESS = {
        "setup_compilation": 3,
        "world_skeleton": 15,
        "world_repair": 27,
        "entities_mechanics": 42,
        "entities_repair": 57,
        "opening_prose": 72,
        "opening_repair": 84,
        "final_commit": 96,
        "initial_visuals": 99,
    }
    _LABELS = {
        "setup_compilation": "Preparing the generation plan...",
        "world_skeleton": "Generating the World Skeleton...",
        "world_repair": "Checking the World Skeleton...",
        "entities_mechanics": "Generating Entities and Mechanics...",
        "entities_repair": "Checking Entities and Mechanics...",
        "opening_prose": "Writing the Opening Prose...",
        "opening_repair": "Checking the Opening Prose...",
        "final_commit": "Finalizing the new adventure...",
        "initial_visuals": "Generating the initial world images...",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._allow_close = False
        self._repair_count = 0
        self._sequence_index = -1
        self._last_stage_time = monotonic()
        self._stage_durations: list[float] = []
        self._eta_timer = QTimer(self)
        self._eta_timer.setInterval(1000)
        self._eta_timer.timeout.connect(self._update_eta)

        self.setWindowTitle("Creating New Adventure")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self.setFixedSize(520, 175)

        title = QLabel("Gemini is creating your new adventure.")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        title.setWordWrap(True)
        self.stage_label = QLabel(self._LABELS["setup_compilation"])
        self.stage_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.eta_label = QLabel("Estimated time remaining: calculating...")
        self.eta_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self.stage_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.eta_label)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._eta_timer.start()
        self._update_eta()

    def update_phase(self, phase: str) -> None:
        """Advances the display to the next worker-reported generation phase."""

        if phase == "targeted_repairs":
            self._repair_count += 1
            repair_names = ("world_repair", "entities_repair", "opening_repair")
            stage = repair_names[min(self._repair_count - 1, len(repair_names) - 1)]
        else:
            stage = phase
        if stage not in self._PROGRESS:
            return

        sequence_index = self._SEQUENCE.index(stage)
        if sequence_index > self._sequence_index:
            now = monotonic()
            if self._sequence_index >= 0:
                self._stage_durations.append(now - self._last_stage_time)
            self._last_stage_time = now
            self._sequence_index = sequence_index

        self.stage_label.setText(self._LABELS[stage])
        self.progress_bar.setValue(self._PROGRESS[stage])
        self._update_eta()

    def finish(self) -> None:
        """Marks the opening as ready and closes the modal programmatically."""

        self._eta_timer.stop()
        self.progress_bar.setValue(100)
        self.stage_label.setText("Opening message ready.")
        self.eta_label.setText("Generation complete.")
        self._allow_close = True
        QDialog.close(self)

    def set_visual_generation_pending(self) -> None:
        """Shows that finalized world images are still being generated."""

        self.update_phase("initial_visuals")

    def _update_eta(self) -> None:
        if len(self._stage_durations) < 2:
            self.eta_label.setText("Estimated time remaining: calculating...")
            return
        average_duration = sum(self._stage_durations) / len(self._stage_durations)
        remaining_stages = max(0, len(self._SEQUENCE) - self._sequence_index)
        elapsed_current_stage = max(0.0, monotonic() - self._last_stage_time)
        remaining_seconds = max(
            0.0, average_duration * remaining_stages - elapsed_current_stage
        )
        if remaining_seconds < 1:
            estimate = "under 1 second"
        elif remaining_seconds < 60:
            estimate = f"about {ceil(remaining_seconds)} seconds"
        else:
            estimate = f"about {remaining_seconds / 60:.1f} minutes"
        self.eta_label.setText(f"Estimated time remaining: {estimate}")

    def closeEvent(self, event: Any) -> None:
        """Prevents the player from dismissing generation in progress."""

        if self._allow_close:
            self._eta_timer.stop()
            super().closeEvent(event)
        else:
            event.ignore()

    def reject(self) -> None:
        """Ignores Escape and other user-triggered dialog rejection."""

        if self._allow_close:
            super().reject()


class MainWindow(QMainWindow):
    """
    Main application window.

    Owns the Main Menu and the in-game tab shell.
    """

    def __init__(self, app_paths: AppPaths) -> None:
        """
        Args:
            app_paths: Centralized application paths.
        """

        super().__init__()

        self.app_paths = app_paths
        self.save_game_service = SaveGameService(self.app_paths.saves_dir)
        self.active_repository: SaveRepository | None = None
        self._new_game_thread: QThread | None = None
        self._new_game_worker: QObject | None = None
        self._pending_new_game_repository: SaveRepository | None = None
        self._pending_new_game_setup: dict[str, Any] | None = None
        self._new_game_progress_dialog: _NewGameGenerationProgressDialog | None = None
        self._new_game_image_source_dialog: NewGameImageSourceDialog | None = None
        self.playtesting_build = is_playtesting_build()
        self.ai_enabled = is_ai_enabled()
        self.tts_enabled = is_tts_enabled()
        self.app_settings = load_app_settings(
            self.app_paths.app_settings_path,
            fallback_theme=self._latest_saved_theme(),
            tts_enabled=self.tts_enabled,
        )
        self.new_game_service = NewGameService(
            tts_enabled=self.tts_enabled,
            audio_defaults=self.app_settings["audio"],
        )
        self.menu_theme = _normalize_theme_name(self.app_settings["theme"])
        self.sound_manager = (
            None
            if self.playtesting_build or _SoundManagerClass is None
            else _SoundManagerClass(
                prepare_sound_directory(self.app_paths),
                prepare_sound_effect_directory(self.app_paths),
                prepare_background_ambience_directory(self.app_paths),
                user_music_directory=self.app_paths.sounds_dir,
                user_sound_effects_directory=self.app_paths.sound_effects_dir,
                user_background_ambience_directory=self.app_paths.background_ambience_dir,
                packaged_music_directory=self.app_paths.package_music_tracks_dir,
                packaged_sound_effects_directory=self.app_paths.package_sound_effects_dir,
                packaged_background_ambience_directory=self.app_paths.package_background_ambience_dir,
            )
        )
        self.narration_player = _create_narration_player(self.app_paths)

        self.application_name = (
            "AI Adventure Playtesting"
            if self.playtesting_build
            else "AI Adventure"
        )
        self.setWindowTitle(self.application_name)
        self._set_app_icon()
        self.resize(1100, 750)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.main_menu = _ExtractedMainMenuScreen(
            saves_dir=self.app_paths.saves_dir,
            save_service=self.save_game_service,
            on_new_game=self.start_new_game_wizard,
            on_load_game=self.load_game_from_path,
            on_settings=self.open_main_menu_settings,
            on_templates=self.open_new_game_templates,
            application_name=self.application_name,
            new_game_label="New Playtest" if self.playtesting_build else "New Game",
            show_templates=not self.playtesting_build,
        )

        self.game_shell = GameShell(
            on_return_to_menu=self.return_to_menu,
            on_theme_changed=self._apply_active_theme,
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
            tts_enabled=self.tts_enabled,
            on_app_tts_settings_saved=self._persist_app_tts_settings,
            global_tts_settings_provider=lambda: self.app_settings["audio"],
            custom_voice_storage_path=self.app_paths.app_settings_path,
            gemini_api_key_path=self.app_paths.gemini_api_key_path,
            generated_images_dir=self.app_paths.images_dir,
            playtesting_tools=self.playtesting_build,
            ai_enabled=self.ai_enabled,
        )
        self.game_shell.visual_asset_coordinator.initial_batch_finished.connect(
            self._handle_initial_visuals_ready
        )

        self.stack.addWidget(self.main_menu)
        self.stack.addWidget(self.game_shell)

        self.return_to_menu()

    def _set_app_icon(self) -> None:
        """Sets the main-window icon when the packaged icon is available."""

        icon_path = self.app_paths.app_icon_path

        if not icon_path.exists():
            LOGGER.warning("Application icon not found: %s", icon_path)
            return

        icon = QIcon(str(icon_path))

        if icon.isNull():
            LOGGER.warning("Application icon could not be loaded: %s", icon_path)
            return

        self.setWindowIcon(icon)

    def start_new_game_wizard(self) -> None:
        """Opens the New Game Wizard."""

        if self.playtesting_build:
            self._start_new_playtest()
            return

        should_continue, template_setup, loaded_template_name = (
            self._choose_new_game_template_setup()
        )

        if not should_continue:
            return

        wizard = NewGameWizard(
            self,
            template_setup=template_setup,
            tts_enabled=self.tts_enabled,
            audio_defaults=self.app_settings["audio"],
            voice_options=_narrator_voice_options(self.narration_player),
            on_sample_voice=self._play_narrator_sample,
            on_tts_settings_saved=self._persist_app_tts_settings,
            custom_voice_storage_path=self.app_paths.app_settings_path,
            api_key_path=self.app_paths.gemini_api_key_path,
            terms_acceptance_path=self.app_paths.gemini_terms_acceptance_path,
            sound_manager=self.sound_manager,
        )
        loaded_template_baseline = (
            wizard.build_setup() if loaded_template_name else None
        )

        while True:
            if wizard.exec() != QDialog.DialogCode.Accepted:
                return

            clean_setup = wizard.build_setup()
            template_save_name: str | None = None
            if (
                loaded_template_name
                and loaded_template_baseline is not None
                and template_setup_has_changes(loaded_template_baseline, clean_setup)
            ):
                template_action = self._prompt_for_modified_template_action(
                    loaded_template_name
                )
                if template_action is None:
                    continue
                if template_action == "overwrite":
                    template_save_name = loaded_template_name
                elif template_action == "save_as_new":
                    template_save_name = self._prompt_for_new_template_name(
                        loaded_template_name
                    )
                    if template_save_name is None:
                        continue

            while True:
                clean_setup = wizard.build_setup()
                try:
                    self._create_new_game_from_setup(
                        clean_setup,
                        template_save_name=template_save_name,
                        auto_save_template_if_available=loaded_template_name is None,
                    )
                    return
                except DuplicateSaveTitleError as error:
                    new_title = self._prompt_for_duplicate_save_title(
                        clean_setup["title"],
                        str(error),
                    )

                    if new_title is None:
                        break

                    wizard.title_input.setText(new_title)
                except Exception:
                    LOGGER.exception("Failed to create new game.")
                    QMessageBox.critical(
                        self,
                        "New Game Failed",
                        "Could not create a new game.",
                    )
                    return

    def _start_new_playtest(self) -> None:
        """Creates an isolated save without invoking setup generation or Gemini."""

        save_service = getattr(
            self,
            "save_game_service",
            SaveGameService(self.app_paths.saves_dir),
        )
        suggested_title = save_service.next_available_title("Combat Playtest")
        title, accepted = QInputDialog.getText(
            self,
            "New Playtest",
            "Playtest save name:",
            QLineEdit.EchoMode.Normal,
            suggested_title,
        )

        if not accepted:
            return

        clean_title = title.strip()

        if not clean_title:
            QMessageBox.warning(
                self,
                "Missing Playtest Name",
                "Enter a name for the playtest save.",
            )
            return

        try:
            save_service = getattr(
                self,
                "save_game_service",
                SaveGameService(self.app_paths.saves_dir),
            )
            repository = save_service.create(clean_title)
        except DuplicateSaveTitleError as error:
            QMessageBox.warning(self, "Playtest Name Already Exists", str(error))
            return
        except Exception:
            LOGGER.exception("Failed to create playtest save.")
            QMessageBox.critical(
                self,
                "New Playtest Failed",
                "Could not create the playtest save.",
            )
            return

        repository.set_setting("theme", self.menu_theme)
        self.open_repository(repository)

    def open_main_menu_settings(self) -> None:
        """Opens app-level settings from the Main Menu."""

        dialog = MainMenuSettingsDialog(
            self,
            settings=self.app_settings,
            tts_enabled=self.tts_enabled,
            music_enabled=not self.playtesting_build,
            voice_options=_narrator_voice_options(self.narration_player),
            on_sample_voice=self._play_narrator_sample,
            custom_voice_storage_path=self.app_paths.app_settings_path,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._apply_app_settings(dialog.build_settings(), persist=True)

    def open_new_game_templates(self) -> None:
        """Opens the app-level new-game template manager."""

        dialog = NewGameTemplateManagerDialog(
            self,
            template_path=self.app_paths.new_game_templates_path,
            legacy_template_path=self.app_paths.legacy_new_game_template_path,
            sound_manager=self.sound_manager,
            audio_defaults=self.app_settings["audio"],
            voice_options=_narrator_voice_options(self.narration_player),
            on_sample_voice=self._play_narrator_sample,
            on_tts_settings_saved=self._persist_app_tts_settings,
            custom_voice_storage_path=self.app_paths.app_settings_path,
        )
        dialog.exec()

    def _choose_new_game_template_setup(
        self,
    ) -> tuple[bool, dict[str, Any] | None, str | None]:
        """Asks whether a new game should start blank or from a saved template."""

        choice = QMessageBox(self)
        choice.setWindowTitle("New Game")
        choice.setText("How would you like to start this new game?")
        scratch_button = choice.addButton(
            "Start From Scratch",
            QMessageBox.ButtonRole.AcceptRole,
        )
        template_button = choice.addButton(
            "Load Template",
            QMessageBox.ButtonRole.ActionRole,
        )
        cancel_button = choice.addButton(QMessageBox.StandardButton.Cancel)
        choice.exec()

        clicked_button = choice.clickedButton()

        if clicked_button == cancel_button:
            return False, None, None

        if clicked_button != template_button:
            return True, None, None

        templates = load_new_game_templates(
            self.app_paths.new_game_templates_path,
            legacy_template_path=self.app_paths.legacy_new_game_template_path,
        )

        if not templates:
            QMessageBox.information(
                self,
                "No Templates Found",
                "No saved new-game templates were found. Starting from scratch instead.",
            )
            return True, None, None

        template_names = [template.name for template in templates]
        selected_name, accepted = QInputDialog.getItem(
            self,
            "Load New Game Template",
            "Template:",
            template_names,
            0,
            False,
        )

        if not accepted:
            return False, None, None

        for template in templates:
            if template.name == selected_name:
                return (
                    True,
                    self._template_setup_with_available_title(
                        template.setup,
                        template_name=template.name,
                    ),
                    template.name,
                )

        return True, None, None

    def _prompt_for_modified_template_action(self, template_name: str) -> str | None:
        """Asks how changed wizard values should affect the loaded template."""

        choice = QMessageBox(self)
        choice.setWindowTitle("Template Changed")
        choice.setText(
            f'It looks like you made changes to the template "{template_name}".'
        )
        choice.setInformativeText("What would you like to do?")
        overwrite_button = choice.addButton(
            "Overwrite Existing Template",
            QMessageBox.ButtonRole.AcceptRole,
        )
        save_new_button = choice.addButton(
            "Save as New Template",
            QMessageBox.ButtonRole.ActionRole,
        )
        game_only_button = choice.addButton(
            "Create Game Only",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel_button = choice.addButton(QMessageBox.StandardButton.Cancel)
        choice.exec()
        clicked = choice.clickedButton()
        if clicked == overwrite_button:
            return "overwrite"
        if clicked == save_new_button:
            return "save_as_new"
        if clicked == game_only_button:
            return "game_only"
        if clicked == cancel_button:
            return None
        return None

    def _prompt_for_new_template_name(self, source_name: str) -> str | None:
        """Prompts until a non-empty, unused template name is supplied."""

        existing_names = {
            template.name.casefold()
            for template in load_new_game_templates(
                self.app_paths.new_game_templates_path,
                legacy_template_path=self.app_paths.legacy_new_game_template_path,
                normalize_setups=False,
            )
        }
        suggested_name = f"{source_name} Copy"
        suffix = 2
        while suggested_name.casefold() in existing_names:
            suggested_name = f"{source_name} Copy {suffix}"
            suffix += 1

        while True:
            template_name, accepted = QInputDialog.getText(
                self,
                "Save as New Template",
                "New template name:",
                QLineEdit.EchoMode.Normal,
                suggested_name,
            )
            if not accepted:
                return None
            clean_name = template_name.strip()
            if not clean_name:
                QMessageBox.warning(
                    self,
                    "Missing Template Name",
                    "Enter a template name.",
                )
                continue
            if clean_name.casefold() in existing_names:
                QMessageBox.warning(
                    self,
                    "Template Already Exists",
                    f'A template named "{clean_name}" already exists.',
                )
                continue
            return clean_name

    def _template_setup_with_available_title(
        self,
        setup: dict[str, Any],
        *,
        template_name: str = "",
    ) -> dict[str, Any]:
        """Returns template setup with a title that does not collide with saves."""

        template_setup = dict(setup)
        template_setup["title"] = _next_available_save_title(
            self.app_paths.saves_dir,
            template_name or str(template_setup.get("title", "")),
        )
        return template_setup

    def _prompt_for_duplicate_save_title(
        self,
        current_title: str,
        message: str,
    ) -> str | None:
        """Asks for a replacement save title after a duplicate title collision."""

        suggested_title = _next_available_save_title(
            self.app_paths.saves_dir,
            current_title,
        )

        while True:
            new_title, accepted = QInputDialog.getText(
                self,
                "Save Name Already Exists",
                f"{message}\n\nNew save name:",
                QLineEdit.EchoMode.Normal,
                suggested_title,
            )

            if not accepted:
                return None

            clean_title = new_title.strip()

            if clean_title:
                return clean_title

            QMessageBox.warning(
                self,
                "Missing Save Name",
                "Enter a save name before starting.",
            )

    def _create_new_game_from_setup(
        self,
        clean_setup: dict[str, Any],
        *,
        template_save_name: str | None = None,
        auto_save_template_if_available: bool = False,
    ) -> None:
        """Creates a new save from normalized setup and opens the shell."""

        clean_setup = self._normalize_new_game_setup_for_runtime(clean_setup)
        new_game_service = getattr(
            self,
            "new_game_service",
            NewGameService(
                tts_enabled=getattr(self, "tts_enabled", True),
                audio_defaults=getattr(self, "app_settings", {}).get("audio", {}),
            ),
        )
        repository = new_game_service.create_repository(
            self.app_paths.saves_dir,
            clean_setup,
            theme=self.menu_theme,
        )

        effective_template_name = template_save_name
        if effective_template_name is None and auto_save_template_if_available:
            effective_template_name = available_automatic_template_name(
                self.app_paths.new_game_templates_path,
                clean_setup,
                legacy_template_path=self.app_paths.legacy_new_game_template_path,
            )

        if effective_template_name and not save_new_game_template(
            self.app_paths.new_game_templates_path,
            {**clean_setup, "title": effective_template_name},
            template_name=effective_template_name,
        ):
            QMessageBox.warning(
                self,
                "Template Not Saved",
                "The game was created, but the template could not be saved.",
            )
        self.open_repository(repository, new_game=True)

        if not self.ai_enabled:
            return

        self.game_shell.story_screen.set_initial_generation_pending(True)
        self.game_shell.menu_button.setEnabled(False)
        self._show_new_game_progress_dialog()
        self._start_new_game_generation(repository, clean_setup)

    def _show_new_game_progress_dialog(self) -> None:
        """Shows the non-dismissible modal used during staged generation."""

        existing = self._new_game_progress_dialog
        if existing is not None:
            existing.finish()
        dialog = _NewGameGenerationProgressDialog(self)
        self._new_game_progress_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _start_new_game_generation(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
    ) -> None:
        """Starts initial world synthesis without blocking the Qt UI thread."""

        try:
            setup_packet = NewGameService.build_setup_packet(
                setup,
                valid_music_tracks=(
                    self.sound_manager.get_valid_track_names()
                    if self.sound_manager is not None
                    else []
                ),
                valid_sound_effect_tracks=(
                    self.sound_manager.get_valid_sound_effect_names()
                    if self.sound_manager is not None
                    else []
                ),
                valid_background_ambience_tracks=(
                    getattr(
                        self.sound_manager,
                        "get_valid_background_ambience_names",
                        lambda: [],
                    )()
                    if self.sound_manager is not None
                    else []
                ),
            )
        except Exception:
            LOGGER.exception("Failed to build the Gemini new-game request.")
            self.new_game_service.apply_fallback(repository, setup)
            self._complete_new_game_generation(repository)
            return

        thread = QThread(self)
        worker = _GeminiNewGameWorker(
            setup_packet,
            self.app_paths.gemini_api_key_path,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.completed.connect(self._handle_new_game_generation_result)
        worker.phase_changed.connect(self._handle_new_game_generation_phase)
        worker.configuration_error.connect(
            self._handle_new_game_generation_configuration_error
        )
        worker.request_failed.connect(self._handle_new_game_generation_request_failure)
        worker.failed.connect(self._handle_new_game_generation_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda thread=thread, worker=worker: self._clear_new_game_worker(
                thread,
                worker,
            )
        )

        self._pending_new_game_repository = repository
        self._pending_new_game_setup = dict(setup)
        self._new_game_thread = thread
        self._new_game_worker = worker
        thread.start()

    @Slot(object)
    def _handle_new_game_generation_result(self, result: Any) -> None:
        """Applies a completed initial-world response on the Qt UI thread."""

        repository = self._pending_new_game_repository
        setup = self._pending_new_game_setup

        if repository is None or setup is None:
            return

        try:
            available_voice_ids = list(
                _narrator_voice_options(self.narration_player).values()
            )
            self.new_game_service.commit_generated_world(
                repository,
                setup,
                result,
                available_voice_ids=available_voice_ids,
            )
        except Exception:
            LOGGER.exception("Failed to apply Gemini new-game synthesis.")
            self.new_game_service.apply_fallback(repository, setup)
        finally:
            self._complete_new_game_generation(repository)

    @Slot(str)
    def _handle_new_game_generation_phase(self, phase: str) -> None:
        """Shows the active staged-generation phase in the disabled menu button."""

        labels = {
            "setup_compilation": "Preparing new game...",
            "world_skeleton": "Generating world map...",
            "entities_mechanics": "Generating entities and mechanics...",
            "opening_prose": "Writing opening scene...",
            "targeted_repairs": "Checking generated content...",
            "final_commit": "Finalizing new game...",
        }
        self.game_shell.menu_button.setText(labels.get(phase, "Generating new game..."))
        dialog = self._new_game_progress_dialog
        if isinstance(dialog, _NewGameGenerationProgressDialog):
            dialog.update_phase(phase)

    @Slot(str)
    def _handle_new_game_generation_configuration_error(self, _message: str) -> None:
        """Uses the ordinary local opening when Gemini is not configured."""

        self._finish_failed_new_game_generation(temporary_failure=False)

    @Slot(str)
    def _handle_new_game_generation_request_failure(self, _message: str) -> None:
        """Uses a request-failure opening when Gemini is temporarily unavailable."""

        self._finish_failed_new_game_generation(temporary_failure=True)

    @Slot()
    def _handle_new_game_generation_failure(self) -> None:
        """Uses the ordinary local opening after an unexpected worker failure."""

        self._finish_failed_new_game_generation(temporary_failure=False)

    def _finish_failed_new_game_generation(self, *, temporary_failure: bool) -> None:
        """Applies the appropriate local fallback after a worker error."""

        repository = self._pending_new_game_repository
        setup = self._pending_new_game_setup

        if repository is None or setup is None:
            return

        try:
            self.new_game_service.apply_fallback(
                repository,
                setup,
                temporary_failure=temporary_failure,
            )
        finally:
            self._complete_new_game_generation(repository)

    def _apply_new_game_fallback(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
        *,
        temporary_failure: bool = False,
    ) -> None:
        """Writes a local opening after Gemini could not initialize a new game."""

        new_game_service = getattr(
            self,
            "new_game_service",
            NewGameService(
                tts_enabled=getattr(self, "tts_enabled", True),
                audio_defaults=getattr(self, "app_settings", {}).get("audio", {}),
            ),
        )
        new_game_service.apply_fallback(
            repository,
            setup,
            temporary_failure=temporary_failure,
        )

    def _apply_new_game_generation_result(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
        result: Any,
    ) -> None:
        """Compatibility adapter for callers that still use the old method."""

        self.new_game_service.commit_generated_world(
            repository,
            setup,
            result,
            available_voice_ids=list(
                _narrator_voice_options(self.narration_player).values()
            ),
        )

    def _complete_new_game_generation(self, repository: SaveRepository) -> None:
        """Refreshes the active game and prepares its opening visual batch."""

        self._pending_new_game_repository = None
        self._pending_new_game_setup = None

        active_repository = self.active_repository
        is_active = self._repository_matches_active(repository)
        LOGGER.info(
            "Completing new-game generation: repository=%s, active_repository=%s, "
            "same_object=%s, same_db_path=%s, inventory_items=%s, skills=%s.",
            repository.db_path,
            active_repository.db_path if active_repository is not None else None,
            active_repository is repository,
            is_active,
            len(repository.list_inventory_items()),
            len(repository.list_skills()),
        )
        if not is_active:
            self.game_shell.menu_button.setEnabled(True)
            self.game_shell.menu_button.setText("Main Menu")
            dialog = self._new_game_progress_dialog
            self._new_game_progress_dialog = None
            if isinstance(dialog, _NewGameGenerationProgressDialog):
                dialog.finish()
            return

        # Keep the opening hidden until every finalized initial visual request
        # has an explicit player-selected source. This prevents a new game
        # from starting paid image requests before the player chooses them.
        dialog = self._new_game_progress_dialog
        if isinstance(dialog, _NewGameGenerationProgressDialog):
            dialog.finish()
        try:
            requests = self.game_shell.visual_asset_coordinator.prepare_initial_batch(
                repository
            )
        except Exception:
            LOGGER.exception("Initial visual asset source selection could not start.")
            self.game_shell.visual_asset_coordinator.finish_initial_batch(repository)
            self._finish_initial_visual_generation(repository)
            return

        if not requests:
            self.game_shell.visual_asset_coordinator.finish_initial_batch(repository)
            self._finish_initial_visual_generation(repository)
            return

        self._show_new_game_image_source_dialog(repository, requests)

    def _show_new_game_image_source_dialog(
        self,
        repository: SaveRepository,
        requests: list[VisualAssetRequest],
    ) -> None:
        """Shows per-subject image choices after the initial world is finalized."""

        coordinator = self.game_shell.visual_asset_coordinator
        dialog = NewGameImageSourceDialog(
            requests,
            choose_file=lambda request, source_path: coordinator.upload_initial_image(
                repository,
                request,
                source_path,
            ),
            create_image=lambda request: coordinator.create_initial_image(
                repository,
                request,
            ),
            skip_image=lambda request: coordinator.skip_initial_image(
                repository,
                request,
            ),
            parent=self,
        )
        self._new_game_image_source_dialog = dialog
        coordinator.asset_status_changed.connect(dialog.set_asset_status)
        try:
            result = dialog.exec()
        finally:
            try:
                coordinator.asset_status_changed.disconnect(dialog.set_asset_status)
            except (RuntimeError, TypeError):
                pass
            self._new_game_image_source_dialog = None

        if result == QDialog.DialogCode.Accepted:
            coordinator.finish_initial_batch(repository)
            self._finish_initial_visual_generation(repository)

    def _repository_matches_active(self, repository: SaveRepository) -> bool:
        """Matches saves by identity or canonical path across repository wrappers."""

        active_repository = self.active_repository
        if active_repository is repository:
            return True
        if active_repository is None:
            return False
        return Path(active_repository.db_path).resolve() == Path(repository.db_path).resolve()

    def _reveal_initial_story(self, repository: SaveRepository) -> bool:
        """Shows and narrates the committed opening after initial images settle."""

        if not self._repository_matches_active(repository):
            return False

        self.game_shell.story_screen.set_initial_generation_pending(False)
        return self.game_shell.story_screen.narrate_latest_story(
            reveal_progressively=True,
        )

    def _finish_initial_visual_generation(self, repository: SaveRepository) -> None:
        """Reveals the completed new game and releases its progress modal."""

        if not self._repository_matches_active(repository):
            return
        try:
            self._reveal_initial_story(repository)
        except Exception:
            LOGGER.exception("Could not reveal the opening after new-game generation.")
        finally:
            # Always release the modal and menu lock, even if a screen refresh
            # or a narration/voice integration fails during finalization.
            self.game_shell.menu_button.setEnabled(True)
            self.game_shell.menu_button.setText("Main Menu")
            dialog = self._new_game_progress_dialog
            self._new_game_progress_dialog = None
            if isinstance(dialog, _NewGameGenerationProgressDialog):
                dialog.finish()

        try:
            # The initial coordinator already reconciled every request. Avoid
            # a second scan here, which could repeat a malformed-request error
            # after the user-visible generation has otherwise completed.
            self.game_shell.refresh_screens(scan_visual_assets=False)
        except Exception:
            LOGGER.exception("Could not refresh screens after new-game generation.")

    @Slot(object)
    def _handle_initial_visuals_ready(self, repository: SaveRepository) -> None:
        """Reveals the opening after the initial visual batch settles."""

        if self._repository_matches_active(repository):
            LOGGER.info(
                "Initial visual asset batch finished; revealing opening for %s.",
                repository.db_path,
            )
            self._finish_initial_visual_generation(repository)

    @Slot()
    def _clear_new_game_worker(
        self,
        thread: QThread | None = None,
        worker: QObject | None = None,
    ) -> None:
        """Drops references after the new-game request thread exits."""

        if thread is None or self._new_game_thread is thread:
            self._new_game_thread = None

        if worker is None or self._new_game_worker is worker:
            self._new_game_worker = None

    def _normalize_new_game_setup_for_runtime(self, setup: dict[str, Any]) -> dict[str, Any]:
        """Normalizes setup and disables narrator settings when TTS is unavailable."""

        new_game_service = getattr(
            self,
            "new_game_service",
            NewGameService(
                tts_enabled=getattr(self, "tts_enabled", True),
                audio_defaults=getattr(self, "app_settings", {}).get("audio", {}),
            ),
        )
        return new_game_service.normalize_setup(setup)

    def _apply_fallback_currency_if_needed(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
    ) -> None:
        """Stores a neutral currency when AI generation cannot run."""

        new_game_service = getattr(
            self,
            "new_game_service",
            NewGameService(
                tts_enabled=getattr(self, "tts_enabled", True),
                audio_defaults=getattr(self, "app_settings", {}).get("audio", {}),
            ),
        )
        new_game_service.apply_fallback_currency(repository, setup)

    def load_game_from_path(self, db_path: Path) -> None:
        """
        Loads an existing save.

        Args:
            db_path: Path to the save database.
        """

        resolved_path = Path(db_path).expanduser().resolve()
        LOGGER.info(
            "Load Game requested: path=%s, exists=%s, is_file=%s, current_page=%s, "
            "stack_index=%s.",
            resolved_path,
            resolved_path.exists(),
            resolved_path.is_file(),
            type(self.stack.currentWidget()).__name__
            if self.stack.currentWidget() is not None
            else None,
            self.stack.currentIndex(),
        )

        if not resolved_path.exists():
            LOGGER.error("Attempted to load missing save database: %s", db_path)
            QMessageBox.warning(self, "Load Failed", "That save file no longer exists.")
            self.main_menu.refresh_saves()
            return

        try:
            save_service = getattr(
                self,
                "save_game_service",
                SaveGameService(self.app_paths.saves_dir),
            )
            LOGGER.info(
                "Opening save repository through %s: %s.",
                type(save_service).__name__,
                resolved_path,
            )
            repository = save_service.load(resolved_path)
            LOGGER.info(
                "Save repository loaded: db_path=%s, inventory_items=%s, skills=%s.",
                repository.db_path,
                len(repository.list_inventory_items()),
                len(repository.list_skills()),
            )
        except Exception:
            LOGGER.exception("Failed to load save from %s.", resolved_path)
            QMessageBox.critical(self, "Load Failed", "Could not load that save.")
            return

        LOGGER.info("Passing loaded repository to open_repository: %s.", repository.db_path)
        self.open_repository(repository)
        LOGGER.info(
            "Load Game completed: active_repository=%s, current_page=%s, stack_index=%s, "
            "game_shell_visible=%s.",
            self.active_repository is repository,
            type(self.stack.currentWidget()).__name__
            if self.stack.currentWidget() is not None
            else None,
            self.stack.currentIndex(),
            self.game_shell.isVisible(),
        )

    def open_repository(
        self,
        repository: SaveRepository,
        *,
        new_game: bool = False,
    ) -> None:
        """
        Opens a repository in the game shell.

        Args:
            repository: Loaded save repository.
        """

        LOGGER.info(
            "Opening repository: db_path=%s, new_game=%s, previous_page=%s, "
            "previous_stack_index=%s.",
            repository.db_path,
            new_game,
            type(self.stack.currentWidget()).__name__
            if self.stack.currentWidget() is not None
            else None,
            self.stack.currentIndex(),
        )
        self.active_repository = repository
        self.game_shell.set_repository(
            repository,
            initially_hide_empty_tabs=new_game,
            defer_music_playback=new_game and self.ai_enabled,
            defer_visual_scan=new_game and self.ai_enabled,
        )
        self.game_shell.story_screen.set_initial_generation_pending(
            new_game and self.ai_enabled
        )
        self._apply_active_theme()
        self.stack.setCurrentWidget(self.game_shell)

        # Keep the load/new-game boundary explicit.  Audio preferences are
        # applied while binding the repository, so a successful music change
        # must never be mistaken for the page transition itself.
        if self.stack.currentWidget() is not self.game_shell:
            LOGGER.error(
                "Repository opened but the game shell was not selected; forcing "
                "the loaded-save page."
            )
            self.stack.setCurrentWidget(self.game_shell)

        LOGGER.info(
            "Bound save data to game shell: inventory_items=%s, skills=%s.",
            len(repository.list_inventory_items()),
            len(repository.list_skills()),
        )
        # A new-game commit or another queued repository notification can finish
        # between the initial screen binding and the first paint. Refresh once
        # on the next event-loop turn so data written at that boundary is visible
        # without requiring the player to change tabs.
        QTimer.singleShot(
            0,
            lambda repository=repository: self._refresh_open_repository(repository),
        )

        title = repository.get_meta("title", default="AI Adventure")
        self.setWindowTitle(f"{self.application_name} - {title}")

        LOGGER.info(
            "Opened save: %s; current_page=%s, stack_index=%s, shell_visible=%s.",
            repository.db_path,
            type(self.stack.currentWidget()).__name__
            if self.stack.currentWidget() is not None
            else None,
            self.stack.currentIndex(),
            self.game_shell.isVisible(),
        )

    def _refresh_open_repository(self, repository: SaveRepository) -> None:
        """Refreshes a still-active repository after its initial UI bind."""

        if (
            self.active_repository is repository
            and self.stack.currentWidget() is self.game_shell
        ):
            LOGGER.info(
                "Running post-bind repository refresh: db_path=%s, inventory_items=%s, "
                "skills=%s.",
                repository.db_path,
                len(repository.list_inventory_items()),
                len(repository.list_skills()),
            )
            self.game_shell.refresh_screens(scan_visual_assets=False)

    def return_to_menu(self) -> None:
        """Returns to the Main Menu."""

        self.active_repository = None
        self.game_shell.set_repository(None)
        self._apply_app_settings(self.app_settings, persist=False)
        self.main_menu.refresh_saves()
        self.stack.setCurrentWidget(self.main_menu)
        self.setWindowTitle(self.application_name)

    def _apply_active_theme(self) -> None:
        """Applies the theme saved for the currently loaded adventure."""

        if self.active_repository is None:
            self._apply_app_settings(self.app_settings, persist=False)
            return

        self.menu_theme = _normalize_theme_name(
            self.active_repository.get_setting("theme", "Light")
        )
        self.app_settings = normalize_app_settings(
            {
                **self.app_settings,
                "theme": self.menu_theme,
            },
            fallback_theme=self.menu_theme,
            tts_enabled=self.tts_enabled,
        )
        save_app_settings(self.app_paths.app_settings_path, self.app_settings)
        apply_application_theme(self.menu_theme, self.app_settings["appearance"])

    def _apply_app_settings(self, settings: dict[str, Any], *, persist: bool) -> None:
        """Applies app-level settings used when no save is active."""

        self.app_settings = normalize_app_settings(
            settings,
            fallback_theme=self.menu_theme,
            tts_enabled=self.tts_enabled,
        )
        self.menu_theme = _normalize_theme_name(self.app_settings["theme"])

        if persist:
            save_app_settings(self.app_paths.app_settings_path, self.app_settings)

        apply_application_theme(self.menu_theme, self.app_settings["appearance"])
        self._apply_menu_audio_settings()

    def _persist_app_tts_settings(self, audio_settings: dict[str, Any]) -> None:
        """Persists app-level TTS defaults from the New Game wizard."""

        self._apply_app_settings(
            {
                **self.app_settings,
                "audio": {
                    **self.app_settings["audio"],
                    **audio_settings,
                },
            },
            persist=True,
        )

    def _apply_menu_audio_settings(self) -> None:
        """Applies app-level audio settings while the Main Menu is active."""

        audio = self.app_settings["audio"]

        if self.sound_manager is not None:
            self.sound_manager.set_music_volume(audio["music_volume"])
            self.sound_manager.set_music_enabled(audio["music_enabled"])
            self.sound_manager.set_sound_effects_volume(audio["sound_effects_volume"])
            self.sound_manager.set_sound_effects_enabled(audio["sound_effects_enabled"])
            if hasattr(self.sound_manager, "set_background_ambience_volume"):
                self.sound_manager.set_background_ambience_volume(
                    audio["background_ambience_volume"]
                )
            if hasattr(self.sound_manager, "set_background_ambience_enabled"):
                self.sound_manager.set_background_ambience_enabled(
                    audio["background_ambience_enabled"]
                )

            if not audio["music_enabled"]:
                self.sound_manager.stop_music(clear_current=False)
            # Background ambience belongs to the active save's scene. It must not
            # continue looping while the app is showing the save-independent menu.
            if hasattr(self.sound_manager, "stop_background_ambience"):
                self.sound_manager.stop_background_ambience(clear_current=False)

        if self.narration_player is not None:
            self.narration_player.set_volume(audio["tts_volume"])
            if hasattr(self.narration_player, "set_speed"):
                self.narration_player.set_speed(audio["tts_speed"])
            if hasattr(self.narration_player, "set_voice"):
                self.narration_player.set_voice(active_voice_spec_from_audio(audio))
            self.narration_player.set_enabled(audio["narrator_enabled"])

    def _play_narrator_sample(
        self,
        voice: str,
        volume: int,
        speed: int = DEFAULT_TTS_SPEED_PERCENT,
    ) -> bool:
        """Plays a local narrator voice sample."""

        if self.narration_player is None or not hasattr(self.narration_player, "play_sample"):
            return False

        return bool(
            self.narration_player.play_sample(
                voice=normalize_narrator_voice_spec(voice),
                volume=volume,
                speed=speed,
            )
        )

    def _latest_saved_theme(self) -> str:
        """Reads the most recent save's theme for the Main Menu."""

        save_service = getattr(
            self,
            "save_game_service",
            SaveGameService(self.app_paths.saves_dir),
        )
        return _normalize_theme_name(save_service.latest_theme())
