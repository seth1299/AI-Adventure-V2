from __future__ import annotations

from ai_adventure.stats import ATTRIBUTES

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class StatsScreen(RepositoryBackedWidget):
    """Attributes, derived stats, progression rewards, and learned skills."""

    def __init__(self, *, playtesting_tools: bool = False) -> None:
        super().__init__()
        self.playtesting_tools = bool(playtesting_tools)

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
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.attributes_label = QLabel()
        self.attributes_label.setWordWrap(True)
        self.spend_rewards_button = QPushButton("Spend rewards")
        self.spend_rewards_button.clicked.connect(self._spend_rewards)
        self.tests_group = QGroupBox("Recent d20 tests")
        self.tests_group.setCheckable(True)
        self.tests_group.setChecked(False)
        self.tests_label = QLabel()
        self.tests_label.setTextFormat(Qt.TextFormat.PlainText)
        self.tests_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.tests_label.setWordWrap(True)
        self.tests_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tests_scroll = QScrollArea()
        self.tests_scroll.setWidgetResizable(True)
        self.tests_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tests_scroll.setMaximumHeight(240)
        self.tests_scroll.setWidget(self.tests_label)
        self.tests_scroll.hide()
        self.tests_group.toggled.connect(self.tests_scroll.setVisible)
        tests_layout = QVBoxLayout(self.tests_group)
        tests_layout.addWidget(self.tests_scroll)
        self.tests_group.setVisible(self.playtesting_tools)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.attributes_label)
        layout.addWidget(self.spend_rewards_button)
        layout.addWidget(QLabel("Known Skills (+1 per training level)"))
        layout.addWidget(self.skills_table)
        layout.addWidget(self.tests_group)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads skills and recent checks."""

        repository = self.repository()

        if repository is None:
            self.skills_table.setRowCount(0)
            self.tests_label.clear()
            return

        stats = repository.player_stats()
        load = repository.inventory_load()
        progress = "Maximum level" if stats["level"] >= 20 else f"{stats['xp_to_next_level']} XP to next level"
        self.summary_label.setText(
            f"Level {stats['level']} · {stats['xp']} Player XP · {progress}\n"
            f"Health {stats['health_current']}/{stats['health_max']} · Carried {load['weight_lb']:g}/{load['capacity_lb']:g} lb\n"
            f"Unused rewards: {stats['reward_choices']} choices · {stats['skill_advances']} skill advances")
        self.attributes_label.setText("   |   ".join(f"{a}: {stats['attributes'][a]} ({stats['modifiers'][a]:+d})" for a in ATTRIBUTES))
        self.spend_rewards_button.setEnabled(bool(stats["reward_choices"] or stats["skill_advances"]))
        if self.playtesting_tools:
            self.tests_label.setText("\n\n".join(
                f"{t['attribute']} {t['test_kind']}" + (f" ({t['skill_name']})" if t['skill_name'] else "") +
                f": {t['rolls']} {t['bonus']:+d} = {t['total']} vs DC {t['dc']} — {t['outcome']}\n"
                f"Reason: {t.get('reason') or 'Not recorded'}\n"
                f"Associated message_ID: {t.get('message_id') or 'Not associated'}"
                for t in repository.list_d20_tests()
            ) or "No d20 tests recorded yet.")
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

    def _spend_rewards(self) -> None:
        repository = self.repository()
        if repository is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Spend saved rewards")
        form = QFormLayout(dialog)
        summary = QLabel()
        summary.setWordWrap(True)
        choice = QComboBox()
        choice.addItem("+1 attribute", "attribute")
        choice.addItem("Bank two skill advances", "skills")
        attribute = QComboBox()
        for name in ATTRIBUTES:
            attribute.addItem(name)
        spend_choice = QPushButton("Spend one reward choice")
        skill_choice = QComboBox()
        name = QLineEdit()
        description = QLineEdit()
        description.setPlaceholderText("Describe this skill's uses and scope")
        spend_skill = QPushButton("Spend one skill advance")
        close = QPushButton("Close")
        form.addRow(summary)
        form.addRow("Reward:", choice)
        form.addRow("Attribute:", attribute)
        form.addRow(spend_choice)
        form.addRow("Skill:", skill_choice)
        form.addRow("New skill name:", name)
        form.addRow("New skill scope:", description)
        form.addRow(spend_skill)
        form.addRow(close)

        def refresh() -> None:
            stats = repository.player_stats()
            summary.setText(f"{stats['reward_choices']} unused choices · {stats['skill_advances']} skill advances\nAttribute cap 20; skill cap 5. Unused rewards stay saved.")
            spend_choice.setEnabled(stats["reward_choices"] > 0)
            spend_skill.setEnabled(stats["skill_advances"] > 0)
            current = skill_choice.currentData()
            skill_choice.clear()
            skill_choice.addItem("Learn a new level-1 skill", "")
            for skill in repository.list_skills():
                if skill["level"] < 5:
                    skill_choice.addItem(f"{skill['name']} (level {skill['level']} → {skill['level'] + 1})", skill["name"])
            skill_choice.setCurrentIndex(max(0, skill_choice.findData(current)))
            self.refresh()

        def apply(skill: bool) -> None:
            try:
                if skill:
                    repository.spend_skill_advance(skill_choice.currentData() or name.text().strip(), description.text().strip())
                else:
                    repository.spend_player_reward(choice.currentData(), attribute=attribute.currentText())
            except (ValueError, RuntimeError) as exc:
                QMessageBox.warning(dialog, "Reward not spent", str(exc))
                return
            refresh()
            self.notify_repository_changed()

        choice.currentIndexChanged.connect(lambda: attribute.setEnabled(choice.currentData() == "attribute"))
        spend_choice.clicked.connect(lambda: apply(False))
        spend_skill.clicked.connect(lambda: apply(True))
        close.clicked.connect(dialog.accept)
        refresh()
        dialog.exec()

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

    floor = XP_THRESHOLDS_BY_LEVEL[level]
    percentage = min(100, max(0, round((xp - floor) * 100 / 8)))
    bar = QProgressBar()
    bar.setObjectName("skillXpProgressBar")
    bar.setRange(0, 100)
    bar.setValue(percentage)
    max_level = level >= MAX_SKILL_LEVEL
    progress_text = "Max Level" if max_level else f"{percentage}%"
    bar.setValue(0 if max_level else percentage)
    bar.setFormat(progress_text)
    bar.setTextVisible(True)
    bar.setMinimumWidth(120)
    bar.setMinimumHeight(20)
    bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    bar.setToolTip(f"XP progress: {_skill_xp_progress_label(skill)}")
    bar.setAccessibleName(
        f"{str(skill.get('name', 'Skill'))} XP progress: {progress_text}"
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
