from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_adventure.ai.gemini_service import (
    EVENT_RESPONSE_SCHEMA,
    KNOWN_EVENT_TYPE_NAMES,
    AiNarrationResult,
    _drop_unauthorized_player_spell_cast_events,
)
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.magic import magic_resource_specs, normalize_magic_setup
from ai_adventure.new_game_setup import build_new_game_setup_packet, normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository


class MagicSystemTests(unittest.TestCase):
    def test_no_magic_world_overrides_player_casting_and_model_contract(self) -> None:
        magic = normalize_magic_setup(
            {
                "world_contains_magic": False,
                "player_magic_enabled": True,
                "enabled": True,
                "casting_mode": "mana",
                "mana_maximum": 18,
            }
        )
        packet = build_new_game_setup_packet(
            normalize_new_game_setup({"magic": magic})
        )

        self.assertFalse(magic["world_contains_magic"])
        self.assertTrue(magic["player_magic_enabled"])
        self.assertFalse(magic["enabled"])
        self.assertEqual(magic_resource_specs(magic), [])
        self.assertIn(
            "the setting contains no magic",
            packet["requirements"]["magic"],
        )

    def test_new_game_setup_persists_starting_magic_and_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Starting Magic",
                setup={
                    "magic": {
                        "enabled": True,
                        "casting_mode": "mana",
                        "tradition": "Stormcalling",
                        "mana_maximum": 15,
                        "starting_spells_mode": "advanced",
                        "starting_spells": [
                            {
                                "name": "Static Touch",
                                "tier": 0,
                                "school": "Storm",
                                "description": "Releases a harmless warning spark.",
                                "mana_cost": 1,
                            }
                        ],
                    }
                },
            )

            self.assertEqual(repository.get_magic_configuration()["tradition"], "Stormcalling")
            self.assertEqual(repository.list_magic_resource_pools()[0]["current_amount"], 15)
            self.assertEqual(repository.list_character_spells()[0]["name"], "Static Touch")

    def test_basic_starting_spell_requests_wait_for_gemini_finalization(self) -> None:
        setup = normalize_new_game_setup(
            {
                "magic": {
                    "enabled": True,
                    "casting_mode": "mana",
                    "starting_spells_mode": "basic",
                    "starting_spell_requests": [
                        "A ward that briefly turns aside an attack"
                    ],
                }
            }
        )
        packet = build_new_game_setup_packet(setup)

        self.assertEqual(setup["magic"]["starting_spells"], [])
        self.assertEqual(
            setup["magic"]["starting_spell_requests"][0]["spell_request"],
            "A ward that briefly turns aside an attack",
        )
        self.assertIn(
            "starting spells from player descriptions",
            packet["fields_requiring_ai_invention"],
        )
        self.assertEqual(packet["magic_contract"]["starting_spell_request_count"], 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(
                Path(temp_dir),
                "Requested Magic",
                setup=setup,
            )
            self.assertEqual(repository.list_character_spells(), [])

            learned = repository.learn_starting_spells(
                [
                    {
                        "name": "Momentary Aegis",
                        "tier": 1,
                        "school": "Warding",
                        "description": "Deflects one incoming blow.",
                        "mana_cost": 2,
                        "prepared": True,
                    }
                ],
                source="Gemini New Game",
            )

            self.assertEqual(len(learned), 1)
            self.assertEqual(repository.list_character_spells()[0]["name"], "Momentary Aegis")

    def test_normalizes_supported_casting_modes_and_resource_specs(self) -> None:
        mana = normalize_magic_setup(
            {"enabled": True, "casting_mode": "mana", "mana_maximum": 18}
        )
        tiered = normalize_magic_setup(
            {"enabled": True, "casting_mode": "tiered", "tier_slots": {"1": 3, "3": 1}}
        )
        narrative = normalize_magic_setup({"enabled": True, "casting_mode": "narrative"})

        self.assertEqual(magic_resource_specs(mana)[0]["maximum_amount"], 18)
        self.assertEqual(
            [(pool["tier"], pool["maximum_amount"]) for pool in magic_resource_specs(tiered)],
            [(1, 3), (3, 1)],
        )
        self.assertEqual(magic_resource_specs(narrative), [])

    def test_repository_creates_all_magic_tables_and_tracks_mana_casts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Magic Test")
            repository.set_magic_configuration(
                {"enabled": True, "casting_mode": "mana", "mana_maximum": 10}
            )
            spell = repository.upsert_spell_catalog(
                name="Ember Lance",
                tier=2,
                school="Flame",
                description="A focused bolt of fire.",
                mana_cost=4,
            )
            assert spell is not None
            repository.learn_character_spell(spell["spell_id"], source="Test")

            result = repository.cast_character_spell(spell["spell_id"])

            self.assertEqual(result["status"], "cast")
            self.assertEqual(repository.list_magic_resource_pools()[0]["current_amount"], 6)
            self.assertEqual(repository.list_spell_cast_history()[0]["amount_spent"], 4)
            connection = sqlite3.connect(repository.db_path)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            finally:
                connection.close()
            self.assertTrue(
                {
                    "spell_catalog",
                    "character_spells",
                    "magic_resource_pools",
                    "spell_cast_history",
                    "active_magic_effects",
                }.issubset(tables)
            )

    def test_legacy_spell_flags_migrate_to_narrative_magic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Legacy Magic")
            repository.set_state_value("spell.Old Flame.known", "true")

            migrated = SaveRepository(repository.db_path)

            self.assertEqual(migrated.get_state_value("spell.Old Flame.known", ""), "")
            self.assertEqual(migrated.get_magic_configuration()["casting_mode"], "narrative")
            self.assertTrue(migrated.get_magic_configuration()["enabled"])
            self.assertEqual(migrated.list_character_spells()[0]["name"], "Old Flame")

    def test_narrative_and_tiered_casting_use_their_own_resource_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Casting Modes")
            spell = repository.upsert_spell_catalog(
                name="Silver Ward", tier=1, description="Raises a brief ward.", mana_cost=3
            )
            cantrip = repository.upsert_spell_catalog(
                name="Mage Light", tier=0, description="Creates a small light.", mana_cost=1
            )
            assert spell is not None and cantrip is not None
            repository.learn_character_spell(spell["spell_id"])
            repository.learn_character_spell(cantrip["spell_id"])

            repository.set_magic_configuration(
                {"enabled": True, "casting_mode": "narrative"}
            )
            self.assertEqual(repository.cast_character_spell(spell["spell_id"])["status"], "cast")
            self.assertEqual(repository.list_magic_resource_pools(), [])

            repository.set_magic_configuration(
                {"enabled": True, "casting_mode": "tiered", "tier_slots": {1: 1}}
            )
            self.assertEqual(repository.cast_character_spell(spell["spell_id"])["status"], "cast")
            self.assertEqual(repository.cast_character_spell(spell["spell_id"])["status"], "rejected")
            self.assertEqual(repository.cast_character_spell(cantrip["spell_id"])["status"], "cast")

    def test_new_events_store_complete_spells_validate_casts_and_track_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Magic Events")
            repository.set_magic_configuration(
                {"enabled": True, "casting_mode": "mana", "mana_maximum": 8}
            )
            applier = EventApplier(repository, message_id="message-1")
            learned = applier.apply_event(
                {
                    "type": "CharacterSpellLearnedEvent",
                    "payload": {
                        "name": "Quiet Step",
                        "tier": 1,
                        "school": "Veil",
                        "description": "Muffles the caster's movement.",
                        "mana_cost": 2,
                        "prepared": True,
                    },
                }
            )
            spell_id = learned.payload["spell_id"]
            rejected = applier.apply_event(
                {
                    "type": "PlayerSpellCastEvent",
                    "payload": {
                        "spell_id": spell_id,
                        "cast_tier": 1,
                        "player_authorized": False,
                    },
                }
            )
            cast = applier.apply_event(
                {
                    "type": "PlayerSpellCastEvent",
                    "payload": {
                        "spell_id": spell_id,
                        "cast_tier": 1,
                        "player_authorized": True,
                    },
                }
            )
            effect = applier.apply_event(
                {
                    "type": "MagicEffectUpsertedEvent",
                    "payload": {
                        "spell_id": spell_id,
                        "name": "Quiet Step",
                        "description": "Footsteps remain muted.",
                        "active": True,
                    },
                }
            )

            self.assertEqual(learned.status, "applied")
            self.assertEqual(repository.list_character_spells()[0]["school"], "Veil")
            self.assertEqual(rejected.status, "skipped")
            self.assertEqual(cast.status, "applied")
            self.assertEqual(effect.status, "applied")
            self.assertEqual(repository.list_active_magic_effects()[0]["name"], "Quiet Step")

    def test_state_context_and_contract_expose_new_magic_model_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Magic Context")
            repository.set_magic_configuration(
                {"enabled": True, "casting_mode": "mana", "mana_maximum": 12}
            )
            spell = repository.upsert_spell_catalog(
                name="Stone Skin", tier=2, school="Earth", description="Hardens skin.", mana_cost=5
            )
            assert spell is not None
            repository.learn_character_spell(spell["spell_id"])
            state = StateManager(repository).load_state()
            packet = AiContextBuilder.from_default_library().build_story_context(
                state, player_command="I cast Stone Skin."
            )

            self.assertEqual(state.magic.known_spells[0].spell_id, spell["spell_id"])
            self.assertEqual(packet["state"]["magic"]["configuration"]["casting_mode"], "mana")
            self.assertTrue(
                packet["state"]["magic"]["configuration"]["world_contains_magic"]
            )
            self.assertIn(
                "world_contains_magic is false",
                packet["state"]["magic"]["rules"]["authority"],
            )
            self.assertIn(
                "enabled is false",
                packet["state"]["magic"]["rules"]["authority"],
            )
            self.assertEqual(packet["state"]["magic"]["resource_pools"][0]["current_amount"], 12)
            self.assertNotIn("SpellLearnedEvent", KNOWN_EVENT_TYPE_NAMES)
            self.assertIn("CharacterSpellLearnedEvent", KNOWN_EVENT_TYPE_NAMES)
            branches = EVENT_RESPONSE_SCHEMA["anyOf"]
            event_names = {
                branch["properties"]["type"]["enum"][0]
                for branch in branches
            }
            self.assertIn("PlayerSpellCastEvent", event_names)
            rules_path = Path("ai_adventure/data/context/default_rules.json")
            rules_text = json.dumps(json.loads(rules_path.read_text(encoding="utf-8")))
            self.assertNotIn('"event_type": "SpellLearnedEvent"', rules_text)
            self.assertIn("MagicEffectUpsertedEvent", rules_text)
            self.assertIn("world_contains_magic is false", rules_text)
            self.assertIn("enabled is false", rules_text)
            context_path = Path("ai_adventure/data/context/default_context.json")
            context_text = json.dumps(json.loads(context_path.read_text(encoding="utf-8")))
            self.assertIn("world_contains_magic is false", context_text)
            self.assertIn("enabled is false", context_text)

    def test_story_guard_drops_cast_without_current_player_authorization(self) -> None:
        cast_event = {
            "type": "PlayerSpellCastEvent",
            "payload": {
                "spell_id": "spell_1",
                "cast_tier": 1,
                "player_authorized": True,
            },
        }
        result = AiNarrationResult(narrative_text="Test", suggested_events=[cast_event])
        state = {"magic": {"known_spells": [{"spell_id": "spell_1", "name": "Flame Arc"}]}}

        dropped = _drop_unauthorized_player_spell_cast_events(
            result, {"player_command": "I wait.", "state": state}
        )
        kept = _drop_unauthorized_player_spell_cast_events(
            result, {"player_command": "I cast Flame Arc.", "state": state}
        )

        self.assertEqual(dropped.suggested_events, [])
        self.assertEqual(kept.suggested_events, [cast_event])


if __name__ == "__main__":
    unittest.main()
