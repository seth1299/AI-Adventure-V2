from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class InventoryLocationPanel(QGroupBox):
    """Compact list of inventory items stored at one free-text location."""

    SORT_OPTIONS = (
        ("Name", "name"),
        ("Category", "category"),
        ("Price", "price"),
        ("Quantity", "quantity"),
    )

    def __init__(
        self,
        location: str,
        items: list[dict[str, Any]],
        on_item_clicked: Callable[[dict[str, Any]], None],
        *,
        sort_field: str = "name",
        sort_descending: bool = False,
        secondary_sort_field: str = "",
        secondary_sort_descending: bool = False,
        on_sort_changed: Callable[[str, bool, str, bool], None] | None = None,
    ) -> None:
        super().__init__(f"{_inventory_location_label(location)} ({len(items)})")
        self.location = location
        self._items = [dict(item) for item in items]
        self._on_item_clicked = on_item_clicked
        self._on_sort_changed = on_sort_changed
        self._secondary_sort_field_preference = str(secondary_sort_field or "")
        self.item_buttons: list[QPushButton] = []
        self.group_separators: list[QFrame] = []
        layout = QVBoxLayout()

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Sort by:"))
        self.sort_field_combo = QComboBox()
        self.sort_field_combo.setObjectName("inventoryLocationSortField")
        for label, value in self.SORT_OPTIONS:
            self.sort_field_combo.addItem(label, value)
        _set_combo_to_data(self.sort_field_combo, sort_field)
        controls.addWidget(self.sort_field_combo, 1)

        self.sort_direction_combo = QComboBox()
        self.sort_direction_combo.setObjectName("inventoryLocationSortDirection")
        self.sort_direction_combo.addItem("Ascending", False)
        self.sort_direction_combo.addItem("Descending", True)
        direction_index = self.sort_direction_combo.findData(bool(sort_descending))
        self.sort_direction_combo.setCurrentIndex(max(0, direction_index))
        controls.addWidget(self.sort_direction_combo)
        layout.addLayout(controls)

        secondary_controls = QHBoxLayout()
        secondary_controls.addWidget(QLabel("Then by:"))
        self.secondary_sort_field_combo = QComboBox()
        self.secondary_sort_field_combo.setObjectName(
            "inventoryLocationSecondarySortField"
        )
        self.secondary_sort_field_combo.addItem("None", "")
        for label, value in self.SORT_OPTIONS:
            self.secondary_sort_field_combo.addItem(label, value)
        _set_combo_to_data(self.secondary_sort_field_combo, secondary_sort_field)
        secondary_controls.addWidget(self.secondary_sort_field_combo, 1)

        self.secondary_sort_direction_combo = QComboBox()
        self.secondary_sort_direction_combo.setObjectName(
            "inventoryLocationSecondarySortDirection"
        )
        self.secondary_sort_direction_combo.addItem("Ascending", False)
        self.secondary_sort_direction_combo.addItem("Descending", True)
        secondary_direction_index = self.secondary_sort_direction_combo.findData(
            bool(secondary_sort_descending)
        )
        self.secondary_sort_direction_combo.setCurrentIndex(
            max(0, secondary_direction_index)
        )
        secondary_controls.addWidget(self.secondary_sort_direction_combo)
        layout.addLayout(secondary_controls)

        self.item_list_layout = QVBoxLayout()
        layout.addLayout(self.item_list_layout)
        layout.addStretch(1)
        self.setLayout(layout)

        self.sort_field_combo.currentIndexChanged.connect(self._sorting_changed)
        self.sort_direction_combo.currentIndexChanged.connect(self._sorting_changed)
        self.secondary_sort_field_combo.currentIndexChanged.connect(
            self._sorting_changed
        )
        self.secondary_sort_direction_combo.currentIndexChanged.connect(
            self._sorting_changed
        )
        self._sync_secondary_sort_controls()
        self._render_items()

    def _sorting_changed(self, _index: int) -> None:
        """Applies this location's independent sort selection immediately."""

        if self.sender() is self.secondary_sort_field_combo:
            self._secondary_sort_field_preference = str(
                self.secondary_sort_field_combo.currentData() or ""
            )
        sort_field = str(self.sort_field_combo.currentData() or "name")
        sort_descending = bool(self.sort_direction_combo.currentData())
        secondary_sort_field = str(
            self.secondary_sort_field_combo.currentData() or ""
        )
        secondary_sort_descending = bool(
            self.secondary_sort_direction_combo.currentData()
        )
        self._sync_secondary_sort_controls()
        if self._on_sort_changed is not None:
            self._on_sort_changed(
                sort_field,
                sort_descending,
                secondary_sort_field,
                secondary_sort_descending,
            )
        self._render_items()

    def _sync_secondary_sort_controls(self) -> None:
        """Keeps secondary choices distinct from the primary sort field."""

        primary_sort_field = str(self.sort_field_combo.currentData() or "name")
        current_secondary = self._secondary_sort_field_preference
        if current_secondary == primary_sort_field:
            current_secondary = ""

        self.secondary_sort_field_combo.blockSignals(True)
        self.secondary_sort_field_combo.clear()
        self.secondary_sort_field_combo.addItem("None", "")
        for label, value in self.SORT_OPTIONS:
            if value != primary_sort_field:
                self.secondary_sort_field_combo.addItem(label, value)
        _set_combo_to_data(self.secondary_sort_field_combo, current_secondary)
        self.secondary_sort_field_combo.blockSignals(False)

        has_secondary_sort = bool(self.secondary_sort_field_combo.currentData())
        self.secondary_sort_direction_combo.setEnabled(has_secondary_sort)

    def _render_items(self) -> None:
        """Rebuilds the item buttons in the selected order."""

        while self.item_list_layout.count():
            layout_item = self.item_list_layout.takeAt(0)
            if layout_item is None:
                continue
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()

        self.item_buttons.clear()
        self.group_separators.clear()
        sort_field = str(self.sort_field_combo.currentData() or "name")
        sort_descending = bool(self.sort_direction_combo.currentData())
        secondary_sort_field = str(
            self.secondary_sort_field_combo.currentData() or ""
        )
        secondary_sort_descending = bool(
            self.secondary_sort_direction_combo.currentData()
        )
        sorted_items = sort_inventory_items(
            self._items,
            primary_field=sort_field,
            primary_descending=sort_descending,
            secondary_field=secondary_sort_field,
            secondary_descending=secondary_sort_descending,
        )
        previous_group: Any = None
        for index, item in enumerate(sorted_items):
            group = self._item_group_key(item, sort_field)
            if index > 0 and group != previous_group:
                separator = QFrame()
                separator.setObjectName("inventorySortGroupSeparator")
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setFrameShadow(QFrame.Shadow.Sunken)
                separator.setToolTip("New sort group")
                self.item_list_layout.addWidget(separator)
                self.group_separators.append(separator)
            previous_group = group

            quantity = max(0, _safe_int(item.get("quantity", 0), 0))
            unit = str(item.get("quantity_unit", "each") or "each")
            category = str(item.get("category", "Item") or "Item")
            display_name = _inventory_item_display_name(
                item.get("name", "Unnamed Item"),
                quantity,
                unit,
            )
            display_quantity = _inventory_quantity_display(quantity, unit)
            button = QPushButton(
                f"{display_name}\n{display_quantity}  ·  {category}"
            )
            button.setObjectName("inventoryItemButton")
            button.setMinimumHeight(52)
            button.setToolTip("Open all item details")
            button.clicked.connect(
                lambda _checked=False, selected=dict(item): self._on_item_clicked(selected)
            )
            self.item_list_layout.addWidget(button)
            self.item_buttons.append(button)

    @staticmethod
    def _item_group_key(item: dict[str, Any], sort_field: str) -> Any:
        """Returns the dynamic group represented by the active sort option."""

        if sort_field == "category":
            return str(item.get("category", "Item") or "Item").casefold()
        if sort_field == "price":
            return max(0, _safe_int(item.get("value_base_units", 0), 0))
        if sort_field == "quantity":
            return max(0, _safe_int(item.get("quantity", 0), 0))
        name = str(item.get("name", "")).strip().casefold()
        return name[:1] or "#"


class InventoryScreen(RepositoryBackedWidget):
    """Location-grouped inventory journal with modal item details."""

    def __init__(self, *, playtesting_tools: bool = False) -> None:
        super().__init__()

        self.playtesting_tools = bool(playtesting_tools)
        self._selected_item_name = ""
        self._loading_item_editor = False
        self._inventory_items: dict[str, dict[str, Any]] = {}
        self._catalog_by_name: dict[str, dict[str, Any]] = {}
        self._denominations: list[dict[str, Any]] = []
        self._location_sort_settings: dict[
            str,
            tuple[str, bool, str, bool],
        ] = {}
        self.location_panels: list[InventoryLocationPanel] = []
        self.currency_label = QLabel("Currency: 0")

        self.inventory_scroll = QScrollArea()
        self.inventory_scroll.setWidgetResizable(True)
        self.inventory_scroll.setObjectName("inventoryLocationScroll")
        self.inventory_panel_host = QWidget()
        self.inventory_panel_layout = QGridLayout()
        self.inventory_panel_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.inventory_panel_layout.setHorizontalSpacing(14)
        self.inventory_panel_layout.setVerticalSpacing(14)
        self.inventory_panel_host.setLayout(self.inventory_panel_layout)
        self.inventory_scroll.setWidget(self.inventory_panel_host)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Inventory"))
        layout.addWidget(self.currency_label)
        layout.addWidget(self.inventory_scroll, 1)

        if self.playtesting_tools:
            layout.addWidget(self._build_playtesting_item_editor())

        self.setLayout(layout)

    def _build_playtesting_item_editor(self) -> QGroupBox:
        """Builds manual item controls used only by the Playtesting build."""

        self.item_name_input = QLineEdit()
        self.item_type_combo = QComboBox()
        self.item_type_combo.addItem("General Item", "Item")
        self.item_type_combo.addItem("Weapon", "Weapon")
        self.item_type_combo.addItem("Armor / Shield", "Armor")
        self.item_type_combo.addItem("Ammunition", "Ammunition")
        self.item_type_combo.currentIndexChanged.connect(
            lambda _index: self._sync_item_editor_type()
        )
        self.item_quantity_input = QSpinBox()
        self.item_quantity_input.setRange(1, 9999)
        self.item_quantity_input.setValue(1)
        self.item_quantity_unit_input = QLineEdit("each")
        self.item_storage_location_combo = QComboBox()
        self.item_storage_location_combo.setEditable(True)
        self.item_storage_location_combo.addItem("Actively Carried", "actively_carried")
        self.item_storage_location_combo.addItem("Home", "home")
        self.item_value_input = QSpinBox()
        self.item_value_input.setRange(0, 999999999)
        self.item_description_input = QLineEdit()

        self.weapon_hands_combo = QComboBox()
        self.weapon_hands_combo.addItem("One-handed", "one-handed")
        self.weapon_hands_combo.addItem("Two-handed", "two-handed")
        self.weapon_damage_input = QLineEdit("1d6")
        self.weapon_attack_skill_input = QLineEdit("Melee")
        self.weapon_range_input = QSpinBox()
        self.weapon_range_input.setRange(0, 999999)
        self.weapon_range_input.setValue(DEFAULT_ATTACK_RANGE_FEET)
        self.weapon_ammunition_type_input = QLineEdit()
        self.weapon_ammunition_type_input.setPlaceholderText(
            "Optional, e.g. 9mm Round"
        )
        self.weapon_clip_size_input = QSpinBox()
        self.weapon_clip_size_input.setRange(0, 9999)
        self.weapon_bullets_per_attack_input = QSpinBox()
        self.weapon_bullets_per_attack_input.setRange(1, 9999)
        self.ammunition_type_name_input = QLineEdit()
        self.ammunition_type_name_input.setPlaceholderText(
            "Type matched by a weapon, e.g. 9mm Round"
        )

        self.armor_body_parts_input = QLineEdit("Torso")
        self.armor_body_parts_input.setPlaceholderText(
            "Head, Torso, Arms, Hands, Legs, Feet, Off Hand"
        )
        self.armor_rating_input = QSpinBox()
        self.armor_rating_input.setRange(0, 99)
        self.armor_rating_input.setValue(1)

        save_button = QPushButton("Add Item")
        save_button.clicked.connect(self._save_playtesting_item)
        self.save_item_button = save_button
        remove_button = QPushButton("Remove Selected Item")
        remove_button.clicked.connect(self._remove_selected_item)
        clear_button = QPushButton("Clear Editor")
        clear_button.clicked.connect(self._clear_item_editor)

        general_form = QFormLayout()
        general_form.addRow("Name:", self.item_name_input)
        general_form.addRow("Type:", self.item_type_combo)
        general_form.addRow("Quantity:", self.item_quantity_input)
        general_form.addRow("Unit:", self.item_quantity_unit_input)
        general_form.addRow("Storage:", self.item_storage_location_combo)
        general_form.addRow("Value (base units):", self.item_value_input)
        general_form.addRow("Description:", self.item_description_input)

        self.weapon_group = QGroupBox("Weapon Metadata")
        weapon_form = QFormLayout()
        weapon_form.addRow("Hands:", self.weapon_hands_combo)
        weapon_form.addRow("Damage:", self.weapon_damage_input)
        weapon_form.addRow("Attack Skill:", self.weapon_attack_skill_input)
        weapon_form.addRow("Attack Range (feet):", self.weapon_range_input)
        weapon_form.addRow(
            "Ammunition Required:",
            self.weapon_ammunition_type_input,
        )
        weapon_form.addRow("Clip Size:", self.weapon_clip_size_input)
        weapon_form.addRow(
            "Bullets per Attack:",
            self.weapon_bullets_per_attack_input,
        )
        self.weapon_group.setLayout(weapon_form)

        self.armor_group = QGroupBox("Armor Metadata")
        armor_form = QFormLayout()
        armor_form.addRow("Covers:", self.armor_body_parts_input)
        armor_form.addRow("Armor Bonus:", self.armor_rating_input)
        self.armor_group.setLayout(armor_form)

        self.ammunition_group = QGroupBox("Ammunition Metadata")
        ammunition_form = QFormLayout()
        ammunition_form.addRow(
            "Ammunition Type:",
            self.ammunition_type_name_input,
        )
        self.ammunition_group.setLayout(ammunition_form)

        editor_layout = QVBoxLayout()
        editor_layout.addLayout(general_form)
        editor_layout.addWidget(self.weapon_group)
        editor_layout.addWidget(self.armor_group)
        editor_layout.addWidget(self.ammunition_group)
        editor_layout.addWidget(_button_row(save_button, remove_button, clear_button))

        editor = QGroupBox("Playtesting Item Editor")
        editor.setLayout(editor_layout)
        self._sync_item_editor_type()
        return editor

    def refresh(self) -> None:
        """Reloads the location panels and their item buttons."""

        repository = self.repository()

        if repository is None:
            self.currency_label.setText("Currency: 0")
            self._inventory_items.clear()
            self._catalog_by_name.clear()
            self._replace_location_panels({})
            return

        items = repository.list_inventory_items()
        denominations = repository.get_currency_denominations()
        self._denominations = denominations
        catalog = repository.list_item_catalog()
        self._catalog_by_name = {
            str(entry.get("name", "")).casefold(): entry
            for entry in catalog
            if str(entry.get("name", "")).strip()
        }
        catalog_by_uuid = {
            str(entry.get("metadata", {}).get("item_uuid", "")): entry
            for entry in catalog
            if isinstance(entry.get("metadata"), dict)
            and str(entry.get("metadata", {}).get("item_uuid", "")).strip()
        }
        balance_base_units = _safe_int(
            repository.get_state_value("currency.balance", "0"),
            0,
        )
        self.currency_label.setText(
            f"Currency: {format_currency_amount(balance_base_units, denominations)}"
        )
        grouped_items: dict[str, list[dict[str, Any]]] = {}
        self._inventory_items = {}
        for raw_item in items:
            item = dict(raw_item)
            metadata = item.get("metadata", {})
            item_uuid = (
                str(metadata.get("item_uuid", ""))
                if isinstance(metadata, dict)
                else ""
            )
            item["catalog_entry"] = (
                catalog_by_uuid.get(item_uuid)
                or self._catalog_by_name.get(str(item.get("name", "")).casefold())
            )
            name = str(item.get("name", ""))
            if name:
                self._inventory_items[name.casefold()] = item
            location = " ".join(
                str(item.get("storage_location", "actively_carried") or "actively_carried")
                .strip()
                .split()
            )[:120] or "actively_carried"
            grouped_items.setdefault(location, []).append(item)

        self._replace_location_panels(grouped_items)

    def _replace_location_panels(
        self,
        grouped_items: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Rebuilds the modular location-card grid."""

        while self.inventory_panel_layout.count():
            layout_item = self.inventory_panel_layout.takeAt(0)
            if layout_item is None:
                continue
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()

        self.location_panels.clear()
        if not grouped_items:
            empty_label = QLabel("No inventory items are currently stored.")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.inventory_panel_layout.addWidget(empty_label, 0, 0, 1, 4)
            return

        def location_key(location: str) -> tuple[int, str]:
            folded = location.casefold()
            priority = 0 if folded == "actively_carried" else 1 if folded == "home" else 2
            return priority, folded

        ordered_locations = sorted(grouped_items, key=location_key)
        location_count = len(ordered_locations)
        for index, location in enumerate(ordered_locations):
            (
                sort_field,
                sort_descending,
                secondary_sort_field,
                secondary_sort_descending,
            ) = self._location_sort_settings.get(
                location,
                ("name", False, "", False),
            )

            def remember_sort(
                field: str,
                descending: bool,
                secondary_field: str,
                secondary_descending: bool,
                panel_location: str = location,
            ) -> None:
                self._remember_location_sort(
                    panel_location,
                    field,
                    descending,
                    secondary_field,
                    secondary_descending,
                )

            panel = InventoryLocationPanel(
                location,
                grouped_items[location],
                self._open_item_details,
                sort_field=sort_field,
                sort_descending=sort_descending,
                secondary_sort_field=secondary_sort_field,
                secondary_sort_descending=secondary_sort_descending,
                on_sort_changed=remember_sort,
            )
            is_unpaired_final_panel = location_count % 2 == 1 and index == location_count - 1
            column = 1 if is_unpaired_final_panel else (0 if index % 2 == 0 else 2)
            self.inventory_panel_layout.addWidget(panel, index // 2, column, 1, 2)
            self.location_panels.append(panel)

        for column in range(4):
            self.inventory_panel_layout.setColumnStretch(column, 1)

    def _remember_location_sort(
        self,
        location: str,
        sort_field: str,
        sort_descending: bool,
        secondary_sort_field: str,
        secondary_sort_descending: bool,
    ) -> None:
        """Keeps each location's sort choice across inventory refreshes."""

        self._location_sort_settings[location] = (
            sort_field,
            sort_descending,
            secondary_sort_field,
            secondary_sort_descending,
        )

    def _open_item_details(self, item: dict[str, Any]) -> None:
        """Opens one blocking item-detail dialog and primes playtesting edits."""

        selected_name = str(item.get("name", ""))
        self._selected_item_name = selected_name
        if self.playtesting_tools:
            self._load_selected_item(selected_name)
        catalog_entry = item.get("catalog_entry")
        repository = self.repository()
        image_asset = (
            repository.get_visual_asset(
                "inventory",
                str(
                    (item.get("metadata") or {}).get("item_uuid", "")
                    if isinstance(item.get("metadata"), dict)
                    else ""
                ).strip()
                or selected_name.casefold(),
            )
            if repository is not None and selected_name
            else None
        )
        dialog = InventoryItemDetailsDialog(
            item=item,
            catalog_entry=catalog_entry if isinstance(catalog_entry, dict) else None,
            denominations=self._denominations,
            image_path=self.visual_asset_path(image_asset),
            show_structured_details=self.playtesting_tools,
            parent=self,
        )
        dialog.exec()

    def _sync_item_editor_type(self) -> None:
        """Shows metadata fields for the selected playtesting item type."""

        item_type = str(self.item_type_combo.currentData() or "Item")
        self.weapon_group.setVisible(item_type == "Weapon")
        self.armor_group.setVisible(item_type == "Armor")
        self.ammunition_group.setVisible(item_type == "Ammunition")

    def _load_selected_item(self, selected_name: str | None = None) -> None:
        """Loads one clicked inventory item into the playtesting editor."""

        if not self.playtesting_tools or self._loading_item_editor:
            return

        repository = self.repository()

        selected_name = str(selected_name or self._selected_item_name).strip()
        if not selected_name or repository is None:
            return

        selected_item = self._inventory_items.get(selected_name.casefold())

        if selected_item is None:
            return

        metadata = item_metadata(selected_item)
        item_type = str(metadata.get("item_type", "Item"))
        self._selected_item_name = selected_name
        self.item_name_input.setText(selected_name)
        _set_combo_to_data(self.item_type_combo, item_type)
        self.item_quantity_input.setValue(max(1, int(selected_item.get("quantity", 1))))
        self.item_quantity_unit_input.setText(str(selected_item.get("quantity_unit", "each")))
        storage_value = str(selected_item.get("storage_location", "actively_carried") or "actively_carried").strip()
        if storage_value.casefold() in {"home", "actively_carried"}:
            _set_combo_to_data(self.item_storage_location_combo, storage_value)
        else:
            self.item_storage_location_combo.setEditText(storage_value)
        self.item_value_input.setValue(max(0, int(selected_item.get("value_base_units", 0))))
        self.item_description_input.setText(str(selected_item.get("description", "")))
        _set_combo_to_data(
            self.weapon_hands_combo,
            str(metadata.get("weapon_hands", "one-handed")),
        )
        self.weapon_damage_input.setText(str(metadata.get("damage", "1d6")))
        self.weapon_attack_skill_input.setText(
            str(metadata.get("attack_skill", "Melee"))
        )
        self.weapon_range_input.setValue(
            max(
                0,
                int(
                    metadata.get(
                        "attack_range_feet",
                        DEFAULT_ATTACK_RANGE_FEET,
                    )
                ),
            )
        )
        self.weapon_ammunition_type_input.setText(
            str(metadata.get("ammunition_type_required", ""))
        )
        self.weapon_clip_size_input.setValue(
            max(0, int(metadata.get("clip_size", 0)))
        )
        self.weapon_bullets_per_attack_input.setValue(
            max(1, int(metadata.get("bullets_per_attack", 1)))
        )
        self.ammunition_type_name_input.setText(
            str(metadata.get("ammunition_type", selected_name))
        )
        self.armor_body_parts_input.setText(
            ", ".join(str(part) for part in metadata.get("covers_body_parts", []))
        )
        self.armor_rating_input.setValue(
            max(0, int(metadata.get("armor_rating", 0)))
        )
        self.save_item_button.setText("Update Item")
        self._sync_item_editor_type()

    def _save_playtesting_item(self) -> None:
        """Adds or updates one manually defined inventory item."""

        repository = self.repository()

        if repository is None:
            return

        name = self.item_name_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Item Name", "Enter an item name.")
            return

        item_type = str(self.item_type_combo.currentData() or "Item")
        metadata: dict[str, Any] = {"item_type": item_type}

        if item_type == "Weapon":
            metadata.update(
                {
                    "weapon_hands": (
                        self.weapon_hands_combo.currentData() or "one-handed"
                    ),
                    "damage": self.weapon_damage_input.text(),
                    "attack_skill": (
                        self.weapon_attack_skill_input.text().strip() or "Melee"
                    ),
                    "attack_range_feet": self.weapon_range_input.value(),
                    "ammunition_type_required": (
                        self.weapon_ammunition_type_input.text().strip()
                    ),
                    "clip_size": self.weapon_clip_size_input.value(),
                    "bullets_per_attack": (
                        self.weapon_bullets_per_attack_input.value()
                    ),
                }
            )
        elif item_type == "Armor":
            metadata.update(
                {
                    "covers_body_parts": _split_list(
                        self.armor_body_parts_input.text()
                    ),
                    "armor_rating": self.armor_rating_input.value(),
                }
            )
        elif item_type == "Ammunition":
            metadata["ammunition_type"] = (
                self.ammunition_type_name_input.text().strip() or name
            )

        metadata["quantity_unit"] = self.item_quantity_unit_input.text().strip() or "each"
        metadata["storage_location"] = (
            self.item_storage_location_combo.currentText().strip()[:120]
            or "actively_carried"
        )

        if self._selected_item_name:
            repository.modify_inventory_item(
                target_name=self._selected_item_name,
                new_name=name,
                category=item_type,
                description=self.item_description_input.text().strip(),
                quantity=self.item_quantity_input.value(),
                value_base_units=self.item_value_input.value(),
                metadata=metadata,
            )
        else:
            repository.add_inventory_item(
                name,
                item_type,
                self.item_quantity_input.value(),
                self.item_description_input.text().strip(),
                self.item_value_input.value(),
                metadata=metadata,
            )

        self._selected_item_name = name
        self.refresh()
        self.notify_repository_changed()

    def _remove_selected_item(self) -> None:
        """Removes the selected inventory stack."""

        repository = self.repository()

        if repository is None or not self._selected_item_name:
            return

        selected_item = next(
            (
                item
                for item in repository.list_inventory_items()
                if str(item.get("name", "")).casefold()
                == self._selected_item_name.casefold()
            ),
            None,
        )

        if selected_item is None:
            return

        repository.remove_inventory_item(
            self._selected_item_name,
            max(1, int(selected_item.get("quantity", 1))),
        )
        self._clear_item_editor()
        self.refresh()
        self.notify_repository_changed()

    def _clear_item_editor(self) -> None:
        """Resets the manual item editor to a blank new item."""

        self._selected_item_name = ""
        self._loading_item_editor = True

        try:
            self.item_name_input.clear()
            _set_combo_to_data(self.item_type_combo, "Item")
            self.item_quantity_input.setValue(1)
            self.item_value_input.setValue(0)
            self.item_description_input.clear()
            _set_combo_to_data(self.weapon_hands_combo, "one-handed")
            self.weapon_damage_input.setText("1d6")
            self.weapon_attack_skill_input.setText("Melee")
            self.weapon_range_input.setValue(DEFAULT_ATTACK_RANGE_FEET)
            self.weapon_ammunition_type_input.clear()
            self.weapon_clip_size_input.setValue(0)
            self.weapon_bullets_per_attack_input.setValue(1)
            self.ammunition_type_name_input.clear()
            self.armor_body_parts_input.setText("Torso")
            self.armor_rating_input.setValue(1)
            self.save_item_button.setText("Add Item")
            self._sync_item_editor_type()
        finally:
            self._loading_item_editor = False
