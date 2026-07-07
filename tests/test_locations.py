from __future__ import annotations

import unittest

from ai_adventure.locations import (
    KnownLocation,
    calculate_travel_estimate,
    clean_player_location_name,
    format_distance,
    format_travel_time,
    normalize_known_locations,
)


class LocationTests(unittest.TestCase):
    def test_clean_player_location_name_removes_scenic_details(self) -> None:
        self.assertEqual(
            clean_player_location_name(
                "Y/N's Office, high up near the penthouse, overlooking the Hudson River"
            ),
            "Y/N's Office",
        )
        self.assertEqual(
            clean_player_location_name("Dock 14 - upper gantry above the storm drain"),
            "Dock 14",
        )
        self.assertEqual(
            clean_player_location_name("Rainmarket Station overlooking the canal"),
            "Rainmarket Station",
        )

    def test_clean_player_location_name_preserves_simple_locations(self) -> None:
        self.assertEqual(clean_player_location_name("The Gilded Tankard"), "The Gilded Tankard")
        self.assertEqual(clean_player_location_name("Frozen Sea"), "Frozen Sea")

    def test_calculate_travel_estimate_uses_coordinates_speed_and_terrain(self) -> None:
        estimate = calculate_travel_estimate(
            KnownLocation(name="Origin", x_miles=0, y_miles=0),
            KnownLocation(
                name="Distant Farm",
                x_miles=3,
                y_miles=4,
                terrain="Muddy road",
                travel_multiplier=0.5,
            ),
            move_speed_mph=3,
            travel_mode="On Foot",
            speed_multiplier=1,
        )

        self.assertTrue(estimate.is_available)
        self.assertEqual(estimate.distance_miles, 5.0)
        self.assertEqual(estimate.effective_speed_mph, 1.5)
        self.assertEqual(estimate.estimated_minutes, 200)
        self.assertEqual(format_distance(estimate.distance_miles), "5.0 miles")
        self.assertEqual(format_travel_time(estimate.estimated_minutes), "About 3 hours 20 minutes")

    def test_missing_map_positions_do_not_invent_a_travel_estimate(self) -> None:
        estimate = calculate_travel_estimate(
            KnownLocation(name="Origin", x_miles=0, y_miles=0),
            KnownLocation(name="Unmapped Ruin"),
        )

        self.assertFalse(estimate.is_available)
        self.assertIsNone(estimate.distance_miles)
        self.assertIsNone(estimate.estimated_minutes)

    def test_normalize_known_locations_merges_duplicate_names(self) -> None:
        locations = normalize_known_locations(
            [
                {"name": "Old Road", "description": "A paved road."},
                {"name": "old road", "x_miles": 4, "y_miles": 2},
            ]
        )

        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0].description, "A paved road.")
        self.assertEqual((locations[0].x_miles, locations[0].y_miles), (4.0, 2.0))


if __name__ == "__main__":
    unittest.main()
