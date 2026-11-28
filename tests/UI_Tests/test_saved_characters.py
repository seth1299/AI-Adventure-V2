import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialogButtonBox, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QStyle, QStyleOptionButton,
)

from ai_adventure.app.user_settings import DEFAULT_UI_FONT_SIZE, MAX_UI_FONT_SIZE
from ai_adventure.application.asset_generation_service import AssetGenerationService
from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.new_game_setup import build_new_game_setup_packet, normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.saved_characters import CHARACTER_PROFILE_FIELDS, SavedCharacterLibrary
from ai_adventure.ui.saved_characters import SavedCharactersDialog
from ai_adventure.ui.screens.character import CharacterScreen
from ai_adventure.ui.wizards.new_game import NewGameWizard
from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator
from tests.qt_test_case import QtTestCase


class SavedCharacterTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.library = SavedCharacterLibrary(self.root / "saved_characters")
        self.profile = {
            "name": "Mara Vale", "name_pronunciation": "MAH-rah", "pronouns": "Xe/Xem",
            "appearance": "A scar above the left eye.", "backstory": "A former archivist.\nNow a courier.",
            "notes": "Collects pressed flowers.",
        }

    def test_played_character_exports_and_imports_into_another_template_with_portrait(self):
        repo = SaveRepository.create_new_save(self.root / "saves", "Mara's Adventure")
        for field, value in self.profile.items():
            repo.set_setting("player_name" if field == "name" else f"player.{field}", value)
        source = self.root / "source.png"
        Image.new("RGBA", (32, 32), (44, 110, 178, 50)).save(source)
        repo.ensure_visual_asset(asset_id="portrait", subject_type="player", subject_key=repo.get_player_id(),
                                 display_name=self.profile["name"], descriptor_hash="test", filename=source.name,
                                 prompt="", model="test", ready=True)
        screen = CharacterScreen(tts_enabled=False, saved_character_library=self.library)
        screen.set_visual_assets_dir(self.root)
        screen.set_repository(repo)
        alias = "Mara Vale (Courier)"
        with patch.object(QMessageBox, "information"), patch.object(
            QInputDialog, "getText", side_effect=[(alias, False), (alias, True), (alias, True)],
        ) as prompt:
            screen.save_character_button.click()
            self.assertEqual(self.library.list(), [])
            screen.save_character_button.click()
            screen.save_character_button.click()
        self.assertEqual([call.kwargs["text"] for call in prompt.call_args_list], [self.profile["name"], self.profile["name"], alias])
        [saved] = self.library.list()
        self.assertEqual(saved.profile, self.profile)
        self.assertEqual(saved.display_name, alias)
        self.assertEqual(saved.portrait["display_name"], self.profile["name"])
        self.assertNotIn(str(source), json.dumps(saved.portrait))
        source.unlink()

        other_image = {"subject_type": "npc", "display_name": "Corin", "png_base64": saved.portrait["png_base64"]}
        old_player_image = {**other_image, "subject_type": "player", "display_name": "Dorian"}
        template = normalize_new_game_setup({"title": "Orbital Station", "specified_genre": "Science fiction",
                                             "world_context": "A research habitat in orbit.",
                                             "character": {"name": "Dorian"},
                                             "template_images": [old_player_image, other_image]})
        wizard = NewGameWizard(template_setup=template, tts_enabled=False, saved_character_library=self.library)
        before = wizard.build_template_setup()
        QTimer.singleShot(0, lambda: self.app.activeModalWidget().use_button.click())
        wizard.load_saved_character_button.click()
        after = wizard.build_template_setup()
        self.assertEqual({field: after["character"][field] for field in CHARACTER_PROFILE_FIELDS}, self.profile)
        self.assertEqual(after["character"]["attributes"], before["character"]["attributes"])
        self.assertEqual({key: value for key, value in after.items() if key not in {"character", "template_images"}},
                         {key: value for key, value in before.items() if key not in {"character", "template_images"}})
        self.assertIn(other_image, after["template_images"])
        self.assertNotIn(old_player_image, after["template_images"])
        setup = wizard.build_setup()
        self.assertNotIn(saved.portrait["png_base64"], json.dumps(build_new_game_setup_packet(setup)))

        new_repo = NewGameService.create_repository(self.root / "saves", setup)
        coordinator = _VisualAssetCoordinator(images_dir=self.root / "cache", api_key_path=self.root / "key", enabled=True)
        requests = coordinator.prepare_initial_batch(new_repo)
        portrait = next(request for request in requests if request.subject_type == "player")
        remaining = coordinator.restore_initial_template_images(new_repo, requests, setup["template_images"])
        self.assertNotIn(portrait, remaining)
        with Image.open(AssetGenerationService.target_path(new_repo, portrait, coordinator.images_dir)) as image:
            self.assertEqual(image.getpixel((0, 0)), (44, 110, 178, 50))

    def test_wizard_creates_a_library_entry_and_updates_the_same_draft(self):
        wizard = NewGameWizard(tts_enabled=False, saved_character_library=self.library)
        wizard._load_character_profile(self.profile)
        with patch.object(QMessageBox, "information"), patch.object(
            QInputDialog, "getText", return_value=("Mara Vale (Courier)", True),
        ):
            wizard.save_character_button.click()
            wizard.character_name_input.setText("Mara the Courier")
            wizard.save_character_button.click()
        [saved] = self.library.list()
        self.assertEqual(saved.profile["name"], "Mara the Courier")
        self.assertEqual(saved.display_name, "Mara Vale (Courier)")
        self.assertIsNone(saved.portrait)

    def test_manager_creates_edits_and_deletes_profiles(self):
        dialog = SavedCharactersDialog(self.library)
        self.assertFalse(dialog.edit_button.isEnabled())

        def enter_name(name, library_name=None):
            editor = self.app.activeModalWidget()
            editor.findChild(QLineEdit, "name").setText(name)
            if library_name is not None:
                editor.findChild(QLineEdit, "library_name").setText(library_name)
            editor.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()

        QTimer.singleShot(0, lambda: enter_name("Dorian"))
        dialog.create_button.click()
        [created] = self.library.list()
        self.assertEqual(created.profile["name"], "Dorian")
        self.assertTrue(dialog.list_widget.item(0).text().startswith("Dorian  ·"))

        QTimer.singleShot(0, lambda: enter_name("Dorian Vale", "Dorian (Mason)"))
        dialog.edit_button.click()
        [edited] = self.library.list()
        self.assertEqual((edited.id, edited.profile["name"]), (created.id, "Dorian Vale"))
        self.assertTrue(dialog.list_widget.item(0).text().startswith("Dorian (Mason)  ·"))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            dialog.delete_button.click()
        self.assertEqual(dialog.list_widget.count(), 1)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog.delete_button.click()
        self.assertEqual(self.library.list(), [])
        self.assertFalse(dialog.delete_button.isEnabled())

    def test_wizard_saves_and_chooses_distinct_entries_with_the_same_character_name(self):
        mason = {**self.profile, "name": "John", "backstory": "A mason."}
        carpenter = {**self.profile, "name": "John", "backstory": "A carpenter."}
        with patch.object(QMessageBox, "information"), patch.object(
            QInputDialog, "getText", side_effect=[("John (Mason)", True), ("John (Carpenter)", True)],
        ):
            for profile in (mason, carpenter):
                wizard = NewGameWizard(tts_enabled=False, saved_character_library=self.library)
                wizard._load_character_profile(profile)
                wizard.save_character_button.click()

        chooser = SavedCharactersDialog(self.library, choose=True)
        self.assertEqual([entry.display_name for entry in chooser.characters], ["John (Carpenter)", "John (Mason)"])
        chooser.list_widget.setCurrentRow(1)
        chooser.use_button.click()
        self.assertEqual(chooser.selected_character.profile, mason)

    def test_manager_creates_custom_pronouns_and_edits_them_to_a_preset(self):
        dialog = SavedCharactersDialog(self.library)
        editor_states = []

        def save_with_pronouns(selection, custom_text=None):
            editor = self.app.activeModalWidget()
            combo = editor.findChild(QComboBox, "pronouns")
            custom = editor.findChild(QLineEdit, "custom_pronouns")
            if combo is None or custom is None:
                editor.reject()
                return
            editor_states.append((combo.currentText(), custom.isVisible(), custom.text()))
            combo.setCurrentText(selection)
            editor_states.append(custom.isVisible())
            if custom_text is not None:
                custom.setText(custom_text)
            editor.findChild(QLineEdit, "name").setText(self.profile["name"])
            editor.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()

        QTimer.singleShot(0, lambda: save_with_pronouns("Custom", "Xe/Xem"))
        dialog.create_button.click()
        [created] = self.library.list()
        self.assertEqual(created.profile["pronouns"], "Xe/Xem")

        QTimer.singleShot(0, lambda: save_with_pronouns("She/Her"))
        dialog.edit_button.click()
        [edited] = self.library.list()
        self.assertEqual(edited.profile["pronouns"], "She/Her")
        self.assertEqual(
            editor_states,
            [("They/Them", False, ""), True, ("Custom", True, "Xe/Xem"), False],
        )

    def test_chooser_buttons_fit_their_labels_at_supported_font_sizes(self):
        self.library.save(self.profile)
        font_property = "ai_adventure_ui_font_size"
        self.addCleanup(self.app.setProperty, font_property, self.app.property(font_property))

        for size in (DEFAULT_UI_FONT_SIZE, MAX_UI_FONT_SIZE):
            with self.subTest(font_size=size):
                self.app.setProperty(font_property, size)
                wizard = NewGameWizard(tts_enabled=False, saved_character_library=self.library)
                dialog = SavedCharactersDialog(self.library, wizard, choose=True)
                dialog.show()
                dialog.resize(dialog.minimumSize())

                for button in dialog.findChildren(QPushButton):
                    with self.subTest(button=button.text()):
                        option = QStyleOptionButton()
                        button.initStyleOption(option)
                        text_area = button.style().subElementRect(
                            QStyle.SubElement.SE_PushButtonContents, option, button,
                        )
                        metrics = button.fontMetrics()
                        self.assertTrue(dialog.rect().contains(button.geometry()))
                        self.assertGreaterEqual(text_area.width(), metrics.horizontalAdvance(button.text()))
                        self.assertGreaterEqual(text_area.height(), metrics.height())
                dialog.close()

    def test_manager_uploads_portable_portrait_for_character_without_appearance(self):
        source = self.root / "portrait.jpg"
        Image.new("RGB", (64, 48), (40, 94, 151)).save(source)
        manager = SavedCharactersDialog(self.library)
        preview_sizes = []

        def edit_profile():
            editor = self.app.activeModalWidget()
            try:
                editor.findChild(QLineEdit, "name").setText("Rowan")
                editor.findChild(QPushButton, "upload_portrait").click()
                preview = editor.findChild(QLabel, "portrait_preview")
                preview_sizes.append(preview.pixmap().size().toTuple())
                editor.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
            finally:
                if editor.isVisible():
                    editor.reject()

        with patch("ai_adventure.ui.saved_characters.ImageFileDialog") as picker:
            picker.return_value.exec.return_value = 1
            picker.return_value.selectedFiles.return_value = [str(source)]
            QTimer.singleShot(0, edit_profile)
            manager.create_button.click()

        [saved] = self.library.list()
        self.assertEqual(preview_sizes, [(112, 84)])
        self.assertEqual(saved.portrait["display_name"], "Rowan")
        self.assertEqual(saved.profile["appearance"], "")
        source.unlink()

        wizard = NewGameWizard(tts_enabled=False, saved_character_library=self.library)
        QTimer.singleShot(0, lambda: self.app.activeModalWidget().use_button.click())
        wizard.load_saved_character_button.click()
        setup = wizard.build_setup()
        self.assertEqual(setup["template_images"], [saved.portrait])
        repository = NewGameService.create_repository(self.root / "saves", setup)
        coordinator = _VisualAssetCoordinator(images_dir=self.root / "cache", api_key_path=self.root / "key", enabled=True)
        requests = coordinator.prepare_initial_batch(repository)
        portrait = next(request for request in requests if request.subject_type == "player")
        self.assertNotIn(portrait, coordinator.restore_initial_template_images(repository, requests, setup["template_images"]))
        with Image.open(AssetGenerationService.target_path(repository, portrait, coordinator.images_dir)) as image:
            self.assertEqual(image.size, (64, 48))
