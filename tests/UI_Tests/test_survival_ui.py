"""Real Qt checks for the wizard setting and fixed Story meters."""

import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ai_adventure.events.event_applier import EventApplier
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.new_game_templates import save_new_game_template
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.dialogues import NewGameTemplateManagerDialog
from ai_adventure.ui.screens.story import StoryScreen
from ai_adventure.ui.wizards.new_game import NewGameWizard
from tests.qt_test_case import QtTestCase


class SurvivalUiTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def wizard(self):
        return NewGameWizard(
            tts_enabled=False, api_key_path=self.root / "key",
            terms_acceptance_path=self.root / "terms",
        )

    def test_genre_default_and_explicit_choice_on_fighting_page(self):
        wizard = self.wizard()
        self.assertEqual(wizard.survival_tracking_combo.currentData(), False)
        self.assertIs(wizard.survival_tracking_combo.parent(), wizard.fighting_focus_combo.parent())
        wizard.genre_input.setText("Horror, Survival")
        self.assertEqual(wizard.survival_tracking_combo.currentData(), True)
        wizard.genre_input.setText("Fantasy")
        self.assertEqual(wizard.survival_tracking_combo.currentData(), False)
        wizard.genre_input.setText("Survival")
        wizard.survival_tracking_combo.setCurrentIndex(0)
        wizard.genre_input.setText("Fantasy, Survival")
        self.assertEqual(wizard.survival_tracking_combo.currentData(), False)
        self.assertEqual(wizard.build_template_setup()["survival"], {"enabled": False})

    def test_loading_templates_restores_choice_and_legacy_genre_default(self):
        wizard = self.wizard()
        for enabled in (False, True):
            wizard.load_setup(normalize_new_game_setup({
                "specified_genre": "Survival", "survival": {"enabled": enabled},
            }))
            self.assertEqual(wizard.survival_tracking_combo.currentData(), enabled)
            self.assertEqual(wizard.build_template_setup()["survival"]["enabled"], enabled)
        legacy = normalize_new_game_setup({"specified_genre": "Survival"})
        legacy.pop("survival")
        wizard.load_setup(legacy)
        self.assertEqual(wizard.survival_tracking_combo.currentData(), True)
        wizard.genre_input.setText("Fantasy")
        self.assertEqual(wizard.survival_tracking_combo.currentData(), False)

    def test_template_editor_restores_and_exports_same_toggle(self):
        path = self.root / "templates.json"
        save_new_game_template(path, normalize_new_game_setup({
            "specified_genre": "Survival", "survival": {"enabled": False},
        }), template_name="Survival without meters")
        dialog = NewGameTemplateManagerDialog(template_path=path, tts_enabled=False)
        self.assertEqual(dialog.survival_tracking_combo.currentData(), False)
        dialog.survival_tracking_combo.setCurrentIndex(1)
        self.assertEqual(dialog._build_setup_from_editor()["survival"], {"enabled": True})

    def test_story_bars_stay_above_scroll_refresh_and_hide_for_disabled_saves(self):
        enabled = SaveRepository.create_new_save(self.root, "Enabled", {"survival": {"enabled": True}})
        disabled = SaveRepository.create_new_save(self.root, "Disabled")
        story = StoryScreen()
        story.resize(900, 700)
        story.set_repository(enabled)
        story.show()
        self.app.processEvents()
        self.assertTrue(story.survival_bars_box.isVisible())
        previous_bottom = 0
        for meter, bar in story.survival_bars.items():
            self.assertTrue(bar.isVisible())
            self.assertEqual(bar.value(), 100)
            self.assertGreaterEqual(bar.geometry().top(), previous_bottom)
            previous_bottom = bar.geometry().bottom()
            self.assertFalse(story.conversation_scroll.isAncestorOf(bar))
        EventApplier(enabled).apply_events([{"type": "StatusUpdatedEvent", "payload": {
            "location": "AUTO", "weather": "AUTO", "minutes_passed": 0,
            "hunger": 70, "thirst": 40, "energy": 20,
        }}])
        story.refresh()
        self.assertEqual([bar.value() for bar in story.survival_bars.values()], [70, 40, 20])
        story.set_repository(disabled)
        self.assertTrue(story.survival_bars_box.isHidden())
        story.set_repository(enabled)
        self.assertEqual(story.survival_bars["hunger"].value(), 70)
        story.set_repository(None)
        self.assertTrue(story.survival_bars_box.isHidden())
