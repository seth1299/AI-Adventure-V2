"""A docked or hover-revealed location preview for the Conversation screen."""

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ai_adventure.ui.primitives import ClickableImageLabel


class LocationImagePanel(QWidget):
    """Keep only a small handle in the layout when the preview is unpinned."""

    pinned_changed = Signal(bool)
    HIDE_DELAY_MS = 3000

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._overlay_parent = parent
        self._image_identity: str | None = None
        self._pinned = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self.HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._hide_if_idle)

        self.reveal_button = QPushButton("Location image")
        self.reveal_button.setToolTip(
            "Hover or click to reveal the location image. In auto-hide mode, "
            "it closes three seconds after the pointer leaves."
        )
        self.reveal_button.clicked.connect(self.reveal)
        self.pin_checkbox = QCheckBox("Keep visible")
        self.pin_checkbox.setToolTip("Reserve space for the image instead of auto-hiding it.")
        self.pin_checkbox.toggled.connect(self._pin_toggled)
        self.handle = QWidget(self)
        handle_layout = QHBoxLayout(self.handle)
        handle_layout.setContentsMargins(0, 0, 0, 0)
        handle_layout.addWidget(self.reveal_button)
        handle_layout.addWidget(self.pin_checkbox)

        self.content = QFrame(parent)
        self.content.setObjectName("storyLocationImagePanel")
        self.content.setFrameShape(QFrame.Shape.StyledPanel)
        self.content.setAutoFillBackground(True)
        image_layout = QVBoxLayout(self.content)
        image_layout.setContentsMargins(4, 4, 4, 4)
        self.image_label = ClickableImageLabel(self.content)
        self.image_label.setObjectName("storyCurrentLocationImage")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet(
            "background: rgba(15, 23, 42, 96); border-radius: 8px; padding: 3px;"
        )
        image_layout.addWidget(self.image_label)
        self.content.hide()

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._layout.addWidget(self.handle, alignment=Qt.AlignmentFlag.AlignHCenter)
        for widget in (self.handle, self.reveal_button, self.pin_checkbox, self.content, self.image_label):
            widget.installEventFilter(self)
        self.hide()

    def set_image_identity(self, identity: str | None) -> None:
        """Reveal a new scene once, without reopening on ordinary refreshes."""
        changed = identity != self._image_identity
        self._image_identity = identity
        if identity is None:
            self._hide_timer.stop()
            self.content.hide()
            self.hide()
            return
        self.show()
        if self._pinned or changed:
            self.reveal()
        elif self.content.isVisible():
            self._position_overlay()

    def set_pinned(self, pinned: bool) -> None:
        """Restore a saved mode without emitting a preference change."""
        self.pin_checkbox.blockSignals(True)
        self.pin_checkbox.setChecked(pinned)
        self.pin_checkbox.blockSignals(False)
        if self._pinned == pinned:
            return
        self._pinned = pinned
        self._hide_timer.stop()
        self.content.hide()
        if pinned:
            self._layout.addWidget(self.content, alignment=Qt.AlignmentFlag.AlignHCenter)
        else:
            self._layout.removeWidget(self.content)
            self.content.setParent(self._overlay_parent)
        self.updateGeometry()
        if self._image_identity is not None:
            self.reveal()

    def _pin_toggled(self, pinned: bool) -> None:
        self.set_pinned(pinned)
        self.pinned_changed.emit(pinned)

    def reveal(self) -> None:
        """Show the preview without taking keyboard focus from the conversation."""
        if self._image_identity is None or not self.isVisible():
            return
        self.content.show()
        self._position_overlay()
        self.content.raise_()
        if not self._pinned:
            self._hide_timer.start()

    def _position_overlay(self) -> None:
        if self._pinned:
            return
        self.content.adjustSize()
        anchor = self.handle.mapTo(self._overlay_parent, QPoint(0, self.handle.height()))
        x = anchor.x() + (self.handle.width() - self.content.width()) // 2
        x = max(0, min(x, self._overlay_parent.width() - self.content.width()))
        self.content.move(x, anchor.y())

    def _pointer_inside(self) -> bool:
        position = QCursor.pos()
        return any(
            widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(position))
            for widget in (self.handle, self.content)
        )

    def _hide_if_idle(self) -> None:
        if not self._pinned and not self._pointer_inside():
            self.content.hide()

    def eventFilter(self, watched, event):
        if not self._pinned:
            if event.type() in (QEvent.Type.Enter, QEvent.Type.FocusIn):
                self.reveal()
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.FocusOut):
                self._hide_timer.start()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_overlay()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._position_overlay()

    def hideEvent(self, event) -> None:
        self._hide_timer.stop()
        self.content.hide()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Child overlays must be restored explicitly when returning to this tab.
        QTimer.singleShot(0, self, self.reveal)
