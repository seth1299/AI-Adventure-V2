from __future__ import annotations

import json
import random
import unittest

from ai_adventure.context.context_builder import AiContextBuilder, infer_context_tags
from ai_adventure.context.creative_ideas import CreativeIdeasLibrary
from ai_adventure.context.reference_loader import ContextReferenceLoader
from ai_adventure.core.models import (
    ActiveTask,
    ActiveTasksState,
    AdventureMetadata,
    AdventureState,
    HistoryEntry,
    HistoryState,
    ItemCatalogEntry,
    ItemCatalogState,
    InventoryItem,
    InventoryState,
    PlayerState,
    WorldState,
)


class ContextBuilderTests(unittest.TestCase):
    def test_default_library_loads(self) -> None:
        library = ContextReferenceLoader().load_default_library()

        self.assertEqual(library.schema_version, 1)
        self.assertGreaterEqual(len(library.sections), 1)
        self.assertIn("always", library.sections[0].tags)

    def test_infer_context_tags_from_command(self) -> None:
        tags = infer_context_tags("Use the lantern to search for a potion recipe")

        self.assertIn("story", tags)
        self.assertIn("inventory", tags)
        self.assertIn("exploration", tags)
        self.assertIn("alchemy", tags)

    def test_build_story_context_selects_relevant_sections(self) -> None:
        library = ContextReferenceLoader().load_default_library()
        builder = AiContextBuilder(
            library,
            creative_ideas=CreativeIdeasLibrary.load_default(),
            max_history_entries=2,
            max_reference_sections=14,
        )
        state = AdventureState(
            metadata=AdventureMetadata(title="Context Test"),
            player=PlayerState(
                name="Mira",
                appearance="A road-worn apothecary in a green cloak.",
                backstory="Raised by caravan healers.",
                condition="Curious",
                notes="Distrusts locked doors.",
            ),
            world=WorldState(location="Old Road", time="Dusk", weather="Rain"),
            inventory=InventoryState(
                items=[
                    InventoryItem(
                        name="Lantern",
                        category="tool",
                        quantity=1,
                        description="A brass lantern.",
                    )
                ]
            ),
            item_catalog=ItemCatalogState(
                items=[
                    ItemCatalogEntry(
                        name="Lantern",
                        category="tool",
                        description="A brass lantern.",
                        value_base_units=12,
                    )
                ]
            ),
            history=HistoryState(
                entries=[
                    HistoryEntry(kind="system", content="Adventure started."),
                    HistoryEntry(kind="player", content="Look around."),
                    HistoryEntry(kind="story", content="The road glistens."),
                ]
            ),
            active_tasks=ActiveTasksState(
                tasks=[
                    ActiveTask(
                        name="Find the Missing Ledger",
                        category="Quest",
                        status="Active",
                        description="Recover the missing tavern ledger.",
                        requester="Mira Coppercup",
                        notes="Legacy task notes should not be advertised as a field.",
                    )
                ]
            ),
        )
        state.settings.values["ai.additional_context"] = (
            "Please respond only in the third person."
        )
        state.settings.values["world.summary"] = "Rainmarket is a canal city."
        state.settings.values["world.genre"] = "Realistic detective mystery"
        state.settings.values["world.game_style"] = "Realistic detective mystery"
        state.settings.values["world.setup_context"] = "Canal guilds control the docks."
        state.settings.values["currency.description"] = "Crowns and half-crowns."

        packet = builder.build_story_context(
            state,
            player_command="Use the lantern to search for reagents",
            relevant_npcs=[
                {
                    "npc_id": "mira_coppercup_bartender_tavern",
                    "name": "Mira Coppercup",
                    "role": "Bartender",
                    "location": "Tavern",
                    "knowledge_scope": ["Common tavern gossip"],
                    "known_facts": ["The player asked about the north road."],
                    "disposition": "Friendly",
                }
            ],
            valid_music_tracks=["Town Village City.mp3", "Boss_Fight.mp3"],
            current_music="Town Village City.mp3",
            resolved_skill_checks=[
                {
                    "skill_name": "Foraging",
                    "roll": 20,
                    "total": 24,
                    "dc": 14,
                    "outcome": "success",
                }
            ],
        )

        section_ids = {
            section["id"] for section in packet["reference_sections"]
        }

        self.assertEqual(packet["state"]["adventure_title"], "Context Test")
        self.assertEqual(packet["state"]["player"]["appearance"], "A road-worn apothecary in a green cloak.")
        self.assertEqual(packet["state"]["player"]["backstory"], "Raised by caravan healers.")
        self.assertEqual(packet["state"]["player"]["notes"], "Distrusts locked doors.")
        self.assertEqual(
            packet["state"]["player_ai_preferences"]["additional_context"],
            "Please respond only in the third person.",
        )
        self.assertEqual(packet["state"]["scene"]["location"], "Old Road")
        self.assertEqual(packet["state"]["world_profile"]["summary"], "Rainmarket is a canal city.")
        self.assertEqual(packet["state"]["world_profile"]["genre"], "Realistic detective mystery")
        self.assertEqual(packet["state"]["currency"]["world_description"], "Crowns and half-crowns.")
        self.assertEqual(packet["state"]["currency"]["balance"], 0)
        self.assertEqual(packet["state"]["currency"]["game_state_key"], "currency.balance")
        self.assertEqual(
            packet["state"]["currency"]["game_state_path"],
            "game_state/currency.balance",
        )
        self.assertIn("display_balance", packet["state"]["currency"])
        self.assertIn(
            "state.currency.balance",
            packet["state"]["currency"]["transaction_rule"],
        )
        self.assertIn(
            "game_state/currency.balance",
            packet["response_contract"]["currency_transactions"],
        )
        self.assertIn("Currency is not an inventory item", packet["state"]["currency"]["transaction_rule"])
        self.assertEqual(
            packet["state"]["scene"]["time"],
            "Monday, Month 1 1, Year 1, Morning",
        )
        self.assertEqual(packet["state"]["calendar"]["current"]["season_hint"], "spring")
        self.assertIn("calendar_time", packet["response_contract"])
        self.assertIn("character_profile", packet["response_contract"])
        self.assertIn("character_scope", packet["response_contract"])
        self.assertIn(
            "not proof that the whole world shares that theme",
            packet["response_contract"]["character_scope"],
        )
        self.assertIn("player_ai_preferences", packet["response_contract"])
        self.assertIn("active_tasks", packet["response_contract"])
        self.assertIn("mature_content", packet["response_contract"])
        self.assertIn(
            "adults of legal drinking age",
            packet["response_contract"]["mature_content"],
        )
        self.assertIn(
            "fictional in-world slurs",
            packet["response_contract"]["mature_content"],
        )
        self.assertIn("background_music", packet["response_contract"])
        self.assertIn("MusicChangedEvent", packet["response_contract"]["known_event_types"])
        self.assertIn("WorldLoreChangedEvent", packet["response_contract"]["known_event_types"])
        self.assertNotIn("StoryAdvancedEvent", packet["response_contract"]["known_event_types"])
        self.assertNotIn("SecretAddedEvent", packet["response_contract"]["known_event_types"])
        self.assertNotIn(
            "MerchantInterfaceRequestedEvent",
            packet["response_contract"]["known_event_types"],
        )
        self.assertEqual(
            packet["state"]["audio"]["valid_music_tracks"][0],
            "Town Village City.mp3",
        )
        self.assertEqual(packet["state"]["audio"]["current_music"], "Town Village City.mp3")
        self.assertEqual(packet["state"]["inventory"]["items"][0]["name"], "Lantern")
        self.assertEqual(packet["state"]["inventory"]["items"][0]["value_base_units"], 0)
        self.assertEqual(packet["state"]["item_catalog"]["items"][0]["name"], "Lantern")
        self.assertNotIn("quantity", packet["state"]["item_catalog"]["items"][0])
        self.assertIn("item_catalog", packet["response_contract"])
        self.assertIn(
            "Material, Ingredient, Reagent, Crafting Item",
            packet["response_contract"]["item_catalog"],
        )
        self.assertIn(
            "state.item_catalog.items",
            packet["state"]["alchemy"]["rules"]["recipe_ingredient_rule"],
        )
        self.assertEqual(
            packet["state"]["active_tasks"]["tasks"][0]["name"],
            "Find the Missing Ledger",
        )
        self.assertNotIn("notes", packet["state"]["active_tasks"]["tasks"][0])
        self.assertEqual(packet["state"]["currency"]["baseline_unit"], "base currency unit")
        self.assertEqual(len(packet["recent_history"]), 2)
        self.assertIn("narration.core_contract", section_ids)
        self.assertIn("gm.safety_and_crime_narration", section_ids)
        self.assertIn("response.structured_story_turn", section_ids)
        self.assertIn("inventory.default_guidance", section_ids)
        self.assertIn("alchemy.default_guidance", section_ids)
        self.assertIn("skills.default_guidance", section_ids)
        self.assertIn("event.add", section_ids)
        self.assertIn("skill_checks", packet["response_contract"])
        self.assertEqual(
            packet["state"]["skills"]["resolved_checks_this_turn"][0]["roll"],
            20,
        )
        self.assertIn(
            "do not request duplicate checks",
            packet["response_contract"]["skill_checks"],
        )
        self.assertIn(
            "very high successful rolls",
            packet["state"]["skills"]["rules"]["resolved_check_rule"],
        )
        self.assertIn("creative_ideas", packet["response_contract"])
        self.assertIn("banned_terms", packet["creative_ideas"])
        self.assertIn("Alden", packet["creative_ideas"]["banned_terms"])
        self.assertIn("player_character_name_examples", packet["creative_ideas"])
        self.assertNotIn(
            "Alden",
            packet["creative_ideas"]["player_character_name_examples"]["ideas"],
        )
        self.assertNotIn(
            "Alden",
            {
                idea
                for category in packet["creative_ideas"]["categories"]
                for idea in category["ideas"]
            },
        )
        self.assertIn(
            "preferred source",
            packet["response_contract"]["creative_ideas"],
        )
        packet_json = json.dumps(packet)
        self.assertNotIn("double-bracket", packet_json)
        self.assertNotIn("legacy_tag", packet_json)
        self.assertNotIn("do_not_emit_legacy_tag", packet_json)
        self.assertIn(
            "alchemy_ingredients",
            {category["id"] for category in packet["creative_ideas"]["categories"]},
        )
        self.assertIn("npc_memory", packet["response_contract"])
        self.assertIn("currency_transactions", packet["response_contract"])
        self.assertIn(
            "not inventory coin items",
            packet["response_contract"]["currency_transactions"],
        )
        self.assertIn("multiple entries", packet["response_contract"]["events"])
        self.assertIn(
            "one NpcUpsertedEvent per distinct meaningful NPC",
            packet["response_contract"]["npc_memory"],
        )
        self.assertIn("known_facts", packet["response_contract"]["npc_memory"])
        self.assertIn("disposition", packet["response_contract"]["npc_memory"])
        self.assertNotIn("disposition", packet["state"]["npcs"]["relevant"][0])
        self.assertIn("ActiveTaskUpsertedEvent", packet["response_contract"]["known_event_types"])
        self.assertIn("ActiveTaskCompletedEvent", packet["response_contract"]["known_event_types"])
        self.assertIn(
            "Do not leave visible task fields blank",
            packet["state"]["active_tasks"]["rules"]["field_completion_rule"],
        )
        self.assertIn(
            "due_elapsed_minutes",
            packet["state"]["active_tasks"]["rules"]["field_completion_rule"],
        )
        self.assertIn(
            "instead of vague due-date prose",
            packet["response_contract"]["active_tasks"],
        )
        self.assertIn(
            "should not be blank",
            packet["state"]["npcs"]["rules"]["new_npc_rule"],
        )
        self.assertEqual(
            packet["state"]["npcs"]["relevant"][0]["name"],
            "Mira Coppercup",
        )
        json.dumps(packet)

    def test_build_story_context_caps_growing_state_sections(self) -> None:
        library = ContextReferenceLoader().load_default_library()
        builder = AiContextBuilder(library, max_history_entries=8)
        state = AdventureState(
            metadata=AdventureMetadata(title="Long Session"),
            player=PlayerState(name="Kit"),
            world=WorldState(location="Workshop", weather="Clear"),
            inventory=InventoryState(
                items=[
                    InventoryItem(
                        name=f"Inventory Item {index:02d}",
                        description="x" * 1400,
                    )
                    for index in range(55)
                ]
            ),
            item_catalog=ItemCatalogState(
                items=[
                    ItemCatalogEntry(
                        name=f"Catalog Item {index:02d}",
                        description="y" * 1400,
                    )
                    for index in range(70)
                ]
            ),
            active_tasks=ActiveTasksState(
                tasks=[
                    ActiveTask(
                        name=f"Task {index:02d}",
                        description="z" * 1400,
                    )
                    for index in range(45)
                ]
            ),
            history=HistoryState(
                entries=[
                    HistoryEntry(kind="story", content=f"Entry {index} " + "h" * 1400)
                    for index in range(12)
                ]
            ),
        )

        packet = builder.build_story_context(
            state,
            player_command="Keep working.",
        )

        self.assertEqual(len(packet["state"]["inventory"]["items"]), 50)
        self.assertEqual(len(packet["state"]["item_catalog"]["items"]), 60)
        self.assertEqual(len(packet["state"]["active_tasks"]["tasks"]), 40)
        self.assertEqual(len(packet["recent_history"]), 8)
        self.assertLessEqual(
            len(packet["state"]["inventory"]["items"][0]["description"]),
            1200,
        )
        self.assertLessEqual(len(packet["recent_history"][0]["content"]), 1200)

    def test_creative_ideas_are_omitted_when_not_relevant(self) -> None:
        packet = AiContextBuilder(
            ContextReferenceLoader().load_default_library(),
            creative_ideas=CreativeIdeasLibrary.load_default(),
        ).build_story_context(
            AdventureState(metadata=AdventureMetadata(title="Context Test")),
            player_command="Wait quietly.",
        )

        self.assertEqual(packet["creative_ideas"]["categories"], [])
        self.assertIn("Alden", packet["creative_ideas"]["banned_terms"])

    def test_creative_ideas_are_randomized_not_front_sliced(self) -> None:
        library = CreativeIdeasLibrary(
            {
                "usage": {},
                "categories": [
                    {
                        "id": "test_names",
                        "title": "Test Names",
                        "tags": ["world"],
                        "ideas": [
                            "Idea 0",
                            "Idea 1",
                            "Idea 2",
                            "Idea 3",
                            "Idea 4",
                            "Idea 5",
                        ],
                    }
                ],
            },
            max_ideas_per_category=3,
            rng=random.Random(7),
        )

        packet = library.select_for_tags({"world"})
        ideas = packet["categories"][0]["ideas"]

        self.assertEqual(len(ideas), 3)
        self.assertNotEqual(ideas, ["Idea 0", "Idea 1", "Idea 2"])

    def test_creative_ideas_include_balanced_player_character_name_pool(self) -> None:
        library = CreativeIdeasLibrary(
            {
                "usage": {},
                "categories": [
                    {
                        "id": "male_character_names",
                        "title": "Male Character Names",
                        "tags": ["character"],
                        "ideas": ["M1", "M2", "M3", "M4"],
                    },
                    {
                        "id": "female_character_names",
                        "title": "Female Character Names",
                        "tags": ["character"],
                        "ideas": ["F1", "F2", "F3", "F4"],
                    },
                ],
            },
            max_ideas_per_category=4,
            rng=random.Random(3),
        )

        ideas = library.select_for_new_game()["player_character_name_examples"]["ideas"]

        self.assertEqual(len(ideas), 8)
        self.assertEqual(len({idea for idea in ideas if idea.startswith("F")}), 4)
        self.assertEqual(len({idea for idea in ideas if idea.startswith("M")}), 4)

    def test_default_library_includes_converted_default_rules(self) -> None:
        library = ContextReferenceLoader().load_default_library()
        section_ids = {section.id for section in library.sections}

        self.assertIn("response.structured_story_turn", section_ids)
        self.assertIn("event.status", section_ids)
        self.assertIn("event.add", section_ids)
        self.assertIn("event.quest", section_ids)


if __name__ == "__main__":
    unittest.main()
