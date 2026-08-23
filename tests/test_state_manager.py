from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository


class StateManagerTests(unittest.TestCase):
    def test_message_snapshot_rolls_back_state_and_associated_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Rollback Test")
            player_message_id = repository.create_message_id()
            response_message_id = repository.create_message_id()
            repository.append_history(
                "player",
                "Search the workbench.",
                message_id=player_message_id,
            )
            repository.capture_message_snapshot(response_message_id)

            EventApplier(
                repository,
                message_id=response_message_id,
            ).apply_events(
                [
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_name": "Recovered Key",
                            "item_type": "Tool",
                            "description": "A small brass key.",
                            "amount": 1,
                            "quantity_unit": "each",
                        },
                    }
                ]
            )
            repository.append_history(
                "story",
                "You find a key.",
                message_id=response_message_id,
            )

            self.assertTrue(repository.rollback_message(response_message_id))
            self.assertNotIn(
                "Recovered Key",
                {item["name"] for item in repository.list_inventory_items()},
            )
            history = repository.list_history()
            self.assertIn("Search the workbench.", [entry["content"] for entry in history])
            self.assertNotIn("You find a key.", [entry["content"] for entry in history])
            self.assertFalse(repository.has_message_snapshot(response_message_id))
            self.assertTrue(
                all(
                    event["message_id"] != response_message_id
                    for event in repository.list_mechanical_events()
                )
            )

    def test_history_and_mechanical_events_have_message_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Message ID Test")
            message_id = repository.append_history("player", "Look around.")
            repository.append_mechanical_event(
                "TestEvent",
                {},
                "skipped",
                "Test result.",
                message_id=message_id,
            )

            history_entry = repository.list_history()[-1]
            mechanical_event = repository.list_mechanical_events()[-1]
            self.assertEqual(history_entry["message_id"], message_id)
            self.assertEqual(mechanical_event["message_id"], message_id)

    def test_history_preserves_narration_sound_cues_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Cue Replay Test")
            cue = {
                "filename": "Hammer.wav",
                "anchor_text": "hammer",
                "position": "after",
            }
            repository.append_history(
                "story",
                "The hammer falls against the anvil.",
                sound_effect_cues=[cue],
            )

            self.assertEqual(repository.list_history()[-1]["sound_effect_cues"], [cue])
            self.assertEqual(
                StateManager(repository).load_state().history.entries[-1].sound_effect_cues,
                [cue],
            )

    def test_new_game_defaults_are_debug_friendly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Test Adventure")

            state = StateManager(repository).load_state()
            item_names = {item.name for item in state.inventory.items}
            catalog_names = {item.name for item in state.item_catalog.items}
            skill_names = {skill.name for skill in state.skills.skills}

            self.assertEqual(state.player.name, "Player Name")
            self.assertEqual(state.world.location, "Tavern")
            self.assertEqual(state.calendar.time_label, "Morning")
            self.assertEqual(state.calendar.month_name, "Month 1")
            self.assertIn("Healing Draught", item_names)
            self.assertIn("Iron Dagger", item_names)
            self.assertIn("Lantern", item_names)
            self.assertIn("Lantern", catalog_names)
            self.assertIn("Trail Ration", item_names)
            self.assertIn("Waterskin", item_names)
            self.assertIn("Crafting", skill_names)
            self.assertIn("Athletics", skill_names)
            self.assertIn("Awareness", skill_names)
            self.assertIn("Melee", skill_names)
            self.assertIn("Persuasion", skill_names)

    def test_load_state_composes_repository_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Test Adventure")
            repository.set_setting("player_name", "Mira")
            repository.set_setting("player.pronouns", "She/Her")
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
            repository.add_crafting_item(
                name="Moon Salt",
                description="Useful in cooling mixtures.",
                uses=["cooling mixtures"],
            )

            state = StateManager(repository).load_state()

            self.assertEqual(state.metadata.title, "Test Adventure")
            self.assertEqual(state.player.name, "Mira")
            self.assertEqual(state.player.pronouns, "She/Her")
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
            self.assertIn("Lantern", {item.name for item in state.item_catalog.items})
            self.assertEqual(state.alchemy.known_reagents[0].name, "Moon Salt")
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
