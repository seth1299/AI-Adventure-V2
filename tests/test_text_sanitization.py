from __future__ import annotations

import unittest

from ai_adventure.text_sanitization import (
    sanitize_english_text,
    sanitize_english_text_in_data,
)


class EnglishTextSanitizationTests(unittest.TestCase):
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
