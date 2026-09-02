from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class AlchemyNotebookScreen(RepositoryBackedWidget):
    """Crafting screen for useful items/materials and recipes."""

    def __init__(self, *, playtesting_tools: bool = False) -> None:
        super().__init__()

        self.playtesting_tools = bool(playtesting_tools)
        self.tabs = QTabWidget()
        self._reagent_rows: list[dict[str, Any]] = []
        self._recipe_ingredient_rows: list[dict[str, Any]] = []
        self._refreshing_reagents = False
        self._reagent_sort_column = 0
        self._reagent_sort_order = Qt.SortOrder.AscendingOrder
        self._recipe_sort_column = 0
        self._recipe_sort_order = Qt.SortOrder.AscendingOrder
        self._recipe_rows: list[dict[str, Any]] = []

        self._setup_reagents_tab()
        self._setup_recipes_tab()

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads all crafting data."""

        repository = self.repository()

        if repository is None:
            self.reagent_table.setRowCount(0)
            self.recipe_table.setRowCount(0)
            self.recipe_reagent_combo.clear()
            return

        self._refresh_reagents(repository)
        self._refresh_recipes(repository)

    def _setup_reagents_tab(self) -> None:
        """Builds the structured useful item/material discovery tab."""

        self.reagent_table = _AppTableWidget(0, 7)
        self.reagent_table.setHorizontalHeaderLabels(
            [
                "Name", "Category", "Description", "Typical Areas", "Uses",
                "Estimated Value", "Notes",
            ]
        )
        self.reagent_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.reagent_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.reagent_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        _allow_selected_row_deselection(self.reagent_table)
        _enable_table_sorting(self.reagent_table, self._sort_reagents_by_column)
        self.reagent_table.horizontalHeader().setSortIndicator(
            self._reagent_sort_column,
            self._reagent_sort_order,
        )
        _configure_wrapping_table(self.reagent_table, {2, 3, 4, 6})
        self.reagent_table.itemSelectionChanged.connect(self._load_selected_reagent)

        self.reagent_name_input = QLineEdit()
        self.reagent_name_input.setPlaceholderText("Item or material name")
        self.reagent_category_combo = _NoWheelComboBox()
        for category in CRAFTING_INGREDIENT_CATEGORIES:
            self.reagent_category_combo.addItem(category, category)
        self.reagent_description_input = QLineEdit()
        self.reagent_description_input.setPlaceholderText("Short description")
        self.reagent_location_input = QLineEdit()
        self.reagent_location_input.setPlaceholderText(
            "General areas, e.g. Forests, Caves"
        )
        self.reagent_uses_input = QLineEdit()
        self.reagent_uses_input.setPlaceholderText(
            "Generalized symptoms/effects, e.g. sleep aid, pain relief"
        )
        self.reagent_rarity_combo = _NoWheelComboBox()
        for rarity in CRAFTING_ITEM_RARITIES:
            self.reagent_rarity_combo.addItem(rarity, rarity)
        self.reagent_value_input = _NoWheelSpinBox()
        self.reagent_value_input.setRange(0, 999_999_999)
        self.reagent_notes_input = QTextEdit()
        self.reagent_notes_input.setPlaceholderText(
            "Rarity is added automatically; include other useful player notes here."
        )
        self.reagent_notes_input.setMaximumHeight(80)

        save_button = QPushButton("Add / Update Item")
        save_button.clicked.connect(self._save_reagent)
        new_button = QPushButton("New Item")
        new_button.clicked.connect(self._clear_reagent_form)

        button_row = QHBoxLayout()
        button_row.addWidget(save_button)
        button_row.addWidget(new_button)
        button_row.addStretch()

        form = QFormLayout()
        form.addRow("Name:", self.reagent_name_input)
        form.addRow("Category:", self.reagent_category_combo)
        form.addRow("Description:", self.reagent_description_input)
        form.addRow("Typical Areas:", self.reagent_location_input)
        form.addRow("Uses:", self.reagent_uses_input)
        form.addRow("Rarity:", self.reagent_rarity_combo)
        form.addRow("Estimated Value (base units):", self.reagent_value_input)
        form.addRow("Notes:", self.reagent_notes_input)
        form.addRow(button_row)

        form_widget = QWidget()
        form_widget.setLayout(form)
        form_widget.setVisible(self.playtesting_tools)

        layout = QVBoxLayout()
        layout.addWidget(form_widget)
        layout.addWidget(self.reagent_table)

        wrapper = QWidget()
        wrapper.setLayout(layout)
        self.tabs.addTab(wrapper, "Items")

    def _setup_recipes_tab(self) -> None:
        """Builds the structured recipe discovery tab."""

        self.recipe_table = _AppTableWidget(0, 4)
        self.recipe_table.setHorizontalHeaderLabels(
            ["Name", "Ingredients", "Estimated Value", "Notes"]
        )
        self.recipe_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _enable_table_sorting(self.recipe_table, self._sort_recipes_by_column)
        self.recipe_table.horizontalHeader().setSortIndicator(
            self._recipe_sort_column,
            self._recipe_sort_order,
        )
        _configure_wrapping_table(self.recipe_table, {1, 3})
        self.recipe_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recipe_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        _allow_selected_row_deselection(self.recipe_table)
        self.recipe_table.itemSelectionChanged.connect(self._update_recipe_craftability)

        self.recipe_craftability_label = QLabel(
            "Select a recipe to see what you can craft."
        )
        self.recipe_craftability_label.setWordWrap(True)
        self.recipe_craftability_label.setObjectName("recipeCraftabilityLabel")

        self.recipe_name_input = QLineEdit()
        self.recipe_name_input.setPlaceholderText("Recipe name")
        self.recipe_result_input = QLineEdit()
        self.recipe_result_input.setPlaceholderText("Recipe result")
        self.recipe_value_input = _NoWheelSpinBox()
        self.recipe_value_input.setRange(0, 999_999_999)
        self.recipe_notes_input = QTextEdit()
        self.recipe_notes_input.setPlaceholderText(
            "State purpose/effect, strength or outcome, onset, duration, and key conditions. "
            "Use Unknown or Not applicable when needed."
        )

        self.recipe_reagent_combo = QComboBox()
        self.recipe_reagent_combo.setEditable(True)
        self.recipe_reagent_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.recipe_reagent_combo.setPlaceholderText(
            "Search the Crafting Items list"
        )
        self.recipe_reagent_combo.setMinimumWidth(220)
        self.recipe_reagent_choice_model = QStringListModel(self)
        self.recipe_reagent_completer = QCompleter(
            self.recipe_reagent_choice_model,
            self.recipe_reagent_combo,
        )
        self.recipe_reagent_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.recipe_reagent_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.recipe_reagent_completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion
        )
        self.recipe_reagent_completer.activated.connect(
            self._select_recipe_reagent_label
        )
        self.recipe_reagent_combo.setCompleter(self.recipe_reagent_completer)
        self.recipe_reagent_line_edit = self.recipe_reagent_combo.lineEdit()
        self.recipe_reagent_combo.installEventFilter(self)
        if self.recipe_reagent_line_edit is not None:
            self.recipe_reagent_line_edit.installEventFilter(self)
            self.recipe_reagent_line_edit.textEdited.connect(
                self._show_recipe_reagent_choices
            )
        self.recipe_quantity_input = QSpinBox()
        self.recipe_quantity_input.setRange(1, 999)
        self.recipe_quantity_input.setValue(1)
        self.recipe_measure_amount_input = QSpinBox()
        self.recipe_measure_amount_input.setRange(1, 99999)
        self.recipe_measure_amount_input.setValue(1)
        self.recipe_measure_unit_combo = QComboBox()
        for unit in COMMON_MEASUREMENT_UNITS:
            self.recipe_measure_unit_combo.addItem(unit, unit)

        add_ingredient_button = QPushButton("Add Ingredient")
        add_ingredient_button.clicked.connect(self._add_recipe_ingredient)
        remove_ingredient_button = QPushButton("Remove Ingredient")
        remove_ingredient_button.clicked.connect(self._remove_recipe_ingredient)

        ingredient_controls = QHBoxLayout()
        ingredient_controls.addWidget(self.recipe_reagent_combo, 2)
        ingredient_controls.addWidget(QLabel("Count:"))
        ingredient_controls.addWidget(self.recipe_quantity_input)
        ingredient_controls.addWidget(QLabel("Measure:"))
        ingredient_controls.addWidget(self.recipe_measure_amount_input)
        ingredient_controls.addWidget(self.recipe_measure_unit_combo)
        ingredient_controls.addWidget(add_ingredient_button)
        ingredient_controls.addWidget(remove_ingredient_button)

        self.recipe_ingredient_table = _AppTableWidget(0, 4)
        self.recipe_ingredient_table.setHorizontalHeaderLabels(
            ["Item", "Count", "Amount", "Unit"]
        )
        self.recipe_ingredient_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recipe_ingredient_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.recipe_ingredient_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        _allow_selected_row_deselection(self.recipe_ingredient_table)
        _use_soft_table_selection(self.recipe_ingredient_table)

        save_button = QPushButton("Add / Update Recipe")
        save_button.clicked.connect(self._add_recipe)
        new_button = QPushButton("New Recipe")
        new_button.clicked.connect(self._clear_recipe_form)

        button_row = QHBoxLayout()
        button_row.addWidget(save_button)
        button_row.addWidget(new_button)
        button_row.addStretch()

        form = QFormLayout()
        form.addRow("Name:", self.recipe_name_input)
        form.addRow("Ingredient:", ingredient_controls)
        form.addRow("Selected Ingredients:", self.recipe_ingredient_table)
        form.addRow("Result:", self.recipe_result_input)
        form.addRow("Estimated Value (base units):", self.recipe_value_input)
        form.addRow("Notes:", self.recipe_notes_input)
        form.addRow(button_row)

        form_widget = QWidget()
        form_widget.setLayout(form)
        form_widget.setVisible(self.playtesting_tools)

        layout = QVBoxLayout()
        layout.addWidget(form_widget)
        layout.addWidget(self.recipe_craftability_label)
        layout.addWidget(self.recipe_table)

        wrapper = QWidget()
        wrapper.setLayout(layout)
        self.tabs.addTab(wrapper, "Recipes")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Keeps the editable Ingredient selector behaving like a dropdown."""

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and hasattr(self, "recipe_reagent_combo")
            and (
                watched is self.recipe_reagent_combo
                or watched is getattr(self, "recipe_reagent_line_edit", None)
            )
            and self.recipe_reagent_combo.count() > 0
        ):
            QTimer.singleShot(0, self._show_recipe_reagent_choices)

        return super().eventFilter(watched, event)

    def _refresh_reagents(self, repository: SaveRepository) -> None:
        """Reloads the known crafting item/material table."""

        reagents = repository.list_crafting_items()
        denominations = repository.get_currency_denominations()
        selected_name = self.reagent_name_input.text().strip()
        reagents.sort(
            key=self._reagent_sort_key,
            reverse=_sort_descending(self._reagent_sort_order),
        )
        self._reagent_rows = reagents
        self._refreshing_reagents = True
        self.reagent_table.clearSelection()
        self.reagent_table.setRowCount(len(reagents))

        for row_index, reagent in enumerate(reagents):
            self.reagent_table.setItem(row_index, 0, _table_item(str(reagent.get("name", ""))))
            self.reagent_table.setItem(row_index, 1, _table_item(str(reagent.get("category", ""))))
            self.reagent_table.setItem(row_index, 2, _table_item(str(reagent.get("description", ""))))
            self.reagent_table.setItem(row_index, 3, _table_item(str(reagent.get("location", ""))))
            self.reagent_table.setItem(row_index, 4, _table_item(_join_list(reagent.get("uses", []))))
            value_base_units = _safe_int(reagent.get("value_base_units", 0), 0)
            self.reagent_table.setItem(
                row_index,
                5,
                _table_item(
                    format_currency_amount(value_base_units, denominations),
                    value_base_units,
                ),
            )
            self.reagent_table.setItem(
                row_index,
                6,
                _table_item(str(reagent.get("notes", ""))),
            )

        _resize_wrapping_table_rows(self.reagent_table)
        self._refreshing_reagents = False

        if selected_name:
            for row_index, reagent in enumerate(reagents):
                if str(reagent.get("name", "")).casefold() == selected_name.casefold():
                    self.reagent_table.selectRow(row_index)
                    break

        self._refresh_recipe_reagent_choices(repository)

    def _refresh_recipes(self, repository: SaveRepository) -> None:
        """Reloads the recipe table."""

        recipes = repository.list_crafting_recipes()
        denominations = repository.get_currency_denominations()
        recipes.sort(
            key=self._recipe_sort_key,
            reverse=_sort_descending(self._recipe_sort_order),
        )
        self._recipe_rows = recipes
        self.recipe_table.setRowCount(len(recipes))

        for row_index, recipe in enumerate(recipes):
            self.recipe_table.setItem(row_index, 0, _table_item(str(recipe.get("name", ""))))
            self.recipe_table.setItem(row_index, 1, _table_item(format_recipe_ingredients(recipe.get("ingredients", []))))
            value_base_units = _safe_int(recipe.get("value_base_units", 0), 0)
            self.recipe_table.setItem(
                row_index,
                2,
                _table_item(
                    format_currency_amount(value_base_units, denominations),
                    value_base_units,
                ),
            )
            self.recipe_table.setItem(row_index, 3, _table_item(str(recipe.get("notes", ""))))

        _resize_wrapping_table_rows(self.recipe_table)
        self._update_recipe_craftability()

    def _update_recipe_craftability(self) -> None:
        """Shows exact owned ingredient counts and the limiting reagent."""

        repository = self.repository()
        row_index = self.recipe_table.currentRow()
        if repository is None or row_index < 0 or row_index >= len(self._recipe_rows):
            self.recipe_craftability_label.setText(
                "Select a recipe to see what you can craft."
            )
            return

        recipe = self._recipe_rows[row_index]
        inventory: dict[str, tuple[int, str]] = {}
        for item in repository.list_inventory_items():
            name = str(item.get("name", "")).strip().casefold()
            metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
            key = str(metadata.get("item_uuid", "")).strip() or name
            unit = str(item.get("quantity_unit", "each") or "each").strip()
            previous_quantity, _previous_unit = inventory.get(key, (0, unit))
            inventory[key] = (
                previous_quantity + max(0, _safe_int(item.get("quantity", 0), 0)),
                unit,
            )

        details: list[str] = []
        craftable = None
        limiting: list[str] = []
        for ingredient in normalize_recipe_ingredients(recipe.get("ingredients", [])):
            name = str(ingredient.get("reagent_name", "")).strip()
            key = str(ingredient.get("item_uuid", "")).strip() or name.casefold()
            recipe_unit = str(ingredient.get("measure_unit", "each") or "each")
            required = max(1, _safe_int(ingredient.get("quantity", 1), 1)) * max(
                1, _safe_int(ingredient.get("measure_amount", 1), 1)
            )
            owned, inventory_unit = inventory.get(key, (0, recipe_unit))
            if inventory_unit.casefold() != recipe_unit.casefold():
                craftable = 0
                limiting.append(name)
                details.append(
                    f"{name}: {owned} {inventory_unit} owned / "
                    f"{required} {recipe_unit} required (unit mismatch)"
                )
                continue
            possible = owned // required
            craftable = possible if craftable is None else min(craftable, possible)
            if possible == craftable:
                limiting.append(name)
            details.append(
                f"{name}: {owned} {inventory_unit} owned / "
                f"{required} {recipe_unit} per item"
            )

        if craftable is None:
            self.recipe_craftability_label.setText("This recipe has no ingredients.")
            return
        limit_text = ", ".join(limiting) if limiting else "none"
        self.recipe_craftability_label.setText(
            f"Can currently craft: {craftable} × {recipe.get('name', 'item')}\n"
            f"Ingredients:\n  • " + "\n  • ".join(details) + "\n"
            f"Limiting reagent: {limit_text}"
        )

    def _save_reagent(self) -> None:
        """Adds or updates a known crafting item/material."""

        repository = self.repository()

        if repository is None:
            return

        name = self.reagent_name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Name", "Item name is required.")
            return

        repository.add_crafting_item(
            name=name,
            category=str(self.reagent_category_combo.currentData() or "Material"),
            description=self.reagent_description_input.text(),
            location=self.reagent_location_input.text(),
            uses=_split_list(self.reagent_uses_input.text()),
            rarity=str(self.reagent_rarity_combo.currentData() or "Common"),
            notes=self.reagent_notes_input.toPlainText(),
            value_base_units=self.reagent_value_input.value(),
        )

        self.reagent_name_input.clear()
        self.reagent_category_combo.setCurrentIndex(0)
        self.reagent_description_input.clear()
        self.reagent_location_input.clear()
        self.reagent_uses_input.clear()
        self.reagent_rarity_combo.setCurrentIndex(0)
        self.reagent_value_input.setValue(0)
        self.reagent_notes_input.clear()

        self.refresh()
        self.notify_repository_changed()

    def _load_selected_reagent(self) -> None:
        """Loads the selected crafting item/material row into the edit controls."""

        if self._refreshing_reagents:
            return

        if not self.reagent_table.selectedItems():
            return

        row_index = self.reagent_table.currentRow()

        if row_index < 0 or row_index >= len(self._reagent_rows):
            return

        reagent = self._reagent_rows[row_index]
        self.reagent_name_input.setText(str(reagent.get("name", "")))
        _set_combo_to_data(
            self.reagent_category_combo,
            str(reagent.get("category", "Material")),
        )
        self.reagent_description_input.setText(str(reagent.get("description", "")))
        self.reagent_location_input.setText(str(reagent.get("location", "")))
        self.reagent_uses_input.setText(_join_list(reagent.get("uses", [])))
        _set_combo_to_data(
            self.reagent_rarity_combo,
            str(reagent.get("rarity", "Common")),
        )
        self.reagent_value_input.setValue(
            max(0, _safe_int(reagent.get("value_base_units", 0), 0))
        )
        self.reagent_notes_input.setPlainText(str(reagent.get("notes", "")))

    def _clear_reagent_form(self) -> None:
        """Clears item edit controls and table selection."""

        self.reagent_table.clearSelection()
        self.reagent_name_input.clear()
        self.reagent_category_combo.setCurrentIndex(0)
        self.reagent_description_input.clear()
        self.reagent_location_input.clear()
        self.reagent_uses_input.clear()
        self.reagent_rarity_combo.setCurrentIndex(0)
        self.reagent_value_input.setValue(0)
        self.reagent_notes_input.clear()

    def _refresh_recipe_reagent_choices(self, repository: SaveRepository) -> None:
        """Reloads the category-filtered item dropdown used by recipe ingredients."""

        current_text = self.recipe_reagent_combo.currentText().strip()
        self.recipe_reagent_combo.clear()
        choices = _crafting_ingredient_catalog_choices(
            repository.list_crafting_items()
        )
        choice_labels: list[str] = []

        for item in choices:
            name = str(item.get("name", "")).strip()
            category = str(item.get("category", "")).strip()
            if name:
                label = f"{name} ({category})"
                self.recipe_reagent_combo.addItem(label, name)
                choice_labels.append(label)

        self.recipe_reagent_choice_model.setStringList(choice_labels)

        if current_text:
            for index in range(self.recipe_reagent_combo.count()):
                item_name = str(self.recipe_reagent_combo.itemData(index)).strip()
                if (
                    item_name.casefold() == current_text.casefold()
                    or self.recipe_reagent_combo.itemText(index).casefold()
                    == current_text.casefold()
                ):
                    self.recipe_reagent_combo.setCurrentIndex(index)
                    return

        if self.recipe_reagent_combo.count() > 0:
            self.recipe_reagent_combo.setCurrentIndex(0)

    @Slot(str)
    def _show_recipe_reagent_choices(self, _text: str = "") -> None:
        """Shows the searchable Ingredient choices popup."""

        if self.recipe_reagent_combo.count() <= 0:
            return

        if self.recipe_reagent_line_edit is None:
            self.recipe_reagent_combo.showPopup()
            return

        self.recipe_reagent_completer.setCompletionPrefix(
            self.recipe_reagent_line_edit.text()
        )
        self.recipe_reagent_completer.complete()

    @Slot(str)
    def _select_recipe_reagent_label(self, label: str) -> None:
        """Selects an Ingredient dropdown row by its visible label."""

        clean_label = str(label).strip()

        if not clean_label:
            return

        for index in range(self.recipe_reagent_combo.count()):
            if self.recipe_reagent_combo.itemText(index) == clean_label:
                self.recipe_reagent_combo.setCurrentIndex(index)
                return

    def _add_recipe_ingredient(self) -> None:
        """Adds a structured known-item ingredient to the draft recipe."""

        repository = self.repository()
        if repository is None:
            return
        selected_name = self._selected_recipe_reagent_name()

        if not selected_name:
            QMessageBox.warning(
                self,
                "Unknown Item",
                (
                    "Choose an item categorized as "
                    f"{CRAFTING_INGREDIENT_CATEGORY_NAMES}."
                ),
            )
            return

        ingredient = normalize_recipe_ingredient(
            {
                "reagent_name": selected_name,
                "item_uuid": next(
                    (
                        str(item.get("metadata", {}).get("item_uuid", ""))
                        for item in repository.list_item_catalog()
                        if str(item.get("name", "")).casefold() == selected_name.casefold()
                    ),
                    "",
                ),
                "quantity": self.recipe_quantity_input.value(),
                "measure_amount": self.recipe_measure_amount_input.value(),
                "measure_unit": self.recipe_measure_unit_combo.currentData(),
            }
        )

        if ingredient is None:
            return

        for index, existing in enumerate(self._recipe_ingredient_rows):
            if (
                str(existing.get("reagent_name", "")).casefold()
                == ingredient["reagent_name"].casefold()
            ):
                self._recipe_ingredient_rows[index] = ingredient
                self._refresh_recipe_ingredient_table()
                return

        self._recipe_ingredient_rows.append(ingredient)
        self._refresh_recipe_ingredient_table()

    def _remove_recipe_ingredient(self) -> None:
        """Removes the selected ingredient from the draft recipe."""

        row_index = self.recipe_ingredient_table.currentRow()

        if row_index < 0 or row_index >= len(self._recipe_ingredient_rows):
            return

        del self._recipe_ingredient_rows[row_index]
        self._refresh_recipe_ingredient_table()

    def _refresh_recipe_ingredient_table(self) -> None:
        """Reloads the draft recipe ingredient table."""

        self.recipe_ingredient_table.setRowCount(len(self._recipe_ingredient_rows))

        for row_index, ingredient in enumerate(self._recipe_ingredient_rows):
            self.recipe_ingredient_table.setItem(
                row_index,
                0,
                _table_item(str(ingredient.get("reagent_name", ""))),
            )
            self.recipe_ingredient_table.setItem(
                row_index,
                1,
                _table_item(str(ingredient.get("quantity", 1))),
            )
            self.recipe_ingredient_table.setItem(
                row_index,
                2,
                _table_item(str(ingredient.get("measure_amount", 1))),
            )
            self.recipe_ingredient_table.setItem(
                row_index,
                3,
                _table_item(str(ingredient.get("measure_unit", "each"))),
            )

        self.recipe_ingredient_table.resizeColumnsToContents()

    def _selected_recipe_reagent_name(self) -> str:
        """Returns the exact selected known item name, or blank."""

        requested_name = self.recipe_reagent_combo.currentText().strip()

        if not requested_name:
            return ""

        for index in range(self.recipe_reagent_combo.count()):
            name = str(self.recipe_reagent_combo.itemData(index)).strip()
            label = str(self.recipe_reagent_combo.itemText(index)).strip()
            if (
                name.casefold() == requested_name.casefold()
                or label.casefold() == requested_name.casefold()
            ):
                return name

        return ""

    def _clear_recipe_form(self) -> None:
        """Clears recipe edit controls and draft ingredients."""

        self.recipe_name_input.clear()
        self._recipe_ingredient_rows.clear()
        self._refresh_recipe_ingredient_table()
        self.recipe_result_input.clear()
        self.recipe_value_input.setValue(0)
        self.recipe_notes_input.clear()
        self.recipe_quantity_input.setValue(1)
        self.recipe_measure_amount_input.setValue(1)
        self.recipe_measure_unit_combo.setCurrentIndex(0)

    def _add_recipe(self) -> None:
        """Adds or updates a known recipe."""

        repository = self.repository()

        if repository is None:
            return

        name = self.recipe_name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Name", "Recipe name is required.")
            return

        if not self._recipe_ingredient_rows:
            QMessageBox.warning(
                self,
                "Missing Ingredients",
                "Add at least one known item ingredient.",
            )
            return

        repository.add_crafting_recipe(
            name=name,
            ingredients=list(self._recipe_ingredient_rows),
            result=self.recipe_result_input.text(),
            notes=self.recipe_notes_input.toPlainText(),
            value_base_units=self.recipe_value_input.value(),
        )

        self._clear_recipe_form()

        self.refresh()
        self.notify_repository_changed()

    def _sort_reagents_by_column(self, column_index: int) -> None:
        """Sorts crafting items/materials by a clicked header column."""

        self._reagent_sort_column, self._reagent_sort_order = _update_sort_state(
            self.reagent_table,
            self._reagent_sort_column,
            self._reagent_sort_order,
            column_index,
        )
        self.refresh()

    def _sort_recipes_by_column(self, column_index: int) -> None:
        """Sorts recipes by a clicked header column."""

        self._recipe_sort_column, self._recipe_sort_order = _update_sort_state(
            self.recipe_table,
            self._recipe_sort_column,
            self._recipe_sort_order,
            column_index,
        )
        self.refresh()

    def _reagent_sort_key(self, reagent: dict[str, Any]) -> tuple[str, str]:
        """Returns the active crafting item/material sort key."""

        name = str(reagent.get("name", "")).casefold()

        if self._reagent_sort_column == 1:
            return str(reagent.get("category", "")).casefold(), name

        if self._reagent_sort_column == 2:
            return str(reagent.get("description", "")).casefold(), name

        if self._reagent_sort_column == 3:
            return str(reagent.get("location", "")).casefold(), name

        if self._reagent_sort_column == 4:
            return _join_list(reagent.get("uses", [])).casefold(), name

        if self._reagent_sort_column == 5:
            return str(reagent.get("value_base_units", 0)).zfill(12), name

        if self._reagent_sort_column == 6:
            return str(reagent.get("notes", "")).casefold(), name

        return name, name

    def _recipe_sort_key(self, recipe: dict[str, Any]) -> tuple[str, str]:
        """Returns the active recipe sort key."""

        name = str(recipe.get("name", "")).casefold()

        if self._recipe_sort_column == 1:
            return format_recipe_ingredients(recipe.get("ingredients", [])).casefold(), name

        if self._recipe_sort_column == 2:
            return str(recipe.get("value_base_units", 0)).zfill(12), name

        if self._recipe_sort_column == 3:
            return str(recipe.get("notes", "")).casefold(), name

        return name, name
