import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.calendar import CalendarScreen
from ai_adventure.ui.screens.timed_events import TimeSensitiveEventsScreen
from tests.qt_test_case import QtTestCase


class TimedEventsUiTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_separate_crops_notes_countdowns_and_ready_state(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Garden calendar")
            repo.set_travel_locations([{"name": "Cottage Garden", "location_scope": "specific"}])
            repo.set_current_calendar_minute(480)
            repo.upsert_timed_event(name="Flax", location="Cottage Garden", quantity=3, quantity_unit="shoots",
                                    current_state="Growing", next_state="Mature", ready_at_minute=3360,
                                    notes="Composted today.")
            repo.upsert_timed_event(name="Cotton", location="Cottage Garden", quantity=6, quantity_unit="plants",
                                    quantity_is_estimate=True, current_state="Growing",
                                    next_state="Mature", notes="Assuming a small bed of six plants; timing unknown.")
            screen = TimeSensitiveEventsScreen()
            self.addCleanup(screen.deleteLater)
            self.addCleanup(screen.close)
            screen.set_repository(repo)
            self.assertEqual(screen.tasks_table.rowCount(), 2)
            rows = {screen.tasks_table.item(row, 0).text():
                    [screen.tasks_table.item(row, col).text() for col in range(6)] for row in range(2)}
            self.assertEqual(rows["Flax"][1:5], ["3 shoots", "Cottage Garden", "Growing", "~2 days"])
            self.assertEqual(rows["Flax"][5], "Composted today.")
            self.assertEqual(rows["Cotton"][1], "~6 plants")
            self.assertEqual(rows["Cotton"][4], "Unknown")
            calendar = CalendarScreen()
            self.addCleanup(calendar.deleteLater)
            self.addCleanup(calendar.close)
            calendar.set_repository(repo)
            calendar_text = "\n".join(calendar.table.item(row, col).text()
                                      for row in range(calendar.table.rowCount())
                                      for col in range(calendar.table.columnCount()))
            self.assertIn("Flax — Cottage Garden", calendar_text)
            self.assertNotIn("Cotton — Cottage Garden", calendar_text)
            repo.set_current_calendar_minute(3360)
            screen.refresh()
            flax_row = next(row for row in range(2) if screen.tasks_table.item(row, 0).text() == "Flax")
            self.assertEqual(screen.tasks_table.item(flax_row, 3).text(), "Mature (estimated)")
            self.assertEqual(screen.tasks_table.item(flax_row, 4).text(), "Expected ready")
            dated = CalendarScreen._task_deadline_events([
                {"name": "Flax", "due_elapsed_minutes": 3360, "description": "Growth milestone"}
            ], repo.get_calendar_settings())
            self.assertEqual(dated[0]["day"], 3)

    def test_existing_crafting_timer_and_task_are_visible_without_duplicate_records(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SaveRepository.create_new_save(Path(directory), "Timed work")
            repo.set_current_calendar_minute(60)
            repo.upsert_active_task(name="Deliver cloth", category="Commission", description="Bring cloth to Mira.",
                                    location="Market", due_elapsed_minutes=180)
            repo.set_setting("crafting.processes", [{"recipe_id": "ferment", "quantity": 2, "stage_index": 1,
                                                     "location": "Workshop", "passive_due_minute": 120}])
            screen = TimeSensitiveEventsScreen()
            self.addCleanup(screen.deleteLater)
            self.addCleanup(screen.close)
            screen.set_repository(repo)
            self.assertEqual(screen.tasks_table.rowCount(), 2)
            self.assertEqual(screen.tasks_table.item(0, 2).text(), "Workshop")
            self.assertEqual(screen.tasks_table.item(0, 4).text(), "1 hr")
            self.assertEqual(screen.tasks_table.item(1, 0).text(), "Deliver cloth")
            self.assertEqual(screen.tasks_table.item(1, 4).text(), "2 hr")
            self.assertEqual(repo.list_timed_events(), [])
