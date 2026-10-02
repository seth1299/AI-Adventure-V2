from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QDialog, QDoubleSpinBox
from ai_adventure.items import normalize_item_metadata
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.container_flow import container_event_issues
from ai_adventure.container_flow import CONTAINER_FLOW_RULE
from ai_adventure.ai.gemini_service import build_story_response_schema
from ai_adventure.new_game_setup import normalize_new_game_setup
from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.inventory import InventoryScreen
from ai_adventure.ui.new_game_form_helpers import _append_starter_item_table_row, _starter_items_from_table, _edit_starter_weight
from ai_adventure.ui.table_helpers import _AppTableWidget


def cargo(name, *, location="Store", capacity=30, weight=2, vehicle=False):
    return {"name": name, "category": "Vehicle" if vehicle else "Container", "item_type": "Vehicle" if vehicle else "Container", "quantity": 1,
            "description": "A cargo vessel.", "storage_location": location, "weight_lb": weight, "carrying_capacity_lb": capacity,
            "container": {"is_open": True, "contents_initialized": True, "contents_known": True, "contents": {"items": [], "currency_base_units": 0}}}


class InventoryCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = SaveRepository.create_new_save(Path(directory.name), "Capacity")
        self.repo.set_state_value("location", "Store")
        self.repo.replace_inventory_items([cargo("Pack"), cargo("Wagon", capacity=200, weight=300, vehicle=True),
            {"name": "Ingots", "category": "Material", "quantity": 6, "description": "Heavy metal.", "weight_lb": 10, "storage_location": "Store"}])

    def item(self, name):
        return next(item for item in self.repo.list_inventory_items() if item["name"] == name)

    def move(self, name, destination):
        dest = next((str(item['id']) for item in self.repo.list_inventory_items() if item['name'] == destination), destination)
        self.repo.move_inventory_item(str(self.item(name)['id']), dest)

    def test_carried_container_adds_bonus_and_ground_contents_remain_accessible(self):
        self.repo.modify_inventory_item(target_name="Ingots", quantity=3)
        self.move("Ingots", "Pack")
        self.move("Pack", "actively_carried")
        load = self.repo.inventory_load()
        self.assertEqual((load['weight_lb'], load['capacity_lb']), (32, 80))
        self.move("Pack", "Store")
        self.assertEqual((self.repo.inventory_load()['weight_lb'], self.repo.inventory_load()['capacity_lb']), (0, 50))
        self.assertTrue(self.repo.inventory_access()[str(self.item('Ingots')['id'])]['available'])
        self.repo.set_state_value('location', 'Road')
        self.assertFalse(self.repo.inventory_access()[str(self.item('Ingots')['id'])]['available'])
        self.repo = SaveRepository(self.repo.db_path)
        self.assertEqual(self.item('Ingots')['metadata']['container_id'], self.item('Pack')['id'])
        self.assertEqual(self.item('Ingots')['metadata']['weight_lb'], 10)

    def test_weight_limit_rejects_move_without_changing_identity_or_membership(self):
        before = self.repo.list_inventory_items()
        with self.assertRaisesRegex(ValueError, 'Player carrying capacity exceeded'):
            self.move('Ingots', 'actively_carried')
        self.assertEqual(before, self.repo.list_inventory_items())
        self.assertNotIn('Actively Carried', dict(self.repo.inventory_move_destinations(str(self.item('Ingots')['id']))))

    def test_vehicle_load_never_counts_against_player_and_parking_preserves_access_rules(self):
        self.move('Ingots', 'Wagon')
        self.move('Wagon', 'actively_carried')
        load = self.repo.inventory_load()
        self.assertEqual((load['weight_lb'], load['capacity_lb']), (0, 50))
        self.assertEqual(load['cargo'][str(self.item('Wagon')['id'])]['weight_lb'], 60)
        self.repo.set_state_value('location', 'Road')
        self.assertTrue(self.repo.inventory_access()[str(self.item('Ingots')['id'])]['available'])
        self.move('Wagon', 'Road')
        self.repo.set_state_value('location', 'Forest')
        self.assertFalse(self.repo.inventory_access()[str(self.item('Ingots')['id'])]['available'])

    def test_vehicle_and_container_cargo_caps_and_vehicle_nesting(self):
        with self.assertRaisesRegex(ValueError, 'Pack cargo capacity exceeded'):
            self.move('Ingots', 'Pack')
        self.repo.modify_inventory_item(target_name='Wagon', metadata={'carrying_capacity_lb': 50})
        with self.assertRaisesRegex(ValueError, 'Wagon cargo capacity exceeded'):
            self.move('Ingots', 'Wagon')
        with self.assertRaisesRegex(ValueError, 'Vehicles cannot'):
            self.move('Wagon', 'Pack')

    def test_nested_container_bonus_cannot_multiply_capacity(self):
        self.repo.add_inventory_item('Small Bag', 'Container', 1, 'A bag.', metadata=cargo('Small Bag', capacity=10))
        self.move('Small Bag', 'Pack')
        self.move('Pack', 'actively_carried')
        load = self.repo.inventory_load()
        self.assertEqual((load['weight_lb'], load['container_bonus_lb']), (4, 30))

    def test_add_and_quantity_modify_over_capacity_roll_back_catalog_and_inventory(self):
        before = self.repo.list_inventory_items()
        catalog = self.repo.list_item_catalog()
        with self.assertRaisesRegex(ValueError, 'capacity exceeded'):
            self.repo.add_inventory_item('Anvil', 'Tool', 1, 'Heavy.', metadata={'weight_lb': 80})
        self.assertEqual(before, self.repo.list_inventory_items())
        self.assertEqual(catalog, self.repo.list_item_catalog())
        self.repo.modify_inventory_item(target_name='Ingots', quantity=4)
        self.move('Ingots', 'actively_carried')
        with self.assertRaisesRegex(ValueError, 'capacity exceeded'):
            self.repo.modify_inventory_item(target_name='Ingots', quantity=6)
        self.assertEqual(self.item('Ingots')['quantity'], 4)

    def test_catalog_weight_is_used_by_older_callers(self):
        self.repo.upsert_item_catalog_entry(name='Anvil', category='Tool', description='Heavy.', metadata={'weight_lb': 80})
        with self.assertRaisesRegex(ValueError, 'capacity exceeded'):
            self.repo.add_inventory_item('Anvil', 'Tool', 1, 'Heavy.')
        self.repo.upsert_item_catalog_entry(name='Anvil', category='Tool', description='Updated details.')
        anvil = next(item for item in self.repo.list_item_catalog() if item['name'] == 'Anvil')
        self.assertEqual(anvil['metadata']['weight_lb'], 80)

    def test_crafting_overweight_output_keeps_ingredients_and_work(self):
        self.repo.add_crafting_recipe(name='Heavy Result', ingredients=[{'reagent_name': 'Ingots', 'item_uuid': self.item('Ingots')['metadata']['item_uuid'], 'quantity': 1, 'measure_amount': 1, 'measure_unit': 'each'}],
            result='A very heavy result.', result_item_uuid='heavy-result', result_item_name='Heavy Result', result_weight_lb=80, stages=[{'kind': 'active', 'work_amount': 1}])
        before = self.repo.list_inventory_items()
        recipe = self.repo.list_crafting_recipes()[0]
        result = self.repo.craft_recipe(str(recipe['id']))
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('capacity exceeded', result['message'])
        self.assertEqual(before, self.repo.list_inventory_items())
        self.assertEqual(self.repo.get_setting('crafting.processes', []), [])

    def test_overweight_purchase_keeps_currency_and_merchant_stock(self):
        self.repo.upsert_npc(name='Merchant', npc_id='merchant')
        self.repo.upsert_item_catalog_entry(name='Anvil', category='Tool', description='Heavy.', metadata={'weight_lb': 80})
        anvil = next(item for item in self.repo.list_item_catalog() if item['name'] == 'Anvil')
        self.repo.upsert_merchant_stock(npc_id='merchant', item_id=anvil['id'], quantity=2, unit_price_base_units=5, stock_id='anvil-stock')
        self.repo.set_state_value('currency.balance', '100')
        with self.assertRaisesRegex(ValueError, 'capacity exceeded'):
            self.repo.execute_merchant_transaction(transaction_id='buy-heavy', npc_id='merchant', direction='buy', reference_id='anvil-stock', quantity=1)
        self.assertEqual(self.repo.get_state_value('currency.balance'), '100')
        self.assertEqual(self.repo.list_merchant_stock('merchant')[0]['quantity'], 2)

    def test_legacy_overload_can_unload_but_cannot_worsen(self):
        self.repo.set_setting('player.attributes', {'Strength': 20})
        self.move('Ingots', 'actively_carried')
        self.repo.set_setting('player.attributes', {'Strength': 10})
        with self.assertRaisesRegex(ValueError, 'capacity exceeded'):
            self.repo.add_inventory_item('Pebble', 'Item', 1, 'Small.', metadata={'weight_lb': 1})
        self.move('Ingots', 'Store')
        self.assertEqual(self.repo.inventory_load()['weight_lb'], 0)

    def test_removing_bonus_cannot_leave_loose_carried_weight_over_capacity(self):
        self.move('Pack', 'actively_carried')
        self.move('Ingots', 'actively_carried')
        before = self.repo.list_inventory_items()
        with self.assertRaisesRegex(ValueError, 'capacity exceeded'):
            self.move('Pack', 'Store')
        self.assertEqual(before, self.repo.list_inventory_items())
        self.move('Ingots', 'Wagon')
        self.move('Pack', 'Store')

    def test_new_game_validates_complete_load_and_custom_base_capacity(self):
        items = [{'name': 'Load', 'category': 'Material', 'quantity': 1, 'description': 'A load.', 'weight_lb': 60}, cargo('Travel Pack', location='actively_carried')]
        repo = SaveRepository.create_new_save(self.repo.db_path.parent, 'Custom', setup={'skill_preset': 'beginner', 'starter_items': items})
        self.assertEqual(repo.player_carrying_capacity_lb(), 55)
        self.assertEqual((repo.inventory_load()['weight_lb'], repo.inventory_load()['capacity_lb']), (62, 85))

    def test_taking_multiple_stacks_preflights_cumulative_capacity_and_currency(self):
        self.repo.modify_inventory_item(target_name='Ingots', quantity=3)
        self.repo.add_inventory_item('Ore', 'Material', 1, 'More metal.', metadata={'weight_lb': 30, 'storage_location': 'Store'})
        self.move('Ingots', 'Wagon')
        self.move('Ore', 'Wagon')
        before = self.repo.list_inventory_items()
        result = EventApplier(self.repo).apply_event({'type': 'ContainerContentsTakenEvent', 'payload': {'container_name': 'Wagon'}})
        self.assertNotEqual(result.status, 'applied')
        self.assertIn('capacity exceeded', result.message)
        self.assertEqual(before, self.repo.list_inventory_items())
        issues = container_event_issues([{'type': 'ContainerContentsTakenEvent', 'payload': {'container_name': 'Wagon'}}],
            {'state': {'inventory': {'items': before, 'container_authority': before, 'container_items': before, 'carrying': self.repo.inventory_load()}, 'world': {'location': 'Store'}}})
        self.assertTrue(any('capacity exceeded' in issue for issue in issues))

    def test_taking_bag_and_heavy_cargo_succeeds_regardless_of_manifest_order(self):
        self.move('Ingots', 'Wagon')
        self.move('Pack', 'Wagon')
        result = EventApplier(self.repo).apply_event({'type': 'ContainerContentsTakenEvent', 'payload': {'container_name': 'Wagon'}})
        self.assertEqual(result.status, 'applied')
        self.assertEqual((self.repo.inventory_load()['weight_lb'], self.repo.inventory_load()['capacity_lb']), (62, 80))

    def test_model_preflight_rejects_additions_and_quantity_changes(self):
        items = self.repo.list_inventory_items()
        context = {'state': {'inventory': {'items': items, 'capacity_items': items, 'carrying': self.repo.inventory_load()}, 'world': {'location': 'Store'}}}
        issues = container_event_issues([{'type': 'InventoryItemAddedEvent', 'payload': {'item_name': 'Anvil', 'category': 'Tool', 'amount': 1, 'weight_lb': 80}}], context)
        self.assertTrue(any('capacity exceeded' in issue for issue in issues))
        self.repo.modify_inventory_item(target_name='Ingots', quantity=4)
        self.move('Ingots', 'actively_carried')
        context['state']['inventory']['capacity_items'] = self.repo.list_inventory_items()
        issues = container_event_issues([{'type': 'InventoryItemModifiedEvent', 'payload': {'target_name': 'Ingots', 'new_amount': 6}}], context)
        self.assertTrue(any('capacity exceeded' in issue for issue in issues))

    def test_new_item_api_contract_and_packaged_rules_include_weight(self):
        schema = build_story_response_schema({}, for_api=True)
        def definitions(value):
            if isinstance(value, dict):
                if 'weight_lb' in value.get('properties', {}) and 'item_type' in value.get('required', []):
                    self.assertIn('weight_lb', value['required'])
                    self.assertIn('carrying_capacity_lb', value['required'])
                for child in value.values(): definitions(child)
            elif isinstance(value, list):
                for child in value: definitions(child)
        definitions(schema)
        rules = Path(__file__).resolve().parents[1] / 'ai_adventure/data/context/default_rules.json'
        self.assertIn(CONTAINER_FLOW_RULE, json.dumps(json.loads(rules.read_text(encoding='utf-8'))))

    def test_gui_summary_details_and_authored_weights_roundtrip(self):
        screen = InventoryScreen(playtesting_tools=True)
        self.addCleanup(screen.close)
        screen.set_repository(self.repo)
        self.assertIn('0 / 50 lb', screen.carrying_label.text())
        self.assertIn('Wagon: 0 / 200 lb', screen.carrying_label.text())
        screen._open_item_details(screen._inventory_items['ingots'])
        dialog = screen._item_detail_dialogs[str(self.item('Ingots')['id'])]
        from PySide6.QtWidgets import QLabel
        self.assertTrue(any('60 lb total' in label.text() for label in dialog.findChildren(QLabel)))
        table = _AppTableWidget(0, 9)
        self.addCleanup(table.close)
        _append_starter_item_table_row(table, cargo('Pack'), lambda _: None)
        button = table.cellWidget(0, 9)
        def accept_weight(dialog):
            controls = dialog.findChildren(QDoubleSpinBox)
            controls[0].setValue(4.5)
            controls[1].setValue(45)
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, 'exec', accept_weight):
            _edit_starter_weight(table, button)
        setup = normalize_new_game_setup({'character': {'carrying_capacity_lb': 75}, 'starter_items': _starter_items_from_table(table)})
        self.assertEqual(setup['character']['carrying_capacity_lb'], 80)
        self.assertEqual((setup['starter_items'][0]['weight_lb'], setup['starter_items'][0]['carrying_capacity_lb']), (4.5, 45))

    def test_unknown_legacy_weights_and_nonfinite_values(self):
        self.assertNotIn('weight_lb', normalize_item_metadata({}))
        clean = normalize_item_metadata({'item_type': 'Vehicle', 'weight_lb': float('nan'), 'carrying_capacity_lb': -2})
        self.assertEqual((clean['weight_lb'], clean['carrying_capacity_lb'], clean['storable']), (0, 0, False))
        self.assertIn('container', clean)


if __name__ == '__main__':
    unittest.main()
