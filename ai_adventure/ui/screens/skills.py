from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class SkillsScreen(RepositoryBackedWidget):
    """Read-only skills journal."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.skills_table = _AppTableWidget(0, 4)
        self.skills_table.setHorizontalHeaderLabels(
            ["Skill", "Training", "XP Progress", "Description"]
        )
        self.skills_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _configure_wrapping_table(self.skills_table, set())
        _enable_table_sorting(self.skills_table, self._sort_by_column)
        self.skills_table.horizontalHeader().setSortIndicator(
            self._sort_column,
            self._sort_order,
        )

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Known Skills"))
        layout.addWidget(self.skills_table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads skills and recent checks."""

        repository = self.repository()

        if repository is None:
            self.skills_table.setRowCount(0)
            return

        skills = repository.list_skills()
        skills.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self.skills_table.setRowCount(len(skills))

        for row_index, skill in enumerate(skills):
            level = int(skill.get("level", 1))
            self.skills_table.setItem(row_index, 0, _table_item(str(skill.get("name", ""))))
            self.skills_table.setItem(
                row_index,
                1,
                _table_item(_skill_level_label(level), level),
            )
            self.skills_table.setItem(
                row_index,
                2,
                _table_item(_skill_xp_progress_label(skill), _safe_int(skill.get("xp", 0), 0)),
            )
            self.skills_table.setItem(
                row_index,
                3,
                _table_item(str(skill.get("description", ""))),
            )

        _resize_wrapping_table_rows(self.skills_table)

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts skills by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.skills_table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, skill: dict[str, Any]) -> tuple[Any, str]:
        """Returns the active skill sort key."""

        name = str(skill.get("name", "")).casefold()

        if self._sort_column == 1:
            return _safe_int(skill.get("level", 1), 1), name

        if self._sort_column == 2:
            return _safe_int(skill.get("xp", 0), 0), name

        if self._sort_column == 3:
            return str(skill.get("description", "")).casefold(), name

        return name, name
