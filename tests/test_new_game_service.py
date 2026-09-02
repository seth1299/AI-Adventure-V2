from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.new_game_setup import normalize_new_game_setup


class NewGameServiceTests(unittest.TestCase):
    def test_commit_generated_world_persists_opening_and_player_identity(self) -> None:
        setup = normalize_new_game_setup(
            {
                "title": "Service Boundary Test",
                "character": {
                    "name": "Mara Stone",
                    "pronouns": "She/Her",
                    "backstory": "A careful investigator.",
                },
                "start_location_mode": "exact",
                "start_location": "The Harbor",
            }
        )
        result = SimpleNamespace(
            world_summary="Different Name stands at the AI Camp.",
            introductory_message="Different Name arrives at AI Camp.",
            finalized_character={
                "name": "Different Name",
                "pronouns": "He/Him",
                "backstory": "Generated backstory.",
            },
            start_location="AI Camp",
            start_weather="Rain",
            suggested_events=[],
            speaker_cues=[],
            sound_effect_cues=[],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = NewGameService.create_repository(
                Path(temp_dir),
                setup,
            )
            commit = NewGameService.commit_generated_world(
                repository,
                setup,
                result,
            )

            self.assertTrue(commit.message_id)
            self.assertEqual(repository.get_world_summary(), "Mara Stone stands at the AI Camp.")
            self.assertEqual(repository.get_setting("player_name"), "Mara Stone")
            self.assertEqual(repository.get_setting("player.pronouns"), "She/Her")
            self.assertEqual(repository.get_state_value("location"), "The Harbor")
            self.assertEqual(repository.get_state_value("weather"), "Rain")
            self.assertEqual(
                repository.list_history()[-1]["content"],
                "Mara Stone arrives at The Harbor.",
            )


if __name__ == "__main__":
    unittest.main()
