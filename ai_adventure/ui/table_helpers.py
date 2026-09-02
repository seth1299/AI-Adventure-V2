"""Reusable table widgets, editor configuration, and table interactions."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QFormLayout, QHeaderView, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QStyledItemDelegate, QStyle, QStyleOptionViewItem, QTableWidget,
    QTableWidgetItem, QWidget,
)

from ai_adventure.ui.primitives import (
    _NoWheelComboBox,
    _NoWheelSpinBox,
    _add_combo_options,
    _set_combo_to_data,
    TABLE_CELL_HORIZONTAL_PADDING,
    TABLE_CELL_VERTICAL_PADDING,
    TABLE_INLINE_EDITOR_HEIGHT,
    TABLE_INLINE_EDITOR_MIN_WIDTH,
)

class _NoCellFocusDelegate(QStyledItemDelegate):
    """Draws data table cells with clean selection and readable padding."""

    def paint(self, painter, option, index) -> None:
        clean_option = QStyleOptionViewItem(option)
        clean_option.state &= ~QStyle.StateFlag.State_HasFocus
        clean_option.rect = clean_option.rect.adjusted(
            TABLE_CELL_HORIZONTAL_PADDING,
            TABLE_CELL_VERTICAL_PADDING,
            -TABLE_CELL_HORIZONTAL_PADDING,
            -TABLE_CELL_VERTICAL_PADDING,
        )
        super().paint(painter, clean_option, index)

    def sizeHint(self, option, index) -> QSize:
        clean_option = QStyleOptionViewItem(option)
        clean_option.rect = clean_option.rect.adjusted(
            TABLE_CELL_HORIZONTAL_PADDING,
            TABLE_CELL_VERTICAL_PADDING,
            -TABLE_CELL_HORIZONTAL_PADDING,
            -TABLE_CELL_VERTICAL_PADDING,
        )
        size = super().sizeHint(clean_option, index)
        return QSize(
            size.width() + (TABLE_CELL_HORIZONTAL_PADDING * 2),
            size.height() + (TABLE_CELL_VERTICAL_PADDING * 2),
        )


def _use_soft_table_selection(table: QTableWidget) -> None:
    """Keeps table selection while hiding the gaudy per-cell focus cursor."""

    table.setItemDelegate(_NoCellFocusDelegate(table))
    _allow_selected_row_deselection(table)


class _DeselectSelectedRowFilter(QObject):
    """Clears a table row when the user clicks its already-selected row."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self.table = table

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            index = self.table.indexAt(event.position().toPoint())
            if index.isValid() and self.table.selectionModel().isRowSelected(
                index.row(), index.parent()
            ):
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
                return True
        return super().eventFilter(watched, event)


def _allow_selected_row_deselection(table: QTableWidget) -> None:
    """Lets a second click on the selected row return the table to no selection."""

    if hasattr(table, "_deselect_selected_row_filter"):
        return
    deselect_filter = _DeselectSelectedRowFilter(table)
    table.viewport().installEventFilter(deselect_filter)
    table._deselect_selected_row_filter = deselect_filter  # type: ignore[attr-defined]


class _TableEditorWheelFilter(QObject):
    """Prevents wheel events from changing editors embedded in tables."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(
            watched, (QComboBox, QAbstractSpinBox)
        ):
            event.ignore()
            return True
        return super().eventFilter(watched, event)


class _AppTableWidget(QTableWidget):
    """Application-wide table defaults and embedded-editor wheel behavior."""

    def __init__(self, rows: int = 0, columns: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(rows, columns, parent)
        self._editor_wheel_filter = _TableEditorWheelFilter(self)
        _use_soft_table_selection(self)

    def setCellWidget(self, row: int, column: int, widget: QWidget) -> None:
        """Installs shared wheel protection on every embedded table editor."""

        super().setCellWidget(row, column, widget)
        editors = [widget, *widget.findChildren(QWidget)]
        for editor in editors:
            if isinstance(editor, (QComboBox, QAbstractSpinBox)):
                editor.installEventFilter(self._editor_wheel_filter)

def _table_item(text: Any, sort_value: Any | None = None) -> QTableWidgetItem:
    """Builds a read-only table item with an optional hidden sort value."""

    display_text = str(text)
    item = QTableWidgetItem(display_text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setToolTip(display_text)

    if sort_value is not None:
        item.setData(Qt.ItemDataRole.UserRole, sort_value)

    return item


def _enable_table_sorting(table: QTableWidget, on_section_clicked) -> None:
    """Makes a data table sortable by clicking its column headers."""

    _use_soft_table_selection(table)
    table.setSortingEnabled(False)
    header = table.horizontalHeader()
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(True)
    header.sectionClicked.connect(on_section_clicked)


def _configure_wrapping_table(
    table: QTableWidget,
    stretch_columns: set[int],
) -> None:
    """Configures a read-only table to wrap long text into taller rows."""

    table.setWordWrap(True)
    table.horizontalHeader().setStretchLastSection(False)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setMinimumSectionSize(28)

    for column_index in range(table.columnCount()):
        resize_mode = (
            QHeaderView.ResizeMode.Stretch
            if column_index in stretch_columns
            else QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(column_index, resize_mode)


def _resize_wrapping_table_rows(table: QTableWidget) -> None:
    """Refreshes row heights after wrapped table content changes."""

    table.resizeRowsToContents()


def _update_sort_state(
    table: QTableWidget,
    current_column: int,
    current_order: Qt.SortOrder,
    clicked_column: int,
) -> tuple[int, Qt.SortOrder]:
    """Returns the next sort column/order and updates the header indicator."""

    if clicked_column == current_column:
        next_order = (
            Qt.SortOrder.DescendingOrder
            if current_order == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
    else:
        next_order = Qt.SortOrder.AscendingOrder

    table.horizontalHeader().setSortIndicator(clicked_column, next_order)
    return clicked_column, next_order


def _sort_descending(order: Qt.SortOrder) -> bool:
    """Returns True when table data should be sorted descending."""

    return order == Qt.SortOrder.DescendingOrder



def _combo_current_data_text(combo: QComboBox | None, default: str) -> str:
    """Returns a combo box's current data as text."""

    if combo is None:
        return default

    value = combo.currentData()
    return str(value or default)


def _scrollable_widget(content: QWidget) -> QScrollArea:
    """Wraps tall dialog content so outer action buttons remain visible."""

    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll_area.setWidget(content)
    return scroll_area


def _row_for_cell_widget(table: QTableWidget, widget: QWidget) -> int:
    """Returns the table row containing widget, or -1 when not found."""

    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            if table.cellWidget(row, column) is widget:
                return row

    return -1


def _table_line_edit(text: str) -> QLineEdit:
    """Builds an inline table editor that focuses like a native text box."""

    line_edit = QLineEdit()
    line_edit.setText(text)
    line_edit.setFrame(False)
    line_edit.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    line_edit.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    return line_edit


def _table_spin_box(minimum: int, maximum: int) -> QSpinBox:
    """Builds an inline table number editor with table-wide sizing."""

    spin_box = _NoWheelSpinBox()
    spin_box.setMinimum(minimum)
    spin_box.setMaximum(maximum)
    spin_box.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    spin_box.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    return spin_box


def _table_combo_box(options: dict[str, str], current_value: str) -> QComboBox:
    """Builds an inline table combo box."""

    combo = _NoWheelComboBox()
    _add_combo_options(combo, options)
    _set_combo_to_data(combo, current_value)
    combo.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    combo.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    return combo


def _set_table_column_widths(table: QTableWidget, widths: tuple[int, ...]) -> None:
    """Applies stable table column widths so inline editors do not autoshrink."""

    for column, width in enumerate(widths):
        if column < table.columnCount():
            table.setColumnWidth(column, width)



def _configure_inline_table(
    table: QTableWidget,
    widths: tuple[int, ...],
    *,
    minimum_height: int,
) -> None:
    """Applies the shared inline-editing table behavior."""

    table.setMinimumHeight(minimum_height)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(36)
    table.horizontalHeader().setStretchLastSection(False)
    table.setAlternatingRowColors(True)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    _use_soft_table_selection(table)
    _set_table_column_widths(table, widths)


def _configure_responsive_form(layout: QFormLayout) -> None:
    """Lets wizard form fields grow and wrap cleanly at narrow widths."""

    layout.setFieldGrowthPolicy(
        QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
    )
    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)


def _configure_responsive_table(
    table: QTableWidget,
    *,
    stretch_columns: set[int],
    compact_columns: set[int],
) -> None:
    """Makes an inline editor table consume available width responsively."""

    table.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Expanding,
    )
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(72)
    for column in range(table.columnCount()):
        if column in compact_columns:
            resize_mode = QHeaderView.ResizeMode.ResizeToContents
        elif column in stretch_columns:
            resize_mode = QHeaderView.ResizeMode.Stretch
        else:
            resize_mode = QHeaderView.ResizeMode.Interactive
        header.setSectionResizeMode(column, resize_mode)
    _configure_auto_height_table(table)
    _configure_table_wheel_passthrough(table)


def _configure_auto_height_table(
    table: QTableWidget,
    *,
    maximum_visible_rows: int = 5,
) -> None:
    """Fits a wizard table to its rows until its scrollbar is actually needed."""

    if hasattr(table, "_auto_height_refresh"):
        return

    def refresh_height() -> None:
        visible_row_count = min(
            max(1, table.rowCount()),
            max(1, maximum_visible_rows),
        )
        row_heights = [
            max(table.rowHeight(row), table.verticalHeader().defaultSectionSize())
            for row in range(min(table.rowCount(), visible_row_count))
        ]
        while len(row_heights) < visible_row_count:
            row_heights.append(table.verticalHeader().defaultSectionSize())
        target_height = (
            table.horizontalHeader().sizeHint().height()
            + sum(row_heights)
            + (table.frameWidth() * 2)
            + 6
        )
        table.setMinimumHeight(target_height)
        table.setMaximumHeight(target_height)

    def schedule_refresh(*_args: Any) -> None:
        QTimer.singleShot(0, refresh_height)

    model = table.model()
    model.rowsInserted.connect(schedule_refresh)
    model.rowsRemoved.connect(schedule_refresh)
    model.modelReset.connect(schedule_refresh)
    table._auto_height_refresh = refresh_height  # type: ignore[attr-defined]
    refresh_height()
    schedule_refresh()


class _TableWheelPassthroughFilter(QObject):
    """Routes table wheel input to the enclosing page instead of the table."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self.table = table

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel or not isinstance(event, QWheelEvent):
            return super().eventFilter(watched, event)

        parent = self.table.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()

        if isinstance(parent, QScrollArea):
            page_scrollbar = parent.verticalScrollBar()
            pixel_delta = event.pixelDelta().y()
            if pixel_delta:
                scroll_amount = -pixel_delta
            else:
                wheel_steps = event.angleDelta().y() / 120.0
                scroll_amount = int(
                    -wheel_steps * max(1, page_scrollbar.singleStep()) * 3
                )
            page_scrollbar.setValue(page_scrollbar.value() + scroll_amount)

        event.accept()
        return True


def _configure_table_wheel_passthrough(table: QTableWidget) -> None:
    """Disables wheel scrolling for a table while preserving scrollbar dragging."""

    if hasattr(table, "_wheel_passthrough_filter"):
        return
    wheel_filter = _TableWheelPassthroughFilter(table)
    for watched in (
        table,
        table.viewport(),
        table.verticalScrollBar(),
        table.horizontalScrollBar(),
    ):
        watched.installEventFilter(wheel_filter)
    table._wheel_passthrough_filter = wheel_filter  # type: ignore[attr-defined]


def _table_row_display_name(
    table: QTableWidget,
    row: int,
    column: int,
) -> str:
    """Returns the current user-facing name for a table row."""

    widget = table.cellWidget(row, column)
    if isinstance(widget, QLineEdit):
        return widget.text().strip()
    if isinstance(widget, QComboBox):
        return widget.currentText().strip()

    item = table.item(row, column)
    return item.text().strip() if item is not None else ""


def _set_remove_row_button(
    table: QTableWidget,
    row: int,
    column: int,
    item_label: str,
    remove_callback: Callable[[QPushButton], None],
    *,
    name_column: int = 0,
    protected: bool = False,
) -> QPushButton:
    """Adds one confirmed, row-local Remove action to an editor table."""

    button = QPushButton("Remove")
    button.setObjectName("rowRemoveButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(f"Remove this {item_label}.")
    button.setEnabled(not protected)
    button.setVisible(not protected)

    def confirm_remove() -> None:
        current_row = _row_for_cell_widget(table, button)
        if current_row < 0:
            return

        display_name = _table_row_display_name(table, current_row, name_column)
        target = f'"{display_name}"' if display_name else f"this {item_label}"
        result = QMessageBox.question(
            table,
            f"Remove {item_label.title()}",
            f"Are you sure you want to remove {target}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            remove_callback(button)

    button.clicked.connect(confirm_remove)
    table.setCellWidget(row, column, button)
    return button

def _remove_table_row_by_button(table: QTableWidget, button: QPushButton) -> int:
    """Removes the table row containing button and returns the removed row."""

    row = _row_for_cell_widget(table, button)

    if row >= 0:
        table.removeRow(row)

    return row




__all__ = [
    "_NoCellFocusDelegate",
    "_use_soft_table_selection",
    "_DeselectSelectedRowFilter",
    "_allow_selected_row_deselection",
    "_TableEditorWheelFilter",
    "_AppTableWidget",
    "_table_item",
    "_enable_table_sorting",
    "_configure_wrapping_table",
    "_resize_wrapping_table_rows",
    "_update_sort_state",
    "_sort_descending",
    "_combo_current_data_text",
    "_scrollable_widget",
    "_row_for_cell_widget",
    "_table_line_edit",
    "_table_spin_box",
    "_table_combo_box",
    "_set_table_column_widths",
    "_configure_inline_table",
    "_configure_responsive_form",
    "_configure_responsive_table",
    "_configure_auto_height_table",
    "_TableWheelPassthroughFilter",
    "_configure_table_wheel_passthrough",
    "_table_row_display_name",
    "_set_remove_row_button",
    "_remove_table_row_by_button"
]
