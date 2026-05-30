from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.calendar_system import (
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    build_month_grid,
    format_time_of_day,
    normalize_calendar_settings,
    resolve_starting_elapsed_minutes,
)
from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository


class CalendarSystemTests(unittest.TestCase):
    def test_default_snapshot_starts_in_first_month_morning(self) -> None:
        snapshot = build_calendar_snapshot(DEFAULT_START_ELAPSED_MINUTES)

        self.assertEqual(snapshot["date_label"], "Monday, Month 1 1, Year 1")
        self.assertEqual(snapshot["time_label"], "Morning")
        self.assertEqual(snapshot["season_name"], "Spring")
        self.assertEqual(snapshot["season_hint"], "spring")

    def test_formats_12_and_24_hour_time(self) -> None:
        self.assertEqual(format_time_of_day(13 * 60, "24_hour"), "13:00")
        self.assertEqual(format_time_of_day(7 * 60, "12_hour"), "7:00 A.M.")

    def test_resolves_ai_selected_starting_season(self) -> None:
        elapsed_minutes = resolve_starting_elapsed_minutes(
            {
                "season_hint": "autumn",
                "day_of_month": 1,
                "time_of_day_minutes": 20 * 60,
            }
        )
        snapshot = build_calendar_snapshot(elapsed_minutes)

        self.assertEqual(snapshot["season_hint"], "autumn")
        self.assertEqual(snapshot["month_name"], "Month 7")
        self.assertEqual(snapshot["day_of_month"], 1)
        self.assertEqual(snapshot["time_label"], "Evening")

    def test_normalizes_custom_calendar_settings(self) -> None:
        settings = normalize_calendar_settings(
            {
                "days_per_week": 5,
                "weeks_per_month": 3,
                "months_per_year": 2,
                "seasons_per_year": 2,
                "day_names": ["Firstday"],
                "month_names": ["Greenwane", "Goldwane"],
                "seasons": [
                    {"name": "Greening", "weather_hint": "spring"},
                    {"name": "Harvest", "weather_hint": "autumn"},
                ],
                "time_display": "24_hour",
            }
        )

        self.assertEqual(settings["days_per_week"], 5)
        self.assertEqual(settings["day_names"][0], "Firstday")
        self.assertEqual(settings["day_names"][4], "Friday")
        self.assertEqual(settings["month_names"], ["Greenwane", "Goldwane"])
        self.assertEqual(settings["seasons"][1]["weather_hint"], "autumn")
        self.assertEqual(settings["time_display"], "24_hour")

    def test_month_grid_marks_current_day(self) -> None:
        snapshot = build_calendar_snapshot(DEFAULT_START_ELAPSED_MINUTES)
        grid = build_month_grid(snapshot)
        current_cells = [
            cell
            for row in grid["rows"]
            for cell in row
            if cell["is_current_day"]
        ]

        self.assertEqual(len(current_cells), 1)
        self.assertEqual(current_cells[0]["day_of_month"], 1)

    def test_save_calendar_settings_feed_state_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Calendar Test")
            repository.set_calendar_settings(
                {
                    "days_per_week": 5,
                    "weeks_per_month": 3,
                    "months_per_year": 2,
                    "seasons_per_year": 2,
                    "day_names": ["A", "B", "C", "D", "E"],
                    "month_names": ["Bloom", "Frost"],
                    "seasons": [
                        {"name": "Warmrise", "weather_hint": "spring"},
                        {"name": "Coldfall", "weather_hint": "winter"},
                    ],
                    "time_display": "12_hour",
                }
            )

            state = StateManager(repository).load_state()

            self.assertEqual(state.calendar.days_per_week, 5)
            self.assertEqual(state.calendar.month_name, "Bloom")
            self.assertEqual(state.calendar.time_label, "8:00 A.M.")


if __name__ == "__main__":
    unittest.main()
