from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ai_adventure.ai.gemini_service import APPLICATION_SYSTEM_INSTRUCTION
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.new_game_setup import build_new_game_setup_packet
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.skills.rules import SKILL_DESCRIPTION_RULE, MAX_SKILL_XP_RULE
from ai_adventure.ui.screens.skills import _skill_xp_progress_bar


class SkillPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_master_progress_is_not_a_percentage(self):
        for xp in (0, 32, 100):
            bar = _skill_xp_progress_bar({"name": "Stealth", "level": 5, "xp": xp})
            self.assertEqual(bar.format(), "Max Level")
            self.assertIn("no further XP", bar.toolTip())
            self.assertIn("Max Level", bar.accessibleName())
        self.assertEqual(_skill_xp_progress_bar({"level": 1, "xp": 4}).format(), "50%")

    def test_xp_stops_after_reaching_master(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Master test")
            repository.upsert_skill("Stealth", "Moving quietly and avoiding notice.", 4)
            repository.add_skill_xp("Stealth", 32)
            mastered = repository.get_skill("Stealth")
            self.assertEqual(mastered["level"], 5)
            self.assertIsNone(repository.add_skill_xp("Stealth", 3))
            result = EventApplier(repository).apply_event({
                "type": "SkillXpAddedEvent", "payload": {"skill_name": "Stealth", "xp_amount": 3},
            })
            self.assertEqual(result.status, "skipped")
            self.assertIn("Max Level", result.message)
            self.assertEqual(repository.get_skill("Stealth"), mastered)

    def test_description_guidance_reaches_all_model_surfaces(self):
        self.assertIn(SKILL_DESCRIPTION_RULE, APPLICATION_SYSTEM_INSTRUCTION)
        self.assertIn(MAX_SKILL_XP_RULE, APPLICATION_SYSTEM_INSTRUCTION)
        self.assertIn(SKILL_DESCRIPTION_RULE, json.dumps(build_new_game_setup_packet({})))
        data = Path(__file__).resolve().parents[1] / "ai_adventure/data/context"
        for filename in ("default_context.json", "default_rules.json"):
            content = json.loads((data / filename).read_text(encoding="utf-8"))
            self.assertIn(SKILL_DESCRIPTION_RULE, json.dumps(content))
            self.assertIn(MAX_SKILL_XP_RULE, json.dumps(content))
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Context test")
            state = StateManager(repository).load_state()
            packet = AiContextBuilder.from_default_library().build_story_context(state, player_command="Roll a skill check")
            self.assertEqual(packet["state"]["skills"]["rules"]["description_rule"], SKILL_DESCRIPTION_RULE)


if __name__ == "__main__":
    unittest.main()
