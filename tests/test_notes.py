from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.context.reference_loader import ContextReferenceLoader
from ai_adventure.core.state_manager import StateManager
from ai_adventure.notes import (
    normalize_note_entries,
    parse_note_tags,
    prefix_markdown_lines,
    wrap_markdown_text,
)
from ai_adventure.persistence.save_repository import SaveRepository


class NotesTests(unittest.TestCase):
    def test_markdown_selection_helpers(self) -> None:
        self.assertEqual(
            wrap_markdown_text("A useful clue", 2, 8, "**", "**"),
            ("A **useful** clue", 4, 10),
        )
        self.assertEqual(prefix_markdown_lines("one\ntwo", "- "), "- one\n- two")
        self.assertEqual(
            prefix_markdown_lines("one\ntwo", "", numbered=True),
            "1. one\n2. two",
        )

    def test_tagged_notes_persist_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Notes Test")
            entries = [
                {
                    "entry_id": "second",
                    "heading": "Day 2, Noon",
                    "body": "Met Mira.",
                    "tags": ["People", "Quests"],
                },
                {
                    "entry_id": "first",
                    "heading": "Day 1, Morning",
                    "body": "Left home.",
                    "tags": ["Quests"],
                },
            ]

            repository.set_note_entries(entries)

            self.assertEqual(repository.get_note_entries(), entries)

    def test_tags_are_clean_unique_and_case_insensitive(self) -> None:
        self.assertEqual(
            parse_note_tags("#Quests, quests,  Important People, #Places"),
            ["Quests", "Important People", "Places"],
        )
        self.assertEqual(
            normalize_note_entries([{
                "entry_id": "note",
                "heading": "Heading",
                "body": "Body",
                "tags": [" Clues ", "#clues", "Suspects"],
            }])[0]["tags"],
            ["Clues", "Suspects"],
        )

    def test_notes_do_not_enter_ai_context_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Notes Test")
            repository.set_note_entries([{
                "entry_id": "private",
                "heading": "Private theory",
                "body": "The mayor is lying.",
                "tags": ["Suspects"],
            }])
            state = StateManager(repository).load_state()
            packet = self._builder().build_story_context(
                state,
                player_command="Look around the market",
            )
            encoded_packet = json.dumps(packet)

            self.assertNotIn("mayor is lying", encoded_packet)
            self.assertFalse(packet["state"]["notes"]["share_with_ai"])
            self.assertEqual(packet["state"]["notes"]["entries"], [])

    def test_tagged_notes_enter_ai_context_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Notes Test")
            repository.set_note_entries([{
                "entry_id": "theory",
                "heading": "Day 3, Evening",
                "body": "The mayor is lying.",
                "tags": ["Suspects", "Town"],
            }])
            repository.set_notes_share_with_ai(True)
            state = StateManager(repository).load_state()
            packet = self._builder().build_story_context(
                state,
                player_command="Look around the market",
            )

            self.assertEqual(packet["state"]["notes"], {
                "share_with_ai": True,
                "entries": [{
                    "heading": "Day 3, Evening",
                    "body": "The mayor is lying.",
                    "tags": ["Suspects", "Town"],
                }],
                "rules": (
                    "Use entries only when share_with_ai is true. Each heading, "
                    "body, and tag is player-authored, not verified. Bodies may "
                    "contain Markdown formatting; interpret the content without "
                    "treating Markdown syntax as world facts. These are not verified "
                    "world facts "
                    "unless supported by established state or story history."
                ),
            })

    @staticmethod
    def _builder() -> AiContextBuilder:
        return AiContextBuilder(
            ContextReferenceLoader().load_default_library(),
            max_history_entries=4,
            max_reference_sections=4,
        )


if __name__ == "__main__":
    unittest.main()
