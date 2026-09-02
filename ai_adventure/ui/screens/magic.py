from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class MagicScreen(RepositoryBackedWidget):
    """Player magic journal with deterministic resource consumption."""

    def __init__(self) -> None:
        super().__init__()
        self._spell_rows: list[dict[str, Any]] = []

        self.summary_label = QLabel("Magic is not configured for this save.")
        self.summary_label.setWordWrap(True)
        self.resources_label = QLabel("")
        self.resources_label.setWordWrap(True)

        self.spells_table = _AppTableWidget(0, 5)
        self.spells_table.setHorizontalHeaderLabels(
            ["Spell", "Tier", "School", "Cost", "Prepared"]
        )
        self.spells_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.spells_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.spells_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        _configure_wrapping_table(self.spells_table, {0, 2})
        self.spells_table.itemSelectionChanged.connect(self._load_selected_spell)

        self.spell_details_label = QLabel("Select a spell to view its details.")
        self.spell_details_label.setWordWrap(True)
        self.cast_spell_button = QPushButton("Cast / Record Use")
        self.cast_spell_button.setEnabled(False)
        self.cast_spell_button.clicked.connect(self._cast_selected_spell)
        self.prepare_spell_button = QPushButton("Prepare / Unprepare")
        self.prepare_spell_button.setEnabled(False)
        self.prepare_spell_button.clicked.connect(self._toggle_selected_spell_prepared)

        details_group = QGroupBox("Selected Spell")
        details_layout = QVBoxLayout()
        details_layout.addWidget(self.spell_details_label)
        details_layout.addStretch()
        details_layout.addWidget(self.cast_spell_button)
        details_layout.addWidget(self.prepare_spell_button)
        details_group.setLayout(details_layout)

        spells_layout = QHBoxLayout()
        spells_layout.addWidget(self.spells_table, 3)
        spells_layout.addWidget(details_group, 2)
        spells_widget = QWidget()
        spells_widget.setLayout(spells_layout)

        self.effects_table = _AppTableWidget(0, 4)
        self.effects_table.setHorizontalHeaderLabels(
            ["Effect", "Target", "Duration", "Concentration"]
        )
        self.effects_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _configure_wrapping_table(self.effects_table, {0, 1, 2})

        self.cast_history_table = _AppTableWidget(0, 4)
        self.cast_history_table.setHorizontalHeaderLabels(
            ["Spell", "Tier", "Resource Spent", "Cast At"]
        )
        self.cast_history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _configure_wrapping_table(self.cast_history_table, {0, 2})

        tabs = QTabWidget()
        tabs.addTab(spells_widget, "Known Spells")
        tabs.addTab(self.effects_table, "Active Effects")
        tabs.addTab(self.cast_history_table, "Cast History")

        layout = QVBoxLayout()
        layout.addWidget(self.summary_label)
        layout.addWidget(self.resources_label)
        layout.addWidget(tabs)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads configuration, spells, resources, effects, and cast history."""

        repository = self.repository()
        if repository is None:
            self._spell_rows = []
            self.spells_table.setRowCount(0)
            self.effects_table.setRowCount(0)
            self.cast_history_table.setRowCount(0)
            self.summary_label.setText("Magic is not configured for this save.")
            self.resources_label.clear()
            return

        magic = repository.get_magic_configuration()
        mode = str(magic["casting_mode"])
        mode_label = MAGIC_CASTING_MODE_LABELS.get(mode, mode.title())
        tradition = str(magic.get("tradition", "")).strip() or "Unspecified tradition"
        self.summary_label.setText(
            "This world does not contain magic."
            if not magic.get("world_contains_magic", True)
            else (
                f"{mode_label} Casting · {tradition}"
                if magic["enabled"]
                else "Player magic is disabled for this save."
            )
        )
        pools = repository.list_magic_resource_pools()
        self.resources_label.setText(
            "Resources: "
            + (
                " · ".join(
                    f"{pool['name']} {pool['current_amount']}/{pool['maximum_amount']}"
                    for pool in pools
                )
                if pools
                else "No consumable casting resource"
            )
        )

        self._spell_rows = repository.list_character_spells()
        self.spells_table.setRowCount(len(self._spell_rows))
        for row_index, spell in enumerate(self._spell_rows):
            cost = (
                f"{spell['mana_cost']} Mana"
                if mode == "mana"
                else ("At-will" if int(spell["tier"]) == 0 else f"Tier {spell['tier']} slot")
                if mode == "tiered"
                else "Narrative"
            )
            values = (
                spell["name"], spell["tier"], spell["school"], cost,
                "Yes" if spell["prepared"] else "No",
            )
            for column, value in enumerate(values):
                item = _table_item(str(value))
                item.setData(Qt.ItemDataRole.UserRole, spell["spell_id"])
                self.spells_table.setItem(row_index, column, item)
        _resize_wrapping_table_rows(self.spells_table)

        effects = repository.list_active_magic_effects()
        self.effects_table.setRowCount(len(effects))
        for row_index, effect in enumerate(effects):
            duration = (
                "Ongoing"
                if int(effect["end_elapsed_minutes"]) < 0
                else f"Until minute {effect['end_elapsed_minutes']}"
            )
            values = (
                effect["name"], effect["target"], duration,
                "Yes" if effect["requires_concentration"] else "No",
            )
            for column, value in enumerate(values):
                self.effects_table.setItem(row_index, column, _table_item(str(value)))

        history = repository.list_spell_cast_history()
        self.cast_history_table.setRowCount(len(history))
        for row_index, cast in enumerate(history):
            spent = (
                "None"
                if int(cast["amount_spent"]) == 0
                else f"{cast['amount_spent']} from {cast['pool_id']}"
            )
            values = (cast.get("spell_name") or "Unknown Spell", cast["cast_tier"], spent, cast["cast_at"])
            for column, value in enumerate(values):
                self.cast_history_table.setItem(row_index, column, _table_item(str(value)))

        self._load_selected_spell()

    def _selected_spell(self) -> dict[str, Any] | None:
        row = self.spells_table.currentRow()
        if row < 0 or row >= len(self._spell_rows):
            return None
        return self._spell_rows[row]

    def _load_selected_spell(self) -> None:
        spell = self._selected_spell()
        if spell is None:
            self.spell_details_label.setText("Select a spell to view its details.")
            self.cast_spell_button.setEnabled(False)
            self.prepare_spell_button.setEnabled(False)
            return
        details = [
            f"{spell['name']} · Tier {spell['tier']} · {spell['school'] or 'Unclassified'}",
            spell["description"] or "No description recorded.",
            f"Casting Time: {spell['casting_time']}",
            f"Range: {spell['range'] or 'Unspecified'}",
            f"Duration: {spell['duration'] or 'Unspecified'}",
            f"Requirements: {spell['requirements'] or 'None recorded'}",
            f"Mana Cost: {spell['mana_cost']}",
        ]
        self.spell_details_label.setText("\n\n".join(details))
        repository = self.repository()
        enabled = bool(repository and repository.get_magic_configuration()["enabled"])
        self.cast_spell_button.setEnabled(enabled)
        self.prepare_spell_button.setEnabled(enabled)

    def _cast_selected_spell(self) -> None:
        repository = self.repository()
        spell = self._selected_spell()
        if repository is None or spell is None:
            return
        magic = repository.get_magic_configuration()
        cast_tier = int(spell["tier"])
        if magic["casting_mode"] == "tiered" and cast_tier > 0:
            cast_tier, accepted = QInputDialog.getInt(
                self,
                "Cast Spell",
                "Slot tier to consume:",
                cast_tier,
                cast_tier,
                9,
            )
            if not accepted:
                return
        confirmation = QMessageBox.question(
            self,
            "Record Spell Cast",
            f"Cast {spell['name']} and consume its required resource?",
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        result = repository.cast_character_spell(
            str(spell["spell_id"]),
            cast_tier=cast_tier,
        )
        if result.get("status") != "cast":
            QMessageBox.warning(self, "Spell Not Cast", str(result.get("message", "Cast rejected.")))
            return
        self.notify_repository_changed()
        self.refresh()

    def _toggle_selected_spell_prepared(self) -> None:
        repository = self.repository()
        spell = self._selected_spell()
        if repository is None or spell is None:
            return
        repository.set_character_spell_prepared(
            str(spell["spell_id"]), not bool(spell["prepared"])
        )
        self.notify_repository_changed()
        self.refresh()
