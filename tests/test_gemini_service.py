from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ai_adventure.ai.gemini_service import (
    DEFAULT_GEMINI_MODEL,
    build_gemini_story_prompt,
    load_gemini_settings,
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

        self.assertIn("The road bends into fog.", result.narrative_text)
        self.assertIn("- Follow the road.", result.narrative_text)
        self.assertEqual(result.suggested_actions[0], "Follow the road.")
        self.assertEqual(result.suggested_events[0]["type"], "FlagSetEvent")

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
