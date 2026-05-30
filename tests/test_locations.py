from __future__ import annotations

import unittest

from ai_adventure.locations import clean_player_location_name


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


if __name__ == "__main__":
    unittest.main()
