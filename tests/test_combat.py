from __future__ import annotations

import unittest

from ai_adventure.combat import (
    BODY_PARTS,
    DEFAULT_TWO_HANDED_DAMAGE,
    DEFAULT_WEAPON_DAMAGE,
    attack_hit_probability,
    calculate_team_threat_levels,
    normalize_equipment,
    normalize_combat_state,
    normalize_item_metadata,
    roll_combat_initiative,
)


class _SequenceRng:
    def __init__(self, values: list[int]) -> None:
        self.values = iter(values)

    def randint(self, _minimum: int, _maximum: int) -> int:
        return next(self.values)


class CombatRuleTests(unittest.TestCase):
    def test_weapon_metadata_raises_damage_that_is_not_better_than_unarmed(self) -> None:
        weak_one_handed = normalize_item_metadata(
            {
                "item_type": "Weapon",
                "weapon_hands": "one-handed",
                "damage": "1d4",
            },
            name="Rusty Dagger",
            category="Weapon",
        )
        weak_two_handed = normalize_item_metadata(
            {
                "item_type": "Weapon",
                "weapon_hands": "two-handed",
                "damage": "1d4",
            },
            name="Cracked Greatclub",
            category="Weapon",
        )
        stronger_weapon = normalize_item_metadata(
            {
                "item_type": "Weapon",
                "weapon_hands": "one-handed",
                "damage": "1d6+1",
            },
            name="Fine Saber",
            category="Weapon",
        )

        self.assertEqual(weak_one_handed["damage"], DEFAULT_WEAPON_DAMAGE)
        self.assertEqual(weak_two_handed["damage"], DEFAULT_TWO_HANDED_DAMAGE)
        self.assertEqual(stronger_weapon["damage"], "1d6+1")

    def test_container_metadata_preserves_exact_contents_and_security(self) -> None:
        metadata = normalize_item_metadata(
            {
                "item_type": "Container",
                "container": {
                    "is_open": False,
                    "contents_taken": False,
                    "is_locked": True,
                    "lockpick_skill": "Lockpicking",
                    "lockpick_dc": 16,
                    "lockpick_failure_consequence": "The pick snaps in the lock.",
                    "is_trapped": True,
                    "trap_notice_skill": "Perception",
                    "trap_notice_dc": 13,
                    "trap_disarm_skill": "Sleight of Hand",
                    "trap_disarm_dc": 15,
                    "trap_failure_consequence": "A poisoned needle strikes the opener.",
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
            },
            name="Stolen Coin Pouch",
            category="Container",
        )
        container = metadata["container"]

        self.assertEqual(metadata["item_type"], "Container")
        self.assertFalse(container["is_open"])
        self.assertFalse(container["contents_taken"])
        self.assertTrue(container["is_locked"])
        self.assertEqual(container["lockpick_dc"], 16)
        self.assertTrue(container["is_trapped"])
        self.assertEqual(container["trap_notice_dc"], 13)
        self.assertEqual(container["trap_disarm_dc"], 15)
        self.assertEqual(container["contents"]["currency_base_units"], 35)
        self.assertEqual(
            container["contents"]["items"][0]["name"],
            "Tarnished Silver Locket",
        )

    def test_team_threat_totals_exactly_one_hundred_and_rewards_tank_stats(self) -> None:
        combatants = [
            {
                "id": "tank",
                "team": "party",
                "current_health": 30,
                "max_health": 30,
                "armor_rating": 18,
                "damage": "2d8",
                "defeated": False,
            },
            {
                "id": "scout",
                "team": "party",
                "current_health": 14,
                "max_health": 14,
                "armor_rating": 12,
                "damage": "1d8",
                "defeated": False,
            },
            {
                "id": "mage",
                "team": "party",
                "current_health": 8,
                "max_health": 8,
                "armor_rating": 9,
                "damage": "1d4",
                "defeated": False,
            },
            {
                "id": "enemy",
                "team": "enemy",
                "current_health": 10,
                "max_health": 10,
                "armor_rating": 10,
                "damage": "1d6",
                "defeated": False,
            },
        ]

        party_threat = calculate_team_threat_levels(combatants, "party")
        enemy_threat = calculate_team_threat_levels(combatants, "enemy")

        self.assertEqual(sum(party_threat.values()), 100)
        self.assertGreater(party_threat["tank"], party_threat["scout"])
        self.assertGreater(party_threat["scout"], party_threat["mage"])
        self.assertEqual(enemy_threat, {"enemy": 100})

    def test_combat_normalization_discards_legacy_spatial_state(self) -> None:
        state = normalize_combat_state(
            {
                "active": True,
                "movement_undo": {"actor_id": "player"},
                "combatants": [
                    {
                        "id": "player",
                        "team": "party",
                        "current_health": 20,
                        "max_health": 20,
                        "movement_speed_feet": 30,
                        "movement_remaining_feet": 10,
                        "distance_feet": 25,
                        "attack_range_feet": 5,
                    },
                    {
                        "id": "enemy",
                        "team": "enemy",
                        "current_health": 8,
                        "max_health": 8,
                    },
                ],
            }
        )

        self.assertNotIn("movement_undo", state)
        for combatant in state["combatants"]:
            self.assertNotIn("movement_speed_feet", combatant)
            self.assertNotIn("movement_remaining_feet", combatant)
            self.assertNotIn("distance_feet", combatant)
            self.assertNotIn("attack_range_feet", combatant)
            self.assertEqual(combatant["threat_level"], 100)

    def test_equipment_respects_owned_quantity_for_optional_hand_slots(self) -> None:
        dagger = {
            "name": "Iron Dagger",
            "quantity": 1,
            "category": "Weapon",
            "metadata": {
                "item_type": "Weapon",
                "weapon_hands": "one-handed",
            },
        }

        main_hand = normalize_equipment(
            {
                "Main Hand": "Iron Dagger",
                "Off Hand": "Iron Dagger",
            },
            [dagger],
        )
        off_hand = normalize_equipment(
            {"Off Hand": "Iron Dagger"},
            [dagger],
        )

        self.assertEqual(main_hand["Main Hand"], "Iron Dagger")
        self.assertEqual(main_hand["Off Hand"], "")
        self.assertEqual(off_hand["Main Hand"], "")
        self.assertEqual(off_hand["Off Hand"], "Iron Dagger")

        dagger["quantity"] = 2
        dual_wielded = normalize_equipment(
            {
                "Main Hand": "Iron Dagger",
                "Off Hand": "Iron Dagger",
            },
            [dagger],
        )

        self.assertEqual(dual_wielded["Main Hand"], "Iron Dagger")
        self.assertEqual(dual_wielded["Off Hand"], "Iron Dagger")

    def test_equipment_expands_armor_into_every_forced_slot(self) -> None:
        full_armor = {
            "name": "Iron Armor",
            "quantity": 1,
            "category": "Armor",
            "metadata": {
                "item_type": "Armor",
                "covers_body_parts": list(BODY_PARTS),
            },
        }

        equipment = normalize_equipment(
            {"Legs": "Iron Armor"},
            [full_armor],
        )

        for slot in BODY_PARTS:
            self.assertEqual(equipment[slot], "Iron Armor")

    def test_duplicate_combatant_names_receive_unique_display_names(self) -> None:
        state = normalize_combat_state(
            {
                "active": True,
                "combatants": [
                    {"id": "enemy-1", "name": "Bandit"},
                    {"id": "enemy-2", "name": "Bandit"},
                    {"id": "enemy-3", "name": "Cleric"},
                ],
            }
        )

        self.assertEqual(
            [
                combatant["display_name"]
                for combatant in state["combatants"]
            ],
            ["Bandit (1)", "Bandit (2)", "Cleric"],
        )

    def test_initiative_rolls_and_sorts_highest_total_first(self) -> None:
        combatants = [
            {"id": "player", "name": "Player", "initiative_bonus": 1},
            {"id": "bandit", "name": "Bandit", "initiative_bonus": 4},
            {"id": "cleric", "name": "Cleric", "initiative_bonus": 0},
        ]

        ordered = roll_combat_initiative(
            combatants,
            rng=_SequenceRng([12, 10, 19]),
        )

        self.assertEqual(
            [combatant["id"] for combatant in ordered],
            ["cleric", "bandit", "player"],
        )
        self.assertEqual(
            [combatant["initiative_total"] for combatant in ordered],
            [19, 14, 13],
        )

    def test_firearm_and_ammunition_metadata_are_normalized(self) -> None:
        firearm = normalize_item_metadata(
            {
                "item_type": "Weapon",
                "Ammunition_Type_Required": "9mm Round",
                "Clip Size": 12,
                "Amount of Bullets Fired Per Attack": 3,
                "attack_range_feet": 90,
            },
            name="Burst Pistol",
            category="Weapon",
        )
        ammunition = normalize_item_metadata(
            {
                "item_type": "Ammunition",
                "ammunition_type": "9mm Round",
            },
            name="9mm Box",
            category="Ammunition",
        )

        self.assertEqual(firearm["ammunition_type_required"], "9mm Round")
        self.assertEqual(firearm["clip_size"], 12)
        self.assertEqual(firearm["bullets_per_attack"], 3)
        self.assertEqual(firearm["attack_range_feet"], 90)
        self.assertEqual(ammunition["item_type"], "Ammunition")
        self.assertEqual(ammunition["ammunition_type"], "9mm Round")

    def test_hit_probability_respects_natural_one_and_twenty(self) -> None:
        self.assertEqual(attack_hit_probability(100, 10), 0.95)
        self.assertEqual(attack_hit_probability(-100, 10), 0.05)
