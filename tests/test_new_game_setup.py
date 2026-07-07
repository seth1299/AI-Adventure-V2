from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.new_game_setup import (
    CHARACTER_GENDER_PRESENTATION_HINTS,
    SKILL_LEVEL_PLAN,
    ai_generated_calendar_settings_or_fallback,
    build_new_game_setup_packet,
    calendar_looks_like_default_gregorian,
    fallback_introductory_message,
    fallback_world_summary,
    normalize_new_game_setup,
    parse_starter_items_text,
)
from ai_adventure.new_game_templates import (
    delete_new_game_template,
    load_new_game_template,
    load_new_game_templates,
    save_new_game_template,
)
from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import (
    DuplicateSaveTitleError,
    SaveRepository,
)


class NewGameSetupTests(unittest.TestCase):
    def test_normalized_setup_enforces_skill_spread_and_preserves_requested_items(self) -> None:
        setup = normalize_new_game_setup(
            {
                "title": "Mystery Save",
                "character": {"name": "Iris Vale"},
                "skills": [{"name": f"Skill {index}"} for index in range(15)],
                "starter_items": [{"name": "Notebook"}],
                "calendar": {"calendar_type": "gregorian"},
                "narration": {
                    "tense": "past",
                    "style": "third_person_omniscient",
                },
                "ai_settings": {
                    "model_intelligence": "smarter",
                    "model_tone": "serious",
                    "response_length": "brief",
                    "allowed_content_categories": [
                        "HARM_CATEGORY_DANGEROUS_CONTENT"
                    ],
                    "additional_context": "Keep the mystery grounded.",
                },
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
        self.assertEqual(setup["start_location_mode"], "suggestion")
        self.assertEqual(setup["calendar"]["month_names"][0], "January")
        self.assertEqual(setup["calendar"]["time_display"], "12_hour")
        self.assertEqual(setup["calendar"]["calendar_type"], "gregorian")
        self.assertFalse(setup["calendar"]["ai_generated"])
        self.assertEqual(setup["narration"]["tense"], "past")
        self.assertEqual(setup["narration"]["tense_label"], "Past Tense")
        self.assertEqual(setup["narration"]["style"], "third_person_omniscient")
        self.assertEqual(
            setup["narration"]["style_label"],
            "Third-Person Omniscient",
        )
        self.assertEqual(setup["ai_settings"]["model_intelligence"], "smarter")
        self.assertEqual(setup["ai_settings"]["model_tone"], "serious")
        self.assertEqual(setup["ai_settings"]["response_length"], "brief")
        self.assertEqual(
            setup["ai_settings"]["allowed_content_categories"],
            ["HARM_CATEGORY_DANGEROUS_CONTENT"],
        )
        self.assertIn("Keep the mystery grounded.", setup["ai_additional_context"])

    def test_start_location_mode_and_turn_prompt_are_model_visible(self) -> None:
        setup = normalize_new_game_setup(
            {
                "character": {"name": "Kit"},
                "start_location": "Kit's Abandoned Loft",
                "start_location_mode": "exactly this",
                "narration": {
                    "tense": "present",
                    "style": "third_person_limited",
                },
            }
        )
        packet = build_new_game_setup_packet(setup)

        self.assertEqual(setup["start_location_mode"], "exact")
        self.assertEqual(packet["setup"]["start_location_mode"], "exact")
        self.assertEqual(packet["turn_prompt"], "What does Kit do now?")
        self.assertIn("start_location_mode", packet["requirements"]["start_location"])
        self.assertIn("turn_prompt", packet["requirements"]["opening_scene"])

    def test_normalized_setup_preserves_explicit_sparse_skill_levels(self) -> None:
        setup = normalize_new_game_setup(
            {
                "skills": [
                    {
                        "name": "Observation",
                        "description": "Spotting small clues.",
                        "level": 5,
                    },
                    {
                        "name": "Deduction",
                        "description": "Connecting subtle evidence.",
                        "level": 3,
                    },
                ]
            }
        )

        self.assertEqual([skill["level"] for skill in setup["skills"]], SKILL_LEVEL_PLAN)
        self.assertEqual(setup["skills"][0]["name"], "Observation")
        self.assertEqual(setup["skills"][1]["name"], "")
        self.assertEqual(setup["skills"][2]["name"], "")
        self.assertEqual(setup["skills"][3]["name"], "Deduction")
        self.assertEqual(setup["skills"][3]["level"], 3)
        self.assertFalse(setup["skills"][3]["requires_ai_invention"])

    def test_normalized_setup_preserves_ai_generated_calendar_mode(self) -> None:
        setup = normalize_new_game_setup(
            {
                "calendar": {
                    "calendar_type": "ai_generated",
                    "time_display": "narrative",
                }
            }
        )

        self.assertEqual(setup["calendar"]["calendar_type"], "ai_generated")
        self.assertTrue(setup["calendar"]["ai_generated"])
        self.assertEqual(setup["calendar"]["time_display"], "narrative")
        self.assertNotEqual(setup["calendar"]["day_names"][0], "Monday")
        self.assertNotEqual(setup["calendar"]["month_names"][0], "January")
        self.assertFalse(calendar_looks_like_default_gregorian(setup["calendar"]))

    def test_ai_generated_calendar_fallback_rejects_default_gregorian_output(self) -> None:
        fallback = ai_generated_calendar_settings_or_fallback(
            {
                "days_per_week": 7,
                "weeks_per_month": 4,
                "months_per_year": 12,
                "seasons_per_year": 4,
                "day_names": [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ],
                "month_names": [
                    "January",
                    "February",
                    "March",
                    "April",
                    "May",
                    "June",
                    "July",
                    "August",
                    "September",
                    "October",
                    "November",
                    "December",
                ],
                "seasons": [
                    {"name": "Spring", "weather_hint": "spring"},
                    {"name": "Summer", "weather_hint": "summer"},
                    {"name": "Autumn", "weather_hint": "autumn"},
                    {"name": "Winter", "weather_hint": "winter"},
                ],
                "time_display": "12_hour",
            }
        )

        self.assertEqual(fallback["days_per_week"], 8)
        self.assertEqual(fallback["day_names"][0], "Dawn")
        self.assertEqual(fallback["month_names"][0], "First Rise")

    def test_parse_starter_items_text_supports_plain_and_structured_lines(self) -> None:
        items = parse_starter_items_text(
            "Notebook\nAn old brass lantern that only burns blue near danger.\nLantern | Tool | 2 | Hooded brass lantern | 15"
        )

        self.assertEqual(items[0]["name"], "Notebook")
        self.assertEqual(items[0]["quantity"], 1)
        self.assertFalse(items[0]["requires_ai_invention"])
        self.assertEqual(items[1]["name"], "")
        self.assertEqual(
            items[1]["item_request"],
            "An old brass lantern that only burns blue near danger.",
        )
        self.assertTrue(items[1]["requires_ai_invention"])
        self.assertEqual(items[2]["name"], "Lantern")
        self.assertEqual(items[2]["category"], "Tool")
        self.assertEqual(items[2]["quantity"], 2)
        self.assertEqual(items[2]["value_base_units"], 15)

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
                    "audio": {
                        "music_enabled": False,
                    "narrator_enabled": False,
                    "music_volume": 0,
                    "tts_volume": 35,
                    "tts_voice": "am_echo",
                    "tts_speed": 125,
                },
                    "narration": {
                        "tense": "future",
                        "style": "first_person_limited",
                    },
                    "ai_settings": {
                        "model_intelligence": "smarter",
                        "model_tone": "friendly",
                        "response_length": "descriptive",
                        "allowed_content_categories": [
                            "HARM_CATEGORY_HARASSMENT"
                        ],
                        "additional_context": "Keep clues internally consistent.",
                    },
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
            self.assertFalse(state.settings.values["audio.music_enabled"])
            self.assertFalse(state.settings.values["audio.narrator_enabled"])
            self.assertEqual(state.settings.values["audio.music_volume"], 0)
            self.assertEqual(state.settings.values["audio.tts_volume"], 35)
            self.assertEqual(state.settings.values["audio.tts_voice"], "am_echo")
            self.assertEqual(state.settings.values["audio.tts_speed"], 125)
            self.assertEqual(state.settings.values["ai.narration_tense"], "future")
            self.assertEqual(
                state.settings.values["ai.narration_style"],
                "first_person_limited",
            )
            self.assertEqual(
                state.settings.values["ai.model_intelligence"],
                "smarter",
            )
            self.assertEqual(state.settings.values["ai.model_tone"], "friendly")
            self.assertEqual(
                state.settings.values["ai.response_length"],
                "descriptive",
            )
            self.assertEqual(
                state.settings.values["ai.allowed_content_categories"],
                ["HARM_CATEGORY_HARASSMENT"],
            )
            self.assertIn(
                "Keep clues internally consistent.",
                state.settings.values["ai.additional_context"],
            )
            self.assertEqual(state.currency.denominations[1]["name"], "Crown")
            self.assertEqual(state.currency.denominations[1]["value"], 12)

    def test_create_new_save_rejects_duplicate_titles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            saves_dir = Path(temp_dir)
            first_repository = SaveRepository.create_new_save(
                saves_dir,
                "Duplicate Test",
            )
            first_repository.set_state_value("location", "First Save Only")

            with self.assertRaises(DuplicateSaveTitleError):
                SaveRepository.create_new_save(saves_dir, " duplicate   test ")

            saves = SaveRepository.list_saves(saves_dir)
            reloaded_repository = SaveRepository(saves[0].db_path)

            self.assertEqual(len(saves), 1)
            self.assertEqual(reloaded_repository.get_state_value("location"), "First Save Only")

    def test_create_new_save_uses_distinct_database_paths_for_unique_titles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            saves_dir = Path(temp_dir)

            first_repository = SaveRepository.create_new_save(saves_dir, "First Save")
            second_repository = SaveRepository.create_new_save(saves_dir, "Second Save")

            self.assertNotEqual(first_repository.db_path, second_repository.db_path)
            self.assertTrue(first_repository.db_path.exists())
            self.assertTrue(second_repository.db_path.exists())

    def test_create_new_save_skips_ai_item_requests_until_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            setup = normalize_new_game_setup(
                {
                    "title": "Item Request Test",
                    "starter_items": parse_starter_items_text(
                        "A compass that points toward unfinished promises."
                    ),
                }
            )
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                setup["title"],
                setup=setup,
            )

            self.assertEqual(repository.list_inventory_items(), [])

    def test_normalized_setup_accepts_raw_narrative_item_requests(self) -> None:
        setup = normalize_new_game_setup(
            {
                "starter_items": [
                    "Notebook",
                    "A compass that points toward unfinished promises.",
                ]
            }
        )

        self.assertEqual(setup["starter_items"][0]["name"], "Notebook")
        self.assertFalse(setup["starter_items"][0]["requires_ai_invention"])
        self.assertEqual(setup["starter_items"][1]["name"], "")
        self.assertEqual(
            setup["starter_items"][1]["item_request"],
            "A compass that points toward unfinished promises.",
        )
        self.assertTrue(setup["starter_items"][1]["requires_ai_invention"])

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

    def test_economy_examples_generate_currency_description_guidance(self) -> None:
        setup = normalize_new_game_setup(
            {
                "economy_examples": [
                    {"name": "Bread", "value_base_units": 2},
                    {"name": "Lantern Oil", "value": 7},
                    {"name": "", "value_base_units": 9},
                ],
            }
        )
        packet = build_new_game_setup_packet(setup)

        self.assertEqual(
            setup["economy_examples"],
            [
                {"name": "Bread", "value_base_units": 2},
                {"name": "Lantern Oil", "value_base_units": 7},
            ],
        )
        self.assertIn("Bread costs 2 base units", setup["currency_description"])
        self.assertIn("setup.economy_examples", packet["requirements"]["currency_generation"])
        self.assertIn(
            "setup.economy_examples",
            packet["requirements"]["starting_currency_balance"],
        )

    def test_starting_task_setup_supports_ai_and_custom_opening_quests(self) -> None:
        ai_setup = normalize_new_game_setup({"starting_task": {"mode": "ai"}})
        ai_packet = build_new_game_setup_packet(ai_setup)

        self.assertEqual(ai_setup["starting_task"]["mode"], "ai")
        self.assertTrue(ai_setup["starting_task"]["task"]["requires_ai_invention"])
        self.assertIn("opening quest/task", ai_packet["fields_requiring_ai_invention"])
        self.assertEqual(ai_packet["starting_task_contract"]["mode"], "ai")
        self.assertIn("ActiveTaskUpsertedEvent", ai_packet["requirements"]["starting_task"])

        custom_setup = normalize_new_game_setup(
            {
                "starting_task": {
                    "mode": "custom",
                    "task": {
                        "name": "Find the Canal Ledger",
                        "description": "Recover the missing tax ledger.",
                        "requester": "Archivist Pell",
                    },
                }
            }
        )
        custom_packet = build_new_game_setup_packet(custom_setup)

        self.assertEqual(custom_setup["starting_task"]["mode"], "custom")
        self.assertEqual(
            custom_setup["starting_task"]["task"]["name"],
            "Find the Canal Ledger",
        )
        self.assertEqual(custom_setup["starting_task"]["task"]["category"], "Quest")
        self.assertTrue(custom_setup["starting_task"]["task"]["requires_ai_invention"])
        self.assertIn(
            "blank starting quest/task fields",
            custom_packet["fields_requiring_ai_invention"],
        )
        self.assertEqual(
            custom_packet["starting_task_contract"]["task"]["requester"],
            "Archivist Pell",
        )

    def test_blank_currency_setup_is_reserved_for_ai_generation(self) -> None:
        setup = normalize_new_game_setup({})
        packet = build_new_game_setup_packet(setup)

        self.assertEqual(setup["currency_denominations"], [])
        self.assertEqual(setup["currency_description"], "")
        self.assertEqual(setup["economy_examples"], [])
        self.assertIn(
            "economy and currency denominations",
            packet["fields_requiring_ai_invention"],
        )
        self.assertEqual(packet["setup"]["narration"]["tense"], "present")
        self.assertEqual(
            packet["setup"]["narration"]["style"],
            "second_person_limited",
        )
        self.assertIn("narration_preferences", packet["requirements"])
        self.assertIn(
            "setup.narration.tense_label",
            packet["requirements"]["narration_preferences"],
        )
        self.assertEqual(
            packet["player_ai_preferences"]["model_intelligence"],
            "faster",
        )
        self.assertEqual(packet["player_ai_preferences"]["model_tone"], "neutral")
        self.assertEqual(
            packet["player_ai_preferences"]["response_length"],
            "normal",
        )
        self.assertIn("ai_modes", packet["requirements"])
        self.assertIn("currency_generation", packet["requirements"])
        self.assertIn("at least one and at most four", packet["requirements"]["currency_generation"])
        self.assertIn("value=1", packet["requirements"]["currency_generation"])
        self.assertIn("starting_currency_balance", packet["requirements"])
        self.assertIn(
            "starting_currency_balance_base_units",
            packet["requirements"]["starting_currency_balance"],
        )
        self.assertIn(
            "game_state/currency.balance",
            packet["requirements"]["starting_currency_balance"],
        )

    def test_new_game_templates_round_trip_multiple_normalized_setups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "new_game_templates.json"
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
            self.assertTrue(
                save_new_game_template(
                    template_path,
                    {
                        "title": "Space Test",
                        "character": {"name": "Nova"},
                        "skills": [{"name": f"Ship Skill {index}"} for index in range(15)],
                        "specified_genre": "Space opera",
                    },
                )
            )

            templates = load_new_game_templates(template_path)
            loaded = load_new_game_template(template_path)

            assert loaded is not None

            self.assertEqual([template.name for template in templates], ["Space Test", "Template Test"])
            self.assertEqual(templates[1].setup["title"], "Template Test")
            self.assertEqual(templates[1].setup["character"]["name"], "Iris Vale")
            self.assertEqual(templates[1].setup["starter_items"][0]["name"], "Notebook")
            self.assertEqual(templates[1].setup["calendar"]["time_display"], "24_hour")
            self.assertEqual(templates[1].setup["currency_denominations"][1]["name"], "Crown")
            self.assertEqual(templates[1].setup["specified_genre"], "Realistic detective mystery")
            self.assertEqual(loaded["title"], "Space Test")

    def test_new_game_templates_can_load_legacy_single_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "new_game_templates.json"
            legacy_template_path = Path(temp_dir) / "new_game_template.json"
            legacy_template_path.write_text(
                """
{
  "schema_version": 1,
  "setup": {
    "title": "Legacy Template",
    "character": {
      "name": "Iris Vale"
    }
  }
}
""".strip(),
                encoding="utf-8",
            )

            templates = load_new_game_templates(
                template_path,
                legacy_template_path=legacy_template_path,
            )

            self.assertEqual(len(templates), 1)
            self.assertEqual(templates[0].name, "Legacy Template")
            self.assertEqual(templates[0].setup["character"]["name"], "Iris Vale")

    def test_new_game_templates_can_store_partial_shells(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "new_game_templates.json"

            self.assertTrue(
                save_new_game_template(
                    template_path,
                    {
                        "title": "",
                        "character": {"name": ""},
                        "specified_genre": "Cozy mystery",
                    },
                    template_name="Cozy Shell",
                    normalize_setup=False,
                )
            )

            raw_templates = load_new_game_templates(template_path, normalize_setups=False)
            normalized_templates = load_new_game_templates(template_path)

            self.assertEqual(raw_templates[0].name, "Cozy Shell")
            self.assertEqual(raw_templates[0].setup["title"], "")
            self.assertEqual(raw_templates[0].setup["character"]["name"], "")
            self.assertEqual(normalized_templates[0].setup["title"], "New Adventure")
            self.assertEqual(normalized_templates[0].setup["character"]["name"], "Player Name")
            self.assertEqual(normalized_templates[0].setup["specified_genre"], "Cozy mystery")

            self.assertTrue(delete_new_game_template(template_path, "Cozy Shell"))
            self.assertEqual(load_new_game_templates(template_path, normalize_setups=False), [])

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
        self.assertIn("blank starting skill names", invention_fields)
        self.assertIn("blank starting skill descriptions", invention_fields)
        self.assertNotIn("starter inventory based on character and skills", invention_fields)
        self.assertIn("ai_invention_policy", packet["requirements"])
        self.assertIn("character_generation", packet["requirements"])
        self.assertIn("should default to male", packet["requirements"]["character_generation"])
        self.assertIn("preserve that field exactly", packet["requirements"]["character_generation"])
        self.assertIn("light Markdown", packet["requirements"]["world_summary"])
        self.assertIn("Light Markdown", packet["requirements"]["opening_scene"])
        self.assertIn("genre_generation", packet["requirements"])
        self.assertIn("Do not default to fantasy", packet["requirements"]["genre_generation"])
        self.assertIn("starting_location", packet["requirements"])
        self.assertIn("does not need to start in a tavern", packet["requirements"]["starting_location"])
        self.assertIn("short, broad place name", packet["requirements"]["starting_location"])
        self.assertIn("skill_generation", packet["requirements"])
        self.assertIn("copy that exact name", packet["requirements"]["skill_generation"])
        self.assertIn("generalized gameplay capabilities", packet["requirements"]["skill_generation"])
        self.assertIn("Lore (Syndicate)", packet["requirements"]["skill_generation"])
        self.assertIn("rather than Syndicate Lore", packet["requirements"]["skill_generation"])
        self.assertIn("currency_generation", packet["requirements"])
        self.assertIn("at least one and at most four", packet["requirements"]["currency_generation"])
        self.assertIn("mature_content", packet["requirements"])
        self.assertIn("adults of legal drinking age", packet["requirements"]["mature_content"])
        self.assertIn("drunken patrons", packet["requirements"]["mature_content"])
        self.assertIn("fictional in-world slurs", packet["requirements"]["mature_content"])
        self.assertIn("starting_currency_balance", packet["requirements"])
        self.assertIn(
            "game_state/currency.balance",
            packet["requirements"]["starting_currency_balance"],
        )
        self.assertIn("creative_ideas", packet["requirements"])
        self.assertIn("high-priority style seeds", packet["requirements"]["creative_ideas"])
        self.assertIn("hard exclusion list", packet["requirements"]["creative_ideas"])
        self.assertIn("scan every string key and value", packet["requirements"]["creative_ideas"])
        self.assertIn(
            "bare category labels as final proper nouns",
            packet["requirements"]["creative_ideas"],
        )
        self.assertIn("the Police Department", packet["requirements"]["creative_ideas"])
        self.assertIn("The Blue Wall", packet["requirements"]["creative_ideas"])
        self.assertIn("item_request", packet["requirements"]["starter_inventory"])
        self.assertIn("at least five", packet["requirements"]["starter_inventory"])
        self.assertIn("has no maximum count", packet["requirements"]["starter_inventory"])
        self.assertIn("starting_items", packet["requirements"]["starter_inventory"])
        self.assertIn("source_index", packet["requirements"]["starter_inventory"])
        self.assertIn("Fuel instead of Starting Fuel Amount", packet["requirements"]["starter_inventory"])
        self.assertIn("Put quantities in quantity, not name", packet["requirements"]["starter_inventory"])
        self.assertEqual(packet["starter_inventory_contract"]["requested_item_count"], 0)
        self.assertEqual(packet["starter_inventory_contract"]["minimum_finalized_item_count"], 5)
        self.assertEqual(
            packet["starter_inventory_contract"]["count_rule"],
            "At least 5 finalized starting items are required; there is no maximum starting item count.",
        )
        self.assertEqual(packet["starter_inventory_contract"]["output_field"], "starting_items")
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
        self.assertIn("calendar_generation", packet["requirements"])

    def test_setup_packet_marks_ai_generated_calendar_for_gemini(self) -> None:
        setup = normalize_new_game_setup({"calendar": {"calendar_type": "ai_generated"}})

        packet = build_new_game_setup_packet(setup)

        self.assertTrue(packet["setup"]["calendar"]["ai_generated"])
        self.assertEqual(packet["setup"]["calendar"]["calendar_type"], "ai_generated")
        self.assertIn("invent calendar_settings", packet["requirements"]["calendar_generation"])

    def test_setup_packet_does_not_require_large_requested_starter_inventory_count(self) -> None:
        setup = normalize_new_game_setup(
            {
                "starter_items": parse_starter_items_text(
                    "\n".join(
                        f"A specialized expedition item number {index}."
                        for index in range(12)
                    )
                )
            }
        )

        packet = build_new_game_setup_packet(setup)

        self.assertEqual(packet["starter_inventory_contract"]["requested_item_count"], 12)
        self.assertEqual(
            packet["starter_inventory_contract"]["count_rule"],
            "At least 5 finalized starting items are required; there is no maximum starting item count.",
        )
        self.assertIn(
            "has no maximum count",
            packet["requirements"]["starter_inventory"],
        )
        self.assertEqual(len(packet["setup"]["starter_items"]), 12)


if __name__ == "__main__":
    unittest.main()
