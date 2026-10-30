from pathlib import Path
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox

from ai_adventure.ui.image_file_dialog import ImageFileDialog, image_picker_directory
from tests.qt_test_case import QtTestCase


class ImageFileDialogTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_paste_folder_and_file_paths_and_maximize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'Images with spaces'
            folder.mkdir()
            image = folder / 'portrait.png'
            image.write_bytes(b'placeholder')
            dialog = ImageFileDialog(directory=str(root))
            try:
                dialog.show()
                self.app.processEvents()
                address = dialog.findChild(QComboBox, 'lookInCombo')
                self.assertTrue(address.isEditable())
                editor = address.lineEdit()
                editor.setText(str(folder))
                QTest.keyClick(editor, Qt.Key.Key_Return)
                self.app.processEvents()
                self.assertEqual(Path(dialog.directory().absolutePath()), folder)
                self.assertTrue(dialog.isVisible())
                editor.setText(str(image))
                QTest.keyClick(editor, Qt.Key.Key_Return)
                self.app.processEvents()
                self.assertEqual(Path(dialog.selectedFiles()[0]), image)
                self.assertTrue(dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)
                dialog.showMaximized()
                self.assertTrue(dialog.isMaximized())
            finally:
                dialog.close()
                dialog.deleteLater()
                self.app.processEvents()

    def test_initial_directory_prefers_save_images_then_image_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / 'images'
            images.mkdir()
            save = root / 'saves' / 'Adventure'
            self.assertEqual(Path(image_picker_directory(images, save)), images)
            (images / 'Adventure').mkdir()
            self.assertEqual(Path(image_picker_directory(images, save)), images / 'Adventure')

    def test_typed_address_navigates_with_enter_and_open(self):
        for action in (Qt.Key.Key_Return, Qt.Key.Key_Enter, 'open'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                folder = root / 'Images with spaces'
                folder.mkdir()
                dialog = ImageFileDialog(directory=str(root))
                try:
                    dialog.show()
                    self.app.processEvents()
                    editor = dialog.findChild(QComboBox, 'lookInCombo').lineEdit()
                    editor.setFocus()
                    editor.selectAll()
                    QTest.keyClicks(editor, '"Images with spaces"')
                    if action == 'open':
                        button = dialog.findChild(QDialogButtonBox, 'buttonBox').button(
                            QDialogButtonBox.StandardButton.Open)
                        self.assertTrue(button.isEnabled())
                        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    else:
                        QTest.keyClick(editor, action)
                    self.app.processEvents()
                    self.assertEqual(Path(dialog.directory().absolutePath()), folder)
                    self.assertEqual(Path(editor.text()), folder)
                    self.assertTrue(dialog.isVisible())
                    self.assertIsNone(dialog._pending_address)
                finally:
                    dialog.close()
                    dialog.deleteLater()
                    self.app.processEvents()

    def test_open_uses_typed_image_path_before_existing_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_image = root / 'old.png'
            old_image.touch()
            folder = root / 'other'
            folder.mkdir()
            image = folder / 'new.png'
            image.touch()
            dialog = ImageFileDialog(directory=str(root))
            try:
                dialog.show()
                dialog.selectFile(str(old_image))
                self.app.processEvents()
                editor = dialog.findChild(QComboBox, 'lookInCombo').lineEdit()
                editor.setFocus()
                editor.selectAll()
                QTest.keyClicks(editor, str(image))
                button = dialog.findChild(QDialogButtonBox, 'buttonBox').button(
                    QDialogButtonBox.StandardButton.Open)
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                self.assertTrue(dialog.isVisible())
                self.assertEqual(Path(dialog.selectedFiles()[0]), image)
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
            finally:
                dialog.close()
                dialog.deleteLater()
                self.app.processEvents()
