import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWizard, QDialog

from ai_adventure.core.state_manager import StateManager
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.stats import ATTRIBUTES
from ai_adventure.ui.screens.stats import StatsScreen
from ai_adventure.ui.skill_details import SkillDetailsDialog
from ai_adventure.ui.wizards.new_game import NewGameWizard
from ai_adventure.visual_assets import build_visual_asset_requests
from tests.qt_test_case import QtTestCase


class SkillDetailsTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_save_as_is_separate_from_save_and_save_exit(self):
        save, save_as = Mock(return_value=True), Mock(return_value=True)
        wizard = NewGameWizard(tts_enabled=False, on_save_and_exit=save, on_save_as=save_as)
        self.addCleanup(wizard.deleteLater)
        self.addCleanup(wizard.close)
        wizard.button(QWizard.WizardButton.CustomButton3).click()
        self.assertEqual(wizard.button(QWizard.WizardButton.CustomButton3).text(), "Save As")
        save_as.assert_called_once()
        save.assert_not_called()
        wizard.button(QWizard.WizardButton.CustomButton2).click()
        save.assert_called_once()
        with patch.object(wizard, "reject") as close:
            wizard.button(QWizard.WizardButton.CustomButton1).click()
            close.assert_called_once()
        self.assertEqual(save.call_count, 2)

    def test_custom_duplicate_names_block_progress_save_and_finish_until_corrected(self):
        save = Mock(return_value=True)
        save_as = Mock(return_value=True)
        wizard = NewGameWizard(tts_enabled=False, on_save_and_exit=save, on_save_as=save_as)
        self.addCleanup(wizard.deleteLater)
        self.addCleanup(wizard.close)
        wizard.skill_preset_combo.setCurrentIndex(wizard.skill_preset_combo.findData("custom"))
        wizard._add_starting_skill_row(4, "Star Weaving")
        wizard._add_starting_skill_row(2)
        first, second = [skill for _level, skill, _description in wizard.skill_inputs]
        second.combo.setCurrentIndex(second.combo.findData(second.CUSTOM_DATA))
        QTest.keyClicks(second.combo.lineEdit(), "  star   WEAVING  ")
        self.assertEqual(wizard._duplicate_starting_skill_names(), ["Star Weaving"])
        self.assertFalse(wizard.stats_page.isComplete())
        self.assertFalse(wizard.skill_name_error.isHidden())
        self.assertIn("Star Weaving", wizard.skill_name_error.text())
        self.assertTrue(first.combo.toolTip())
        self.assertTrue(second.combo.toolTip())
        with patch("ai_adventure.ui.wizards.new_game.QMessageBox.warning") as warning:
            wizard.button(QWizard.WizardButton.CustomButton2).click()
            wizard.button(QWizard.WizardButton.CustomButton1).click()
            wizard.button(QWizard.WizardButton.CustomButton3).click()
            wizard.accept()
        self.assertEqual(warning.call_count, 4)
        save.assert_not_called()
        save_as.assert_not_called()
        self.assertNotEqual(wizard.result(), QDialog.DialogCode.Accepted)
        # A longer name sharing the prefix must remain freely typeable.
        second.combo.lineEdit().selectAll()
        QTest.keyClicks(second.combo.lineEdit(), "Star Weaving Mastery")
        self.assertTrue(wizard.stats_page.isComplete())
        self.assertTrue(wizard.skill_name_error.isHidden())
        self.assertFalse(first.combo.toolTip())
        wizard.button(QWizard.WizardButton.CustomButton2).click()
        save.assert_called_once()
        wizard.accept()
        self.assertEqual(wizard.result(), QDialog.DialogCode.Accepted)

    def test_duplicate_names_within_one_level_clear_when_a_row_is_removed(self):
        wizard = NewGameWizard(tts_enabled=False)
        self.addCleanup(wizard.deleteLater)
        self.addCleanup(wizard.close)
        wizard.skill_preset_combo.setCurrentIndex(wizard.skill_preset_combo.findData("custom"))
        wizard._add_starting_skill_row(2, "Sewing")
        wizard._add_starting_skill_row(2, "Sewing")
        self.assertFalse(wizard.stats_page.isComplete())
        wizard.skill_tables[2].removeRow(1)
        wizard._sync_starting_skill_inputs()
        self.assertTrue(wizard.stats_page.isComplete())
        self.assertTrue(wizard.skill_name_error.isHidden())

    def test_generated_subjects_get_transparency_but_locations_keep_backgrounds(self):
        from io import BytesIO
        from PIL import Image, ImageDraw
        from ai_adventure.ui.workers.visual_assets import _VisualAssetCoordinator
        from ai_adventure.visual_assets import VisualAssetRequest, save_relative_image_filename

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = SaveRepository.create_new_save(root / "saves", "Transparent icon")
            for subject_type in ("skill", "inventory", "player", "npc", "bestiary", "location", "region_map"):
                with self.subTest(subject_type=subject_type):
                    cutout = subject_type not in {"location", "region_map"}
                    request = VisualAssetRequest(subject_type, "subject", "Subject", "Visible details.")
                    filename = save_relative_image_filename(repo, request)
                    repo.ensure_visual_asset(
                        asset_id=request.asset_id, subject_type=subject_type, subject_key="subject",
                        display_name=request.display_name, descriptor_hash=request.descriptor_hash,
                        filename=filename, prompt=request.prompt, model="test",
                    )
                    source = Image.new("RGB", (64, 64), (255, 0, 255))
                    ImageDraw.Draw(source).ellipse((16, 16, 48, 48), fill=(80, 80, 80))
                    data = BytesIO()
                    source.save(data, format="PNG")
                    coordinator = _VisualAssetCoordinator(
                        images_dir=root / "images", api_key_path=root / "key", enabled=True,
                    )
                    self.addCleanup(coordinator.deleteLater)
                    coordinator._handle_completed(repo, request, data.getvalue(), "image/png")
                    self.assertEqual(repo.get_visual_asset_by_id(request.asset_id)["status"], "ready")
                    with Image.open(root / "images" / filename) as saved:
                        self.assertEqual(saved.mode, "RGBA" if cutout else "RGB")
                        self.assertEqual(saved.convert("RGBA").getpixel((0, 0))[3], 0 if cutout else 255)
                        self.assertEqual(saved.convert("RGBA").getpixel((512, 512))[3], 255)

    def test_wizard_skill_choices_exclude_names_used_at_other_levels(self):
        wizard = NewGameWizard(tts_enabled=False, skill_catalog=[
            {"name": "Accounting", "description": "Manage accounts."},
            {"name": "Geology", "description": "Study rocks."},
        ])
        self.addCleanup(wizard.close)
        wizard.skill_preset_combo.setCurrentIndex(wizard.skill_preset_combo.findData("custom"))
        wizard._add_starting_skill_row(3, "Accounting")
        wizard._add_starting_skill_row(2)
        first, second = [skill for _level, skill, _description in wizard.skill_inputs]
        accounting = next(index for index in range(2, second.combo.count())
                          if second.combo.itemData(index)[0] == "Accounting")
        self.assertTrue(second.combo.view().isRowHidden(accounting))
        self.assertFalse(second.combo.model().item(accounting).isEnabled())
        self.assertEqual(first.text(), "Accounting")

        first.setText("")
        self.assertFalse(second.combo.view().isRowHidden(accounting))
        self.assertTrue(second.combo.model().item(accounting).isEnabled())
        first.setText("  accounting  ")
        self.assertTrue(second.combo.view().isRowHidden(accounting))
        first.combo.setCurrentIndex(first.combo.findData(first.CUSTOM_DATA))
        first.combo.setEditText("ACCOUNTING")
        self.assertTrue(second.combo.view().isRowHidden(accounting))
        first.combo.setEditText("Custom trade")
        self.assertFalse(second.combo.view().isRowHidden(accounting))
        first.combo.setEditText("Accounting")
        wizard.skill_tables[3].removeRow(0)
        wizard._sync_starting_skill_inputs()
        self.assertFalse(second.combo.view().isRowHidden(accounting))

    def test_wizard_catalog_stat_selection_template_and_save_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wizard = NewGameWizard(
                tts_enabled=False, api_key_path=root / "key.json",
                terms_acceptance_path=root / "terms.json",
                custom_voice_storage_path=root / "voices.json",
                skill_catalog=[{"name": "Weather Reading", "description": "Read weather patterns.", "attribute": "Wisdom"}],
            )
            wizard.skill_preset_combo.setCurrentIndex(wizard.skill_preset_combo.findData("custom"))
            wizard._add_starting_skill_row(2)
            _, skill, _ = wizard.skill_inputs[0]
            self.assertEqual(wizard.skill_tables[2].horizontalHeaderItem(3).text(), "Related Stat")
            self.assertEqual([skill.attribute_input.itemText(i) for i in range(skill.attribute_input.count())], ["Let the A.I. Decide", *ATTRIBUTES])
            self.assertEqual(skill.attribute_input.currentText(), "Let the A.I. Decide")
            self.assertFalse(skill.attribute_input.isEditable())
            skill.setText("Stealth")
            self.assertEqual(skill.attribute_input.currentText(), "Let the A.I. Decide")
            skill.setText("Weather Reading")
            self.assertEqual(skill.attribute_input.currentText(), "Wisdom")
            skill.attribute_input.setCurrentText("Constitution")
            template = wizard.build_template_setup()
            wizard.load_setup(template)
            self.assertEqual(wizard.skill_inputs[0][1].attribute_input.currentText(), "Constitution")
            repository = SaveRepository.create_new_save(root / "saves", "Skills")
            repository.replace_skills(wizard.build_template_setup()["skills"])
            reopened = SaveRepository(repository.db_path)
            self.assertEqual(reopened.get_skill("Weather Reading")["attribute"], "Constitution")
            self.assertEqual(StateManager(reopened).load_state().skills.skills[0].to_dict()["attribute"], "Constitution")

    def test_ai_related_stat_survives_template_and_packet_until_generation(self):
        from ai_adventure.new_game_setup import build_new_game_setup_packet
        from ai_adventure.new_game_templates import save_new_game_template, load_new_game_templates
        from ai_adventure.application.new_game_service import _finalized_skills_for_save

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wizard = NewGameWizard(tts_enabled=False)
            self.addCleanup(wizard.deleteLater)
            self.addCleanup(wizard.close)
            self.assertTrue(wizard.skill_inputs)
            self.assertTrue(all(skill.attribute_input.currentText() == "Let the A.I. Decide"
                                for _level, skill, _description in wizard.skill_inputs))
            wizard.skill_preset_combo.setCurrentIndex(wizard.skill_preset_combo.findData("custom"))
            wizard._add_starting_skill_row(2, "Weaving", "Work a loom.")
            wizard._add_starting_skill_row(1, "Herb Lore", "Identify herbs.", "Wisdom")
            path = root / "templates.json"
            self.assertTrue(save_new_game_template(path, wizard.build_template_setup(), template_name="Skills"))
            setup = load_new_game_templates(path)[0].setup
            wizard.load_setup(setup)
            stats = {skill.text(): skill.attribute_input.currentText()
                     for _level, skill, _description in wizard.skill_inputs}
            self.assertEqual(stats, {"Weaving": "Let the A.I. Decide", "Herb Lore": "Wisdom"})
            packet = build_new_game_setup_packet(wizard.build_setup())
            choices = {skill["name"]: skill["attribute"] for skill in packet["setup"]["skills"]}
            self.assertEqual(choices, {"Weaving": "", "Herb Lore": "Wisdom"})
            finalized = _finalized_skills_for_save([
                {"name": "Weaving", "description": "Work a loom.", "level": 2, "attribute": "Dexterity"},
                {"name": "Herb Lore", "description": "Identify herbs.", "level": 1, "attribute": "Intelligence"},
            ], packet["setup"]["skills"])
            repo = SaveRepository.create_new_save(root / "saves", "Skill stats")
            repo.replace_skills(finalized)
            reopened = SaveRepository(repo.db_path)
            self.assertEqual(reopened.get_skill("Weaving")["attribute"], "Dexterity")
            self.assertEqual(reopened.get_skill("Herb Lore")["attribute"], "Wisdom")

    def test_sorted_skill_click_shows_stat_bonus_and_icon_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Skill details")
            repository.replace_skills([
                {"name": "Tracking", "description": "Follow trails.", "level": 2, "attribute": "Wisdom"},
                {"name": "Accounting", "description": "Manage ledgers.", "level": 3, "attribute": "Intelligence"},
            ])
            screen = StatsScreen()
            screen.set_repository(repository)
            self.assertEqual(
                [screen.skills_table.horizontalHeaderItem(i).text() for i in range(screen.skills_table.columnCount())],
                ["Skill", "Related Stat", "Training", "XP Progress", "Description"],
            )
            screen.resize(1000, 600)
            screen.show()
            self.app.processEvents()
            self.assertEqual(screen.skills_table.item(0, 1).text(), "Intelligence")
            self.assertFalse(screen.skills_table.item(0, 0).icon().isNull())
            self.assertEqual(screen.skills_table.cellWidget(0, 3).objectName(), "skillXpProgressBar")
            key = str(repository.get_skill("Accounting")["id"]).casefold()
            seen = []

            def inspect(dialog):
                seen.append(dialog.windowTitle())
                text = [label.text() for label in dialog.findChildren(QLabel)]
                self.assertIn("Intelligence", text)
                self.assertIn("Manage ledgers.", text)
                self.assertTrue(any("training =" in value for value in text))
                dialog.findChild(QPushButton, "skillSelectIconButton").click()
                dialog.findChild(QPushButton, "skillGenerateIconButton").click()
                return 0

            with patch.object(SkillDetailsDialog, "exec", inspect), \
                 patch.object(screen, "choose_visual_asset") as upload, \
                 patch.object(screen, "create_visual_asset") as generate:
                cell = screen.skills_table.visualItemRect(screen.skills_table.item(0, 0))
                QTest.mouseClick(screen.skills_table.viewport(), Qt.MouseButton.LeftButton, pos=cell.center())
            self.assertEqual(seen, ["Accounting"])
            upload.assert_called_once_with("skill", key)
            generate.assert_called_once_with("skill", key)
            repository.set_setting("images.enabled", False)
            with patch.object(SkillDetailsDialog, "exec", lambda dialog: self.assertIsNone(dialog.findChild(QPushButton, "skillGenerateIconButton"))):
                screen._open_skill_details(0, 0)

    def test_skill_icon_request_can_be_persisted_and_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SaveRepository.create_new_save(Path(directory), "Icons")
            repository.upsert_skill("Accounting", "Manage ledgers.", 1, attribute="Intelligence")
            request = next(r for r in build_visual_asset_requests(repository) if r.subject_type == "skill")
            self.assertEqual(request.aspect_ratio, "1:1")
            self.assertIn("32 by 32", request.prompt)
            repository.ensure_visual_asset(
                asset_id=request.asset_id, subject_type="skill", subject_key=request.subject_key,
                display_name=request.display_name, descriptor_hash=request.descriptor_hash,
                filename="skills/accounting.png", prompt=request.prompt, model="test", ready=True,
            )
            reopened = SaveRepository(repository.db_path)
            self.assertEqual(reopened.get_visual_asset("skill", request.subject_key)["filename"], "skills/accounting.png")
            screen = StatsScreen()
            image_path = Path(directory) / "icon.png"
            image = QPixmap(64, 64)
            image.fill(Qt.GlobalColor.red)
            image.save(str(image_path))
            with patch.object(screen, "visual_asset_path", return_value=image_path):
                screen.set_repository(reopened)
            self.assertFalse(screen.skills_table.item(0, 0).icon().isNull())
