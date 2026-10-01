from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from ai_adventure.persistence.save_repository import SaveRepository


class RepositoryRelationshipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = SaveRepository.create_new_save(Path(self.temp.name), "Relationships")

    def populate(self):
        repo = self.repository
        repo.upsert_npc(name="Merchant", npc_id="merchant")
        repo.upsert_party_member("merchant")
        repo.add_party_inventory_item("merchant", name="Bag", category="Container", quantity=1, description="A bag")
        repo.upsert_merchant_profile("merchant", can_sell=True, can_buy=True)
        repo.upsert_item_catalog_entry(name="Letter", category="Document")
        with repo._connect() as connection:
            item = connection.execute("SELECT id FROM item_catalog WHERE name='Letter'").fetchone()
        repo.upsert_merchant_stock(npc_id="merchant", item_id=item["id"], quantity=2, unit_price_base_units=1)
        repo.upsert_merchant_buy_offer(npc_id="merchant", item_id=item["id"], unit_price_base_units=1)
        with repo._connect() as connection:
            connection.execute(
                "INSERT INTO merchant_transactions VALUES "
                "('trade', 'merchant', 'buy', 'ref', ?, 'Letter', 1, 1, 1, '')",
                (item["id"],),
            )
        repo.upsert_spell_catalog(name="Light", spell_id="light")
        repo.learn_character_spell("light")
        repo.ensure_visual_asset(
            asset_id="portrait", subject_type="npc", subject_key="merchant",
            display_name="Merchant", descriptor_hash="hash", filename="portrait.png",
            prompt="Merchant portrait", model="test", message_ids=["opening"],
        )
        return item["id"]

    def test_every_new_transaction_enforces_relationships(self):
        for _ in range(2):
            with self.repository.transaction() as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repository._connect() as connection:
                connection.execute(
                    "INSERT INTO party_members (npc_id, created_at, updated_at) VALUES ('missing', '', '')"
                )

    def test_all_declared_foreign_keys_reject_missing_parents(self):
        self.populate()
        with self.repository._connect() as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )]
            relationships = [
                (table, fk[3]) for table in tables
                for fk in connection.execute(f'PRAGMA foreign_key_list("{table}")')
            ]
        for table, column in relationships:
            with self.subTest(table=table, column=column):
                with self.assertRaises(sqlite3.IntegrityError):
                    with self.repository._connect() as connection:
                        cursor = connection.execute(f'UPDATE "{table}" SET "{column}" = ?', ("missing-parent",))
                        self.assertGreater(cursor.rowcount, 0)

    def test_deleting_owner_removes_only_its_dependent_records(self):
        self.populate()
        self.repository.upsert_npc(name="Other", npc_id="other")
        self.repository.upsert_party_member("other")
        with self.repository._connect() as connection:
            connection.execute("DELETE FROM npcs WHERE id='merchant'")
            for table in ("party_members", "party_inventory_items", "merchant_profiles", "merchant_stock", "merchant_buy_offers", "merchant_transactions"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE npc_id='merchant'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM party_members WHERE npc_id='other'").fetchone()[0], 1)
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])

    def test_catalog_and_asset_deletions_remove_dependent_records(self):
        item_id = self.populate()
        with self.repository._connect() as connection:
            connection.execute("DELETE FROM item_catalog WHERE id=?", (item_id,))
            connection.execute("DELETE FROM spell_catalog WHERE spell_id='light'")
            connection.execute("DELETE FROM visual_assets WHERE asset_id='portrait'")
            for table in ("merchant_stock", "merchant_buy_offers", "character_spells", "message_visual_assets"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            # A completed trade is a historical snapshot, not a current listing.
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM merchant_transactions").fetchone()[0], 1)

    def test_snapshot_restores_children_and_parents_with_checks_enabled(self):
        self.populate()
        self.repository.capture_message_snapshot("opening")
        with self.repository._connect() as connection:
            connection.execute("DELETE FROM npcs WHERE id='merchant'")
            connection.execute("DELETE FROM spell_catalog WHERE spell_id='light'")
        with self.repository.transaction() as connection:
            self.assertTrue(self.repository.rollback_message("opening"))
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(len(self.repository.list_party_members()), 1)
        self.assertEqual(len(self.repository.list_character_spells()), 1)
        with self.repository._connect() as connection:
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])

    def test_invalid_snapshot_rolls_back_without_consuming_snapshot(self):
        self.populate()
        self.repository.capture_message_snapshot("broken")
        with self.repository._connect() as connection:
            snapshot = json.loads(connection.execute(
                "SELECT snapshot_json FROM message_snapshots WHERE message_id='broken'"
            ).fetchone()[0])
            snapshot["npcs"]["rows"] = []
            connection.execute("UPDATE message_snapshots SET snapshot_json=?", (json.dumps(snapshot),))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.rollback_message("broken")
        self.assertIsNotNone(self.repository.get_npc("merchant"))
        self.assertTrue(self.repository.has_message_snapshot("broken"))


if __name__ == "__main__":
    unittest.main()
