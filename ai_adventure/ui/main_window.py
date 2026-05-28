from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.ai.gemini_service import (
    GeminiConfigurationError,
    GeminiNarrationService,
)
from ai_adventure.calendar_system import (
    DEFAULT_CALENDAR_SETTINGS,
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    build_month_grid,
)
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.currency import DEFAULT_CURRENCY_DENOMINATIONS, format_currency_amount
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository, SaveSummary


LOGGER = logging.getLogger(__name__)


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

        self.setWindowTitle("AI Adventure")
        self.resize(1100, 750)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.main_menu = MainMenuScreen(
            saves_dir=self.app_paths.saves_dir,
            on_new_game=self.create_new_game,
            on_load_game=self.load_game_from_path,
        )

        self.game_shell = GameShell(on_return_to_menu=self.return_to_menu)

        self.stack.addWidget(self.main_menu)
        self.stack.addWidget(self.game_shell)

        self.return_to_menu()

    def create_new_game(self, title: str) -> None:
        """
        Creates a new save and opens it.

        Args:
            title: Player-facing adventure title.
        """

        try:
            repository = SaveRepository.create_new_save(self.app_paths.saves_dir, title)
        except Exception:
            LOGGER.exception("Failed to create new game.")
            QMessageBox.critical(self, "New Game Failed", "Could not create a new game.")
            return

        self.open_repository(repository)

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

        self.new_game_name = QLineEdit()
        self.new_game_name.setPlaceholderText("Adventure name")

        new_game_button = QPushButton("New Game")
        new_game_button.clicked.connect(self._handle_new_game)

        self.save_combo = QComboBox()

        load_button = QPushButton("Load Game")
        load_button.clicked.connect(self._handle_load_game)

        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(title_label)
        layout.addSpacing(30)

        form = QFormLayout()
        form.addRow("New Adventure:", self.new_game_name)
        layout.addLayout(form)
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

        title = self.new_game_name.text().strip() or "New Adventure"
        self.on_new_game(title)

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


class GameShell(QWidget):
    """In-game shell containing the core play screens."""

    def __init__(self, on_return_to_menu) -> None:
        """
        Args:
            on_return_to_menu: Callback for returning to the Main Menu.
        """

        super().__init__()

        self.on_return_to_menu = on_return_to_menu
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

        self.story_screen = StoryScreen()
        self.calendar_screen = CalendarScreen()
        self.inventory_screen = InventoryScreen()
        self.npcs_screen = NpcsScreen()
        self.skills_screen = SkillsScreen()
        self.alchemy_screen = AlchemyNotebookScreen()
        self.history_screen = HistoryScreen()
        self.settings_screen = SettingsScreen()

        self.screens: list[RepositoryBackedWidget] = [
            self.story_screen,
            self.calendar_screen,
            self.inventory_screen,
            self.npcs_screen,
            self.skills_screen,
            self.alchemy_screen,
            self.history_screen,
            self.settings_screen,
        ]

        self.tabs.addTab(self.story_screen, "Story")
        self.tabs.addTab(self.calendar_screen, "Calendar")
        self.tabs.addTab(self.inventory_screen, "Inventory")
        self.tabs.addTab(self.npcs_screen, "NPCs")
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

    def _handle_tab_changed(self, index: int) -> None:
        """Resets the calendar view to the current month when opened."""

        if self.tabs.widget(index) == self.calendar_screen:
            self.calendar_screen.return_to_current_month()


class StoryScreen(RepositoryBackedWidget):
    """Story screen for player input and narrative output."""

    def __init__(self) -> None:
        super().__init__()

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
                story_lines.append(f"You: {content}")
            elif kind == "story":
                story_lines.append(content)

        self.story_output.setPlainText("\n\n".join(story_lines))
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
        context_packet = AiContextBuilder.from_default_library().build_story_context(
            state,
            player_command=player_text,
            relevant_npcs=relevant_npcs,
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

        self.player_input.clear()
        self.refresh()


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

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Category", "Qty", "Value", "Description"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Inventory"))
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads inventory table."""

        repository = self.repository()

        if repository is None:
            self.table.setRowCount(0)
            return

        items = repository.list_inventory_items()
        denominations = repository.get_currency_denominations()
        self.table.setRowCount(len(items))

        for row_index, item in enumerate(items):
            self.table.setItem(row_index, 0, QTableWidgetItem(str(item.get("name", ""))))
            self.table.setItem(row_index, 1, QTableWidgetItem(str(item.get("category", ""))))
            self.table.setItem(row_index, 2, QTableWidgetItem(str(item.get("quantity", ""))))
            self.table.setItem(
                row_index,
                3,
                QTableWidgetItem(
                    format_currency_amount(
                        int(item.get("value_base_units", 0)),
                        denominations,
                    )
                ),
            )
            self.table.setItem(row_index, 4, QTableWidgetItem(str(item.get("description", ""))))

        self.table.resizeColumnsToContents()


class NpcsScreen(RepositoryBackedWidget):
    """Player-facing NPC journal."""

    def __init__(self) -> None:
        super().__init__()

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Location", "Player-Facing Information", "Last Updated"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

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
        self.table.setRowCount(len(npcs))

        for row_index, npc in enumerate(npcs):
            player_info = str(
                npc.get("player_facing_information")
                or ""
            )
            self.table.setItem(
                row_index,
                0,
                QTableWidgetItem(str(npc.get("display_name", "Unknown NPC"))),
            )
            self.table.setItem(row_index, 1, QTableWidgetItem(str(npc.get("location", ""))))
            self.table.setItem(row_index, 2, QTableWidgetItem(player_info))
            self.table.setItem(row_index, 3, QTableWidgetItem(str(npc.get("updated_at", ""))))

        self.table.resizeColumnsToContents()


class SkillsScreen(RepositoryBackedWidget):
    """Read-only skills journal."""

    def __init__(self) -> None:
        super().__init__()

        self.skills_table = QTableWidget(0, 3)
        self.skills_table.setHorizontalHeaderLabels(
            ["Skill", "Training", "Description"]
        )
        self.skills_table.horizontalHeader().setStretchLastSection(True)
        self.skills_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

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
        self.skills_table.setRowCount(len(skills))

        for row_index, skill in enumerate(skills):
            level = int(skill.get("level", 1))
            self.skills_table.setItem(row_index, 0, QTableWidgetItem(str(skill.get("name", ""))))
            self.skills_table.setItem(row_index, 1, QTableWidgetItem(_skill_level_label(level)))
            self.skills_table.setItem(row_index, 2, QTableWidgetItem(str(skill.get("description", ""))))

        self.skills_table.resizeColumnsToContents()


class AlchemyNotebookScreen(RepositoryBackedWidget):
    """Alchemy notebook screen for notes, reagents, and recipes."""

    def __init__(self) -> None:
        super().__init__()

        self.tabs = QTabWidget()

        self._notes: list[dict] = []
        self._setup_notes_tab()
        self._setup_reagents_tab()
        self._setup_recipes_tab()

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads all alchemy notebook data."""

        repository = self.repository()

        if repository is None:
            self.note_list.clear()
            self.body_input.clear()
            self._notes = []
            self.reagent_table.setRowCount(0)
            self.recipe_table.setRowCount(0)
            return

        self._refresh_notes(repository)
        self._refresh_reagents(repository)
        self._refresh_recipes(repository)

    def _setup_notes_tab(self) -> None:
        """Builds the freeform notes tab."""

        self.note_list = QListWidget()
        self.note_list.currentRowChanged.connect(self._display_selected_note)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Note title")

        self.body_input = QTextEdit()
        self.body_input.setPlaceholderText("Alchemy observations, recipes, reagent notes...")

        add_button = QPushButton("Add Note")
        add_button.clicked.connect(self._add_note)

        layout = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(QLabel("Notes:"))
        left.addWidget(self.note_list)

        right = QVBoxLayout()
        right.addWidget(QLabel("Title:"))
        right.addWidget(self.title_input)
        right.addWidget(QLabel("Body:"))
        right.addWidget(self.body_input)
        right.addWidget(add_button)

        layout.addLayout(left, stretch=1)
        layout.addLayout(right, stretch=2)

        wrapper = QWidget()
        wrapper.setLayout(layout)
        self.tabs.addTab(wrapper, "Notes")

    def _setup_reagents_tab(self) -> None:
        """Builds the structured reagent discovery tab."""

        self.reagent_table = QTableWidget(0, 6)
        self.reagent_table.setHorizontalHeaderLabels(
            ["Name", "Qualities", "Motions", "Virtues", "Uses", "Notes"]
        )
        self.reagent_table.horizontalHeader().setStretchLastSection(True)

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

        add_button = QPushButton("Save Reagent")
        add_button.clicked.connect(self._add_reagent)

        form = QFormLayout()
        form.addRow("Name:", self.reagent_name_input)
        form.addRow("Qualities:", self.reagent_qualities_input)
        form.addRow("Motions:", self.reagent_motions_input)
        form.addRow("Virtues:", self.reagent_virtues_input)
        form.addRow("Uses:", self.reagent_uses_input)
        form.addRow("Notes:", self.reagent_notes_input)
        form.addRow(add_button)

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

    def _refresh_notes(self, repository: SaveRepository) -> None:
        """Reloads alchemy notes."""

        self._notes = repository.list_alchemy_notes()
        self.note_list.clear()

        for note in self._notes:
            title = str(note.get("title", "Untitled Note"))
            created_at = str(note.get("created_at", ""))
            self.note_list.addItem(f"{title} - {created_at}")

    def _refresh_reagents(self, repository: SaveRepository) -> None:
        """Reloads the reagent table."""

        reagents = repository.list_alchemy_reagents()
        self.reagent_table.setRowCount(len(reagents))

        for row_index, reagent in enumerate(reagents):
            self.reagent_table.setItem(row_index, 0, QTableWidgetItem(str(reagent.get("name", ""))))
            self.reagent_table.setItem(row_index, 1, QTableWidgetItem(_join_list(reagent.get("qualities", []))))
            self.reagent_table.setItem(row_index, 2, QTableWidgetItem(_join_list(reagent.get("motions", []))))
            self.reagent_table.setItem(row_index, 3, QTableWidgetItem(_join_list(reagent.get("virtues", []))))
            self.reagent_table.setItem(row_index, 4, QTableWidgetItem(_join_list(reagent.get("uses", []))))
            self.reagent_table.setItem(row_index, 5, QTableWidgetItem(str(reagent.get("notes", ""))))

        self.reagent_table.resizeColumnsToContents()

    def _refresh_recipes(self, repository: SaveRepository) -> None:
        """Reloads the recipe table."""

        recipes = repository.list_alchemy_recipes()
        self.recipe_table.setRowCount(len(recipes))

        for row_index, recipe in enumerate(recipes):
            self.recipe_table.setItem(row_index, 0, QTableWidgetItem(str(recipe.get("name", ""))))
            self.recipe_table.setItem(row_index, 1, QTableWidgetItem(_join_list(recipe.get("ingredients", []))))
            self.recipe_table.setItem(row_index, 2, QTableWidgetItem(str(recipe.get("result", ""))))
            self.recipe_table.setItem(row_index, 3, QTableWidgetItem(_join_list(recipe.get("motions", []))))
            self.recipe_table.setItem(row_index, 4, QTableWidgetItem(_join_list(recipe.get("virtues", []))))
            self.recipe_table.setItem(row_index, 5, QTableWidgetItem(str(recipe.get("notes", ""))))

        self.recipe_table.resizeColumnsToContents()

    def _add_note(self) -> None:
        """Adds an alchemy note."""

        repository = self.repository()

        if repository is None:
            return

        title = self.title_input.text().strip()

        if not title:
            QMessageBox.warning(self, "Missing Title", "Alchemy note title is required.")
            return

        repository.add_alchemy_note(title=title, body=self.body_input.toPlainText())

        self.title_input.clear()
        self.body_input.clear()

        self.refresh()

    def _add_reagent(self) -> None:
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

    def _display_selected_note(self, row_index: int) -> None:
        """
        Displays the selected note body.

        Args:
            row_index: Selected row index.
        """

        if row_index < 0 or row_index >= len(self._notes):
            return

        note = self._notes[row_index]
        self.title_input.setText(str(note.get("title", "")))
        self.body_input.setPlainText(str(note.get("body", "")))


class HistoryScreen(RepositoryBackedWidget):
    """Player-facing adventure journal."""

    def __init__(self) -> None:
        super().__init__()

        self.history_output = QTextEdit()
        self.history_output.setReadOnly(True)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        layout = QVBoxLayout()
        layout.addWidget(refresh_button)
        layout.addWidget(self.history_output)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads player-facing adventure history."""

        repository = self.repository()

        if repository is None:
            self.history_output.clear()
            return

        entries = repository.list_history()
        lines: list[str] = []

        for entry in entries:
            created_at = str(entry.get("created_at", ""))
            kind = str(entry.get("kind", "misc")).casefold()
            content = str(entry.get("content", ""))

            if kind == "player":
                lines.append(f"{created_at} | You | {content}")
            elif kind in {"story", "quest", "world", "note", "spell"}:
                lines.append(f"{created_at} | {content}")

        self.history_output.setPlainText("\n\n".join(lines))


class SettingsScreen(RepositoryBackedWidget):
    """Basic save-specific settings screen."""

    def __init__(self) -> None:
        super().__init__()

        self.player_name_input = QLineEdit()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["System", "Light", "Dark"])

        self.days_per_week_input = QSpinBox()
        self.days_per_week_input.setRange(1, 14)
        self.days_per_week_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["days_per_week"]))

        self.weeks_per_month_input = QSpinBox()
        self.weeks_per_month_input.setRange(1, 12)
        self.weeks_per_month_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["weeks_per_month"]))

        self.months_per_year_input = QSpinBox()
        self.months_per_year_input.setRange(1, 24)
        self.months_per_year_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["months_per_year"]))

        self.seasons_per_year_input = QSpinBox()
        self.seasons_per_year_input.setRange(1, 12)
        self.seasons_per_year_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["seasons_per_year"]))

        self.day_names_input = QLineEdit()
        self.month_names_input = QLineEdit()
        self.season_names_input = QLineEdit()
        self.season_hints_input = QLineEdit()

        self.time_display_combo = QComboBox()
        self.time_display_combo.addItem("Narrative", "narrative")
        self.time_display_combo.addItem("12-hour", "12_hour")
        self.time_display_combo.addItem("24-hour", "24_hour")

        self.currency_name_inputs: list[QLineEdit] = []
        self.currency_plural_inputs: list[QLineEdit] = []
        self.currency_value_inputs: list[QSpinBox] = []

        save_button = QPushButton("Save Settings")
        save_button.clicked.connect(self._save_settings)

        layout = QFormLayout()
        layout.addRow("Player Name:", self.player_name_input)
        layout.addRow("Theme Preference:", self.theme_combo)
        layout.addRow("Days Per Week:", self.days_per_week_input)
        layout.addRow("Weeks Per Month:", self.weeks_per_month_input)
        layout.addRow("Months Per Year:", self.months_per_year_input)
        layout.addRow("Seasons Per Year:", self.seasons_per_year_input)
        layout.addRow("Day Names:", self.day_names_input)
        layout.addRow("Month Names:", self.month_names_input)
        layout.addRow("Season Names:", self.season_names_input)
        layout.addRow("Season Weather Hints:", self.season_hints_input)
        layout.addRow("Time Display:", self.time_display_combo)

        for index, denomination in enumerate(DEFAULT_CURRENCY_DENOMINATIONS):
            name_input = QLineEdit()
            plural_input = QLineEdit()
            value_input = QSpinBox()
            value_input.setMinimum(1)
            value_input.setMaximum(1_000_000_000)
            value_input.setValue(int(denomination["value"]))
            value_input.setEnabled(index != 0)

            row = QHBoxLayout()
            row.addWidget(name_input)
            row.addWidget(plural_input)
            row.addWidget(value_input)

            self.currency_name_inputs.append(name_input)
            self.currency_plural_inputs.append(plural_input)
            self.currency_value_inputs.append(value_input)

            layout.addRow(f"Currency {index + 1}:", row)

        layout.addRow(save_button)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads settings."""

        repository = self.repository()

        if repository is None:
            self.player_name_input.clear()
            self.theme_combo.setCurrentText("System")
            self.days_per_week_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["days_per_week"]))
            self.weeks_per_month_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["weeks_per_month"]))
            self.months_per_year_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["months_per_year"]))
            self.seasons_per_year_input.setValue(int(DEFAULT_CALENDAR_SETTINGS["seasons_per_year"]))
            self.day_names_input.clear()
            self.month_names_input.clear()
            self.season_names_input.clear()
            self.season_hints_input.clear()
            self.time_display_combo.setCurrentIndex(0)
            return

        player_name = repository.get_setting("player_name", "")
        theme = repository.get_setting("theme", "System")
        denominations = repository.get_currency_denominations()
        calendar_settings = repository.get_calendar_settings()

        self.player_name_input.setText(str(player_name))

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

    def _save_settings(self) -> None:
        """Saves settings to the active save."""

        repository = self.repository()

        if repository is None:
            return

        repository.set_setting("player_name", self.player_name_input.text().strip())
        repository.set_setting("theme", self.theme_combo.currentText())
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
        repository.append_history("system", "Settings updated.")

        QMessageBox.information(self, "Settings Saved", "Settings were saved.")


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
