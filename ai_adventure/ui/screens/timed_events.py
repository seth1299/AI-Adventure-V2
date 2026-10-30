from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.timed_events import remaining_time_label


def timed_processes(repository: SaveRepository) -> list[dict[str, Any]]:
    """Project stored timers and crafting stages for both the main tab and calendar."""
    events = repository.list_timed_events()
    processes = repository.get_setting("crafting.processes", [])
    if not processes:
        return events
    recipes = {recipe["id"]: recipe for recipe in repository.list_crafting_recipes()}
    current = repository.get_current_calendar_minute()
    for process in processes:
        due = process.get("passive_due_minute")
        recipe = recipes.get(process.get("recipe_id"), {})
        events.append({
            "name": recipe.get("name", "Crafting process"), "quantity": process.get("quantity", 1),
            "quantity_unit": "batches", "location": process.get("location", "Not recorded"),
            "state": ("Work required" if due is None else
                      "Ready for next stage" if current >= due else "Processing"),
            "ready_at_minute": due if due is not None else -1, "is_estimate": False, "category": "Crafting",
            "notes": f"Recipe stage {process.get('stage_index', 0) + 1}. Continue crafting to advance or collect the result.",
        })
    return events



class TimeSensitiveEventsScreen(RepositoryBackedWidget):
    """Processes, crafting timers and task deadlines in one main-tab table."""

    def __init__(self) -> None:
        super().__init__()
        self.tasks_table = _AppTableWidget(0, 6)
        self.tasks_table.setHorizontalHeaderLabels(
            ["Event", "Quantity", "Location", "Current State", "Time Remaining", "Details"]
        )
        self.tasks_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _use_soft_table_selection(self.tasks_table)
        _configure_wrapping_table(self.tasks_table, {0, 2, 5})
        self.tasks_table.horizontalHeader().setMinimumSectionSize(110)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tasks_table)

    def refresh(self) -> None:
        """Show independent processes, crafting timers and task deadlines together."""

        repository = self.repository()
        if repository is None:
            self.tasks_table.setRowCount(0)
            return
        tasks = repository.list_active_tasks()
        current = repository.get_current_calendar_minute()
        settings = repository.get_calendar_settings()
        rows = []
        tooltips = []
        for event in timed_processes(repository):
            due = event["ready_at_minute"]
            count = event["quantity"]
            quantity = "Unknown" if count < 0 else f"{count:g} {event.get('quantity_unit', '')}".strip()
            if count >= 0 and event.get("quantity_is_estimate", False):
                quantity = "~" + quantity
            state = event["state"]
            if event["is_estimate"] and 0 <= due <= current:
                state += " (estimated)"
            rows.append([
                event["name"], quantity, event["location"], state,
                remaining_time_label(due, current, estimated=event["is_estimate"]),
                event.get("notes", ""),
            ])
            milestone = build_calendar_snapshot(due, settings)["display_label"] if due >= 0 else "Unknown"
            tooltips.append(f"Type: {event['category']}\nMilestone: {milestone}")
        for task in tasks:
            due = task.get("due_elapsed_minutes", -1)
            remaining = ("Overdue" if 0 <= due < current else "Due now" if due == current
                         else remaining_time_label(due, current))
            details = "\n\n".join(value for value in (
                str(task.get("description", "")), str(task.get("notes", "")),
                f"Contact: {task['requester']}" if task.get("requester") not in (None, "", "N/A") else "",
                f"Reward: {task['reward']}" if task.get("reward") not in (None, "", "N/A") else "",
            ) if value)
            rows.append([task.get("name", ""), "—", task.get("location", ""), task.get("status", ""),
                         remaining, details or "No description recorded yet."])
            tooltips.append(f"Type: {task.get('category', 'Task')}\nDue: {task.get('due_date', 'N/A')}")
        self.tasks_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = _table_item(str(value))
                item.setToolTip(tooltips[row])
                self.tasks_table.setItem(row, column, item)
        _resize_wrapping_table_rows(self.tasks_table)

