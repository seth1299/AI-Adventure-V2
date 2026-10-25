import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

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
            self.assertEqual([skill.attribute_input.itemText(i) for i in range(skill.attribute_input.count())], list(ATTRIBUTES))
            self.assertFalse(skill.attribute_input.isEditable())
            skill.setText("Stealth")
            self.assertEqual(skill.attribute_input.currentText(), "Dexterity")
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
