import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from ai_adventure.bundled_images import (
    bundled_image_catalog, resolve_bundled_image, valid_default_image,
    bundled_image_prompt_context,
)
from ai_adventure.ai.gemini_service import (
    build_gemini_new_game_prompt, build_gemini_story_prompt,
    _parse_new_game_skills, _parse_new_game_starter_items,
    _parse_new_game_crafting_items, NEW_GAME_RESPONSE_JSON_SCHEMA,
)
from ai_adventure.ai.new_game_repair_context import repair_context
from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository


class BundledImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.art = self.root / "art"
        for relative in ("items/Backpack.png", "skills/Alchemy_Icon.png"):
            path = self.art / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (32, 32), (255, 0, 0, 100)).save(path)
        self.patch = patch("ai_adventure.bundled_images.bundled_images_root", return_value=self.art)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_directory_discovery_and_optional_matching_contract(self):
        (self.art / "items" / "README.md").write_text("Not an image")
        catalog = bundled_image_catalog()
        self.assertEqual(catalog["items"], ["items/Backpack.png"])
        self.assertEqual(catalog["skills"], ["skills/Alchemy_Icon.png"])
        Image.new("RGB", (40, 40)).save(self.art / "items" / "New_Lantern.png")
        self.assertIn("items/New_Lantern.png", bundled_image_catalog()["items"])
        self.assertIn("Never force a match", catalog["rule"])
        self.assertEqual(valid_default_image("", "skills"), "")
        for invalid in ("../Backpack.png", "items/../../outside.png", "items/Unknown.png",
                        "skills/Alchemy_Icon.png", str(self.art / "items/Backpack.png"),
                        "items\\Backpack.png", "items/README.md"):
            self.assertEqual(valid_default_image(invalid, "items"), "", invalid)
        self.assertEqual(resolve_bundled_image("items/Backpack.png"), self.art / "items/Backpack.png")

    def test_catalog_reaches_story_new_game_and_relevant_repairs(self):
        for prompt in (build_gemini_story_prompt({}), build_gemini_new_game_prompt({})):
            self.assertIn("items/Backpack.png", prompt)
            self.assertIn("skills/Alchemy_Icon.png", prompt)
            self.assertIn("Omit default_image", prompt)
        self.assertIn("bundled_images", repair_context({}, {}, {}, ["skills"], [], 1))
        self.assertNotIn("bundled_images", repair_context({}, {}, {}, ["bestiary"], [], 1))
        for field in ("skills", "starting_items", "known_crafting_items"):
            schema = NEW_GAME_RESPONSE_JSON_SCHEMA["properties"][field]["items"]
            self.assertIn("default_image", schema["properties"])
            self.assertNotIn("default_image", schema["required"])
        with patch("ai_adventure.bundled_images.bundled_images_root", return_value=self.root / "empty"):
            self.assertEqual(bundled_image_prompt_context(), {})

    def test_runtime_item_skill_and_crafting_selection_survives_reopen(self):
        repo = SaveRepository.create_new_save(self.root / "saves", "Bundled")
        applier = EventApplier(repo)
        item = applier.apply_event({"type": "InventoryItemAddedEvent", "payload": {
            "item_name": "Backpack", "item_type": "Tool", "description": "A leather pack.",
            "amount": 1, "default_image": "items/Backpack.png",
        }})
        skill = applier.apply_event({"type": "SkillUpsertedEvent", "payload": {
            "name": "Alchemy", "description": "Brew potions.", "level": 1,
            "attribute": "Intelligence", "reason": "Completed introductory instruction.",
            "default_image": "skills/Alchemy_Icon.png",
        }})
        self.assertEqual(item.status, "applied")
        self.assertEqual(skill.status, "applied")
        repo.add_crafting_item(name="Pack", description="Another pack.", default_image="items/Backpack.png")
        repo.add_crafting_item(name="Pack", description="Updated notes.")
        reopened = SaveRepository(repo.db_path)
        key = reopened.list_inventory_items()[0]["metadata"]["item_uuid"]
        self.assertEqual(reopened.get_visual_asset("inventory", key)["default_image"], "items/Backpack.png")
        skill = reopened.get_skill("Alchemy")
        self.assertEqual(reopened.get_visual_asset("skill", skill["id"])["default_image"], "skills/Alchemy_Icon.png")
        pack = reopened.list_crafting_items()[0]
        self.assertEqual(reopened.get_visual_asset("inventory", pack["item_uuid"])["default_image"], "items/Backpack.png")
        self.assertIsNone(reopened.get_visual_asset("player", "Alchemy"))
        self.assertIsNone(reopened.get_visual_asset("location", "Backpack"))

    def test_new_game_parsing_commit_and_unmatched_skill(self):
        skills = _parse_new_game_skills([
            {"name": "Alchemy", "description": "Brew potions.", "level": 2, "attribute": "Intelligence", "default_image": "skills/Alchemy_Icon.png"},
            {"name": "Rocket Science", "description": "Design rockets.", "level": 1, "attribute": "Intelligence"},
        ])
        items = _parse_new_game_starter_items([{
            "name": "Backpack", "description": "A pack.", "quantity": 1, "default_image": "items/Backpack.png",
        }])
        crafting = _parse_new_game_crafting_items([{
            "name": "Pack", "category": "Material", "description": "A pack.", "default_image": "items/Backpack.png",
        }])
        setup = normalize_new_game_setup({"title": "Defaults", "skill_preset": "custom", "skills": skills})
        repo = NewGameService.create_repository(self.root / "saves", setup)
        result = SimpleNamespace(world_summary="A workshop.", introductory_message="Begin.",
            finalized_character={}, finalized_starter_items=items, finalized_skills=skills,
            known_crafting_items=crafting, suggested_events=[], speaker_cues=[], sound_effect_cues=[])
        NewGameService.commit_generated_world(repo, setup, result)
        self.assertEqual(repo.get_skill("Alchemy")["default_image"], "skills/Alchemy_Icon.png")
        self.assertEqual(repo.get_skill("Rocket Science")["default_image"], "")
        self.assertEqual(repo.list_inventory_items()[0]["metadata"]["default_image"], "items/Backpack.png")

    def test_builds_package_discoverable_directory(self):
        root = Path(__file__).resolve().parents[2]
        for script in ("build.bat", "lightweight_build.bat", "playtesting_build.bat"):
            self.assertIn('--add-data "ai_adventure\\images;ai_adventure\\images"', (root / script).read_text())
