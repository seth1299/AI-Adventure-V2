from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository


class AlchemySystemTests(unittest.TestCase):
    def test_existing_saves_gain_reagent_material_type_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "old.sqlite3"
            connection = sqlite3.connect(save_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE alchemy_reagents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        qualities_json TEXT NOT NULL DEFAULT '[]',
                        motions_json TEXT NOT NULL DEFAULT '[]',
                        virtues_json TEXT NOT NULL DEFAULT '[]',
                        uses_json TEXT NOT NULL DEFAULT '[]',
                        notes TEXT NOT NULL DEFAULT '',
                        discovered_at TEXT NOT NULL
                    );
                    INSERT INTO alchemy_reagents (
                        name,
                        qualities_json,
                        motions_json,
                        virtues_json,
                        uses_json,
                        notes,
                        discovered_at
                    )
                    VALUES ('Old Salt', '["dry"]', '["settling"]', '["clarity"]', '[]', '', '2026-05-31T00:00:00');
                    """
                )
            finally:
                connection.close()

            repository = SaveRepository(save_path)
            reagents = repository.list_alchemy_reagents()

            self.assertEqual(reagents[0]["name"], "Old Salt")
            self.assertEqual(reagents[0]["material_type"], "")

    def test_reagent_discovery_persists_structured_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alchemy Test")

            repository.add_alchemy_reagent(
                name="Moon Salt",
                material_type="Geological",
                qualities=["cold", "silver", ""],
                motions=["settling"],
                virtues=["clarity"],
                uses=["cooling draughts", "mirror inks"],
                notes="Forms under moonlit stone.",
            )

            reagents = repository.list_alchemy_reagents()

            self.assertEqual(len(reagents), 1)
            self.assertEqual(reagents[0]["name"], "Moon Salt")
            self.assertEqual(reagents[0]["material_type"], "Geological")
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
                material_type="Botanical",
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
            self.assertEqual(state.alchemy.known_reagents[0].material_type, "Botanical")
            self.assertEqual(state.alchemy.known_reagents[0].qualities, ["dry", "bitter"])
            self.assertEqual(state.alchemy.known_recipes[0].name, "Ember Mnemonic")
            self.assertEqual(state.alchemy.known_recipes[0].ingredients, ["Ash Fern"])


if __name__ == "__main__":
    unittest.main()
