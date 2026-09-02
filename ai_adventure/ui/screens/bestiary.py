from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class BestiaryScreen(RepositoryBackedWidget):
    """Player-facing collection of learned, non-secret creature lore."""

    def __init__(self) -> None:
        super().__init__()

        self.creature_list = QListWidget()
        self.creature_list.currentItemChanged.connect(
            self._display_selected_creature
        )

        self.details_output = QTextEdit()
        self.details_output.setReadOnly(True)

        list_layout = QVBoxLayout()
        list_layout.addWidget(QLabel("Known Creatures"))
        list_layout.addWidget(self.creature_list)

        details_layout = QVBoxLayout()
        details_layout.addWidget(self.details_output)

        layout = QHBoxLayout()
        layout.addLayout(list_layout, 1)
        layout.addLayout(details_layout, 2)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads learned creatures while preserving the visible selection."""

        repository = self.repository()
        selected_id = self._selected_creature_id()
        self.creature_list.blockSignals(True)
        self.creature_list.clear()

        if repository is None:
            self.creature_list.blockSignals(False)
            self.details_output.clear()
            return

        for creature in repository.list_bestiary_entries():
            name = str(creature.get("name", "")).strip()
            if not name:
                continue

            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, creature)
            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                str(creature.get("creature_id", "")).strip(),
            )
            self.creature_list.addItem(item)

        self.creature_list.blockSignals(False)

        if self.creature_list.count() == 0:
            self.details_output.setPlainText(
                "No creatures have been learned about yet."
            )
            return

        target_row = 0
        for row in range(self.creature_list.count()):
            item = self.creature_list.item(row)
            if item is not None and str(
                item.data(Qt.ItemDataRole.UserRole + 1) or ""
            ) == selected_id:
                target_row = row
                break

        self.creature_list.setCurrentRow(target_row)
        self._display_selected_creature()

    def _selected_creature_id(self) -> str:
        """Returns the selected creature's durable public-lore ID."""

        current_item = self.creature_list.currentItem()
        if current_item is None:
            return ""
        return str(
            current_item.data(Qt.ItemDataRole.UserRole + 1) or ""
        ).strip()

    def _display_selected_creature(self, *_args: Any) -> None:
        """Displays only the selected public miscellaneous record."""

        current_item = self.creature_list.currentItem()
        if current_item is None:
            self.details_output.clear()
            return

        raw_creature = current_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(raw_creature, dict):
            self.details_output.clear()
            return

        name = str(raw_creature.get("name", "")).strip()
        details = str(raw_creature.get("details", "")).strip()
        sections = [f"# {name}"] if name else []
        if details:
            sections.append(details)
        _set_markdown_text(self.details_output, "\n\n".join(sections))
