from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import Qt
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

        if self._repository is None:
            LOGGER.error("Screen attempted to use repository before save was loaded.")
            return None

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
    """In-game shell containing all six core screens."""

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
        self.state_screen = StateInspectorScreen()
        self.inventory_screen = InventoryScreen()
        self.alchemy_screen = AlchemyNotebookScreen()
        self.history_screen = HistoryScreen()
        self.settings_screen = SettingsScreen()

        self.screens: list[RepositoryBackedWidget] = [
            self.story_screen,
            self.state_screen,
            self.inventory_screen,
            self.alchemy_screen,
            self.history_screen,
            self.settings_screen,
        ]

        self.tabs.addTab(self.story_screen, "Story")
        self.tabs.addTab(self.state_screen, "State Inspector")
        self.tabs.addTab(self.inventory_screen, "Inventory")
        self.tabs.addTab(self.alchemy_screen, "Alchemy Notebook")
        self.tabs.addTab(self.history_screen, "History")
        self.tabs.addTab(self.settings_screen, "Settings")

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


class StoryScreen(RepositoryBackedWidget):
    """Story screen for player input and narrative output."""

    def __init__(self) -> None:
        super().__init__()

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
        layout.addWidget(self.story_output)
        layout.addLayout(input_row)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Refreshes the story output from history."""

        repository = self.repository()

        if repository is None:
            self.story_output.clear()
            return

        entries = repository.list_history()
        story_lines: list[str] = []

        for entry in entries:
            kind = str(entry.get("kind", "misc")).upper()
            content = str(entry.get("content", ""))
            story_lines.append(f"[{kind}] {content}")

        self.story_output.setPlainText("\n\n".join(story_lines))
        self.story_output.moveCursor(self.story_output.textCursor().MoveOperation.End)

    def _submit_player_action(self) -> None:
        """Records a player action and a placeholder system response."""

        repository = self.repository()

        if repository is None:
            return

        player_text = self.player_input.text().strip()

        if not player_text:
            LOGGER.warning("Skipped blank player action.")
            return

        repository.append_history("player", player_text)
        repository.append_history(
            "story",
            "No A.I. engine is connected yet. This action was recorded successfully.",
        )

        self.player_input.clear()
        self.refresh()


class StateInspectorScreen(RepositoryBackedWidget):
    """Screen for viewing and lightly editing the current game state."""

    def __init__(self) -> None:
        super().__init__()

        self.state_output = QTextEdit()
        self.state_output.setReadOnly(True)

        self.location_input = QLineEdit()
        self.time_input = QLineEdit()
        self.weather_input = QLineEdit()
        self.condition_input = QLineEdit()

        save_button = QPushButton("Save State Fields")
        save_button.clicked.connect(self._save_state_fields)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        form = QFormLayout()
        form.addRow("Location:", self.location_input)
        form.addRow("Time:", self.time_input)
        form.addRow("Weather:", self.weather_input)
        form.addRow("Condition:", self.condition_input)

        button_row = QHBoxLayout()
        button_row.addWidget(save_button)
        button_row.addWidget(refresh_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(button_row)
        layout.addWidget(QLabel("Full State Snapshot:"))
        layout.addWidget(self.state_output)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Refreshes state fields and snapshot display."""

        repository = self.repository()

        if repository is None:
            self.state_output.clear()
            return

        snapshot = repository.get_state_snapshot()

        self.location_input.setText(snapshot.get("location", ""))
        self.time_input.setText(snapshot.get("time", ""))
        self.weather_input.setText(snapshot.get("weather", ""))
        self.condition_input.setText(snapshot.get("condition", ""))

        lines = [f"{key}: {value}" for key, value in snapshot.items()]
        self.state_output.setPlainText("\n".join(lines))

    def _save_state_fields(self) -> None:
        """Saves the editable state fields."""

        repository = self.repository()

        if repository is None:
            return

        repository.set_state_value("location", self.location_input.text().strip())
        repository.set_state_value("time", self.time_input.text().strip())
        repository.set_state_value("weather", self.weather_input.text().strip())
        repository.set_state_value("condition", self.condition_input.text().strip())
        repository.append_history("system", "State fields updated.")

        self.refresh()


class InventoryScreen(RepositoryBackedWidget):
    """Inventory screen with simple add/list behavior."""

    def __init__(self) -> None:
        super().__init__()

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Category", "Qty", "Description"])
        self.table.horizontalHeader().setStretchLastSection(True)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Item name")

        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText("Category")

        self.quantity_input = QSpinBox()
        self.quantity_input.setMinimum(1)
        self.quantity_input.setMaximum(9999)
        self.quantity_input.setValue(1)

        self.description_input = QLineEdit()
        self.description_input.setPlaceholderText("Description")

        add_button = QPushButton("Add Item")
        add_button.clicked.connect(self._add_item)

        form = QFormLayout()
        form.addRow("Name:", self.name_input)
        form.addRow("Category:", self.category_input)
        form.addRow("Quantity:", self.quantity_input)
        form.addRow("Description:", self.description_input)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(add_button)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads inventory table."""

        repository = self.repository()

        if repository is None:
            self.table.setRowCount(0)
            return

        items = repository.list_inventory_items()
        self.table.setRowCount(len(items))

        for row_index, item in enumerate(items):
            self.table.setItem(row_index, 0, QTableWidgetItem(str(item.get("name", ""))))
            self.table.setItem(row_index, 1, QTableWidgetItem(str(item.get("category", ""))))
            self.table.setItem(row_index, 2, QTableWidgetItem(str(item.get("quantity", ""))))
            self.table.setItem(row_index, 3, QTableWidgetItem(str(item.get("description", ""))))

        self.table.resizeColumnsToContents()

    def _add_item(self) -> None:
        """Adds an item to the active save."""

        repository = self.repository()

        if repository is None:
            return

        name = self.name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Name", "Inventory item name is required.")
            return

        repository.add_inventory_item(
            name=name,
            category=self.category_input.text(),
            quantity=self.quantity_input.value(),
            description=self.description_input.text(),
        )

        self.name_input.clear()
        self.category_input.clear()
        self.quantity_input.setValue(1)
        self.description_input.clear()

        self.refresh()


class AlchemyNotebookScreen(RepositoryBackedWidget):
    """Alchemy notebook screen for basic notes."""

    def __init__(self) -> None:
        super().__init__()

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

        self.setLayout(layout)

        self._notes: list[dict] = []

    def refresh(self) -> None:
        """Reloads alchemy notes."""

        repository = self.repository()

        if repository is None:
            self.note_list.clear()
            self.body_input.clear()
            self._notes = []
            return

        self._notes = repository.list_alchemy_notes()

        self.note_list.clear()

        for note in self._notes:
            title = str(note.get("title", "Untitled Note"))
            created_at = str(note.get("created_at", ""))
            self.note_list.addItem(f"{title} - {created_at}")

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
    """Full history log screen."""

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
        """Reloads full adventure history."""

        repository = self.repository()

        if repository is None:
            self.history_output.clear()
            return

        entries = repository.list_history()
        lines: list[str] = []

        for entry in entries:
            created_at = str(entry.get("created_at", ""))
            kind = str(entry.get("kind", "misc"))
            content = str(entry.get("content", ""))
            lines.append(f"{created_at} | {kind.upper()} | {content}")

        self.history_output.setPlainText("\n\n".join(lines))


class SettingsScreen(RepositoryBackedWidget):
    """Basic save-specific settings screen."""

    def __init__(self) -> None:
        super().__init__()

        self.player_name_input = QLineEdit()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["System", "Light", "Dark"])

        save_button = QPushButton("Save Settings")
        save_button.clicked.connect(self._save_settings)

        layout = QFormLayout()
        layout.addRow("Player Name:", self.player_name_input)
        layout.addRow("Theme Preference:", self.theme_combo)
        layout.addRow(save_button)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads settings."""

        repository = self.repository()

        if repository is None:
            self.player_name_input.clear()
            self.theme_combo.setCurrentText("System")
            return

        player_name = repository.get_setting("player_name", "")
        theme = repository.get_setting("theme", "System")

        self.player_name_input.setText(str(player_name))

        if theme in ["System", "Light", "Dark"]:
            self.theme_combo.setCurrentText(str(theme))
        else:
            LOGGER.warning("Unknown theme setting '%s'. Falling back to System.", theme)
            self.theme_combo.setCurrentText("System")

    def _save_settings(self) -> None:
        """Saves settings to the active save."""

        repository = self.repository()

        if repository is None:
            return

        repository.set_setting("player_name", self.player_name_input.text().strip())
        repository.set_setting("theme", self.theme_combo.currentText())
        repository.append_history("system", "Settings updated.")

        QMessageBox.information(self, "Settings Saved", "Settings were saved.")