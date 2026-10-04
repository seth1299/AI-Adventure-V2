"""New Game wizard table, calendar, and form serialization helpers."""

from __future__ import annotations

import uuid
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QCheckBox, QComboBox, QFormLayout, QHeaderView, QLineEdit,
    QPushButton, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit,
    QTimeEdit, QVBoxLayout, QWidget,
)

from ai_adventure.alchemy.ingredients import (
    COMMON_MEASUREMENT_UNITS,
    CRAFTING_INGREDIENT_CATEGORIES,
    CRAFTING_INGREDIENT_CATEGORY_NAMES,
    CRAFTING_ITEM_RARITIES,
    format_recipe_ingredients,
    is_crafting_ingredient_category,
    normalize_recipe_ingredients,
)
from ai_adventure.calendar_system import format_time_of_day
from ai_adventure.items import EQUIPMENT_SLOTS
from ai_adventure.currency import describe_currency_denominations, format_currency_amount
from ai_adventure.new_game_setup import (
    GREGORIAN_CALENDAR_SETTINGS,
    normalize_economy_examples,
)
from ai_adventure.ui.primitives import (
    _NoWheelComboBox,
    _safe_int,
    _set_combo_to_data,
    _set_combo_to_text,
    _split_list,
)
from ai_adventure.ui.table_helpers import (
    _AppTableWidget,
    _configure_auto_height_table,
    _configure_inline_table,
    _configure_responsive_form,
    _configure_responsive_table,
    _configure_table_wheel_passthrough,
    _remove_table_row_by_button,
    _set_remove_row_button,
    _set_table_column_widths,
    _table_combo_box,
    _table_line_edit,
    _table_row_display_name,
    _table_spin_box,
)

LOGGER = __import__("logging").getLogger(__name__)
STARTER_ITEM_COLUMN_WIDTHS = (140, 132, 140, 220, 132, 150, 100, 100, 100)
STARTER_WEAPON_COLUMN_WIDTHS = (150, 132, 100, 96, 120, 120, 132, 132, 100, 100, 100)
STARTER_ARMOR_COLUMN_WIDTHS = (150, 132, 220, 132, 132, 100, 100, 100)
STARTING_NPC_COLUMN_WIDTHS = (150, 160, 260, 132, 100)
STARTING_LOCATION_COLUMN_WIDTHS = (180, 320, 132, 110, 180, 120, 190)
CURRENCY_COLUMN_WIDTHS = (150, 160, 132, 100)
ECONOMY_EXAMPLE_COLUMN_WIDTHS = (220, 132, 100)
STARTING_WEALTH_COLUMN_WIDTHS = (220, 132, 100)
TABLE_INLINE_EDITOR_HEIGHT = 30
TABLE_INLINE_EDITOR_MIN_WIDTH = 132


class _LocationParentComboBox(_NoWheelComboBox):
    """Keep inactive dropdowns hidden when the table refreshes editor geometry."""

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self.property("sublocation_active"):
            self.hide()


def _sync_sublocation_cell_style(checkbox: QCheckBox, within_item: QTableWidgetItem) -> None:
    """Give both dependent cells a stable background independent of row focus."""
    checked = checkbox.isChecked()
    checkbox.setStyleSheet(
        "QCheckBox { background-color: palette(base); }" if checked
        else "QCheckBox { background-color: #808080; }"
    )
    within_item.setBackground(QBrush() if checked else QBrush(QColor("#808080")))

def _append_starting_location_table_row(
    table: QTableWidget,
    location: dict[str, Any],
    row_id: int,
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable starting location row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name_input = _table_line_edit(str(location.get("name", "")))
    name_input.setProperty("starting_location_row_id", str(row_id))
    description_input = _table_line_edit(str(location.get("description", "")))
    mode_input = _table_combo_box(
        {"suggestion": "Suggestion", "exact": "Exact"},
        str(location.get("location_mode", "suggestion") or "suggestion"),
    )
    sublocation_input = QCheckBox()
    sublocation_input.setChecked(bool(location.get("is_sublocation", False)))
    parent_input = _LocationParentComboBox()
    parent_input.setProperty("sublocation_active", sublocation_input.isChecked())
    parent_input.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    parent_input.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    parent_input.setProperty(
        "pending_parent_location",
        str(location.get("parent_location", "") or ""),
    )
    parent_input.setVisible(sublocation_input.isChecked())

    if table.columnCount() < 7:
        table.setColumnCount(7)
    scope_input = _table_combo_box(
        {"broad": "Broad / general", "specific": "Specific storage site"},
        str(location.get("location_scope", "broad")),
    )
    scope_input.setToolTip("Specific: a room, campsite or hideout where items can be recovered. Broad: a city, region or continent.")
    table.setCellWidget(row, 6, scope_input)
    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, description_input)
    table.setCellWidget(row, 2, mode_input)
    table.setCellWidget(row, 3, sublocation_input)
    table.setCellWidget(row, 4, parent_input)
    within_item = QTableWidgetItem()
    within_item.setFlags(Qt.ItemFlag.NoItemFlags)
    table.setItem(row, 4, within_item)

    def sync_sublocation_cells(checked: bool) -> None:
        # Paint the whole checkbox cell, rather than exposing alternating-row
        # or focus backgrounds through a transparent checkbox.
        _sync_sublocation_cell_style(sublocation_input, within_item)
        parent_input.setProperty("sublocation_active", checked)
        parent_input.setVisible(checked)

    sublocation_input.toggled.connect(sync_sublocation_cells)
    sync_sublocation_cells(sublocation_input.isChecked())
    _set_remove_row_button(
        table,
        row,
        5,
        "location",
        remove_callback,
    )
    parent_input.setVisible(sublocation_input.isChecked())
    _set_table_column_widths(table, STARTING_LOCATION_COLUMN_WIDTHS)


def _starting_locations_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads requested starting location rows from table."""

    locations: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        description_widget = table.cellWidget(row, 1)
        mode_widget = table.cellWidget(row, 2)
        sublocation_widget = table.cellWidget(row, 3)
        parent_widget = table.cellWidget(row, 4)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""
        description = (
            description_widget.text().strip()
            if isinstance(description_widget, QLineEdit)
            else ""
        )
        location_mode = (
            str(mode_widget.currentData())
            if isinstance(mode_widget, QComboBox)
            else "suggestion"
        )
        if location_mode not in {"suggestion", "exact"}:
            location_mode = "suggestion"
        is_sublocation = (
            sublocation_widget.isChecked()
            if isinstance(sublocation_widget, QCheckBox)
            else False
        )
        parent_location = (
            str(parent_widget.currentText()).strip()
            if isinstance(parent_widget, QComboBox)
            and parent_widget.currentData() not in (None, "")
            else ""
        )

        locations.append(
            {
                "name": name,
                "description": description,
                "location_mode": location_mode,
                "is_sublocation": is_sublocation,
                "parent_location": parent_location if is_sublocation else "",
                "location_scope": str(table.cellWidget(row, 6).currentData()) if isinstance(table.cellWidget(row, 6), QComboBox) else "broad",
                "requires_ai_invention": (
                    location_mode == "suggestion" or not name or not description
                ),
            }
        )

    return locations


def _starting_location_row_id_for_row(table: QTableWidget, row: int) -> str:
    """Returns the stable id assigned to a starting-location row."""

    name_widget = table.cellWidget(row, 0)

    if not isinstance(name_widget, QLineEdit):
        return ""

    return str(name_widget.property("starting_location_row_id") or "")


def _starting_location_row_for_id(table: QTableWidget, row_id: Any) -> int:
    """Returns the row matching row_id, or -1."""

    target_id = str(row_id)

    for row in range(table.rowCount()):
        if _starting_location_row_id_for_row(table, row) == target_id:
            return row

    return -1


def _starting_location_options_from_table(
    table: QTableWidget,
) -> list[tuple[str, str]]:
    """Returns nonblank starting-location names keyed by stable row id."""

    options: list[tuple[str, str]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        row_id = _starting_location_row_id_for_row(table, row)
        name_widget = table.cellWidget(row, 0)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if row_id and name:
            options.append((row_id, name))

    return options


def _sync_starting_npc_location_dropdowns(
    npc_table: QTableWidget,
    locations: list[tuple[str, str]],
) -> None:
    """Keeps NPC location choices tied to the live starting-location rows."""

    valid_ids = {row_id for row_id, _name in locations}
    for row in range(npc_table.rowCount()):
        if npc_table.isRowHidden(row):
            continue
        location_widget = npc_table.cellWidget(row, 1)
        if not isinstance(location_widget, QComboBox):
            continue
        selected_id = str(location_widget.currentData() or "")
        location_widget.blockSignals(True)
        location_widget.clear()
        location_widget.addItem("Select a location", "")
        for row_id, name in locations:
            location_widget.addItem(name, row_id)
        if selected_id in valid_ids:
            _set_combo_to_data(location_widget, selected_id)
        else:
            location_widget.setCurrentIndex(0)
        location_widget.blockSignals(False)


def _sync_starting_location_parent_dropdowns(
    table: QTableWidget,
    locations: list[tuple[str, str]],
) -> None:
    """Keeps each sublocation parent dropdown hidden until needed and up to date."""

    valid_ids = {row_id for row_id, _name in locations}

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        row_id = _starting_location_row_id_for_row(table, row)
        sublocation_widget = table.cellWidget(row, 3)
        parent_widget = table.cellWidget(row, 4)
        parent_selected = (
            parent_widget.currentData()
            if isinstance(parent_widget, QComboBox)
            else ""
        )
        is_sublocation = (
            sublocation_widget.isChecked()
            if isinstance(sublocation_widget, QCheckBox)
            else False
        )

        if not isinstance(parent_widget, QComboBox):
            continue

        previous_parent_text = parent_widget.currentText().strip()
        pending_parent = str(
            parent_widget.property("pending_parent_location") or ""
        ).strip()
        if not pending_parent and parent_widget.currentData() in (None, ""):
            pending_parent = previous_parent_text

        parent_widget.blockSignals(True)
        parent_widget.clear()
        parent_widget.addItem("Select containing location", "")

        for option_id, name in locations:
            if option_id == row_id:
                continue
            parent_widget.addItem(name, option_id)

        if pending_parent:
            _set_combo_to_text(parent_widget, pending_parent)
            if parent_widget.currentData() not in (None, ""):
                parent_widget.setProperty("pending_parent_location", "")
        elif str(parent_selected or "") in valid_ids:
            _set_combo_to_data(parent_widget, str(parent_selected))

        parent_widget.blockSignals(False)
        if isinstance(sublocation_widget, QCheckBox) and table.item(row, 4) is not None:
            _sync_sublocation_cell_style(sublocation_widget, table.item(row, 4))
        parent_widget.setProperty("sublocation_active", is_sublocation)
        parent_widget.setVisible(is_sublocation)


def _append_starting_npc_table_row(
    table: QTableWidget,
    npc: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
    *,
    location_options: list[tuple[str, str]] | None = None,
    change_callback: Callable[[], None] | None = None,
) -> None:
    """Adds one editable starting NPC row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name_input = _table_line_edit(str(npc.get("name", npc.get("display_name", ""))))
    name_input.setProperty(
        "npc_id",
        str(npc.get("npc_id", "")).strip() or f"starting_npc_{uuid.uuid4().hex}",
    )
    location_input = _NoWheelComboBox()
    location_input.addItem("Select a location", "")
    for row_id, location_name in location_options or []:
        location_input.addItem(location_name, row_id)
    requested_location = str(npc.get("location", "")).strip().casefold()
    if requested_location:
        for index in range(1, location_input.count()):
            if location_input.itemText(index).strip().casefold() == requested_location:
                location_input.setCurrentIndex(index)
                break
    description_input = _table_line_edit(
        str(npc.get("description", npc.get("public_description", "")))
    )
    mode_input = _table_combo_box(
        {"suggestion": "Suggestion", "exact": "Exact"},
        str(npc.get("description_mode", "suggestion") or "suggestion"),
    )
    if change_callback is not None:
        name_input.textChanged.connect(change_callback)
        location_input.currentIndexChanged.connect(change_callback)

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, location_input)
    table.setCellWidget(row, 2, description_input)
    table.setCellWidget(row, 3, mode_input)
    _set_remove_row_button(
        table,
        row,
        4,
        "NPC",
        remove_callback,
    )
    _set_table_column_widths(table, STARTING_NPC_COLUMN_WIDTHS)


def _starting_npcs_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads requested starting NPC rows from table."""

    npcs: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        location_widget = table.cellWidget(row, 1)
        description_widget = table.cellWidget(row, 2)
        mode_widget = table.cellWidget(row, 3)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""
        npc_id = (
            str(name_widget.property("npc_id") or "").strip()
            if isinstance(name_widget, QLineEdit)
            else ""
        )
        location = (
            location_widget.currentText().strip()
            if isinstance(location_widget, QComboBox)
            and location_widget.currentData() not in (None, "")
            else ""
        )
        description = (
            description_widget.text().strip()
            if isinstance(description_widget, QLineEdit)
            else ""
        )

        description_mode = (
            str(mode_widget.currentData())
            if isinstance(mode_widget, QComboBox)
            else "suggestion"
        )
        if description_mode not in {"suggestion", "exact"}:
            description_mode = "suggestion"

        npcs.append(
            {
                "npc_id": npc_id,
                "name": name,
                "location": location,
                "location_source_index": (
                    location_widget.currentIndex() - 1
                    if isinstance(location_widget, QComboBox)
                    and location_widget.currentData() not in (None, "")
                    else -1
                ),
                "description": description,
                "description_mode": description_mode,
                "requires_ai_invention": (
                    description_mode == "suggestion"
                    or not name
                    or not location
                    or not description
                ),
            }
        )

    return npcs



def _build_starter_suggestion_table(kind: str) -> _AppTableWidget:
    """Builds the compact single-column table used for AI item concepts."""

    table = _AppTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["Suggestion", "Remove"])
    # Give the table a real viewport.  With the old 70px minimum, the header
    # consumed nearly all available space and the suggestion editor was
    # effectively unreadable.  The maximum keeps the wizard compact while
    # allowing the table's own vertical scrollbar to handle longer lists.
    _configure_inline_table(table, (520, 90), minimum_height=190)
    table.setMaximumHeight(240)
    table.setToolTip(
        f"Enter a {kind.lower()} concept such as 'Iron Sword'. Gemini will create "
        "the item's description, value, and other details."
    )
    return table


def _append_starter_suggestion_table_row(
    table: QTableWidget,
    kind: str,
    suggestion: str = "",
    remove_callback: Callable[[QPushButton], None] | None = None,
) -> None:
    """Adds one natural-language starter-item suggestion row."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)
    suggestion_input = _table_line_edit(suggestion)
    suggestion_input.setPlaceholderText(f"e.g. {'Iron Sword' if kind == 'Weapon' else 'Leather Satchel'}")
    table.setCellWidget(row, 0, suggestion_input)
    callback: Callable[[QPushButton], None]
    if remove_callback is None:
        def remove_default(button: QPushButton) -> None:
            _remove_table_row_by_button(table, button)
        callback = remove_default
    else:
        callback = remove_callback
    _set_remove_row_button(
        table,
        row,
        1,
        f"{kind.lower()} idea",
        callback,
    )


def _starter_suggestions_from_table(
    table: QTableWidget,
    kind: str,
) -> list[dict[str, Any]]:
    """Reads natural-language starter-item suggestions from a compact table."""

    suggestions: list[dict[str, Any]] = []
    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        widget = table.cellWidget(row, 0)
        suggestion = widget.text().strip() if isinstance(widget, QLineEdit) else ""
        if not suggestion:
            continue
        item: dict[str, Any] = {
            "name": "",
            "category": kind,
            "quantity": 1,
            "description": "",
            "value_base_units": 0,
            "item_request": suggestion,
            "requires_ai_invention": True,
        }
        if kind in {"Weapon", "Armor"}:
            item["item_type"] = kind
        suggestions.append(item)
    return suggestions


def _append_starter_item_table_row(
    table: QTableWidget,
    item: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable starter-item row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)
    name_input = _table_line_edit(str(item.get("name", "")))
    name_input.setProperty("starterItemSource", dict(item))
    category_input = _table_line_edit(str(item.get("category", "Item") or "Item"))
    description_input = _table_line_edit(str(item.get("description", "")))

    quantity_input = _table_spin_box(1, 999_999)
    quantity_input.setValue(_safe_int(item.get("quantity", 1), 1))

    value_input = _table_spin_box(0, 1_000_000_000)
    value_input.setValue(_safe_int(item.get("value_base_units", 0), 0))

    storage_input = QComboBox()
    storage_input.setEditable(True)
    storage_input.addItem("Actively Carried", "actively_carried")
    storage_input.addItem("Home", "home")
    storage_value = str(item.get("storage_location", "actively_carried") or "actively_carried").strip()
    if storage_value.casefold() in {"home", "actively_carried"}:
        _set_combo_to_data(storage_input, storage_value)
    else:
        storage_input.setEditText(storage_value)

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, quantity_input)
    table.setCellWidget(row, 2, category_input)
    table.setCellWidget(row, 3, description_input)
    table.setCellWidget(row, 4, value_input)
    table.setCellWidget(row, 5, storage_input)
    _set_starter_mobility_controls(table, row, 6, item)
    _set_remove_row_button(table, row, 8, "item", remove_callback)
    _set_table_column_widths(table, STARTER_ITEM_COLUMN_WIDTHS)


def _starter_items_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads starter-item rows from table."""

    items: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        quantity_widget = table.cellWidget(row, 1)
        category_widget = table.cellWidget(row, 2)
        description_widget = table.cellWidget(row, 3)
        value_widget = table.cellWidget(row, 4)
        storage_widget = table.cellWidget(row, 5)
        moveable_widget = table.cellWidget(row, 6)
        storable_widget = table.cellWidget(row, 7)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        items.append(
            {
                **(name_widget.property("starterItemSource") or {}),
                "name": name,
                "category": (
                    category_widget.text().strip()
                    if isinstance(category_widget, QLineEdit)
                    and category_widget.text().strip()
                    else "Item"
                ),
                "quantity": quantity_widget.value() if isinstance(quantity_widget, QSpinBox) else 1,
                "description": (
                    description_widget.text().strip()
                    if isinstance(description_widget, QLineEdit)
                    else ""
                ),
                "value_base_units": value_widget.value() if isinstance(value_widget, QSpinBox) else 0,
                "storage_location": (
                    str((storage_widget.currentData() if storage_widget.currentText() == storage_widget.itemText(storage_widget.currentIndex()) else storage_widget.currentText()) or "actively_carried")
                    if isinstance(storage_widget, QComboBox)
                    else "actively_carried"
                ),
                "moveable": moveable_widget.isChecked() if isinstance(moveable_widget, QCheckBox) else True,
                "storable": storable_widget.isChecked() if isinstance(storable_widget, QCheckBox) else True,
                "item_request": "",
                "requires_ai_invention": False,
            }
        )

    return items


def _starter_item_kind(item: dict[str, Any]) -> str:
    """Returns the starter item table kind for a normalized item."""

    category = str(item.get("category", "") or "").strip().casefold()
    item_type = str(item.get("item_type", "") or "").strip().casefold()

    if not item_type and isinstance(item.get("metadata"), dict):
        item_type = str(item["metadata"].get("item_type", "") or "").strip().casefold()

    if category == "weapon" or item_type == "weapon":
        return "Weapon"
    if category in {"armor", "armour", "shield"} or item_type == "armor":
        return "Armor"
    return "Item"


def _metadata_text(item: dict[str, Any], key: str, default: str = "") -> str:
    """Reads a top-level or metadata-backed text value."""

    value = item.get(key, None)

    if (value is None or value == "") and isinstance(item.get("metadata"), dict):
        value = item["metadata"].get(key, default)

    return str(default if value is None else value).strip()


def _metadata_int(item: dict[str, Any], key: str, default: int = 0) -> int:
    """Reads a top-level or metadata-backed integer value."""

    value = item.get(key, None)

    if value is None and isinstance(item.get("metadata"), dict):
        value = item["metadata"].get(key, default)

    return _safe_int(value, default)


def _append_starter_weapon_table_row(table, item, remove_callback):
    table.setColumnCount(9)
    table.setHorizontalHeaderLabels(["Name", "Amount", "Category", "Description", "Value", "Storage", "Moveable", "Storable", "Remove"])
    _append_starter_item_table_row(table, {**item, "category": "Weapon"}, remove_callback)


def _starter_weapons_from_table(table):
    return _starter_items_from_table(table)


def _append_starter_armor_table_row(table, item, remove_callback):
    table.setColumnCount(9)
    table.setHorizontalHeaderLabels(["Name", "Amount", "Category", "Description", "Value", "Storage", "Moveable", "Storable", "Remove"])
    _append_starter_item_table_row(table, {**item, "category": "Armor"}, remove_callback)


def _starter_armor_from_table(table):
    return _starter_items_from_table(table)


def _set_starter_mobility_controls(table: QTableWidget, row: int, first_column: int, item: dict[str, Any]) -> None:
    column = first_column + 3
    if table.columnCount() <= column:
        table.setColumnCount(column + 1)
    table.setHorizontalHeaderItem(column, QTableWidgetItem("Weight / Capacity"))
    button = table.cellWidget(row, column)
    if not isinstance(button, QPushButton):
        button = QPushButton("Weight / Capacityâ€¦")
        button.setToolTip("Set pounds per quantity unit and container/vehicle cargo capacity; leave unspecified for Gemini.")
        button.clicked.connect(lambda: _edit_starter_weight(table, button))
        table.setCellWidget(row, column, button)
    table.setColumnWidth(column, 160)
    for offset, field in enumerate(("moveable", "storable")):
        checkbox = table.cellWidget(row, first_column + offset)
        if not isinstance(checkbox, QCheckBox):
            checkbox = QCheckBox()
            table.setCellWidget(row, first_column + offset, checkbox)
        checkbox.setChecked(item.get(field, (item.get("metadata") or {}).get(field, True)) is not False)
        checkbox.setToolTip("Can be moved" if field == "moveable" else "Can be put inside a container")


def _edit_starter_weight(table: QTableWidget, button: QPushButton) -> None:
    row = next((r for r in range(table.rowCount()) if any(table.cellWidget(r, c) is button for c in range(table.columnCount()))), -1)
    if row < 0:
        return
    name = table.cellWidget(row, 0)
    source = dict(name.property("starterItemSource") or {})
    metadata = source.get("metadata") or {}
    dialog = QDialog(table)
    dialog.setWindowTitle("Item weight and cargo capacity")
    form = QFormLayout()
    controls = {}
    for field, label in (("weight_lb", "Weight per quantity unit:"), ("carrying_capacity_lb", "Container / Vehicle capacity:")):
        control = QDoubleSpinBox()
        control.setRange(-1, 1_000_000_000)
        control.setDecimals(4)
        control.setSuffix(" lb")
        control.setSpecialValueText("Gemini decides")
        control.setValue(float(source.get(field, metadata.get(field, -1))))
        controls[field] = control
        form.addRow(label, control)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    layout.addLayout(form)
    layout.addWidget(buttons)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        for field, control in controls.items():
            source.pop(field, None)
            if isinstance(source.get("metadata"), dict):
                source["metadata"] = dict(source["metadata"])
                source["metadata"].pop(field, None)
            if control.value() >= 0:
                source[field] = control.value()
        name.setProperty("starterItemSource", source)


def _starter_mobility_values(table: QTableWidget, row: int, first_column: int) -> dict[str, bool]:
    return {field: table.cellWidget(row, first_column + offset).isChecked()
            if isinstance(table.cellWidget(row, first_column + offset), QCheckBox) else True
            for offset, field in enumerate(("moveable", "storable"))}


def _append_currency_table_row(
    table: QTableWidget,
    denomination: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable currency denomination row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name = str(denomination.get("name", ""))
    plural_name = str(denomination.get("plural_name", ""))
    name_input = _table_line_edit(name)
    plural_name_input = _table_line_edit(plural_name)
    value_input = _table_spin_box(1, 1_000_000_000)
    if "value" in denomination:
        default_value = _safe_int(denomination.get("value"), 1)
    elif row > 0:
        previous_value_input = table.cellWidget(row - 1, 2)
        previous_value = (
            previous_value_input.value()
            if isinstance(previous_value_input, QSpinBox)
            else 1
        )
        default_value = min(1_000_000_000, max(1, previous_value) * 10)
    else:
        default_value = 1
    value_input.setValue(default_value)

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, plural_name_input)
    table.setCellWidget(row, 2, value_input)
    _set_remove_row_button(
        table,
        row,
        3,
        "currency",
        remove_callback,
        protected=row == 0,
    )
    _sync_currency_base_value_row(table)
    _set_table_column_widths(table, CURRENCY_COLUMN_WIDTHS)


def _sync_currency_base_value_row(table: QTableWidget) -> None:
    """Keeps the first visible currency row as the baseline denomination."""

    for row in range(table.rowCount()):
        value_widget = table.cellWidget(row, 2)

        if not isinstance(value_widget, QSpinBox):
            continue

        if row == 0:
            value_widget.setValue(1)
            value_widget.setEnabled(False)
            value_widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        else:
            value_widget.setEnabled(True)
            value_widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)

        remove_button = table.cellWidget(row, 3)
        if isinstance(remove_button, QPushButton):
            remove_button.setEnabled(row != 0)
            remove_button.setVisible(row != 0)


def _currency_denominations_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads currency denomination rows from table."""

    denominations: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        plural_widget = table.cellWidget(row, 1)
        value_widget = table.cellWidget(row, 2)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        denominations.append(
            {
                "name": name,
                "plural_name": (
                    plural_widget.text().strip()
                    if isinstance(plural_widget, QLineEdit)
                    else ""
                )
                or name,
                "value": value_widget.value() if isinstance(value_widget, QSpinBox) else 1,
            }
        )

    return denominations


def _append_economy_example_table_row(
    table: QTableWidget,
    example: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one common-price example row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name_input = _table_line_edit(str(example.get("name", "")))
    value_input = _table_spin_box(1, 1_000_000_000)
    value_input.setValue(_safe_int(example.get("value_base_units", 1), 1))

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, value_input)
    _set_remove_row_button(
        table,
        row,
        2,
        "economy item",
        remove_callback,
    )
    _set_table_column_widths(table, ECONOMY_EXAMPLE_COLUMN_WIDTHS)


def _economy_examples_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads common-price examples from table."""

    examples: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        value_widget = table.cellWidget(row, 1)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        examples.append(
            {
                "name": name,
                "value_base_units": (
                    value_widget.value()
                    if isinstance(value_widget, QSpinBox)
                    else 1
                ),
            }
        )

    return normalize_economy_examples(examples)



def _calendar_type_from_settings(settings: dict[str, Any]) -> str:
    """Infers which calendar option should be selected for saved settings."""

    if bool(settings.get("ai_generated", False)):
        return "ai_generated"

    if str(settings.get("calendar_type", "")).strip().casefold() == "ai_generated":
        return "ai_generated"

    for key, value in GREGORIAN_CALENDAR_SETTINGS.items():
        if key == "time_display":
            continue

        if settings.get(key) != value:
            return "custom"

    return "gregorian"


def _build_season_settings(
    *,
    names: list[str],
    hints: list[str],
    count: int,
) -> list[dict[str, str]]:
    """Builds season setting dictionaries from comma-separated UI lists."""

    seasons: list[dict[str, str]] = []

    for index in range(max(1, count)):
        name = names[index] if index < len(names) else ""
        hint = hints[index] if index < len(hints) else ""
        seasons.append(
            {
                "name": name,
                "weather_hint": hint,
            }
        )

    return seasons



def _crafting_ingredient_catalog_choices(
    catalog_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Returns sorted catalog items that may be used as recipe ingredients."""

    choices = [
        item
        for item in catalog_items
        if str(item.get("name", "")).strip()
        and is_crafting_ingredient_category(item.get("category", ""))
    ]
    choices.sort(
        key=lambda item: (
            str(item.get("name", "")).casefold(),
            str(item.get("category", "")).casefold(),
        )
    )
    return choices




__all__ = [
    "_append_starting_location_table_row",
    "_starting_locations_from_table",
    "_starting_location_row_id_for_row",
    "_starting_location_row_for_id",
    "_starting_location_options_from_table",
    "_sync_starting_npc_location_dropdowns",
    "_sync_starting_location_parent_dropdowns",
    "_append_starting_npc_table_row",
    "_starting_npcs_from_table",
    "_build_starter_suggestion_table",
    "_append_starter_suggestion_table_row",
    "_starter_suggestions_from_table",
    "_append_starter_item_table_row",
    "_starter_items_from_table",
    "_starter_item_kind",
    "_metadata_text",
    "_metadata_int",
    "_append_starter_weapon_table_row",
    "_starter_weapons_from_table",
    "_append_starter_armor_table_row",
    "_starter_armor_from_table",
    "_append_currency_table_row",
    "_sync_currency_base_value_row",
    "_currency_denominations_from_table",
    "_append_economy_example_table_row",
    "_economy_examples_from_table",
    "_calendar_type_from_settings",
    "_build_season_settings",
    "_crafting_ingredient_catalog_choices"
]
