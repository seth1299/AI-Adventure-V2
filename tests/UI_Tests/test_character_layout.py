from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from ai_adventure.ui.screens.character import CharacterScreen
from tests.qt_test_case import QtTestCase


class CharacterLayoutTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def settle(self):
        for _ in range(8):
            self.app.processEvents()

    def test_portrait_and_profile_start_at_top_and_adapt_to_window(self):
        screen = CharacterScreen()
        screen.backstory_input.setPlainText('A short character history.')
        screen._sync_profile_preview(has_portrait=True)
        screen.portrait_group.show()
        screen.resize(1500, 900)
        screen.show()
        self.settle()
        portrait = screen.portrait_group.mapTo(screen, QPoint())
        title = screen.profile_title_label.mapTo(screen, QPoint())
        self.assertLess(portrait.x(), 30)
        self.assertLess(portrait.y(), 30)
        self.assertGreater(title.x(), portrait.x() + screen.portrait_group.width())
        self.assertLess(title.y(), 50)
        self.assertLess(screen.profile_backstory_display.height(), 60)

        screen.resize(850, 700)
        self.settle()
        title = screen.profile_title_label.mapTo(screen, QPoint())
        portrait = screen.portrait_group.mapTo(screen, QPoint())
        self.assertGreater(title.y(), portrait.y() + screen.portrait_group.height())
        self.assertEqual(screen.sheet_scroll.horizontalScrollBar().maximum(), 0)

    def test_text_height_tracks_wrapping_content_and_edit_mode(self):
        screen = CharacterScreen()
        screen.resize(1400, 900)
        screen.show()
        display = screen.profile_backstory_display
        display.setMarkdown('Short history.')
        self.settle()
        short_height = display.height()
        display.setMarkdown('A long history with many adventures. ' * 150)
        self.settle()
        self.assertGreater(display.height(), short_height * 3)
        self.assertGreaterEqual(display.viewport().height(), display.document().size().height())
        display.setMarkdown('Short again.')
        self.settle()
        self.assertEqual(display.height(), short_height)

        screen._set_profile_editing(True)
        screen.backstory_input.setPlainText('An editable history. ' * 150)
        self.settle()
        editor = screen.backstory_input
        self.assertGreaterEqual(editor.viewport().height(), editor.document().size().height())
        self.assertGreater(screen.sheet_scroll.verticalScrollBar().maximum(), 0)
        editor.setPlainText('Short again.')
        self.settle()
        self.assertLess(editor.height(), 100)
