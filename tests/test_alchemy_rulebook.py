from __future__ import annotations

import unittest

from ai_adventure.alchemy.rulebook import AlchemyRulebookLoader
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.context.reference_loader import ContextReferenceLoader
from ai_adventure.core.models import AdventureState


class AlchemyRulebookTests(unittest.TestCase):
    def test_default_rulebook_loads_real_reagent_table(self) -> None:
        rulebook = AlchemyRulebookLoader().load_default_rulebook()

        self.assertEqual(rulebook.schema_version, 1)
        self.assertEqual(len(rulebook.qualities), 4)
        self.assertEqual(len(rulebook.motions), 7)
        self.assertGreaterEqual(len(rulebook.example_reagents), 50)
        self.assertIn("preparation", [stage.id for stage in rulebook.stages])
        self.assertIn("potion", [product.id for product in rulebook.product_types])

    def test_rulebook_selects_relevant_reagents(self) -> None:
        rulebook = AlchemyRulebookLoader().load_default_rulebook()

        reagents = rulebook.select_reagents(
            "Brew a warming draught with emberseed and hearth salt",
            max_reagents=5,
        )
        names = [reagent.name for reagent in reagents]

        self.assertIn("Emberseed", names)
        self.assertIn("Red Hearth Salt", names)

    def test_context_builder_includes_rulebook_for_alchemy_commands(self) -> None:
        builder = AiContextBuilder(
            ContextReferenceLoader().load_default_library(),
            alchemy_rulebook=AlchemyRulebookLoader().load_default_rulebook(),
        )

        packet = builder.build_story_context(
            AdventureState(),
            player_command="Experiment with moonwater and a sleep draught",
        )

        self.assertIn("alchemy", packet["rulebooks"])
        self.assertEqual(len(packet["rulebooks"]["alchemy"]["motions"]), 7)
        reagent_names = {
            reagent["name"]
            for reagent in packet["rulebooks"]["alchemy"]["example_reagents"]
        }
        self.assertIn("Moonwater", reagent_names)
        moonwater = next(
            reagent
            for reagent in packet["rulebooks"]["alchemy"]["example_reagents"]
            if reagent["name"] == "Moonwater"
        )
        self.assertIn("material_type", moonwater)

    def test_context_builder_omits_rulebook_for_non_alchemy_commands(self) -> None:
        builder = AiContextBuilder(
            ContextReferenceLoader().load_default_library(),
            alchemy_rulebook=AlchemyRulebookLoader().load_default_rulebook(),
        )

        packet = builder.build_story_context(
            AdventureState(),
            player_command="Look north along the old road",
        )

        self.assertEqual(packet["rulebooks"], {})


if __name__ == "__main__":
    unittest.main()
