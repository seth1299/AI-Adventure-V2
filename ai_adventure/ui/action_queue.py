"""Edit and order the player actions for a single conversation turn."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ai_adventure.turn_actions import ACTION_LABELS, TurnAction


def action_selector(action_type: str = "do") -> QComboBox:
    combo = QComboBox()
    combo.setAccessibleName("Action")
    for kind, label in ACTION_LABELS.items():
        combo.addItem(label, kind)
    combo.setCurrentIndex(combo.findData(action_type))
    return combo


class ActionQueueDialog(QDialog):
    def __init__(self, actions: list[TurnAction], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Action Queue")
        self.resize(660, 410)
        self.entries = QListWidget()
        self.entries.setAccessibleName("Queued actions")
        self.entries.setWordWrap(True)
        self.entries.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.entries.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.entries.setToolTip("Drag actions to change the order in which they resolve.")
        for action in actions:
            self._write_item(QListWidgetItem(self.entries), action)

        add = QPushButton("Add…")
        self.edit = QPushButton("Edit…")
        self.remove = QPushButton("Remove")
        self.submit_all = QPushButton("Submit All")
        close = QPushButton("Close")
        add.clicked.connect(lambda: self._edit_entry(None))
        self.edit.clicked.connect(lambda: self._edit_entry(self.entries.currentItem()))
        self.remove.clicked.connect(self._remove_entry)
        self.entries.itemDoubleClicked.connect(self._edit_entry)
        self.entries.currentItemChanged.connect(self._sync_buttons)
        self.entries.model().rowsMoved.connect(self._sync_buttons)
        self.submit_all.clicked.connect(self.accept)
        close.clicked.connect(self.reject)

        controls = QHBoxLayout()
        for button in (add, self.edit, self.remove):
            controls.addWidget(button)
        controls.addStretch()
        controls.addWidget(self.submit_all)
        controls.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.entries)
        layout.addLayout(controls)
        self.entries.setCurrentRow(self.entries.count() - 1)
        self._sync_buttons()

    def actions(self) -> list[TurnAction]:
        return [self._action(self.entries.item(row)) for row in range(self.entries.count())]

    @staticmethod
    def _action(item: QListWidgetItem) -> TurnAction:
        return TurnAction(*item.data(Qt.ItemDataRole.UserRole))

    @staticmethod
    def _write_item(item: QListWidgetItem, action: TurnAction) -> None:
        item.setData(Qt.ItemDataRole.UserRole, [action.action_type, action.text])

    def _sync_buttons(self, *_args) -> None:
        for row, action in enumerate(self.actions()):
            self.entries.item(row).setText(f"{row + 1}. {ACTION_LABELS[action.action_type]}: {action.text}")
        selected = self.entries.currentItem() is not None
        self.edit.setEnabled(selected)
        self.remove.setEnabled(selected)
        self.submit_all.setEnabled(self.entries.count() > 0)

    def _edit_entry(self, item: QListWidgetItem | None) -> None:
        existing = self._action(item) if item is not None else TurnAction("do", "")
        editor = QDialog(self)
        editor.setWindowTitle("Edit Action" if item is not None else "Add Action")
        kind = action_selector(existing.action_type)
        text = QPlainTextEdit(existing.text)
        text.setPlaceholderText("Enter your action, or describe when it should happen.")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(bool(existing.text.strip()))
        text.textChanged.connect(lambda: save.setEnabled(bool(text.toPlainText().strip())))
        buttons.accepted.connect(editor.accept)
        buttons.rejected.connect(editor.reject)
        form = QFormLayout(editor)
        form.addRow("Action", kind)
        form.addRow("Message", text)
        form.addRow(buttons)
        editor.resize(480, 270)
        text.setFocus()
        if editor.exec() == QDialog.DialogCode.Accepted:
            if item is None:
                item = QListWidgetItem()
            self._write_item(item, TurnAction(str(kind.currentData()), text.toPlainText().strip()))
            if item.listWidget() is None:
                self.entries.addItem(item)
            self.entries.setCurrentItem(item)
            self._sync_buttons()

    def _remove_entry(self) -> None:
        self.entries.takeItem(self.entries.currentRow())
        self._sync_buttons()
