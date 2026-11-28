import json
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QPlainTextEdit

from ai_adventure.ai.gemini_service import build_gemini_new_game_phase_prompt, build_gemini_story_prompt
from ai_adventure.ai.new_game_repair_context import repair_context
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.core.state_manager import StateManager
from ai_adventure.new_game_setup import build_new_game_setup_packet, fallback_world_summary, normalize_new_game_setup
from ai_adventure.new_game_templates import load_new_game_template, save_new_game_template
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.saved_characters import SavedCharacterLibrary
from ai_adventure.ui.screens.character import CharacterScreen
from ai_adventure.ui.wizards.new_game import NewGameWizard
from ai_adventure.world_species import SPECIES_PROFILE_RULE, WORLD_SPECIES_RULE, bundled_species, normalize_world_species
from tests.qt_test_case import QtTestCase


GLASSKIN = {
    "name": "Glasskin",
    "genders": ["Facet", "Prism"],
    "typical_maturation_age": "40 years",
    "typical_lifespan": "Three centuries",
    "typical_height": "5 to 6 feet",
    "typical_weight": "100 to 140 pounds",
    "typical_genres": ["Fantasy", "Sci-Fi"],
    "description": "Glasskin grow thin, translucent plates that catch colored light.",
}


class WorldSpeciesTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    @staticmethod
    def menu_action(page, text):
        page.add_menu.aboutToShow.emit()
        return next(action for action in page.add_menu.actions() if action.text() == text)

    def test_wizard_species_menu_saves_custom_json_and_restores_template_snapshot(self):
        custom_dir = self.root / "saved_species"
        wizard = NewGameWizard(tts_enabled=False, saved_species_dir=custom_dir)
        wizard.show()
        wizard.next()
        page = wizard.world_species_page
        self.assertIs(wizard.currentPage(), page)
        self.menu_action(page, "Human").trigger()
        self.assertFalse(self.menu_action(page, "Human").isEnabled())

        def fill_species_dialog():
            editor = self.app.activeModalWidget()
            try:
                for field, value in (
                    ("speciesName", GLASSKIN["name"]),
                    ("speciesGenders", ", ".join(GLASSKIN["genders"])),
                    ("speciesMaturation", GLASSKIN["typical_maturation_age"]),
                    ("speciesLifespan", GLASSKIN["typical_lifespan"]),
                    ("speciesHeight", GLASSKIN["typical_height"]),
                    ("speciesWeight", GLASSKIN["typical_weight"]),
                    ("speciesGenres", ", ".join(GLASSKIN["typical_genres"])),
                ):
                    editor.findChild(QLineEdit, field).setText(value)
                editor.findChild(QPlainTextEdit, "speciesDescription").setPlainText(GLASSKIN["description"])
                editor.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
            finally:
                if editor.isVisible():
                    editor.reject()

        QTimer.singleShot(0, fill_species_dialog)
        self.menu_action(page, "Create Custom Species…").trigger()
        [saved_file] = custom_dir.glob("*.species.json")
        self.assertEqual(json.loads(saved_file.read_text(encoding="utf-8")), GLASSKIN)
        self.assertIn(GLASSKIN["description"], page.details.toPlainText())

        template_path = self.root / "templates.json"
        self.assertTrue(save_new_game_template(template_path, wizard.build_template_setup()))
        saved_template = load_new_game_template(template_path)
        reloaded = NewGameWizard(template_setup=saved_template, tts_enabled=False, saved_species_dir=custom_dir)
        self.assertEqual(reloaded.build_setup()["world_species"], wizard.build_setup()["world_species"])

        fresh = NewGameWizard(tts_enabled=False, saved_species_dir=custom_dir)
        self.menu_action(fresh.world_species_page, "Glasskin").trigger()
        self.assertEqual(fresh.build_setup()["world_species"], [GLASSKIN])

        def edit_species_description():
            editor = self.app.activeModalWidget()
            try:
                editor.findChild(QPlainTextEdit, "speciesDescription").setPlainText("A world-specific description.")
                editor.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
            finally:
                if editor.isVisible():
                    editor.reject()

        QTimer.singleShot(0, edit_species_description)
        fresh.world_species_page.edit_button.click()
        self.assertEqual(fresh.build_setup()["world_species"], [
            {**GLASSKIN, "description": "A world-specific description."},
        ])
        self.assertEqual(json.loads(saved_file.read_text(encoding="utf-8")), GLASSKIN)
        fresh.world_species_page.remove_button.click()
        self.assertEqual(fresh.build_setup()["world_species"], [])
        self.assertTrue(self.menu_action(fresh.world_species_page, "Glasskin").isEnabled())

    def test_world_species_survives_save_and_reaches_opening_and_story_prompts(self):
        self.assertEqual(normalize_new_game_setup({})["world_species"], [])
        setup = normalize_new_game_setup({"world_species": [GLASSKIN, {"name": "glasskin"}]})
        setup_packet = build_new_game_setup_packet(setup)
        self.assertEqual(setup_packet["setup"]["world_species"], [GLASSKIN])
        for phase in ("world_skeleton", "entities_mechanics", "opening_prose"):
            with self.subTest(phase=phase):
                prompt = build_gemini_new_game_phase_prompt(setup_packet, phase)
                self.assertIn(GLASSKIN["description"], prompt)
                self.assertIn(WORLD_SPECIES_RULE, prompt)
        repair = repair_context(setup_packet, {}, {}, ["starting_npcs"], [], 1)
        self.assertEqual(repair["setup"]["world_species"], [GLASSKIN])
        self.assertIn("Glasskin", fallback_world_summary(setup))

        repository = SaveRepository.create_new_save(self.root / "saves", "Glass Coast", setup)
        state = StateManager(repository).load_state()
        self.assertEqual(repository.get_setting("world.species"), [GLASSKIN])
        builder = AiContextBuilder.from_default_library()
        routine_packet = builder.build_story_context(state, player_command="I enter town.")
        self.assertNotIn("species", routine_packet["state"]["world_profile"])
        story_packet = builder.build_story_context(state, player_command="How tall am I?")
        self.assertEqual(story_packet["state"]["world_profile"]["species"], [GLASSKIN])
        story_prompt = build_gemini_story_prompt(story_packet)
        self.assertIn(GLASSKIN["description"], story_prompt)
        self.assertIn(GLASSKIN["typical_weight"], story_prompt)
        self.assertIn(SPECIES_PROFILE_RULE, story_prompt)

    def test_bundled_profiles_and_legacy_gender_strings_load(self):
        defaults = {profile["name"]: profile for profile in bundled_species()}
        self.assertEqual(defaults["Elf"]["genders"], ["Male", "Female"])
        self.assertEqual(defaults["Elf"]["typical_height"], "6' to 7'")
        self.assertEqual(defaults["Elf"]["typical_weight"], "160-190 pounds")
        self.assertEqual(defaults["Elf"]["typical_genres"], ["Fantasy", "High Fantasy"])
        [legacy] = normalize_world_species([{"name": "Old Species", "genders": "Male, Female"}])
        self.assertEqual(legacy["genders"], ["Male", "Female"])
        self.assertEqual(legacy["typical_height"], "")
        self.assertEqual(legacy["typical_genres"], [])

    def test_story_sends_focused_species_and_omits_unrelated_profiles(self):
        defaults = {profile["name"]: profile for profile in bundled_species()}
        roster = [defaults["Human"], defaults["Elf"], GLASSKIN]
        setup = {"world_species": roster, "start_location": "Glass Coast"}
        repository = SaveRepository.create_new_save(self.root / "saves", "Glass Coast", setup)
        alia = repository.upsert_npc(name="Alia", location="Glass Coast", species="elf")
        repository.upsert_npc(name="Sela", location="Glass Coast", species="Glasskin")

        packet = StoryTurnService.build_context_packet(repository, "I ask Alia about the town.")
        world = packet["state"]["world_profile"]
        self.assertEqual(world["species_roster"], ["Human", "Elf", "Glasskin"])
        self.assertEqual(world["species"], [defaults["Elf"]])
        prompt = build_gemini_story_prompt(packet)
        self.assertIn(defaults["Elf"]["typical_weight"], prompt)
        self.assertIn("High Fantasy", prompt)
        self.assertNotIn(GLASSKIN["description"], prompt)
        self.assertNotIn(defaults["Human"]["description"], prompt)

        repository.append_history("story", "Alia describes the town.", subjects=[{"type": "npc", "id": alia["id"]}])
        follow_up = StoryTurnService.build_context_packet(repository, '"Thanks!"')
        self.assertEqual(follow_up["state"]["world_profile"]["species"], [defaults["Elf"]])

        routine = StoryTurnService.build_context_packet(repository, "I wait quietly.")
        self.assertNotIn("species", routine["state"]["world_profile"])
        lore = StoryTurnService.build_context_packet(repository, "How tall are Elves?")
        self.assertEqual(lore["state"]["world_profile"]["species"], [defaults["Elf"]])

        repository.set_setting("world.species", [])
        packet = StoryTurnService.build_context_packet(repository, "I ask Alia about the town.")
        self.assertEqual(packet["state"]["world_profile"]["species"], [defaults["Elf"]])
        self.assertNotIn("species_roster", packet["state"]["world_profile"])

        opening = build_gemini_new_game_phase_prompt(
            build_new_game_setup_packet(setup), "opening_prose",
            approved_state={"starting_npcs": [{"name": "Alia", "species": "Elf"}]},
        )
        self.assertIn(defaults["Elf"]["typical_weight"], opening)
        self.assertNotIn(GLASSKIN["description"], opening)

    def test_player_species_defaults_and_species_and_age_round_trip(self):
        library = SavedCharacterLibrary(self.root / "saved_characters")
        wizard = NewGameWizard(tts_enabled=False, saved_character_library=library)
        self.assertFalse(wizard.character_species_combo.isEnabled())
        roster = [GLASSKIN, {"name": "Human"}]
        wizard.world_species_page.set_species(roster)
        self.assertEqual(wizard.character_species_combo.currentData(), "Human")
        wizard.world_species_page.set_species([GLASSKIN])
        self.assertEqual(wizard.character_species_combo.currentData(), "Glasskin")
        wizard.world_species_page.set_species(roster)
        combo = wizard.character_species_combo
        combo.setCurrentIndex(combo.findData("Glasskin"))
        combo.activated.emit(combo.currentIndex())
        wizard.world_species_page.set_species(list(reversed(roster)))
        self.assertEqual(combo.currentData(), "Glasskin")
        wizard.character_age_input.setValue(45)
        library.save(wizard._character_profile_from_controls())
        [saved] = library.list()
        wizard._load_character_profile(saved.profile)
        setup = wizard.build_setup()
        reloaded = NewGameWizard(template_setup=setup, tts_enabled=False)
        self.assertEqual(reloaded.character_species_combo.currentData(), "Glasskin")
        self.assertEqual(reloaded.character_age_input.age(), 45)
        self.assertIsNone(normalize_new_game_setup({"character": {"age": -2}})["character"]["age"])

        repository = SaveRepository.create_new_save(self.root / "saves", "Glass Coast", setup)
        state = StateManager(repository).load_state()
        self.assertEqual((state.player.species, state.player.age), ("Glasskin", 45))
        screen = CharacterScreen(tts_enabled=False)
        screen.set_repository(repository)
        self.assertEqual(screen.profile_species_label.text(), "Glasskin")
        self.assertEqual(screen.profile_age_label.text(), "45 years")
        screen.age_input.setValue(46)
        screen.age_input.editingFinished.emit()
        self.assertEqual(repository.get_setting("player.age"), 46)
