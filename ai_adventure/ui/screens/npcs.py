from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class NpcsScreen(RepositoryBackedWidget):
    """Player-facing NPC journal."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._npcs_by_id: dict[str, dict[str, Any]] = {}
        self.table = _AppTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Location", "Notes", "Portrait"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.cellClicked.connect(self._open_npc_details)
        _configure_wrapping_table(self.table, {2})
        _enable_table_sorting(self.table, self._sort_by_column)
        self.table.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)

        layout = QVBoxLayout()
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads the player-visible NPC journal."""

        repository = self.repository()

        if repository is None:
            self._npcs_by_id.clear()
            self.table.setRowCount(0)
            return

        npcs = repository.list_player_visible_npcs()
        npcs.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self._npcs_by_id = {
            str(npc.get("npc_id", "") or "").strip(): dict(npc)
            for npc in npcs
            if str(npc.get("npc_id", "") or "").strip()
        }
        self.table.setRowCount(len(npcs))

        for row_index, npc in enumerate(npcs):
            npc_id = str(npc.get("npc_id", "") or "").strip()
            values = (
                str(npc.get("display_name", "Unknown NPC")),
                str(npc.get("location", "")),
                str(npc.get("notes", "")),
            )
            for column, value in enumerate(values):
                table_item = _table_item(value)
                table_item.setData(Qt.ItemDataRole.UserRole, npc_id)
                self.table.setItem(row_index, column, table_item)
            portrait = QLabel()
            portrait.setObjectName("npcGeneratedPortrait")
            portrait.setMargin(4)
            asset = repository.get_visual_asset(
                "npc",
                str(npc.get("npc_id", "") or "").casefold(),
            )
            if _set_generated_image(
                portrait,
                self.visual_asset_path(asset),
                maximum_width=96,
                maximum_height=96,
                accessible_name=(
                    f"Generated portrait of {npc.get('display_name', 'Unknown NPC')}"
                ),
            ):
                self.table.setCellWidget(row_index, 3, portrait)

        _resize_wrapping_table_rows(self.table)
        for row_index in range(self.table.rowCount()):
            if self.table.cellWidget(row_index, 3) is not None:
                self.table.setRowHeight(row_index, max(104, self.table.rowHeight(row_index)))

    def _open_npc_details(self, row: int, _column: int) -> None:
        """Opens the selected NPC's complete player-visible profile."""

        if row < 0:
            return
        table_item = self.table.item(row, 0)
        npc_id = (
            str(table_item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if table_item is not None
            else ""
        )
        npc = self._npcs_by_id.get(npc_id)
        repository = self.repository()
        if npc is None or repository is None:
            return
        asset = repository.get_visual_asset("npc", npc_id.casefold())
        dialog = NpcDetailsDialog(
            npc=npc,
            image_path=self.visual_asset_path(asset),
            parent=self,
        )
        dialog.exec()

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts NPCs by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, npc: dict[str, Any]) -> tuple[str, str]:
        """Returns the active NPC sort key."""

        name = str(npc.get("display_name", "Unknown NPC")).casefold()

        if self._sort_column == 1:
            return str(npc.get("location", "")).casefold(), name

        if self._sort_column == 2:
            return str(npc.get("notes", "")).casefold(), name

        return name, name
