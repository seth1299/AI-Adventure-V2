import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from ai_adventure.ai.gemini_service import build_new_game_response_schema, build_story_response_schema
from ai_adventure.application.new_game_service import _travel_locations_for_save
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.container_flow import container_event_issues
from ai_adventure.inventory_storage import inventory_access
from ai_adventure.items import item_is_valid_for_slot
from ai_adventure.locations import normalize_known_location
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.character import CharacterScreen
from ai_adventure.ui.screens.travel import TravelScreen
from ai_adventure.ui.new_game_form_helpers import _append_starting_location_table_row, _starting_locations_from_table
from ai_adventure.ui.table_helpers import _AppTableWidget


class EquipmentLocationScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = SaveRepository.create_new_save(Path(directory.name), "Scope")
        self.repo.set_state_value("location", "North America")
        self.repo.set_travel_locations([{"name": "North America", "location_scope": "broad"},
                                        {"name": "Hideout", "location_scope": "specific"}])
        self.repo.replace_inventory_items([
            {"name": "Dagger", "category": "Weapon", "weapon_hands": "one-handed", "quantity": 1},
            {"name": "Rations", "category": "Food", "quantity": 1},
            {"name": "Backpack", "category": "Container", "quantity": 1, "weight_lb": 2,
             "carrying_capacity_lb": 20, "container": {"is_open": True, "contents_initialized": True}},
        ])

    def test_hand_slot_validation_rejects_food_preserves_weapons_and_bags(self):
        items = {item["name"]: item for item in self.repo.list_inventory_items()}
        for slot in ["Main Hand", "Off Hand"]:
            self.assertTrue(item_is_valid_for_slot(items["Dagger"], slot))
            self.assertFalse(item_is_valid_for_slot(items["Rations"], slot))
            self.assertFalse(item_is_valid_for_slot(items["Backpack"], slot))
        self.assertTrue(item_is_valid_for_slot(items["Backpack"], "Back"))
        self.repo.set_player_equipment({"Main Hand": "Rations"})
        self.assertNotIn("Rations", self.repo.get_player_equipment().values())

    def test_character_scroll_and_capacity_summary_and_dropdowns(self):
        screen = CharacterScreen()
        self.addCleanup(screen.close)
        screen.set_repository(self.repo)
        screen.resize(800, 500)
        screen.show()
        self.app.processEvents()
        combo = screen.equipment_combos["Main Hand"]
        self.assertNotIn("Rations", [combo.itemText(i) for i in range(combo.count())])
        self.assertGreater(screen.sheet_scroll.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(screen.profile_backstory_display.height(), 52)
        self.assertIn("carried bags 20 lb", screen.carrying_summary_label.text())
        before = self.repo.inventory_load()
        self.repo.set_player_equipment({"Back": "Backpack"})
        self.assertEqual(before["capacity_lb"], self.repo.inventory_load()["capacity_lb"])

    def test_broad_storage_is_rejected_and_specific_site_round_trip_works(self):
        dagger = self.repo.list_inventory_items()[0]
        self.assertNotIn(("Leave at North America", "North America"), self.repo.inventory_move_destinations(dagger["id"]))
        with self.assertRaisesRegex(ValueError, "specific storage site"):
            self.repo.move_inventory_item(dagger["id"], "North America")
        self.repo.set_state_value("location", "Hideout")
        self.repo.move_inventory_item(dagger["id"], "Hideout")
        self.assertTrue(self.repo.inventory_access()[dagger["id"]]["available"])
        self.repo.set_state_value("location", "North America")
        self.assertFalse(self.repo.inventory_access()[dagger["id"]]["available"])
        self.repo.set_state_value("location", "Hideout")
        self.repo.move_inventory_item(dagger["id"], "actively_carried")

    def test_items_recorded_at_broad_area_are_not_available_on_arrival(self):
        rows = [{"id": "1", "name": "Dagger", "storage_location": "North America", "metadata": {}}]
        self.assertFalse(inventory_access(rows, "North America", self.repo.get_travel_locations())["1"]["available"])
        self.assertFalse(inventory_access(rows, "North America")["1"]["available"])

    def test_event_move_rejects_broad_and_new_site_can_be_established_in_same_batch(self):
        applier = EventApplier(self.repo)
        move = {"type": "InventoryItemModifiedEvent", "payload": {
            "target_name": "Dagger", "new_storage_location": "North America"}}
        self.assertEqual(applier.apply_event(move).status, "skipped")
        events = [
            {"type": "LocationUpsertedEvent", "payload": {
                "name": "Safe Room", "location_scope": "specific", "x_miles": 0, "y_miles": 0}},
            {"type": "StatusUpdatedEvent", "payload": {"location": "Safe Room", "minutes_passed": 0}},
            {"type": "InventoryItemModifiedEvent", "payload": {
                "target_name": "Dagger", "new_storage_location": "Safe Room"}},
        ]
        context = {"state": {"scene": {"location": "North America"},
                    "travel": {"locations": self.repo.get_travel_locations()},
                    "inventory": {"items": self.repo.list_inventory_items()}}}
        self.assertEqual(container_event_issues(events, context), [])
        results = applier.apply_events(events)
        self.assertTrue(all(result.status == "applied" for result in results))
        self.assertEqual(next(i for i in self.repo.list_inventory_items() if i["name"] == "Dagger")["storage_location"], "Safe Room")

    def test_scope_round_trip_and_sublocation_does_not_imply_specific(self):
        broad = normalize_known_location({"name": "District", "is_sublocation": True, "parent_location": "City"})
        self.assertEqual(broad.location_scope, "broad")
        table = _AppTableWidget(0, 7)
        self.addCleanup(table.close)
        _append_starting_location_table_row(table, {"name": "Hideout", "location_scope": "specific"}, 1, lambda _: None)
        self.assertEqual(_starting_locations_from_table(table)[0]["location_scope"], "specific")
        self.repo.upsert_travel_location({"name": "Hideout", "description": "A small room"})
        self.assertEqual(self.repo.find_travel_location("Hideout")["location_scope"], "specific")
        reopened = SaveRepository(self.repo.db_path)
        self.assertEqual(reopened.find_travel_location("Hideout")["location_scope"], "specific")

    def test_travel_scope_can_be_marked_explicitly(self):
        screen = TravelScreen()
        self.addCleanup(screen.close)
        screen.set_repository(self.repo)
        screen.scope_selector.setCurrentIndex(screen.scope_selector.findData("specific"))
        self.assertEqual(self.repo.find_travel_location("North America")["location_scope"], "specific")

    def test_gemini_creation_and_story_require_scope(self):
        creation = build_new_game_response_schema({})
        self.assertIn("location_scope", creation["properties"]["locations"]["items"]["required"])
        story = build_story_response_schema({"selection": {"tags": ["travel"]}})
        branches = story["properties"]["events"]["items"]["anyOf"]
        location = next(b for b in branches if b["properties"]["type"]["enum"] == ["LocationUpsertedEvent"])
        self.assertIn("location_scope", location["properties"]["payload"]["required"])

    def test_creation_service_preserves_authored_scope_with_missing_or_renamed_result(self):
        from types import SimpleNamespace
        setup = {"starting_locations": [{"name": "My Room", "location_mode": "exact", "location_scope": "specific"}]}
        rows = _travel_locations_for_save([], setup, SimpleNamespace(start_location=""))
        self.assertEqual(rows[0]["location_scope"], "specific")
        setup["starting_locations"][0]["location_mode"] = "suggestion"
        rows = _travel_locations_for_save([{"name": "Safe Room", "source_index": 0, "location_scope": "broad"}], setup, SimpleNamespace(start_location=""))
        self.assertEqual(rows[0]["name"], "Safe Room")
        self.assertEqual(rows[0]["location_scope"], "specific")

