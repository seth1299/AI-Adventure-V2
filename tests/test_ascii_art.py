from __future__ import annotations

import unittest

from ai_adventure.ascii_art import (
    ensure_substantive_ascii_art,
    is_substantive_ascii_art,
    normalize_ascii_art,
)


class AsciiArtTests(unittest.TestCase):
    def test_normalize_ascii_art_decodes_only_escaped_line_endings(self) -> None:
        raw_art = "+------+\\n| /\\/\\ |\\n+------+"

        self.assertEqual(
            normalize_ascii_art(raw_art),
            "+------+\n| /\\/\\ |\n+------+",
        )

    def test_normalize_ascii_art_removes_markdown_fences(self) -> None:
        self.assertEqual(
            normalize_ascii_art("```text\\n /\\\\n/__\\\\n```"),
            " /\\\n/__\\",
        )

    def test_bracketed_item_name_is_not_substantive_ascii_art(self) -> None:
        self.assertFalse(is_substantive_ascii_art("[Camera]"))
        self.assertFalse(is_substantive_ascii_art("[===]"))
        self.assertTrue(
            is_substantive_ascii_art(
                "   ______\n"
                "  / ___  \\\n"
                " | (___) |\n"
                "  \\_____/"
            )
        )

    def test_camera_placeholder_gets_pictorial_fallback(self) -> None:
        art = ensure_substantive_ascii_art(
            "[Camera]",
            item_name="Camera",
            category="Item",
        )

        self.assertTrue(is_substantive_ascii_art(art))
        self.assertGreaterEqual(len(art.splitlines()), 3)
        self.assertNotIn("Camera", art)


if __name__ == "__main__":
    unittest.main()
