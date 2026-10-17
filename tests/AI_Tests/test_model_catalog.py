from __future__ import annotations

import unittest

from ai_adventure.ai.model_catalog import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_TEXT_MODEL,
    IMAGE_MODEL_OPTIONS,
    KNOWN_IMAGE_MODELS,
    KNOWN_TEXT_MODELS,
    TEXT_MODEL_OPTIONS,
    normalize_image_model,
    normalize_image_preferences,
    normalize_text_model,
    thinking_config_for_text_model,
)
from ai_adventure.ai.gemini_service import _structured_output_config
from ai_adventure.ai.image_styles import (
    DEFAULT_IMAGE_STYLE,
    IMAGE_STYLE_OPTIONS,
    KNOWN_IMAGE_STYLES,
    image_style_metadata,
    normalize_image_style,
)


class GeminiModelCatalogTests(unittest.TestCase):
    def test_catalog_contains_only_the_reviewed_ga_output_models(self) -> None:
        self.assertEqual(
            KNOWN_TEXT_MODELS,
            {
                "gemini-3.7-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-2.5-pro",
                "gemini-2.5-flash",
                "gemini-2.5-flash-lite",
            },
        )
        self.assertEqual(
            KNOWN_IMAGE_MODELS,
            {
                "gemini-3.1-flash-image",
                "gemini-3.1-flash-lite-image",
                "gemini-3-pro-image",
                "gemini-2.5-flash-image",
            },
        )
        for option in (*TEXT_MODEL_OPTIONS, *IMAGE_MODEL_OPTIONS):
            self.assertTrue(option["label"])
            self.assertTrue(option["description"])
            self.assertTrue(option["url"].startswith("https://ai.google.dev/"))

    def test_unknown_or_wrong_modality_models_fall_back_safely(self) -> None:
        self.assertEqual(normalize_text_model("gemini-3-pro-image"), DEFAULT_TEXT_MODEL)
        self.assertEqual(normalize_text_model("models/gemini-3.7-flash"), "gemini-3.7-flash")
        self.assertEqual(normalize_image_model("gemini-3.7-flash"), DEFAULT_IMAGE_MODEL)
        self.assertEqual(
            normalize_image_model("models/gemini-3.1-flash-image"),
            "gemini-3.1-flash-image",
        )

    def test_thinking_control_matches_each_model_generation(self) -> None:
        self.assertEqual(
            thinking_config_for_text_model("gemini-3.7-flash", smarter=False),
            {"thinking_level": "low"},
        )
        self.assertEqual(
            thinking_config_for_text_model("gemini-3.6-flash", smarter=False),
            {"thinking_level": "minimal"},
        )
        self.assertEqual(
            thinking_config_for_text_model("gemini-3.1-flash-lite", smarter=True),
            {"thinking_level": "high"},
        )
        self.assertEqual(
            thinking_config_for_text_model("gemini-2.5-pro", smarter=False),
            {"thinking_budget": 1024},
        )
        self.assertEqual(
            thinking_config_for_text_model("gemini-2.5-flash", smarter=True),
            {"thinking_budget": 24576},
        )

    def test_structured_requests_omit_non_universal_sampling_arguments(self) -> None:
        for model in KNOWN_TEXT_MODELS:
            with self.subTest(model=model):
                config = _structured_output_config(
                    {"type": "object", "properties": {}},
                    model=model,
                    ai_preferences={"model_intelligence": "faster"},
                )
                self.assertNotIn("temperature", config)
                self.assertNotIn("top_p", config)
                self.assertNotIn("top_k", config)
                self.assertNotIn("candidate_count", config)
                thinking = config["thinking_config"]
                self.assertNotEqual(
                    "thinking_level" in thinking,
                    "thinking_budget" in thinking,
                )

    def test_image_generation_can_be_disabled_without_losing_model_choice(self) -> None:
        preferences = normalize_image_preferences(
            {
                "enabled": False,
                "model": "gemini-3-pro-image",
                "style": "oil_painting",
            }
        )

        self.assertFalse(preferences["enabled"])
        self.assertEqual(preferences["model"], "gemini-3-pro-image")
        self.assertEqual(preferences["style"], "oil_painting")

    def test_image_style_catalog_is_complete_and_normalized(self) -> None:
        self.assertEqual(len(IMAGE_STYLE_OPTIONS), 12)
        self.assertEqual(
            KNOWN_IMAGE_STYLES,
            {option["value"] for option in IMAGE_STYLE_OPTIONS},
        )
        for option in IMAGE_STYLE_OPTIONS:
            self.assertTrue(option["label"])
            self.assertTrue(option["description"])
            self.assertTrue(option["prompt"])
        self.assertEqual(normalize_image_style("Noire"), "film_noir")
        self.assertEqual(normalize_image_style("unknown"), DEFAULT_IMAGE_STYLE)
        self.assertEqual(image_style_metadata("crayon")["label"], "Crayon")


if __name__ == "__main__":
    unittest.main()
