from __future__ import annotations

import unittest

from ai_adventure.ascii_art import normalize_ascii_art


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


if __name__ == "__main__":
    unittest.main()
