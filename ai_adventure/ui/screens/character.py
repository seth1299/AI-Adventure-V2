from __future__ import annotations
from typing import cast

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403
from ai_adventure.ui.screens.combat import CombatScreen


class CharacterScreen(RepositoryBackedWidget):
    """Dungeons-and-Dragons-style character sheet."""

    def __init__(
        self,
        *,
        playtesting_tools: bool = False,
        tts_enabled: bool = True,
    ) -> None:
        super().__init__()

        self.playtesting_tools = bool(playtesting_tools)
        self.tts_enabled = bool(tts_enabled)
        self._loading_character = False
        self._saving_character = False
        self._editing_profile = False
        self._last_saved_character_payload: dict[str, Any] | None = None
        self._profile_markdown_target: QTextEdit | None = None
        self.name_input = QLineEdit()
        self.name_input.editingFinished.connect(self._save_character)
        self.name_pronunciation_input = QLineEdit()
        self.name_pronunciation_input.setPlaceholderText(
            "Optional: kah-tha-lah, or /kəˈθɑlə/ for exact IPA"
        )
        self.name_pronunciation_input.editingFinished.connect(self._save_character)
        self.pronouns_combo = _NoWheelComboBox()
        for pronouns in CHARACTER_PRONOUN_OPTIONS:
            self.pronouns_combo.addItem(pronouns, pronouns)
        self.pronouns_combo.addItem("Other", "other")
        self.custom_pronouns_input = QLineEdit()
        self.custom_pronouns_input.setPlaceholderText(
            "Enter custom pronouns, such as Xe/Xem"
        )
        self.pronouns_combo.currentIndexChanged.connect(
            self._handle_pronouns_changed
        )
        self.custom_pronouns_input.editingFinished.connect(self._save_character)
        self.health_current_input = QSpinBox()
        self.health_current_input.setRange(0, 9999)
        self.health_current_input.valueChanged.connect(
            lambda _value: self._handle_character_spin_changed()
        )
        self.health_max_input = QSpinBox()
        self.health_max_input.setRange(1, 9999)
        self.health_max_input.setValue(DEFAULT_PLAYER_MAX_HEALTH)
        self.health_max_input.valueChanged.connect(
            lambda _value: self._handle_character_spin_changed()
        )
        self.initiative_bonus_input = QSpinBox()
        self.initiative_bonus_input.setRange(-99, 99)
        self.initiative_bonus_input.valueChanged.connect(
            lambda _value: self._save_character()
        )

        if not self.playtesting_tools:
            health_tooltip = (
                "Health is managed by gameplay. Direct health editing is available "
                "only in the Playtesting build."
            )
            self.health_current_input.setEnabled(False)
            self.health_max_input.setEnabled(False)
            self.health_current_input.setToolTip(health_tooltip)
            self.health_max_input.setToolTip(health_tooltip)
            self.initiative_bonus_input.setEnabled(False)
        self.armor_rating_label = QLabel(str(DEFAULT_BASE_ARMOR_RATING))
        self.weapon_damage_label = QLabel(DEFAULT_UNARMED_DAMAGE)
        self.equipment_combos: dict[str, QComboBox] = {}
        self._equipment_selection = {
            slot: ""
            for slot in EQUIPMENT_SLOTS
        }

        self.appearance_input = QTextEdit()
        self.backstory_input = QTextEdit()
        self.notes_input = QTextEdit()
        for text_edit in [
            self.appearance_input,
            self.backstory_input,
            self.notes_input,
        ]:
            text_edit.setAcceptRichText(False)
            text_edit.installEventFilter(self)

        self.appearance_input.setPlaceholderText("Visible traits, clothing, manner, scars, voice...")
        self.backstory_input.setPlaceholderText("Origin, important history, relationships, goals...")
        self.notes_input.setPlaceholderText("Player notes about this character...")

        for slot in EQUIPMENT_SLOTS:
            combo = QComboBox()
            combo.setToolTip(
                (
                    "Optional hand slot. One-handed weapons may be placed in "
                    "either hand; an owned copy can occupy only one slot."
                )
                if slot in {"Main Hand", "Off Hand"}
                else (
                    "Forced armor slot. Equipping armor fills every body slot "
                    "listed in that item's coverage metadata."
                )
            )
            combo.currentIndexChanged.connect(lambda _index, slot=slot: self._equipment_changed(slot))
            self.equipment_combos[slot] = combo

        identity_group = QGroupBox("Identity")
        identity_layout = QFormLayout()
        identity_layout.addRow("Name:", self.name_input)
        identity_layout.addRow("Pronouns:", self.pronouns_combo)
        self.custom_pronouns_label = QLabel("Custom Pronouns:")
        identity_layout.addRow(
            self.custom_pronouns_label,
            self.custom_pronouns_input,
        )
        self.name_pronunciation_label = QLabel("Name Pronunciation:")
        identity_layout.addRow(
            self.name_pronunciation_label,
            self.name_pronunciation_input,
        )
        identity_layout.addRow("Appearance:", self.appearance_input)
        identity_layout.addRow("Backstory:", self.backstory_input)
        identity_layout.addRow("Notes:", self.notes_input)
        identity_group.setLayout(identity_layout)
        self._profile_markdown_target = self.backstory_input

        self.stats_group = QGroupBox("Vitals")
        stats_layout = QFormLayout()
        stats_layout.addRow("Health:", _spin_pair_row(self.health_current_input, self.health_max_input))
        stats_layout.addRow("Initiative Bonus:", self.initiative_bonus_input)
        stats_layout.addRow("Armor Rating:", self.armor_rating_label)
        stats_layout.addRow("Weapon Damage:", self.weapon_damage_label)
        self.stats_group.setLayout(stats_layout)

        self.equipment_group = QGroupBox("Equipment")
        equipment_layout = QFormLayout()
        for slot in EQUIPMENT_SLOTS:
            equipment_layout.addRow(f"{slot}:", self.equipment_combos[slot])
        self.equipment_group.setLayout(equipment_layout)

        self.condition_group = QGroupBox("Status")
        condition_layout = QFormLayout()
        self.condition_label = QLabel("Healthy")
        self.condition_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        condition_layout.addRow("Condition:", self.condition_label)
        self.condition_group.setLayout(condition_layout)

        self.portrait_group = QGroupBox("Portrait")
        portrait_layout = QVBoxLayout()
        self.portrait_label = ClickableImageLabel()
        self.portrait_label.setMinimumWidth(220)
        portrait_layout.addWidget(self.portrait_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.select_portrait_button = QPushButton("Select Image...")
        self.select_portrait_button.clicked.connect(self._select_portrait_image)
        self.create_portrait_button = QPushButton("Create new image for me")
        self.create_portrait_button.clicked.connect(self._create_portrait_image)
        portrait_layout.addWidget(
            _button_row(self.select_portrait_button, self.create_portrait_button)
        )
        self.portrait_group.setLayout(portrait_layout)
        self.portrait_group.hide()

        self.profile_title_label = QLabel()
        self.profile_title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.profile_title_label.setStyleSheet("font-size: 24px; font-weight: 700;")

        self.profile_pronouns_label = QLabel()
        self.profile_pronunciation_label = QLabel()
        self.profile_pronouns_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.profile_pronunciation_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        profile_identity_layout = QFormLayout()
        profile_identity_layout.addRow("Pronouns:", self.profile_pronouns_label)
        profile_identity_layout.addRow(
            "Name Pronunciation:", self.profile_pronunciation_label
        )
        self.profile_identity_display = QWidget()
        self.profile_identity_display.setLayout(profile_identity_layout)

        self.profile_appearance_display = MarkdownDisplay()
        self.profile_backstory_display = MarkdownDisplay()
        self.profile_notes_display = MarkdownDisplay()
        for display in (
            self.profile_appearance_display,
            self.profile_backstory_display,
            self.profile_notes_display,
        ):
            display.setMinimumHeight(52)
            display.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.MinimumExpanding,
            )

        self.profile_appearance_group = QGroupBox("Appearance")
        appearance_layout = QVBoxLayout()
        appearance_layout.addWidget(self.profile_appearance_display)
        self.profile_appearance_group.setLayout(appearance_layout)
        self.profile_backstory_group = QGroupBox("Backstory")
        backstory_layout = QVBoxLayout()
        backstory_layout.addWidget(self.profile_backstory_display)
        self.profile_backstory_group.setLayout(backstory_layout)
        self.profile_notes_group = QGroupBox("Notes")
        notes_layout = QVBoxLayout()
        notes_layout.addWidget(self.profile_notes_display)
        self.profile_notes_group.setLayout(notes_layout)

        read_profile_layout = QVBoxLayout()
        read_profile_layout.addWidget(self.profile_identity_display)
        read_profile_layout.addWidget(self.profile_appearance_group)
        read_profile_layout.addWidget(self.profile_backstory_group)
        read_profile_layout.addWidget(self.profile_notes_group)
        read_profile_layout.addStretch()
        self.read_profile_page = QWidget()
        self.read_profile_page.setLayout(read_profile_layout)

        markdown_toolbar = QHBoxLayout()
        self._profile_markdown_buttons: list[QPushButton] = []
        self._add_profile_markdown_button(markdown_toolbar, "B", "Bold", "bold")
        self._add_profile_markdown_button(markdown_toolbar, "I", "Italic", "italic")
        self._add_profile_markdown_button(markdown_toolbar, "U", "Underline", "underline")
        self._add_profile_markdown_button(markdown_toolbar, "• List", "Bulleted list", "bullet")
        self._add_profile_markdown_button(markdown_toolbar, "1. List", "Numbered list", "numbered")
        self._add_profile_markdown_button(markdown_toolbar, "H", "Heading", "heading")
        self._add_profile_markdown_button(markdown_toolbar, "Quote", "Block quote", "quote")
        self._add_profile_markdown_button(markdown_toolbar, "Code", "Inline code", "code")
        markdown_toolbar.addStretch()
        editor_layout = QVBoxLayout()
        editor_layout.addLayout(markdown_toolbar)
        editor_layout.addWidget(identity_group)
        self.edit_profile_page = QWidget()
        self.edit_profile_page.setLayout(editor_layout)

        self.profile_pages = QStackedWidget()
        self.profile_pages.addWidget(self.read_profile_page)
        self.profile_pages.addWidget(self.edit_profile_page)

        self.edit_profile_button = QPushButton("Edit Profile")
        self.edit_profile_button.clicked.connect(self._toggle_profile_editor)

        header_layout = QVBoxLayout()
        header_layout.addWidget(self.profile_title_label)
        header_layout.addWidget(self.portrait_group, 0, Qt.AlignmentFlag.AlignHCenter)
        header_layout.addWidget(self.condition_group)
        header_layout.addWidget(self.profile_pages)

        secondary_layout = QHBoxLayout()
        secondary_layout.addWidget(self.stats_group)
        secondary_layout.addWidget(self.equipment_group)

        sheet_layout = QVBoxLayout()
        sheet_layout.addLayout(header_layout)
        sheet_layout.addLayout(secondary_layout)
        edit_button_layout = QHBoxLayout()
        edit_button_layout.addStretch()
        edit_button_layout.addWidget(self.edit_profile_button)
        sheet_layout.addLayout(edit_button_layout)

        self.setLayout(sheet_layout)
        self._set_pronouns(DEFAULT_CHARACTER_PRONOUNS)
        self._sync_contextual_controls(None)
        self._set_profile_editing(False)

    def _add_profile_markdown_button(
        self,
        layout: QHBoxLayout,
        label: str,
        tooltip: str,
        action: str,
    ) -> None:
        """Adds a portable-Markdown formatting control for profile editors."""

        button = QPushButton(label)
        button.setToolTip(tooltip)
        button.setMaximumWidth(78)
        button.clicked.connect(
            lambda _checked=False, name=action: self._apply_profile_markdown(name)
        )
        if action == "bold":
            button.setShortcut("Ctrl+B")
        elif action == "italic":
            button.setShortcut("Ctrl+I")
        elif action == "underline":
            button.setShortcut("Ctrl+U")
        layout.addWidget(button)
        self._profile_markdown_buttons.append(button)

    def _apply_profile_markdown(self, action: str) -> None:
        """Applies portable Markdown to the active profile text editor."""

        editor = self._profile_markdown_target
        if editor is None:
            return

        cursor = editor.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        text = editor.toPlainText()
        wrappers = {
            "bold": ("**", "**", "bold text"),
            "italic": ("*", "*", "italic text"),
            "underline": ("<u>", "</u>", "underlined text"),
            "code": ("`", "`", "code"),
        }
        if action in wrappers:
            prefix, suffix, placeholder = wrappers[action]
            updated, selection_start, selection_end = wrap_markdown_text(
                text,
                start,
                end,
                prefix,
                suffix,
                placeholder=placeholder,
            )
        else:
            cursor.setPosition(start)
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            block_start = cursor.position()
            cursor.setPosition(end)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            block_end = cursor.position()
            selected_lines = text[block_start:block_end]
            if action == "numbered":
                replacement = prefix_markdown_lines(selected_lines, "", numbered=True)
            else:
                prefixes = {"bullet": "- ", "heading": "## ", "quote": "> "}
                replacement = prefix_markdown_lines(selected_lines, prefixes[action])
            updated = text[:block_start] + replacement + text[block_end:]
            selection_start = block_start
            selection_end = block_start + len(replacement)

        editor.setPlainText(updated)
        cursor = editor.textCursor()
        cursor.setPosition(selection_start)
        cursor.setPosition(selection_end, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.setFocus()

    def _toggle_profile_editor(self) -> None:
        """Toggles the read-only profile view and its Markdown editors."""

        self._set_profile_editing(not self._editing_profile)
        if self._editing_profile:
            self.name_input.setFocus()

    def _set_profile_editing(self, editing: bool) -> None:
        """Selects the profile page and keeps the bottom action descriptive."""

        self._editing_profile = bool(editing)
        self.profile_pages.setCurrentWidget(
            self.edit_profile_page if self._editing_profile else self.read_profile_page
        )
        self.edit_profile_button.setText(
            "View Profile" if self._editing_profile else "Edit Profile"
        )

        enabled = self._editing_profile
        for button in self._profile_markdown_buttons:
            button.setEnabled(enabled)

    def _sync_profile_preview(self, *, has_portrait: bool) -> None:
        """Copies the current profile values into the read-only Markdown view."""

        self.profile_title_label.setText(self.name_input.text().strip() or "Unnamed Character")
        self.profile_pronouns_label.setText(self._pronouns_from_controls() or "Not specified")
        pronunciation = self.name_pronunciation_input.text().strip()
        self.profile_pronunciation_label.setText(pronunciation or "Not specified")
        _set_markdown_text(
            self.profile_appearance_display,
            self.appearance_input.toPlainText(),
            preserve_blank_lines=True,
        )
        _set_markdown_text(
            self.profile_backstory_display,
            self.backstory_input.toPlainText(),
            preserve_blank_lines=True,
        )
        _set_markdown_text(
            self.profile_notes_display,
            self.notes_input.toPlainText(),
            preserve_blank_lines=True,
        )
        self.profile_appearance_group.setVisible(not has_portrait)

    def _handle_pronouns_changed(self, _index: int = -1) -> None:
        """Shows custom pronoun entry when needed and saves the selection."""

        self._sync_pronoun_controls()
        if (
            self.pronouns_combo.currentData() == "other"
            and not self.custom_pronouns_input.text().strip()
        ):
            return
        self._save_character()

    def _sync_pronoun_controls(self) -> None:
        """Shows custom pronoun entry only when Other is selected."""

        is_custom = self.pronouns_combo.currentData() == "other"
        self.custom_pronouns_label.setVisible(is_custom)
        self.custom_pronouns_input.setVisible(is_custom)

    def _pronouns_from_controls(self) -> str:
        """Returns canonical standard or custom pronouns from the sheet."""

        if self.pronouns_combo.currentData() == "other":
            return normalize_character_pronouns(self.custom_pronouns_input.text())
        return normalize_character_pronouns(self.pronouns_combo.currentData())

    def _set_pronouns(self, pronouns: Any) -> None:
        """Loads canonical standard or custom pronouns into the sheet."""

        canonical = normalize_character_pronouns(pronouns)
        index = self.pronouns_combo.findData(canonical)
        is_custom = index < 0
        if is_custom:
            index = self.pronouns_combo.findData("other")
        self.pronouns_combo.setCurrentIndex(max(0, index))
        self.custom_pronouns_input.setText(canonical if is_custom else "")
        self._sync_pronoun_controls()

    def _sync_contextual_controls(
        self,
        repository: SaveRepository | None,
    ) -> None:
        """Shows only character controls relevant to the save's active systems."""

        narrative_combat = bool(
            repository is not None and CombatScreen._uses_narrative_combat(repository)
        )
        narrator_enabled = bool(
            self.tts_enabled
            and (
                repository is None
                or _bool_setting(
                    repository.get_setting("audio.narrator_enabled", True),
                    True,
                )
            )
        )
        # Status is part of the profile summary in every game mode.  The
        # combat-specific stats and equipment controls remain contextual.
        self.condition_group.setVisible(True)
        self.stats_group.setVisible(not narrative_combat)
        self.equipment_group.setVisible(not narrative_combat)
        self.name_pronunciation_label.setVisible(narrator_enabled)
        self.name_pronunciation_input.setVisible(narrator_enabled)

    def refresh(self) -> None:
        """Reloads the character sheet."""

        repository = self.repository()
        self._loading_character = True

        try:
            if repository is None:
                self.portrait_group.hide()
                self.name_input.clear()
                self.name_pronunciation_input.clear()
                self._set_pronouns(DEFAULT_CHARACTER_PRONOUNS)
                self.appearance_input.clear()
                self.backstory_input.clear()
                self.notes_input.clear()
                self.health_current_input.setValue(DEFAULT_PLAYER_MAX_HEALTH)
                self.health_max_input.setValue(DEFAULT_PLAYER_MAX_HEALTH)
                self.initiative_bonus_input.setValue(0)
                self._populate_equipment_combos([], empty_equipment())
                self._sync_equipment_summary()
                self.condition_label.setText("Healthy")
                self._sync_contextual_controls(None)
                self._sync_profile_preview(has_portrait=False)
                self._set_profile_editing(False)
                return

            state = StateManager(repository).load_state()
            inventory_items = repository.list_inventory_items()
            equipment = normalize_equipment(repository.get_player_equipment(), inventory_items)
            armor_rating = armor_rating_from_equipment(equipment, inventory_items)
            repository.set_setting("player.armor_rating", armor_rating)

            self.name_input.setText(state.player.name)
            self.name_pronunciation_input.setText(state.player.name_pronunciation)
            self._set_pronouns(state.player.pronouns)
            self.appearance_input.setPlainText(state.player.appearance)
            self.backstory_input.setPlainText(state.player.backstory)
            self.notes_input.setPlainText(state.player.notes)
            self.health_max_input.setValue(max(1, int(state.player.health_max)))
            self.health_current_input.setValue(
                max(0, min(int(state.player.health_current), self.health_max_input.value()))
            )
            self.initiative_bonus_input.setValue(
                _safe_int(
                    repository.get_setting("player.initiative_bonus", 0),
                    0,
                )
            )
            self._populate_equipment_combos(inventory_items, equipment)
            self._sync_equipment_summary()
            self.condition_label.setText(state.player.condition or "Healthy")
            portrait_asset = repository.get_visual_asset(
                "player",
                repository.get_player_id(),
            )
            has_portrait = _set_generated_image(
                self.portrait_label,
                self.visual_asset_path(portrait_asset),
                maximum_width=280,
                maximum_height=340,
                accessible_name=f"Generated portrait of {state.player.name}",
            )
            self.portrait_group.setVisible(True)
            self.create_portrait_button.setVisible(
                self.visual_asset_generation_enabled()
            )
            self._sync_contextual_controls(repository)
            self._sync_profile_preview(has_portrait=has_portrait)
            self._set_profile_editing(False)
        finally:
            self._loading_character = False
            self._last_saved_character_payload = (
                self._character_payload(repository) if repository is not None else None
            )

    def _select_portrait_image(self) -> None:
        repository = self.repository()
        if repository is not None:
            self.choose_visual_asset("player", repository.get_player_id())

    def _create_portrait_image(self) -> None:
        repository = self.repository()
        if repository is not None:
            self.create_visual_asset("player", repository.get_player_id())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Autosaves multi-line character fields when focus leaves them."""

        profile_editors = (
            self.appearance_input,
            self.backstory_input,
            self.notes_input,
        )
        if event.type() == QEvent.Type.FocusIn and watched in profile_editors:
            self._profile_markdown_target = cast(QTextEdit, watched)

        if (
            event.type() == QEvent.Type.FocusOut
            and watched in profile_editors
        ):
            QTimer.singleShot(0, self._save_character)

        return super().eventFilter(watched, event)

    def _handle_character_spin_changed(self) -> None:
        """Keeps health bounds valid and persists playtesting stat edits."""

        self._sync_health_bounds()
        self._save_character()

    def _character_payload(self, repository: SaveRepository) -> dict[str, Any]:
        """Builds the character payload currently represented by the widgets."""

        inventory_items = repository.list_inventory_items()
        equipment = normalize_equipment(
            {
                slot: self.equipment_combos[slot].currentData() or ""
                for slot in EQUIPMENT_SLOTS
            },
            inventory_items,
        )
        armor_rating = armor_rating_from_equipment(equipment, inventory_items)
        if self.playtesting_tools:
            health_max = max(1, self.health_max_input.value())
            health_current = max(0, min(self.health_current_input.value(), health_max))
            initiative_bonus = self.initiative_bonus_input.value()
        else:
            health_max = max(
                1,
                _safe_int(
                    repository.get_setting(
                        "player.health_max",
                        DEFAULT_PLAYER_MAX_HEALTH,
                    ),
                    DEFAULT_PLAYER_MAX_HEALTH,
                ),
            )
            health_current = max(
                0,
                min(
                    _safe_int(
                        repository.get_setting("player.health_current", health_max),
                        health_max,
                    ),
                    health_max,
                ),
            )
            initiative_bonus = _safe_int(
                repository.get_setting("player.initiative_bonus", 0),
                0,
            )

        return {
            "name": self.name_input.text().strip(),
            "name_pronunciation": self.name_pronunciation_input.text().strip(),
            "pronouns": self._pronouns_from_controls(),
            "appearance": self.appearance_input.toPlainText().strip(),
            "backstory": self.backstory_input.toPlainText().strip(),
            "notes": self.notes_input.toPlainText().strip(),
            "health_current": health_current,
            "health_max": health_max,
            "initiative_bonus": initiative_bonus,
            "armor_rating": armor_rating,
            "equipment": equipment,
        }

    def _save_character(self) -> None:
        """Persists the editable character sheet."""

        repository = self.repository()

        if repository is None or self._loading_character or self._saving_character:
            return

        payload = self._character_payload(repository)
        if payload == self._last_saved_character_payload:
            return

        self._saving_character = True
        try:
            previous_name = repository.get_setting("player_name", "")
            pronunciation_map = repository.get_setting("tts.pronunciation_map", {})
            if str(previous_name).strip().casefold() != payload["name"].casefold():
                pronunciation_map = set_authoritative_pronunciation(
                    pronunciation_map,
                    previous_name,
                    "",
                )
            pronunciation_map = set_authoritative_pronunciation(
                pronunciation_map,
                payload["name"],
                payload["name_pronunciation"],
            )
            repository.set_setting("player_name", payload["name"])
            repository.set_setting(
                "player.name_pronunciation",
                payload["name_pronunciation"],
            )
            repository.set_setting("player.pronouns", payload["pronouns"])
            repository.set_setting("tts.pronunciation_map", pronunciation_map)
            repository.set_setting("player.appearance", payload["appearance"])
            repository.set_setting("player.backstory", payload["backstory"])
            repository.set_setting("player.notes", payload["notes"])
            repository.set_setting("player.health_current", payload["health_current"])
            repository.set_setting("player.health_max", payload["health_max"])
            repository.set_setting("player.armor_rating", payload["armor_rating"])

            if self.playtesting_tools:
                repository.set_setting(
                    "player.initiative_bonus",
                    payload["initiative_bonus"],
                )
            repository.set_player_equipment(payload["equipment"])
            self._sync_player_combatant(
                repository,
                payload["health_current"],
                payload["health_max"],
                payload["armor_rating"],
            )
            self._last_saved_character_payload = payload
        finally:
            self._saving_character = False

        self._sync_equipment_summary()
        self._sync_profile_preview(
            has_portrait=self.portrait_group.isVisible(),
        )
        self.notify_repository_changed()

    def _populate_equipment_combos(
        self,
        inventory_items: list[dict[str, Any]],
        equipment: dict[str, str],
    ) -> None:
        """Reloads all equipment dropdown choices."""

        used_counts = equipment_item_counts(equipment, inventory_items)

        for slot, combo in self.equipment_combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("None", "")
            selected_name = str(equipment.get(slot, "") or "")

            for item in inventory_items:
                item_name = str(item.get("name", "") or "").strip()

                if not item_name or not item_is_valid_for_slot(item, slot):
                    continue

                is_selected_here = item_name.casefold() == selected_name.casefold()
                available_quantity = max(
                    0,
                    _safe_int(item.get("quantity", 0), 0)
                    - used_counts.get(item_name.casefold(), 0),
                )

                if is_selected_here or available_quantity > 0:
                    combo.addItem(item_name, item_name)

            _set_combo_to_data(combo, selected_name)
            combo.blockSignals(False)

        self._remember_equipment_selection()

    def _equipment_changed(self, slot: str) -> None:
        """Enforces equipment constraints when a dropdown changes."""

        if self._loading_character:
            return

        selected_name = str(self.equipment_combos[slot].currentData() or "")
        previous_name = self._equipment_selection.get(slot, "")
        self._loading_character = True

        try:
            if previous_name and previous_name.casefold() != selected_name.casefold():
                self._unequip_previous_item(previous_name)

            selected_item = self._inventory_item_by_name(selected_name)

            if selected_item is not None:
                metadata = item_metadata(selected_item)

                if str(metadata.get("item_type", "")).casefold() == "armor":
                    self._equip_armor_in_covered_slots(
                        selected_name,
                        list(metadata.get("covers_body_parts", [])),
                    )

            main_name = str(
                self.equipment_combos["Main Hand"].currentData() or ""
            )
            main_item = self._inventory_item_by_name(main_name)
            main_is_two_handed = (
                main_item is not None
                and item_metadata(main_item).get("weapon_hands") == "two-handed"
            )

            if main_is_two_handed:
                off_hand_name = str(
                    self.equipment_combos["Off Hand"].currentData() or ""
                )

                if off_hand_name:
                    self._clear_item_from_equipment(off_hand_name)

            repository = self.repository()

            if repository is not None:
                inventory_items = repository.list_inventory_items()
                equipment = repository.set_player_equipment(
                    {
                        equipment_slot: combo.currentData() or ""
                        for equipment_slot, combo in self.equipment_combos.items()
                    }
                )
                armor_rating = armor_rating_from_equipment(
                    equipment,
                    inventory_items,
                )
                repository.set_setting("player.armor_rating", armor_rating)
                self._populate_equipment_combos(inventory_items, equipment)
                self._sync_player_combatant(
                    repository,
                    self.health_current_input.value(),
                    self.health_max_input.value(),
                    armor_rating,
                )
        finally:
            self._loading_character = False
            self._remember_equipment_selection()

        self._sync_equipment_summary()
        self.notify_repository_changed()

    def _unequip_previous_item(self, item_name: str) -> None:
        """Clears every forced slot when the previous item was linked armor."""

        item = self._inventory_item_by_name(item_name)

        if (
            item is not None
            and str(item_metadata(item).get("item_type", "")).casefold() == "armor"
        ):
            self._clear_item_from_equipment(item_name)

    def _equip_armor_in_covered_slots(
        self,
        item_name: str,
        covered_slots: list[Any],
    ) -> None:
        """Equips one armor item into every slot its metadata covers."""

        clean_slots = [
            str(covered_slot)
            for covered_slot in covered_slots
            if str(covered_slot) in self.equipment_combos
        ]
        conflicting_items = {
            str(self.equipment_combos[covered_slot].currentData() or "")
            for covered_slot in clean_slots
        }
        conflicting_items.discard("")
        conflicting_items = {
            equipped_name
            for equipped_name in conflicting_items
            if equipped_name.casefold() != item_name.casefold()
        }

        for conflicting_item in conflicting_items:
            self._clear_item_from_equipment(conflicting_item)

        for covered_slot in clean_slots:
            _set_combo_to_data(
                self.equipment_combos[covered_slot],
                item_name,
            )

    def _clear_item_from_equipment(self, item_name: str) -> None:
        """Clears every slot currently occupied by the named item."""

        folded_name = item_name.casefold()

        for combo in self.equipment_combos.values():
            equipped_name = str(combo.currentData() or "")

            if equipped_name.casefold() == folded_name:
                _set_combo_to_data(combo, "")

    def _remember_equipment_selection(self) -> None:
        """Stores the current dropdown state for the next user change."""

        self._equipment_selection = {
            slot: str(combo.currentData() or "")
            for slot, combo in self.equipment_combos.items()
        }

    def _inventory_item_by_name(self, name: str) -> dict[str, Any] | None:
        """Finds one current inventory item by name."""

        repository = self.repository()

        if repository is None or not name:
            return None

        for item in repository.list_inventory_items():
            if str(item.get("name", "")).casefold() == name.casefold():
                return item

        return None

    def _sync_health_bounds(self) -> None:
        """Keeps current health inside max health."""

        if self.health_current_input.value() > self.health_max_input.value():
            self.health_current_input.setValue(self.health_max_input.value())

    def _sync_equipment_summary(self) -> None:
        """Updates computed armor and weapon labels."""

        repository = self.repository()
        inventory_items = repository.list_inventory_items() if repository is not None else []
        equipment = normalize_equipment(
            {
                slot: self.equipment_combos[slot].currentData() or ""
                for slot in EQUIPMENT_SLOTS
            },
            inventory_items,
        )
        self.armor_rating_label.setText(str(armor_rating_from_equipment(equipment, inventory_items)))
        self.weapon_damage_label.setText(equipped_weapon_damage(equipment, inventory_items))

    def _sync_player_combatant(
        self,
        repository: SaveRepository,
        health_current: int,
        health_max: int,
        armor_rating: int,
    ) -> None:
        """Updates the persisted player combatant when combat is active."""

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        for combatant in combat_state["combatants"]:
            if combatant.get("id") != "player":
                continue

            inventory_items = repository.list_inventory_items()
            equipment = repository.get_player_equipment()
            weapon_profile = equipped_weapon_combat_profile(
                equipment,
                inventory_items,
            )
            combatant["name"] = self.name_input.text().strip() or combatant.get("name", "Player")
            combatant["current_health"] = health_current
            combatant["max_health"] = health_max
            combatant["armor_rating"] = armor_rating
            attack_skill = equipped_weapon_attack_skill(
                equipment,
                inventory_items,
            )
            combatant["to_hit_bonus"] = attack_bonus_from_skills(
                attack_skill,
                repository.list_skills(),
            )
            combatant["damage"] = equipped_weapon_damage(
                equipment,
                inventory_items,
            )
            previous_weapon_name = str(combatant.get("weapon_name", ""))
            combatant.update(weapon_profile)

            if previous_weapon_name.casefold() != str(
                weapon_profile["weapon_name"]
            ).casefold():
                combatant["clip_ammo"] = int(weapon_profile["clip_size"])

            combatant["initiative_bonus"] = _safe_int(
                repository.get_setting("player.initiative_bonus", 0),
                0,
            )
            combatant["defeated"] = health_current <= 0
            break

        repository.set_combat_state(combat_state)
