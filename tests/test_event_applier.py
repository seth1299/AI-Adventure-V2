from __future__ import annotations

import json
import tempfile
import unittest
import sqlite3
import random
from pathlib import Path
from typing import TypeVar

from ai_adventure.calendar_system import build_calendar_snapshot
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository


ValueT = TypeVar("ValueT")


def _require(value: ValueT | None) -> ValueT:
    """Narrows an optional repository lookup for a test assertion."""

    assert value is not None
    return value


class _FixedRollRng:
    def __init__(self, roll: int) -> None:
        self.roll = roll

    def randint(self, minimum: int, maximum: int, /) -> int:
        return self.roll


def _test_container_metadata(
    *,
    locked: bool = False,
    trapped: bool = False,
) -> dict[str, object]:
    return {
        "item_type": "Container",
        "container": {
            "is_open": False,
            "contents_taken": False,
            "is_locked": locked,
            "lockpick_skill": "Lockpicking",
            "lockpick_dc": 16 if locked else 0,
            "lockpick_failure_consequence": "The pick snaps.",
            "is_trapped": trapped,
            "trap_notice_skill": "Perception",
            "trap_notice_dc": 12 if trapped else 0,
            "trap_disarm_skill": "Sleight of Hand",
            "trap_disarm_dc": 15 if trapped else 0,
            "trap_failure_consequence": "A poisoned needle strikes.",
            "contents": {
                "currency_base_units": 35,
                "items": [
                    {
                        "name": "Tarnished Silver Locket",
                        "category": "Valuable",
                        "quantity": 1,
                        "description": "A locket with a worn clasp.",
                        "value_base_units": 12,
                    }
                ],
            },
        },
    }


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
            self.assertEqual(glass_jars[0]["value_base_units"], 1)
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

    def test_container_contents_require_opening_and_transfer_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Container Test")
            repository.set_state_value("currency.balance", "5")
            repository.add_inventory_item(
                "Stolen Coin Pouch",
                "Container",
                1,
                "A tied leather pouch.",
                2,
                metadata=_test_container_metadata(),
            )
            applier = EventApplier(repository)

            generic_modify = applier.apply_event(
                {
                    "type": "InventoryItemModifiedEvent",
                    "payload": {
                        "target_name": "Stolen Coin Pouch",
                        "new_description": "An opened, empty pouch.",
                    },
                }
            )
            closed_take = applier.apply_event(
                {
                    "type": "ContainerContentsTakenEvent",
                    "payload": {"container_name": "Stolen Coin Pouch"},
                }
            )
            opened_and_taken = applier.apply_events(
                [
                    {
                        "type": "ContainerOpenedEvent",
                        "payload": {"container_name": "Stolen Coin Pouch"},
                    },
                    {
                        "type": "ContainerContentsTakenEvent",
                        "payload": {"container_name": "Stolen Coin Pouch"},
                    },
                ]
            )
            repeated_take = applier.apply_event(
                {
                    "type": "ContainerContentsTakenEvent",
                    "payload": {"container_name": "Stolen Coin Pouch"},
                }
            )
            items = {
                item["name"]: item
                for item in repository.list_inventory_items()
            }
            container = items["Stolen Coin Pouch"]["metadata"]["container"]

            self.assertEqual(generic_modify.status, "skipped")
            self.assertEqual(closed_take.status, "skipped")
            self.assertEqual(
                [result.status for result in opened_and_taken],
                ["applied", "applied"],
            )
            self.assertEqual(repeated_take.status, "skipped")
            self.assertEqual(repository.get_state_value("currency.balance"), "40")
            self.assertEqual(items["Tarnished Silver Locket"]["quantity"], 1)
            self.assertTrue(container["is_open"])
            self.assertTrue(container["contents_taken"])

    def test_locked_trapped_container_uses_stored_check_skills_and_dcs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Secured Container")
            repository.add_inventory_item(
                "Cipher Chest",
                "Container",
                1,
                "A small chest with a complex lock.",
                20,
                metadata=_test_container_metadata(locked=True, trapped=True),
            )
            repository.upsert_skill("Lockpicking", "Opening locks.", 1)
            repository.upsert_skill("Sleight of Hand", "Fine manual work.", 1)
            applier = EventApplier(repository, rng=_FixedRollRng(20))

            missing_checks = applier.apply_event(
                {
                    "type": "ContainerOpenedEvent",
                    "payload": {"container_name": "Cipher Chest"},
                }
            )
            check_results = applier.apply_events(
                [
                    {
                        "type": "SkillCheckRequestedEvent",
                        "payload": {"skill_name": "Lockpicking", "dc": 16},
                    },
                    {
                        "type": "SkillCheckRequestedEvent",
                        "payload": {"skill_name": "Sleight of Hand", "dc": 15},
                    },
                ]
            )
            opened = applier.apply_events(
                [
                    {
                        "type": "ContainerOpenedEvent",
                        "payload": {"container_name": "Cipher Chest"},
                    }
                ],
                prior_results=check_results,
            )
            container = repository.list_inventory_items()[0]["metadata"]["container"]

            self.assertEqual(missing_checks.status, "skipped")
            self.assertIn("Lockpicking", missing_checks.message)
            self.assertEqual(opened[0].status, "applied")
            self.assertTrue(container["is_open"])
            self.assertFalse(container["is_locked"])
            self.assertFalse(container["is_trapped"])

    def test_container_batch_rejects_duplicate_direct_rewards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Duplicate Guard")
            repository.set_state_value("currency.balance", "0")
            repository.add_inventory_item(
                "Coin Pouch",
                "Container",
                1,
                "An open pouch.",
                2,
                metadata={
                    **_test_container_metadata(),
                    "container": {
                        **_test_container_metadata()["container"],
                        "is_open": True,
                    },
                },
            )

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "ContainerContentsTakenEvent",
                        "payload": {"container_name": "Coin Pouch"},
                    },
                    {
                        "type": "CurrencyChangedEvent",
                        "payload": {"base_unit_amount": 35},
                    },
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_type": "Valuable",
                            "item_name": "Tarnished Silver Locket",
                            "description": "An accidental duplicate.",
                            "amount": 1,
                            "value_base_units": 12,
                        },
                    },
                ]
            )
            items = repository.list_inventory_items()

            self.assertEqual(
                [result.status for result in results],
                ["applied", "skipped", "skipped"],
            )
            self.assertEqual(repository.get_state_value("currency.balance"), "35")
            self.assertEqual(
                sum(
                    item["quantity"]
                    for item in items
                    if item["name"] == "Tarnished Silver Locket"
                ),
                1,
            )

    def test_combat_started_event_persists_combat_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Combat Event Test")
            repository.add_inventory_item(
                "Spear",
                "Weapon",
                1,
                "A sturdy one-handed spear.",
                6,
                metadata={
                    "item_type": "Weapon",
                    "weapon_hands": "one-handed",
                    "damage": "1d8",
                },
            )
            repository.set_player_equipment({"Main Hand": "Spear"})
            repository.set_setting("player.health_current", 18)
            repository.set_setting("player.health_max", 24)

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "CombatStartedEvent",
                        "payload": {
                            "description": "Two bandits draw blades.",
                            "enemies": [
                                {
                                    "name": "Bandit",
                                    "health": 7,
                                    "armor_rating": 12,
                                    "to_hit_bonus": 3,
                                    "initiative_bonus": 4,
                                    "personality": "aggressive",
                                    "weapon_name": "Rusty Knife",
                                    "ammunition_type_required": "",
                                    "clip_size": 0,
                                    "clip_ammo": 0,
                                    "bullets_per_attack": 0,
                                    "reserve_ammo": 0,
                                    "damage": "1d6+1",
                                    "loot": ["Rusty Knife", "Copper Ring"],
                                }
                            ],
                            "allies": [
                                {
                                    "name": "Mira",
                                    "health": 10,
                                    "armor_rating": 11,
                                    "to_hit_bonus": 2,
                                    "initiative_bonus": 1,
                                    "personality": "cautious",
                                    "weapon_name": "Shortbow",
                                    "ammunition_type_required": "Arrow",
                                    "clip_size": 1,
                                    "clip_ammo": 1,
                                    "bullets_per_attack": 1,
                                    "reserve_ammo": 12,
                                    "damage": "1d4",
                                }
                            ],
                        },
                    }
                ]
            )
            combat_state = repository.get_combat_state()
            player = next(
                combatant
                for combatant in combat_state["combatants"]
                if combatant["id"] == "player"
            )
            ally = next(
                combatant
                for combatant in combat_state["combatants"]
                if combatant["name"] == "Mira"
            )
            enemy = next(
                combatant
                for combatant in combat_state["combatants"]
                if combatant["name"] == "Bandit"
            )

            self.assertEqual(results[0].status, "applied")
            self.assertTrue(combat_state["active"])
            self.assertEqual(combat_state["round"], 1)
            self.assertEqual(player["name"], "Player Name")
            self.assertEqual(player["team"], "party")
            self.assertEqual(player["current_health"], 18)
            self.assertEqual(player["max_health"], 24)
            self.assertEqual(player["damage"], "1d8")
            self.assertEqual(player["to_hit_bonus"], 2)
            self.assertEqual(ally["name"], "Mira")
            self.assertEqual(ally["team"], "party")
            self.assertEqual(ally["to_hit_bonus"], 2)
            self.assertEqual(enemy["name"], "Bandit")
            self.assertEqual(enemy["team"], "enemy")
            self.assertEqual(enemy["current_health"], 7)
            self.assertEqual(enemy["armor_rating"], 12)
            self.assertEqual(enemy["to_hit_bonus"], 3)
            self.assertEqual(enemy["initiative_bonus"], 4)
            self.assertEqual(enemy["personality"], "aggressive")
            self.assertGreater(enemy["threat_level"], 0)
            self.assertEqual(enemy["damage"], "1d6+1")
            self.assertEqual(enemy["loot"], ["Rusty Knife", "Copper Ring"])
            self.assertEqual(combat_state["log"][0], "Two bandits draw blades.")
            self.assertIn("Initiative order:", combat_state["log"][1])
            self.assertTrue(
                all(
                    1 <= combatant["initiative_roll"] <= 20
                    for combatant in combat_state["combatants"]
                )
            )

            skipped = EventApplier(repository).apply_event(
                {"type": "CombatStartedEvent", "payload": {"enemy_name": "Second Bandit"}}
            )

            self.assertEqual(skipped.status, "skipped")

    def test_player_equipment_updates_inventory_equipped_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Inventory Equipment Flag Test",
            )

            repository.set_player_equipment({"Main Hand": "Iron Dagger"})
            dagger = next(
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Iron Dagger"
            )

            self.assertTrue(dagger["equipped"])

            repository.set_player_equipment({})
            dagger = next(
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Iron Dagger"
            )

            self.assertFalse(dagger["equipped"])

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
            self.assertNotIn("elapsed_minutes", snapshot)
            self.assertEqual(repository.get_current_calendar_minute(), 495)
            self.assertEqual(
                snapshot["time"],
                build_calendar_snapshot(
                    495,
                    repository.get_calendar_settings(),
                )["display_label"],
            )
            self.assertEqual(snapshot["flag.met_gate_guard"], "True")
            self.assertEqual(snapshot["currency.balance"], "25")

    def test_purchase_events_use_single_currency_balance_for_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Purchase Test")
            repository.set_state_value("currency.balance", "100")

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_name": "Traveling Cloak",
                            "category": "Clothing",
                            "quantity": 1,
                            "description": "A warm cloak for canal evenings.",
                            "value_base_units": 35,
                        },
                    },
                    {
                        "type": "CurrencyChangedEvent",
                        "payload": {"base_unit_amount": -35},
                    },
                ]
            )

            items = repository.list_inventory_items()
            snapshot = repository.get_state_snapshot()

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertIn("Traveling Cloak", {item["name"] for item in items})
            self.assertEqual(snapshot["currency.balance"], "65")
            self.assertIn("6 Silver Pieces and 5 Copper Pieces", results[1].message)
            self.assertNotIn(
                "Gold Piece",
                {
                    item["name"]
                    for item in items
                    if str(item.get("category", "")).casefold() == "currency"
                },
            )

    def test_inventory_item_added_merges_existing_stack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Stack Test")

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_type": "Botanical",
                            "item_name": "Silver-Spire Fern",
                            "description": "Cool-natured fern.",
                            "amount": 2,
                            "value_base_units": 8,
                        },
                    },
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_type": "Botanical",
                            "item_name": "Silver-Spire Fern",
                            "description": "Cool-natured fern.",
                            "amount": 2,
                            "value_base_units": 8,
                        },
                    },
                ]
            )

            items = repository.list_inventory_items()

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            fern_items = [item for item in items if item["name"] == "Silver-Spire Fern"]
            self.assertEqual(len(fern_items), 1)
            self.assertEqual(fern_items[0]["quantity"], 4)
            self.assertFalse(fern_items[0]["equipped"])
            self.assertEqual(fern_items[0]["value_base_units"], 8)

    def test_failed_skill_check_blocks_following_reward_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Gate Test")
            repository.upsert_skill("Foraging", "Finding useful materials.", 1)
            results = EventApplier(repository, rng=random.Random(2)).apply_events(
                [
                    {
                        "type": "SkillCheckRequestedEvent",
                        "payload": {"skill_name": "Foraging", "dc": 14},
                    },
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_type": "Geological",
                            "item_name": "Shimmering Stream Mineral",
                            "description": "A dense, cool-to-the-touch stone.",
                            "amount": 1,
                            "value_base_units": 25,
                        },
                    },
                    {
                        "type": "StatusUpdatedEvent",
                        "payload": {
                            "location": "Dastrium Valley",
                            "minutes_passed": 15,
                            "weather": "Clear",
                        },
                    },
                ]
            )

            self.assertEqual([result.status for result in results], ["applied", "skipped", "applied"])
            self.assertIn("previous skill check failed", results[1].message)
            self.assertNotIn(
                "Shimmering Stream Mineral",
                {item["name"] for item in repository.list_inventory_items()},
            )
            self.assertEqual(repository.get_state_snapshot()["location"], "Dastrium Valley")

    def test_failed_prior_skill_check_blocks_later_reward_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Split Gate Test")
            repository.upsert_skill("Foraging", "Finding useful materials.", 1)
            applier = EventApplier(repository, rng=random.Random(2))
            prior_results = applier.apply_events(
                [
                    {
                        "type": "SkillCheckRequestedEvent",
                        "payload": {"skill_name": "Foraging", "dc": 14},
                    }
                ]
            )

            results = applier.apply_events(
                [
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_type": "Botanical",
                            "item_name": "Silver-Spire Fern",
                            "description": "Cool-natured fern.",
                            "amount": 1,
                            "value_base_units": 8,
                        },
                    }
                ],
                prior_results=prior_results,
            )

            self.assertEqual(prior_results[0].payload["outcome"], "failure")
            self.assertEqual(results[0].status, "skipped")
            self.assertIn("previous skill check failed", results[0].message)
            self.assertNotIn(
                "Silver-Spire Fern",
                {item["name"] for item in repository.list_inventory_items()},
            )

    def test_successful_skill_check_allows_following_reward_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Gate Test")
            repository.upsert_skill("Foraging", "Finding useful materials.", 5)

            results = EventApplier(repository, rng=random.Random(2)).apply_events(
                [
                    {
                        "type": "SkillCheckRequestedEvent",
                        "payload": {"skill_name": "Foraging", "dc": 11},
                    },
                    {
                        "type": "InventoryItemAddedEvent",
                        "payload": {
                            "item_type": "Botanical",
                            "item_name": "Silver-Spire Fern",
                            "description": "Cool-natured fern.",
                            "amount": 1,
                            "value_base_units": 8,
                        },
                    },
                ]
            )

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertIn(
                "Silver-Spire Fern",
                {item["name"] for item in repository.list_inventory_items()},
            )

    def test_bad_luck_streak_nudges_skill_check_roll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Luck Test")
            repository.upsert_skill("Prospecting", "Reading ore signs and mineral veins.", 3)

            for roll in [3, 11, 9, 2, 13, 5, 4]:
                repository.record_skill_check(
                    skill_name="Prospecting",
                    level=3,
                    bonus=6,
                    roll=roll,
                    total=roll + 6,
                    dc=14,
                    outcome="success" if roll + 6 >= 14 else "failure",
                )

            result = EventApplier(repository, rng=_FixedRollRng(8)).apply_event(
                {
                    "type": "SkillCheckRequestedEvent",
                    "payload": {"skill_name": "Prospecting", "dc": 15},
                }
            )
            check = repository.list_skill_checks(limit=1)[0]

            self.assertEqual(result.status, "applied")
            self.assertEqual(result.payload["raw_roll"], 8)
            self.assertEqual(result.payload["bad_luck_nudge"], 2)
            self.assertEqual(result.payload["roll"], 10)
            self.assertEqual(result.payload["total"], 16)
            self.assertEqual(result.payload["outcome"], "success")
            self.assertEqual(check["roll"], 10)
            self.assertEqual(check["total"], 16)

    def test_normal_roll_history_does_not_nudge_skill_check_roll(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Luck Test")
            repository.upsert_skill("Prospecting", "Reading ore signs and mineral veins.", 3)

            for roll in [3, 11, 9, 12, 13]:
                repository.record_skill_check(
                    skill_name="Prospecting",
                    level=3,
                    bonus=6,
                    roll=roll,
                    total=roll + 6,
                    dc=14,
                    outcome="success" if roll + 6 >= 14 else "failure",
                )

            result = EventApplier(repository, rng=_FixedRollRng(8)).apply_event(
                {
                    "type": "SkillCheckRequestedEvent",
                    "payload": {"skill_name": "Prospecting", "dc": 15},
                }
            )

            self.assertEqual(result.status, "applied")
            self.assertEqual(result.payload["raw_roll"], 8)
            self.assertEqual(result.payload["bad_luck_nudge"], 0)
            self.assertEqual(result.payload["roll"], 8)
            self.assertEqual(result.payload["total"], 14)
            self.assertEqual(result.payload["outcome"], "failure")

    def test_existing_duplicate_inventory_stacks_are_coalesced_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "old.sqlite3"
            connection = sqlite3.connect(save_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE inventory_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT '',
                        quantity INTEGER NOT NULL DEFAULT 1,
                        description TEXT NOT NULL DEFAULT '',
                        value_base_units INTEGER NOT NULL DEFAULT 0
                    );
                    INSERT INTO inventory_items (
                        name,
                        category,
                        quantity,
                        description,
                        value_base_units
                    )
                    VALUES
                        ('Silver-Spire Fern', 'Botanical', 2, 'Cool-natured fern.', 8),
                        ('Silver-Spire Fern', 'Botanical', 2, 'Cool-natured fern.', 8);
                    """
                )
            finally:
                connection.close()

            repository = SaveRepository(save_path)
            fern_items = [
                item
                for item in repository.list_inventory_items()
                if item["name"] == "Silver-Spire Fern"
            ]

            self.assertEqual(len(fern_items), 1)
            self.assertEqual(fern_items[0]["quantity"], 4)

    def test_status_updated_event_stores_short_broad_location_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Location Test")

            EventApplier(repository).apply_event(
                {
                    "type": "StatusUpdatedEvent",
                    "payload": {
                        "location": (
                            "Y/N's Office, high up near the penthouse, overlooking "
                            "the Hudson River"
                        ),
                        "minutes_passed": "AUTO",
                        "weather": "AUTO",
                        "discover_location": True,
                    },
                }
            )

            self.assertEqual(
                repository.get_state_snapshot()["location"],
                "Y/N's Office",
            )

    def test_location_upserted_and_travel_mode_events_store_travel_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Travel Event Test")
            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "LocationUpsertedEvent",
                        "payload": {
                            "name": "Old Road",
                            "description": "A well-marked road beyond the city gate.",
                            "x_miles": 3,
                            "y_miles": 4,
                            "terrain": "Packed dirt",
                            "travel_multiplier": 0.8,
                            "travel_notes": "Patrolled at dusk.",
                        },
                    },
                    {
                        "type": "TravelModeChangedEvent",
                        "payload": {"mode": "Horse and Buggy", "speed_multiplier": 1.8},
                    },
                ]
            )

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            location = _require(repository.find_travel_location("Old Road"))
            self.assertEqual(location["terrain"], "Packed dirt")
            self.assertEqual((location["x_miles"], location["y_miles"]), (3.0, 4.0))
            self.assertEqual(repository.get_travel_profile()["travel_mode"], "Horse and Buggy")
            self.assertEqual(repository.get_travel_profile()["speed_multiplier"], 1.8)

    def test_travel_mode_event_requires_speed_multiplier(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Travel Mode Test")
            result = EventApplier(repository).apply_event(
                {
                    "type": "TravelModeChangedEvent",
                    "payload": {"mode": "Horse and Buggy"},
                }
            )

            self.assertEqual(result.status, "skipped")
            self.assertIn("speed multiplier", result.message.casefold())

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

    def test_world_lore_upsert_event_replaces_player_lore(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Lore Test")

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "WorldLoreUpsertedEvent",
                        "payload": {
                            "section": "Factions",
                            "key": "The Gilded Compact",
                            "text": "The Gilded Compact controls the river tolls.",
                        },
                    },
                    {
                        "type": "WorldLoreUpsertedEvent",
                        "payload": {
                            "section": "Factions",
                            "key": "The Gilded Compact",
                            "text": (
                                "The Gilded Compact controls the river tolls and "
                                "licenses trusted merchants."
                            ),
                        },
                    }
                ]
            )

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertEqual(
                repository.get_world_lore()["Factions"]["The Gilded Compact"],
                "The Gilded Compact controls the river tolls and licenses trusted merchants.",
            )

    def test_event_payloads_sanitize_banned_creative_terms_before_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Guardrail Test")

            with self.assertLogs("ai_adventure.events.event_applier", level="WARNING"):
                results = EventApplier(repository).apply_events(
                    [
                        {
                            "type": "StatusUpdatedEvent",
                            "payload": {
                                "location": "New Aethelgard",
                                "minutes_passed": "AUTO",
                                "weather": "Clear",
                            },
                        },
                        {
                            "type": "WorldLoreUpsertedEvent",
                            "payload": {
                                "section": "Factions",
                                "key": "New Aethelgard",
                                "text": "New Aethelgard is a crowded city.",
                            },
                        },
                    ]
                )

            stored_lore = json.dumps(repository.get_world_lore(), ensure_ascii=False)

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertNotIn("Aethelgard", repository.get_state_value("location"))
            self.assertNotIn("Aethelgard", stored_lore)

    def test_applies_alchemy_discovery_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Event Test")

            EventApplier(repository).apply_events(
                [
                    {
                        "type": "ReagentDiscoveredEvent",
                        "payload": {
                            "name": "Moonwater",
                            "description": "Water prepared under moonlight.",
                            "location": "Open bowls left beneath a full moon",
                            "uses": ["sleep draughts"],
                        },
                    },
                    {
                        "type": "ReagentDiscoveredEvent",
                        "payload": {
                            "name": "Mooncap Fungus",
                            "description": "Soft blue fungus that releases a drowsy scent.",
                            "location": "Damp cave mouths and shaded roots",
                            "uses": ["sleep draughts"],
                        },
                    },
                    {
                        "type": "RecipeDiscoveredEvent",
                        "payload": {
                            "name": "Quiet Sleep Draught",
                            "ingredients": [
                                {
                                    "reagent_name": "Moonwater",
                                    "quantity": 1,
                                    "measure_amount": 100,
                                    "measure_unit": "mL",
                                },
                                {
                                    "reagent_name": "Mooncap Fungus",
                                    "quantity": 1,
                                    "measure_amount": 1,
                                    "measure_unit": "each",
                                },
                            ],
                            "result": "Invites sleep.",
                            "notes": "Best brewed at low heat.",
                        },
                    },
                ]
            )

            reagents = repository.list_crafting_items()
            recipes = repository.list_crafting_recipes()

            moonwater = next(reagent for reagent in reagents if reagent["name"] == "Moonwater")

            self.assertEqual(moonwater["description"], "Water prepared under moonlight.")
            self.assertEqual(moonwater["location"], "Open bowls left beneath a full moon")
            catalog = repository.list_item_catalog()
            catalog_moonwater = next(item for item in catalog if item["name"] == "Moonwater")
            self.assertEqual(catalog_moonwater["category"], "Material")
            self.assertEqual(recipes[0]["name"], "Quiet Sleep Draught")
            self.assertEqual(recipes[0]["ingredients"][0]["reagent_name"], "Moonwater")
            self.assertEqual(recipes[0]["ingredients"][0]["measure_unit"], "mL")

    def test_recipe_discovery_requires_allowed_item_catalog_category(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Event Test")
            repository.add_inventory_item(
                name="Glass Stirring Rod",
                category="Tool",
                quantity=1,
                description="A glass rod used to stir mixtures.",
                value_base_units=3,
            )

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "RecipeDiscoveredEvent",
                        "payload": {
                            "name": "Rod Powder",
                            "ingredients": [
                                {
                                    "reagent_name": "Glass Stirring Rod",
                                    "quantity": 1,
                                    "measure_amount": 1,
                                    "measure_unit": "each",
                                }
                            ],
                            "result": "A questionable powder.",
                            "notes": "This should be rejected.",
                        },
                    }
                ]
            )

            self.assertEqual(results[0].status, "skipped")
            self.assertEqual(repository.list_crafting_recipes(), [])

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

    def test_upserts_private_gm_secrets_and_hides_inactive_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Secret Test")
            applier = EventApplier(repository)

            created = applier.apply_event(
                {
                    "type": "SecretUpsertedEvent",
                    "payload": {
                        "secret_id": "station_master_is_villain",
                        "title": "Station Master's Identity",
                        "details": "The station master directs the canal murders.",
                        "reveal_condition": "The player deciphers the black ledger.",
                        "related_npc_ids": ["station_master"],
                        "related_locations": ["Rainmarket Station"],
                        "status": "active",
                    },
                }
            )
            active_secrets = repository.list_gm_secrets(active_only=True)

            self.assertEqual(created.status, "applied")
            self.assertEqual(len(active_secrets), 1)
            self.assertEqual(
                active_secrets[0]["details"],
                "The station master directs the canal murders.",
            )
            self.assertEqual(active_secrets[0]["related_npc_ids"], ["station_master"])

            revealed = applier.apply_event(
                {
                    "type": "SecretUpsertedEvent",
                    "payload": {
                        "secret_id": "station_master_is_villain",
                        "title": "Station Master's Identity",
                        "details": "The station master directs the canal murders.",
                        "reveal_condition": "The player deciphers the black ledger.",
                        "related_npc_ids": ["station_master"],
                        "related_locations": ["Rainmarket Station"],
                        "status": "revealed",
                    },
                }
            )

            self.assertEqual(revealed.status, "applied")
            self.assertEqual(repository.list_gm_secrets(active_only=True), [])
            self.assertEqual(repository.list_gm_secrets()[0]["status"], "revealed")

    def test_applies_active_task_events_including_quests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Task Test")
            repository.set_calendar_settings({"time_display": "12_hour"})

            results = EventApplier(repository).apply_events(
                [
                    {
                        "type": "ActiveTaskUpsertedEvent",
                        "payload": {
                            "name": "Find the Missing Ledger",
                            "category": "Quest",
                            "status": "Active",
                            "requester": "Mira Coppercup",
                            "description": "Recover the missing tavern ledger.",
                            "location": "Tavern",
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

            ledger_task = _require(
                repository.get_active_task("Find the Missing Ledger")
            )
            self.assertIsNotNone(ledger_task)
            self.assertEqual(ledger_task["category"], "Quest")
            self.assertEqual(ledger_task["requester"], "Mira Coppercup")
            self.assertEqual(ledger_task["location"], "Tavern")
            self.assertEqual(ledger_task["reward"], "Free room and board.")
            self.assertEqual(ledger_task["due_date"], "N/A")
            self.assertEqual(ledger_task["due_elapsed_minutes"], -1)
            self.assertEqual(
                repository.get_state_value("quest.Find the Missing Ledger.status"),
                "active",
            )

            commission_task = _require(
                repository.get_active_task("Silver Ring Commission")
            )
            self.assertIsNotNone(commission_task)
            self.assertEqual(
                commission_task["due_date"],
                "Wednesday, Month 1 3, Year 1, 5:00 P.M.",
            )
            self.assertEqual(commission_task["due_elapsed_minutes"], 3900)

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

            completed_task = _require(
                repository.get_active_task("Silver Ring Commission")
            )
            self.assertIsNotNone(completed_task)
            self.assertEqual(completed_task["status"], "Completed")
            self.assertEqual(completed_task["notes"], "The ring was collected.")

            quest_complete_result = EventApplier(repository).apply_event(
                {
                    "type": "ActiveTaskCompletedEvent",
                    "payload": {"name": "Find the Missing Ledger"},
                }
            )
            self.assertEqual(quest_complete_result.status, "applied")
            self.assertEqual(
                repository.get_state_value("quest.Find the Missing Ledger.status"),
                "completed",
            )

    def test_active_task_vague_due_date_is_stored_as_exact_elapsed_minute(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Task Due Date")
            repository.set_calendar_settings({"time_display": "12_hour"})
            repository.set_current_calendar_minute(8 * 60)

            result = EventApplier(repository).apply_event(
                {
                    "type": "ActiveTaskUpsertedEvent",
                    "payload": {
                        "name": "Deliver the Samples",
                        "category": "Delivery",
                        "status": "Active",
                        "description": "Bring the samples to the river office.",
                        "requester": "Mira Coppercup",
                        "location": "River Office",
                        "reward": "12 Bits",
                        "due_date": "the end of the week",
                    },
                }
            )

            task = _require(repository.get_active_task("Deliver the Samples"))

            self.assertEqual(result.status, "applied")
            self.assertIsNotNone(task)
            self.assertEqual(task["due_elapsed_minutes"], (6 * 24 * 60) + (23 * 60) + 59)
            self.assertEqual(
                task["due_date"],
                "Sunday, Month 1 7, Year 1, 11:59 P.M.",
            )

            repository.set_calendar_settings(
                {
                    "days_per_week": 10,
                    "day_names": [f"Day {index}" for index in range(1, 11)],
                    "time_display": "24_hour",
                }
            )
            refreshed_task = _require(
                repository.get_active_task("Deliver the Samples")
            )

            self.assertEqual(
                refreshed_task["due_elapsed_minutes"],
                task["due_elapsed_minutes"],
            )
            self.assertIn("23:59", refreshed_task["due_date"])

    def test_active_task_blank_visible_fields_get_meaningful_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Task Defaults")
            repository.set_state_value("location", "Old Workshop")

            result = EventApplier(repository).apply_event(
                {
                    "type": "ActiveTaskUpsertedEvent",
                    "payload": {
                        "name": "Craft spare lockpicks",
                        "category": "Personal Goal",
                        "description": "Create more lockpicks for future work.",
                    },
                }
            )

            task = _require(repository.get_active_task("Craft spare lockpicks"))

            self.assertEqual(result.status, "applied")
            self.assertIsNotNone(task)
            self.assertEqual(task["category"], "Personal Goal")
            self.assertEqual(task["status"], "Active")
            self.assertEqual(
                task["description"],
                "Create more lockpicks for future work.",
            )
            self.assertEqual(task["requester"], "Self")
            self.assertEqual(task["location"], "Player's Workshop")
            self.assertEqual(task["reward"], "N/A")
            self.assertEqual(task["due_date"], "N/A")

    def test_active_task_defaults_do_not_overwrite_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Task Defaults")
            repository.upsert_active_task(
                name="Collect the Samples",
                category="Research",
                status="Active",
                description="Collect river mud samples.",
                requester="Self",
                location="Riverbank",
                reward="N/A",
                due_date="Before dawn",
            )

            result = EventApplier(repository).apply_event(
                {
                    "type": "ActiveTaskUpsertedEvent",
                    "payload": {
                        "name": "Collect the Samples",
                        "description": "Collect river mud samples and label each jar.",
                    },
                }
            )

            task = _require(repository.get_active_task("Collect the Samples"))

            self.assertEqual(result.status, "applied")
            self.assertIsNotNone(task)
            self.assertEqual(task["category"], "Research")
            self.assertEqual(task["status"], "Active")
            self.assertEqual(
                task["description"],
                "Collect river mud samples and label each jar.",
            )
            self.assertEqual(task["requester"], "Self")
            self.assertEqual(task["location"], "Riverbank")
            self.assertEqual(task["reward"], "N/A")
            self.assertEqual(task["due_date"], "Before dawn")

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
                        "disposition": "Hostile",
                    },
                }
            )

            npcs = repository.list_npcs()
            visible_npcs = repository.list_player_visible_npcs()

            self.assertEqual(result.status, "applied")
            self.assertEqual(npcs[0]["role"], "Shady Character")
            self.assertEqual(
                npcs[0]["known_facts"],
                ["A wary figure lingered near the alley mouth."],
            )
            self.assertEqual(npcs[0]["disposition"], "")
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
            self.assertEqual(visible_npcs[0]["location"], "Tavern")
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

    def test_npc_upsert_merges_changed_internal_name_for_same_person(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "NPC Test")
            applier = EventApplier(repository)

            first_result = applier.apply_event(
                {
                    "type": "NpcUpsertedEvent",
                    "payload": {
                        "internal_name": "copper_kettle_bartender",
                        "display_name": "Bartender",
                        "role": "Innkeeper",
                        "location": "Copper Kettle",
                        "player_facing_information": (
                            "The person managing the Copper Kettle."
                        ),
                    },
                }
            )
            with self.assertLogs("ai_adventure.events.event_applier", level="WARNING") as logs:
                second_result = applier.apply_event(
                    {
                        "type": "NpcUpsertedEvent",
                        "payload": {
                            "internal_name": "copper_kettle_bartender_innkeeper_copper_kettle",
                            "display_name": "Elara",
                            "role": "Innkeeper",
                            "location": "Copper Kettle",
                            "player_facing_information": (
                                "The manager of the Copper Kettle who hears local rumors."
                            ),
                        },
                    }
                )

            npcs = repository.list_npcs()
            visible_npcs = repository.list_player_visible_npcs()

            self.assertEqual(first_result.status, "applied")
            self.assertEqual(second_result.status, "applied")
            self.assertIn("Removed banned generated NPC display_name", "\n".join(logs.output))
            self.assertEqual(len(npcs), 1)
            self.assertEqual(npcs[0]["npc_id"], "copper_kettle_bartender")
            self.assertEqual(visible_npcs[0]["display_name"], "Bartender")
            self.assertNotEqual(visible_npcs[0]["display_name"], "Elara")
            self.assertIn("local rumors", visible_npcs[0]["notes"])

    def test_npc_lists_coalesce_legacy_duplicate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "NPC Test")

            with repository._connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO npcs (
                        npc_id,
                        name,
                        display_name,
                        role,
                        location,
                        public_description,
                        player_facing_information,
                        knowledge_scope_json,
                        known_facts_json,
                        disposition,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "copper_kettle_bartender",
                            "copper_kettle_bartender",
                            "Bartender",
                            "Innkeeper",
                            "Copper Kettle",
                            "A practical bartender.",
                            "The person managing the Copper Kettle.",
                            '["Local gossip"]',
                            "[]",
                            "",
                            "2026-05-30T18:26:05",
                            "2026-05-30T18:26:05",
                        ),
                        (
                            "copper_kettle_bartender_innkeeper_copper_kettle",
                            "copper_kettle_bartender_innkeeper_copper_kettle",
                            "Elara",
                            "Innkeeper",
                            "Copper Kettle",
                            "A practical bartender with a welcoming presence.",
                            "The manager of the Copper Kettle who hears local rumors.",
                            '["Merchant trade"]',
                            "[]",
                            "",
                            "2026-05-30T18:28:23",
                            "2026-05-30T18:28:23",
                        ),
                    ],
                )

            visible_npcs = repository.list_player_visible_npcs()
            relevant_npcs = repository.list_relevant_npcs(
                location="Copper Kettle",
                query_text="talk to the bartender",
            )

            self.assertEqual(len(visible_npcs), 1)
            self.assertEqual(visible_npcs[0]["display_name"], "Bartender")
            self.assertIn("local rumors", visible_npcs[0]["notes"])
            self.assertEqual(len(relevant_npcs), 1)
            self.assertEqual(relevant_npcs[0]["npc_id"], "copper_kettle_bartender")
            self.assertIn("Local gossip", relevant_npcs[0]["knowledge_scope"])
            self.assertIn("Merchant trade", relevant_npcs[0]["knowledge_scope"])

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
