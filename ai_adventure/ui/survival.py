"""Mandatory death choices and a shared viewing-only policy for game screens."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (QApplication, QAbstractButton, QAbstractItemView,
    QAbstractSpinBox, QComboBox, QDialog, QLabel, QLineEdit, QMenu, QPlainTextEdit,
    QPushButton, QSlider, QTextEdit, QVBoxLayout, QWidget)


class SurvivalDeathDialog(QDialog):
    """Escape, reject, and the close button cannot bypass a fate choice."""
    def __init__(self, depleted: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice: str | None = None
        self.setWindowTitle("You have died")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        layout = QVBoxLayout(self)
        message = QLabel("You have died. " + ", ".join(depleted) + " reached 0%. Choose your fate.")
        message.setWordWrap(True)
        layout.addWidget(message)
        self.accept_fate_button = QPushButton("Accept my fate")
        self.recover_button = QPushButton("It is not my time yet")
        layout.addWidget(self.accept_fate_button)
        layout.addWidget(QLabel("Preserve this adventure for viewing, Read Aloud, and images."))
        layout.addWidget(self.recover_button)
        explanation = QLabel("Black out and wake elsewhere at 20% Hunger, Thirst, and Energy. "
            "Lose half the XP toward each skill's next level (rounded down); keep all earned levels.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.accept_fate_button.clicked.connect(lambda: self._choose("accepted"))
        self.recover_button.clicked.connect(lambda: self._choose("recovering"))

    def _choose(self, choice: str) -> None:
        self.choice = choice
        super().accept()

    def accept(self) -> None:
        if self.choice is not None:
            super().accept()

    def reject(self) -> None:
        pass

    def closeEvent(self, event) -> None:
        event.accept() if self.choice is not None else event.ignore()


class AdventureUiGuard(QObject):
    """Restrict screens and newly opened detail dialogs; browsing/media stay usable.

    Database guards independently freeze accepted saves. EnabledChange handling
    keeps queued refreshes from re-enabling a gameplay control.
    """
    SAFE_BUTTONS = {
        "Read Aloud", "Close", "Cancel", "Fit", "100%",
        "Previous", "Next", "Today", "Refresh", "Map", "Load older messages",
        "View Hidden Messages", "Copy Image Prompt", "Copy Map Prompt",
        "Select Image...", "Select Icon...", "Generate New Icon",
        "Create new image for me", "Retry Failed Images",
    }

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._applying = False
        app.installEventFilter(self)

    @staticmethod
    def owner(widget: QObject):
        current = widget
        while current is not None:
            if hasattr(current, "_adventure_locked"):
                return current
            current = current.parent()
        return None

    def set_locked(self, screen: QWidget, locked: bool) -> None:
        screen._adventure_locked = locked
        for widget in screen.findChildren(QWidget):
            if not locked:
                was_enabled = widget.property("adventureWasEnabled")
                if was_enabled is not None:
                    widget.setProperty("adventureWasEnabled", None)
                    widget.setEnabled(was_enabled)
                was_readonly = widget.property("adventureWasReadOnly")
                if was_readonly is not None:
                    widget.setProperty("adventureWasReadOnly", None)
                    widget.setReadOnly(was_readonly)
                if isinstance(widget, QAbstractItemView):
                    edit_triggers = widget.property("adventureEditTriggers")
                    if edit_triggers is not None:
                        widget.setEditTriggers(edit_triggers)
                        widget.setDragDropMode(widget.property("adventureDragDropMode"))
                        widget.setProperty("adventureEditTriggers", None)
                        widget.setProperty("adventureDragDropMode", None)
            else:
                self._restrict(widget)

    def _restrict(self, widget: QWidget) -> None:
        if self._applying or widget.property("adventureViewControl") is True:
            return
        ancestor = widget
        while ancestor is not None:
            if ancestor.property("adventureMediaDialog") is True:
                return
            ancestor = ancestor.parent()
        self._applying = True
        try:
            if isinstance(widget, QAbstractButton):
                if widget.text().replace("&", "") in self.SAFE_BUTTONS or widget.objectName() == "inventoryItemButton":
                    return
                self._disable(widget)
            elif isinstance(widget, (QComboBox, QAbstractSpinBox, QSlider)):
                self._disable(widget)
            elif isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                if widget.property("adventureWasReadOnly") is None:
                    widget.setProperty("adventureWasReadOnly", widget.isReadOnly())
                widget.setReadOnly(True)
            elif isinstance(widget, QAbstractItemView):
                if widget.property("adventureEditTriggers") is None:
                    widget.setProperty("adventureEditTriggers", widget.editTriggers())
                    widget.setProperty("adventureDragDropMode", widget.dragDropMode())
                widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                widget.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
            elif isinstance(widget, QMenu):
                for action in widget.actions():
                    if action.text().replace("&", "") not in self.SAFE_BUTTONS:
                        action.setEnabled(False)
        finally:
            self._applying = False

    @staticmethod
    def _disable(widget: QWidget) -> None:
        if widget.property("adventureWasEnabled") is None:
            widget.setProperty("adventureWasEnabled", widget.isEnabled())
        widget.setEnabled(False)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not self._applying and isinstance(watched, QWidget) and event.type() in {
            QEvent.Type.Show, QEvent.Type.EnabledChange, QEvent.Type.ChildPolished,
        }:
            screen = self.owner(watched)
            if screen is not None and screen._adventure_locked:
                self._restrict(watched)
                if event.type() == QEvent.Type.Show:
                    for child in watched.findChildren(QWidget):
                        self._restrict(child)
        return False


def adventure_ui_guard() -> AdventureUiGuard:
    app = QApplication.instance()
    guard = getattr(app, "_adventure_ui_guard", None)
    if guard is None:
        guard = AdventureUiGuard(app)
        app._adventure_ui_guard = guard
    return guard
