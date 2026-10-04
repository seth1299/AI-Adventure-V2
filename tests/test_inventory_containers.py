from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QGraphicsOpacityEffect, QCheckBox

from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.items import normalize_item_metadata
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.inventory_storage import INVENTORY_STORAGE_RULE
from ai_adventure.container_flow import container_event_issues
from ai_adventure.container_access import has_immediate_container_unlock_method
from ai_adventure.ai.gemini_service import build_new_game_response_schema, build_story_response_schema
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.inventory import InventoryScreen, InventoryMoveDialog
from ai_adventure.ui.new_game_form_helpers import (
    _append_starter_item_table_row, _starter_items_from_table,
    _append_starter_weapon_table_row, _starter_weapons_from_table,
    _append_starter_armor_table_row, _starter_armor_from_table,
)
from ai_adventure.ui.table_helpers import _AppTableWidget


def box(name, location="Player Store", *, opened=True, contents=None):
    return {"name": name, "category": "Container", "quantity": 1,
            "description": "A plain storage box.", "value_base_units": 2,
            "storage_location": location, "container": {
                "is_open": opened, "contents_initialized": True,
                "contents": contents or {"currency_base_units": 0, "items": []}}}


class InventoryContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = SaveRepository.create_new_save(Path(self.directory.name), "Storage")
        self.repo.set_travel_locations([{"name": "Player Store", "location_scope": "specific"}])
        self.repo.set_state_value("location", "Player Store")
        self.repo.replace_inventory_items([
            box("Oak Box"), box("Blue Box"), box("Remote Box", "Blacksmith"),
            box("Sealed Box", opened=False),
            {"name": "Ruby", "category": "Valuable", "quantity": 3, "description": "A red gem."},
            {"name": "Forge", "category": "Tool", "quantity": 1, "description": "A fixed forge.",
             "storage_location": "Player Store", "moveable": False, "storable": False},
        ])

    def item(self, name):
        return next(item for item in self.repo.list_inventory_items() if item["name"] == name)

    def move(self, name, destination):
        self.repo.move_inventory_item(str(self.item(name)["id"]), str(self.item(destination)["id"]) if destination.endswith("Box") else destination)

    def test_ordinary_empty_box_becomes_storage_and_survives_reopening_save(self):
        original = self.item("Ruby")
        self.move("Ruby", "Oak Box")
        self.repo = SaveRepository(self.repo.db_path)
        ruby = self.item("Ruby")
        oak = self.item("Oak Box")
        self.assertEqual(ruby["id"], original["id"])
        self.assertEqual(ruby["metadata"]["item_uuid"], original["metadata"]["item_uuid"])
        self.assertEqual(ruby["quantity"], 3)
        self.assertEqual(oak["metadata"]["container"]["contents"]["items"], [ruby["id"]])
        self.assertIn(ruby["id"], {entry["id"] for entry in self.repo.list_item_catalog()})
        screen = InventoryScreen()
        self.addCleanup(screen.close)
        screen.set_repository(self.repo)
        self.assertIn("Oak Box", [panel.location for panel in screen.location_panels])

    def test_move_between_boxes_removes_old_membership_and_preserves_quantity(self):
        self.move("Ruby", "Oak Box")
        self.move("Ruby", "Blue Box")
        self.assertEqual(self.item("Oak Box")["metadata"]["container"]["contents"]["items"], [])
        self.assertEqual(self.item("Blue Box")["metadata"]["container"]["contents"]["items"], [self.item("Ruby")["id"]])
        self.move("Ruby", "actively_carried")
        self.assertNotIn("container_id", self.item("Ruby")["metadata"])
        self.assertEqual(self.item("Ruby")["quantity"], 3)

    def test_remote_source_and_destination_are_rejected_without_changes(self):
        self.move("Ruby", "Oak Box")
        before = self.repo.list_inventory_items()
        with self.assertRaisesRegex(ValueError, "not currently accessible"):
            self.move("Ruby", "Remote Box")
        self.assertEqual(before, self.repo.list_inventory_items())
        self.repo.set_state_value("location", "Road")
        with self.assertRaisesRegex(ValueError, "Stored at"):
            self.move("Ruby", "actively_carried")
        self.assertEqual(before, self.repo.list_inventory_items())

    def test_move_dropdown_filters_closed_remote_and_self_containers(self):
        ruby = self.item("Ruby")
        destinations = self.repo.inventory_move_destinations(str(ruby["id"]))
        self.assertEqual({label for label, _ in destinations}, {"Oak Box", "Blue Box", "Leave at Player Store"})
        self.move("Ruby", "Oak Box")
        self.assertEqual({label for label, _ in self.repo.inventory_move_destinations(str(ruby["id"]))}, {"Actively Carried", "Blue Box", "Leave at Player Store"})

    def test_fixed_item_and_nonstorable_item_rules(self):
        with self.assertRaisesRegex(ValueError, "cannot be moved/stored"):
            self.move("Forge", "Oak Box")
        self.repo.modify_inventory_item(target_name="Ruby", metadata={"storable": False})
        with self.assertRaisesRegex(ValueError, "cannot be stored"):
            self.move("Ruby", "Oak Box")
        self.move("Ruby", "Player Store")
        self.move("Ruby", "actively_carried")

    def test_nested_contents_follow_outer_container_and_cycles_are_rejected(self):
        self.move("Ruby", "Oak Box")
        self.move("Oak Box", "Blue Box")
        before = self.repo.list_inventory_items()
        with self.assertRaisesRegex(ValueError, "inside itself"):
            self.move("Blue Box", "Oak Box")
        self.assertEqual(before, self.repo.list_inventory_items())
        self.move("Blue Box", "actively_carried")
        self.repo.set_state_value("location", "Road")
        self.assertTrue(self.repo.inventory_access()[str(self.item("Ruby")["id"])]["available"])
        self.assertEqual(self.item("Ruby")["metadata"]["container_id"], self.item("Oak Box")["id"])

    def test_unknown_contents_are_materialized_but_hidden_until_opened(self):
        self.repo.add_inventory_item("Loot Box", "Container", 1, "Sealed loot.", metadata={
            "container": {"contents": {"currency_base_units": 10, "items": [
                {"name": "Secret Map", "category": "Document", "quantity": 1,
                 "description": "A private map.", "value_base_units": 2}]}}})
        hidden = self.item("Secret Map")
        self.assertFalse(self.repo.inventory_access()[str(hidden["id"])]["known"])
        packet = StoryTurnService.build_context_packet(self.repo, "Look around.")
        self.assertNotIn("Secret Map", json.dumps(packet["state"]["inventory"]["items"]))
        self.assertNotIn("Secret Map", json.dumps(packet["state"]["item_catalog"]["items"]))
        screen = InventoryScreen()
        self.addCleanup(screen.close)
        screen.set_repository(self.repo)
        self.assertNotIn("Loot Box", [panel.location for panel in screen.location_panels])
        result = EventApplier(self.repo).apply_event({"type": "ContainerOpenedEvent", "payload": {"container_name": "Loot Box"}})
        self.assertEqual(result.status, "applied")
        self.assertTrue(self.repo.inventory_access()[str(hidden["id"])]["known"])
        screen.refresh()
        self.assertIn("Loot Box", [panel.location for panel in screen.location_panels])

    def test_closing_container_keeps_known_contents_but_prevents_access(self):
        self.move("Ruby", "Oak Box")
        container = self.item("Oak Box")["metadata"]["container"]
        container["is_open"] = False
        self.repo.modify_inventory_item(target_name="Oak Box", metadata={"container": container})
        status = self.repo.inventory_access()[str(self.item("Ruby")["id"])]
        self.assertTrue(status["known"])
        self.assertFalse(status["available"])

    def test_remote_rows_are_faded_clickable_and_move_disabled(self):
        self.move("Ruby", "Oak Box")
        self.repo.set_state_value("location", "Road")
        screen = InventoryScreen()
        self.addCleanup(screen.close)
        screen.set_repository(self.repo)
        panel = next(panel for panel in screen.location_panels if panel.location == "Oak Box")
        button = panel.item_buttons[0]
        self.assertTrue(button.isEnabled())
        self.assertIsInstance(button.graphicsEffect(), QGraphicsOpacityEffect)
        button.click()
        dialog = screen._item_detail_dialogs[str(self.item("Ruby")["id"])]
        self.assertFalse(dialog.move_button.isEnabled())
        self.assertIn("Stored at Player Store", dialog.move_button.toolTip())

    def test_fixed_item_move_button_has_explanatory_tooltip(self):
        screen = InventoryScreen()
        self.addCleanup(screen.close)
        screen.set_repository(self.repo)
        screen._open_item_details(screen._inventory_items["forge"])
        dialog = screen._item_detail_dialogs[str(self.item("Forge")["id"])]
        self.assertFalse(dialog.move_button.isEnabled())
        self.assertEqual(dialog.move_button.toolTip(), "Item cannot be moved/stored.")

    def test_gui_move_persists_without_gemini_and_cancel_is_inert(self):
        screen = InventoryScreen()
        self.addCleanup(screen.close)
        screen.set_repository(self.repo)
        before = self.repo.list_inventory_items()
        with patch.object(InventoryMoveDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            screen._move_item(str(self.item("Ruby")["id"]))
        self.assertEqual(before, self.repo.list_inventory_items())
        with patch.object(InventoryMoveDialog, "exec", lambda dialog: (dialog.destination_combo.setCurrentIndex(dialog.destination_combo.findData(str(self.item("Blue Box")["id"]))), QDialog.DialogCode.Accepted)[1]):
            screen._move_item(str(self.item("Ruby")["id"]))
        self.assertEqual(self.item("Ruby")["storage_location"], "Blue Box")

    def test_modeless_move_button_updates_after_travel(self):
        self.move("Ruby", "Oak Box")
        screen = InventoryScreen()
        self.addCleanup(screen.close)
        screen.set_repository(self.repo)
        screen._open_item_details(screen._inventory_items["ruby"])
        dialog = screen._item_detail_dialogs[str(self.item("Ruby")["id"])]
        self.assertTrue(dialog.move_button.isEnabled())
        self.repo.set_state_value("location", "Road")
        screen.refresh()
        self.assertFalse(dialog.move_button.isEnabled())

    def test_full_container_cannot_be_removed_or_contents_orphaned(self):
        self.move("Ruby", "Oak Box")
        before = self.repo.list_inventory_items()
        with self.assertRaisesRegex(ValueError, "Empty the container"):
            self.repo.remove_inventory_item("Oak Box", 1)
        self.assertEqual(before, self.repo.list_inventory_items())
        self.move("Ruby", "actively_carried")
        self.repo.remove_inventory_item("Oak Box", 1)
        self.assertNotIn("Oak Box", {item["name"] for item in self.repo.list_inventory_items()})

    def test_consuming_contained_item_updates_references(self):
        self.move("Ruby", "Oak Box")
        self.repo.remove_inventory_item("Ruby", 1)
        self.assertEqual(self.item("Ruby")["quantity"], 2)
        self.repo.remove_inventory_item("Ruby", 2)
        self.assertEqual(self.item("Oak Box")["metadata"]["container"]["contents"]["items"], [])

    def test_container_rename_keeps_identity_and_child_membership(self):
        self.move("Ruby", "Oak Box")
        identity = self.item("Oak Box")["id"]
        self.repo.modify_inventory_item(target_name="Oak Box", new_name="Renamed Box")
        self.assertEqual(self.item("Renamed Box")["id"], identity)
        self.assertEqual(self.item("Ruby")["storage_location"], "Renamed Box")
        self.assertEqual(self.item("Ruby")["metadata"]["container_id"], identity)

    def test_gemini_moves_containers_and_cannot_take_remote_contents(self):
        self.move("Ruby", "Oak Box")
        applier = EventApplier(self.repo)
        moved = applier.apply_event({"type": "ItemModifiedEvent", "payload": {"target_name": "Oak Box", "new_storage_location": "actively_carried"}})
        self.assertEqual(moved.status, "applied")
        self.move("Oak Box", "Player Store")
        self.repo.set_state_value("location", "Road")
        before = self.repo.list_inventory_items()
        for kind in ("ContainerOpenedEvent", "ContainerContentsTakenEvent"):
            result = applier.apply_event({"type": kind, "payload": {"container_name": "Oak Box"}})
            self.assertEqual(result.status, "skipped")
        self.assertEqual(before, self.repo.list_inventory_items())

    def test_context_remembers_remote_items_with_unavailable_flag(self):
        self.move("Ruby", "Oak Box")
        self.repo.set_state_value("location", "Road")
        packet = StoryTurnService.build_context_packet(self.repo, "Use the Ruby.")
        ruby = next(item for item in packet["state"]["inventory"]["items"] if item["name"] == "Ruby")
        self.assertFalse(ruby["available"])
        self.assertEqual(ruby["physical_location"], "Player Store")
        self.assertTrue(ruby["metadata"]["moveable"])
        self.assertIn(INVENTORY_STORAGE_RULE, packet["state"]["inventory"]["container_flow"])

    def test_new_game_and_table_roundtrip_preserve_flags_and_custom_storage(self):
        item = {"name": "Forge", "category": "Tool", "description": "A fixed forge.", "storage_location": "Player Store", "moveable": False, "storable": False}
        setup = normalize_new_game_setup({"starter_items": [item]})
        self.assertFalse(setup["starter_items"][0]["moveable"])
        table = _AppTableWidget(0, 9)
        self.addCleanup(table.close)
        _append_starter_item_table_row(table, item, lambda button: None)
        self.assertIsInstance(table.cellWidget(0, 6), QCheckBox)
        saved = _starter_items_from_table(table)[0]
        self.assertEqual(saved["storage_location"], "Player Store")
        self.assertFalse(saved["moveable"])
        self.assertFalse(saved["storable"])

    def test_legacy_embedded_records_migrate_once_to_real_catalog_ids(self):
        loot = box("Legacy Box", opened=False, contents={"currency_base_units": 0, "items": [
            {"name": "Old Coin", "category": "Valuable", "quantity": 2, "description": "A copper collectible.", "value_base_units": 5}]})
        self.repo.add_inventory_item(loot["name"], loot["category"], 1, loot["description"], metadata=loot)
        before = self.repo.list_inventory_items()
        self.repo = SaveRepository(self.repo.db_path)
        self.assertEqual(before, self.repo.list_inventory_items())
        self.assertEqual(self.item("Legacy Box")["metadata"]["container"]["contents"]["items"], [self.item("Old Coin")["id"]])
        self.assertEqual(self.item("Old Coin")["quantity"], 2)

    def test_normalization_does_not_treat_string_false_as_true(self):
        metadata = normalize_item_metadata({"moveable": "false", "storable": False})
        self.assertFalse(metadata["moveable"])
        self.assertFalse(metadata["storable"])

    def test_new_gemini_inventory_schema_requires_flags_and_supports_containers(self):
        schema = build_new_game_response_schema({}, for_api=True)
        item = schema["properties"]["starting_items"]["items"]
        self.assertIn("moveable", item["required"])
        self.assertIn("storable", item["required"])
        self.assertIn("container", item["properties"])
        story = build_story_response_schema({"selection": {"tags": ["inventory"]}})
        branches = story["properties"]["events"]["items"]["anyOf"]
        added = next(branch for branch in branches if branch["properties"]["type"]["enum"] == ["InventoryItemAddedEvent"])
        self.assertIn("moveable", added["properties"]["payload"]["required"])
        self.assertIn("storable", added["properties"]["payload"]["required"])

    def test_weapon_and_armor_tables_preserve_storage_and_mobility_flags(self):
        for columns, append, read in ((11, _append_starter_weapon_table_row, _starter_weapons_from_table), (8, _append_starter_armor_table_row, _starter_armor_from_table)):
            with self.subTest(columns=columns):
                table = _AppTableWidget(0, columns)
                self.addCleanup(table.close)
                item = {"name": "Heavy Equipment", "moveable": False, "storable": False, "storage_location": "Player Store"}
                append(table, item, lambda button: None)
                saved = read(table)[0]
                self.assertFalse(saved["moveable"])
                self.assertFalse(saved["storable"])
                self.assertEqual(saved["storage_location"], "Player Store")

    def test_remote_move_is_rejected_before_narration_commit(self):
        self.move("Ruby", "Oak Box")
        self.repo.set_state_value("location", "Road")
        packet = StoryTurnService.build_context_packet(self.repo, "Move the Ruby to my pockets.")
        issues = container_event_issues([{"type": "InventoryItemModifiedEvent", "payload": {"target_name": "Ruby", "new_storage_location": "actively_carried"}}], packet)
        self.assertTrue(any("Stored at Player Store" in issue for issue in issues))

    def test_remote_keys_do_not_unlock_containers(self):
        items = [{"name": "Oak Box Key", "category": "Tool", "description": "A matching key.", "available": False}]
        self.assertFalse(has_immediate_container_unlock_method(items, "Oak Box"))
        items[0]["available"] = True
        self.assertTrue(has_immediate_container_unlock_method(items, "Oak Box"))

    def test_unrelated_catalog_update_preserves_mobility_flags(self):
        self.repo.upsert_item_catalog_entry(name="Forge", category="Tool", description="The same fixed forge.")
        forge = next(item for item in self.repo.list_item_catalog() if item["name"] == "Forge")
        self.assertFalse(forge["metadata"]["moveable"])
        self.assertFalse(forge["metadata"]["storable"])

    def test_stowing_equipped_item_clears_equipment(self):
        self.repo.add_inventory_item("Test Sword", "Weapon", 1, "A steel sword.")
        self.repo.set_player_equipment({"Main Hand": "Test Sword"})
        self.assertIn("Test Sword", self.repo.get_player_equipment().values())
        self.move("Test Sword", "Oak Box")
        self.assertNotIn("Test Sword", self.repo.get_player_equipment().values())
        self.assertFalse(self.item("Test Sword")["equipped"])

    def test_crafting_consumes_accessible_contents_and_updates_membership(self):
        self.move("Ruby", "Oak Box")
        recipe = {"name": "Gem Dust", "ingredients": [{
            "name": "Ruby", "item_uuid": self.item("Ruby")["metadata"]["item_uuid"],
            "quantity": 3, "measure_amount": 1, "measure_unit": "each",
        }]}
        self.repo.set_state_value("location", "Road")
        self.assertFalse(self.repo._consume_crafting_ingredients(recipe, 1))
        self.assertEqual(self.item("Ruby")["quantity"], 3)
        self.repo.set_state_value("location", "Player Store")
        self.assertTrue(self.repo._consume_crafting_ingredients(recipe, 1))
        self.assertEqual(self.item("Oak Box")["metadata"]["container"]["contents"]["items"], [])
        self.assertNotIn("Ruby", [item["name"] for item in self.repo.list_inventory_items()])
        self.repo = SaveRepository(self.repo.db_path)

    def test_gemini_can_move_by_container_id_then_take_in_same_batch(self):
        packet = StoryTurnService.build_context_packet(self.repo, "Put Ruby in Oak Box, then take it out.")
        events = [
            {"type": "InventoryItemModifiedEvent", "payload": {
                "target_name": "Ruby", "new_storage_location": self.item("Oak Box")["id"]}},
            {"type": "ContainerContentsTakenEvent", "payload": {
                "container_name": "Oak Box", "item_ids": [self.item("Ruby")["id"]], "take_currency": False}},
        ]
        self.assertEqual(container_event_issues(events, packet), [])

    def test_second_addition_cannot_teleport_a_stored_stack(self):
        self.move("Ruby", "Oak Box")
        before = self.repo.list_inventory_items()
        with self.assertRaisesRegex(ValueError, "stored elsewhere"):
            self.repo.add_inventory_item("Ruby", "Valuable", 1, "Another ruby.")
        self.assertEqual(self.repo.list_inventory_items(), before)

    def test_generic_container_type_is_detected_without_misclassifying_keys(self):
        ordinary = normalize_item_metadata({"item_type": "Tool"}, name="Empty Oak Box", category="Tool")
        self.assertEqual(ordinary["item_type"], "Container")
        self.assertIn("container", ordinary)
        for name in ("Oak Box Key", "Box Cutter", "Chest Plate"):
            with self.subTest(name=name):
                result = normalize_item_metadata({"item_type": "Tool"}, name=name, category="Tool")
                self.assertNotIn("container", result)


    def test_embedded_duplicate_copy_does_not_merge_or_hide_existing_possession(self):
        before = self.item("Ruby")
        loot = box("Loot Box", opened=False, contents={"currency_base_units": 0, "items": [{
            "name": "Ruby", "category": "Valuable", "quantity": 2,
            "item_uuid": before["metadata"]["item_uuid"], "description": "More red gems.",
        }]})
        self.repo.add_inventory_item(loot["name"], loot["category"], 1, loot["description"], metadata=loot)
        self.assertEqual(self.item("Ruby"), before)
        contained = self.item("Ruby (Loot Box)")
        self.assertNotEqual(contained["id"], before["id"])
        self.assertEqual(contained["quantity"], 2)
        self.assertFalse(self.repo.inventory_access()[contained["id"]]["known"])
        self.assertEqual(self.item("Loot Box")["metadata"]["container"]["contents"]["items"], [contained["id"]])
        self.repo = SaveRepository(self.repo.db_path)
        self.assertEqual(self.item("Ruby (Loot Box)")["id"], contained["id"])
        applier = EventApplier(self.repo)
        self.assertEqual(applier.apply_event({"type": "ContainerOpenedEvent", "payload": {"container_name": "Loot Box"}}).status, "applied")
        self.assertEqual(applier.apply_event({"type": "ContainerContentsTakenEvent", "payload": {
            "container_name": "Loot Box", "item_names": ["Ruby"], "take_currency": False,
        }}).status, "applied")
        self.assertEqual(self.item("Ruby")["quantity"], 3)
        self.assertEqual(self.item("Ruby (Loot Box)")["storage_location"], "actively_carried")
