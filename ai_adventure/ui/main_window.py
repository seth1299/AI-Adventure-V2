from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Protocol

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
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

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.ai.gemini_service import (
    GeminiConfigurationError,
    GeminiNarrationService,
    format_story_message,
)
from ai_adventure.audio.narration import NarrationPlayer
from ai_adventure.audio.sound_manager import SoundManager, prepare_sound_directory
from ai_adventure.audio.tts.tts_manager import create_tts_manager
from ai_adventure.calendar_system import (
    DEFAULT_CALENDAR_SETTINGS,
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    build_month_grid,
    resolve_starting_elapsed_minutes,
)
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.currency import (
    DEFAULT_CURRENCY_DENOMINATIONS,
    FALLBACK_CURRENCY_DENOMINATIONS,
    describe_currency_denominations,
    format_currency_amount,
)
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.new_game_setup import (
    GREGORIAN_CALENDAR_SETTINGS,
    SKILL_LEVEL_PLAN,
    build_new_game_setup_packet,
    fallback_introductory_message,
    fallback_world_summary,
    normalize_new_game_setup,
    parse_starter_items_text,
)
from ai_adventure.new_game_templates import (
    load_new_game_templates,
    save_new_game_template,
)
from ai_adventure.persistence.save_repository import SaveRepository, SaveSummary


LOGGER = logging.getLogger(__name__)


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
    header = table.horizontalHeader()
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(True)
    header.sectionClicked.connect(on_section_clicked)
    table.setSortingEnabled(False)


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
        self.sound_manager = SoundManager(prepare_sound_directory(self.app_paths))
        self.narration_player = NarrationPlayer(
            create_tts_manager(
                model_path=self.app_paths.kokoro_model_path,
                voices_path=self.app_paths.kokoro_voices_path,
                output_directory=self.app_paths.tts_output_dir,
            )
        )

        self.setWindowTitle("AI Adventure")
        self._set_app_icon()
        self.resize(1100, 750)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.main_menu = MainMenuScreen(
            saves_dir=self.app_paths.saves_dir,
            on_new_game=self.start_new_game_wizard,
            on_load_game=self.load_game_from_path,
        )

        self.game_shell = GameShell(
            on_return_to_menu=self.return_to_menu,
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
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

        wizard = NewGameWizard(self, template_setup=template_setup)

        if wizard.exec() != QDialog.DialogCode.Accepted:
            return

        self.create_new_game(wizard.build_setup())

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
                return True, template.setup

        return True, None

    def create_new_game(self, setup: dict[str, Any]) -> None:
        """
        Creates a new save and opens it.

        Args:
            setup: New-game wizard setup dictionary.
        """

        clean_setup = normalize_new_game_setup(setup)

        try:
            repository = SaveRepository.create_new_save(
                self.app_paths.saves_dir,
                clean_setup["title"],
                setup=clean_setup,
            )
        except Exception:
            LOGGER.exception("Failed to create new game.")
            QMessageBox.critical(self, "New Game Failed", "Could not create a new game.")
            return

        save_new_game_template(self.app_paths.new_game_templates_path, clean_setup)
        self._synthesize_new_game_world(repository, clean_setup)
        self.open_repository(repository)
        self.game_shell.story_screen.narrate_latest_story()

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
        repository.set_world_summary(result.world_summary)
        repository.set_world_lore(result.world_lore)
        repository.append_history("story", result.introductory_message)

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

    def _apply_new_game_ai_state(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
        result,
    ) -> None:
        """Persists AI-finalized new-game character, skills, and start location."""

        if result.start_location:
            repository.set_state_value("location", result.start_location)

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

        character = result.finalized_character

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
            repository.replace_skills(result.finalized_skills)
        elif result.finalized_skills:
            LOGGER.warning(
                "Skipped AI-finalized skills because they did not match the starting skill plan."
            )

        minimum_item_count = max(5, len(setup.get("starter_items", [])))

        if len(result.finalized_starter_items) >= minimum_item_count:
            repository.replace_inventory_items(result.finalized_starter_items)
        elif result.finalized_starter_items:
            LOGGER.warning(
                "Skipped AI-finalized starter inventory because it had fewer than %s items.",
                minimum_item_count,
            )

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
        self.stack.setCurrentWidget(self.game_shell)

        title = repository.get_meta("title", default="AI Adventure")
        self.setWindowTitle(f"AI Adventure - {title}")

        LOGGER.info("Opened save: %s", repository.db_path)

    def return_to_menu(self) -> None:
        """Returns to the Main Menu."""

        self.active_repository = None
        self.game_shell.set_repository(None)
        self.main_menu.refresh_saves()
        self.stack.setCurrentWidget(self.main_menu)
        self.setWindowTitle("AI Adventure")


class MainMenuScreen(QWidget):
    """Main Menu with New Game and Load Game actions."""

    def __init__(
        self,
        saves_dir: Path,
        on_new_game,
        on_load_game,
    ) -> None:
        """
        Args:
            saves_dir: Directory containing save folders.
            on_new_game: Callback for creating a new game.
            on_load_game: Callback for loading a save by database path.
        """

        super().__init__()

        self.saves_dir = saves_dir
        self.on_new_game = on_new_game
        self.on_load_game = on_load_game

        title_label = QLabel("AI Adventure")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 32px; font-weight: bold;")

        new_game_button = QPushButton("New Game")
        new_game_button.clicked.connect(self._handle_new_game)

        self.save_combo = QComboBox()

        load_button = QPushButton("Load Game")
        load_button.clicked.connect(self._handle_load_game)

        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(title_label)
        layout.addSpacing(30)

        layout.addWidget(new_game_button)

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


class NewGameWizard(QWizard):
    """Multi-step new-game setup flow."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        template_setup: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("New Game Wizard")
        self.resize(780, 620)
        self._apply_theme()

        self._build_adventure_page()
        self._build_character_page()
        self._build_skills_page()
        self._build_inventory_currency_page()
        self._build_calendar_page()

        if template_setup is not None:
            self.load_setup(template_setup)

    def _apply_theme(self) -> None:
        """Applies a cohesive local theme to the new-game wizard."""

        self.setObjectName("newGameWizard")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#20242b"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#f3f4f6"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#11151b"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#242a33"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#f3f4f6"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#96a0ad"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#2d3642"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f3f4f6"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f7dd3"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        self.setPalette(palette)

        self.setStyleSheet(
            """
            QWizard#newGameWizard {
                background-color: #20242b;
                color: #f3f4f6;
            }

            QWizard#newGameWizard QWizardPage {
                background-color: #20242b;
                color: #f3f4f6;
            }

            QWizard#newGameWizard QLabel {
                color: #f3f4f6;
                font-size: 13px;
            }

            QWizard#newGameWizard QLineEdit,
            QWizard#newGameWizard QTextEdit,
            QWizard#newGameWizard QComboBox,
            QWizard#newGameWizard QSpinBox {
                background-color: #11151b;
                border: 1px solid #3a4250;
                border-radius: 5px;
                color: #f3f4f6;
                padding: 6px;
                selection-background-color: #2f7dd3;
                selection-color: #ffffff;
            }

            QWizard#newGameWizard QTextEdit {
                padding: 8px;
            }

            QWizard#newGameWizard QLineEdit:focus,
            QWizard#newGameWizard QTextEdit:focus,
            QWizard#newGameWizard QComboBox:focus,
            QWizard#newGameWizard QSpinBox:focus {
                border-color: #6aa6ff;
            }

            QWizard#newGameWizard QComboBox::drop-down,
            QWizard#newGameWizard QSpinBox::up-button,
            QWizard#newGameWizard QSpinBox::down-button {
                background-color: #242a33;
                border: 0;
                width: 22px;
            }

            QWizard#newGameWizard QTableWidget {
                background-color: #11151b;
                alternate-background-color: #171c24;
                border: 1px solid #3a4250;
                border-radius: 5px;
                color: #f3f4f6;
                gridline-color: #303845;
                selection-background-color: #2f7dd3;
                selection-color: #ffffff;
            }

            QWizard#newGameWizard QHeaderView::section {
                background-color: #242a33;
                border: 0;
                border-right: 1px solid #303845;
                color: #dbe3ee;
                font-weight: 600;
                padding: 7px;
            }

            QWizard#newGameWizard QPushButton {
                background-color: #2d3642;
                border: 1px solid #465163;
                border-radius: 5px;
                color: #f3f4f6;
                min-width: 76px;
                padding: 6px 14px;
            }

            QWizard#newGameWizard QPushButton:hover {
                background-color: #374353;
                border-color: #5b6a80;
            }

            QWizard#newGameWizard QPushButton:pressed {
                background-color: #25303c;
            }

            QWizard#newGameWizard QPushButton:default {
                background-color: #2f7dd3;
                border-color: #6aa6ff;
                color: #ffffff;
            }

            QWizard#newGameWizard QPushButton:disabled {
                background-color: #252a32;
                border-color: #303640;
                color: #737d8c;
            }
            """
        )

    def build_setup(self) -> dict[str, Any]:
        """Builds a normalized setup dictionary from wizard fields."""

        calendar_type = self.calendar_type_combo.currentData() or "gregorian"
        calendar_settings: dict[str, Any] = {
            "calendar_type": calendar_type,
            "time_display": self.time_format_combo.currentData() or "12_hour",
        }

        if calendar_type == "custom":
            calendar_settings.update(
                {
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
                }
            )

        skills = [
            {
                "name": skill_input.text(),
                "description": "",
                "level": level,
                "requires_ai_invention": not skill_input.text().strip(),
            }
            for level, skill_input in self.skill_inputs
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
            "starter_items": parse_starter_items_text(
                self.starter_items_input.toPlainText()
            ),
            "calendar": calendar_settings,
            "currency_denominations": self._currency_denominations_from_table(),
            "currency_description": self.currency_description_input.toPlainText(),
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

        self.title_input.setText(clean_setup["title"])
        self.genre_input.setText(clean_setup["specified_genre"])
        self.game_style_input.setPlainText(clean_setup["game_style"])
        self.start_location_input.setText(clean_setup["start_location"])
        self.world_context_input.setPlainText(clean_setup["world_context"])

        self.character_name_input.setText(character["name"])
        self.appearance_input.setPlainText(character["appearance"])
        self.backstory_input.setPlainText(character["backstory"])
        self.character_notes_input.setPlainText(character["notes"])

        for index, (_, skill_input) in enumerate(self.skill_inputs):
            skill = clean_setup["skills"][index] if index < len(clean_setup["skills"]) else {}
            skill_input.setText(str(skill.get("name", "")))

        self.starter_items_input.setPlainText(
            _format_starter_items_for_template(clean_setup["starter_items"])
        )
        self.currency_table.setRowCount(0)

        for denomination in clean_setup["currency_denominations"]:
            self._append_currency_row(denomination)

        self.currency_description_input.setPlainText(clean_setup["currency_description"])

        _set_combo_to_data(
            self.calendar_type_combo,
            _calendar_type_from_settings(calendar),
        )
        _set_combo_to_data(
            self.time_format_combo,
            str(calendar.get("time_display", "12_hour")),
        )
        self.days_per_week_input.setValue(int(calendar["days_per_week"]))
        self.weeks_per_month_input.setValue(int(calendar["weeks_per_month"]))
        self.months_per_year_input.setValue(int(calendar["months_per_year"]))
        self.seasons_per_year_input.setValue(int(calendar["seasons_per_year"]))
        self.day_names_input.setText(", ".join(str(name) for name in calendar["day_names"]))
        self.month_names_input.setText(", ".join(str(name) for name in calendar["month_names"]))
        self.season_names_input.setText(
            ", ".join(str(season["name"]) for season in calendar["seasons"])
        )
        self.season_hints_input.setText(
            ", ".join(str(season["weather_hint"]) for season in calendar["seasons"])
        )

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

        self.world_context_input = QTextEdit()
        self.world_context_input.setPlaceholderText(
            "Named locations, factions, guilds, religions, political tensions, tone, themes..."
        )

        layout = QFormLayout()
        layout.addRow("Game Name:", self.title_input)
        layout.addRow("Genre:", self.genre_input)
        layout.addRow("Game Style:", self.game_style_input)
        layout.addRow("Starting Location:", self.start_location_input)
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
        page.setSubTitle(
            "Choose one level 5 skill, two level 4 skills, three level 3 skills, "
            "four level 2 skills, and five level 1 skills."
        )

        self.skill_inputs: list[tuple[int, QLineEdit]] = []
        layout = QFormLayout()
        level_counts: dict[int, int] = {}

        for level in SKILL_LEVEL_PLAN:
            level_counts[level] = level_counts.get(level, 0) + 1
            skill_input = QLineEdit()
            skill_input.setPlaceholderText(f"Level {level} skill")
            self.skill_inputs.append((level, skill_input))
            layout.addRow(f"Level {level} Skill {level_counts[level]}:", skill_input)

        page.setLayout(layout)
        self.addPage(page)

    def _build_inventory_currency_page(self) -> None:
        """Builds the starter inventory and currency page."""

        page = QWizardPage()
        page.setTitle("Inventory and Currency")
        page.setSubTitle("Add requested starter items and describe the world's money.")

        self.starter_items_input = QTextEdit()
        self.starter_items_input.setPlaceholderText(
            "One item per line. Example: Lantern | Tool | 2 | Hooded brass lantern | 15"
        )

        self.currency_table = QTableWidget(0, 3)
        self.currency_table.setHorizontalHeaderLabels(["Name", "Plural Name", "Base Value"])
        self.currency_table.setMinimumHeight(180)
        self.currency_table.verticalHeader().setVisible(False)
        self.currency_table.horizontalHeader().setStretchLastSection(True)
        self.currency_table.setAlternatingRowColors(True)

        add_currency_button = QPushButton("Add Currency")
        add_currency_button.clicked.connect(lambda: self._append_currency_row({}))

        self.currency_description_input = QTextEdit()
        self.currency_description_input.setPlaceholderText(
            "Optional economy notes. Leave currencies blank for AI-generated money."
        )

        layout = QFormLayout()
        layout.addRow("Starter Items:", self.starter_items_input)
        layout.addRow("Currencies:", self.currency_table)
        layout.addRow("", add_currency_button)
        layout.addRow("Economy Notes:", self.currency_description_input)
        page.setLayout(layout)

        self.addPage(page)

    def _append_currency_row(self, denomination: dict[str, Any]) -> None:
        """Adds a currency denomination row to the wizard table."""

        row = self.currency_table.rowCount()
        self.currency_table.insertRow(row)

        name = str(denomination.get("name", ""))
        plural_name = str(denomination.get("plural_name", ""))
        value = _safe_int(denomination.get("value", 1), 1)
        value_input = QSpinBox()
        value_input.setMinimum(1)
        value_input.setMaximum(1_000_000_000)
        value_input.setValue(value)
        value_input.setEnabled(row != 0)

        self.currency_table.setItem(row, 0, QTableWidgetItem(name))
        self.currency_table.setItem(row, 1, QTableWidgetItem(plural_name))
        self.currency_table.setCellWidget(row, 2, value_input)

    def _currency_denominations_from_table(self) -> list[dict[str, Any]]:
        """Reads currency denomination rows from the wizard table."""

        denominations: list[dict[str, Any]] = []

        for row in range(self.currency_table.rowCount()):
            name_item = self.currency_table.item(row, 0)
            plural_item = self.currency_table.item(row, 1)
            value_widget = self.currency_table.cellWidget(row, 2)

            denominations.append(
                {
                    "name": name_item.text() if name_item is not None else "",
                    "plural_name": plural_item.text() if plural_item is not None else "",
                    "value": (
                        value_widget.value()
                        if isinstance(value_widget, QSpinBox)
                        else 1
                    ),
                }
            )

        return denominations

    def _build_calendar_page(self) -> None:
        """Builds the calendar and time page."""

        page = QWizardPage()
        page.setTitle("Calendar and Time")
        page.setSubTitle("Choose the calendar and displayed time format.")

        self.calendar_type_combo = QComboBox()
        self.calendar_type_combo.addItem("Default Gregorian Calendar", "gregorian")
        self.calendar_type_combo.addItem("Custom Calendar", "custom")

        self.time_format_combo = QComboBox()
        self.time_format_combo.addItem("12-hour A.M./P.M.", "12_hour")
        self.time_format_combo.addItem("24-hour", "24_hour")
        self.time_format_combo.addItem("Narrative", "narrative")

        self.days_per_week_input = QSpinBox()
        self.days_per_week_input.setRange(1, 14)
        self.days_per_week_input.setValue(int(GREGORIAN_CALENDAR_SETTINGS["days_per_week"]))

        self.weeks_per_month_input = QSpinBox()
        self.weeks_per_month_input.setRange(1, 12)
        self.weeks_per_month_input.setValue(int(GREGORIAN_CALENDAR_SETTINGS["weeks_per_month"]))

        self.months_per_year_input = QSpinBox()
        self.months_per_year_input.setRange(1, 24)
        self.months_per_year_input.setValue(int(GREGORIAN_CALENDAR_SETTINGS["months_per_year"]))

        self.seasons_per_year_input = QSpinBox()
        self.seasons_per_year_input.setRange(1, 12)
        self.seasons_per_year_input.setValue(int(GREGORIAN_CALENDAR_SETTINGS["seasons_per_year"]))

        self.day_names_input = QLineEdit()
        self.day_names_input.setText(", ".join(GREGORIAN_CALENDAR_SETTINGS["day_names"]))

        self.month_names_input = QLineEdit()
        self.month_names_input.setText(", ".join(GREGORIAN_CALENDAR_SETTINGS["month_names"]))

        self.season_names_input = QLineEdit()
        self.season_names_input.setText(
            ", ".join(season["name"] for season in GREGORIAN_CALENDAR_SETTINGS["seasons"])
        )

        self.season_hints_input = QLineEdit()
        self.season_hints_input.setText(
            ", ".join(
                season["weather_hint"] for season in GREGORIAN_CALENDAR_SETTINGS["seasons"]
            )
        )

        layout = QFormLayout()
        layout.addRow("Calendar:", self.calendar_type_combo)
        layout.addRow("Time Format:", self.time_format_combo)
        layout.addRow("Days Per Week:", self.days_per_week_input)
        layout.addRow("Weeks Per Month:", self.weeks_per_month_input)
        layout.addRow("Months Per Year:", self.months_per_year_input)
        layout.addRow("Seasons Per Year:", self.seasons_per_year_input)
        layout.addRow("Day Names:", self.day_names_input)
        layout.addRow("Month Names:", self.month_names_input)
        layout.addRow("Season Names:", self.season_names_input)
        layout.addRow("Season Weather Hints:", self.season_hints_input)
        page.setLayout(layout)

        self.addPage(page)


class GameShell(QWidget):
    """In-game shell containing the core play screens."""

    def __init__(
        self,
        on_return_to_menu,
        *,
        sound_manager: SoundManager | None = None,
        narration_player: NarrationPlayer | None = None,
    ) -> None:
        """
        Args:
            on_return_to_menu: Callback for returning to the Main Menu.
        """

        super().__init__()

        self.on_return_to_menu = on_return_to_menu
        self.sound_manager = sound_manager
        self.narration_player = narration_player
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
        self.npcs_screen = NpcsScreen()
        self.active_tasks_screen = ActiveTasksScreen()
        self.skills_screen = SkillsScreen()
        self.alchemy_screen = AlchemyNotebookScreen()
        self.history_screen = HistoryScreen()
        self.settings_screen = SettingsScreen(on_audio_settings_changed=self._apply_audio_settings)

        self.screens: list[RepositoryBackedWidget] = [
            self.story_screen,
            self.character_screen,
            self.world_screen,
            self.calendar_screen,
            self.inventory_screen,
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
        self.tabs.addTab(self.npcs_screen, "NPCs")
        self.tabs.addTab(self.active_tasks_screen, "Active Tasks")
        self.tabs.addTab(self.skills_screen, "Skills")
        self.tabs.addTab(self.alchemy_screen, "Alchemy Notebook")
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
        self.player_input.setPlaceholderText("Enter a player action...")

        submit_button = QPushButton("Submit")
        submit_button.clicked.connect(self._submit_player_action)
        self.player_input.returnPressed.connect(self._submit_player_action)

        input_row = QHBoxLayout()
        input_row.addWidget(self.player_input)
        input_row.addWidget(submit_button)

        layout = QVBoxLayout()
        layout.addLayout(status_row)
        layout.addWidget(self.story_output)
        layout.addLayout(input_row)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Refreshes the story output from history."""

        repository = self.repository()

        if repository is None:
            self.story_output.clear()
            self.location_value.setText("-")
            self.day_value.setText("-")
            self.time_value.setText("-")
            self.weather_value.setText("-")
            return

        state = StateManager(repository).load_state()
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
                story_lines.append(f"> {content}")
            elif kind == "story":
                entry_id = _safe_int(entry.get("id"), -1)

                if entry_id == self._revealing_story_id:
                    if self._revealed_story_chunks:
                        story_lines.append("\n\n".join(self._revealed_story_chunks))
                else:
                    story_lines.append(format_story_message(content))
        #"\n\n".join(story_lines).join("\n\n")
        output = "\n\n".join(story_lines).join("\n\n")
        #output += "\n============================================================================\n"
        self.story_output.setPlainText(output)
        self.story_output.moveCursor(self.story_output.textCursor().MoveOperation.End)

    def _submit_player_action(self) -> None:
        """Records a player action and requests AI narration when configured."""

        repository = self.repository()

        if repository is None:
            return

        player_text = self.player_input.text().strip()

        if not player_text:
            LOGGER.warning("Skipped blank player action.")
            return

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
        context_packet = AiContextBuilder.from_default_library().build_story_context(
            state,
            player_command=player_text,
            relevant_npcs=relevant_npcs,
            valid_music_tracks=valid_music_tracks,
            current_music=str(repository.get_setting("audio.current_music", "")),
        )

        repository.append_history("player", player_text)

        try:
            result = GeminiNarrationService().generate_story_response(context_packet)
        except GeminiConfigurationError as error:
            LOGGER.warning("Gemini narration skipped: %s", error)
            repository.append_history(
                "story",
                (
                    "No Gemini API key is configured yet. "
                    "This action was recorded successfully."
                ),
            )
        except Exception:
            LOGGER.exception("Gemini narration request failed.")
            repository.append_history(
                "story",
                "The narration falters for a moment. Check the application log for details.",
            )
        else:
            repository.append_history("story", result.narrative_text)

            if result.suggested_events:
                event_results = EventApplier(repository).apply_events(result.suggested_events)
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

        self.player_input.clear()

        if "result" in locals():
            latest_story = self._latest_story_entry()
            if latest_story is not None and self._reveal_story_with_narration(
                int(latest_story["id"]),
                result.narrative_text,
            ):
                return

        self.refresh()

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
                if not self._reveal_story_with_narration(
                    int(entry.get("id", -1)),
                    str(entry.get("content", "")),
                ):
                    self.refresh()
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
        self.refresh()

        if self._narrate_text(text, story_id=story_id):
            return True

        self._revealing_story_id = None
        self._revealed_story_chunks = []
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

        self._revealing_story_id = None
        self._revealed_story_chunks = []
        self.refresh()

    def _latest_story_entry(self) -> dict[str, Any] | None:
        """Returns the most recent saved story entry."""

        repository = self.repository()

        if repository is None:
            return None

        for entry in reversed(repository.list_history()):
            if str(entry.get("kind", "")).casefold() == "story":
                return entry

        return None


class CharacterScreen(RepositoryBackedWidget):
    """Editable player character profile."""

    def __init__(self) -> None:
        super().__init__()

        self.name_input = QLineEdit()
        self.appearance_input = QTextEdit()
        self.backstory_input = QTextEdit()
        self.notes_input = QTextEdit()

        self.appearance_input.setPlaceholderText("Visible traits, clothing, manner, scars, voice...")
        self.backstory_input.setPlaceholderText("Origin, important history, relationships, goals...")
        self.notes_input.setPlaceholderText("Player notes about this character...")

        save_button = QPushButton("Save Character")
        save_button.clicked.connect(self._save_character)

        layout = QFormLayout()
        layout.addRow("Name:", self.name_input)
        layout.addRow("Appearance:", self.appearance_input)
        layout.addRow("Backstory:", self.backstory_input)
        layout.addRow("Notes:", self.notes_input)
        layout.addRow(save_button)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads the character profile."""

        repository = self.repository()

        if repository is None:
            self.name_input.clear()
            self.appearance_input.clear()
            self.backstory_input.clear()
            self.notes_input.clear()
            return

        state = StateManager(repository).load_state()
        self.name_input.setText(state.player.name)
        self.appearance_input.setPlainText(state.player.appearance)
        self.backstory_input.setPlainText(state.player.backstory)
        self.notes_input.setPlainText(state.player.notes)

    def _save_character(self) -> None:
        """Persists the editable character profile."""

        repository = self.repository()

        if repository is None:
            return

        repository.set_setting("player_name", self.name_input.text().strip())
        repository.set_setting("player.appearance", self.appearance_input.toPlainText().strip())
        repository.set_setting("player.backstory", self.backstory_input.toPlainText().strip())
        repository.set_setting("player.notes", self.notes_input.toPlainText().strip())
        repository.append_history("system", "Character profile updated.")
        self.notify_repository_changed()

        QMessageBox.information(self, "Character Saved", "Character profile was saved.")


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
            sections.append(f"World Overview\n\n{summary}")

        lore = repository.get_world_lore()

        for category in sorted(lore):
            entries = lore[category]

            if not entries:
                continue

            body = "\n".join(
                f"- {key}: {text}"
                for key, text in sorted(entries.items())
            )
            sections.append(f"{category}\n\n{body}")

        if not sections:
            sections.append("No world information has been recorded yet.")

        self.world_output.setPlainText("\n\n".join(sections))


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

        navigation_row = QHBoxLayout()
        navigation_row.addWidget(previous_button)
        navigation_row.addStretch()
        navigation_row.addWidget(self.month_label)
        navigation_row.addStretch()
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
            return

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
    """Alchemy notebook screen for reagents and recipes."""

    def __init__(self) -> None:
        super().__init__()

        self.tabs = QTabWidget()
        self._reagent_rows: list[dict[str, Any]] = []
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
        """Reloads all alchemy notebook data."""

        repository = self.repository()

        if repository is None:
            self.reagent_table.setRowCount(0)
            self.recipe_table.setRowCount(0)
            return

        self._refresh_reagents(repository)
        self._refresh_recipes(repository)

    def _setup_reagents_tab(self) -> None:
        """Builds the structured reagent discovery tab."""

        self.reagent_table = QTableWidget(0, 6)
        self.reagent_table.setHorizontalHeaderLabels(
            ["Name", "Qualities", "Motions", "Virtues", "Uses", "Notes"]
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
        self.reagent_name_input.setPlaceholderText("Reagent name")
        self.reagent_qualities_input = QLineEdit()
        self.reagent_qualities_input.setPlaceholderText("Comma-separated qualities")
        self.reagent_motions_input = QLineEdit()
        self.reagent_motions_input.setPlaceholderText("Comma-separated motions")
        self.reagent_virtues_input = QLineEdit()
        self.reagent_virtues_input.setPlaceholderText("Comma-separated virtues")
        self.reagent_uses_input = QLineEdit()
        self.reagent_uses_input.setPlaceholderText("Comma-separated uses")
        self.reagent_notes_input = QTextEdit()
        self.reagent_notes_input.setPlaceholderText("Reagent notes")

        save_button = QPushButton("Save Reagent")
        save_button.clicked.connect(self._save_reagent)
        new_button = QPushButton("New Reagent")
        new_button.clicked.connect(self._clear_reagent_form)

        button_row = QHBoxLayout()
        button_row.addWidget(save_button)
        button_row.addWidget(new_button)
        button_row.addStretch()

        form = QFormLayout()
        form.addRow("Name:", self.reagent_name_input)
        form.addRow("Qualities:", self.reagent_qualities_input)
        form.addRow("Motions:", self.reagent_motions_input)
        form.addRow("Virtues:", self.reagent_virtues_input)
        form.addRow("Uses:", self.reagent_uses_input)
        form.addRow("Notes:", self.reagent_notes_input)
        form.addRow(button_row)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.reagent_table)

        wrapper = QWidget()
        wrapper.setLayout(layout)
        self.tabs.addTab(wrapper, "Reagents")

    def _setup_recipes_tab(self) -> None:
        """Builds the structured recipe discovery tab."""

        self.recipe_table = QTableWidget(0, 6)
        self.recipe_table.setHorizontalHeaderLabels(
            ["Name", "Ingredients", "Result", "Motions", "Virtues", "Notes"]
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
        self.recipe_ingredients_input = QLineEdit()
        self.recipe_ingredients_input.setPlaceholderText("Comma-separated ingredients")
        self.recipe_result_input = QLineEdit()
        self.recipe_result_input.setPlaceholderText("Recipe result")
        self.recipe_motions_input = QLineEdit()
        self.recipe_motions_input.setPlaceholderText("Comma-separated motions")
        self.recipe_virtues_input = QLineEdit()
        self.recipe_virtues_input.setPlaceholderText("Comma-separated virtues")
        self.recipe_notes_input = QTextEdit()
        self.recipe_notes_input.setPlaceholderText("Recipe notes")

        add_button = QPushButton("Save Recipe")
        add_button.clicked.connect(self._add_recipe)

        form = QFormLayout()
        form.addRow("Name:", self.recipe_name_input)
        form.addRow("Ingredients:", self.recipe_ingredients_input)
        form.addRow("Result:", self.recipe_result_input)
        form.addRow("Motions:", self.recipe_motions_input)
        form.addRow("Virtues:", self.recipe_virtues_input)
        form.addRow("Notes:", self.recipe_notes_input)
        form.addRow(add_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.recipe_table)

        wrapper = QWidget()
        wrapper.setLayout(layout)
        self.tabs.addTab(wrapper, "Recipes")

    def _refresh_reagents(self, repository: SaveRepository) -> None:
        """Reloads the reagent table."""

        reagents = repository.list_alchemy_reagents()
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
            self.reagent_table.setItem(row_index, 1, _table_item(_join_list(reagent.get("qualities", []))))
            self.reagent_table.setItem(row_index, 2, _table_item(_join_list(reagent.get("motions", []))))
            self.reagent_table.setItem(row_index, 3, _table_item(_join_list(reagent.get("virtues", []))))
            self.reagent_table.setItem(row_index, 4, _table_item(_join_list(reagent.get("uses", []))))
            self.reagent_table.setItem(row_index, 5, _table_item(str(reagent.get("notes", ""))))

        self.reagent_table.resizeColumnsToContents()
        self._refreshing_reagents = False

        if selected_name:
            for row_index, reagent in enumerate(reagents):
                if str(reagent.get("name", "")).casefold() == selected_name.casefold():
                    self.reagent_table.selectRow(row_index)
                    break

    def _refresh_recipes(self, repository: SaveRepository) -> None:
        """Reloads the recipe table."""

        recipes = repository.list_alchemy_recipes()
        recipes.sort(
            key=self._recipe_sort_key,
            reverse=_sort_descending(self._recipe_sort_order),
        )
        self.recipe_table.setRowCount(len(recipes))

        for row_index, recipe in enumerate(recipes):
            self.recipe_table.setItem(row_index, 0, _table_item(str(recipe.get("name", ""))))
            self.recipe_table.setItem(row_index, 1, _table_item(_join_list(recipe.get("ingredients", []))))
            self.recipe_table.setItem(row_index, 2, _table_item(str(recipe.get("result", ""))))
            self.recipe_table.setItem(row_index, 3, _table_item(_join_list(recipe.get("motions", []))))
            self.recipe_table.setItem(row_index, 4, _table_item(_join_list(recipe.get("virtues", []))))
            self.recipe_table.setItem(row_index, 5, _table_item(str(recipe.get("notes", ""))))

        self.recipe_table.resizeColumnsToContents()

    def _save_reagent(self) -> None:
        """Adds or updates a known reagent."""

        repository = self.repository()

        if repository is None:
            return

        name = self.reagent_name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Name", "Reagent name is required.")
            return

        repository.add_alchemy_reagent(
            name=name,
            qualities=_split_list(self.reagent_qualities_input.text()),
            motions=_split_list(self.reagent_motions_input.text()),
            virtues=_split_list(self.reagent_virtues_input.text()),
            uses=_split_list(self.reagent_uses_input.text()),
            notes=self.reagent_notes_input.toPlainText(),
        )

        self.reagent_name_input.clear()
        self.reagent_qualities_input.clear()
        self.reagent_motions_input.clear()
        self.reagent_virtues_input.clear()
        self.reagent_uses_input.clear()
        self.reagent_notes_input.clear()

        self.refresh()
        self.notify_repository_changed()

    def _load_selected_reagent(self) -> None:
        """Loads the selected reagent row into the edit controls."""

        if self._refreshing_reagents:
            return

        if not self.reagent_table.selectedItems():
            return

        row_index = self.reagent_table.currentRow()

        if row_index < 0 or row_index >= len(self._reagent_rows):
            return

        reagent = self._reagent_rows[row_index]
        self.reagent_name_input.setText(str(reagent.get("name", "")))
        self.reagent_qualities_input.setText(_join_list(reagent.get("qualities", [])))
        self.reagent_motions_input.setText(_join_list(reagent.get("motions", [])))
        self.reagent_virtues_input.setText(_join_list(reagent.get("virtues", [])))
        self.reagent_uses_input.setText(_join_list(reagent.get("uses", [])))
        self.reagent_notes_input.setPlainText(str(reagent.get("notes", "")))

    def _clear_reagent_form(self) -> None:
        """Clears reagent edit controls and table selection."""

        self.reagent_table.clearSelection()
        self.reagent_name_input.clear()
        self.reagent_qualities_input.clear()
        self.reagent_motions_input.clear()
        self.reagent_virtues_input.clear()
        self.reagent_uses_input.clear()
        self.reagent_notes_input.clear()

    def _add_recipe(self) -> None:
        """Adds or updates a known recipe."""

        repository = self.repository()

        if repository is None:
            return

        name = self.recipe_name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Name", "Recipe name is required.")
            return

        repository.add_alchemy_recipe(
            name=name,
            ingredients=_split_list(self.recipe_ingredients_input.text()),
            result=self.recipe_result_input.text(),
            motions=_split_list(self.recipe_motions_input.text()),
            virtues=_split_list(self.recipe_virtues_input.text()),
            notes=self.recipe_notes_input.toPlainText(),
        )

        self.recipe_name_input.clear()
        self.recipe_ingredients_input.clear()
        self.recipe_result_input.clear()
        self.recipe_motions_input.clear()
        self.recipe_virtues_input.clear()
        self.recipe_notes_input.clear()

        self.refresh()
        self.notify_repository_changed()

    def _sort_reagents_by_column(self, column_index: int) -> None:
        """Sorts reagents by a clicked header column."""

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
        """Returns the active reagent sort key."""

        name = str(reagent.get("name", "")).casefold()

        if self._reagent_sort_column == 1:
            return _join_list(reagent.get("qualities", [])).casefold(), name

        if self._reagent_sort_column == 2:
            return _join_list(reagent.get("motions", [])).casefold(), name

        if self._reagent_sort_column == 3:
            return _join_list(reagent.get("virtues", [])).casefold(), name

        if self._reagent_sort_column == 4:
            return _join_list(reagent.get("uses", [])).casefold(), name

        if self._reagent_sort_column == 5:
            return str(reagent.get("notes", "")).casefold(), name

        return name, name

    def _recipe_sort_key(self, recipe: dict[str, Any]) -> tuple[str, str]:
        """Returns the active recipe sort key."""

        name = str(recipe.get("name", "")).casefold()

        if self._recipe_sort_column == 1:
            return _join_list(recipe.get("ingredients", [])).casefold(), name

        if self._recipe_sort_column == 2:
            return str(recipe.get("result", "")).casefold(), name

        if self._recipe_sort_column == 3:
            return _join_list(recipe.get("motions", [])).casefold(), name

        if self._recipe_sort_column == 4:
            return _join_list(recipe.get("virtues", [])).casefold(), name

        if self._recipe_sort_column == 5:
            return str(recipe.get("notes", "")).casefold(), name

        return name, name

class HistoryScreen(RepositoryBackedWidget):
    """Private player journal that is never sent to the AI."""

    def __init__(self) -> None:
        super().__init__()

        self.journal_input = QTextEdit()
        self.journal_input.setPlaceholderText("Write private notes here. These are not sent to the AI.")

        save_button = QPushButton("Save Journal")
        save_button.clicked.connect(self._save_journal)

        layout = QVBoxLayout()
        layout.addWidget(self.journal_input)
        layout.addWidget(save_button)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads private journal notes."""

        repository = self.repository()

        if repository is None:
            self.journal_input.clear()
            return

        self.journal_input.setPlainText(repository.get_journal_notes())

    def _save_journal(self) -> None:
        """Persists private journal notes without touching AI context."""

        repository = self.repository()

        if repository is None:
            return

        repository.set_journal_notes(self.journal_input.toPlainText())
        self.notify_repository_changed()
        QMessageBox.information(self, "Journal Saved", "Journal notes were saved.")


class SettingsScreen(RepositoryBackedWidget):
    """Basic save-specific settings screen."""

    def __init__(self, on_audio_settings_changed=None) -> None:
        super().__init__()

        self.on_audio_settings_changed = on_audio_settings_changed
        self._loading_settings = False
        self._saving_settings = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(400)
        self._autosave_timer.timeout.connect(self._save_settings)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["System", "Light", "Dark"])
        self.theme_combo.currentIndexChanged.connect(lambda _index: self._save_settings())

        self.music_enabled_checkbox = QCheckBox("Music enabled")
        self.music_enabled_checkbox.setChecked(True)
        self.music_enabled_checkbox.toggled.connect(lambda _checked: self._save_settings())

        self.narrator_enabled_checkbox = QCheckBox("Narrator enabled")
        self.narrator_enabled_checkbox.setChecked(True)
        self.narrator_enabled_checkbox.toggled.connect(lambda _checked: self._save_settings())

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(25)
        self.music_volume_label = QLabel("25%")
        self.music_volume_slider.valueChanged.connect(
            lambda value: self.music_volume_label.setText(f"{value}%")
        )
        self.music_volume_slider.sliderReleased.connect(self._save_settings)

        self.tts_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.tts_volume_slider.setRange(0, 100)
        self.tts_volume_slider.setValue(90)
        self.tts_volume_label = QLabel("90%")
        self.tts_volume_slider.valueChanged.connect(
            lambda value: self.tts_volume_label.setText(f"{value}%")
        )
        self.tts_volume_slider.sliderReleased.connect(self._save_settings)

        self.days_per_week_input = QSpinBox()
        self.days_per_week_input.setRange(1, 14)
        self.days_per_week_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["days_per_week"]))
        self.days_per_week_input.valueChanged.connect(lambda _value: self._save_settings())

        self.weeks_per_month_input = QSpinBox()
        self.weeks_per_month_input.setRange(1, 12)
        self.weeks_per_month_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["weeks_per_month"]))
        self.weeks_per_month_input.valueChanged.connect(lambda _value: self._save_settings())

        self.months_per_year_input = QSpinBox()
        self.months_per_year_input.setRange(1, 24)
        self.months_per_year_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["months_per_year"]))
        self.months_per_year_input.valueChanged.connect(lambda _value: self._save_settings())

        self.seasons_per_year_input = QSpinBox()
        self.seasons_per_year_input.setRange(1, 12)
        self.seasons_per_year_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["seasons_per_year"]))
        self.seasons_per_year_input.valueChanged.connect(lambda _value: self._save_settings())

        self.day_names_input = QLineEdit()
        self.day_names_input.editingFinished.connect(self._save_settings)
        self.day_names_input.textChanged.connect(lambda _text: self._schedule_settings_save())
        self.month_names_input = QLineEdit()
        self.month_names_input.editingFinished.connect(self._save_settings)
        self.month_names_input.textChanged.connect(lambda _text: self._schedule_settings_save())
        self.season_names_input = QLineEdit()
        self.season_names_input.editingFinished.connect(self._save_settings)
        self.season_names_input.textChanged.connect(lambda _text: self._schedule_settings_save())
        self.season_hints_input = QLineEdit()
        self.season_hints_input.editingFinished.connect(self._save_settings)
        self.season_hints_input.textChanged.connect(lambda _text: self._schedule_settings_save())

        self.additional_ai_context_input = QTextEdit()
        self.additional_ai_context_input.setPlaceholderText(
            "Optional AI-facing guidance, style preferences, boundaries, or reminders..."
        )
        self.additional_ai_context_input.textChanged.connect(self._schedule_settings_save)

        self.time_display_combo = QComboBox()
        self.time_display_combo.addItem("Narrative", "narrative")
        self.time_display_combo.addItem("12-hour", "12_hour")
        self.time_display_combo.addItem("24-hour", "24_hour")
        self.time_display_combo.currentIndexChanged.connect(lambda _index: self._save_settings())

        self.currency_name_inputs: list[QLineEdit] = []
        self.currency_plural_inputs: list[QLineEdit] = []
        self.currency_value_inputs: list[QSpinBox] = []

        layout = QFormLayout()
        layout.addRow("Theme Preference:", self.theme_combo)
        layout.addRow("Background Music:", self.music_enabled_checkbox)
        layout.addRow("Music Volume:", _slider_row(self.music_volume_slider, self.music_volume_label))
        layout.addRow("Narrator:", self.narrator_enabled_checkbox)
        layout.addRow("Narrator Volume:", _slider_row(self.tts_volume_slider, self.tts_volume_label))
        layout.addRow("Days Per Week:", self.days_per_week_input)
        layout.addRow("Weeks Per Month:", self.weeks_per_month_input)
        layout.addRow("Months Per Year:", self.months_per_year_input)
        layout.addRow("Seasons Per Year:", self.seasons_per_year_input)
        layout.addRow("Day Names:", self.day_names_input)
        layout.addRow("Month Names:", self.month_names_input)
        layout.addRow("Season Names:", self.season_names_input)
        layout.addRow("Season Weather Hints:", self.season_hints_input)
        layout.addRow("Time Display:", self.time_display_combo)
        layout.addRow("Additional AI Context:", self.additional_ai_context_input)

        for index, denomination in enumerate(DEFAULT_CURRENCY_DENOMINATIONS):
            name_input = QLineEdit()
            plural_input = QLineEdit()
            value_input = QSpinBox()
            value_input.setMinimum(1)
            value_input.setMaximum(1_000_000_000)
            value_input.setValue(int(denomination["value"]))
            value_input.setEnabled(index != 0)
            name_input.editingFinished.connect(self._save_settings)
            name_input.textChanged.connect(lambda _text: self._schedule_settings_save())
            plural_input.editingFinished.connect(self._save_settings)
            plural_input.textChanged.connect(lambda _text: self._schedule_settings_save())
            value_input.valueChanged.connect(lambda _value: self._save_settings())

            row = QHBoxLayout()
            row.addWidget(name_input)
            row.addWidget(plural_input)
            row.addWidget(value_input)

            self.currency_name_inputs.append(name_input)
            self.currency_plural_inputs.append(plural_input)
            self.currency_value_inputs.append(value_input)

            layout.addRow(f"Currency {index + 1}:", row)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads settings."""

        repository = self.repository()
        self._autosave_timer.stop()
        self._loading_settings = True

        try:
            if repository is None:
                self.theme_combo.setCurrentText("System")
                self.days_per_week_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["days_per_week"]))
                self.weeks_per_month_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["weeks_per_month"]))
                self.months_per_year_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["months_per_year"]))
                self.seasons_per_year_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["seasons_per_year"]))
                self.day_names_input.clear()
                self.month_names_input.clear()
                self.season_names_input.clear()
                self.season_hints_input.clear()
                self.additional_ai_context_input.clear()
                self.time_display_combo.setCurrentIndex(0)
                self.music_enabled_checkbox.setChecked(True)
                self.narrator_enabled_checkbox.setChecked(True)
                self.music_volume_slider.setValue(25)
                self.tts_volume_slider.setValue(90)
                return

            theme = repository.get_setting("theme", "System")
            additional_ai_context = repository.get_setting("ai.additional_context", "")
            denominations = repository.get_currency_denominations()
            calendar_settings = repository.get_calendar_settings()

            if theme in ["System", "Light", "Dark"]:
                self.theme_combo.setCurrentText(str(theme))
            else:
                LOGGER.warning("Unknown theme setting '%s'. Falling back to System.", theme)
                self.theme_combo.setCurrentText("System")

            for index, denomination in enumerate(denominations[: len(self.currency_name_inputs)]):
                self.currency_name_inputs[index].setText(str(denomination["name"]))
                self.currency_plural_inputs[index].setText(str(denomination["plural_name"]))
                self.currency_value_inputs[index].setValue(int(denomination["value"]))

            self.days_per_week_input.setValue(int(calendar_settings["days_per_week"]))
            self.weeks_per_month_input.setValue(int(calendar_settings["weeks_per_month"]))
            self.months_per_year_input.setValue(int(calendar_settings["months_per_year"]))
            self.seasons_per_year_input.setValue(int(calendar_settings["seasons_per_year"]))
            self.day_names_input.setText(", ".join(str(name) for name in calendar_settings["day_names"]))
            self.month_names_input.setText(
                ", ".join(str(name) for name in calendar_settings["month_names"])
            )
            self.season_names_input.setText(
                ", ".join(str(season["name"]) for season in calendar_settings["seasons"])
            )
            self.season_hints_input.setText(
                ", ".join(str(season["weather_hint"]) for season in calendar_settings["seasons"])
            )
            _set_combo_to_data(self.time_display_combo, str(calendar_settings["time_display"]))
            self.additional_ai_context_input.setPlainText(str(additional_ai_context))
            self.music_enabled_checkbox.setChecked(
                _bool_setting(repository.get_setting("audio.music_enabled", True), True)
            )
            self.narrator_enabled_checkbox.setChecked(
                _bool_setting(repository.get_setting("audio.narrator_enabled", True), True)
            )
            self.music_volume_slider.setValue(
                _clamped_int(repository.get_setting("audio.music_volume", 25), 25, 0, 100)
            )
            self.tts_volume_slider.setValue(
                _clamped_int(repository.get_setting("audio.tts_volume", 90), 90, 0, 100)
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
            repository.set_setting("audio.music_enabled", self.music_enabled_checkbox.isChecked())
            repository.set_setting("audio.narrator_enabled", self.narrator_enabled_checkbox.isChecked())
            repository.set_setting("audio.music_volume", self.music_volume_slider.value())
            repository.set_setting("audio.tts_volume", self.tts_volume_slider.value())
            repository.set_currency_denominations(
                [
                    {
                        "name": name_input.text(),
                        "plural_name": plural_input.text(),
                        "value": 1 if index == 0 else value_input.value(),
                    }
                    for index, (name_input, plural_input, value_input) in enumerate(
                        zip(
                            self.currency_name_inputs,
                            self.currency_plural_inputs,
                            self.currency_value_inputs,
                        )
                    )
                ]
            )
            repository.set_calendar_settings(
                {
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
            )
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

            if self.on_audio_settings_changed is not None:
                self.on_audio_settings_changed()
        finally:
            self._saving_settings = False

        self.notify_repository_changed()


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
    tts_volume = _clamped_int(repository.get_setting("audio.tts_volume", 90), 90, 0, 100)

    if sound_manager is not None:
        sound_manager.set_music_volume(music_volume)
        sound_manager.set_music_enabled(music_enabled)

        current_music = str(repository.get_setting("audio.current_music", "") or "").strip()

        if music_enabled and current_music:
            sound_manager.play_music(current_music)
        elif not music_enabled:
            sound_manager.stop_music(clear_current=False)

    if narration_player is not None:
        narration_player.set_volume(tts_volume)
        narration_player.set_enabled(narrator_enabled)


def _slider_row(slider: QSlider, value_label: QLabel) -> QWidget:
    """Builds a compact slider row with a fixed-width value label."""

    value_label.setFixedWidth(42)
    row = QHBoxLayout()
    row.addWidget(slider)
    row.addWidget(value_label)

    widget = QWidget()
    widget.setLayout(row)
    return widget


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

    skill_names = [str(skill.get("name", "")).strip().casefold() for skill in ai_skills]

    if len(skill_names) != len(set(skill_names)):
        return False

    return all(
        str(skill.get("name", "")).strip()
        and str(skill.get("description", "")).strip()
        for skill in ai_skills
    )


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


def _format_starter_items_for_template(items: list[dict[str, Any]]) -> str:
    """Formats starter item dictionaries back into wizard text lines."""

    lines: list[str] = []

    for item in items:
        name = str(item.get("name", "")).strip()

        if not name:
            continue

        parts = [
            name,
            str(item.get("category", "Item")).strip() or "Item",
            str(_safe_int(item.get("quantity"), 1)),
            str(item.get("description", "")).strip(),
            str(_safe_int(item.get("value_base_units"), 0)),
        ]
        lines.append(" | ".join(parts))

    return "\n".join(lines)


def _calendar_type_from_settings(settings: dict[str, Any]) -> str:
    """Infers which calendar option should be selected for saved settings."""

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


def _join_list(values) -> str:
    """Formats a list-like value for table display."""

    if not isinstance(values, list):
        return ""

    return ", ".join(str(value) for value in values if str(value).strip())


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
