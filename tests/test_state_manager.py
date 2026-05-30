from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository


class StateManagerTests(unittest.TestCase):
    def test_new_game_defaults_are_debug_friendly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Test Adventure")

            state = StateManager(repository).load_state()
            item_names = {item.name for item in state.inventory.items}
            skill_names = {skill.name for skill in state.skills.skills}

            self.assertEqual(state.player.name, "Player Name")
            self.assertEqual(state.world.location, "Tavern")
            self.assertEqual(state.calendar.time_label, "Morning")
            self.assertEqual(state.calendar.month_name, "Month 1")
            self.assertIn("Healing Draught", item_names)
            self.assertIn("Iron Dagger", item_names)
            self.assertIn("Lantern", item_names)
            self.assertIn("Trail Ration", item_names)
            self.assertIn("Waterskin", item_names)
            self.assertIn("Alchemy", skill_names)
            self.assertIn("Athletics", skill_names)
            self.assertIn("Awareness", skill_names)
            self.assertIn("Melee", skill_names)
            self.assertIn("Persuasion", skill_names)

    def test_load_state_composes_repository_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Test Adventure")
            repository.set_setting("player_name", "Mira")
            repository.set_setting("player.appearance", "A road-worn apothecary.")
            repository.set_setting("player.backstory", "Raised by caravan healers.")
            repository.set_setting("player.notes", "Prefers quiet solutions.")
            repository.set_setting("ai.additional_context", "Use third-person narration.")
            repository.upsert_active_task(
                name="Find the Missing Ledger",
                category="Quest",
                description="Recover the missing tavern ledger.",
                requester="Mira Coppercup",
                location="Tavern",
            )
            repository.add_inventory_item("Lantern", "tool", 1, "A brass lantern.")
            repository.add_alchemy_note("Moon Salt", "Useful in cooling mixtures.")

            state = StateManager(repository).load_state()

            self.assertEqual(state.metadata.title, "Test Adventure")
            self.assertEqual(state.player.name, "Mira")
            self.assertEqual(state.player.appearance, "A road-worn apothecary.")
            self.assertEqual(state.player.backstory, "Raised by caravan healers.")
            self.assertEqual(state.player.notes, "Prefers quiet solutions.")
            self.assertEqual(
                state.settings.values["ai.additional_context"],
                "Use third-person narration.",
            )
            self.assertEqual(state.active_tasks.tasks[0].name, "Find the Missing Ledger")
            self.assertEqual(state.active_tasks.tasks[0].category, "Quest")
            self.assertEqual(state.player.condition, "Healthy")
            self.assertEqual(state.world.location, "Tavern")
            self.assertIn("Lantern", {item.name for item in state.inventory.items})
            self.assertEqual(state.alchemy.notes[0].title, "Moon Salt")
            self.assertGreaterEqual(len(state.history.entries), 3)

    def test_update_core_fields_persists_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Test Adventure")

            state = StateManager(repository).update_core_fields(
                location="Old Road",
                time="Day 1, Dusk",
                weather="Rain",
                condition="Winded",
            )

            self.assertEqual(state.world.location, "Old Road")
            self.assertEqual(state.world.time, "Day 1, Dusk")
            self.assertEqual(state.world.weather, "Rain")
            self.assertEqual(state.player.condition, "Winded")


if __name__ == "__main__":
    unittest.main()
