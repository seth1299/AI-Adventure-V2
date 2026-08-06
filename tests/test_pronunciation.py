from __future__ import annotations

import unittest
import json

from ai_adventure.ai.gemini_service import parse_gemini_story_response
from ai_adventure.audio.pronunciation import (
    apply_pronunciation_map,
    merge_pronunciation_maps,
    normalize_pronunciation_map,
)
from ai_adventure.new_game_setup import normalize_new_game_setup


class PronunciationTests(unittest.TestCase):
    def test_normalizes_dict_and_structured_entry_lists(self) -> None:
        self.assertEqual(
            normalize_pronunciation_map(
                [
                    {"term": "Qh’thala", "phonetic": "KAH-tha-lah"},
                    {"term": "ignored", "pronunciation": "ihg-NORED"},
                ]
            ),
            {
                "Qh’thala": "KAH-tha-lah",
                "ignored": "ihg-NORED",
            },
        )

    def test_longer_terms_are_replaced_before_shorter_terms(self) -> None:
        self.assertEqual(
            apply_pronunciation_map(
                "Qh’thala Market is outside Qh’thala.",
                {"Qh’thala Market": "KAH-tha-lah MAR-kit", "Qh’thala": "KAH-tha-lah"},
            ),
            "KAH-tha-lah MAR-kit is outside KAH-tha-lah.",
        )

    def test_character_name_pronunciation_becomes_authoritative_map_entry(self) -> None:
        setup = normalize_new_game_setup(
            {
                "character": {
                    "name": "Qh’thala",
                    "name_pronunciation": "KAH-tha-lah",
                },
                "pronunciation_map": {"Qh’thala": "wrong"},
            }
        )
        self.assertEqual(setup["pronunciation_map"]["Qh’thala"], "KAH-tha-lah")

    def test_merge_preserves_first_seen_spelling(self) -> None:
        self.assertEqual(
            merge_pronunciation_maps(
                {"Qh’thala": "KAH-tha-lah"},
                {"qh’thala": "wrong", "Myr": "MEER"},
            ),
            {"Qh’thala": "KAH-tha-lah", "Myr": "MEER"},
        )

    def test_story_parser_reads_structured_pronunciation_entries(self) -> None:
        result = parse_gemini_story_response(
            json.dumps(
                {
                    "response": "Qh’thala waits beside Myr.",
                    "suggested_actions": [],
                    "events": [],
                    "out_of_game": True,
                    "pronunciation_map": [
                        {"term": "Qh’thala", "phonetic": "KAH-tha-lah"},
                        {"term": "Myr", "phonetic": "MEER"},
                    ],
                }
            ),
            context_packet={"conversation_mode": "out_of_game"},
        )
        self.assertEqual(result.pronunciation_map["Qh’thala"], "KAH-tha-lah")


if __name__ == "__main__":
    unittest.main()
