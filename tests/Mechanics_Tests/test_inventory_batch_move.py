from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_adventure.persistence.save_repository import SaveRepository


def container(name, capacity=20):
    return {'name': name, 'category': 'Container', 'quantity': 1, 'weight_lb': 2,
            'carrying_capacity_lb': capacity, 'storage_location': 'Workshop',
            'container': {'is_open': True, 'contents_initialized': True,
                          'contents': {'items': [], 'currency_base_units': 0}}}


class InventoryBatchMoveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = SaveRepository.create_new_save(Path(temporary.name), 'Batch moves')
        self.repository.set_state_value('location', 'Workshop')
        self.repository.set_travel_locations([{'name': 'Workshop', 'location_scope': 'specific'}])
        self.repository.replace_inventory_items([
            container('Pack'), container('Small Box', 5),
            {'name': 'Cloth', 'quantity': 2, 'weight_lb': 2, 'storage_location': 'Workshop'},
            {'name': 'Needles', 'quantity': 3, 'weight_lb': 1, 'storage_location': 'Workshop'},
        ])

    def item(self, name):
        return next(item for item in self.repository.list_inventory_items() if item['name'] == name)

    def ids(self, *names):
        return [str(self.item(name)['id']) for name in names]

    def test_batch_moves_whole_stacks_preserving_identity_and_membership(self):
        before = {name: self.item(name) for name in ('Cloth', 'Needles')}
        self.repository.move_inventory_items(self.ids('Cloth', 'Needles'), self.ids('Pack')[0])
        reopened = SaveRepository(self.repository.db_path)
        self.assertEqual(set(self.item('Pack')['metadata']['container']['contents']['items']), set(self.ids('Cloth', 'Needles')))
        for name, original in before.items():
            moved = next(item for item in reopened.list_inventory_items() if item['name'] == name)
            self.assertEqual(moved['id'], original['id'])
            self.assertEqual(moved['quantity'], original['quantity'])
            self.assertEqual(moved['metadata']['container_id'], self.ids('Pack')[0])

    def test_combined_cargo_limit_filters_destination_and_rejects_entire_batch(self):
        ids = self.ids('Cloth', 'Needles')
        small = self.ids('Small Box')[0]
        self.assertTrue(all(small in dict((value, label) for label, value in self.repository.inventory_move_destinations(item_id)) for item_id in ids))
        self.assertNotIn(small, [value for _, value in self.repository.inventory_batch_move_destinations(ids)])
        with self.assertRaisesRegex(ValueError, 'cargo capacity exceeded'):
            self.repository.move_inventory_items(ids, small)
        self.assertTrue(all(self.item(name)['storage_location'] == 'Workshop' for name in ('Cloth', 'Needles')))

    def test_capacity_providing_pack_can_be_picked_up_with_heavy_cargo_without_intermediate_failure(self):
        self.repository.modify_inventory_item(target_name='Cloth', metadata={'weight_lb': 6})
        cloth, pack = self.ids('Cloth', 'Pack')
        with patch.object(self.repository, 'player_carrying_capacity_lb', return_value=5):
            self.assertNotIn('actively_carried', [value for _, value in self.repository.inventory_move_destinations(cloth)])
            self.assertIn('actively_carried', [value for _, value in self.repository.inventory_batch_move_destinations([cloth, pack])])
            self.repository.move_inventory_items([cloth, pack], 'actively_carried')
            self.assertEqual(self.repository.inventory_load()['weight_lb'], 14)
            self.assertEqual(self.repository.inventory_load()['capacity_lb'], 25)

    def test_selected_contents_stay_inside_selected_container(self):
        cloth, pack = self.ids('Cloth', 'Pack')
        self.repository.move_inventory_item(cloth, pack)
        self.repository.move_inventory_items([cloth, pack], 'actively_carried')
        self.assertEqual(self.item('Cloth')['metadata']['container_id'], pack)
        self.assertEqual(self.item('Pack')['storage_location'], 'actively_carried')
        with self.assertRaises(ValueError):
            self.repository.move_inventory_items([pack, cloth], cloth)

    def test_failure_during_second_write_rolls_back_all_items_and_history(self):
        ids = self.ids('Cloth', 'Needles')
        before = self.repository.list_inventory_items()
        history = self.repository.list_history()
        move = self.repository._relocate_inventory_item
        calls = []

        def fail_second(item_id, destination):
            calls.append(item_id)
            if len(calls) == 2:
                raise OSError('Simulated persistence failure')
            move(item_id, destination)

        with patch.object(self.repository, '_relocate_inventory_item', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.repository.move_inventory_items(ids, self.ids('Pack')[0])
        reopened = SaveRepository(self.repository.db_path)
        self.assertEqual(reopened.list_inventory_items(), before)
        self.assertEqual(reopened.list_history(), history)

    def test_inaccessible_item_rejects_batch_without_moving_accessible_item(self):
        self.repository.move_inventory_item(self.ids('Needles')[0], self.ids('Pack')[0])
        self.repository.modify_inventory_item(target_name='Pack', metadata={'container': {'is_open': False}})
        with self.assertRaises(ValueError):
            self.repository.move_inventory_items(self.ids('Cloth', 'Needles'), 'actively_carried')
        self.assertEqual(self.item('Cloth')['storage_location'], 'Workshop')

    def test_items_already_at_destination_are_unchanged(self):
        cloth, needles = self.ids('Cloth', 'Needles')
        self.repository.move_inventory_item(cloth, 'actively_carried')
        with patch.object(self.repository, '_relocate_inventory_item', wraps=self.repository._relocate_inventory_item) as move:
            self.repository.move_inventory_items([cloth, needles], 'actively_carried')
        move.assert_called_once_with(needles, 'actively_carried')

    def test_fixed_items_and_remote_destinations_are_rejected(self):
        self.repository.modify_inventory_item(target_name='Needles', metadata={'moveable': False})
        for destination in ('actively_carried', 'Remote Warehouse'):
            with self.assertRaises(ValueError):
                self.repository.move_inventory_items(self.ids('Cloth', 'Needles'), destination)
        self.assertEqual(self.item('Cloth')['storage_location'], 'Workshop')
