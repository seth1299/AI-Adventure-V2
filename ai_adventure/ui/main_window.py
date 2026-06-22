from __future__ import annotations

import re
import logging
import importlib
import random
from pathlib import Path
from typing import Any, Callable, Protocol

from PySide6.QtCore import (
    QEvent,
    QObject,
    QStringListModel,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ai_adventure.alchemy.ingredients import (
    COMMON_MEASUREMENT_UNITS,
    CRAFTING_INGREDIENT_CATEGORY_NAMES,
    format_recipe_ingredients,
    is_crafting_ingredient_category,
    normalize_recipe_ingredient,
)
from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.features import is_tts_enabled
from ai_adventure.app.user_settings import (
    load_app_settings,
    normalize_app_settings,
    save_app_settings,
)
from ai_adventure.ai.gemini_service import (
    GeminiConfigurationError,
    GeminiNarrationService,
    format_story_message,
)
from ai_adventure.audio.narration import NarrationPlayer
from ai_adventure.audio.sound_manager import SoundManager, prepare_sound_directory
from ai_adventure.audio.tts_settings import (
    DEFAULT_TTS_SPEED_PERCENT,
    active_voice_spec_from_audio,
    merge_custom_voices,
    normalize_custom_voices,
    normalize_narrator_voice_spec,
    normalize_tts_audio_fields,
    normalize_tts_speed_percent,
    normalize_tts_voice_mode,
    normalize_voice_blend,
    voice_display_name,
)
from ai_adventure.audio.voices import (
    DEFAULT_NARRATOR_VOICE,
    available_narrator_voices,
    normalize_narrator_voice,
)
from ai_adventure.calendar_system import (
    DEFAULT_CALENDAR_SETTINGS,
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    build_month_grid,
    resolve_starting_elapsed_minutes,
)
from ai_adventure.combat import (
    BODY_PARTS,
    DEFAULT_BASE_ARMOR_RATING,
    DEFAULT_PLAYER_MAX_HEALTH,
    DEFAULT_UNARMED_DAMAGE,
    EQUIPMENT_SLOTS,
    armor_rating_from_equipment,
    combat_team_defeated,
    empty_equipment,
    equipped_weapon_damage,
    item_is_valid_for_slot,
    item_metadata,
    next_living_index,
    normalize_combat_state,
    normalize_damage_expression,
    normalize_equipment,
    roll_damage_expression,
)
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.currency import (
    FALLBACK_CURRENCY_DENOMINATIONS,
    describe_currency_denominations,
    format_currency_amount,
)
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.new_game_setup import (
    GREGORIAN_CALENDAR_SETTINGS,
    SKILL_LEVEL_PLAN,
    STARTER_INVENTORY_MIN_ITEMS,
    ai_generated_calendar_settings_or_fallback,
    build_new_game_setup_packet,
    describe_economy_examples,
    fallback_introductory_message,
    fallback_world_summary,
    normalize_economy_examples,
    normalize_new_game_setup,
    parse_starter_items_text,
)
from ai_adventure.narration_preferences import (
    DEFAULT_NARRATION_STYLE,
    DEFAULT_NARRATION_TENSE,
    NARRATION_STYLE_OPTIONS,
    NARRATION_TENSE_OPTIONS,
    normalize_narration_preferences,
)
from ai_adventure.new_game_templates import (
    NewGameTemplate,
    delete_new_game_template,
    load_new_game_templates,
    save_new_game_template,
)
from ai_adventure.persistence.save_repository import (
    DuplicateSaveTitleError,
    SaveRepository,
    SaveSummary,
)


LOGGER = logging.getLogger(__name__)
GM_THINKING_TEXT = "GM is thinking..."
STORY_REVEAL_STALL_TIMEOUT_MS = 8000
CONTINUE_STORY_INSTRUCTION = (
    "Continue the previous story response as though it had been longer originally. "
    "Do not treat this as a new player action. Do not invent new player-character "
    "dialogue or choices. Add concrete scene detail, NPC reaction, immediate "
    "outcome, obstacles, discoveries, or consequences already implied by the "
    "previous action."
)
TABLE_INLINE_EDITOR_HEIGHT = 30
TABLE_INLINE_EDITOR_MIN_WIDTH = 132
TABLE_INLINE_BUTTON_MIN_WIDTH = 96
STARTER_ITEM_COLUMN_WIDTHS = (140, 132, 140, 220, 132, 100)
CURRENCY_COLUMN_WIDTHS = (150, 160, 132, 100)
ECONOMY_EXAMPLE_COLUMN_WIDTHS = (220, 132, 100)
THEME_NAMES = {"Light", "Dark"}
SKILL_LEVEL_DESCRIPTIONS = {
    5: "Signature expertise - the character's strongest, defining capability.",
    4: "Expert - a major specialty the character can rely on often.",
    3: "Skilled - solid professional competence in meaningful situations.",
    2: "Trained - useful practice, but not a primary specialty.",
    1: "Familiar - basic exposure that can still matter in the right moment.",
}


def apply_application_theme(theme: str) -> None:
    """Applies the selected app-wide theme to the active QApplication."""

    app = QApplication.instance()

    if not isinstance(app, QApplication):
        return

    clean_theme = _normalize_theme_name(theme)

    if clean_theme == "Dark":
        app.setPalette(_dark_theme_palette())
        app.setStyleSheet(_dark_theme_stylesheet())
        return

    if clean_theme == "Light":
        app.setPalette(_light_theme_palette())
        app.setStyleSheet(_light_theme_stylesheet())
        return

    app.setPalette(_light_theme_palette())
    app.setStyleSheet(_light_theme_stylesheet())


class _NoCellFocusDelegate(QStyledItemDelegate):
    """Draws selected table cells without Qt's per-cell focus marker."""

    def paint(self, painter, option, index) -> None:
        clean_option = QStyleOptionViewItem(option)
        clean_option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, clean_option, index)


def _use_soft_table_selection(table: QTableWidget) -> None:
    """Keeps table selection while hiding the gaudy per-cell focus cursor."""

    table.setItemDelegate(_NoCellFocusDelegate(table))


def _table_item(text: Any, sort_value: Any | None = None) -> QTableWidgetItem:
    """Builds a read-only table item with an optional hidden sort value."""

    item = QTableWidgetItem(str(text))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    if sort_value is not None:
        item.setData(Qt.ItemDataRole.UserRole, sort_value)

    return item


def _enable_table_sorting(table: QTableWidget, on_section_clicked) -> None:
    """Makes a data table sortable by clicking its column headers."""

    _use_soft_table_selection(table)
    table.setSortingEnabled(False)
    header = table.horizontalHeader()
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(True)
    header.sectionClicked.connect(on_section_clicked)


def _update_sort_state(
    table: QTableWidget,
    current_column: int,
    current_order: Qt.SortOrder,
    clicked_column: int,
) -> tuple[int, Qt.SortOrder]:
    """Returns the next sort column/order and updates the header indicator."""

    if clicked_column == current_column:
        next_order = (
            Qt.SortOrder.DescendingOrder
            if current_order == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
    else:
        next_order = Qt.SortOrder.AscendingOrder

    table.horizontalHeader().setSortIndicator(clicked_column, next_order)
    return clicked_column, next_order


def _sort_descending(order: Qt.SortOrder) -> bool:
    """Returns True when table data should be sorted descending."""

    return order == Qt.SortOrder.DescendingOrder


class RefreshableScreen(Protocol):
    """Protocol for screens that can reload their data from the save repository."""

    def refresh(self) -> None:
        """Refreshes visible screen data."""
        ...


class RepositoryBackedWidget(QWidget):
    """
    Base widget for screens that need save access.

    This keeps every screen from directly knowing how save loading works.
    """

    def __init__(self) -> None:
        super().__init__()
        self._repository: SaveRepository | None = None
        self.on_repository_changed: Callable[["RepositoryBackedWidget"], None] | None = None

    def set_repository(self, repository: SaveRepository | None) -> None:
        """
        Sets the active save repository.

        Args:
            repository: Active save repository, or None when no save is loaded.
        """

        self._repository = repository
        self.refresh()

    def repository(self) -> SaveRepository | None:
        """
        Gets the active save repository.

        Returns:
            Active repository, or None if no save is loaded.
        """

        return self._repository

    def refresh(self) -> None:
        """Refreshes screen data. Subclasses may override this."""

    def notify_repository_changed(self) -> None:
        """Notifies the shell that saved data changed and other tabs should refresh."""

        if self.on_repository_changed is not None:
            self.on_repository_changed(self)


class _GeminiStoryWorker(QObject):
    """Runs one Gemini story request away from the Qt UI thread."""

    completed = Signal(object)
    configuration_error = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(self, context_packet: dict[str, Any]) -> None:
        super().__init__()
        self._context_packet = context_packet

    @Slot()
    def run(self) -> None:
        """Generates one story response and emits the result on completion."""

        try:
            result = GeminiNarrationService().generate_story_response(
                self._context_packet
            )
        except GeminiConfigurationError as error:
            LOGGER.warning("Gemini narration skipped: %s", error)
            self.configuration_error.emit(str(error))
        except Exception:
            LOGGER.exception("Gemini narration request failed.")
            self.failed.emit()
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()


class _GeminiSkillCheckPlanWorker(QObject):
    """Runs one lightweight skill-check planning request away from the UI thread."""

    completed = Signal(object)
    configuration_error = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(self, context_packet: dict[str, Any]) -> None:
        super().__init__()
        self._context_packet = context_packet

    @Slot()
    def run(self) -> None:
        """Generates one pre-narration skill-check plan."""

        try:
            result = GeminiNarrationService().plan_story_skill_checks(
                self._context_packet
            )
        except GeminiConfigurationError as error:
            LOGGER.warning("Gemini skill-check planning skipped: %s", error)
            self.configuration_error.emit(str(error))
        except Exception:
            LOGGER.exception("Gemini skill-check planning request failed.")
            self.failed.emit()
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()


def _create_narration_player(app_paths: AppPaths) -> NarrationPlayer | None:
    """Creates the narration player only when TTS support is enabled."""

    if not is_tts_enabled():
        LOGGER.info("TTS narration disabled by application configuration.")
        return None

    try:
        tts_module = importlib.import_module("ai_adventure.audio.tts.tts_manager")
        create_tts_manager = getattr(tts_module, "create_tts_manager")
    except Exception as error:
        LOGGER.warning("Narrator is unavailable because TTS could not be loaded: %s", error)
        return None

    return NarrationPlayer(
        create_tts_manager(
            model_path=app_paths.kokoro_model_path,
            voices_path=app_paths.kokoro_voices_path,
            output_directory=app_paths.tts_output_dir,
        )
    )


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
        self.active_repository: SaveRepository | None = None
        self.tts_enabled = is_tts_enabled()
        self.app_settings = load_app_settings(
            self.app_paths.app_settings_path,
            fallback_theme=self._latest_saved_theme(),
            tts_enabled=self.tts_enabled,
        )
        self.menu_theme = _normalize_theme_name(self.app_settings["theme"])
        self.sound_manager = SoundManager(prepare_sound_directory(self.app_paths))
        self.narration_player = _create_narration_player(self.app_paths)

        self.setWindowTitle("AI Adventure")
        self._set_app_icon()
        self.resize(1100, 750)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.main_menu = MainMenuScreen(
            saves_dir=self.app_paths.saves_dir,
            on_new_game=self.start_new_game_wizard,
            on_load_game=self.load_game_from_path,
            on_settings=self.open_main_menu_settings,
            on_templates=self.open_new_game_templates,
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

        should_continue, template_setup = self._choose_new_game_template_setup()

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
        )

        while True:
            if wizard.exec() != QDialog.DialogCode.Accepted:
                return

            while True:
                clean_setup = wizard.build_setup()

                try:
                    self._create_new_game_from_setup(clean_setup)
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

    def open_main_menu_settings(self) -> None:
        """Opens app-level settings from the Main Menu."""

        dialog = MainMenuSettingsDialog(
            self,
            settings=self.app_settings,
            tts_enabled=self.tts_enabled,
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
        )
        dialog.exec()

    def _choose_new_game_template_setup(self) -> tuple[bool, dict[str, Any] | None]:
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
            return False, None

        if clicked_button != template_button:
            return True, None

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
            return True, None

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
            return False, None

        for template in templates:
            if template.name == selected_name:
                return True, self._template_setup_with_available_title(template.setup)

        return True, None

    def _template_setup_with_available_title(
        self,
        setup: dict[str, Any],
    ) -> dict[str, Any]:
        """Returns template setup with a title that does not collide with saves."""

        template_setup = dict(setup)
        template_setup["title"] = _next_available_save_title(
            self.app_paths.saves_dir,
            str(template_setup.get("title", "")),
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

    def create_new_game(self, setup: dict[str, Any]) -> bool:
        """
        Creates a new save and opens it.

        Args:
            setup: New-game wizard setup dictionary.

        Returns:
            True when the save was created and opened.
        """

        clean_setup = self._normalize_new_game_setup_for_runtime(setup)

        try:
            self._create_new_game_from_setup(clean_setup)
        except DuplicateSaveTitleError as error:
            QMessageBox.warning(self, "Save Name Already Exists", str(error))
            return False
        except Exception:
            LOGGER.exception("Failed to create new game.")
            QMessageBox.critical(self, "New Game Failed", "Could not create a new game.")
            return False

        return True

    def _create_new_game_from_setup(self, clean_setup: dict[str, Any]) -> None:
        """Creates a new save from normalized setup and opens the shell."""

        clean_setup = self._normalize_new_game_setup_for_runtime(clean_setup)

        repository = SaveRepository.create_new_save(
            self.app_paths.saves_dir,
            clean_setup["title"],
            setup=clean_setup,
        )
        repository.set_setting("theme", self.menu_theme)

        save_new_game_template(self.app_paths.new_game_templates_path, clean_setup)
        self.open_repository(repository)
        self.game_shell.story_screen.set_initial_generation_pending(True)
        QApplication.processEvents()
        QTimer.singleShot(
            0,
            lambda: self._finish_new_game_generation(repository, clean_setup),
        )

    def _finish_new_game_generation(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
    ) -> None:
        """Completes AI world synthesis after the fresh save is visible."""

        if self.active_repository is not repository:
            return

        try:
            self._synthesize_new_game_world(repository, setup)
            self.game_shell.refresh_screens()
            self.game_shell.story_screen.narrate_latest_story()
        finally:
            self.game_shell.story_screen.set_initial_generation_pending(False)

    def _synthesize_new_game_world(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
    ) -> None:
        """Uses Gemini to synthesize the initial world and opening scene."""

        try:
            result = GeminiNarrationService().generate_new_game_world(
                build_new_game_setup_packet(
                    setup,
                    valid_music_tracks=self.sound_manager.get_valid_track_names(),
                )
            )
        except GeminiConfigurationError as error:
            LOGGER.warning("Gemini new-game synthesis skipped: %s", error)
            self._apply_fallback_currency_if_needed(repository, setup)
            repository.set_world_summary(fallback_world_summary(setup))
            repository.append_history("story", fallback_introductory_message(setup))
            return
        except Exception:
            LOGGER.exception("Gemini new-game synthesis failed.")
            self._apply_fallback_currency_if_needed(repository, setup)
            repository.set_world_summary(fallback_world_summary(setup))
            repository.append_history("story", fallback_introductory_message(setup))
            return

        self._apply_new_game_ai_state(repository, setup, result)
        repository.set_world_summary(
            _preserve_player_character_text(
                result.world_summary,
                setup,
                result.finalized_character,
            )
        )
        repository.set_world_lore(
            _preserve_player_character_text(
                result.world_lore,
                setup,
                result.finalized_character,
            )
        )
        repository.append_history(
            "story",
            _preserve_player_character_text(
                result.introductory_message,
                setup,
                result.finalized_character,
            ),
        )

        if result.suggested_events:
            event_results = EventApplier(repository).apply_events(result.suggested_events)
            applied_count = sum(
                1 for event_result in event_results if event_result.status == "applied"
            )
            skipped_count = len(event_results) - applied_count
            LOGGER.info(
                "Applied %s new-game event(s); skipped %s.",
                applied_count,
                skipped_count,
            )

    def _normalize_new_game_setup_for_runtime(self, setup: dict[str, Any]) -> dict[str, Any]:
        """Normalizes setup and disables narrator settings when TTS is unavailable."""

        raw_setup = dict(setup) if isinstance(setup, dict) else {}
        audio = dict(self.app_settings["audio"])

        if isinstance(raw_setup.get("audio"), dict):
            audio.update(raw_setup["audio"])

        raw_setup["audio"] = audio
        clean_setup = normalize_new_game_setup(raw_setup)

        if self.tts_enabled:
            return clean_setup

        audio = dict(clean_setup["audio"])
        audio.update(normalize_tts_audio_fields(audio, tts_enabled=False))
        audio["tts_voice"] = DEFAULT_NARRATOR_VOICE
        audio["tts_voice_mode"] = "preset"

        clean_setup = dict(clean_setup)
        clean_setup["audio"] = audio
        return clean_setup

    def _apply_new_game_ai_state(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
        result,
    ) -> None:
        """Persists AI-finalized new-game character, skills, and start location."""

        if result.start_location:
            repository.set_state_value("location", result.start_location)

        setup_calendar = setup.get("calendar", {})
        if (
            isinstance(setup_calendar, dict)
            and bool(setup_calendar.get("ai_generated", False))
        ):
            repository.set_calendar_settings(
                ai_generated_calendar_settings_or_fallback(
                    getattr(result, "calendar_settings", {})
                )
            )

        if result.starting_calendar:
            elapsed_minutes = resolve_starting_elapsed_minutes(
                result.starting_calendar,
                repository.get_calendar_settings(),
                default_elapsed_minutes=DEFAULT_START_ELAPSED_MINUTES,
            )
            calendar_snapshot = build_calendar_snapshot(
                elapsed_minutes,
                repository.get_calendar_settings(),
            )
            repository.set_state_value("elapsed_minutes", str(elapsed_minutes))
            repository.set_state_value("time", calendar_snapshot["display_label"])

        if result.start_weather:
            repository.set_state_value("weather", result.start_weather)

        if result.finalized_starting_currency_balance_base_units is not None:
            repository.set_state_value(
                "currency.balance",
                str(result.finalized_starting_currency_balance_base_units),
            )

        if not setup.get("currency_denominations"):
            if result.finalized_currency_denominations:
                repository.set_currency_denominations(result.finalized_currency_denominations)
                repository.set_setting(
                    "currency.description",
                    result.finalized_currency_description
                    or describe_currency_denominations(
                        result.finalized_currency_denominations,
                        fallback_denominations=[],
                    ),
                )
            else:
                LOGGER.warning("AI new-game setup omitted generated currency denominations.")
                self._apply_fallback_currency_if_needed(repository, setup)

        if result.selected_genre:
            repository.set_setting("world.genre", result.selected_genre)
            repository.set_setting(
                "ai.additional_context",
                _append_ai_context_line(
                    str(repository.get_setting("ai.additional_context", "")),
                    f"Selected genre: {result.selected_genre}",
                ),
            )

        character = _preserved_player_character_fields(
            setup,
            result.finalized_character,
        )

        if character:
            if character.get("name"):
                repository.set_setting("player_name", character["name"])
            if character.get("appearance"):
                repository.set_setting("player.appearance", character["appearance"])
            if character.get("backstory"):
                repository.set_setting("player.backstory", character["backstory"])
            if character.get("notes"):
                repository.set_setting("player.notes", character["notes"])

        if _ai_skills_match_setup(result.finalized_skills, setup.get("skills", [])):
            repository.replace_skills(_deduplicated_ai_skills(result.finalized_skills))
        elif result.finalized_skills:
            LOGGER.warning(
                "Skipped AI-finalized skills because they did not match the starting skill plan."
            )

        finalized_starter_items = _starter_items_for_save(
            result.finalized_starter_items,
            setup,
        )

        if finalized_starter_items:
            repository.replace_inventory_items(finalized_starter_items)

    def _apply_fallback_currency_if_needed(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
    ) -> None:
        """Stores a neutral currency when AI generation cannot run."""

        if setup.get("currency_denominations"):
            return

        repository.set_currency_denominations(FALLBACK_CURRENCY_DENOMINATIONS)
        repository.set_setting(
            "currency.description",
            describe_currency_denominations(
                FALLBACK_CURRENCY_DENOMINATIONS,
                fallback_denominations=[],
            ),
        )

    def load_game_from_path(self, db_path: Path) -> None:
        """
        Loads an existing save.

        Args:
            db_path: Path to the save database.
        """

        if not db_path.exists():
            LOGGER.error("Attempted to load missing save database: %s", db_path)
            QMessageBox.warning(self, "Load Failed", "That save file no longer exists.")
            self.main_menu.refresh_saves()
            return

        try:
            repository = SaveRepository(db_path)
        except Exception:
            LOGGER.exception("Failed to load save from %s.", db_path)
            QMessageBox.critical(self, "Load Failed", "Could not load that save.")
            return

        self.open_repository(repository)

    def open_repository(self, repository: SaveRepository) -> None:
        """
        Opens a repository in the game shell.

        Args:
            repository: Loaded save repository.
        """

        self.active_repository = repository
        self.game_shell.set_repository(repository)
        self._apply_active_theme()
        self.stack.setCurrentWidget(self.game_shell)

        title = repository.get_meta("title", default="AI Adventure")
        self.setWindowTitle(f"AI Adventure - {title}")

        LOGGER.info("Opened save: %s", repository.db_path)

    def return_to_menu(self) -> None:
        """Returns to the Main Menu."""

        self.active_repository = None
        self.game_shell.set_repository(None)
        self._apply_app_settings(self.app_settings, persist=False)
        self.main_menu.refresh_saves()
        self.stack.setCurrentWidget(self.main_menu)
        self.setWindowTitle("AI Adventure")

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
        apply_application_theme(self.menu_theme)

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

        apply_application_theme(self.menu_theme)
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

            if not audio["music_enabled"]:
                self.sound_manager.stop_music(clear_current=False)

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

        for summary in SaveRepository.list_saves(self.app_paths.saves_dir):
            try:
                repository = SaveRepository(summary.db_path)
            except Exception:
                LOGGER.exception("Failed to read theme from save: %s", summary.db_path)
                continue

            return _normalize_theme_name(repository.get_setting("theme", "Light"))

        return "Light"


class CustomVoiceDialog(QDialog):
    """Dedicated manager for loading, editing, and saving custom narrator voices."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        audio_settings: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: Callable[[str, int, int], bool] | None = None,
        storage_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        self.storage_path = Path(storage_path) if storage_path is not None else None
        self._base_audio = normalize_tts_audio_fields(audio_settings or {})
        self._syncing_blend_sliders = False
        self._loading_controls = False
        self._use_blend = normalize_tts_voice_mode(
            self._base_audio["tts_voice_mode"]
        ) == "blend"
        self.custom_voice_library_changed = False
        self.custom_voices: list[dict[str, Any]] = []
        self.current_blend = normalize_voice_blend({})
        self.loaded_custom_voice_name: str | None = None

        self.setWindowTitle("Custom Voices")
        self.resize(620, 520)

        self.custom_voice_combo = QComboBox()
        self.custom_voice_combo.currentIndexChanged.connect(lambda _index: self._sync_action_states())
        self.load_custom_voice_button = QPushButton("Load")
        self.load_custom_voice_button.clicked.connect(self._load_selected_custom_voice)

        self.current_voice_label = QLabel("Unsaved Custom Voice")
        self.current_voice_label.setWordWrap(True)

        self.tts_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.tts_volume_slider.setRange(0, 100)
        self.tts_volume_slider.setValue(90)
        self.tts_volume_label = QLabel(f"{self.tts_volume_slider.value()}%")
        self.tts_volume_slider.valueChanged.connect(
            lambda value: self.tts_volume_label.setText(f"{value}%")
        )
        self.tts_volume_slider.valueChanged.connect(lambda _value: self._mark_blend_in_use())

        self.tts_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.tts_speed_slider.setRange(50, 200)
        self.tts_speed_slider.setValue(DEFAULT_TTS_SPEED_PERCENT)
        self.tts_speed_label = QLabel(f"{self.tts_speed_slider.value()}%")
        self.tts_speed_slider.valueChanged.connect(
            lambda value: self.tts_speed_label.setText(f"{value}%")
        )
        self.tts_speed_slider.valueChanged.connect(lambda _value: self._mark_blend_in_use())

        self.voice_a_combo = QComboBox()
        self.voice_b_combo = QComboBox()
        _populate_narrator_voice_combo(
            self.voice_a_combo,
            DEFAULT_NARRATOR_VOICE,
            voice_options=self.voice_options,
        )
        _populate_narrator_voice_combo(
            self.voice_b_combo,
            "am_echo",
            voice_options=self.voice_options,
        )
        self.voice_a_combo.currentIndexChanged.connect(lambda _index: self._mark_blend_in_use())
        self.voice_b_combo.currentIndexChanged.connect(lambda _index: self._mark_blend_in_use())

        self.voice_a_weight_slider = QSlider(Qt.Orientation.Horizontal)
        self.voice_a_weight_slider.setRange(0, 100)
        self.voice_a_weight_slider.setValue(50)
        self.voice_a_weight_label = QLabel(f"{self.voice_a_weight_slider.value()}%")
        self.voice_a_weight_slider.valueChanged.connect(self._handle_voice_a_weight_changed)

        self.voice_b_weight_slider = QSlider(Qt.Orientation.Horizontal)
        self.voice_b_weight_slider.setRange(0, 100)
        self.voice_b_weight_slider.setValue(50)
        self.voice_b_weight_label = QLabel(f"{self.voice_b_weight_slider.value()}%")
        self.voice_b_weight_slider.valueChanged.connect(self._handle_voice_b_weight_changed)

        self.sample_voice_button = QPushButton("Sample Voice")
        self.sample_voice_button.clicked.connect(self._sample_voice)
        self.save_custom_voice_button = QPushButton("Save")
        self.save_custom_voice_button.clicked.connect(self._save_current_custom_voice)
        self.save_custom_voice_as_button = QPushButton("Save As...")
        self.save_custom_voice_as_button.clicked.connect(self._save_current_custom_voice_as)
        self.rename_custom_voice_button = QPushButton("Rename")
        self.rename_custom_voice_button.clicked.connect(self._rename_current_custom_voice)

        self.tts_volume_row = _slider_row(self.tts_volume_slider, self.tts_volume_label)
        self.tts_speed_row = _slider_row(self.tts_speed_slider, self.tts_speed_label)
        self.voice_a_weight_row = _slider_row(
            self.voice_a_weight_slider,
            self.voice_a_weight_label,
        )
        self.voice_b_weight_row = _slider_row(
            self.voice_b_weight_slider,
            self.voice_b_weight_label,
        )
        self.library_row = _button_row(self.custom_voice_combo, self.load_custom_voice_button)
        self.action_row = _button_row(
            self.save_custom_voice_button,
            self.save_custom_voice_as_button,
            self.rename_custom_voice_button,
            self.sample_voice_button,
        )

        form = QFormLayout()
        form.addRow("Saved Voice:", self.library_row)
        form.addRow("Editing:", self.current_voice_label)
        form.addRow("Volume:", self.tts_volume_row)
        form.addRow("Speed:", self.tts_speed_row)
        form.addRow("Voice A:", self.voice_a_combo)
        form.addRow("Voice A Blend:", self.voice_a_weight_row)
        form.addRow("Voice B:", self.voice_b_combo)
        form.addRow("Voice B Blend:", self.voice_b_weight_row)
        form.addRow("", self.action_row)

        if self.storage_path is not None:
            self.storage_label = QLabel(str(self.storage_path))
            self.storage_label.setWordWrap(True)
            form.addRow("Storage:", self.storage_label)
        else:
            self.storage_label = None

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(close_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addStretch()
        layout.addLayout(close_row)
        self.setLayout(layout)
        self.load_audio_settings(audio_settings or {})

    def load_audio_settings(self, audio_settings: dict[str, Any]) -> None:
        """Loads normalized audio settings into the custom voice editor."""

        self._base_audio = normalize_tts_audio_fields(audio_settings)
        blend = normalize_voice_blend(self._base_audio["tts_voice_blend"])
        self._loading_controls = True

        try:
            self.custom_voices = normalize_custom_voices(self._base_audio["tts_custom_voices"])
            self.current_blend = blend
            self.loaded_custom_voice_name = self._saved_voice_name_for(blend)
            self._populate_custom_voice_combo(selected_name=self.loaded_custom_voice_name)
            self._apply_blend_to_controls(blend)
        finally:
            self._loading_controls = False

        self._sync_loaded_voice_label()
        self._sync_action_states()

    def build_audio_settings(self) -> dict[str, Any]:
        """Builds normalized TTS settings from the current custom voice editor state."""

        blend_name = self.loaded_custom_voice_name or str(self.current_blend["name"])
        self.current_blend = self._blend_from_controls(name=blend_name)
        audio = {
            **self._base_audio,
            "tts_volume": self.tts_volume_slider.value(),
            "tts_speed": self.tts_speed_slider.value(),
            "tts_voice_mode": "blend" if self._use_blend else self._base_audio["tts_voice_mode"],
            "tts_voice_blend": self.current_blend,
            "tts_custom_voices": self.custom_voices,
        }
        return normalize_tts_audio_fields(audio)

    def _blend_from_controls(self, *, name: str) -> dict[str, Any]:
        """Returns a normalized blend using the explicit supplied name."""

        return normalize_voice_blend(
            {
                "name": name,
                "voice_a": _combo_current_data_text(self.voice_a_combo, DEFAULT_NARRATOR_VOICE),
                "voice_b": _combo_current_data_text(self.voice_b_combo, "am_echo"),
                "voice_a_weight": self.voice_a_weight_slider.value(),
                "tts_volume": self.tts_volume_slider.value(),
                "tts_speed": self.tts_speed_slider.value(),
            }
        )

    def _apply_blend_to_controls(self, blend: dict[str, Any]) -> None:
        """Sets blend controls without changing the saved-name state."""

        clean_blend = normalize_voice_blend(blend)
        self.current_blend = clean_blend
        _set_combo_to_data(self.voice_a_combo, clean_blend["voice_a"])
        _set_combo_to_data(self.voice_b_combo, clean_blend["voice_b"])
        self._set_blend_weights(int(clean_blend["voice_a_weight"]))
        self.tts_volume_slider.setValue(int(clean_blend["tts_volume"]))
        self.tts_speed_slider.setValue(int(clean_blend["tts_speed"]))

    def _set_blend_weights(self, voice_a_weight: int) -> None:
        """Sets the linked voice blend sliders."""

        self._syncing_blend_sliders = True

        try:
            voice_a_weight = max(0, min(100, int(voice_a_weight)))
            self.voice_a_weight_slider.setValue(voice_a_weight)
            self.voice_b_weight_slider.setValue(100 - voice_a_weight)
            self.voice_a_weight_label.setText(f"{voice_a_weight}%")
            self.voice_b_weight_label.setText(f"{100 - voice_a_weight}%")
        finally:
            self._syncing_blend_sliders = False

    def _handle_voice_a_weight_changed(self, value: int) -> None:
        """Keeps voice B weight complementary to voice A."""

        if self._syncing_blend_sliders:
            return

        self._mark_blend_in_use()
        self._set_blend_weights(value)

    def _handle_voice_b_weight_changed(self, value: int) -> None:
        """Keeps voice A weight complementary to voice B."""

        if self._syncing_blend_sliders:
            return

        self._mark_blend_in_use()
        self._set_blend_weights(100 - value)

    def _mark_blend_in_use(self) -> None:
        """Marks that the current custom blend should be applied."""

        if not self._loading_controls:
            self._use_blend = True

    def _populate_custom_voice_combo(self, *, selected_name: str | None = None) -> None:
        """Reloads the saved custom voice selector."""

        selected_key = str(selected_name or "").strip().casefold()
        selected_index = 0
        self.custom_voice_combo.blockSignals(True)
        self.custom_voice_combo.clear()
        self.custom_voice_combo.addItem("Choose a saved voice", None)

        for voice in self.custom_voices:
            blend = normalize_voice_blend(voice)
            self.custom_voice_combo.addItem(_custom_voice_display_text(blend), blend)

            if selected_key and str(blend["name"]).strip().casefold() == selected_key:
                selected_index = self.custom_voice_combo.count() - 1

        self.custom_voice_combo.setCurrentIndex(selected_index)
        self.custom_voice_combo.blockSignals(False)

    def _load_selected_custom_voice(self) -> None:
        """Loads the selected saved custom voice into the editor."""

        blend = self.custom_voice_combo.currentData()

        if not isinstance(blend, dict):
            return

        clean_blend = normalize_voice_blend(blend)
        self._loading_controls = True

        try:
            self.loaded_custom_voice_name = str(clean_blend["name"])
            self._apply_blend_to_controls(clean_blend)
        finally:
            self._loading_controls = False

        self._use_blend = True
        self._sync_loaded_voice_label()
        self._sync_action_states()

    def _save_current_custom_voice(self) -> None:
        """Saves over the currently loaded custom voice."""

        if self.loaded_custom_voice_name is None:
            self._save_current_custom_voice_as()
            return

        self._store_current_voice(self.loaded_custom_voice_name)

    def _save_current_custom_voice_as(self) -> None:
        """Prompts for a name and saves the current blend as that voice."""

        proposed_name = self.loaded_custom_voice_name or str(self.current_blend["name"])
        voice_name = self._prompt_for_voice_name("Save Custom Voice As", proposed_name)

        if voice_name is None:
            return

        self._store_current_voice(voice_name)

    def _rename_current_custom_voice(self) -> None:
        """Prompts for a replacement name for the currently loaded voice."""

        if self.loaded_custom_voice_name is None:
            return

        old_name = self.loaded_custom_voice_name
        voice_name = self._prompt_for_voice_name("Rename Custom Voice", old_name)

        if voice_name is None:
            return

        self._store_current_voice(voice_name, old_name=old_name)

    def _prompt_for_voice_name(self, title: str, current_name: str) -> str | None:
        """Prompts for a custom voice name."""

        voice_name, accepted = QInputDialog.getText(
            self,
            title,
            "Voice name:",
            QLineEdit.EchoMode.Normal,
            str(current_name or "").strip(),
        )

        if not accepted:
            return None

        clean_name = str(voice_name or "").strip()

        if not clean_name:
            QMessageBox.warning(self, "Missing Name", "Custom voice name is required.")
            return None

        return clean_name

    def _store_current_voice(self, name: str, *, old_name: str | None = None) -> None:
        """Stores the current controls under an explicit saved voice name."""

        clean_name = str(name or "").strip()

        if not clean_name:
            return

        remove_keys = {clean_name.casefold()}

        if old_name is not None:
            remove_keys.add(str(old_name).strip().casefold())

        blend = self._blend_from_controls(name=clean_name)
        self.custom_voices = [
            voice
            for voice in self.custom_voices
            if str(voice.get("name", "")).strip().casefold() not in remove_keys
        ]
        self.custom_voices.append(blend)
        self.custom_voices = normalize_custom_voices(self.custom_voices)
        self.current_blend = blend
        self.loaded_custom_voice_name = str(blend["name"])
        self.custom_voice_library_changed = True
        self._use_blend = True
        self._populate_custom_voice_combo(selected_name=self.loaded_custom_voice_name)
        self._sync_loaded_voice_label()
        self._sync_action_states()

    def _saved_voice_name_for(self, blend: dict[str, Any]) -> str | None:
        """Returns the saved voice name matching a blend name, if present."""

        blend_key = str(blend.get("name", "")).strip().casefold()

        if not blend_key:
            return None

        for voice in self.custom_voices:
            voice_name = str(voice.get("name", "")).strip()

            if voice_name.casefold() == blend_key:
                return voice_name

        return None

    def _sync_loaded_voice_label(self) -> None:
        """Updates the non-editable loaded voice label."""

        blend = self._blend_from_controls(
            name=self.loaded_custom_voice_name or str(self.current_blend["name"])
        )

        if self.loaded_custom_voice_name is None:
            self.current_voice_label.setText(
                f"Unsaved Custom Voice - {_custom_voice_display_text(blend)}"
            )
        else:
            self.current_voice_label.setText(_custom_voice_display_text(blend))

    def _sync_action_states(self) -> None:
        """Enables actions that require a selected or loaded voice."""

        self.load_custom_voice_button.setEnabled(
            isinstance(self.custom_voice_combo.currentData(), dict)
        )
        has_loaded_voice = self.loaded_custom_voice_name is not None
        self.save_custom_voice_button.setEnabled(has_loaded_voice)
        self.rename_custom_voice_button.setEnabled(has_loaded_voice)
        self.sample_voice_button.setEnabled(self.on_sample_voice is not None)

    def _sample_voice(self) -> None:
        """Plays a sample using the current custom blend."""

        if self.on_sample_voice is None:
            return

        blend_name = self.loaded_custom_voice_name or str(self.current_blend["name"])
        blend = self._blend_from_controls(name=blend_name)
        _invoke_sample_voice_callback(
            self.on_sample_voice,
            active_voice_spec_from_audio({"tts_voice_mode": "blend", "tts_voice_blend": blend}),
            self.tts_volume_slider.value(),
            self.tts_speed_slider.value(),
        )


class TTSSettingsWidget(QWidget):
    """Shared advanced narrator controls."""

    def __init__(
        self,
        *,
        audio_settings: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: Callable[[str, int, int], bool] | None = None,
        on_custom_voice_saved: Callable[[dict[str, Any]], None] | None = None,
        custom_voice_storage_path: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        self.on_custom_voice_saved = on_custom_voice_saved
        self.custom_voice_storage_path = custom_voice_storage_path
        self._loading_tts_settings = False
        self.custom_voice_library_changed = False
        self.custom_voices: list[dict[str, Any]] = []
        self.current_voice_blend = normalize_voice_blend({})

        self.narrator_enabled_checkbox = QCheckBox("Narrator enabled")
        self.narrator_enabled_checkbox.toggled.connect(
            lambda checked: self._sync_control_states(checked)
        )

        self.tts_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.tts_volume_slider.setRange(0, 100)
        self.tts_volume_slider.setValue(90)
        self.tts_volume_label = QLabel(f"{self.tts_volume_slider.value()}%")
        self.tts_volume_slider.valueChanged.connect(
            lambda value: self.tts_volume_label.setText(f"{value}%")
        )

        self.tts_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.tts_speed_slider.setRange(50, 200)
        self.tts_speed_slider.setValue(DEFAULT_TTS_SPEED_PERCENT)
        self.tts_speed_label = QLabel(f"{self.tts_speed_slider.value()}%")
        self.tts_speed_slider.valueChanged.connect(
            lambda value: self.tts_speed_label.setText(f"{value}%")
        )

        self.voice_mode_combo = QComboBox()
        self.voice_mode_combo.addItem("Preset Voice", "preset")
        self.voice_mode_combo.addItem("Custom Blend", "blend")
        self.voice_mode_combo.currentIndexChanged.connect(
            lambda _index: self._sync_control_states(self.narrator_enabled_checkbox.isChecked())
        )

        self.preset_voice_combo = QComboBox()
        self.tts_voice_combo = self.preset_voice_combo
        _populate_narrator_voice_combo(
            self.preset_voice_combo,
            DEFAULT_NARRATOR_VOICE,
            voice_options=self.voice_options,
        )

        self.custom_voice_summary_label = QLabel("Current Blend")
        self.custom_voice_summary_label.setWordWrap(True)
        self.custom_voice_button = QPushButton("Custom Voices...")
        self.custom_voice_button.clicked.connect(self._open_custom_voice_dialog)

        self.sample_voice_button = QPushButton("Sample Voice")
        self.sample_voice_button.clicked.connect(self._sample_voice)

        self.tts_volume_row = _slider_row(self.tts_volume_slider, self.tts_volume_label)
        self.tts_speed_row = _slider_row(self.tts_speed_slider, self.tts_speed_label)
        self.custom_voice_row = _button_row(
            self.custom_voice_summary_label,
            self.custom_voice_button,
        )
        self.voice_button_row = _button_row(self.sample_voice_button)

        form = QFormLayout()
        form.addRow("Narrator:", self.narrator_enabled_checkbox)
        form.addRow("Volume:", self.tts_volume_row)
        form.addRow("Speed:", self.tts_speed_row)
        form.addRow("Voice Source:", self.voice_mode_combo)
        form.addRow("Preset Voice:", self.preset_voice_combo)
        form.addRow("Custom Voice:", self.custom_voice_row)
        form.addRow("", self.voice_button_row)
        self.setLayout(form)
        self.load_audio_settings(audio_settings or {})

    def load_audio_settings(self, audio_settings: dict[str, Any]) -> None:
        """Loads normalized TTS settings into the controls."""

        audio = normalize_tts_audio_fields(audio_settings)
        self._loading_tts_settings = True

        try:
            self.custom_voices = normalize_custom_voices(audio["tts_custom_voices"])
            self.current_voice_blend = normalize_voice_blend(audio["tts_voice_blend"])
            self.narrator_enabled_checkbox.setChecked(bool(audio["narrator_enabled"]))
            self.tts_volume_slider.setValue(int(audio["tts_volume"]))
            self.tts_speed_slider.setValue(normalize_tts_speed_percent(audio["tts_speed"]))
            _set_combo_to_data(self.voice_mode_combo, audio["tts_voice_mode"])
            _set_combo_to_data(self.preset_voice_combo, audio["tts_voice"])
        finally:
            self._loading_tts_settings = False

        self._sync_custom_voice_summary()
        self._sync_control_states(self.narrator_enabled_checkbox.isChecked())

    def build_audio_settings(self) -> dict[str, Any]:
        """Builds normalized TTS settings from the controls."""

        return normalize_tts_audio_fields(
            {
                "narrator_enabled": self.narrator_enabled_checkbox.isChecked(),
                "tts_volume": self.tts_volume_slider.value(),
                "tts_voice": self._preset_voice_value(),
                "tts_speed": self.tts_speed_slider.value(),
                "tts_voice_mode": self.voice_mode_combo.currentData() or "preset",
                "tts_voice_blend": self._current_blend(),
                "tts_custom_voices": self.custom_voices,
            }
        )

    def active_voice_spec(self) -> str:
        """Returns the selected engine voice id or blend spec."""

        return active_voice_spec_from_audio(self.build_audio_settings())

    def _preset_voice_value(self) -> str:
        """Returns the selected preset voice id."""

        return normalize_narrator_voice(
            _combo_current_data_text(self.preset_voice_combo, DEFAULT_NARRATOR_VOICE)
        )

    def _current_blend(self) -> dict[str, Any]:
        """Returns the current custom voice blend with current audio controls."""

        blend = normalize_voice_blend(self.current_voice_blend)
        blend["tts_volume"] = self.tts_volume_slider.value()
        blend["tts_speed"] = self.tts_speed_slider.value()
        return normalize_voice_blend(blend)

    def _open_custom_voice_dialog(self) -> None:
        """Opens the dedicated custom voice editor."""

        dialog = CustomVoiceDialog(
            self,
            audio_settings=self.build_audio_settings(),
            voice_options=self.voice_options,
            on_sample_voice=self.on_sample_voice,
            storage_path=self.custom_voice_storage_path,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.load_audio_settings(dialog.build_audio_settings())

        if dialog.custom_voice_library_changed:
            self.custom_voice_library_changed = True

            if self.on_custom_voice_saved is not None:
                self.on_custom_voice_saved(self.build_audio_settings())

    def _sync_custom_voice_summary(self) -> None:
        """Updates the selected custom voice summary label."""

        blend = self._current_blend()
        saved_name = self._saved_voice_name_for(blend)

        if saved_name is not None:
            blend["name"] = saved_name

        display_blend = blend if saved_name is not None else {**blend, "name": "Current Blend"}
        self.custom_voice_summary_label.setText(_custom_voice_display_text(display_blend))

    def _saved_voice_name_for(self, blend: dict[str, Any]) -> str | None:
        """Returns the saved voice name matching a blend name, if present."""

        blend_key = str(blend.get("name", "")).strip().casefold()

        if not blend_key:
            return None

        for voice in self.custom_voices:
            voice_name = str(voice.get("name", "")).strip()

            if voice_name.casefold() == blend_key:
                return voice_name

        return None

    def _sample_voice(self) -> None:
        """Plays a sample using the current preset or blend."""

        if self.on_sample_voice is None:
            return

        _invoke_sample_voice_callback(
            self.on_sample_voice,
            self.active_voice_spec(),
            self.tts_volume_slider.value(),
            self.tts_speed_slider.value(),
        )

    def _sync_control_states(self, checked: bool) -> None:
        """Enables controls based on narrator and voice-source state."""

        mode = normalize_tts_voice_mode(self.voice_mode_combo.currentData())
        preset_visible = checked and mode == "preset"
        custom_visible = checked and mode == "blend"

        for widget in (
            self.tts_volume_slider,
            self.tts_speed_slider,
            self.voice_mode_combo,
            self.custom_voice_button,
            self.sample_voice_button,
        ):
            widget.setEnabled(checked)

        self.preset_voice_combo.setEnabled(preset_visible)
        self.custom_voice_button.setEnabled(checked)
        self.sample_voice_button.setEnabled(checked and self.on_sample_voice is not None)

        for field in (
            self.tts_volume_row,
            self.tts_speed_row,
            self.voice_mode_combo,
            self.voice_button_row,
        ):
            self._set_form_field_visible(field, checked)

        self._set_form_field_visible(self.preset_voice_combo, preset_visible)
        self._set_form_field_visible(self.custom_voice_row, custom_visible)

    def _set_form_field_visible(self, field: QWidget, visible: bool) -> None:
        """Shows or hides a form field and its label together."""

        field.setVisible(visible)

        layout = self.layout()

        if not isinstance(layout, QFormLayout):
            return

        label = layout.labelForField(field)

        if label is not None:
            label.setVisible(visible)


class TTSSettingsDialog(QDialog):
    """Dialog wrapper for shared advanced narrator controls."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        audio_settings: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: Callable[[str, int, int], bool] | None = None,
        on_custom_voice_saved: Callable[[dict[str, Any]], None] | None = None,
        custom_voice_storage_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("TTS Settings")
        self.resize(560, 520)
        self.tts_settings_widget = TTSSettingsWidget(
            audio_settings=audio_settings,
            voice_options=voice_options,
            on_sample_voice=on_sample_voice,
            on_custom_voice_saved=on_custom_voice_saved,
            custom_voice_storage_path=custom_voice_storage_path,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self.tts_settings_widget)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def build_audio_settings(self) -> dict[str, Any]:
        """Builds normalized TTS settings from the dialog."""

        return self.tts_settings_widget.build_audio_settings()

    @property
    def custom_voice_library_changed(self) -> bool:
        """Returns whether the custom voice library changed while open."""

        return self.tts_settings_widget.custom_voice_library_changed


class MainMenuSettingsDialog(QDialog):
    """App-level settings available before a save is loaded."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: dict[str, Any],
        tts_enabled: bool = True,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: Callable[[str, int], bool] | None = None,
        custom_voice_storage_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self.tts_enabled = bool(tts_enabled)
        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        clean_settings = normalize_app_settings(
            settings,
            tts_enabled=self.tts_enabled,
        )
        audio = clean_settings["audio"]

        self.setWindowTitle("Settings")
        self.resize(500, 340)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        self.theme_combo.setCurrentText(clean_settings["theme"])

        self.music_enabled_checkbox = QCheckBox("Music enabled")
        self.music_enabled_checkbox.setChecked(bool(audio["music_enabled"]))

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(int(audio["music_volume"]))
        self.music_volume_label = QLabel(f"{self.music_volume_slider.value()}%")
        self.music_volume_slider.valueChanged.connect(
            lambda value: self.music_volume_label.setText(f"{value}%")
        )

        self.narrator_enabled_checkbox: QCheckBox | None = None
        self.tts_volume_slider: QSlider | None = None
        self.tts_volume_label: QLabel | None = None
        self.tts_voice_combo: QComboBox | None = None
        self.sample_voice_button: QPushButton | None = None
        self.tts_speed_slider: QSlider | None = None
        self.tts_settings_widget: TTSSettingsWidget | None = None
        self._custom_calendar_settings = dict(GREGORIAN_CALENDAR_SETTINGS)

        form = QFormLayout()
        form.addRow("Theme Preference:", self.theme_combo)
        form.addRow("Background Music:", self.music_enabled_checkbox)
        form.addRow(
            "Music Volume:",
            _slider_row(self.music_volume_slider, self.music_volume_label),
        )

        if self.tts_enabled:
            self.tts_settings_widget = TTSSettingsWidget(
                audio_settings=audio,
                voice_options=self.voice_options,
                on_sample_voice=self._sample_voice,
                custom_voice_storage_path=custom_voice_storage_path,
            )
            self.narrator_enabled_checkbox = (
                self.tts_settings_widget.narrator_enabled_checkbox
            )
            self.tts_volume_slider = self.tts_settings_widget.tts_volume_slider
            self.tts_volume_label = self.tts_settings_widget.tts_volume_label
            self.tts_speed_slider = self.tts_settings_widget.tts_speed_slider
            self.tts_voice_combo = self.tts_settings_widget.tts_voice_combo
            self.sample_voice_button = self.tts_settings_widget.sample_voice_button
            form.addRow("TTS:", self.tts_settings_widget)

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(save_button)
        button_row.addWidget(cancel_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addStretch()
        layout.addLayout(button_row)
        self.setLayout(layout)

    def build_settings(self) -> dict[str, Any]:
        """Builds normalized app-level settings from dialog fields."""

        return normalize_app_settings(
            {
                "theme": self.theme_combo.currentText(),
                "audio": {
                    "music_enabled": self.music_enabled_checkbox.isChecked(),
                    "music_volume": self.music_volume_slider.value(),
                    **self._tts_settings_value(),
                },
            },
            tts_enabled=self.tts_enabled,
        )

    def _narrator_enabled_value(self) -> bool:
        """Returns the requested narrator setting."""

        if self.narrator_enabled_checkbox is None:
            return False

        return self.narrator_enabled_checkbox.isChecked()

    def _tts_volume_value(self) -> int:
        """Returns the requested narrator volume."""

        if self.tts_volume_slider is None:
            return 0

        return self.tts_volume_slider.value()

    def _tts_voice_value(self) -> str:
        """Returns the selected narrator voice id."""

        return normalize_narrator_voice(
            _combo_current_data_text(self.tts_voice_combo, DEFAULT_NARRATOR_VOICE)
        )

    def _sync_narrator_control_states(self, checked: bool) -> None:
        """Enables narrator-specific controls only when narration is enabled."""

        if self.tts_volume_slider is not None:
            self.tts_volume_slider.setEnabled(checked)
        if self.tts_voice_combo is not None:
            self.tts_voice_combo.setEnabled(checked)
        if self.sample_voice_button is not None:
            self.sample_voice_button.setEnabled(checked and self.on_sample_voice is not None)

    def _sample_voice(
        self,
        voice: str | None = None,
        volume: int | None = None,
        speed: int | None = None,
    ) -> bool | None:
        """Plays the selected voice sample."""

        if self.on_sample_voice is None:
            return None

        return _invoke_sample_voice_callback(
            self.on_sample_voice,
            voice
            or (
                self.tts_settings_widget.active_voice_spec()
                if self.tts_settings_widget is not None
                else self._tts_voice_value()
            ),
            self._tts_volume_value() if volume is None else int(volume),
            DEFAULT_TTS_SPEED_PERCENT if speed is None else int(speed),
        )

    def _tts_settings_value(self) -> dict[str, Any]:
        """Returns advanced TTS settings for app-level settings."""

        if self.tts_settings_widget is None:
            return normalize_tts_audio_fields({}, tts_enabled=False)

        return self.tts_settings_widget.build_audio_settings()


class MainMenuScreen(QWidget):
    """Main Menu with New Game, Load Game, and Settings actions."""

    def __init__(
        self,
        saves_dir: Path,
        on_new_game,
        on_load_game,
        on_settings,
        on_templates,
    ) -> None:
        """
        Args:
            saves_dir: Directory containing save folders.
            on_new_game: Callback for creating a new game.
            on_load_game: Callback for loading a save by database path.
            on_settings: Callback for opening app-level settings.
            on_templates: Callback for managing reusable new-game templates.
        """

        super().__init__()

        self.saves_dir = saves_dir
        self.on_new_game = on_new_game
        self.on_load_game = on_load_game
        self.on_settings = on_settings
        self.on_templates = on_templates

        title_label = QLabel("AI Adventure")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 32px; font-weight: bold;")

        new_game_button = QPushButton("New Game")
        new_game_button.clicked.connect(self._handle_new_game)

        self.save_combo = QComboBox()

        load_button = QPushButton("Load Game")
        load_button.clicked.connect(self._handle_load_game)

        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.on_settings)

        self.templates_button = QPushButton("New Game Templates")
        self.templates_button.clicked.connect(self.on_templates)

        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(title_label)
        layout.addSpacing(30)

        layout.addWidget(new_game_button)
        layout.addWidget(self.templates_button)
        layout.addWidget(self.settings_button)

        layout.addSpacing(30)
        layout.addWidget(QLabel("Existing Saves:"))
        layout.addWidget(self.save_combo)
        layout.addWidget(load_button)
        layout.addStretch()

        wrapper = QHBoxLayout()
        wrapper.addStretch()
        wrapper.addLayout(layout, stretch=2)
        wrapper.addStretch()

        self.setLayout(wrapper)
        self.refresh_saves()

    def refresh_saves(self) -> None:
        """Reloads save summaries into the load-game combo box."""

        self.save_combo.clear()

        saves = SaveRepository.list_saves(self.saves_dir)

        if not saves:
            self.save_combo.addItem("No saves found", None)
            return

        for summary in saves:
            label = self._format_save_summary(summary)
            self.save_combo.addItem(label, summary.db_path)

    def _handle_new_game(self) -> None:
        """Handles the New Game button."""

        self.on_new_game()

    def _handle_load_game(self) -> None:
        """Handles the Load Game button."""

        db_path = self.save_combo.currentData()

        if db_path is None:
            QMessageBox.information(self, "No Save Selected", "There is no save to load.")
            return

        self.on_load_game(Path(db_path))

    def _format_save_summary(self, summary: SaveSummary) -> str:
        """
        Formats a save summary for display.

        Args:
            summary: Save summary.

        Returns:
            Display label.
        """

        modified = summary.last_modified.strftime("%Y-%m-%d %I:%M %p")
        return f"{summary.title} - {modified}"


class NewGameTemplateManagerDialog(QDialog):
    """Main-menu dialog for creating and editing reusable new-game templates."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        template_path: Path,
        legacy_template_path: Path | None = None,
    ) -> None:
        super().__init__(parent)

        self.template_path = template_path
        self.legacy_template_path = legacy_template_path
        self.templates: list[NewGameTemplate] = []
        self.active_template_name: str | None = None
        self.active_setup: dict[str, Any] = {}

        self.setWindowTitle("New Game Templates")
        self.resize(980, 680)

        self.template_list = QListWidget()
        self.template_list.currentRowChanged.connect(self._load_selected_template)

        new_button = QPushButton("New")
        new_button.clicked.connect(self._new_template)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self._save_template)
        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self._delete_template)

        self.template_name_input = QLineEdit()
        self.template_name_input.setPlaceholderText("Template name")
        self.save_title_input = QLineEdit()
        self.save_title_input.setPlaceholderText("Suggested save name when loaded")
        self.genre_input = QLineEdit()
        self.genre_input.setPlaceholderText("Genre or adventure type")
        self.start_location_input = QLineEdit()
        self.start_location_input.setPlaceholderText("Starting place")
        self.narration_tense_combo = QComboBox()
        _add_combo_options(self.narration_tense_combo, NARRATION_TENSE_OPTIONS)
        _set_combo_to_data(self.narration_tense_combo, DEFAULT_NARRATION_TENSE)
        self.narration_style_combo = QComboBox()
        _add_combo_options(self.narration_style_combo, NARRATION_STYLE_OPTIONS)
        _set_combo_to_data(self.narration_style_combo, DEFAULT_NARRATION_STYLE)
        self.game_style_input = QTextEdit()
        self.game_style_input.setPlaceholderText("Tone, pacing, realism, themes, playstyle...")
        self.world_context_input = QTextEdit()
        self.world_context_input.setPlaceholderText("World facts, factions, locations, constraints...")

        self.character_name_input = QLineEdit()
        self.character_name_input.setPlaceholderText("Player character name")
        self.appearance_input = QTextEdit()
        self.appearance_input.setPlaceholderText("Appearance, clothing, visible traits...")
        self.backstory_input = QTextEdit()
        self.backstory_input.setPlaceholderText("Origin, history, goals, relationships...")
        self.character_notes_input = QTextEdit()
        self.character_notes_input.setPlaceholderText("Other player-character notes...")

        self.skill_inputs: list[tuple[int, QLineEdit, QLineEdit]] = []
        self.starter_items_table = QTableWidget(0, 6)
        self.starter_items_table.setHorizontalHeaderLabels(
            ["Name", "Amount", "Category", "Description", "Value", ""]
        )
        _configure_inline_table(
            self.starter_items_table,
            STARTER_ITEM_COLUMN_WIDTHS,
            minimum_height=170,
        )
        self.add_starter_item_button = QPushButton("Add Item")
        self.add_starter_item_button.clicked.connect(
            lambda: self._append_starter_item_row({})
        )
        self.currency_table = QTableWidget(0, 4)
        self.currency_table.setHorizontalHeaderLabels(["Name", "Plural Name", "Base Value", ""])
        _configure_inline_table(
            self.currency_table,
            CURRENCY_COLUMN_WIDTHS,
            minimum_height=160,
        )
        self.add_currency_button = QPushButton("Add Currency")
        self.add_currency_button.clicked.connect(lambda: self._append_currency_row({}))
        self.economy_examples_table = QTableWidget(0, 3)
        self.economy_examples_table.setHorizontalHeaderLabels(["Item", "Base Units", ""])
        _configure_inline_table(
            self.economy_examples_table,
            ECONOMY_EXAMPLE_COLUMN_WIDTHS,
            minimum_height=140,
        )
        self.add_economy_example_button = QPushButton("Add Economy Item")
        self.add_economy_example_button.clicked.connect(
            lambda: self._append_economy_example_row({})
        )
        self._legacy_currency_description = ""
        self.calendar_type_combo = QComboBox()
        self.calendar_type_combo.addItem("Gregorian-style calendar", "gregorian")
        self.calendar_type_combo.addItem("AI-generated calendar", "ai_generated")
        self.calendar_type_combo.addItem("Keep/custom calendar", "custom")

        template_buttons = _button_row(new_button, save_button, delete_button)
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Templates"))
        left_layout.addWidget(self.template_list)
        left_layout.addWidget(template_buttons)
        left_panel = QWidget()
        left_panel.setLayout(left_layout)
        left_panel.setMinimumWidth(260)

        tabs = QTabWidget()
        tabs.addTab(self._build_overview_tab(), "Overview")
        tabs.addTab(self._build_character_tab(), "Character")
        tabs.addTab(self._build_skills_tab(), "Skills")
        tabs.addTab(self._build_world_tab(), "World")

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(close_button)

        editor_layout = QVBoxLayout()
        editor_layout.addWidget(tabs)
        editor_layout.addLayout(close_row)

        main_layout = QHBoxLayout()
        main_layout.addWidget(left_panel)
        main_layout.addLayout(editor_layout, stretch=1)
        self.setLayout(main_layout)

        self._refresh_templates()

        if self.template_list.count() == 0:
            self._new_template()
        else:
            self.template_list.setCurrentRow(0)

    def _build_overview_tab(self) -> QWidget:
        """Builds the template overview tab."""

        form = QFormLayout()
        form.addRow("Template Name:", self.template_name_input)
        form.addRow("Suggested Save Name:", self.save_title_input)
        form.addRow("Genre:", self.genre_input)
        form.addRow("Starting Location:", self.start_location_input)
        form.addRow("Narration Tense:", self.narration_tense_combo)
        form.addRow("Narration Style:", self.narration_style_combo)
        form.addRow("Game Style:", self.game_style_input)

        tab = QWidget()
        tab.setLayout(form)
        return tab

    def _build_character_tab(self) -> QWidget:
        """Builds the player-character template tab."""

        form = QFormLayout()
        form.addRow("Character Name:", self.character_name_input)
        form.addRow("Appearance:", self.appearance_input)
        form.addRow("Backstory:", self.backstory_input)
        form.addRow("Notes:", self.character_notes_input)

        tab = QWidget()
        tab.setLayout(form)
        return tab

    def _build_skills_tab(self) -> QWidget:
        """Builds the starting skills template tab."""

        layout = QGridLayout()
        layout.addWidget(QLabel("Level"), 0, 0)
        layout.addWidget(QLabel("Skill"), 0, 1)
        layout.addWidget(QLabel("Description"), 0, 2)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 2)

        for row, level in enumerate(SKILL_LEVEL_PLAN, start=1):
            skill_input = QLineEdit()
            skill_input.setPlaceholderText("Skill name")
            description_input = QLineEdit()
            description_input.setPlaceholderText("What this skill covers")
            self.skill_inputs.append((level, skill_input, description_input))
            layout.addWidget(QLabel(str(level)), row, 0)
            layout.addWidget(skill_input, row, 1)
            layout.addWidget(description_input, row, 2)

        layout.setRowStretch(len(SKILL_LEVEL_PLAN) + 1, 1)
        tab = QWidget()
        tab.setLayout(layout)
        return tab

    def _build_world_tab(self) -> QWidget:
        """Builds the world, items, economy, and calendar template tab."""

        form = QFormLayout()
        form.addRow("World Details:", self.world_context_input)
        form.addRow("Starter Items:", self.starter_items_table)
        form.addRow("", self.add_starter_item_button)
        form.addRow("Currencies:", self.currency_table)
        form.addRow("", self.add_currency_button)
        form.addRow("Economy Notes:", self.economy_examples_table)
        form.addRow("", self.add_economy_example_button)
        form.addRow("Calendar:", self.calendar_type_combo)

        tab = QWidget()
        tab.setLayout(form)
        return tab

    def _refresh_templates(self, *, selected_name: str | None = None) -> None:
        """Reloads templates from disk into the selector."""

        self.templates = load_new_game_templates(
            self.template_path,
            legacy_template_path=self.legacy_template_path,
            normalize_setups=False,
        )
        selected_key = str(selected_name or "").strip().casefold()
        selected_row = -1

        self.template_list.blockSignals(True)
        self.template_list.clear()

        for index, template in enumerate(self.templates):
            self.template_list.addItem(template.name)

            if selected_key and template.name.casefold() == selected_key:
                selected_row = index

        self.template_list.blockSignals(False)

        if selected_row >= 0:
            self.template_list.setCurrentRow(selected_row)

    def _new_template(self) -> None:
        """Starts a blank reusable template."""

        self.active_template_name = None
        self.active_setup = {}
        self.template_list.clearSelection()
        self._load_setup_into_editor("New Template", {})

    def _load_selected_template(self, row: int) -> None:
        """Loads the selected stored template into the editor."""

        if row < 0 or row >= len(self.templates):
            return

        template = self.templates[row]
        self.active_template_name = template.name
        self.active_setup = dict(template.setup)
        self._load_setup_into_editor(template.name, template.setup)

    def _load_setup_into_editor(self, template_name: str, setup: dict[str, Any]) -> None:
        """Populates editor controls from a possibly partial template setup."""

        character = setup.get("character", {}) if isinstance(setup.get("character"), dict) else {}
        self.template_name_input.setText(template_name)
        self.save_title_input.setText(str(setup.get("title", "") or ""))
        self.genre_input.setText(str(setup.get("specified_genre", setup.get("genre", "")) or ""))
        self.start_location_input.setText(str(setup.get("start_location", "") or ""))
        narration = normalize_narration_preferences(
            setup.get("narration", {}) if isinstance(setup.get("narration"), dict) else {}
        )
        _set_combo_to_data(self.narration_tense_combo, narration["tense"])
        _set_combo_to_data(self.narration_style_combo, narration["style"])
        self.game_style_input.setPlainText(str(setup.get("game_style", "") or ""))
        self.world_context_input.setPlainText(str(setup.get("world_context", "") or ""))
        self.character_name_input.setText(str(character.get("name", "") or ""))
        self.appearance_input.setPlainText(str(character.get("appearance", "") or ""))
        self.backstory_input.setPlainText(str(character.get("backstory", "") or ""))
        self.character_notes_input.setPlainText(str(character.get("notes", "") or ""))

        skills = self._skills_for_editor(setup.get("skills", []))

        for index, (_level, skill_input, description_input) in enumerate(self.skill_inputs):
            skill = skills[index] if index < len(skills) else {}
            skill_input.setText(str(skill.get("name", "") or ""))
            description_input.setText(str(skill.get("description", "") or ""))

        self.starter_items_table.setRowCount(0)

        for item in self._starter_items_for_editor(setup.get("starter_items", [])):
            self._append_starter_item_row(item)

        self.currency_table.setRowCount(0)

        for denomination in self._currency_denominations_for_editor(
            setup.get("currency_denominations", [])
        ):
            self._append_currency_row(denomination)

        self.economy_examples_table.setRowCount(0)

        economy_examples = normalize_economy_examples(setup.get("economy_examples", []))

        for example in economy_examples:
            self._append_economy_example_row(example)

        self._legacy_currency_description = (
            "" if economy_examples else str(setup.get("currency_description", "") or "")
        )
        _set_combo_to_data(
            self.calendar_type_combo,
            self._template_calendar_type(setup.get("calendar", {})),
        )

    def _save_template(self) -> None:
        """Saves the current editor contents as a reusable template."""

        template_name = self.template_name_input.text().strip()

        if not template_name:
            QMessageBox.warning(self, "Missing Template Name", "Enter a template name first.")
            return

        setup = self._build_setup_from_editor()

        if (
            self.active_template_name
            and self.active_template_name.casefold() != template_name.casefold()
        ):
            delete_new_game_template(self.template_path, self.active_template_name)

        if not save_new_game_template(
            self.template_path,
            setup,
            template_name=template_name,
            normalize_setup=False,
        ):
            QMessageBox.warning(self, "Template Not Saved", "Could not save the template.")
            return

        self.active_template_name = template_name
        self.active_setup = setup
        self._refresh_templates(selected_name=template_name)

    def _delete_template(self) -> None:
        """Deletes the selected reusable template."""

        template_name = self.active_template_name or self.template_name_input.text().strip()

        if not template_name:
            return

        result = QMessageBox.question(
            self,
            "Delete Template",
            f"Delete the template '{template_name}'?",
        )

        if result != QMessageBox.StandardButton.Yes:
            return

        if not delete_new_game_template(self.template_path, template_name):
            QMessageBox.warning(self, "Template Not Deleted", "Could not delete the template.")
            return

        self._refresh_templates()

        if self.template_list.count() == 0:
            self._new_template()
        else:
            self.template_list.setCurrentRow(0)

    def _build_setup_from_editor(self) -> dict[str, Any]:
        """Builds a partial setup dictionary from the editor controls."""

        setup = dict(self.active_setup)
        setup["title"] = self.save_title_input.text().strip()
        setup["specified_genre"] = self.genre_input.text().strip()
        setup["game_style"] = self.game_style_input.toPlainText().strip()
        setup["start_location"] = self.start_location_input.text().strip()
        setup["world_context"] = self.world_context_input.toPlainText().strip()
        setup["narration"] = {
            "tense": self.narration_tense_combo.currentData() or DEFAULT_NARRATION_TENSE,
            "style": self.narration_style_combo.currentData() or DEFAULT_NARRATION_STYLE,
        }
        setup["character"] = {
            **(setup.get("character", {}) if isinstance(setup.get("character"), dict) else {}),
            "name": self.character_name_input.text().strip(),
            "appearance": self.appearance_input.toPlainText().strip(),
            "backstory": self.backstory_input.toPlainText().strip(),
            "notes": self.character_notes_input.toPlainText().strip(),
        }
        setup["skills"] = [
            {
                "name": skill_input.text().strip(),
                "description": description_input.text().strip(),
                "level": level,
            }
            for level, skill_input, description_input in self.skill_inputs
            if skill_input.text().strip() or description_input.text().strip()
        ]
        setup["starter_items"] = self._starter_items_from_table()
        setup["currency_denominations"] = self._currency_denominations_from_table()
        setup["economy_examples"] = self._economy_examples_from_table()
        setup["currency_description"] = (
            describe_economy_examples(setup["economy_examples"])
            or self._legacy_currency_description
        )

        calendar_type = str(self.calendar_type_combo.currentData() or "gregorian")

        if calendar_type == "ai_generated":
            setup["calendar"] = {"calendar_type": "ai_generated", "ai_generated": True}
        elif calendar_type == "gregorian":
            setup["calendar"] = {"calendar_type": "gregorian", "ai_generated": False}
        else:
            existing_calendar = (
                setup.get("calendar", {}) if isinstance(setup.get("calendar"), dict) else {}
            )
            setup["calendar"] = {**existing_calendar, "calendar_type": "custom"}

        return setup

    def _skills_for_editor(self, raw_skills: Any) -> list[dict[str, Any]]:
        """Returns sparse template skills positioned by explicit level."""

        skills = raw_skills if isinstance(raw_skills, list) else []
        editor_skills: list[dict[str, Any]] = [{} for _level, _name, _description in self.skill_inputs]
        next_position = 0

        for raw_skill in skills[: len(editor_skills)]:
            if not isinstance(raw_skill, dict):
                raw_skill = {"name": str(raw_skill)}

            requested_level = _safe_int(raw_skill.get("level"), 0)
            target_index = -1

            if requested_level:
                for index, (level, _skill_input, _description_input) in enumerate(self.skill_inputs):
                    if level == requested_level and not editor_skills[index]:
                        target_index = index
                        break

            if target_index < 0:
                while next_position < len(editor_skills) and editor_skills[next_position]:
                    next_position += 1

                if next_position >= len(editor_skills):
                    break

                target_index = next_position

            editor_skills[target_index] = dict(raw_skill)

        return editor_skills

    def _append_starter_item_row(self, item: dict[str, Any]) -> None:
        """Adds a starter item row to the template editor."""

        _append_starter_item_table_row(
            self.starter_items_table,
            item,
            self._remove_starter_item_row,
        )

    def _remove_starter_item_row(self, button: QPushButton) -> None:
        """Removes the starter item row containing button."""

        _remove_table_row_by_button(self.starter_items_table, button)

    def _starter_items_from_table(self) -> list[dict[str, Any]]:
        """Reads starter item rows from the template editor."""

        return _starter_items_from_table(self.starter_items_table)

    @staticmethod
    def _starter_items_for_editor(raw_items: Any) -> list[dict[str, Any]]:
        """Returns legacy and current starter items as table rows."""

        if not isinstance(raw_items, list):
            return []

        items: list[dict[str, Any]] = []

        for raw_item in raw_items:
            if isinstance(raw_item, dict):
                items.append(raw_item)
                continue

            items.extend(parse_starter_items_text(str(raw_item)))

        return items

    def _append_currency_row(self, denomination: dict[str, Any]) -> None:
        """Adds a currency denomination row to the template editor."""

        _append_currency_table_row(
            self.currency_table,
            denomination,
            self._remove_currency_row,
        )

    def _remove_currency_row(self, button: QPushButton) -> None:
        """Removes the currency denomination row containing button."""

        if _remove_table_row_by_button(self.currency_table, button) >= 0:
            _sync_currency_base_value_row(self.currency_table)

    def _currency_denominations_from_table(self) -> list[dict[str, Any]]:
        """Reads currency denomination rows from the template editor."""

        return _currency_denominations_from_table(self.currency_table)

    @staticmethod
    def _currency_denominations_for_editor(raw_denominations: Any) -> list[dict[str, Any]]:
        """Returns current currency denominations as table rows."""

        if not isinstance(raw_denominations, list):
            return []

        return [
            denomination
            for denomination in raw_denominations
            if isinstance(denomination, dict)
        ]

    def _append_economy_example_row(self, example: dict[str, Any]) -> None:
        """Adds a common-price example row to the template editor."""

        _append_economy_example_table_row(
            self.economy_examples_table,
            example,
            self._remove_economy_example_row,
        )

    def _remove_economy_example_row(self, button: QPushButton) -> None:
        """Removes the common-price example row containing button."""

        _remove_table_row_by_button(self.economy_examples_table, button)

    def _economy_examples_from_table(self) -> list[dict[str, Any]]:
        """Reads common-price examples from the template editor."""

        return _economy_examples_from_table(self.economy_examples_table)

    @staticmethod
    def _template_calendar_type(raw_calendar: Any) -> str:
        """Returns the editor calendar mode for a partial template."""

        if not isinstance(raw_calendar, dict):
            return "gregorian"

        if bool(raw_calendar.get("ai_generated", False)):
            return "ai_generated"

        calendar_type = str(raw_calendar.get("calendar_type", "") or "").casefold()

        if calendar_type in {"ai_generated", "custom", "gregorian"}:
            return calendar_type

        return "gregorian"


class NewGameWizard(QWizard):
    """Multi-step new-game setup flow."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        template_setup: dict[str, Any] | None = None,
        tts_enabled: bool = True,
        audio_defaults: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: Callable[[str, int], bool] | None = None,
        on_tts_settings_saved: Callable[[dict[str, Any]], None] | None = None,
        custom_voice_storage_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self.tts_enabled = bool(tts_enabled)
        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        self.on_tts_settings_saved = on_tts_settings_saved
        self.custom_voice_storage_path = custom_voice_storage_path
        self.audio_defaults = normalize_app_settings(
            {"audio": audio_defaults or {}},
            tts_enabled=self.tts_enabled,
        )["audio"]
        self.narrator_enabled_checkbox: QCheckBox | None = None
        self.tts_volume_slider: QSlider | None = None
        self.tts_volume_label: QLabel | None = None
        self.tts_voice_combo: QComboBox | None = None
        self.sample_voice_button: QPushButton | None = None
        self.tts_speed_slider: QSlider | None = None
        self.tts_settings_widget: TTSSettingsWidget | None = None
        self._legacy_currency_description = ""

        self.setWindowTitle("New Game Wizard")
        self.resize(780, 620)
        self._apply_theme()

        self._build_adventure_page()
        self._build_character_page()
        self._build_skills_page()
        self._build_inventory_currency_page()
        self._build_audio_page()
        if self.tts_enabled:
            self._build_tts_page()
        self._build_calendar_page()

        if template_setup is not None:
            self.load_setup(template_setup)

    def _apply_theme(self) -> None:
        """Applies a cohesive local theme to the new-game wizard."""

        self.setObjectName("newGameWizard")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        use_dark_theme = _application_uses_dark_theme()
        colors = (
            {
                "window": "#202124",
                "window_text": "#f1f3f4",
                "base": "#121416",
                "alternate_base": "#2a2d30",
                "border": "#5b6268",
                "muted_border": "#4b5258",
                "placeholder": "#a9b0b6",
                "button": "#303437",
                "button_hover": "#3c4247",
                "button_pressed": "#262a2d",
                "button_disabled": "#25282b",
                "disabled_text": "#8b949e",
                "accent": "#4c8fcb",
                "accent_dark": "#2f6fb0",
                "drop_down": "#25292d",
                "group": "#25282b",
                "grid": "#3d444b",
                "selection": "#2f6fb0",
                "selection_text": "#ffffff",
            }
            if use_dark_theme
            else {
                "window": "#f5f7fb",
                "window_text": "#111827",
                "base": "#ffffff",
                "alternate_base": "#eef2f7",
                "border": "#64748b",
                "muted_border": "#94a3b8",
                "placeholder": "#4b5563",
                "button": "#e5e7eb",
                "button_hover": "#dbe4ee",
                "button_pressed": "#cbd5e1",
                "button_disabled": "#edf1f5",
                "disabled_text": "#6b7280",
                "accent": "#2563eb",
                "accent_dark": "#1d4ed8",
                "drop_down": "#e2e8f0",
                "group": "#eef2f7",
                "grid": "#cbd5e1",
                "selection": "#1d4ed8",
                "selection_text": "#ffffff",
            }
        )

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["window_text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["base"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["alternate_base"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["window_text"]))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["placeholder"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(colors["button"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["window_text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["selection"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["selection_text"]))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["button"]))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["window_text"]))
        self.setPalette(palette)

        stylesheet = """
            QWizard#newGameWizard {
                background-color: {colors["window"]};
                color: {colors["window_text"]};
            }

            QWizard#newGameWizard QWidget {
                background-color: {colors["window"]};
                color: {colors["window_text"]};
            }

            QWizard#newGameWizard QWizardPage,
            QWizard#newGameWizard QFrame {
                background-color: {colors["window"]};
                color: {colors["window_text"]};
            }

            QWizard#newGameWizard QLabel {
                background-color: transparent;
                color: {colors["window_text"]};
                font-size: 13px;
            }

            QWizard#newGameWizard QLineEdit,
            QWizard#newGameWizard QTextEdit,
            QWizard#newGameWizard QComboBox,
            QWizard#newGameWizard QSpinBox {
                background-color: {colors["base"]};
                border: 1px solid {colors["border"]};
                border-radius: 5px;
                color: {colors["window_text"]};
                padding: 6px;
                selection-background-color: {colors["selection"]};
                selection-color: {colors["selection_text"]};
            }

            QWizard#newGameWizard QComboBox {
                padding-right: 40px;
            }

            QWizard#newGameWizard QSpinBox {
                padding-right: 36px;
            }

            QWizard#newGameWizard QLineEdit::placeholder,
            QWizard#newGameWizard QTextEdit::placeholder {
                color: {colors["placeholder"]};
            }

            QWizard#newGameWizard QTextEdit {
                padding: 8px;
            }

            QWizard#newGameWizard QCheckBox {
                background-color: transparent;
                color: {colors["window_text"]};
                spacing: 8px;
            }

            QWizard#newGameWizard QCheckBox::indicator {
                background-color: {colors["base"]};
                border: 1px solid {colors["border"]};
                border-radius: 3px;
                height: 16px;
                width: 16px;
            }

            QWizard#newGameWizard QCheckBox::indicator:hover {
                border-color: {colors["accent"]};
            }

            QWizard#newGameWizard QCheckBox::indicator:checked {
                background-color: {colors["accent"]};
                border-color: {colors["accent_dark"]};
            }

            QWizard#newGameWizard QGroupBox {
                background-color: {colors["group"]};
                border: 1px solid {colors["muted_border"]};
                border-radius: 6px;
                color: {colors["window_text"]};
                font-weight: 600;
                margin-top: 12px;
                padding: 10px;
            }

            QWizard#newGameWizard QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                background-color: {colors["group"]};
                color: {colors["window_text"]};
                padding: 0 4px;
            }

            QWizard#newGameWizard QScrollArea {
                background-color: transparent;
                border: 0;
            }

            QWizard#newGameWizard QScrollArea > QWidget > QWidget {
                background-color: {colors["window"]};
            }

            QWizard#newGameWizard QSlider::groove:horizontal {
                background-color: {colors["alternate_base"]};
                border: 1px solid {colors["muted_border"]};
                border-radius: 4px;
                height: 8px;
            }

            QWizard#newGameWizard QSlider::handle:horizontal {
                background-color: {colors["accent"]};
                border: 1px solid {colors["accent_dark"]};
                border-radius: 7px;
                margin: -4px 0;
                width: 14px;
            }

            QWizard#newGameWizard QLineEdit:focus,
            QWizard#newGameWizard QTextEdit:focus,
            QWizard#newGameWizard QComboBox:focus,
            QWizard#newGameWizard QSpinBox:focus {
                border-color: {colors["accent"]};
            }

            QWizard#newGameWizard QComboBox::drop-down {
                background-color: {colors["drop_down"]};
                border: 0;
                border-left: 1px solid {colors["muted_border"]};
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 32px;
            }

            QWizard#newGameWizard QComboBox::down-arrow {
                image: none;
                width: 0;
                height: 0;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid {colors["window_text"]};
            }

            QWizard#newGameWizard QSpinBox::up-button,
            QWizard#newGameWizard QSpinBox::down-button {
                background-color: {colors["drop_down"]};
                border-left: 1px solid {colors["muted_border"]};
                subcontrol-origin: border;
                width: 28px;
            }

            QWizard#newGameWizard QSpinBox::up-button {
                border-top-right-radius: 4px;
                border-bottom: 1px solid {colors["muted_border"]};
                subcontrol-position: top right;
                height: 14px;
            }

            QWizard#newGameWizard QSpinBox::down-button {
                border-bottom-right-radius: 4px;
                subcontrol-position: bottom right;
                height: 14px;
            }

            QWizard#newGameWizard QComboBox QAbstractItemView {
                background-color: {colors["base"]};
                border: 1px solid {colors["border"]};
                color: {colors["window_text"]};
                selection-background-color: {colors["selection"]};
                selection-color: {colors["selection_text"]};
            }

            QWizard#newGameWizard QTableWidget {
                background-color: {colors["base"]};
                alternate-background-color: {colors["alternate_base"]};
                border: 1px solid {colors["border"]};
                border-radius: 5px;
                color: {colors["window_text"]};
                gridline-color: {colors["grid"]};
                selection-background-color: {colors["selection"]};
                selection-color: {colors["selection_text"]};
            }

            QWizard#newGameWizard QHeaderView::section {
                background-color: {colors["drop_down"]};
                border: 0;
                border-right: 1px solid {colors["grid"]};
                color: {colors["window_text"]};
                font-weight: 600;
                padding: 7px;
            }

            QWizard#newGameWizard QPushButton {
                background-color: {colors["button"]};
                border: 1px solid {colors["border"]};
                border-radius: 5px;
                color: {colors["window_text"]};
                min-width: 76px;
                padding: 6px 14px;
            }

            QWizard#newGameWizard QPushButton:hover {
                background-color: {colors["button_hover"]};
                border-color: {colors["muted_border"]};
            }

            QWizard#newGameWizard QPushButton:pressed {
                background-color: {colors["button_pressed"]};
            }

            QWizard#newGameWizard QPushButton:default {
                background-color: {colors["accent"]};
                border-color: {colors["accent_dark"]};
                color: {colors["selection_text"]};
            }

            QWizard#newGameWizard QPushButton:disabled {
                background-color: {colors["button_disabled"]};
                border-color: {colors["grid"]};
                color: {colors["disabled_text"]};
            }
            """

        for color_name, color_value in colors.items():
            stylesheet = stylesheet.replace(f'{{colors["{color_name}"]}}', color_value)

        self.setStyleSheet(stylesheet)

    def build_setup(self) -> dict[str, Any]:
        """Builds a normalized setup dictionary from wizard fields."""

        calendar_type = self.calendar_type_combo.currentData() or "gregorian"
        calendar_settings = self._calendar_settings_for_setup(str(calendar_type))
        economy_examples = self._economy_examples_from_table()

        skills = [
            {
                "name": skill_input.text(),
                "description": description_input.text(),
                "level": level,
                "requires_ai_invention": (
                    not skill_input.text().strip()
                    or not description_input.text().strip()
                ),
            }
            for level, skill_input, description_input in self.skill_inputs
        ]
        setup = {
            "title": self.title_input.text(),
            "character": {
                "name": self.character_name_input.text(),
                "appearance": self.appearance_input.toPlainText(),
                "backstory": self.backstory_input.toPlainText(),
                "notes": self.character_notes_input.toPlainText(),
            },
            "skills": skills,
            "starter_items": self._starter_items_from_table(),
            "calendar": calendar_settings,
            "audio": {
                "music_enabled": self.music_enabled_checkbox.isChecked(),
                "music_volume": self.music_volume_slider.value(),
                **self._tts_settings_value(),
            },
            "narration": {
                "tense": self.narration_tense_combo.currentData(),
                "style": self.narration_style_combo.currentData(),
            },
            "currency_denominations": self._currency_denominations_from_table(),
            "currency_description": (
                describe_economy_examples(economy_examples)
                or self._legacy_currency_description
            ),
            "economy_examples": economy_examples,
            "specified_genre": self.genre_input.text(),
            "game_style": self.game_style_input.toPlainText(),
            "start_location": self.start_location_input.text(),
            "world_context": self.world_context_input.toPlainText(),
        }

        return normalize_new_game_setup(setup)

    def load_setup(self, setup: dict[str, Any]) -> None:
        """Populates wizard fields from a reusable setup template."""

        clean_setup = normalize_new_game_setup(setup)
        character = clean_setup["character"]
        calendar = clean_setup["calendar"]
        audio = clean_setup["audio"]
        narration = clean_setup["narration"]

        self.title_input.setText(clean_setup["title"])
        self.genre_input.setText(clean_setup["specified_genre"])
        self.game_style_input.setPlainText(clean_setup["game_style"])
        self.start_location_input.setText(clean_setup["start_location"])
        self.world_context_input.setPlainText(clean_setup["world_context"])
        _set_combo_to_data(self.narration_tense_combo, narration["tense"])
        _set_combo_to_data(self.narration_style_combo, narration["style"])

        self.character_name_input.setText(character["name"])
        self.appearance_input.setPlainText(character["appearance"])
        self.backstory_input.setPlainText(character["backstory"])
        self.character_notes_input.setPlainText(character["notes"])

        for index, (_, skill_input, description_input) in enumerate(self.skill_inputs):
            skill = clean_setup["skills"][index] if index < len(clean_setup["skills"]) else {}
            skill_input.setText(str(skill.get("name", "")))
            description_input.setText(str(skill.get("description", "")))

        self.starter_items_table.setRowCount(0)

        for item in clean_setup["starter_items"]:
            self._append_starter_item_row(item)

        self.currency_table.setRowCount(0)

        for denomination in clean_setup["currency_denominations"]:
            self._append_currency_row(denomination)

        self.economy_examples_table.setRowCount(0)

        for example in clean_setup["economy_examples"]:
            self._append_economy_example_row(example)

        self._legacy_currency_description = (
            "" if clean_setup["economy_examples"] else clean_setup["currency_description"]
        )

        _set_combo_to_data(
            self.calendar_type_combo,
            _calendar_type_from_settings(calendar),
        )
        self._custom_calendar_settings = dict(calendar)
        self._sync_calendar_settings_button()
        self.music_enabled_checkbox.setChecked(bool(audio["music_enabled"]))
        self.music_volume_slider.setValue(int(audio["music_volume"]))

        if self.tts_settings_widget is not None:
            self.tts_settings_widget.load_audio_settings(audio)

    def _build_adventure_page(self) -> None:
        """Builds the adventure/world setup page."""

        page = QWizardPage()
        page.setTitle("Adventure")
        page.setSubTitle("Name the save and describe the kind of game you want.")

        self.title_input = QLineEdit()
        self.title_input.setText("New Adventure")

        self.game_style_input = QTextEdit()
        self.game_style_input.setPlaceholderText(
            "Tone, realism, pacing, themes, or playstyle preferences..."
        )

        self.genre_input = QLineEdit()
        self.genre_input.setPlaceholderText(
            "Optional: survival, detective mystery, post-apocalyptic, space frontier..."
        )

        self.start_location_input = QLineEdit()
        self.start_location_input.setPlaceholderText(
            "Optional: deserted island, frozen sea, crime scene, ruined store..."
        )

        self.narration_tense_combo = QComboBox()
        _add_combo_options(self.narration_tense_combo, NARRATION_TENSE_OPTIONS)
        _set_combo_to_data(self.narration_tense_combo, DEFAULT_NARRATION_TENSE)

        self.narration_style_combo = QComboBox()
        _add_combo_options(self.narration_style_combo, NARRATION_STYLE_OPTIONS)
        _set_combo_to_data(self.narration_style_combo, DEFAULT_NARRATION_STYLE)

        self.world_context_input = QTextEdit()
        self.world_context_input.setPlaceholderText(
            "Named locations, factions, guilds, religions, political tensions, tone, themes..."
        )

        layout = QFormLayout()
        layout.addRow("Game Name:", self.title_input)
        layout.addRow("Genre:", self.genre_input)
        layout.addRow("Game Style:", self.game_style_input)
        layout.addRow("Starting Location:", self.start_location_input)
        layout.addRow("Narration Tense:", self.narration_tense_combo)
        layout.addRow("Narration Style:", self.narration_style_combo)
        layout.addRow("World Details:", self.world_context_input)
        page.setLayout(layout)

        self.addPage(page)

    def _build_character_page(self) -> None:
        """Builds the character page."""

        page = QWizardPage()
        page.setTitle("Character")
        page.setSubTitle("Describe the player character.")

        self.character_name_input = QLineEdit()
        self.character_name_input.setText("Player Name")

        self.appearance_input = QTextEdit()
        self.backstory_input = QTextEdit()
        self.character_notes_input = QTextEdit()

        self.appearance_input.setPlaceholderText("Appearance, clothing, visible traits, voice...")
        self.backstory_input.setPlaceholderText("Origin, history, goals, relationships...")
        self.character_notes_input.setPlaceholderText("Other character notes the AI should know...")

        layout = QFormLayout()
        layout.addRow("Name:", self.character_name_input)
        layout.addRow("Appearance:", self.appearance_input)
        layout.addRow("Backstory:", self.backstory_input)
        layout.addRow("Notes:", self.character_notes_input)
        page.setLayout(layout)

        self.addPage(page)

    def _build_skills_page(self) -> None:
        """Builds the starting skills page."""

        page = QWizardPage()
        page.setTitle("Skills")
        page.setSubTitle("Name the character's starting skills and describe what each means in play.")

        self.skill_inputs: list[tuple[int, QLineEdit, QLineEdit]] = []
        content = QWidget()
        layout = QVBoxLayout()

        for level in sorted(set(SKILL_LEVEL_PLAN), reverse=True):
            skill_count = SKILL_LEVEL_PLAN.count(level)
            group = QGroupBox(f"Level {level}")
            group_layout = QGridLayout()
            group_layout.setColumnStretch(0, 1)
            group_layout.setColumnStretch(1, 2)

            meaning_label = QLabel(SKILL_LEVEL_DESCRIPTIONS[level])
            meaning_label.setWordWrap(True)
            group_layout.addWidget(meaning_label, 0, 0, 1, 2)
            group_layout.addWidget(QLabel("Skill"), 1, 0)
            group_layout.addWidget(QLabel("Description for AI"), 1, 1)

            for row_index in range(skill_count):
                skill_input = QLineEdit()
                skill_input.setPlaceholderText("Skill name")
                description_input = QLineEdit()
                description_input.setPlaceholderText(
                    "What this skill covers, how it shows up, or what makes it distinct"
                )
                self.skill_inputs.append((level, skill_input, description_input))
                group_layout.addWidget(skill_input, row_index + 2, 0)
                group_layout.addWidget(description_input, row_index + 2, 1)

            group.setLayout(group_layout)
            layout.addWidget(group)

        layout.addStretch()
        content.setLayout(layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setWidget(content)

        page_layout = QVBoxLayout()
        page_layout.addWidget(scroll_area)
        page.setLayout(page_layout)
        self.addPage(page)

    def _build_inventory_currency_page(self) -> None:
        """Builds the starter inventory and currency page."""

        page = QWizardPage()
        page.setTitle("Inventory and Currency")
        page.setSubTitle("Add requested starter items and describe the world's money.")

        self.starter_items_table = QTableWidget(0, 6)
        self.starter_items_table.setHorizontalHeaderLabels(
            ["Name", "Amount", "Category", "Description", "Value", ""]
        )
        self.starter_items_table.setMinimumHeight(170)
        self.starter_items_table.verticalHeader().setVisible(False)
        self.starter_items_table.verticalHeader().setDefaultSectionSize(36)
        self.starter_items_table.horizontalHeader().setStretchLastSection(False)
        self.starter_items_table.setAlternatingRowColors(True)
        self.starter_items_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.starter_items_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _set_table_column_widths(self.starter_items_table, STARTER_ITEM_COLUMN_WIDTHS)

        add_item_button = QPushButton("Add Item")
        add_item_button.clicked.connect(lambda: self._append_starter_item_row({}))

        self.currency_table = QTableWidget(0, 4)
        self.currency_table.setHorizontalHeaderLabels(["Name", "Plural Name", "Base Value", ""])
        self.currency_table.setMinimumHeight(180)
        self.currency_table.verticalHeader().setVisible(False)
        self.currency_table.verticalHeader().setDefaultSectionSize(36)
        self.currency_table.horizontalHeader().setStretchLastSection(False)
        self.currency_table.setAlternatingRowColors(True)
        self.currency_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.currency_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _set_table_column_widths(self.currency_table, CURRENCY_COLUMN_WIDTHS)

        add_currency_button = QPushButton("Add Currency")
        add_currency_button.clicked.connect(lambda: self._append_currency_row({}))

        self.economy_examples_table = QTableWidget(0, 3)
        self.economy_examples_table.setHorizontalHeaderLabels(["Item", "Base Units", ""])
        _configure_inline_table(
            self.economy_examples_table,
            ECONOMY_EXAMPLE_COLUMN_WIDTHS,
            minimum_height=140,
        )

        add_economy_example_button = QPushButton("Add Economy Item")
        add_economy_example_button.clicked.connect(
            lambda: self._append_economy_example_row({})
        )

        layout = QFormLayout()
        layout.addRow("Starter Items:", self.starter_items_table)
        layout.addRow("", add_item_button)
        layout.addRow("Currencies:", self.currency_table)
        layout.addRow("", add_currency_button)
        layout.addRow("Economy Notes:", self.economy_examples_table)
        layout.addRow("", add_economy_example_button)
        page.setLayout(layout)

        self.addPage(page)

    def _build_audio_page(self) -> None:
        """Builds the starting audio preferences page."""

        page = QWizardPage()
        page.setTitle("Audio")
        page.setSubTitle("Choose music preferences before the save starts.")

        self.music_enabled_checkbox = QCheckBox("Music enabled")
        self.music_enabled_checkbox.setChecked(bool(self.audio_defaults["music_enabled"]))

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(int(self.audio_defaults["music_volume"]))
        self.music_volume_label = QLabel(f"{self.music_volume_slider.value()}%")
        self.music_volume_slider.valueChanged.connect(
            lambda value: self.music_volume_label.setText(f"{value}%")
        )

        layout = QFormLayout()
        layout.addRow("Background Music:", self.music_enabled_checkbox)
        layout.addRow("Music Volume:", _slider_row(self.music_volume_slider, self.music_volume_label))

        page.setLayout(layout)

        self.addPage(page)

    def _build_tts_page(self) -> None:
        """Builds the dedicated starting TTS preferences page."""

        page = QWizardPage()
        page.setTitle("TTS")
        page.setSubTitle("Choose narrator speed, voice, and custom blends before the save starts.")

        self.tts_settings_widget = TTSSettingsWidget(
            audio_settings=self.audio_defaults,
            voice_options=self.voice_options,
            on_sample_voice=self._sample_voice,
            on_custom_voice_saved=self.on_tts_settings_saved,
            custom_voice_storage_path=self.custom_voice_storage_path,
        )
        self.narrator_enabled_checkbox = self.tts_settings_widget.narrator_enabled_checkbox
        self.tts_volume_slider = self.tts_settings_widget.tts_volume_slider
        self.tts_volume_label = self.tts_settings_widget.tts_volume_label
        self.tts_speed_slider = self.tts_settings_widget.tts_speed_slider
        self.tts_voice_combo = self.tts_settings_widget.tts_voice_combo
        self.sample_voice_button = self.tts_settings_widget.sample_voice_button

        layout = QVBoxLayout()
        layout.addWidget(self.tts_settings_widget)
        page.setLayout(layout)

        self.addPage(page)

    def _narrator_enabled_value(self) -> bool:
        """Returns the requested new-game narrator setting."""

        if self.narrator_enabled_checkbox is None:
            return False

        return self.narrator_enabled_checkbox.isChecked()

    def _tts_volume_value(self) -> int:
        """Returns the requested new-game narrator volume."""

        if self.tts_volume_slider is None:
            return 0

        return self.tts_volume_slider.value()

    def _tts_voice_value(self) -> str:
        """Returns the selected new-game narrator voice id."""

        return normalize_narrator_voice(
            _combo_current_data_text(self.tts_voice_combo, DEFAULT_NARRATOR_VOICE)
        )

    def _sync_narrator_control_states(self, checked: bool) -> None:
        """Enables narrator-specific controls only when narration is enabled."""

        if self.tts_volume_slider is not None:
            self.tts_volume_slider.setEnabled(checked)
        if self.tts_voice_combo is not None:
            self.tts_voice_combo.setEnabled(checked)
        if self.sample_voice_button is not None:
            self.sample_voice_button.setEnabled(checked and self.on_sample_voice is not None)

    def _sample_voice(
        self,
        voice: str | None = None,
        volume: int | None = None,
        speed: int | None = None,
    ) -> bool | None:
        """Plays the selected narrator voice sample."""

        if self.on_sample_voice is None:
            return None

        return _invoke_sample_voice_callback(
            self.on_sample_voice,
            voice
            or (
                self.tts_settings_widget.active_voice_spec()
                if self.tts_settings_widget is not None
                else self._tts_voice_value()
            ),
            self._tts_volume_value() if volume is None else int(volume),
            DEFAULT_TTS_SPEED_PERCENT if speed is None else int(speed),
        )

    def _tts_settings_value(self) -> dict[str, Any]:
        """Returns new-game TTS settings."""

        if self.tts_settings_widget is None:
            return normalize_tts_audio_fields({}, tts_enabled=False)

        return self.tts_settings_widget.build_audio_settings()

    def _append_starter_item_row(self, item: dict[str, Any]) -> None:
        """Adds a starter item row to the wizard table."""

        _append_starter_item_table_row(
            self.starter_items_table,
            item,
            self._remove_starter_item_row,
        )

    def _remove_starter_item_row(self, button: QPushButton) -> None:
        """Removes the starter item row containing button."""

        _remove_table_row_by_button(self.starter_items_table, button)

    def _starter_items_from_table(self) -> list[dict[str, Any]]:
        """Reads starter item rows from the wizard table."""

        return _starter_items_from_table(self.starter_items_table)

    def _append_currency_row(self, denomination: dict[str, Any]) -> None:
        """Adds a currency denomination row to the wizard table."""

        _append_currency_table_row(
            self.currency_table,
            denomination,
            self._remove_currency_row,
        )

    def _remove_currency_row(self, button: QPushButton) -> None:
        """Removes the currency row containing button."""

        if _remove_table_row_by_button(self.currency_table, button) >= 0:
            self._sync_currency_base_value_row()

    def _sync_currency_base_value_row(self) -> None:
        """Keeps the first visible currency row as the baseline denomination."""

        _sync_currency_base_value_row(self.currency_table)

    def _currency_denominations_from_table(self) -> list[dict[str, Any]]:
        """Reads currency denomination rows from the wizard table."""

        return _currency_denominations_from_table(self.currency_table)

    def _append_economy_example_row(self, example: dict[str, Any]) -> None:
        """Adds a common-price example row to the wizard table."""

        _append_economy_example_table_row(
            self.economy_examples_table,
            example,
            self._remove_economy_example_row,
        )

    def _remove_economy_example_row(self, button: QPushButton) -> None:
        """Removes the common-price example row containing button."""

        _remove_table_row_by_button(self.economy_examples_table, button)

    def _economy_examples_from_table(self) -> list[dict[str, Any]]:
        """Reads common-price examples from the wizard table."""

        return _economy_examples_from_table(self.economy_examples_table)

    def _build_calendar_page(self) -> None:
        """Builds the calendar and time page."""

        page = QWizardPage()
        page.setTitle("Calendar and Time")
        page.setSubTitle("Choose whether to use a standard, custom, or AI-generated calendar.")

        self.calendar_type_combo = QComboBox()
        self.calendar_type_combo.addItem("Default Gregorian Calendar", "gregorian")
        self.calendar_type_combo.addItem("Custom Calendar", "custom")
        self.calendar_type_combo.addItem("AI-Generated Calendar", "ai_generated")
        self.calendar_type_combo.currentIndexChanged.connect(
            lambda _index: self._sync_calendar_settings_button()
        )

        self.calendar_settings_button = QPushButton("Calendar Settings...")
        self.calendar_settings_button.clicked.connect(self._open_wizard_calendar_settings)

        layout = QFormLayout()
        layout.addRow("Calendar:", self.calendar_type_combo)
        layout.addRow("Custom Settings:", self.calendar_settings_button)
        page.setLayout(layout)

        self.addPage(page)
        self._sync_calendar_settings_button()

    def _calendar_settings_for_setup(self, calendar_type: str) -> dict[str, Any]:
        """Returns wizard calendar settings for the selected calendar mode."""

        if calendar_type == "ai_generated":
            return {
                **GREGORIAN_CALENDAR_SETTINGS,
                "calendar_type": "ai_generated",
                "ai_generated": True,
            }

        if calendar_type == "custom":
            return {
                **self._custom_calendar_settings,
                "calendar_type": "custom",
                "ai_generated": False,
            }

        return {
            **GREGORIAN_CALENDAR_SETTINGS,
            "calendar_type": "gregorian",
            "ai_generated": False,
        }

    def _sync_calendar_settings_button(self) -> None:
        """Enables custom calendar editing only for custom new-game calendars."""

        if not hasattr(self, "calendar_settings_button"):
            return

        is_custom = self.calendar_type_combo.currentData() == "custom"
        self.calendar_settings_button.setEnabled(is_custom)

    def _open_wizard_calendar_settings(self) -> None:
        """Opens the shared calendar settings dialog for the custom wizard calendar."""

        dialog = CalendarSettingsDialog(self._custom_calendar_settings, self)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._custom_calendar_settings = dialog.build_settings()


class GameShell(QWidget):
    """In-game shell containing the core play screens."""

    def __init__(
        self,
        on_return_to_menu,
        *,
        on_theme_changed=None,
        sound_manager: SoundManager | None = None,
        narration_player: NarrationPlayer | None = None,
        tts_enabled: bool = True,
        on_app_tts_settings_saved: Callable[[dict[str, Any]], None] | None = None,
        global_tts_settings_provider: Callable[[], dict[str, Any]] | None = None,
        custom_voice_storage_path: Path | str | None = None,
    ) -> None:
        """
        Args:
            on_return_to_menu: Callback for returning to the Main Menu.
        """

        super().__init__()

        self.on_return_to_menu = on_return_to_menu
        self.on_theme_changed = on_theme_changed
        self.sound_manager = sound_manager
        self.narration_player = narration_player
        self.tts_enabled = bool(tts_enabled)
        self.on_app_tts_settings_saved = on_app_tts_settings_saved
        self.global_tts_settings_provider = global_tts_settings_provider
        self.custom_voice_storage_path = custom_voice_storage_path
        self.repository: SaveRepository | None = None

        self.title_label = QLabel("No Save Loaded")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")

        menu_button = QPushButton("Main Menu")
        menu_button.clicked.connect(self.on_return_to_menu)

        top_bar = QHBoxLayout()
        top_bar.addWidget(self.title_label)
        top_bar.addStretch()
        top_bar.addWidget(menu_button)

        self.tabs = QTabWidget()

        self.story_screen = StoryScreen(
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
        )
        self.character_screen = CharacterScreen()
        self.world_screen = WorldScreen()
        self.calendar_screen = CalendarScreen()
        self.inventory_screen = InventoryScreen()
        self.combat_screen = CombatScreen()
        self.npcs_screen = NpcsScreen()
        self.active_tasks_screen = ActiveTasksScreen()
        self.skills_screen = SkillsScreen()
        self.alchemy_screen = AlchemyNotebookScreen()
        self.history_screen = HistoryScreen()
        self.settings_screen = SettingsScreen(
            on_audio_settings_changed=self._apply_audio_settings,
            on_theme_changed=self._apply_theme,
            tts_enabled=self.tts_enabled,
            voice_options=_narrator_voice_options(self.narration_player),
            on_sample_voice=self._sample_narrator_voice,
            on_app_tts_settings_saved=self.on_app_tts_settings_saved,
            global_tts_settings_provider=self.global_tts_settings_provider,
            custom_voice_storage_path=self.custom_voice_storage_path,
        )

        self.screens: list[RepositoryBackedWidget] = [
            self.story_screen,
            self.character_screen,
            self.world_screen,
            self.calendar_screen,
            self.inventory_screen,
            self.combat_screen,
            self.npcs_screen,
            self.active_tasks_screen,
            self.skills_screen,
            self.alchemy_screen,
            self.history_screen,
            self.settings_screen,
        ]

        for screen in self.screens:
            screen.on_repository_changed = self._handle_screen_repository_changed

        self.tabs.addTab(self.story_screen, "Story")
        self.tabs.addTab(self.character_screen, "Character")
        self.tabs.addTab(self.world_screen, "World")
        self.tabs.addTab(self.calendar_screen, "Calendar")
        self.tabs.addTab(self.inventory_screen, "Inventory")
        self.tabs.addTab(self.combat_screen, "Combat")
        self.tabs.addTab(self.npcs_screen, "NPCs")
        self.tabs.addTab(self.active_tasks_screen, "Active Tasks")
        self.tabs.addTab(self.skills_screen, "Skills")
        self.tabs.addTab(self.alchemy_screen, "Crafting")
        self.tabs.addTab(self.history_screen, "Journal")
        self.tabs.addTab(self.settings_screen, "Settings")
        self.tabs.currentChanged.connect(self._handle_tab_changed)

        layout = QVBoxLayout()
        layout.addLayout(top_bar)
        layout.addWidget(self.tabs)

        self.setLayout(layout)

    def set_repository(self, repository: SaveRepository | None) -> None:
        """
        Sets the active save repository for every screen.

        Args:
            repository: Active save repository, or None when returning to menu.
        """

        self.repository = repository

        if repository is None:
            self.title_label.setText("No Save Loaded")
        else:
            title = repository.get_meta("title", default="Untitled Adventure")
            self.title_label.setText(title)

        for screen in self.screens:
            screen.set_repository(repository)

        self._apply_audio_settings()

    def refresh_screens(
        self,
        *,
        exclude: set[RepositoryBackedWidget] | None = None,
    ) -> None:
        """Refreshes tabs from saved data while preserving each screen's local state."""

        excluded_screens = exclude or set()

        for screen in self.screens:
            if screen in excluded_screens:
                continue

            screen.refresh()

    def _handle_screen_repository_changed(self, source: RepositoryBackedWidget) -> None:
        """Refreshes tabs after a screen or event changes repository data."""

        self._apply_audio_settings()
        self.refresh_screens(exclude={source})

    def _handle_tab_changed(self, index: int) -> None:
        """Resets the calendar view to the current month when opened."""

        if self.tabs.widget(index) == self.calendar_screen:
            self.calendar_screen.return_to_current_month()

    def _apply_audio_settings(self) -> None:
        """Applies saved audio settings to the active audio managers."""

        if self.repository is None:
            if self.sound_manager is not None:
                self.sound_manager.stop_music()
            if self.narration_player is not None:
                self.narration_player.stop()
            return

        _apply_audio_settings_to_managers(
            self.repository,
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
        )

    def _sample_narrator_voice(
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

    def _apply_theme(self) -> None:
        """Notifies the main window that save-specific theme settings changed."""

        if self.on_theme_changed is not None:
            self.on_theme_changed()


class StoryScreen(RepositoryBackedWidget):
    """Story screen for player input and narrative output."""

    _narration_chunk_ready = Signal(int, str)
    _narration_complete = Signal(int)

    def __init__(
        self,
        *,
        sound_manager: SoundManager | None = None,
        narration_player: NarrationPlayer | None = None,
    ) -> None:
        super().__init__()

        self.sound_manager = sound_manager
        self.narration_player = narration_player
        self._revealing_story_id: int | None = None
        self._revealed_story_chunks: list[str] = []
        self._story_reveal_generation = 0
        self._gemini_thread: QThread | None = None
        self._gemini_worker: QObject | None = None
        self._pending_skill_check_event_results: list[Any] = []
        self._waiting_for_gm = False
        self._combat_active = False
        self._default_input_placeholder = "Enter a player action..."
        self._narration_chunk_ready.connect(self._append_revealed_story_chunk)
        self._narration_complete.connect(self._complete_revealed_story)
        self.location_value = QLabel("-")
        self.day_value = QLabel("-")
        self.time_value = QLabel("-")
        self.weather_value = QLabel("-")

        status_row = QHBoxLayout()
        status_row.addWidget(_status_label("Location", self.location_value))
        status_row.addWidget(_status_label("Day", self.day_value))
        status_row.addWidget(_status_label("Time", self.time_value))
        status_row.addWidget(_status_label("Weather", self.weather_value))
        status_row.addStretch()

        self.story_output = QTextEdit()
        self.story_output.setReadOnly(True)

        self.player_input = QLineEdit()
        self.player_input.setPlaceholderText(self._default_input_placeholder)

        self.submit_button = QPushButton("Submit")
        self.submit_button.clicked.connect(self._submit_player_action)
        self.player_input.returnPressed.connect(self._submit_player_action)

        self.continue_button = QPushButton("Continue")
        self.continue_button.setToolTip("Ask the GM to expand the latest response.")
        self.continue_button.clicked.connect(self._continue_story_response)
        self.continue_button.setEnabled(False)

        input_row = QHBoxLayout()
        input_row.addWidget(self.player_input)
        input_row.addWidget(self.submit_button)
        input_row.addWidget(self.continue_button)

        layout = QVBoxLayout()
        layout.addLayout(status_row)
        layout.addWidget(self.story_output)
        layout.addLayout(input_row)

        self.setLayout(layout)

    def set_repository(self, repository: SaveRepository | None) -> None:
        """Sets the active save and clears any stale narration reveal state."""

        self._clear_story_reveal_state()
        super().set_repository(repository)

    def refresh(self) -> None:
        """Refreshes the story output from history."""

        repository = self.repository()

        if repository is None:
            self.story_output.clear()
            self.location_value.setText("-")
            self.day_value.setText("-")
            self.time_value.setText("-")
            self.weather_value.setText("-")
            self._combat_active = False
            self._sync_story_input_state()
            self._update_continue_button_state()
            return

        state = StateManager(repository).load_state()
        self._combat_active = repository.is_combat_active()
        self.location_value.setText(state.world.location or "-")
        self.day_value.setText(state.calendar.date_label or "-")
        self.time_value.setText(state.calendar.time_label or "-")
        self.weather_value.setText(state.world.weather or "-")

        entries = repository.list_history()
        story_lines: list[str] = []

        for entry in entries:
            kind = str(entry.get("kind", "misc")).casefold()
            content = str(entry.get("content", ""))

            if kind == "player":
                story_lines.append(_player_command_markdown(content))
            elif kind == "story":
                entry_id = _safe_int(entry.get("id"), -1)

                if entry_id == self._revealing_story_id:
                    if self._revealed_story_chunks:
                        story_lines.append("\n\n".join(self._revealed_story_chunks))
                else:
                    story_lines.append(format_story_message(content))

        output = "\n\n".join(story_lines)
        _set_markdown_text(self.story_output, output)
        self.story_output.moveCursor(self.story_output.textCursor().MoveOperation.End)
        self._sync_story_input_state()
        self._update_continue_button_state()

    def _submit_player_action(self) -> None:
        """Records a player action and requests AI narration when configured."""

        if self._waiting_for_gm or self._combat_active:
            return

        repository = self.repository()

        if repository is None:
            return

        player_text = self.player_input.text().strip()

        if not player_text:
            LOGGER.warning("Skipped blank player action.")
            return

        repository.append_history("player", player_text)
        self.player_input.clear()
        self._pending_skill_check_event_results = []
        self._set_waiting_for_gm(True)
        self.refresh()

        context_packet = self._build_story_context_packet(repository, player_text)

        self._start_skill_check_planning_request(context_packet)

    def _continue_story_response(self) -> None:
        """Requests a fuller continuation of the latest story response."""

        if self._waiting_for_gm or self._combat_active:
            return

        repository = self.repository()

        if repository is None:
            return

        latest_story = self._latest_story_entry()

        if latest_story is None:
            LOGGER.warning("Skipped story continuation without a prior story response.")
            return

        player_text = self._latest_player_command() or "Continue the previous narration."
        self._pending_skill_check_event_results = []
        self._set_waiting_for_gm(True)
        self.refresh()

        context_packet = self._build_story_context_packet(repository, player_text)
        context_packet["continuation_request"] = {
            "active": True,
            "instruction": CONTINUE_STORY_INSTRUCTION,
            "latest_story_response": str(latest_story.get("content", "")).strip()[:4000],
        }

        self._start_gemini_story_request(context_packet)

    def _build_story_context_packet(
        self,
        repository: SaveRepository,
        player_text: str,
        *,
        resolved_skill_checks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Builds the Gemini story context packet for the current save."""

        state = StateManager(repository).load_state()
        relevant_npcs = repository.list_relevant_npcs(
            location=state.world.location,
            query_text=player_text,
        )
        valid_music_tracks = (
            self.sound_manager.get_valid_track_names()
            if self.sound_manager is not None
            else []
        )
        return AiContextBuilder.from_default_library().build_story_context(
            state,
            player_command=player_text,
            relevant_npcs=relevant_npcs,
            valid_music_tracks=valid_music_tracks,
            current_music=str(repository.get_setting("audio.current_music", "")),
            resolved_skill_checks=resolved_skill_checks,
        )

    def _start_skill_check_planning_request(self, context_packet: dict[str, Any]) -> None:
        """Starts one background pre-narration skill-check planning request."""

        thread = QThread(self)
        worker = _GeminiSkillCheckPlanWorker(context_packet)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.completed.connect(self._handle_skill_check_plan_result)
        worker.configuration_error.connect(self._handle_gemini_configuration_error)
        worker.failed.connect(self._handle_gemini_story_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda thread=thread, worker=worker: self._clear_gemini_worker(thread, worker)
        )

        self._gemini_thread = thread
        self._gemini_worker = worker
        thread.start()

    @Slot(object)
    def _handle_skill_check_plan_result(self, plan_result: Any) -> None:
        """Applies planned skill checks, then starts the full narration request."""

        repository = self.repository()

        if repository is None:
            self._pending_skill_check_event_results = []
            self._set_waiting_for_gm(False)
            return

        player_text = self._latest_player_command()

        if not player_text:
            LOGGER.warning("Skill-check plan finished without a player command.")
            self._set_waiting_for_gm(False)
            return

        planned_checks = [
            check
            for check in getattr(plan_result, "checks", [])
            if isinstance(check, dict) and str(check.get("skill_name", "")).strip()
        ]
        check_events = [
            {
                "type": "SkillCheckRequestedEvent",
                "payload": check,
            }
            for check in planned_checks
        ]

        if check_events:
            self._pending_skill_check_event_results = EventApplier(repository).apply_events(
                check_events
            )
            LOGGER.info(
                "Applied %s pre-narration skill check(s).",
                len(self._pending_skill_check_event_results),
            )
            self.notify_repository_changed()
        else:
            self._pending_skill_check_event_results = []

        resolved_skill_checks = _resolved_skill_checks_for_context(
            self._pending_skill_check_event_results
        )
        context_packet = self._build_story_context_packet(
            repository,
            player_text,
            resolved_skill_checks=resolved_skill_checks,
        )

        self._start_gemini_story_request(context_packet)

    def _start_gemini_story_request(self, context_packet: dict[str, Any]) -> None:
        """Starts one background Gemini story request."""

        thread = QThread(self)
        worker = _GeminiStoryWorker(context_packet)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.completed.connect(self._handle_gemini_story_result)
        worker.configuration_error.connect(self._handle_gemini_configuration_error)
        worker.failed.connect(self._handle_gemini_story_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda thread=thread, worker=worker: self._clear_gemini_worker(thread, worker)
        )

        self._gemini_thread = thread
        self._gemini_worker = worker
        thread.start()

    @Slot(object)
    def _handle_gemini_story_result(self, result: Any) -> None:
        """Stores and displays a completed Gemini narration result."""

        repository = self.repository()

        if repository is None:
            self._set_waiting_for_gm(False)
            return

        repository.append_history("story", result.narrative_text)

        if result.suggested_events:
            event_results = EventApplier(repository).apply_events(
                result.suggested_events,
                prior_results=self._pending_skill_check_event_results,
            )
            applied_count = sum(
                1 for event_result in event_results if event_result.status == "applied"
            )
            skipped_count = len(event_results) - applied_count
            LOGGER.info(
                "Applied %s Gemini event(s); skipped %s.",
                applied_count,
                skipped_count,
            )
            _apply_audio_settings_to_managers(
                repository,
                sound_manager=self.sound_manager,
                narration_player=self.narration_player,
            )
            self.notify_repository_changed()

        self._pending_skill_check_event_results = []
        latest_story = self._latest_story_entry()

        if latest_story is not None and self._reveal_story_with_narration(
            int(latest_story["id"]),
            result.narrative_text,
        ):
            return

        self.refresh()
        self._set_waiting_for_gm(False)

    @Slot(str)
    def _handle_gemini_configuration_error(self, _message: str) -> None:
        """Displays the configured-no-key fallback after a recorded player action."""

        repository = self.repository()
        self._pending_skill_check_event_results = []

        if repository is not None:
            repository.append_history(
                "story",
                (
                    "No Gemini API key is configured yet. "
                    "This action was recorded successfully."
                ),
            )

        self.refresh()
        self._set_waiting_for_gm(False)

    @Slot()
    def _handle_gemini_story_failure(self) -> None:
        """Displays the generic Gemini failure fallback."""

        repository = self.repository()
        self._pending_skill_check_event_results = []

        if repository is not None:
            repository.append_history(
                "story",
                "The narration falters for a moment. Check the application log for details.",
            )

        self.refresh()
        self._set_waiting_for_gm(False)

    @Slot()
    def _clear_gemini_worker(
        self,
        thread: QThread | None = None,
        worker: QObject | None = None,
    ) -> None:
        """Drops references after the Gemini request thread exits."""

        if thread is None or self._gemini_thread is thread:
            self._gemini_thread = None

        if worker is None or self._gemini_worker is worker:
            self._gemini_worker = None

    def _set_waiting_for_gm(self, waiting: bool) -> None:
        """Toggles player input while Gemini or narration is still working."""

        self._waiting_for_gm = waiting
        self._sync_story_input_state()
        self._update_continue_button_state()

        if waiting:
            self.player_input.setPlaceholderText(GM_THINKING_TEXT)
            self.player_input.setToolTip(GM_THINKING_TEXT)
            self.submit_button.setToolTip(GM_THINKING_TEXT)
            self.continue_button.setToolTip(GM_THINKING_TEXT)
        elif self._combat_active:
            tooltip = "Resolve the active combat in the Combat tab before sending story actions."
            self.player_input.setPlaceholderText("Combat is active...")
            self.player_input.setToolTip(tooltip)
            self.submit_button.setToolTip(tooltip)
            self.continue_button.setToolTip(tooltip)
        else:
            self.player_input.setPlaceholderText(self._default_input_placeholder)
            self.player_input.setToolTip("")
            self.submit_button.setToolTip("")
            self.continue_button.setToolTip("Ask the GM to expand the latest response.")

    def _sync_story_input_state(self) -> None:
        """Enables story input only when GM and combat state permit it."""

        can_submit = not self._waiting_for_gm and not self._combat_active
        self.player_input.setEnabled(can_submit)
        self.submit_button.setEnabled(can_submit)

        if self._waiting_for_gm:
            self.player_input.setPlaceholderText(GM_THINKING_TEXT)
            self.player_input.setToolTip(GM_THINKING_TEXT)
            self.submit_button.setToolTip(GM_THINKING_TEXT)
            self.continue_button.setToolTip(GM_THINKING_TEXT)
            return

        if self._combat_active:
            tooltip = "Resolve the active combat in the Combat tab before sending story actions."
            self.player_input.setPlaceholderText("Combat is active...")
            self.player_input.setToolTip(tooltip)
            self.submit_button.setToolTip(tooltip)
            self.continue_button.setToolTip(tooltip)
            return

        self.player_input.setPlaceholderText(self._default_input_placeholder)
        self.player_input.setToolTip("")
        self.submit_button.setToolTip("")
        self.continue_button.setToolTip("Ask the GM to expand the latest response.")

    def _update_continue_button_state(self) -> None:
        """Enables Continue only when there is a story response to expand."""

        if not hasattr(self, "continue_button"):
            return

        self.continue_button.setEnabled(
            not self._waiting_for_gm
            and not self._combat_active
            and self.repository() is not None
            and self._latest_story_entry() is not None
        )

    def set_initial_generation_pending(self, pending: bool) -> None:
        """Toggles the story input while the opening scene is generated."""

        self._set_waiting_for_gm(pending)

    def narrate_latest_story(self) -> None:
        """Narrates the latest story history entry when narrator is enabled."""

        repository = self.repository()

        if repository is None:
            return

        entries = repository.list_history()

        for entry in reversed(entries):
            if str(entry.get("kind", "")).casefold() == "story":
                _apply_audio_settings_to_managers(
                    repository,
                    sound_manager=self.sound_manager,
                    narration_player=self.narration_player,
                )
                self.refresh()
                self._narrate_text(str(entry.get("content", "")))
                return

    def _narrate_text(
        self,
        text: str,
        *,
        story_id: int | None = None,
    ) -> bool:
        """Sends text to the narration player if available."""

        if self.narration_player is None:
            return False

        if story_id is None:
            return self.narration_player.narrate(text)

        return self.narration_player.narrate(
            text,
            on_chunk_start=lambda chunk: self._narration_chunk_ready.emit(
                story_id,
                chunk,
            ),
            on_complete=lambda: self._narration_complete.emit(story_id),
        )

    def _reveal_story_with_narration(self, story_id: int, text: str) -> bool:
        """Displays the latest story progressively as TTS starts each chunk."""

        self._revealing_story_id = story_id
        self._revealed_story_chunks = []
        self._story_reveal_generation += 1
        reveal_generation = self._story_reveal_generation
        self.refresh()

        if self._narrate_text(text, story_id=story_id):
            QTimer.singleShot(
                STORY_REVEAL_STALL_TIMEOUT_MS,
                lambda: self._recover_stalled_story_reveal(
                    story_id,
                    reveal_generation,
                ),
            )
            return True

        self._clear_story_reveal_state()
        return False

    def _append_revealed_story_chunk(self, story_id: int, chunk: str) -> None:
        """Appends one just-started narration chunk to the story output."""

        if story_id != self._revealing_story_id:
            return

        clean_chunk = str(chunk or "").strip()

        if not clean_chunk:
            return

        self._revealed_story_chunks.append(clean_chunk)
        self.refresh()

    def _complete_revealed_story(self, story_id: int) -> None:
        """Restores normal full-history rendering after chunked narration."""

        if story_id != self._revealing_story_id:
            return

        self._clear_story_reveal_state()
        self.refresh()
        self._set_waiting_for_gm(False)

    def _recover_stalled_story_reveal(
        self,
        story_id: int,
        reveal_generation: int,
    ) -> None:
        """Shows the saved story if narration starts but never reveals chunks."""

        if story_id != self._revealing_story_id:
            return

        if reveal_generation != self._story_reveal_generation:
            return

        if self._revealed_story_chunks:
            return

        LOGGER.warning(
            "Narration reveal for story entry %s produced no visible chunks; "
            "showing the saved story text.",
            story_id,
        )
        self._clear_story_reveal_state()
        self.refresh()
        self._set_waiting_for_gm(False)

    def _clear_story_reveal_state(self) -> None:
        """Clears progressive story reveal state."""

        self._story_reveal_generation += 1
        self._revealing_story_id = None
        self._revealed_story_chunks = []

    def _latest_story_entry(self) -> dict[str, Any] | None:
        """Returns the most recent saved story entry."""

        repository = self.repository()

        if repository is None:
            return None

        for entry in reversed(repository.list_history()):
            if str(entry.get("kind", "")).casefold() == "story":
                return entry

        return None

    def _latest_player_command(self) -> str:
        """Returns the most recent saved player command."""

        repository = self.repository()

        if repository is None:
            return ""

        for entry in reversed(repository.list_history()):
            if str(entry.get("kind", "")).casefold() == "player":
                return str(entry.get("content", "")).strip()

        return ""


class CharacterScreen(RepositoryBackedWidget):
    """Dungeons-and-Dragons-style character sheet."""

    def __init__(self) -> None:
        super().__init__()

        self._loading_character = False
        self.name_input = QLineEdit()
        self.health_current_input = QSpinBox()
        self.health_current_input.setRange(0, 9999)
        self.health_current_input.valueChanged.connect(lambda _value: self._sync_health_bounds())
        self.health_max_input = QSpinBox()
        self.health_max_input.setRange(1, 9999)
        self.health_max_input.setValue(DEFAULT_PLAYER_MAX_HEALTH)
        self.health_max_input.valueChanged.connect(lambda _value: self._sync_health_bounds())
        self.armor_rating_label = QLabel(str(DEFAULT_BASE_ARMOR_RATING))
        self.weapon_damage_label = QLabel(DEFAULT_UNARMED_DAMAGE)
        self.equipment_combos: dict[str, QComboBox] = {}

        self.appearance_input = QTextEdit()
        self.backstory_input = QTextEdit()
        self.notes_input = QTextEdit()

        self.appearance_input.setPlaceholderText("Visible traits, clothing, manner, scars, voice...")
        self.backstory_input.setPlaceholderText("Origin, important history, relationships, goals...")
        self.notes_input.setPlaceholderText("Player notes about this character...")

        self.body_map_label = QLabel(
            "\n".join(
                [
                    "           [Head]",
                    "             O",
                    "      [Arms] /|\\ [Hands]",
                    "           / | \\",
                    "        [Torso]",
                    "            |",
                    "          /   \\",
                    "       [Legs] [Feet]",
                    "",
                    "Main Hand / Off Hand",
                ]
            )
        )
        self.body_map_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body_map_label.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

        for slot in EQUIPMENT_SLOTS:
            combo = QComboBox()
            combo.currentIndexChanged.connect(lambda _index, slot=slot: self._equipment_changed(slot))
            self.equipment_combos[slot] = combo

        save_button = QPushButton("Save Character Sheet")
        save_button.clicked.connect(self._save_character)

        identity_group = QGroupBox("Identity")
        identity_layout = QFormLayout()
        identity_layout.addRow("Name:", self.name_input)
        identity_layout.addRow("Appearance:", self.appearance_input)
        identity_layout.addRow("Backstory:", self.backstory_input)
        identity_layout.addRow("Notes:", self.notes_input)
        identity_group.setLayout(identity_layout)

        stats_group = QGroupBox("Vitals")
        stats_layout = QFormLayout()
        stats_layout.addRow("Health:", _spin_pair_row(self.health_current_input, self.health_max_input))
        stats_layout.addRow("Armor Rating:", self.armor_rating_label)
        stats_layout.addRow("Weapon Damage:", self.weapon_damage_label)
        stats_group.setLayout(stats_layout)

        equipment_group = QGroupBox("Equipment")
        equipment_layout = QFormLayout()
        for slot in EQUIPMENT_SLOTS:
            equipment_layout.addRow(f"{slot}:", self.equipment_combos[slot])
        equipment_group.setLayout(equipment_layout)

        left_layout = QVBoxLayout()
        left_layout.addWidget(stats_group)
        left_layout.addWidget(self.body_map_label)
        left_layout.addStretch()

        right_layout = QVBoxLayout()
        right_layout.addWidget(identity_group)
        right_layout.addWidget(equipment_group)
        right_layout.addWidget(save_button)

        sheet_layout = QHBoxLayout()
        sheet_layout.addLayout(left_layout)
        sheet_layout.addLayout(right_layout, stretch=1)

        self.setLayout(sheet_layout)

    def refresh(self) -> None:
        """Reloads the character sheet."""

        repository = self.repository()
        self._loading_character = True

        try:
            if repository is None:
                self.name_input.clear()
                self.appearance_input.clear()
                self.backstory_input.clear()
                self.notes_input.clear()
                self.health_current_input.setValue(DEFAULT_PLAYER_MAX_HEALTH)
                self.health_max_input.setValue(DEFAULT_PLAYER_MAX_HEALTH)
                self._populate_equipment_combos([], empty_equipment())
                self._sync_equipment_summary()
                return

            state = StateManager(repository).load_state()
            inventory_items = repository.list_inventory_items()
            equipment = normalize_equipment(repository.get_player_equipment(), inventory_items)
            armor_rating = armor_rating_from_equipment(equipment, inventory_items)
            repository.set_setting("player.armor_rating", armor_rating)

            self.name_input.setText(state.player.name)
            self.appearance_input.setPlainText(state.player.appearance)
            self.backstory_input.setPlainText(state.player.backstory)
            self.notes_input.setPlainText(state.player.notes)
            self.health_max_input.setValue(max(1, int(state.player.health_max)))
            self.health_current_input.setValue(
                max(0, min(int(state.player.health_current), self.health_max_input.value()))
            )
            self._populate_equipment_combos(inventory_items, equipment)
            self._sync_equipment_summary()
        finally:
            self._loading_character = False

    def _save_character(self) -> None:
        """Persists the editable character sheet."""

        repository = self.repository()

        if repository is None:
            return

        inventory_items = repository.list_inventory_items()
        equipment = normalize_equipment(
            {
                slot: self.equipment_combos[slot].currentData() or ""
                for slot in EQUIPMENT_SLOTS
            },
            inventory_items,
        )
        armor_rating = armor_rating_from_equipment(equipment, inventory_items)
        health_max = max(1, self.health_max_input.value())
        health_current = max(0, min(self.health_current_input.value(), health_max))

        repository.set_setting("player_name", self.name_input.text().strip())
        repository.set_setting("player.appearance", self.appearance_input.toPlainText().strip())
        repository.set_setting("player.backstory", self.backstory_input.toPlainText().strip())
        repository.set_setting("player.notes", self.notes_input.toPlainText().strip())
        repository.set_setting("player.health_current", health_current)
        repository.set_setting("player.health_max", health_max)
        repository.set_setting("player.armor_rating", armor_rating)
        repository.set_player_equipment(equipment)
        self._sync_player_combatant(repository, health_current, health_max, armor_rating)
        repository.append_history("system", "Character sheet updated.")
        self.refresh()
        self.notify_repository_changed()

        QMessageBox.information(self, "Character Saved", "Character sheet was saved.")

    def _populate_equipment_combos(
        self,
        inventory_items: list[dict[str, Any]],
        equipment: dict[str, str],
    ) -> None:
        """Reloads all equipment dropdown choices."""

        for slot, combo in self.equipment_combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("None", "")

            for item in inventory_items:
                if item_is_valid_for_slot(item, slot):
                    combo.addItem(str(item.get("name", "")), str(item.get("name", "")))

            _set_combo_to_data(combo, equipment.get(slot, ""))
            combo.blockSignals(False)

    def _equipment_changed(self, slot: str) -> None:
        """Enforces equipment constraints when a dropdown changes."""

        if self._loading_character:
            return

        if slot == "Main Hand":
            main_name = str(self.equipment_combos["Main Hand"].currentData() or "")
            main_item = self._inventory_item_by_name(main_name)

            if main_item is not None and item_metadata(main_item).get("weapon_hands") == "two-handed":
                _set_combo_to_data(self.equipment_combos["Off Hand"], "")

        if slot == "Off Hand":
            off_name = str(self.equipment_combos["Off Hand"].currentData() or "")
            off_item = self._inventory_item_by_name(off_name)

            if off_item is not None and item_metadata(off_item).get("weapon_hands") == "two-handed":
                _set_combo_to_data(self.equipment_combos["Off Hand"], "")

        self._sync_equipment_summary()

    def _inventory_item_by_name(self, name: str) -> dict[str, Any] | None:
        """Finds one current inventory item by name."""

        repository = self.repository()

        if repository is None or not name:
            return None

        for item in repository.list_inventory_items():
            if str(item.get("name", "")).casefold() == name.casefold():
                return item

        return None

    def _sync_health_bounds(self) -> None:
        """Keeps current health inside max health."""

        if self.health_current_input.value() > self.health_max_input.value():
            self.health_current_input.setValue(self.health_max_input.value())

    def _sync_equipment_summary(self) -> None:
        """Updates computed armor and weapon labels."""

        repository = self.repository()
        inventory_items = repository.list_inventory_items() if repository is not None else []
        equipment = normalize_equipment(
            {
                slot: self.equipment_combos[slot].currentData() or ""
                for slot in EQUIPMENT_SLOTS
            },
            inventory_items,
        )
        self.armor_rating_label.setText(str(armor_rating_from_equipment(equipment, inventory_items)))
        self.weapon_damage_label.setText(equipped_weapon_damage(equipment, inventory_items))

    def _sync_player_combatant(
        self,
        repository: SaveRepository,
        health_current: int,
        health_max: int,
        armor_rating: int,
    ) -> None:
        """Updates the persisted player combatant when combat is active."""

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        for combatant in combat_state["combatants"]:
            if combatant.get("id") != "player":
                continue

            combatant["name"] = self.name_input.text().strip() or combatant.get("name", "Player")
            combatant["current_health"] = health_current
            combatant["max_health"] = health_max
            combatant["armor_rating"] = armor_rating
            combatant["damage"] = equipped_weapon_damage(
                repository.get_player_equipment(),
                repository.list_inventory_items(),
            )
            combatant["defeated"] = health_current <= 0
            break

        repository.set_combat_state(combat_state)


class CombatScreen(RepositoryBackedWidget):
    """Deterministic saved combat manager."""

    def __init__(self) -> None:
        super().__init__()

        self.status_label = QLabel("No active combat.")
        self.combatants_table = QTableWidget(0, 7)
        self.combatants_table.setHorizontalHeaderLabels(
            ["Turn", "Name", "Team", "Health", "Armor", "Damage", "Loot/Status"]
        )
        self.combatants_table.horizontalHeader().setStretchLastSection(True)
        self.combatants_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.combatants_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        self.target_combo = QComboBox()
        self.attack_button = QPushButton("Attack / Resolve Turn")
        self.attack_button.clicked.connect(self._resolve_current_turn)
        self.end_turn_button = QPushButton("End Turn")
        self.end_turn_button.clicked.connect(self._end_turn_without_attack)
        self.resolve_button = QPushButton("Mark Combat Resolved")
        self.resolve_button.clicked.connect(self._resolve_combat_manually)

        self.team_combo = QComboBox()
        self.team_combo.addItem("Enemy", "enemy")
        self.team_combo.addItem("Player Party", "party")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Bandit, wolf, guard ally...")
        self.health_input = QSpinBox()
        self.health_input.setRange(1, 9999)
        self.health_input.setValue(8)
        self.armor_input = QSpinBox()
        self.armor_input.setRange(1, 99)
        self.armor_input.setValue(10)
        self.damage_input = QLineEdit("1d6")
        self.loot_input = QLineEdit()
        self.loot_input.setPlaceholderText("Optional loot names separated by commas")
        self.add_combatant_button = QPushButton("Add Combatant")
        self.add_combatant_button.clicked.connect(self._add_combatant)
        self.start_button = QPushButton("Start Combat")
        self.start_button.clicked.connect(self._start_combat)

        self.adjust_target_combo = QComboBox()
        self.adjust_amount_input = QSpinBox()
        self.adjust_amount_input.setRange(1, 9999)
        self.adjust_amount_input.setValue(1)
        self.damage_button = QPushButton("Apply Damage")
        self.damage_button.clicked.connect(lambda: self._adjust_health(-self.adjust_amount_input.value()))
        self.heal_button = QPushButton("Heal")
        self.heal_button.clicked.connect(lambda: self._adjust_health(self.adjust_amount_input.value()))

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        action_group = QGroupBox("Current Turn")
        action_layout = QFormLayout()
        action_layout.addRow("Target:", self.target_combo)
        action_layout.addRow(_button_row(self.attack_button, self.end_turn_button, self.resolve_button))
        action_group.setLayout(action_layout)

        add_group = QGroupBox("Combatants")
        add_layout = QFormLayout()
        add_layout.addRow("Team:", self.team_combo)
        add_layout.addRow("Name:", self.name_input)
        add_layout.addRow("Health:", self.health_input)
        add_layout.addRow("Armor Rating:", self.armor_input)
        add_layout.addRow("Damage:", self.damage_input)
        add_layout.addRow("Loot:", self.loot_input)
        add_layout.addRow(_button_row(self.start_button, self.add_combatant_button))
        add_group.setLayout(add_layout)

        adjust_group = QGroupBox("Damage and Recovery")
        adjust_layout = QFormLayout()
        adjust_layout.addRow("Combatant:", self.adjust_target_combo)
        adjust_layout.addRow("Amount:", self.adjust_amount_input)
        adjust_layout.addRow(_button_row(self.damage_button, self.heal_button))
        adjust_group.setLayout(adjust_layout)

        controls = QVBoxLayout()
        controls.addWidget(action_group)
        controls.addWidget(add_group)
        controls.addWidget(adjust_group)
        controls.addStretch()

        main_row = QHBoxLayout()
        main_row.addWidget(self.combatants_table, stretch=2)
        main_row.addLayout(controls, stretch=1)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addLayout(main_row)
        layout.addWidget(QLabel("Combat Log"))
        layout.addWidget(self.log_output)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads saved combat state."""

        repository = self.repository()

        if repository is None:
            self.status_label.setText("No active combat.")
            self.combatants_table.setRowCount(0)
            self.target_combo.clear()
            self.adjust_target_combo.clear()
            self.log_output.clear()
            self._sync_buttons(False)
            return

        combat_state = repository.get_combat_state()
        self._render_combat_state(combat_state)

    def _start_combat(self) -> None:
        """Starts deterministic combat with the player and first opponent."""

        repository = self.repository()

        if repository is None:
            return

        state = StateManager(repository).load_state()
        inventory_items = repository.list_inventory_items()
        equipment = repository.get_player_equipment()
        armor_rating = armor_rating_from_equipment(equipment, inventory_items)
        player = {
            "id": "player",
            "name": state.player.name or "Player",
            "team": "party",
            "current_health": max(0, int(state.player.health_current)),
            "max_health": max(1, int(state.player.health_max)),
            "armor_rating": armor_rating,
            "damage": equipped_weapon_damage(equipment, inventory_items),
            "status_effects": [],
            "loot": [],
            "defeated": int(state.player.health_current) <= 0,
        }
        enemy = self._combatant_from_inputs(
            default_team="enemy",
            fallback_name="Enemy",
            use_selected_team=False,
        )
        combat_state = {
            "active": True,
            "round": 1,
            "turn_index": 0,
            "combatants": [player, enemy],
            "log": [f"Combat begins: {player['name']} faces {enemy['name']}."],
        }
        repository.set_combat_state(combat_state)
        repository.append_history("system", "Combat started.")
        self.refresh()
        self.notify_repository_changed()

    def _add_combatant(self) -> None:
        """Adds a party member or enemy to active combat."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            self._start_combat()
            return

        combatant = self._combatant_from_inputs(
            default_team=str(self.team_combo.currentData() or "enemy"),
            fallback_name="Combatant",
            index=len(combat_state["combatants"]) + 1,
        )
        combat_state["combatants"].append(combatant)
        combat_state["log"].append(f"{combatant['name']} joins the fight.")
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_current_turn(self) -> None:
        """Resolves the current combatant's attack."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        combatants = combat_state["combatants"]
        turn_index = int(combat_state["turn_index"])
        actor = combatants[turn_index]

        if actor.get("defeated"):
            self._advance_turn(combat_state)
            repository.set_combat_state(combat_state)
            self.refresh()
            self.notify_repository_changed()
            return

        target = self._target_for_actor(actor, combatants)

        if target is None:
            self._resolve_combat(repository, combat_state)
            return

        attack_roll = random.randint(1, 20)
        target_armor = int(target.get("armor_rating", 10))
        hit = attack_roll == 20 or (attack_roll != 1 and attack_roll >= target_armor)

        if hit:
            damage, damage_detail = roll_damage_expression(actor.get("damage", DEFAULT_UNARMED_DAMAGE))
            target["current_health"] = max(0, int(target["current_health"]) - damage)
            target["defeated"] = target["current_health"] <= 0
            combat_state["log"].append(
                f"{actor['name']} hits {target['name']} with {attack_roll} vs AR {target_armor}, "
                f"dealing {damage} damage [{damage_detail}]."
            )

            if target["defeated"]:
                combat_state["log"].append(f"{target['name']} is defeated.")
        else:
            combat_state["log"].append(
                f"{actor['name']} misses {target['name']} with {attack_roll} vs AR {target_armor}."
            )

        self._sync_player_health_from_combat(repository, combat_state)

        if combat_team_defeated(combatants, "enemy") or combat_team_defeated(combatants, "party"):
            self._resolve_combat(repository, combat_state)
            return

        self._advance_turn(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _end_turn_without_attack(self) -> None:
        """Skips the active combatant's turn."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        actor = combat_state["combatants"][int(combat_state["turn_index"])]
        combat_state["log"].append(f"{actor['name']} holds position.")
        self._advance_turn(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_combat_manually(self) -> None:
        """Marks combat resolved without more attacks."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        combat_state["log"].append("Combat is marked resolved.")
        combat_state["active"] = False
        self._sync_player_health_from_combat(repository, combat_state)
        repository.set_combat_state(combat_state)
        repository.append_history("system", "Combat resolved.")
        self.refresh()
        self.notify_repository_changed()

    def _adjust_health(self, delta: int) -> None:
        """Applies direct damage or healing to a combatant."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("combatants"):
            return

        combatant_id = str(self.adjust_target_combo.currentData() or "")

        for combatant in combat_state["combatants"]:
            if combatant.get("id") != combatant_id:
                continue

            old_health = int(combatant["current_health"])
            new_health = max(0, min(old_health + delta, int(combatant["max_health"])))
            combatant["current_health"] = new_health
            combatant["defeated"] = new_health <= 0
            verb = "heals" if delta > 0 else "takes"
            combat_state["log"].append(
                f"{combatant['name']} {verb} {abs(delta)}; health is now "
                f"{new_health}/{combatant['max_health']}."
            )
            break

        self._sync_player_health_from_combat(repository, combat_state)

        if combat_state.get("active") and (
            combat_team_defeated(combat_state["combatants"], "enemy")
            or combat_team_defeated(combat_state["combatants"], "party")
        ):
            self._resolve_combat(repository, combat_state)
            return

        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _combatant_from_inputs(
        self,
        *,
        default_team: str,
        fallback_name: str,
        index: int = 1,
        use_selected_team: bool = True,
    ) -> dict[str, Any]:
        """Builds a combatant from the input row."""

        name = self.name_input.text().strip() or fallback_name
        damage = normalize_damage_expression(self.damage_input.text(), default="1d6")
        team = str(self.team_combo.currentData() or default_team) if use_selected_team else default_team

        if team not in {"party", "enemy"}:
            team = default_team

        return {
            "id": f"{team}-{index}-{_slug_for_id(name)}",
            "name": name,
            "team": team,
            "current_health": self.health_input.value(),
            "max_health": self.health_input.value(),
            "armor_rating": self.armor_input.value(),
            "damage": damage,
            "status_effects": [],
            "loot": _split_loot_items(self.loot_input.text()) if team == "enemy" else [],
            "defeated": False,
        }

    def _target_for_actor(
        self,
        actor: dict[str, Any],
        combatants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Returns the selected or automatic attack target."""

        enemy_team = "party" if actor.get("team") == "enemy" else "enemy"

        if actor.get("team") == "party":
            selected_id = str(self.target_combo.currentData() or "")

            for combatant in combatants:
                if combatant.get("id") == selected_id and not combatant.get("defeated"):
                    return combatant

        for combatant in combatants:
            if combatant.get("team") == enemy_team and not combatant.get("defeated"):
                return combatant

        return None

    def _advance_turn(self, combat_state: dict[str, Any]) -> None:
        """Moves to the next living combatant."""

        old_index = int(combat_state["turn_index"])
        new_index = next_living_index(combat_state["combatants"], old_index)

        if new_index <= old_index:
            combat_state["round"] = int(combat_state.get("round", 1)) + 1

        combat_state["turn_index"] = new_index

    def _resolve_combat(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Finishes combat, stores state, and grants defeated-enemy loot."""

        party_defeated = combat_team_defeated(combat_state["combatants"], "party")
        enemies_defeated = combat_team_defeated(combat_state["combatants"], "enemy")

        if enemies_defeated and not party_defeated:
            granted_loot: list[str] = []

            for combatant in combat_state["combatants"]:
                if combatant.get("team") != "enemy" or not combatant.get("defeated"):
                    continue

                for loot_name in combatant.get("loot", []):
                    repository.add_inventory_item(
                        loot_name,
                        "Loot",
                        1,
                        f"Loot recovered from {combatant['name']}.",
                        0,
                    )
                    granted_loot.append(loot_name)

            if granted_loot:
                combat_state["log"].append("Recovered loot: " + ", ".join(granted_loot) + ".")

            combat_state["log"].append("Combat resolved: victory.")
            repository.append_history("system", "Combat resolved: victory.")
        elif party_defeated:
            combat_state["log"].append("Combat resolved: party defeated.")
            repository.append_history("system", "Combat resolved: party defeated.")
        else:
            combat_state["log"].append("Combat resolved.")
            repository.append_history("system", "Combat resolved.")

        combat_state["active"] = False
        self._sync_player_health_from_combat(repository, combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _sync_player_health_from_combat(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Persists player health from the player combatant."""

        for combatant in combat_state.get("combatants", []):
            if combatant.get("id") != "player":
                continue

            repository.set_setting("player.health_current", int(combatant["current_health"]))
            repository.set_setting("player.health_max", int(combatant["max_health"]))
            repository.set_setting("player.armor_rating", int(combatant["armor_rating"]))
            repository.set_state_value(
                "condition",
                "Incapacitated" if int(combatant["current_health"]) <= 0 else "Healthy",
            )
            break

    def _render_combat_state(self, combat_state: dict[str, Any]) -> None:
        """Renders saved combat state."""

        active = bool(combat_state.get("active", False))
        combatants = combat_state.get("combatants", [])
        current_id = ""

        if active and combatants:
            turn_index = int(combat_state.get("turn_index", 0))
            actor = combatants[turn_index]
            current_id = str(actor.get("id", ""))
            self.status_label.setText(
                f"Round {combat_state.get('round', 1)} - {actor['name']}'s turn"
            )
        else:
            self.status_label.setText("No active combat.")

        self.combatants_table.setRowCount(len(combatants))

        for row_index, combatant in enumerate(combatants):
            current_marker = "->" if combatant.get("id") == current_id else ""
            status_bits = []

            if combatant.get("defeated"):
                status_bits.append("Defeated")

            if combatant.get("status_effects"):
                status_bits.extend(str(effect) for effect in combatant.get("status_effects", []))

            loot_text = ", ".join(str(item) for item in combatant.get("loot", []))

            if loot_text:
                status_bits.append(f"Loot: {loot_text}")

            self.combatants_table.setItem(row_index, 0, _table_item(current_marker))
            self.combatants_table.setItem(row_index, 1, _table_item(str(combatant["name"])))
            self.combatants_table.setItem(row_index, 2, _table_item(str(combatant["team"])))
            self.combatants_table.setItem(
                row_index,
                3,
                _table_item(f"{combatant['current_health']}/{combatant['max_health']}"),
            )
            self.combatants_table.setItem(row_index, 4, _table_item(str(combatant["armor_rating"])))
            self.combatants_table.setItem(row_index, 5, _table_item(str(combatant["damage"])))
            self.combatants_table.setItem(row_index, 6, _table_item("; ".join(status_bits)))

        self.combatants_table.resizeColumnsToContents()
        self._populate_target_combos(combat_state)
        self.log_output.setPlainText("\n".join(str(entry) for entry in combat_state.get("log", [])))
        self.log_output.moveCursor(self.log_output.textCursor().MoveOperation.End)
        self._sync_buttons(active)

    def _populate_target_combos(self, combat_state: dict[str, Any]) -> None:
        """Reloads target dropdowns from combatants."""

        self.target_combo.clear()
        self.adjust_target_combo.clear()
        combatants = combat_state.get("combatants", [])
        actor = None

        if combat_state.get("active") and combatants:
            actor = combatants[int(combat_state.get("turn_index", 0))]

        for combatant in combatants:
            if combatant.get("defeated"):
                continue

            label = f"{combatant['name']} ({combatant['team']})"
            self.adjust_target_combo.addItem(label, combatant["id"])

            if actor is None:
                continue

            if combatant.get("team") != actor.get("team"):
                self.target_combo.addItem(label, combatant["id"])

    def _sync_buttons(self, combat_active: bool) -> None:
        """Enables combat controls for the active state."""

        self.attack_button.setEnabled(combat_active)
        self.end_turn_button.setEnabled(combat_active)
        self.resolve_button.setEnabled(combat_active)
        self.add_combatant_button.setEnabled(self.repository() is not None)
        self.start_button.setEnabled(self.repository() is not None and not combat_active)
        self.damage_button.setEnabled(bool(self.adjust_target_combo.count()))
        self.heal_button.setEnabled(bool(self.adjust_target_combo.count()))


class WorldScreen(RepositoryBackedWidget):
    """Read-only player-facing world information."""

    def __init__(self) -> None:
        super().__init__()

        self.world_output = QTextEdit()
        self.world_output.setReadOnly(True)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        layout = QVBoxLayout()
        layout.addWidget(refresh_button)
        layout.addWidget(self.world_output)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads player-known world lore."""

        repository = self.repository()

        if repository is None:
            self.world_output.clear()
            return

        sections: list[str] = []
        summary = repository.get_world_summary().strip()

        if summary:
            sections.append(f"# World Overview\n\n{summary}")

        lore = repository.get_world_lore()

        for category in sorted(lore):
            entries = lore[category]

            if not entries:
                continue

            body = "\n".join(
                f"- **{key}:** {text}"
                for key, text in sorted(entries.items())
            )
            sections.append(f"## {category}\n\n{body}")

        if not sections:
            sections.append("_No world information has been recorded yet._")

        _set_markdown_text(self.world_output, "\n\n".join(sections))


class CalendarScreen(RepositoryBackedWidget):
    """Player-facing custom calendar view."""

    def __init__(self) -> None:
        super().__init__()

        self.month_offset = 0
        self.month_label = QLabel("-")
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.month_label.setStyleSheet("font-size: 18px; font-weight: bold;")

        previous_button = QPushButton("Previous")
        previous_button.clicked.connect(self._show_previous_month)

        today_button = QPushButton("Today")
        today_button.clicked.connect(self.return_to_current_month)

        next_button = QPushButton("Next")
        next_button.clicked.connect(self._show_next_month)

        self.settings_button = QPushButton("Calendar Settings")
        self.settings_button.clicked.connect(self._open_calendar_settings_dialog)
        self.settings_button.setEnabled(False)

        navigation_row = QHBoxLayout()
        navigation_row.addWidget(previous_button)
        navigation_row.addStretch()
        navigation_row.addWidget(self.month_label)
        navigation_row.addStretch()
        navigation_row.addWidget(self.settings_button)
        navigation_row.addWidget(today_button)
        navigation_row.addWidget(next_button)

        self.summary_label = QLabel("-")
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        _use_soft_table_selection(self.table)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)

        layout = QVBoxLayout()
        layout.addLayout(navigation_row)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def set_repository(self, repository: SaveRepository | None) -> None:
        """Sets the active repository and resets to the current month."""

        self.month_offset = 0
        super().set_repository(repository)

    def refresh(self) -> None:
        """Reloads the calendar grid."""

        repository = self.repository()

        if repository is None:
            self.month_label.setText("-")
            self.summary_label.setText("-")
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.settings_button.setEnabled(False)
            return

        self.settings_button.setEnabled(True)
        state = StateManager(repository).load_state()
        grid = build_month_grid(state.calendar.to_dict(), self.month_offset)
        self.month_offset = int(grid["month_offset"])

        self.month_label.setText(f"{grid['month_name']} - Year {grid['year']}")
        self.summary_label.setText(
            (
                f"Today: {state.calendar.date_label}, {state.calendar.time_label} "
                f"| Season: {state.calendar.season_name}"
            )
        )
        self.table.setColumnCount(int(grid["days_per_week"]))
        self.table.setRowCount(int(grid["weeks_per_month"]))
        self.table.setHorizontalHeaderLabels([str(name) for name in grid["day_names"]])

        for row_index, week in enumerate(grid["rows"]):
            for column_index, day in enumerate(week):
                label = str(day["day_of_month"])

                if day["is_current_day"]:
                    label = f"{label}\nToday"

                item = QTableWidgetItem(label)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                if day["is_current_day"]:
                    item.setBackground(QColor("#d7ecff"))
                    item.setToolTip("Current day")

                self.table.setItem(row_index, column_index, item)

        self.table.resizeColumnsToContents()
        self.table.resizeRowsToContents()

    def return_to_current_month(self) -> None:
        """Returns the grid to the current month and refreshes."""

        self.month_offset = 0
        self.refresh()

    def _show_previous_month(self) -> None:
        """Shows the previous month."""

        self.month_offset -= 1
        self.refresh()

    def _show_next_month(self) -> None:
        """Shows the next month."""

        self.month_offset += 1
        self.refresh()

    def _open_calendar_settings_dialog(self) -> None:
        """Opens the save-specific calendar settings dialog."""

        repository = self.repository()

        if repository is None:
            return

        dialog = CalendarSettingsDialog(repository.get_calendar_settings(), self)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_calendar_settings(dialog.build_settings())

    def _save_calendar_settings(self, settings: dict[str, Any]) -> None:
        """Persists calendar settings and refreshes dependent screens."""

        repository = self.repository()

        if repository is None:
            return

        repository.set_calendar_settings(settings)
        _refresh_repository_calendar_time(repository)
        self.month_offset = 0
        self.refresh()
        self.notify_repository_changed()


class CalendarSettingsDialog(QDialog):
    """Dialog for editing save-specific calendar settings."""

    def __init__(
        self,
        settings: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("Calendar Settings")
        calendar_settings = dict(settings or DEFAULT_CALENDAR_SETTINGS)

        self.days_per_week_input = QSpinBox()
        self.days_per_week_input.setRange(1, 14)
        self.days_per_week_input.setValue(int(calendar_settings["days_per_week"]))

        self.weeks_per_month_input = QSpinBox()
        self.weeks_per_month_input.setRange(1, 12)
        self.weeks_per_month_input.setValue(int(calendar_settings["weeks_per_month"]))

        self.months_per_year_input = QSpinBox()
        self.months_per_year_input.setRange(1, 24)
        self.months_per_year_input.setValue(int(calendar_settings["months_per_year"]))

        self.seasons_per_year_input = QSpinBox()
        self.seasons_per_year_input.setRange(1, 12)
        self.seasons_per_year_input.setValue(int(calendar_settings["seasons_per_year"]))

        self.day_names_input = QLineEdit(
            ", ".join(str(name) for name in calendar_settings["day_names"])
        )
        self.month_names_input = QLineEdit(
            ", ".join(str(name) for name in calendar_settings["month_names"])
        )
        self.season_names_input = QLineEdit(
            ", ".join(str(season["name"]) for season in calendar_settings["seasons"])
        )
        self.season_hints_input = QLineEdit(
            ", ".join(
                str(season["weather_hint"]) for season in calendar_settings["seasons"]
            )
        )

        self.time_display_combo = QComboBox()
        self.time_display_combo.addItem("Narrative", "narrative")
        self.time_display_combo.addItem("12-hour", "12_hour")
        self.time_display_combo.addItem("24-hour", "24_hour")
        _set_combo_to_data(
            self.time_display_combo,
            str(calendar_settings.get("time_display", "narrative")),
        )

        form = QFormLayout()
        form.addRow("Days Per Week:", self.days_per_week_input)
        form.addRow("Weeks Per Month:", self.weeks_per_month_input)
        form.addRow("Months Per Year:", self.months_per_year_input)
        form.addRow("Seasons Per Year:", self.seasons_per_year_input)
        form.addRow("Day Names:", self.day_names_input)
        form.addRow("Month Names:", self.month_names_input)
        form.addRow("Season Names:", self.season_names_input)
        form.addRow("Season Weather Hints:", self.season_hints_input)
        form.addRow("Time Display:", self.time_display_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def build_settings(self) -> dict[str, Any]:
        """Builds normalized calendar settings from dialog controls."""

        return {
            "days_per_week": self.days_per_week_input.value(),
            "weeks_per_month": self.weeks_per_month_input.value(),
            "months_per_year": self.months_per_year_input.value(),
            "seasons_per_year": self.seasons_per_year_input.value(),
            "day_names": _split_list(self.day_names_input.text()),
            "month_names": _split_list(self.month_names_input.text()),
            "seasons": _build_season_settings(
                names=_split_list(self.season_names_input.text()),
                hints=_split_list(self.season_hints_input.text()),
                count=self.seasons_per_year_input.value(),
            ),
            "time_display": self.time_display_combo.currentData() or "narrative",
        }


class InventoryScreen(RepositoryBackedWidget):
    """Read-only inventory journal."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Category", "Qty", "Value", "Description"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _enable_table_sorting(self.table, self._sort_by_column)
        self.table.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)
        self.currency_label = QLabel("Currency: 0")

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Inventory"))
        layout.addWidget(self.currency_label)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads inventory table."""

        repository = self.repository()

        if repository is None:
            self.currency_label.setText("Currency: 0")
            self.table.setRowCount(0)
            return

        items = repository.list_inventory_items()
        denominations = repository.get_currency_denominations()
        balance_base_units = _safe_int(
            repository.get_state_value("currency.balance", "0"),
            0,
        )
        self.currency_label.setText(
            f"Currency: {format_currency_amount(balance_base_units, denominations)}"
        )
        items.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self.table.setRowCount(len(items))

        for row_index, item in enumerate(items):
            self.table.setItem(row_index, 0, _table_item(str(item.get("name", ""))))
            self.table.setItem(row_index, 1, _table_item(str(item.get("category", ""))))
            quantity = int(item.get("quantity", 0))
            value_base_units = int(item.get("value_base_units", 0))
            self.table.setItem(row_index, 2, _table_item(str(quantity), quantity))
            self.table.setItem(
                row_index,
                3,
                _table_item(
                    format_currency_amount(
                        value_base_units,
                        denominations,
                    ),
                    value_base_units,
                ),
            )
            self.table.setItem(row_index, 4, _table_item(str(item.get("description", ""))))

        self.table.resizeColumnsToContents()

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts inventory by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, item: dict[str, Any]) -> tuple[Any, str]:
        """Returns the active inventory sort key."""

        name = str(item.get("name", "")).casefold()

        if self._sort_column == 1:
            return str(item.get("category", "")).casefold(), name

        if self._sort_column == 2:
            return _safe_int(item.get("quantity", 0), 0), name

        if self._sort_column == 3:
            return _safe_int(item.get("value_base_units", 0), 0), name

        if self._sort_column == 4:
            return str(item.get("description", "")).casefold(), name

        return name, name


class NpcsScreen(RepositoryBackedWidget):
    """Player-facing NPC journal."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Location", "Notes"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _enable_table_sorting(self.table, self._sort_by_column)
        self.table.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        layout = QVBoxLayout()
        layout.addWidget(refresh_button)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads the player-visible NPC journal."""

        repository = self.repository()

        if repository is None:
            self.table.setRowCount(0)
            return

        npcs = repository.list_player_visible_npcs()
        npcs.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self.table.setRowCount(len(npcs))

        for row_index, npc in enumerate(npcs):
            self.table.setItem(
                row_index,
                0,
                _table_item(str(npc.get("display_name", "Unknown NPC"))),
            )
            self.table.setItem(row_index, 1, _table_item(str(npc.get("location", ""))))
            self.table.setItem(row_index, 2, _table_item(str(npc.get("notes", ""))))

        self.table.resizeColumnsToContents()

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts NPCs by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, npc: dict[str, Any]) -> tuple[str, str]:
        """Returns the active NPC sort key."""

        name = str(npc.get("display_name", "Unknown NPC")).casefold()

        if self._sort_column == 1:
            return str(npc.get("location", "")).casefold(), name

        if self._sort_column == 2:
            return str(npc.get("notes", "")).casefold(), name

        return name, name


class ActiveTasksScreen(RepositoryBackedWidget):
    """Player-facing list of current quests, commissions, and obligations."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Task",
                "Type",
                "Status",
                "Details",
                "Contact",
                "Location",
                "Reward",
                "Due",
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _enable_table_sorting(self.table, self._sort_by_column)
        self.table.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        layout = QVBoxLayout()
        layout.addWidget(refresh_button)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads active tasks."""

        repository = self.repository()

        if repository is None:
            self.table.setRowCount(0)
            return

        tasks = repository.list_active_tasks()
        tasks.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self.table.setRowCount(len(tasks))

        for row_index, task in enumerate(tasks):
            details = str(task.get("description", ""))
            notes = str(task.get("notes", ""))

            if notes:
                details = f"{details}\n\n{notes}" if details else notes

            self.table.setItem(row_index, 0, _table_item(str(task.get("name", ""))))
            self.table.setItem(row_index, 1, _table_item(str(task.get("category", ""))))
            self.table.setItem(row_index, 2, _table_item(str(task.get("status", ""))))
            self.table.setItem(row_index, 3, _table_item(details))
            self.table.setItem(row_index, 4, _table_item(str(task.get("requester", ""))))
            self.table.setItem(row_index, 5, _table_item(str(task.get("location", ""))))
            self.table.setItem(row_index, 6, _table_item(str(task.get("reward", ""))))
            self.table.setItem(row_index, 7, _table_item(str(task.get("due_date", ""))))

        self.table.resizeColumnsToContents()

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts active tasks by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, task: dict[str, Any]) -> tuple[str, str]:
        """Returns the active task sort key."""

        name = str(task.get("name", "")).casefold()

        if self._sort_column == 1:
            return str(task.get("category", "")).casefold(), name

        if self._sort_column == 2:
            return str(task.get("status", "")).casefold(), name

        if self._sort_column == 3:
            details = str(task.get("description", ""))
            notes = str(task.get("notes", ""))
            return f"{details}\n\n{notes}".casefold(), name

        if self._sort_column == 4:
            return str(task.get("requester", "")).casefold(), name

        if self._sort_column == 5:
            return str(task.get("location", "")).casefold(), name

        if self._sort_column == 6:
            return str(task.get("reward", "")).casefold(), name

        if self._sort_column == 7:
            due_elapsed_minutes = _safe_int(task.get("due_elapsed_minutes"), -1)

            if due_elapsed_minutes >= 0:
                return f"{due_elapsed_minutes:012d}", name

            return str(task.get("due_date", "")).casefold(), name

        return name, name


class SkillsScreen(RepositoryBackedWidget):
    """Read-only skills journal."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.skills_table = QTableWidget(0, 3)
        self.skills_table.setHorizontalHeaderLabels(
            ["Skill", "Training", "Description"]
        )
        self.skills_table.horizontalHeader().setStretchLastSection(True)
        self.skills_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _enable_table_sorting(self.skills_table, self._sort_by_column)
        self.skills_table.horizontalHeader().setSortIndicator(
            self._sort_column,
            self._sort_order,
        )

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Known Skills"))
        layout.addWidget(self.skills_table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads skills and recent checks."""

        repository = self.repository()

        if repository is None:
            self.skills_table.setRowCount(0)
            return

        skills = repository.list_skills()
        skills.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self.skills_table.setRowCount(len(skills))

        for row_index, skill in enumerate(skills):
            level = int(skill.get("level", 1))
            self.skills_table.setItem(row_index, 0, _table_item(str(skill.get("name", ""))))
            self.skills_table.setItem(
                row_index,
                1,
                _table_item(_skill_level_label(level), level),
            )
            self.skills_table.setItem(
                row_index,
                2,
                _table_item(str(skill.get("description", ""))),
            )

        self.skills_table.resizeColumnsToContents()

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts skills by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.skills_table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, skill: dict[str, Any]) -> tuple[Any, str]:
        """Returns the active skill sort key."""

        name = str(skill.get("name", "")).casefold()

        if self._sort_column == 1:
            return _safe_int(skill.get("level", 1), 1), name

        if self._sort_column == 2:
            return str(skill.get("description", "")).casefold(), name

        return name, name


class AlchemyNotebookScreen(RepositoryBackedWidget):
    """Crafting screen for useful items/materials and recipes."""

    def __init__(self) -> None:
        super().__init__()

        self.tabs = QTabWidget()
        self._reagent_rows: list[dict[str, Any]] = []
        self._recipe_ingredient_rows: list[dict[str, Any]] = []
        self._refreshing_reagents = False
        self._reagent_sort_column = 0
        self._reagent_sort_order = Qt.SortOrder.AscendingOrder
        self._recipe_sort_column = 0
        self._recipe_sort_order = Qt.SortOrder.AscendingOrder

        self._setup_reagents_tab()
        self._setup_recipes_tab()

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads all crafting data."""

        repository = self.repository()

        if repository is None:
            self.reagent_table.setRowCount(0)
            self.recipe_table.setRowCount(0)
            self.recipe_reagent_combo.clear()
            return

        self._refresh_reagents(repository)
        self._refresh_recipes(repository)

    def _setup_reagents_tab(self) -> None:
        """Builds the structured useful item/material discovery tab."""

        self.reagent_table = QTableWidget(0, 4)
        self.reagent_table.setHorizontalHeaderLabels(
            ["Name", "Description", "Location", "Uses"]
        )
        self.reagent_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.reagent_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.reagent_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        _enable_table_sorting(self.reagent_table, self._sort_reagents_by_column)
        self.reagent_table.horizontalHeader().setSortIndicator(
            self._reagent_sort_column,
            self._reagent_sort_order,
        )
        self.reagent_table.horizontalHeader().setStretchLastSection(True)
        self.reagent_table.itemSelectionChanged.connect(self._load_selected_reagent)

        self.reagent_name_input = QLineEdit()
        self.reagent_name_input.setPlaceholderText("Item or material name")
        self.reagent_description_input = QLineEdit()
        self.reagent_description_input.setPlaceholderText("Short description")
        self.reagent_location_input = QLineEdit()
        self.reagent_location_input.setPlaceholderText("Where this item or material is found")
        self.reagent_uses_input = QLineEdit()
        self.reagent_uses_input.setPlaceholderText(
            "Comma-separated uses, such as repair, dye, medicine, fuel"
        )

        save_button = QPushButton("Save Item")
        save_button.clicked.connect(self._save_reagent)
        new_button = QPushButton("New Item")
        new_button.clicked.connect(self._clear_reagent_form)

        button_row = QHBoxLayout()
        button_row.addWidget(save_button)
        button_row.addWidget(new_button)
        button_row.addStretch()

        form = QFormLayout()
        form.addRow("Name:", self.reagent_name_input)
        form.addRow("Description:", self.reagent_description_input)
        form.addRow("Location:", self.reagent_location_input)
        form.addRow("Uses:", self.reagent_uses_input)
        form.addRow(button_row)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.reagent_table)

        wrapper = QWidget()
        wrapper.setLayout(layout)
        self.tabs.addTab(wrapper, "Items")

    def _setup_recipes_tab(self) -> None:
        """Builds the structured recipe discovery tab."""

        self.recipe_table = QTableWidget(0, 4)
        self.recipe_table.setHorizontalHeaderLabels(
            ["Name", "Ingredients", "Result", "Notes"]
        )
        self.recipe_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _enable_table_sorting(self.recipe_table, self._sort_recipes_by_column)
        self.recipe_table.horizontalHeader().setSortIndicator(
            self._recipe_sort_column,
            self._recipe_sort_order,
        )
        self.recipe_table.horizontalHeader().setStretchLastSection(True)

        self.recipe_name_input = QLineEdit()
        self.recipe_name_input.setPlaceholderText("Recipe name")
        self.recipe_result_input = QLineEdit()
        self.recipe_result_input.setPlaceholderText("Recipe result")
        self.recipe_notes_input = QTextEdit()
        self.recipe_notes_input.setPlaceholderText("Recipe notes")

        self.recipe_reagent_combo = QComboBox()
        self.recipe_reagent_combo.setEditable(True)
        self.recipe_reagent_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.recipe_reagent_combo.setPlaceholderText("Search material, ingredient, reagent, or crafting item")
        self.recipe_reagent_combo.setMinimumWidth(220)
        self.recipe_reagent_choice_model = QStringListModel(self)
        self.recipe_reagent_completer = QCompleter(
            self.recipe_reagent_choice_model,
            self.recipe_reagent_combo,
        )
        self.recipe_reagent_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.recipe_reagent_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.recipe_reagent_completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion
        )
        self.recipe_reagent_completer.activated[str].connect(
            self._select_recipe_reagent_label
        )
        self.recipe_reagent_combo.setCompleter(self.recipe_reagent_completer)
        self.recipe_reagent_line_edit = self.recipe_reagent_combo.lineEdit()
        self.recipe_reagent_combo.installEventFilter(self)
        if self.recipe_reagent_line_edit is not None:
            self.recipe_reagent_line_edit.installEventFilter(self)
            self.recipe_reagent_line_edit.textEdited.connect(
                self._show_recipe_reagent_choices
            )
        self.recipe_quantity_input = QSpinBox()
        self.recipe_quantity_input.setRange(1, 999)
        self.recipe_quantity_input.setValue(1)
        self.recipe_measure_amount_input = QSpinBox()
        self.recipe_measure_amount_input.setRange(1, 99999)
        self.recipe_measure_amount_input.setValue(1)
        self.recipe_measure_unit_combo = QComboBox()
        for unit in COMMON_MEASUREMENT_UNITS:
            self.recipe_measure_unit_combo.addItem(unit, unit)

        add_ingredient_button = QPushButton("Add Ingredient")
        add_ingredient_button.clicked.connect(self._add_recipe_ingredient)
        remove_ingredient_button = QPushButton("Remove Ingredient")
        remove_ingredient_button.clicked.connect(self._remove_recipe_ingredient)

        ingredient_controls = QHBoxLayout()
        ingredient_controls.addWidget(self.recipe_reagent_combo, 2)
        ingredient_controls.addWidget(QLabel("Count:"))
        ingredient_controls.addWidget(self.recipe_quantity_input)
        ingredient_controls.addWidget(QLabel("Measure:"))
        ingredient_controls.addWidget(self.recipe_measure_amount_input)
        ingredient_controls.addWidget(self.recipe_measure_unit_combo)
        ingredient_controls.addWidget(add_ingredient_button)
        ingredient_controls.addWidget(remove_ingredient_button)

        self.recipe_ingredient_table = QTableWidget(0, 4)
        self.recipe_ingredient_table.setHorizontalHeaderLabels(
            ["Item", "Count", "Amount", "Unit"]
        )
        self.recipe_ingredient_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recipe_ingredient_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.recipe_ingredient_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        _use_soft_table_selection(self.recipe_ingredient_table)

        save_button = QPushButton("Save Recipe")
        save_button.clicked.connect(self._add_recipe)
        new_button = QPushButton("New Recipe")
        new_button.clicked.connect(self._clear_recipe_form)

        button_row = QHBoxLayout()
        button_row.addWidget(save_button)
        button_row.addWidget(new_button)
        button_row.addStretch()

        form = QFormLayout()
        form.addRow("Name:", self.recipe_name_input)
        form.addRow("Ingredient:", ingredient_controls)
        form.addRow("Selected Ingredients:", self.recipe_ingredient_table)
        form.addRow("Result:", self.recipe_result_input)
        form.addRow("Notes:", self.recipe_notes_input)
        form.addRow(button_row)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.recipe_table)

        wrapper = QWidget()
        wrapper.setLayout(layout)
        self.tabs.addTab(wrapper, "Recipes")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Keeps the editable Ingredient selector behaving like a dropdown."""

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and hasattr(self, "recipe_reagent_combo")
            and (
                watched is self.recipe_reagent_combo
                or watched is getattr(self, "recipe_reagent_line_edit", None)
            )
            and self.recipe_reagent_combo.count() > 0
        ):
            QTimer.singleShot(0, self._show_recipe_reagent_choices)

        return super().eventFilter(watched, event)

    def _refresh_reagents(self, repository: SaveRepository) -> None:
        """Reloads the known crafting item/material table."""

        reagents = repository.list_crafting_items()
        selected_name = self.reagent_name_input.text().strip()
        reagents.sort(
            key=self._reagent_sort_key,
            reverse=_sort_descending(self._reagent_sort_order),
        )
        self._reagent_rows = reagents
        self._refreshing_reagents = True
        self.reagent_table.clearSelection()
        self.reagent_table.setRowCount(len(reagents))

        for row_index, reagent in enumerate(reagents):
            self.reagent_table.setItem(row_index, 0, _table_item(str(reagent.get("name", ""))))
            self.reagent_table.setItem(row_index, 1, _table_item(str(reagent.get("description", ""))))
            self.reagent_table.setItem(row_index, 2, _table_item(str(reagent.get("location", ""))))
            self.reagent_table.setItem(row_index, 3, _table_item(_join_list(reagent.get("uses", []))))

        self.reagent_table.resizeColumnsToContents()
        self._refreshing_reagents = False

        if selected_name:
            for row_index, reagent in enumerate(reagents):
                if str(reagent.get("name", "")).casefold() == selected_name.casefold():
                    self.reagent_table.selectRow(row_index)
                    break

        self._refresh_recipe_reagent_choices(repository)

    def _refresh_recipes(self, repository: SaveRepository) -> None:
        """Reloads the recipe table."""

        recipes = repository.list_crafting_recipes()
        recipes.sort(
            key=self._recipe_sort_key,
            reverse=_sort_descending(self._recipe_sort_order),
        )
        self.recipe_table.setRowCount(len(recipes))

        for row_index, recipe in enumerate(recipes):
            self.recipe_table.setItem(row_index, 0, _table_item(str(recipe.get("name", ""))))
            self.recipe_table.setItem(row_index, 1, _table_item(format_recipe_ingredients(recipe.get("ingredients", []))))
            self.recipe_table.setItem(row_index, 2, _table_item(str(recipe.get("result", ""))))
            self.recipe_table.setItem(row_index, 3, _table_item(str(recipe.get("notes", ""))))

        self.recipe_table.resizeColumnsToContents()

    def _save_reagent(self) -> None:
        """Adds or updates a known crafting item/material."""

        repository = self.repository()

        if repository is None:
            return

        name = self.reagent_name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Name", "Item name is required.")
            return

        repository.add_crafting_item(
            name=name,
            description=self.reagent_description_input.text(),
            location=self.reagent_location_input.text(),
            uses=_split_list(self.reagent_uses_input.text()),
        )

        self.reagent_name_input.clear()
        self.reagent_description_input.clear()
        self.reagent_location_input.clear()
        self.reagent_uses_input.clear()

        self.refresh()
        self.notify_repository_changed()

    def _load_selected_reagent(self) -> None:
        """Loads the selected crafting item/material row into the edit controls."""

        if self._refreshing_reagents:
            return

        if not self.reagent_table.selectedItems():
            return

        row_index = self.reagent_table.currentRow()

        if row_index < 0 or row_index >= len(self._reagent_rows):
            return

        reagent = self._reagent_rows[row_index]
        self.reagent_name_input.setText(str(reagent.get("name", "")))
        self.reagent_description_input.setText(str(reagent.get("description", "")))
        self.reagent_location_input.setText(str(reagent.get("location", "")))
        self.reagent_uses_input.setText(_join_list(reagent.get("uses", [])))

    def _clear_reagent_form(self) -> None:
        """Clears item edit controls and table selection."""

        self.reagent_table.clearSelection()
        self.reagent_name_input.clear()
        self.reagent_description_input.clear()
        self.reagent_location_input.clear()
        self.reagent_uses_input.clear()

    def _refresh_recipe_reagent_choices(self, repository: SaveRepository) -> None:
        """Reloads the category-filtered item dropdown used by recipe ingredients."""

        current_text = self.recipe_reagent_combo.currentText().strip()
        self.recipe_reagent_combo.clear()
        choices = _crafting_ingredient_catalog_choices(repository.list_item_catalog())
        choice_labels: list[str] = []

        for item in choices:
            name = str(item.get("name", "")).strip()
            category = str(item.get("category", "")).strip()
            if name:
                label = f"{name} ({category})"
                self.recipe_reagent_combo.addItem(label, name)
                choice_labels.append(label)

        self.recipe_reagent_choice_model.setStringList(choice_labels)

        if current_text:
            for index in range(self.recipe_reagent_combo.count()):
                item_name = str(self.recipe_reagent_combo.itemData(index)).strip()
                if (
                    item_name.casefold() == current_text.casefold()
                    or self.recipe_reagent_combo.itemText(index).casefold()
                    == current_text.casefold()
                ):
                    self.recipe_reagent_combo.setCurrentIndex(index)
                    return

        if self.recipe_reagent_combo.count() > 0:
            self.recipe_reagent_combo.setCurrentIndex(0)

    @Slot(str)
    def _show_recipe_reagent_choices(self, _text: str = "") -> None:
        """Shows the searchable Ingredient choices popup."""

        if self.recipe_reagent_combo.count() <= 0:
            return

        if self.recipe_reagent_line_edit is None:
            self.recipe_reagent_combo.showPopup()
            return

        self.recipe_reagent_completer.setCompletionPrefix(
            self.recipe_reagent_line_edit.text()
        )
        self.recipe_reagent_completer.complete()

    @Slot(str)
    def _select_recipe_reagent_label(self, label: str) -> None:
        """Selects an Ingredient dropdown row by its visible label."""

        clean_label = str(label).strip()

        if not clean_label:
            return

        for index in range(self.recipe_reagent_combo.count()):
            if self.recipe_reagent_combo.itemText(index) == clean_label:
                self.recipe_reagent_combo.setCurrentIndex(index)
                return

    def _add_recipe_ingredient(self) -> None:
        """Adds a structured known-item ingredient to the draft recipe."""

        selected_name = self._selected_recipe_reagent_name()

        if not selected_name:
            QMessageBox.warning(
                self,
                "Unknown Item",
                (
                    "Choose an item categorized as "
                    f"{CRAFTING_INGREDIENT_CATEGORY_NAMES}."
                ),
            )
            return

        ingredient = normalize_recipe_ingredient(
            {
                "reagent_name": selected_name,
                "quantity": self.recipe_quantity_input.value(),
                "measure_amount": self.recipe_measure_amount_input.value(),
                "measure_unit": self.recipe_measure_unit_combo.currentData(),
            }
        )

        if ingredient is None:
            return

        for index, existing in enumerate(self._recipe_ingredient_rows):
            if (
                str(existing.get("reagent_name", "")).casefold()
                == ingredient["reagent_name"].casefold()
            ):
                self._recipe_ingredient_rows[index] = ingredient
                self._refresh_recipe_ingredient_table()
                return

        self._recipe_ingredient_rows.append(ingredient)
        self._refresh_recipe_ingredient_table()

    def _remove_recipe_ingredient(self) -> None:
        """Removes the selected ingredient from the draft recipe."""

        row_index = self.recipe_ingredient_table.currentRow()

        if row_index < 0 or row_index >= len(self._recipe_ingredient_rows):
            return

        del self._recipe_ingredient_rows[row_index]
        self._refresh_recipe_ingredient_table()

    def _refresh_recipe_ingredient_table(self) -> None:
        """Reloads the draft recipe ingredient table."""

        self.recipe_ingredient_table.setRowCount(len(self._recipe_ingredient_rows))

        for row_index, ingredient in enumerate(self._recipe_ingredient_rows):
            self.recipe_ingredient_table.setItem(
                row_index,
                0,
                _table_item(str(ingredient.get("reagent_name", ""))),
            )
            self.recipe_ingredient_table.setItem(
                row_index,
                1,
                _table_item(str(ingredient.get("quantity", 1))),
            )
            self.recipe_ingredient_table.setItem(
                row_index,
                2,
                _table_item(str(ingredient.get("measure_amount", 1))),
            )
            self.recipe_ingredient_table.setItem(
                row_index,
                3,
                _table_item(str(ingredient.get("measure_unit", "each"))),
            )

        self.recipe_ingredient_table.resizeColumnsToContents()

    def _selected_recipe_reagent_name(self) -> str:
        """Returns the exact selected known item name, or blank."""

        requested_name = self.recipe_reagent_combo.currentText().strip()

        if not requested_name:
            return ""

        for index in range(self.recipe_reagent_combo.count()):
            name = str(self.recipe_reagent_combo.itemData(index)).strip()
            label = str(self.recipe_reagent_combo.itemText(index)).strip()
            if (
                name.casefold() == requested_name.casefold()
                or label.casefold() == requested_name.casefold()
            ):
                return name

        return ""

    def _clear_recipe_form(self) -> None:
        """Clears recipe edit controls and draft ingredients."""

        self.recipe_name_input.clear()
        self._recipe_ingredient_rows.clear()
        self._refresh_recipe_ingredient_table()
        self.recipe_result_input.clear()
        self.recipe_notes_input.clear()
        self.recipe_quantity_input.setValue(1)
        self.recipe_measure_amount_input.setValue(1)
        self.recipe_measure_unit_combo.setCurrentIndex(0)

    def _add_recipe(self) -> None:
        """Adds or updates a known recipe."""

        repository = self.repository()

        if repository is None:
            return

        name = self.recipe_name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Name", "Recipe name is required.")
            return

        if not self._recipe_ingredient_rows:
            QMessageBox.warning(
                self,
                "Missing Ingredients",
                "Add at least one known item ingredient.",
            )
            return

        repository.add_crafting_recipe(
            name=name,
            ingredients=list(self._recipe_ingredient_rows),
            result=self.recipe_result_input.text(),
            notes=self.recipe_notes_input.toPlainText(),
        )

        self._clear_recipe_form()

        self.refresh()
        self.notify_repository_changed()

    def _sort_reagents_by_column(self, column_index: int) -> None:
        """Sorts crafting items/materials by a clicked header column."""

        self._reagent_sort_column, self._reagent_sort_order = _update_sort_state(
            self.reagent_table,
            self._reagent_sort_column,
            self._reagent_sort_order,
            column_index,
        )
        self.refresh()

    def _sort_recipes_by_column(self, column_index: int) -> None:
        """Sorts recipes by a clicked header column."""

        self._recipe_sort_column, self._recipe_sort_order = _update_sort_state(
            self.recipe_table,
            self._recipe_sort_column,
            self._recipe_sort_order,
            column_index,
        )
        self.refresh()

    def _reagent_sort_key(self, reagent: dict[str, Any]) -> tuple[str, str]:
        """Returns the active crafting item/material sort key."""

        name = str(reagent.get("name", "")).casefold()

        if self._reagent_sort_column == 1:
            return str(reagent.get("description", "")).casefold(), name

        if self._reagent_sort_column == 2:
            return str(reagent.get("location", "")).casefold(), name

        if self._reagent_sort_column == 3:
            return _join_list(reagent.get("uses", [])).casefold(), name

        return name, name

    def _recipe_sort_key(self, recipe: dict[str, Any]) -> tuple[str, str]:
        """Returns the active recipe sort key."""

        name = str(recipe.get("name", "")).casefold()

        if self._recipe_sort_column == 1:
            return format_recipe_ingredients(recipe.get("ingredients", [])).casefold(), name

        if self._recipe_sort_column == 2:
            return str(recipe.get("result", "")).casefold(), name

        if self._recipe_sort_column == 3:
            return str(recipe.get("notes", "")).casefold(), name

        return name, name

class HistoryScreen(RepositoryBackedWidget):
    """Player journal with explicit AI sharing control."""

    def __init__(self) -> None:
        super().__init__()

        self._loading_journal = False
        self._saving_journal = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(900)
        self._autosave_timer.timeout.connect(self._autosave_journal)

        self.journal_input = QTextEdit()
        self.journal_input.setPlaceholderText(
            "Write player notes here. They stay private unless sharing is enabled."
        )
        self.journal_input.textChanged.connect(self._schedule_journal_autosave)

        self.share_with_ai_checkbox = QCheckBox("Send these journal notes to the AI")
        self.share_with_ai_checkbox.toggled.connect(
            lambda _checked: self._schedule_journal_autosave()
        )

        save_button = QPushButton("Save Journal")
        save_button.clicked.connect(self._save_journal)

        layout = QVBoxLayout()
        layout.addWidget(self.share_with_ai_checkbox)
        layout.addWidget(self.journal_input)
        layout.addWidget(save_button)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads journal notes and sharing preference."""

        repository = self.repository()
        self._autosave_timer.stop()
        self._loading_journal = True

        try:
            if repository is None:
                self.journal_input.clear()
                self.share_with_ai_checkbox.setChecked(False)
                return

            self.journal_input.setPlainText(repository.get_journal_notes())
            self.share_with_ai_checkbox.setChecked(repository.get_journal_share_with_ai())
        finally:
            self._loading_journal = False

    def _schedule_journal_autosave(self) -> None:
        """Debounces journal autosaves while the player is typing."""

        if self._loading_journal or self._saving_journal:
            return

        self._autosave_timer.start()

    def _autosave_journal(self) -> None:
        """Persists journal changes without interrupting the player."""

        self._autosave_timer.stop()
        self._persist_journal(show_confirmation=False)

    def _save_journal(self) -> None:
        """Persists journal notes from the manual save button."""

        self._autosave_timer.stop()
        self._persist_journal(show_confirmation=True)

    def _persist_journal(self, *, show_confirmation: bool) -> None:
        """Persists journal notes and AI sharing preference."""

        repository = self.repository()

        if repository is None or self._loading_journal or self._saving_journal:
            return

        self._saving_journal = True

        try:
            repository.set_journal_notes(self.journal_input.toPlainText())
            repository.set_journal_share_with_ai(self.share_with_ai_checkbox.isChecked())
            self.notify_repository_changed()

            if show_confirmation:
                QMessageBox.information(self, "Journal Saved", "Journal notes were saved.")
        finally:
            self._saving_journal = False


class SettingsScreen(RepositoryBackedWidget):
    """Basic save-specific settings screen."""

    def __init__(
        self,
        on_audio_settings_changed=None,
        on_theme_changed=None,
        tts_enabled: bool = True,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: Callable[[str, int], bool] | None = None,
        on_app_tts_settings_saved: Callable[[dict[str, Any]], None] | None = None,
        global_tts_settings_provider: Callable[[], dict[str, Any]] | None = None,
        custom_voice_storage_path: Path | str | None = None,
    ) -> None:
        super().__init__()

        self.on_audio_settings_changed = on_audio_settings_changed
        self.on_theme_changed = on_theme_changed
        self.tts_enabled = bool(tts_enabled)
        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        self.on_app_tts_settings_saved = on_app_tts_settings_saved
        self.global_tts_settings_provider = global_tts_settings_provider
        self.custom_voice_storage_path = custom_voice_storage_path
        self.narrator_enabled_checkbox: QCheckBox | None = None
        self.tts_volume_slider: QSlider | None = None
        self.tts_volume_label: QLabel | None = None
        self.tts_voice_combo: QComboBox | None = None
        self.sample_voice_button: QPushButton | None = None
        self.tts_speed_slider: QSlider | None = None
        self.tts_settings_button: QPushButton | None = None
        self.custom_voice_button: QPushButton | None = None
        self._loading_settings = False
        self._saving_settings = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(400)
        self._autosave_timer.timeout.connect(self._save_settings)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        self.theme_combo.currentIndexChanged.connect(lambda _index: self._save_settings())

        self.narration_tense_combo = QComboBox()
        _add_combo_options(self.narration_tense_combo, NARRATION_TENSE_OPTIONS)
        _set_combo_to_data(self.narration_tense_combo, DEFAULT_NARRATION_TENSE)
        self.narration_tense_combo.currentIndexChanged.connect(
            lambda _index: self._save_settings()
        )

        self.narration_style_combo = QComboBox()
        _add_combo_options(self.narration_style_combo, NARRATION_STYLE_OPTIONS)
        _set_combo_to_data(self.narration_style_combo, DEFAULT_NARRATION_STYLE)
        self.narration_style_combo.currentIndexChanged.connect(
            lambda _index: self._save_settings()
        )

        self.music_enabled_checkbox = QCheckBox("Music enabled")
        self.music_enabled_checkbox.setChecked(True)
        self.music_enabled_checkbox.toggled.connect(lambda _checked: self._save_settings())

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(25)
        self.music_volume_label = QLabel("25%")
        self.music_volume_slider.valueChanged.connect(
            lambda value: self.music_volume_label.setText(f"{value}%")
        )
        self.music_volume_slider.sliderReleased.connect(self._save_settings)

        if self.tts_enabled:
            self.tts_settings_button = QPushButton("TTS Settings")
            self.tts_settings_button.clicked.connect(self._open_tts_settings_dialog)
            self.custom_voice_button = QPushButton("Custom Voices...")
            self.custom_voice_button.clicked.connect(self._open_custom_voice_dialog)

        self.additional_ai_context_input = QTextEdit()
        self.additional_ai_context_input.setPlaceholderText(
            "Optional AI-facing guidance, style preferences, boundaries, or reminders..."
        )
        self.additional_ai_context_input.textChanged.connect(self._schedule_settings_save)

        self.currency_name_inputs: list[QLineEdit] = []
        self.currency_plural_inputs: list[QLineEdit] = []
        self.currency_value_inputs: list[QSpinBox] = []
        self.currency_row_widgets: list[QWidget] = []
        self.currency_remove_buttons: list[QPushButton] = []
        self.currency_rows_layout = QFormLayout()
        self.currency_rows_widget = QWidget()
        self.currency_rows_widget.setLayout(self.currency_rows_layout)
        self.add_settings_currency_button = QPushButton("Add New Currency")
        self.add_settings_currency_button.clicked.connect(self._add_settings_currency_row)

        layout = QFormLayout()
        layout.addRow("Theme Preference:", self.theme_combo)
        layout.addRow("Narration Tense:", self.narration_tense_combo)
        layout.addRow("Narration Style:", self.narration_style_combo)
        layout.addRow("Background Music:", self.music_enabled_checkbox)
        layout.addRow("Music Volume:", _slider_row(self.music_volume_slider, self.music_volume_label))

        if self.tts_settings_button is not None:
            layout.addRow("Narration Audio:", self.tts_settings_button)

        if self.custom_voice_button is not None:
            layout.addRow("Custom Voices:", self.custom_voice_button)

        layout.addRow("Additional AI Context:", self.additional_ai_context_input)
        layout.addRow("Currencies:", self.currency_rows_widget)
        layout.addRow("", self.add_settings_currency_button)

        self.setLayout(layout)

    def _add_settings_currency_row(self) -> None:
        """Adds a blank currency row to the settings screen."""

        self._append_settings_currency_row({})
        self._sync_settings_currency_rows()

    def _append_settings_currency_row(self, denomination: dict[str, Any]) -> None:
        """Appends one editable currency row to the settings screen."""

        name_input = QLineEdit()
        name_input.setPlaceholderText("Name")
        name_input.setText(str(denomination.get("name", "")))
        plural_input = QLineEdit()
        plural_input.setPlaceholderText("Plural name")
        plural_input.setText(str(denomination.get("plural_name", "")))
        value_input = QSpinBox()
        value_input.setMinimum(1)
        value_input.setMaximum(1_000_000_000)
        value_input.setValue(_safe_int(denomination.get("value", 1), 1))
        remove_button = QPushButton("Remove")

        row_widget = QWidget()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(name_input)
        row.addWidget(plural_input)
        row.addWidget(value_input)
        row.addWidget(remove_button)
        row_widget.setLayout(row)

        name_input.editingFinished.connect(self._save_settings)
        name_input.textChanged.connect(lambda _text: self._schedule_settings_save())
        plural_input.editingFinished.connect(self._save_settings)
        plural_input.textChanged.connect(lambda _text: self._schedule_settings_save())
        value_input.valueChanged.connect(lambda _value: self._save_settings())
        remove_button.clicked.connect(
            lambda _checked=False, widget=row_widget: self._remove_settings_currency_row(widget)
        )

        self.currency_name_inputs.append(name_input)
        self.currency_plural_inputs.append(plural_input)
        self.currency_value_inputs.append(value_input)
        self.currency_row_widgets.append(row_widget)
        self.currency_remove_buttons.append(remove_button)
        self.currency_rows_layout.addRow(
            f"Currency {len(self.currency_row_widgets)}:",
            row_widget,
        )

    def _remove_settings_currency_row(self, row_widget: QWidget) -> None:
        """Removes one editable currency row from the settings screen."""

        if row_widget not in self.currency_row_widgets:
            return

        index = self.currency_row_widgets.index(row_widget)
        self.currency_rows_layout.removeRow(row_widget)
        del self.currency_name_inputs[index]
        del self.currency_plural_inputs[index]
        del self.currency_value_inputs[index]
        del self.currency_row_widgets[index]
        del self.currency_remove_buttons[index]
        self._sync_settings_currency_rows()
        self._save_settings()

    def _load_settings_currency_rows(self, denominations: list[dict[str, Any]]) -> None:
        """Rebuilds the settings currency editor from saved denominations."""

        self._clear_settings_currency_rows()

        for denomination in denominations:
            self._append_settings_currency_row(denomination)

        self._sync_settings_currency_rows()

    def _clear_settings_currency_rows(self) -> None:
        """Clears all dynamic settings currency rows."""

        for row_widget in list(self.currency_row_widgets):
            self.currency_rows_layout.removeRow(row_widget)

        self.currency_name_inputs.clear()
        self.currency_plural_inputs.clear()
        self.currency_value_inputs.clear()
        self.currency_row_widgets.clear()
        self.currency_remove_buttons.clear()

    def _sync_settings_currency_rows(self) -> None:
        """Updates currency labels and baseline-value state."""

        previous_loading = self._loading_settings
        self._loading_settings = True

        try:
            row_count = len(self.currency_row_widgets)

            for index, (row_widget, value_input, remove_button) in enumerate(
                zip(
                    self.currency_row_widgets,
                    self.currency_value_inputs,
                    self.currency_remove_buttons,
                )
            ):
                label = self.currency_rows_layout.labelForField(row_widget)

                if label is not None:
                    label.setText(f"Currency {index + 1}:")

                if index == 0:
                    value_input.setValue(1)
                    value_input.setEnabled(False)
                else:
                    value_input.setEnabled(True)

                remove_button.setVisible(row_count > 1)
        finally:
            self._loading_settings = previous_loading

    def _settings_currency_denominations(self) -> list[dict[str, Any]]:
        """Reads nonblank settings currency rows."""

        denominations: list[dict[str, Any]] = []

        for name_input, plural_input, value_input in zip(
            self.currency_name_inputs,
            self.currency_plural_inputs,
            self.currency_value_inputs,
        ):
            name = name_input.text().strip()

            if not name:
                continue

            denominations.append(
                {
                    "name": name,
                    "plural_name": plural_input.text().strip() or name,
                    "value": value_input.value(),
                }
            )

        if denominations:
            denominations[0]["value"] = 1

        return denominations

    def refresh(self) -> None:
        """Reloads settings."""

        repository = self.repository()
        self._autosave_timer.stop()
        self._loading_settings = True

        try:
            if repository is None:
                self.theme_combo.setCurrentText("Light")
                _set_combo_to_data(self.narration_tense_combo, DEFAULT_NARRATION_TENSE)
                _set_combo_to_data(self.narration_style_combo, DEFAULT_NARRATION_STYLE)
                self.additional_ai_context_input.clear()
                self.music_enabled_checkbox.setChecked(True)
                self.music_volume_slider.setValue(25)
                self._load_settings_currency_rows([])
                self.add_settings_currency_button.setEnabled(False)

                if self.tts_settings_button is not None:
                    self.tts_settings_button.setEnabled(False)

                if self.custom_voice_button is not None:
                    self.custom_voice_button.setEnabled(False)

                if self.narrator_enabled_checkbox is not None:
                    self.narrator_enabled_checkbox.setChecked(True)

                if self.tts_volume_slider is not None:
                    self.tts_volume_slider.setValue(90)

                if self.tts_voice_combo is not None:
                    _set_combo_to_data(self.tts_voice_combo, DEFAULT_NARRATOR_VOICE)

                self._sync_narrator_control_states(
                    self.narrator_enabled_checkbox.isChecked()
                    if self.narrator_enabled_checkbox is not None
                    else False
                )
                return

            theme = repository.get_setting("theme", "Light")
            additional_ai_context = repository.get_setting("ai.additional_context", "")
            narration_preferences = normalize_narration_preferences(
                {
                    "tense": repository.get_setting(
                        "ai.narration_tense",
                        DEFAULT_NARRATION_TENSE,
                    ),
                    "style": repository.get_setting(
                        "ai.narration_style",
                        DEFAULT_NARRATION_STYLE,
                    ),
                }
            )
            denominations = repository.get_currency_denominations()

            if theme in ["Light", "Dark"]:
                self.theme_combo.setCurrentText(str(theme))
            else:
                LOGGER.warning("Unknown theme setting '%s'. Falling back to Light.", theme)
                self.theme_combo.setCurrentText("Light")
                repository.set_setting("theme", "Light")

            self._load_settings_currency_rows(denominations)
            self.add_settings_currency_button.setEnabled(True)

            _set_combo_to_data(
                self.narration_tense_combo,
                narration_preferences["tense"],
            )
            _set_combo_to_data(
                self.narration_style_combo,
                narration_preferences["style"],
            )
            self.additional_ai_context_input.setPlainText(str(additional_ai_context))
            self.music_enabled_checkbox.setChecked(
                _bool_setting(repository.get_setting("audio.music_enabled", True), True)
            )
            self.music_volume_slider.setValue(
                _clamped_int(repository.get_setting("audio.music_volume", 25), 25, 0, 100)
            )

            if self.tts_settings_button is not None:
                self.tts_settings_button.setEnabled(True)

            if self.custom_voice_button is not None:
                self.custom_voice_button.setEnabled(True)

            if self.narrator_enabled_checkbox is not None:
                self.narrator_enabled_checkbox.setChecked(
                    _bool_setting(repository.get_setting("audio.narrator_enabled", True), True)
                )

            if self.tts_volume_slider is not None:
                self.tts_volume_slider.setValue(
                    _clamped_int(repository.get_setting("audio.tts_volume", 90), 90, 0, 100)
                )

            if self.tts_voice_combo is not None:
                _set_combo_to_data(
                    self.tts_voice_combo,
                    normalize_narrator_voice(
                        repository.get_setting(
                            "audio.tts_voice",
                            DEFAULT_NARRATOR_VOICE,
                        )
                    ),
                )

            self._sync_narrator_control_states(
                self.narrator_enabled_checkbox.isChecked()
                if self.narrator_enabled_checkbox is not None
                else False
            )
        finally:
            self._loading_settings = False

    def _schedule_settings_save(self) -> None:
        """Debounces text-field autosaves."""

        if self._loading_settings or self._saving_settings:
            return

        self._autosave_timer.start()

    def _save_settings(self) -> None:
        """Autosaves settings to the active save."""

        repository = self.repository()

        if repository is None or self._loading_settings or self._saving_settings:
            return

        self._autosave_timer.stop()
        self._saving_settings = True

        try:
            repository.set_setting("theme", self.theme_combo.currentText())
            repository.set_setting(
                "ai.additional_context",
                self.additional_ai_context_input.toPlainText().strip(),
            )
            repository.set_setting(
                "ai.narration_tense",
                self.narration_tense_combo.currentData() or DEFAULT_NARRATION_TENSE,
            )
            repository.set_setting(
                "ai.narration_style",
                self.narration_style_combo.currentData() or DEFAULT_NARRATION_STYLE,
            )
            repository.set_setting("audio.music_enabled", self.music_enabled_checkbox.isChecked())
            repository.set_setting("audio.music_volume", self.music_volume_slider.value())

            if self.narrator_enabled_checkbox is not None:
                repository.set_setting(
                    "audio.narrator_enabled",
                    self.narrator_enabled_checkbox.isChecked(),
                )

            if self.tts_volume_slider is not None:
                repository.set_setting("audio.tts_volume", self.tts_volume_slider.value())

            if self.tts_voice_combo is not None:
                repository.set_setting("audio.tts_voice", self._tts_voice_value())

            repository.set_currency_denominations(self._settings_currency_denominations())
            if self.on_audio_settings_changed is not None:
                self.on_audio_settings_changed()
            if self.on_theme_changed is not None:
                self.on_theme_changed()
        finally:
            self._saving_settings = False

        self.notify_repository_changed()

    def _open_tts_settings_dialog(self) -> None:
        """Opens the save-specific TTS settings dialog."""

        repository = self.repository()

        if repository is None:
            return

        dialog = TTSSettingsDialog(
            self,
            audio_settings=self._current_tts_settings(repository),
            voice_options=self.voice_options,
            on_sample_voice=self._sample_voice,
            custom_voice_storage_path=self.custom_voice_storage_path,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_tts_settings(
            dialog.build_audio_settings(),
            persist_app_defaults=dialog.custom_voice_library_changed,
        )

    def _open_custom_voice_dialog(self) -> None:
        """Opens the save-specific custom voice manager."""

        repository = self.repository()

        if repository is None:
            return

        dialog = CustomVoiceDialog(
            self,
            audio_settings=self._current_tts_settings(repository),
            voice_options=self.voice_options,
            on_sample_voice=self._sample_voice,
            storage_path=self.custom_voice_storage_path,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_tts_settings(
            dialog.build_audio_settings(),
            persist_app_defaults=dialog.custom_voice_library_changed,
        )

    def _current_tts_settings(self, repository: SaveRepository) -> dict[str, Any]:
        """Reads current save TTS settings into one normalized audio object."""

        return normalize_tts_audio_fields(
            {
                "narrator_enabled": repository.get_setting("audio.narrator_enabled", True),
                "tts_volume": repository.get_setting("audio.tts_volume", 90),
                "tts_voice": repository.get_setting("audio.tts_voice", DEFAULT_NARRATOR_VOICE),
                "tts_speed": repository.get_setting(
                    "audio.tts_speed",
                    DEFAULT_TTS_SPEED_PERCENT,
                ),
                "tts_voice_mode": repository.get_setting("audio.tts_voice_mode", "preset"),
                "tts_voice_blend": repository.get_setting("audio.tts_voice_blend", {}),
                "tts_custom_voices": merge_custom_voices(
                    repository.get_setting("audio.tts_custom_voices", []),
                    self._global_custom_voices(),
                ),
            },
            tts_enabled=self.tts_enabled,
        )

    def _save_tts_settings(
        self,
        audio_settings: dict[str, Any],
        *,
        persist_app_defaults: bool = False,
    ) -> None:
        """Persists save-specific TTS settings and applies them live."""

        repository = self.repository()

        if repository is None or self._loading_settings or self._saving_settings:
            return

        audio = normalize_tts_audio_fields(audio_settings, tts_enabled=self.tts_enabled)
        self._saving_settings = True

        try:
            repository.set_setting("audio.narrator_enabled", audio["narrator_enabled"])
            repository.set_setting("audio.tts_volume", audio["tts_volume"])
            repository.set_setting("audio.tts_voice", audio["tts_voice"])
            repository.set_setting("audio.tts_speed", audio["tts_speed"])
            repository.set_setting("audio.tts_voice_mode", audio["tts_voice_mode"])
            repository.set_setting("audio.tts_voice_blend", audio["tts_voice_blend"])
            repository.set_setting("audio.tts_custom_voices", audio["tts_custom_voices"])
            if persist_app_defaults and self.on_app_tts_settings_saved is not None:
                self.on_app_tts_settings_saved(audio)
            if self.on_audio_settings_changed is not None:
                self.on_audio_settings_changed()
        finally:
            self._saving_settings = False

        self.notify_repository_changed()

    def _global_custom_voices(self) -> list[dict[str, Any]]:
        """Returns app-level custom voices available outside the active save."""

        if self.global_tts_settings_provider is None:
            return []

        try:
            global_audio = self.global_tts_settings_provider()
        except Exception as error:
            LOGGER.warning("Failed to read app custom voices: %s", error)
            return []

        return normalize_tts_audio_fields(global_audio, tts_enabled=self.tts_enabled)[
            "tts_custom_voices"
        ]

    def _handle_narrator_enabled_toggled(self, checked: bool) -> None:
        """Saves narrator enabled changes and syncs dependent controls."""

        self._sync_narrator_control_states(checked)
        self._save_settings()

    def _tts_voice_value(self) -> str:
        """Returns the selected narrator voice id."""

        return normalize_narrator_voice(
            _combo_current_data_text(self.tts_voice_combo, DEFAULT_NARRATOR_VOICE)
        )

    def _sync_narrator_control_states(self, checked: bool) -> None:
        """Enables narrator-specific controls only when narration is enabled."""

        if self.tts_volume_slider is not None:
            self.tts_volume_slider.setEnabled(checked)
        if self.tts_voice_combo is not None:
            self.tts_voice_combo.setEnabled(checked)
        if self.sample_voice_button is not None:
            self.sample_voice_button.setEnabled(checked and self.on_sample_voice is not None)

    def _sample_voice(
        self,
        voice: str | None = None,
        volume: int | None = None,
        speed: int | None = None,
    ) -> bool | None:
        """Plays the selected voice sample."""

        if self.on_sample_voice is None:
            return None

        return _invoke_sample_voice_callback(
            self.on_sample_voice,
            voice or self._tts_voice_value(),
            self._tts_volume_value() if volume is None else int(volume),
            DEFAULT_TTS_SPEED_PERCENT if speed is None else int(speed),
        )

    def _tts_volume_value(self) -> int:
        """Returns the selected narrator volume."""

        if self.tts_volume_slider is None:
            return 0

        return self.tts_volume_slider.value()


def _refresh_repository_calendar_time(repository: SaveRepository) -> None:
    """Recomputes the saved display time from current calendar settings."""

    elapsed_minutes = _safe_int(
        repository.get_state_value(
            "elapsed_minutes",
            str(DEFAULT_START_ELAPSED_MINUTES),
        ),
        DEFAULT_START_ELAPSED_MINUTES,
    )
    calendar_snapshot = build_calendar_snapshot(
        elapsed_minutes,
        repository.get_calendar_settings(),
    )
    repository.set_state_value("time", calendar_snapshot["display_label"])


def _apply_audio_settings_to_managers(
    repository: SaveRepository,
    *,
    sound_manager: SoundManager | None,
    narration_player: NarrationPlayer | None,
) -> None:
    """Applies saved music and narrator settings to runtime audio managers."""

    music_enabled = _bool_setting(repository.get_setting("audio.music_enabled", True), True)
    narrator_enabled = _bool_setting(
        repository.get_setting("audio.narrator_enabled", True),
        True,
    )
    music_volume = _clamped_int(repository.get_setting("audio.music_volume", 25), 25, 0, 100)
    tts_audio = normalize_tts_audio_fields(
        {
            "narrator_enabled": narrator_enabled,
            "tts_volume": repository.get_setting("audio.tts_volume", 90),
            "tts_voice": repository.get_setting("audio.tts_voice", DEFAULT_NARRATOR_VOICE),
            "tts_speed": repository.get_setting("audio.tts_speed", DEFAULT_TTS_SPEED_PERCENT),
            "tts_voice_mode": repository.get_setting("audio.tts_voice_mode", "preset"),
            "tts_voice_blend": repository.get_setting("audio.tts_voice_blend", {}),
            "tts_custom_voices": repository.get_setting("audio.tts_custom_voices", []),
        }
    )
    tts_volume = int(tts_audio["tts_volume"])
    tts_voice = active_voice_spec_from_audio(tts_audio)
    tts_speed = int(tts_audio["tts_speed"])

    if sound_manager is not None:
        sound_manager.set_music_volume(music_volume)
        sound_manager.set_music_enabled(music_enabled)

        current_music = str(repository.get_setting("audio.current_music", "") or "").strip()

        if music_enabled and current_music:
            sound_manager.play_music(current_music)
        elif not music_enabled:
            sound_manager.stop_music(clear_current=False)

    if narration_player is not None and hasattr(narration_player, "set_volume"):
        narration_player.set_volume(tts_volume)
    if narration_player is not None and hasattr(narration_player, "set_speed"):
        narration_player.set_speed(tts_speed)
    if narration_player is not None and hasattr(narration_player, "set_voice"):
        narration_player.set_voice(tts_voice)
    if narration_player is not None and hasattr(narration_player, "set_enabled"):
        narration_player.set_enabled(narrator_enabled)


def _preserved_player_character_fields(
    setup: dict[str, Any],
    ai_character: Any,
) -> dict[str, str]:
    """Returns character fields while preserving explicit player setup values."""

    clean_setup = normalize_new_game_setup(setup)
    setup_character = clean_setup["character"]
    ai_character = ai_character if isinstance(ai_character, dict) else {}
    preserved: dict[str, str] = {}

    for key in ("name", "appearance", "backstory", "notes"):
        setup_value = str(setup_character.get(key, "")).strip()
        ai_value = str(ai_character.get(key, "")).strip()

        if _is_player_provided_character_field(key, setup_value):
            preserved[key] = setup_value
        elif ai_value:
            preserved[key] = ai_value
        elif setup_value:
            preserved[key] = setup_value

    return preserved


def _preserve_player_character_text(
    value: Any,
    setup: dict[str, Any],
    ai_character: Any,
) -> Any:
    """Repairs generated text that renamed an explicitly supplied character."""

    replacements = _player_character_name_replacements(setup, ai_character)

    if not replacements:
        return value

    if isinstance(value, str):
        clean_value = value

        for source, target in replacements:
            clean_value = _replace_whole_name(clean_value, source, target)

        return clean_value

    if isinstance(value, list):
        return [
            _preserve_player_character_text(item, setup, ai_character)
            for item in value
        ]

    if isinstance(value, dict):
        return {
            _preserve_player_character_text(key, setup, ai_character)
            if isinstance(key, str)
            else key: _preserve_player_character_text(item, setup, ai_character)
            for key, item in value.items()
        }

    return value


def _player_character_name_replacements(
    setup: dict[str, Any],
    ai_character: Any,
) -> list[tuple[str, str]]:
    """Builds safe character-name replacements for AI-renamed setup text."""

    clean_setup = normalize_new_game_setup(setup)
    player_name = str(clean_setup["character"].get("name", "")).strip()

    if not _is_player_provided_character_field("name", player_name):
        return []

    if not isinstance(ai_character, dict):
        return []

    ai_name = str(ai_character.get("name", "")).strip()

    if not ai_name or ai_name.casefold() == player_name.casefold():
        return []

    replacements = [(ai_name, player_name)]
    ai_first = ai_name.split()[0] if ai_name.split() else ""
    player_first = player_name.split()[0] if player_name.split() else player_name

    if ai_first and player_first and ai_first.casefold() != player_first.casefold():
        replacements.append((ai_first, player_first))

    return replacements


def _is_player_provided_character_field(key: str, value: str) -> bool:
    """Returns True when a character field is a custom player value."""

    clean_value = str(value or "").strip()

    if not clean_value:
        return False

    if key == "name" and clean_value == "Player Name":
        return False

    return True


def _replace_whole_name(text: str, source: str, target: str) -> str:
    """Replaces a generated name without touching substrings inside words."""

    if not source:
        return text

    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(source)}(?![A-Za-z0-9])",
        flags=re.IGNORECASE,
    )
    return pattern.sub(target, text)


def _normalize_theme_name(theme: str) -> str:
    """Returns a supported theme name."""

    clean_theme = str(theme or "Light").strip()
    return clean_theme if clean_theme in THEME_NAMES else "Light"


def _application_uses_dark_theme() -> bool:
    """Returns True when the active Qt application palette is dark."""

    app = QApplication.instance()

    if not isinstance(app, QApplication):
        return False

    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def _next_available_save_title(saves_dir: Path, requested_title: str) -> str:
    """Returns the first non-conflicting player-facing save title."""

    base_title = str(requested_title or "").strip() or "New Adventure"

    if not SaveRepository.save_title_exists(saves_dir, base_title):
        return base_title

    suffix = 2

    while True:
        candidate = f"{base_title} {suffix}"

        if not SaveRepository.save_title_exists(saves_dir, candidate):
            return candidate

        suffix += 1


def _light_theme_palette() -> QPalette:
    """Builds a light, high-contrast application palette."""

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f5f7fb"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#eef2f7"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#4b5563"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#e5e7eb"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#1d4ed8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#111827"))
    return palette


def _dark_theme_palette() -> QPalette:
    """Builds a dark, high-contrast application palette."""

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#121416"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2a2d30"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#a9b0b6"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#303437"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4c8fcb"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#303437"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f1f3f4"))
    return palette


def _light_theme_stylesheet() -> str:
    """Returns stylesheet rules that make the light theme visible on all platforms."""

    return """
        QWidget {
            background-color: #f5f7fb;
            color: #111827;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {
            background-color: #ffffff;
            color: #111827;
            border: 1px solid #64748b;
            selection-background-color: #1d4ed8;
            selection-color: #ffffff;
        }
        QComboBox, QSpinBox {
            min-height: 24px;
            padding: 3px 6px;
        }
        QComboBox {
            padding-right: 40px;
        }
        QSpinBox {
            padding-right: 36px;
        }
        QComboBox::drop-down {
            background-color: #e2e8f0;
            border-left: 1px solid #94a3b8;
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 32px;
        }
        QComboBox::down-arrow {
            image: none;
            width: 0;
            height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #111827;
        }
        QComboBox QAbstractItemView {
            background-color: #ffffff;
            color: #111827;
            selection-background-color: #1d4ed8;
            selection-color: #ffffff;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            background-color: #e2e8f0;
            border-left: 1px solid #94a3b8;
            subcontrol-origin: border;
            width: 28px;
        }
        QSpinBox::up-button {
            border-bottom: 1px solid #94a3b8;
            subcontrol-position: top right;
            height: 14px;
        }
        QSpinBox::down-button {
            subcontrol-position: bottom right;
            height: 14px;
        }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
            border-color: #1d4ed8;
        }
        QLabel, QCheckBox {
            background-color: transparent;
            color: #111827;
        }
        QCheckBox::indicator {
            background-color: #ffffff;
            border: 1px solid #64748b;
            border-radius: 3px;
            height: 16px;
            width: 16px;
        }
        QCheckBox::indicator:hover {
            border-color: #1d4ed8;
        }
        QCheckBox::indicator:checked {
            background-color: #2563eb;
            border-color: #1d4ed8;
        }
        QSlider::groove:horizontal {
            background-color: #d1d9e6;
            border: 1px solid #94a3b8;
            border-radius: 4px;
            height: 8px;
        }
        QSlider::handle:horizontal {
            background-color: #2563eb;
            border: 1px solid #1e40af;
            border-radius: 7px;
            margin: -4px 0;
            width: 14px;
        }
        QPushButton {
            background-color: #e5e7eb;
            color: #111827;
            border: 1px solid #64748b;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #dbe4ee;
            border-color: #475569;
        }
        QPushButton:default {
            background-color: #2563eb;
            border-color: #1d4ed8;
            color: #ffffff;
        }
        QTabWidget::pane {
            border: 1px solid #94a3b8;
        }
        QTabBar::tab {
            background-color: #e5e7eb;
            color: #111827;
            border: 1px solid #94a3b8;
            padding: 6px 10px;
        }
        QTabBar::tab:selected {
            background-color: #ffffff;
            border-bottom-color: #ffffff;
        }
        QHeaderView::section {
            background-color: #e2e8f0;
            color: #111827;
            border: 1px solid #cbd5e1;
            padding: 4px;
        }
    """


def _dark_theme_stylesheet() -> str:
    """Returns stylesheet rules that make the dark theme visible on all platforms."""

    return """
        QWidget {
            background-color: #202124;
            color: #f1f3f4;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {
            background-color: #121416;
            color: #f1f3f4;
            border: 1px solid #4b5258;
            selection-background-color: #4c8fcb;
            selection-color: #ffffff;
        }
        QComboBox, QSpinBox {
            min-height: 24px;
            padding: 3px 6px;
        }
        QComboBox {
            padding-right: 40px;
        }
        QSpinBox {
            padding-right: 36px;
        }
        QComboBox::drop-down {
            background-color: #303437;
            border-left: 1px solid #5b6268;
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 32px;
        }
        QComboBox::down-arrow {
            image: none;
            width: 0;
            height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #f1f3f4;
        }
        QComboBox QAbstractItemView {
            background-color: #121416;
            color: #f1f3f4;
            selection-background-color: #4c8fcb;
            selection-color: #ffffff;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            background-color: #303437;
            border-left: 1px solid #5b6268;
            subcontrol-origin: border;
            width: 28px;
        }
        QSpinBox::up-button {
            border-bottom: 1px solid #5b6268;
            subcontrol-position: top right;
            height: 14px;
        }
        QSpinBox::down-button {
            subcontrol-position: bottom right;
            height: 14px;
        }
        QLabel, QCheckBox {
            background-color: transparent;
            color: #f1f3f4;
        }
        QCheckBox::indicator {
            background-color: #121416;
            border: 1px solid #5b6268;
            border-radius: 3px;
            height: 16px;
            width: 16px;
        }
        QCheckBox::indicator:hover {
            border-color: #4c8fcb;
        }
        QCheckBox::indicator:checked {
            background-color: #4c8fcb;
            border-color: #2f6fb0;
        }
        QPushButton {
            background-color: #303437;
            color: #f1f3f4;
            border: 1px solid #5b6268;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #3c4247;
        }
        QTabWidget::pane {
            border: 1px solid #4b5258;
        }
        QTabBar::tab {
            background-color: #303437;
            color: #f1f3f4;
            border: 1px solid #4b5258;
            padding: 6px 10px;
        }
        QTabBar::tab:selected {
            background-color: #202124;
            border-bottom-color: #202124;
        }
        QHeaderView::section {
            background-color: #303437;
            color: #f1f3f4;
            border: 1px solid #4b5258;
            padding: 4px;
        }
    """


def _slider_row(slider: QSlider, value_label: QLabel) -> QWidget:
    """Builds a compact slider row with a fixed-width value label."""

    value_label.setFixedWidth(42)
    row = QHBoxLayout()
    row.addWidget(slider)
    row.addWidget(value_label)

    widget = QWidget()
    widget.setLayout(row)
    return widget


def _spin_pair_row(current_spin: QSpinBox, max_spin: QSpinBox) -> QWidget:
    """Builds a compact current/max spin-box row."""

    row = QHBoxLayout()
    row.addWidget(current_spin)
    row.addWidget(QLabel("/"))
    row.addWidget(max_spin)
    row.addStretch()
    widget = QWidget()
    widget.setLayout(row)
    return widget


def _button_row(*widgets: QWidget) -> QWidget:
    """Builds a compact horizontal control row."""

    row = QHBoxLayout()

    for widget in widgets:
        row.addWidget(widget)

    row.addStretch()

    container = QWidget()
    container.setLayout(row)
    return container


def _invoke_sample_voice_callback(
    callback: Callable[..., bool] | None,
    voice: str,
    volume: int,
    speed: int,
) -> bool:
    """Calls old and new sample-voice callbacks."""

    if callback is None:
        return False

    try:
        return bool(callback(voice, volume, speed))
    except TypeError:
        return bool(callback(voice, volume))


def _narrator_voice_options(narration_player: Any | None = None) -> dict[str, str]:
    """Returns known narrator voice display options."""

    if narration_player is not None and hasattr(narration_player, "get_available_voices"):
        try:
            voices = narration_player.get_available_voices()
        except Exception as error:
            LOGGER.warning("Could not read narrator voice options: %s", error)
        else:
            if isinstance(voices, dict) and voices:
                return {
                    str(label): str(voice_id)
                    for label, voice_id in voices.items()
                    if str(label).strip() and str(voice_id).strip()
                }

    return available_narrator_voices()


def _custom_voice_display_text(blend: dict[str, Any]) -> str:
    """Returns a compact custom voice summary."""

    clean_blend = normalize_voice_blend(blend)
    return (
        f"{clean_blend['name']} ({voice_display_name(clean_blend['voice_a'])} "
        f"{clean_blend['voice_a_weight']}% / "
        f"{voice_display_name(clean_blend['voice_b'])} "
        f"{clean_blend['voice_b_weight']}%)"
    )


def _populate_narrator_voice_combo(
    combo: QComboBox,
    selected_voice: Any,
    *,
    voice_options: dict[str, str] | None = None,
) -> None:
    """Fills a narrator voice combo and selects a supported voice id."""

    combo.clear()
    combo.setMinimumWidth(220)
    voices = voice_options or available_narrator_voices()

    for label, voice_id in voices.items():
        combo.addItem(label, voice_id)

    _set_combo_to_data(combo, normalize_narrator_voice(selected_voice))


def _combo_current_data_text(combo: QComboBox | None, default: str) -> str:
    """Returns a combo box's current data as text."""

    if combo is None:
        return default

    value = combo.currentData()
    return str(value or default)


def _row_for_cell_widget(table: QTableWidget, widget: QWidget) -> int:
    """Returns the table row containing widget, or -1 when not found."""

    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            if table.cellWidget(row, column) is widget:
                return row

    return -1


def _table_line_edit(text: str) -> QLineEdit:
    """Builds an inline table editor that focuses like a native text box."""

    line_edit = QLineEdit()
    line_edit.setText(text)
    line_edit.setFrame(False)
    line_edit.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    line_edit.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    return line_edit


def _table_spin_box(minimum: int, maximum: int) -> QSpinBox:
    """Builds an inline table number editor with table-wide sizing."""

    spin_box = QSpinBox()
    spin_box.setMinimum(minimum)
    spin_box.setMaximum(maximum)
    spin_box.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    spin_box.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    return spin_box


def _set_table_column_widths(table: QTableWidget, widths: tuple[int, ...]) -> None:
    """Applies stable table column widths so inline editors do not autoshrink."""

    for column, width in enumerate(widths):
        if column < table.columnCount():
            table.setColumnWidth(column, width)


def _configure_inline_table(
    table: QTableWidget,
    widths: tuple[int, ...],
    *,
    minimum_height: int,
) -> None:
    """Applies the shared inline-editing table behavior."""

    table.setMinimumHeight(minimum_height)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(36)
    table.horizontalHeader().setStretchLastSection(False)
    table.setAlternatingRowColors(True)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    _set_table_column_widths(table, widths)


def _append_starter_item_table_row(
    table: QTableWidget,
    item: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable starter-item row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)
    name_input = _table_line_edit(str(item.get("name", "")))
    category_input = _table_line_edit(str(item.get("category", "Item") or "Item"))
    description_input = _table_line_edit(str(item.get("description", "")))

    quantity_input = _table_spin_box(1, 999_999)
    quantity_input.setValue(_safe_int(item.get("quantity", 1), 1))

    value_input = _table_spin_box(0, 1_000_000_000)
    value_input.setValue(_safe_int(item.get("value_base_units", 0), 0))

    remove_button = QPushButton("Remove")
    remove_button.setMinimumWidth(TABLE_INLINE_BUTTON_MIN_WIDTH)
    remove_button.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    remove_button.clicked.connect(
        lambda _checked=False, button=remove_button: remove_callback(button)
    )

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, quantity_input)
    table.setCellWidget(row, 2, category_input)
    table.setCellWidget(row, 3, description_input)
    table.setCellWidget(row, 4, value_input)
    table.setCellWidget(row, 5, remove_button)
    _set_table_column_widths(table, STARTER_ITEM_COLUMN_WIDTHS)


def _starter_items_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads starter-item rows from table."""

    items: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        name_widget = table.cellWidget(row, 0)
        quantity_widget = table.cellWidget(row, 1)
        category_widget = table.cellWidget(row, 2)
        description_widget = table.cellWidget(row, 3)
        value_widget = table.cellWidget(row, 4)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        items.append(
            {
                "name": name,
                "category": (
                    category_widget.text().strip()
                    if isinstance(category_widget, QLineEdit)
                    and category_widget.text().strip()
                    else "Item"
                ),
                "quantity": quantity_widget.value() if isinstance(quantity_widget, QSpinBox) else 1,
                "description": (
                    description_widget.text().strip()
                    if isinstance(description_widget, QLineEdit)
                    else ""
                ),
                "value_base_units": value_widget.value() if isinstance(value_widget, QSpinBox) else 0,
                "item_request": "",
                "requires_ai_invention": False,
            }
        )

    return items


def _append_currency_table_row(
    table: QTableWidget,
    denomination: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable currency denomination row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name = str(denomination.get("name", ""))
    plural_name = str(denomination.get("plural_name", ""))
    name_input = _table_line_edit(name)
    plural_name_input = _table_line_edit(plural_name)
    value_input = _table_spin_box(1, 1_000_000_000)
    value_input.setValue(_safe_int(denomination.get("value", 1), 1))
    remove_button = QPushButton("Remove")
    remove_button.setMinimumWidth(TABLE_INLINE_BUTTON_MIN_WIDTH)
    remove_button.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    remove_button.clicked.connect(
        lambda _checked=False, button=remove_button: remove_callback(button)
    )

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, plural_name_input)
    table.setCellWidget(row, 2, value_input)
    table.setCellWidget(row, 3, remove_button)
    _sync_currency_base_value_row(table)
    _set_table_column_widths(table, CURRENCY_COLUMN_WIDTHS)


def _sync_currency_base_value_row(table: QTableWidget) -> None:
    """Keeps the first visible currency row as the baseline denomination."""

    for row in range(table.rowCount()):
        value_widget = table.cellWidget(row, 2)

        if not isinstance(value_widget, QSpinBox):
            continue

        if row == 0:
            value_widget.setValue(1)
            value_widget.setEnabled(False)
        else:
            value_widget.setEnabled(True)


def _currency_denominations_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads currency denomination rows from table."""

    denominations: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        name_widget = table.cellWidget(row, 0)
        plural_widget = table.cellWidget(row, 1)
        value_widget = table.cellWidget(row, 2)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        denominations.append(
            {
                "name": name,
                "plural_name": (
                    plural_widget.text().strip()
                    if isinstance(plural_widget, QLineEdit)
                    else ""
                )
                or name,
                "value": value_widget.value() if isinstance(value_widget, QSpinBox) else 1,
            }
        )

    return denominations


def _append_economy_example_table_row(
    table: QTableWidget,
    example: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one common-price example row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name_input = _table_line_edit(str(example.get("name", "")))
    value_input = _table_spin_box(1, 1_000_000_000)
    value_input.setValue(_safe_int(example.get("value_base_units", 1), 1))
    remove_button = QPushButton("Remove")
    remove_button.setMinimumWidth(TABLE_INLINE_BUTTON_MIN_WIDTH)
    remove_button.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    remove_button.clicked.connect(
        lambda _checked=False, button=remove_button: remove_callback(button)
    )

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, value_input)
    table.setCellWidget(row, 2, remove_button)
    _set_table_column_widths(table, ECONOMY_EXAMPLE_COLUMN_WIDTHS)


def _economy_examples_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads common-price examples from table."""

    examples: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        name_widget = table.cellWidget(row, 0)
        value_widget = table.cellWidget(row, 1)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        examples.append(
            {
                "name": name,
                "value_base_units": (
                    value_widget.value()
                    if isinstance(value_widget, QSpinBox)
                    else 1
                ),
            }
        )

    return normalize_economy_examples(examples)


def _remove_table_row_by_button(table: QTableWidget, button: QPushButton) -> int:
    """Removes the table row containing button and returns the removed row."""

    row = _row_for_cell_widget(table, button)

    if row >= 0:
        table.removeRow(row)

    return row


def _bool_setting(value: Any, default: bool) -> bool:
    """Reads a flexible boolean setting."""

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().casefold()

        if normalized in {"true", "1", "yes", "on"}:
            return True

        if normalized in {"false", "0", "no", "off"}:
            return False

    return default


def _clamped_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Returns an integer clamped to the provided range."""

    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        parsed_value = default

    return max(minimum, min(maximum, parsed_value))


def _ai_skills_match_setup(
    ai_skills: list[dict[str, Any]],
    setup_skills: Any,
) -> bool:
    """Returns True when AI-finalized skills preserve the setup level spread."""

    if not isinstance(setup_skills, list):
        return False

    if len(ai_skills) != len(setup_skills):
        return False

    try:
        ai_levels = sorted(int(skill.get("level", 0)) for skill in ai_skills)
        setup_levels = sorted(int(skill.get("level", 0)) for skill in setup_skills)
    except (AttributeError, TypeError, ValueError):
        return False

    if ai_levels != setup_levels:
        return False

    return all(
        str(skill.get("name", "")).strip()
        and str(skill.get("description", "")).strip()
        for skill in ai_skills
    )


def _deduplicated_ai_skills(ai_skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns AI-finalized skills with unique names suitable for persistence."""

    deduplicated_skills: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for raw_skill in ai_skills:
        skill = dict(raw_skill)
        name = str(skill.get("name", "")).strip()
        folded_name = name.casefold()

        if folded_name in seen_names:
            try:
                level = int(skill.get("level", 0))
            except (TypeError, ValueError):
                level = 0

            name = _unique_ai_skill_name(
                name,
                seen_names,
                suffix=_duplicate_skill_suffix(level),
            )
            skill["name"] = name
            LOGGER.info("Renamed duplicate AI-finalized skill to %s.", name)

        seen_names.add(name.casefold())
        deduplicated_skills.append(skill)

    return deduplicated_skills


def _unique_ai_skill_name(base_name: str, seen_names: set[str], *, suffix: str) -> str:
    """Builds a unique generated skill name from an AI duplicate."""

    clean_base = base_name.strip() or "Generated Skill"
    clean_suffix = suffix.strip() or "Alternate"
    candidate = f"{clean_base} ({clean_suffix})"

    if candidate.casefold() not in seen_names:
        return candidate

    index = 2

    while True:
        candidate = f"{clean_base} ({clean_suffix} {index})"

        if candidate.casefold() not in seen_names:
            return candidate

        index += 1


def _duplicate_skill_suffix(level: int) -> str:
    """Returns a compact descriptor for duplicate generated skill names."""

    return {
        5: "Signature",
        4: "Expert",
        3: "Skilled",
        2: "Trained",
        1: "Familiar",
    }.get(level, "Alternate")


def _starter_items_for_save(
    ai_items: list[dict[str, Any]],
    setup: dict[str, Any],
) -> list[dict[str, Any]]:
    """Returns AI starter items while preserving explicit named setup items."""

    setup_items = setup.get("starter_items", [])

    if not isinstance(setup_items, list):
        setup_items = []

    completed_items = [item for item in ai_items if isinstance(item, dict)]
    original_completed_count = len(completed_items)
    used_source_indexes = {
        source_index
        for source_index in (
            _optional_int(item.get("source_index"))
            for item in completed_items
            if isinstance(item, dict)
        )
        if source_index is not None and source_index >= 0
    }
    seen_names = {
        str(item.get("name", "")).strip().casefold()
        for item in completed_items
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }

    for index, setup_item in enumerate(setup_items):
        if index in used_source_indexes:
            continue

        if not isinstance(setup_item, dict):
            continue

        requires_ai_invention = bool(setup_item.get("requires_ai_invention"))

        if requires_ai_invention and len(completed_items) >= STARTER_INVENTORY_MIN_ITEMS:
            continue

        fallback_item = _fallback_starter_item_from_setup(setup_item, source_index=index)

        if not fallback_item:
            continue

        folded_name = fallback_item["name"].casefold()

        if folded_name in seen_names:
            continue

        completed_items.append(fallback_item)
        seen_names.add(folded_name)

    while len(completed_items) < STARTER_INVENTORY_MIN_ITEMS:
        fallback_item = _starter_inventory_top_up_item(seen_names)

        if fallback_item is None:
            break

        completed_items.append(fallback_item)
        seen_names.add(fallback_item["name"].casefold())

    if len(completed_items) > original_completed_count:
        added_count = len(completed_items) - original_completed_count

        if original_completed_count < STARTER_INVENTORY_MIN_ITEMS:
            LOGGER.warning(
                "Gemini returned fewer than %s complete starter item(s); added %s "
                "fallback item(s) so the new save starts with enough inventory.",
                STARTER_INVENTORY_MIN_ITEMS,
                added_count,
            )
        else:
            LOGGER.warning(
                "Gemini omitted %s explicit starter item(s); preserved named setup item(s).",
                added_count,
            )

    return completed_items


def _fallback_starter_item_from_setup(
    raw_item: Any,
    *,
    source_index: int = -1,
) -> dict[str, Any] | None:
    """Builds a structured starter item from a wizard entry when AI output is partial."""

    if not isinstance(raw_item, dict):
        return None

    name = str(raw_item.get("name", "")).strip()
    item_request = str(raw_item.get("item_request", "")).strip()
    description = str(raw_item.get("description", "")).strip()

    if not name:
        name = _starter_item_name_from_request(item_request)

    if not name:
        return None

    return {
        "name": name,
        "category": str(raw_item.get("category", "Item")).strip() or "Item",
        "quantity": max(1, _safe_int(raw_item.get("quantity"), 1)),
        "description": description
        or item_request
        or "Player-requested starter item awaiting AI detail.",
        "value_base_units": max(0, _safe_int(raw_item.get("value_base_units"), 0)),
        "source_index": source_index,
    }


def _starter_inventory_top_up_item(seen_names: set[str]) -> dict[str, Any] | None:
    """Returns a neutral fallback item for short AI starter inventories."""

    fallback_items = [
        {
            "name": "Personal Pack",
            "category": "Container",
            "description": "A sturdy pack for keeping essential belongings close.",
            "value_base_units": 5,
        },
        {
            "name": "Packed Meal",
            "category": "Supply",
            "description": "Simple food set aside for the first stretch of travel.",
            "value_base_units": 2,
        },
        {
            "name": "Water Flask",
            "category": "Supply",
            "description": "A refillable flask of clean drinking water.",
            "value_base_units": 2,
        },
        {
            "name": "Utility Tool",
            "category": "Tool",
            "description": "A compact everyday tool for small repairs and practical tasks.",
            "value_base_units": 4,
        },
        {
            "name": "Weather-Ready Clothes",
            "category": "Clothing",
            "description": "Durable clothing suitable for uncertain conditions.",
            "value_base_units": 6,
        },
        {
            "name": "Personal Keepsake",
            "category": "Personal",
            "description": "A small memento connecting the character to their past.",
            "value_base_units": 1,
        },
    ]

    for fallback_item in fallback_items:
        if fallback_item["name"].casefold() in seen_names:
            continue

        return {
            **fallback_item,
            "quantity": 1,
            "source_index": -1,
        }

    return None


def _starter_item_name_from_request(item_request: str) -> str:
    """Derives a compact item name from a natural-language starter item request."""

    clean_request = str(item_request or "").strip()

    if not clean_request:
        return ""

    candidate = clean_request

    for separator in [" that ", " which ", " with ", " used ", " for ", ".", ",", ";", ":"]:
        before_separator = candidate.split(separator, 1)[0].strip()

        if before_separator:
            candidate = before_separator

    words = [
        word.strip("'\"()[]{}")
        for word in candidate.split()
        if word.strip("'\"()[]{}")
    ]

    while words and words[0].casefold() in {"a", "an", "the", "my", "his", "her", "their", "our"}:
        words.pop(0)

    if not words:
        return ""

    return " ".join(words[:5]).title()


def _resolved_skill_checks_for_context(event_results: list[Any]) -> list[dict[str, Any]]:
    """Converts applied skill-check results into serializable narration context."""

    resolved_checks: list[dict[str, Any]] = []

    for result in event_results:
        if getattr(result, "event_type", "") != "SkillCheckRequestedEvent":
            continue

        if getattr(result, "status", "") != "applied":
            continue

        payload = getattr(result, "payload", {})

        if not isinstance(payload, dict):
            continue

        resolved_checks.append(
            {
                **payload,
                "status": getattr(result, "status", ""),
                "message": getattr(result, "message", ""),
            }
        )

    return resolved_checks


def _optional_int(value: Any) -> int | None:
    """Parses an optional integer."""

    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _append_ai_context_line(existing_context: str, line: str) -> str:
    """Appends an AI-facing setup context line if it is not already present."""

    clean_existing = str(existing_context or "").strip()
    clean_line = str(line or "").strip()

    if not clean_line:
        return clean_existing

    if clean_line in clean_existing.splitlines():
        return clean_existing

    if clean_existing:
        return f"{clean_existing}\n\n{clean_line}"

    return clean_line


def _calendar_type_from_settings(settings: dict[str, Any]) -> str:
    """Infers which calendar option should be selected for saved settings."""

    if bool(settings.get("ai_generated", False)):
        return "ai_generated"

    if str(settings.get("calendar_type", "")).strip().casefold() == "ai_generated":
        return "ai_generated"

    for key, value in GREGORIAN_CALENDAR_SETTINGS.items():
        if key == "time_display":
            continue

        if settings.get(key) != value:
            return "custom"

    return "gregorian"


def _build_season_settings(
    *,
    names: list[str],
    hints: list[str],
    count: int,
) -> list[dict[str, str]]:
    """Builds season setting dictionaries from comma-separated UI lists."""

    seasons: list[dict[str, str]] = []

    for index in range(max(1, count)):
        name = names[index] if index < len(names) else ""
        hint = hints[index] if index < len(hints) else ""
        seasons.append(
            {
                "name": name,
                "weather_hint": hint,
            }
        )

    return seasons


def _set_combo_to_data(combo: QComboBox, value: str) -> None:
    """Selects a combo-box item by its stored data value."""

    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return

    combo.setCurrentIndex(0)


def _add_combo_options(combo: QComboBox, options: dict[str, str]) -> None:
    """Adds keyed display options to a combo box."""

    for value, label in options.items():
        combo.addItem(label, value)


def _set_markdown_text(text_edit: QTextEdit, markdown_text: str) -> None:
    """Sets read-only body text as Markdown when the Qt runtime supports it."""

    if hasattr(text_edit, "setMarkdown"):
        text_edit.setMarkdown(str(markdown_text or ""))
        return

    text_edit.setPlainText(str(markdown_text or ""))


def _player_command_markdown(command: str) -> str:
    """Formats a player command as a Markdown blockquote."""

    lines = [line.strip() for line in str(command or "").splitlines() if line.strip()]

    if not lines:
        return "> **You:**"

    first_line, *remaining_lines = lines
    quoted_lines = [f"> **You:** {first_line}"]
    quoted_lines.extend(f"> {line}" for line in remaining_lines)
    return "\n".join(quoted_lines)


def _safe_int(value, default: int) -> int:
    """Converts a value to int with a fallback."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _split_list(raw_text: str) -> list[str]:
    """Splits comma-separated UI text into a clean string list."""

    return [
        value.strip()
        for value in raw_text.split(",")
        if value.strip()
    ]


def _split_loot_items(text: str) -> list[str]:
    """Splits comma/semicolon-delimited loot names."""

    return [
        part.strip()
        for part in re.split(r"[,;]+", str(text or ""))
        if part.strip()
    ]


def _slug_for_id(value: str) -> str:
    """Returns a compact identifier fragment."""

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "combatant"


def _join_list(values) -> str:
    """Formats a list-like value for table display."""

    if not isinstance(values, list):
        return ""

    return ", ".join(str(value) for value in values if str(value).strip())


def _crafting_ingredient_catalog_choices(
    catalog_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Returns sorted catalog items that may be used as recipe ingredients."""

    choices = [
        item
        for item in catalog_items
        if str(item.get("name", "")).strip()
        and is_crafting_ingredient_category(item.get("category", ""))
    ]
    choices.sort(
        key=lambda item: (
            str(item.get("name", "")).casefold(),
            str(item.get("category", "")).casefold(),
        )
    )
    return choices


def _status_label(label: str, value_label: QLabel) -> QWidget:
    """Builds a compact story status display item."""

    wrapper = QWidget()
    layout = QVBoxLayout()
    title = QLabel(label)
    title.setStyleSheet("font-size: 11px; color: #666;")
    value_label.setStyleSheet("font-weight: bold;")
    layout.addWidget(title)
    layout.addWidget(value_label)
    layout.setContentsMargins(0, 0, 24, 8)
    wrapper.setLayout(layout)
    return wrapper


def _split_day_time(raw_time: str) -> tuple[str, str]:
    """Splits a combined world time string into day and time labels."""

    clean_time = raw_time.strip()

    if not clean_time:
        return "-", "-"

    if "," in clean_time:
        day, time = clean_time.split(",", 1)
        return day.strip() or "-", time.strip() or "-"

    return "-", clean_time


def _skill_level_label(level: int) -> str:
    """Formats skill level as an in-world-friendly training label."""

    labels = {
        1: "Novice",
        2: "Practiced",
        3: "Skilled",
        4: "Expert",
        5: "Master",
    }
    return labels.get(level, "Unknown")
