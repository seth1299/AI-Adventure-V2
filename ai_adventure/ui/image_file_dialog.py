"""Shared image picker with an editable folder address and resizable window."""

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QFileDialog, QLineEdit

from ai_adventure.visual_assets import descriptive_image_stem


def image_picker_directory(images_dir: Path | None, save_dir: Path | None) -> str:
    """Prefer this save's generated images, then the persistent image root."""
    if images_dir is not None:
        if save_dir is not None:
            grouped = images_dir / descriptive_image_stem(save_dir.name, maximum_length=96)
            if grouped.is_dir():
                return str(grouped)
        if images_dir.is_dir():
            return str(images_dir)
    return str(save_dir) if save_dir is not None else str(Path.home())


class ImageFileDialog(QFileDialog):
    """Qt image picker with a pasteable full-path address bar."""

    def __init__(self, parent=None, caption="Select Image", directory=""):
        super().__init__(parent, caption)
        self._address_editor = None
        self._pending_address = None
        self.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(960, 640)
        self.setFileMode(QFileDialog.FileMode.ExistingFile)
        self.setNameFilters(["Images (*.png *.jpg *.jpeg *.webp *.bmp)", "All Files (*)"])
        if directory:
            self.setDirectory(directory)
        address = self.findChild(QComboBox, "lookInCombo")
        if address is not None:
            address.setEditable(True)
            address.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            address.setEditText(self.directory().absolutePath())
            address.setAccessibleName("Folder path")
            address.setToolTip("Paste a folder or image file path and press Enter.")
            editor = address.lineEdit()
            self._address_editor = editor
            editor.installEventFilter(self)
            address.installEventFilter(self)
            editor.textEdited.connect(self._address_edited)
            self.directoryEntered.connect(self._sync_address)

    def _sync_address(self, directory: str) -> None:
        self._pending_address = None
        if self._address_editor is not None:
            self._address_editor.setText(directory)

    def _address_edited(self, text: str) -> None:
        # QFileDialog only considers the filename when enabling Open. Keep the
        # typed address separately: its internal combo may reset on focus loss.
        self._pending_address = text
        buttons = self.findChild(QDialogButtonBox, "buttonBox")
        if buttons is not None:
            button = buttons.button(QDialogButtonBox.StandardButton.Open)
            if button is not None:
                button.setEnabled(True)

    def accept(self) -> None:
        if self._pending_address is not None:
            self._open_address(self._pending_address)
            return
        super().accept()

    def eventFilter(self, watched, event):
        if (self._address_editor is not None
                and watched in (self._address_editor, self._address_editor.parentWidget())
                and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()
            else:
                self._open_address(self._address_editor.text())
            return True
        return super().eventFilter(watched, event)

    def _open_address(self, text: str) -> None:
        path = Path(text.strip().strip('"')).expanduser()
        if not path.is_absolute():
            path = Path(self.directory().absolutePath()) / path
        if path.is_dir():
            self.setDirectory(str(path))
            self._sync_address(self.directory().absolutePath())
        elif path.is_file():
            self.setDirectory(str(path.parent))
            self.selectFile(str(path))
            filename = self.findChild(QLineEdit, "fileNameEdit")
            if filename is not None:
                filename.setText(path.name)
            self._sync_address(self.directory().absolutePath())
