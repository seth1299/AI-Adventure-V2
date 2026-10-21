import tempfile
import unittest
from pathlib import Path

from ai_adventure.ai.gemini_service import (
    NEW_GAME_RESPONSE_JSON_SCHEMA, _parse_new_game_skills, _d20_test_planning_packet,
    _story_prompt_packet,
)
from ai_adventure.application.new_game_service import _finalized_skills_for_save
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.stats import ATTRIBUTES


class SkillAttributeTests(unittest.TestCase):
    def test_learning_and_training_retain_related_stat(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Training")
            result = EventApplier(repository).apply_event({
                "type": "SkillUpsertedEvent",
                "payload": {"name": "Endurance", "description": "Sustain physical exertion.",
                            "level": 1, "attribute": "Constitution", "reason": "Practiced sustained exertion with an instructor."},
            })
            self.assertEqual(result.status, "applied")
            repository.add_skill_xp("Endurance", 2)
            repository.upsert_skill("Endurance", "Sustain demanding physical exertion.", 1)
            self.assertEqual(SaveRepository(repository.db_path).get_skill("Endurance")["attribute"], "Constitution")

    def test_new_game_contract_parser_and_commit_preserve_player_stat(self):
        schema = NEW_GAME_RESPONSE_JSON_SCHEMA["properties"]["skills"]["items"]
        self.assertIn("attribute", schema["required"])
        self.assertEqual(schema["properties"]["attribute"]["enum"], list(ATTRIBUTES))
        parsed = _parse_new_game_skills([{
            "name": "Accounting", "description": "Manage financial records.",
            "level": 2, "attribute": "Intelligence",
        }])
        self.assertEqual(parsed[0]["attribute"], "Intelligence")
        context = {"selection": {"tags": ["skill"]}, "state": {"skills": {"known_skills": parsed}}}
        self.assertEqual(_d20_test_planning_packet(context)["known_skills"][0]["attribute"], "Intelligence")
        self.assertIn('Intelligence', str(_story_prompt_packet(context)))
        setup = [{"name": "Accounting", "description": "Manage records.", "level": 2, "attribute": "Wisdom"}]
        finalized = _finalized_skills_for_save(parsed, setup)
        self.assertEqual(finalized[0]["attribute"], "Wisdom")
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Finalized")
            repository.replace_skills(finalized)
            self.assertEqual(repository.list_skills()[0]["attribute"], "Wisdom")
