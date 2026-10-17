"""Viewport bounds for input text and rendered Story messages."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTextEdit

from ai_adventure.ui.widgets.inputs import install_plain_text_paste
from ai_adventure.ui.primitives import _set_markdown_text


class TextWrappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_plain_text_paste(cls.app)

    def assert_wrapped(self, editor):
        self.app.processEvents()
        block = editor.document().begin()
        line_count = 0
        while block.isValid():
            layout = block.layout()
            line_count += layout.lineCount()
            for index in range(layout.lineCount()):
                self.assertLessEqual(layout.lineAt(index).naturalTextWidth(), editor.viewport().width() + 1)
            self.assertFalse(block.blockFormat().nonBreakableLines())
            block = block.next()
        self.assertGreater(line_count, 1)
        self.assertEqual(editor.horizontalScrollBar().maximum(), 0)

    def test_dynamic_multiline_inputs_wrap_words_and_long_tokens_with_vertical_scrolling(self):
        for editor_type in (QTextEdit, QPlainTextEdit):
            with self.subTest(editor=editor_type.__name__):
                editor = editor_type()
                editor.resize(260, 100)
                editor.show()
                text = 'World details with spaces. ' * 30 + 'X' * 400
                editor.setPlainText(text)
                self.assert_wrapped(editor)
                self.assertGreater(editor.verticalScrollBar().maximum(), 0)
                editor.resize(180, 100)
                self.assert_wrapped(editor)
                self.assertEqual(editor.toPlainText(), text)
                editor.close()

    def test_preformatted_content_cannot_override_viewport_wrapping(self):
        editor = QTextEdit()
        editor.resize(240, 100)
        editor.show()
        editor.setHtml('<pre>' + 'Old template details ' * 40 + '</pre>')
        self.assert_wrapped(editor)
        self.assertFalse(editor.acceptRichText())
        self.assertGreater(editor.verticalScrollBar().maximum(), 0)
        editor.close()

    def test_story_markdown_code_blocks_wrap_without_changing_message_text(self):
        from ai_adventure.ui.screens.story import StoryScreen

        screen = StoryScreen()
        text = 'X' * 400
        for role in ("player", "story"):
            with self.subTest(role=role):
                bubble = screen._conversation_bubble(role, "live_game", '```json\n' + text + '\n```')
                bubble.resize(300, 100)
                bubble.show()
                editor = bubble.findChild(QTextEdit)
                self.assert_wrapped(editor)
                self.assertIn(text, editor.toPlainText())
                _set_markdown_text(editor, '```json\n' + 'Y' * 500 + '\n```', preserve_blank_lines=True)
                self.assert_wrapped(editor)
                bubble.close()
        screen.close()


if __name__ == "__main__":
    unittest.main()
