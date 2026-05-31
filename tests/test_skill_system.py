from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from ai_adventure.context.context_builder import AiContextBuilder, infer_context_tags
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.skills.rules import bonus_for_level, dc_for_difficulty, level_for_xp


class SkillSystemTests(unittest.TestCase):
    def test_skill_rule_math_is_simple_and_predictable(self) -> None:
        self.assertEqual(bonus_for_level(1), 2)
        self.assertEqual(bonus_for_level(5), 10)
        self.assertEqual(level_for_xp(1, 9), 1)
        self.assertEqual(level_for_xp(1, 10), 2)
        self.assertEqual(level_for_xp(2, 70), 5)
        self.assertEqual(dc_for_difficulty("easy"), 10)
        self.assertEqual(dc_for_difficulty("hard"), 18)

    def test_skill_upsert_and_xp_events_persist_progression(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Skill Test")
            applier = EventApplier(repository)

            results = applier.apply_events(
                [
                    {
                        "type": "SkillUpsertedEvent",
                        "payload": {
                            "name": "Stealth",
                            "description": "Moving quietly and avoiding notice.",
                            "level": 1,
                        },
                    },
                    {
                        "type": "SkillXpAddedEvent",
                        "payload": {"skill_name": "Stealth", "xp_amount": 10},
                    },
                ]
            )

            skill = repository.get_skill("Stealth")

            self.assertEqual([result.status for result in results], ["applied", "applied"])
            self.assertIsNotNone(skill)
            assert skill is not None
            self.assertEqual(skill["level"], 2)
            self.assertEqual(skill["xp"], 10)
            self.assertEqual(skill["bonus"], 4)

    def test_skill_xp_event_without_amount_defaults_to_one_xp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Skill Test")
            repository.upsert_skill("Stealth", "Moving quietly and avoiding notice.", 1)

            result = EventApplier(repository).apply_event(
                {
                    "type": "SkillXpAddedEvent",
                    "payload": {"skill_name": "Stealth"},
                }
            )
            skill = repository.get_skill("Stealth")

            self.assertEqual(result.status, "applied")
            self.assertIsNotNone(skill)
            assert skill is not None
            self.assertEqual(skill["xp"], 1)
            self.assertEqual(skill["level"], 1)

    def test_replace_skills_removes_new_game_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Skill Test")
            repository.upsert_skill(
                "Signature Expertise",
                "Player-selected level 5 starting skill.",
                5,
            )

            repository.replace_skills(
                [
                    {
                        "name": "Canal Investigation",
                        "description": "Reading wet footprints, dock ledgers, and canal-side clues.",
                        "level": 5,
                    },
                    {
                        "name": "Quiet Leverage",
                        "description": "Getting cooperation through careful pressure and timing.",
                        "level": 4,
                    },
                ]
            )

            skills = repository.list_skills()

            self.assertEqual([skill["name"] for skill in skills], ["Canal Investigation", "Quiet Leverage"])
            self.assertNotIn(
                "Player-selected",
                " ".join(str(skill["description"]) for skill in skills),
            )

    def test_skill_check_event_rolls_and_records_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Skill Test")
            repository.upsert_skill("Athletics", "Physical effort and movement.", 2)
            applier = EventApplier(repository, rng=random.Random(2))

            result = applier.apply_event(
                {
                    "type": "SkillCheckRequestedEvent",
                    "payload": {"skill_name": "Athletics", "dc": 12},
                }
            )
            checks = repository.list_skill_checks()
            history = repository.list_history()

            self.assertEqual(result.status, "applied")
            self.assertEqual(len(checks), 1)
            self.assertEqual(checks[0]["skill_name"], "Athletics")
            self.assertEqual(checks[0]["bonus"], 4)
            self.assertEqual(checks[0]["roll"], 2)
            self.assertEqual(checks[0]["total"], 6)
            self.assertEqual(checks[0]["outcome"], "failure")
            self.assertFalse(
                any("d20" in str(entry.get("content", "")) for entry in history)
            )

    def test_state_manager_and_context_include_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Skill Test")
            repository.upsert_skill("Alchemy", "Preparing and combining reagents.", 3)
            EventApplier(repository, rng=random.Random(5)).apply_event(
                {
                    "type": "SkillCheckRequestedEvent",
                    "payload": {"skill_name": "Alchemy", "difficulty": "easy"},
                }
            )

            state = StateManager(repository).load_state()
            packet = AiContextBuilder.from_default_library().build_story_context(
                state,
                player_command="Roll an alchemy skill check",
            )

            self.assertEqual(state.skills.skills[0].name, "Alchemy")
            self.assertEqual(state.skills.skills[0].bonus, 6)
            self.assertEqual(state.skills.recent_checks[0].skill_name, "Alchemy")
            self.assertIn("skill", infer_context_tags("Roll a skill check"))
            self.assertEqual(packet["state"]["skills"]["known_skills"][0]["name"], "Alchemy")
            self.assertEqual(packet["state"]["skills"]["rules"]["bonus_formula"], "level * 2")
            self.assertIn(
                "SkillCheckRequestedEvent",
                packet["state"]["skills"]["rules"]["uncertain_action_rule"],
            )


if __name__ == "__main__":
    unittest.main()
