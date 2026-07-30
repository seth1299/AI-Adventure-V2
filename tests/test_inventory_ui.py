from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QLabel,
    QLayoutItem,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.main_window import (
    _DetachedTabWindow,
    AlchemyNotebookScreen,
    CalendarPlayerEventDialog,
    CalendarScreen,
    GameShell,
    InventoryItemDetailsDialog,
    InventoryLocationPanel,
    InventoryScreen,
    NpcsScreen,
    StoryScreen,
    _inventory_item_display_name,
    _inventory_quantity_display,
)


class InventoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_detached_tab_window_reshows_page_hidden_by_tab_removal(self) -> None:
        host = QWidget()
        tabs = QTabWidget(host)
        screen = QWidget()
        screen_layout = QVBoxLayout(screen)
        content = QLabel("Detached inventory content", screen)
        screen_layout.addWidget(content)
        tabs.addTab(screen, "Inventory")
        host.show()
        self.app.processEvents()

        self.assertTrue(screen.isVisible())
        tabs.removeTab(0)
        self.assertTrue(screen.isHidden())

        window = _DetachedTabWindow(
            "inventory",
            "Inventory - Test Adventure",
            screen,
            host,
        )
        window.show()
        self.app.processEvents()

        self.assertIs(window.centralWidget(), screen)
        self.assertTrue(screen.isVisible())
        self.assertTrue(content.isVisible())

        window.close()
        self.app.processEvents()
        screen.close()
        host.close()

    def test_conversation_bubbles_fill_available_width_and_wrap_text(self) -> None:
        screen = StoryScreen()
        screen.resize(1200, 700)
        screen._render_conversation(
            [
                (
                    "ai",
                    "live_game",
                    "A deliberately long sentence that should use the available "
                    "conversation width before wrapping naturally at a word boundary.",
                    1,
                )
            ]
        )
        screen.show()
        self.app.processEvents()

        bubble = screen.findChild(QWidget, "conversationBubble")
        self.assertIsNotNone(bubble)
        assert bubble is not None
        message = bubble.findChild(QTextEdit)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertGreater(bubble.width(), 1000)
        self.assertEqual(
            message.lineWrapMode(),
            QTextEdit.LineWrapMode.WidgetWidth,
        )

        screen.close()

    def test_recipe_table_uses_notes_instead_of_redundant_result_column(self) -> None:
        screen = AlchemyNotebookScreen()
        headers = [
            cast(
                QTableWidgetItem,
                screen.recipe_table.horizontalHeaderItem(column),
            ).text()
            for column in range(screen.recipe_table.columnCount())
        ]

        self.assertEqual(
            headers,
            ["Name", "Ingredients", "Estimated Value", "Notes"],
        )
        self.assertNotIn("Result", headers)

        screen.close()

    def test_crafting_items_show_typical_areas_value_and_rarity_notes(self) -> None:
        screen = AlchemyNotebookScreen()
        headers = [
            cast(
                QTableWidgetItem,
                screen.reagent_table.horizontalHeaderItem(column),
            ).text()
            for column in range(screen.reagent_table.columnCount())
        ]

        self.assertEqual(
            headers,
            [
                "Name",
                "Category",
                "Description",
                "Typical Areas",
                "Uses",
                "Estimated Value",
                "Notes",
            ],
        )
        self.assertNotIn("Location", headers)

        screen.close()

    def test_calendar_month_title_shares_navigation_row_and_player_events_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Calendar UI Test")
            screen = CalendarScreen()
            screen.set_repository(repository)
            screen.resize(1200, 700)
            screen.show()
            self.app.processEvents()

            buttons = {
                button.text(): button
                for button in screen.findChildren(QPushButton)
            }
            self.assertIn("Previous", buttons)
            self.assertIn("Today", buttons)
            self.assertIn("Next", buttons)
            self.assertIn("+ Event", buttons)
            self.assertLess(
                abs(screen.month_label.geometry().center().y() - buttons["Today"].geometry().center().y()),
                8,
            )

            dialog = CalendarPlayerEventDialog(
                calendar_settings=repository.get_calendar_settings(),
                default_year=1,
                default_month=2,
                default_day=7,
                parent=screen,
            )
            dialog.title_input.setText("Watch the eclipse")
            dialog.all_day_checkbox.setChecked(False)
            dialog.hour_input.setValue(21)
            dialog.minute_input.setValue(10)
            event = dialog.build_event()

            self.assertEqual(event["origin"], "player")
            self.assertEqual(event["time_of_day_minutes"], 1270)
            self.assertTrue(str(event["event_id"]).startswith("player_"))

            dialog.close()
            screen.close()

    def test_npcs_auto_refresh_after_repository_change_without_refresh_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "NPC UI Test")
            shell = GameShell(
                lambda: None,
                tts_enabled=False,
                ai_enabled=False,
            )
            shell.set_repository(repository)
            self.app.processEvents()

            self.assertEqual(shell.npcs_screen.table.rowCount(), 0)
            refresh_buttons = [
                button
                for button in shell.npcs_screen.findChildren(QPushButton)
                if button.text() == "Refresh"
            ]
            self.assertEqual(refresh_buttons, [])

            repository.upsert_npc(
                npc_id="mira_coppercup",
                name="Mira Coppercup",
                display_name="Bartender",
                role="Tavern keeper",
                location="Copper Kettle Tavern",
                player_facing_information="A friendly bartender who remembers regulars.",
            )
            shell.story_screen.notify_repository_changed()
            self.app.processEvents()

            self.assertEqual(shell.npcs_screen.table.rowCount(), 1)
            name_item = shell.npcs_screen.table.item(0, 0)
            location_item = shell.npcs_screen.table.item(0, 1)
            self.assertIsNotNone(name_item)
            self.assertIsNotNone(location_item)
            assert name_item is not None
            assert location_item is not None
            self.assertEqual(name_item.text(), "Bartender")
            self.assertEqual(
                location_item.text(),
                "Copper Kettle Tavern",
            )

            shell.close()

    def test_npcs_screen_contains_only_the_live_table(self) -> None:
        screen = NpcsScreen()

        self.assertEqual(
            [button.text() for button in screen.findChildren(QPushButton)],
            [],
        )

        screen.close()

    def test_inventory_uses_location_panels_and_modal_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Inventory UI Test")
            repository.add_inventory_item(
                "Brass Compass",
                "Tool",
                1,
                "A pocket compass with a scratched lid.",
                value_base_units=8,
                metadata={
                    "quantity_unit": "each",
                    "storage_location": "Detective's Car",
                    "ascii_art": "  .--.\\n ( N  )\\n  '--'",
                },
            )
            repository.add_inventory_item(
                "Notebook",
                "Book",
                2,
                "Two clothbound notebooks.",
                value_base_units=2,
                metadata={
                    "quantity_unit": "each",
                    "storage_location": "actively_carried",
                    "ascii_art": " _____\n|_____|\n|_____|",
                },
            )

            screen = InventoryScreen()
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertFalse(hasattr(screen, "table"))
            self.assertEqual(len(screen.location_panels), 2)
            self.assertTrue(screen.location_panels[0].title().startswith("Actively Carried ("))
            self.assertEqual(screen.location_panels[1].title(), "Detective's Car (1)")
            self.assertTrue(all(panel.item_buttons for panel in screen.location_panels))
            panel_layout = cast(QGridLayout, screen.inventory_panel_layout)
            self.assertEqual(panel_layout.getItemPosition(0), (0, 0, 1, 2))
            self.assertEqual(panel_layout.getItemPosition(1), (0, 2, 1, 2))

            compass = screen._inventory_items["brass compass"]
            catalog_entry = compass["catalog_entry"]
            dialog = InventoryItemDetailsDialog(
                item=compass,
                catalog_entry=catalog_entry,
                denominations=screen._denominations,
                parent=screen,
            )
            art_view = cast(
                QPlainTextEdit,
                dialog.findChild(QPlainTextEdit, "inventoryAsciiArt"),
            )

            self.assertTrue(dialog.isModal())
            self.assertEqual(
                dialog.windowModality(),
                Qt.WindowModality.ApplicationModal,
            )
            self.assertIsNotNone(art_view)
            self.assertIn("( N  )", art_view.toPlainText())
            self.assertNotIn("\\n", art_view.toPlainText())
            self.assertEqual(
                art_view.document().firstBlock().blockFormat().alignment(),
                Qt.AlignmentFlag.AlignCenter,
            )
            dialog_labels = [label.text() for label in dialog.findChildren(QLabel)]
            self.assertNotIn("Item Art", dialog_labels)
            self.assertNotIn("Equipped:", dialog_labels)
            self.assertIsNone(
                dialog.findChild(QPlainTextEdit, "inventoryStructuredDetails")
            )
            self.assertIsNone(
                dialog.findChild(QLabel, "inventoryStructuredDetailsLabel")
            )
            dialog_layout = cast(QVBoxLayout, dialog.layout())
            art_layout_item = cast(
                QLayoutItem,
                dialog_layout.itemAt(dialog_layout.indexOf(art_view)),
            )
            self.assertIsNotNone(art_layout_item)
            self.assertTrue(
                art_layout_item.alignment()
                & Qt.AlignmentFlag.AlignHCenter
            )

            tall_catalog_entry = dict(catalog_entry)
            tall_catalog_entry["ascii_art"] = "\n".join(
                f"line {index}" for index in range(10)
            )
            tall_dialog = InventoryItemDetailsDialog(
                item=compass,
                catalog_entry=tall_catalog_entry,
                denominations=screen._denominations,
                parent=screen,
            )
            tall_art_view = cast(
                QPlainTextEdit,
                tall_dialog.findChild(QPlainTextEdit, "inventoryAsciiArt"),
            )
            self.assertGreater(tall_art_view.height(), art_view.height())

            playtesting_dialog = InventoryItemDetailsDialog(
                item=compass,
                catalog_entry=catalog_entry,
                denominations=screen._denominations,
                show_structured_details=True,
                parent=screen,
            )
            structured_details = playtesting_dialog.findChild(
                QPlainTextEdit,
                "inventoryStructuredDetails",
            )
            self.assertIsNotNone(structured_details)
            structured_details = cast(QPlainTextEdit, structured_details)
            self.assertIn("item_uuid", structured_details.toPlainText())
            playtesting_dialog.close()
            tall_dialog.close()
            dialog.close()
            screen.close()

    def test_inventory_quantities_use_x_format_and_natural_plurals(self) -> None:
        self.assertEqual(_inventory_quantity_display(1, "each"), "x1")
        self.assertEqual(_inventory_quantity_display(3, "day"), "x3 days")
        self.assertEqual(_inventory_quantity_display(2, "flask"), "x2 flasks")
        self.assertEqual(_inventory_quantity_display(2, "oz"), "x2 oz")
        self.assertEqual(
            _inventory_item_display_name("Healing Potion", 3, "each"),
            "Healing Potions",
        )
        self.assertEqual(
            _inventory_item_display_name("Food Rations", 3, "day"),
            "Food Rations",
        )

        panel = InventoryLocationPanel(
            "actively_carried",
            [
                {
                    "name": "Steel Dagger",
                    "quantity": 1,
                    "quantity_unit": "each",
                    "category": "Weapon",
                },
                {
                    "name": "Healing Potion",
                    "quantity": 3,
                    "quantity_unit": "each",
                    "category": "Consumable",
                },
                {
                    "name": "Food Rations",
                    "quantity": 3,
                    "quantity_unit": "day",
                    "category": "Consumable",
                },
            ],
            lambda _item: None,
        )
        button_texts = [button.text() for button in panel.item_buttons]

        self.assertIn("Steel Dagger\nx1  ·  Weapon", button_texts)
        self.assertIn("Healing Potions\nx3  ·  Consumable", button_texts)
        self.assertIn("Food Rations\nx3 days  ·  Consumable", button_texts)

        panel.close()

    def test_single_location_panel_is_centered_at_half_width(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SaveRepository.create_new_save(Path(temp_dir), "Centered Inventory")
            screen = InventoryScreen()
            screen.set_repository(repository)
            self.app.processEvents()

            self.assertEqual(len(screen.location_panels), 1)
            panel_layout = cast(QGridLayout, screen.inventory_panel_layout)
            self.assertEqual(panel_layout.getItemPosition(0), (0, 1, 1, 2))
            screen.close()


if __name__ == "__main__":
    unittest.main()
