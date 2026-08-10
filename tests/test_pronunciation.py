from __future__ import annotations

import json
import unittest

from ai_adventure.ai.gemini_service import parse_gemini_story_response
from ai_adventure.audio.pronunciation import (
    apply_pronunciation_map,
    compile_kokoro_phoneme_overrides,
    invalid_kokoro_ipa_characters,
    merge_pronunciation_maps,
    normalize_kokoro_ipa,
    normalize_pronunciation_map,
)
from ai_adventure.new_game_setup import normalize_new_game_setup


class PronunciationTests(unittest.TestCase):
    def test_normalizes_ipa_and_legacy_structured_entries(self) -> None:
        self.assertEqual(
            normalize_pronunciation_map(
                [
                    {"term": "Qh’thala", "ipa": "kəˈθɑlə"},
                    {"term": "ignored", "phonetic": "ihg-NORED"},
                ]
            ),
            {
                "Qh’thala": {"ipa": "kəˈθɑlə"},
                "ignored": {"respelling": "ihgnored"},
            },
        )

    def test_validates_kokoro_ipa_and_strips_human_delimiters(self) -> None:
        self.assertEqual(normalize_kokoro_ipa("/kəˈθɑlə/"), "kəˈθɑlə")
        self.assertEqual(normalize_kokoro_ipa("[ˈɑnɪkˌspaɪɚ]"), "ˈɑnɪkˌspaɪɚ")
        self.assertEqual(normalize_kokoro_ipa("kah-tha-lah"), "")
        self.assertEqual(invalid_kokoro_ipa_characters("kah-tha-lah"), ("-",))

    def test_drops_invalid_model_ipa_instead_of_sending_unknown_tokens(self) -> None:
        self.assertEqual(
            normalize_pronunciation_map(
                [{"term": "Qh’thala", "ipa": "kah-tha-lah"}]
            ),
            {},
        )

    def test_drops_redundant_or_cosmetic_legacy_respelling_entries(self) -> None:
        self.assertEqual(
            normalize_pronunciation_map(
                {
                    "Kit": "KIT",
                    "Copper Square": "KOP-er SKWAIR",
                    "Striolia": "stree-OH-lee-uh",
                }
            ),
            {"Striolia": {"respelling": "streeohleeuh"}},
        )

    def test_legacy_hyphenated_respelling_is_joined_without_tts_pauses(self) -> None:
        self.assertEqual(
            normalize_pronunciation_map({"Xyra": "ZAI-rah"}),
            {"Xyra": {"respelling": "zairah"}},
        )

    def test_legacy_spaced_syllables_are_joined_within_visible_words(self) -> None:
        self.assertEqual(
            normalize_pronunciation_map(
                {
                    "Onyxspire": "on iks spire",
                    "Qh’thala Market": "kah tha lah mar kit",
                    "Sunlit Bazaar": "sun lit ba zaar",
                    "Droynga": "droyn ga",
                }
            ),
            {
                "Onyxspire": {"respelling": "oniksspire"},
                "Qh’thala Market": {"respelling": "kahthalah markit"},
                "Sunlit Bazaar": {"respelling": "sunlit bazaar"},
                "Droynga": {"respelling": "droynga"},
            },
        )

    def test_letter_by_letter_legacy_separators_are_still_rejected(self) -> None:
        self.assertEqual(
            normalize_pronunciation_map({"Kit": "K-I-T", "Copper": "C O P P E R"}),
            {},
        )

    def test_longer_ipa_terms_are_annotated_before_shorter_terms(self) -> None:
        self.assertEqual(
            apply_pronunciation_map(
                "Qh’thala Market is outside Qh’thala.",
                {
                    "Qh’thala Market": {"ipa": "kəˈθɑlə ˈmɑɹkət"},
                    "Qh’thala": {"ipa": "kəˈθɑlə"},
                },
            ),
            (
                '[Qh’thala Market]{ph="kəˈθɑlə ˈmɑɹkət"} is outside '
                '[Qh’thala]{ph="kəˈθɑlə"}.'
            ),
        )

    def test_legacy_respelling_replaces_only_tts_text_without_hyphens(self) -> None:
        self.assertEqual(
            apply_pronunciation_map(
                "Ironpeak City wakes.",
                {"Ironpeak City": "eye-urn-peek City"},
            ),
            "eyeurnpeek city wakes.",
        )

    def test_character_name_respelling_becomes_authoritative_map_entry(self) -> None:
        setup = normalize_new_game_setup(
            {
                "character": {
                    "name": "Qh’thala",
                    "name_pronunciation": "KAH-tha-lah",
                },
                "pronunciation_map": {
                    "Qh’thala": {"ipa": "rɑŋ"},
                },
            }
        )
        self.assertEqual(
            setup["pronunciation_map"]["Qh’thala"],
            {"respelling": "kahthalah"},
        )

    def test_character_name_can_use_explicit_validated_ipa(self) -> None:
        setup = normalize_new_game_setup(
            {
                "character": {
                    "name": "Qh’thala",
                    "name_pronunciation": "/kəˈθɑlə/",
                }
            }
        )
        self.assertEqual(
            setup["pronunciation_map"]["Qh’thala"],
            {"ipa": "kəˈθɑlə"},
        )

    def test_identity_name_pronunciation_clears_conflicting_ai_entry(self) -> None:
        setup = normalize_new_game_setup(
            {
                "character": {
                    "name": "Kit",
                    "name_pronunciation": "KIT",
                },
                "pronunciation_map": {
                    "Kit": {"ipa": "kɪt"},
                    "Myr": "MEER",
                },
            }
        )
        self.assertEqual(
            setup["pronunciation_map"],
            {"Myr": {"respelling": "meer"}},
        )

    def test_merge_preserves_first_spelling_and_fills_missing_ipa(self) -> None:
        self.assertEqual(
            merge_pronunciation_maps(
                {"Qh’thala": "KAH-tha-lah"},
                {
                    "qh’thala": {"ipa": "kəˈθɑlə"},
                    "Myr": {"ipa": "mɪɹ"},
                },
            ),
            {
                "Qh’thala": {"respelling": "kahthalah", "ipa": "kəˈθɑlə"},
                "Myr": {"ipa": "mɪɹ"},
            },
        )

    def test_compiles_mixed_ipa_annotations_for_kokoro_onnx(self) -> None:
        compiled = compile_kokoro_phoneme_overrides(
            'The [Qh’thala]{ph="kəˈθɑlə"} waits.',
            lambda value: f"<{value}>",
        )
        self.assertEqual(compiled, "<The> kəˈθɑlə <waits.>")

    def test_story_parser_reads_validated_ipa_entries(self) -> None:
        result = parse_gemini_story_response(
            json.dumps(
                {
                    "response": "Qh’thala waits beside Myr.",
                    "suggested_actions": [],
                    "events": [],
                    "out_of_game": True,
                    "pronunciation_map": [
                        {"term": "Qh’thala", "ipa": "kəˈθɑlə"},
                        {"term": "Myr", "ipa": "mɪɹ"},
                    ],
                }
            ),
            context_packet={"conversation_mode": "out_of_game"},
        )
        self.assertEqual(
            result.pronunciation_map["Qh’thala"],
            {"ipa": "kəˈθɑlə"},
        )


if __name__ == "__main__":
    unittest.main()
