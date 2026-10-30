from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QDialog

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.inventory import InventoryScreen, InventoryMoveDialog
from tests.qt_test_case import QtTestCase


class InventoryBatchMoveUiTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = SaveRepository.create_new_save(Path(temporary.name), 'Batch move UI')
        self.repository.set_state_value('location', 'Workshop')
        self.repository.set_travel_locations([{'name': 'Workshop', 'location_scope': 'specific'}])
        self.repository.replace_inventory_items([
            {'name': 'Pack', 'category': 'Container', 'quantity': 1, 'storage_location': 'Workshop',
             'container': {'is_open': True, 'contents_initialized': True,
                           'contents': {'items': [], 'currency_base_units': 0}}},
            {'name': 'Cloth', 'quantity': 2, 'storage_location': 'Workshop'},
            {'name': 'Needles', 'quantity': 3, 'storage_location': 'actively_carried'},
            {'name': 'Forge', 'quantity': 1, 'storage_location': 'Workshop', 'moveable': False},
            {'name': 'Remote Tool', 'quantity': 1, 'storage_location': 'Warehouse'},
        ])
        self.screen = InventoryScreen()
        self.screen.set_repository(self.repository)
        self.screen.on_repository_changed = Mock()

    def item(self, name):
        return next(item for item in self.repository.list_inventory_items() if item['name'] == name)

    def checkbox(self, name):
        item_id = str(self.item(name)['id'])
        return next(panel.item_checkboxes[item_id] for panel in self.screen.location_panels
                    if item_id in panel.item_checkboxes)

    def test_select_across_panels_and_move_whole_stacks_together(self):
        self.assertFalse(self.screen.move_selected_button.isEnabled())
        self.checkbox('Cloth').click()
        self.checkbox('Needles').click()
        self.assertEqual(self.screen.selection_label.text(), '2 selected')
        pack_id = str(self.item('Pack')['id'])

        def choose(dialog):
            dialog.destination_combo.setCurrentIndex(dialog.destination_combo.findData(pack_id))
            return QDialog.DialogCode.Accepted

        with patch.object(InventoryMoveDialog, 'exec', choose):
            self.screen.move_selected_button.click()
        for name in ('Cloth', 'Needles'):
            self.assertEqual(self.item(name)['metadata']['container_id'], pack_id)
        self.assertFalse(self.screen.move_selected_button.isEnabled())
        self.screen.on_repository_changed.assert_called_once_with(self.screen)

    def test_sort_and_refresh_preserve_selected_ids_and_clear_updates_checks(self):
        self.checkbox('Cloth').click()
        panel = next(panel for panel in self.screen.location_panels if panel.location == 'Workshop')
        panel.sort_direction_combo.setCurrentIndex(1)
        self.assertTrue(self.checkbox('Cloth').isChecked())
        self.screen.refresh()
        self.assertTrue(self.checkbox('Cloth').isChecked())
        self.screen.clear_selection_button.click()
        self.assertFalse(self.checkbox('Cloth').isChecked())
        self.assertEqual(self.screen.selection_label.text(), '0 selected')

    def test_cancel_preserves_selection_and_inventory(self):
        self.checkbox('Cloth').click()
        self.checkbox('Needles').click()
        before = self.repository.list_inventory_items()
        with patch.object(InventoryMoveDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
            self.screen.move_selected_button.click()
        self.assertEqual(self.repository.list_inventory_items(), before)
        self.assertEqual(self.screen.selection_label.text(), '2 selected')
        self.screen.on_repository_changed.assert_not_called()

    def test_select_all_skips_fixed_items_and_inaccessible_rows(self):
        panel = next(panel for panel in self.screen.location_panels if panel.location == 'Workshop')
        panel.select_all_button.click()
        self.assertTrue(self.checkbox('Cloth').isChecked())
        self.assertTrue(self.checkbox('Pack').isChecked())
        self.assertFalse(self.checkbox('Forge').isEnabled())
        self.assertFalse(self.checkbox('Remote Tool').isEnabled())
        self.assertFalse(self.checkbox('Forge').isChecked())
        self.assertFalse(self.checkbox('Remote Tool').isChecked())

    def test_travel_prunes_newly_inaccessible_selection_and_save_unbind_clears_it(self):
        self.checkbox('Cloth').click()
        self.checkbox('Needles').click()
        self.repository.set_state_value('location', 'Elsewhere')
        self.screen.refresh()
        self.assertFalse(self.checkbox('Cloth').isChecked())
        self.assertTrue(self.checkbox('Needles').isChecked())
        self.assertEqual(self.screen.selection_label.text(), '1 selected')
        self.screen.set_repository(None)
        self.assertEqual(self.screen.selection_label.text(), '0 selected')
        self.assertFalse(self.screen.move_selected_button.isEnabled())
