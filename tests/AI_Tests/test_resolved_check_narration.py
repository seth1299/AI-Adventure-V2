from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_adventure.ai import gemini_service as g


class ResolvedCheckNarrationTests(unittest.TestCase):
    def packet(self, outcome="success"):
        return {
            "player_command": "Scan the area for potential reagents.",
            # Neither of these tags normally selects skills.
            "selection": {"tags": ["reagent", "inventory"]},
            "state": {"skills": {"resolved_checks_this_turn": [{
                "attribute": "Wisdom", "test_kind": "check", "skill_name": "Foraging",
                "reason": "Scanning for reagents", "roll": 20 if outcome == "success" else 2,
                "total": 27 if outcome == "success" else 9, "dc": 12, "outcome": outcome,
            }]}},
        }

    def response(self, *, duplicate=False, text="You find a patch of silverleaf beneath the oak."):
        return json.dumps({
            "response": text, "suggested_actions": ["Examine the silverleaf."],
            "out_of_game": False,
            "events": [{"type": "D20TestRequestedEvent", "payload": {
                "attribute": "Wisdom", "test_kind": "check", "skill_name": "Foraging",
                "reason": "Scanning for reagents", "dc": 12,
            }}] if duplicate else [],
        })

    def generate(self, packet, responses):
        from google import genai
        with patch.object(genai, "Client"), patch.object(
            g, "_create_interaction_with_retry",
            side_effect=[SimpleNamespace(output_text=value) for value in responses],
        ) as request:
            result = g.GeminiNarrationService(g.GeminiSettings(api_key="test-key")).generate_story_response(packet)
        return result, request

    def test_prompt_keeps_resolved_results_without_skill_tags(self):
        for tags in ([], ["reagent", "inventory"], ["skill"]):
            with self.subTest(tags=tags):
                packet = self.packet()
                packet["selection"]["tags"] = tags
                prompt = g.build_gemini_story_prompt(packet)
                self.assertIn('"total":27', prompt)
                self.assertIn('"outcome":"success"', prompt)
                self.assertIn("<resolved_check_narration>", prompt)
                self.assertIn("failed search must explicitly resolve", prompt)
                contents, config = g._separate_system_rules(prompt, {})
                self.assertIn("Narrate their concrete consequences", config["system_instruction"])
                self.assertNotIn('"total":27', config["system_instruction"])
                self.assertIn('"total":27', contents)

    def test_no_extra_request_when_response_does_not_repeat_check(self):
        result, request = self.generate(self.packet(), [self.response()])
        self.assertEqual(request.call_count, 1)
        self.assertIn("patch of silverleaf", result.narrative_text)
        self.assertFalse(result.dropped_events)

    def test_duplicate_repairs_entire_response_using_success_or_failure(self):
        for outcome, text in (
            ("success", "You find a patch of silverleaf beneath the oak."),
            ("failure", "Your search turns up no usable reagents among the blighted plants."),
        ):
            with self.subTest(outcome=outcome):
                result, request = self.generate(self.packet(outcome), [
                    self.response(duplicate=True, text="You continue scanning the undergrowth."),
                    self.response(text=text),
                ])
                self.assertEqual(request.call_count, 2)
                self.assertIn(text, result.narrative_text)
                self.assertNotIn("continue scanning", result.narrative_text)
                self.assertFalse(any(e["type"] == "D20TestRequestedEvent" for e in result.suggested_events))
                self.assertEqual(len(result.dropped_events), 1)
                self.assertEqual(result.dropped_events[0]["payload"]["skill_name"], "Foraging")
                repair = request.call_args.kwargs
                self.assertEqual(repair["request_label"], "resolved-check narration repair")
                self.assertIn(f'"outcome":"{outcome}"', repair["contents"])
                self.assertIn("continue scanning", repair["contents"])

    def test_repeated_duplicate_fails_after_two_repairs(self):
        from google import genai
        with patch.object(genai, "Client"), patch.object(
            g, "_create_interaction_with_retry",
            return_value=SimpleNamespace(output_text=self.response(duplicate=True)),
        ) as request:
            with self.assertRaisesRegex(g.GeminiRequestError, "already-resolved check"):
                g.GeminiNarrationService(g.GeminiSettings(api_key="test-key")).generate_story_response(self.packet())
        self.assertEqual(request.call_count, 3)

    def test_container_repair_also_repairs_repeated_checks(self):
        packet = self.packet()
        original = g.parse_gemini_story_response(self.response())
        corrected = self.response(duplicate=True)
        with patch.object(g, "_enforce_container_reward_flow", side_effect=[
            g.ContainerFlowError("inconsistent contents"), original,
        ]), patch.object(g, "_create_interaction_with_retry", side_effect=[
            SimpleNamespace(output_text=corrected), SimpleNamespace(output_text=self.response()),
        ]) as request:
            result = g._repair_container_reward_flow(
                object(), "gemini-3.5-flash-lite", original, packet,
                g.build_story_response_schema(packet), ai_preferences={},
            )
        self.assertEqual(request.call_count, 2)
        self.assertFalse(any(e["type"] == "D20TestRequestedEvent" for e in result.suggested_events))


if __name__ == "__main__":
    unittest.main()
