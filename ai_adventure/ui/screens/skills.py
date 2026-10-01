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
            self.skills_table.setCellWidget(
                row_index,
                2,
                _skill_xp_progress_bar(skill),
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


def _skill_xp_progress_bar(skill: dict[str, Any]) -> QProgressBar:
    """Builds a themed XP bar using the skill system's existing thresholds."""

    level = max(1, min(MAX_SKILL_LEVEL, _safe_int(skill.get("level", 1), 1)))
    xp = max(0, _safe_int(skill.get("xp", 0), 0))
    target_xp = XP_THRESHOLDS_BY_LEVEL[MAX_SKILL_LEVEL]
    if level < MAX_SKILL_LEVEL:
        target_xp = XP_THRESHOLDS_BY_LEVEL[level + 1]

    percentage = 0 if target_xp <= 0 else min(100, round(xp * 100 / target_xp))
    bar = QProgressBar()
    bar.setObjectName("skillXpProgressBar")
    bar.setRange(0, 100)
    bar.setValue(percentage)
    bar.setFormat(f"{percentage}%")
    bar.setTextVisible(True)
    bar.setMinimumWidth(120)
    bar.setMinimumHeight(20)
    bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    bar.setToolTip(f"XP progress: {_skill_xp_progress_label(skill)}")
    bar.setAccessibleName(
        f"{str(skill.get('name', 'Skill'))} XP progress: {percentage}%"
    )
    bar.setStyleSheet(
        "QProgressBar#skillXpProgressBar {"
        " border: 1px solid #64748b; border-radius: 4px;"
        " background-color: #111827; color: #f8fafc;"
        " text-align: center; min-height: 18px;"
        "}"
        "QProgressBar#skillXpProgressBar::chunk {"
        " background-color: #38bdf8; border-radius: 3px;"
        "}"
    )
    return bar
