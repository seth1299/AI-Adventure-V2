from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.context.reference_loader import ContextReferenceLoader
from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository


class JournalTests(unittest.TestCase):
    def test_private_journal_notes_persist_outside_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Journal Test")

            repository.set_journal_notes("Private theory: the mayor is lying.")

            self.assertEqual(
                repository.get_journal_notes(),
                "Private theory: the mayor is lying.",
            )
            self.assertNotIn(
                "Private theory",
                " ".join(entry["content"] for entry in repository.list_history()),
            )

    def test_private_journal_notes_do_not_enter_ai_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Journal Test")
            repository.set_journal_notes("Private theory: the mayor is lying.")
            repository.set_setting(
                "ai.additional_context",
                "Please respond only in the third person.",
            )
            state = StateManager(repository).load_state()
            builder = AiContextBuilder(
                ContextReferenceLoader().load_default_library(),
                max_history_entries=4,
                max_reference_sections=4,
            )

            packet = builder.build_story_context(
                state,
                player_command="Look around the market",
            )
            encoded_packet = json.dumps(packet)

            self.assertNotIn("journal.private_notes", encoded_packet)
            self.assertNotIn("Private theory", encoded_packet)
            self.assertNotIn("mayor is lying", encoded_packet)
            self.assertIn("Please respond only in the third person.", encoded_packet)


if __name__ == "__main__":
    unittest.main()
