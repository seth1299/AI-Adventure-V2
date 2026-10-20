import unittest
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from tests.qt_test_case import QtTestCase


class QtLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cleanup_deletes_hidden_test_windows_and_preserves_existing_widgets(self):
        existing = QWidget()
        created = []
        class Example(QtTestCase):
            def runTest(self):
                widget = QWidget()
                widget.close()
                created.append(widget)
        result = Example().run()
        try:
            self.assertTrue(result.wasSuccessful())
            self.assertFalse(isValid(created[0]))
            self.assertTrue(isValid(existing))
        finally:
            existing.deleteLater()
            QtTestCase._delete_test_widgets(set())

    def test_cleanup_runs_after_a_failing_test_too(self):
        created = []
        class Example(QtTestCase):
            def runTest(self):
                created.append(QWidget())
                self.fail("intentional fixture test")
        result = Example().run()
        self.assertEqual(len(result.failures), 1)
        self.assertFalse(isValid(created[0]))

    def test_pending_story_render_does_not_run_after_screen_destruction(self):
        from ai_adventure.ui.screens.story import StoryScreen
        screen = StoryScreen()
        callback = Mock()
        screen._finish_conversation_render = callback
        screen._render_conversation([])
        screen.deleteLater()
        QtTestCase._delete_test_widgets(set())
        self.app.processEvents()
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
