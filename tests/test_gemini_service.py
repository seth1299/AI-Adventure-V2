from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from ai_adventure.ai.gemini_service import (
    DEFAULT_GEMINI_MODEL,
    EVENT_RESPONSE_SCHEMA,
    KNOWN_EVENT_TYPE_NAMES,
    NEW_GAME_RESPONSE_JSON_SCHEMA,
    STORY_RESPONSE_JSON_SCHEMA,
    GeminiNarrationService,
    GeminiSettings,
    build_gemini_new_game_prompt,
    build_gemini_story_prompt,
    format_story_message,
    load_gemini_settings,
    parse_gemini_new_game_response,
    parse_gemini_story_response,
    _json_schema_shape_errors,
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

    def test_story_request_uses_structured_output_schema(self) -> None:
        fake_client_class = self._install_fake_genai_client(
            json.dumps(
                {
                    "response": "The road bends into fog.",
                    "suggested_actions": [],
                    "events": [],
                    "out_of_game": False,
                }
            )
        )

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            result = service.generate_story_response({"packet_type": "story_turn"})
        finally:
            self._remove_fake_genai_client()

        call = fake_client_class.last_client.models.calls[0]

        self.assertEqual(result.narrative_text, "The road bends into fog.")
        self.assertEqual(call["model"], "gemini-2.5-flash")
        self.assertEqual(call["config"]["response_mime_type"], "application/json")
        self.assertEqual(
            call["config"]["response_json_schema"],
            STORY_RESPONSE_JSON_SCHEMA,
        )

    def test_story_schema_requires_currency_changed_base_unit_amount(self) -> None:
        valid_response = {
            "response": "The purchase is complete.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "CurrencyChangedEvent",
                    "payload": {"base_unit_amount": -20},
                }
            ],
            "out_of_game": False,
        }
        invalid_response = {
            "response": "The purchase is complete.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "CurrencyChangedEvent",
                    "payload": {"net_base_unit_amount": -20},
                }
            ],
            "out_of_game": False,
        }

        self.assertEqual(
            _json_schema_shape_errors(valid_response, STORY_RESPONSE_JSON_SCHEMA),
            [],
        )
        self.assertIn(
            "$.events[0] did not match any allowed schema",
            _json_schema_shape_errors(invalid_response, STORY_RESPONSE_JSON_SCHEMA),
        )

    def test_story_schema_requires_inventory_item_value(self) -> None:
        valid_response = {
            "response": "You pick the fern.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "InventoryItemAddedEvent",
                    "payload": {
                        "item_type": "Botanical",
                        "item_name": "Silver-Spire Fern",
                        "description": "A cool-natured fern.",
                        "amount": 2,
                        "value_base_units": 1,
                    },
                }
            ],
            "out_of_game": False,
        }
        missing_value_response = {
            "response": "You pick the fern.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "InventoryItemAddedEvent",
                    "payload": {
                        "item_type": "Botanical",
                        "item_name": "Silver-Spire Fern",
                        "description": "A cool-natured fern.",
                        "amount": 2,
                    },
                }
            ],
            "out_of_game": False,
        }
        zero_value_response = {
            "response": "You pick the fern.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "InventoryItemAddedEvent",
                    "payload": {
                        "item_type": "Botanical",
                        "item_name": "Silver-Spire Fern",
                        "description": "A cool-natured fern.",
                        "amount": 2,
                        "value_base_units": 0,
                    },
                }
            ],
            "out_of_game": False,
        }

        self.assertEqual(
            _json_schema_shape_errors(valid_response, STORY_RESPONSE_JSON_SCHEMA),
            [],
        )
        self.assertIn(
            "$.events[0] did not match any allowed schema",
            _json_schema_shape_errors(missing_value_response, STORY_RESPONSE_JSON_SCHEMA),
        )
        self.assertIn(
            "$.events[0] did not match any allowed schema",
            _json_schema_shape_errors(zero_value_response, STORY_RESPONSE_JSON_SCHEMA),
        )

    def test_story_request_injects_missing_skill_check_for_uncertain_action(self) -> None:
        fake_client_class = self._install_fake_genai_client(
            json.dumps(
                {
                    "response": "You find a bright fern in the brush.",
                    "suggested_actions": [],
                    "events": [
                        {
                            "type": "SkillXpAddedEvent",
                            "payload": {"skill_name": "Foraging", "xp_amount": 1},
                        },
                        {
                            "type": "InventoryItemAddedEvent",
                            "payload": {
                                "item_type": "Botanical",
                                "item_name": "Silver-Spire Fern",
                                "description": "A cool-natured fern.",
                                "amount": 2,
                                "value_base_units": 1,
                            },
                        },
                    ],
                    "out_of_game": False,
                }
            )
        )

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            result = service.generate_story_response(
                {
                    "packet_type": "story_turn",
                    "player_command": "Forage through the brush for useful herbs.",
                    "state": {
                        "skills": {
                            "known_skills": [
                                {"name": "Fieldcraft"},
                            ]
                        }
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        self.assertIsNotNone(fake_client_class.last_client)
        self.assertEqual(result.suggested_events[0]["type"], "SkillCheckRequestedEvent")
        self.assertEqual(result.suggested_events[0]["payload"]["skill_name"], "Fieldcraft")
        self.assertNotIn(
            "SkillXpAddedEvent",
            [event["type"] for event in result.suggested_events],
        )

    def test_story_request_adds_inventory_for_collected_reagent(self) -> None:
        self._install_fake_genai_client(
            json.dumps(
                {
                    "response": "You collect the Blue Cave Salt and stow it in your basket.",
                    "suggested_actions": [],
                    "events": [
                        {
                            "type": "SkillCheckRequestedEvent",
                            "payload": {"skill_name": "Alchemy", "difficulty": "normal"},
                        },
                        {
                            "type": "ReagentDiscoveredEvent",
                            "payload": {
                                "name": "Blue Cave Salt",
                                "material_type": "Geological",
                                "qualities": ["Cool", "Dry"],
                                "motions": ["Stilling"],
                                "virtues": ["Cooling steadiness"],
                                "uses": ["Sleep draughts"],
                                "notes": "Pale blue salt that cools and steadies.",
                            },
                        },
                        {
                            "type": "StatusUpdatedEvent",
                            "payload": {
                                "location": "Zoclar Outskirts",
                                "minutes_passed": 30,
                                "weather": "Clear",
                            },
                        },
                    ],
                    "out_of_game": False,
                }
            )
        )

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            result = service.generate_story_response(
                {
                    "packet_type": "story_turn",
                    "player_command": "Search for reagents to collect.",
                    "state": {"skills": {"known_skills": [{"name": "Alchemy"}]}},
                }
            )
        finally:
            self._remove_fake_genai_client()

        inventory_events = [
            event
            for event in result.suggested_events
            if event["type"] == "InventoryItemAddedEvent"
        ]

        self.assertEqual(len(inventory_events), 1)
        self.assertEqual(inventory_events[0]["payload"]["item_name"], "Blue Cave Salt")
        self.assertEqual(inventory_events[0]["payload"]["item_type"], "Geological")
        self.assertEqual(inventory_events[0]["payload"]["value_base_units"], 1)

    def test_story_request_adds_inventory_for_narrated_collection(self) -> None:
        self._install_fake_genai_client(
            json.dumps(
                {
                    "response": (
                        "You spend the next few hours scouring every patch of scrub "
                        "until you have a bounty of fresh, high-quality specimens. "
                        "Your basket is brimming with local flora and rare geological "
                        "finds. It is quite the collection."
                    ),
                    "suggested_actions": [],
                    "events": [
                        {
                            "type": "SkillXpAddedEvent",
                            "payload": {"skill_name": "Foraging", "xp_amount": 1},
                        },
                        {
                            "type": "StatusUpdatedEvent",
                            "payload": {
                                "location": "Zoclar Outskirts",
                                "minutes_passed": 120,
                                "weather": "Clear",
                            },
                        },
                    ],
                    "out_of_game": False,
                }
            )
        )

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            result = service.generate_story_response(
                {
                    "packet_type": "story_turn",
                    "player_command": "Spend the next couple of in-game hours outside.",
                    "state": {"skills": {"known_skills": [{"name": "Foraging"}]}},
                }
            )
        finally:
            self._remove_fake_genai_client()

        event_types = [event["type"] for event in result.suggested_events]
        inventory_events = [
            event
            for event in result.suggested_events
            if event["type"] == "InventoryItemAddedEvent"
        ]

        self.assertEqual(event_types[0], "SkillCheckRequestedEvent")
        self.assertEqual(result.suggested_events[0]["payload"]["skill_name"], "Foraging")
        self.assertNotIn("SkillXpAddedEvent", event_types)
        self.assertEqual(len(inventory_events), 1)
        self.assertEqual(
            inventory_events[0]["payload"],
            {
                "item_type": "Foraged Goods",
                "item_name": "Assorted Foraged Specimens",
                "description": (
                    "A mixed bounty of local flora and rare geological finds gathered "
                    "during foraging."
                ),
                "amount": 1,
                "value_base_units": 1,
            },
        )

    def test_story_request_does_not_add_inventory_for_promising_search_site(self) -> None:
        self._install_fake_genai_client(
            json.dumps(
                {
                    "response": (
                        "A few interesting rock formations catch your attention near "
                        "the water's edge, some showing a peculiar mineral-rich luster "
                        "that might prove useful if handled correctly."
                    ),
                    "suggested_actions": [],
                    "events": [
                        {
                            "type": "SkillCheckRequestedEvent",
                            "payload": {
                                "skill_name": "Geology",
                                "dc": 12,
                                "difficulty": "Moderate",
                            },
                        }
                    ],
                    "out_of_game": False,
                }
            )
        )

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            result = service.generate_story_response(
                {
                    "packet_type": "story_turn",
                    "player_command": "Search the stream bank for new botanical reagents.",
                    "state": {"skills": {"known_skills": [{"name": "Geology"}]}},
                }
            )
        finally:
            self._remove_fake_genai_client()

        self.assertNotIn(
            "InventoryItemAddedEvent",
            [event["type"] for event in result.suggested_events],
        )

    def test_story_schema_rejects_skill_xp_without_skill_name(self) -> None:
        invalid_response = {
            "response": "Study pays off.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "SkillXpAddedEvent",
                    "payload": {"skill_id": 8, "xp_amount": 1},
                }
            ],
            "out_of_game": False,
        }
        valid_response = {
            "response": "Study pays off.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "SkillXpAddedEvent",
                    "payload": {"skill_name": "Alchemy", "xp_amount": 1},
                }
            ],
            "out_of_game": False,
        }

        self.assertIn(
            "$.events[0] did not match any allowed schema",
            _json_schema_shape_errors(invalid_response, STORY_RESPONSE_JSON_SCHEMA),
        )
        self.assertEqual(
            _json_schema_shape_errors(valid_response, STORY_RESPONSE_JSON_SCHEMA),
            [],
        )

    def test_story_schema_requires_structured_reagent_discovery(self) -> None:
        invalid_response = {
            "response": "You identify Moss-Vein Tallow.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "ReagentDiscoveredEvent",
                    "payload": {"name": "Moss-Vein Tallow"},
                }
            ],
            "out_of_game": False,
        }
        valid_response = {
            "response": "You identify Moss-Vein Tallow.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "ReagentDiscoveredEvent",
                    "payload": {
                        "name": "Moss-Vein Tallow",
                        "material_type": "Fungal",
                        "qualities": ["waxy"],
                        "motions": ["binding"],
                        "virtues": ["stability"],
                        "uses": ["stabilizing volatile mixtures"],
                        "notes": "Thrives in damp shaded valley crevices.",
                    },
                }
            ],
            "out_of_game": False,
        }

        self.assertIn(
            "$.events[0] did not match any allowed schema",
            _json_schema_shape_errors(invalid_response, STORY_RESPONSE_JSON_SCHEMA),
        )
        self.assertEqual(
            _json_schema_shape_errors(valid_response, STORY_RESPONSE_JSON_SCHEMA),
            [],
        )

    def test_story_schema_only_advertises_supported_event_types(self) -> None:
        self.assertNotIn("StoryAdvancedEvent", KNOWN_EVENT_TYPE_NAMES)
        self.assertNotIn("SecretAddedEvent", KNOWN_EVENT_TYPE_NAMES)
        self.assertNotIn("MerchantInterfaceRequestedEvent", KNOWN_EVENT_TYPE_NAMES)

    def test_event_schema_matches_advertised_event_types(self) -> None:
        schema_event_types = [
            branch["properties"]["type"]["enum"][0]
            for branch in EVENT_RESPONSE_SCHEMA["anyOf"]
        ]

        self.assertEqual(sorted(schema_event_types), sorted(KNOWN_EVENT_TYPE_NAMES))
        self.assertEqual(len(schema_event_types), len(set(schema_event_types)))

    def test_default_rule_event_contracts_are_schema_supported(self) -> None:
        rules_path = (
            Path(__file__).resolve().parents[1]
            / "ai_adventure"
            / "data"
            / "context"
            / "default_rules.json"
        )
        rules_data = json.loads(rules_path.read_text(encoding="utf-8"))
        rule_event_types = {
            section["content"]["event_type"]
            for section in rules_data["sections"]
            if isinstance(section.get("content"), dict)
            and section["content"].get("event_type")
        }

        self.assertEqual(rule_event_types - set(KNOWN_EVENT_TYPE_NAMES), set())

    def test_new_game_request_uses_structured_output_schema(self) -> None:
        fake_client_class = self._install_fake_genai_client(
            json.dumps(
                {
                    "selected_genre": "Solar noir",
                    "world_summary": "A city under glass.",
                    "world_lore": {},
                    "start_location": "Dawn Gate",
                    "starting_calendar": {},
                    "weather": "Bright and cold.",
                    "character": {
                        "name": "Ari",
                        "appearance": "Sharp coat, tired eyes.",
                        "backstory": "A courier with too many sealed envelopes.",
                        "notes": "Keeps promises when possible.",
                    },
                    "skills": [],
                    "starting_items": [
                        {
                            "name": f"Starter Item {index}",
                            "category": "Tool",
                            "quantity": 1,
                            "description": "Useful enough to keep.",
                            "value_base_units": index,
                        }
                        for index in range(5)
                    ],
                    "currency_denominations": [
                        {"name": "Credit", "plural_name": "Credits", "value": 1}
                    ],
                    "currency_description": "Credits are stored on brass chits.",
                    "starting_currency_balance_base_units": 12,
                    "introductory_message": "The gate opens. What do you do now?",
                    "events": [],
                }
            )
        )

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            result = service.generate_new_game_world({"packet_type": "new_game_setup"})
        finally:
            self._remove_fake_genai_client()

        call = fake_client_class.last_client.models.calls[0]

        self.assertEqual(result.world_summary, "A city under glass.")
        self.assertEqual(call["config"]["response_mime_type"], "application/json")
        self.assertEqual(
            call["config"]["response_json_schema"],
            NEW_GAME_RESPONSE_JSON_SCHEMA,
        )

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
        self.assertIn("Spoken dialogue must use double quotation marks", prompt)
        self.assertIn("Do not use single quotation marks as the outer boundary", prompt)
        self.assertIn("Use single quotation marks only when", prompt)
        self.assertIn("Currency is stored as one integer", prompt)
        self.assertIn("payload.base_unit_amount", prompt)
        self.assertIn("Never use net_base_unit_amount", prompt)
        self.assertIn("Every InventoryItemAddedEvent payload must include value_base_units", prompt)
        self.assertIn("ReagentDiscoveredEvent records Alchemy Notebook knowledge only", prompt)
        self.assertIn("Do not describe a successful bounty", prompt)
        self.assertIn("For uncertain actions, suggest SkillCheckRequestedEvent", prompt)
        self.assertIn("do not create coin inventory", prompt)
        self.assertNotIn("object must match this shape", prompt)
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
                "starting_currency_balance_base_units": 49,
                "introductory_message": "Rain falls on the station.",
                "events": [{"type": "NpcUpsertedEvent", "payload": {}}],
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
        self.assertIn("starting_currency_balance_base_units", prompt)
        self.assertIn("game_state/currency.balance", prompt)
        self.assertIn("Do not create coin or purse", prompt)
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
        self.assertIn("do not use event_type", prompt)
        self.assertIn("API response schema defines the required JSON fields", prompt)
        self.assertNotIn("Return this JSON shape", prompt)
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
        self.assertEqual(result.finalized_starting_currency_balance_base_units, 49)
        self.assertTrue(result.introductory_message.endswith("What do you do now?"))
        self.assertEqual(result.suggested_events[0]["type"], "NpcUpsertedEvent")

    def test_parse_legacy_json_response_shape(self) -> None:
        raw_text = json.dumps(
            {
                "narrative_text": "The old field name still works.",
                "suggested_events": [{"type": "StoryAdvancedEvent"}],
            }
        )

        with self.assertLogs("ai_adventure.ai.gemini_service", level="WARNING"):
            result = parse_gemini_story_response(raw_text)

        self.assertEqual(result.narrative_text, "The old field name still works.")
        self.assertEqual(result.suggested_events[0]["type"], "StoryAdvancedEvent")

    def test_parse_non_json_response_falls_back_to_narrative(self) -> None:
        with self.assertLogs("ai_adventure.ai.gemini_service", level="WARNING"):
            result = parse_gemini_story_response("A plain narration response.")

        self.assertEqual(result.narrative_text, "A plain narration response.")
        self.assertEqual(result.suggested_events, [])

    def _install_fake_genai_client(self, response_text: str) -> type:
        class FakeModels:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def generate_content(self, **kwargs: object) -> object:
                self.calls.append(kwargs)
                return types.SimpleNamespace(text=response_text)

        class FakeClient:
            last_client: object | None = None

            def __init__(self, api_key: str) -> None:
                self.api_key = api_key
                self.models = FakeModels()
                FakeClient.last_client = self

        google_module = types.ModuleType("google")
        genai_module = types.ModuleType("google.genai")
        genai_module.Client = FakeClient
        google_module.genai = genai_module
        self._old_google_module = sys.modules.get("google")
        self._old_genai_module = sys.modules.get("google.genai")
        sys.modules["google"] = google_module
        sys.modules["google.genai"] = genai_module

        return FakeClient

    def _remove_fake_genai_client(self) -> None:
        old_google = getattr(self, "_old_google_module", None)
        old_genai = getattr(self, "_old_genai_module", None)

        if old_google is None:
            sys.modules.pop("google", None)
        else:
            sys.modules["google"] = old_google

        if old_genai is None:
            sys.modules.pop("google.genai", None)
        else:
            sys.modules["google.genai"] = old_genai


if __name__ == "__main__":
    unittest.main()
