from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.new_game_setup import (
    DEFAULT_CHARACTER_PRONOUNS,
    DEFAULT_STARTING_WEALTH_GUIDANCE,
    SKILL_LEVEL_PLAN,
    SKILL_PRESET_LEVEL_PLANS,
    ai_generated_calendar_settings_or_fallback,
    build_new_game_setup_packet,
    calendar_looks_like_generic_fantasy_artisan,
    calendar_looks_like_default_gregorian,
    fallback_introductory_message,
    fallback_world_summary,
    merge_authoritative_starting_calendar,
    normalize_new_game_setup,
    normalize_character_pronouns,
    parse_starter_items_text,
)
from ai_adventure.new_game_templates import (
    available_automatic_template_name,
    delete_new_game_template,
    load_new_game_template,
    load_new_game_templates,
    save_new_game_template,
    template_setup_has_changes,
)
from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import (
    DuplicateSaveTitleError,
    SaveRepository,
)


class NewGameSetupTests(unittest.TestCase):
    def test_character_pronouns_are_normalized_as_canonical_setup_data(self) -> None:
        self.assertEqual(
            normalize_new_game_setup({})["character"]["pronouns"],
            DEFAULT_CHARACTER_PRONOUNS,
        )
        self.assertEqual(normalize_character_pronouns("she/her"), "She/Her")
        self.assertEqual(normalize_character_pronouns("Xe/Xem"), "Xe/Xem")

    def test_starting_party_keeps_only_ids_from_the_starting_npc_list(self) -> None:
        setup = normalize_new_game_setup(
            {
                "starting_npcs": [
                    {"npc_id": "npc_mira", "name": "Mira"},
                    {"npc_id": "npc_orin", "name": "Orin"},
                ],
                "starting_party_npc_ids": [
                    "npc_mira",
                    "deleted_npc",
                    "npc_mira",
                ],
            }
        )

        self.assertEqual(
            [npc["npc_id"] for npc in setup["starting_npcs"]],
            ["npc_mira", "npc_orin"],
        )
        self.assertEqual(setup["starting_party_npc_ids"], ["npc_mira"])
        packet = build_new_game_setup_packet(setup)
        self.assertIn(
            "setup.starting_party_npc_ids",
            packet["requirements"]["events"],
        )
        self.assertIn("copy its npc_id exactly", packet["requirements"]["events"])

    def test_normalize_new_game_setup_preserves_starter_storage_location(self) -> None:
        setup = normalize_new_game_setup(
            {
                "starter_items": [
                    {
                        "name": "Loaded Revolver",
                        "storage_location": "Detective's Car",
                    }
                ]
            }
        )

        self.assertEqual(
            setup["starter_items"][0]["storage_location"],
            "Detective's Car",
        )

    def test_starting_skill_presets_control_exact_level_plan(self) -> None:
        for preset, expected_plan in SKILL_PRESET_LEVEL_PLANS.items():
            with self.subTest(preset=preset):
                setup = normalize_new_game_setup({"skill_preset": preset})
                self.assertEqual(setup["skill_level_plan"], expected_plan)
                self.assertEqual(
                    [skill["level"] for skill in setup["skills"]],
                    expected_plan,
                )

    def test_custom_starting_skills_allow_any_level_mix(self) -> None:
        setup = normalize_new_game_setup(
            {
                "skill_preset": "custom",
                "skill_level_plan": [5, 5, 2, 1, 1, 1],
                "skills": [
                    {"name": f"Custom Skill {index}", "description": "Useful capability."}
                    for index in range(6)
                ],
            }
        )
        self.assertEqual([skill["level"] for skill in setup["skills"]], [5, 5, 2, 1, 1, 1])

    def test_blank_slate_has_no_starting_skill_slots(self) -> None:
        setup = normalize_new_game_setup(
            {"skill_preset": "blank", "skills": [{"name": "Should Be Removed"}]}
        )
        self.assertEqual(setup["skills"], [])
        self.assertEqual(setup["skill_level_plan"], [])

    def test_normalized_setup_enforces_skill_spread_and_preserves_requested_items(self) -> None:
        setup = normalize_new_game_setup(
            {
                "title": "Mystery Save",
                "character": {"name": "Iris Vale"},
                "skills": [{"name": f"Skill {index}"} for index in range(15)],
                "starter_items": [{"name": "Notebook"}],
                "starting_locations": [
                    {
                        "name": "Rainmarket Station",
                        "description": "A canal station under an old clock.",
                        "location_mode": "exact",
                    },
                    {
                        "name": "Blacksmith Shop",
                        "description": "A forge inside the station concourse.",
                        "is_sublocation": True,
                        "parent_location": "Rainmarket Station",
                    }
                ],
                "starting_npcs": [
                    {
                        "name": "Quartermaster Vale",
                        "location": "Rainmarket Station",
                        "description": "Sells practical travel supplies.",
                        "description_mode": "exact",
                    }
                ],
                "calendar": {"calendar_type": "gregorian"},
                "narration": {
                    "tense": "past",
                    "style": "third_person_omniscient",
                },
                "ai_settings": {
                    "text_model": "gemini-3.7-flash",
                    "model_intelligence": "smarter",
                    "model_tone": "serious",
                    "response_length": "brief",
                    "allowed_content_categories": [
                        "HARM_CATEGORY_DANGEROUS_CONTENT"
                    ],
                    "additional_context": "Keep the mystery grounded.",
                },
                "images": {
                    "enabled": False,
                    "model": "gemini-3-pro-image",
                    "style": "film_noir",
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
        self.assertEqual(len(setup["starting_locations"]), 2)
        self.assertEqual(setup["starting_locations"][0]["name"], "Rainmarket Station")
        self.assertEqual(setup["starting_locations"][0]["location_mode"], "exact")
        self.assertFalse(setup["starting_locations"][0]["requires_ai_invention"])
        self.assertTrue(setup["starting_locations"][1]["is_sublocation"])
        self.assertEqual(
            setup["starting_locations"][1]["parent_location"],
            "Rainmarket Station",
        )
        self.assertEqual(len(setup["starting_npcs"]), 1)
        self.assertEqual(setup["starting_npcs"][0]["name"], "Quartermaster Vale")
        self.assertEqual(setup["starting_npcs"][0]["description_mode"], "exact")
        self.assertFalse(setup["starting_npcs"][0]["requires_ai_invention"])
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
        self.assertEqual(setup["ai_settings"]["text_model"], "gemini-3.7-flash")
        self.assertEqual(setup["ai_settings"]["model_tone"], "serious")
        self.assertEqual(setup["ai_settings"]["response_length"], "brief")
        self.assertEqual(
            setup["ai_settings"]["allowed_content_categories"],
            ["HARM_CATEGORY_DANGEROUS_CONTENT"],
        )
        self.assertIn("Keep the mystery grounded.", setup["ai_additional_context"])
        self.assertEqual(
            setup["images"],
            {
                "enabled": False,
                "model": "gemini-3-pro-image",
                "style": "film_noir",
            },
        )

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
        self.assertEqual(fallback["month_names"][0], "First Light")

    def test_ai_generated_calendar_fallback_rejects_any_gregorian_weekday(self) -> None:
        fallback = ai_generated_calendar_settings_or_fallback(
            {
                "days_per_week": 7,
                "weeks_per_month": 4,
                "months_per_year": 4,
                "seasons_per_year": 2,
                "day_names": [
                    "Solday",
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Satsday",
                ],
                "month_names": ["Firstbloom", "Highsun", "Leafturn", "Longnight"],
                "seasons": [
                    {"name": "Bloom", "weather_hint": "spring"},
                    {"name": "Frost", "weather_hint": "winter"},
                ],
                "time_display": "narrative",
            }
        )

        self.assertEqual(fallback["day_names"][0], "Dawn")
        self.assertTrue(
            {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}.isdisjoint(
                fallback["day_names"]
            )
        )

    def test_ai_generated_calendar_fallback_rejects_artisan_names_for_sci_fi(self) -> None:
        raw_calendar = {
            "days_per_week": 8,
            "weeks_per_month": 5,
            "months_per_year": 10,
            "seasons_per_year": 5,
            "day_names": [
                "Dawn",
                "Bell",
                "Hearth",
                "Market",
                "Lantern",
                "Tide",
                "Star",
                "Rest",
            ],
            "month_names": [
                "First Rise",
                "Greenwake",
                "Highsun",
                "Goldleaf",
                "Longshade",
                "Deepfrost",
                "Raincall",
                "Bloomturn",
                "Redharvest",
                "Yearsend",
            ],
            "seasons": [
                {"name": "Waking", "weather_hint": "spring"},
                {"name": "Highlight", "weather_hint": "summer"},
                {"name": "Harvest", "weather_hint": "autumn"},
                {"name": "Frost", "weather_hint": "winter"},
                {"name": "Rainmoot", "weather_hint": "rainy"},
            ],
            "time_display": "12_hour",
        }

        fallback = ai_generated_calendar_settings_or_fallback(
            raw_calendar,
            genre_hint="Futuristic sci-fi crash landing on an unknown alien planet.",
        )

        self.assertTrue(calendar_looks_like_generic_fantasy_artisan(raw_calendar))
        self.assertEqual(fallback["day_names"][0], "Launch")
        self.assertEqual(fallback["month_names"][0], "Perihelion")
        self.assertNotIn("Hearth", fallback["day_names"])
        self.assertNotIn("Market", fallback["day_names"])

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
                        "pronouns": "She/Her",
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
                        "text_model": "gemini-3.6-flash",
                        "model_intelligence": "smarter",
                        "model_tone": "friendly",
                        "response_length": "descriptive",
                        "allowed_content_categories": [
                            "HARM_CATEGORY_HARASSMENT"
                        ],
                        "additional_context": "Keep clues internally consistent.",
                    },
                    "images": {
                        "enabled": False,
                        "model": "gemini-3.1-flash-image",
                        "style": "crayon",
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
            self.assertEqual(state.player.pronouns, "She/Her")
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
            self.assertEqual(
                state.settings.values["ai.text_model"],
                "gemini-3.6-flash",
            )
            self.assertFalse(state.settings.values["images.enabled"])
            self.assertEqual(
                state.settings.values["images.model"],
                "gemini-3.1-flash-image",
            )
            self.assertEqual(state.settings.values["images.style"], "crayon")
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

    def test_list_saves_does_not_update_database_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            saves_dir = Path(temp_dir)
            repository = SaveRepository.create_new_save(saves_dir, "Timestamp Test")
            db_path = repository.db_path
            before = db_path.stat().st_mtime_ns

            saves = SaveRepository.list_saves(saves_dir)

            self.assertEqual(len(saves), 1)
            self.assertEqual(saves[0].title, "Timestamp Test")
            self.assertEqual(db_path.stat().st_mtime_ns, before)

    def test_read_save_setting_does_not_update_database_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            saves_dir = Path(temp_dir)
            repository = SaveRepository.create_new_save(saves_dir, "Theme Test")
            repository.set_setting("theme", "Dark")
            db_path = repository.db_path
            before = db_path.stat().st_mtime_ns

            theme = SaveRepository.read_save_setting(db_path, "theme", "Light")

            self.assertEqual(theme, "Dark")
            self.assertEqual(db_path.stat().st_mtime_ns, before)

    def test_save_repository_renames_and_deletes_existing_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            saves_dir = Path(temp_dir)
            repository = SaveRepository.create_new_save(saves_dir, "Old Save")
            db_path = repository.db_path
            old_save_dir = db_path.parent

            renamed_db_path = SaveRepository.rename_save(saves_dir, db_path, "New Save")

            self.assertFalse(old_save_dir.exists())
            self.assertNotEqual(renamed_db_path.parent, old_save_dir)
            self.assertIn("New_Save", renamed_db_path.parent.name)
            self.assertTrue(renamed_db_path.exists())
            renamed_repository = SaveRepository(renamed_db_path)
            self.assertEqual(renamed_repository.get_meta("title"), "New Save")
            self.assertTrue(SaveRepository.save_title_exists(saves_dir, "new save"))

            SaveRepository.delete_save(saves_dir, renamed_db_path)

            self.assertFalse(renamed_db_path.exists())
            self.assertEqual(SaveRepository.list_saves(saves_dir), [])

    def test_save_repository_rename_rejects_duplicate_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            saves_dir = Path(temp_dir)
            first_repository = SaveRepository.create_new_save(saves_dir, "First Save")
            SaveRepository.create_new_save(saves_dir, "Second Save")

            with self.assertRaises(DuplicateSaveTitleError):
                SaveRepository.rename_save(
                    saves_dir,
                    first_repository.db_path,
                    " second   save ",
                )

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

    def test_starting_wealth_basic_guidance_requires_ai_interpretation(self) -> None:
        setup = normalize_new_game_setup(
            {
                "starting_wealth": {
                    "mode": "basic",
                    "guidance": "Enough money for a room and three meals.",
                }
            }
        )
        packet = build_new_game_setup_packet(setup)

        self.assertEqual(setup["starting_wealth"]["mode"], "basic")
        self.assertEqual(
            setup["starting_wealth"]["guidance"],
            "Enough money for a room and three meals.",
        )
        self.assertIsNone(setup["starting_wealth"]["balance_base_units"])
        self.assertTrue(setup["starting_wealth"]["requires_ai_invention"])
        self.assertIn(
            "starting wealth from player guidance",
            packet["fields_requiring_ai_invention"],
        )
        self.assertEqual(packet["starting_wealth_contract"]["mode"], "basic")

        default_setup = normalize_new_game_setup({})
        self.assertEqual(
            default_setup["starting_wealth"]["guidance"],
            DEFAULT_STARTING_WEALTH_GUIDANCE,
        )

    def test_starting_wealth_advanced_calculates_and_persists_exact_balance(self) -> None:
        raw_setup = {
            "title": "Exact Wealth Test",
            "currency_denominations": [
                {"name": "Bit", "plural_name": "Bits", "value": 1},
                {"name": "Crown", "plural_name": "Crowns", "value": 12},
            ],
            "starting_wealth": {
                "mode": "advanced",
                "amounts": [
                    {"denomination_name": "Crown", "quantity": 3},
                    {"denomination_value": 1, "quantity": 4},
                ],
            },
        }
        setup = normalize_new_game_setup(raw_setup)
        packet = build_new_game_setup_packet(setup)

        self.assertEqual(setup["starting_wealth"]["mode"], "advanced")
        self.assertEqual(setup["starting_wealth"]["balance_base_units"], 40)
        self.assertFalse(setup["starting_wealth"]["requires_ai_invention"])
        self.assertNotIn(
            "starting wealth from player guidance",
            packet["fields_requiring_ai_invention"],
        )
        self.assertEqual(packet["starting_wealth_contract"]["balance_base_units"], 40)

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                setup["title"],
                setup=setup,
            )
            self.assertEqual(repository.get_state_value("currency.balance"), "40")
            self.assertEqual(repository.list_inventory_items(), [])

    def test_starting_task_setup_supports_ai_and_custom_opening_quests(self) -> None:
        ai_setup = normalize_new_game_setup(
            {
                "starting_task": {
                    "mode": "ai",
                    "guidance": "A missing courier tied to the opening location",
                }
            }
        )
        ai_packet = build_new_game_setup_packet(ai_setup)

        self.assertEqual(ai_setup["starting_task"]["mode"], "ai")
        self.assertEqual(
            ai_setup["starting_task"]["guidance"],
            "A missing courier tied to the opening location",
        )
        self.assertTrue(ai_setup["starting_task"]["task"]["requires_ai_invention"])
        self.assertIn("opening quest/task", ai_packet["fields_requiring_ai_invention"])
        self.assertEqual(ai_packet["starting_task_contract"]["mode"], "ai")
        self.assertEqual(
            ai_packet["starting_task_contract"]["guidance"],
            "A missing courier tied to the opening location",
        )
        self.assertIn("optional player inspiration", ai_packet["requirements"]["starting_task"])
        self.assertIn("top-level starting_task", ai_packet["requirements"]["starting_task"])
        self.assertIn(
            "how to recognize completion",
            ai_packet["requirements"]["starting_task"],
        )
        self.assertIn(
            "complete player-visible description",
            ai_packet["starting_task_contract"]["rules"],
        )

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

    def test_starter_inventory_mode_defaults_to_basic_and_supports_advanced(self) -> None:
        basic_setup = normalize_new_game_setup({})
        advanced_setup = normalize_new_game_setup(
            {"starter_inventory_mode": "advanced"}
        )
        advanced_packet = build_new_game_setup_packet(advanced_setup)

        self.assertEqual(basic_setup["starter_inventory_mode"], "basic")
        self.assertEqual(advanced_setup["starter_inventory_mode"], "advanced")
        self.assertEqual(
            advanced_packet["starter_inventory_contract"]["mode"], "advanced"
        )
        self.assertIn(
            "preserve the player's exact structured values",
            advanced_packet["requirements"]["starter_inventory"],
        )

    def test_combat_preferences_default_to_current_rules_and_support_narrative_mode(self) -> None:
        default_setup = normalize_new_game_setup({})
        narrative_setup = normalize_new_game_setup(
            {"combat": {"resolution_mode": "narrative", "focus": "high"}}
        )
        packet = build_new_game_setup_packet(narrative_setup)

        self.assertEqual(
            default_setup["combat"],
            {"resolution_mode": "strict", "focus": "balanced"},
        )
        self.assertEqual(narrative_setup["combat"]["resolution_mode"], "narrative")
        self.assertEqual(narrative_setup["combat"]["focus"], "high")
        self.assertEqual(packet["combat_contract"]["resolution_mode"], "narrative")
        self.assertIn("major recurring part", packet["combat_contract"]["focus_instruction"])
        self.assertIn("forbids CombatStartedEvent", packet["combat_contract"]["rules"])

    def test_new_game_setup_persists_combat_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir), "Combat Preferences Test"
            )
            repository.apply_new_game_setup(
                {"combat": {"resolution_mode": "narrative", "focus": "high"}}
            )

            self.assertEqual(
                repository.get_setting("combat.preferences"),
                {"resolution_mode": "narrative", "focus": "high"},
            )
            self.assertEqual(
                repository.get_setting("combat.resolution_mode"), "narrative"
            )
            self.assertEqual(repository.get_setting("combat.focus"), "high")

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

    def test_opening_scene_request_is_preserved_and_sent_as_scene_guidance(self) -> None:
        setup = normalize_new_game_setup(
            {
                "start_location": "The Agency Office",
                "opening_scene_request": (
                    "Begin with a threatening newspaper clipping arriving before dawn."
                ),
            }
        )
        packet = build_new_game_setup_packet(setup)

        self.assertEqual(
            setup["opening_scene_request"],
            "Begin with a threatening newspaper clipping arriving before dawn.",
        )
        self.assertEqual(
            packet["setup"]["opening_scene_request"],
            setup["opening_scene_request"],
        )
        self.assertIn("setup.opening_scene_request", packet["requirements"]["opening_scene"])
        self.assertIn("finalized in-world narration", packet["requirements"]["opening_scene"])

    def test_template_change_detection_uses_canonical_setup_values(self) -> None:
        original = {
            "title": "Mystery",
            "character": {"name": "Iris"},
            "starting_locations": [{"name": "Market"}],
        }

        self.assertFalse(template_setup_has_changes(original, dict(original)))
        self.assertFalse(template_setup_has_changes(
            original,
            {**original, "title": "Mystery Save 2"},
        ))
        self.assertTrue(template_setup_has_changes(
            original,
            {**original, "character": {"name": "Mira"}},
        ))

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

    def test_automatic_template_name_only_returns_an_unused_game_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template_path = Path(temp_dir) / "new_game_templates.json"
            setup = {"title": "Gun Jam Online", "specified_genre": "Tactical"}

            self.assertEqual(
                available_automatic_template_name(template_path, setup),
                "Gun Jam Online",
            )
            self.assertTrue(
                save_new_game_template(
                    template_path,
                    {"title": "Gun Jam Online", "specified_genre": "Existing"},
                )
            )
            self.assertIsNone(
                available_automatic_template_name(
                    template_path,
                    {"title": "gun jam online", "specified_genre": "Replacement"},
                )
            )
            stored = load_new_game_templates(template_path, normalize_setups=False)
            self.assertEqual(stored[0].setup["specified_genre"], "Existing")

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
            valid_sound_effect_tracks=["Steady Rain.wav"],
            valid_background_ambience_tracks=["Quiet Rain.ogg"],
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
        self.assertIn("english_text", packet["requirements"])
        self.assertIn(
            "printable ASCII English characters",
            packet["requirements"]["english_text"],
        )
        self.assertNotIn("pronunciation_map", packet["requirements"])
        self.assertIn("speaker_cues", packet["requirements"])
        self.assertIn(
            "exact npc_id as speaker_id",
            packet["requirements"]["speaker_cues"],
        )
        self.assertIn(
            "visible bubble label",
            packet["requirements"]["speaker_cues"],
        )
        self.assertIn("genre_generation", packet["requirements"])
        self.assertIn("Do not default to fantasy", packet["requirements"]["genre_generation"])
        self.assertIn("starting_location", packet["requirements"])
        self.assertIn("does not need to start in a tavern", packet["requirements"]["starting_location"])
        self.assertIn("short, broad place name", packet["requirements"]["starting_location"])
        self.assertIn("travel_locations", packet["requirements"])
        self.assertIn("setup.starting_locations", packet["requirements"]["travel_locations"])
        self.assertIn(
            "never reuse the superseded setup placeholder",
            packet["requirements"]["travel_locations"],
        )
        self.assertIn("location_mode is exact", packet["requirements"]["travel_locations"])
        self.assertIn("do not parse starting locations", packet["requirements"]["travel_locations"])
        self.assertIn("is_sublocation is true", packet["requirements"]["travel_locations"])
        self.assertIn("parent_location is set", packet["requirements"]["travel_locations"])
        self.assertIn("only known location", packet["requirements"]["travel_locations"])
        self.assertIn("six or more", packet["requirements"]["travel_locations"])
        self.assertIn("setup_scope_counts", packet["requirements"])
        self.assertIn("zero, one, or many", packet["requirements"]["setup_scope_counts"])
        self.assertIn("crafting_knowledge", packet["requirements"])
        self.assertIn("known_crafting_items", packet["requirements"]["crafting_knowledge"])
        self.assertIn(
            "generalized environments or source areas",
            packet["requirements"]["crafting_knowledge"],
        )
        self.assertIn(
            "Rare and Very Rare items must be materially more expensive",
            packet["requirements"]["crafting_knowledge"],
        )
        self.assertIn("not physical inventory", packet["requirements"]["crafting_knowledge"])
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
        self.assertIn(
            "Calendar settings are exempt",
            packet["requirements"]["creative_ideas"],
        )
        self.assertIn(
            "NPC or location name",
            packet["requirements"]["creative_ideas"],
        )
        self.assertIn(
            "bare category labels as final proper nouns",
            packet["requirements"]["creative_ideas"],
        )
        self.assertIn("the Police Department", packet["requirements"]["creative_ideas"])
        self.assertIn("The Blue Wall", packet["requirements"]["creative_ideas"])
        self.assertIn("gm_secrets", packet["requirements"])
        self.assertIn(
            "unknown to both the player and the Player Character",
            packet["requirements"]["gm_secrets"],
        )
        self.assertIn(
            "item they deliberately hid or stored",
            packet["requirements"]["gm_secrets"],
        )
        self.assertIn(
            "cannot be a skill check or search",
            packet["requirements"]["gm_secrets"],
        )
        self.assertIn("miscellaneous", packet["requirements"])
        self.assertIn(
            "original creatures or species",
            packet["requirements"]["miscellaneous"],
        )
        self.assertIn(
            "Do not duplicate records",
            packet["requirements"]["miscellaneous"],
        )
        self.assertIn("bestiary", packet["requirements"])
        self.assertIn("creature_id", packet["requirements"]["bestiary"])
        self.assertIn("item_request", packet["requirements"]["starter_inventory"])
        self.assertIn("at least five", packet["requirements"]["starter_inventory"])
        self.assertIn("has no maximum count", packet["requirements"]["starter_inventory"])
        self.assertIn("starting_items", packet["requirements"]["starter_inventory"])
        self.assertIn(
            "Prioritize concrete tools and supplies",
            packet["requirements"]["starter_inventory"],
        )
        self.assertIn(
            "not a Container merely because it stores information",
            packet["requirements"]["starter_inventory"],
        )
        self.assertIn("source_index", packet["requirements"]["starter_inventory"])
        self.assertIn("Fuel instead of Starting Fuel Amount", packet["requirements"]["starter_inventory"])
        self.assertIn("Put quantities in quantity, not name", packet["requirements"]["starter_inventory"])
        self.assertNotIn("ascii_art", packet["requirements"]["starter_inventory"])
        self.assertEqual(packet["starter_inventory_contract"]["requested_item_count"], 0)
        self.assertEqual(packet["starter_inventory_contract"]["minimum_finalized_item_count"], 5)
        self.assertEqual(
            packet["starter_inventory_contract"]["count_rule"],
            "At least 5 finalized starting items are required; there is no maximum starting item count.",
        )
        self.assertEqual(packet["starter_inventory_contract"]["output_field"], "starting_items")
        self.assertIn("creative_ideas", packet)
        self.assertIn("character_generation_guidance", packet)
        self.assertEqual(
            packet["character_generation_guidance"]["canonical_pronouns"],
            "They/Them",
        )
        self.assertIn(
            "canonical",
            packet["requirements"]["character_generation"],
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
        self.assertIn("starting_sound_effect", packet["requirements"])
        self.assertIn("starting_background_ambience", packet["requirements"])
        self.assertEqual(packet["audio"]["valid_music_tracks"], ["Town Village City.mp3"])
        self.assertEqual(
            packet["audio"]["valid_sound_effect_tracks"],
            ["Steady Rain.wav"],
        )
        self.assertEqual(
            packet["audio"]["valid_background_ambience_tracks"],
            ["Quiet Rain.ogg"],
        )
        self.assertIn(
            "must never come from audio.valid_music_tracks",
            packet["requirements"]["starting_sound_effect"],
        )
        self.assertIn(
            "no fixed cue-count target",
            packet["requirements"]["starting_sound_effect"],
        )
        self.assertEqual(packet["current_calendar"]["season_hint"], "spring")
        self.assertEqual(packet["current_weather"], "Clear")
        self.assertIn("calendar_weather_consistency", packet["requirements"])
        self.assertIn(
            "instead of Clear or another contradictory default",
            packet["requirements"]["calendar_weather_consistency"],
        )
        self.assertIn("calendar_generation", packet["requirements"])

    def test_setup_packet_uses_authoritative_start_calendar_and_weather(self) -> None:
        packet = build_new_game_setup_packet(
            {
                "starting_calendar": {
                    "year": 4,
                    "month_number": 2,
                    "day_of_month": 6,
                    "time_of_day_minutes": 21 * 60 + 15,
                },
                "starting_weather": "Heavy Snow",
            }
        )

        self.assertEqual(packet["current_calendar"]["year"], 4)
        self.assertEqual(packet["current_calendar"]["month_number"], 2)
        self.assertEqual(packet["current_calendar"]["day_of_month"], 6)
        self.assertEqual(packet["current_calendar"]["time_of_day_minutes"], 1275)
        self.assertEqual(packet["current_weather"], "Heavy Snow")

    def test_player_start_fields_remove_generated_current_minute_override(self) -> None:
        merged = merge_authoritative_starting_calendar(
            {
                "current_minute": 8 * 60,
                "month_number": 2,
                "day_of_month": 1,
            },
            {
                "year": 3,
                "day_of_month": 6,
                "time_of_day_minutes": 21 * 60,
            },
        )

        self.assertNotIn("current_minute", merged)
        self.assertEqual(merged["year"], 3)
        self.assertEqual(merged["month_number"], 2)
        self.assertEqual(merged["day_of_month"], 6)
        self.assertEqual(merged["time_of_day_minutes"], 21 * 60)

    def test_setup_packet_removes_music_tracks_from_sound_effect_catalog(self) -> None:
        packet = build_new_game_setup_packet(
            normalize_new_game_setup({}),
            valid_music_tracks=["Homey_Cottage.mp3", "Town Village City.mp3"],
            valid_sound_effect_tracks=["Town Village City.mp3"],
        )

        self.assertEqual(
            packet["audio"]["valid_music_tracks"],
            ["Homey_Cottage.mp3", "Town Village City.mp3"],
        )
        self.assertEqual(packet["audio"]["valid_sound_effect_tracks"], [])
        self.assertNotIn("starting_sound_effect", packet["requirements"])

    def test_setup_packet_marks_ai_generated_calendar_for_gemini(self) -> None:
        setup = normalize_new_game_setup({"calendar": {"calendar_type": "ai_generated"}})

        packet = build_new_game_setup_packet(setup)

        self.assertTrue(packet["setup"]["calendar"]["ai_generated"])
        self.assertEqual(packet["setup"]["calendar"]["calendar_type"], "ai_generated")
        self.assertEqual(
            packet["setup"]["calendar"],
            {"calendar_type": "ai_generated", "ai_generated": True},
        )
        self.assertNotIn("current_calendar", packet)
        self.assertIn("invent calendar_settings", packet["requirements"]["calendar_generation"])
        self.assertIn(
            "Never use Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, or Sunday",
            packet["requirements"]["calendar_generation"],
        )

    def test_ai_calendar_generation_guidance_survives_normalization_and_packet_building(self) -> None:
        setup = normalize_new_game_setup(
            {
                "calendar": {
                    "calendar_type": "ai_generated",
                    "generation_guidance": "Use Emberfall as one month name; start in late autumn on day 18.",
                }
            }
        )

        packet = build_new_game_setup_packet(setup)

        self.assertEqual(
            packet["setup"]["calendar"]["generation_guidance"],
            "Use Emberfall as one month name; start in late autumn on day 18.",
        )
        self.assertIn("generation_guidance", packet["requirements"]["calendar_generation"])

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

    def test_setup_packet_uses_structured_starting_npcs_instead_of_plaintext_parsing(self) -> None:
        setup = normalize_new_game_setup(
            {
                "world_context": (
                    "Her team included the captain, the engineer, and the weapons expert."
                ),
                "starting_npcs": [
                    {},
                    {
                        "name": "Captain Ives",
                        "location": "",
                        "description": "A tense expedition captain.",
                        "description_mode": "suggestion",
                    },
                    {
                        "name": "",
                        "location": "Supply Deck",
                        "description": "Do not change this exact description.",
                        "description_mode": "exact",
                    },
                ],
            }
        )

        packet = build_new_game_setup_packet(setup)

        self.assertEqual(len(packet["setup"]["starting_npcs"]), 3)
        self.assertTrue(packet["setup"]["starting_npcs"][0]["requires_ai_invention"])
        self.assertEqual(packet["setup"]["starting_npcs"][0]["name"], "")
        self.assertTrue(packet["setup"]["starting_npcs"][1]["requires_ai_invention"])
        self.assertEqual(
            packet["setup"]["starting_npcs"][2]["description_mode"],
            "exact",
        )
        self.assertIn("setup.starting_npcs", packet["requirements"]["events"])
        self.assertIn("public_description", packet["requirements"]["events"])
        self.assertIn("materially different", packet["requirements"]["events"])
        self.assertIn("suggested or incomplete starting NPCs", packet["fields_requiring_ai_invention"])
        self.assertIn("Do not parse NPCs out of ordinary setup prose", packet["requirements"]["events"])
        self.assertNotIn(
            "the captain, the engineer, and the weapons expert",
            packet["requirements"]["events"],
        )

        no_npc_setup = normalize_new_game_setup(
            {
                "no_starting_npcs": True,
                "starting_npcs": [{"name": "Should Be Cleared"}],
            }
        )

        self.assertTrue(no_npc_setup["no_starting_npcs"])
        self.assertEqual(no_npc_setup["starting_npcs"], [])

    def test_setup_packet_uses_structured_starting_locations_instead_of_plaintext_parsing(self) -> None:
        setup = normalize_new_game_setup(
            {
                "world_context": (
                    "The city mentions a canal station, a market, and a north road in passing."
                ),
                "starting_locations": [
                    {},
                    {
                        "name": "Rainmarket Station",
                        "description": "A canal station under an old clock.",
                        "location_mode": "exact",
                    },
                    {
                        "name": "North Road",
                        "description": "",
                        "location_mode": "suggestion",
                        "is_sublocation": True,
                        "parent_location": "Rainmarket Station",
                    },
                ],
            }
        )

        packet = build_new_game_setup_packet(setup)

        self.assertEqual(len(packet["setup"]["starting_locations"]), 3)
        self.assertTrue(packet["setup"]["starting_locations"][0]["requires_ai_invention"])
        self.assertEqual(packet["setup"]["starting_locations"][0]["name"], "")
        self.assertEqual(
            packet["setup"]["starting_locations"][1]["location_mode"],
            "exact",
        )
        self.assertFalse(
            packet["setup"]["starting_locations"][1]["requires_ai_invention"]
        )
        self.assertTrue(packet["setup"]["starting_locations"][2]["requires_ai_invention"])
        self.assertTrue(packet["setup"]["starting_locations"][2]["is_sublocation"])
        self.assertEqual(
            packet["setup"]["starting_locations"][2]["parent_location"],
            "Rainmarket Station",
        )
        self.assertIn("setup.starting_locations", packet["requirements"]["travel_locations"])
        self.assertIn("materially different", packet["requirements"]["travel_locations"])
        self.assertIn("locations entry unchanged", packet["requirements"]["travel_locations"])
        self.assertIn("parent_location is set", packet["requirements"]["travel_locations"])
        self.assertIn(
            "do not parse starting locations out of ordinary setup prose",
            packet["requirements"]["travel_locations"],
        )
        self.assertNotIn(
            "a canal station, a market, and a north road",
            packet["requirements"]["travel_locations"],
        )

    def test_setup_packet_preserves_weapon_armor_metadata_for_gemini(self) -> None:
        setup = normalize_new_game_setup(
            {
                "world_context": (
                    "Her team included the captain, the engineer, and the weapons expert."
                ),
                "starter_items": [
                    {
                        "name": "Rail Pistol",
                        "category": "Weapon",
                        "quantity": 1,
                        "weapon_hands": "one-handed",
                        "damage": "1d8",
                        "attack_skill": "Ranged",
                        "attack_range_feet": 80,
                        "ammunition_type_required": "Rail Cells",
                        "clip_size": 6,
                        "bullets_per_attack": 1,
                    },
                    {
                        "name": "Vac Suit",
                        "category": "Armor",
                        "covers_body_parts": ["Torso", "Arms", "Legs"],
                        "armor_rating": 2,
                    },
                ],
            }
        )

        packet = build_new_game_setup_packet(setup)
        weapon = packet["setup"]["starter_items"][0]
        armor = packet["setup"]["starter_items"][1]

        self.assertEqual(weapon["item_type"], "Weapon")
        self.assertEqual(weapon["damage"], "1d8")
        self.assertEqual(weapon["ammunition_type_required"], "Rail Cells")
        self.assertEqual(weapon["clip_size"], 6)
        self.assertEqual(armor["item_type"], "Armor")
        self.assertEqual(armor["covers_body_parts"], ["Torso", "Arms", "Legs"])
        self.assertEqual(armor["armor_rating"], 2)
        self.assertIn(
            "Do not downgrade setup weapons or armor into generic items",
            packet["requirements"]["starter_inventory"],
        )
        self.assertIn(
            "setup.starting_npcs",
            packet["requirements"]["events"],
        )


if __name__ == "__main__":
    unittest.main()
