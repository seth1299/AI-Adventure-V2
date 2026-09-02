from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class NotesScreen(RepositoryBackedWidget):
    """Structured player notes with tag organization and AI sharing control."""

    def __init__(self) -> None:
        super().__init__()

        self._loading_notes = False
        self._saving_notes = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(900)
        self._autosave_timer.timeout.connect(self._autosave_notes)

        self._note_entries: list[dict[str, Any]] = []

        self.add_entry_button = QPushButton("Add new entry")
        self.add_entry_button.clicked.connect(self._add_note_entry)
        self.delete_entry_button = QPushButton("Delete entry")
        self.delete_entry_button.clicked.connect(self._delete_selected_entry)

        self.entry_tree = QTreeWidget()
        self.entry_tree.setHeaderHidden(True)
        self.entry_tree.setMinimumWidth(240)
        self.entry_tree.currentItemChanged.connect(self._show_selected_entry)

        self.entry_heading_input = QLineEdit()
        self.entry_heading_input.setPlaceholderText("Entry heading")
        self.entry_heading_input.textChanged.connect(self._entry_editor_changed)
        self.entry_body_input = QTextEdit()
        self.entry_body_input.setAcceptRichText(False)
        self.entry_body_input.setPlaceholderText(
            "Write the note in Markdown. Select text and use the formatting buttons."
        )
        self.entry_body_input.textChanged.connect(self._entry_editor_changed)
        self.entry_tags_input = QLineEdit()
        self.entry_tags_input.setPlaceholderText("e.g. quests, suspects, places")
        self.entry_tags_input.textChanged.connect(self._tags_text_changed)
        self.entry_tags_input.editingFinished.connect(self._tags_editing_finished)

        editor_layout = QVBoxLayout()
        editor_layout.addWidget(
            QLabel("Heading (starts with the current in-game date and time):")
        )
        editor_layout.addWidget(self.entry_heading_input)
        editor_layout.addWidget(QLabel("Note (Markdown):"))
        markdown_toolbar = QHBoxLayout()
        self._markdown_buttons: list[QPushButton] = []
        self._add_markdown_button(markdown_toolbar, "B", "Bold (Ctrl+B)", "bold")
        self._add_markdown_button(markdown_toolbar, "I", "Italic (Ctrl+I)", "italic")
        self._add_markdown_button(markdown_toolbar, "U", "Underline (Ctrl+U)", "underline")
        self._add_markdown_button(markdown_toolbar, "• List", "Bulleted list", "bullet")
        self._add_markdown_button(markdown_toolbar, "1. List", "Numbered list", "numbered")
        self._add_markdown_button(markdown_toolbar, "H", "Heading", "heading")
        self._add_markdown_button(markdown_toolbar, "Quote", "Block quote", "quote")
        self._add_markdown_button(markdown_toolbar, "Code", "Inline code", "code")
        self._add_markdown_button(markdown_toolbar, "Link", "Link", "link")
        markdown_toolbar.addStretch()
        editor_layout.addLayout(markdown_toolbar)
        editor_layout.addWidget(self.entry_body_input)
        editor_layout.addWidget(QLabel("Tags (comma-separated or #tag):"))
        editor_layout.addWidget(self.entry_tags_input)
        editor = QWidget()
        editor.setLayout(editor_layout)

        entries_layout = QHBoxLayout()
        entries_layout.addWidget(self.entry_tree, 1)
        entries_layout.addWidget(editor, 3)

        self.share_with_ai_checkbox = QCheckBox("Send these notes to the AI")
        self.share_with_ai_checkbox.toggled.connect(
            lambda _checked: self._schedule_notes_autosave()
        )

        layout = QVBoxLayout()
        layout.addWidget(self.share_with_ai_checkbox)
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.add_entry_button)
        button_layout.addWidget(self.delete_entry_button)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        layout.addLayout(entries_layout)

        self.setLayout(layout)
        self._set_editor_entry(None)

    def _add_markdown_button(
        self,
        layout: QHBoxLayout,
        label: str,
        tooltip: str,
        action: str,
    ) -> None:
        button = QPushButton(label)
        button.setToolTip(tooltip)
        button.setMaximumWidth(78)
        button.clicked.connect(lambda _checked=False, name=action: self._apply_markdown(name))
        if action == "bold":
            button.setShortcut("Ctrl+B")
        elif action == "italic":
            button.setShortcut("Ctrl+I")
        elif action == "underline":
            button.setShortcut("Ctrl+U")
        layout.addWidget(button)
        self._markdown_buttons.append(button)

    def _apply_markdown(self, action: str) -> None:
        """Applies portable Markdown syntax to the current body selection."""

        if self._selected_entry() is None:
            return
        cursor = self.entry_body_input.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        text = self.entry_body_input.toPlainText()

        wrappers = {
            "bold": ("**", "**", "bold text"),
            "italic": ("*", "*", "italic text"),
            "underline": ("<u>", "</u>", "underlined text"),
            "code": ("`", "`", "code"),
            "link": ("[", "](https://example.com)", "link text"),
        }
        if action in wrappers:
            prefix, suffix, placeholder = wrappers[action]
            updated, selection_start, selection_end = wrap_markdown_text(
                text, start, end, prefix, suffix, placeholder=placeholder
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

        self.entry_body_input.setPlainText(updated)
        cursor = self.entry_body_input.textCursor()
        cursor.setPosition(selection_start)
        cursor.setPosition(selection_end, QTextCursor.MoveMode.KeepAnchor)
        self.entry_body_input.setTextCursor(cursor)
        self.entry_body_input.setFocus()

    def refresh(self) -> None:
        """Reloads notes, tag groups, and sharing preference."""

        repository = self.repository()
        self._autosave_timer.stop()
        self._loading_notes = True
        try:
            if repository is None:
                self._note_entries = []
                self.entry_tree.clear()
                self._set_editor_entry(None)
                self.share_with_ai_checkbox.setChecked(False)
                return
            self._note_entries = repository.get_note_entries()
            self._refresh_entry_list()
            self.share_with_ai_checkbox.setChecked(repository.get_notes_share_with_ai())
        finally:
            self._loading_notes = False

    def _schedule_notes_autosave(self) -> None:
        if not self._loading_notes and not self._saving_notes:
            self._autosave_timer.start()

    def _autosave_notes(self) -> None:
        self._autosave_timer.stop()
        self._persist_notes()

    def _persist_notes(self) -> None:
        repository = self.repository()
        if repository is None or self._loading_notes or self._saving_notes:
            return
        self._saving_notes = True
        try:
            repository.set_note_entries(self._note_entries)
            repository.set_notes_share_with_ai(self.share_with_ai_checkbox.isChecked())
            self.notify_repository_changed()
        finally:
            self._saving_notes = False

    def _add_note_entry(self) -> None:
        """Adds and selects an entry headed with the current in-game time."""

        repository = self.repository()
        if repository is None:
            return
        heading = build_calendar_snapshot(
            repository.get_current_calendar_minute(),
            repository.get_calendar_settings(),
        )["display_label"]
        entry = {"entry_id": str(uuid.uuid4()), "heading": heading, "body": "", "tags": []}
        self._note_entries.insert(0, entry)
        self._refresh_entry_list(selected_entry_id=entry["entry_id"])
        self.entry_body_input.setFocus()
        self._schedule_notes_autosave()

    def _delete_selected_entry(self) -> None:
        """Deletes the selected note after confirmation."""

        entry = self._selected_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete Note",
            "Delete this note? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._note_entries = [
            candidate for candidate in self._note_entries
            if candidate["entry_id"] != entry["entry_id"]
        ]
        self._refresh_entry_list()
        self._schedule_notes_autosave()

    def _refresh_entry_list(self, *, selected_entry_id: str = "") -> None:
        """Groups notes under All Notes and every assigned tag."""

        previous_loading = self._loading_notes
        self._loading_notes = True
        try:
            self.entry_tree.clear()
            selected_item = None
            groups: list[tuple[str, list[dict[str, Any]]]] = [("All Notes", self._note_entries)]
            tag_groups: dict[str, tuple[str, list[dict[str, Any]]]] = {}
            for entry in self._note_entries:
                for tag in entry["tags"]:
                    identity = tag.casefold()
                    if identity not in tag_groups:
                        tag_groups[identity] = (tag, [])
                    tag_groups[identity][1].append(entry)
            groups.extend(
                (f"#{label}", entries)
                for label, entries in sorted(
                    tag_groups.values(), key=lambda group: group[0].casefold()
                )
            )
            for group_label, entries in groups:
                group_item = QTreeWidgetItem([f"{group_label} ({len(entries)})"])
                group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.entry_tree.addTopLevelItem(group_item)
                group_item.setExpanded(True)
                for entry in entries:
                    item = QTreeWidgetItem([entry["heading"].strip() or "Untitled note"])
                    item.setData(0, Qt.ItemDataRole.UserRole, entry["entry_id"])
                    group_item.addChild(item)
                    if entry["entry_id"] == selected_entry_id and selected_item is None:
                        selected_item = item
            if selected_item is not None:
                self.entry_tree.setCurrentItem(selected_item)
            elif self._note_entries:
                all_notes_group = self.entry_tree.topLevelItem(0)
                if all_notes_group is not None:
                    self.entry_tree.setCurrentItem(all_notes_group.child(0))
            else:
                self._set_editor_entry(None)
        finally:
            self._loading_notes = previous_loading
        self._show_selected_entry(self.entry_tree.currentItem(), None)

    def _selected_entry(self) -> dict[str, Any] | None:
        item = self.entry_tree.currentItem()
        entry_id = str(item.data(0, Qt.ItemDataRole.UserRole) or "") if item else ""
        return next(
            (entry for entry in self._note_entries if entry["entry_id"] == entry_id),
            None,
        )

    def _show_selected_entry(self, current: Any, _previous: Any) -> None:
        """Loads the selected entry into the editable fields."""

        self._set_editor_entry(self._selected_entry() if current is not None else None)

    def _set_editor_entry(self, entry: dict[str, Any] | None) -> None:
        previous_loading = self._loading_notes
        self._loading_notes = True
        try:
            enabled = entry is not None
            self.entry_heading_input.setEnabled(enabled)
            self.entry_body_input.setEnabled(enabled)
            self.entry_tags_input.setEnabled(enabled)
            for button in self._markdown_buttons:
                button.setEnabled(enabled)
            self.delete_entry_button.setEnabled(enabled)
            self.entry_heading_input.setText(entry["heading"] if entry else "")
            self.entry_body_input.setPlainText(entry["body"] if entry else "")
            self.entry_tags_input.setText(", ".join(entry["tags"]) if entry else "")
        finally:
            self._loading_notes = previous_loading

    def _entry_editor_changed(self) -> None:
        """Copies editor changes into the selected structured entry."""

        if self._loading_notes:
            return
        entry = self._selected_entry()
        if entry is None:
            return
        entry["heading"] = self.entry_heading_input.text()
        entry["body"] = self.entry_body_input.toPlainText()
        new_tags = parse_note_tags(self.entry_tags_input.text())
        tags_changed = new_tags != entry["tags"]
        entry["tags"] = new_tags
        selected_id = entry["entry_id"]
        if tags_changed:
            self._refresh_entry_list(selected_entry_id=selected_id)
        else:
            label = entry["heading"].strip() or "Untitled note"
            for group_index in range(self.entry_tree.topLevelItemCount()):
                group_item = self.entry_tree.topLevelItem(group_index)
                if group_item is None:
                    continue
                for child_index in range(group_item.childCount()):
                    item = group_item.child(child_index)
                    if item is None:
                        continue
                    if str(item.data(0, Qt.ItemDataRole.UserRole) or "") == selected_id:
                        item.setText(0, label)
        self._schedule_notes_autosave()

    def _tags_text_changed(self) -> None:
        """Keeps tag edits durable without rebuilding groups on every keystroke."""

        if self._loading_notes:
            return
        entry = self._selected_entry()
        if entry is None:
            return
        entry["tags"] = parse_note_tags(self.entry_tags_input.text())
        self._schedule_notes_autosave()

    def _tags_editing_finished(self) -> None:
        """Rebuilds automatic tag groups after the user finishes editing tags."""

        entry = self._selected_entry()
        if entry is not None:
            self._refresh_entry_list(selected_entry_id=entry["entry_id"])
