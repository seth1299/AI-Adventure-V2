from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ai_adventure.ai.gemini_service import (
    DEFAULT_GEMINI_MODEL,
    build_gemini_new_game_prompt,
    build_gemini_story_prompt,
    format_story_message,
    load_gemini_settings,
    parse_gemini_new_game_response,
    parse_gemini_story_response,
)


class GeminiServiceTests(unittest.TestCase):
    def test_load_gemini_settings_reads_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "GEMINI_API_KEY=test-key\nGEMINI_MODEL=gemini-2.5-pro\n",
                encoding="utf-8",
            )

            old_key = os.environ.pop("GEMINI_API_KEY", None)
            old_model = os.environ.pop("GEMINI_MODEL", None)

            try:
                settings = load_gemini_settings(env_path)
            finally:
                if old_key is not None:
                    os.environ["GEMINI_API_KEY"] = old_key
                if old_model is not None:
                    os.environ["GEMINI_MODEL"] = old_model

            self.assertEqual(settings.api_key, "test-key")
            self.assertEqual(settings.model, "gemini-2.5-pro")

    def test_load_gemini_settings_uses_default_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")

            old_model = os.environ.pop("GEMINI_MODEL", None)

            try:
                settings = load_gemini_settings(env_path)
            finally:
                if old_model is not None:
                    os.environ["GEMINI_MODEL"] = old_model

            self.assertEqual(settings.model, DEFAULT_GEMINI_MODEL)

    def test_build_prompt_contains_strict_json_contract(self) -> None:
        prompt = build_gemini_story_prompt(
            {
                "packet_type": "story_turn",
                "player_command": "look around",
                "creative_ideas": {"banned_terms": ["Elara"]},
            }
        )

        self.assertIn("Return one JSON object", prompt)
        self.assertIn("response", prompt)
        self.assertIn("suggested_actions", prompt)
        self.assertIn("events", prompt)
        self.assertIn("NPC knowledge boundary", prompt)
        self.assertIn("must not reference private player state", prompt)
        self.assertIn("display_name is the name", prompt)
        self.assertIn("multiple events with the same type", prompt)
        self.assertIn("one NpcUpsertedEvent for each", prompt)
        self.assertIn("player_facing_information is shown directly", prompt)
        self.assertIn("Creative naming boundary", prompt)
        self.assertIn("Never use creative_ideas.banned_terms", prompt)
        self.assertIn("Exact banned proper nouns", prompt)
        self.assertIn("Elara", prompt)
        self.assertIn("reuse that exact npc_id/internal identifier", prompt)
        self.assertIn("use single quotation marks for the inner quoted name", prompt)
        self.assertIn("Currency is stored as one integer", prompt)
        self.assertIn("do not create coin inventory", prompt)
        self.assertIn("look around", prompt)

    def test_parse_json_response(self) -> None:
        raw_text = json.dumps(
            {
                "response": "The road bends into fog.\n\nWhat do you do now?",
                "suggested_actions": ["Follow the road.", "Listen for movement."],
                "events": [{"type": "FlagSetEvent", "payload": {"key": "fog_seen"}}],
                "out_of_game": False,
            }
        )

        result = parse_gemini_story_response(raw_text)

        self.assertEqual(
            result.narrative_text,
            (
                "The road bends into fog.\n\n"
                "What do you do now?\n"
                "- Follow the road.\n"
                "- Listen for movement."
            ),
        )
        self.assertEqual(result.suggested_actions[0], "Follow the road.")
        self.assertEqual(result.suggested_events[0]["type"], "FlagSetEvent")

    def test_story_formatting_spaces_sentences_and_keeps_actions_tight(self) -> None:
        formatted = format_story_message(
            "It is 8:00 A.M. The lantern gutters. What do you do now?\n\n"
            "- Shield the flame.\n"
            "- Listen at the door."
        )

        self.assertEqual(
            formatted,
            (
                "It is 8:00 A.M.\n\n"
                "The lantern gutters.\n\n"
                "What do you do now?\n"
                "- Shield the flame.\n"
                "- Listen at the door."
            ),
        )

    def test_story_formatting_splits_after_sentence_ending_quote(self) -> None:
        formatted = format_story_message(
            '"Are you looking for a bite to eat, or something else?" What do you do now?\n'
            "- Order a meal.\n"
            "- Ask about rumors."
        )

        self.assertEqual(
            formatted,
            (
                '"Are you looking for a bite to eat, or something else?"\n\n'
                "What do you do now?\n"
                "- Order a meal.\n"
                "- Ask about rumors."
            ),
        )

    def test_story_formatting_keeps_multi_sentence_dialogue_together(self) -> None:
        formatted = format_story_message(
            '"It is not just the rocks, Kit. The herb-gatherers I talk to? '
            "They have been complaining. Some call it 'Ghost Moss.' "
            'Does that sound like your sort of thing?" What do you do now?\n'
            "- Ask about Ghost Moss.\n"
            "- Order a drink."
        )

        self.assertEqual(
            formatted,
            (
                '"It is not just the rocks, Kit. The herb-gatherers I talk to? '
                "They have been complaining. Some call it 'Ghost Moss.' "
                'Does that sound like your sort of thing?"\n\n'
                "What do you do now?\n"
                "- Ask about Ghost Moss.\n"
                "- Order a drink."
            ),
        )

    def test_story_formatting_keeps_dialogue_with_attribution_together(self) -> None:
        formatted = format_story_message(
            '"Fair enough. A scholar is just as good as a merchant, I suppose," '
            'she says with a light chuckle. What do you do now?'
        )

        self.assertEqual(
            formatted,
            (
                '"Fair enough. A scholar is just as good as a merchant, I suppose," '
                "she says with a light chuckle.\n\n"
                "What do you do now?"
            ),
        )

    def test_build_and_parse_new_game_response(self) -> None:
        prompt = build_gemini_new_game_prompt(
            {
                "packet_type": "new_game_setup",
                "setup": {"title": "Rainmarket"},
            }
        )
        raw_text = json.dumps(
            {
                "selected_genre": "Realistic detective mystery",
                "world_summary": "Rainmarket is a canal city.",
                "world_lore": {
                    "Locations": {
                        "Rainmarket Station": "Rainmarket Station anchors the canal district."
                    },
                    "Economy": {"Crowns": "Crowns dominate official trade."},
                },
                "start_location": "Rainmarket Station, beneath the old canal clock",
                "starting_calendar": {
                    "season_hint": "autumn",
                    "day_of_month": 1,
                    "time_of_day_minutes": 480,
                },
                "weather": "Clear, cold autumn wind.",
                "character": {
                    "name": "Iris Vale",
                    "appearance": "A detective in a rain-dark coat.",
                    "backstory": "Raised among station ledgers and canal warrants.",
                    "notes": "Careful, observant, and slow to trust.",
                },
                "skills": [
                    {
                        "name": "Canal Investigation",
                        "description": "Reading wet footprints, dock ledgers, and canal-side clues.",
                        "level": 5,
                    }
                ],
                "starting_items": [
                    {
                        "name": "Case Notebook",
                        "category": "Tool",
                        "quantity": 1,
                        "description": "A pocket notebook filled with case notes.",
                        "value_base_units": 4,
                    },
                    {
                        "name": "Rain-Dark Coat",
                        "category": "Clothing",
                        "quantity": 1,
                        "description": "A heavy coat suited to canal rain.",
                        "value_base_units": 25,
                    },
                    {
                        "name": "Brass Magnifier",
                        "category": "Tool",
                        "quantity": 1,
                        "description": "A lens for reading small marks.",
                        "value_base_units": 18,
                    },
                    {
                        "name": "Rail Warrant",
                        "category": "Document",
                        "quantity": 1,
                        "description": "A stamped warrant for station inquiries.",
                        "value_base_units": 0,
                    },
                    {
                        "name": "Half-Crown Purse",
                        "category": "Currency",
                        "quantity": 1,
                        "description": "A modest purse of local money.",
                        "value_base_units": 12,
                    },
                ],
                "currency_denominations": [
                    {"name": "Bit", "plural_name": "Bits", "value": 1},
                    {"name": "Crown", "plural_name": "Crowns", "value": 12},
                    {"name": "Moonmark", "plural_name": "Moonmarks", "value": 37},
                ],
                "currency_description": "Crowns and moonmarks are common canal-city money.",
                "introductory_message": "Rain falls on the station.",
                "events": [{"type": "NpcUpsertedEvent"}],
            }
        )

        result = parse_gemini_new_game_response(raw_text)

        self.assertIn("world_summary", prompt)
        self.assertIn("Rainmarket", prompt)
        self.assertIn("fields_requiring_ai_invention", prompt)
        self.assertIn("blank/default placeholders", prompt)
        self.assertIn("high-priority style seeds", prompt)
        self.assertIn("Never use creative_ideas.banned_terms", prompt)
        self.assertIn("gender_presentation_hint", prompt)
        self.assertIn("does not imply male", prompt)
        self.assertIn("selected_genre", prompt)
        self.assertIn("Do not default to fantasy", prompt)
        self.assertIn(
            "not as instructions that the entire world must share the same theme",
            prompt,
        )
        self.assertIn("every institution being coin-themed", prompt)
        self.assertIn("MusicChangedEvent", prompt)
        self.assertIn("start_location", prompt)
        self.assertIn("short and broad", prompt)
        self.assertIn("Y/N's Office", prompt)
        self.assertIn("does not need to start in a tavern", prompt)
        self.assertIn("starting_items must contain at least five", prompt)
        self.assertIn("currency_denominations must", prompt)
        self.assertIn("do not need to be multiples or powers of 10", prompt)
        self.assertIn("Use CurrencyDefinedEvent only when a story event", prompt)
        self.assertIn("skills must contain every starting skill", prompt)
        self.assertIn("requires_ai_invention=true", prompt)
        self.assertIn("Do not reuse generic default names", prompt)
        self.assertIn("generalized gameplay capabilities", prompt)
        self.assertIn("Lore (Syndicate)", prompt)
        self.assertIn("rather than Syndicate Lore", prompt)
        self.assertIn("current_calendar", prompt)
        self.assertIn("do not mention autumn winds", prompt)
        self.assertIn('"type": "EventTypeName"', prompt)
        self.assertIn("Do not use event_type", prompt)
        self.assertEqual(result.world_summary, "Rainmarket is a canal city.")
        self.assertEqual(
            result.world_lore["Locations"]["Rainmarket Station"],
            "Rainmarket Station anchors the canal district.",
        )
        self.assertEqual(result.start_location, "Rainmarket Station")
        self.assertEqual(result.selected_genre, "Realistic detective mystery")
        self.assertEqual(result.starting_calendar["season_hint"], "autumn")
        self.assertEqual(result.start_weather, "Clear, cold autumn wind.")
        self.assertEqual(result.finalized_character["name"], "Iris Vale")
        self.assertEqual(result.finalized_skills[0]["name"], "Canal Investigation")
        self.assertEqual(len(result.finalized_starter_items), 5)
        self.assertEqual(result.finalized_starter_items[0]["name"], "Case Notebook")
        self.assertEqual(result.finalized_starter_items[0]["value_base_units"], 4)
        self.assertEqual(result.finalized_currency_denominations[1]["name"], "Crown")
        self.assertEqual(result.finalized_currency_denominations[2]["value"], 37)
        self.assertEqual(
            result.finalized_currency_description,
            "Crowns and moonmarks are common canal-city money.",
        )
        self.assertTrue(result.introductory_message.endswith("What do you do now?"))
        self.assertEqual(result.suggested_events[0]["type"], "NpcUpsertedEvent")

    def test_parse_legacy_json_response_shape(self) -> None:
        raw_text = json.dumps(
            {
                "narrative_text": "The old field name still works.",
                "suggested_events": [{"type": "StoryAdvancedEvent"}],
            }
        )

        result = parse_gemini_story_response(raw_text)

        self.assertEqual(result.narrative_text, "The old field name still works.")
        self.assertEqual(result.suggested_events[0]["type"], "StoryAdvancedEvent")

    def test_parse_non_json_response_falls_back_to_narrative(self) -> None:
        with self.assertLogs("ai_adventure.ai.gemini_service", level="WARNING"):
            result = parse_gemini_story_response("A plain narration response.")

        self.assertEqual(result.narrative_text, "A plain narration response.")
        self.assertEqual(result.suggested_events, [])


if __name__ == "__main__":
    unittest.main()
