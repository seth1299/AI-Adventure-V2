from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403
from ai_adventure.ui.workers.gemini import (
    GeminiSkillCheckPlanWorker as _GeminiSkillCheckPlanWorker,
    GeminiStoryWorker as _GeminiStoryWorker,
)


class StoryScreen(RepositoryBackedWidget):
    """Conversation screen for live-game actions and out-of-game AI chat."""

    _narration_chunk_ready = Signal(int, str)
    _narration_complete = Signal(int)

    def __init__(
        self,
        *,
        sound_manager: SoundManagerProtocol | None = None,
        narration_player: NarrationPlayerProtocol | None = None,
        api_key_path: Path | str | None = None,
    ) -> None:
        super().__init__()

        self.sound_manager = sound_manager
        self.narration_player = narration_player
        self.api_key_path = (
            Path(api_key_path).expanduser().resolve()
            if api_key_path is not None
            else None
        )
        self._revealing_story_id: int | None = None
        self._revealed_story_chunks: list[str] = []
        self._progressive_story_message: QTextEdit | None = None
        self._story_reveal_generation = 0
        self._gemini_thread: QThread | None = None
        self._gemini_worker: QObject | None = None
        self._pending_skill_check_event_results: list[Any] = []
        self._pending_travel_request: dict[str, Any] | None = None
        self._pending_conversation_mode = "live_game"
        self._pending_message_id: str | None = None
        self._pending_regeneration_request: dict[str, Any] | None = None
        self._conversation_render_generation = 0
        self._waiting_for_gm = False
        self._thinking_frame_index = 0
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(GM_THINKING_TIMER_INTERVAL_MS)
        self._thinking_timer.timeout.connect(self._advance_thinking_indicator)
        self._combat_active = False
        self._default_input_placeholder = "Enter a player action..."
        self._out_of_game_input_placeholder = "Ask the AI about the adventure..."
        self._narration_chunk_ready.connect(self._append_revealed_story_chunk)
        self._narration_complete.connect(self._complete_revealed_story)
        self._initial_generation_pending = False
        self.location_value = QLabel(UNRESOLVED_STATUS_TEXT)
        self.day_value = QLabel(UNRESOLVED_STATUS_TEXT)
        self.time_value = QLabel(UNRESOLVED_STATUS_TEXT)
        self.weather_value = QLabel(UNRESOLVED_STATUS_TEXT)
        self.location_image_label = QLabel()
        self.location_image_label.setObjectName("storyCurrentLocationImage")
        self.location_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.location_image_label.setStyleSheet(
            "background: rgba(15, 23, 42, 96); border-radius: 8px; padding: 3px;"
        )
        self.location_image_label.hide()

        status_row = QHBoxLayout()
        status_row.addStretch()
        status_row.addWidget(_status_label("Location", self.location_value))
        status_row.addWidget(_status_label("Day", self.day_value))
        status_row.addWidget(_status_label("Time", self.time_value))
        status_row.addWidget(_status_label("Weather", self.weather_value))
        status_row.addStretch()

        self.conversation_scroll = QScrollArea()
        self.conversation_scroll.setWidgetResizable(True)
        self.conversation_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.conversation_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.conversation_contents = QWidget()
        self.conversation_layout = QVBoxLayout(self.conversation_contents)
        self.conversation_layout.setContentsMargins(12, 12, 12, 12)
        self.conversation_layout.setSpacing(12)
        self.conversation_layout.addStretch()
        self.conversation_bottom_padding = QWidget(self.conversation_contents)
        self.conversation_bottom_padding.setFixedHeight(0)
        self.conversation_layout.addWidget(self.conversation_bottom_padding)
        self.conversation_scroll.setWidget(self.conversation_contents)

        self.mode_button = QPushButton("Mode: Live Game")
        self.mode_button.setCheckable(True)
        self.mode_button.setToolTip(
            "Switch between story actions and out-of-game questions for the AI."
        )
        self.mode_button.toggled.connect(self._set_out_of_game_mode)
        self._update_mode_button_style()

        self.player_input = QLineEdit()
        self.player_input.setPlaceholderText(self._default_input_placeholder)

        self.submit_button = QPushButton("Submit")
        self.submit_button.clicked.connect(self._submit_player_action)
        self.player_input.returnPressed.connect(self._submit_player_action)

        self.continue_button = QPushButton("Continue")
        self.continue_button.setToolTip("Ask the GM to expand the latest response.")
        self.continue_button.clicked.connect(self._continue_story_response)
        self.continue_button.setEnabled(False)

        input_row = QHBoxLayout()
        input_row.addWidget(self.mode_button)
        input_row.addWidget(self.player_input)
        input_row.addWidget(self.submit_button)
        input_row.addWidget(self.continue_button)

        layout = QVBoxLayout()
        layout.addLayout(status_row)
        location_image_row = QHBoxLayout()
        location_image_row.setContentsMargins(0, 0, 0, 0)
        location_image_row.addStretch()
        location_image_row.addWidget(self.location_image_label)
        location_image_row.addStretch()
        layout.addLayout(location_image_row)
        layout.addWidget(self.conversation_scroll)
        layout.addLayout(input_row)

        self.setLayout(layout)

    def set_repository(self, repository: SaveRepository | None) -> None:
        """Sets the active save and clears any stale narration reveal state."""

        self._clear_story_reveal_state()
        self._initial_generation_pending = False
        self._pending_travel_request = None
        self._pending_conversation_mode = "live_game"
        self.mode_button.setChecked(False)
        super().set_repository(repository)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Recomputes the final-message reading buffer after a window resize."""

        bar = self.conversation_scroll.verticalScrollBar()
        old_value = bar.value()
        follow_bottom = bar.maximum() - old_value <= 48
        super().resizeEvent(event)
        QTimer.singleShot(
            0,
            lambda: self._finish_conversation_resize(old_value, follow_bottom),
        )

    def _finish_conversation_resize(self, old_value: int, follow_bottom: bool) -> None:
        """Restores the reader's position after the viewport is resized."""

        self._update_conversation_bottom_padding()
        bar = self.conversation_scroll.verticalScrollBar()
        if follow_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(max(0, old_value), bar.maximum()))

    def refresh(self) -> None:
        """Refreshes the story output from history."""

        repository = self.repository()

        if repository is None:
            self._clear_conversation_messages()
            self._show_unresolved_status()
            self._combat_active = False
            self._sync_story_input_state()
            self._update_continue_button_state()
            return

        state = StateManager(repository).load_state()
        self._combat_active = repository.is_combat_active()
        if self._initial_generation_pending:
            self._show_unresolved_status()
        else:
            self.location_value.setText(
                state.world.location or UNRESOLVED_STATUS_TEXT
            )
            self.day_value.setText(
                state.calendar.date_label or UNRESOLVED_STATUS_TEXT
            )
            self.time_value.setText(
                state.calendar.time_label or UNRESOLVED_STATUS_TEXT
            )
            self.weather_value.setText(
                state.world.weather or UNRESOLVED_STATUS_TEXT
            )
            self._refresh_current_location_image(state.world.location)

        if self._initial_generation_pending:
            self._render_conversation([])
            self._sync_story_input_state()
            self._update_continue_button_state()
            return

        entries = repository.list_history()
        conversation_entries: list[tuple[Any, ...]] = []
        live_turn_number = 0

        for entry in entries:
            kind = str(entry.get("kind", "misc")).casefold()
            content = str(entry.get("content", ""))

            if kind in {"player", "player_oog"}:
                conversation_entries.append(
                    (
                        "player",
                        "out_of_game" if kind == "player_oog" else "live_game",
                        content,
                        _safe_int(entry.get("id"), -1),
                        str(entry.get("message_id", "") or ""),
                    )
                )
            elif kind in {"story", "story_oog"}:
                entry_id = _safe_int(entry.get("id"), -1)
                turn_number: int | None = None
                if kind == "story":
                    live_turn_number += 1
                    turn_number = live_turn_number
                is_revealing = entry_id == self._revealing_story_id
                visible_content = (
                    "".join(self._revealed_story_chunks)
                    if is_revealing
                    else content
                )
                if not visible_content:
                    continue
                mode = "out_of_game" if kind == "story_oog" else "live_game"
                speaker_cues = (
                    entry.get("speaker_cues", []) if kind == "story" else []
                )
                sound_effect_cues = (
                    entry.get("sound_effect_cues", []) if kind == "story" else []
                )
                if is_revealing:
                    conversation_entries.append(
                        (
                            "ai",
                            mode,
                            visible_content,
                            entry_id,
                            str(entry.get("message_id", "") or ""),
                            turn_number,
                            "",
                            visible_content,
                            [],
                            [],
                        )
                    )
                    continue
                for segment in split_story_bubble_segments(
                    visible_content,
                    sound_effect_cues=sound_effect_cues,
                    speaker_cues=speaker_cues,
                ):
                    segment_text = str(segment["content"])
                    conversation_entries.append(
                        (
                            "ai",
                            mode,
                            (
                                format_story_message(segment_text)
                            ),
                            entry_id,
                            str(entry.get("message_id", "") or ""),
                            turn_number,
                            str(segment.get("speaker_name", "") or ""),
                            segment_text,
                            segment.get("sound_effect_cues", []),
                            segment.get("speaker_cues", []),
                        )
                    )

        self._render_conversation(conversation_entries)
        self._sync_story_input_state()
        self._update_continue_button_state()

    def _clear_conversation_messages(self) -> None:
        """Removes all rendered conversation bubbles while preserving the stretch."""

        while self.conversation_layout.count() > 2:
            item = self.conversation_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _render_conversation(
        self,
        entries: list[tuple[Any, ...]],
    ) -> None:
        """Rebuilds the modern, role-aligned conversation timeline."""

        bar = self.conversation_scroll.verticalScrollBar()
        old_value = bar.value()
        old_maximum = bar.maximum()
        follow_bottom = old_maximum - old_value <= 48
        self._conversation_render_generation += 1
        render_generation = self._conversation_render_generation

        self._clear_conversation_messages()
        self._progressive_story_message = None
        repository = self.repository()
        latest_ai_entry = self._latest_ai_message_entry()
        latest_ai_history_id = _safe_int(
            latest_ai_entry.get("id") if latest_ai_entry is not None else None,
            -1,
        )
        rendered_visual_message_ids: set[str] = set()
        opening_live_message_id = ""
        opening_live_history_id = -1
        for candidate in entries:
            if candidate[0] != "ai" or candidate[1] != "live_game":
                continue
            opening_live_message_id = str(candidate[4]) if len(candidate) > 4 else ""
            opening_live_history_id = _safe_int(
                candidate[3] if len(candidate) > 3 else -1,
                -1,
            )
            break
        for entry in entries:
            role, mode, content, entry_id = entry[:4]
            message_id = str(entry[4]) if len(entry) > 4 else ""
            turn_number = (
                _safe_int(entry[5], 0)
                if len(entry) > 5 and entry[5] is not None
                else None
            )
            speaker_name = str(entry[6]) if len(entry) > 6 else ""
            narration_text = str(entry[7]) if len(entry) > 7 else None
            sound_effect_cues = entry[8] if len(entry) > 8 else None
            speaker_cues = entry[9] if len(entry) > 9 else None
            is_opening_message = (
                role == "ai"
                and mode == "live_game"
                and (
                    (
                        bool(opening_live_message_id)
                        and message_id == opening_live_message_id
                    )
                    or (
                        not opening_live_message_id
                        and _safe_int(entry_id, -1) == opening_live_history_id
                    )
                )
            )
            show_visual_assets = bool(
                role == "ai"
                and mode == "live_game"
                and message_id
                and message_id not in rendered_visual_message_ids
                and not is_opening_message
            )
            if show_visual_assets:
                rendered_visual_message_ids.add(message_id)
            can_regenerate = (
                role == "ai"
                and mode == "live_game"
                and _safe_int(entry_id, -1) == latest_ai_history_id
                and bool(message_id)
                and repository is not None
                and repository.has_message_snapshot(message_id)
                and not self._waiting_for_gm
            )
            bubble = self._conversation_bubble(
                role,
                mode,
                content,
                history_entry_id=_safe_int(entry_id, -1),
                message_id=message_id,
                can_regenerate=can_regenerate,
                turn_number=turn_number,
                speaker_name=speaker_name,
                narration_text=narration_text,
                sound_effect_cues=sound_effect_cues,
                speaker_cues=speaker_cues,
                show_visual_assets=show_visual_assets,
                is_opening_message=is_opening_message,
            )
            if (
                role == "ai"
                and _safe_int(entry_id, -1) == self._revealing_story_id
            ):
                self._progressive_story_message = bubble.findChild(QTextEdit)
            self.conversation_layout.insertWidget(
                self.conversation_layout.count() - 2,
                bubble,
            )
        QTimer.singleShot(
            0,
            lambda: self._finish_conversation_render(
                render_generation,
                old_value,
                follow_bottom,
            ),
        )

    def _finish_conversation_render(
        self,
        render_generation: int,
        old_value: int,
        follow_bottom: bool,
    ) -> None:
        """Restores reading position after the conversation layout settles."""

        if render_generation != self._conversation_render_generation:
            return

        self.conversation_layout.activate()
        self.conversation_contents.adjustSize()
        self._update_conversation_bottom_padding()
        self.conversation_layout.activate()
        self.conversation_contents.adjustSize()
        bar = self.conversation_scroll.verticalScrollBar()
        if follow_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(max(0, old_value), bar.maximum()))

    def _update_conversation_bottom_padding(self) -> None:
        """Adds enough blank space to read the final bubble from its beginning."""

        bubble_count = self.conversation_layout.count() - 2
        if bubble_count <= 0:
            self.conversation_bottom_padding.setFixedHeight(0)
            return

        last_item = self.conversation_layout.itemAt(bubble_count - 1)
        last_bubble = last_item.widget() if last_item is not None else None
        if last_bubble is None:
            self.conversation_bottom_padding.setFixedHeight(0)
            return

        viewport_height = max(0, self.conversation_scroll.viewport().height())
        bubble_height = max(0, last_bubble.sizeHint().height())
        padding = max(0, viewport_height - bubble_height - 24)
        self.conversation_bottom_padding.setFixedHeight(padding)

    def _conversation_bubble(
        self,
        role: str,
        mode: str,
        content: str,
        *,
        history_entry_id: int = -1,
        message_id: str = "",
        can_regenerate: bool = False,
        turn_number: int | None = None,
        speaker_name: str = "",
        narration_text: str | None = None,
        sound_effect_cues: list[dict[str, str]] | None = None,
        speaker_cues: list[dict[str, str]] | None = None,
        show_visual_assets: bool = False,
        is_opening_message: bool = False,
    ) -> QWidget:
        """Builds one readable AI or player chat bubble."""

        repository = self.repository()
        is_player = role == "player"
        is_out_of_game = mode == "out_of_game"
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)

        bubble = QFrame()
        bubble.setObjectName("conversationBubble")
        bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(14, 10, 14, 10)
        bubble_layout.setSpacing(5)

        speaker = (
            "You" if is_player else (speaker_name.strip() or "AI Game Master")
        )
        mode_label = "Out-of-Game" if is_out_of_game else "Live Game"
        turn_label = (
            f"  |  Turn #{turn_number}"
            if not is_player and not is_out_of_game and turn_number is not None
            else ""
        )
        header = QLabel(f"{speaker}  |  {mode_label}{turn_label}")
        header.setStyleSheet("font-size: 11px; font-weight: 700;")
        read_aloud_button = QPushButton("Read Aloud")
        read_aloud_button.setEnabled(self.narration_player is not None)
        read_aloud_button.setToolTip(
            "Play this bubble with its saved narrator or character voice. No AI request is sent."
            if self.narration_player is not None
            else "Local narrator playback is unavailable in this build."
        )
        read_aloud_button.setStyleSheet(
            "QPushButton { background-color: rgba(255, 255, 255, 28); "
            "color: white; border: 1px solid rgba(255, 255, 255, 70); "
            "border-radius: 7px; padding: 3px 9px; font-size: 11px; } "
            "QPushButton:hover { background-color: rgba(255, 255, 255, 48); }"
        )
        def read_message_aloud(*_args: Any) -> None:
            self._read_conversation_message_aloud(
                content,
                history_entry_id=history_entry_id,
                narration_text=narration_text,
                sound_effect_cues=sound_effect_cues,
                speaker_cues=speaker_cues,
            )

        read_aloud_button.clicked.connect(read_message_aloud)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(header)
        header_row.addStretch()
        header_row.addWidget(read_aloud_button)
        if can_regenerate:
            regenerate_button = QPushButton("Regenerate")
            regenerate_button.setToolTip(
                "Regenerate this whole turn after reverting every bubble and state "
                "change tied to its message ID."
            )
            regenerate_button.setStyleSheet(
                "QPushButton { background-color: rgba(255, 255, 255, 28); "
                "color: white; border: 1px solid rgba(255, 255, 255, 70); "
                "border-radius: 7px; padding: 3px 9px; font-size: 11px; } "
                "QPushButton:hover { background-color: rgba(255, 255, 255, 48); }"
            )
            regenerate_button.clicked.connect(
                lambda _checked=False, current_message_id=message_id: (
                    self._request_regeneration(current_message_id)
                )
            )
            header_row.addWidget(regenerate_button)
        bubble_layout.addLayout(header_row)

        speaker_asset = self._conversation_speaker_asset(
            role,
            speaker_cues,
        )
        if speaker_asset is not None:
            portrait_label = QLabel()
            portrait_label.setObjectName("conversationSpeakerPortrait")
            portrait_label.setStyleSheet(
                "background: rgba(15, 23, 42, 96); border-radius: 8px; padding: 3px;"
            )
            _set_generated_image(
                portrait_label,
                self.visual_asset_path(speaker_asset),
                maximum_width=76,
                maximum_height=96,
                accessible_name=f"Profile picture of {speaker}",
            )
            bubble_layout.addWidget(
                portrait_label,
                0,
                Qt.AlignmentFlag.AlignLeft,
            )

        if show_visual_assets and repository is not None and message_id:
            visual_assets = [
                asset
                for asset in repository.list_visual_assets_for_message(message_id)
                if str(asset.get("subject_type", "")).casefold() == "inventory"
            ]
            if visual_assets:
                image_grid = QGridLayout()
                image_grid.setContentsMargins(0, 3, 0, 3)
                image_grid.setHorizontalSpacing(8)
                image_grid.setVerticalSpacing(8)
                for index, asset in enumerate(visual_assets[:12]):
                    image_label = QLabel()
                    image_label.setObjectName("conversationGeneratedImage")
                    image_label.setStyleSheet(
                        "background: rgba(15, 23, 42, 96); border-radius: 8px; padding: 3px;"
                    )
                    if not _set_generated_image(
                        image_label,
                        self.visual_asset_path(asset),
                        maximum_width=220,
                        maximum_height=180,
                        accessible_name=(
                            f"Generated {asset.get('subject_type', 'subject')} image: "
                            f"{asset.get('display_name', '')}"
                        ),
                    ):
                        continue
                    image_grid.addWidget(image_label, index // 3, index % 3)
                bubble_layout.addLayout(image_grid)

        message = QTextEdit()
        message.setReadOnly(True)
        message.setFrameShape(QFrame.Shape.NoFrame)
        message.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        message.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        message.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        message.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        message.setStyleSheet("background: transparent; border: none; padding: 0;")
        _set_markdown_text(message, content)
        message.document().setDocumentMargin(0)

        def resize_message(*_args: Any) -> None:
            message.setFixedHeight(max(24, int(message.document().size().height()) + 4))

        message.document().documentLayout().documentSizeChanged.connect(resize_message)
        resize_message()
        bubble_layout.addWidget(message)

        if is_player:
            background = "#6d28d9" if is_out_of_game else "#2563eb"
            bubble.setStyleSheet(
                "QFrame#conversationBubble {"
                f"background-color: {background}; border-radius: 14px;"
                "} QFrame#conversationBubble QLabel, QFrame#conversationBubble QTextEdit {"
                "color: white; }"
            )
            row_layout.addWidget(bubble, 1)
        else:
            background = "#3f3f46" if is_out_of_game else "#334155"
            bubble.setStyleSheet(
                "QFrame#conversationBubble {"
                f"background-color: {background}; border-radius: 14px;"
                "} QFrame#conversationBubble QLabel, QFrame#conversationBubble QTextEdit {"
                "color: white; }"
            )
            row_layout.addWidget(bubble, 1)
        return row

    def _conversation_speaker_asset(
        self,
        role: str,
        speaker_cues: Any,
    ) -> dict[str, Any] | None:
        """Returns the ready portrait for a player or explicitly named NPC speaker."""

        repository = self.repository()
        if repository is None:
            return None
        if role == "player":
            return repository.get_visual_asset("player", repository.get_player_id())
        if role != "ai" or not isinstance(speaker_cues, list):
            return None

        for cue in speaker_cues:
            if not isinstance(cue, dict):
                continue
            speaker_id = str(cue.get("speaker_id", "") or "").strip()
            if speaker_id:
                return repository.get_visual_asset("npc", speaker_id)
        return None

    def _refresh_current_location_image(self, location_name: str) -> None:
        """Shows the ready establishing image for the player's current location."""

        repository = self.repository()
        if repository is None or not str(location_name or "").strip():
            self.location_image_label.hide()
            return

        location = repository.find_travel_location(location_name)
        if not isinstance(location, dict):
            self.location_image_label.hide()
            return
        location_key = str(
            location.get("location_id", "") or location.get("name", "")
        ).strip()
        location_asset = repository.get_visual_asset("location", location_key)
        if location_asset is None:
            location_asset = repository.get_visual_asset(
                "location",
                str(location.get("name", "") or ""),
            )
        _set_generated_image(
            self.location_image_label,
            self.visual_asset_path(location_asset),
            maximum_width=560,
            maximum_height=280,
            accessible_name=f"Current location: {location.get('name', location_name)}",
        )

    def _read_conversation_message_aloud(
        self,
        text: str,
        *,
        history_entry_id: int = -1,
        narration_text: str | None = None,
        sound_effect_cues: list[dict[str, str]] | None = None,
        speaker_cues: list[dict[str, str]] | None = None,
    ) -> bool:
        """Replays one saved passage and its sound cues without contacting Gemini."""

        if self.narration_player is None:
            return False

        repository = self.repository()
        if repository is not None:
            _apply_audio_settings_to_managers(
                repository,
                sound_manager=self.sound_manager,
                narration_player=self.narration_player,
            )

        pronunciation_map = (
            repository.get_setting("tts.pronunciation_map", {})
            if repository is not None
            else {}
        )
        playback_text = text if narration_text is None else narration_text
        playback_sound_effect_cues = [
            cue for cue in (sound_effect_cues or []) if isinstance(cue, dict)
        ]
        playback_speaker_cues = [
            cue for cue in (speaker_cues or []) if isinstance(cue, dict)
        ]
        if (
            narration_text is None
            and repository is not None
            and history_entry_id >= 0
        ):
            history_entry = next(
                (
                    entry
                    for entry in repository.list_history()
                    if _safe_int(entry.get("id"), -1) == history_entry_id
                ),
                None,
            )
            if history_entry is not None:
                playback_text = str(history_entry.get("content", text))
                raw_cues = history_entry.get("sound_effect_cues", [])
                if isinstance(raw_cues, list):
                    playback_sound_effect_cues = [
                        cue for cue in raw_cues if isinstance(cue, dict)
                    ]
                raw_speaker_cues = history_entry.get("speaker_cues", [])
                if isinstance(raw_speaker_cues, list):
                    playback_speaker_cues = [
                        cue for cue in raw_speaker_cues if isinstance(cue, dict)
                    ]

        return bool(
            self.narration_player.play_sample(
                text=playback_text,
                sound_effect_cues=playback_sound_effect_cues,
                speaker_cues=playback_speaker_cues,
                tts_text_transform=lambda chunk: apply_pronunciation_map(
                    chunk,
                    pronunciation_map,
                ),
                on_sound_effect=(
                    self.sound_manager.play_sound_effect
                    if self.sound_manager is not None
                    else None
                ),
            )
        )

    def _request_regeneration(self, message_id: str) -> None:
        """Reverts and regenerates only the latest live-game response."""

        if self._waiting_for_gm:
            return

        repository = self.repository()
        latest_ai = self._latest_ai_message_entry()
        if (
            repository is None
            or latest_ai is None
            or str(latest_ai.get("kind", "")).casefold() != "story"
            or str(latest_ai.get("message_id", "")).strip() != str(message_id).strip()
            or not repository.has_message_snapshot(message_id)
        ):
            return

        reason, accepted = QInputDialog.getMultiLineText(
            self,
            "Regenerate Message",
            "Why should this message be regenerated?",
            "",
        )
        if not accepted or not reason.strip():
            return

        player_text = self._player_command_before_history_id(
            _safe_int(latest_ai.get("id"), -1)
        )
        if not player_text:
            QMessageBox.warning(
                self,
                "Cannot Regenerate",
                "The original player command for this message could not be found.",
            )
            return

        if not repository.rollback_message(message_id):
            QMessageBox.warning(
                self,
                "Cannot Regenerate",
                "The saved state for this message could not be restored.",
            )
            return

        self._clear_story_reveal_state()
        self.mode_button.setChecked(False)
        self._pending_message_id = repository.create_message_id()
        self._pending_regeneration_request = {
            "active": True,
            "original_message_id": str(message_id),
            "reason": reason.strip(),
            "instruction": (
                "Regenerate the latest response to the same player command. "
                "Use the player's reason as additional direction, while keeping "
                "Python's restored state authoritative."
            ),
        }
        self._pending_skill_check_event_results = []
        self._pending_travel_request = None
        self._pending_conversation_mode = "live_game"
        repository.capture_message_snapshot(self._pending_message_id)
        self.notify_repository_changed()
        self.refresh()
        self._set_waiting_for_gm(True)

        context_packet = self._build_story_context_packet(
            repository,
            player_text,
            conversation_mode="live_game",
        )
        self._apply_pending_regeneration_context(context_packet)
        self._start_skill_check_planning_request(context_packet)

    def _apply_pending_regeneration_context(
        self,
        context_packet: dict[str, Any],
    ) -> None:
        """Adds the player's regeneration reason to the next Gemini packet."""

        if self._pending_regeneration_request is not None:
            context_packet["regeneration_request"] = dict(
                self._pending_regeneration_request
            )

    def _set_out_of_game_mode(self, enabled: bool) -> None:
        """Switches the explicit player-to-AI conversation mode."""

        self._update_mode_button_style()
        self._sync_story_input_state()
        self._update_continue_button_state()

    def _update_mode_button_style(self) -> None:
        """Makes the active mode obvious without relying on button state alone."""

        if self.mode_button.isChecked():
            self.mode_button.setText("Mode: Out-of-Game")
            self.mode_button.setStyleSheet(
                "QPushButton { background-color: #6d28d9; color: white; font-weight: 700; }"
            )
        else:
            self.mode_button.setText("Mode: Live Game")
            self.mode_button.setStyleSheet(
                "QPushButton { background-color: #2563eb; color: white; font-weight: 700; }"
            )

    def _conversation_mode(self) -> str:
        """Returns the mode selected by the player."""

        return "out_of_game" if self.mode_button.isChecked() else "live_game"

    def _submit_player_action(self) -> None:
        """Records a player message in the explicitly selected mode."""

        self._submit_player_command(
            self.player_input.text().strip(),
            conversation_mode=self._conversation_mode(),
        )

    def submit_travel_request(
        self,
        raw_destination: dict[str, Any],
        player_context: str,
    ) -> bool:
        """Submits a Travel-tab journey with calculated logistics for Gemini."""

        if self._waiting_for_gm or self._combat_active:
            return False

        repository = self.repository()
        destination = normalize_known_location(raw_destination)

        if repository is None or destination is None:
            return False

        state = StateManager(repository).load_state()
        origin = normalize_known_location(
            repository.find_travel_location(state.world.location)
        )
        estimate = calculate_travel_estimate(
            origin,
            destination,
            move_speed_mph=state.travel.move_speed_mph,
            travel_mode=state.travel.travel_mode,
            speed_multiplier=state.travel.speed_multiplier,
        )

        if not estimate.is_available:
            return False

        clean_context = player_context.strip()
        player_text = f"Travel toward {destination.name}."

        if clean_context:
            player_text = f"{player_text} {clean_context}"

        return self._submit_player_command(
            player_text,
            conversation_mode="live_game",
            travel_request={
                "active": True,
                "origin": origin.to_dict() if origin is not None else {},
                "destination": destination.to_dict(),
                "estimate": estimate.to_dict(),
                "player_context": clean_context,
                "rules": (
                    "This is an attempted journey. The estimate is mathematical "
                    "logistics, not an automatic arrival."
                ),
            },
        )

    def _submit_player_command(
        self,
        player_text: str,
        *,
        conversation_mode: str = "live_game",
        travel_request: dict[str, Any] | None = None,
    ) -> bool:
        """Records one submitted message and starts its mode-specific Gemini request."""

        clean_mode = "out_of_game" if conversation_mode == "out_of_game" else "live_game"
        if self._waiting_for_gm or (self._combat_active and clean_mode == "live_game"):
            return False

        repository = self.repository()

        if repository is None:
            return False

        player_text = player_text.strip()

        if not player_text:
            LOGGER.warning("Skipped blank player action.")
            return False

        self._pending_message_id = repository.create_message_id()
        self._pending_regeneration_request = None
        StoryTurnService.record_player_action(
            repository,
            player_text,
            message_id=self._pending_message_id,
            conversation_mode=clean_mode,
        )
        self.player_input.clear()
        self._pending_skill_check_event_results = []
        self._pending_travel_request = travel_request
        self._pending_conversation_mode = clean_mode
        self._set_waiting_for_gm(True)
        self.refresh()

        context_packet = self._build_story_context_packet(
            repository,
            player_text,
            conversation_mode=clean_mode,
        )

        if travel_request is not None:
            context_packet["travel_request"] = travel_request
        self._apply_pending_regeneration_context(context_packet)

        if clean_mode == "out_of_game":
            self._start_gemini_story_request(context_packet)
        else:
            self._start_skill_check_planning_request(context_packet)
        return True

    def _continue_story_response(self) -> None:
        """Requests a fuller continuation of the latest story response."""

        if self._waiting_for_gm or self._combat_active:
            return

        repository = self.repository()

        if repository is None:
            return

        latest_story = self._latest_story_entry()

        if latest_story is None:
            LOGGER.warning("Skipped story continuation without a prior story response.")
            return

        player_text = self._latest_player_command() or "Continue the previous narration."
        self._pending_skill_check_event_results = []
        self._pending_travel_request = None
        self._pending_conversation_mode = "live_game"
        self._pending_message_id = repository.create_message_id()
        self._pending_regeneration_request = None
        self._set_waiting_for_gm(True)
        self.refresh()

        context_packet = self._build_story_context_packet(repository, player_text)
        context_packet["continuation_request"] = {
            "active": True,
            "instruction": CONTINUE_STORY_INSTRUCTION,
            "latest_story_response": str(latest_story.get("content", "")).strip()[:4000],
        }

        self._start_gemini_story_request(context_packet)

    def _build_story_context_packet(
        self,
        repository: SaveRepository,
        player_text: str,
        *,
        conversation_mode: str = "live_game",
        resolved_skill_checks: list[dict[str, Any]] | None = None,
        planner_context_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Builds the Gemini story context packet for the current save."""

        return StoryTurnService.build_context_packet(
            repository,
            player_text=player_text,
            conversation_mode=conversation_mode,
            resolved_skill_checks=resolved_skill_checks,
            planner_context_tags=planner_context_tags,
            sound_manager=self.sound_manager,
        )

    def _start_skill_check_planning_request(self, context_packet: dict[str, Any]) -> None:
        """Starts one background pre-narration skill-check planning request."""

        thread = QThread(self)
        worker = _GeminiSkillCheckPlanWorker(context_packet, self.api_key_path)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.completed.connect(self._handle_skill_check_plan_result)
        worker.configuration_error.connect(self._handle_gemini_configuration_error)
        worker.failed.connect(self._handle_gemini_story_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda thread=thread, worker=worker: self._clear_gemini_worker(thread, worker)
        )

        self._gemini_thread = thread
        self._gemini_worker = worker
        thread.start()

    @Slot(object)
    def _handle_skill_check_plan_result(self, plan_result: Any) -> None:
        """Applies planned skill checks, then starts the full narration request."""

        repository = self.repository()

        if repository is None:
            self._pending_skill_check_event_results = []
            self._set_waiting_for_gm(False)
            return

        player_text = self._latest_player_command()

        if not player_text:
            LOGGER.warning("Skill-check plan finished without a player command.")
            self._set_waiting_for_gm(False)
            return

        pending_message_id = self._pending_message_id
        if not pending_message_id:
            LOGGER.warning("Skill-check plan finished without a pending message ID.")
            self._set_waiting_for_gm(False)
            return

        check_events = StoryTurnService.skill_plan_events(plan_result)

        if check_events:
            self._pending_skill_check_event_results = StoryTurnService.apply_suggested_events(
                repository,
                message_id=pending_message_id,
                suggested_events=check_events,
            )
            LOGGER.info(
                "Applied %s pre-narration skill check(s).",
                len(self._pending_skill_check_event_results),
            )
            self.notify_repository_changed()
        else:
            self._pending_skill_check_event_results = []

        resolved_skill_checks = _resolved_skill_checks_for_context(
            self._pending_skill_check_event_results
        )
        context_packet = self._build_story_context_packet(
            repository,
            player_text,
            conversation_mode=self._pending_conversation_mode,
            resolved_skill_checks=resolved_skill_checks,
            planner_context_tags=getattr(plan_result, "relevant_tags", None),
        )
        self._apply_pending_regeneration_context(context_packet)

        if self._pending_travel_request is not None:
            context_packet["travel_request"] = self._pending_travel_request

        self._start_gemini_story_request(context_packet)

    def _start_gemini_story_request(self, context_packet: dict[str, Any]) -> None:
        """Starts one background Gemini story request."""

        thread = QThread(self)
        worker = _GeminiStoryWorker(context_packet, self.api_key_path)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.completed.connect(self._handle_gemini_story_result)
        worker.configuration_error.connect(self._handle_gemini_configuration_error)
        worker.failed.connect(self._handle_gemini_story_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda thread=thread, worker=worker: self._clear_gemini_worker(thread, worker)
        )

        self._gemini_thread = thread
        self._gemini_worker = worker
        thread.start()

    @Slot(object)
    def _handle_gemini_story_result(self, result: Any) -> None:
        """Stores and displays a completed Gemini narration result."""

        repository = self.repository()

        if repository is None:
            self._set_waiting_for_gm(False)
            self._pending_travel_request = None
            self._pending_conversation_mode = "live_game"
            return

        is_out_of_game = self._pending_conversation_mode == "out_of_game"
        message_id = self._pending_message_id or repository.create_message_id()
        commit_result = StoryTurnService.commit_response(
            repository,
            result,
            message_id=message_id,
            conversation_mode=self._pending_conversation_mode,
            prior_event_results=self._pending_skill_check_event_results,
            available_voice_ids=list(
                _narrator_voice_options(self.narration_player).values()
            ),
        )
        speaker_cues = commit_result.speaker_cues
        if not is_out_of_game:
            merchant_npc_id = ""
            for cue in speaker_cues:
                speaker_id = str(cue.get("speaker_id", "") or "").strip()
                if speaker_id and repository.get_merchant_profile(speaker_id):
                    merchant_npc_id = speaker_id
                    break
            repository.set_active_merchant_npc(merchant_npc_id or None)
        if commit_result.event_results:
            event_results = commit_result.event_results
            applied_count = sum(
                1 for event_result in event_results if event_result.status == "applied"
            )
            skipped_count = len(event_results) - applied_count
            LOGGER.info(
                "Applied %s Gemini event(s); skipped %s.",
                applied_count,
                skipped_count,
            )
            _apply_audio_settings_to_managers(
                repository,
                sound_manager=self.sound_manager,
                narration_player=self.narration_player,
            )
            self.notify_repository_changed()

        self._pending_skill_check_event_results = []
        self._pending_travel_request = None
        self._pending_conversation_mode = "live_game"
        self._pending_message_id = None
        self._pending_regeneration_request = None
        latest_story = self._latest_story_entry()

        if not is_out_of_game and latest_story is not None and self._reveal_story_with_narration(
            int(latest_story["id"]),
            result.narrative_text,
            result.sound_effect_cues,
            speaker_cues,
        ):
            return

        self._set_waiting_for_gm(False)
        self.refresh()

    @Slot(str)
    def _handle_gemini_configuration_error(self, _message: str) -> None:
        """Displays the configured-no-key fallback after a recorded player action."""

        repository = self.repository()
        is_out_of_game = self._pending_conversation_mode == "out_of_game"
        self._pending_skill_check_event_results = []
        self._pending_travel_request = None
        self._pending_conversation_mode = "live_game"
        message_id = self._pending_message_id or (
            repository.create_message_id() if repository is not None else ""
        )
        self._pending_message_id = None
        self._pending_regeneration_request = None

        if repository is not None:
            StoryTurnService.record_failure(
                repository,
                message_id=message_id,
                conversation_mode="out_of_game" if is_out_of_game else "live_game",
                message=(
                    "No Gemini API key is configured yet. "
                    "This action was recorded successfully."
                ),
            )

        self._set_waiting_for_gm(False)
        self.refresh()

    @Slot()
    def _handle_gemini_story_failure(self) -> None:
        """Displays the generic Gemini failure fallback."""

        repository = self.repository()
        is_out_of_game = self._pending_conversation_mode == "out_of_game"
        self._pending_skill_check_event_results = []
        self._pending_travel_request = None
        self._pending_conversation_mode = "live_game"
        message_id = self._pending_message_id or (
            repository.create_message_id() if repository is not None else ""
        )
        self._pending_message_id = None
        self._pending_regeneration_request = None

        if repository is not None:
            StoryTurnService.record_failure(
                repository,
                message_id=message_id,
                conversation_mode="out_of_game" if is_out_of_game else "live_game",
                message=(
                    "Gemini is temporarily unavailable. Your action was recorded "
                    "and your save is safe; please try again shortly."
                ),
            )

        self._set_waiting_for_gm(False)
        self.refresh()

    @Slot()
    def _clear_gemini_worker(
        self,
        thread: QThread | None = None,
        worker: QObject | None = None,
    ) -> None:
        """Drops references after the Gemini request thread exits."""

        if thread is None or self._gemini_thread is thread:
            self._gemini_thread = None

        if worker is None or self._gemini_worker is worker:
            self._gemini_worker = None

    def _set_waiting_for_gm(self, waiting: bool) -> None:
        """Toggles player input while Gemini or narration is still working."""

        self._waiting_for_gm = waiting
        if waiting:
            self._thinking_frame_index = 0
            self._thinking_timer.start()
        else:
            self._thinking_timer.stop()
        self._sync_story_input_state()
        self._update_continue_button_state()

        if waiting:
            self._apply_thinking_indicator_text()
        elif self._combat_active and self._conversation_mode() == "live_game":
            tooltip = "Resolve the active combat in the Combat tab before sending story actions."
            self.player_input.setPlaceholderText("Combat is active...")
            self.player_input.setToolTip(tooltip)
            self.submit_button.setToolTip(tooltip)
            self.continue_button.setToolTip(tooltip)
        else:
            self.player_input.setPlaceholderText(
                self._out_of_game_input_placeholder
                if self._conversation_mode() == "out_of_game"
                else self._default_input_placeholder
            )
            self.player_input.setToolTip("")
            self.submit_button.setToolTip("")
        self.continue_button.setToolTip(
            "Ask the GM to expand the latest live-game response."
        )

    def _advance_thinking_indicator(self) -> None:
        """Advances the visible thinking indicator while a request is active."""

        if not self._waiting_for_gm:
            self._thinking_timer.stop()
            return
        self._thinking_frame_index = (
            self._thinking_frame_index + 1
        ) % len(GM_THINKING_FRAMES)
        self._apply_thinking_indicator_text()

    def _apply_thinking_indicator_text(self) -> None:
        """Applies the current animated thinking text to the input affordances."""

        text = GM_THINKING_FRAMES[self._thinking_frame_index]
        self.player_input.setPlaceholderText(text)
        self.player_input.setToolTip(text)
        self.submit_button.setToolTip(text)
        self.continue_button.setToolTip(text)

    def _sync_story_input_state(self) -> None:
        """Enables input according to request state, combat, and explicit mode."""

        is_out_of_game = self._conversation_mode() == "out_of_game"
        can_submit = not self._waiting_for_gm and (not self._combat_active or is_out_of_game)
        self.player_input.setEnabled(can_submit)
        self.submit_button.setEnabled(can_submit)
        self.mode_button.setEnabled(not self._waiting_for_gm)

        if self._waiting_for_gm:
            self._apply_thinking_indicator_text()
            return

        if self._combat_active and not is_out_of_game:
            tooltip = "Resolve the active combat in the Combat tab before sending story actions."
            self.player_input.setPlaceholderText("Combat is active...")
            self.player_input.setToolTip(tooltip)
            self.submit_button.setToolTip(tooltip)
            self.continue_button.setToolTip(tooltip)
            return

        self.player_input.setPlaceholderText(
            self._out_of_game_input_placeholder
            if is_out_of_game
            else self._default_input_placeholder
        )
        self.player_input.setToolTip("")
        self.submit_button.setToolTip("")
        if is_out_of_game:
            self.player_input.setToolTip(
                "Out-of-game messages are saved in the conversation but cannot change game state or advance a turn."
            )
            self.submit_button.setToolTip(self.player_input.toolTip())
        self.continue_button.setToolTip("Ask the GM to expand the latest live-game response.")

    def _update_continue_button_state(self) -> None:
        """Enables Continue only when there is a story response to expand."""

        if not hasattr(self, "continue_button"):
            return

        self.continue_button.setEnabled(
            not self._waiting_for_gm
            and not self._combat_active
            and self._conversation_mode() == "live_game"
            and self.repository() is not None
            and self._latest_story_entry() is not None
        )

    def set_initial_generation_pending(self, pending: bool) -> None:
        """Toggles the story input while the opening scene is generated."""

        self._initial_generation_pending = bool(pending)
        self._set_waiting_for_gm(pending)
        self.refresh()

    def _show_unresolved_status(self) -> None:
        """Shows neutral values while the opening game state is unresolved."""

        self.location_value.setText(UNRESOLVED_STATUS_TEXT)
        self.day_value.setText(UNRESOLVED_STATUS_TEXT)
        self.time_value.setText(UNRESOLVED_STATUS_TEXT)
        self.weather_value.setText(UNRESOLVED_STATUS_TEXT)
        self.location_image_label.hide()

    def narrate_latest_story(self, *, reveal_progressively: bool = False) -> bool:
        """Narrates the latest story, optionally revealing its chunks on screen."""

        repository = self.repository()

        if repository is None:
            return False

        entries = repository.list_history()

        for entry in reversed(entries):
            if str(entry.get("kind", "")).casefold() == "story":
                _apply_audio_settings_to_managers(
                    repository,
                    sound_manager=self.sound_manager,
                    narration_player=self.narration_player,
                )
                content = str(entry.get("content", ""))
                sound_effect_cues = entry.get("sound_effect_cues", [])
                speaker_cues = entry.get("speaker_cues", [])
                if reveal_progressively:
                    story_id = _safe_int(entry.get("id"), -1)
                    if story_id >= 0 and self._reveal_story_with_narration(
                        story_id,
                        content,
                        sound_effect_cues,
                        speaker_cues,
                    ):
                        return True
                self.refresh()
                return self._narrate_text(
                    content,
                    sound_effect_cues=sound_effect_cues,
                    speaker_cues=speaker_cues,
                )
        return False

    def _narrate_text(
        self,
        text: str,
        *,
        story_id: int | None = None,
        sound_effect_cues: list[dict[str, str]] | None = None,
        speaker_cues: list[dict[str, str]] | None = None,
    ) -> bool:
        """Sends text to the narration player if available."""

        if self.narration_player is None:
            return False

        repository = self.repository()
        pronunciation_map = (
            repository.get_setting("tts.pronunciation_map", {})
            if repository is not None
            else {}
        )
        tts_text_transform = lambda chunk: apply_pronunciation_map(
            chunk,
            pronunciation_map,
        )
        on_sound_effect = (
            self.sound_manager.play_sound_effect
            if self.sound_manager is not None
            else None
        )

        if story_id is None:
            return self.narration_player.narrate(
                text,
                sound_effect_cues=sound_effect_cues,
                speaker_cues=speaker_cues,
                tts_text_transform=tts_text_transform,
                on_sound_effect=on_sound_effect,
            )

        return self.narration_player.narrate(
            text,
            sound_effect_cues=sound_effect_cues,
            speaker_cues=speaker_cues,
            tts_text_transform=tts_text_transform,
            on_chunk_start=lambda chunk: self._narration_chunk_ready.emit(
                story_id,
                chunk,
            ),
            on_sound_effect=on_sound_effect,
            on_complete=lambda: self._narration_complete.emit(story_id),
        )

    def _reveal_story_with_narration(
        self,
        story_id: int,
        text: str,
        sound_effect_cues: list[dict[str, str]] | None = None,
        speaker_cues: list[dict[str, str]] | None = None,
    ) -> bool:
        """Displays the latest story progressively as TTS starts each chunk."""

        self._revealing_story_id = story_id
        self._revealed_story_chunks = []
        self._story_reveal_generation += 1
        reveal_generation = self._story_reveal_generation
        self.refresh()

        if self._narrate_text(
            text,
            story_id=story_id,
            sound_effect_cues=sound_effect_cues,
            speaker_cues=speaker_cues,
        ):
            QTimer.singleShot(
                STORY_REVEAL_STALL_TIMEOUT_MS,
                lambda: self._recover_stalled_story_reveal(
                    story_id,
                    reveal_generation,
                ),
            )
            return True

        self._clear_story_reveal_state()
        return False

    def _append_revealed_story_chunk(self, story_id: int, chunk: str) -> None:
        """Appends one just-started narration chunk to the story output."""

        if story_id != self._revealing_story_id:
            return

        clean_chunk = str(chunk or "")

        if not clean_chunk.strip():
            return

        self._revealed_story_chunks.append(clean_chunk)
        message = self._progressive_story_message
        if message is None:
            self.refresh()
            return

        bar = self.conversation_scroll.verticalScrollBar()
        follow_bottom = bar.maximum() - bar.value() <= 48
        message.setPlainText("".join(self._revealed_story_chunks))
        message.document().adjustSize()
        message.setFixedHeight(max(24, int(message.document().size().height()) + 4))
        self.conversation_layout.activate()
        self.conversation_contents.adjustSize()
        self._update_conversation_bottom_padding()
        if follow_bottom:
            bar.setValue(bar.maximum())

    def _complete_revealed_story(self, story_id: int) -> None:
        """Restores normal full-history rendering after chunked narration."""

        if story_id != self._revealing_story_id:
            return

        self._clear_story_reveal_state()
        if self._initial_generation_pending:
            self.set_initial_generation_pending(False)
        else:
            self._set_waiting_for_gm(False)
            self.refresh()

    def _recover_stalled_story_reveal(
        self,
        story_id: int,
        reveal_generation: int,
    ) -> None:
        """Shows the saved story if narration starts but never reveals chunks."""

        if story_id != self._revealing_story_id:
            return

        if reveal_generation != self._story_reveal_generation:
            return

        if self._revealed_story_chunks:
            return

        LOGGER.warning(
            "Narration reveal for story entry %s produced no visible chunks; "
            "showing the saved story text.",
            story_id,
        )
        self._clear_story_reveal_state()
        if self._initial_generation_pending:
            self.set_initial_generation_pending(False)
        else:
            self._set_waiting_for_gm(False)
            self.refresh()

    def _clear_story_reveal_state(self) -> None:
        """Clears progressive story reveal state."""

        self._story_reveal_generation += 1
        self._revealing_story_id = None
        self._revealed_story_chunks = []
        self._progressive_story_message = None

    def _latest_story_entry(self) -> dict[str, Any] | None:
        """Returns the most recent saved story entry."""

        repository = self.repository()

        if repository is None:
            return None

        for entry in reversed(repository.list_history()):
            if str(entry.get("kind", "")).casefold() == "story":
                return entry

        return None

    def _latest_ai_message_entry(self) -> dict[str, Any] | None:
        """Returns the newest AI conversation message of either mode."""

        repository = self.repository()
        if repository is None:
            return None

        for entry in reversed(repository.list_history()):
            if str(entry.get("kind", "")).casefold() in {"story", "story_oog"}:
                return entry
        return None

    def _player_command_before_history_id(self, history_id: int) -> str:
        """Returns the player command immediately preceding one AI response."""

        repository = self.repository()
        if repository is None:
            return ""

        for entry in reversed(repository.list_history()):
            entry_id = _safe_int(entry.get("id"), -1)
            if entry_id >= history_id:
                continue
            if str(entry.get("kind", "")).casefold() == "player":
                return str(entry.get("content", "")).strip()
        return ""

    def _latest_player_command(self) -> str:
        """Returns the most recent saved player command."""

        repository = self.repository()

        if repository is None:
            return ""

        for entry in reversed(repository.list_history()):
            if str(entry.get("kind", "")).casefold() == "player":
                return str(entry.get("content", "")).strip()

        return ""
