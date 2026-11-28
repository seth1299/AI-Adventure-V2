"""Manage or choose a character in the app's local profile library."""

from base64 import b64decode
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

from PIL.Image import DecompressionBombError
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ai_adventure.character_identity import normalize_character_age
from ai_adventure.new_game_setup import CHARACTER_PRONOUN_OPTIONS, normalize_character_pronouns
from ai_adventure.saved_characters import CHARACTER_PROFILE_FIELDS, SavedCharacter, SavedCharacterLibrary
from ai_adventure.template_images import capture_template_image
from ai_adventure.ui.image_file_dialog import ImageFileDialog
from ai_adventure.ui.primitives import _NoWheelComboBox
from ai_adventure.ui.widgets.character_identity import CharacterAgeInput
from ai_adventure.visual_assets import save_scaled_png


def save_profile(parent: QWidget, library: SavedCharacterLibrary, profile: dict[str, Any], *,
                 portrait: dict[str, str] | None = None, portrait_path: Path | None = None,
                 character_id: str | None = None) -> str | None:
    name = profile["name"].strip()
    try:
        if not name:
            raise ValueError("Enter a character name before saving.")
        existing = library.get(character_id) if character_id else None
        default_name = existing.library_name if existing and existing.library_name else name
        library_name, accepted = QInputDialog.getText(
            parent, "Save to Saved Characters", "Name in Saved Characters", text=default_name,
        )
        if not accepted:
            return None
        library_name = library_name.strip()
        if portrait_path is not None:
            portrait = capture_template_image(SimpleNamespace(subject_type="player", display_name=profile["name"]), portrait_path)
        saved_id = library.save(profile, portrait, character_id=character_id,
                                library_name=library_name if library_name != name else "")
    except (OSError, ValueError) as error:
        QMessageBox.warning(parent, "Could Not Save Character", str(error))
        return None
    QMessageBox.information(parent, "Character Saved", f"{library_name or name} is in Saved Characters.")
    return saved_id


class SavedCharactersDialog(QDialog):
    def __init__(self, library: SavedCharacterLibrary, parent: QWidget | None = None, *, choose: bool = False) -> None:
        super().__init__(parent)
        self.library = library
        self.characters: list[SavedCharacter] = []
        self.selected_character: SavedCharacter | None = None
        self.setWindowTitle("Choose Saved Character" if choose else "Manage Saved Characters")
        preferred_width = 590
        self.list_widget = QListWidget()
        self.list_widget.setAccessibleName("Saved Characters")
        self.empty_label = QLabel("No saved characters yet.")
        self.create_button = QPushButton("New Character…", self)
        self.edit_button = QPushButton("Edit…", self)
        self.delete_button = QPushButton("Delete…", self)
        self.use_button = QPushButton("Use Character", self)
        close = QPushButton("Close", self)
        self.use_button.setVisible(choose)
        self.create_button.clicked.connect(lambda: self._edit(None))
        self.edit_button.clicked.connect(lambda: self._edit(self._current()))
        self.delete_button.clicked.connect(self._delete)
        self.use_button.clicked.connect(self._choose)
        close.clicked.connect(self.reject)
        self.list_widget.currentRowChanged.connect(self._sync_buttons)
        if choose:
            self.list_widget.itemDoubleClicked.connect(self._choose)
        else:
            self.list_widget.itemDoubleClicked.connect(lambda: self._edit(self._current()))
        layout = QVBoxLayout(self)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.list_widget)
        controls = QHBoxLayout()
        layout.addLayout(controls)

        margins = layout.contentsMargins()
        controls_width = preferred_width - margins.left() - margins.right()
        buttons = [self.create_button, self.edit_button, self.delete_button]
        actions = [self.use_button, close] if choose else [close]
        for button in buttons + actions:
            # The wizard stylesheet's minimum width can be smaller than the label.
            button.setMinimumSize(button.sizeHint())

        for button in buttons + actions:
            starts_actions = button is actions[0]
            next_width = button.minimumWidth()
            if starts_actions:
                next_width = sum(action.minimumWidth() for action in actions)
                next_width += controls.spacing() * (len(actions) - 1)
            if controls.count() and controls.sizeHint().width() + controls.spacing() + next_width > controls_width:
                controls = QHBoxLayout()
                layout.addLayout(controls)
            if starts_actions or (button is close and controls.count() == 0):
                controls.addStretch()
            controls.addWidget(button)

        self.resize(preferred_width, 370)
        self._reload()

    def _reload(self, selected_id: str | None = None) -> None:
        self.characters = self.library.list()
        self.list_widget.clear()
        selected_row = 0
        for index, character in enumerate(self.characters):
            self.list_widget.addItem(f"{character.display_name}  ·  {character.modified_at:%Y-%m-%d %I:%M %p}")
            if character.id == selected_id:
                selected_row = index
        self.empty_label.setVisible(not self.characters)
        self.list_widget.setCurrentRow(selected_row if self.characters else -1)
        self._sync_buttons()

    def _current(self) -> SavedCharacter | None:
        row = self.list_widget.currentRow()
        return self.characters[row] if 0 <= row < len(self.characters) else None

    def _sync_buttons(self, *_args) -> None:
        selected = self._current() is not None
        for button in (self.edit_button, self.delete_button, self.use_button):
            button.setEnabled(selected)

    def _choose(self, *_args) -> None:
        if character := self._current():
            self.selected_character = character
            self.accept()

    def _edit(self, character: SavedCharacter | None) -> None:
        editor = QDialog(self)
        editor.setWindowTitle("Edit Saved Character" if character else "New Character")
        editor.resize(550, 630)
        labels = ("Name", "Name pronunciation", "Pronouns", "Appearance", "Backstory", "Notes")
        profile = character.profile if character else {}
        form = QFormLayout(editor)
        library_name = QLineEdit(character.library_name if character else "")
        library_name.setObjectName("library_name")
        library_name.setPlaceholderText("Uses the character name if left blank")
        species = QLineEdit(profile.get("species", ""))
        age = CharacterAgeInput()
        age.setValue(normalize_character_age(profile.get("age")) or 0)
        pronouns_combo = _NoWheelComboBox()
        pronouns_combo.setObjectName("pronouns")
        for option in CHARACTER_PRONOUN_OPTIONS:
            pronouns_combo.addItem(option, option)
        custom_index = pronouns_combo.count()
        pronouns_combo.addItem("Custom")
        custom_pronouns = QLineEdit()
        custom_pronouns.setObjectName("custom_pronouns")
        custom_pronouns.setPlaceholderText("Enter custom pronouns, such as Xe/Xem")

        pronouns = normalize_character_pronouns(profile.get("pronouns"))
        pronouns_index = pronouns_combo.findData(pronouns)
        if pronouns_index < 0:
            pronouns_index = custom_index
            custom_pronouns.setText(pronouns)
        pronouns_combo.setCurrentIndex(pronouns_index)

        fields = {}
        for field, label in zip(CHARACTER_PROFILE_FIELDS, labels):
            if field == "pronouns":
                form.addRow(label, pronouns_combo)
                form.addRow("Custom pronouns", custom_pronouns)
                form.addRow("Species", species)
                form.addRow("Age", age)
                continue
            widget = QPlainTextEdit(profile.get(field, "")) if field in {"appearance", "backstory", "notes"} else QLineEdit(profile.get(field, ""))
            widget.setObjectName(field)
            fields[field] = widget
            form.addRow(label, widget)
            if field == "name":
                form.addRow("Library name", library_name)

        def sync_custom_pronouns():
            form.setRowVisible(custom_pronouns, pronouns_combo.currentIndex() == custom_index)

        pronouns_combo.currentIndexChanged.connect(sync_custom_pronouns)
        sync_custom_pronouns()
        portrait = character.portrait if character else None
        preview = QLabel()
        preview.setObjectName("portrait_preview")
        preview.setAccessibleName("Character portrait preview")
        preview.setFixedSize(112, 112)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        upload = QPushButton("Upload Portrait…")
        upload.setObjectName("upload_portrait")
        portrait_row = QHBoxLayout()
        portrait_row.addWidget(preview)
        portrait_row.addWidget(upload)
        portrait_row.addStretch()
        form.addRow("Portrait", portrait_row)

        def show_portrait() -> None:
            preview.setVisible(portrait is not None)
            if portrait is None:
                return
            pixmap = QPixmap()
            pixmap.loadFromData(b64decode(portrait["png_base64"]), "PNG")
            preview.setPixmap(pixmap.scaled(
                preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            ))

        def upload_portrait() -> None:
            nonlocal portrait
            picker = ImageFileDialog(editor, "Select Character Portrait")
            try:
                if not picker.exec():
                    return
                [filename] = picker.selectedFiles()
                with TemporaryDirectory() as directory:
                    normalized = Path(directory) / "portrait.png"
                    save_scaled_png(Path(filename).read_bytes(), normalized, max_pixels=1024, allow_upscale=False)
                    request = SimpleNamespace(subject_type="player", display_name="")
                    portrait = capture_template_image(request, normalized)
            except (OSError, ValueError, DecompressionBombError) as error:
                QMessageBox.warning(editor, "Could Not Load Portrait", str(error))
                return
            finally:
                picker.deleteLater()
            show_portrait()

        upload.clicked.connect(upload_portrait)
        show_portrait()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        name = fields["name"]
        save.setEnabled(bool(name.text().strip()))
        name.textChanged.connect(lambda text: save.setEnabled(bool(text.strip())))
        buttons.rejected.connect(editor.reject)
        form.addRow(buttons)

        def save_edits():
            updated = {field: (widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()).strip()
                       for field, widget in fields.items()}
            selected_pronouns = pronouns_combo.currentData()
            if pronouns_combo.currentIndex() == custom_index:
                selected_pronouns = custom_pronouns.text()
            updated["pronouns"] = normalize_character_pronouns(selected_pronouns)
            updated["species"] = species.text()
            updated["age"] = age.age()
            saved_portrait = {**portrait, "display_name": updated["name"]} if portrait else None
            try:
                saved_id = self.library.save(updated, saved_portrait, character_id=character.id if character else None,
                                             library_name=library_name.text())
            except (OSError, ValueError) as error:
                QMessageBox.warning(editor, "Could Not Save Character", str(error))
                return
            editor.accept()
            self._reload(saved_id)

        buttons.accepted.connect(save_edits)
        editor.exec()
        editor.deleteLater()

    def _delete(self) -> None:
        character = self._current()
        if character is None or QMessageBox.question(
            self, "Delete Saved Character", f"Delete {character.display_name} from Saved Characters?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.delete(character.id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could Not Delete Character", str(error))
        self._reload()
