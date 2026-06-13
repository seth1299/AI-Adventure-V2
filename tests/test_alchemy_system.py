from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository


class AlchemySystemTests(unittest.TestCase):
    def test_new_saves_use_crafting_tables_without_legacy_alchemy_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Crafting Schema Test")

            with repository._connect() as connection:
                table_names = {
                    str(row["name"])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }

            self.assertIn("crafting_items", table_names)
            self.assertIn("crafting_recipes", table_names)
            self.assertNotIn("alchemy_notes", table_names)
            self.assertNotIn("alchemy_reagents", table_names)
            self.assertNotIn("alchemy_recipes", table_names)

    def test_reagent_discovery_persists_simplified_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alchemy Test")

            repository.add_crafting_item(
                name="Moon Salt",
                description="Pale crystals that hum under moonlight.",
                location="Moonlit stone basins",
                uses=["cooling draughts", "mirror inks"],
            )

            reagents = repository.list_crafting_items()

            self.assertEqual(len(reagents), 1)
            self.assertEqual(reagents[0]["name"], "Moon Salt")
            self.assertEqual(reagents[0]["description"], "Pale crystals that hum under moonlight.")
            self.assertEqual(reagents[0]["location"], "Moonlit stone basins")
            self.assertEqual(reagents[0]["uses"], ["cooling draughts", "mirror inks"])
            catalog = repository.list_item_catalog()
            moon_salt = next(item for item in catalog if item["name"] == "Moon Salt")
            self.assertEqual(moon_salt["category"], "Material")

    def test_recipe_discovery_persists_structured_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alchemy Test")

            repository.add_crafting_recipe(
                name="Mistglass Tincture",
                ingredients=[
                    {
                        "reagent_name": "Moon Salt",
                        "quantity": 1,
                        "measure_amount": 5,
                        "measure_unit": "grams",
                    },
                    {
                        "reagent_name": "Rainwater",
                        "quantity": 1,
                        "measure_amount": 100,
                        "measure_unit": "mL",
                    },
                ],
                result="Reveals faint hidden script.",
                notes="Clouds if stirred too quickly.",
            )

            recipes = repository.list_crafting_recipes()

            self.assertEqual(len(recipes), 1)
            self.assertEqual(recipes[0]["name"], "Mistglass Tincture")
            self.assertEqual(
                recipes[0]["ingredients"][0],
                {
                    "reagent_name": "Moon Salt",
                    "quantity": 1,
                    "measure_amount": 5,
                    "measure_unit": "grams",
                },
            )
            self.assertEqual(recipes[0]["result"], "Reveals faint hidden script.")

    def test_state_manager_loads_reagents_and_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alchemy Test")
            repository.add_crafting_item(
                name="Ash Fern",
                description="Grey fronds that curl toward heat.",
                location="Charcoal-rich forest clearings",
                uses=["smoke readings"],
            )
            repository.add_crafting_recipe(
                name="Ember Mnemonic",
                ingredients=[
                    {
                        "reagent_name": "Ash Fern",
                        "quantity": 1,
                        "measure_amount": 1,
                        "measure_unit": "handful",
                    }
                ],
                result="Restores a recent sensory impression.",
                notes="Unstable in rain.",
            )

            state = StateManager(repository).load_state()

            self.assertEqual(state.alchemy.known_reagents[0].name, "Ash Fern")
            self.assertEqual(state.alchemy.known_reagents[0].description, "Grey fronds that curl toward heat.")
            self.assertEqual(state.alchemy.known_reagents[0].location, "Charcoal-rich forest clearings")
            self.assertEqual(state.alchemy.known_recipes[0].name, "Ember Mnemonic")
            self.assertEqual(
                state.alchemy.known_recipes[0].ingredients[0].reagent_name,
                "Ash Fern",
            )

    def test_item_catalog_remembers_removed_inventory_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Catalog Test")
            repository.add_inventory_item(
                "Glass Prism",
                "Tool",
                1,
                "A triangular glass prism.",
                value_base_units=12,
            )
            repository.remove_inventory_item("Glass Prism", 1)

            self.assertNotIn(
                "Glass Prism",
                {item["name"] for item in repository.list_inventory_items()},
            )
            catalog = repository.list_item_catalog()

            self.assertIn("Glass Prism", {item["name"] for item in catalog})
            prism = next(item for item in catalog if item["name"] == "Glass Prism")
            self.assertNotIn("quantity", prism)
            self.assertEqual(prism["description"], "A triangular glass prism.")


if __name__ == "__main__":
    unittest.main()
