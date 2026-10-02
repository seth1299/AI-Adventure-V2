import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QWidget
from ai_adventure.ui.game_shell import GameShell
from ai_adventure.ui.screens.main_menu import MainMenuScreen
from ai_adventure.ui.themes_audio import apply_application_theme
from ai_adventure.ui.widgets.containers import CurrentPageStackedWidget


class WindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def settle(self):
        for _ in range(4):
            self.app.processEvents()

    def test_hidden_game_pages_do_not_resize_or_displace_main_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            window = QMainWindow()
            stack = CurrentPageStackedWidget()
            window.setCentralWidget(stack)
            menu = MainMenuScreen(Path(directory), lambda: None, lambda _path: None, lambda: None, lambda: None)
            game = GameShell(on_return_to_menu=lambda: None, tts_enabled=True, ai_enabled=False)
            stack.addWidget(menu)
            stack.addWidget(game)
            stack.setCurrentWidget(menu)
            apply_application_theme("Dark", {"font_size": 11})
            window.resize(1100, 750)
            window.show()
            self.settle()
            try:
                for family, size in [("Comic Sans MS", 14), ("Arial", 24), ("", 11), ("Comic Sans MS", 14), ("", 11)]:
                    with self.subTest(family=family, size=size):
                        apply_application_theme("Dark", {"font_family": family, "font_size": size})
                        self.settle()
                        self.assertEqual(window.size().toTuple(), (1100, 750))
                        content = menu.layout().itemAt(1).layout()
                        top = content.itemAt(0).geometry().height()
                        bottom = content.itemAt(content.count()-1).geometry().height()
                        self.assertLessEqual(abs(top-bottom), 1)
                        button = next(button for button in menu.findChildren(QPushButton) if button.text() == "New Game")
                        self.assertLessEqual(abs(button.geometry().center().x()-menu.rect().center().x()), 1)
                        self.assertLess(button.width(), menu.width()*0.75)
                        self.assertEqual(menu.geometry().bottom(), stack.rect().bottom())
                # Selecting a page must still honor that visible page's constraints.
                stack.setCurrentWidget(game)
                self.settle()
                self.assertGreaterEqual(stack.minimumSizeHint().height(), game.minimumSizeHint().height())
                stack.setCurrentWidget(menu)
                window.resize(1100, 750)
                self.settle()
                self.assertEqual(window.size().toTuple(), (1100, 750))
            finally:
                window.close()
                window.deleteLater()
                apply_application_theme("Light", {"font_size": 10})
                self.settle()

    def test_empty_stack_and_explicit_active_page_minimums(self):
        stack = CurrentPageStackedWidget()
        self.assertEqual(stack.minimumSizeHint().toTuple(), (0, 0))
        small = QWidget()
        small.setMinimumSize(200, 150)
        large = QWidget()
        large.setMinimumSize(1600, 1400)
        stack.addWidget(small)
        stack.addWidget(large)
        stack.show()
        self.settle()
        self.assertEqual(stack.minimumSizeHint().toTuple(), (200, 150))
        stack.setCurrentWidget(large)
        self.settle()
        self.assertEqual(stack.minimumSizeHint().toTuple(), (1600, 1400))
        stack.close()
        stack.deleteLater()


if __name__ == "__main__":
    unittest.main()
