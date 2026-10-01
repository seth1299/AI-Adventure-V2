from __future__ import annotations

import uuid

from ai_adventure.ui.common import *  # noqa: F401,F403


class MerchantScreen(RepositoryBackedWidget):
    """Deterministic trading screen for the NPC in the active conversation."""

    def __init__(self) -> None:
        super().__init__()
        self.title = QLabel("No active merchant")
        self.balance = QLabel("Currency: 0")
        self.status = QLabel("Talk to a merchant to view their offers.")
        self.sell_table = self._make_table(["Item", "In stock", "Unit price", "Quantity", "Buy"])
        self.buy_table = self._make_table(["Item", "You have", "Unit offer", "Quantity", "Sell"])
        layout = QVBoxLayout()
        layout.addWidget(self.title)
        layout.addWidget(self.balance)
        layout.addWidget(QLabel("Merchant's goods"))
        layout.addWidget(self.sell_table)
        layout.addWidget(QLabel("Merchant buys from you"))
        layout.addWidget(self.buy_table)
        layout.addWidget(self.status)
        self.setLayout(layout)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = _AppTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.horizontalHeader().setStretchLastSection(False)
        return table

    def refresh(self) -> None:
        repository = self.repository()
        if repository is None:
            self._clear()
            return
        npc_id = repository.get_active_merchant_npc_id()
        npc = repository.get_npc(npc_id) if npc_id else None
        profile = repository.get_merchant_profile(npc_id) if npc_id else None
        if npc is None or profile is None:
            self._clear()
            return
        display_name = npc.get("display_name") or npc.get("name") or "Merchant"
        self.title.setText(f"Trading with {display_name}")
        balance = _safe_int(repository.get_state_value("currency.balance", "0"), 0)
        self.balance.setText(
            "Currency: " + format_currency_amount(balance, repository.get_currency_denominations())
        )
        self._render_sell(repository, npc_id, balance, profile)
        self._render_buy(repository, npc_id, profile)
        self.status.setText("Select an offer to trade.")

    def _clear(self) -> None:
        self.title.setText("No active merchant")
        self.balance.setText("Currency: 0")
        self.sell_table.setRowCount(0)
        self.buy_table.setRowCount(0)
        self.status.setText("Talk to a merchant to view their offers.")

    def _render_sell(self, repository: SaveRepository, npc_id: str, balance: int, profile: dict[str, Any]) -> None:
        rows = repository.list_merchant_stock(npc_id) if profile.get("can_sell") else []
        self.sell_table.setRowCount(len(rows))
        for row_index, offer in enumerate(rows):
            self.sell_table.setItem(row_index, 0, _table_item(str(offer["item_name"])))
            self.sell_table.setItem(row_index, 1, _table_item(str(offer["quantity"])))
            self.sell_table.setItem(row_index, 2, _table_item(format_currency_amount(int(offer["unit_price_base_units"]), repository.get_currency_denominations())))
            spin = QSpinBox()
            spin.setRange(1, max(1, int(offer["quantity"])))
            self.sell_table.setCellWidget(row_index, 3, spin)
            button = QPushButton("Buy")
            button.setEnabled(int(offer["quantity"]) > 0 and balance >= int(offer["unit_price_base_units"]))
            button.clicked.connect(lambda _checked=False, selected=dict(offer), control=spin: self._buy(repository, selected, control.value()))
            self.sell_table.setCellWidget(row_index, 4, button)

    def _render_buy(self, repository: SaveRepository, npc_id: str, profile: dict[str, Any]) -> None:
        inventory = {str(item.get("id")): item for item in repository.list_inventory_items()}
        rows = [offer for offer in repository.list_merchant_buy_offers(npc_id) if any(str(item.get("name", "")).casefold() == str(offer["item_name"]).casefold() for item in inventory.values())] if profile.get("can_buy") else []
        self.buy_table.setRowCount(len(rows))
        for row_index, offer in enumerate(rows):
            item = next(item for item in inventory.values() if str(item.get("name", "")).casefold() == str(offer["item_name"]).casefold())
            available = min(int(item.get("quantity", 0)), int(offer["max_quantity"]) or int(item.get("quantity", 0)))
            self.buy_table.setItem(row_index, 0, _table_item(str(offer["item_name"])))
            self.buy_table.setItem(row_index, 1, _table_item(str(item.get("quantity", 0))))
            self.buy_table.setItem(row_index, 2, _table_item(format_currency_amount(int(offer["unit_price_base_units"]), repository.get_currency_denominations())))
            spin = QSpinBox()
            spin.setRange(1, max(1, available))
            self.buy_table.setCellWidget(row_index, 3, spin)
            button = QPushButton("Sell")
            button.setEnabled(available > 0)
            button.clicked.connect(lambda _checked=False, selected=dict(offer), control=spin: self._sell(repository, selected, control.value()))
            self.buy_table.setCellWidget(row_index, 4, button)

    def _buy(self, repository: SaveRepository, offer: dict[str, Any], quantity: int) -> None:
        self._trade(repository, "buy", str(offer["stock_id"]), quantity)

    def _sell(self, repository: SaveRepository, offer: dict[str, Any], quantity: int) -> None:
        # The offer ID is the reference; the repository resolves the player's item by its name.
        self._trade(repository, "sell", str(offer["offer_id"]), quantity)

    def _trade(self, repository: SaveRepository, direction: str, reference_id: str, quantity: int) -> None:
        try:
            repository.execute_merchant_transaction(
                transaction_id=f"txn_{uuid.uuid4().hex}",
                npc_id=repository.get_active_merchant_npc_id(),
                direction=direction,
                reference_id=reference_id,
                quantity=quantity,
            )
        except ValueError as error:
            self.status.setText(str(error))
        else:
            self.status.setText("Trade completed.")
            self.notify_repository_changed()
            self.refresh()
