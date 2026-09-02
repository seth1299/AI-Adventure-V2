from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403


class MainMenuScreen(QWidget):
    """Main Menu with New Game, Load Game, and Settings actions."""

    def __init__(
        self,
        saves_dir: Path,
        on_new_game,
        on_load_game,
        on_settings,
        on_templates,
        *,
        save_service: SaveGameService | None = None,
        application_name: str = "AI Adventure",
        new_game_label: str = "New Game",
        show_templates: bool = True,
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
        """Reloads save summaries into the load-game combo box."""

        self.save_combo.clear()

        saves = self.save_service.list()

        if not saves:
            self.save_combo.addItem("No saves found", None)
            self._sync_save_action_buttons()
            return

        for summary in saves:
            label = self._format_save_summary(summary)
            self.save_combo.addItem(label, summary.db_path)

        self._sync_save_action_buttons()

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

    def _handle_rename_save(self) -> None:
        """Prompts for a new title for the selected save."""

        db_path = self.save_combo.currentData()

        if db_path is None:
            QMessageBox.information(self, "No Save Selected", "There is no save to rename.")
            return

        summary = self._selected_save_summary()
        current_title = summary.title if summary is not None else ""
        new_title, accepted = QInputDialog.getText(
            self,
            "Rename Save",
            "Save name:",
            text=current_title,
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
            return
        except (SaveFileOperationError, OSError) as error:
            QMessageBox.warning(self, "Save Not Renamed", str(error))
            self.refresh_saves()
            return

        self.refresh_saves()

    def _handle_delete_save(self) -> None:
        """Confirms and deletes the selected save."""

        db_path = self.save_combo.currentData()

        if db_path is None:
            QMessageBox.information(self, "No Save Selected", "There is no save to delete.")
            return

        summary = self._selected_save_summary()
        title = summary.title if summary is not None else "this save"
        result = QMessageBox.question(
            self,
            "Delete Save",
            f"Delete '{title}' permanently?",
        )

        if result != QMessageBox.StandardButton.Yes:
            return

        try:
            self.save_service.delete(Path(db_path))
        except (SaveFileOperationError, OSError) as error:
            QMessageBox.warning(self, "Save Not Deleted", str(error))
            self.refresh_saves()
            return

        self.refresh_saves()

    def _selected_save_summary(self) -> SaveSummary | None:
        """Returns the selected save summary, if it still exists."""

        db_path = self.save_combo.currentData()

        if db_path is None:
            return None

        selected_path = Path(db_path).resolve()

        for summary in self.save_service.list():
            if summary.db_path.resolve() == selected_path:
                return summary

        return None

    def _sync_save_action_buttons(self) -> None:
        """Enables save actions only when a real save is selected."""

        has_save = self.save_combo.currentData() is not None
        self.load_button.setEnabled(has_save)
        self.rename_save_button.setEnabled(has_save)
        self.delete_save_button.setEnabled(has_save)

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
