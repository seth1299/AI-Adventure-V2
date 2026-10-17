from __future__ import annotations

import unittest

from ai_adventure.ai.modes import (
    ALL_CONTENT_HARM_CATEGORIES,
    ai_mode_preferences_from_settings,
    build_ai_mode_prompt_guidance,
    normalize_ai_mode_preferences,
)


class AiModeTests(unittest.TestCase):
    def test_defaults_preserve_current_fast_unrestricted_behavior(self) -> None:
        preferences = normalize_ai_mode_preferences({})

        self.assertEqual(preferences["text_model"], "gemini-3.5-flash-lite")
        self.assertEqual(preferences["model_intelligence"], "faster")
        self.assertEqual(preferences["thinking_level"], "minimal")
        self.assertEqual(preferences["model_tone"], "neutral")
        self.assertEqual(preferences["response_length"], "normal")
        self.assertEqual(
            preferences["allowed_content_categories"],
            list(ALL_CONTENT_HARM_CATEGORIES),
        )
        self.assertEqual(preferences["blocked_content_categories"], [])

    def test_normalization_filters_unknown_values_and_preserves_empty_content(self) -> None:
        preferences = normalize_ai_mode_preferences(
            {
                "text_model": "gemini-3.7-flash",
                "model_intelligence": "Smarter",
                "model_tone": "Quirky",
                "response_length": "Super Brief",
                "allowed_content_categories": [],
            }
        )

        self.assertEqual(preferences["model_intelligence"], "smarter")
        self.assertEqual(preferences["text_model"], "gemini-3.7-flash")
        self.assertEqual(preferences["thinking_level"], "high")
        self.assertEqual(preferences["model_tone"], "quirky")
        self.assertEqual(preferences["response_length"], "super_brief")
        self.assertEqual(preferences["max_output_tokens"], 1536)
        self.assertEqual(preferences["allowed_content_categories"], [])
        self.assertEqual(
            preferences["blocked_content_categories"],
            list(ALL_CONTENT_HARM_CATEGORIES),
        )

    def test_save_settings_and_prompt_guidance_use_selected_modes(self) -> None:
        preferences = ai_mode_preferences_from_settings(
            {
                "ai.text_model": "gemini-3.6-flash",
                "ai.model_intelligence": "smarter",
                "ai.model_tone": "efficient",
                "ai.response_length": "brief",
                "ai.allowed_content_categories": [
                    "HARM_CATEGORY_DANGEROUS_CONTENT"
                ],
            }
        )
        prompt = build_ai_mode_prompt_guidance(
            {"state": {"player_ai_preferences": preferences}}
        )

        self.assertEqual(preferences["model_tone_label"], "Efficient")
        self.assertEqual(preferences["text_model"], "gemini-3.6-flash")
        self.assertIn("plain, direct wording", prompt)
        self.assertIn("Response length — Brief", prompt)
        self.assertIn("Dangerous Content", prompt)
        self.assertIn("unchecked categories", prompt)


if __name__ == "__main__":
    unittest.main()
