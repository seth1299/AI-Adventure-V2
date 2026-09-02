"""Main-menu screen.

The screen owns only menu presentation and save-file selection actions.  The
application shell supplies callbacks for workflows that belong to the window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_adventure.application.save_game_service import SaveGameService
from ai_adventure.persistence.save_repository import (
    DuplicateSaveTitleError,
    SaveFileOperationError,
    SaveRepository,
    SaveSummary,
)


class MainMenuScreen(QWidget):
    """Main Menu with New Game, Load Game, and Settings actions."""

    def __init__(
        self,
        saves_dir: Path,
        on_new_game: Callable[[], None],
        on_load_game: Callable[[Path], None],
        on_settings: Callable[[], None],
        on_templates: Callable[[], None],
        *,
        save_service: SaveGameService | None = None,
        application_name: str = "AI Adventure",
        new_game_label: str = "New Game",
        show_templates: bool = True,
    ) -> None:
        super().__init__()
        self.saves_dir = saves_dir
        self.save_service = save_service or SaveGameService(saves_dir)
        self.on_new_game = on_new_game
        self.on_load_game = on_load_game
        self.on_settings = on_settings
        self.on_templates = on_templates

        title_label = QLabel(application_name)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 32px; font-weight: bold;")

        new_game_button = QPushButton(new_game_label)
        new_game_button.clicked.connect(self._handle_new_game)

        self.save_combo = QComboBox()
        self.save_combo.currentIndexChanged.connect(
            lambda _index: self._sync_save_action_buttons()
        )
        self.load_button = QPushButton("Load Game")
        self.load_button.clicked.connect(self._handle_load_game)
        self.rename_save_button = QPushButton("Rename Save")
        self.rename_save_button.clicked.connect(self._handle_rename_save)
        self.delete_save_button = QPushButton("Delete Save")
        self.delete_save_button.clicked.connect(self._handle_delete_save)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.on_settings)
        self.templates_button = QPushButton("New Game Templates")
        self.templates_button.clicked.connect(self.on_templates)
        self.templates_button.setVisible(show_templates)

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
        layout.addWidget(self.load_button)
        layout.addWidget(self.rename_save_button)
        layout.addWidget(self.delete_save_button)
        layout.addStretch()

        wrapper = QHBoxLayout()
        wrapper.addStretch()
        wrapper.addLayout(layout, stretch=2)
        wrapper.addStretch()
        self.setLayout(wrapper)
        self.refresh_saves()

    def refresh_saves(self) -> None:
        """Reload save summaries into the load-game combo box."""
        self.save_combo.clear()
        saves = self.save_service.list()
        if not saves:
            self.save_combo.addItem("No saves found", None)
            self._sync_save_action_buttons()
            return
        for summary in saves:
            self.save_combo.addItem(self._format_save_summary(summary), summary.db_path)
        self._sync_save_action_buttons()

    def _handle_new_game(self) -> None:
        self.on_new_game()

    def _handle_load_game(self) -> None:
        db_path = self.save_combo.currentData()
        if db_path is None:
            QMessageBox.information(self, "No Save Selected", "There is no save to load.")
            return
        self.on_load_game(Path(db_path))

    def _handle_rename_save(self) -> None:
        db_path = self.save_combo.currentData()
        if db_path is None:
            QMessageBox.information(self, "No Save Selected", "There is no save to rename.")
            return
        summary = self._selected_save_summary()
        new_title, accepted = QInputDialog.getText(
            self, "Rename Save", "Save name:", text=summary.title if summary else ""
        )
        if not accepted:
            return
        clean_title = new_title.strip()
        if not clean_title:
            QMessageBox.warning(self, "Missing Save Name", "Enter a save name.")
            return
        try:
            self.save_service.rename(Path(db_path), clean_title)
        except DuplicateSaveTitleError as error:
            QMessageBox.warning(self, "Save Name Already Exists", str(error))
        except (SaveFileOperationError, OSError) as error:
            QMessageBox.warning(self, "Save Not Renamed", str(error))
        self.refresh_saves()

    def _handle_delete_save(self) -> None:
        db_path = self.save_combo.currentData()
        if db_path is None:
            QMessageBox.information(self, "No Save Selected", "There is no save to delete.")
            return
        summary = self._selected_save_summary()
        title = summary.title if summary else "this save"
        if QMessageBox.question(self, "Delete Save", f"Delete '{title}' permanently?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.save_service.delete(Path(db_path))
        except (SaveFileOperationError, OSError) as error:
            QMessageBox.warning(self, "Save Not Deleted", str(error))
        self.refresh_saves()

    def _selected_save_summary(self) -> SaveSummary | None:
        db_path = self.save_combo.currentData()
        if db_path is None:
            return None
        selected_path = Path(db_path).resolve()
        return next(
            (summary for summary in self.save_service.list()
             if summary.db_path.resolve() == selected_path),
            None,
        )

    def _sync_save_action_buttons(self) -> None:
        has_save = self.save_combo.currentData() is not None
        for button in (self.load_button, self.rename_save_button, self.delete_save_button):
            button.setEnabled(has_save)

    @staticmethod
    def _format_save_summary(summary: SaveSummary) -> str:
        modified = summary.last_modified.strftime("%Y-%m-%d %I:%M %p")
        return f"{summary.title} - {modified}"
