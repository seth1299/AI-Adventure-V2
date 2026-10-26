from PySide6.QtWidgets import QApplication, QTableWidget

from ai_adventure.ui.wizards.new_game import NewGameWizard
from ai_adventure.ui.common import _append_starter_suggestion_table_row
from tests.qt_test_case import QtTestCase


class WizardClearListTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.wizard = NewGameWizard(tts_enabled=False)
        self.addCleanup(self.wizard.deleteLater)
        self.addCleanup(self.wizard.close)

    def test_every_editable_table_has_a_clear_action_and_lists_can_be_readded(self):
        w = self.wizard
        self.assertEqual(set(w.findChildren(QTableWidget)), set(w.clear_table_buttons))
        w.magic_enabled_checkbox.setChecked(True)
        tables = [
            (w.starter_item_suggestions_table,
             lambda: _append_starter_suggestion_table_row(w.starter_item_suggestions_table, "Item", "Lantern")),
            (w.starter_items_table, lambda: w._append_starter_item_row({"name": "Rope"})),
            (w.economy_examples_table, lambda: w._append_economy_example_row({"item": "Bread", "base_units": 2})),
            (w.magic_traditions_table, lambda: w._append_magic_tradition_row({"name": "Arcane"})),
            (w.starting_spell_requests_table, lambda: w._append_starting_spell_request_row({})),
            (w.starting_spells_table, lambda: w._append_starting_spell_row({"name": "Light"})),
        ]
        for table, append in tables:
            with self.subTest(table=table.horizontalHeaderItem(0).text()):
                append()
                self.assertGreater(table.rowCount(), 0)
                w.clear_table_buttons[table].click()
                self.assertEqual(table.rowCount(), 0)
                append()
                self.assertEqual(table.rowCount(), 1)

    def test_clearing_locations_and_npcs_refreshes_dependent_choices(self):
        w = self.wizard
        w._append_starting_location_row({"name": "Workshop"})
        w._append_starting_npc_row({"name": "Mira", "location": "Workshop"})
        npc_id = w._starting_npc_choices()[0]["npc_id"]
        w._append_starting_party_member_row(npc_id)
        w.clear_table_buttons[w.starting_party_table].click()
        self.assertEqual(w._starting_party_npc_ids_from_table(), [])
        w._append_starting_party_member_row(npc_id)
        w.clear_table_buttons[w.starting_npcs_table].click()
        self.assertEqual(w.starting_npcs_table.rowCount(), 0)
        self.assertEqual(w.starting_party_table.rowCount(), 0)
        self.assertFalse(w.add_starting_party_member_button.isEnabled())
        w.clear_table_buttons[w.starting_locations_table].click()
        self.assertEqual(w.starting_locations_table.rowCount(), 0)
        self.assertEqual(w.start_location_combo.count(), 1)
        self.assertFalse(w.start_location_combo.currentData())

    def test_clearing_currencies_keeps_blank_base_row_and_clears_wealth(self):
        w = self.wizard
        w._set_starting_wealth_mode("advanced")
        w._append_starting_wealth_amount_row({"denomination_value": 1, "quantity": 25})
        w.clear_table_buttons[w.starting_wealth_amounts_table].click()
        self.assertEqual(w._starting_wealth_from_controls()["amounts"], [])
        self.assertIn("0 base units", w.starting_wealth_summary_label.text())
        w._append_starting_wealth_amount_row({"denomination_value": 1, "quantity": 25})
        w.clear_table_buttons[w.currency_table].click()
        self.assertEqual(w.currency_table.rowCount(), 1)
        self.assertEqual(w.currency_table.cellWidget(0, 0).text(), "")
        self.assertEqual(w.currency_table.cellWidget(0, 2).value(), 1)
        self.assertEqual(w._starting_wealth_from_controls()["amounts"], [])
        self.assertIn("0 base units", w.starting_wealth_summary_label.text())

    def test_skill_clear_keeps_preset_slots_but_removes_custom_rows(self):
        w = self.wizard
        level, skill, description = w.skill_inputs[0]
        count = w.skill_tables[level].rowCount()
        self.assertFalse(w.skill_table_controls[level].isHidden())
        self.assertFalse(w.clear_table_buttons[w.skill_tables[level]].isHidden())
        skill.setText("Sewing")
        description.setText("Sew clothes.")
        skill.attribute_input.setCurrentText("Dexterity")
        w.clear_table_buttons[w.skill_tables[level]].click()
        self.assertEqual(w.skill_tables[level].rowCount(), count)
        self.assertEqual(skill.text(), "")
        self.assertEqual(description.text(), "")
        self.assertEqual(skill.attribute_input.currentText(), "Let the A.I. Decide")
        w.skill_preset_combo.setCurrentIndex(w.skill_preset_combo.findData("custom"))
        w._add_starting_skill_row(2, "Sewing")
        w._add_starting_skill_row(2, "Sewing")
        self.assertFalse(w.stats_page.isComplete())
        w.clear_table_buttons[w.skill_tables[2]].click()
        self.assertEqual(w.skill_inputs, [])
        self.assertTrue(w.stats_page.isComplete())
        self.assertTrue(w.skill_name_error.isHidden())
