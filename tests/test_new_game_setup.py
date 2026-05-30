from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.new_game_setup import (
    CHARACTER_GENDER_PRESENTATION_HINTS,
    SKILL_LEVEL_PLAN,
    build_new_game_setup_packet,
    fallback_introductory_message,
    fallback_world_summary,
    normalize_new_game_setup,
    parse_starter_items_text,
)
from ai_adventure.new_game_templates import (
    load_new_game_template,
    save_new_game_template,
)
from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository


class NewGameSetupTests(unittest.TestCase):
    def test_normalized_setup_enforces_skill_spread_and_preserves_requested_items(self) -> None:
        setup = normalize_new_game_setup(
            {
                "title": "Mystery Save",
                "character": {"name": "Iris Vale"},
                "skills": [{"name": f"Skill {index}"} for index in range(15)],
                "starter_items": [{"name": "Notebook"}],
                "calendar": {"calendar_type": "gregorian"},
                "specified_genre": "Realistic detective mystery",
                "start_location": "Rainmarket Station",
            }
        )

        self.assertEqual([skill["level"] for skill in setup["skills"]], SKILL_LEVEL_PLAN)
        self.assertEqual(setup["skills"][0]["name"], "Skill 0")
        self.assertEqual(setup["skills"][0]["description"], "")
        self.assertTrue(setup["skills"][0]["requires_ai_invention"])
        self.assertEqual(len(setup["starter_items"]), 1)
        self.assertEqual(setup["specified_genre"], "Realistic detective mystery")
        self.assertEqual(setup["calendar"]["month_names"][0], "January")
        self.assertEqual(setup["calendar"]["time_display"], "12_hour")

    def test_parse_starter_items_text_supports_plain_and_structured_lines(self) -> None:
        items = parse_starter_items_text(
            "Notebook\nLantern | Tool | 2 | Hooded brass lantern | 15"
        )

        self.assertEqual(items[0]["name"], "Notebook")
        self.assertEqual(items[0]["quantity"], 1)
        self.assertEqual(items[1]["name"], "Lantern")
        self.assertEqual(items[1]["category"], "Tool")
        self.assertEqual(items[1]["quantity"], 2)
        self.assertEqual(items[1]["value_base_units"], 15)

    def test_create_new_save_with_setup_persists_player_choices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            setup = normalize_new_game_setup(
                {
                    "title": "Detective Test",
                    "character": {
                        "name": "Iris Vale",
                        "appearance": "A careful detective in a rain-dark coat.",
                    },
                    "skills": [{"name": f"Skill {index}"} for index in range(15)],
                    "starter_items": [{"name": "Notebook"}],
                    "currency_denominations": [
                        {"name": "Bit", "plural_name": "Bits", "value": 1},
                        {"name": "Crown", "plural_name": "Crowns", "value": 12},
                    ],
                    "currency_description": "Crowns dominate city trade.",
                    "specified_genre": "Realistic detective mystery",
                    "game_style": "Realistic detective mystery",
                    "start_location": "Rainmarket Station",
                    "world_context": "The city is controlled by canal guilds.",
                }
            )
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                setup["title"],
                setup=setup,
            )
            state = StateManager(repository).load_state()

            self.assertEqual(state.metadata.title, "Detective Test")
            self.assertEqual(state.player.name, "Iris Vale")
            self.assertEqual(state.player.appearance, "A careful detective in a rain-dark coat.")
            self.assertEqual(state.world.location, "Rainmarket Station")
            self.assertEqual(state.calendar.time_display, "12_hour")
            self.assertEqual(len(state.inventory.items), 1)
            self.assertEqual(len(state.skills.skills), 15)
            self.assertEqual(
                state.settings.values["world.genre"],
                "Realistic detective mystery",
            )
            self.assertIn(
                "Specified genre: Realistic detective mystery",
                state.settings.values["ai.additional_context"],
            )
            self.assertEqual(
                state.settings.values["currency.description"],
                "Crowns dominate city trade.",
            )
            self.assertTrue(state.settings.values["audio.music_enabled"])
            self.assertTrue(state.settings.values["audio.narrator_enabled"])
            self.assertEqual(state.settings.values["audio.music_volume"], 25)
            self.assertEqual(state.settings.values["audio.tts_volume"], 90)
            repository.set_world_lore(
                {
                    "Locations": {
                        "Rainmarket Station": "Rainmarket Station is the central rail terminal."
                    },
                    "Economy": {"Crowns": "Crowns dominate city trade."},
                }
            )
            self.assertEqual(
                repository.get_world_lore()["Locations"]["Rainmarket Station"],
                "Rainmarket Station is the central rail terminal.",
            )
            repository.set_world_lore(
                {"Locations": ["Old Entry: Converted from legacy list lore."]}
            )
            self.assertEqual(
                repository.get_world_lore()["Locations"]["Old Entry"],
                "Old Entry: Converted from legacy list lore.",
            )
            self.assertEqual(state.currency.denominations[1]["name"], "Crown")
            self.assertEqual(state.currency.denominations[1]["value"], 12)

    def test_currency_description_defaults_to_structured_denominations(self) -> None:
        setup = normalize_new_game_setup(
            {
                "currency_denominations": [
                    {"name": "Bit", "plural_name": "Bits", "value": 1},
                    {"name": "Crown", "plural_name": "Crowns", "value": 12},
                ],
            }
        )

        self.assertEqual(setup["currency_denominations"][1]["name"], "Crown")
        self.assertIn("Crown (12 base units)", setup["currency_description"])

    def test_blank_currency_setup_is_reserved_for_ai_generation(self) -> None:
        setup = normalize_new_game_setup({})
        packet = build_new_game_setup_packet(setup)

        self.assertEqual(setup["currency_denominations"], [])
        self.assertEqual(setup["currency_description"], "")
        self.assertIn(
            "economy and currency denominations",
            packet["fields_requiring_ai_invention"],
        )
        self.assertIn("currency_generation", packet["requirements"])
        self.assertIn("at least one and at most four", packet["requirements"]["currency_generation"])
        self.assertIn("value=1", packet["requirements"]["currency_generation"])

    def test_new_game_template_round_trips_normalized_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "new_game_template.json"
            setup = normalize_new_game_setup(
                {
                    "title": "Template Test",
                    "character": {
                        "name": "Iris Vale",
                        "appearance": "Rain-dark coat.",
                    },
                    "skills": [{"name": f"Skill {index}"} for index in range(15)],
                    "starter_items": [
                        {
                            "name": "Notebook",
                            "category": "Tool",
                            "quantity": 1,
                            "description": "Case notes.",
                            "value_base_units": 4,
                        }
                    ],
                    "calendar": {"calendar_type": "gregorian", "time_display": "24_hour"},
                    "currency_denominations": [
                        {"name": "Bit", "plural_name": "Bits", "value": 1},
                        {"name": "Crown", "plural_name": "Crowns", "value": 12},
                    ],
                    "currency_description": "Crowns dominate city trade.",
                    "specified_genre": "Realistic detective mystery",
                    "game_style": "Quiet investigation.",
                    "start_location": "Rainmarket Station",
                    "world_context": "Canal guilds control the docks.",
                }
            )

            self.assertTrue(save_new_game_template(template_path, setup))

            loaded = load_new_game_template(template_path)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["title"], "Template Test")
            self.assertEqual(loaded["character"]["name"], "Iris Vale")
            self.assertEqual(loaded["starter_items"][0]["name"], "Notebook")
            self.assertEqual(loaded["calendar"]["time_display"], "24_hour")
            self.assertEqual(loaded["currency_denominations"][1]["name"], "Crown")
            self.assertEqual(loaded["specified_genre"], "Realistic detective mystery")

    def test_repository_can_replace_setup_inventory_with_ai_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            setup = normalize_new_game_setup(
                {
                    "title": "Inventory Test",
                    "starter_items": [{"name": "Notebook"}],
                }
            )
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                setup["title"],
                setup=setup,
            )

            repository.replace_inventory_items(
                [
                    {
                        "name": "Case Notebook",
                        "category": "Tool",
                        "quantity": 1,
                        "description": "A notebook keyed to the opening case.",
                        "value_base_units": 4,
                    },
                    {
                        "name": "Rain-Dark Coat",
                        "category": "Clothing",
                        "quantity": 1,
                        "description": "A coat suited to canal weather.",
                        "value_base_units": 25,
                    },
                ]
            )

            item_names = {item["name"] for item in repository.list_inventory_items()}

            self.assertEqual(item_names, {"Case Notebook", "Rain-Dark Coat"})

    def test_world_setup_packet_and_fallbacks_are_available_without_ai(self) -> None:
        setup = normalize_new_game_setup(
            {
                "title": "Fallback Test",
                "character": {"name": "Iris Vale"},
                "game_style": "Realistic detective mystery",
                "start_location": "Rainmarket Station",
            }
        )

        packet = build_new_game_setup_packet(setup)
        world_summary = fallback_world_summary(setup)
        intro = fallback_introductory_message(setup)

        self.assertEqual(packet["packet_type"], "new_game_setup")
        self.assertIn("Rainmarket Station", world_summary)
        self.assertIn("Realistic detective mystery", world_summary)
        self.assertTrue(intro.endswith("What do you do now?"))

    def test_setup_packet_marks_defaults_as_requiring_ai_invention(self) -> None:
        packet = build_new_game_setup_packet(
            normalize_new_game_setup({}),
            valid_music_tracks=["Town Village City.mp3"],
        )

        invention_fields = packet["fields_requiring_ai_invention"]

        self.assertIn("character name", invention_fields)
        self.assertIn("specific starting location", invention_fields)
        self.assertIn("specific genre or premise", invention_fields)
        self.assertIn("world context, factions, religions, and locations", invention_fields)
        self.assertIn("distinct starting skill identities", invention_fields)
        self.assertIn("starter inventory based on character and skills", invention_fields)
        self.assertIn("ai_invention_policy", packet["requirements"])
        self.assertIn("character_generation", packet["requirements"])
        self.assertIn("should default to male", packet["requirements"]["character_generation"])
        self.assertIn("genre_generation", packet["requirements"])
        self.assertIn("Do not default to fantasy", packet["requirements"]["genre_generation"])
        self.assertIn("starting_location", packet["requirements"])
        self.assertIn("does not need to start in a tavern", packet["requirements"]["starting_location"])
        self.assertIn("short, broad place name", packet["requirements"]["starting_location"])
        self.assertIn("skill_generation", packet["requirements"])
        self.assertIn("requires_ai_invention=true", packet["requirements"]["skill_generation"])
        self.assertIn("generalized gameplay capabilities", packet["requirements"]["skill_generation"])
        self.assertIn("Lore (Syndicate)", packet["requirements"]["skill_generation"])
        self.assertIn("rather than Syndicate Lore", packet["requirements"]["skill_generation"])
        self.assertIn("currency_generation", packet["requirements"])
        self.assertIn("at least one and at most four", packet["requirements"]["currency_generation"])
        self.assertIn("creative_ideas", packet["requirements"])
        self.assertIn("high-priority style seeds", packet["requirements"]["creative_ideas"])
        self.assertIn("creative_ideas", packet)
        self.assertIn("character_generation_guidance", packet)
        self.assertIn(
            packet["character_generation_guidance"]["gender_presentation_hint"],
            CHARACTER_GENDER_PRESENTATION_HINTS,
        )
        self.assertIn("genre_generation_guidance", packet)
        self.assertTrue(packet["genre_generation_guidance"]["genre_hint"])
        self.assertEqual(packet["setup"]["specified_genre"], "")
        self.assertEqual(packet["setup"]["start_location"], "")
        self.assertEqual(packet["setup"]["starter_items"], [])
        self.assertEqual(packet["setup"]["skills"][0]["name"], "")
        self.assertEqual(packet["setup"]["skills"][0]["description"], "")
        self.assertTrue(packet["setup"]["skills"][0]["requires_ai_invention"])
        self.assertIn(
            "player_character_name_examples",
            packet["creative_ideas"],
        )
        self.assertGreater(
            len(packet["creative_ideas"]["player_character_name_examples"]["ideas"]),
            1,
        )
        self.assertGreater(len(packet["creative_ideas"]["categories"]), 1)
        self.assertIn("Alden", packet["creative_ideas"]["banned_terms"])
        self.assertNotIn(
            "Alden",
            packet["creative_ideas"]["player_character_name_examples"]["ideas"],
        )
        self.assertNotIn(
            "Alden",
            {
                idea
                for category in packet["creative_ideas"]["categories"]
                for idea in category["ideas"]
            },
        )
        self.assertIn("starting_music", packet["requirements"])
        self.assertEqual(packet["audio"]["valid_music_tracks"], ["Town Village City.mp3"])
        self.assertEqual(packet["current_calendar"]["season_hint"], "spring")
        self.assertEqual(packet["current_weather"], "Clear")
        self.assertIn("calendar_weather_consistency", packet["requirements"])


if __name__ == "__main__":
    unittest.main()
