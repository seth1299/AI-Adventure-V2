"""Small reusable input widgets with application-wide interaction rules."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QFormLayout, QLineEdit, QPlainTextEdit, QSpinBox, QTextEdit, QWidget


def configure_wrapped_text(editor: QTextEdit | QPlainTextEdit) -> None:
    """Bound multiline text to its viewport, including rich preformatted blocks."""
    editor.setLineWrapMode(type(editor).LineWrapMode.WidgetWidth)
    editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if isinstance(editor, QTextEdit) and not editor.isReadOnly():
        editor.setAcceptRichText(False)
    if getattr(editor, "_bounded_text_configured", False):
        return
    editor._bounded_text_configured = True

    def wrap_blocks() -> None:
        if getattr(editor, "_normalizing_text_wrap", False):
            return
        editor._normalizing_text_wrap = True
        try:
            block = editor.document().begin()
            while block.isValid():
                block_format = block.blockFormat()
                if block_format.nonBreakableLines():
                    block_format.setNonBreakableLines(False)
                    QTextCursor(block).setBlockFormat(block_format)
                block = block.next()
        finally:
            editor._normalizing_text_wrap = False

    editor.document().contentsChanged.connect(wrap_blocks)
    wrap_blocks()


class _PlainTextPasteFilter(QObject):
    """Apply bounded wrapping and plain-text paste to native text editors."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in {QEvent.Type.Polish, QEvent.Type.Show} and isinstance(watched, (QTextEdit, QPlainTextEdit)):
            configure_wrapped_text(watched)
        if (
            event.type() not in {QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress}
            or not isinstance(watched, (QLineEdit, QTextEdit, QPlainTextEdit))
            or event.key() != Qt.Key.Key_V
            or event.modifiers() != (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        ):
            return False
        event.accept()
        if event.type() == QEvent.Type.ShortcutOverride:
            return True
        if watched.isReadOnly() or not watched.isEnabled():
            return True
        text = QApplication.clipboard().text()
        if not text:
            return True
        if isinstance(watched, QLineEdit):
            # QLineEdit always pastes plain text and applies its native
            # single-line, validator, and maximum-length handling.
            watched.paste()
        elif isinstance(watched, QPlainTextEdit):
            watched.insertPlainText(text)
        else:
            cursor = watched.textCursor()
            plain_format = QTextCharFormat()
            plain_format.setFont(watched.font())
            cursor.beginEditBlock()
            cursor.insertText(text, plain_format)
            cursor.endEditBlock()
            watched.setTextCursor(cursor)
            watched.setCurrentCharFormat(plain_format)
        return True


def install_plain_text_paste(app: QApplication) -> None:
    """Install once, including editors created later by tables and dialogs."""
    if getattr(app, "_plain_text_paste_filter", None) is None:
        app._plain_text_paste_filter = _PlainTextPasteFilter(app)
        app.installEventFilter(app._plain_text_paste_filter)


class FeatureToggleCheckBox(QCheckBox):
    """A feature toggle whose label describes its current checked state."""

    def __init__(self, feature_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._feature_name = feature_name
        self._related_rows: list[tuple[QFormLayout, tuple[int, ...]]] = []
        self.toggled.connect(self._sync_label)
        self._sync_label()

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        # Settings loaders may block signals while restoring saved preferences.
        self._sync_label()

    def _sync_label(self, _checked: bool = False) -> None:
        self.setText(f"{self._feature_name} {'enabled' if self.isChecked() else 'disabled'}")
        for form, rows in self._related_rows:
            for row in rows:
                form.setRowVisible(row, self.isChecked())

    def bind_form_children(self, form: QFormLayout, *controls: QWidget) -> None:
        """Collapse whole child rows, including labels and nested control groups."""
        rows = set()
        for control in controls:
            field = control
            while field is not None:
                row, _role = form.getWidgetPosition(field)
                if row >= 0:
                    rows.add(row)
                    break
                field = field.parentWidget()
            else:
                raise ValueError("Related control must belong to the supplied form")
        self._related_rows.append((form, tuple(sorted(rows))))
        self._sync_label()


class NoWheelComboBox(QComboBox):
    """A combo box that does not change selection from mouse-wheel scrolling."""

    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    """A spin box that does not change value from mouse-wheel scrolling."""

    def wheelEvent(self, event: Any) -> None:
        event.ignore()
