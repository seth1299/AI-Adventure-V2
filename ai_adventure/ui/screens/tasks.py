from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class ActiveTasksScreen(RepositoryBackedWidget):
    """Player-facing list of current quests, commissions, and obligations."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.table = _AppTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Task",
                "Type",
                "Status",
                "Details",
                "Contact",
                "Location",
                "Reward",
                "Due",
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _configure_wrapping_table(self.table, {0, 3, 6})
        _enable_table_sorting(self.table, self._sort_by_column)
        self.table.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        layout = QVBoxLayout()
        layout.addWidget(refresh_button)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads active tasks."""

        repository = self.repository()

        if repository is None:
            self.table.setRowCount(0)
            return

        tasks = repository.list_active_tasks()
        tasks.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self.table.setRowCount(len(tasks))

        for row_index, task in enumerate(tasks):
            details = str(task.get("description", ""))
            notes = str(task.get("notes", ""))

            if notes:
                details = f"{details}\n\n{notes}" if details else notes

            self.table.setItem(row_index, 0, _table_item(str(task.get("name", ""))))
            self.table.setItem(row_index, 1, _table_item(str(task.get("category", ""))))
            self.table.setItem(row_index, 2, _table_item(str(task.get("status", ""))))
            self.table.setItem(row_index, 3, _table_item(details))
            self.table.setItem(row_index, 4, _table_item(str(task.get("requester", ""))))
            self.table.setItem(row_index, 5, _table_item(str(task.get("location", ""))))
            self.table.setItem(row_index, 6, _table_item(str(task.get("reward", ""))))
            self.table.setItem(row_index, 7, _table_item(str(task.get("due_date", ""))))

        _resize_wrapping_table_rows(self.table)

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts active tasks by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, task: dict[str, Any]) -> tuple[str, str]:
        """Returns the active task sort key."""

        name = str(task.get("name", "")).casefold()

        if self._sort_column == 1:
            return str(task.get("category", "")).casefold(), name

        if self._sort_column == 2:
            return str(task.get("status", "")).casefold(), name

        if self._sort_column == 3:
            details = str(task.get("description", ""))
            notes = str(task.get("notes", ""))
            return f"{details}\n\n{notes}".casefold(), name

        if self._sort_column == 4:
            return str(task.get("requester", "")).casefold(), name

        if self._sort_column == 5:
            return str(task.get("location", "")).casefold(), name

        if self._sort_column == 6:
            return str(task.get("reward", "")).casefold(), name

        if self._sort_column == 7:
            due_elapsed_minutes = _safe_int(task.get("due_elapsed_minutes"), -1)

            if due_elapsed_minutes >= 0:
                return f"{due_elapsed_minutes:012d}", name

            return str(task.get("due_date", "")).casefold(), name

        return name, name
