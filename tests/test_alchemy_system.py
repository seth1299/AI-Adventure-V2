from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository


class AlchemySystemTests(unittest.TestCase):
    def test_reagent_discovery_persists_structured_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alchemy Test")

            repository.add_alchemy_reagent(
                name="Moon Salt",
                qualities=["cold", "silver", ""],
                motions=["settling"],
                virtues=["clarity"],
                uses=["cooling draughts", "mirror inks"],
                notes="Forms under moonlit stone.",
            )

            reagents = repository.list_alchemy_reagents()

            self.assertEqual(len(reagents), 1)
            self.assertEqual(reagents[0]["name"], "Moon Salt")
            self.assertEqual(reagents[0]["qualities"], ["cold", "silver"])
            self.assertEqual(reagents[0]["motions"], ["settling"])
            self.assertEqual(reagents[0]["virtues"], ["clarity"])
            self.assertEqual(reagents[0]["uses"], ["cooling draughts", "mirror inks"])

    def test_recipe_discovery_persists_structured_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alchemy Test")

            repository.add_alchemy_recipe(
                name="Mistglass Tincture",
                ingredients=["Moon Salt", "Rainwater"],
                result="Reveals faint hidden script.",
                motions=["dissolve", "turn clockwise"],
                virtues=["revelation"],
                notes="Clouds if stirred too quickly.",
            )

            recipes = repository.list_alchemy_recipes()

            self.assertEqual(len(recipes), 1)
            self.assertEqual(recipes[0]["name"], "Mistglass Tincture")
            self.assertEqual(recipes[0]["ingredients"], ["Moon Salt", "Rainwater"])
            self.assertEqual(recipes[0]["result"], "Reveals faint hidden script.")
            self.assertEqual(recipes[0]["motions"], ["dissolve", "turn clockwise"])
            self.assertEqual(recipes[0]["virtues"], ["revelation"])

    def test_state_manager_loads_reagents_and_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alchemy Test")
            repository.add_alchemy_reagent(
                name="Ash Fern",
                qualities=["dry", "bitter"],
                motions=["rising"],
                virtues=["memory"],
                uses=["smoke readings"],
                notes="Curls toward heat.",
            )
            repository.add_alchemy_recipe(
                name="Ember Mnemonic",
                ingredients=["Ash Fern"],
                result="Restores a recent sensory impression.",
                motions=["kindle"],
                virtues=["memory"],
                notes="Unstable in rain.",
            )

            state = StateManager(repository).load_state()

            self.assertEqual(state.alchemy.known_reagents[0].name, "Ash Fern")
            self.assertEqual(state.alchemy.known_reagents[0].qualities, ["dry", "bitter"])
            self.assertEqual(state.alchemy.known_recipes[0].name, "Ember Mnemonic")
            self.assertEqual(state.alchemy.known_recipes[0].ingredients, ["Ash Fern"])


if __name__ == "__main__":
    unittest.main()
