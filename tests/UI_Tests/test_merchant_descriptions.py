import os
from pathlib import Path
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QHeaderView

from ai_adventure.persistence.save_repository import SaveRepository
from ai_adventure.ui.screens.merchant import MerchantScreen
from tests.qt_test_case import QtTestCase


class MerchantDescriptionTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = SaveRepository.create_new_save(Path(temporary.name), "Merchant descriptions")
        self.repo.upsert_npc(name="Barnaby", npc_id="barnaby")
        self.repo.upsert_merchant_profile("barnaby", can_buy=True, can_sell=True)
        self.repo.set_active_merchant_npc("barnaby")
        self.repo.set_state_value("currency.balance", "100")
        self.description = "Durable salted biscuits for travel meals; soften in broth or water before eating."
        self.repo.add_inventory_item("Hard Tack", "Food", 3, self.description)
        item = next(item for item in self.repo.list_item_catalog() if item["name"] == "Hard Tack")
        self.repo.upsert_merchant_stock(npc_id="barnaby", item_id=item["id"], quantity=10,
                                       unit_price_base_units=2, stock_id="tack_stock")
        self.repo.upsert_merchant_buy_offer(npc_id="barnaby", item_id=item["id"], unit_price_base_units=1)
        self.screen = MerchantScreen()
        self.screen.resize(1000, 700)
        self.screen.set_repository(self.repo)
        self.screen.show()
        self.app.processEvents()

    def test_both_tables_show_saved_descriptions_and_wrap_available_space(self):
        for table in (self.screen.sell_table, self.screen.buy_table):
            self.assertEqual(table.horizontalHeaderItem(5).text(), "Description")
            self.assertEqual(table.item(0, 5).text(), self.description)
            self.assertEqual(table.item(0, 5).toolTip(), self.description)
            self.assertTrue(table.wordWrap())
            self.assertEqual(table.horizontalHeader().sectionResizeMode(5), QHeaderView.ResizeMode.Stretch)

    def test_sell_description_prefers_current_owned_item_over_catalog(self):
        with self.repo.transaction() as connection:
            connection.execute("UPDATE inventory_items SET description=? WHERE name='Hard Tack'",
                               ("Crumbled biscuits suitable for soup thickening.",))
        self.screen.refresh()
        self.assertEqual(self.screen.buy_table.item(0, 5).text(), "Crumbled biscuits suitable for soup thickening.")
        self.assertEqual(self.screen.sell_table.item(0, 5).text(), self.description)

    def test_missing_description_uses_honest_placeholder(self):
        with self.repo.transaction() as connection:
            connection.execute("UPDATE item_catalog SET description='' WHERE name='Hard Tack'")
            connection.execute("UPDATE inventory_items SET description='' WHERE name='Hard Tack'")
        self.screen.refresh()
        for table in (self.screen.sell_table, self.screen.buy_table):
            self.assertEqual(table.item(0, 5).text(), "No description recorded.")

    def test_trade_controls_still_use_selected_quantities(self):
        self.screen.sell_table.cellWidget(0, 3).setValue(2)
        self.screen.sell_table.cellWidget(0, 4).click()
        self.assertEqual(int(self.repo.get_state_value("currency.balance")), 96)
        self.assertEqual(self.repo.list_merchant_stock("barnaby")[0]["quantity"], 8)
        self.screen.buy_table.cellWidget(0, 3).setValue(2)
        self.screen.buy_table.cellWidget(0, 4).click()
        self.assertEqual(int(self.repo.get_state_value("currency.balance")), 98)
        self.assertEqual(self.screen.buy_table.item(0, 5).text(), self.description)
