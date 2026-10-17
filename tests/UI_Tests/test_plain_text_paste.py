"""Application-wide paste without formatting, including dynamic editors."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit

from ai_adventure.ui.widgets.inputs import install_plain_text_paste


class PlainTextPasteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_plain_text_paste(cls.app)

    @classmethod
    def tearDownClass(cls):
        # The offscreen clipboard is process-local; release its MIME objects
        # before Qt and Python shut down in different orders.
        if cls.app.platformName() == "offscreen":
            cls.app.clipboard().clear()

    def setUp(self):
        self.clipboard = self.app.clipboard()
        original = self.clipboard.mimeData()
        self.original = QMimeData()
        for format_name in original.formats() if original is not None else ():
            self.original.setData(format_name, original.data(format_name))
        mime = QMimeData()
        mime.setText('Literal "JSON": {value}\nNext line')
        mime.setHtml('<pre style="color:red;background:black"><b>HTML payload</b></pre>')
        self.clipboard.setMimeData(mime)

    def tearDown(self):
        self.clipboard.setMimeData(self.original)

    def paste(self, editor):
        editor.show()
        editor.setFocus()
        self.app.processEvents()
        QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)

    def test_multiline_editors_use_clipboard_text_replace_selection_and_undo(self):
        for editor_type in (QTextEdit, QPlainTextEdit):
            with self.subTest(editor=editor_type.__name__):
                editor = editor_type()
                editor.setPlainText("Replace me")
                editor.selectAll()
                self.paste(editor)
                self.assertEqual(editor.toPlainText(), 'Literal "JSON": {value}\nNext line')
                if isinstance(editor, QTextEdit):
                    cursor = editor.textCursor()
                    cursor.setPosition(2)
                    self.assertEqual(cursor.charFormat().font(), editor.font())
                    self.assertFalse(cursor.charFormat().background().isOpaque())
                editor.undo()
                self.assertEqual(editor.toPlainText(), "Replace me")
                editor.close()

    def test_line_and_editable_combo_inputs_respect_selection_and_readonly(self):
        combo = QComboBox()
        combo.setEditable(True)
        for editor in (QLineEdit(), combo.lineEdit()):
            editor.setText("Before")
            editor.selectAll()
            self.paste(editor)
            self.assertIn('Literal "JSON": {value}', editor.text())
            self.assertNotIn("HTML payload", editor.text())
            editor.undo()
            self.assertEqual(editor.text(), "Before")
            editor.setReadOnly(True)
            self.paste(editor)
            self.assertEqual(editor.text(), "Before")
            editor.close()
        combo.close()

    def test_empty_clipboard_does_not_erase_selection_and_install_is_idempotent(self):
        handler = self.app._plain_text_paste_filter
        install_plain_text_paste(self.app)
        self.assertIs(handler, self.app._plain_text_paste_filter)
        self.clipboard.clear()
        editor = QTextEdit()
        editor.setPlainText("Keep me")
        editor.selectAll()
        self.paste(editor)
        self.assertEqual(editor.toPlainText(), "Keep me")
        editor.close()


if __name__ == "__main__":
    unittest.main()
