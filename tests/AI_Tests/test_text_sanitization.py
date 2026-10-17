from __future__ import annotations

import unittest

from ai_adventure.text_sanitization import (
    sanitize_english_text,
    sanitize_english_text_in_data,
)


class EnglishTextSanitizationTests(unittest.TestCase):
    def test_clause_dashes_keep_word_boundaries_and_are_idempotent(self) -> None:
        for dash in ("\u2013", "\u2014", "\u2015"):
            for spacing in ("", " "):
                with self.subTest(dash=dash, spacing=spacing):
                    raw = f"wild brierroot{spacing}{dash}{spacing}a safe botanical specimen."
                    expected = "wild brierroot - a safe botanical specimen."
                    self.assertEqual(sanitize_english_text(raw), expected)
                    self.assertEqual(sanitize_english_text_in_data({"response": raw})["response"], expected)
                    self.assertEqual(sanitize_english_text(expected), expected)

    def test_word_hyphens_and_numeric_ranges_remain_joined(self) -> None:
        for raw in ("low-level", "low\u2010level", "low\u2011level"):
            self.assertEqual(sanitize_english_text(raw), "low-level")
        self.assertEqual(sanitize_english_text("Use 3\u20135 leaves at -5 degrees."), "Use 3-5 leaves at -5 degrees.")

    def test_transliterates_latin_and_removes_foreign_script_runs(self) -> None:
        self.assertEqual(
            sanitize_english_text(
                "The café sanctuary stands ofوینت the citadel — “safely.”"
            ),
            'The cafe sanctuary stands of the citadel - "safely."',
        )

    def test_recursively_sanitizes_generated_string_values(self) -> None:
        value = sanitize_english_text_in_data(
            {
                "response": "Gérlinde waits العربية nearby.",
                "events": [{"payload": {"name": "München"}}],
                "count": 2,
            }
        )

        self.assertEqual(value["response"], "Gerlinde waits nearby.")
        self.assertEqual(value["events"][0]["payload"]["name"], "Munchen")
        self.assertEqual(value["count"], 2)


if __name__ == "__main__":
    unittest.main()
