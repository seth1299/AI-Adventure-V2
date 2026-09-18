from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class BestiaryScreen(RepositoryBackedWidget):
    """Player-facing collection of learned, non-secret creature lore."""

    def __init__(self) -> None:
        super().__init__()

        # Keep the historical list as a hidden compatibility surface; the
        # player-facing control is the compact selector above the details.
        self.creature_list = QListWidget()
        self.creature_list.hide()
        self.creature_selector = _NoWheelComboBox()
        self.creature_selector.setObjectName("bestiaryCreatureSelector")
        self.creature_selector.currentIndexChanged.connect(
            self._display_selected_creature
        )

        self.creature_image_label = ClickableImageLabel()
        self.creature_image_label.setObjectName("bestiaryGeneratedImage")
        self.creature_image_label.setMargin(4)

        self.details_output = MarkdownDisplay()
        self.details_output.setObjectName("bestiaryCreatureDetails")

        details_layout = QVBoxLayout()
        selector_layout = QHBoxLayout()
        selector_layout.addWidget(QLabel("Known Creatures:"))
        selector_layout.addWidget(self.creature_selector, 1)
        details_layout.addLayout(selector_layout)
        details_layout.addWidget(
            self.creature_image_label,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        details_layout.addWidget(self.details_output)

        layout = QVBoxLayout()
        layout.addLayout(details_layout)
        layout.addWidget(self.creature_list)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads learned creatures while preserving the visible selection."""

        repository = self.repository()
        selected_id = self._selected_creature_id()
        self.creature_list.blockSignals(True)
        self.creature_selector.blockSignals(True)
        self.creature_list.clear()
        self.creature_selector.clear()

        if repository is None:
            self.creature_list.blockSignals(False)
            self.creature_selector.blockSignals(False)
            self.creature_image_label.clear()
            self.creature_image_label.hide()
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
            self.creature_selector.addItem(name, creature)

        self.creature_list.blockSignals(False)
        self.creature_selector.blockSignals(False)

        if self.creature_selector.count() == 0:
            self.creature_image_label.clear()
            self.creature_image_label.hide()
            _set_markdown_text(
                self.details_output,
                "No creatures have been learned about yet.",
            )
            return

        target_row = 0
        for row in range(self.creature_selector.count()):
            raw_creature = self.creature_selector.itemData(row)
            creature_id = (
                str(raw_creature.get("creature_id", "") or "").strip()
                if isinstance(raw_creature, dict)
                else ""
            )
            if creature_id == selected_id:
                target_row = row
                break

        self.creature_selector.setCurrentIndex(target_row)
        self._display_selected_creature()

    def _selected_creature_id(self) -> str:
        """Returns the selected creature's durable public-lore ID."""

        index = self.creature_selector.currentIndex()
        if index < 0:
            return ""
        raw_creature = self.creature_selector.itemData(index)
        return (
            str(raw_creature.get("creature_id", "") or "").strip()
            if isinstance(raw_creature, dict)
            else ""
        )

    def _display_selected_creature(self, *_args: Any) -> None:
        """Displays only the selected public miscellaneous record."""

        index = self.creature_selector.currentIndex()
        if index < 0:
            self.creature_image_label.clear()
            self.creature_image_label.hide()
            self.details_output.clear()
            return

        raw_creature = self.creature_selector.itemData(index)
        if not isinstance(raw_creature, dict):
            self.creature_image_label.clear()
            self.creature_image_label.hide()
            self.details_output.clear()
            return

        name = str(raw_creature.get("name", "")).strip()
        details = str(raw_creature.get("details", "")).strip()
        repository = self.repository()
        creature_key = str(
            raw_creature.get("creature_id", "") or name
        ).strip().casefold()
        asset = (
            repository.get_visual_asset("bestiary", creature_key)
            if repository is not None and creature_key
            else None
        )
        _set_generated_image(
            self.creature_image_label,
            self.visual_asset_path(asset),
            maximum_width=384,
            maximum_height=384,
            accessible_name=f"Generated image of {name or 'creature'}",
        )
        sections = [f"# {name}"] if name else []
        if details:
            sections.append(details)
        _set_markdown_text(self.details_output, "\n\n".join(sections))
