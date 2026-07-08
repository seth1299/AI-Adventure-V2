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
    NEW_GAME_EVENT_RESPONSE_SCHEMA,
    NEW_GAME_RESPONSE_JSON_SCHEMA,
    SKILL_CHECK_PLAN_RESPONSE_JSON_SCHEMA,
    STORY_RESPONSE_JSON_SCHEMA,
    GeminiNarrationService,
    GeminiSettings,
    build_skill_check_plan_prompt,
    build_gemini_new_game_prompt,
    build_gemini_story_prompt,
    format_story_message,
    load_gemini_settings,
    parse_gemini_new_game_response,
    parse_skill_check_plan_response,
    parse_gemini_story_response,
    _drop_unwarranted_skill_check_events,
    _filter_unwarranted_planned_skill_checks,
    _json_schema_shape_errors,
    _normalize_visible_currency_phrasing,
    _parse_new_game_starter_items,
)


def _container_metadata() -> dict[str, object]:
    return {
        "is_open": False,
        "contents_taken": False,
        "is_locked": False,
        "lockpick_skill": "Lockpicking",
        "lockpick_dc": 0,
        "lockpick_failure_consequence": "",
        "is_trapped": False,
        "trap_notice_skill": "Perception",
        "trap_notice_dc": 0,
        "trap_disarm_skill": "Sleight of Hand",
        "trap_disarm_dc": 0,
        "trap_failure_consequence": "",
        "contents": {
            "currency_base_units": 20,
            "items": [
                {
                    "name": "Tarnished Silver Locket",
                    "category": "Valuable",
                    "quantity": 1,
                    "description": "A small worn locket.",
                    "value_base_units": 12,
                }
            ],
        },
    }


class GeminiServiceTests(unittest.TestCase):
    def test_new_game_starter_item_parser_preserves_firearm_metadata(self) -> None:
        items = _parse_new_game_starter_items(
            [
                {
                    "name": "Service Pistol",
                    "category": "Weapon",
                    "quantity": 1,
                    "description": "A compact sidearm.",
                    "value_base_units": 50,
                    "source_index": 0,
                    "weapon_hands": "one-handed",
                    "damage": "1d6",
                    "attack_skill": "Ranged",
                    "attack_range_feet": 60,
                    "ammunition_type_required": "9mm Round",
                    "clip_size": 12,
                    "bullets_per_attack": 2,
                }
            ]
        )

        self.assertEqual(items[0]["ammunition_type_required"], "9mm Round")
        self.assertEqual(items[0]["clip_size"], 12)
        self.assertEqual(items[0]["bullets_per_attack"], 2)

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

        self.assertIn("The road bends into fog.", result.narrative_text)
        self.assertIn("What do you do now?", result.narrative_text)
        self.assertEqual(len(result.suggested_actions), 3)
        self.assertEqual(call["model"], "gemini-2.5-flash")
        self.assertEqual(call["config"]["response_mime_type"], "application/json")
        self.assertEqual(
            call["config"]["response_json_schema"],
            STORY_RESPONSE_JSON_SCHEMA,
        )
        self.assertEqual(
            call["config"]["safety_settings"][0]["threshold"],
            "OFF",
        )
        self.assertEqual(
            call["config"]["thinking_config"]["thinking_budget"],
            1024,
        )
        self.assertNotIn("max_output_tokens", call["config"])

    def test_story_request_applies_selected_ai_modes_to_config(self) -> None:
        fake_client_class = self._install_fake_genai_client(
            json.dumps(
                {
                    "response": "A clipped answer.",
                    "suggested_actions": [],
                    "events": [],
                    "out_of_game": False,
                }
            )
        )

        try:
            service = GeminiNarrationService(
                GeminiSettings(
                    api_key="test-key",
                    model="gemini-3.1-flash-lite",
                )
            )
            service.generate_story_response(
                {
                    "packet_type": "story_turn",
                    "state": {
                        "player_ai_preferences": {
                            "model_intelligence": "smarter",
                            "model_tone": "serious",
                            "response_length": "super_brief",
                            "allowed_content_categories": [
                                "HARM_CATEGORY_HARASSMENT"
                            ],
                        }
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        call = fake_client_class.last_client.models.calls[0]
        safety_by_category = {
            setting["category"]: setting["threshold"]
            for setting in call["config"]["safety_settings"]
        }

        self.assertEqual(
            call["config"]["thinking_config"],
            {"thinking_level": "high"},
        )
        self.assertEqual(call["config"]["max_output_tokens"], 1536)
        self.assertEqual(safety_by_category["HARM_CATEGORY_HARASSMENT"], "OFF")
        self.assertEqual(
            safety_by_category["HARM_CATEGORY_DANGEROUS_CONTENT"],
            "BLOCK_LOW_AND_ABOVE",
        )
        self.assertIn("cold, restrained, serious voice", call["contents"])
        self.assertIn("as short as practical", call["contents"])
        self.assertIn("unchecked categories", call["contents"])

    def test_skill_check_plan_request_uses_lightweight_schema(self) -> None:
        fake_client_class = self._install_fake_genai_client(
            json.dumps(
                {
                    "checks": [
                        {
                            "skill_name": "Foraging",
                            "difficulty": "hard",
                            "reason": "Searching unstable cliffs for rare herbs.",
                        }
                    ],
                    "relevant_tags": ["exploration", "skill", "uncertainty"],
                }
            )
        )

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            result = service.plan_story_skill_checks(
                {
                    "packet_type": "story_turn",
                    "player_command": "Search the cliff face for rare herbs.",
                    "state": {
                        "scene": {"location": "Wind Cliff"},
                        "gm_secrets": {
                            "active": [
                                {
                                    "secret_id": "cliff_is_trapped",
                                    "details": "A concealed wire triggers a rockfall.",
                                    "status": "active",
                                }
                            ]
                        },
                        "skills": {
                            "known_skills": [
                                {"name": "Foraging", "level": 2, "bonus": 4}
                            ],
                            "recent_checks": [],
                        },
                    },
                    "recent_history": [
                        {"kind": "story", "content": "The cliff wind rises."}
                    ],
                }
            )
        finally:
            self._remove_fake_genai_client()

        call = fake_client_class.last_client.models.calls[0]

        self.assertEqual(result.checks[0]["skill_name"], "Foraging")
        self.assertEqual(result.checks[0]["difficulty"], "hard")
        self.assertEqual(result.relevant_tags, ["exploration", "skill", "uncertainty"])
        self.assertEqual(call["config"]["response_mime_type"], "application/json")
        self.assertEqual(
            call["config"]["response_json_schema"],
            SKILL_CHECK_PLAN_RESPONSE_JSON_SCHEMA,
        )
        self.assertIn("skill_check_planning", call["contents"])
        self.assertNotIn('"inventory":', call["contents"].casefold())
        self.assertIn("cliff_is_trapped", call["contents"])
        self.assertIn("concealed wire", call["contents"])
        self.assertIn("Available context tags:", call["contents"])
        self.assertIn('"relevant_tags"', call["contents"])

    def test_parse_skill_check_plan_response_normalizes_checks(self) -> None:
        result = parse_skill_check_plan_response(
            json.dumps(
                {
                    "checks": [
                        {
                            "skill_name": "Alchemy",
                            "dc": 18,
                            "difficulty": "hard",
                            "reason": "Identifying an unstable reagent.",
                        },
                        {
                            "skill_name": "Alchemy",
                            "difficulty": "easy",
                        },
                        {"difficulty": "normal"},
                    ],
                    "relevant_tags": ["alchemy", "skill", "not-a-real-tag", "alchemy"],
                }
            )
        )

        self.assertEqual(len(result.checks), 1)
        self.assertEqual(result.checks[0]["skill_name"], "Alchemy")
        self.assertEqual(result.checks[0]["dc"], 18)
        self.assertNotIn("difficulty", result.checks[0])
        self.assertIn("unstable reagent", result.checks[0]["reason"])
        self.assertEqual(result.relevant_tags, ["alchemy", "skill"])

    def test_parse_skill_check_plan_missing_tags_uses_keyword_fallback(self) -> None:
        result = parse_skill_check_plan_response(json.dumps({"checks": []}))

        self.assertIsNone(result.relevant_tags)

    def test_routine_action_drops_planned_skill_checks(self) -> None:
        result = parse_skill_check_plan_response(
            json.dumps(
                {
                    "checks": [
                        {
                            "skill_name": "Navigation",
                            "difficulty": "easy",
                            "reason": "Walking across town.",
                        }
                    ],
                    "relevant_tags": ["skill", "uncertainty", "travel"],
                }
            )
        )
        filtered = _filter_unwarranted_planned_skill_checks(
            result,
            {
                "packet_type": "story_turn",
                "player_command": "Walk to the market.",
            },
        )

        self.assertEqual(filtered.checks, [])
        self.assertEqual(filtered.relevant_tags, ["travel"])

    def test_risky_action_keeps_planned_skill_checks(self) -> None:
        result = parse_skill_check_plan_response(
            json.dumps(
                {
                    "checks": [
                        {
                            "skill_name": "Stealth",
                            "difficulty": "normal",
                            "reason": "Sneaking through a watched market.",
                        }
                    ],
                    "relevant_tags": ["skill", "uncertainty"],
                }
            )
        )
        filtered = _filter_unwarranted_planned_skill_checks(
            result,
            {
                "packet_type": "story_turn",
                "player_command": "Sneak through the market without being noticed.",
            },
        )

        self.assertEqual(filtered.checks, result.checks)

    def test_routine_action_drops_story_skill_check_events(self) -> None:
        result = parse_gemini_story_response(
            json.dumps(
                {
                    "response": "You make your way to the market.",
                    "suggested_actions": [],
                    "events": [
                        {
                            "type": "SkillCheckRequestedEvent",
                            "payload": {
                                "skill_name": "Navigation",
                                "difficulty": "easy",
                            },
                        },
                        {
                            "type": "StatusUpdatedEvent",
                            "payload": {
                                "location": "Market",
                                "minutes_passed": 10,
                                "weather": "Clear",
                            },
                        },
                    ],
                    "out_of_game": False,
                }
            )
        )
        filtered = _drop_unwarranted_skill_check_events(
            result,
            {
                "packet_type": "story_turn",
                "player_command": "Go to the market.",
            },
        )

        self.assertEqual(
            [event["type"] for event in filtered.suggested_events],
            ["StatusUpdatedEvent"],
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

    def test_story_schema_accepts_container_metadata_and_lifecycle_events(self) -> None:
        response = {
            "response": "You secure the still-closed pouch.",
            "suggested_actions": ["Open the pouch."],
            "events": [
                {
                    "type": "InventoryItemAddedEvent",
                    "payload": {
                        "item_type": "Container",
                        "item_name": "Stolen Coin Pouch",
                        "description": "A tied leather pouch.",
                        "amount": 1,
                        "value_base_units": 2,
                        "container": _container_metadata(),
                    },
                },
                {
                    "type": "ContainerOpenedEvent",
                    "payload": {"container_name": "Stolen Coin Pouch"},
                },
                {
                    "type": "ContainerContentsTakenEvent",
                    "payload": {"container_name": "Stolen Coin Pouch"},
                },
            ],
            "out_of_game": False,
        }

        self.assertEqual(
            _json_schema_shape_errors(response, STORY_RESPONSE_JSON_SCHEMA),
            [],
        )
        self.assertIn("ContainerOpenedEvent", KNOWN_EVENT_TYPE_NAMES)
        self.assertIn("ContainerContentsTakenEvent", KNOWN_EVENT_TYPE_NAMES)

    def test_story_request_does_not_invent_check_when_planner_selected_none(self) -> None:
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
        event_types = [event["type"] for event in result.suggested_events]

        self.assertNotIn("SkillCheckRequestedEvent", event_types)
        self.assertIn("SkillXpAddedEvent", event_types)

    def test_story_request_does_not_fuzzy_infer_mining_check(self) -> None:
        self._install_fake_genai_client(
            json.dumps(
                {
                    "response": (
                        "You work the exposed vein and load a satisfying heap of "
                        "iron-bearing stone into the cart."
                    ),
                    "suggested_actions": [],
                    "events": [
                        {
                            "type": "SkillXpAddedEvent",
                            "payload": {"skill_name": "Mining", "xp_amount": 1},
                        },
                        {
                            "type": "InventoryItemAddedEvent",
                            "payload": {
                                "item_type": "Material",
                                "item_name": "Raw Iron Ore",
                                "description": "Dense iron-bearing ore from the foothills.",
                                "amount": 1,
                                "value_base_units": 100,
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
                    "player_command": (
                        "I will mine some more of the vein and gather some of the "
                        "mineral and load it into the cart."
                    ),
                    "state": {
                        "skills": {
                            "known_skills": [
                                {"name": "Foraging"},
                                {"name": "Mining"},
                            ]
                        }
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        event_types = [event["type"] for event in result.suggested_events]

        self.assertNotIn("SkillCheckRequestedEvent", event_types)
        self.assertIn("SkillXpAddedEvent", event_types)

    def test_story_request_does_not_fuzzy_infer_custom_skill_check(self) -> None:
        self._install_fake_genai_client(
            json.dumps(
                {
                    "response": "You set chisel to stone and begin the delicate work.",
                    "suggested_actions": [],
                    "events": [
                        {
                            "type": "StatusUpdatedEvent",
                            "payload": {
                                "location": "Rune Vault",
                                "minutes_passed": 15,
                                "weather": "Still",
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
                    "player_command": (
                        "I carefully carve shadow runes into the basalt seal."
                    ),
                    "state": {
                        "skills": {
                            "known_skills": [
                                {"name": "Foraging"},
                                {"name": "Shadow Rune Carving"},
                            ]
                        }
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        self.assertNotIn(
            "SkillCheckRequestedEvent",
            [event["type"] for event in result.suggested_events],
        )

    def test_story_request_drops_direct_rewards_from_unopened_container(self) -> None:
        self._install_fake_genai_client(
            json.dumps(
                {
                    "response": (
                        "You open the Stolen Coin Pouch and see coins beside a "
                        "tarnished locket."
                    ),
                    "suggested_actions": ["Take the contents of the pouch."],
                    "events": [
                        {
                            "type": "CurrencyChangedEvent",
                            "payload": {"base_unit_amount": 35},
                        },
                        {
                            "type": "InventoryItemAddedEvent",
                            "payload": {
                                "item_type": "Valuable",
                                "item_name": "Tarnished Silver Locket",
                                "description": "A small worn locket.",
                                "amount": 1,
                                "value_base_units": 12,
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
                    "player_command": "Open the Stolen Coin Pouch.",
                    "state": {
                        "inventory": {
                            "items": [
                                {
                                    "name": "Stolen Coin Pouch",
                                    "category": "Container",
                                    "quantity": 1,
                                    "metadata": {
                                        "item_type": "Container",
                                        "container": _container_metadata(),
                                    },
                                }
                            ]
                        }
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        event_types = [event["type"] for event in result.suggested_events]

        self.assertNotIn("CurrencyChangedEvent", event_types)
        self.assertNotIn("InventoryItemAddedEvent", event_types)

    def test_story_request_keeps_container_events_but_drops_duplicate_rewards(self) -> None:
        self._install_fake_genai_client(
            json.dumps(
                {
                    "response": "You open the pouch and pocket its contents.",
                    "suggested_actions": [],
                    "events": [
                        {
                            "type": "ContainerOpenedEvent",
                            "payload": {"container_name": "Stolen Coin Pouch"},
                        },
                        {
                            "type": "ContainerContentsTakenEvent",
                            "payload": {"container_name": "Stolen Coin Pouch"},
                        },
                        {
                            "type": "CurrencyChangedEvent",
                            "payload": {"base_unit_amount": 35},
                        },
                        {
                            "type": "InventoryItemAddedEvent",
                            "payload": {
                                "item_type": "Valuable",
                                "item_name": "Tarnished Silver Locket",
                                "description": "A duplicate direct reward.",
                                "amount": 1,
                                "value_base_units": 12,
                            },
                        },
                    ],
                    "out_of_game": False,
                }
            )
        )

        try:
            result = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            ).generate_story_response(
                {
                    "packet_type": "story_turn",
                    "player_command": "Open the pouch and take everything inside.",
                    "state": {"inventory": {"items": []}},
                }
            )
        finally:
            self._remove_fake_genai_client()

        event_types = [event["type"] for event in result.suggested_events]

        self.assertIn("ContainerOpenedEvent", event_types)
        self.assertIn("ContainerContentsTakenEvent", event_types)
        self.assertNotIn("CurrencyChangedEvent", event_types)
        self.assertNotIn("InventoryItemAddedEvent", event_types)

    def test_story_request_does_not_inject_skill_check_from_narration_or_actions(self) -> None:
        self._install_fake_genai_client(
            json.dumps(
                {
                    "response": (
                        "The vendor accepts your silver coin, ladles a bowl of "
                        "vegetable stew, and mentions the herbs were gathered fresh."
                    ),
                    "suggested_actions": [
                        "Ask about the herbs in the stew.",
                        "Prepare your own meal tomorrow.",
                    ],
                    "events": [
                        {
                            "type": "CurrencyChangedEvent",
                            "payload": {"base_unit_amount": -10},
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
                    "player_command": (
                        '"That would be lovely, thank you. Here is a silver piece." '
                        "Kit will sit down to eat the stew and drink water."
                    ),
                    "state": {
                        "skills": {
                            "known_skills": [
                                {"name": "Foraging"},
                                {"name": "Alchemy"},
                            ]
                        }
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        event_types = [event["type"] for event in result.suggested_events]

        self.assertEqual(event_types, ["CurrencyChangedEvent", "StatusUpdatedEvent"])

    def test_story_request_does_not_treat_looking_for_dinner_as_a_skill_check(self) -> None:
        self._install_fake_genai_client(
            json.dumps(
                {
                    "response": (
                        "You find a modest food stall where a vendor is serving "
                        "vegetable stew with fresh herbs."
                    ),
                    "suggested_actions": [
                        "Ask what the stew costs.",
                        "Watch the market wind down.",
                    ],
                    "events": [
                        {
                            "type": "StatusUpdatedEvent",
                            "payload": {
                                "location": "Zoclar Market",
                                "minutes_passed": 10,
                                "weather": "Clear",
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
                    "player_command": "Look for a modest dinner in the market.",
                    "state": {
                        "skills": {
                            "known_skills": [
                                {"name": "Perception"},
                                {"name": "Foraging"},
                            ]
                        }
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        event_types = [event["type"] for event in result.suggested_events]

        self.assertEqual(event_types, ["StatusUpdatedEvent"])

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
                                "description": "Pale blue salt that cools and steadies.",
                                "location": "Blue cave walls near still pools",
                                "uses": ["Sleep draughts"],
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
        self.assertEqual(inventory_events[0]["payload"]["item_type"], "Item")
        self.assertEqual(
            inventory_events[0]["payload"]["description"],
            "Pale blue salt that cools and steadies.",
        )
        self.assertEqual(inventory_events[0]["payload"]["value_base_units"], 1)

    def test_story_request_trims_narrated_collection_without_inventory_event(self) -> None:
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

        self.assertNotIn("SkillCheckRequestedEvent", event_types)
        self.assertIn("SkillXpAddedEvent", event_types)
        self.assertEqual(inventory_events, [])
        self.assertNotIn("Assorted Foraged Specimens", result.narrative_text)
        self.assertNotIn("bounty of fresh, high-quality specimens", result.narrative_text)
        self.assertNotIn("Your basket is brimming", result.narrative_text)
        self.assertNotIn("quite the collection", result.narrative_text)
        self.assertIn("What do you do now?", result.narrative_text)

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

    def test_story_schema_allows_active_task_updates_with_only_changed_fields(self) -> None:
        valid_response = {
            "response": "You make a note to prepare more supplies.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "ActiveTaskUpsertedEvent",
                    "payload": {
                        "name": "Prepare spare lockpicks",
                        "category": "Personal Goal",
                        "status": "Active",
                        "description": "Create more lockpicks for future work.",
                        "requester": "Self",
                        "location": "Player's Workshop",
                        "reward": "N/A",
                        "due_date": "N/A",
                        "due_elapsed_minutes": -1,
                    },
                }
            ],
            "out_of_game": False,
        }
        partial_update_response = {
            "response": "You make a note to prepare more supplies.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "ActiveTaskUpsertedEvent",
                    "payload": {
                        "name": "Prepare spare lockpicks",
                        "description": "Create more lockpicks for future work.",
                    },
                }
            ],
            "out_of_game": False,
        }

        self.assertEqual(
            _json_schema_shape_errors(valid_response, STORY_RESPONSE_JSON_SCHEMA),
            [],
        )
        self.assertEqual(
            _json_schema_shape_errors(partial_update_response, STORY_RESPONSE_JSON_SCHEMA),
            [],
        )

        extra_notes_response = {
            "response": "You make a note to prepare more supplies.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "ActiveTaskUpsertedEvent",
                    "payload": {
                        "name": "Prepare spare lockpicks",
                        "category": "Personal Goal",
                        "status": "Active",
                        "description": "Create more lockpicks for future work.",
                        "requester": "Self",
                        "location": "Player's Workshop",
                        "reward": "N/A",
                        "due_date": "N/A",
                        "due_elapsed_minutes": -1,
                        "Notes": "This field does not belong on active tasks.",
                    },
                }
            ],
            "out_of_game": False,
        }
        self.assertIn(
            "$.events[0] did not match any allowed schema",
            _json_schema_shape_errors(extra_notes_response, STORY_RESPONSE_JSON_SCHEMA),
        )

    def test_story_schema_requires_complete_npc_fields_without_disposition(self) -> None:
        valid_response = {
            "response": "The bartender looks up from the chipped mug.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "NpcUpsertedEvent",
                    "payload": {
                        "display_name": "Bartender",
                        "role": "Tavern bartender",
                        "location": "Copper Kettle",
                        "public_description": "A tired bartender polishing cloudy glasses.",
                        "player_facing_information": (
                            "The bartender tends the Copper Kettle and watches the room."
                        ),
                        "knowledge_scope": ["Local tavern gossip"],
                        "known_facts": ["The bartender knows the regular patrons."],
                    },
                }
            ],
            "out_of_game": False,
        }
        missing_role_response = {
            "response": "The bartender looks up from the chipped mug.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "NpcUpsertedEvent",
                    "payload": {
                        "display_name": "Bartender",
                        "location": "Copper Kettle",
                        "public_description": "A tired bartender polishing cloudy glasses.",
                        "player_facing_information": (
                            "The bartender tends the Copper Kettle and watches the room."
                        ),
                        "knowledge_scope": ["Local tavern gossip"],
                        "known_facts": ["The bartender knows the regular patrons."],
                    },
                }
            ],
            "out_of_game": False,
        }
        extra_disposition_response = {
            "response": "The bartender looks up from the chipped mug.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "NpcUpsertedEvent",
                    "payload": {
                        "display_name": "Bartender",
                        "role": "Tavern bartender",
                        "location": "Copper Kettle",
                        "public_description": "A tired bartender polishing cloudy glasses.",
                        "player_facing_information": (
                            "The bartender tends the Copper Kettle and watches the room."
                        ),
                        "knowledge_scope": ["Local tavern gossip"],
                        "known_facts": ["The bartender knows the regular patrons."],
                        "disposition": "Friendly",
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
            _json_schema_shape_errors(missing_role_response, STORY_RESPONSE_JSON_SCHEMA),
        )
        self.assertIn(
            "$.events[0] did not match any allowed schema",
            _json_schema_shape_errors(extra_disposition_response, STORY_RESPONSE_JSON_SCHEMA),
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
                        "description": "Waxy tallow threaded with moss-green veins.",
                        "location": "Damp shaded valley crevices",
                        "uses": ["stabilizing volatile mixtures"],
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
        self.assertIn("SecretUpsertedEvent", KNOWN_EVENT_TYPE_NAMES)
        self.assertNotIn("MerchantInterfaceRequestedEvent", KNOWN_EVENT_TYPE_NAMES)

    def test_event_schema_matches_advertised_event_types(self) -> None:
        schema_event_types = [
            branch["properties"]["type"]["enum"][0]
            for branch in EVENT_RESPONSE_SCHEMA["anyOf"]
        ]

        self.assertEqual(sorted(schema_event_types), sorted(KNOWN_EVENT_TYPE_NAMES))
        self.assertEqual(len(schema_event_types), len(set(schema_event_types)))

    def test_new_game_schema_allows_only_setup_event_types(self) -> None:
        schema_event_types = {
            branch["properties"]["type"]["enum"][0]
            for branch in NEW_GAME_EVENT_RESPONSE_SCHEMA["anyOf"]
        }

        self.assertEqual(
            schema_event_types,
            {
                "NpcUpsertedEvent",
                "ActiveTaskUpsertedEvent",
                "MusicChangedEvent",
            },
        )
        self.assertIn("gm_secrets", NEW_GAME_RESPONSE_JSON_SCHEMA["properties"])
        self.assertIn("gm_secrets", NEW_GAME_RESPONSE_JSON_SCHEMA["required"])
        self.assertIn("known_crafting_items", NEW_GAME_RESPONSE_JSON_SCHEMA["properties"])
        self.assertIn("known_crafting_recipes", NEW_GAME_RESPONSE_JSON_SCHEMA["properties"])
        self.assertIn("known_crafting_items", NEW_GAME_RESPONSE_JSON_SCHEMA["required"])
        self.assertIn("known_crafting_recipes", NEW_GAME_RESPONSE_JSON_SCHEMA["required"])
        self.assertNotIn(
            "maxItems",
            NEW_GAME_RESPONSE_JSON_SCHEMA["properties"]["known_crafting_items"],
        )
        self.assertNotIn(
            "maxItems",
            NEW_GAME_RESPONSE_JSON_SCHEMA["properties"]["known_crafting_recipes"],
        )
        self.assertIs(
            NEW_GAME_RESPONSE_JSON_SCHEMA["properties"]["events"]["items"],
            NEW_GAME_EVENT_RESPONSE_SCHEMA,
        )
        secret_schema = NEW_GAME_RESPONSE_JSON_SCHEMA["properties"]["gm_secrets"]["items"]
        self.assertNotIn("status", secret_schema["properties"])
        self.assertIn(
            "$.status is not allowed",
            _json_schema_shape_errors(
                {
                    "secret_id": "station_master_is_villain",
                    "title": "Station Master's Identity",
                    "details": "The station master directs the canal murders.",
                    "reveal_condition": "The player deciphers the ledger.",
                    "related_npc_ids": ["station_master"],
                    "related_locations": ["Rainmarket Station"],
                    "status": "active",
                },
                secret_schema,
            ),
        )
        task_branch = next(
            branch
            for branch in NEW_GAME_EVENT_RESPONSE_SCHEMA["anyOf"]
            if branch["properties"]["type"]["enum"] == ["ActiveTaskUpsertedEvent"]
        )
        self.assertNotIn("status", task_branch["properties"]["payload"]["properties"])
        self.assertIn(
            "$.payload.status is not allowed",
            _json_schema_shape_errors(
                {
                    "type": "ActiveTaskUpsertedEvent",
                    "payload": {
                        "name": "Opening Lead",
                        "status": "Active",
                    },
                },
                task_branch,
            ),
        )

    def test_story_schema_accepts_combat_started_event(self) -> None:
        valid_response = {
            "response": "The ambush begins.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "CombatStartedEvent",
                    "payload": {
                        "description": "Two bandits rush from the alley.",
                        "enemies": [
                            {
                                "name": "Bandit",
                                "health": 8,
                                "armor_rating": 12,
                                "to_hit_bonus": 3,
                                "initiative_bonus": 2,
                                "personality": "aggressive",
                                "weapon_name": "Rusty Knife",
                                "ammunition_type_required": "",
                                "clip_size": 0,
                                "clip_ammo": 0,
                                "bullets_per_attack": 0,
                                "reserve_ammo": 0,
                                "damage": "1d6",
                                "loot": ["Rusty Knife"],
                            }
                        ],
                        "allies": [
                            {
                                "name": "Mira",
                                "health": 10,
                                "armor_rating": 11,
                                "to_hit_bonus": 2,
                                "initiative_bonus": 1,
                                "personality": "intelligent",
                                "weapon_name": "Shortbow",
                                "ammunition_type_required": "Arrow",
                                "clip_size": 1,
                                "clip_ammo": 1,
                                "bullets_per_attack": 1,
                                "reserve_ammo": 12,
                                "damage": "1d4",
                            }
                        ],
                    },
                }
            ],
            "out_of_game": False,
        }
        missing_enemy_stats_response = {
            "response": "The ambush begins.",
            "suggested_actions": [],
            "events": [
                {
                    "type": "CombatStartedEvent",
                    "payload": {
                        "description": "A bandit rushes from the alley.",
                        "enemies": [
                            {
                                "name": "Bandit",
                                "health": 8,
                                "damage": "1d6",
                                "loot": ["Rusty Knife"],
                            }
                        ],
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
            _json_schema_shape_errors(
                missing_enemy_stats_response,
                STORY_RESPONSE_JSON_SCHEMA,
            ),
        )

    def test_story_schema_accepts_private_secret_upsert_event(self) -> None:
        valid_response = {
            "response": "The station master closes the ledger before you can read it.",
            "suggested_actions": ["Ask about the ledger."],
            "events": [
                {
                    "type": "SecretUpsertedEvent",
                    "payload": {
                        "secret_id": "station_master_is_villain",
                        "title": "Station Master's Identity",
                        "details": "The station master directs the canal murders.",
                        "reveal_condition": "The player deciphers the black ledger.",
                        "related_npc_ids": ["station_master"],
                        "related_locations": ["Rainmarket Station"],
                        "status": "active",
                    },
                }
            ],
            "out_of_game": False,
        }

        self.assertEqual(
            _json_schema_shape_errors(valid_response, STORY_RESPONSE_JSON_SCHEMA),
            [],
        )

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
                    "gm_secrets": [],
                    "locations": [],
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
                            "source_index": -1,
                        }
                        for index in range(5)
                    ],
                    "known_crafting_items": [],
                    "known_crafting_recipes": [],
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
        self.assertEqual(
            call["config"]["safety_settings"][0]["threshold"],
            "OFF",
        )

    def test_new_game_request_applies_wizard_ai_modes(self) -> None:
        fake_client_class = self._install_fake_genai_client("{}")

        try:
            service = GeminiNarrationService(
                GeminiSettings(
                    api_key="test-key",
                    model="gemini-3.1-flash-lite",
                )
            )
            service.generate_new_game_world(
                {
                    "packet_type": "new_game_setup",
                    "player_ai_preferences": {
                        "model_intelligence": "smarter",
                        "model_tone": "quirky",
                        "response_length": "super_brief",
                        "allowed_content_categories": [
                            "HARM_CATEGORY_DANGEROUS_CONTENT"
                        ],
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        call = fake_client_class.last_client.models.calls[0]
        safety_by_category = {
            setting["category"]: setting["threshold"]
            for setting in call["config"]["safety_settings"]
        }

        self.assertEqual(
            call["config"]["thinking_config"],
            {"thinking_level": "high"},
        )
        self.assertEqual(call["config"]["max_output_tokens"], 6144)
        self.assertEqual(
            safety_by_category["HARM_CATEGORY_DANGEROUS_CONTENT"],
            "OFF",
        )
        self.assertEqual(
            safety_by_category["HARM_CATEGORY_HARASSMENT"],
            "BLOCK_LOW_AND_ABOVE",
        )
        self.assertIn("playful, occasionally zany voice", call["contents"])
        self.assertIn("Response length — Super Brief", call["contents"])

    def test_new_game_repairs_banned_terms_until_response_is_clean(self) -> None:
        def response_for(
            *,
            world_summary: str,
            start_location: str,
            npc_name: str,
            item_name: str,
            intro: str,
        ) -> str:
            return json.dumps(
                {
                    "selected_genre": "Detective mystery",
                    "world_summary": world_summary,
                    "locations": [
                        {
                            "name": start_location,
                            "description": f"{start_location} overlooks the tram line.",
                            "x_miles": 0,
                            "y_miles": 0,
                            "terrain": "Rainy streets",
                            "travel_multiplier": 1.0,
                            "travel_notes": "Morning tram traffic is heavy.",
                        }
                    ],
                    "gm_secrets": [],
                    "start_location": start_location,
                    "starting_calendar": {},
                    "weather": "Rain",
                    "character": {
                        "name": "Mara Vale",
                        "appearance": "A detective in a dark coat.",
                        "backstory": f"Known for cases near {start_location}.",
                        "notes": "Keeps careful notes.",
                    },
                    "skills": [
                        {
                            "name": "Investigation",
                            "description": "Reading clues in crowded streets.",
                            "level": 4,
                        }
                    ],
                    "starting_items": [
                        {
                            "name": item_name if index == 0 else f"Case Item {index}",
                            "category": "Tool",
                            "quantity": 1,
                            "description": "Useful enough to keep.",
                            "value_base_units": index + 1,
                            "source_index": -1,
                        }
                        for index in range(5)
                    ],
                    "known_crafting_items": [],
                    "known_crafting_recipes": [],
                    "currency_denominations": [
                        {"name": "Credit", "plural_name": "Credits", "value": 1}
                    ],
                    "currency_description": "Credits.",
                    "starting_currency_balance_base_units": 10,
                    "introductory_message": intro,
                    "suggested_actions": [
                        "Check the case file.",
                        "Question the desk clerk.",
                        "Step into the rain.",
                    ],
                    "events": [
                        {
                            "type": "NpcUpsertedEvent",
                            "payload": {
                                "npc_id": "desk_clerk",
                                "display_name": npc_name,
                                "role": "Desk clerk",
                                "location": start_location,
                                "public_description": "A clerk with sharp eyes.",
                                "player_facing_information": "Handles the morning desk.",
                                "knowledge_scope": ["Station routine"],
                                "known_facts": ["Rain delays the tram line."],
                            },
                        }
                    ],
                }
            )

        fake_client_class = self._install_fake_genai_client(
            [
                response_for(
                    world_summary="Oakhaven is a rain-heavy city.",
                    start_location="Oakhaven Office",
                    npc_name="Mira Cross",
                    item_name="Oakhaven Casebook",
                    intro="Rain taps the Oakhaven office window. What do you do now?",
                ),
                response_for(
                    world_summary="Elias watches the Silas Vane district.",
                    start_location="Silas Vane Office",
                    npc_name="Elias Vane",
                    item_name="Vane Casebook",
                    intro="Rain taps the Silas Vane office window. What do you do now?",
                ),
                response_for(
                    world_summary="Brassgate is a rain-heavy city.",
                    start_location="Brassgate Office",
                    npc_name="Mira Cross",
                    item_name="Brassgate Casebook",
                    intro="Rain taps the Brassgate office window. What do you do now?",
                ),
            ]
        )

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            with self.assertLogs("ai_adventure.ai.gemini_service", level="WARNING") as logs:
                result = service.generate_new_game_world(
                    {
                        "packet_type": "new_game_setup",
                        "creative_ideas": {
                            "banned_terms": ["Oakhaven", "Elias", "Silas", "Vane"]
                        },
                    }
                )
        finally:
            self._remove_fake_genai_client()

        calls = fake_client_class.last_client.models.calls
        first_repair_prompt = str(calls[1]["contents"])
        second_repair_prompt = str(calls[2]["contents"])
        combined_output = json.dumps(
            {
                "world_summary": result.world_summary,
                "locations": result.locations,
                "start_location": result.start_location,
                "starting_items": result.finalized_starter_items,
                "introductory_message": result.introductory_message,
                "events": result.suggested_events,
                "raw_text": result.raw_text,
            },
            ensure_ascii=False,
        )

        self.assertEqual(len(calls), 3)
        self.assertIn("Attempt 1", first_repair_prompt)
        self.assertIn("Full forbidden terms list", first_repair_prompt)
        self.assertIn("Elias", first_repair_prompt)
        self.assertIn("Silas", first_repair_prompt)
        self.assertIn("Vane", first_repair_prompt)
        self.assertIn("Attempt 2", second_repair_prompt)
        self.assertIn(
            "Observed offending terms in the current JSON: Elias, Silas, Vane",
            second_repair_prompt,
        )
        self.assertIn("repair attempt 1/4 still contained", "\n".join(logs.output))
        self.assertIn("Brassgate", result.world_summary)
        for term in ("Oakhaven", "Elias", "Silas", "Vane"):
            self.assertNotIn(term, combined_output)

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
        self.assertIn("narration_tense_label", prompt)
        self.assertIn("narration_style_label", prompt)
        self.assertIn("Player-selected AI modes", prompt)
        self.assertIn("Model tone — Neutral", prompt)
        self.assertIn("Response length — Normal", prompt)
        self.assertIn("No Restrictions is selected", prompt)
        self.assertIn("Omniscient narration", prompt)
        self.assertIn("light Markdown", prompt)
        self.assertIn("italics for inner thoughts", prompt)
        self.assertIn("bold for the first mention", prompt)
        self.assertIn("display_name is the name", prompt)
        self.assertIn("multiple events with the same type", prompt)
        self.assertIn("one NpcUpsertedEvent for each", prompt)
        self.assertIn("Private GM secret memory", prompt)
        self.assertIn("state.gm_secrets.active", prompt)
        self.assertIn("SecretUpsertedEvent", prompt)
        self.assertIn("status='active'", prompt)
        self.assertIn("player_facing_information is shown directly", prompt)
        self.assertIn("knowledge_scope", prompt)
        self.assertIn("known_facts", prompt)
        self.assertIn("location must be a meaningful", prompt)
        self.assertNotIn("disposition", prompt)
        self.assertIn("ActiveTaskUpsertedEvent is shown directly", prompt)
        self.assertIn("requester='Self'", prompt)
        self.assertIn("due_date='N/A'", prompt)
        self.assertIn("due_elapsed_minutes=-1", prompt)
        self.assertIn("Do not add notes", prompt)
        self.assertIn("exact player-facing date and time", prompt)
        self.assertIn("Mature fictional content is allowed", prompt)
        self.assertIn("adults of legal drinking age", prompt)
        self.assertIn("Alcohol, drunken patrons, gambling", prompt)
        self.assertIn("fictional in-world terms", prompt)
        self.assertIn("do not use real-world slurs", prompt)
        self.assertIn("Creative naming boundary", prompt)
        self.assertIn("hard requirement", prompt)
        self.assertIn("Never use creative_ideas.banned_terms", prompt)
        self.assertIn("scan every string key and value", prompt)
        self.assertIn("Exact banned proper nouns", prompt)
        self.assertIn("bare category labels as final proper nouns", prompt)
        self.assertIn("the Police Department", prompt)
        self.assertIn("The Blue Wall", prompt)
        self.assertIn("Elara", prompt)
        self.assertIn("reuse that exact npc_id/internal identifier", prompt)
        self.assertIn("Spoken dialogue must use double quotation marks", prompt)
        self.assertIn("Do not use single quotation marks as the outer boundary", prompt)
        self.assertIn("Use single quotation marks only when", prompt)
        self.assertIn("Currency is stored as one integer", prompt)
        self.assertIn("payload.base_unit_amount", prompt)
        self.assertIn("Never use net_base_unit_amount", prompt)
        self.assertIn("35 copper coins worth of silver", prompt)
        self.assertIn("3 Silver Coins and 5 Copper Coins", prompt)
        self.assertIn("Every InventoryItemAddedEvent payload must include value_base_units", prompt)
        self.assertIn("Containers are inventory items", prompt)
        self.assertIn("ContainerContentsTakenEvent", prompt)
        self.assertIn("Python then transfers the exact stored contents once", prompt)
        self.assertIn("weapon_hands", prompt)
        self.assertIn("average damage strictly higher", prompt)
        self.assertIn("unarmed base damage of 1d4", prompt)
        self.assertIn("covers_body_parts", prompt)
        self.assertIn("armor_rating", prompt)
        self.assertIn("state.item_catalog.items is the master list", prompt)
        self.assertIn("CombatStartedEvent", prompt)
        self.assertIn("to_hit_bonus", prompt)
        self.assertIn("initiative_bonus", prompt)
        self.assertIn("ammunition_type_required", prompt)
        self.assertIn("personality", prompt)
        self.assertIn("Threat Levels", prompt)
        self.assertIn("non-intelligent NPC", prompt)
        self.assertIn("damage dice", prompt)
        self.assertIn("Combat tab", prompt)
        self.assertIn("ReagentDiscoveredEvent records Crafting tab knowledge", prompt)
        self.assertIn("useful items/materials", prompt)
        self.assertIn("RecipeDiscoveredEvent ingredients must be structured entries", prompt)
        self.assertIn("Only items with category Material, Ingredient, Reagent, Crafting Item", prompt)
        self.assertIn("Do not describe a successful bounty", prompt)
        self.assertIn("actions with meaningful uncertainty", prompt)
        self.assertIn("Do not request a check merely because", prompt)
        self.assertIn("resolved_checks_this_turn", prompt)
        self.assertIn("Do not request duplicate SkillCheckRequestedEvent", prompt)
        self.assertIn("low failed rolls should", prompt)
        self.assertIn("Follow the selected Response Length mode", prompt)
        self.assertIn("Routine movement, paying a known price", prompt)
        self.assertIn("do not create coin inventory", prompt)
        self.assertIn("must not include 'What do you do now?'", prompt)
        self.assertIn("Do not speak for the player character", prompt)
        self.assertIn("continuation_request.active", prompt)
        self.assertNotIn("double-bracket", prompt)
        self.assertNotIn("legacy_tag", prompt)
        self.assertNotIn("do_not_emit_legacy_tag", prompt)
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

    def test_parse_json_response_sanitizes_banned_creative_terms(self) -> None:
        raw_text = json.dumps(
            {
                "response": "The skyline of New Aethelgard catches the sun.",
                "suggested_actions": [
                    "Walk into New Aethelgard.",
                    "Ask about Aethelgard's mayor.",
                ],
                "events": [
                    {
                        "type": "StatusUpdatedEvent",
                        "payload": {
                            "location": "New Aethelgard",
                            "minutes_passed": "AUTO",
                            "weather": "Clear",
                        },
                    }
                ],
                "out_of_game": False,
            }
        )

        with self.assertLogs("ai_adventure.ai.gemini_service", level="WARNING") as logs:
            result = parse_gemini_story_response(raw_text)

        combined_output = json.dumps(
            {
                "narrative_text": result.narrative_text,
                "suggested_actions": result.suggested_actions,
                "suggested_events": result.suggested_events,
                "raw_text": result.raw_text,
            },
            ensure_ascii=False,
        )

        self.assertIn("banned creative term", "\n".join(logs.output))
        self.assertNotIn("Aethelgard", combined_output)
        self.assertIn("skyline of the city", result.narrative_text)
        self.assertEqual(
            result.suggested_events[0]["payload"]["location"],
            "the city",
        )

    def test_parse_json_response_strips_model_supplied_turn_prompt(self) -> None:
        raw_text = json.dumps(
            {
                "response": "The road bends into fog. What do you do now?",
                "suggested_actions": ["Follow the road."],
                "events": [],
                "out_of_game": False,
            }
        )

        result = parse_gemini_story_response(raw_text)

        self.assertEqual(
            result.narrative_text,
            "The road bends into fog.\n\nWhat do you do now?\n- Follow the road.",
        )

    def test_parse_json_response_uses_contextual_turn_prompt(self) -> None:
        raw_text = json.dumps(
            {
                "response": "The clue glittered under the desk.",
                "suggested_actions": ["Pocket the clue."],
                "events": [],
                "out_of_game": False,
            }
        )

        result = parse_gemini_story_response(
            raw_text,
            context_packet={
                "state": {
                    "player": {"name": "Iris Vale"},
                    "player_ai_preferences": {
                        "narration_tense": "past",
                        "narration_style": "first_person_limited",
                    },
                }
            },
        )

        self.assertIn("What did I do next?", result.narrative_text)
        self.assertNotIn("What do you do now?", result.narrative_text)

    def test_story_response_adds_fallback_actions_and_status_event(self) -> None:
        raw_text = json.dumps(
            {
                "response": "You clean up the shop and settle in for the evening.",
                "suggested_actions": [],
                "events": [],
                "out_of_game": False,
            }
        )
        self._install_fake_genai_client(raw_text)

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            result = service.generate_story_response(
                {
                    "packet_type": "story_turn",
                    "player_command": "Clean up and rest.",
                    "state": {
                        "scene": {
                            "location": "Kit's Karpentry",
                            "weather": "Clear",
                        },
                        "skills": {"known_skills": []},
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        self.assertEqual(len(result.suggested_actions), 3)
        self.assertIn("What do you do now?", result.narrative_text)
        self.assertIn("- Look around", result.narrative_text)
        self.assertEqual(result.suggested_events[0]["type"], "StatusUpdatedEvent")
        self.assertEqual(
            result.suggested_events[0]["payload"]["location"],
            "Kit's Karpentry",
        )
        self.assertEqual(
            result.suggested_events[0]["payload"]["minutes_passed"],
            "AUTO",
        )

    def test_continuation_request_does_not_inject_new_turn_events(self) -> None:
        raw_text = json.dumps(
            {
                "response": "The ink resolves into a second line of cramped notes.",
                "suggested_actions": ["Read the cramped notes."],
                "events": [],
                "out_of_game": False,
            }
        )
        self._install_fake_genai_client(raw_text)

        try:
            service = GeminiNarrationService(
                GeminiSettings(api_key="test-key", model="gemini-2.5-flash")
            )
            result = service.generate_story_response(
                {
                    "packet_type": "story_turn",
                    "player_command": "Open the book and search for clues.",
                    "continuation_request": {"active": True},
                    "state": {
                        "scene": {
                            "location": "Archive",
                            "weather": "Still",
                        },
                        "skills": {
                            "known_skills": [
                                {
                                    "name": "Investigation",
                                    "description": "Finding clues in written evidence.",
                                }
                            ]
                        },
                    },
                }
            )
        finally:
            self._remove_fake_genai_client()

        self.assertEqual(result.suggested_events, [])
        self.assertIn("The ink resolves", result.narrative_text)

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

    def test_story_formatting_preserves_markdown_blocks(self) -> None:
        formatted = format_story_message(
            "# Discoveries\n\n"
            "You meet **Mira Coppercup**. *Stay calm,* you think.\n\n"
            "- Ask about rumors.\n"
            "- Inspect the ledger."
        )

        self.assertIn("# Discoveries", formatted)
        self.assertIn("**Mira Coppercup**", formatted)
        self.assertIn("*Stay calm,* you think.", formatted)
        self.assertIn("- Ask about rumors.", formatted)

    def test_story_currency_phrasing_is_normalized_to_denominations(self) -> None:
        result = parse_gemini_story_response(
            json.dumps(
                {
                    "response": "You find 35 copper coins' worth of silver.",
                    "suggested_actions": ["Spend 35 base units at the stall."],
                    "events": [],
                }
            )
        )
        normalized = _normalize_visible_currency_phrasing(
            result,
            {
                "state": {
                    "currency": {
                        "denominations": [
                            {
                                "name": "Copper Coin",
                                "plural_name": "Copper Coins",
                                "value": 1,
                            },
                            {
                                "name": "Silver Coin",
                                "plural_name": "Silver Coins",
                                "value": 10,
                            },
                        ],
                    },
                },
            },
        )

        self.assertIn("3 Silver Coins and 5 Copper Coins", normalized.narrative_text)
        self.assertIn(
            "Spend 3 Silver Coins and 5 Copper Coins at the stall.",
            normalized.suggested_actions,
        )
        self.assertNotIn("copper coins' worth of silver", normalized.narrative_text)
        self.assertNotIn("base units", normalized.narrative_text)

    def test_build_and_parse_new_game_response(self) -> None:
        prompt = build_gemini_new_game_prompt(
            {
                "packet_type": "new_game_setup",
                "setup": {
                    "title": "Rainmarket",
                    "starting_task": {
                        "mode": "custom",
                        "task": {
                            "name": "Find the Canal Ledger",
                            "description": "",
                        },
                    },
                    "narration": {
                        "tense": "future",
                        "tense_label": "Future Tense",
                        "style": "first_person_limited",
                        "style_label": "First-Person Limited",
                    },
                },
            }
        )
        self.assertIn("setup.narration.tense_label", prompt)
        self.assertIn("setup.narration.style_label", prompt)
        self.assertIn("Limited styles", prompt)
        self.assertIn("Do not fall back to second-person wording", prompt)
        self.assertIn("third-person styles should use the player character's name", prompt)
        self.assertIn("light Markdown", prompt)
        self.assertIn("world_summary", prompt)
        self.assertIn("locations must be a player-known array", prompt)
        self.assertIn("starting location may be the only known location", prompt)
        self.assertIn("even six or more", prompt)
        self.assertIn("introductory_message may use", prompt)
        self.assertIn("setup.starting_task.mode", prompt)
        self.assertIn("ActiveTaskUpsertedEvent", prompt)
        self.assertIn("setup_packet.turn_prompt", prompt)
        self.assertIn("start_location_mode is exact", prompt)
        self.assertIn("copy that exact skill name", prompt)
        raw_text = json.dumps(
            {
                "selected_genre": "Realistic detective mystery",
                "world_summary": "Rainmarket is a canal city.",
                "gm_secrets": [
                    {
                        "secret_id": "station_master_is_villain",
                        "title": "Station Master's Identity",
                        "details": "The station master directs the canal murders.",
                        "reveal_condition": "The player deciphers the black ledger.",
                        "related_npc_ids": ["station_master"],
                        "related_locations": ["Rainmarket Station"],
                    }
                ],
                "locations": [
                    {
                        "name": "Rainmarket Station",
                        "description": (
                            "A crowded canal-side transit hub where official trade "
                            "uses Crowns and station bells set local custom."
                        ),
                        "x_miles": 12,
                        "y_miles": -4,
                        "terrain": "Canal streets",
                        "travel_multiplier": 0.9,
                        "travel_notes": "Crowded at morning and dusk.",
                    },
                    {
                        "name": "North Lock",
                        "description": "A guarded lock beyond the warehouse district.",
                        "x_miles": 7,
                        "y_miles": 3,
                        "terrain": "Cobblestone",
                        "travel_multiplier": 1.0,
                        "travel_notes": "The gate closes after dark.",
                    },
                ],
                "start_location": "Rainmarket Station, beneath the old canal clock",
                "calendar_settings": {
                    "days_per_week": 8,
                    "weeks_per_month": 5,
                    "months_per_year": 10,
                    "seasons_per_year": 2,
                    "day_names": [
                        "Bell",
                        "Canal",
                        "Ledger",
                        "Rain",
                        "Market",
                        "Lantern",
                        "Lock",
                        "Mist",
                    ],
                    "month_names": ["First Rain", "Second Rain"],
                    "seasons": [
                        {"name": "Wet", "weather_hint": "spring"},
                        {"name": "Cold", "weather_hint": "winter"},
                    ],
                    "time_display": "24_hour",
                },
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
                        "source_index": 0,
                    },
                    {
                        "name": "Rain-Dark Coat",
                        "category": "Clothing",
                        "quantity": 1,
                        "description": "A heavy coat suited to canal rain.",
                        "value_base_units": 25,
                        "source_index": 1,
                    },
                    {
                        "name": "Brass Magnifier",
                        "category": "Tool",
                        "quantity": 1,
                        "description": "A lens for reading small marks.",
                        "value_base_units": 18,
                        "source_index": 2,
                    },
                    {
                        "name": "Rail Warrant",
                        "category": "Document",
                        "quantity": 1,
                        "description": "A stamped warrant for station inquiries.",
                        "value_base_units": 0,
                        "source_index": 3,
                    },
                    {
                        "name": "Half-Crown Purse",
                        "category": "Currency",
                        "quantity": 1,
                        "description": "A modest purse of local money.",
                        "value_base_units": 12,
                        "source_index": 4,
                    },
                ],
                "known_crafting_items": [
                    {
                        "name": "Moonwater",
                        "category": "Material",
                        "description": "Water exposed to moonlight for dream work.",
                        "location": "Prepared under moonlight.",
                        "uses": ["sleep draughts", "gentle washes"],
                    },
                    {
                        "name": "Canal Salt",
                        "category": "Reagent",
                        "description": "Mineral salt from old lock gates.",
                        "location": "Rainmarket lockhouses.",
                        "uses": ["clarifying tinctures"],
                    },
                ],
                "known_crafting_recipes": [
                    {
                        "name": "Mistglass Tincture",
                        "ingredients": [
                            {
                                "reagent_name": "Moonwater",
                                "quantity": 1,
                                "measure_amount": 100,
                                "measure_unit": "mL",
                            },
                            {
                                "reagent_name": "Canal Salt",
                                "quantity": 1,
                                "measure_amount": 1,
                                "measure_unit": "pinch",
                            },
                        ],
                        "result": "Reveals faint hidden script.",
                        "notes": "Useful for ledger work.",
                    }
                ],
                "currency_denominations": [
                    {"name": "Bit", "plural_name": "Bits", "value": 1},
                    {"name": "Crown", "plural_name": "Crowns", "value": 12},
                    {"name": "Moonmark", "plural_name": "Moonmarks", "value": 37},
                ],
                "currency_description": "Crowns and moonmarks are common canal-city money.",
                "starting_currency_balance_base_units": 49,
                "introductory_message": "Rain falls on the station.",
                "suggested_actions": [
                    "Inspect the canal clock.",
                    "Question the station porter.",
                    "Review the case notebook.",
                ],
                "events": [
                    {
                        "type": "NpcUpsertedEvent",
                        "payload": {
                            "display_name": "Station Porter",
                            "role": "Porter",
                            "location": "Rainmarket Station",
                            "public_description": "A porter in a weathered blue coat.",
                            "player_facing_information": "The porter knows the platform schedule.",
                            "knowledge_scope": ["Station routines"],
                            "known_facts": ["The porter can identify the next train."],
                        },
                    }
                ],
            }
        )

        result = parse_gemini_new_game_response(raw_text)

        self.assertIn("world_summary", prompt)
        self.assertNotIn("world_lore", prompt)
        self.assertIn("Rainmarket", prompt)
        self.assertIn("fields_requiring_ai_invention", prompt)
        self.assertIn("blank/default placeholders", prompt)
        self.assertIn("high-priority style seeds", prompt)
        self.assertIn("hard requirement", prompt)
        self.assertIn("Never use creative_ideas.banned_terms", prompt)
        self.assertIn("scan every string key and value", prompt)
        self.assertIn("Exact banned proper nouns", prompt)
        self.assertIn("bare category labels as final proper nouns", prompt)
        self.assertIn("the Police Department", prompt)
        self.assertEqual(result.locations[0]["name"], "Rainmarket Station")
        self.assertEqual((result.locations[0]["x_miles"], result.locations[0]["y_miles"]), (0.0, 0.0))
        self.assertIn("setup.starting_locations", prompt)
        self.assertIn("do not parse starting locations out of ordinary setup prose", prompt)
        self.assertIn("location_mode is exact", prompt)
        self.assertIn("is_sublocation is true", prompt)
        self.assertIn("parent_location is set", prompt)
        self.assertIn("locations entry unchanged", prompt)
        self.assertIn("The Blue Wall", prompt)
        self.assertIn("gender_presentation_hint", prompt)
        self.assertIn("does not imply male", prompt)
        self.assertIn("selected_genre", prompt)
        self.assertIn("Do not default to fantasy", prompt)
        self.assertIn("Mature fictional content is allowed", prompt)
        self.assertIn("drunken patrons, gambling, brawls", prompt)
        self.assertIn("legal drinking age", prompt)
        self.assertIn("do not invent or use real-world slurs", prompt)
        self.assertIn(
            "not as instructions that the entire world must share the same theme",
            prompt,
        )
        self.assertIn("every institution being coin-themed", prompt)
        self.assertIn("MusicChangedEvent", prompt)
        self.assertIn("setup.starting_npcs", prompt)
        self.assertIn("start with no known NPCs", prompt)
        self.assertIn("Do not parse NPCs out of ordinary setup prose", prompt)
        self.assertIn("payload.public_description", prompt)
        self.assertNotIn("the captain, the engineer, and the weapons expert", prompt)
        self.assertIn("top-level gm_secrets array", prompt)
        self.assertIn("GM-only starting truths", prompt)
        self.assertNotIn("gm_secrets array with status", prompt)
        self.assertIn("start_location", prompt)
        self.assertIn("short and broad", prompt)
        self.assertIn("Y/N's Office", prompt)
        self.assertIn("does not need to start in a tavern", prompt)
        self.assertIn("starting_items must contain at least five", prompt)
        self.assertIn("has no maximum item count", prompt)
        self.assertIn("invent enough additional concrete items", prompt)
        self.assertIn("Do not return starter weapons with damage of 1d4", prompt)
        self.assertIn("at least 1d6 for ordinary one-handed weapons", prompt)
        self.assertIn("keep its mechanical fields instead of downgrading", prompt)
        self.assertNotIn("starter_inventory_contract is present it defines", prompt)
        self.assertIn("known_crafting_items and known_crafting_recipes", prompt)
        self.assertIn("not physical inventory", prompt)
        self.assertIn("alchemist, cook, engineer", prompt)
        self.assertEqual(
            NEW_GAME_RESPONSE_JSON_SCHEMA["properties"]["starting_items"]["minItems"],
            5,
        )
        starter_item_properties = NEW_GAME_RESPONSE_JSON_SCHEMA["properties"][
            "starting_items"
        ]["items"]["properties"]
        self.assertNotIn("container", starter_item_properties)
        self.assertIn(
            "new-game schema intentionally keeps starter inventory flat",
            prompt,
        )
        calendar_schema = NEW_GAME_RESPONSE_JSON_SCHEMA["properties"]["calendar_settings"]
        for list_field in ("day_names", "month_names", "seasons"):
            self.assertNotIn(
                "minItems",
                calendar_schema["properties"][list_field],
            )
            self.assertNotIn(
                "maxItems",
                calendar_schema["properties"][list_field],
            )
        self.assertIn("do not use the alias starting_inventory", prompt)
        self.assertIn("source_index", prompt)
        self.assertIn("item_request text", prompt)
        self.assertIn("convert it into the number of concrete", prompt)
        self.assertIn("Fuel instead of Starting Fuel Amount", prompt)
        self.assertIn("Put quantities in quantity, not name", prompt)
        self.assertIn("currency_denominations must", prompt)
        self.assertIn("starting_currency_balance_base_units", prompt)
        self.assertIn("game_state/currency.balance", prompt)
        self.assertIn("Do not create coin or purse", prompt)
        self.assertIn("do not need to be multiples or powers of 10", prompt)
        self.assertIn("Use CurrencyDefinedEvent only when a story event", prompt)
        self.assertIn("skills must contain every starting skill", prompt)
        self.assertIn("Preserve explicit custom player input exactly", prompt)
        self.assertIn("Do not rename, partially rename", prompt)
        self.assertIn("requires_ai_invention=true", prompt)
        self.assertIn("generalized gameplay capabilities", prompt)
        self.assertIn("Lore (Syndicate)", prompt)
        self.assertIn("rather than Syndicate Lore", prompt)
        self.assertNotIn("Do not reuse generic default names", prompt)
        self.assertNotIn("Primary Training", prompt)
        self.assertNotIn("Signature Expertise", prompt)
        self.assertIn("current_calendar", prompt)
        self.assertIn("do not mention autumn winds", prompt)
        self.assertIn("setup.calendar.ai_generated", prompt)
        self.assertIn("invent calendar_settings", prompt)
        self.assertIn("Do not copy the default Gregorian calendar", prompt)
        self.assertIn("generic fantasy/artisan defaults", prompt)
        self.assertIn("not hearth, market, lantern", prompt)
        self.assertIn("January-through-December", prompt)
        self.assertIn("Month 1/Month 2 placeholder", prompt)
        self.assertIn("do not use event_type", prompt)
        self.assertIn("API response schema defines the required JSON fields", prompt)
        self.assertNotIn("Return this JSON shape", prompt)
        self.assertEqual(result.world_summary, "Rainmarket is a canal city.")
        self.assertNotIn("world_lore", NEW_GAME_RESPONSE_JSON_SCHEMA["properties"])
        self.assertIn("Crowns", result.locations[0]["description"])
        self.assertEqual(
            result.gm_secrets[0]["secret_id"],
            "station_master_is_villain",
        )
        self.assertEqual(result.gm_secrets[0]["status"], "active")
        self.assertEqual(result.start_location, "Rainmarket Station")
        self.assertEqual(result.selected_genre, "Realistic detective mystery")
        self.assertEqual(result.calendar_settings["days_per_week"], 8)
        self.assertEqual(result.calendar_settings["time_display"], "24_hour")
        self.assertEqual(result.starting_calendar["season_hint"], "autumn")
        self.assertEqual(result.start_weather, "Clear, cold autumn wind.")
        self.assertEqual(result.finalized_character["name"], "Iris Vale")
        self.assertEqual(result.finalized_skills[0]["name"], "Canal Investigation")
        self.assertEqual(len(result.finalized_starter_items), 5)
        self.assertEqual(result.finalized_starter_items[0]["name"], "Case Notebook")
        self.assertEqual(result.finalized_starter_items[0]["value_base_units"], 4)
        self.assertEqual(result.finalized_starter_items[0]["source_index"], 0)
        self.assertEqual(result.known_crafting_items[0]["name"], "Moonwater")
        self.assertEqual(result.known_crafting_items[1]["category"], "Reagent")
        self.assertEqual(result.known_crafting_recipes[0]["name"], "Mistglass Tincture")
        self.assertEqual(
            result.known_crafting_recipes[0]["ingredients"][0]["measure_unit"],
            "mL",
        )
        self.assertEqual(result.finalized_currency_denominations[1]["name"], "Crown")
        self.assertEqual(result.finalized_currency_denominations[2]["value"], 37)
        self.assertEqual(
            result.finalized_currency_description,
            "Crowns and moonmarks are common canal-city money.",
        )
        self.assertEqual(result.finalized_starting_currency_balance_base_units, 49)
        self.assertIn("What do you do now?\n- Inspect the canal clock.", result.introductory_message)
        self.assertEqual(result.suggested_actions[0], "Inspect the canal clock.")
        self.assertEqual(result.suggested_events[0]["type"], "NpcUpsertedEvent")

    def test_parse_new_game_response_uses_setup_turn_prompt(self) -> None:
        raw_text = json.dumps(
            {
                "selected_genre": "Fantasy",
                "world_summary": "A small city of rooftops.",
                "gm_secrets": [],
                "start_location": "Kit's Abandoned Loft",
                "starting_calendar": {},
                "weather": "Clear",
                "character": {
                    "name": "Kit",
                    "appearance": "Practical clothes.",
                    "backstory": "Streetwise.",
                    "notes": "Careful.",
                },
                "skills": [],
                "starting_items": [],
                "known_crafting_items": [],
                "known_crafting_recipes": [],
                "currency_denominations": [{"name": "Copper", "plural_name": "Coppers", "value": 1}],
                "currency_description": "Copper coins.",
                "starting_currency_balance_base_units": 4,
                "introductory_message": "Kit wakes in the loft.",
                "suggested_actions": ["Check the window."],
                "locations": [],
                "events": [],
            }
        )

        result = parse_gemini_new_game_response(
            raw_text,
            setup_packet={"turn_prompt": "What does Kit do now?"},
        )

        self.assertIn("What does Kit do now?", result.introductory_message)
        self.assertNotIn("What do you do now?", result.introductory_message)

    def test_parse_new_game_starter_items_generalizes_setup_amount_names(self) -> None:
        raw_text = json.dumps(
            {
                "selected_genre": "Frontier survival",
                "world_summary": "A route across dry country.",
                "gm_secrets": [],
                "start_location": "Fuel Depot",
                "starting_calendar": {},
                "weather": "Hot",
                "character": {
                    "name": "Mara",
                    "appearance": "Dust-coated traveler.",
                    "backstory": "Keeps the rover moving.",
                    "notes": "Practical.",
                },
                "skills": [],
                "starting_items": [
                    {
                        "name": "Starting Fuel Amount",
                        "category": "Supply",
                        "quantity": 20,
                        "description": "Fuel for the rover.",
                        "value_base_units": 40,
                        "source_index": 0,
                    },
                    {
                        "name": "Starting Food Amount",
                        "category": "Supply",
                        "quantity": 7,
                        "description": "Shelf-stable travel food.",
                        "value_base_units": 14,
                        "source_index": 1,
                    },
                    {
                        "name": "Starting Water Quantity",
                        "category": "Supply",
                        "quantity": 5,
                        "description": "Clean water in sealed cans.",
                        "value_base_units": 10,
                        "source_index": 2,
                    },
                    {
                        "name": "Initial Ammo Count",
                        "category": "Ammunition",
                        "quantity": 12,
                        "description": "Ammunition for the old rifle.",
                        "value_base_units": 12,
                        "source_index": 3,
                    },
                    {
                        "name": "Rover Toolkit",
                        "category": "Tool",
                        "quantity": 1,
                        "description": "Tools for field repairs.",
                        "value_base_units": 35,
                        "source_index": 4,
                    },
                ],
                "known_crafting_items": [],
                "known_crafting_recipes": [],
                "currency_denominations": [
                    {"name": "Credit", "plural_name": "Credits", "value": 1}
                ],
                "currency_description": "Credits.",
                "starting_currency_balance_base_units": 10,
                "introductory_message": "The depot gate opens. What do you do now?",
                "events": [],
            }
        )

        result = parse_gemini_new_game_response(raw_text)

        self.assertEqual(
            [item["name"] for item in result.finalized_starter_items],
            ["Fuel", "Food", "Water", "Ammo", "Rover Toolkit"],
        )
        self.assertEqual(result.finalized_starter_items[0]["quantity"], 20)

    def test_parse_new_game_response_sanitizes_banned_creative_terms(self) -> None:
        raw_text = json.dumps(
            {
                "selected_genre": "Detective mystery",
                "world_summary": "New Aethelgard is a rain-heavy city.",
                "locations": [
                    {
                        "name": "New Aethelgard",
                        "description": "New Aethelgard has old elevated rails.",
                        "x_miles": 0,
                        "y_miles": 0,
                        "terrain": "City streets",
                        "travel_multiplier": 1.0,
                        "travel_notes": "Elevated trains cross the district.",
                    }
                ],
                "start_location": "New Aethelgard Office",
                "starting_calendar": {},
                "weather": "Clear",
                "character": {
                    "name": "Mara Vale",
                    "appearance": "A detective in a dark coat.",
                    "backstory": "Known for cases across New Aethelgard.",
                    "notes": "Avoids old Aethelgard habits.",
                },
                "skills": [
                    {
                        "name": "City Investigation",
                        "description": "Reading clues in New Aethelgard alleys.",
                        "level": 4,
                    }
                ],
                "starting_items": [
                    {
                        "name": "Aethelgard Casebook",
                        "category": "Tool",
                        "quantity": 1,
                        "description": "A notebook of New Aethelgard cases.",
                        "value_base_units": 1,
                        "source_index": -1,
                    },
                    {
                        "name": "Rain Coat",
                        "category": "Clothing",
                        "quantity": 1,
                        "description": "A coat for city rain.",
                        "value_base_units": 1,
                        "source_index": -1,
                    },
                    {
                        "name": "Magnifier",
                        "category": "Tool",
                        "quantity": 1,
                        "description": "A lens for evidence.",
                        "value_base_units": 1,
                        "source_index": -1,
                    },
                    {
                        "name": "Rail Pass",
                        "category": "Document",
                        "quantity": 1,
                        "description": "A pass for elevated trains.",
                        "value_base_units": 1,
                        "source_index": -1,
                    },
                    {
                        "name": "Coffee Tin",
                        "category": "Supply",
                        "quantity": 1,
                        "description": "Bitter coffee.",
                        "value_base_units": 1,
                        "source_index": -1,
                    },
                ],
                "known_crafting_items": [],
                "known_crafting_recipes": [],
                "currency_denominations": [
                    {"name": "Credit", "plural_name": "Credits", "value": 1}
                ],
                "currency_description": "Credits.",
                "starting_currency_balance_base_units": 10,
                "introductory_message": "The sun rises over New Aethelgard.",
                "events": [
                    {
                        "type": "ActiveTaskUpsertedEvent",
                        "payload": {
                            "name": "New Aethelgard Opening Task",
                        },
                    }
                ],
            }
        )

        with self.assertLogs("ai_adventure.ai.gemini_service", level="WARNING"):
            result = parse_gemini_new_game_response(raw_text)

        combined_output = json.dumps(
            {
                "world_summary": result.world_summary,
                "locations": result.locations,
                "start_location": result.start_location,
                "character": result.finalized_character,
                "skills": result.finalized_skills,
                "starting_items": result.finalized_starter_items,
                "introductory_message": result.introductory_message,
                "events": result.suggested_events,
                "raw_text": result.raw_text,
            },
            ensure_ascii=False,
        )

        self.assertNotIn("Aethelgard", combined_output)
        self.assertNotIn("unnamed place", combined_output)
        self.assertIn("sun rises over the city", result.introductory_message)
        self.assertIn("What do you do now?\n-", result.introductory_message)
        self.assertEqual(len(result.suggested_actions), 3)

    def test_parse_new_game_response_accepts_starting_inventory_alias(self) -> None:
        raw_text = json.dumps(
            {
                "selected_genre": "Expedition",
                "world_summary": "A long road waits.",
                "start_location": "Trailhead",
                "starting_calendar": {},
                "weather": "Clear",
                "character": {
                    "name": "Rin",
                    "appearance": "Dusty boots.",
                    "backstory": "Packed for a difficult crossing.",
                    "notes": "Careful with supplies.",
                },
                "skills": [],
                "starting_inventory": [
                    {
                        "name": f"Trail Item {index}",
                        "category": "Supply",
                        "quantity": 1,
                        "description": "A packed expedition supply.",
                        "value_base_units": index,
                        "source_index": index,
                    }
                    for index in range(12)
                ],
                "known_crafting_items": [],
                "known_crafting_recipes": [],
                "currency_denominations": [
                    {"name": "Credit", "plural_name": "Credits", "value": 1}
                ],
                "currency_description": "Credits.",
                "starting_currency_balance_base_units": 7,
                "introductory_message": "The trail begins. What do you do now?",
                "events": [],
            }
        )

        with self.assertLogs("ai_adventure.ai.gemini_service", level="WARNING"):
            result = parse_gemini_new_game_response(raw_text)

        self.assertEqual(len(result.finalized_starter_items), 12)
        self.assertEqual(result.finalized_starter_items[11]["name"], "Trail Item 11")
        self.assertEqual(result.finalized_starter_items[11]["source_index"], 11)

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

    def _install_fake_genai_client(self, response_text: str | list[str]) -> type:
        response_texts = (
            [response_text]
            if isinstance(response_text, str)
            else [str(text) for text in response_text]
        )

        class FakeModels:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def generate_content(self, **kwargs: object) -> object:
                response_index = min(len(self.calls), len(response_texts) - 1)
                self.calls.append(kwargs)
                return types.SimpleNamespace(text=response_texts[response_index])

        class FakeClient:
            last_client: object | None = None

            def __init__(self, api_key: str) -> None:
                self.api_key = api_key
                self.models = FakeModels()
                FakeClient.last_client = self

        google_module = types.ModuleType("google")
        genai_module = types.ModuleType("google.genai")
        setattr(genai_module, "Client", FakeClient)
        setattr(google_module, "genai", genai_module)
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
