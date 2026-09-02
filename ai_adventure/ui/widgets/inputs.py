"""Small reusable input widgets with application-wide interaction rules."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QSpinBox


class NoWheelComboBox(QComboBox):
    """A combo box that does not change selection from mouse-wheel scrolling."""

    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    """A spin box that does not change value from mouse-wheel scrolling."""

    def wheelEvent(self, event: Any) -> None:
        event.ignore()
