from __future__ import annotations

import json
import unittest

from ai_adventure.context.context_builder import AiContextBuilder, infer_context_tags
from ai_adventure.context.reference_loader import ContextReferenceLoader
from ai_adventure.core.models import (
    AdventureMetadata,
    AdventureState,
    HistoryEntry,
    HistoryState,
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
            max_history_entries=2,
            max_reference_sections=14,
        )
        state = AdventureState(
            metadata=AdventureMetadata(title="Context Test"),
            player=PlayerState(name="Mira", condition="Curious"),
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
            history=HistoryState(
                entries=[
                    HistoryEntry(kind="system", content="Adventure started."),
                    HistoryEntry(kind="player", content="Look around."),
                    HistoryEntry(kind="story", content="The road glistens."),
                ]
            ),
        )

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
                }
            ],
        )

        section_ids = {
            section["id"] for section in packet["reference_sections"]
        }

        self.assertEqual(packet["state"]["adventure_title"], "Context Test")
        self.assertEqual(packet["state"]["scene"]["location"], "Old Road")
        self.assertEqual(
            packet["state"]["scene"]["time"],
            "Monday, Month 1 1, Year 1, Morning",
        )
        self.assertEqual(packet["state"]["calendar"]["current"]["season_hint"], "spring")
        self.assertIn("calendar_time", packet["response_contract"])
        self.assertEqual(packet["state"]["inventory"]["items"][0]["name"], "Lantern")
        self.assertEqual(packet["state"]["inventory"]["items"][0]["value_base_units"], 0)
        self.assertEqual(packet["state"]["currency"]["baseline_unit"], "Copper Piece")
        self.assertEqual(len(packet["recent_history"]), 2)
        self.assertIn("narration.core_contract", section_ids)
        self.assertIn("response.structured_story_turn", section_ids)
        self.assertIn("inventory.default_guidance", section_ids)
        self.assertIn("alchemy.default_guidance", section_ids)
        self.assertIn("skills.default_guidance", section_ids)
        self.assertIn("event.add", section_ids)
        self.assertIn("skill_checks", packet["response_contract"])
        self.assertIn("npc_memory", packet["response_contract"])
        self.assertIn("multiple entries", packet["response_contract"]["events"])
        self.assertIn(
            "one NpcUpsertedEvent per distinct meaningful NPC",
            packet["response_contract"]["npc_memory"],
        )
        self.assertEqual(
            packet["state"]["npcs"]["relevant"][0]["name"],
            "Mira Coppercup",
        )
        json.dumps(packet)

    def test_default_library_includes_converted_default_rules(self) -> None:
        library = ContextReferenceLoader().load_default_library()
        section_ids = {section.id for section in library.sections}

        self.assertIn("response.structured_story_turn", section_ids)
        self.assertIn("event.status", section_ids)
        self.assertIn("event.add", section_ids)
        self.assertIn("event.quest", section_ids)


if __name__ == "__main__":
    unittest.main()
