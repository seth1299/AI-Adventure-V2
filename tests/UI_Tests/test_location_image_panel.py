from __future__ import annotations

import os
from pathlib import Path
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.story import StoryScreen
from tests.qt_test_case import QtTestCase


class LocationImagePanelTests(QtTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repository = SaveRepository.create_new_save(self.root / "saves", "Image panel")
        self.repository.set_state_value("location", "Library")
        self.repository.set_travel_locations([
            {"location_id": "library", "name": "Library", "description": "Tall shelves."},
            {"location_id": "courtyard", "name": "Courtyard", "description": "Open sky."},
        ])
        self.images = self.root / "images"
        self.images.mkdir()
        for name in ("library", "courtyard"):
            image = QImage(560, 280, QImage.Format.Format_RGB32)
            image.fill(QColor("steelblue"))
            self.assertTrue(image.save(str(self.images / f"{name}.png")))
            self.repository.ensure_visual_asset(
                asset_id=f"image_{name}", subject_type="location", subject_key=name,
                display_name=name, descriptor_hash=name, filename=f"{name}.png",
                prompt="location", model="test", ready=True,
            )
        self.screen = self.make_screen(self.repository)
        self.panel = self.screen.location_image_panel
        self.panel._hide_timer.setInterval(40)
        self.move_outside()

    def make_screen(self, repository) -> StoryScreen:
        screen = StoryScreen()
        screen.set_visual_assets_dir(self.images)
        screen.set_repository(repository)
        screen.resize(1000, 750)
        screen.show()
        self.app.processEvents()
        return screen

    def move_outside(self) -> None:
        QTest.mouseMove(self.screen.player_input, QPoint(5, 5))
        self.app.processEvents()

    def settle(self) -> None:
        for _ in range(3):
            self.app.processEvents()

    def test_auto_hide_and_hover_reveal_preserve_reading_space(self) -> None:
        self.assertFalse(self.panel.pin_checkbox.isChecked())
        geometry = self.screen.conversation_scroll.geometry()
        QTest.qWait(80)
        self.assertFalse(self.panel.content.isVisible())
        self.assertTrue(self.panel.reveal_button.isVisible())
        QTest.mouseMove(self.panel.reveal_button)
        self.settle()
        self.assertTrue(self.panel.content.isVisible())
        self.assertEqual(self.screen.conversation_scroll.geometry(), geometry)
        QTest.mouseMove(self.screen.location_image_label)
        QTest.qWait(80)
        self.assertTrue(self.panel.content.isVisible())
        self.move_outside()
        QTest.qWait(80)
        self.assertFalse(self.panel.content.isVisible())

    def test_pin_reserves_space_and_restores_it_when_unpinned(self) -> None:
        available_height = self.screen.conversation_scroll.height()
        self.panel.pin_checkbox.click()
        self.settle()
        self.assertTrue(self.panel.content.isVisible())
        self.assertLess(self.screen.conversation_scroll.height(), available_height - 250)
        self.move_outside()
        QTest.qWait(80)
        self.assertTrue(self.panel.content.isVisible())
        self.panel.pin_checkbox.click()
        self.settle()
        self.assertEqual(self.screen.conversation_scroll.height(), available_height)

    def test_mode_persists_after_reopening_and_does_not_leak_to_other_saves(self) -> None:
        self.panel.pin_checkbox.click()
        saved = SaveRepository(self.repository.db_path)
        reopened = self.make_screen(saved)
        self.assertTrue(reopened.location_image_panel.pin_checkbox.isChecked())
        self.screen.set_repository(saved)
        self.assertTrue(self.panel.pin_checkbox.isChecked())
        other = SaveRepository.create_new_save(self.root / "other_saves", "Other")
        self.screen.set_repository(other)
        self.assertFalse(self.panel.pin_checkbox.isChecked())
        self.assertFalse(self.panel.isVisible())
        self.screen.set_repository(saved)
        self.panel.pin_checkbox.click()
        reopened.set_repository(saved)
        self.assertFalse(reopened.location_image_panel.pin_checkbox.isChecked())

    def test_refresh_stays_collapsed_and_new_location_reveals(self) -> None:
        QTest.qWait(80)
        self.assertFalse(self.panel.content.isVisible())
        self.screen.refresh()
        self.settle()
        self.assertFalse(self.panel.content.isVisible())
        self.repository.set_state_value("location", "Courtyard")
        self.screen.refresh()
        self.settle()
        self.assertTrue(self.panel.content.isVisible())
        self.assertIn("Courtyard", self.screen.location_image_label.accessibleName())

    def test_no_image_or_unloaded_save_hides_both_panel_and_handle(self) -> None:
        self.repository.set_state_value("location", "No artwork")
        self.screen.refresh()
        self.assertFalse(self.panel.isVisible())
        self.assertFalse(self.panel.content.isVisible())
        self.repository.set_state_value("location", "Library")
        self.screen.refresh()
        self.screen.set_repository(None)
        self.assertFalse(self.panel.isVisible())
        self.assertFalse(self.panel.content.isVisible())

    def test_switching_tabs_hides_overlay_and_returning_restores_it(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self.screen, "Conversation")
        tabs.addTab(QWidget(), "Other")
        tabs.resize(1000, 750)
        tabs.show()
        self.settle()
        self.assertTrue(self.panel.content.isVisible())
        tabs.setCurrentIndex(1)
        self.settle()
        self.assertTrue(self.panel.content.isHidden())
        tabs.setCurrentIndex(0)
        self.settle()
        self.assertTrue(self.panel.content.isVisible())

    def test_image_still_opens_full_size_viewer(self) -> None:
        with patch("ai_adventure.ui.primitives._open_image_viewer") as viewer:
            QTest.mouseClick(self.screen.location_image_label, Qt.MouseButton.LeftButton)
        viewer.assert_called_once_with(self.screen.location_image_label)
