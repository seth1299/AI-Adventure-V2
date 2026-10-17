from __future__ import annotations

import unittest

from ai_adventure.context.creative_guardrails import (
    find_banned_creative_terms,
    sanitize_banned_creative_terms,
)


class CreativeGuardrailTests(unittest.TestCase):
    def test_detects_hyphenated_banned_term_variants(self) -> None:
        found_terms = find_banned_creative_terms("The skyline of New Aethel-gard.")

        self.assertIn("Aethelgard", found_terms)

    def test_detects_close_spelling_variant_inside_titled_npc_name(self) -> None:
        found_terms = find_banned_creative_terms(
            "Kaelen the Red",
            terms=("Kaelan",),
        )

        self.assertEqual(found_terms, ["Kaelan"])

    def test_sanitizes_close_spelling_variant_inside_titled_npc_name(self) -> None:
        sanitized = sanitize_banned_creative_terms(
            "Kaelen the Red",
            terms=("Kaelan",),
            replacement="Marrec",
        )

        self.assertEqual(sanitized, "Marrec the Red")

    def test_fuzzy_matching_does_not_ban_unrelated_capitalized_word(self) -> None:
        found_terms = find_banned_creative_terms(
            "Kaelen met the Scarlet courier.",
            terms=("Kaelan",),
        )

        self.assertEqual(found_terms, ["Kaelan"])
        self.assertEqual(
            sanitize_banned_creative_terms(
                "Scarlet courier",
                terms=("Kaelan",),
                replacement="Marrec",
            ),
            "Scarlet courier",
        )

    def test_sanitizer_preserves_lowercase_common_words(self) -> None:
        sanitized = sanitize_banned_creative_terms(
            "Aethelgard rises beyond verdant moss."
        )

        self.assertNotIn("Aethelgard", sanitized)
        self.assertNotIn("unnamed place", sanitized)
        self.assertIn("verdant moss", sanitized)

    def test_sanitizer_removes_awful_appositive_placeholder(self) -> None:
        sanitized = sanitize_banned_creative_terms(
            "Elara, my secretary, has not arrived yet."
        )

        self.assertEqual(sanitized, "my secretary has not arrived yet.")

    def test_sanitizer_cleans_compound_place_name_artifacts(self) -> None:
        sanitized = sanitize_banned_creative_terms(
            "The city of Alden Heights is a sprawl of soot-stained brick.",
            terms=("Alden",),
        )

        self.assertEqual(
            sanitized,
            "The city of the Heights is a sprawl of soot-stained brick.",
        )
        self.assertNotIn("the city of the city", sanitized)

    def test_sanitizer_cleans_leading_compound_place_artifact(self) -> None:
        sanitized = sanitize_banned_creative_terms(
            "Alden Heights is quiet before sunrise.",
            terms=("Alden",),
        )

        self.assertEqual(sanitized, "the Heights is quiet before sunrise.")
        self.assertNotIn("the city Heights", sanitized)


if __name__ == "__main__":
    unittest.main()
