"""Readable detail views shared by the crafting notebook's two tables."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QScrollArea, QVBoxLayout, QWidget,
)


class CraftingDetailsDialog(QDialog):
    """Resizable, scrollable view of one player-visible crafting record."""

    def __init__(self, *, title: str, sections: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(480, 400)
        self.setSizeGripEnabled(True)
        self.resize(640, 680)

        heading = QLabel(title)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        content = QWidget()
        body = QVBoxLayout(content)
        for name, text in sections:
            label = QLabel(name)
            label.setStyleSheet("font-weight: 700;")
            value = QLabel(text or "Not recorded.")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.addWidget(label)
            body.addWidget(value)
            body.addSpacing(12)
        body.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
