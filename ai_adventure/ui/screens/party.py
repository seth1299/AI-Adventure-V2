from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class PartyScreen(RepositoryBackedWidget):
    """Player-facing party roster backed by canonical NPC identities."""

    def __init__(self) -> None:
        super().__init__()

        self.table = _AppTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Name",
                "Status",
                "Health",
                "Armor Class",
                "Combat Style",
                "Skills",
                "Description",
                "Equipment",
                "Portrait",
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        _configure_wrapping_table(self.table, {4, 5, 6, 7})

        explanation = QLabel(
            "Party members are shared NPC identities. Names and descriptions come "
            "from the NPC profile; this tab shows their current party-specific state."
        )
        explanation.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(explanation)
        layout.addWidget(self.table)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads current party records joined to their NPC profiles."""

        repository = self.repository()
        if repository is None:
            self.table.setRowCount(0)
            return

        members = repository.list_party_members()
        self.table.setRowCount(len(members))
        for row_index, member in enumerate(members):
            health_current = _safe_int(member.get("health_current"), -1)
            health_max = _safe_int(member.get("health_max"), -1)
            health = (
                f"{health_current}/{health_max}"
                if health_current >= 0 and health_max >= 0
                else "N/A"
            )
            armor_class = _safe_int(member.get("armor_class"), -1)
            values = (
                member.get("display_name") or member.get("name") or "Unknown NPC",
                member.get("status", "Active"),
                health,
                armor_class if armor_class >= 0 else "N/A",
                member.get("combat_style", ""),
                ", ".join(str(skill) for skill in member.get("skills", [])),
                member.get("description") or member.get("notes") or "",
                ", ".join(
                    (
                        f"{item.get('name', 'Unknown item')}"
                        + (
                            f" x{item.get('quantity', 1)}"
                            if _safe_int(item.get("quantity"), 1) != 1
                            else ""
                        )
                        + (
                            f" [{item.get('equipment_slot')}]"
                            if str(item.get("equipment_slot", "")).strip()
                            else ""
                        )
                    )
                    for item in member.get("equipment", [])
                    if isinstance(item, dict)
                )
                or "None recorded",
            )
            for column, value in enumerate(values):
                item = _table_item(str(value))
                item.setData(Qt.ItemDataRole.UserRole, str(member.get("npc_id", "")))
                self.table.setItem(row_index, column, item)
            npc_id = str(member.get("npc_id", "") or "").strip()
            portrait = QLabel()
            portrait.setObjectName("partyGeneratedPortrait")
            portrait.setMargin(4)
            asset = repository.get_visual_asset("npc", npc_id.casefold())
            if _set_generated_image(
                portrait,
                self.visual_asset_path(asset),
                maximum_width=96,
                maximum_height=96,
                accessible_name=(
                    f"Generated portrait of {member.get('display_name', 'Unknown NPC')}"
                ),
            ):
                self.table.setCellWidget(row_index, 8, portrait)

        _resize_wrapping_table_rows(self.table)
        for row_index in range(self.table.rowCount()):
            if self.table.cellWidget(row_index, 8) is not None:
                self.table.setRowHeight(
                    row_index,
                    max(104, self.table.rowHeight(row_index)),
                )
