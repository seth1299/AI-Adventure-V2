"""Suggested-action contracts without Qt widgets or temporary saves."""
import json
import unittest

from ai_adventure.ai import gemini_service as g
from ai_adventure.suggested_actions import compose_action_prompt, saved_actions


class SuggestedActionContractTests(unittest.TestCase):
    def response(self):
        return g.parse_gemini_story_response(json.dumps({
            "response": "The camp is quiet.", "out_of_game": False, "events": [],
            "suggested_actions": [{"label": "Rest and eat", "prompt": "I will take some time to rest and eat."},
                                  {"label": "Inspect camp", "prompt": "I will inspect the camp for signs of visitors."}],
        }))

    def test_point_of_view_composition_for_all_wizard_styles(self):
        for style, actor in (("first_person_limited", "I"), ("second_person_limited", "I"),
                             ("third_person_limited", "Iris Vale"), ("third_person_omniscient", "Iris Vale")):
            with self.subTest(style=style):
                expected = f"{actor} will take some time to rest and eat."
                for text in ("I will take some time to rest and eat.", "You will take some time to rest and eat.",
                             "Iris Vale will take some time to rest and eat."):
                    self.assertEqual(compose_action_prompt(text, style=style, player_name="Iris Vale"), expected)

    def test_legacy_labels_have_safe_composed_fallback(self):
        self.assertEqual(saved_actions(["Rest and eat"], {}, player_name="Iris Vale"),
                         [{"label": "Rest and eat", "prompt": "I will rest and eat."}])

    def test_hidden_prompts_survive_guard_filters_and_opening_parser(self):
        result = self.response()
        for guard in (g._ensure_in_game_suggested_actions, g._ensure_status_event_for_in_game_response,
                      g._normalize_visible_currency_phrasing):
            result = g._apply_story_guard(result, {"packet_type": "story_turn"}, guard)
        self.assertEqual(result.suggested_action_prompts["Rest and eat"], "I will take some time to rest and eat.")
        opening = g.parse_gemini_new_game_response(json.dumps({
            "introductory_message": "The camp is quiet.",
            "suggested_actions": [{"label": "Rest", "prompt": "I will rest."}],
        }))
        self.assertEqual(opening.suggested_actions, ["Rest"])
        self.assertEqual(opening.suggested_action_prompts, {"Rest": "I will rest."})

    def test_crafting_filter_checks_hidden_prompt_and_removes_its_metadata(self):
        result = g.AiNarrationResult("The camp is quiet.",
            suggested_actions=["Prepare supplies", "Rest"],
            suggested_action_prompts={"Prepare supplies": "I will brew a Healing Tonic.", "Rest": "I will rest."})
        filtered = g._apply_story_guard(result, {"state": {"alchemy": {"crafting_status": [
            {"recipe_name": "Healing Tonic", "craftable_now": False},
        ]}}}, g._filter_unsupported_crafting_suggestions)
        self.assertEqual(filtered.suggested_actions, ["Rest"])
        self.assertEqual(filtered.suggested_action_prompts, {"Rest": "I will rest."})


if __name__ == "__main__":
    unittest.main()
