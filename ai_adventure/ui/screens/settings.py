from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class SettingsScreen(RepositoryBackedWidget):
    """Basic save-specific settings screen."""

    def __init__(
        self,
        on_audio_settings_changed=None,
        on_theme_changed=None,
        sound_manager: SoundManagerProtocol | None = None,
        tts_enabled: bool = True,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: SampleVoiceCallback | None = None,
        on_app_tts_settings_saved: Callable[[dict[str, Any]], None] | None = None,
        global_tts_settings_provider: Callable[[], dict[str, Any]] | None = None,
        custom_voice_storage_path: Path | str | None = None,
        ai_enabled: bool = True,
        music_enabled: bool = True,
        playtesting_tools: bool = False,
    ) -> None:
        super().__init__()

        self.on_audio_settings_changed = on_audio_settings_changed
        self.on_theme_changed = on_theme_changed
        self.sound_manager = sound_manager
        self.tts_enabled = bool(tts_enabled)
        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        self.on_app_tts_settings_saved = on_app_tts_settings_saved
        self.global_tts_settings_provider = global_tts_settings_provider
        self.custom_voice_storage_path = custom_voice_storage_path
        self.ai_enabled = bool(ai_enabled)
        self.music_feature_enabled = bool(music_enabled)
        self.playtesting_tools = bool(playtesting_tools)
        self.narrator_enabled_checkbox: QCheckBox | None = None
        self.tts_volume_slider: QSlider | None = None
        self.tts_volume_label: QLabel | None = None
        self.tts_voice_combo: QComboBox | None = None
        self.sample_voice_button: QPushButton | None = None
        self.tts_speed_slider: QSlider | None = None
        self.tts_settings_button: QPushButton | None = None
        self.custom_voice_button: QPushButton | None = None
        self._loading_settings = False
        self._saving_settings = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(400)
        self._autosave_timer.timeout.connect(self._save_settings)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        self.theme_combo.currentIndexChanged.connect(lambda _index: self._save_settings())

        self.ai_settings_button = QPushButton("A.I. Settings...")
        self.ai_settings_button.setEnabled(False)
        self.ai_settings_button.clicked.connect(self._open_ai_settings_dialog)

        self.generated_images_enabled_checkbox = QCheckBox(
            "Generate and reuse portraits, locations, items, and NPC images"
        )
        self.generated_images_enabled_checkbox.setChecked(True)
        self.generated_images_enabled_checkbox.setToolTip(
            "New subjects may incur one Gemini image-generation charge; matching "
            "descriptions reuse the existing cached image."
        )
        self.generated_images_enabled_checkbox.toggled.connect(
            lambda _checked: self._save_settings()
        )
        self.maximum_generated_images_input = QSpinBox()
        self.maximum_generated_images_input.setRange(1, 10_000)
        self.maximum_generated_images_input.setValue(DEFAULT_IMAGE_LIMIT)
        self.maximum_generated_images_input.setSuffix(" images")
        self.maximum_generated_images_input.setToolTip(
            "Maximum paid image-generation attempts for this save. Cached reuse is free."
        )
        self.maximum_generated_images_input.valueChanged.connect(
            lambda _value: self._save_settings()
        )
        self.generated_image_model_label = QLabel(DEFAULT_IMAGE_MODEL)
        self.generated_image_model_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.retry_failed_images_button = QPushButton("Retry Failed Images")
        self.retry_failed_images_button.setToolTip(
            "Explicitly requeue failed image requests. Failures are never retried automatically."
        )
        self.retry_failed_images_button.clicked.connect(self._retry_failed_images)

        self.music_enabled_checkbox = QCheckBox("Music enabled")
        self.music_enabled_checkbox.setChecked(True)
        self.music_enabled_checkbox.toggled.connect(lambda _checked: self._save_settings())

        self.music_track_combo = QComboBox()
        self.music_track_combo.setObjectName("settingsMusicTrack")
        self._populate_audio_track_combo(
            self.music_track_combo,
            "get_valid_track_names",
        )
        self.music_track_combo.currentIndexChanged.connect(
            lambda _index: self._save_settings()
        )

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(25)
        self.music_volume_label = QLabel("25%")
        self.music_volume_slider.valueChanged.connect(
            lambda value: self.music_volume_label.setText(f"{value}%")
        )
        self.music_volume_slider.sliderReleased.connect(self._save_settings)

        self.sound_effects_enabled_checkbox = QCheckBox("Sound effects enabled")
        self.sound_effects_enabled_checkbox.setChecked(True)
        self.sound_effects_enabled_checkbox.toggled.connect(
            lambda _checked: self._save_settings()
        )
        self.sound_effects_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.sound_effects_volume_slider.setRange(0, 100)
        self.sound_effects_volume_slider.setValue(35)
        self.sound_effects_volume_label = QLabel("35%")
        self.sound_effects_volume_slider.valueChanged.connect(
            lambda value: self.sound_effects_volume_label.setText(f"{value}%")
        )
        self.sound_effects_volume_slider.sliderReleased.connect(self._save_settings)

        self.background_ambience_enabled_checkbox = QCheckBox(
            "Background ambience enabled"
        )
        self.background_ambience_enabled_checkbox.setChecked(True)
        self.background_ambience_enabled_checkbox.toggled.connect(
            lambda _checked: self._save_settings()
        )
        self.background_ambience_track_combo = QComboBox()
        self.background_ambience_track_combo.setObjectName(
            "settingsBackgroundAmbienceTrack"
        )
        self._populate_audio_track_combo(
            self.background_ambience_track_combo,
            "get_valid_background_ambience_names",
        )
        self.background_ambience_track_combo.currentIndexChanged.connect(
            lambda _index: self._save_settings()
        )
        self.background_ambience_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.background_ambience_volume_slider.setRange(0, 100)
        self.background_ambience_volume_slider.setValue(15)
        self.background_ambience_volume_label = QLabel("15%")
        self.background_ambience_volume_slider.valueChanged.connect(
            lambda value: self.background_ambience_volume_label.setText(f"{value}%")
        )
        self.background_ambience_volume_slider.sliderReleased.connect(
            self._save_settings
        )

        if self.tts_enabled:
            self.tts_settings_button = QPushButton("TTS Settings")
            self.tts_settings_button.clicked.connect(self._open_tts_settings_dialog)
            self.custom_voice_button = QPushButton("Custom Voices...")
            self.custom_voice_button.clicked.connect(self._open_custom_voice_dialog)

        self.currency_name_inputs: list[QLineEdit] = []
        self.currency_plural_inputs: list[QLineEdit] = []
        self.currency_value_inputs: list[QSpinBox] = []
        self.currency_row_widgets: list[QWidget] = []
        self.currency_remove_buttons: list[QPushButton] = []
        self.currency_rows_layout = QFormLayout()
        self.currency_rows_widget = QWidget()
        self.currency_rows_widget.setLayout(self.currency_rows_layout)
        self.add_settings_currency_button = QPushButton("Add New Currency")
        self.add_settings_currency_button.clicked.connect(self._add_settings_currency_row)

        layout = QFormLayout()
        layout.addRow("Theme Preference:", self.theme_combo)
        if self.ai_enabled:
            layout.addRow("Artificial Intelligence:", self.ai_settings_button)
        if self.ai_enabled and not self.playtesting_tools:
            layout.addRow("Generated Images:", self.generated_images_enabled_checkbox)
            layout.addRow("Image Model:", self.generated_image_model_label)
            layout.addRow("Generation Limit:", self.maximum_generated_images_input)
            layout.addRow("Failed Images:", self.retry_failed_images_button)
        if self.music_feature_enabled:
            layout.addRow("Background Music:", self.music_enabled_checkbox)
            layout.addRow("Music Track:", self.music_track_combo)
            layout.addRow(
                "Music Volume:",
                _slider_row(self.music_volume_slider, self.music_volume_label),
            )
            layout.addRow("Narration Sound Effects:", self.sound_effects_enabled_checkbox)
            layout.addRow(
                "Sound Effects Volume:",
                _slider_row(
                    self.sound_effects_volume_slider,
                    self.sound_effects_volume_label,
                ),
            )
            layout.addRow(
                "Background Ambience:",
                self.background_ambience_enabled_checkbox,
            )
            layout.addRow("Ambience Track:", self.background_ambience_track_combo)
            layout.addRow(
                "Ambience Volume:",
                _slider_row(
                    self.background_ambience_volume_slider,
                    self.background_ambience_volume_label,
                ),
            )

        if self.tts_settings_button is not None:
            layout.addRow("Narration Audio:", self.tts_settings_button)

        if self.custom_voice_button is not None:
            layout.addRow("Custom Voices:", self.custom_voice_button)

        if self.playtesting_tools:
            layout.addRow("Currencies:", self.currency_rows_widget)
            layout.addRow("", self.add_settings_currency_button)

        self.setLayout(layout)

    def _populate_audio_track_combo(
        self,
        combo: QComboBox,
        method_name: str,
    ) -> None:
        """Loads locally available music or ambience names into one selector."""

        combo.clear()
        combo.addItem("No track selected", "")
        sound_manager = self.sound_manager
        if sound_manager is None:
            combo.setEnabled(False)
            return
        provider = getattr(sound_manager, method_name, None)
        if not callable(provider):
            combo.setEnabled(False)
            return
        try:
            track_names = provider()
        except Exception as error:
            LOGGER.warning("Failed to load audio track choices: %s", error)
            combo.setEnabled(False)
            return
        for track_name in track_names if isinstance(track_names, list) else []:
            clean_name = str(track_name or "").strip()
            if clean_name:
                combo.addItem(clean_name, clean_name)
        combo.setEnabled(True)

    @staticmethod
    def _set_audio_track_combo_value(combo: QComboBox, value: Any) -> None:
        """Selects a saved track while preserving a name from an older catalog."""

        clean_value = str(value or "").strip()
        if clean_value and combo.findData(clean_value) < 0:
            combo.addItem(clean_value, clean_value)
        _set_combo_to_data(combo, clean_value)

    def _add_settings_currency_row(self) -> None:
        """Adds a blank currency row to the settings screen."""

        self._append_settings_currency_row({})
        self._sync_settings_currency_rows()

    def _append_settings_currency_row(self, denomination: dict[str, Any]) -> None:
        """Appends one editable currency row to the settings screen."""

        name_input = QLineEdit()
        name_input.setPlaceholderText("Name")
        name_input.setText(str(denomination.get("name", "")))
        plural_input = QLineEdit()
        plural_input.setPlaceholderText("Plural name")
        plural_input.setText(str(denomination.get("plural_name", "")))
        value_input = QSpinBox()
        value_input.setMinimum(1)
        value_input.setMaximum(1_000_000_000)
        value_input.setValue(_safe_int(denomination.get("value", 1), 1))
        remove_button = QPushButton("Remove")

        row_widget = QWidget()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(name_input)
        row.addWidget(plural_input)
        row.addWidget(value_input)
        row.addWidget(remove_button)
        row_widget.setLayout(row)

        name_input.editingFinished.connect(self._save_settings)
        name_input.textChanged.connect(lambda _text: self._schedule_settings_save())
        plural_input.editingFinished.connect(self._save_settings)
        plural_input.textChanged.connect(lambda _text: self._schedule_settings_save())
        value_input.valueChanged.connect(lambda _value: self._save_settings())
        remove_button.clicked.connect(
            lambda _checked=False, widget=row_widget: self._remove_settings_currency_row(widget)
        )

        self.currency_name_inputs.append(name_input)
        self.currency_plural_inputs.append(plural_input)
        self.currency_value_inputs.append(value_input)
        self.currency_row_widgets.append(row_widget)
        self.currency_remove_buttons.append(remove_button)
        self.currency_rows_layout.addRow(
            f"Currency {len(self.currency_row_widgets)}:",
            row_widget,
        )

    def _remove_settings_currency_row(self, row_widget: QWidget) -> None:
        """Removes one editable currency row from the settings screen."""

        if row_widget not in self.currency_row_widgets:
            return

        index = self.currency_row_widgets.index(row_widget)
        self.currency_rows_layout.removeRow(row_widget)
        del self.currency_name_inputs[index]
        del self.currency_plural_inputs[index]
        del self.currency_value_inputs[index]
        del self.currency_row_widgets[index]
        del self.currency_remove_buttons[index]
        self._sync_settings_currency_rows()
        self._save_settings()

    def _load_settings_currency_rows(self, denominations: list[dict[str, Any]]) -> None:
        """Rebuilds the settings currency editor from saved denominations."""

        self._clear_settings_currency_rows()

        for denomination in denominations:
            self._append_settings_currency_row(denomination)

        self._sync_settings_currency_rows()

    def _clear_settings_currency_rows(self) -> None:
        """Clears all dynamic settings currency rows."""

        for row_widget in list(self.currency_row_widgets):
            self.currency_rows_layout.removeRow(row_widget)

        self.currency_name_inputs.clear()
        self.currency_plural_inputs.clear()
        self.currency_value_inputs.clear()
        self.currency_row_widgets.clear()
        self.currency_remove_buttons.clear()

    def _sync_settings_currency_rows(self) -> None:
        """Updates currency labels and baseline-value state."""

        previous_loading = self._loading_settings
        self._loading_settings = True

        try:
            for index, (row_widget, value_input, remove_button) in enumerate(
                zip(
                    self.currency_row_widgets,
                    self.currency_value_inputs,
                    self.currency_remove_buttons,
                )
            ):
                label = self.currency_rows_layout.labelForField(row_widget)

                if isinstance(label, QLabel):
                    label.setText(f"Currency {index + 1}:")

                if index == 0:
                    value_input.setValue(1)
                    value_input.setEnabled(False)
                    value_input.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
                    remove_button.setVisible(False)
                    remove_button.setEnabled(False)
                else:
                    value_input.setEnabled(True)
                    value_input.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
                    remove_button.setVisible(True)
                    remove_button.setEnabled(True)
        finally:
            self._loading_settings = previous_loading

    def _settings_currency_denominations(self) -> list[dict[str, Any]]:
        """Reads nonblank settings currency rows."""

        denominations: list[dict[str, Any]] = []

        for name_input, plural_input, value_input in zip(
            self.currency_name_inputs,
            self.currency_plural_inputs,
            self.currency_value_inputs,
        ):
            name = name_input.text().strip()

            if not name:
                continue

            denominations.append(
                {
                    "name": name,
                    "plural_name": plural_input.text().strip() or name,
                    "value": value_input.value(),
                }
            )

        if denominations:
            denominations[0]["value"] = 1

        return denominations

    def refresh(self) -> None:
        """Reloads settings."""

        repository = self.repository()
        self._autosave_timer.stop()
        self._loading_settings = True

        try:
            if repository is None:
                self.theme_combo.setCurrentText("Light")
                self.ai_settings_button.setEnabled(False)
                self.generated_images_enabled_checkbox.setChecked(True)
                self.generated_images_enabled_checkbox.setEnabled(False)
                self.maximum_generated_images_input.setValue(DEFAULT_IMAGE_LIMIT)
                self.maximum_generated_images_input.setEnabled(False)
                self.retry_failed_images_button.setEnabled(False)
                self.music_enabled_checkbox.setChecked(True)
                self._set_audio_track_combo_value(self.music_track_combo, "")
                self.music_track_combo.setEnabled(False)
                self.music_volume_slider.setValue(25)
                self.sound_effects_enabled_checkbox.setChecked(True)
                self.sound_effects_volume_slider.setValue(35)
                self.background_ambience_enabled_checkbox.setChecked(True)
                self._set_audio_track_combo_value(
                    self.background_ambience_track_combo,
                    "",
                )
                self.background_ambience_track_combo.setEnabled(False)
                self.background_ambience_volume_slider.setValue(15)
                self._load_settings_currency_rows([])
                self.add_settings_currency_button.setEnabled(False)

                if self.tts_settings_button is not None:
                    self.tts_settings_button.setEnabled(False)

                if self.custom_voice_button is not None:
                    self.custom_voice_button.setEnabled(False)

                if self.narrator_enabled_checkbox is not None:
                    self.narrator_enabled_checkbox.setChecked(True)

                if self.tts_volume_slider is not None:
                    self.tts_volume_slider.setValue(90)

                if self.tts_voice_combo is not None:
                    _set_combo_to_data(self.tts_voice_combo, DEFAULT_NARRATOR_VOICE)

                self._sync_narrator_control_states(
                    self.narrator_enabled_checkbox.isChecked()
                    if self.narrator_enabled_checkbox is not None
                    else False
                )
                return

            theme = repository.get_setting("theme", "Light")
            denominations = repository.get_currency_denominations()

            if theme in ["Light", "Dark"]:
                self.theme_combo.setCurrentText(str(theme))
            else:
                LOGGER.warning("Unknown theme setting '%s'. Falling back to Light.", theme)
                self.theme_combo.setCurrentText("Light")
                repository.set_setting("theme", "Light")

            self._load_settings_currency_rows(denominations)
            self.add_settings_currency_button.setEnabled(True)
            self.ai_settings_button.setEnabled(True)
            self.generated_images_enabled_checkbox.setEnabled(True)
            self.generated_images_enabled_checkbox.setChecked(
                _bool_setting(repository.get_setting("images.enabled", True), True)
            )
            self.maximum_generated_images_input.setEnabled(True)
            self.maximum_generated_images_input.setValue(
                _clamped_int(
                    repository.get_setting(
                        "images.maximum_generated",
                        DEFAULT_IMAGE_LIMIT,
                    ),
                    DEFAULT_IMAGE_LIMIT,
                    1,
                    10_000,
                )
            )
            self.generated_image_model_label.setText(
                str(
                    repository.get_setting("images.model", DEFAULT_IMAGE_MODEL)
                    or DEFAULT_IMAGE_MODEL
                )
            )
            self.retry_failed_images_button.setEnabled(True)
            self._set_audio_track_combo_value(
                self.music_track_combo,
                repository.get_setting("audio.current_music", ""),
            )
            self.music_track_combo.setEnabled(self.sound_manager is not None)
            self.music_enabled_checkbox.setChecked(
                _bool_setting(repository.get_setting("audio.music_enabled", True), True)
            )
            self.music_volume_slider.setValue(
                _clamped_int(repository.get_setting("audio.music_volume", 25), 25, 0, 100)
            )
            self.sound_effects_enabled_checkbox.setChecked(
                _bool_setting(
                    repository.get_setting("audio.sound_effects_enabled", True),
                    True,
                )
            )
            self.sound_effects_volume_slider.setValue(
                _clamped_int(
                    repository.get_setting("audio.sound_effects_volume", 35),
                    35,
                    0,
                    100,
                )
            )
            self.background_ambience_enabled_checkbox.setChecked(
                _bool_setting(
                    repository.get_setting("audio.background_ambience_enabled", True),
                    True,
                )
            )
            self._set_audio_track_combo_value(
                self.background_ambience_track_combo,
                repository.get_setting("audio.current_background_ambience", ""),
            )
            self.background_ambience_track_combo.setEnabled(self.sound_manager is not None)
            self.background_ambience_volume_slider.setValue(
                _clamped_int(
                    repository.get_setting("audio.background_ambience_volume", 15),
                    15,
                    0,
                    100,
                )
            )

            if self.tts_settings_button is not None:
                self.tts_settings_button.setEnabled(True)

            if self.custom_voice_button is not None:
                self.custom_voice_button.setEnabled(True)

            if self.narrator_enabled_checkbox is not None:
                self.narrator_enabled_checkbox.setChecked(
                    _bool_setting(repository.get_setting("audio.narrator_enabled", True), True)
                )

            if self.tts_volume_slider is not None:
                self.tts_volume_slider.setValue(
                    _clamped_int(repository.get_setting("audio.tts_volume", 90), 90, 0, 100)
                )

            if self.tts_voice_combo is not None:
                _set_combo_to_data(
                    self.tts_voice_combo,
                    normalize_narrator_voice(
                        repository.get_setting(
                            "audio.tts_voice",
                            DEFAULT_NARRATOR_VOICE,
                        )
                    ),
                )

            self._sync_narrator_control_states(
                self.narrator_enabled_checkbox.isChecked()
                if self.narrator_enabled_checkbox is not None
                else False
            )
        finally:
            self._loading_settings = False

    def _schedule_settings_save(self) -> None:
        """Debounces text-field autosaves."""

        if self._loading_settings or self._saving_settings:
            return

        self._autosave_timer.start()

    def _save_settings(self) -> None:
        """Autosaves settings to the active save."""

        repository = self.repository()

        if repository is None or self._loading_settings or self._saving_settings:
            return

        self._autosave_timer.stop()
        self._saving_settings = True

        try:
            repository.set_setting("theme", self.theme_combo.currentText())
            repository.set_setting(
                "images.enabled",
                self.generated_images_enabled_checkbox.isChecked(),
            )
            repository.set_setting(
                "images.maximum_generated",
                self.maximum_generated_images_input.value(),
            )
            repository.set_setting("audio.music_enabled", self.music_enabled_checkbox.isChecked())
            repository.set_setting("audio.music_volume", self.music_volume_slider.value())
            repository.set_setting(
                "audio.current_music",
                str(self.music_track_combo.currentData() or "").strip(),
            )
            repository.set_setting(
                "audio.sound_effects_enabled",
                self.sound_effects_enabled_checkbox.isChecked(),
            )
            repository.set_setting(
                "audio.sound_effects_volume",
                self.sound_effects_volume_slider.value(),
            )
            repository.set_setting(
                "audio.background_ambience_enabled",
                self.background_ambience_enabled_checkbox.isChecked(),
            )
            repository.set_setting(
                "audio.current_background_ambience",
                str(self.background_ambience_track_combo.currentData() or "").strip(),
            )
            repository.set_setting(
                "audio.background_ambience_volume",
                self.background_ambience_volume_slider.value(),
            )

            if self.narrator_enabled_checkbox is not None:
                repository.set_setting(
                    "audio.narrator_enabled",
                    self.narrator_enabled_checkbox.isChecked(),
                )

            if self.tts_volume_slider is not None:
                repository.set_setting("audio.tts_volume", self.tts_volume_slider.value())

            if self.tts_voice_combo is not None:
                repository.set_setting("audio.tts_voice", self._tts_voice_value())

            repository.set_currency_denominations(self._settings_currency_denominations())
            if self.on_audio_settings_changed is not None:
                self.on_audio_settings_changed()
            if self.on_theme_changed is not None:
                self.on_theme_changed()
        finally:
            self._saving_settings = False

        self.notify_repository_changed()

    def _retry_failed_images(self) -> None:
        """Requeues failed image requests only after explicit player intent."""

        repository = self.repository()
        if repository is None:
            return
        reset_count = repository.reset_failed_visual_assets()
        self.retry_failed_images_button.setText(
            f"Requeued {reset_count}" if reset_count else "No Failed Images"
        )
        QTimer.singleShot(
            1800,
            lambda: self.retry_failed_images_button.setText("Retry Failed Images"),
        )
        if reset_count:
            self.notify_repository_changed()

    def _open_ai_settings_dialog(self) -> None:
        """Opens and persists the save-specific A.I. settings modal."""

        repository = self.repository()
        if repository is None:
            return

        dialog = AISettingsDialog(
            self,
            settings=self._current_ai_settings(repository),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_ai_settings(dialog.build_ai_settings())

    @staticmethod
    def _current_ai_settings(repository: SaveRepository) -> dict[str, Any]:
        """Reads current save A.I. settings for the modal."""

        return {
            "model_intelligence": repository.get_setting(
                "ai.model_intelligence",
                DEFAULT_MODEL_INTELLIGENCE,
            ),
            "model_tone": repository.get_setting(
                "ai.model_tone",
                DEFAULT_MODEL_TONE,
            ),
            "response_length": repository.get_setting(
                "ai.response_length",
                DEFAULT_RESPONSE_LENGTH,
            ),
            "allowed_content_categories": repository.get_setting(
                "ai.allowed_content_categories",
                list(DEFAULT_ALLOWED_CONTENT_HARM_CATEGORIES),
            ),
            "narration_tense": repository.get_setting(
                "ai.narration_tense",
                DEFAULT_NARRATION_TENSE,
            ),
            "narration_style": repository.get_setting(
                "ai.narration_style",
                DEFAULT_NARRATION_STYLE,
            ),
            "additional_context": repository.get_setting(
                "ai.additional_context",
                "",
            ),
        }

    def _save_ai_settings(self, raw_settings: dict[str, Any]) -> None:
        """Persists normalized A.I. settings and refreshes model-facing state."""

        repository = self.repository()
        if repository is None or self._saving_settings:
            return

        modes = normalize_ai_mode_preferences(raw_settings)
        narration = normalize_narration_preferences(
            {
                "tense": raw_settings.get("narration_tense"),
                "style": raw_settings.get("narration_style"),
            }
        )
        self._saving_settings = True
        try:
            repository.set_setting(
                "ai.model_intelligence",
                modes["model_intelligence"],
            )
            repository.set_setting("ai.model_tone", modes["model_tone"])
            repository.set_setting(
                "ai.response_length",
                modes["response_length"],
            )
            repository.set_setting(
                "ai.allowed_content_categories",
                modes["allowed_content_categories"],
            )
            repository.set_setting(
                "ai.narration_tense",
                narration["tense"],
            )
            repository.set_setting(
                "ai.narration_style",
                narration["style"],
            )
            repository.set_setting(
                "ai.additional_context",
                str(raw_settings.get("additional_context", "")).strip(),
            )
        finally:
            self._saving_settings = False

        self.notify_repository_changed()

    def _open_tts_settings_dialog(self) -> None:
        """Opens the save-specific TTS settings dialog."""

        repository = self.repository()

        if repository is None:
            return

        dialog = TTSSettingsDialog(
            self,
            audio_settings=self._current_tts_settings(repository),
            voice_options=self.voice_options,
            on_sample_voice=self._sample_voice,
            custom_voice_storage_path=self.custom_voice_storage_path,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_tts_settings(
            dialog.build_audio_settings(),
            persist_app_defaults=dialog.custom_voice_library_changed,
        )

    def _open_custom_voice_dialog(self) -> None:
        """Opens the save-specific custom voice manager."""

        repository = self.repository()

        if repository is None:
            return

        dialog = CustomVoiceDialog(
            self,
            audio_settings=self._current_tts_settings(repository),
            voice_options=self.voice_options,
            on_sample_voice=self._sample_voice,
            storage_path=self.custom_voice_storage_path,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_tts_settings(
            dialog.build_audio_settings(),
            persist_app_defaults=dialog.custom_voice_library_changed,
        )

    def _current_tts_settings(self, repository: SaveRepository) -> dict[str, Any]:
        """Reads current save TTS settings into one normalized audio object."""

        return normalize_tts_audio_fields(
            {
                "narrator_enabled": repository.get_setting("audio.narrator_enabled", True),
                "tts_volume": repository.get_setting("audio.tts_volume", 90),
                "tts_voice": repository.get_setting("audio.tts_voice", DEFAULT_NARRATOR_VOICE),
                "tts_speed": repository.get_setting(
                    "audio.tts_speed",
                    DEFAULT_TTS_SPEED_PERCENT,
                ),
                "tts_voice_mode": repository.get_setting("audio.tts_voice_mode", "preset"),
                "tts_voice_blend": repository.get_setting("audio.tts_voice_blend", {}),
                "tts_custom_voices": merge_custom_voices(
                    repository.get_setting("audio.tts_custom_voices", []),
                    self._global_custom_voices(),
                ),
            },
            tts_enabled=self.tts_enabled,
        )

    def _save_tts_settings(
        self,
        audio_settings: dict[str, Any],
        *,
        persist_app_defaults: bool = False,
    ) -> None:
        """Persists save-specific TTS settings and applies them live."""

        repository = self.repository()

        if repository is None or self._loading_settings or self._saving_settings:
            return

        audio = normalize_tts_audio_fields(audio_settings, tts_enabled=self.tts_enabled)
        self._saving_settings = True

        try:
            repository.set_setting("audio.narrator_enabled", audio["narrator_enabled"])
            repository.set_setting("audio.tts_volume", audio["tts_volume"])
            repository.set_setting("audio.tts_voice", audio["tts_voice"])
            repository.set_setting("audio.tts_speed", audio["tts_speed"])
            repository.set_setting("audio.tts_voice_mode", audio["tts_voice_mode"])
            repository.set_setting("audio.tts_voice_blend", audio["tts_voice_blend"])
            repository.set_setting("audio.tts_custom_voices", audio["tts_custom_voices"])
            if persist_app_defaults and self.on_app_tts_settings_saved is not None:
                self.on_app_tts_settings_saved(audio)
            if self.on_audio_settings_changed is not None:
                self.on_audio_settings_changed()
        finally:
            self._saving_settings = False

        self.notify_repository_changed()

    def _global_custom_voices(self) -> list[dict[str, Any]]:
        """Returns app-level custom voices available outside the active save."""

        if self.global_tts_settings_provider is None:
            return []

        try:
            global_audio = self.global_tts_settings_provider()
        except Exception as error:
            LOGGER.warning("Failed to read app custom voices: %s", error)
            return []

        return normalize_tts_audio_fields(global_audio, tts_enabled=self.tts_enabled)[
            "tts_custom_voices"
        ]

    def _tts_voice_value(self) -> str:
        """Returns the selected narrator voice id."""

        return normalize_narrator_voice(
            _combo_current_data_text(self.tts_voice_combo, DEFAULT_NARRATOR_VOICE)
        )

    def _sync_narrator_control_states(self, checked: bool) -> None:
        """Enables narrator-specific controls only when narration is enabled."""

        if self.tts_volume_slider is not None:
            self.tts_volume_slider.setEnabled(checked)
        if self.tts_voice_combo is not None:
            self.tts_voice_combo.setEnabled(checked)
        if self.sample_voice_button is not None:
            self.sample_voice_button.setEnabled(checked and self.on_sample_voice is not None)

    def _sample_voice(
        self,
        voice: str | None = None,
        volume: int | None = None,
        speed: int | None = None,
    ) -> bool:
        """Plays the selected voice sample."""

        if self.on_sample_voice is None:
            return False

        return _invoke_sample_voice_callback(
            self.on_sample_voice,
            voice or self._tts_voice_value(),
            self._tts_volume_value() if volume is None else int(volume),
            DEFAULT_TTS_SPEED_PERCENT if speed is None else int(speed),
        )

    def _tts_volume_value(self) -> int:
        """Returns the selected narrator volume."""

        if self.tts_volume_slider is None:
            return 0

        return self.tts_volume_slider.value()
