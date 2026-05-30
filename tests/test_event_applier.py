from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository


class EventApplierTests(unittest.TestCase):
    def test_applies_inventory_add_remove_and_modify_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Event Test")
            applier = EventApplier(repository)

            results = applier.apply_events(
                [
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_type": "Tool",
                            "item_name": "Glass Jar",
                            "description": "A clean stoppered jar.",
                            "amount": 2,
                        },
                    },
                    {
                        "type": "InventoryItemModifiedEvent",
                        "payload": {
                            "target_name": "Glass Jar",
                            "new_description": "A clean stoppered jar holding dried herbs.",
                            "new_amount": 1,
                        },
                    },
                ]
            )

            items = repository.list_inventory_items()
            glass_jars = [item for item in items if item["name"] == "Glass Jar"]

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertEqual(len(glass_jars), 1)
            self.assertEqual(glass_jars[0]["quantity"], 1)
            self.assertIn("dried herbs", glass_jars[0]["description"])

            remove_result = applier.apply_event(
                {
                    "type": "InventoryItemRemovedEvent",
                    "payload": {"item_name": "Glass Jar", "amount": 1},
                }
            )

            self.assertEqual(remove_result.status, "applied")
            self.assertNotIn(
                "Glass Jar",
                {item["name"] for item in repository.list_inventory_items()},
            )

    def test_applies_status_flag_and_currency_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Event Test")

            EventApplier(repository).apply_events(
                [
                    {
                        "type": "StatusUpdatedEvent",
                        "payload": {
                            "location": "Old Road",
                            "minutes_passed": 15,
                            "weather": "Rain",
                        },
                    },
                    {
                        "type": "FlagSetEvent",
                        "payload": {"key": "met_gate_guard", "value": True},
                    },
                    {
                        "type": "CurrencyChangedEvent",
                        "payload": {"base_unit_amount": 25},
                    },
                ]
            )

            snapshot = repository.get_state_snapshot()

            self.assertEqual(snapshot["location"], "Old Road")
            self.assertEqual(snapshot["weather"], "Rain")
            self.assertEqual(snapshot["elapsed_minutes"], "495")
            self.assertEqual(snapshot["flag.met_gate_guard"], "True")
            self.assertEqual(snapshot["currency.balance"], "25")

    def test_location_changed_event_stores_short_broad_location_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Location Test")

            EventApplier(repository).apply_event(
                {
                    "type": "LocationChangedEvent",
                    "payload": {
                        "location": (
                            "Y/N's Office, high up near the penthouse, overlooking "
                            "the Hudson River"
                        )
                    },
                }
            )

            self.assertEqual(
                repository.get_state_snapshot()["location"],
                "Y/N's Office",
            )

    def test_applies_music_changed_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Music Test")

            result = EventApplier(repository).apply_events(
                [
                    {
                        "type": "MusicChangedEvent",
                        "payload": {"filename": "Town Village City.mp3"},
                    }
                ]
            )[0]

            self.assertEqual(result.status, "applied")
            self.assertEqual(
                repository.get_setting("audio.current_music"),
                "Town Village City.mp3",
            )

    def test_normalizes_event_type_alias_from_new_game_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Alias Test")

            results = EventApplier(repository).apply_events(
                [
                    {
                        "event_type": "MusicChangedEvent",
                        "filename": "Boss_Fight.mp3",
                    },
                    {
                        "event_type": "NpcUpsertedEvent",
                        "display_name": "Bartender",
                        "location": "The Gilded Tankard",
                        "player_facing_information": "A tired bartender polishes cloudy glasses.",
                    },
                ]
            )
            visible_npcs = repository.list_player_visible_npcs()

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertEqual(repository.get_setting("audio.current_music"), "Boss_Fight.mp3")
            self.assertEqual(visible_npcs[0]["display_name"], "Bartender")

    def test_world_lore_events_update_player_lore_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Lore Test")

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "WorldLoreAddedEvent",
                        "payload": {
                            "section": "Locations",
                            "key": "The Gilded Tankard",
                            "text": "The Gilded Tankard is a smoky tavern in Amberfell.",
                        },
                    },
                    {
                        "type": "WorldLoreChangedEvent",
                        "payload": {
                            "section": "Locations",
                            "key": "The Gilded Tankard",
                            "replacement_lore": (
                                "The Gilded Tankard is a smoky tavern in Amberfell "
                                "known for discreet contract work."
                            ),
                        },
                    }
                ]
            )

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertEqual(
                repository.get_world_lore()["Locations"]["The Gilded Tankard"],
                "The Gilded Tankard is a smoky tavern in Amberfell known for discreet contract work.",
            )

    def test_applies_alchemy_discovery_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Event Test")

            EventApplier(repository).apply_events(
                [
                    {
                        "type": "ReagentDiscoveredEvent",
                        "payload": {
                            "name": "Moonwater",
                            "qualities": ["cool", "moist"],
                            "motions": ["stilling", "opening"],
                            "virtues": ["reception"],
                            "uses": ["sleep draughts"],
                            "notes": "Prepared under moonlight.",
                        },
                    },
                    {
                        "type": "RecipeDiscoveredEvent",
                        "payload": {
                            "name": "Quiet Sleep Draught",
                            "ingredients": {"Moonwater": 1, "Mooncap Fungus": 1},
                            "result": "Invites sleep.",
                            "motions": ["stilling"],
                            "virtues": ["sleep"],
                        },
                    },
                ]
            )

            reagents = repository.list_alchemy_reagents()
            recipes = repository.list_alchemy_recipes()

            self.assertEqual(reagents[0]["name"], "Moonwater")
            self.assertEqual(reagents[0]["qualities"], ["cool", "moist"])
            self.assertEqual(recipes[0]["name"], "Quiet Sleep Draught")
            self.assertEqual(recipes[0]["ingredients"], ["Moonwater: 1", "Mooncap Fungus: 1"])

    def test_applies_npc_profile_and_knowledge_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "NPC Test")

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "NpcUpsertedEvent",
                        "payload": {
                            "name": "Mira Coppercup",
                            "display_name": "Bartender",
                            "role": "Bartender",
                            "location": "Tavern",
                            "public_description": "A tired bartender polishing cloudy glasses.",
                            "player_facing_information": (
                                "Mira Coppercup tends bar at the tavern and hears local gossip."
                            ),
                            "knowledge_scope": [
                                "Common tavern gossip",
                                "Visible behavior at the bar",
                            ],
                            "known_facts": ["Mira knows which regulars water their ale."],
                        },
                    },
                    {
                        "type": "NpcKnowledgeAddedEvent",
                        "payload": {
                            "name": "Mira Coppercup",
                            "facts": ["The player asked about the north road."],
                        },
                    },
                ]
            )

            npcs = repository.list_relevant_npcs(
                location="Tavern",
                query_text="ask the bartender about rumors",
            )
            visible_npcs = repository.list_player_visible_npcs()

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertEqual(len(npcs), 1)
            self.assertEqual(npcs[0]["name"], "Mira Coppercup")
            self.assertEqual(
                npcs[0]["player_facing_information"],
                "Mira Coppercup tends bar at the tavern and hears local gossip.",
            )
            self.assertIn("Common tavern gossip", npcs[0]["knowledge_scope"])
            self.assertIn("Mira knows which regulars water their ale.", npcs[0]["known_facts"])
            self.assertIn("The player asked about the north road.", npcs[0]["known_facts"])
            self.assertEqual(visible_npcs[0]["display_name"], "Bartender")
            self.assertEqual(
                visible_npcs[0]["description"],
                "A tired bartender polishing cloudy glasses.",
            )
            self.assertEqual(visible_npcs[0]["location"], "Tavern")
            self.assertEqual(
                visible_npcs[0]["notes"],
                "Mira Coppercup tends bar at the tavern and hears local gossip.",
            )
            self.assertNotIn("name", visible_npcs[0])
            self.assertNotIn("role", visible_npcs[0])
            self.assertNotIn("known_facts", visible_npcs[0])
            self.assertNotIn("knowledge_scope", visible_npcs[0])
            self.assertNotIn("updated_at", visible_npcs[0])

    def test_applies_active_task_and_quest_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Task Test")

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "QuestAddedEvent",
                        "payload": {
                            "name": "Find the Missing Ledger",
                            "giver": "Mira Coppercup",
                            "description": "Recover the missing tavern ledger.",
                            "turn_in": "Tavern",
                            "reward": "Free room and board.",
                        },
                    },
                    {
                        "type": "ActiveTaskUpsertedEvent",
                        "payload": {
                            "name": "Silver Ring Commission",
                            "category": "Commission",
                            "status": "Waiting",
                            "description": "Pick up the engraved silver ring.",
                            "requester": "Silversmith Orren",
                            "location": "Market Lane",
                            "reward": "Paid in advance.",
                            "due_date": "Month 1 3",
                        },
                    },
                ]
            )

            tasks = repository.list_active_tasks()
            task_names = {task["name"] for task in tasks}

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertIn("Find the Missing Ledger", task_names)
            self.assertIn("Silver Ring Commission", task_names)

            ledger_task = repository.get_active_task("Find the Missing Ledger")
            self.assertIsNotNone(ledger_task)
            self.assertEqual(ledger_task["category"], "Quest")
            self.assertEqual(ledger_task["requester"], "Mira Coppercup")

            complete_result = EventApplier(repository).apply_event(
                {
                    "type": "ActiveTaskCompletedEvent",
                    "payload": {
                        "name": "Silver Ring Commission",
                        "notes": "The ring was collected.",
                    },
                }
            )

            self.assertEqual(complete_result.status, "applied")
            self.assertNotIn(
                "Silver Ring Commission",
                {task["name"] for task in repository.list_active_tasks()},
            )

            completed_task = repository.get_active_task("Silver Ring Commission")
            self.assertIsNotNone(completed_task)
            self.assertEqual(completed_task["status"], "Completed")
            self.assertEqual(completed_task["notes"], "The ring was collected.")

    def test_npc_upsert_allows_display_name_without_known_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "NPC Test")

            result = EventApplier(repository).apply_event(
                {
                    "type": "NpcUpsertedEvent",
                    "payload": {
                        "display_name": "Shady Character",
                        "location": "Dark Alley",
                        "player_facing_information": (
                            "A wary figure lingered near the alley mouth."
                        ),
                        "knowledge_scope": ["Street rumors", "Visible alley activity"],
                    },
                }
            )

            visible_npcs = repository.list_player_visible_npcs()

            self.assertEqual(result.status, "applied")
            self.assertEqual(visible_npcs[0]["display_name"], "Shady Character")
            self.assertNotIn("name", visible_npcs[0])

    def test_npc_upsert_uses_name_as_visible_fallback_before_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "NPC Test")

            result = EventApplier(repository).apply_event(
                {
                    "type": "NpcUpsertedEvent",
                    "payload": {
                        "name": "Barmaid Elina",
                        "role": "Tavern server and local gossip source",
                    },
                }
            )

            visible_npcs = repository.list_player_visible_npcs()

            self.assertEqual(result.status, "applied")
            self.assertEqual(visible_npcs[0]["display_name"], "Barmaid Elina")
            self.assertEqual(
                visible_npcs[0]["notes"],
                "Tavern server and local gossip source",
            )
            self.assertEqual(visible_npcs[0]["description"], "")

    def test_applies_multiple_npc_upsert_events_in_one_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "NPC Test")

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "NpcUpsertedEvent",
                        "payload": {
                            "internal_name": "dice_player_one",
                            "display_name": "Rough-Looking Figure",
                            "location": "Tavern",
                        },
                    },
                    {
                        "type": "NpcUpsertedEvent",
                        "payload": {
                            "internal_name": "dice_player_two",
                            "display_name": "Second Rough-Looking Figure",
                            "location": "Tavern",
                        },
                    },
                ]
            )

            visible_names = {
                npc["display_name"] for npc in repository.list_player_visible_npcs()
            }

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertIn("Rough-Looking Figure", visible_names)
            self.assertIn("Second Rough-Looking Figure", visible_names)

    def test_records_mechanical_event_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Event Test")

            with self.assertLogs("ai_adventure.events.event_applier", level="WARNING"):
                EventApplier(repository).apply_events(
                    [
                        {"type": "UnknownEvent", "payload": {"value": 1}},
                    ]
                )

            events = repository.list_mechanical_events()

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "UnknownEvent")
            self.assertEqual(events[0]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
