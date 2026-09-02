from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403


class CustomVoiceDialog(QDialog):
    """Dedicated manager for loading, editing, and saving custom narrator voices."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        audio_settings: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: SampleVoiceCallback | None = None,
        storage_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        self.storage_path = Path(storage_path) if storage_path is not None else None
        self._base_audio = normalize_tts_audio_fields(audio_settings or {})
        self._syncing_blend_sliders = False
        self._loading_controls = False
        self._use_blend = normalize_tts_voice_mode(
            self._base_audio["tts_voice_mode"]
        ) == "blend"
        self.custom_voice_library_changed = False
        self.custom_voices: list[dict[str, Any]] = []
        self.current_blend = normalize_voice_blend({})
        self.loaded_custom_voice_name: str | None = None

        self.setWindowTitle("Custom Voices")
        self.resize(620, 520)

        self.custom_voice_combo = _NoWheelComboBox()
        self.custom_voice_combo.currentIndexChanged.connect(lambda _index: self._sync_action_states())
        self.load_custom_voice_button = QPushButton("Load")
        self.load_custom_voice_button.clicked.connect(self._load_selected_custom_voice)

        self.current_voice_label = QLabel("Unsaved Custom Voice")
        self.current_voice_label.setWordWrap(True)

        self.tts_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.tts_volume_slider.setRange(0, 100)
        self.tts_volume_slider.setValue(90)
        self.tts_volume_label = QLabel(f"{self.tts_volume_slider.value()}%")
        self.tts_volume_slider.valueChanged.connect(
            lambda value: self.tts_volume_label.setText(f"{value}%")
        )
        self.tts_volume_slider.valueChanged.connect(lambda _value: self._mark_blend_in_use())

        self.tts_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.tts_speed_slider.setRange(50, 200)
        self.tts_speed_slider.setValue(DEFAULT_TTS_SPEED_PERCENT)
        self.tts_speed_label = QLabel(f"{self.tts_speed_slider.value()}%")
        self.tts_speed_slider.valueChanged.connect(
            lambda value: self.tts_speed_label.setText(f"{value}%")
        )
        self.tts_speed_slider.valueChanged.connect(lambda _value: self._mark_blend_in_use())

        self.voice_a_combo = _NoWheelComboBox()
        self.voice_b_combo = _NoWheelComboBox()
        _populate_narrator_voice_combo(
            self.voice_a_combo,
            DEFAULT_NARRATOR_VOICE,
            voice_options=self.voice_options,
        )
        _populate_narrator_voice_combo(
            self.voice_b_combo,
            "am_echo",
            voice_options=self.voice_options,
        )
        self.voice_a_combo.currentIndexChanged.connect(lambda _index: self._mark_blend_in_use())
        self.voice_b_combo.currentIndexChanged.connect(lambda _index: self._mark_blend_in_use())

        self.voice_a_weight_slider = QSlider(Qt.Orientation.Horizontal)
        self.voice_a_weight_slider.setRange(0, 100)
        self.voice_a_weight_slider.setValue(50)
        self.voice_a_weight_label = QLabel(f"{self.voice_a_weight_slider.value()}%")
        self.voice_a_weight_slider.valueChanged.connect(self._handle_voice_a_weight_changed)

        self.voice_b_weight_slider = QSlider(Qt.Orientation.Horizontal)
        self.voice_b_weight_slider.setRange(0, 100)
        self.voice_b_weight_slider.setValue(50)
        self.voice_b_weight_label = QLabel(f"{self.voice_b_weight_slider.value()}%")
        self.voice_b_weight_slider.valueChanged.connect(self._handle_voice_b_weight_changed)

        self.sample_voice_button = QPushButton("Sample Voice")
        self.sample_voice_button.clicked.connect(self._sample_voice)
        self.save_custom_voice_button = QPushButton("Update")
        self.save_custom_voice_button.clicked.connect(self._save_current_custom_voice)
        self.save_custom_voice_as_button = QPushButton("Store As...")
        self.save_custom_voice_as_button.clicked.connect(self._save_current_custom_voice_as)
        self.rename_custom_voice_button = QPushButton("Rename")
        self.rename_custom_voice_button.clicked.connect(self._rename_current_custom_voice)

        self.tts_volume_row = _slider_row(self.tts_volume_slider, self.tts_volume_label)
        self.tts_speed_row = _slider_row(self.tts_speed_slider, self.tts_speed_label)
        self.voice_a_weight_row = _slider_row(
            self.voice_a_weight_slider,
            self.voice_a_weight_label,
        )
        self.voice_b_weight_row = _slider_row(
            self.voice_b_weight_slider,
            self.voice_b_weight_label,
        )
        self.library_row = _button_row(self.custom_voice_combo, self.load_custom_voice_button)
        self.action_row = _button_row(
            self.save_custom_voice_button,
            self.save_custom_voice_as_button,
            self.rename_custom_voice_button,
            self.sample_voice_button,
        )

        form = QFormLayout()
        form.addRow("Saved Voice:", self.library_row)
        form.addRow("Editing:", self.current_voice_label)
        form.addRow("Volume:", self.tts_volume_row)
        form.addRow("Speed:", self.tts_speed_row)
        form.addRow("Voice A:", self.voice_a_combo)
        form.addRow("Voice A Blend:", self.voice_a_weight_row)
        form.addRow("Voice B:", self.voice_b_combo)
        form.addRow("Voice B Blend:", self.voice_b_weight_row)
        form.addRow("", self.action_row)

        if self.storage_path is not None:
            self.storage_label = QLabel(str(self.storage_path))
            self.storage_label.setWordWrap(True)
            form.addRow("Storage:", self.storage_label)
        else:
            self.storage_label = None

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(close_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addStretch()
        layout.addLayout(close_row)
        self.setLayout(layout)
        self.load_audio_settings(audio_settings or {})

    def load_audio_settings(self, audio_settings: dict[str, Any]) -> None:
        """Loads normalized audio settings into the custom voice editor."""

        self._base_audio = normalize_tts_audio_fields(audio_settings)
        blend = normalize_voice_blend(self._base_audio["tts_voice_blend"])
        self._loading_controls = True

        try:
            self.custom_voices = normalize_custom_voices(self._base_audio["tts_custom_voices"])
            self.current_blend = blend
            self.loaded_custom_voice_name = self._saved_voice_name_for(blend)
            self._populate_custom_voice_combo(selected_name=self.loaded_custom_voice_name)
            self._apply_blend_to_controls(blend)
        finally:
            self._loading_controls = False

        self._sync_loaded_voice_label()
        self._sync_action_states()

    def build_audio_settings(self) -> dict[str, Any]:
        """Builds normalized TTS settings from the current custom voice editor state."""

        blend_name = self.loaded_custom_voice_name or str(self.current_blend["name"])
        self.current_blend = self._blend_from_controls(name=blend_name)
        audio = {
            **self._base_audio,
            "tts_volume": self.tts_volume_slider.value(),
            "tts_speed": self.tts_speed_slider.value(),
            "tts_voice_mode": "blend" if self._use_blend else self._base_audio["tts_voice_mode"],
            "tts_voice_blend": self.current_blend,
            "tts_custom_voices": self.custom_voices,
        }
        return normalize_tts_audio_fields(audio)

    def _blend_from_controls(self, *, name: str) -> dict[str, Any]:
        """Returns a normalized blend using the explicit supplied name."""

        return normalize_voice_blend(
            {
                "name": name,
                "voice_a": _combo_current_data_text(self.voice_a_combo, DEFAULT_NARRATOR_VOICE),
                "voice_b": _combo_current_data_text(self.voice_b_combo, "am_echo"),
                "voice_a_weight": self.voice_a_weight_slider.value(),
                "tts_volume": self.tts_volume_slider.value(),
                "tts_speed": self.tts_speed_slider.value(),
            }
        )

    def _apply_blend_to_controls(self, blend: dict[str, Any]) -> None:
        """Sets blend controls without changing the saved-name state."""

        clean_blend = normalize_voice_blend(blend)
        self.current_blend = clean_blend
        _set_combo_to_data(self.voice_a_combo, clean_blend["voice_a"])
        _set_combo_to_data(self.voice_b_combo, clean_blend["voice_b"])
        self._set_blend_weights(int(clean_blend["voice_a_weight"]))
        self.tts_volume_slider.setValue(int(clean_blend["tts_volume"]))
        self.tts_speed_slider.setValue(int(clean_blend["tts_speed"]))

    def _set_blend_weights(self, voice_a_weight: int) -> None:
        """Sets the linked voice blend sliders."""

        self._syncing_blend_sliders = True

        try:
            voice_a_weight = max(0, min(100, int(voice_a_weight)))
            self.voice_a_weight_slider.setValue(voice_a_weight)
            self.voice_b_weight_slider.setValue(100 - voice_a_weight)
            self.voice_a_weight_label.setText(f"{voice_a_weight}%")
            self.voice_b_weight_label.setText(f"{100 - voice_a_weight}%")
        finally:
            self._syncing_blend_sliders = False

    def _handle_voice_a_weight_changed(self, value: int) -> None:
        """Keeps voice B weight complementary to voice A."""

        if self._syncing_blend_sliders:
            return

        self._mark_blend_in_use()
        self._set_blend_weights(value)

    def _handle_voice_b_weight_changed(self, value: int) -> None:
        """Keeps voice A weight complementary to voice B."""

        if self._syncing_blend_sliders:
            return

        self._mark_blend_in_use()
        self._set_blend_weights(100 - value)

    def _mark_blend_in_use(self) -> None:
        """Marks that the current custom blend should be applied."""

        if not self._loading_controls:
            self._use_blend = True

    def _populate_custom_voice_combo(self, *, selected_name: str | None = None) -> None:
        """Reloads the saved custom voice selector."""

        selected_key = str(selected_name or "").strip().casefold()
        selected_index = 0
        self.custom_voice_combo.blockSignals(True)
        self.custom_voice_combo.clear()
        self.custom_voice_combo.addItem("Choose a saved voice", None)

        for voice in self.custom_voices:
            blend = normalize_voice_blend(voice)
            self.custom_voice_combo.addItem(_custom_voice_display_text(blend), blend)

            if selected_key and str(blend["name"]).strip().casefold() == selected_key:
                selected_index = self.custom_voice_combo.count() - 1

        self.custom_voice_combo.setCurrentIndex(selected_index)
        self.custom_voice_combo.blockSignals(False)

    def _load_selected_custom_voice(self) -> None:
        """Loads the selected saved custom voice into the editor."""

        blend = self.custom_voice_combo.currentData()

        if not isinstance(blend, dict):
            return

        clean_blend = normalize_voice_blend(blend)
        self._loading_controls = True

        try:
            self.loaded_custom_voice_name = str(clean_blend["name"])
            self._apply_blend_to_controls(clean_blend)
        finally:
            self._loading_controls = False

        self._use_blend = True
        self._sync_loaded_voice_label()
        self._sync_action_states()

    def _save_current_custom_voice(self) -> None:
        """Saves over the currently loaded custom voice."""

        if self.loaded_custom_voice_name is None:
            self._save_current_custom_voice_as()
            return

        self._store_current_voice(self.loaded_custom_voice_name)

    def _save_current_custom_voice_as(self) -> None:
        """Prompts for a name and saves the current blend as that voice."""

        proposed_name = self.loaded_custom_voice_name or str(self.current_blend["name"])
        voice_name = self._prompt_for_voice_name("Store Custom Voice As", proposed_name)

        if voice_name is None:
            return

        self._store_current_voice(voice_name)

    def _rename_current_custom_voice(self) -> None:
        """Prompts for a replacement name for the currently loaded voice."""

        if self.loaded_custom_voice_name is None:
            return

        old_name = self.loaded_custom_voice_name
        voice_name = self._prompt_for_voice_name("Rename Custom Voice", old_name)

        if voice_name is None:
            return

        self._store_current_voice(voice_name, old_name=old_name)

    def _prompt_for_voice_name(self, title: str, current_name: str) -> str | None:
        """Prompts for a custom voice name."""

        voice_name, accepted = QInputDialog.getText(
            self,
            title,
            "Voice name:",
            QLineEdit.EchoMode.Normal,
            str(current_name or "").strip(),
        )

        if not accepted:
            return None

        clean_name = str(voice_name or "").strip()

        if not clean_name:
            QMessageBox.warning(self, "Missing Name", "Custom voice name is required.")
            return None

        return clean_name

    def _store_current_voice(self, name: str, *, old_name: str | None = None) -> None:
        """Stores the current controls under an explicit saved voice name."""

        clean_name = str(name or "").strip()

        if not clean_name:
            return

        remove_keys = {clean_name.casefold()}

        if old_name is not None:
            remove_keys.add(str(old_name).strip().casefold())

        blend = self._blend_from_controls(name=clean_name)
        self.custom_voices = [
            voice
            for voice in self.custom_voices
            if str(voice.get("name", "")).strip().casefold() not in remove_keys
        ]
        self.custom_voices.append(blend)
        self.custom_voices = normalize_custom_voices(self.custom_voices)
        self.current_blend = blend
        self.loaded_custom_voice_name = str(blend["name"])
        self.custom_voice_library_changed = True
        self._use_blend = True
        self._populate_custom_voice_combo(selected_name=self.loaded_custom_voice_name)
        self._sync_loaded_voice_label()
        self._sync_action_states()

    def _saved_voice_name_for(self, blend: dict[str, Any]) -> str | None:
        """Returns the saved voice name matching a blend name, if present."""

        blend_key = str(blend.get("name", "")).strip().casefold()

        if not blend_key:
            return None

        for voice in self.custom_voices:
            voice_name = str(voice.get("name", "")).strip()

            if voice_name.casefold() == blend_key:
                return voice_name

        return None

    def _sync_loaded_voice_label(self) -> None:
        """Updates the non-editable loaded voice label."""

        blend = self._blend_from_controls(
            name=self.loaded_custom_voice_name or str(self.current_blend["name"])
        )

        if self.loaded_custom_voice_name is None:
            self.current_voice_label.setText(
                f"Unsaved Custom Voice - {_custom_voice_display_text(blend)}"
            )
        else:
            self.current_voice_label.setText(_custom_voice_display_text(blend))

    def _sync_action_states(self) -> None:
        """Enables actions that require a selected or loaded voice."""

        self.load_custom_voice_button.setEnabled(
            isinstance(self.custom_voice_combo.currentData(), dict)
        )
        has_loaded_voice = self.loaded_custom_voice_name is not None
        self.save_custom_voice_button.setEnabled(has_loaded_voice)
        self.rename_custom_voice_button.setEnabled(has_loaded_voice)
        self.sample_voice_button.setEnabled(self.on_sample_voice is not None)

    def _sample_voice(self) -> None:
        """Plays a sample using the current custom blend."""

        if self.on_sample_voice is None:
            return

        blend_name = self.loaded_custom_voice_name or str(self.current_blend["name"])
        blend = self._blend_from_controls(name=blend_name)
        _invoke_sample_voice_callback(
            self.on_sample_voice,
            active_voice_spec_from_audio({"tts_voice_mode": "blend", "tts_voice_blend": blend}),
            self.tts_volume_slider.value(),
            self.tts_speed_slider.value(),
        )


class TTSSettingsWidget(QWidget):
    """Shared advanced narrator controls."""

    def __init__(
        self,
        *,
        audio_settings: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: SampleVoiceCallback | None = None,
        on_custom_voice_saved: Callable[[dict[str, Any]], None] | None = None,
        custom_voice_storage_path: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        self.on_custom_voice_saved = on_custom_voice_saved
        self.custom_voice_storage_path = custom_voice_storage_path
        self._loading_tts_settings = False
        self.custom_voice_library_changed = False
        self.custom_voices: list[dict[str, Any]] = []
        self.current_voice_blend = normalize_voice_blend({})

        self.narrator_enabled_checkbox = QCheckBox("Narrator enabled")
        self.narrator_enabled_checkbox.toggled.connect(
            lambda checked: self._sync_control_states(checked)
        )

        self.tts_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.tts_volume_slider.setRange(0, 100)
        self.tts_volume_slider.setValue(90)
        self.tts_volume_label = QLabel(f"{self.tts_volume_slider.value()}%")
        self.tts_volume_slider.valueChanged.connect(
            lambda value: self.tts_volume_label.setText(f"{value}%")
        )

        self.tts_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.tts_speed_slider.setRange(50, 200)
        self.tts_speed_slider.setValue(DEFAULT_TTS_SPEED_PERCENT)
        self.tts_speed_label = QLabel(f"{self.tts_speed_slider.value()}%")
        self.tts_speed_slider.valueChanged.connect(
            lambda value: self.tts_speed_label.setText(f"{value}%")
        )

        self.voice_mode_combo = _NoWheelComboBox()
        self.voice_mode_combo.addItem("Preset Voice", "preset")
        self.voice_mode_combo.addItem("Custom Blend", "blend")
        self.voice_mode_combo.currentIndexChanged.connect(
            lambda _index: self._sync_control_states(self.narrator_enabled_checkbox.isChecked())
        )

        self.preset_voice_combo = _NoWheelComboBox()
        self.tts_voice_combo = self.preset_voice_combo
        _populate_narrator_voice_combo(
            self.preset_voice_combo,
            DEFAULT_NARRATOR_VOICE,
            voice_options=self.voice_options,
        )

        self.custom_voice_summary_label = QLabel("Current Blend")
        self.custom_voice_summary_label.setWordWrap(True)
        self.custom_voice_button = QPushButton("Custom Voices...")
        self.custom_voice_button.clicked.connect(self._open_custom_voice_dialog)

        self.sample_voice_button = QPushButton("Sample Voice")
        self.sample_voice_button.clicked.connect(self._sample_voice)

        self.tts_volume_row = _slider_row(self.tts_volume_slider, self.tts_volume_label)
        self.tts_speed_row = _slider_row(self.tts_speed_slider, self.tts_speed_label)
        self.custom_voice_row = _button_row(
            self.custom_voice_summary_label,
            self.custom_voice_button,
        )
        self.voice_button_row = _button_row(self.sample_voice_button)

        form = QFormLayout()
        form.addRow("Narrator:", self.narrator_enabled_checkbox)
        form.addRow("Volume:", self.tts_volume_row)
        form.addRow("Speed:", self.tts_speed_row)
        form.addRow("Voice Source:", self.voice_mode_combo)
        form.addRow("Preset Voice:", self.preset_voice_combo)
        form.addRow("Custom Voice:", self.custom_voice_row)
        form.addRow("", self.voice_button_row)
        self.setLayout(form)
        self.load_audio_settings(audio_settings or {})

    def load_audio_settings(self, audio_settings: dict[str, Any]) -> None:
        """Loads normalized TTS settings into the controls."""

        audio = normalize_tts_audio_fields(audio_settings)
        self._loading_tts_settings = True

        try:
            self.custom_voices = normalize_custom_voices(audio["tts_custom_voices"])
            self.current_voice_blend = normalize_voice_blend(audio["tts_voice_blend"])
            self.narrator_enabled_checkbox.setChecked(bool(audio["narrator_enabled"]))
            self.tts_volume_slider.setValue(int(audio["tts_volume"]))
            self.tts_speed_slider.setValue(normalize_tts_speed_percent(audio["tts_speed"]))
            _set_combo_to_data(self.voice_mode_combo, audio["tts_voice_mode"])
            _set_combo_to_data(self.preset_voice_combo, audio["tts_voice"])
        finally:
            self._loading_tts_settings = False

        self._sync_custom_voice_summary()
        self._sync_control_states(self.narrator_enabled_checkbox.isChecked())

    def build_audio_settings(self) -> dict[str, Any]:
        """Builds normalized TTS settings from the controls."""

        return normalize_tts_audio_fields(
            {
                "narrator_enabled": self.narrator_enabled_checkbox.isChecked(),
                "tts_volume": self.tts_volume_slider.value(),
                "tts_voice": self._preset_voice_value(),
                "tts_speed": self.tts_speed_slider.value(),
                "tts_voice_mode": self.voice_mode_combo.currentData() or "preset",
                "tts_voice_blend": self._current_blend(),
                "tts_custom_voices": self.custom_voices,
            }
        )

    def active_voice_spec(self) -> str:
        """Returns the selected engine voice id or blend spec."""

        return active_voice_spec_from_audio(self.build_audio_settings())

    def _preset_voice_value(self) -> str:
        """Returns the selected preset voice id."""

        return normalize_narrator_voice(
            _combo_current_data_text(self.preset_voice_combo, DEFAULT_NARRATOR_VOICE)
        )

    def _current_blend(self) -> dict[str, Any]:
        """Returns the current custom voice blend with current audio controls."""

        blend = normalize_voice_blend(self.current_voice_blend)
        blend["tts_volume"] = self.tts_volume_slider.value()
        blend["tts_speed"] = self.tts_speed_slider.value()
        return normalize_voice_blend(blend)

    def _open_custom_voice_dialog(self) -> None:
        """Opens the dedicated custom voice editor."""

        dialog = CustomVoiceDialog(
            self,
            audio_settings=self.build_audio_settings(),
            voice_options=self.voice_options,
            on_sample_voice=self.on_sample_voice,
            storage_path=self.custom_voice_storage_path,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.load_audio_settings(dialog.build_audio_settings())

        if dialog.custom_voice_library_changed:
            self.custom_voice_library_changed = True

            if self.on_custom_voice_saved is not None:
                self.on_custom_voice_saved(self.build_audio_settings())

    def _sync_custom_voice_summary(self) -> None:
        """Updates the selected custom voice summary label."""

        blend = self._current_blend()
        saved_name = self._saved_voice_name_for(blend)

        if saved_name is not None:
            blend["name"] = saved_name

        display_blend = blend if saved_name is not None else {**blend, "name": "Current Blend"}
        self.custom_voice_summary_label.setText(_custom_voice_display_text(display_blend))

    def _saved_voice_name_for(self, blend: dict[str, Any]) -> str | None:
        """Returns the saved voice name matching a blend name, if present."""

        blend_key = str(blend.get("name", "")).strip().casefold()

        if not blend_key:
            return None

        for voice in self.custom_voices:
            voice_name = str(voice.get("name", "")).strip()

            if voice_name.casefold() == blend_key:
                return voice_name

        return None

    def _sample_voice(self) -> None:
        """Plays a sample using the current preset or blend."""

        if self.on_sample_voice is None:
            return

        _invoke_sample_voice_callback(
            self.on_sample_voice,
            self.active_voice_spec(),
            self.tts_volume_slider.value(),
            self.tts_speed_slider.value(),
        )

    def _sync_control_states(self, checked: bool) -> None:
        """Enables controls based on narrator and voice-source state."""

        mode = normalize_tts_voice_mode(self.voice_mode_combo.currentData())
        preset_visible = checked and mode == "preset"
        custom_visible = checked and mode == "blend"

        for widget in (
            self.tts_volume_slider,
            self.tts_speed_slider,
            self.voice_mode_combo,
            self.custom_voice_button,
            self.sample_voice_button,
        ):
            widget.setEnabled(checked)

        self.preset_voice_combo.setEnabled(preset_visible)
        self.custom_voice_button.setEnabled(checked)
        self.sample_voice_button.setEnabled(checked and self.on_sample_voice is not None)

        for field in (
            self.tts_volume_row,
            self.tts_speed_row,
            self.voice_mode_combo,
            self.voice_button_row,
        ):
            self._set_form_field_visible(field, checked)

        self._set_form_field_visible(self.preset_voice_combo, preset_visible)
        self._set_form_field_visible(self.custom_voice_row, custom_visible)

    def _set_form_field_visible(self, field: QWidget, visible: bool) -> None:
        """Shows or hides a form field and its label together."""

        field.setVisible(visible)

        layout = self.layout()

        if not isinstance(layout, QFormLayout):
            return

        label = layout.labelForField(field)

        if label is not None:
            label.setVisible(visible)


class TTSSettingsDialog(QDialog):
    """Dialog wrapper for shared advanced narrator controls."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        audio_settings: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: SampleVoiceCallback | None = None,
        on_custom_voice_saved: Callable[[dict[str, Any]], None] | None = None,
        custom_voice_storage_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("TTS Settings")
        self.resize(560, 520)
        self.tts_settings_widget = TTSSettingsWidget(
            audio_settings=audio_settings,
            voice_options=voice_options,
            on_sample_voice=on_sample_voice,
            on_custom_voice_saved=on_custom_voice_saved,
            custom_voice_storage_path=custom_voice_storage_path,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self.tts_settings_widget)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def build_audio_settings(self) -> dict[str, Any]:
        """Builds normalized TTS settings from the dialog."""

        return self.tts_settings_widget.build_audio_settings()

    @property
    def custom_voice_library_changed(self) -> bool:
        """Returns whether the custom voice library changed while open."""

        return self.tts_settings_widget.custom_voice_library_changed


class ContentCategoryComboBox(_NoWheelComboBox):
    """Checkable multi-select dropdown for Gemini harm categories."""

    selection_changed = Signal()
    _ALL_VALUE = "__no_restrictions__"

    def __init__(
        self,
        selected_categories: list[str] | tuple[str, ...] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._keep_popup_open = False
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.setEditable(True)

        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setReadOnly(True)
            line_edit.setPlaceholderText("Select allowed content...")

        self._append_checkable_item("No Restrictions", self._ALL_VALUE)
        for option in CONTENT_HARM_CATEGORY_OPTIONS:
            self._append_checkable_item(option["label"], option["value"])

        self.view().pressed.connect(self._toggle_item)
        self.set_selected_categories(
            selected_categories
            if selected_categories is not None
            else list(DEFAULT_ALLOWED_CONTENT_HARM_CATEGORIES)
        )

    def selected_categories(self) -> list[str]:
        """Returns checked Gemini harm category identifiers in API order."""

        selected: list[str] = []
        for row in range(1, self._model.rowCount()):
            item = self._model.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return selected

    def set_selected_categories(self, categories: list[str] | tuple[str, ...]) -> None:
        """Checks only the provided Gemini harm category identifiers."""

        selected = {
            str(category)
            for category in categories
            if str(category) in ALL_CONTENT_HARM_CATEGORIES
        }
        for row in range(1, self._model.rowCount()):
            item = self._model.item(row)
            if item is None:
                continue
            category = str(item.data(Qt.ItemDataRole.UserRole))
            item.setCheckState(
                Qt.CheckState.Checked
                if category in selected
                else Qt.CheckState.Unchecked
            )

        self._sync_no_restrictions_item()
        self._refresh_summary()

    def hidePopup(self) -> None:  # noqa: N802 - Qt method name
        """Keeps the popup open while checkboxes are being toggled."""

        if self._keep_popup_open:
            self._keep_popup_open = False
            return
        super().hidePopup()

    def _append_checkable_item(self, label: str, value: str) -> None:
        item = QStandardItem(label)
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setData(value, Qt.ItemDataRole.UserRole)
        item.setCheckState(Qt.CheckState.Unchecked)
        self._model.appendRow(item)

    def _toggle_item(self, index) -> None:
        self._keep_popup_open = True
        item = self._model.itemFromIndex(index)
        if item is None:
            return

        value = str(item.data(Qt.ItemDataRole.UserRole))
        if value == self._ALL_VALUE:
            should_check_all = len(self.selected_categories()) != len(
                ALL_CONTENT_HARM_CATEGORIES
            )
            self.set_selected_categories(
                list(ALL_CONTENT_HARM_CATEGORIES) if should_check_all else []
            )
        else:
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            self._sync_no_restrictions_item()
            self._refresh_summary()

        self.selection_changed.emit()
        QTimer.singleShot(0, self._refresh_summary)

    def _sync_no_restrictions_item(self) -> None:
        all_item = self._model.item(0)
        if all_item is None:
            return
        all_item.setCheckState(
            Qt.CheckState.Checked
            if len(self.selected_categories()) == len(ALL_CONTENT_HARM_CATEGORIES)
            else Qt.CheckState.Unchecked
        )

    def _refresh_summary(self) -> None:
        categories = self.selected_categories()
        labels_by_category = {
            option["value"]: option["label"]
            for option in CONTENT_HARM_CATEGORY_OPTIONS
        }

        if len(categories) == len(ALL_CONTENT_HARM_CATEGORIES):
            summary = "No Restrictions"
        elif not categories:
            summary = "None"
        else:
            summary = ", ".join(labels_by_category[category] for category in categories)

        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setText(summary)
        self.setToolTip(summary)


class AISettingsDialog(QDialog):
    """Save-specific AI behavior, prose, and content controls."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)

        raw_settings = settings or {}
        modes = normalize_ai_mode_preferences(raw_settings)
        narration = normalize_narration_preferences(
            {
                "tense": raw_settings.get(
                    "narration_tense",
                    DEFAULT_NARRATION_TENSE,
                ),
                "style": raw_settings.get(
                    "narration_style",
                    DEFAULT_NARRATION_STYLE,
                ),
            }
        )

        self.setWindowTitle("A.I. Settings")
        self.resize(640, 720)

        self.model_intelligence_combo = _NoWheelComboBox()
        self._add_mode_options(
            self.model_intelligence_combo,
            MODEL_INTELLIGENCE_OPTIONS,
        )
        _set_combo_to_data(
            self.model_intelligence_combo,
            modes["model_intelligence"],
        )
        self.model_intelligence_description = self._description_label()

        self.model_tone_combo = _NoWheelComboBox()
        self._add_mode_options(self.model_tone_combo, MODEL_TONE_OPTIONS)
        _set_combo_to_data(self.model_tone_combo, modes["model_tone"])
        self.model_tone_description = self._description_label()

        self.response_length_combo = _NoWheelComboBox()
        self._add_mode_options(self.response_length_combo, RESPONSE_LENGTH_OPTIONS)
        _set_combo_to_data(self.response_length_combo, modes["response_length"])
        self.response_length_description = self._description_label()

        self.model_content_combo = ContentCategoryComboBox(
            list(modes["allowed_content_categories"])
        )
        self.model_content_description = self._description_label()

        self.narration_tense_combo = _NoWheelComboBox()
        _add_combo_options(self.narration_tense_combo, NARRATION_TENSE_OPTIONS)
        _set_combo_to_data(self.narration_tense_combo, narration["tense"])
        self.narration_tense_description = self._description_label(
            "Controls the grammatical tense used for player-facing narration."
        )

        self.narration_style_combo = _NoWheelComboBox()
        _add_combo_options(self.narration_style_combo, NARRATION_STYLE_OPTIONS)
        _set_combo_to_data(self.narration_style_combo, narration["style"])
        self.narration_style_description = self._description_label(
            "Controls narrative person and camera. Limited styles preserve the "
            "player character's perspective; omniscient styles may use a wider camera "
            "without revealing hidden information."
        )

        self.additional_ai_context_input = QTextEdit()
        self.additional_ai_context_input.setPlaceholderText(
            "Optional AI-facing guidance, style preferences, boundaries, or reminders..."
        )
        self.additional_ai_context_input.setPlainText(
            str(raw_settings.get("additional_context", ""))
        )

        behavior_group = QGroupBox("Model Modes")
        behavior_layout = QVBoxLayout()
        behavior_layout.addWidget(
            self._choice_field(
                "Model Intelligence",
                self.model_intelligence_combo,
                self.model_intelligence_description,
            )
        )
        behavior_layout.addWidget(
            self._choice_field(
                "Model Tone",
                self.model_tone_combo,
                self.model_tone_description,
            )
        )
        behavior_layout.addWidget(
            self._choice_field(
                "Response Length",
                self.response_length_combo,
                self.response_length_description,
            )
        )
        behavior_layout.addWidget(
            self._choice_field(
                "Model Content (select every category that may appear)",
                self.model_content_combo,
                self.model_content_description,
            )
        )
        behavior_group.setLayout(behavior_layout)

        narration_group = QGroupBox("Narration")
        narration_layout = QVBoxLayout()
        narration_layout.addWidget(
            self._choice_field(
                "Narration Tense",
                self.narration_tense_combo,
                self.narration_tense_description,
            )
        )
        narration_layout.addWidget(
            self._choice_field(
                "Narration Style",
                self.narration_style_combo,
                self.narration_style_description,
            )
        )
        additional_label = QLabel("Additional A.I. Context")
        narration_layout.addWidget(additional_label)
        narration_layout.addWidget(self.additional_ai_context_input)
        additional_description = self._description_label(
            "Persistent free-form guidance sent to the A.I. with every story turn."
        )
        narration_layout.addWidget(additional_description)
        narration_group.setLayout(narration_layout)

        content = QWidget()
        content_layout = QVBoxLayout()
        content_layout.addWidget(behavior_group)
        content_layout.addWidget(narration_group)
        content_layout.addStretch()
        content.setLayout(content_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(content)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(scroll_area)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self.model_intelligence_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_mode_descriptions()
        )
        self.model_tone_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_mode_descriptions()
        )
        self.response_length_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_mode_descriptions()
        )
        self.model_content_combo.selection_changed.connect(
            self._refresh_mode_descriptions
        )
        self._refresh_mode_descriptions()

    def build_ai_settings(self) -> dict[str, Any]:
        """Builds normalized save-specific AI settings from the dialog."""

        modes = normalize_ai_mode_preferences(
            {
                "model_intelligence": (
                    self.model_intelligence_combo.currentData()
                    or DEFAULT_MODEL_INTELLIGENCE
                ),
                "model_tone": self.model_tone_combo.currentData()
                or DEFAULT_MODEL_TONE,
                "response_length": self.response_length_combo.currentData()
                or DEFAULT_RESPONSE_LENGTH,
                "allowed_content_categories": (
                    self.model_content_combo.selected_categories()
                ),
            }
        )
        narration = normalize_narration_preferences(
            {
                "tense": (
                    self.narration_tense_combo.currentData()
                    or DEFAULT_NARRATION_TENSE
                ),
                "style": (
                    self.narration_style_combo.currentData()
                    or DEFAULT_NARRATION_STYLE
                ),
            }
        )
        return {
            "model_intelligence": modes["model_intelligence"],
            "model_tone": modes["model_tone"],
            "response_length": modes["response_length"],
            "allowed_content_categories": modes["allowed_content_categories"],
            "narration_tense": narration["tense"],
            "narration_style": narration["style"],
            "additional_context": (
                self.additional_ai_context_input.toPlainText().strip()
            ),
        }

    @staticmethod
    def _description_label(text: str = "") -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("font-size: 11px;")
        return label

    @staticmethod
    def _choice_field(title: str, combo: QComboBox, description: QLabel) -> QWidget:
        field = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 4, 0, 8)
        layout.addWidget(QLabel(title))
        layout.addWidget(combo)
        layout.addWidget(description)
        field.setLayout(layout)
        return field

    @staticmethod
    def _add_mode_options(
        combo: QComboBox,
        options: tuple[dict[str, Any], ...],
    ) -> None:
        for option in options:
            combo.addItem(str(option["label"]), str(option["value"]))

    def _refresh_mode_descriptions(self) -> None:
        modes = normalize_ai_mode_preferences(
            {
                "model_intelligence": self.model_intelligence_combo.currentData(),
                "model_tone": self.model_tone_combo.currentData(),
                "response_length": self.response_length_combo.currentData(),
                "allowed_content_categories": (
                    self.model_content_combo.selected_categories()
                ),
            }
        )
        self.model_intelligence_description.setText(
            str(modes["model_intelligence_description"])
        )
        self.model_tone_description.setText(str(modes["model_tone_description"]))
        self.response_length_description.setText(
            str(modes["response_length_description"])
        )

        if not modes["blocked_content_labels"]:
            content_text = (
                "No Restrictions: all five configurable Gemini harm categories may "
                "appear when appropriate."
            )
        elif modes["allowed_content_labels"]:
            content_text = (
                "Checked categories may appear; unchecked categories are blocked at "
                "Gemini's strict threshold. Allowed: "
                + ", ".join(modes["allowed_content_labels"])
                + "."
            )
        else:
            content_text = (
                "No categories are allowed. All five configurable Gemini harm "
                "categories are blocked at the strict threshold."
            )

        selected = set(modes["allowed_content_categories"])
        selected_descriptions = [
            f"• {option['label']}: {option['description']}"
            for option in CONTENT_HARM_CATEGORY_OPTIONS
            if option["value"] in selected
        ]
        if selected_descriptions:
            content_text += "\n" + "\n".join(selected_descriptions)

        self.model_content_description.setText(content_text)


class MainMenuSettingsDialog(QDialog):
    """App-level settings available before a save is loaded."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: dict[str, Any],
        tts_enabled: bool = True,
        music_enabled: bool = True,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: SampleVoiceCallback | None = None,
        custom_voice_storage_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self.tts_enabled = bool(tts_enabled)
        self.music_enabled = bool(music_enabled)
        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        clean_settings = normalize_app_settings(
            settings,
            tts_enabled=self.tts_enabled,
        )
        audio = clean_settings["audio"]

        self.setWindowTitle("Settings")
        self.resize(500, 340)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        self.theme_combo.setCurrentText(clean_settings["theme"])

        self.music_enabled_checkbox = QCheckBox("Music enabled")
        self.music_enabled_checkbox.setChecked(bool(audio["music_enabled"]))

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(int(audio["music_volume"]))
        self.music_volume_label = QLabel(f"{self.music_volume_slider.value()}%")
        self.music_volume_slider.valueChanged.connect(
            lambda value: self.music_volume_label.setText(f"{value}%")
        )

        self.sound_effects_enabled_checkbox = QCheckBox("Sound effects enabled")
        self.sound_effects_enabled_checkbox.setChecked(
            bool(audio["sound_effects_enabled"])
        )
        self.sound_effects_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.sound_effects_volume_slider.setRange(0, 100)
        self.sound_effects_volume_slider.setValue(int(audio["sound_effects_volume"]))
        self.sound_effects_volume_label = QLabel(
            f"{self.sound_effects_volume_slider.value()}%"
        )
        self.sound_effects_volume_slider.valueChanged.connect(
            lambda value: self.sound_effects_volume_label.setText(f"{value}%")
        )

        self.background_ambience_enabled_checkbox = QCheckBox(
            "Background ambience enabled"
        )
        self.background_ambience_enabled_checkbox.setChecked(
            bool(audio["background_ambience_enabled"])
        )
        self.background_ambience_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.background_ambience_volume_slider.setRange(0, 100)
        self.background_ambience_volume_slider.setValue(
            int(audio["background_ambience_volume"])
        )
        self.background_ambience_volume_label = QLabel(
            f"{self.background_ambience_volume_slider.value()}%"
        )
        self.background_ambience_volume_slider.valueChanged.connect(
            lambda value: self.background_ambience_volume_label.setText(f"{value}%")
        )

        self.narrator_enabled_checkbox: QCheckBox | None = None
        self.tts_volume_slider: QSlider | None = None
        self.tts_volume_label: QLabel | None = None
        self.tts_voice_combo: QComboBox | None = None
        self.sample_voice_button: QPushButton | None = None
        self.tts_speed_slider: QSlider | None = None
        self.tts_settings_widget: TTSSettingsWidget | None = None
        self._custom_calendar_settings = dict(GREGORIAN_CALENDAR_SETTINGS)

        form = QFormLayout()
        form.addRow("Theme Preference:", self.theme_combo)

        if self.music_enabled:
            form.addRow("Background Music:", self.music_enabled_checkbox)
            form.addRow(
                "Music Volume:",
                _slider_row(self.music_volume_slider, self.music_volume_label),
            )
            form.addRow("Narration Sound Effects:", self.sound_effects_enabled_checkbox)
            form.addRow(
                "Sound Effects Volume:",
                _slider_row(
                    self.sound_effects_volume_slider,
                    self.sound_effects_volume_label,
                ),
            )

            form.addRow(
                "Background Ambience:",
                self.background_ambience_enabled_checkbox,
            )
            form.addRow(
                "Ambience Volume:",
                _slider_row(
                    self.background_ambience_volume_slider,
                    self.background_ambience_volume_label,
                ),
            )

        if self.tts_enabled:
            self.tts_settings_widget = TTSSettingsWidget(
                audio_settings=audio,
                voice_options=self.voice_options,
                on_sample_voice=self._sample_voice,
                custom_voice_storage_path=custom_voice_storage_path,
            )
            self.narrator_enabled_checkbox = (
                self.tts_settings_widget.narrator_enabled_checkbox
            )
            self.tts_volume_slider = self.tts_settings_widget.tts_volume_slider
            self.tts_volume_label = self.tts_settings_widget.tts_volume_label
            self.tts_speed_slider = self.tts_settings_widget.tts_speed_slider
            self.tts_voice_combo = self.tts_settings_widget.tts_voice_combo
            self.sample_voice_button = self.tts_settings_widget.sample_voice_button
            form.addRow("TTS:", self.tts_settings_widget)

        save_button = QPushButton("Apply")
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(save_button)
        button_row.addWidget(cancel_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addStretch()
        layout.addLayout(button_row)
        self.setLayout(layout)

    def build_settings(self) -> dict[str, Any]:
        """Builds normalized app-level settings from dialog fields."""

        return normalize_app_settings(
            {
                "theme": self.theme_combo.currentText(),
                "audio": {
                    "music_enabled": self.music_enabled_checkbox.isChecked(),
                    "music_volume": self.music_volume_slider.value(),
                    "sound_effects_enabled": (
                        self.sound_effects_enabled_checkbox.isChecked()
                    ),
                    "sound_effects_volume": self.sound_effects_volume_slider.value(),
                    "background_ambience_enabled": (
                        self.background_ambience_enabled_checkbox.isChecked()
                    ),
                    "background_ambience_volume": (
                        self.background_ambience_volume_slider.value()
                    ),
                    **self._tts_settings_value(),
                },
            },
            tts_enabled=self.tts_enabled,
        )

    def _tts_volume_value(self) -> int:
        """Returns the requested narrator volume."""

        if self.tts_volume_slider is None:
            return 0

        return self.tts_volume_slider.value()

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
            voice
            or (
                self.tts_settings_widget.active_voice_spec()
                if self.tts_settings_widget is not None
                else self._tts_voice_value()
            ),
            self._tts_volume_value() if volume is None else int(volume),
            DEFAULT_TTS_SPEED_PERCENT if speed is None else int(speed),
        )

    def _tts_settings_value(self) -> dict[str, Any]:
        """Returns advanced TTS settings for app-level settings."""

        if self.tts_settings_widget is None:
            return normalize_tts_audio_fields({}, tts_enabled=False)

        return self.tts_settings_widget.build_audio_settings()


class NewGameTemplateManagerDialog(QDialog):
    """Main-menu dialog for creating and editing reusable new-game templates."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        template_path: Path,
        legacy_template_path: Path | None = None,
        sound_manager: SoundManagerProtocol | None = None,
        audio_defaults: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: SampleVoiceCallback | None = None,
        on_tts_settings_saved: Callable[[dict[str, Any]], None] | None = None,
        custom_voice_storage_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self.template_path = template_path
        self.legacy_template_path = legacy_template_path
        self.sound_manager = sound_manager
        self.audio_defaults = normalize_tts_audio_fields(audio_defaults or {})
        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        self.on_tts_settings_saved = on_tts_settings_saved
        self.custom_voice_storage_path = custom_voice_storage_path
        self.templates: list[NewGameTemplate] = []
        self.active_template_name: str | None = None
        self.active_setup: dict[str, Any] = {}
        self._loading_template_setup = False

        self.setWindowTitle("New Game Templates")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.resize(980, 680)

        self.template_list = QListWidget()
        self.template_list.currentRowChanged.connect(self._load_selected_template)

        new_button = QPushButton("New")
        new_button.clicked.connect(self._new_template)
        duplicate_button = QPushButton("Duplicate")
        duplicate_button.clicked.connect(self._duplicate_template)
        save_button = QPushButton("Update Template")
        save_button.clicked.connect(self._save_template)
        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self._delete_template)

        self.template_name_input = QLineEdit()
        self.template_name_input.setPlaceholderText("Template name")
        self.genre_input = QLineEdit()
        self.genre_input.setPlaceholderText("Genre or adventure type")
        self.start_location_input = QLineEdit()
        self.start_location_input.setPlaceholderText("Starting place")
        self.start_location_input.setVisible(False)
        self.start_location_mode_combo = _NoWheelComboBox()
        self.start_location_mode_combo.addItem("Use as suggestion", "suggestion")
        self.start_location_mode_combo.addItem("Use exactly this", "exact")
        self.start_location_mode_combo.setVisible(False)
        self.opening_scene_request_input = QTextEdit()
        self.opening_scene_request_input.setPlaceholderText(
            "Optional: describe the situation, mood, event, or hook you want the opening scene to begin with..."
        )
        self.narration_tense_combo = _NoWheelComboBox()
        _add_combo_options(self.narration_tense_combo, NARRATION_TENSE_OPTIONS)
        _set_combo_to_data(self.narration_tense_combo, DEFAULT_NARRATION_TENSE)
        self.narration_style_combo = _NoWheelComboBox()
        _add_combo_options(self.narration_style_combo, NARRATION_STYLE_OPTIONS)
        _set_combo_to_data(self.narration_style_combo, DEFAULT_NARRATION_STYLE)
        self.game_style_input = QTextEdit()
        self.game_style_input.setPlaceholderText("Tone, pacing, realism, themes, playstyle...")
        self.world_context_input = QTextEdit()
        self.world_context_input.setPlaceholderText("World facts, factions, locations, constraints...")

        self._new_game_ai_settings = normalize_ai_mode_preferences({})
        self.ai_settings_button = QPushButton("A.I. Settings...")
        self.ai_settings_button.clicked.connect(self._open_template_ai_settings)
        self.ai_settings_summary_label = QLabel()
        self.ai_settings_summary_label.setWordWrap(True)
        self.ai_settings_summary_label.setStyleSheet("font-size: 11px;")

        self.character_name_input = QLineEdit()
        self.character_name_input.setPlaceholderText("Player character name")
        self.character_name_pronunciation_input = QLineEdit()
        self.character_name_pronunciation_input.setPlaceholderText(
            "Optional: kah-tha-lah, or /kəˈθɑlə/ for exact IPA"
        )
        self.appearance_input = QTextEdit()
        self.appearance_input.setPlaceholderText("Appearance, clothing, visible traits...")
        self.backstory_input = QTextEdit()
        self.backstory_input.setPlaceholderText("Origin, history, goals, relationships...")
        self.character_notes_input = QTextEdit()
        self.character_notes_input.setPlaceholderText("Other player-character notes...")

        self.skill_inputs: list[tuple[int, QLineEdit, QLineEdit]] = []
        self.skill_tables: dict[int, _AppTableWidget] = {}
        self.skill_table_controls: dict[int, QWidget] = {}
        self._starting_location_row_id_counter = 0
        self.start_location_combo = _NoWheelComboBox()
        self.start_location_combo.addItem("Select from starting locations", "")
        self.start_location_combo.currentIndexChanged.connect(
            lambda _index: self._sync_template_start_location_from_locations_combo()
        )
        self.starting_locations_table = _AppTableWidget(0, 6)
        self.starting_locations_table.setHorizontalHeaderLabels(
            ["Name", "Description", "Location Mode", "Sublocation?", "Within", "Remove"]
        )
        _configure_inline_table(
            self.starting_locations_table,
            STARTING_LOCATION_COLUMN_WIDTHS,
            minimum_height=240,
        )
        self.add_location_button = QPushButton("Add Location")
        self.add_location_button.clicked.connect(
            lambda: self._append_starting_location_row({})
        )
        self.starting_npcs_table = _AppTableWidget(0, 5)
        self.starting_npcs_table.setHorizontalHeaderLabels(
            ["Name", "Location", "Description", "Description Mode", "Remove"]
        )
        _configure_inline_table(
            self.starting_npcs_table,
            STARTING_NPC_COLUMN_WIDTHS,
            minimum_height=240,
        )
        self.no_starting_npcs_checkbox = QCheckBox("No starting NPCs")
        self.no_starting_npcs_checkbox.toggled.connect(
            self._handle_template_no_starting_npcs_toggled
        )
        self.add_npc_button = QPushButton("Add NPC")
        self.add_npc_button.clicked.connect(lambda: self._append_starting_npc_row({}))
        self.starter_items_table = _AppTableWidget(0, 7)
        self.starter_items_table.setHorizontalHeaderLabels(
            ["Name", "Amount", "Category", "Description", "Value", "Storage", "Remove"]
        )
        _configure_inline_table(
            self.starter_items_table,
            STARTER_ITEM_COLUMN_WIDTHS,
            minimum_height=170,
        )
        self.add_starter_item_button = QPushButton("Add Item")
        self.add_starter_item_button.clicked.connect(
            lambda: self._append_starter_item_row({})
        )
        self.starter_weapons_table = _AppTableWidget(0, 9)
        self.starter_weapons_table.setHorizontalHeaderLabels(
            [
                "Name",
                "Amount",
                "Hands",
                "Damage",
                "Skill",
                "Range",
                "Ammo Type",
                "Clip Size",
                "Remove",
            ]
        )
        _configure_inline_table(
            self.starter_weapons_table,
            STARTER_WEAPON_COLUMN_WIDTHS,
            minimum_height=150,
        )
        self.add_starter_weapon_button = QPushButton("Add Weapon")
        self.add_starter_weapon_button.clicked.connect(
            lambda: self._append_starter_weapon_row({})
        )
        self.starter_armor_table = _AppTableWidget(0, 6)
        self.starter_armor_table.setHorizontalHeaderLabels(
            ["Name", "Amount", "Covers", "Armor Bonus", "Value", "Remove"]
        )
        _configure_inline_table(
            self.starter_armor_table,
            STARTER_ARMOR_COLUMN_WIDTHS,
            minimum_height=130,
        )
        self.add_starter_armor_button = QPushButton("Add Armor")
        self.add_starter_armor_button.clicked.connect(
            lambda: self._append_starter_armor_row({})
        )
        self.starter_item_suggestions_table = _build_starter_suggestion_table("Item")
        self.starter_weapon_suggestions_table = _build_starter_suggestion_table("Weapon")
        self.starter_armor_suggestions_table = _build_starter_suggestion_table("Armor")
        self.add_item_suggestion_button = QPushButton("Add Item Suggestion")
        self.add_item_suggestion_button.clicked.connect(
            lambda: _append_starter_suggestion_table_row(
                self.starter_item_suggestions_table, "Item"
            )
        )
        self.add_weapon_suggestion_button = QPushButton("Add Weapon Suggestion")
        self.add_weapon_suggestion_button.clicked.connect(
            lambda: _append_starter_suggestion_table_row(
                self.starter_weapon_suggestions_table, "Weapon"
            )
        )
        self.add_armor_suggestion_button = QPushButton("Add Armor Suggestion")
        self.add_armor_suggestion_button.clicked.connect(
            lambda: _append_starter_suggestion_table_row(
                self.starter_armor_suggestions_table, "Armor"
            )
        )
        self.currency_table = _AppTableWidget(0, 4)
        self.currency_table.setHorizontalHeaderLabels(["Name", "Plural Name", "Base Value", "Remove"])
        _configure_inline_table(
            self.currency_table,
            CURRENCY_COLUMN_WIDTHS,
            minimum_height=160,
        )
        self.add_currency_button = QPushButton("Add Currency")
        self.add_currency_button.clicked.connect(lambda: self._append_currency_row({}))
        self.economy_examples_table = _AppTableWidget(0, 3)
        self.economy_examples_table.setHorizontalHeaderLabels(["Item", "Base Units", "Remove"])
        _configure_inline_table(
            self.economy_examples_table,
            ECONOMY_EXAMPLE_COLUMN_WIDTHS,
            minimum_height=140,
        )
        self.add_economy_example_button = QPushButton("Add Economy Item")
        self.add_economy_example_button.clicked.connect(
            lambda: self._append_economy_example_row({})
        )
        self._legacy_currency_description = ""
        self.calendar_type_combo = _NoWheelComboBox()
        self.calendar_type_combo.addItem("Gregorian-style calendar", "gregorian")
        self.calendar_type_combo.addItem("AI-generated calendar", "ai_generated")
        self.calendar_type_combo.addItem("Keep/custom calendar", "custom")

        self.starting_task_mode_combo = _NoWheelComboBox()
        self.starting_task_mode_combo.addItem("No starting quest", "none")
        self.starting_task_mode_combo.addItem("Let the A.I. create one", "ai")
        self.starting_task_mode_combo.addItem("Use a custom starting quest", "custom")
        self.starting_task_mode_combo.currentIndexChanged.connect(
            lambda _index: self._sync_template_starting_task_controls()
        )
        self.starting_task_name_input = QLineEdit()
        self.starting_task_description_input = QTextEdit()
        self.starting_task_requester_input = QLineEdit()
        self.starting_task_location_input = QLineEdit()
        self.starting_task_reward_input = QLineEdit()
        self.starting_task_due_date_input = QLineEdit()
        self.starting_task_custom_group = QGroupBox("Custom Quest Draft")
        task_form = QFormLayout()
        task_form.addRow("Name:", self.starting_task_name_input)
        task_form.addRow("Description:", self.starting_task_description_input)
        task_form.addRow("Requester:", self.starting_task_requester_input)
        task_form.addRow("Location:", self.starting_task_location_input)
        task_form.addRow("Reward:", self.starting_task_reward_input)
        task_form.addRow("Due:", self.starting_task_due_date_input)
        self.starting_task_custom_group.setLayout(task_form)

        self.music_enabled_checkbox = QCheckBox("Music enabled")
        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_label = QLabel()
        self.music_volume_slider.valueChanged.connect(
            lambda value: self.music_volume_label.setText(f"{value}%")
        )
        self.sound_effects_enabled_checkbox = QCheckBox("Sound effects enabled")
        self.sound_effects_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.sound_effects_volume_slider.setRange(0, 100)
        self.sound_effects_volume_label = QLabel()
        self.sound_effects_volume_slider.valueChanged.connect(
            lambda value: self.sound_effects_volume_label.setText(f"{value}%")
        )
        self.background_ambience_enabled_checkbox = QCheckBox(
            "Background ambience enabled"
        )
        self.background_ambience_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.background_ambience_volume_slider.setRange(0, 100)
        self.background_ambience_volume_label = QLabel()
        self.background_ambience_volume_slider.valueChanged.connect(
            lambda value: self.background_ambience_volume_label.setText(f"{value}%")
        )
        self.music_test_button = QPushButton("Test")
        self.music_test_button.clicked.connect(self._test_music_preview)
        self.sound_effects_test_button = QPushButton("Test")
        self.sound_effects_test_button.clicked.connect(
            self._test_sound_effects_preview
        )
        self.background_ambience_test_button = QPushButton("Test")
        self.background_ambience_test_button.clicked.connect(
            self._test_background_ambience_preview
        )
        self.template_tts_settings_widget = TTSSettingsWidget(
            audio_settings=self.audio_defaults,
            voice_options=self.voice_options,
            on_sample_voice=self.on_sample_voice,
            on_custom_voice_saved=self._handle_template_custom_voice_saved,
            custom_voice_storage_path=self.custom_voice_storage_path,
        )

        template_buttons = _button_row(new_button, duplicate_button, save_button, delete_button)
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Templates"))
        left_layout.addWidget(self.template_list)
        left_layout.addWidget(template_buttons)
        left_panel = QWidget()
        left_panel.setLayout(left_layout)
        left_panel.setMinimumWidth(260)

        tabs = QTabWidget()
        tabs.addTab(_scrollable_widget(self._build_overview_tab()), "Overview")
        tabs.addTab(_scrollable_widget(self._build_character_tab()), "Character")
        tabs.addTab(_scrollable_widget(self._build_skills_tab()), "Skills")
        tabs.addTab(_scrollable_widget(self._build_starting_task_tab()), "Starting Quest")
        tabs.addTab(_scrollable_widget(self._build_locations_tab()), "Locations")
        tabs.addTab(_scrollable_widget(self._build_npcs_tab()), "NPCs")
        tabs.addTab(_scrollable_widget(self._build_world_tab()), "Inventory & World")
        tabs.addTab(_scrollable_widget(self._build_audio_tab()), "Audio")

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(close_button)

        editor_layout = QVBoxLayout()
        editor_layout.addWidget(tabs)
        editor_layout.addLayout(close_row)

        main_layout = QHBoxLayout()
        main_layout.addWidget(left_panel)
        main_layout.addLayout(editor_layout, stretch=1)
        self.setLayout(main_layout)

        self._refresh_templates()
        self._prime_template_table_capacity()

        if self.template_list.count() == 0:
            self._new_template()
        else:
            self.template_list.setCurrentRow(0)

        self._bind_music_preview_stop_buttons()

    def _build_overview_tab(self) -> QWidget:
        """Builds the template overview tab."""

        form = QFormLayout()
        form.addRow("Template Name:", self.template_name_input)
        form.addRow("Genre:", self.genre_input)
        form.addRow("Narration Tense:", self.narration_tense_combo)
        form.addRow("Narration Style:", self.narration_style_combo)
        form.addRow("Game Style:", self.game_style_input)
        form.addRow("Artificial Intelligence:", self.ai_settings_button)
        form.addRow("", self.ai_settings_summary_label)

        tab = QWidget()
        tab.setLayout(form)
        return tab

    def _build_character_tab(self) -> QWidget:
        """Builds the player-character template tab."""

        form = QFormLayout()
        form.addRow("Character Name:", self.character_name_input)
        form.addRow("Name Pronunciation:", self.character_name_pronunciation_input)
        form.addRow("Appearance:", self.appearance_input)
        form.addRow("Backstory:", self.backstory_input)
        form.addRow("Notes:", self.character_notes_input)

        tab = QWidget()
        tab.setLayout(form)
        return tab

    def _build_skills_tab(self) -> QWidget:
        """Builds the starting skills template tab."""

        layout = QVBoxLayout()
        self.skill_preset_combo = _NoWheelComboBox()
        for label, key in (("Professional Adventurer", "professional"), ("Experienced Adventurer", "experienced"), ("Average Adventurer", "average"), ("Beginner Adventurer", "beginner"), ("Blank Slate / Hardcore Mode", "blank"), ("Custom", "custom")):
            self.skill_preset_combo.addItem(label, key)
        layout.addWidget(QLabel("Starting Skill Profile"))
        layout.addWidget(self.skill_preset_combo)
        for level in range(5, 0, -1):
            group = QGroupBox(f"Level {level}")
            group_layout = QVBoxLayout()
            table = _AppTableWidget(0, 3)
            table.setHorizontalHeaderLabels(["Remove", "Skill", "Description for AI"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self.skill_tables[level] = table
            add_button = QPushButton("Add Skill")
            add_button.clicked.connect(lambda _checked=False, skill_level=level: self._add_template_skill_row(skill_level))
            controls = _button_row(add_button)
            self.skill_table_controls[level] = controls
            group_layout.addWidget(table)
            group_layout.addWidget(controls)
            group.setLayout(group_layout)
            layout.addWidget(group)
        self.skill_preset_combo.currentIndexChanged.connect(self._apply_template_skill_preset)
        self._apply_template_skill_preset()
        layout.addStretch()
        tab = QWidget()
        tab.setLayout(layout)
        return tab

    def _build_starting_task_tab(self) -> QWidget:
        layout = QVBoxLayout()
        form = QFormLayout()
        form.addRow("Starting Quest:", self.starting_task_mode_combo)
        layout.addLayout(form)
        layout.addWidget(self.starting_task_custom_group)
        layout.addStretch()
        tab = QWidget()
        tab.setLayout(layout)
        return tab

    def _build_audio_tab(self) -> QWidget:
        form = QFormLayout()
        form.addRow("Background Music:", self.music_enabled_checkbox)
        form.addRow("Music Volume:", _slider_row(self.music_volume_slider, self.music_volume_label))
        form.addRow("Music Preview:", self.music_test_button)
        form.addRow("Narration Sound Effects:", self.sound_effects_enabled_checkbox)
        form.addRow(
            "Sound Effects Volume:",
            _slider_row(
                self.sound_effects_volume_slider,
                self.sound_effects_volume_label,
            ),
        )
        form.addRow("Sound Effects Preview:", self.sound_effects_test_button)
        form.addRow("Background Ambience:", self.background_ambience_enabled_checkbox)
        form.addRow(
            "Ambience Volume:",
            _slider_row(
                self.background_ambience_volume_slider,
                self.background_ambience_volume_label,
            ),
        )
        form.addRow("Ambience Preview:", self.background_ambience_test_button)
        form.addRow("Narration / TTS:", self.template_tts_settings_widget)
        tab = QWidget()
        tab.setLayout(form)
        return tab

    def _test_music_preview(self) -> None:
        if self.sound_manager is None:
            return

        tracks = self.sound_manager.get_valid_track_names()
        if not tracks:
            return

        self.sound_manager.set_music_volume(self.music_volume_slider.value())
        self.sound_manager.play_music_preview(random.choice(tracks))

    def _test_sound_effects_preview(self) -> None:
        if self.sound_manager is None:
            return

        sound_effects = self.sound_manager.get_valid_sound_effect_names()
        if not sound_effects:
            return

        self.sound_manager.set_sound_effects_volume(
            self.sound_effects_volume_slider.value()
        )
        self.sound_manager.play_sound_effect(random.choice(sound_effects))

    def _test_background_ambience_preview(self) -> None:
        if self.sound_manager is None:
            return

        ambience_tracks = self.sound_manager.get_valid_background_ambience_names()
        if not ambience_tracks:
            return

        self.sound_manager.set_background_ambience_volume(
            self.background_ambience_volume_slider.value()
        )
        self.sound_manager.play_background_ambience(random.choice(ambience_tracks))

    def _bind_music_preview_stop_buttons(self) -> None:
        for button in self.findChildren(QPushButton):
            if button not in {
                self.music_test_button,
                self.background_ambience_test_button,
            }:
                button.clicked.connect(self._stop_audio_previews)

    def _stop_audio_previews(self) -> None:
        if self.sound_manager is not None:
            self.sound_manager.stop_music()
            if hasattr(self.sound_manager, "stop_background_ambience"):
                self.sound_manager.stop_background_ambience()

    def _build_locations_tab(self) -> QWidget:
        """Builds the template starting locations tab."""

        layout = QVBoxLayout()
        form = QFormLayout()
        form.addRow("Start Location:", self.start_location_combo)
        layout.addLayout(form)
        layout.addWidget(self.starting_locations_table)
        layout.addWidget(_button_row(self.add_location_button))
        opening_scene_group = QGroupBox("Opening Scene Request")
        opening_scene_group_layout = QVBoxLayout(opening_scene_group)
        opening_scene_group_layout.addWidget(
            QLabel(
                "Suggest what you would like the first scene to be about at the selected starting location."
            )
        )
        opening_scene_group_layout.addWidget(self.opening_scene_request_input)
        layout.addWidget(opening_scene_group)
        layout.addStretch()

        tab = QWidget()
        tab.setLayout(layout)
        return tab

    def _build_npcs_tab(self) -> QWidget:
        """Builds the template starting NPCs tab."""

        layout = QVBoxLayout()
        layout.addWidget(self.no_starting_npcs_checkbox)
        layout.addWidget(self.starting_npcs_table)
        layout.addWidget(_button_row(self.add_npc_button))
        layout.addStretch()

        tab = QWidget()
        tab.setLayout(layout)
        return tab

    def _handle_template_no_starting_npcs_toggled(self, checked: bool) -> None:
        """Clears and disables template NPC rows when none are requested."""

        if checked:
            self._resize_template_table(
                self.starting_npcs_table,
                0,
                lambda: self._append_starting_npc_row({}),
            )
        self._sync_template_no_starting_npcs_controls()

    def _sync_template_no_starting_npcs_controls(self) -> None:
        """Keeps template NPC editing aligned with the no-NPC option."""

        allow_npcs = not self.no_starting_npcs_checkbox.isChecked()
        self.starting_npcs_table.setEnabled(allow_npcs)
        self.add_npc_button.setEnabled(allow_npcs)

    def _build_world_tab(self) -> QWidget:
        """Builds the world, items, economy, and calendar template tab."""

        form = QFormLayout()
        form.addRow("World Details:", self.world_context_input)
        form.addRow("Starter Items:", self.starter_items_table)
        form.addRow("", _button_row(self.add_starter_item_button))
        form.addRow("Item Suggestions:", self.starter_item_suggestions_table)
        form.addRow("", _button_row(self.add_item_suggestion_button))
        form.addRow("Starter Weapons:", self.starter_weapons_table)
        form.addRow("", _button_row(self.add_starter_weapon_button))
        form.addRow("Weapon Suggestions:", self.starter_weapon_suggestions_table)
        form.addRow("", _button_row(self.add_weapon_suggestion_button))
        form.addRow("Starter Armor:", self.starter_armor_table)
        form.addRow("", _button_row(self.add_starter_armor_button))
        form.addRow("Armor Suggestions:", self.starter_armor_suggestions_table)
        form.addRow("", _button_row(self.add_armor_suggestion_button))
        form.addRow("Currencies:", self.currency_table)
        form.addRow("", _button_row(self.add_currency_button))
        form.addRow("Economy Notes:", self.economy_examples_table)
        form.addRow("", _button_row(self.add_economy_example_button))
        form.addRow("Calendar:", self.calendar_type_combo)

        tab = QWidget()
        tab.setLayout(form)
        return tab

    def _refresh_templates(self, *, selected_name: str | None = None) -> None:
        """Reloads templates from disk into the selector."""

        self.templates = load_new_game_templates(
            self.template_path,
            legacy_template_path=self.legacy_template_path,
            normalize_setups=False,
        )
        selected_key = str(selected_name or "").strip().casefold()
        selected_row = -1

        self.template_list.blockSignals(True)
        self.template_list.clear()

        for index, template in enumerate(self.templates):
            self.template_list.addItem(template.name)

            if selected_key and template.name.casefold() == selected_key:
                selected_row = index

        self.template_list.blockSignals(False)

        if selected_row >= 0:
            self.template_list.setCurrentRow(selected_row)

    def _new_template(self) -> None:
        """Starts a blank reusable template."""

        self.active_template_name = None
        self.active_setup = {}
        self.template_list.clearSelection()
        self._load_setup_into_editor("New Template", {})

    def _duplicate_template(self) -> None:
        """Creates and selects a copy of the current template."""
        source_name = self.active_template_name or self.template_name_input.text().strip()
        if not source_name:
            return
        setup = self._build_setup_from_editor()
        existing = {template.name.casefold() for template in self.templates}
        candidate = f"{source_name} Copy"
        suffix = 2
        while candidate.casefold() in existing:
            candidate = f"{source_name} Copy {suffix}"
            suffix += 1
        if not save_new_game_template(self.template_path, setup, template_name=candidate, normalize_setup=False):
            QMessageBox.warning(self, "Template Not Duplicated", "Could not duplicate the template.")
            return
        self.active_template_name = candidate
        self.active_setup = setup
        self._refresh_templates(selected_name=candidate)

    def _open_template_ai_settings(self) -> None:
        dialog = AISettingsDialog(
            self,
            settings={
                **self._new_game_ai_settings,
                "narration_tense": self.narration_tense_combo.currentData(),
                "narration_style": self.narration_style_combo.currentData(),
            },
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.build_ai_settings()
        current_text_model = normalize_text_model(
            self._new_game_ai_settings.get("text_model")
        )
        self._new_game_ai_settings = {
            key: value for key, value in settings.items()
            if key not in {"narration_tense", "narration_style"}
        }
        self._new_game_ai_settings["text_model"] = current_text_model
        _set_combo_to_data(self.narration_tense_combo, settings["narration_tense"])
        _set_combo_to_data(self.narration_style_combo, settings["narration_style"])
        self._refresh_template_ai_settings_summary()

    def _refresh_template_ai_settings_summary(self) -> None:
        modes = normalize_ai_mode_preferences(self._new_game_ai_settings)
        self.ai_settings_summary_label.setText(
            f"{modes['model_intelligence'].title()} model, "
            f"{modes['model_tone'].title()} tone, "
            f"{modes['response_length'].title()} responses"
        )

    def _load_selected_template(self, row: int) -> None:
        """Loads the selected stored template into the editor."""

        if row < 0 or row >= len(self.templates):
            return

        template = self.templates[row]
        self.active_template_name = template.name
        self.active_setup = deepcopy(template.setup)
        self._load_setup_into_editor(template.name, deepcopy(template.setup))

    @staticmethod
    def _resize_template_table(
        table: QTableWidget,
        desired_rows: int,
        append_row: Callable[[], None],
    ) -> None:
        """Reuses existing inline editors and changes only the row-count difference."""

        desired_rows = max(0, desired_rows)
        while table.rowCount() < desired_rows:
            append_row()
        for row in range(table.rowCount()):
            table.setRowHidden(row, row >= desired_rows)

    def _prime_template_table_capacity(self) -> None:
        """Allocates the largest required row pools once before selection begins."""

        maximums = {
            "locations": 0,
            "npcs": 0,
            "items": 0,
            "weapons": 0,
            "armor": 0,
            "item_suggestions": 0,
            "weapon_suggestions": 0,
            "armor_suggestions": 0,
            "currencies": 0,
            "economy": 0,
        }
        skill_maximums = {level: 0 for level in range(1, 6)}
        for template in self.templates:
            setup = template.setup
            maximums["locations"] = max(
                maximums["locations"],
                len(self._starting_locations_for_editor(setup.get("starting_locations", []))),
            )
            maximums["npcs"] = max(
                maximums["npcs"],
                len(self._starting_npcs_for_editor(setup.get("starting_npcs", []))),
            )
            item_counts = {key: 0 for key in (
                "items", "weapons", "armor", "item_suggestions",
                "weapon_suggestions", "armor_suggestions",
            )}
            for item in self._starter_items_for_editor(setup.get("starter_items", [])):
                kind = _starter_item_kind(item)
                if item.get("requires_ai_invention") and item.get("item_request"):
                    key = {
                        "Weapon": "weapon_suggestions",
                        "Armor": "armor_suggestions",
                    }.get(kind, "item_suggestions")
                else:
                    key = {"Weapon": "weapons", "Armor": "armor"}.get(
                        kind, "items"
                    )
                item_counts[key] += 1
            for key, count in item_counts.items():
                maximums[key] = max(maximums[key], count)
            maximums["currencies"] = max(
                maximums["currencies"],
                len(self._currency_denominations_for_editor(
                    setup.get("currency_denominations", [])
                )),
            )
            maximums["economy"] = max(
                maximums["economy"],
                len(normalize_economy_examples(setup.get("economy_examples", []))),
            )
            preset = str(setup.get("skill_preset", "professional") or "professional")
            plan = list(SKILL_PRESET_LEVEL_PLANS.get(preset, []))
            if preset == "custom" and isinstance(setup.get("skill_level_plan"), list):
                plan = [
                    min(5, max(1, _safe_int(raw_level, 1)))
                    for raw_level in setup["skill_level_plan"]
                ]
            skill_counts = {level: plan.count(level) for level in range(1, 6)}
            raw_skills = setup.get("skills", [])
            if isinstance(raw_skills, list):
                explicit_counts = {level: 0 for level in range(1, 6)}
                for skill in raw_skills:
                    if isinstance(skill, dict):
                        level = min(5, max(1, _safe_int(skill.get("level"), 1)))
                        explicit_counts[level] += 1
                for level in range(1, 6):
                    skill_counts[level] = max(
                        skill_counts[level], explicit_counts[level]
                    )
            for level, count in skill_counts.items():
                skill_maximums[level] = max(skill_maximums[level], count)

        self._loading_template_setup = True
        try:
            self._resize_template_table(
                self.starting_locations_table,
                maximums["locations"],
                lambda: self._append_starting_location_row({}),
            )
            self._resize_template_table(
                self.starting_npcs_table,
                maximums["npcs"],
                lambda: self._append_starting_npc_row({}),
            )
            for level, maximum in skill_maximums.items():
                self._resize_template_table(
                    self.skill_tables[level],
                    maximum,
                    lambda skill_level=level: self._add_template_skill_row(skill_level),
                )
            for table, maximum, append_row in (
                (self.starter_items_table, maximums["items"], lambda: self._append_starter_item_row({})),
                (self.starter_weapons_table, maximums["weapons"], lambda: self._append_starter_weapon_row({})),
                (self.starter_armor_table, maximums["armor"], lambda: self._append_starter_armor_row({})),
                (self.starter_item_suggestions_table, maximums["item_suggestions"], lambda: _append_starter_suggestion_table_row(self.starter_item_suggestions_table, "Item")),
                (self.starter_weapon_suggestions_table, maximums["weapon_suggestions"], lambda: _append_starter_suggestion_table_row(self.starter_weapon_suggestions_table, "Weapon")),
                (self.starter_armor_suggestions_table, maximums["armor_suggestions"], lambda: _append_starter_suggestion_table_row(self.starter_armor_suggestions_table, "Armor")),
                (self.currency_table, maximums["currencies"], lambda: self._append_currency_row({})),
                (self.economy_examples_table, maximums["economy"], lambda: self._append_economy_example_row({})),
            ):
                self._resize_template_table(table, maximum, append_row)
        finally:
            self._loading_template_setup = False

    def _load_setup_into_editor(self, template_name: str, setup: dict[str, Any]) -> None:
        """Populates editor controls from a possibly partial template setup."""

        self._loading_template_setup = True
        self.setUpdatesEnabled(False)
        try:
            character = (
                setup.get("character", {})
                if isinstance(setup.get("character"), dict)
                else {}
            )
            self.template_name_input.setText(template_name)
            self.genre_input.setText(
                str(setup.get("specified_genre", setup.get("genre", "")) or "")
            )
            self.start_location_input.setText(str(setup.get("start_location", "") or ""))
            self.opening_scene_request_input.setPlainText(
                str(setup.get("opening_scene_request", "") or "")
            )
            _set_combo_to_data(
                self.start_location_mode_combo,
                str(setup.get("start_location_mode", "suggestion") or "suggestion"),
            )
            narration = normalize_narration_preferences(
                setup.get("narration", {}) if isinstance(setup.get("narration"), dict) else {}
            )
            _set_combo_to_data(self.narration_tense_combo, narration["tense"])
            _set_combo_to_data(self.narration_style_combo, narration["style"])
            self.game_style_input.setPlainText(str(setup.get("game_style", "") or ""))
            self.world_context_input.setPlainText(str(setup.get("world_context", "") or ""))

            locations = self._starting_locations_for_editor(
                setup.get("starting_locations", [])
            )
            self._resize_template_table(
                self.starting_locations_table,
                len(locations),
                lambda: self._append_starting_location_row({}),
            )
            for location_row, location in enumerate(locations):
                self._update_starting_location_row(location_row, location)

            self._refresh_starting_location_dropdowns(force=True)
            self._select_starting_location_combo_by_name(
                str(setup.get("start_location", "") or "")
            )

            npcs = self._starting_npcs_for_editor(setup.get("starting_npcs", []))
            self._resize_template_table(
                self.starting_npcs_table,
                len(npcs),
                lambda: self._append_starting_npc_row({}),
            )
            for npc_row, npc in enumerate(npcs):
                self._update_starting_npc_row(npc_row, npc)

            self.no_starting_npcs_checkbox.blockSignals(True)
            self.no_starting_npcs_checkbox.setChecked(
                bool(setup.get("no_starting_npcs", False))
                and not npcs
            )
            self.no_starting_npcs_checkbox.blockSignals(False)
            self._sync_template_no_starting_npcs_controls()

            self.character_name_input.setText(str(character.get("name", "") or ""))
            self.character_name_pronunciation_input.setText(
                str(character.get("name_pronunciation", "") or "")
            )
            self.appearance_input.setPlainText(
                str(character.get("appearance", "") or "")
            )
            self.backstory_input.setPlainText(str(character.get("backstory", "") or ""))
            self.character_notes_input.setPlainText(
                str(character.get("notes", "") or "")
            )

            preset_index = self.skill_preset_combo.findData(
                setup.get("skill_preset", "professional")
            )
            self.skill_preset_combo.blockSignals(True)
            self.skill_preset_combo.setCurrentIndex(max(0, preset_index))
            self.skill_preset_combo.blockSignals(False)
            self._apply_template_skill_preset()

            selected_preset = str(
                self.skill_preset_combo.currentData() or "professional"
            )
            if selected_preset == "custom":
                custom_plan = setup.get("skill_level_plan", [])
                level_counts = {level: 0 for level in range(1, 6)}
                if isinstance(custom_plan, list):
                    for raw_level in custom_plan:
                        level = min(5, max(1, _safe_int(raw_level, 1)))
                        level_counts[level] += 1
                for level, table in self.skill_tables.items():
                    self._resize_template_table(
                        table,
                        level_counts[level],
                        lambda skill_level=level: self._add_template_skill_row(
                            skill_level
                        ),
                    )
                self._sync_template_skill_inputs()

            skills = self._skills_for_editor(setup.get("skills", []))
            self._load_template_skills(skills)

            exact_items: list[dict[str, Any]] = []
            exact_weapons: list[dict[str, Any]] = []
            exact_armor: list[dict[str, Any]] = []
            item_suggestions: list[str] = []
            weapon_suggestions: list[str] = []
            armor_suggestions: list[str] = []
            for item in self._starter_items_for_editor(setup.get("starter_items", [])):
                kind = _starter_item_kind(item)
                if item.get("requires_ai_invention") and item.get("item_request"):
                    suggestion = str(item.get("item_request", ""))
                    if kind == "Weapon":
                        weapon_suggestions.append(suggestion)
                    elif kind == "Armor":
                        armor_suggestions.append(suggestion)
                    else:
                        item_suggestions.append(suggestion)
                elif kind == "Weapon":
                    exact_weapons.append(item)
                elif kind == "Armor":
                    exact_armor.append(item)
                else:
                    exact_items.append(item)

            self._load_starter_item_rows(exact_items)
            self._load_starter_weapon_rows(exact_weapons)
            self._load_starter_armor_rows(exact_armor)
            self._load_starter_suggestion_rows(
                self.starter_item_suggestions_table, "Item", item_suggestions
            )
            self._load_starter_suggestion_rows(
                self.starter_weapon_suggestions_table, "Weapon", weapon_suggestions
            )
            self._load_starter_suggestion_rows(
                self.starter_armor_suggestions_table, "Armor", armor_suggestions
            )

            denominations = self._currency_denominations_for_editor(
                setup.get("currency_denominations", [])
            )
            self._load_currency_rows(denominations)

            economy_examples = normalize_economy_examples(
                setup.get("economy_examples", [])
            )
            self._load_economy_example_rows(economy_examples)

            self._legacy_currency_description = (
                "" if economy_examples else str(setup.get("currency_description", "") or "")
            )
            _set_combo_to_data(
                self.calendar_type_combo,
                self._template_calendar_type(setup.get("calendar", {})),
            )
            self._load_template_starting_task(
                setup.get("starting_task", setup.get("starting_quest", {}))
            )
            template_audio = (
                setup.get("audio", {})
                if isinstance(setup.get("audio"), dict)
                else {}
            )
            audio = {
                **self.audio_defaults,
                **template_audio,
                "tts_custom_voices": merge_custom_voices(
                    template_audio.get("tts_custom_voices", []),
                    self.audio_defaults.get("tts_custom_voices", []),
                ),
            }
            self.music_enabled_checkbox.setChecked(
                bool(audio.get("music_enabled", True))
            )
            self.music_volume_slider.setValue(_safe_int(audio.get("music_volume"), 25))
            self.music_volume_label.setText(f"{self.music_volume_slider.value()}%")
            self.sound_effects_enabled_checkbox.setChecked(
                bool(audio.get("sound_effects_enabled", True))
            )
            self.sound_effects_volume_slider.setValue(
                _safe_int(audio.get("sound_effects_volume"), 35)
            )
            self.sound_effects_volume_label.setText(
                f"{self.sound_effects_volume_slider.value()}%"
            )
            self.background_ambience_enabled_checkbox.setChecked(
                bool(audio.get("background_ambience_enabled", True))
            )
            self.background_ambience_volume_slider.setValue(
                _safe_int(audio.get("background_ambience_volume"), 15)
            )
            self.background_ambience_volume_label.setText(
                f"{self.background_ambience_volume_slider.value()}%"
            )
            self.template_tts_settings_widget.load_audio_settings(audio)
            ai_settings = (
                setup.get("ai_settings", {})
                if isinstance(setup.get("ai_settings"), dict)
                else {}
            )
            self._new_game_ai_settings = normalize_ai_mode_preferences(ai_settings)
            self._new_game_ai_settings["additional_context"] = str(
                ai_settings.get("additional_context", "") or ""
            )
            self._refresh_template_ai_settings_summary()
        finally:
            self._loading_template_setup = False
            self.setUpdatesEnabled(True)
            self.update()

    def _handle_template_custom_voice_saved(self, audio_settings: dict[str, Any]) -> None:
        """Keeps template-editor custom voices in the shared app-level library."""

        audio = normalize_tts_audio_fields(audio_settings)
        self.audio_defaults = normalize_tts_audio_fields(
            {
                **self.audio_defaults,
                **audio,
                "tts_custom_voices": merge_custom_voices(
                    audio["tts_custom_voices"],
                    self.audio_defaults.get("tts_custom_voices", []),
                ),
            }
        )
        if self.on_tts_settings_saved is not None:
            self.on_tts_settings_saved(self.audio_defaults)

    def _save_template(self) -> None:
        """Saves the current editor contents as a reusable template."""

        template_name = self.template_name_input.text().strip()

        if not template_name:
            QMessageBox.warning(self, "Missing Template Name", "Enter a template name first.")
            return

        setup = self._build_setup_from_editor()

        if (
            self.active_template_name
            and self.active_template_name.casefold() != template_name.casefold()
        ):
            delete_new_game_template(self.template_path, self.active_template_name)

        if not save_new_game_template(
            self.template_path,
            setup,
            template_name=template_name,
            normalize_setup=False,
        ):
            QMessageBox.warning(self, "Template Not Saved", "Could not save the template.")
            return

        self.active_template_name = template_name
        self.active_setup = setup
        self._refresh_templates(selected_name=template_name)

    def _delete_template(self) -> None:
        """Deletes the selected reusable template."""

        template_name = self.active_template_name or self.template_name_input.text().strip()

        if not template_name:
            return

        result = QMessageBox.question(
            self,
            "Delete Template",
            f"Delete the template '{template_name}'?",
        )

        if result != QMessageBox.StandardButton.Yes:
            return

        if not delete_new_game_template(self.template_path, template_name):
            QMessageBox.warning(self, "Template Not Deleted", "Could not delete the template.")
            return

        self._refresh_templates()

        if self.template_list.count() == 0:
            self._new_template()
        else:
            self.template_list.setCurrentRow(0)

    def _build_setup_from_editor(self) -> dict[str, Any]:
        """Builds a partial setup dictionary from the editor controls."""

        setup = deepcopy(self.active_setup)
        setup["title"] = self.template_name_input.text().strip()
        setup["specified_genre"] = self.genre_input.text().strip()
        setup["game_style"] = self.game_style_input.toPlainText().strip()
        selected_start_location = self._selected_starting_location_for_setup()
        setup["starting_locations"] = self._starting_locations_from_table()
        setup["starting_npcs"] = self._starting_npcs_from_table()
        setup["no_starting_npcs"] = self.no_starting_npcs_checkbox.isChecked()
        setup["start_location"] = (
            selected_start_location.get("name") or self.start_location_input.text().strip()
        )
        setup["start_location_mode"] = (
            selected_start_location.get("location_mode")
            or self.start_location_mode_combo.currentData()
            or "suggestion"
        )
        setup["opening_scene_request"] = (
            self.opening_scene_request_input.toPlainText().strip()
        )
        setup["world_context"] = self.world_context_input.toPlainText().strip()
        setup["narration"] = {
            "tense": self.narration_tense_combo.currentData() or DEFAULT_NARRATION_TENSE,
            "style": self.narration_style_combo.currentData() or DEFAULT_NARRATION_STYLE,
        }
        setup["character"] = {
            **(setup.get("character", {}) if isinstance(setup.get("character"), dict) else {}),
            "name": self.character_name_input.text().strip(),
            "name_pronunciation": self.character_name_pronunciation_input.text().strip(),
            "appearance": self.appearance_input.toPlainText().strip(),
            "backstory": self.backstory_input.toPlainText().strip(),
            "notes": self.character_notes_input.toPlainText().strip(),
        }
        setup["skills"] = [
            skill for level in range(5, 0, -1)
            for skill in self._template_skills_from_table(level)
        ]
        setup["skill_preset"] = str(self.skill_preset_combo.currentData() or "professional")
        setup["skill_level_plan"] = [level for level, _name, _description in self.skill_inputs]
        setup["starter_items"] = [
            *self._starter_items_from_table(),
            *_starter_suggestions_from_table(
                self.starter_item_suggestions_table, "Item"
            ),
            *_starter_suggestions_from_table(
                self.starter_weapon_suggestions_table, "Weapon"
            ),
            *_starter_suggestions_from_table(
                self.starter_armor_suggestions_table, "Armor"
            ),
        ]
        setup["currency_denominations"] = self._currency_denominations_from_table()
        setup["economy_examples"] = self._economy_examples_from_table()
        setup["currency_description"] = (
            describe_economy_examples(setup["economy_examples"])
            or self._legacy_currency_description
        )

        calendar_type = str(self.calendar_type_combo.currentData() or "gregorian")

        if calendar_type == "ai_generated":
            setup["calendar"] = {"calendar_type": "ai_generated", "ai_generated": True}
        elif calendar_type == "gregorian":
            setup["calendar"] = {"calendar_type": "gregorian", "ai_generated": False}
        else:
            existing_calendar = (
                setup.get("calendar", {}) if isinstance(setup.get("calendar"), dict) else {}
            )
            setup["calendar"] = {**existing_calendar, "calendar_type": "custom"}

        setup["starting_task"] = self._template_starting_task_from_controls()
        setup["audio"] = {
            "music_enabled": self.music_enabled_checkbox.isChecked(),
            "music_volume": self.music_volume_slider.value(),
            "sound_effects_enabled": self.sound_effects_enabled_checkbox.isChecked(),
            "sound_effects_volume": self.sound_effects_volume_slider.value(),
            "background_ambience_enabled": (
                self.background_ambience_enabled_checkbox.isChecked()
            ),
            "background_ambience_volume": (
                self.background_ambience_volume_slider.value()
            ),
            **self.template_tts_settings_widget.build_audio_settings(),
        }
        setup["ai_settings"] = dict(self._new_game_ai_settings)

        return setup

    def _add_template_skill_row(self, level: int, name: str = "", description: str = "") -> None:
        table = self.skill_tables[level]
        row = table.rowCount()
        table.insertRow(row)
        _set_remove_row_button(
            table,
            row,
            0,
            "skill",
            lambda button, skill_level=level: self._remove_template_skill_row(
                skill_level,
                button,
            ),
            name_column=1,
        )
        table.setCellWidget(row, 1, QLineEdit(name))
        table.setCellWidget(row, 2, QLineEdit(description))
        self._sync_template_skill_inputs()

    def _remove_template_skill_row(
        self,
        level: int,
        button: QPushButton,
    ) -> None:
        """Removes one confirmed custom skill row from the template."""

        table = self.skill_tables[level]
        if _remove_table_row_by_button(table, button) >= 0:
            self._sync_template_skill_inputs()

    def _sync_template_skill_inputs(self) -> None:
        self.skill_inputs = []
        for level in range(5, 0, -1):
            table = self.skill_tables[level]
            for row in range(table.rowCount()):
                if table.isRowHidden(row):
                    continue
                name = table.cellWidget(row, 1)
                description = table.cellWidget(row, 2)
                if isinstance(name, QLineEdit) and isinstance(description, QLineEdit):
                    self.skill_inputs.append((level, name, description))

    def _apply_template_skill_preset(self, _index: int = -1) -> None:
        preset = str(self.skill_preset_combo.currentData() or "professional")
        plan = SKILL_PRESET_LEVEL_PLANS.get(preset, [])
        custom = preset == "custom"
        for level, table in self.skill_tables.items():
            count = plan.count(level)
            self._resize_template_table(
                table,
                count,
                lambda skill_level=level: self._add_template_skill_row(skill_level),
            )
            for row in range(table.rowCount()):
                name = table.cellWidget(row, 1)
                description = table.cellWidget(row, 2)
                if isinstance(name, QLineEdit):
                    name.clear()
                if isinstance(description, QLineEdit):
                    description.clear()
            parent_widget = table.parentWidget()
            if parent_widget is not None:
                parent_widget.setVisible(custom or count > 0)
            self.skill_table_controls[level].setVisible(custom)
        self._sync_template_skill_inputs()

    def _load_template_skills(self, skills: list[dict[str, Any]]) -> None:
        for table in self.skill_tables.values():
            for row in range(table.rowCount()):
                name = table.cellWidget(row, 1)
                description = table.cellWidget(row, 2)
                if isinstance(name, QLineEdit):
                    name.clear()
                if isinstance(description, QLineEdit):
                    description.clear()

        for skill in skills:
            if not isinstance(skill, dict):
                continue
            if not str(skill.get("name", "") or "").strip() and not str(
                skill.get("description", "") or ""
            ).strip():
                continue
            level = _safe_int(skill.get("level"), 1)
            level = min(5, max(1, level))
            table = self.skill_tables[level]
            target_row = None
            for row in range(table.rowCount()):
                if table.isRowHidden(row):
                    continue
                name = table.cellWidget(row, 1)
                description = table.cellWidget(row, 2)
                if (
                    isinstance(name, QLineEdit)
                    and isinstance(description, QLineEdit)
                    and not name.text().strip()
                    and not description.text().strip()
                ):
                    target_row = row
                    break

            if target_row is None:
                self._add_template_skill_row(level)
                target_row = table.rowCount() - 1

            name = table.cellWidget(target_row, 1)
            description = table.cellWidget(target_row, 2)
            if isinstance(name, QLineEdit):
                name.setText(str(skill.get("name", "") or ""))
            if isinstance(description, QLineEdit):
                description.setText(str(skill.get("description", "") or ""))
        self._sync_template_skill_inputs()

    def _template_skills_from_table(self, level: int) -> list[dict[str, Any]]:
        table = self.skill_tables[level]
        skills = []
        for row in range(table.rowCount()):
            if table.isRowHidden(row):
                continue
            name = table.cellWidget(row, 1)
            description = table.cellWidget(row, 2)
            if isinstance(name, QLineEdit) and isinstance(description, QLineEdit):
                if name.text().strip() or description.text().strip():
                    skills.append({"name": name.text().strip(), "description": description.text().strip(), "level": level})
        return skills

    def _sync_template_starting_task_controls(self) -> None:
        self.starting_task_custom_group.setVisible(
            self.starting_task_mode_combo.currentData() == "custom"
        )

    def _template_starting_task_from_controls(self) -> dict[str, Any]:
        mode = str(self.starting_task_mode_combo.currentData() or "none")
        task: dict[str, Any] = {"mode": mode}
        if mode == "custom":
            task["task"] = {
                "name": self.starting_task_name_input.text().strip(),
                "description": self.starting_task_description_input.toPlainText().strip(),
                "requester": self.starting_task_requester_input.text().strip(),
                "location": self.starting_task_location_input.text().strip(),
                "reward": self.starting_task_reward_input.text().strip(),
                "due_date": self.starting_task_due_date_input.text().strip(),
            }
        return task

    def _load_template_starting_task(self, starting_task: Any) -> None:
        task_setup = starting_task if isinstance(starting_task, dict) else {}
        mode = str(task_setup.get("mode", "none") or "none")
        _set_combo_to_data(self.starting_task_mode_combo, mode)
        task = task_setup.get("task", {}) if isinstance(task_setup.get("task"), dict) else {}
        self.starting_task_name_input.setText(str(task.get("name", "") or ""))
        self.starting_task_description_input.setPlainText(str(task.get("description", "") or ""))
        self.starting_task_requester_input.setText(str(task.get("requester", "") or ""))
        self.starting_task_location_input.setText(str(task.get("location", "") or ""))
        self.starting_task_reward_input.setText(str(task.get("reward", "") or ""))
        self.starting_task_due_date_input.setText(str(task.get("due_date", "") or ""))
        self._sync_template_starting_task_controls()

    def _append_starting_location_row(self, location: dict[str, Any]) -> None:
        """Adds one starting location row to the template editor."""

        self._starting_location_row_id_counter += 1
        _append_starting_location_table_row(
            self.starting_locations_table,
            location,
            self._starting_location_row_id_counter,
            self._remove_starting_location_row,
        )
        row = self.starting_locations_table.rowCount() - 1
        name_widget = self.starting_locations_table.cellWidget(row, 0)
        sublocation_widget = self.starting_locations_table.cellWidget(row, 3)
        parent_widget = self.starting_locations_table.cellWidget(row, 4)

        if isinstance(name_widget, QLineEdit):
            name_widget.textChanged.connect(
                lambda _text: self._refresh_starting_location_dropdowns()
            )

        if isinstance(sublocation_widget, QCheckBox):
            sublocation_widget.toggled.connect(
                lambda _checked: self._refresh_starting_location_dropdowns()
            )

        if isinstance(parent_widget, QComboBox):
            parent_widget.currentIndexChanged.connect(
                lambda _index: self._refresh_starting_location_dropdowns()
            )

        if not self._loading_template_setup:
            self._refresh_starting_location_dropdowns()

    def _update_starting_location_row(
        self,
        row: int,
        location: dict[str, Any],
    ) -> None:
        """Updates one existing location row without replacing its widgets."""

        name_widget = self.starting_locations_table.cellWidget(row, 0)
        description_widget = self.starting_locations_table.cellWidget(row, 1)
        mode_widget = self.starting_locations_table.cellWidget(row, 2)
        sublocation_widget = self.starting_locations_table.cellWidget(row, 3)
        parent_widget = self.starting_locations_table.cellWidget(row, 4)
        if isinstance(name_widget, QLineEdit):
            name_widget.setText(str(location.get("name", "")))
        if isinstance(description_widget, QLineEdit):
            description_widget.setText(str(location.get("description", "")))
        if isinstance(mode_widget, QComboBox):
            _set_combo_to_data(
                mode_widget,
                str(location.get("location_mode", "suggestion") or "suggestion"),
            )
        is_sublocation = bool(location.get("is_sublocation", False))
        if isinstance(sublocation_widget, QCheckBox):
            sublocation_widget.setChecked(is_sublocation)
        if isinstance(parent_widget, QComboBox):
            parent_widget.setProperty(
                "pending_parent_location",
                str(location.get("parent_location", "") or ""),
            )
            parent_widget.setVisible(is_sublocation)

    def _remove_starting_location_row(self, button: QPushButton) -> None:
        """Removes the starting location row containing button."""

        _remove_table_row_by_button(self.starting_locations_table, button)
        self._refresh_starting_location_dropdowns()

    def _starting_locations_from_table(self) -> list[dict[str, Any]]:
        """Reads requested starting location rows from the template editor."""

        return _starting_locations_from_table(self.starting_locations_table)

    def _selected_starting_location_for_setup(self) -> dict[str, str]:
        """Returns the selected template start location, if any."""

        row_id = self.start_location_combo.currentData()

        if row_id in (None, ""):
            return {}

        row = _starting_location_row_for_id(self.starting_locations_table, row_id)

        if row < 0:
            return {}

        name_widget = self.starting_locations_table.cellWidget(row, 0)
        mode_widget = self.starting_locations_table.cellWidget(row, 2)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            return {}

        return {
            "name": name,
            "location_mode": (
                str(mode_widget.currentData())
                if isinstance(mode_widget, QComboBox)
                else "suggestion"
            ),
        }

    def _sync_template_start_location_from_locations_combo(self) -> None:
        """Updates hidden start-location fields from the template Locations tab."""

        selected_start_location = self._selected_starting_location_for_setup()

        if not selected_start_location:
            return

        self.start_location_input.setText(selected_start_location["name"])
        _set_combo_to_data(
            self.start_location_mode_combo,
            selected_start_location["location_mode"],
        )

    def _refresh_starting_location_dropdowns(self, *, force: bool = False) -> None:
        """Keeps template start and parent-location dropdowns aligned."""

        if self._loading_template_setup and not force:
            return

        locations = _starting_location_options_from_table(self.starting_locations_table)
        selected_start = self.start_location_combo.currentData()
        self.start_location_combo.blockSignals(True)
        self.start_location_combo.clear()
        self.start_location_combo.addItem("Select from starting locations", "")

        for row_id, name in locations:
            self.start_location_combo.addItem(name, row_id)

        _set_combo_to_data(self.start_location_combo, str(selected_start or ""))
        self.start_location_combo.blockSignals(False)
        _sync_starting_location_parent_dropdowns(
            self.starting_locations_table,
            locations,
        )
        _sync_starting_npc_location_dropdowns(
            self.starting_npcs_table,
            locations,
        )
        valid_ids = {row_id for row_id, _name in locations}

        if str(selected_start or "") not in valid_ids:
            self.start_location_combo.setCurrentIndex(0)
            if selected_start not in (None, ""):
                self.start_location_input.clear()
            return

        self._sync_template_start_location_from_locations_combo()

    def _select_starting_location_combo_by_name(self, name: str) -> None:
        """Selects a structured template start-location row by visible name."""

        clean_name = str(name or "").strip().casefold()

        if not clean_name:
            return

        for index in range(self.start_location_combo.count()):
            if self.start_location_combo.itemText(index).strip().casefold() == clean_name:
                self.start_location_combo.setCurrentIndex(index)
                return

    def _append_starting_npc_row(self, npc: dict[str, Any]) -> None:
        """Adds one requested starting NPC row to the template editor."""

        _append_starting_npc_table_row(
            self.starting_npcs_table,
            npc,
            self._remove_starting_npc_row,
            location_options=_starting_location_options_from_table(
                self.starting_locations_table
            ),
        )

    def _update_starting_npc_row(self, row: int, npc: dict[str, Any]) -> None:
        """Updates one existing NPC row without replacing its widgets."""

        name_widget = self.starting_npcs_table.cellWidget(row, 0)
        location_widget = self.starting_npcs_table.cellWidget(row, 1)
        description_widget = self.starting_npcs_table.cellWidget(row, 2)
        mode_widget = self.starting_npcs_table.cellWidget(row, 3)
        if isinstance(name_widget, QLineEdit):
            name_widget.setText(str(npc.get("name", npc.get("display_name", ""))))
            name_widget.setProperty(
                "npc_id",
                str(npc.get("npc_id", "")).strip()
                or f"starting_npc_{uuid.uuid4().hex}",
            )
        if isinstance(location_widget, QComboBox):
            requested_location = str(npc.get("location", "") or "").strip()
            _set_combo_to_text(location_widget, requested_location)
            if not requested_location:
                location_widget.setCurrentIndex(0)
        if isinstance(description_widget, QLineEdit):
            description_widget.setText(
                str(npc.get("description", npc.get("public_description", "")))
            )
        if isinstance(mode_widget, QComboBox):
            _set_combo_to_data(
                mode_widget,
                str(npc.get("description_mode", "suggestion") or "suggestion"),
            )

    def _remove_starting_npc_row(self, button: QPushButton) -> None:
        """Removes the starting NPC row containing button."""

        _remove_table_row_by_button(self.starting_npcs_table, button)

    def _starting_npcs_from_table(self) -> list[dict[str, Any]]:
        """Reads requested starting NPC rows from the template editor."""

        return _starting_npcs_from_table(self.starting_npcs_table)

    @staticmethod
    def _starting_locations_for_editor(raw_locations: Any) -> list[dict[str, Any]]:
        """Returns current starting locations as table rows."""

        return [
            location
            for location in (raw_locations if isinstance(raw_locations, list) else [])
            if isinstance(location, dict)
        ]

    @staticmethod
    def _starting_npcs_for_editor(raw_npcs: Any) -> list[dict[str, Any]]:
        """Returns current starting NPCs as table rows."""

        return [
            npc
            for npc in (raw_npcs if isinstance(raw_npcs, list) else [])
            if isinstance(npc, dict)
        ]

    def _skills_for_editor(self, raw_skills: Any) -> list[dict[str, Any]]:
        """Returns sparse template skills positioned by explicit level."""

        skills = raw_skills if isinstance(raw_skills, list) else []
        editor_skills: list[dict[str, Any]] = [{} for _level, _name, _description in self.skill_inputs]
        next_position = 0

        for raw_skill in skills[: len(editor_skills)]:
            if not isinstance(raw_skill, dict):
                raw_skill = {"name": str(raw_skill)}

            if not str(raw_skill.get("name", "") or "").strip() and not str(
                raw_skill.get("description", "") or ""
            ).strip():
                continue

            requested_level = _safe_int(raw_skill.get("level"), 0)
            target_index = -1

            if requested_level:
                for index, (level, _skill_input, _description_input) in enumerate(self.skill_inputs):
                    if level == requested_level and not editor_skills[index]:
                        target_index = index
                        break

            if target_index < 0:
                while next_position < len(editor_skills) and editor_skills[next_position]:
                    next_position += 1

                if next_position >= len(editor_skills):
                    break

                target_index = next_position

            editor_skills[target_index] = dict(raw_skill)

        return editor_skills

    def _append_starter_item_row(self, item: dict[str, Any]) -> None:
        """Adds a starter item row to the template editor."""

        _append_starter_item_table_row(
            self.starter_items_table,
            item,
            self._remove_starter_item_row,
        )

    def _load_starter_item_rows(self, items: list[dict[str, Any]]) -> None:
        """Loads exact starter items while reusing existing row editors."""

        self._resize_template_table(
            self.starter_items_table,
            len(items),
            lambda: self._append_starter_item_row({}),
        )
        for row, item in enumerate(items):
            name = self.starter_items_table.cellWidget(row, 0)
            quantity = self.starter_items_table.cellWidget(row, 1)
            category = self.starter_items_table.cellWidget(row, 2)
            description = self.starter_items_table.cellWidget(row, 3)
            value = self.starter_items_table.cellWidget(row, 4)
            storage = self.starter_items_table.cellWidget(row, 5)
            if isinstance(name, QLineEdit):
                name.setText(str(item.get("name", "")))
            if isinstance(quantity, QSpinBox):
                quantity.setValue(_safe_int(item.get("quantity", 1), 1))
            if isinstance(category, QLineEdit):
                category.setText(str(item.get("category", "Item") or "Item"))
            if isinstance(description, QLineEdit):
                description.setText(str(item.get("description", "")))
            if isinstance(value, QSpinBox):
                value.setValue(_safe_int(item.get("value_base_units", 0), 0))
            if isinstance(storage, QComboBox):
                storage_value = str(
                    item.get("storage_location", "actively_carried")
                    or "actively_carried"
                ).strip()
                if storage.findData(storage_value) >= 0:
                    _set_combo_to_data(storage, storage_value)
                else:
                    storage.setEditText(storage_value)

    def _remove_starter_item_row(self, button: QPushButton) -> None:
        """Removes the starter item row containing button."""

        _remove_table_row_by_button(self.starter_items_table, button)

    def _starter_items_from_table(self) -> list[dict[str, Any]]:
        """Reads starter item rows from the template editor."""

        return [
            *_starter_items_from_table(self.starter_items_table),
            *_starter_weapons_from_table(self.starter_weapons_table),
            *_starter_armor_from_table(self.starter_armor_table),
        ]

    def _append_starter_weapon_row(self, item: dict[str, Any]) -> None:
        """Adds a starter weapon row to the template editor."""

        _append_starter_weapon_table_row(
            self.starter_weapons_table,
            item,
            self._remove_starter_weapon_row,
        )

    def _load_starter_weapon_rows(self, items: list[dict[str, Any]]) -> None:
        """Loads exact starter weapons while reusing existing row editors."""

        self._resize_template_table(
            self.starter_weapons_table,
            len(items),
            lambda: self._append_starter_weapon_row({}),
        )
        for row, item in enumerate(items):
            name = self.starter_weapons_table.cellWidget(row, 0)
            quantity = self.starter_weapons_table.cellWidget(row, 1)
            hands = self.starter_weapons_table.cellWidget(row, 2)
            damage = self.starter_weapons_table.cellWidget(row, 3)
            attack_skill = self.starter_weapons_table.cellWidget(row, 4)
            attack_range = self.starter_weapons_table.cellWidget(row, 5)
            ammunition = self.starter_weapons_table.cellWidget(row, 6)
            clip_size = self.starter_weapons_table.cellWidget(row, 7)
            if isinstance(name, QLineEdit):
                name.setText(str(item.get("name", "")))
            if isinstance(quantity, QSpinBox):
                quantity.setValue(_safe_int(item.get("quantity", 1), 1))
            if isinstance(hands, QComboBox):
                _set_combo_to_data(
                    hands,
                    _metadata_text(item, "weapon_hands", "one-handed")
                    or "one-handed",
                )
            if isinstance(damage, QLineEdit):
                damage.setText(_metadata_text(item, "damage", "1d6") or "1d6")
            if isinstance(attack_skill, QLineEdit):
                attack_skill.setText(
                    _metadata_text(item, "attack_skill", "Melee") or "Melee"
                )
            if isinstance(attack_range, QSpinBox):
                attack_range.setValue(
                    max(0, _metadata_int(item, "attack_range_feet", 5))
                )
            if isinstance(ammunition, QLineEdit):
                ammunition.setText(
                    _metadata_text(item, "ammunition_type_required")
                )
            if isinstance(clip_size, QSpinBox):
                clip_size.setValue(max(0, _metadata_int(item, "clip_size", 0)))

    def _remove_starter_weapon_row(self, button: QPushButton) -> None:
        """Removes the starter weapon row containing button."""

        _remove_table_row_by_button(self.starter_weapons_table, button)

    def _append_starter_armor_row(self, item: dict[str, Any]) -> None:
        """Adds a starter armor row to the template editor."""

        _append_starter_armor_table_row(
            self.starter_armor_table,
            item,
            self._remove_starter_armor_row,
        )

    def _load_starter_armor_rows(self, items: list[dict[str, Any]]) -> None:
        """Loads exact starter armor while reusing existing row editors."""

        self._resize_template_table(
            self.starter_armor_table,
            len(items),
            lambda: self._append_starter_armor_row({}),
        )
        for row, item in enumerate(items):
            name = self.starter_armor_table.cellWidget(row, 0)
            quantity = self.starter_armor_table.cellWidget(row, 1)
            covers = self.starter_armor_table.cellWidget(row, 2)
            armor_rating = self.starter_armor_table.cellWidget(row, 3)
            value = self.starter_armor_table.cellWidget(row, 4)
            raw_covers = item.get("covers_body_parts")
            if not isinstance(raw_covers, list) and isinstance(
                item.get("metadata"), dict
            ):
                raw_covers = item["metadata"].get("covers_body_parts")
            covers_parts = raw_covers if isinstance(raw_covers, list) else []
            if isinstance(name, QLineEdit):
                name.setText(str(item.get("name", "")))
            if isinstance(quantity, QSpinBox):
                quantity.setValue(_safe_int(item.get("quantity", 1), 1))
            if isinstance(covers, QLineEdit):
                covers.setText(
                    ", ".join(str(part) for part in covers_parts if part is not None)
                )
            if isinstance(armor_rating, QSpinBox):
                armor_rating.setValue(
                    max(0, _metadata_int(item, "armor_rating", 1))
                )
            if isinstance(value, QSpinBox):
                value.setValue(_safe_int(item.get("value_base_units", 0), 0))

    def _load_starter_suggestion_rows(
        self,
        table: QTableWidget,
        kind: str,
        suggestions: list[str],
    ) -> None:
        """Loads starter suggestions while reusing existing row editors."""

        self._resize_template_table(
            table,
            len(suggestions),
            lambda: _append_starter_suggestion_table_row(table, kind),
        )
        for row, suggestion in enumerate(suggestions):
            editor = table.cellWidget(row, 0)
            if isinstance(editor, QLineEdit):
                editor.setText(suggestion)

    def _remove_starter_armor_row(self, button: QPushButton) -> None:
        """Removes the starter armor row containing button."""

        _remove_table_row_by_button(self.starter_armor_table, button)

    @staticmethod
    def _starter_items_for_editor(raw_items: Any) -> list[dict[str, Any]]:
        """Returns legacy and current starter items as table rows."""

        if not isinstance(raw_items, list):
            return []

        items: list[dict[str, Any]] = []

        for raw_item in raw_items:
            if isinstance(raw_item, dict):
                items.append(raw_item)
                continue

            items.extend(parse_starter_items_text(str(raw_item)))

        return items

    def _append_currency_row(self, denomination: dict[str, Any]) -> None:
        """Adds a currency denomination row to the template editor."""

        _append_currency_table_row(
            self.currency_table,
            denomination,
            self._remove_currency_row,
        )

    def _load_currency_rows(self, denominations: list[dict[str, Any]]) -> None:
        """Loads currency rows while reusing existing row editors."""

        self._resize_template_table(
            self.currency_table,
            len(denominations),
            lambda: self._append_currency_row({}),
        )
        for row, denomination in enumerate(denominations):
            name = self.currency_table.cellWidget(row, 0)
            plural_name = self.currency_table.cellWidget(row, 1)
            value = self.currency_table.cellWidget(row, 2)
            if isinstance(name, QLineEdit):
                name.setText(str(denomination.get("name", "")))
            if isinstance(plural_name, QLineEdit):
                plural_name.setText(str(denomination.get("plural_name", "")))
            if isinstance(value, QSpinBox):
                value.setValue(_safe_int(denomination.get("value", 1), 1))
        _sync_currency_base_value_row(self.currency_table)

    def _remove_currency_row(self, button: QPushButton) -> None:
        """Removes the currency denomination row containing button."""

        if _row_for_cell_widget(self.currency_table, button) == 0:
            return

        if _remove_table_row_by_button(self.currency_table, button) >= 0:
            _sync_currency_base_value_row(self.currency_table)

    def _currency_denominations_from_table(self) -> list[dict[str, Any]]:
        """Reads currency denomination rows from the template editor."""

        return _currency_denominations_from_table(self.currency_table)

    @staticmethod
    def _currency_denominations_for_editor(raw_denominations: Any) -> list[dict[str, Any]]:
        """Returns current currency denominations as table rows."""

        if not isinstance(raw_denominations, list):
            return []

        return [
            denomination
            for denomination in raw_denominations
            if isinstance(denomination, dict)
        ]

    def _append_economy_example_row(self, example: dict[str, Any]) -> None:
        """Adds a common-price example row to the template editor."""

        _append_economy_example_table_row(
            self.economy_examples_table,
            example,
            self._remove_economy_example_row,
        )

    def _load_economy_example_rows(
        self,
        examples: list[dict[str, Any]],
    ) -> None:
        """Loads economy examples while reusing existing row editors."""

        self._resize_template_table(
            self.economy_examples_table,
            len(examples),
            lambda: self._append_economy_example_row({}),
        )
        for row, example in enumerate(examples):
            name = self.economy_examples_table.cellWidget(row, 0)
            value = self.economy_examples_table.cellWidget(row, 1)
            if isinstance(name, QLineEdit):
                name.setText(str(example.get("name", "")))
            if isinstance(value, QSpinBox):
                value.setValue(_safe_int(example.get("value_base_units", 1), 1))

    def _remove_economy_example_row(self, button: QPushButton) -> None:
        """Removes the common-price example row containing button."""

        _remove_table_row_by_button(self.economy_examples_table, button)

    def _economy_examples_from_table(self) -> list[dict[str, Any]]:
        """Reads common-price examples from the template editor."""

        return _economy_examples_from_table(self.economy_examples_table)

    @staticmethod
    def _template_calendar_type(raw_calendar: Any) -> str:
        """Returns the editor calendar mode for a partial template."""

        if not isinstance(raw_calendar, dict):
            return "gregorian"

        if bool(raw_calendar.get("ai_generated", False)):
            return "ai_generated"

        calendar_type = str(raw_calendar.get("calendar_type", "") or "").casefold()

        if calendar_type in {"ai_generated", "custom", "gregorian"}:
            return calendar_type

        return "gregorian"


class CalendarPlayerEventDialog(QDialog):
    """Editor for a private player-authored calendar event."""

    def __init__(
        self,
        *,
        calendar_settings: dict[str, Any],
        default_year: int = 1,
        default_month: int = 1,
        default_day: int = 1,
        event: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Player Calendar Event")
        self._event = dict(event or {})
        settings = dict(calendar_settings)

        self.title_input = QLineEdit(str(self._event.get("title", "")))
        self.category_input = QLineEdit(str(self._event.get("category", "Reminder")))

        self.month_combo = _NoWheelComboBox()
        month_names = list(settings.get("month_names", []))
        for index, name in enumerate(month_names):
            self.month_combo.addItem(str(name), index + 1)
        selected_month = max(
            1,
            _safe_int(self._event.get("month", default_month), default_month),
        )
        selected_month_index = self.month_combo.findData(selected_month)
        if selected_month_index >= 0:
            self.month_combo.setCurrentIndex(selected_month_index)

        days_per_month = max(
            1,
            _safe_int(settings.get("days_per_week", 7), 7)
            * _safe_int(settings.get("weeks_per_month", 4), 4),
        )
        self.day_input = _NoWheelSpinBox()
        self.day_input.setRange(1, days_per_month)
        self.day_input.setValue(
            min(
                days_per_month,
                max(1, _safe_int(self._event.get("day", default_day), default_day)),
            )
        )

        self.year_input = _NoWheelSpinBox()
        self.year_input.setRange(1, 999999)
        self.year_input.setValue(
            max(1, _safe_int(self._event.get("year", default_year), default_year))
        )

        time_minutes = _safe_int(self._event.get("time_of_day_minutes", -1), -1)
        self.all_day_checkbox = QCheckBox("All day / no exact time")
        self.all_day_checkbox.setChecked(time_minutes < 0)
        self.hour_input = _NoWheelSpinBox()
        self.hour_input.setRange(0, 23)
        self.hour_input.setValue(max(0, time_minutes) // 60)
        self.minute_input = _NoWheelSpinBox()
        self.minute_input.setRange(0, 59)
        self.minute_input.setValue(max(0, time_minutes) % 60)
        self.all_day_checkbox.toggled.connect(self._sync_time_inputs)

        time_widget = QWidget()
        time_layout = QHBoxLayout(time_widget)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.addWidget(QLabel("Hour:"))
        time_layout.addWidget(self.hour_input)
        time_layout.addWidget(QLabel("Minute:"))
        time_layout.addWidget(self.minute_input)
        time_layout.addWidget(self.all_day_checkbox)
        time_layout.addStretch(1)

        self.recurrence_combo = _NoWheelComboBox()
        self.recurrence_combo.addItem("One time", "none")
        self.recurrence_combo.addItem("Every year", "yearly")
        _set_combo_to_data(
            self.recurrence_combo,
            str(self._event.get("recurrence", "none")),
        )

        self.duration_input = _NoWheelSpinBox()
        self.duration_input.setRange(1, max(1, days_per_month * len(month_names)))
        self.duration_input.setValue(
            max(1, _safe_int(self._event.get("duration_days", 1), 1))
        )

        self.description_input = QTextEdit()
        self.description_input.setMaximumHeight(80)
        self.description_input.setPlainText(str(self._event.get("description", "")))
        self.details_input = QTextEdit()
        self.details_input.setMinimumHeight(120)
        self.details_input.setPlainText(str(self._event.get("details", "")))

        form = QFormLayout()
        form.addRow("Title:", self.title_input)
        form.addRow("Category:", self.category_input)
        form.addRow("Month:", self.month_combo)
        form.addRow("Day:", self.day_input)
        form.addRow("Year:", self.year_input)
        form.addRow("Time:", time_widget)
        form.addRow("Repeats:", self.recurrence_combo)
        form.addRow("Duration (days):", self.duration_input)
        form.addRow("Short summary:", self.description_input)
        form.addRow("Full details:", self.details_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(
            QLabel(
                "This is a private player reminder. It is saved in the Calendar "
                "but is not treated as game canon or sent to the A.I."
            )
        )
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.resize(620, 600)
        self._sync_time_inputs()

    def _sync_time_inputs(self) -> None:
        """Disables exact time controls for an all-day event."""

        enabled = not self.all_day_checkbox.isChecked()
        self.hour_input.setEnabled(enabled)
        self.minute_input.setEnabled(enabled)

    def accept(self) -> None:
        """Requires a visible title before saving."""

        if not self.title_input.text().strip():
            QMessageBox.warning(self, "Calendar Event", "Enter a title for this event.")
            return
        super().accept()

    def build_event(self) -> dict[str, Any]:
        """Returns a normalized player-only event payload."""

        event_id = str(self._event.get("event_id", "")).strip()
        if not event_id:
            event_id = f"player_{uuid.uuid4().hex}"
        time_minutes = -1
        if not self.all_day_checkbox.isChecked():
            time_minutes = self.hour_input.value() * 60 + self.minute_input.value()
        return {
            "event_id": event_id,
            "title": self.title_input.text().strip(),
            "description": self.description_input.toPlainText().strip(),
            "category": self.category_input.text().strip() or "Reminder",
            "month": int(self.month_combo.currentData() or 1),
            "day": self.day_input.value(),
            "duration_days": self.duration_input.value(),
            "recurrence": str(self.recurrence_combo.currentData() or "none"),
            "year": self.year_input.value(),
            "time_of_day_minutes": time_minutes,
            "importance": "",
            "details": self.details_input.toPlainText().strip(),
            "origin": "player",
        }


class CalendarDayEventsDialog(QDialog):
    """Detailed day view with safe editing for player-authored events only."""

    def __init__(
        self,
        *,
        repository: SaveRepository,
        events: list[dict[str, Any]],
        calendar_settings: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calendar Events")
        self.repository = repository
        self.events = [dict(event) for event in events]
        self.calendar_settings = dict(calendar_settings)
        self.changed = False

        self.event_list = QListWidget()
        self.event_list.currentItemChanged.connect(self._show_selected_event)
        self.details_output = QTextEdit()
        self.details_output.setReadOnly(True)

        self.edit_button = QPushButton("Edit Personal Event")
        self.edit_button.clicked.connect(self._edit_selected_event)
        self.delete_button = QPushButton("Delete Personal Event")
        self.delete_button.clicked.connect(self._delete_selected_event)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        actions = QHBoxLayout()
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        actions.addWidget(close_button)

        layout = QVBoxLayout()
        layout.addWidget(self.event_list)
        layout.addWidget(self.details_output, 1)
        layout.addLayout(actions)
        self.setLayout(layout)
        self.resize(680, 500)
        self._reload_events()

    def _reload_events(self, selected_event_id: str = "") -> None:
        """Reloads event choices after a personal event changes."""

        self.event_list.clear()
        selected_row = 0
        for row, event in enumerate(self.events):
            time_label = _calendar_event_time_label(event, self.calendar_settings)
            title = str(event.get("title", "Event"))
            label = f"{time_label} — {title}" if time_label else title
            if str(event.get("origin", "game")) == "player":
                label += "  [Personal]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, event)
            self.event_list.addItem(item)
            if str(event.get("event_id", "")) == selected_event_id:
                selected_row = row
        if self.event_list.count():
            self.event_list.setCurrentRow(selected_row)
        else:
            self.details_output.clear()
            self.edit_button.setEnabled(False)
            self.delete_button.setEnabled(False)

    def _selected_event(self) -> dict[str, Any] | None:
        """Returns the event attached to the selected list row."""

        item = self.event_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return dict(value) if isinstance(value, dict) else None

    def _show_selected_event(self) -> None:
        """Shows all player-facing details for the selected event."""

        event = self._selected_event()
        if event is None:
            self.details_output.clear()
            return
        personal = str(event.get("origin", "game")) == "player"
        self.edit_button.setEnabled(personal)
        self.delete_button.setEnabled(personal)
        recurrence = (
            "Repeats every year"
            if event.get("recurrence") == "yearly"
            else f"Year {event.get('year', 1)}"
        )
        time_label = _calendar_event_time_label(event, self.calendar_settings) or "All day"
        sections = [
            str(event.get("title", "Event")),
            (
                f"{event.get('category', 'Event')} · Month {event.get('month', 1)}, "
                f"Day {event.get('day', 1)} · {time_label} · {recurrence}"
            ),
        ]
        description = str(event.get("description", "")).strip()
        details = str(event.get("details", "")).strip()
        if description:
            sections.append(description)
        if details and details != description:
            sections.append(details)
        self.details_output.setPlainText("\n\n".join(sections))

    def _edit_selected_event(self) -> None:
        """Edits only a player-authored event."""

        event = self._selected_event()
        if event is None or str(event.get("origin", "game")) != "player":
            return
        dialog = CalendarPlayerEventDialog(
            calendar_settings=self.calendar_settings,
            event=event,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = self.repository.upsert_calendar_event(dialog.build_event())
        if updated is None:
            return
        event_id = str(updated["event_id"])
        self.events = [
            updated if str(candidate.get("event_id", "")) == event_id else candidate
            for candidate in self.events
        ]
        self.changed = True
        self._reload_events(event_id)

    def _delete_selected_event(self) -> None:
        """Deletes only a player-authored event after confirmation."""

        event = self._selected_event()
        if event is None or str(event.get("origin", "game")) != "player":
            return
        if QMessageBox.question(
            self,
            "Delete Personal Event",
            f"Delete '{event.get('title', 'this event')}'?",
        ) != QMessageBox.StandardButton.Yes:
            return
        event_id = str(event.get("event_id", ""))
        if not self.repository.delete_calendar_event(event_id):
            return
        self.events = [
            candidate
            for candidate in self.events
            if str(candidate.get("event_id", "")) != event_id
        ]
        self.changed = True
        self._reload_events()


class CalendarSettingsDialog(QDialog):
    """Dialog for editing save-specific calendar settings."""

    def __init__(
        self,
        settings: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("Calendar Settings")
        calendar_settings = dict(settings or DEFAULT_CALENDAR_SETTINGS)

        self.days_per_week_input = _NoWheelSpinBox()
        self.days_per_week_input.setRange(1, 14)
        self.days_per_week_input.setValue(int(calendar_settings["days_per_week"]))

        self.weeks_per_month_input = _NoWheelSpinBox()
        self.weeks_per_month_input.setRange(1, 12)
        self.weeks_per_month_input.setValue(int(calendar_settings["weeks_per_month"]))

        self.months_per_year_input = _NoWheelSpinBox()
        self.months_per_year_input.setRange(1, 24)
        self.months_per_year_input.setValue(int(calendar_settings["months_per_year"]))

        self.seasons_per_year_input = _NoWheelSpinBox()
        self.seasons_per_year_input.setRange(1, 12)
        self.seasons_per_year_input.setValue(int(calendar_settings["seasons_per_year"]))

        self.day_names_input = QLineEdit(
            ", ".join(str(name) for name in calendar_settings["day_names"])
        )
        self.month_names_input = QLineEdit(
            ", ".join(str(name) for name in calendar_settings["month_names"])
        )
        self.season_names_input = QLineEdit(
            ", ".join(str(season["name"]) for season in calendar_settings["seasons"])
        )
        self.season_hints_input = QLineEdit(
            ", ".join(
                str(season["weather_hint"]) for season in calendar_settings["seasons"]
            )
        )

        self.time_display_combo = _NoWheelComboBox()
        self.time_display_combo.addItem("Narrative", "narrative")
        self.time_display_combo.addItem("12-hour", "12_hour")
        self.time_display_combo.addItem("24-hour", "24_hour")
        _set_combo_to_data(
            self.time_display_combo,
            str(calendar_settings.get("time_display", "narrative")),
        )

        form = QFormLayout()
        form.addRow("Days Per Week:", self.days_per_week_input)
        form.addRow("Weeks Per Month:", self.weeks_per_month_input)
        form.addRow("Months Per Year:", self.months_per_year_input)
        form.addRow("Seasons Per Year:", self.seasons_per_year_input)
        form.addRow("Day Names:", self.day_names_input)
        form.addRow("Month Names:", self.month_names_input)
        form.addRow("Season Names:", self.season_names_input)
        form.addRow("Season Weather Hints:", self.season_hints_input)
        form.addRow("Time Display:", self.time_display_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def build_settings(self) -> dict[str, Any]:
        """Builds normalized calendar settings from dialog controls."""

        return {
            "days_per_week": self.days_per_week_input.value(),
            "weeks_per_month": self.weeks_per_month_input.value(),
            "months_per_year": self.months_per_year_input.value(),
            "seasons_per_year": self.seasons_per_year_input.value(),
            "day_names": _split_list(self.day_names_input.text()),
            "month_names": _split_list(self.month_names_input.text()),
            "seasons": _build_season_settings(
                names=_split_list(self.season_names_input.text()),
                hints=_split_list(self.season_hints_input.text()),
                count=self.seasons_per_year_input.value(),
            ),
            "time_display": self.time_display_combo.currentData() or "narrative",
        }


class InventoryItemDetailsDialog(QDialog):
    """Application-modal view of one inventory item's player-facing details."""

    def __init__(
        self,
        *,
        item: dict[str, Any],
        catalog_entry: dict[str, Any] | None,
        denominations: list[dict[str, Any]],
        image_path: Path | None = None,
        show_structured_details: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        quantity = max(0, _safe_int(item.get("quantity", 0), 0))
        quantity_unit = str(item.get("quantity_unit", "each") or "each")
        name = _inventory_item_display_name(
            item.get("name", "Unnamed Item"),
            quantity,
            quantity_unit,
        )
        self.setWindowTitle(name)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(520, 520 if show_structured_details else 440)
        self.setSizeGripEnabled(True)

        title = QLabel(name)
        title.setObjectName("inventoryItemDetailTitle")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")

        storage_location = _inventory_location_label(
            item.get("storage_location", "actively_carried")
        )
        summary = QFormLayout()
        summary.addRow("Category:", _selectable_label(item.get("category", "")))
        summary.addRow(
            "Quantity:",
            _selectable_label(_inventory_quantity_display(quantity, quantity_unit)),
        )
        summary.addRow("Stored at:", _selectable_label(storage_location))
        summary.addRow(
            "Value:",
            _selectable_label(
                format_currency_amount(
                    max(0, _safe_int(item.get("value_base_units", 0), 0)),
                    denominations,
                )
            ),
        )
        if any(item_is_valid_for_slot(item, slot) for slot in EQUIPMENT_SLOTS):
            summary.addRow(
                "Equipped:",
                _selectable_label("Yes" if item.get("equipped") else "No"),
            )

        description = QTextEdit()
        description.setReadOnly(True)
        description.setPlainText(str(item.get("description", "")) or "No description.")
        description.setMaximumHeight(100)

        metadata_view: QPlainTextEdit | None = None
        catalog_form: QFormLayout | None = None
        if show_structured_details:
            metadata = dict((catalog_entry or {}).get("metadata", {}))
            inventory_metadata = item.get("metadata", {})
            if isinstance(inventory_metadata, dict):
                metadata.update(inventory_metadata)
            metadata_view = QPlainTextEdit()
            metadata_view.setObjectName("inventoryStructuredDetails")
            metadata_view.setReadOnly(True)
            metadata_view.setPlainText(
                json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True)
                if metadata
                else "No additional item details."
            )

        if show_structured_details and catalog_entry is not None:
            catalog_form = QFormLayout()
            catalog_form.addRow(
                "First created:",
                _selectable_label(catalog_entry.get("first_seen_at", "")),
            )
            catalog_form.addRow(
                "Last updated:",
                _selectable_label(catalog_entry.get("updated_at", "")),
            )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(title)
        generated_image = QLabel()
        generated_image.setObjectName("inventoryGeneratedImage")
        if _set_generated_image(
            generated_image,
            image_path,
            maximum_width=384,
            maximum_height=300,
            accessible_name=f"Generated image of {name}",
        ):
            layout.addWidget(generated_image, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(summary)
        layout.addWidget(QLabel("Description"))
        layout.addWidget(description)
        if metadata_view is not None:
            metadata_label = QLabel("All Structured Details")
            metadata_label.setObjectName("inventoryStructuredDetailsLabel")
            layout.addWidget(metadata_label)
            layout.addWidget(metadata_view, 1)
        if catalog_form is not None:
            layout.addLayout(catalog_form)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.resize(
            560,
            580 if show_structured_details else 500,
        )


class NpcDetailsDialog(QDialog):
    """Resizable, player-facing detail view for one known NPC."""

    def __init__(
        self,
        *,
        npc: dict[str, Any],
        image_path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        display_name = str(npc.get("display_name", "Unknown NPC") or "Unknown NPC")
        self.setWindowTitle(display_name)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(520, 480)
        self.setSizeGripEnabled(True)

        title = QLabel(display_name)
        title.setObjectName("npcDetailTitle")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")

        summary = QFormLayout()
        summary.addRow("Name:", _selectable_label(display_name))
        summary.addRow(
            "Location:",
            _selectable_label(npc.get("location", "") or "Not specified"),
        )
        summary.addRow(
            "Gender identity:",
            _selectable_label(npc.get("gender_identity", "") or "Not specified"),
        )
        summary.addRow(
            "Age:",
            _selectable_label(npc.get("age", "") or "Not specified"),
        )
        summary.addRow(
            "Species:",
            _selectable_label(npc.get("species", "") or "Not specified"),
        )

        description = QTextEdit()
        description.setObjectName("npcDetailDescription")
        description.setReadOnly(True)
        description.setPlainText(
            str(npc.get("description", "") or "No description recorded.")
        )
        description.setMinimumHeight(100)

        notes = QTextEdit()
        notes.setObjectName("npcDetailNotes")
        notes.setReadOnly(True)
        notes.setPlainText(str(npc.get("notes", "") or "No notes recorded."))
        notes.setMinimumHeight(100)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(title)
        generated_image = QLabel()
        generated_image.setObjectName("npcGeneratedDetailImage")
        if _set_generated_image(
            generated_image,
            image_path,
            maximum_width=384,
            maximum_height=320,
            accessible_name=f"Generated portrait of {display_name}",
        ):
            layout.addWidget(generated_image, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(summary)
        layout.addWidget(QLabel("Description"))
        layout.addWidget(description, 1)
        layout.addWidget(QLabel("Notes"))
        layout.addWidget(notes, 1)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.resize(560, 620)
