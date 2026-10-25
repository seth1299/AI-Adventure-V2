from pathlib import Path
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox

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
