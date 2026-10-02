"""Small reusable input widgets with application-wide interaction rules."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QSpinBox, QWidget


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
