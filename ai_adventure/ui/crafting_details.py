"""Readable detail views shared by the crafting notebook's two tables."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QScrollArea, QVBoxLayout, QWidget,
    QHBoxLayout, QPushButton,
)
from ai_adventure.ui.primitives import _set_generated_image


class CraftingDetailsDialog(QDialog):
    """Resizable, scrollable view of one player-visible crafting record."""

    def __init__(self, *, title: str, sections: list[tuple[str, str]], parent=None,
                 image_path=None, on_select_image=None, on_create_image=None):
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
        self.image_preview = QLabel()
        _set_generated_image(self.image_preview, image_path, maximum_width=256,
                             maximum_height=192, accessible_name=f"{title} image")
        layout.addWidget(self.image_preview)
        if on_select_image is not None or on_create_image is not None:
            controls = QHBoxLayout()
            if on_select_image is not None:
                button = QPushButton("Select Image...")
                button.setObjectName("craftingSelectImageButton")
                button.clicked.connect(on_select_image)
                controls.addWidget(button)
            if on_create_image is not None:
                button = QPushButton("Generate New Image")
                button.setObjectName("craftingGenerateImageButton")
                button.clicked.connect(on_create_image)
                controls.addWidget(button)
            layout.addLayout(controls)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
