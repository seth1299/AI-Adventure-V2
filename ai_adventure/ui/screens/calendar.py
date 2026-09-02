from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class CalendarScreen(RepositoryBackedWidget):
    """Player-facing custom calendar view."""

    def __init__(self, *, playtesting_tools: bool = False) -> None:
        super().__init__()

        self.playtesting_tools = bool(playtesting_tools)
        self.month_offset = 0
        self.month_label = QLabel("-")
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.month_label.setStyleSheet("font-size: 18px; font-weight: bold;")

        previous_button = QPushButton("Previous")
        previous_button.clicked.connect(self._show_previous_month)

        today_button = QPushButton("Today")
        today_button.clicked.connect(self.return_to_current_month)

        next_button = QPushButton("Next")
        next_button.clicked.connect(self._show_next_month)

        self.add_event_button = QPushButton("+ Event")
        self.add_event_button.setToolTip("Add a private player-created calendar event")
        self.add_event_button.clicked.connect(self._add_player_event)
        self.add_event_button.setEnabled(False)

        self.settings_button = QPushButton("Calendar Settings")
        self.settings_button.clicked.connect(self._open_calendar_settings_dialog)
        self.settings_button.setEnabled(False)
        self.settings_button.setVisible(self.playtesting_tools)

        navigation_left = QWidget()
        navigation_left_layout = QHBoxLayout(navigation_left)
        navigation_left_layout.setContentsMargins(0, 0, 0, 0)
        navigation_left_layout.addWidget(previous_button)
        navigation_left_layout.addStretch(1)

        navigation_right = QWidget()
        navigation_actions = QHBoxLayout(navigation_right)
        navigation_actions.setContentsMargins(0, 0, 0, 0)
        navigation_actions.addStretch(1)
        navigation_actions.addWidget(self.settings_button)
        navigation_actions.addWidget(self.add_event_button)
        navigation_actions.addWidget(today_button)
        navigation_actions.addWidget(next_button)

        side_width = max(
            navigation_left.sizeHint().width(),
            navigation_right.sizeHint().width(),
        )
        navigation_left.setMinimumWidth(side_width)
        navigation_right.setMinimumWidth(side_width)

        navigation_row = QGridLayout()
        navigation_row.addWidget(
            navigation_left,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        navigation_row.addWidget(
            self.month_label,
            0,
            1,
            alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )
        navigation_row.addWidget(
            navigation_right,
            0,
            2,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        navigation_row.setColumnStretch(0, 1)
        navigation_row.setColumnStretch(2, 1)

        self.summary_label = QLabel("-")
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary_label.setStyleSheet("font-size: 16px; font-weight: 600;")

        self.table = _AppTableWidget(0, 0)
        self.table.setObjectName("calendarGrid")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        _use_soft_table_selection(self.table)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self._pending_day_cell: tuple[int, int] | None = None
        self._day_click_timer = QTimer(self)
        self._day_click_timer.setSingleShot(True)
        self._day_click_timer.timeout.connect(self._open_pending_day_events)
        self.table.cellClicked.connect(self._schedule_open_day_events)
        self.table.cellDoubleClicked.connect(self._add_player_event_for_day)

        self.year_table = _AppTableWidget(0, 0)
        self.year_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.year_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        _use_soft_table_selection(self.year_table)
        self.year_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.year_table.verticalHeader().setVisible(False)

        self.tasks_table = _AppTableWidget(0, 6)
        self.tasks_table.setHorizontalHeaderLabels(
            ["Task", "Description", "Category", "Due", "Location", "Reward"]
        )
        self.tasks_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _use_soft_table_selection(self.tasks_table)
        _configure_wrapping_table(self.tasks_table, {1})

        self.views = QTabWidget()
        month_page = QWidget()
        month_layout = QVBoxLayout()
        month_layout.addLayout(navigation_row)
        month_layout.addWidget(self.summary_label, alignment=Qt.AlignmentFlag.AlignCenter)
        month_layout.addWidget(self.table)
        month_page.setLayout(month_layout)
        self.views.addTab(month_page, "Month")
        self.views.addTab(self.year_table, "Year Overview")
        self.views.addTab(self.tasks_table, "Tasks & Deadlines")

        layout = QVBoxLayout()
        layout.addWidget(self.views)

        self.setLayout(layout)

    def set_repository(self, repository: SaveRepository | None) -> None:
        """Sets the active repository and resets to the current month."""

        self.month_offset = 0
        super().set_repository(repository)

    def refresh(self) -> None:
        """Reloads the calendar grid."""

        repository = self.repository()

        if repository is None:
            self.month_label.setText("-")
            self.summary_label.setText("-")
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.settings_button.setEnabled(False)
            self.add_event_button.setEnabled(False)
            self.year_table.setRowCount(0)
            self.tasks_table.setRowCount(0)
            return

        self.settings_button.setEnabled(True)
        self.add_event_button.setEnabled(True)
        state = StateManager(repository).load_state()
        grid = build_month_grid(state.calendar.to_dict(), self.month_offset)
        calendar_events = repository.list_calendar_events()
        calendar_events.extend(
            self._task_deadline_events(
                repository.list_active_tasks(),
                repository.get_calendar_settings(),
            )
        )
        self.month_offset = int(grid["month_offset"])

        self.month_label.setText(f"{grid['month_name']} - Year {grid['year']}")
        self.summary_label.setText(f"Season: {state.calendar.season_name}")
        self.table.setColumnCount(int(grid["days_per_week"]))
        self.table.setRowCount(int(grid["weeks_per_month"]))
        self.table.setHorizontalHeaderLabels([str(name) for name in grid["day_names"]])

        for row_index, week in enumerate(grid["rows"]):
            for column_index, day in enumerate(week):
                self.table.removeCellWidget(row_index, column_index)
                events = self._events_for_day(
                    calendar_events,
                    int(grid["year"]),
                    int(grid["month_index"]),
                    int(day["day_of_month"]),
                    int(state.calendar.days_per_month),
                    int(state.calendar.days_per_year),
                )
                event_titles = [
                    self._event_display_title(event, state.calendar.settings)
                    for event in events[:3]
                ]
                label = str(day["day_of_month"])
                if event_titles:
                    label += "\n" + "\n".join(f"• {title}" for title in event_titles)
                    if len(events) > 3:
                        label += f"\n+{len(events) - 3} more"

                if day["is_current_day"]:
                    label = f"{day['day_of_month']} · Today" + ("\n" + "\n".join(f"• {title}" for title in event_titles) if event_titles else "")

                item = QTableWidgetItem(label)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, events)
                item.setData(
                    int(Qt.ItemDataRole.UserRole) + 1,
                    {
                        "year": int(grid["year"]),
                        "month": int(grid["month_index"]) + 1,
                        "day": int(day["day_of_month"]),
                    },
                )
                self.table.setItem(row_index, column_index, item)

                if day["is_current_day"]:
                    item.setToolTip("Current day")
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)

                if events:
                    item.setToolTip("\n".join(event_titles))

        self.table.resizeRowsToContents()
        self._refresh_year_overview(state.calendar.to_dict(), calendar_events)
        self._refresh_tasks(repository)

    @staticmethod
    def _task_deadline_events(
        tasks: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Projects dated active tasks into calendar day cells."""

        events: list[dict[str, Any]] = []
        for task in tasks:
            due_minute = _safe_int(task.get("due_elapsed_minutes", -1), -1)
            if due_minute < 0:
                continue
            due = build_calendar_snapshot(due_minute, settings)
            events.append(
                {
                    "event_id": f"active_task_{task.get('name', '')}",
                    "title": str(task.get("name", "Task Deadline")),
                    "description": str(task.get("description", "")),
                    "details": "\n".join(
                        value for value in [
                            str(task.get("description", "")),
                            f"Requester: {task.get('requester', '')}",
                            f"Reward: {task.get('reward', '')}",
                            f"Location: {task.get('location', '')}",
                        ] if value and not value.endswith(": ")
                    ),
                    "category": str(task.get("category", "Task")),
                    "month": int(due["month_number"]),
                    "day": int(due["day_of_month"]),
                    "duration_days": 1,
                    "recurrence": "none",
                    "year": int(due["year"]),
                }
            )
        return events

    @staticmethod
    def _events_for_day(
        events: list[dict[str, Any]],
        year: int,
        month_index: int,
        day: int,
        days_per_month: int,
        days_per_year: int,
    ) -> list[dict[str, Any]]:
        """Returns events whose date range includes the requested day."""

        target = month_index * days_per_month + day
        matches: list[dict[str, Any]] = []
        for event in events:
            if event.get("recurrence") != "yearly" and int(event.get("year", 1)) != year:
                continue
            start = (int(event.get("month", 1)) - 1) * days_per_month + int(event.get("day", 1))
            duration = max(1, int(event.get("duration_days", 1)))
            if any(((start - 1 + offset) % days_per_year) + 1 == target for offset in range(duration)):
                matches.append(event)
        return matches

    def _schedule_open_day_events(self, row: int, column: int) -> None:
        """Delays single-click details so a double-click can create an event."""

        self._pending_day_cell = (row, column)
        self._day_click_timer.start(max(1, QApplication.doubleClickInterval()))

    def _open_pending_day_events(self) -> None:
        """Opens the day selected by a completed single click."""

        pending = self._pending_day_cell
        self._pending_day_cell = None
        if pending is not None:
            self._open_day_events(*pending)

    def _open_day_events(self, row: int, column: int) -> None:
        """Shows details for calendar events listed in a day cell."""

        item = self.table.item(row, column)
        events = item.data(Qt.ItemDataRole.UserRole) if item is not None else []
        if not isinstance(events, list) or not events:
            return
        repository = self.repository()
        if repository is None:
            return
        dialog = CalendarDayEventsDialog(
            repository=repository,
            events=events,
            calendar_settings=repository.get_calendar_settings(),
            parent=self,
        )
        dialog.exec()
        if dialog.changed:
            self.refresh()
            self.notify_repository_changed()

    @staticmethod
    def _event_display_title(
        event: dict[str, Any],
        calendar_settings: dict[str, Any],
    ) -> str:
        """Returns a compact title prefixed by its time when one is known."""

        title = str(event.get("title", "Event") or "Event")
        time_label = _calendar_event_time_label(event, calendar_settings)
        if not time_label:
            return title
        return f"{time_label} — {title}"

    def _add_player_event(self) -> None:
        """Creates a private player-authored event for the viewed month."""

        repository = self.repository()
        if repository is None:
            return
        state = StateManager(repository).load_state()
        grid = build_month_grid(state.calendar.to_dict(), self.month_offset)
        self._open_player_event_dialog(
            default_year=int(grid["year"]),
            default_month=int(grid["month_index"]) + 1,
            default_day=1,
        )

    def _add_player_event_for_day(self, row: int, column: int) -> None:
        """Creates an event with a double-clicked calendar date preselected."""

        self._day_click_timer.stop()
        self._pending_day_cell = None
        item = self.table.item(row, column)
        date = (
            item.data(int(Qt.ItemDataRole.UserRole) + 1)
            if item is not None
            else None
        )
        if not isinstance(date, dict):
            return
        self._open_player_event_dialog(
            default_year=_safe_int(date.get("year"), 1),
            default_month=_safe_int(date.get("month"), 1),
            default_day=_safe_int(date.get("day"), 1),
        )

    def _open_player_event_dialog(
        self,
        *,
        default_year: int,
        default_month: int,
        default_day: int,
    ) -> None:
        """Opens and persists the shared player-created calendar event dialog."""

        repository = self.repository()
        if repository is None:
            return
        dialog = _main_window_override("CalendarPlayerEventDialog", CalendarPlayerEventDialog)(
            calendar_settings=repository.get_calendar_settings(),
            default_year=default_year,
            default_month=default_month,
            default_day=default_day,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        repository.upsert_calendar_event(dialog.build_event())
        self.refresh()
        self.notify_repository_changed()

    def _refresh_year_overview(
        self,
        calendar: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> None:
        """Builds a season-grouped overview of every month in the current year."""

        settings = calendar.get("settings", {})
        months = list(settings.get("month_names", []))
        seasons = list(settings.get("seasons", []))
        season_count = max(1, len(seasons))
        grouped: list[list[int]] = [[] for _ in range(season_count)]
        for month_index in range(len(months)):
            grouped[min(month_index * season_count // max(1, len(months)), season_count - 1)].append(month_index)
        self.year_table.setColumnCount(season_count)
        self.year_table.setHorizontalHeaderLabels([
            str(season.get("name", f"Season {index + 1}")) for index, season in enumerate(seasons)
        ] or ["Year"])
        self.year_table.setRowCount(max((len(group) for group in grouped), default=0))
        year = int(calendar.get("year", 1))
        for column, month_indexes in enumerate(grouped):
            for row, month_index in enumerate(month_indexes):
                month_events = [
                    event for event in events
                    if int(event.get("month", 1)) == month_index + 1
                    and (event.get("recurrence") == "yearly" or int(event.get("year", 1)) == year)
                ]
                text = str(months[month_index])
                if month_events:
                    text += "\n" + "\n".join(f"• {event['title']}" for event in month_events[:4])
                year_item = QTableWidgetItem(text)
                year_item.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
                self.year_table.setItem(row, column, year_item)
        self.year_table.resizeRowsToContents()

    def _refresh_tasks(self, repository: SaveRepository) -> None:
        """Projects active tasks and deadlines into the Calendar tab."""

        tasks = repository.list_active_tasks()
        self.tasks_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            values = [
                task.get("name", ""),
                task.get("description", "") or "No description recorded yet.",
                task.get("category", ""),
                task.get("due_date", "N/A"),
                task.get("location", ""),
                task.get("reward", ""),
            ]
            for column, value in enumerate(values):
                self.tasks_table.setItem(row, column, _table_item(str(value)))
        _resize_wrapping_table_rows(self.tasks_table)

    def return_to_current_month(self) -> None:
        """Returns the grid to the current month and refreshes."""

        self.month_offset = 0
        self.refresh()

    def _show_previous_month(self) -> None:
        """Shows the previous month."""

        self.month_offset -= 1
        self.refresh()

    def _show_next_month(self) -> None:
        """Shows the next month."""

        self.month_offset += 1
        self.refresh()

    def _open_calendar_settings_dialog(self) -> None:
        """Opens the save-specific calendar settings dialog."""

        repository = self.repository()

        if repository is None:
            return

        dialog = _main_window_override("CalendarSettingsDialog", CalendarSettingsDialog)(repository.get_calendar_settings(), self)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_calendar_settings(dialog.build_settings())

    def _save_calendar_settings(self, settings: dict[str, Any]) -> None:
        """Persists calendar settings and refreshes dependent screens."""

        repository = self.repository()

        if repository is None:
            return

        repository.set_calendar_settings(settings)
        _refresh_repository_calendar_time(repository)
        self.month_offset = 0
        self.refresh()
        self.notify_repository_changed()
