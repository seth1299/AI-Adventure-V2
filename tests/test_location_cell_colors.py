"""Sublocation cells must reflect state, not row striping or focus."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ai_adventure.ui.new_game_form_helpers import (
    _append_starting_location_table_row, _sync_starting_location_parent_dropdowns,
)
from ai_adventure.ui.table_helpers import _AppTableWidget, _configure_inline_table
from ai_adventure.ui.themes_audio import (
    _dark_theme_palette, _dark_theme_stylesheet, _light_theme_palette, _light_theme_stylesheet,
)


class LocationCellColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_state_colors_remain_consistent_across_rows_focus_and_theme_changes(self):
        palette, stylesheet = self.app.palette(), self.app.styleSheet()
        table = _AppTableWidget(0, 6)
        _configure_inline_table(table, (180, 180, 140, 120, 180, 100), minimum_height=180)
        for row in range(3):
            _append_starting_location_table_row(table, {"name": str(row)}, row, lambda _: None)
        table.resize(950, 200)
        table.show()
        try:
            for theme_palette, theme_stylesheet in (
                (_dark_theme_palette(), _dark_theme_stylesheet()),
                (_light_theme_palette(), _light_theme_stylesheet()),
            ):
                self.app.setPalette(theme_palette)
                self.app.setStyleSheet(theme_stylesheet)
                for row in range(3):
                    checkbox = table.cellWidget(row, 3)
                    parent = table.cellWidget(row, 4)
                    for checked in (False, True, False):
                        checkbox.setChecked(checked)
                        _sync_starting_location_parent_dropdowns(table, [(str(i), str(i)) for i in range(3)])
                        checkbox.setFocus()
                        self.app.processEvents()
                        expected = theme_palette.base().color() if checked else QColor("#808080")
                        image = checkbox.grab().toImage()
                        self.assertEqual(image.pixelColor(image.width() - 5, image.height() // 2), expected)
                        self.assertEqual(parent.isHidden(), not checked)
                        if not checked:
                            self.assertEqual(table.item(row, 4).background().color(), expected)
                        QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=QPoint(8, checkbox.height() // 2))
                        self.app.processEvents()
                        self.assertEqual(checkbox.isChecked(), not checked)
                    # Template loaders block signals when reusing existing rows.
                    checkbox.blockSignals(True)
                    checkbox.setChecked(False)
                    checkbox.blockSignals(False)
                    _sync_starting_location_parent_dropdowns(table, [(str(i), str(i)) for i in range(3)])
                    self.assertIn("#808080", checkbox.styleSheet())
                    self.assertTrue(parent.isHidden())
        finally:
            table.close()
            self.app.setPalette(palette)
            self.app.setStyleSheet(stylesheet)


if __name__ == "__main__":
    unittest.main()

