"""Capacity-aware approval of generated starting possessions."""
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QSpinBox, QVBoxLayout,
)

from ai_adventure.inventory_storage import is_container
from ai_adventure.items import normalize_item_metadata
from ai_adventure.ui.primitives import _NoWheelComboBox
from ai_adventure.ui.table_helpers import _AppTableWidget, _table_item


class StartingInventoryReviewDialog(QDialog):
    """Keep or remove any quantity, or choose an established storage site."""

    def __init__(self, repository, proposal, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.proposal = proposal
        self.setWindowTitle("Review Starting Inventory")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(960, 620)
        self.quantity_inputs = []
        self.storage_inputs = []
        self._updating = False
        self.preview = {}
        explanation = QLabel(
            "Your generated inventory needs review before the opening scene can be written. "
            "Reduce quantities (0 removes an item), or store items at a known specific site. "
            "Only actively carried items and their contents count toward your carried weight. "
            "Carried bags can also add capacity; removing one may reduce your limit."
        )
        explanation.setWordWrap(True)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.table = _AppTableWidget(len(proposal.items), 4)
        self.table.setHorizontalHeaderLabels(["Item", "Quantity to keep", "lb per unit", "Store at"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        choices = ["actively_carried", *[p["name"] for p in proposal.locations
                                        if p.get("location_scope") == "specific"]]
        for item in proposal.items:
            md = normalize_item_metadata(item.get("metadata", item), name=item["name"],
                                         category=item.get("category", "Item"))
            if is_container({"metadata": md}):
                choices.append(item["name"])
        for row, item in enumerate(proposal.items):
            self.table.setItem(row, 0, _table_item(item["name"]))
            quantity = QSpinBox()
            quantity.setRange(0, int(item.get("quantity", 1)))
            quantity.setValue(int(item.get("quantity", 1)))
            quantity.valueChanged.connect(self._recalculate)
            self.table.setCellWidget(row, 1, quantity)
            self.quantity_inputs.append(quantity)
            md = item.get("metadata", item)
            self.table.setItem(row, 2, _table_item(f"{float(md.get('weight_lb', 0)):g}"))
            storage = _NoWheelComboBox()
            current = str(item.get("storage_location", md.get("storage_location", "actively_carried")))
            for choice in dict.fromkeys([*choices, current]):
                storage.addItem("Actively carried" if choice == "actively_carried" else choice, choice)
            storage.setCurrentIndex(storage.findData(current))
            storage.currentTextChanged.connect(self._recalculate)
            self.table.setCellWidget(row, 3, storage)
            self.storage_inputs.append(storage)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.continue_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.continue_button.setText("Continue to Opening")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for widget in (explanation, self.summary, self.table, self.error_label, buttons):
            layout.addWidget(widget)
        self._recalculate()

    def selected_items(self):
        items = []
        for index, original in enumerate(self.proposal.items):
            quantity = self.quantity_inputs[index].value()
            if quantity <= 0:
                continue
            item = deepcopy(original)
            item["quantity"] = quantity
            item["storage_location"] = self.storage_inputs[index].currentData()
            # An explicit location choice supersedes any old parent membership.
            item.pop("container_id", None)
            if isinstance(item.get("metadata"), dict):
                item["metadata"].pop("container_id", None)
                item["metadata"]["storage_location"] = item["storage_location"]
            items.append(item)
        return items

    def _recalculate(self, *_args):
        if self._updating:
            return
        self._updating = True
        try:
            removed = {item["name"] for index, item in enumerate(self.proposal.items)
                       if self.quantity_inputs[index].value() == 0}
            for combo in self.storage_inputs:
                if combo.currentData() in removed:
                    combo.setCurrentIndex(combo.findData("actively_carried"))
            self.preview = self.proposal.preview(self.repository, self.selected_items())
            load = self.preview["load"]
            remaining = (f"Over capacity: {-load['remaining_lb']:g} lb."
                         if load["remaining_lb"] < 0 else f"Remaining: {load['remaining_lb']:g} lb.")
            self.summary.setText(f"Actively carried: {load['weight_lb']:g} lb / {load['capacity_lb']:g} lb capacity. "
                                 + remaining)
            self.error_label.setText(self.preview["error"])
            self.continue_button.setEnabled(not self.preview["error"])
        except ValueError as error:
            self.error_label.setText(str(error))
            self.continue_button.setEnabled(False)
        finally:
            self._updating = False

    def accept(self):
        self._recalculate()
        if self.continue_button.isEnabled():
            super().accept()
