"""Containers whose layout requirements follow the visible content."""

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLayout, QStackedWidget


class CurrentPageStackedWidget(QStackedWidget):
    """Keep hidden pages from enlarging the window containing the active page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # The default stacked layout otherwise imposes the largest hidden page's
        # minimum size, even when our public size hints describe the visible one.
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def _current_size_hint(self, *, minimum: bool) -> QSize:
        page = self.currentWidget()
        if page is None:
            return QSize(0, 0)
        hint = page.minimumSizeHint() if minimum else page.sizeHint()
        hint = hint.expandedTo(page.minimumSize()).expandedTo(QSize(0, 0))
        margins = self.contentsMargins()
        return hint + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def minimumSizeHint(self) -> QSize:
        return self._current_size_hint(minimum=True)

    def sizeHint(self) -> QSize:
        return self._current_size_hint(minimum=False)
