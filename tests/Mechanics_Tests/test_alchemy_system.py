from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.alchemy.ingredients import (
    COMMON_MEASUREMENT_UNITS,
    format_recipe_ingredient,
    normalize_recipe_ingredient,
)
from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.crafting import evaluate_recipe_craftability, recipe_progress
from ai_adventure.calendar_system import build_calendar_snapshot


class AlchemySystemTests(unittest.TestCase):
    def test_saved_work_reports_increasing_progress_and_remaining_estimates(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Progress")
            repository.set_setting("survival.enabled", True)
            start_minute = repository.get_current_calendar_minute()
            repository.upsert_skill("Spinning", "Spinning fiber", 4)
            repository.add_crafting_recipe(
                name="Yarn", ingredients=[], result="Yarn", skill_name="Spinning",
                stages=[{"kind": "active", "work_amount": 50, "estimated_minutes": 30}],
            )
            identity = repository.list_crafting_recipes()[0]["id"]
            first = repository.craft_recipe(identity)
            self.assertEqual(first["minutes_spent"], 2)
            self.assertEqual(repository.get_current_calendar_minute(), start_minute + 2)
            self.assertEqual(first["previous_percentage"], 0)
            self.assertEqual(first["progress"]["percentage"], 8)
            self.assertEqual(first["progress"]["active_work_remaining"], 46)
            self.assertEqual(first["progress"]["actions_remaining"], 12)
            self.assertEqual(first["progress"]["active_minutes_remaining"], 28)
            reopened = SaveRepository(repository.db_path)
            self.assertEqual(reopened.crafting_progress(identity), first["progress"])
            second = reopened.craft_recipe(identity)
            self.assertEqual(second["progress"]["percentage"], 16)
            self.assertEqual(second["progress"]["active_minutes_remaining"], 26)
            self.assertIn("8% → 16%", second["message"])
            for _ in range(11):
                result = reopened.craft_recipe(identity)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["progress"]["percentage"], 100)
            self.assertEqual(result["progress"]["actions_remaining"], 0)
            self.assertFalse(reopened.crafting_progress(identity)["continuation"])
            self.assertEqual(reopened.get_current_calendar_minute(), start_minute + 30)
            self.assertEqual({meter: reopened.survival_status()[meter] for meter in ("hunger", "thirst", "energy")},
                             {"hunger": 98, "thirst": 96, "energy": 97})

    def test_work_until_done_advances_through_active_and_passive_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Timed crafting")
            repository.set_current_calendar_minute(1430)
            repository.set_setting("survival.enabled", True)
            repository.add_inventory_item("Flax", "Material", 3, "Fiber")
            repository.add_crafting_recipe(
                name="Linen", ingredients=[{"reagent_name": "Flax", "quantity": 2}], result="Linen",
                stages=[{"kind": "active", "work_amount": 10, "estimated_minutes": 60},
                        {"kind": "passive", "duration_minutes": 30},
                        {"kind": "active", "work_amount": 3, "estimated_minutes": 30}],
            )
            identity = repository.list_crafting_recipes()[0]["id"]
            result = repository.craft_recipe(identity, until_done=True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["minutes_spent"], 120)
            self.assertEqual(repository.get_current_calendar_minute(), 1550)
            self.assertEqual(repository.get_state_value("time"),
                             build_calendar_snapshot(1550, repository.get_calendar_settings())["display_label"])
            self.assertEqual(result["survival_changes"], {"hunger": -7, "thirst": -14, "energy": -10})
            items = {item["name"]: item["quantity"] for item in repository.list_inventory_items()}
            self.assertEqual((items["Flax"], items["Linen"]), (1, 1))

    def test_work_until_done_stops_clock_and_crafting_at_survival_depletion(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Interrupted crafting")
            repository.set_setting("survival.enabled", True)
            repository.set_state_value("player.thirst", "1")
            start = repository.get_current_calendar_minute()
            repository.add_crafting_recipe(
                name="Long job", ingredients=[], result="Linen",
                stages=[{"kind": "active", "work_amount": 1, "estimated_minutes": 60}],
            )
            identity = repository.list_crafting_recipes()[0]["id"]
            result = repository.craft_recipe(identity, until_done=True)
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["minutes_spent"], 8)
            self.assertEqual(repository.get_current_calendar_minute(), start + 8)
            self.assertEqual(repository.survival_status()["fate"], "pending")
            self.assertEqual(repository.crafting_progress(identity)["active_minutes_remaining"], 52)
            self.assertGreater(result["progress"]["percentage"], 0)
            self.assertNotIn("Linen", {item["name"] for item in repository.list_inventory_items()})
            self.assertEqual(repository.craft_recipe(identity, until_done=True)["status"], "blocked")
            self.assertEqual(repository.get_current_calendar_minute(), start + 8)
            death = repository.choose_survival_fate("recovering")
            self.assertTrue(repository.commit_survival_recovery(death["incident_id"], {
                "location": "Clinic", "response": "You wake in a clinic.",
            }))
            resumed = repository.craft_recipe(identity, until_done=True)
            self.assertEqual(resumed["status"], "completed")
            self.assertEqual(resumed["minutes_spent"], 52)
            self.assertEqual(repository.get_current_calendar_minute(), start + 60)

    def test_progress_accounts_for_batches_passive_clock_and_future_stages(self):
        recipe = {"stages": [
            {"kind": "active", "work_amount": 10, "estimated_minutes": 20},
            {"kind": "passive", "duration_minutes": 10},
            {"kind": "active", "work_amount": 5},
        ]}
        skills = [{"name": "Crafting", "level": 2}]
        batch = recipe_progress(recipe, {"quantity": 2, "active_work_completed": 4}, skills)
        self.assertEqual(batch["active_work_remaining"], 26)
        self.assertEqual(batch["actions_remaining"], 13)
        self.assertEqual(batch["active_minutes_remaining"], 37)
        process = {"stage_index": 1, "passive_due_minute": 110}
        waiting = recipe_progress(recipe, process, skills, current_minute=105)
        ready = recipe_progress(recipe, process, skills, current_minute=115)
        self.assertEqual(waiting["passive_minutes_remaining"], 5)
        self.assertEqual(waiting["active_work_remaining"], 5)
        self.assertTrue(waiting["waiting"])
        self.assertFalse(ready["waiting"])
        self.assertGreater(ready["percentage"], waiting["percentage"])
        self.assertEqual(ready["active_minutes_remaining"], 3)

    def test_flax_count_units_and_local_spinning_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Spinning")
            repository.set_travel_locations([{"name": "Meadowview Cottage", "location_scope": "specific"}])
            repository.set_state_value("location", "Meadowview Cottage")
            repository.add_inventory_item(
                "Raw Flax Fiber", "Material", 15, "Flax stalks",
                metadata={"quantity_unit": "stalks"},
            )
            repository.add_inventory_item(
                "Spinning Wheel", "Furniture", 1, "A wheel",
                metadata={"storage_location": "Meadowview Cottage"},
            )
            repository.add_crafting_recipe(
                name="Spun Linen Yarn",
                ingredients=[{"reagent_name": "Raw Flax Fiber", "quantity": 2}],
                required_tool_item_names=["Spinning Wheel"], result="Linen Yarn",
                stages=[{"kind": "active", "work_amount": 1}],
            )
            recipe = repository.list_crafting_recipes()[0]
            availability = evaluate_recipe_craftability(recipe, repository.list_accessible_inventory_items())
            self.assertTrue(availability["craftable"])
            self.assertEqual(availability["craftable_quantity"], 7)
            repository.set_state_value("location", "Village Square")
            self.assertFalse(evaluate_recipe_craftability(recipe, repository.list_accessible_inventory_items())["craftable"])
            repository.set_state_value("location", "Meadowview Cottage")
            self.assertEqual(repository.craft_recipe(recipe["id"])["status"], "completed")
            flax = next(item for item in repository.list_inventory_items() if item["name"] == "Raw Flax Fiber")
            self.assertEqual(flax["quantity"], 13)
            self.assertEqual(flax["quantity_unit"], "stalks")

    def test_each_does_not_convert_physical_measurements(self) -> None:
        for unit in ("grams", "mL", "lbs", "meters"):
            with self.subTest(unit=unit):
                inventory = [{"name": "Flax", "quantity": 15, "quantity_unit": unit, "item_uuid": "flax"}]
                recipe = {"ingredients": [{"reagent_name": "Flax", "item_uuid": "flax", "quantity": 2}]}
                availability = evaluate_recipe_craftability(recipe, inventory)
                self.assertFalse(availability["craftable"])
                self.assertEqual(availability["missing_ingredients"][0]["reason"], "unit_mismatch")

    def test_single_measured_ingredient_omits_redundant_count(self) -> None:
        self.assertEqual(
            format_recipe_ingredient(
                {
                    "reagent_name": "Dried Herbs",
                    "quantity": 1,
                    "measure_amount": 2,
                    "measure_unit": "grams",
                }
            ),
            "Dried Herbs (2 grams)",
        )

    def test_repeated_each_ingredient_displays_total_consumed(self) -> None:
        self.assertEqual(
            format_recipe_ingredient(
                {
                    "reagent_name": "Sage Bundle",
                    "quantity": 2,
                    "measure_amount": 2,
                    "measure_unit": "each",
                }
            ),
            "Sage Bundle (4 each)",
        )

    def test_inventory_and_recipe_share_item_uuid_and_measurement_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Identity Test")
            repository.add_inventory_item(
                "Dried Herbs",
                "Ingredient",
                5,
                "Aromatic dried leaves.",
                metadata={"quantity_unit": "grams"},
            )
            repository.add_crafting_recipe(
                name="Herbal Poultice",
                ingredients=[{
                    "reagent_name": "Dried Herbs",
                    "quantity": 1,
                    "measure_amount": 2,
                    "measure_unit": "grams",
                }],
                result="A simple poultice.",
            )

            inventory_item = repository.list_inventory_items()[0]
            ingredient = repository.list_crafting_recipes()[0]["ingredients"][0]
            self.assertEqual(inventory_item["quantity_unit"], "grams")
            self.assertEqual(
                ingredient["item_uuid"], inventory_item["metadata"]["item_uuid"]
            )

    def test_recipe_measurements_reject_vague_units(self) -> None:
        self.assertNotIn("pinch", COMMON_MEASUREMENT_UNITS)
        self.assertNotIn("handful", COMMON_MEASUREMENT_UNITS)
        ingredient = normalize_recipe_ingredient(
            {
                "reagent_name": "Moon Salt",
                "quantity": 1,
                "measure_amount": 1,
                "measure_unit": "handful",
            }
        )
        self.assertIsNotNone(ingredient)
        assert ingredient is not None
        self.assertEqual(ingredient["measure_unit"], "each")

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
                category="Container",
                description="Pale crystals that hum under moonlight.",
                location="Caves, Mineral Basins",
                uses=["cooling draughts", "mirror inks"],
                rarity="Rare",
                notes="Harvested only when the crystals are resonant.",
                value_base_units=36,
            )

            reagents = repository.list_crafting_items()

            self.assertEqual(len(reagents), 1)
            self.assertEqual(reagents[0]["name"], "Moon Salt")
            self.assertEqual(reagents[0]["category"], "Container")
            self.assertEqual(reagents[0]["description"], "Pale crystals that hum under moonlight.")
            self.assertEqual(reagents[0]["location"], "Caves, Mineral Basins")
            self.assertEqual(reagents[0]["uses"], ["cooling draughts", "mirror inks"])
            self.assertEqual(reagents[0]["rarity"], "Rare")
            self.assertEqual(
                reagents[0]["notes"],
                "Harvested only when the crystals are resonant. Rarity: Rare.",
            )
            self.assertEqual(reagents[0]["value_base_units"], 36)
            catalog = repository.list_item_catalog()
            moon_salt = next(item for item in catalog if item["name"] == "Moon Salt")
            self.assertEqual(moon_salt["category"], "Container")
            self.assertEqual(moon_salt["value_base_units"], 36)

    def test_crafting_item_notes_have_one_rarity_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Rarity Notes Test")
            repository.add_crafting_item(
                name="Noxious Herb Extract",
                description="A concentrated botanical essence.",
                notes=(
                    "Rarity: Common. Commonly found in damp urban alleys. "
                    "Rarity: Common."
                ),
                rarity="Common",
            )

            notes = repository.list_crafting_items()[0]["notes"]

            self.assertEqual(
                notes,
                "Commonly found in damp urban alleys. Rarity: Common.",
            )
            self.assertEqual(notes.count("Rarity:"), 1)
            self.assertTrue(notes.endswith("Rarity: Common."))

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
                value_base_units=24,
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
            self.assertEqual(recipes[0]["value_base_units"], 24)

    def test_state_manager_loads_reagents_and_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alchemy Test")
            repository.add_crafting_item(
                name="Ash Fern",
                description="Grey fronds that curl toward heat.",
                location="Charcoal-rich forest clearings",
                uses=["smoke readings"],
                rarity="Uncommon",
                notes="Rarity: Uncommon. Most visible after brushfires.",
                value_base_units=7,
            )
            repository.add_crafting_recipe(
                name="Ember Mnemonic",
                ingredients=[
                    {
                        "reagent_name": "Ash Fern",
                        "quantity": 1,
                        "measure_amount": 15,
                        "measure_unit": "grams",
                    }
                ],
                result="Restores a recent sensory impression.",
                notes="Unstable in rain.",
                value_base_units=11,
            )

            state = StateManager(repository).load_state()

            self.assertEqual(state.alchemy.known_reagents[0].name, "Ash Fern")
            self.assertEqual(state.alchemy.known_reagents[0].description, "Grey fronds that curl toward heat.")
            self.assertEqual(state.alchemy.known_reagents[0].location, "Charcoal-rich forest clearings")
            self.assertEqual(state.alchemy.known_reagents[0].rarity, "Uncommon")
            self.assertEqual(state.alchemy.known_reagents[0].value_base_units, 7)
            self.assertEqual(state.alchemy.known_recipes[0].name, "Ember Mnemonic")
            self.assertEqual(state.alchemy.known_recipes[0].value_base_units, 11)
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

    def test_deterministic_crafting_consumes_exact_ids_and_completes_passive_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Crafting Flow")
            repository.upsert_skill("Crafting", "Practical crafting skill.", 2)
            repository.add_inventory_item(
                "Dried Herb",
                "Ingredient",
                5,
                "A measured herb.",
                metadata={"item_uuid": "ingredient-dried-herb", "quantity_unit": "grams"},
            )
            repository.add_crafting_recipe(
                name="Herbal Tonic",
                ingredients=[
                    {
                        "reagent_name": "Dried Herb",
                        "item_uuid": "ingredient-dried-herb",
                        "quantity": 1,
                        "measure_amount": 2,
                        "measure_unit": "grams",
                    }
                ],
                result="A restorative tonic.",
                result_item_uuid="result-herbal-tonic",
                result_item_name="Herbal Tonic",
                stages=[
                    {"kind": "active", "work_amount": 3},
                    {
                        "kind": "passive",
                        "duration_minutes": 10,
                        "required_tool_item_names": ["Drying Rack"],
                    },
                ],
            )
            recipe = repository.list_crafting_recipes()[0]

            drying_rack = next(
                item for item in repository.list_item_catalog() if item["name"] == "Drying Rack"
            )
            repository.add_inventory_item(
                "Drying Rack",
                "Tool",
                1,
                "A rack for passive drying.",
            )
            self.assertEqual(
                recipe["stages"][1]["required_tool_item_uuids"],
                [drying_rack["metadata"]["item_uuid"]],
            )
            self.assertEqual(recipe["result_item_uuid"], "result-herbal-tonic")
            repository.set_current_calendar_minute(100)

            self.assertEqual(repository.craft_recipe(str(recipe["id"]))["status"], "active")
            self.assertEqual(repository.craft_recipe(str(recipe["id"]))["status"], "passive")
            self.assertEqual(
                next(
                    item
                    for item in repository.list_inventory_items()
                    if item["name"] == "Dried Herb"
                )["quantity"],
                3,
            )

            self.assertEqual(repository.get_current_calendar_minute(), 102)
            self.assertEqual(repository.get_setting("crafting.processes", [])[0]["passive_due_minute"], 112)
            self.assertEqual(repository.craft_recipe(str(recipe["id"]))["minutes_spent"], 0)
            repository.set_current_calendar_minute(108)
            result = repository.craft_recipe(str(recipe["id"]), until_done=True)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["minutes_spent"], 4)
            self.assertEqual(repository.get_current_calendar_minute(), 112)
            self.assertNotIn("survival_changes", result)
            tonic = next(
                item for item in repository.list_inventory_items() if item["name"] == "Herbal Tonic"
            )
            self.assertEqual(tonic["metadata"]["item_uuid"], "result-herbal-tonic")

    def test_deterministic_crafting_rejects_missing_tool_before_consuming_ingredients(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Crafting Tool Test")
            repository.add_inventory_item(
                "Dried Herb",
                "Ingredient",
                2,
                "A measured herb.",
                metadata={"item_uuid": "ingredient-dried-herb", "quantity_unit": "grams"},
            )
            repository.add_crafting_recipe(
                name="Rack Tonic",
                ingredients=[
                    {
                        "reagent_name": "Dried Herb",
                        "item_uuid": "ingredient-dried-herb",
                        "quantity": 1,
                        "measure_amount": 2,
                        "measure_unit": "grams",
                    }
                ],
                result="A tonic.",
                result_item_uuid="result-rack-tonic",
                required_tool_item_uuids=["tool-missing"],
            )
            recipe = repository.list_crafting_recipes()[0]

            result = repository.craft_recipe(str(recipe["id"]))

            self.assertEqual(result["status"], "rejected")
            self.assertEqual(repository.list_inventory_items()[0]["quantity"], 2)


if __name__ == "__main__":
    unittest.main()
