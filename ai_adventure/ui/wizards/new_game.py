from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.dialogues import *  # noqa: F401,F403


class _GeminiApiKeyWizardPage(QWizardPage):
    """Collects consent and the local Google Gemini API key."""

    def __init__(
        self,
        api_key_path: Path,
        terms_acceptance_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.api_key_path = api_key_path.expanduser().resolve()
        self.terms_acceptance_path = terms_acceptance_path.expanduser().resolve()
        self.setTitle("Google Gemini API Key")
        self.setSubTitle(
            "Enter the key this device will use for Gemini-powered adventures."
        )

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("Paste your Google Gemini API Key")
        self.api_key_input.setText(read_api_key(self.api_key_path))
        self.api_key_input.setToolTip(
            "The key is kept locally and is never included in save files or templates."
        )

        self.terms_text = QTextEdit()
        self.terms_text.setReadOnly(True)
        self.terms_text.setMinimumHeight(250)
        self.terms_text.setMaximumHeight(360)
        self.terms_text.setPlainText(self._terms_text())

        self.help_link = QLabel(
            '<a href="api-key-help">What is a Google Gemini API Key?</a>'
        )
        self.help_link.setTextFormat(Qt.TextFormat.RichText)
        self.help_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self.help_link.setOpenExternalLinks(False)
        self.help_link.setStyleSheet("color: #2563eb; font-size: 12px;")
        self.help_link.linkActivated.connect(self._show_api_key_help)

        self.terms_checkbox = QCheckBox(
            "I have read and agree to the Terms of Use."
        )
        self.security_checkbox = QCheckBox(
            "I understand that providing and storing my Google Gemini API Key, "
            "even locally on my own device, is insecure."
        )

        self.api_key_input.textChanged.connect(self.completeChanged)
        self.terms_checkbox.toggled.connect(self.completeChanged)
        self.security_checkbox.toggled.connect(self.completeChanged)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Google Gemini API Key:"))
        layout.addWidget(self.api_key_input)
        layout.addSpacing(8)
        layout.addWidget(QLabel("Terms of Use and Arbitration Notice:"))
        layout.addWidget(self.terms_text)
        layout.addWidget(self.help_link, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.terms_checkbox)
        layout.addWidget(self.security_checkbox)
        self.setLayout(layout)

    def isComplete(self) -> bool:
        """Requires a key and both acknowledgements before advancing."""

        return bool(self.api_key_input.text().strip()) and all(
            checkbox.isChecked()
            for checkbox in (self.terms_checkbox, self.security_checkbox)
        )

    def validatePage(self) -> bool:
        """Stores the key locally when the user advances past this page."""

        if not self.api_key_input.text().strip():
            QMessageBox.warning(
                self,
                "Missing Google Gemini API Key",
                "Enter a Google Gemini API key before continuing.",
            )
            return False

        if not self.terms_checkbox.isChecked() or not self.security_checkbox.isChecked():
            QMessageBox.warning(
                self,
                "Acknowledgements Required",
                "Read and accept both acknowledgements before continuing.",
            )
            return False

        try:
            write_api_key(self.api_key_path, self.api_key_input.text())
            record_terms_acceptance(
                self.terms_acceptance_path,
                self.terms_text.toPlainText(),
            )
        except (OSError, ValueError) as error:
            LOGGER.exception("Failed to store the local Gemini API key.")
            QMessageBox.critical(
                self,
                "Could Not Store API Key",
                f"The API key could not be stored locally:\n{error}",
            )
            return False

        return True

    def _terms_text(self) -> str:
        """Returns the visible local-storage terms and arbitration notice."""

        return (
            "AI Adventure Local API-Key Terms of Use and Arbitration Notice\n\n"
            "1. Local storage. By entering a Google Gemini API Key, you authorize "
            "AI Adventure to use it to authenticate requests to Google's Gemini "
            "service. AI Adventure will store the key only in the following local "
            "file on this device:\n\n"
            f"{self.api_key_path}\n\n"
            "On Windows, the key is encrypted at rest with Windows Data Protection "
            "API (DPAPI), tied to the current Windows user. AI Adventure does not "
            "hard-code or store a separate decryption key. The key is decrypted only "
            "in memory when a Gemini request needs it.\n\n"
            "The key is not written to save files, new-game templates, logs, story "
            "history, prompts, or any AI Adventure cloud service. AI Adventure has "
            "no remote key-storage service and will never upload this key for storage "
            "in a cloud, database, synchronization service, or other remote location.\n\n"
            "After both acknowledgements are accepted, AI Adventure also writes a "
            "small local receipt containing the UTC timestamp, terms version, and a "
            "fingerprint of this text. The receipt never contains the API key and is "
            "not uploaded or included in saves.\n\n"
            "2. Requests to Google. The original key must be available locally because "
            "it is sent through the Google Gemini SDK as the credential for a request. "
            "A one-way hash or Caesar/substitution cipher is not used: a hash cannot "
            "authenticate with Gemini, and a simple substitution would only disguise "
            "the key rather than protect it. Google "
            "may process the credential under Google's own terms and policies; this "
            "notice describes AI Adventure's storage behavior, not Google's systems.\n\n"
            "3. Security. Anyone or any software with access to this device or the "
            "local file may be able to read or misuse the key. You are responsible for "
            "protecting this device, revoking exposed keys, and following Google's "
            "key-management guidance. Do not share the key in screenshots, messages, "
            "save files, or bug reports.\n\n"
            "4. Arbitration. To the maximum extent permitted by applicable law, any "
            "dispute between you and the publisher of AI Adventure concerning this "
            "local API-key handling notice will be resolved individually through "
            "binding arbitration rather than a class action or class-wide proceeding. "
            "This clause does not change Google's separate terms, does not authorize "
            "remote storage, and does not limit rights that cannot legally be waived. "
            "This product notice is not a substitute for jurisdiction-specific legal "
            "review."
        )

    def _show_api_key_help(self, _link: str) -> None:
        """Shows plain-language instructions for obtaining a Gemini API key."""

        dialog = QDialog(self)
        dialog.setWindowTitle("What is a Google Gemini API Key?")
        dialog.resize(600, 440)

        explanation = QTextEdit()
        explanation.setReadOnly(True)
        explanation.setPlainText(
            "An API key is a secret-looking string that identifies your Google "
            "project when an application asks Google Gemini to do work. It is similar "
            "to a password for a program: keep it private, use only keys you created, "
            "and revoke it if you think it was exposed.\n\n"
            "To get one, sign in to Google AI Studio, open the API-key area, choose "
            "Create API key, and select or create a Google Cloud project when Google "
            "asks you to do so. Copy the key once it is shown.\n\n"
            "In AI Adventure, paste that key into the field on the previous page. The "
            "wizard will save it only to the local path shown in the Terms of Use. "
            "You do not need to edit a .env file. If the key is ever exposed, revoke "
            "it in Google AI Studio and create a replacement.\n\n"
            "Google's screens and account requirements can change, so follow the "
            "current instructions shown by Google when creating or managing your key."
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)

        layout = QVBoxLayout()
        layout.addWidget(explanation)
        layout.addWidget(buttons)
        dialog.setLayout(layout)
        dialog.exec()


class NewGameWizard(QWizard):
    """Multi-step new-game setup flow."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        template_setup: dict[str, Any] | None = None,
        tts_enabled: bool = True,
        audio_defaults: dict[str, Any] | None = None,
        voice_options: dict[str, str] | None = None,
        on_sample_voice: SampleVoiceCallback | None = None,
        on_tts_settings_saved: Callable[[dict[str, Any]], None] | None = None,
        custom_voice_storage_path: Path | str | None = None,
        api_key_path: Path | str | None = None,
        terms_acceptance_path: Path | str | None = None,
        sound_manager: SoundManagerProtocol | None = None,
    ) -> None:
        super().__init__(parent)

        self.tts_enabled = bool(tts_enabled)
        self.voice_options = voice_options or available_narrator_voices()
        self.on_sample_voice = on_sample_voice
        self.on_tts_settings_saved = on_tts_settings_saved
        self.custom_voice_storage_path = custom_voice_storage_path
        self.sound_manager = sound_manager
        self.api_key_path = (
            Path(api_key_path).expanduser().resolve()
            if api_key_path is not None
            else AppPaths.create().gemini_api_key_path
        )
        self.terms_acceptance_path = (
            Path(terms_acceptance_path).expanduser().resolve()
            if terms_acceptance_path is not None
            else self.api_key_path.parent / "gemini_api_key_terms_acceptance.json"
        )
        self.audio_defaults = normalize_app_settings(
            {"audio": audio_defaults or {}},
            tts_enabled=self.tts_enabled,
        )["audio"]
        self.narrator_enabled_checkbox: QCheckBox | None = None
        self.tts_volume_slider: QSlider | None = None
        self.tts_volume_label: QLabel | None = None
        self.tts_voice_combo: QComboBox | None = None
        self.sample_voice_button: QPushButton | None = None
        self.tts_speed_slider: QSlider | None = None
        self.tts_settings_widget: TTSSettingsWidget | None = None
        self._legacy_currency_description = ""
        self._pronunciation_map: PronunciationMap = {}
        self._starting_location_row_id_counter = 0
        self._custom_calendar_settings = dict(GREGORIAN_CALENDAR_SETTINGS)
        default_modes = normalize_ai_mode_preferences({})
        self._new_game_ai_settings: dict[str, Any] = {
            "text_model": default_modes["text_model"],
            "model_intelligence": default_modes["model_intelligence"],
            "model_tone": default_modes["model_tone"],
            "response_length": default_modes["response_length"],
            "allowed_content_categories": default_modes[
                "allowed_content_categories"
            ],
            "additional_context": "",
        }
        self._new_game_image_settings = normalize_image_preferences({})

        self.setWindowTitle("New Game Wizard")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMaximumSize(16777215, 16777215)
        self.setSizeGripEnabled(True)
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(960, 700)
        else:
            available = screen.availableGeometry()
            self.resize(
                min(1100, max(780, int(available.width() * 0.82))),
                min(800, max(620, int(available.height() * 0.82))),
            )
        self._apply_theme()

        self._build_adventure_page()
        self._build_ai_settings_page()
        self._build_api_key_page()
        self._build_starting_locations_page()
        self._build_starting_task_page()
        self._build_starting_npcs_page()
        self._build_starting_party_page()
        self._build_character_page()
        self._build_skills_page()
        self._build_magic_page()
        self._build_combat_page()
        self._build_inventory_currency_page()
        self._build_audio_page()
        if self.tts_enabled:
            self._build_tts_page()
        self._build_calendar_page()

        self.currentIdChanged.connect(self._schedule_page_heading_style)
        self._style_current_page_headings()
        self._bind_music_preview_stop_buttons()

        if template_setup is not None:
            self.load_setup(template_setup)

    def nextId(self) -> int:
        """Skips Party dynamically when the setup explicitly has no NPCs."""

        if (
            self.currentId() == getattr(self, "starting_npcs_page_id", -1)
            and hasattr(self, "no_starting_npcs_checkbox")
            and self.no_starting_npcs_checkbox.isChecked()
        ):
            character_page_id = getattr(self, "character_page_id", -1)
            if character_page_id >= 0:
                return character_page_id
        return super().nextId()

    def _apply_theme(self) -> None:
        """Applies a cohesive local theme to the new-game wizard."""

        self.setObjectName("newGameWizard")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        use_dark_theme = _application_uses_dark_theme()
        colors = (
            {
                "window": "#202124",
                "window_text": "#f1f3f4",
                "base": "#121416",
                "alternate_base": "#2a2d30",
                "border": "#5b6268",
                "muted_border": "#4b5258",
                "placeholder": "#a9b0b6",
                "button": "#303437",
                "button_hover": "#3c4247",
                "button_pressed": "#262a2d",
                "button_disabled": "#25282b",
                "disabled_text": "#8b949e",
                "accent": "#4c8fcb",
                "accent_dark": "#2f6fb0",
                "drop_down": "#25292d",
                "group": "#25282b",
                "grid": "#3d444b",
                "selection": "#2f6fb0",
                "selection_text": "#ffffff",
            }
            if use_dark_theme
            else {
                "window": "#f5f7fb",
                "window_text": "#111827",
                "base": "#ffffff",
                "alternate_base": "#eef2f7",
                "border": "#64748b",
                "muted_border": "#94a3b8",
                "placeholder": "#4b5563",
                "button": "#e5e7eb",
                "button_hover": "#dbe4ee",
                "button_pressed": "#cbd5e1",
                "button_disabled": "#edf1f5",
                "disabled_text": "#6b7280",
                "accent": "#2563eb",
                "accent_dark": "#1d4ed8",
                "drop_down": "#e2e8f0",
                "group": "#eef2f7",
                "grid": "#cbd5e1",
                "selection": "#1d4ed8",
                "selection_text": "#ffffff",
            }
        )

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["window_text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["base"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["alternate_base"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["window_text"]))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["placeholder"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(colors["button"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["window_text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["selection"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["selection_text"]))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["button"]))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["window_text"]))
        self.setPalette(palette)

        stylesheet = """
            QWizard#newGameWizard {
                background-color: {colors["window"]};
                color: {colors["window_text"]};
            }

            QWizard#newGameWizard QWidget {
                background-color: {colors["window"]};
                color: {colors["window_text"]};
                font-size: 15px;
            }

            QWizard#newGameWizard QWizardPage,
            QWizard#newGameWizard QFrame {
                background-color: {colors["window"]};
                color: {colors["window_text"]};
            }

            QWizard#newGameWizard QLabel {
                background-color: transparent;
                color: {colors["window_text"]};
                font-size: 15px;
            }

            QWizard#newGameWizard QLabel#newGameWizardPageTitle {
                font-size: 26px;
                font-weight: 700;
                padding: 8px 0 2px 0;
            }

            QWizard#newGameWizard QLabel#newGameWizardPageSubtitle {
                color: {colors["placeholder"]};
                font-size: 17px;
                padding: 0 0 10px 0;
            }

            QWizard#newGameWizard QLineEdit,
            QWizard#newGameWizard QTextEdit,
            QWizard#newGameWizard QComboBox,
            QWizard#newGameWizard QSpinBox {
                background-color: {colors["base"]};
                border: 1px solid {colors["border"]};
                border-radius: 5px;
                color: {colors["window_text"]};
                font-size: 15px;
                padding: 6px;
                selection-background-color: {colors["selection"]};
                selection-color: {colors["selection_text"]};
            }

            QWizard#newGameWizard QComboBox {
                padding-right: 40px;
            }

            QWizard#newGameWizard QSpinBox {
                padding-right: 36px;
            }

            QWizard#newGameWizard QLineEdit::placeholder,
            QWizard#newGameWizard QTextEdit::placeholder {
                color: {colors["placeholder"]};
            }

            QWizard#newGameWizard QTextEdit {
                padding: 8px;
            }

            QWizard#newGameWizard QCheckBox {
                background-color: transparent;
                color: {colors["window_text"]};
                font-size: 15px;
                spacing: 8px;
            }

            QWizard#newGameWizard QCheckBox::indicator {
                background-color: {colors["base"]};
                border: 1px solid {colors["border"]};
                border-radius: 3px;
                height: 16px;
                width: 16px;
            }

            QWizard#newGameWizard QCheckBox::indicator:hover {
                border-color: {colors["accent"]};
            }

            QWizard#newGameWizard QCheckBox::indicator:checked {
                background-color: {colors["accent"]};
                border-color: {colors["accent_dark"]};
            }

            QWizard#newGameWizard QGroupBox {
                background-color: {colors["group"]};
                border: 1px solid {colors["muted_border"]};
                border-radius: 6px;
                color: {colors["window_text"]};
                font-weight: 600;
                margin-top: 12px;
                padding: 10px;
            }

            QWizard#newGameWizard QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                background-color: {colors["group"]};
                color: {colors["window_text"]};
                padding: 0 4px;
            }

            QWizard#newGameWizard QScrollArea {
                background-color: transparent;
                border: 0;
            }

            QWizard#newGameWizard QScrollArea > QWidget > QWidget {
                background-color: {colors["window"]};
            }

            QWizard#newGameWizard QSlider::groove:horizontal {
                background-color: {colors["alternate_base"]};
                border: 1px solid {colors["muted_border"]};
                border-radius: 4px;
                height: 8px;
            }

            QWizard#newGameWizard QSlider::handle:horizontal {
                background-color: {colors["accent"]};
                border: 1px solid {colors["accent_dark"]};
                border-radius: 7px;
                margin: -4px 0;
                width: 14px;
            }

            QWizard#newGameWizard QLineEdit:focus,
            QWizard#newGameWizard QTextEdit:focus,
            QWizard#newGameWizard QComboBox:focus,
            QWizard#newGameWizard QSpinBox:focus {
                border-color: {colors["accent"]};
            }

            QWizard#newGameWizard QComboBox::drop-down {
                background-color: {colors["drop_down"]};
                border: 0;
                border-left: 1px solid {colors["muted_border"]};
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 32px;
            }

            QWizard#newGameWizard QComboBox::down-arrow {
                image: none;
                width: 0;
                height: 0;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid {colors["window_text"]};
            }

            QWizard#newGameWizard QSpinBox::up-button,
            QWizard#newGameWizard QSpinBox::down-button {
                background-color: {colors["drop_down"]};
                border-left: 1px solid {colors["muted_border"]};
                subcontrol-origin: border;
                width: 28px;
            }

            QWizard#newGameWizard QSpinBox::up-button {
                border-top-right-radius: 4px;
                border-bottom: 1px solid {colors["muted_border"]};
                subcontrol-position: top right;
                height: 14px;
            }

            QWizard#newGameWizard QSpinBox::down-button {
                border-bottom-right-radius: 4px;
                subcontrol-position: bottom right;
                height: 14px;
            }

            QWizard#newGameWizard QComboBox QAbstractItemView {
                background-color: {colors["base"]};
                border: 1px solid {colors["border"]};
                color: {colors["window_text"]};
                selection-background-color: {colors["selection"]};
                selection-color: {colors["selection_text"]};
            }

            QWizard#newGameWizard QTableWidget {
                background-color: {colors["base"]};
                alternate-background-color: {colors["alternate_base"]};
                border: 1px solid {colors["border"]};
                border-radius: 5px;
                color: {colors["window_text"]};
                gridline-color: {colors["grid"]};
                selection-background-color: {colors["selection"]};
                selection-color: {colors["selection_text"]};
            }

            QWizard#newGameWizard QHeaderView::section {
                background-color: {colors["drop_down"]};
                border: 0;
                border-right: 1px solid {colors["grid"]};
                color: {colors["window_text"]};
                font-size: 14px;
                font-weight: 600;
                padding: 9px 7px;
            }

            QWizard#newGameWizard QPushButton {
                background-color: {colors["button"]};
                border: 1px solid {colors["border"]};
                border-radius: 5px;
                color: {colors["window_text"]};
                font-size: 14px;
                min-width: 76px;
                padding: 7px 14px;
            }

            QWizard#newGameWizard QPushButton#rowRemoveButton {
                min-width: 66px;
                padding: 5px 10px;
            }

            QWizard#newGameWizard QPushButton:hover {
                background-color: {colors["button_hover"]};
                border-color: {colors["muted_border"]};
            }

            QWizard#newGameWizard QPushButton:pressed {
                background-color: {colors["button_pressed"]};
            }

            QWizard#newGameWizard QPushButton#starterInventoryModeButton:checked {
                background-color: {colors["accent"]};
                border-color: {colors["accent_dark"]};
                color: {colors["selection_text"]};
            }

            QWizard#newGameWizard QPushButton:default {
                background-color: {colors["accent"]};
                border-color: {colors["accent_dark"]};
                color: {colors["selection_text"]};
            }

            QWizard#newGameWizard QPushButton:disabled {
                background-color: {colors["button_disabled"]};
                border-color: {colors["grid"]};
                color: {colors["disabled_text"]};
            }
            """

        for color_name, color_value in colors.items():
            stylesheet = stylesheet.replace(f'{{colors["{color_name}"]}}', color_value)

        self.setStyleSheet(stylesheet)
        self._wizard_subtitle_color = colors["placeholder"]

    def _schedule_page_heading_style(self, _page_id: int) -> None:
        """Restyles Qt's shared title labels after the page text changes."""

        QTimer.singleShot(0, self._style_current_page_headings)

    def _style_current_page_headings(self) -> None:
        """Makes the current page title and subtitle visibly distinct."""

        page = self.currentPage()
        if page is None:
            return

        title = page.title()
        subtitle = page.subTitle()
        title_styled = False
        subtitle_styled = False
        for label in self.findChildren(QLabel):
            if not title_styled and label.text() == title:
                label.setObjectName("newGameWizardPageTitle")
                label.setStyleSheet(
                    "font-size: 26px; font-weight: 700; padding: 8px 0 2px 0;"
                )
                label.ensurePolished()
                heading_width = max(320, self.width() - 100)
                title_height = label.heightForWidth(heading_width)
                if title_height <= 0:
                    title_height = label.fontMetrics().lineSpacing() + 10
                label.setSizePolicy(
                    QSizePolicy.Policy.Preferred,
                    QSizePolicy.Policy.Fixed,
                )
                label.setFixedHeight(title_height)
                label.updateGeometry()
                title_styled = True
                continue
            if not subtitle_styled and label.text() == subtitle:
                label.setObjectName("newGameWizardPageSubtitle")
                label.setStyleSheet(
                    f"color: {self._wizard_subtitle_color}; font-size: 17px; "
                    "padding: 0 0 10px 0;"
                )
                label.ensurePolished()
                heading_width = max(320, self.width() - 100)
                subtitle_height = label.heightForWidth(heading_width)
                if subtitle_height <= 0:
                    subtitle_height = label.fontMetrics().lineSpacing() + 10
                label.setSizePolicy(
                    QSizePolicy.Policy.Preferred,
                    QSizePolicy.Policy.Fixed,
                )
                label.setFixedHeight(subtitle_height)
                label.updateGeometry()
                subtitle_styled = True

    def build_setup(self) -> dict[str, Any]:
        """Builds a normalized setup dictionary from wizard fields."""

        self._new_game_ai_settings = self._new_game_ai_settings_from_controls()
        self._new_game_image_settings = self._new_game_image_settings_from_controls()
        calendar_type = self.calendar_type_combo.currentData() or "gregorian"
        calendar_settings = self._calendar_settings_for_setup(str(calendar_type))
        starting_calendar: dict[str, Any] = {}
        season_name = self.calendar_start_season_input.text().strip()
        if season_name:
            starting_calendar["season_name"] = season_name
        if self.calendar_start_year_input.value() > 0:
            starting_calendar["year"] = self.calendar_start_year_input.value()
        if self.calendar_start_month_input.value() > 0:
            starting_calendar["month_number"] = self.calendar_start_month_input.value()
        if self.calendar_start_day_input.value() > 0:
            starting_calendar["day_of_month"] = self.calendar_start_day_input.value()
        if self.calendar_start_time_checkbox.isChecked():
            start_time = self.calendar_start_time_input.time()
            starting_calendar["time_of_day_minutes"] = (
                start_time.hour() * 60 + start_time.minute()
            )
        economy_examples = self._economy_examples_from_table()

        skills = [
            {
                "name": skill_input.text(),
                "description": description_input.text(),
                "level": level,
                "requires_ai_invention": (
                    not skill_input.text().strip()
                    or not description_input.text().strip()
                ),
            }
            for level, skill_input, description_input in self.skill_inputs
        ]
        selected_start_location = self._selected_starting_location_for_setup()
        setup = {
            "title": self.title_input.text(),
            "character": {
                "name": self.character_name_input.text(),
                "name_pronunciation": self.character_name_pronunciation_input.text(),
                "pronouns": self._character_pronouns_from_controls(),
                "appearance": self.appearance_input.toPlainText(),
                "backstory": self.backstory_input.toPlainText(),
                "notes": self.character_notes_input.toPlainText(),
            },
            "skills": skills,
            "skill_preset": str(self.skill_preset_combo.currentData() or "professional"),
            "skill_level_plan": [level for level, _name, _description in self.skill_inputs],
            "magic": self._magic_setup_from_controls(),
            "combat": self._combat_setup_from_controls(),
            "starter_inventory_mode": self._starter_inventory_mode(),
            "starter_items": self._starter_items_from_table(),
            "starting_npcs": self._starting_npcs_from_table(),
            "no_starting_npcs": self.no_starting_npcs_checkbox.isChecked()
            if hasattr(self, "no_starting_npcs_checkbox")
            else False,
            "starting_party_npc_ids": self._starting_party_npc_ids_from_table(),
            "starting_locations": self._starting_locations_from_table(),
            "starting_task": self._starting_task_from_controls(),
            "calendar": calendar_settings,
            "starting_calendar": starting_calendar,
            "starting_weather": self.calendar_start_weather_input.text().strip(),
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
            "pronunciation_map": dict(self._pronunciation_map),
            "ai_settings": dict(self._new_game_ai_settings),
            "images": dict(self._new_game_image_settings),
            "narration": {
                "tense": self.narration_tense_combo.currentData(),
                "style": self.narration_style_combo.currentData(),
            },
            "currency_denominations": self._currency_denominations_from_table(),
            "starting_wealth": self._starting_wealth_from_controls(),
            "currency_description": (
                describe_economy_examples(economy_examples)
                or self._legacy_currency_description
            ),
            "economy_examples": economy_examples,
            "specified_genre": self.genre_input.text(),
            "game_style": self.game_style_input.toPlainText(),
            "start_location": (
                selected_start_location.get("name") or self.start_location_input.text()
            ),
            "start_location_mode": (
                selected_start_location.get("location_mode")
                or self.start_location_mode_combo.currentData()
                or "suggestion"
            ),
            "opening_scene_request": self.opening_scene_request_input.toPlainText(),
            "world_context": self.world_context_input.toPlainText(),
        }

        return normalize_new_game_setup(setup)

    def load_setup(self, setup: dict[str, Any]) -> None:
        """Populates wizard fields from a reusable setup template."""

        clean_setup = normalize_new_game_setup(setup)
        character = clean_setup["character"]
        calendar = clean_setup["calendar"]
        audio = {
            **clean_setup["audio"],
            "tts_custom_voices": merge_custom_voices(
                clean_setup["audio"].get("tts_custom_voices", []),
                self.audio_defaults.get("tts_custom_voices", []),
            ),
        }
        self._pronunciation_map = normalize_pronunciation_map(
            clean_setup.get("pronunciation_map", {})
        )
        narration = clean_setup["narration"]
        ai_settings = clean_setup["ai_settings"]

        self.title_input.setText(clean_setup["title"])
        self.genre_input.setText(clean_setup["specified_genre"])
        self.game_style_input.setPlainText(clean_setup["game_style"])
        self.start_location_input.setText(clean_setup["start_location"])
        self.opening_scene_request_input.setPlainText(
            clean_setup["opening_scene_request"]
        )
        _set_combo_to_data(
            self.start_location_mode_combo,
            clean_setup["start_location_mode"],
        )
        self.world_context_input.setPlainText(clean_setup["world_context"])
        self.starting_locations_table.setRowCount(0)

        for location in clean_setup["starting_locations"]:
            self._append_starting_location_row(location)

        self._refresh_starting_location_dropdowns()
        self._select_starting_location_combo_by_name(clean_setup["start_location"])
        self._load_starting_task(clean_setup["starting_task"])
        no_starting_npcs = bool(clean_setup.get("no_starting_npcs", False))
        self.starting_npcs_table.setRowCount(0)

        for npc in clean_setup["starting_npcs"]:
            self._append_starting_npc_row(npc)

        self._set_starting_party_npc_ids(
            clean_setup.get("starting_party_npc_ids", [])
        )

        if hasattr(self, "no_starting_npcs_checkbox"):
            self.no_starting_npcs_checkbox.blockSignals(True)
            self.no_starting_npcs_checkbox.setChecked(
                no_starting_npcs and self.starting_npcs_table.rowCount() == 0
            )
            self.no_starting_npcs_checkbox.blockSignals(False)
            self._sync_starting_npcs_controls()

        self._apply_new_game_ai_settings(
            {
                **ai_settings,
                "narration_tense": narration["tense"],
                "narration_style": narration["style"],
            },
            clean_setup["images"],
        )

        self.character_name_input.setText(character["name"])
        self.character_name_pronunciation_input.setText(
            character["name_pronunciation"]
        )
        self._set_character_pronouns(character["pronouns"])
        self.appearance_input.setPlainText(character["appearance"])
        self.backstory_input.setPlainText(character["backstory"])
        self.character_notes_input.setPlainText(character["notes"])
        self._load_magic_setup(clean_setup["magic"])
        self._load_combat_setup(clean_setup["combat"])

        preset_index = self.skill_preset_combo.findData(clean_setup.get("skill_preset", "professional"))
        self.skill_preset_combo.setCurrentIndex(max(0, preset_index))
        if clean_setup.get("skill_preset") == "custom":
            for table in self.skill_tables.values():
                table.setRowCount(0)
            for skill in clean_setup["skills"]:
                self._add_starting_skill_row(
                    _safe_int(skill.get("level"), 1),
                    str(skill.get("name", "")),
                    str(skill.get("description", "")),
                )
        else:
            for index, (_, skill_input, description_input) in enumerate(self.skill_inputs):
                skill = clean_setup["skills"][index] if index < len(clean_setup["skills"]) else {}
                skill_input.setText(str(skill.get("name", "")))
                description_input.setText(str(skill.get("description", "")))

        self.starter_items_table.setRowCount(0)
        self.starter_weapons_table.setRowCount(0)
        self.starter_armor_table.setRowCount(0)
        self.starter_item_suggestions_table.setRowCount(0)
        self.starter_weapon_suggestions_table.setRowCount(0)
        self.starter_armor_suggestions_table.setRowCount(0)

        self._set_starter_inventory_mode(clean_setup["starter_inventory_mode"])

        for item in clean_setup["starter_items"]:
            kind = _starter_item_kind(item)

            if item.get("requires_ai_invention") and item.get("item_request"):
                suggestion_table = {
                    "Weapon": self.starter_weapon_suggestions_table,
                    "Armor": self.starter_armor_suggestions_table,
                }.get(kind, self.starter_item_suggestions_table)
                _append_starter_suggestion_table_row(
                    suggestion_table, kind, str(item.get("item_request", ""))
                )
            elif kind == "Weapon":
                self._append_starter_weapon_row(item)
            elif kind == "Armor":
                self._append_starter_armor_row(item)
            else:
                self._append_starter_item_row(item)

        self.currency_table.setRowCount(0)

        for denomination in clean_setup["currency_denominations"]:
            self._append_currency_row(denomination)

        self._load_starting_wealth(clean_setup["starting_wealth"])

        self.economy_examples_table.setRowCount(0)

        for example in clean_setup["economy_examples"]:
            self._append_economy_example_row(example)

        self._legacy_currency_description = (
            "" if clean_setup["economy_examples"] else clean_setup["currency_description"]
        )

        _set_combo_to_data(
            self.calendar_type_combo,
            _calendar_type_from_settings(calendar),
        )
        self.calendar_generation_guidance_input.setPlainText(
            str(calendar.get("generation_guidance", "") or "")
        )
        starting_calendar = clean_setup.get("starting_calendar", {})
        if not isinstance(starting_calendar, dict):
            starting_calendar = {}
        self.calendar_start_season_input.setText(
            str(starting_calendar.get("season_name", "") or "")
        )
        self.calendar_start_year_input.setValue(
            min(9999, max(0, _safe_int(starting_calendar.get("year"), 0)))
        )
        self.calendar_start_month_input.setValue(
            min(24, max(0, _safe_int(starting_calendar.get("month_number"), 0)))
        )
        self.calendar_start_day_input.setValue(
            min(366, max(0, _safe_int(starting_calendar.get("day_of_month"), 0)))
        )
        raw_start_time = starting_calendar.get("time_of_day_minutes")
        has_start_time = raw_start_time is not None
        start_minutes = min(1439, max(0, _safe_int(raw_start_time, 8 * 60)))
        self.calendar_start_time_input.setTime(
            QTime(start_minutes // 60, start_minutes % 60)
        )
        self.calendar_start_time_checkbox.setChecked(has_start_time)
        self.calendar_start_weather_input.setText(
            str(clean_setup.get("starting_weather", "") or "")
        )
        self._custom_calendar_settings = dict(calendar)
        self._sync_calendar_settings_button()
        self.music_enabled_checkbox.setChecked(bool(audio["music_enabled"]))
        self.music_volume_slider.setValue(int(audio["music_volume"]))
        self.sound_effects_enabled_checkbox.setChecked(
            bool(audio["sound_effects_enabled"])
        )
        self.sound_effects_volume_slider.setValue(int(audio["sound_effects_volume"]))
        self.background_ambience_enabled_checkbox.setChecked(
            bool(audio["background_ambience_enabled"])
        )
        self.background_ambience_volume_slider.setValue(
            int(audio["background_ambience_volume"])
        )

        if self.tts_settings_widget is not None:
            self.tts_settings_widget.load_audio_settings(audio)

    def _apply_new_game_ai_settings(
        self,
        raw_settings: dict[str, Any],
        raw_images: dict[str, Any] | None = None,
    ) -> None:
        """Applies normalized settings to the dedicated wizard page."""

        modes = normalize_ai_mode_preferences(raw_settings)
        images = normalize_image_preferences(raw_images)
        narration = normalize_narration_preferences(
            {
                "tense": raw_settings.get("narration_tense"),
                "style": raw_settings.get("narration_style"),
            }
        )
        self._new_game_ai_settings = {
            "text_model": modes["text_model"],
            "model_intelligence": modes["model_intelligence"],
            "model_tone": modes["model_tone"],
            "response_length": modes["response_length"],
            "allowed_content_categories": modes[
                "allowed_content_categories"
            ],
            "additional_context": str(
                raw_settings.get("additional_context", "")
            ).strip(),
        }
        self._new_game_image_settings = images
        _set_combo_to_data(self.text_model_combo, modes["text_model"])
        self.smarter_ai_checkbox.setChecked(
            modes["model_intelligence"] == "smarter"
        )
        _set_combo_to_data(self.model_tone_combo, modes["model_tone"])
        _set_combo_to_data(self.response_length_combo, modes["response_length"])
        self.model_content_combo.set_selected_categories(
            list(modes["allowed_content_categories"])
        )
        _set_combo_to_data(self.narration_tense_combo, narration["tense"])
        _set_combo_to_data(self.narration_style_combo, narration["style"])
        self.additional_ai_context_input.setPlainText(
            self._new_game_ai_settings["additional_context"]
        )
        self.generated_images_enabled_checkbox.setChecked(images["enabled"])
        _set_combo_to_data(self.image_model_combo, images["model"])
        _set_combo_to_data(self.image_style_combo, images["style"])
        self._refresh_new_game_ai_descriptions()

    def _new_game_ai_settings_from_controls(self) -> dict[str, Any]:
        """Builds the normalized text-model preferences from page controls."""

        modes = normalize_ai_mode_preferences(
            {
                "text_model": self.text_model_combo.currentData(),
                "model_intelligence": (
                    "smarter" if self.smarter_ai_checkbox.isChecked() else "faster"
                ),
                "model_tone": self.model_tone_combo.currentData(),
                "response_length": self.response_length_combo.currentData(),
                "allowed_content_categories": (
                    self.model_content_combo.selected_categories()
                ),
            }
        )
        return {
            "text_model": modes["text_model"],
            "model_intelligence": modes["model_intelligence"],
            "model_tone": modes["model_tone"],
            "response_length": modes["response_length"],
            "allowed_content_categories": modes["allowed_content_categories"],
            "additional_context": self.additional_ai_context_input.toPlainText().strip(),
        }

    def _new_game_image_settings_from_controls(self) -> dict[str, Any]:
        """Builds normalized image-generation preferences from page controls."""

        return normalize_image_preferences(
            {
                "enabled": self.generated_images_enabled_checkbox.isChecked(),
                "model": self.image_model_combo.currentData(),
                "style": self.image_style_combo.currentData(),
            }
        )

    def _refresh_new_game_ai_descriptions(self) -> None:
        """Refreshes model-page descriptions and mode guidance."""

        text_model = text_model_metadata(self.text_model_combo.currentData())
        image_model = image_model_metadata(self.image_model_combo.currentData())
        image_style = image_style_metadata(self.image_style_combo.currentData())
        modes = normalize_ai_mode_preferences(
            {
                "model_tone": self.model_tone_combo.currentData(),
                "response_length": self.response_length_combo.currentData(),
                "allowed_content_categories": (
                    self.model_content_combo.selected_categories()
                ),
            }
        )
        self.text_model_description.setText(str(text_model["description"]))
        self.image_model_description.setText(str(image_model["description"]))
        self.image_style_description.setText(str(image_style["description"]))
        self.model_tone_description.setText(str(modes["model_tone_description"]))
        self.response_length_description.setText(
            str(modes["response_length_description"])
        )
        self.model_content_description.setText(
            "No Restrictions: all configurable Gemini harm categories may appear."
            if not modes["blocked_content_labels"]
            else "Allowed categories: "
            + (", ".join(modes["allowed_content_labels"]) or "None")
            + "."
        )
        enabled = self.generated_images_enabled_checkbox.isChecked()
        self.image_model_combo.setEnabled(enabled)
        self.image_model_description.setEnabled(enabled)
        self.image_style_combo.setEnabled(enabled)
        self.image_style_description.setEnabled(enabled)

    @staticmethod
    def _new_game_ai_choice_field(
        title: str,
        control: QWidget,
        description: QLabel,
    ) -> QWidget:
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 3, 0, 6)
        layout.addWidget(QLabel(title))
        layout.addWidget(control)
        layout.addWidget(description)
        return field

    def _build_adventure_page(self) -> None:
        """Builds the adventure/world setup page."""

        page = QWizardPage()
        page.setTitle("Adventure")
        page.setSubTitle("Name the save and describe the kind of game you want.")

        self.title_input = QLineEdit()
        self.title_input.setText("New Adventure")

        self.game_style_input = QTextEdit()
        self.game_style_input.setPlaceholderText(
            "Tone, realism, pacing, themes, or playstyle preferences..."
        )

        self.genre_input = QLineEdit()
        self.genre_input.setPlaceholderText(
            "Optional: survival, detective mystery, post-apocalyptic, space frontier..."
        )

        self.start_location_input = QLineEdit()
        self.start_location_input.setPlaceholderText(
            "Optional: deserted island, frozen sea, crime scene, ruined store..."
        )
        self.start_location_input.setVisible(False)
        self.start_location_mode_combo = _NoWheelComboBox()
        self.start_location_mode_combo.addItem("Use as suggestion", "suggestion")
        self.start_location_mode_combo.addItem("Use exactly this", "exact")
        self.start_location_mode_combo.setVisible(False)

        self.opening_scene_request_input = QTextEdit()
        self.opening_scene_request_input.setPlaceholderText(
            "Optional: describe the situation, mood, event, or hook you want the opening scene to begin with..."
        )

        self.world_context_input = QTextEdit()
        self.world_context_input.setPlaceholderText(
            "Named locations, factions, guilds, religions, political tensions, tone, themes..."
        )

        layout = QFormLayout()
        _configure_responsive_form(layout)
        layout.addRow("Game Name:", self.title_input)
        layout.addRow("Genre:", self.genre_input)
        layout.addRow("Game Style:", self.game_style_input)
        layout.addRow("World Details:", self.world_context_input)
        page.setLayout(layout)

        self.addPage(page)

    def _build_api_key_page(self) -> None:
        """Builds the local Gemini API-key and consent page."""

        self.api_key_page = _GeminiApiKeyWizardPage(
            self.api_key_path,
            self.terms_acceptance_path,
            self,
        )
        self.addPage(self.api_key_page)

    def _build_starting_locations_page(self) -> None:
        """Builds the requested starting Travel locations page."""

        page = QWizardPage()
        page.setTitle("Locations")
        page.setSubTitle("Add starting locations the player character may know.")

        self.start_location_combo = _NoWheelComboBox()
        self.start_location_combo.addItem("Select from starting locations", "")
        self.start_location_combo.currentIndexChanged.connect(
            lambda _index: self._sync_start_location_from_locations_combo()
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

        add_location_button = QPushButton("Add Location")
        add_location_button.clicked.connect(
            lambda: self._append_starting_location_row({})
        )

        layout = QVBoxLayout()
        form = QFormLayout()
        _configure_responsive_form(form)
        form.addRow("Start Location:", self.start_location_combo)
        layout.addLayout(form)
        _configure_responsive_table(
            self.starting_locations_table,
            stretch_columns={0, 1, 4},
            compact_columns={2, 3, 5},
        )
        layout.addWidget(_button_row(add_location_button))
        layout.addWidget(self.starting_locations_table)
        opening_scene_group = QGroupBox("Opening Scene Request")
        opening_scene_group_layout = QVBoxLayout(opening_scene_group)
        opening_scene_group_layout.addWidget(
            QLabel(
                "Suggest what you would like the first scene to be about at the selected starting location."
            )
        )
        opening_scene_group_layout.addWidget(self.opening_scene_request_input)
        layout.addWidget(opening_scene_group, 1)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        page.setLayout(layout)

        self.addPage(page)

    def _append_starting_location_row(self, location: dict[str, Any]) -> None:
        """Adds one requested starting location row to the wizard table."""

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

        self._refresh_starting_location_dropdowns()

    def _remove_starting_location_row(self, button: QPushButton) -> None:
        """Removes the starting location row containing button."""

        _remove_table_row_by_button(self.starting_locations_table, button)
        self._refresh_starting_location_dropdowns()

    def _starting_locations_from_table(self) -> list[dict[str, Any]]:
        """Reads requested starting location rows from the wizard table."""

        return _starting_locations_from_table(self.starting_locations_table)

    def _selected_starting_location_for_setup(self) -> dict[str, str]:
        """Returns the Locations-page selected start location, if any."""

        if not hasattr(self, "start_location_combo"):
            return {}

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

    def _sync_start_location_from_locations_combo(self) -> None:
        """Uses the Locations-page selection as the actual starting location."""

        if not hasattr(self, "start_location_combo"):
            return

        row_id = self.start_location_combo.currentData()
        if row_id in (None, ""):
            return

        row = _starting_location_row_for_id(self.starting_locations_table, row_id)

        if row < 0:
            return

        name_widget = self.starting_locations_table.cellWidget(row, 0)
        mode_widget = self.starting_locations_table.cellWidget(row, 2)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            return

        self.start_location_input.setText(name)

        if isinstance(mode_widget, QComboBox):
            _set_combo_to_data(
                self.start_location_mode_combo,
                str(mode_widget.currentData() or "suggestion"),
            )

    def _refresh_starting_location_dropdowns(self) -> None:
        """Keeps start and parent-location dropdowns aligned with live row names."""

        if not hasattr(self, "starting_locations_table"):
            return

        locations = _starting_location_options_from_table(self.starting_locations_table)
        selected_start = (
            self.start_location_combo.currentData()
            if hasattr(self, "start_location_combo")
            else ""
        )

        if hasattr(self, "start_location_combo"):
            self.start_location_combo.blockSignals(True)
            self.start_location_combo.clear()
            self.start_location_combo.addItem("Select from starting locations", "")

            for row_id, name in locations:
                self.start_location_combo.addItem(name, row_id)

            _set_combo_to_data(self.start_location_combo, str(selected_start or ""))
            self.start_location_combo.blockSignals(False)

        valid_ids = {row_id for row_id, _name in locations}
        _sync_starting_location_parent_dropdowns(
            self.starting_locations_table,
            locations,
        )
        if hasattr(self, "starting_npcs_table"):
            _sync_starting_npc_location_dropdowns(
                self.starting_npcs_table,
                locations,
            )
        if hasattr(self, "starting_party_table"):
            self._sync_starting_party_choices()

        if str(selected_start or "") not in valid_ids and hasattr(
            self,
            "start_location_combo",
        ):
            self.start_location_combo.setCurrentIndex(0)
            if selected_start not in (None, ""):
                self.start_location_input.clear()
            return

        self._sync_start_location_from_locations_combo()

    def _select_starting_location_combo_by_name(self, name: str) -> None:
        """Selects a structured start-location row by visible location name."""

        if not hasattr(self, "start_location_combo"):
            return

        clean_name = str(name or "").strip().casefold()

        if not clean_name:
            return

        for index in range(self.start_location_combo.count()):
            if self.start_location_combo.itemText(index).strip().casefold() == clean_name:
                self.start_location_combo.setCurrentIndex(index)
                return

    def _build_starting_task_page(self) -> None:
        """Builds the optional opening quest page."""

        page = QWizardPage()
        page.setTitle("Starting Quest")
        page.setSubTitle("Choose whether the save starts with an active quest.")

        self.starting_task_mode_combo = _NoWheelComboBox()
        self.starting_task_mode_combo.addItem("No starting quest", "none")
        self.starting_task_mode_combo.addItem("Let the A.I. create one", "ai")
        self.starting_task_mode_combo.addItem("Use a custom starting quest", "custom")
        self.starting_task_mode_combo.currentIndexChanged.connect(
            lambda _index: self._sync_starting_task_controls()
        )

        self.starting_task_guidance_input = QTextEdit()
        self.starting_task_guidance_input.setPlaceholderText(
            "Optional: describe the kind of starting quest you have in mind. "
            "The A.I. will use this as inspiration and fill in the details."
        )
        self.starting_task_guidance_input.setMinimumHeight(120)
        self.starting_task_guidance_input.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.starting_task_guidance_group = QGroupBox("Optional A.I. Quest Nudge")
        guidance_layout = QVBoxLayout()
        guidance_layout.addWidget(self.starting_task_guidance_input)
        self.starting_task_guidance_group.setLayout(guidance_layout)

        self.starting_task_name_input = QLineEdit()
        self.starting_task_name_input.setPlaceholderText("Optional quest name")
        self.starting_task_description_input = QTextEdit()
        self.starting_task_description_input.setPlaceholderText(
            "What the quest is about. Leave blanks for the A.I. to complete."
        )
        self.starting_task_requester_input = QLineEdit()
        self.starting_task_requester_input.setPlaceholderText(
            "NPC, faction, shop, Self, or blank"
        )
        self.starting_task_location_input = QLineEdit()
        self.starting_task_location_input.setPlaceholderText(
            "Where the quest is picked up, done, or turned in"
        )
        self.starting_task_reward_input = QLineEdit()
        self.starting_task_reward_input.setPlaceholderText("Reward, N/A, or blank")
        self.starting_task_due_date_input = QLineEdit()
        self.starting_task_due_date_input.setPlaceholderText("Deadline, N/A, or blank")

        self.starting_task_custom_group = QGroupBox("Custom Quest Draft")
        custom_form = QFormLayout()
        _configure_responsive_form(custom_form)
        custom_form.addRow("Name:", self.starting_task_name_input)
        custom_form.addRow("Description:", self.starting_task_description_input)
        custom_form.addRow("Requester:", self.starting_task_requester_input)
        custom_form.addRow("Location:", self.starting_task_location_input)
        custom_form.addRow("Reward:", self.starting_task_reward_input)
        custom_form.addRow("Due:", self.starting_task_due_date_input)
        self.starting_task_custom_group.setLayout(custom_form)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Opening quest mode"))
        layout.addWidget(self.starting_task_mode_combo)
        layout.addWidget(self.starting_task_guidance_group)
        layout.addWidget(self.starting_task_custom_group)
        layout.addStretch()
        page.setLayout(layout)

        self.addPage(page)
        self._sync_starting_task_controls()

    def _sync_starting_task_controls(self) -> None:
        """Shows custom quest fields only when custom mode is selected."""

        if not hasattr(self, "starting_task_custom_group"):
            return

        mode = self.starting_task_mode_combo.currentData()
        self.starting_task_guidance_group.setVisible(mode == "ai")
        is_custom = mode == "custom"
        self.starting_task_custom_group.setVisible(is_custom)

    def _starting_task_from_controls(self) -> dict[str, Any]:
        """Reads the optional starting quest page."""

        mode = str(self.starting_task_mode_combo.currentData() or "none")
        return {
            "mode": mode,
            "guidance": self.starting_task_guidance_input.toPlainText(),
            "task": {
                "name": self.starting_task_name_input.text(),
                "category": "Quest",
                "description": self.starting_task_description_input.toPlainText(),
                "requester": self.starting_task_requester_input.text(),
                "location": self.starting_task_location_input.text(),
                "reward": self.starting_task_reward_input.text(),
                "due_date": self.starting_task_due_date_input.text(),
                "due_elapsed_minutes": -1,
            },
        }

    def _load_starting_task(self, starting_task: dict[str, Any]) -> None:
        """Loads optional starting quest controls from normalized setup."""

        task_setup = starting_task if isinstance(starting_task, dict) else {}
        task = task_setup.get("task", {})
        if not isinstance(task, dict):
            task = {}

        _set_combo_to_data(
            self.starting_task_mode_combo,
            str(task_setup.get("mode", "none")),
        )
        self.starting_task_guidance_input.setPlainText(
            str(task_setup.get("guidance", ""))
        )
        self.starting_task_name_input.setText(str(task.get("name", "")))
        self.starting_task_description_input.setPlainText(
            str(task.get("description", ""))
        )
        self.starting_task_requester_input.setText(str(task.get("requester", "")))
        self.starting_task_location_input.setText(str(task.get("location", "")))
        self.starting_task_reward_input.setText(str(task.get("reward", "")))
        self.starting_task_due_date_input.setText(str(task.get("due_date", "")))
        self._sync_starting_task_controls()

    def _build_starting_npcs_page(self) -> None:
        """Builds the requested starting NPCs page."""

        page = QWizardPage()
        page.setTitle("NPCs")
        page.setSubTitle("Add starting NPCs the player character may know.")

        self.starting_npcs_table = _AppTableWidget(0, 5)
        self.starting_npcs_table.setHorizontalHeaderLabels(
            ["Name", "Location", "Description", "Description Mode", "Remove"]
        )
        _configure_inline_table(
            self.starting_npcs_table,
            STARTING_NPC_COLUMN_WIDTHS,
            minimum_height=240,
        )
        _configure_responsive_table(
            self.starting_npcs_table,
            stretch_columns={0, 1, 2},
            compact_columns={3, 4},
        )

        self.no_starting_npcs_checkbox = QCheckBox("No starting NPCs")
        self.no_starting_npcs_checkbox.toggled.connect(
            self._handle_no_starting_npcs_toggled
        )
        self.add_npc_button = QPushButton("Add NPC")
        self.add_npc_button.clicked.connect(lambda: self._append_starting_npc_row({}))

        npc_editor_layout = QVBoxLayout()
        npc_editor_layout.setContentsMargins(0, 0, 0, 0)
        npc_editor_layout.setSpacing(8)
        npc_editor_layout.addWidget(_button_row(self.add_npc_button))
        npc_editor_layout.addWidget(self.starting_npcs_table)
        self.starting_npcs_editor_container = QWidget()
        self.starting_npcs_editor_container.setLayout(npc_editor_layout)
        self.starting_npcs_editor_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

        layout = QVBoxLayout()
        layout.addWidget(self.no_starting_npcs_checkbox)
        layout.addWidget(self.starting_npcs_editor_container)
        layout.addStretch()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        page.setLayout(layout)

        self.starting_npcs_page_id = self.addPage(page)
        self._sync_starting_npcs_controls()

    def _append_starting_npc_row(self, npc: dict[str, Any]) -> None:
        """Adds one requested starting NPC row to the wizard table."""

        _append_starting_npc_table_row(
            self.starting_npcs_table,
            npc,
            self._remove_starting_npc_row,
            location_options=_starting_location_options_from_table(
                self.starting_locations_table
            ),
            change_callback=self._sync_starting_party_choices,
        )
        self._sync_starting_party_choices()

    def _remove_starting_npc_row(self, button: QPushButton) -> None:
        """Removes the starting NPC row containing button."""

        _remove_table_row_by_button(self.starting_npcs_table, button)
        self._sync_starting_party_choices()

    def _starting_npcs_from_table(self) -> list[dict[str, Any]]:
        """Reads requested starting NPC rows from the wizard table."""

        if (
            hasattr(self, "no_starting_npcs_checkbox")
            and self.no_starting_npcs_checkbox.isChecked()
        ):
            return []

        return _starting_npcs_from_table(self.starting_npcs_table)

    def _handle_no_starting_npcs_toggled(self, checked: bool) -> None:
        """Confirms and applies the no-starting-NPCs option."""

        if checked and self.starting_npcs_table.rowCount() > 0:
            result = QMessageBox.question(
                self,
                "Clear Starting NPCs",
                "Are you sure you want to clear all NPCs?",
            )

            if result != QMessageBox.StandardButton.Yes:
                self.no_starting_npcs_checkbox.blockSignals(True)
                self.no_starting_npcs_checkbox.setChecked(False)
                self.no_starting_npcs_checkbox.blockSignals(False)
                self._sync_starting_npcs_controls()
                return

            self.starting_npcs_table.setRowCount(0)
            self._sync_starting_party_choices()

        self._sync_starting_npcs_controls()

    def _sync_starting_npcs_controls(self) -> None:
        """Hides starting NPC editing while the no-NPCs option is active."""

        if not hasattr(self, "no_starting_npcs_checkbox"):
            return

        allow_npcs = not self.no_starting_npcs_checkbox.isChecked()
        self.starting_npcs_editor_container.setVisible(allow_npcs)

    def _build_starting_party_page(self) -> None:
        """Builds the starting-party selector from the Wizard NPC list."""

        page = QWizardPage()
        page.setTitle("Party")
        page.setSubTitle(
            "Choose which starting NPCs are already traveling with the player."
        )

        self.starting_party_npc_combo = QComboBox()
        self.add_starting_party_member_button = QPushButton("Add to Party")
        self.add_starting_party_member_button.clicked.connect(
            self._add_selected_starting_party_member
        )
        selection_controls = _button_row(
            self.starting_party_npc_combo,
            self.add_starting_party_member_button,
        )
        selection_form = QFormLayout()
        _configure_responsive_form(selection_form)
        selection_form.addRow("NPC to Add:", selection_controls)
        self.starting_party_selection_container = QWidget()
        self.starting_party_selection_container.setLayout(selection_form)

        self.starting_party_table = _AppTableWidget(0, 3)
        self.starting_party_table.setHorizontalHeaderLabels(
            ["NPC", "Starting Location", "Remove"]
        )
        _configure_inline_table(
            self.starting_party_table,
            (360, 320, 90),
            minimum_height=240,
        )
        _configure_responsive_table(
            self.starting_party_table,
            stretch_columns={0, 1},
            compact_columns={2},
        )
        explanation = QLabel(
            "Party members remain the same NPCs shown on the NPC page and retain "
            "the same hidden NPC ID. Removing an NPC there also removes them here."
        )
        explanation.setWordWrap(True)
        self.starting_party_empty_label = QLabel()
        self.starting_party_empty_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(explanation)
        layout.addWidget(self.starting_party_selection_container)
        layout.addWidget(self.starting_party_empty_label)
        layout.addWidget(self.starting_party_table)
        layout.addStretch()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        page.setLayout(layout)
        self.starting_party_page_id = self.addPage(page)
        self._sync_starting_party_choices()

    def _starting_npc_choices(self) -> list[dict[str, str]]:
        """Reads current NPC names and locations with their stable hidden IDs."""

        choices: list[dict[str, str]] = []
        for row in range(self.starting_npcs_table.rowCount()):
            name_widget = self.starting_npcs_table.cellWidget(row, 0)
            location_widget = self.starting_npcs_table.cellWidget(row, 1)
            if not isinstance(name_widget, QLineEdit):
                continue
            npc_id = str(name_widget.property("npc_id") or "").strip()
            if not npc_id:
                continue
            name = name_widget.text().strip() or f"Unnamed NPC {row + 1}"
            location = (
                location_widget.currentText().strip()
                if isinstance(location_widget, QComboBox)
                and location_widget.currentData() not in (None, "")
                else ""
            )
            choices.append({"npc_id": npc_id, "name": name, "location": location})
        return choices

    def _starting_party_npc_ids_from_table(self) -> list[str]:
        """Reads selected party identities from the Party page."""

        npc_ids: list[str] = []
        for row in range(self.starting_party_table.rowCount()):
            item = self.starting_party_table.item(row, 0)
            npc_id = (
                str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if item is not None
                else ""
            )
            if npc_id and npc_id not in npc_ids:
                npc_ids.append(npc_id)
        return npc_ids

    def _set_starting_party_npc_ids(self, npc_ids: Any) -> None:
        """Loads selected IDs, dropping any that are absent from the NPC page."""

        requested = (
            [str(value).strip() for value in npc_ids]
            if isinstance(npc_ids, list)
            else []
        )
        available = {choice["npc_id"] for choice in self._starting_npc_choices()}
        self.starting_party_table.setRowCount(0)
        for npc_id in requested:
            if npc_id in available:
                self._append_starting_party_member_row(npc_id)
        self._sync_starting_party_choices()

    def _append_starting_party_member_row(self, npc_id: str) -> None:
        """Adds one selected NPC identity to the Party table."""

        choice = next(
            (
                entry
                for entry in self._starting_npc_choices()
                if entry["npc_id"] == npc_id
            ),
            None,
        )
        if choice is None or npc_id in self._starting_party_npc_ids_from_table():
            return
        row = self.starting_party_table.rowCount()
        self.starting_party_table.insertRow(row)
        name_item = QTableWidgetItem(choice["name"])
        name_item.setData(Qt.ItemDataRole.UserRole, npc_id)
        location_item = QTableWidgetItem(choice["location"] or "Not specified")
        location_item.setData(Qt.ItemDataRole.UserRole, npc_id)
        self.starting_party_table.setItem(row, 0, name_item)
        self.starting_party_table.setItem(row, 1, location_item)
        _set_remove_row_button(
            self.starting_party_table,
            row,
            2,
            "party member",
            self._remove_starting_party_member_row,
        )

    def _remove_starting_party_member_row(self, button: QPushButton) -> None:
        """Removes one confirmed member from the starting party."""

        if _remove_table_row_by_button(self.starting_party_table, button) >= 0:
            self._sync_starting_party_choices()

    def _add_selected_starting_party_member(self) -> None:
        """Adds the NPC currently selected in the dropdown."""

        npc_id = str(self.starting_party_npc_combo.currentData() or "").strip()
        if npc_id:
            self._append_starting_party_member_row(npc_id)
            self._sync_starting_party_choices()

    def _sync_starting_party_choices(self) -> None:
        """Refreshes Party selections and options from the live Wizard NPC rows."""

        if not hasattr(self, "starting_party_table"):
            return
        choices = self._starting_npc_choices()
        choices_by_id = {choice["npc_id"]: choice for choice in choices}
        selected_ids = [
            npc_id
            for npc_id in self._starting_party_npc_ids_from_table()
            if npc_id in choices_by_id
        ]

        self.starting_party_table.setRowCount(0)
        for npc_id in selected_ids:
            self._append_starting_party_member_row(npc_id)

        previous_id = str(self.starting_party_npc_combo.currentData() or "").strip()
        self.starting_party_npc_combo.blockSignals(True)
        self.starting_party_npc_combo.clear()
        for choice in choices:
            if choice["npc_id"] in selected_ids:
                continue
            label = choice["name"]
            if choice["location"]:
                label = f"{label} — {choice['location']}"
            self.starting_party_npc_combo.addItem(label, choice["npc_id"])
        restored_index = self.starting_party_npc_combo.findData(previous_id)
        if restored_index >= 0:
            self.starting_party_npc_combo.setCurrentIndex(restored_index)
        self.starting_party_npc_combo.blockSignals(False)
        self.add_starting_party_member_button.setEnabled(
            self.starting_party_npc_combo.count() > 0
        )
        has_choices = self.starting_party_npc_combo.count() > 0
        has_party_members = self.starting_party_table.rowCount() > 0
        has_any_npcs = bool(choices)
        self.starting_party_selection_container.setVisible(has_choices)
        self.starting_party_table.setVisible(has_party_members)
        if not has_any_npcs:
            empty_text = "Add NPCs on the previous page before choosing a starting party."
        elif not has_party_members:
            empty_text = "No NPCs are in the starting party yet."
        else:
            empty_text = ""
        self.starting_party_empty_label.setText(empty_text)
        self.starting_party_empty_label.setVisible(bool(empty_text))

    def _build_character_page(self) -> None:
        """Builds the character page."""

        page = QWizardPage()
        page.setTitle("Character")
        page.setSubTitle("Describe the player character.")

        self.character_name_input = QLineEdit()
        self.character_name_input.setText("Player Name")
        self.character_name_pronunciation_input = QLineEdit()
        self.character_name_pronunciation_input.setPlaceholderText(
            "Optional: kah-tha-lah, or /kəˈθɑlə/ for exact IPA"
        )
        self.character_pronouns_combo = _NoWheelComboBox()
        for pronouns in CHARACTER_PRONOUN_OPTIONS:
            self.character_pronouns_combo.addItem(pronouns, pronouns)
        self.character_pronouns_combo.addItem("Other", "other")
        self.character_custom_pronouns_input = QLineEdit()
        self.character_custom_pronouns_input.setPlaceholderText(
            "Enter custom pronouns, such as Xe/Xem"
        )
        self.character_pronouns_combo.currentIndexChanged.connect(
            self._sync_character_pronoun_controls
        )
        self._set_character_pronouns(DEFAULT_CHARACTER_PRONOUNS)

        self.appearance_input = QTextEdit()
        self.backstory_input = QTextEdit()
        self.character_notes_input = QTextEdit()

        self.appearance_input.setPlaceholderText("Appearance, clothing, visible traits, voice...")
        self.backstory_input.setPlaceholderText("Origin, history, goals, relationships...")
        self.character_notes_input.setPlaceholderText("Other character notes the AI should know...")

        layout = QFormLayout()
        self.character_form_layout = layout
        _configure_responsive_form(layout)
        layout.addRow("Name:", self.character_name_input)
        layout.addRow("Name Pronunciation:", self.character_name_pronunciation_input)
        layout.addRow("Pronouns:", self.character_pronouns_combo)
        layout.addRow("Custom Pronouns:", self.character_custom_pronouns_input)
        layout.addRow("Appearance:", self.appearance_input)
        layout.addRow("Backstory:", self.backstory_input)
        layout.addRow("Notes:", self.character_notes_input)
        self._sync_character_pronoun_controls()
        page.setLayout(layout)

        self.character_page_id = self.addPage(page)

    def _sync_character_pronoun_controls(self, _index: int = -1) -> None:
        """Shows custom pronoun entry only when Other is selected."""

        is_custom = self.character_pronouns_combo.currentData() == "other"
        if hasattr(self, "character_form_layout"):
            self.character_form_layout.setRowVisible(
                self.character_custom_pronouns_input,
                is_custom,
            )
        else:
            self.character_custom_pronouns_input.setVisible(is_custom)

    def _character_pronouns_from_controls(self) -> str:
        """Returns the Wizard's canonical player-character pronouns."""

        if self.character_pronouns_combo.currentData() == "other":
            return normalize_character_pronouns(
                self.character_custom_pronouns_input.text()
            )
        return normalize_character_pronouns(
            self.character_pronouns_combo.currentData()
        )

    def _set_character_pronouns(self, pronouns: Any) -> None:
        """Loads standard or custom canonical pronouns into the Wizard."""

        canonical = normalize_character_pronouns(pronouns)
        index = self.character_pronouns_combo.findData(canonical)
        is_custom = index < 0
        if is_custom:
            index = self.character_pronouns_combo.findData("other")
        self.character_pronouns_combo.setCurrentIndex(max(0, index))
        self.character_custom_pronouns_input.setText(canonical if is_custom else "")
        self._sync_character_pronoun_controls()

    def _build_skills_page(self) -> None:
        """Builds the starting skills page."""

        page = QWizardPage()
        page.setTitle("Skills")
        page.setSubTitle("Choose a starting experience profile, then name and describe its skills.")

        self.skill_inputs: list[tuple[int, QLineEdit, QLineEdit]] = []
        self.skill_tables: dict[int, _AppTableWidget] = {}
        self.skill_table_controls: dict[int, QWidget] = {}
        content = QWidget()
        layout = QVBoxLayout()

        self.skill_preset_combo = _NoWheelComboBox()
        preset_options = (
            ("Professional Adventurer", "professional"),
            ("Experienced Adventurer", "experienced"),
            ("Average Adventurer", "average"),
            ("Beginner Adventurer", "beginner"),
            ("Blank Slate / Hardcore Mode", "blank"),
            ("Custom", "custom"),
        )
        for label, key in preset_options:
            self.skill_preset_combo.addItem(label, key)
        layout.addWidget(QLabel("Starting Skill Profile"))
        layout.addWidget(self.skill_preset_combo)

        for level in range(5, 0, -1):
            group = QGroupBox(f"Level {level}")
            group_layout = QVBoxLayout()

            meaning_label = QLabel(SKILL_LEVEL_DESCRIPTIONS[level])
            meaning_label.setWordWrap(True)
            group_layout.addWidget(meaning_label)
            table = _AppTableWidget(0, 3)
            table.setHorizontalHeaderLabels(["Remove", "Skill", "Description for AI"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            table.verticalHeader().setVisible(False)
            table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
            table.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
            _configure_auto_height_table(table)
            _configure_table_wheel_passthrough(table)
            self.skill_tables[level] = table

            add_button = QPushButton("Add Skill")
            add_button.clicked.connect(lambda _checked=False, skill_level=level: self._add_starting_skill_row(skill_level))
            controls = QWidget()
            controls_layout = QHBoxLayout()
            controls_layout.setContentsMargins(0, 0, 0, 0)
            controls_layout.addWidget(add_button)
            controls_layout.addStretch()
            controls.setLayout(controls_layout)
            group_layout.addWidget(controls)
            group_layout.addWidget(table)
            self.skill_table_controls[level] = controls

            group.setLayout(group_layout)
            layout.addWidget(group)

        self.skill_preset_combo.currentIndexChanged.connect(self._apply_starting_skill_preset)
        self._apply_starting_skill_preset()

        layout.addStretch()
        content.setLayout(layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setWidget(content)

        page_layout = QVBoxLayout()
        page_layout.addWidget(scroll_area)
        page.setLayout(page_layout)
        self.addPage(page)

    def _add_starting_skill_row(
        self,
        level: int,
        name: str = "",
        description: str = "",
    ) -> None:
        table = self.skill_tables[level]
        row = table.rowCount()
        table.insertRow(row)
        _set_remove_row_button(
            table,
            row,
            0,
            "skill",
            lambda button, skill_level=level: self._remove_starting_skill_row(
                skill_level,
                button,
            ),
            name_column=1,
        )
        skill_input = QLineEdit(name)
        skill_input.setPlaceholderText("Skill name")
        description_input = QLineEdit(description)
        description_input.setPlaceholderText("What this skill covers and when it applies")
        table.setCellWidget(row, 1, skill_input)
        table.setCellWidget(row, 2, description_input)
        self._sync_starting_skill_inputs()

    def _remove_starting_skill_row(
        self,
        level: int,
        button: QPushButton,
    ) -> None:
        """Removes one confirmed custom skill row."""

        table = self.skill_tables[level]
        if _remove_table_row_by_button(table, button) >= 0:
            self._sync_starting_skill_inputs()

    def _sync_starting_skill_inputs(self) -> None:
        self.skill_inputs = []
        for level in range(5, 0, -1):
            table = self.skill_tables[level]
            for row in range(table.rowCount()):
                name_input = table.cellWidget(row, 1)
                description_input = table.cellWidget(row, 2)
                if isinstance(name_input, QLineEdit) and isinstance(description_input, QLineEdit):
                    self.skill_inputs.append((level, name_input, description_input))

    def _apply_starting_skill_preset(self, _index: int = -1) -> None:
        preset = str(self.skill_preset_combo.currentData() or "professional")
        plan = SKILL_PRESET_LEVEL_PLANS.get(preset, [])
        is_custom = preset == "custom"
        for level, table in self.skill_tables.items():
            table.setRowCount(0)
            table.setColumnHidden(0, not is_custom)
            count = plan.count(level)
            for _unused in range(count):
                self._add_starting_skill_row(level)
            parent_widget = table.parentWidget()
            if parent_widget is not None:
                parent_widget.setVisible(is_custom or count > 0)
            self.skill_table_controls[level].setVisible(is_custom)
        self._sync_starting_skill_inputs()

    def _build_magic_page(self) -> None:
        """Builds world magic and optional starting player-spell controls."""

        page = QWizardPage()
        page.setTitle("Magic")
        page.setSubTitle(
            "Choose whether magic exists and how player spellcasting works at the start."
        )

        self.no_world_magic_checkbox = QCheckBox("This world does not contain magic.")
        self.magic_enabled_checkbox = QCheckBox(
            "The player character can cast spells at the start"
        )
        self.magic_casting_mode_combo = QComboBox()
        for mode in MAGIC_CASTING_MODES:
            self.magic_casting_mode_combo.addItem(MAGIC_CASTING_MODE_LABELS[mode], mode)
        self.magic_tradition_input = QLineEdit()
        self.magic_tradition_input.setPlaceholderText(
            "Arcane, Divine, Psychic, Elemental, or another setting-appropriate tradition"
        )
        self.magic_mana_maximum_input = _NoWheelSpinBox()
        self.magic_mana_maximum_input.setRange(1, 9999)
        self.magic_mana_maximum_input.setValue(10)

        self.magic_tier_slot_inputs: dict[int, QSpinBox] = {}
        tier_form = QFormLayout()
        for tier in range(1, 10):
            slot_input = _NoWheelSpinBox()
            slot_input.setRange(0, 99)
            slot_input.setValue(2 if tier == 1 else 0)
            self.magic_tier_slot_inputs[tier] = slot_input
            tier_form.addRow(f"Tier {tier} slots:", slot_input)
        self.magic_tier_slots_group = QGroupBox("Starting Tiered Slots")
        self.magic_tier_slots_group.setLayout(tier_form)

        mana_form = QFormLayout()
        mana_form.addRow("Maximum Mana:", self.magic_mana_maximum_input)
        self.magic_mana_group = QGroupBox("Starting Mana")
        self.magic_mana_group.setLayout(mana_form)

        self.starting_spell_requests_table = _AppTableWidget(0, 2)
        self.starting_spell_requests_table.setHorizontalHeaderLabels(
            ["Spell Description", "Remove"]
        )
        self.starting_spell_requests_table.verticalHeader().setVisible(False)
        self.starting_spell_requests_table.verticalHeader().setDefaultSectionSize(40)
        self.starting_spell_requests_table.setSelectionMode(
            QTableWidget.SelectionMode.NoSelection
        )
        _configure_responsive_table(
            self.starting_spell_requests_table,
            stretch_columns={0},
            compact_columns={1},
        )
        self.magic_add_spell_request_button = QPushButton("Add Starting Spell")
        self.magic_add_spell_request_button.clicked.connect(
            lambda: self._append_starting_spell_request_row({})
        )

        self.starting_spells_table = _AppTableWidget(0, 7)
        self.starting_spells_table.setHorizontalHeaderLabels(
            ["Name", "Tier", "School", "Mana Cost", "Prepared", "Description", "Remove"]
        )
        self.starting_spells_table.verticalHeader().setVisible(False)
        self.starting_spells_table.verticalHeader().setDefaultSectionSize(40)
        self.starting_spells_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        _configure_responsive_table(
            self.starting_spells_table,
            stretch_columns={0, 2, 5},
            compact_columns={1, 3, 4, 6},
        )
        self.magic_add_spell_button = QPushButton("Add Starting Spell")
        self.magic_add_spell_button.clicked.connect(
            lambda: self._append_starting_spell_row({})
        )

        self.starting_spells_basic_button = QPushButton("Basic")
        self.starting_spells_advanced_button = QPushButton("Advanced")
        self.starting_spells_mode_buttons = QButtonGroup(self)
        self.starting_spells_mode_buttons.setExclusive(True)
        self.starting_spells_mode_buttons.addButton(
            self.starting_spells_basic_button,
            0,
        )
        self.starting_spells_mode_buttons.addButton(
            self.starting_spells_advanced_button,
            1,
        )
        for mode_button in (
            self.starting_spells_basic_button,
            self.starting_spells_advanced_button,
        ):
            mode_button.setObjectName("starterInventoryModeButton")
            mode_button.setCheckable(True)
        self.starting_spells_basic_button.setChecked(True)

        form = QFormLayout()
        _configure_responsive_form(form)
        form.addRow("Casting Model:", self.magic_casting_mode_combo)
        form.addRow("Tradition / Style:", self.magic_tradition_input)

        basic_spells_layout = QVBoxLayout()
        basic_spells_explanation = QLabel(
            "Describe each spell plainly. Gemini will create its name, tier, school, "
            "cost, and complete gameplay details during game creation."
        )
        basic_spells_explanation.setWordWrap(True)
        basic_spells_layout.addWidget(basic_spells_explanation)
        basic_spells_layout.addWidget(
            _button_row(self.magic_add_spell_request_button)
        )
        basic_spells_layout.addWidget(self.starting_spell_requests_table)
        basic_spells_widget = QWidget()
        basic_spells_widget.setLayout(basic_spells_layout)

        advanced_spells_layout = QVBoxLayout()
        advanced_spells_layout.addWidget(_button_row(self.magic_add_spell_button))
        advanced_spells_layout.addWidget(self.starting_spells_table)
        advanced_spells_widget = QWidget()
        advanced_spells_widget.setLayout(advanced_spells_layout)

        self.starting_spells_mode_stack = QStackedWidget()
        self.starting_spells_mode_stack.addWidget(basic_spells_widget)
        self.starting_spells_mode_stack.addWidget(advanced_spells_widget)
        self.starting_spells_mode_buttons.idClicked.connect(
            self.starting_spells_mode_stack.setCurrentIndex
        )

        spells_layout = QVBoxLayout()
        spells_mode_explanation = QLabel(
            "Basic lets Gemini develop plain-language spell ideas; Advanced lets "
            "you enter exact spell values."
        )
        spells_mode_explanation.setWordWrap(True)
        spells_layout.addWidget(spells_mode_explanation)
        spells_layout.addWidget(
            _button_row(
                self.starting_spells_basic_button,
                self.starting_spells_advanced_button,
            )
        )
        spells_layout.addWidget(self.starting_spells_mode_stack)
        self.starting_spells_group = QGroupBox("Starting Spells")
        self.starting_spells_group.setLayout(spells_layout)

        player_casting_controls_layout = QVBoxLayout()
        player_casting_controls_layout.setContentsMargins(0, 0, 0, 0)
        player_casting_controls_layout.addWidget(self.magic_mana_group)
        player_casting_controls_layout.addWidget(self.magic_tier_slots_group)
        player_casting_controls_layout.addWidget(self.starting_spells_group)
        self.magic_player_casting_controls_container = QWidget()
        self.magic_player_casting_controls_container.setLayout(
            player_casting_controls_layout
        )

        explanation = QLabel(
            "Narrative casting tracks known spells without a consumable resource. "
            "Mana spends each spell's Mana Cost. Tiered casting spends one slot at "
            "the selected tier; Tier 0 spells are at-will."
        )
        explanation.setWordWrap(True)
        player_options_layout = QVBoxLayout()
        player_options_layout.setContentsMargins(0, 0, 0, 0)
        player_options_layout.addWidget(explanation)
        player_options_layout.addLayout(form)
        player_options_layout.addWidget(self.magic_player_casting_controls_container)
        player_options_layout.addStretch()
        self.magic_player_options_container = QWidget()
        self.magic_player_options_container.setLayout(player_options_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setWidget(self.magic_player_options_container)
        self.magic_player_options_scroll = scroll_area
        layout = QVBoxLayout()
        player_magic_explanation = QLabel(
            "Leave player casting unchecked when magic exists in the world but "
            "the player character cannot use it at the start."
        )
        player_magic_explanation.setWordWrap(True)
        options_layout = QVBoxLayout()
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.addWidget(player_magic_explanation)
        options_layout.addWidget(self.magic_enabled_checkbox)
        options_layout.addWidget(scroll_area)
        self.magic_options_container = QWidget()
        self.magic_options_container.setLayout(options_layout)

        layout.addWidget(self.no_world_magic_checkbox)
        layout.addWidget(self.magic_options_container)
        page.setLayout(layout)

        self.no_world_magic_checkbox.toggled.connect(self._sync_magic_controls)
        self.magic_enabled_checkbox.toggled.connect(self._sync_magic_controls)
        self.magic_casting_mode_combo.currentIndexChanged.connect(self._sync_magic_controls)
        self._sync_magic_controls()
        self.addPage(page)

    def _build_ai_settings_page(self) -> None:
        """Builds the dedicated new-game A.I. configuration page."""

        page = QWizardPage()
        page.setTitle("A.I. Settings")
        page.setSubTitle(
            "Choose the text and image models used by this adventure, then tune "
            "narration and content preferences."
        )

        description_style = "font-size: 11px;"
        self.text_model_combo = _NoWheelComboBox(page)
        AISettingsDialog._add_mode_options(self.text_model_combo, TEXT_MODEL_OPTIONS)
        _set_combo_to_data(self.text_model_combo, self._new_game_ai_settings["text_model"])
        self.text_model_description = QLabel()
        self.text_model_description.setWordWrap(True)
        self.text_model_description.setStyleSheet(description_style)

        self.smarter_ai_checkbox = QCheckBox(
            'Do you want the A.I. to be "Smarter"?'
        )
        self.smarter_ai_checkbox.setChecked(False)
        self.smarter_ai_checkbox.setToolTip(
            "May cause longer delays between messages and slower overall gameplay, "
            "but response quality should increase."
        )
        smarter_note = QLabel(
            "May cause longer delays between messages and slower overall gameplay, "
            "but response quality should increase."
        )
        smarter_note.setWordWrap(True)
        smarter_note.setStyleSheet(description_style)

        self.generated_images_enabled_checkbox = QCheckBox(
            "Generate images for characters, locations, NPCs, and inventory items"
        )
        self.generated_images_enabled_checkbox.setChecked(True)
        self.image_model_combo = _NoWheelComboBox(page)
        AISettingsDialog._add_mode_options(self.image_model_combo, IMAGE_MODEL_OPTIONS)
        _set_combo_to_data(
            self.image_model_combo,
            self._new_game_image_settings["model"],
        )
        self.image_model_description = QLabel()
        self.image_model_description.setWordWrap(True)
        self.image_model_description.setStyleSheet(description_style)

        self.image_style_combo = _NoWheelComboBox(page)
        AISettingsDialog._add_mode_options(self.image_style_combo, IMAGE_STYLE_OPTIONS)
        _set_combo_to_data(
            self.image_style_combo,
            self._new_game_image_settings["style"],
        )
        self.image_style_description = QLabel()
        self.image_style_description.setWordWrap(True)
        self.image_style_description.setStyleSheet(description_style)

        catalog_note = QLabel(
            "Only Google Gemini API models marked Stable (GA) and supporting the "
            "required output type are listed. Catalog reviewed "
            f"{MODEL_CATALOG_REVIEWED_DATE}."
        )
        catalog_note.setWordWrap(True)
        catalog_note.setStyleSheet(description_style)

        model_group = QGroupBox("Models")
        model_layout = QVBoxLayout(model_group)
        model_layout.addWidget(catalog_note)
        model_layout.addWidget(
            self._new_game_ai_choice_field(
                "Text Model",
                self.text_model_combo,
                self.text_model_description,
            )
        )
        model_layout.addWidget(self.smarter_ai_checkbox)
        model_layout.addWidget(smarter_note)
        model_layout.addWidget(self.generated_images_enabled_checkbox)
        model_layout.addWidget(
            self._new_game_ai_choice_field(
                "Image Model",
                self.image_model_combo,
                self.image_model_description,
            )
        )
        model_layout.addWidget(
            self._new_game_ai_choice_field(
                "Image Style (applies to every generated image)",
                self.image_style_combo,
                self.image_style_description,
            )
        )

        modes = normalize_ai_mode_preferences(self._new_game_ai_settings)
        self.model_tone_combo = _NoWheelComboBox(page)
        AISettingsDialog._add_mode_options(self.model_tone_combo, MODEL_TONE_OPTIONS)
        _set_combo_to_data(self.model_tone_combo, modes["model_tone"])
        self.model_tone_description = QLabel()
        self.model_tone_description.setWordWrap(True)
        self.model_tone_description.setStyleSheet(description_style)

        self.response_length_combo = _NoWheelComboBox(page)
        AISettingsDialog._add_mode_options(
            self.response_length_combo,
            RESPONSE_LENGTH_OPTIONS,
        )
        _set_combo_to_data(self.response_length_combo, modes["response_length"])
        self.response_length_description = QLabel()
        self.response_length_description.setWordWrap(True)
        self.response_length_description.setStyleSheet(description_style)

        self.model_content_combo = ContentCategoryComboBox(
            list(modes["allowed_content_categories"]),
            page,
        )
        self.model_content_description = QLabel()
        self.model_content_description.setWordWrap(True)
        self.model_content_description.setStyleSheet(description_style)

        behavior_group = QGroupBox("Response Preferences")
        behavior_layout = QVBoxLayout(behavior_group)
        behavior_layout.addWidget(
            self._new_game_ai_choice_field(
                "Model Tone",
                self.model_tone_combo,
                self.model_tone_description,
            )
        )
        behavior_layout.addWidget(
            self._new_game_ai_choice_field(
                "Response Length",
                self.response_length_combo,
                self.response_length_description,
            )
        )
        behavior_layout.addWidget(
            self._new_game_ai_choice_field(
                "Model Content (select every category that may appear)",
                self.model_content_combo,
                self.model_content_description,
            )
        )

        self.narration_tense_combo = _NoWheelComboBox(page)
        _add_combo_options(self.narration_tense_combo, NARRATION_TENSE_OPTIONS)
        _set_combo_to_data(self.narration_tense_combo, DEFAULT_NARRATION_TENSE)
        self.narration_tense_description = QLabel(
            "Controls the grammatical tense used for player-facing narration."
        )
        self.narration_tense_description.setWordWrap(True)
        self.narration_tense_description.setStyleSheet(description_style)

        self.narration_style_combo = _NoWheelComboBox(page)
        _add_combo_options(self.narration_style_combo, NARRATION_STYLE_OPTIONS)
        _set_combo_to_data(self.narration_style_combo, DEFAULT_NARRATION_STYLE)
        self.narration_style_description = QLabel(
            "Controls narrative person and camera while preserving hidden information."
        )
        self.narration_style_description.setWordWrap(True)
        self.narration_style_description.setStyleSheet(description_style)

        self.additional_ai_context_input = QTextEdit(page)
        self.additional_ai_context_input.setPlaceholderText(
            "Optional AI-facing guidance, style preferences, boundaries, or reminders..."
        )
        self.additional_ai_context_input.setMaximumHeight(120)
        additional_description = QLabel(
            "Persistent free-form guidance sent to the A.I. with every story turn."
        )
        additional_description.setWordWrap(True)
        additional_description.setStyleSheet(description_style)

        narration_group = QGroupBox("Narration")
        narration_layout = QVBoxLayout(narration_group)
        narration_layout.addWidget(
            self._new_game_ai_choice_field(
                "Narration Tense",
                self.narration_tense_combo,
                self.narration_tense_description,
            )
        )
        narration_layout.addWidget(
            self._new_game_ai_choice_field(
                "Narration Style",
                self.narration_style_combo,
                self.narration_style_description,
            )
        )
        narration_layout.addWidget(QLabel("Additional A.I. Context"))
        narration_layout.addWidget(self.additional_ai_context_input)
        narration_layout.addWidget(additional_description)

        content = QWidget(page)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(model_group)
        content_layout.addWidget(behavior_group)
        content_layout.addWidget(narration_group)
        content_layout.addStretch()
        scroll_area = QScrollArea(page)
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(content)
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(scroll_area)

        for combo in (
            self.text_model_combo,
            self.image_model_combo,
            self.image_style_combo,
            self.model_tone_combo,
            self.response_length_combo,
        ):
            combo.currentIndexChanged.connect(
                lambda _index: self._refresh_new_game_ai_descriptions()
            )
        self.model_content_combo.selection_changed.connect(
            self._refresh_new_game_ai_descriptions
        )
        self.generated_images_enabled_checkbox.toggled.connect(
            lambda _checked: self._refresh_new_game_ai_descriptions()
        )
        self._refresh_new_game_ai_descriptions()
        self.addPage(page)

    def _append_starting_spell_request_row(self, request: dict[str, Any]) -> None:
        """Adds one Basic-mode plain-language spell request."""

        row = self.starting_spell_requests_table.rowCount()
        self.starting_spell_requests_table.insertRow(row)
        self.starting_spell_requests_table.setRowHeight(row, 40)
        request_input = QLineEdit(str(request.get("spell_request", "")))
        request_input.setPlaceholderText(
            "For example: a protective ward that briefly turns aside an attack"
        )
        self.starting_spell_requests_table.setCellWidget(row, 0, request_input)
        _set_remove_row_button(
            self.starting_spell_requests_table,
            row,
            1,
            "spell request",
            self._remove_starting_spell_request_row,
        )

    def _remove_starting_spell_request_row(self, button: QPushButton) -> None:
        """Removes one Basic-mode spell request."""

        _remove_table_row_by_button(self.starting_spell_requests_table, button)

    def _append_starting_spell_row(self, spell: dict[str, Any]) -> None:
        """Adds one editable starting-spell row to the Wizard."""

        row = self.starting_spells_table.rowCount()
        self.starting_spells_table.insertRow(row)
        self.starting_spells_table.setRowHeight(row, 40)
        name_input = QLineEdit(str(spell.get("name", "")))
        name_input.setPlaceholderText("Spell name")
        tier_input = QSpinBox()
        tier_input.setRange(0, 9)
        tier_input.setValue(_safe_int(spell.get("tier", 0), 0))
        school_input = QLineEdit(str(spell.get("school", "")))
        school_input.setPlaceholderText("School or tradition")
        cost_input = QSpinBox()
        cost_input.setRange(0, 9999)
        cost_input.setValue(_safe_int(spell.get("mana_cost", 0), 0))
        prepared_input = QCheckBox()
        prepared_input.setChecked(bool(spell.get("prepared", True)))
        description_input = QLineEdit(str(spell.get("description", "")))
        description_input.setPlaceholderText("Practical gameplay effect")
        for column, widget in enumerate(
            (name_input, tier_input, school_input, cost_input, prepared_input, description_input)
        ):
            self.starting_spells_table.setCellWidget(row, column, widget)
        _set_remove_row_button(
            self.starting_spells_table,
            row,
            6,
            "spell",
            self._remove_starting_spell_row,
        )

    def _remove_starting_spell_row(self, button: QPushButton) -> None:
        """Removes one confirmed spell from the starting-spell table."""

        _remove_table_row_by_button(self.starting_spells_table, button)

    def _magic_setup_from_controls(self) -> dict[str, Any]:
        """Serializes the Wizard's authoritative magic configuration."""

        world_contains_magic = not self.no_world_magic_checkbox.isChecked()
        player_magic_enabled = (
            world_contains_magic and self.magic_enabled_checkbox.isChecked()
        )
        starting_spells_mode = self._starting_spells_mode()
        starting_spell_requests: list[dict[str, Any]] = []
        starting_spells: list[dict[str, Any]] = []
        if player_magic_enabled and starting_spells_mode == "basic":
            for row in range(self.starting_spell_requests_table.rowCount()):
                request_input = self.starting_spell_requests_table.cellWidget(row, 0)
                if not isinstance(request_input, QLineEdit):
                    continue
                request = request_input.text().strip()
                if request:
                    starting_spell_requests.append(
                        {
                            "spell_request": request,
                            "requires_ai_invention": True,
                        }
                    )
        elif player_magic_enabled:
            for row in range(self.starting_spells_table.rowCount()):
                name_input = self.starting_spells_table.cellWidget(row, 0)
                tier_input = self.starting_spells_table.cellWidget(row, 1)
                school_input = self.starting_spells_table.cellWidget(row, 2)
                cost_input = self.starting_spells_table.cellWidget(row, 3)
                prepared_input = self.starting_spells_table.cellWidget(row, 4)
                description_input = self.starting_spells_table.cellWidget(row, 5)
                if not isinstance(name_input, QLineEdit):
                    continue
                name = name_input.text().strip()
                if not name:
                    continue
                starting_spells.append(
                    {
                        "name": name,
                        "tier": (
                            tier_input.value()
                            if isinstance(tier_input, QSpinBox)
                            else 0
                        ),
                        "school": (
                            school_input.text()
                            if isinstance(school_input, QLineEdit)
                            else ""
                        ),
                        "mana_cost": (
                            cost_input.value()
                            if isinstance(cost_input, QSpinBox)
                            else 0
                        ),
                        "prepared": (
                            prepared_input.isChecked()
                            if isinstance(prepared_input, QCheckBox)
                            else True
                        ),
                        "description": (
                            description_input.text()
                            if isinstance(description_input, QLineEdit)
                            else ""
                        ),
                    }
                )
        return {
            "world_contains_magic": world_contains_magic,
            "player_magic_enabled": player_magic_enabled,
            "enabled": player_magic_enabled,
            "casting_mode": str(
                self.magic_casting_mode_combo.currentData() or "narrative"
            ),
            "tradition": self.magic_tradition_input.text(),
            "mana_maximum": self.magic_mana_maximum_input.value(),
            "tier_slots": {
                tier: slot_input.value()
                for tier, slot_input in self.magic_tier_slot_inputs.items()
            },
            "starting_spells_mode": starting_spells_mode,
            "starting_spell_requests": starting_spell_requests,
            "starting_spells": starting_spells,
        }

    def _starting_spells_mode(self) -> str:
        """Returns the selected Basic/Advanced starting-spell mode."""

        return (
            "advanced"
            if self.starting_spells_advanced_button.isChecked()
            else "basic"
        )

    def _set_starting_spells_mode(self, mode: str) -> None:
        """Selects the starting-spell editing depth and matching editor."""

        is_advanced = str(mode).casefold() == "advanced"
        self.starting_spells_advanced_button.setChecked(is_advanced)
        self.starting_spells_basic_button.setChecked(not is_advanced)
        self.starting_spells_mode_stack.setCurrentIndex(1 if is_advanced else 0)

    def _load_magic_setup(self, magic: dict[str, Any]) -> None:
        """Loads normalized magic setup into Wizard controls."""

        self.no_world_magic_checkbox.setChecked(
            not bool(magic.get("world_contains_magic", True))
        )
        self.magic_enabled_checkbox.setChecked(
            bool(magic.get("player_magic_enabled", magic.get("enabled", False)))
        )
        _set_combo_to_data(
            self.magic_casting_mode_combo,
            str(magic.get("casting_mode", "narrative")),
        )
        self.magic_tradition_input.setText(str(magic.get("tradition", "")))
        self.magic_mana_maximum_input.setValue(
            max(1, _safe_int(magic.get("mana_maximum", 10), 10))
        )
        slots = magic.get("tier_slots", {})
        if not isinstance(slots, dict):
            slots = {}
        for tier, slot_input in self.magic_tier_slot_inputs.items():
            slot_input.setValue(_safe_int(slots.get(tier, slots.get(str(tier), 0)), 0))
        self.starting_spell_requests_table.setRowCount(0)
        self.starting_spells_table.setRowCount(0)
        self._set_starting_spells_mode(
            str(magic.get("starting_spells_mode", "basic"))
        )
        for request in magic.get("starting_spell_requests", []):
            if isinstance(request, dict):
                self._append_starting_spell_request_row(request)
        for spell in magic.get("starting_spells", []):
            if isinstance(spell, dict):
                self._append_starting_spell_row(spell)
        self._sync_magic_controls()

    def _sync_magic_controls(self, _value: Any = None) -> None:
        """Shows only controls relevant to the selected casting model."""

        world_contains_magic = not self.no_world_magic_checkbox.isChecked()
        player_magic_enabled = self.magic_enabled_checkbox.isChecked()
        mode = str(self.magic_casting_mode_combo.currentData() or "narrative")
        self.magic_options_container.setVisible(world_contains_magic)
        self.magic_player_options_scroll.setVisible(world_contains_magic)
        self.magic_player_options_container.setVisible(world_contains_magic)
        show_player_casting_controls = world_contains_magic and player_magic_enabled
        self.magic_player_casting_controls_container.setVisible(
            show_player_casting_controls
        )
        self.magic_mana_group.setVisible(
            show_player_casting_controls and mode == "mana"
        )
        self.magic_tier_slots_group.setVisible(
            show_player_casting_controls and mode == "tiered"
        )
        self.starting_spells_group.setVisible(show_player_casting_controls)

    def _build_combat_page(self) -> None:
        """Builds the player-facing combat focus and resolution page."""

        page = QWizardPage()
        page.setTitle("Combat")
        page.setSubTitle(
            "Choose how prominent combat should be and who resolves actual fights."
        )

        self.combat_focus_combo = QComboBox()
        for focus in COMBAT_FOCUS_LEVELS:
            self.combat_focus_combo.addItem(COMBAT_FOCUS_LABELS[focus], focus)

        self.combat_resolution_mode_combo = QComboBox()
        for mode in COMBAT_RESOLUTION_MODES:
            self.combat_resolution_mode_combo.addItem(
                COMBAT_RESOLUTION_MODE_LABELS[mode], mode
            )

        self.combat_resolution_explanation = QLabel()
        self.combat_resolution_explanation.setWordWrap(True)

        form = QFormLayout()
        _configure_responsive_form(form)
        form.addRow("Combat Focus:", self.combat_focus_combo)
        form.addRow("Combat Resolution:", self.combat_resolution_mode_combo)
        form.addRow(self.combat_resolution_explanation)

        content = QWidget()
        content.setLayout(form)
        layout = QVBoxLayout()
        introduction = QLabel(
            "Combat Focus influences how often the adventure presents fights. "
            "Combat Resolution determines whether fights use the deterministic "
            "Combat tab or remain part of Gemini's narration."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        layout.addWidget(content)
        layout.addStretch()
        page.setLayout(layout)

        self.combat_resolution_mode_combo.currentIndexChanged.connect(
            self._sync_combat_explanation
        )
        self._sync_combat_explanation()
        self.addPage(page)

    def _combat_setup_from_controls(self) -> dict[str, str]:
        """Serializes the Wizard's combat preferences."""

        return {
            "focus": str(self.combat_focus_combo.currentData() or "balanced"),
            "resolution_mode": str(
                self.combat_resolution_mode_combo.currentData() or "strict"
            ),
        }

    def _load_combat_setup(self, combat: dict[str, Any]) -> None:
        """Loads normalized combat preferences into Wizard controls."""

        _set_combo_to_data(
            self.combat_focus_combo,
            str(combat.get("focus", "balanced")),
        )
        _set_combo_to_data(
            self.combat_resolution_mode_combo,
            str(combat.get("resolution_mode", "strict")),
        )
        self._sync_combat_explanation()

    def _sync_combat_explanation(self, _value: Any = None) -> None:
        """Explains the behavior controlled by the selected resolution mode."""

        mode = str(self.combat_resolution_mode_combo.currentData() or "strict")
        if mode == "narrative":
            explanation = (
                "Gemini narrates and resolves fights as part of the story. It will "
                "not start the deterministic Combat tab or use CombatStartedEvent."
            )
        else:
            explanation = (
                "When a fight begins, Gemini hands it to the deterministic Combat "
                "tab. Python controls initiative, attacks, damage, victory, and loot."
            )
        self.combat_resolution_explanation.setText(explanation)

    def _build_inventory_currency_page(self) -> None:
        """Builds the starter inventory and currency page."""

        page = QWizardPage()
        page.setTitle("Inventory and Currency")
        page.setSubTitle(
            "Add requested starter items, define the world's money, and choose "
            "the Player's starting wealth."
        )

        self.starter_items_table = _AppTableWidget(0, 7)
        self.starter_items_table.setHorizontalHeaderLabels(
            ["Name", "Amount", "Category", "Description", "Value", "Storage", "Remove"]
        )
        self.starter_items_table.setMinimumHeight(170)
        self.starter_items_table.verticalHeader().setVisible(False)
        self.starter_items_table.verticalHeader().setDefaultSectionSize(36)
        self.starter_items_table.horizontalHeader().setStretchLastSection(False)
        self.starter_items_table.setAlternatingRowColors(True)
        self.starter_items_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.starter_items_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _set_table_column_widths(self.starter_items_table, STARTER_ITEM_COLUMN_WIDTHS)
        _configure_responsive_table(
            self.starter_items_table,
            stretch_columns={0, 2, 3, 5},
            compact_columns={1, 4, 6},
        )

        add_item_button = QPushButton("Add Item")
        add_item_button.clicked.connect(lambda: self._append_starter_item_row({}))

        self.starter_item_suggestions_table = _build_starter_suggestion_table("Item")
        add_item_suggestion_button = QPushButton("Add Item Idea")
        add_item_suggestion_button.clicked.connect(
            lambda: _append_starter_suggestion_table_row(
                self.starter_item_suggestions_table, "Item"
            )
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
        _configure_responsive_table(
            self.starter_weapons_table,
            stretch_columns={0, 3, 4, 5, 6},
            compact_columns={1, 2, 7, 8},
        )

        add_weapon_button = QPushButton("Add Weapon")
        add_weapon_button.clicked.connect(lambda: self._append_starter_weapon_row({}))

        self.starter_weapon_suggestions_table = _build_starter_suggestion_table("Weapon")
        add_weapon_suggestion_button = QPushButton("Add Weapon Idea")
        add_weapon_suggestion_button.clicked.connect(
            lambda: _append_starter_suggestion_table_row(
                self.starter_weapon_suggestions_table, "Weapon"
            )
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
        _configure_responsive_table(
            self.starter_armor_table,
            stretch_columns={0, 2},
            compact_columns={1, 3, 4, 5},
        )

        add_armor_button = QPushButton("Add Armor")
        add_armor_button.clicked.connect(lambda: self._append_starter_armor_row({}))

        self.starter_armor_suggestions_table = _build_starter_suggestion_table("Armor")
        add_armor_suggestion_button = QPushButton("Add Armor Idea")
        add_armor_suggestion_button.clicked.connect(
            lambda: _append_starter_suggestion_table_row(
                self.starter_armor_suggestions_table, "Armor"
            )
        )

        self.currency_table = _AppTableWidget(0, 4)
        self.currency_table.setHorizontalHeaderLabels(["Name", "Plural Name", "Base Value", "Remove"])
        self.currency_table.setMinimumHeight(180)
        self.currency_table.verticalHeader().setVisible(False)
        self.currency_table.verticalHeader().setDefaultSectionSize(36)
        self.currency_table.horizontalHeader().setStretchLastSection(False)
        self.currency_table.setAlternatingRowColors(True)
        self.currency_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.currency_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _set_table_column_widths(self.currency_table, CURRENCY_COLUMN_WIDTHS)
        _configure_responsive_table(
            self.currency_table,
            stretch_columns={0, 1},
            compact_columns={2, 3},
        )

        add_currency_button = QPushButton("Add Currency")
        add_currency_button.clicked.connect(lambda: self._append_currency_row({}))

        self.economy_examples_table = _AppTableWidget(0, 3)
        self.economy_examples_table.setHorizontalHeaderLabels(["Item", "Base Units", "Remove"])
        _configure_inline_table(
            self.economy_examples_table,
            ECONOMY_EXAMPLE_COLUMN_WIDTHS,
            minimum_height=140,
        )
        _configure_responsive_table(
            self.economy_examples_table,
            stretch_columns={0},
            compact_columns={1, 2},
        )

        add_economy_example_button = QPushButton("Add Economy Item")
        add_economy_example_button.clicked.connect(
            lambda: self._append_economy_example_row({})
        )

        self.starting_wealth_basic_button = QPushButton("Basic")
        self.starting_wealth_advanced_button = QPushButton("Advanced")
        self.starting_wealth_mode_buttons = QButtonGroup(self)
        self.starting_wealth_mode_buttons.setExclusive(True)
        self.starting_wealth_mode_buttons.addButton(
            self.starting_wealth_basic_button, 0
        )
        self.starting_wealth_mode_buttons.addButton(
            self.starting_wealth_advanced_button, 1
        )
        for mode_button in (
            self.starting_wealth_basic_button,
            self.starting_wealth_advanced_button,
        ):
            mode_button.setObjectName("startingWealthModeButton")
            mode_button.setCheckable(True)
        self.starting_wealth_basic_button.setChecked(True)

        self.starting_wealth_guidance_input = QPlainTextEdit()
        self.starting_wealth_guidance_input.setPlaceholderText(
            DEFAULT_STARTING_WEALTH_GUIDANCE
        )
        self.starting_wealth_guidance_input.setPlainText(
            DEFAULT_STARTING_WEALTH_GUIDANCE
        )
        self.starting_wealth_guidance_input.setMinimumHeight(85)

        basic_wealth_layout = QFormLayout()
        _configure_responsive_form(basic_wealth_layout)
        basic_wealth_layout.addRow(
            "Starting Wealth Guidance:",
            self.starting_wealth_guidance_input,
        )
        basic_wealth_widget = QWidget()
        basic_wealth_widget.setLayout(basic_wealth_layout)

        self.starting_wealth_amounts_table = _AppTableWidget(0, 3)
        self.starting_wealth_amounts_table.setHorizontalHeaderLabels(
            ["Currency", "Amount", "Remove"]
        )
        _configure_inline_table(
            self.starting_wealth_amounts_table,
            STARTING_WEALTH_COLUMN_WIDTHS,
            minimum_height=140,
        )
        _configure_responsive_table(
            self.starting_wealth_amounts_table,
            stretch_columns={0},
            compact_columns={1, 2},
        )
        self.add_starting_wealth_amount_button = QPushButton(
            "Add Starting Currency Amount"
        )
        self.add_starting_wealth_amount_button.clicked.connect(
            lambda: self._append_starting_wealth_amount_row({})
        )
        self.starting_wealth_summary_label = QLabel("Total: 0 base units")
        self.starting_wealth_summary_label.setWordWrap(True)

        advanced_wealth_layout = QFormLayout()
        _configure_responsive_form(advanced_wealth_layout)
        advanced_wealth_layout.addRow(
            "Starting Currency:", self.starting_wealth_amounts_table
        )
        advanced_wealth_layout.addRow(
            "", _button_row(self.add_starting_wealth_amount_button)
        )
        advanced_wealth_layout.addRow("", self.starting_wealth_summary_label)
        advanced_wealth_widget = QWidget()
        advanced_wealth_widget.setLayout(advanced_wealth_layout)

        self.starting_wealth_mode_stack = QStackedWidget()
        self.starting_wealth_mode_stack.addWidget(basic_wealth_widget)
        self.starting_wealth_mode_stack.addWidget(advanced_wealth_widget)
        self.starting_wealth_mode_buttons.idClicked.connect(
            lambda index: self._set_starting_wealth_mode(
                "advanced" if index == 1 else "basic"
            )
        )

        self.starter_inventory_basic_button = QPushButton("Basic")
        self.starter_inventory_advanced_button = QPushButton("Advanced")
        self.starter_inventory_mode_buttons = QButtonGroup(self)
        self.starter_inventory_mode_buttons.setExclusive(True)
        self.starter_inventory_mode_buttons.addButton(
            self.starter_inventory_basic_button, 0
        )
        self.starter_inventory_mode_buttons.addButton(
            self.starter_inventory_advanced_button, 1
        )
        for mode_button in (
            self.starter_inventory_basic_button,
            self.starter_inventory_advanced_button,
        ):
            mode_button.setObjectName("starterInventoryModeButton")
            mode_button.setCheckable(True)
        self.starter_inventory_basic_button.setChecked(True)

        basic_inventory_layout = QFormLayout()
        _configure_responsive_form(basic_inventory_layout)
        for suggestion_table in (
            self.starter_item_suggestions_table,
            self.starter_weapon_suggestions_table,
            self.starter_armor_suggestions_table,
        ):
            suggestion_table.setMaximumHeight(16777215)
            _configure_responsive_table(
                suggestion_table,
                stretch_columns={0},
                compact_columns={1},
            )
        basic_inventory_layout.addRow("Items:", self.starter_item_suggestions_table)
        basic_inventory_layout.addRow("", _button_row(add_item_suggestion_button))
        basic_inventory_layout.addRow("Weapons:", self.starter_weapon_suggestions_table)
        basic_inventory_layout.addRow("", _button_row(add_weapon_suggestion_button))
        basic_inventory_layout.addRow("Armor:", self.starter_armor_suggestions_table)
        basic_inventory_layout.addRow("", _button_row(add_armor_suggestion_button))
        basic_inventory_widget = QWidget()
        basic_inventory_widget.setLayout(basic_inventory_layout)

        advanced_inventory_layout = QFormLayout()
        _configure_responsive_form(advanced_inventory_layout)
        advanced_inventory_layout.addRow("Items:", self.starter_items_table)
        advanced_inventory_layout.addRow("", _button_row(add_item_button))
        advanced_inventory_layout.addRow("Weapons:", self.starter_weapons_table)
        advanced_inventory_layout.addRow("", _button_row(add_weapon_button))
        advanced_inventory_layout.addRow("Armor:", self.starter_armor_table)
        advanced_inventory_layout.addRow("", _button_row(add_armor_button))
        advanced_inventory_widget = QWidget()
        advanced_inventory_widget.setLayout(advanced_inventory_layout)

        self.starter_inventory_mode_stack = QStackedWidget()
        self.starter_inventory_mode_stack.addWidget(basic_inventory_widget)
        self.starter_inventory_mode_stack.addWidget(advanced_inventory_widget)
        self.starter_inventory_mode_buttons.idClicked.connect(
            self.starter_inventory_mode_stack.setCurrentIndex
        )

        layout = QFormLayout()
        _configure_responsive_form(layout)
        layout.addRow(self.starter_inventory_mode_stack)
        layout.addRow("Currencies:", self.currency_table)
        layout.addRow("", _button_row(add_currency_button))
        layout.addRow("Economy Notes:", self.economy_examples_table)
        layout.addRow("", _button_row(add_economy_example_button))
        wealth_mode_label = QLabel(
            "Starting wealth: Basic lets the A.I. interpret plain-language "
            "guidance; Advanced stores the exact denomination counts below."
        )
        wealth_mode_label.setWordWrap(True)
        layout.addRow(wealth_mode_label)
        layout.addRow(
            _button_row(
                self.starting_wealth_basic_button,
                self.starting_wealth_advanced_button,
            )
        )
        layout.addRow(self.starting_wealth_mode_stack)

        content = QWidget()
        content.setLayout(layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setWidget(content)

        page_layout = QVBoxLayout()
        mode_label = QLabel(
            "Starter equipment detail: Basic lets the A.I. develop your ideas; "
            "Advanced lets you enter exact values."
        )
        mode_label.setWordWrap(True)
        page_layout.addWidget(mode_label)
        page_layout.addWidget(
            _button_row(
                self.starter_inventory_basic_button,
                self.starter_inventory_advanced_button,
            )
        )
        page_layout.addWidget(scroll_area)
        page.setLayout(page_layout)

        self._sync_starting_wealth_currency_options()
        self.addPage(page)

    def _build_audio_page(self) -> None:
        """Builds the starting audio preferences page."""

        page = QWizardPage()
        page.setTitle("Audio")
        page.setSubTitle(
            "Choose music, background ambience, and narration sound-effect preferences."
        )

        self.music_enabled_checkbox = QCheckBox("Music enabled")
        self.music_enabled_checkbox.setChecked(bool(self.audio_defaults["music_enabled"]))

        self.music_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_volume_slider.setRange(0, 100)
        self.music_volume_slider.setValue(int(self.audio_defaults["music_volume"]))
        self.music_volume_label = QLabel(f"{self.music_volume_slider.value()}%")
        self.music_volume_slider.valueChanged.connect(
            lambda value: self.music_volume_label.setText(f"{value}%")
        )
        self.sound_effects_enabled_checkbox = QCheckBox("Sound effects enabled")
        self.sound_effects_enabled_checkbox.setChecked(
            bool(self.audio_defaults["sound_effects_enabled"])
        )
        self.sound_effects_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.sound_effects_volume_slider.setRange(0, 100)
        self.sound_effects_volume_slider.setValue(
            int(self.audio_defaults["sound_effects_volume"])
        )
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
            bool(self.audio_defaults["background_ambience_enabled"])
        )
        self.background_ambience_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.background_ambience_volume_slider.setRange(0, 100)
        self.background_ambience_volume_slider.setValue(
            int(self.audio_defaults["background_ambience_volume"])
        )
        self.background_ambience_volume_label = QLabel(
            f"{self.background_ambience_volume_slider.value()}%"
        )
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

        layout = QFormLayout()
        _configure_responsive_form(layout)
        layout.addRow("Background Music:", self.music_enabled_checkbox)
        layout.addRow("Music Volume:", _slider_row(self.music_volume_slider, self.music_volume_label))
        layout.addRow("Music Preview:", self.music_test_button)
        layout.addRow("Narration Sound Effects:", self.sound_effects_enabled_checkbox)
        layout.addRow(
            "Sound Effects Volume:",
            _slider_row(
                self.sound_effects_volume_slider,
                self.sound_effects_volume_label,
            ),
        )
        layout.addRow("Sound Effects Preview:", self.sound_effects_test_button)
        layout.addRow(
            "Background Ambience:",
            self.background_ambience_enabled_checkbox,
        )
        layout.addRow(
            "Ambience Volume:",
            _slider_row(
                self.background_ambience_volume_slider,
                self.background_ambience_volume_label,
            ),
        )
        layout.addRow("Ambience Preview:", self.background_ambience_test_button)

        page.setLayout(layout)

        self.addPage(page)

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

    def _build_tts_page(self) -> None:
        """Builds the dedicated starting TTS preferences page."""

        page = QWizardPage()
        page.setTitle("TTS")
        page.setSubTitle("Choose narrator speed, voice, and custom blends before the save starts.")

        self.tts_settings_widget = TTSSettingsWidget(
            audio_settings=self.audio_defaults,
            voice_options=self.voice_options,
            on_sample_voice=self._sample_voice,
            on_custom_voice_saved=self.on_tts_settings_saved,
            custom_voice_storage_path=self.custom_voice_storage_path,
        )
        self.narrator_enabled_checkbox = self.tts_settings_widget.narrator_enabled_checkbox
        self.tts_volume_slider = self.tts_settings_widget.tts_volume_slider
        self.tts_volume_label = self.tts_settings_widget.tts_volume_label
        self.tts_speed_slider = self.tts_settings_widget.tts_speed_slider
        self.tts_voice_combo = self.tts_settings_widget.tts_voice_combo
        self.sample_voice_button = self.tts_settings_widget.sample_voice_button

        layout = QVBoxLayout()
        layout.addWidget(self.tts_settings_widget)
        page.setLayout(layout)

        self.addPage(page)

    def _tts_volume_value(self) -> int:
        """Returns the requested new-game narrator volume."""

        if self.tts_volume_slider is None:
            return 0

        return self.tts_volume_slider.value()

    def _tts_voice_value(self) -> str:
        """Returns the selected new-game narrator voice id."""

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
        """Plays the selected narrator voice sample."""

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
        """Returns new-game TTS settings."""

        if self.tts_settings_widget is None:
            return normalize_tts_audio_fields({}, tts_enabled=False)

        return self.tts_settings_widget.build_audio_settings()

    def _append_starter_item_row(self, item: dict[str, Any]) -> None:
        """Adds a starter item row to the wizard table."""

        _append_starter_item_table_row(
            self.starter_items_table,
            item,
            self._remove_starter_item_row,
        )

    def _remove_starter_item_row(self, button: QPushButton) -> None:
        """Removes the starter item row containing button."""

        _remove_table_row_by_button(self.starter_items_table, button)

    def _starter_items_from_table(self) -> list[dict[str, Any]]:
        """Reads starter item rows from the wizard table."""

        if self._starter_inventory_mode() == "advanced":
            return [
                *_starter_items_from_table(self.starter_items_table),
                *_starter_weapons_from_table(self.starter_weapons_table),
                *_starter_armor_from_table(self.starter_armor_table),
            ]

        return [
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

    def _starter_inventory_mode(self) -> str:
        """Returns the selected Basic/Advanced starter-equipment mode."""

        return (
            "advanced"
            if self.starter_inventory_advanced_button.isChecked()
            else "basic"
        )

    def _set_starter_inventory_mode(self, mode: str) -> None:
        """Selects the starter-equipment mode and matching editor page."""

        is_advanced = str(mode).casefold() == "advanced"
        self.starter_inventory_advanced_button.setChecked(is_advanced)
        self.starter_inventory_basic_button.setChecked(not is_advanced)
        self.starter_inventory_mode_stack.setCurrentIndex(1 if is_advanced else 0)

    def _append_starter_weapon_row(self, item: dict[str, Any]) -> None:
        """Adds a starter weapon row to the wizard table."""

        _append_starter_weapon_table_row(
            self.starter_weapons_table,
            item,
            self._remove_starter_weapon_row,
        )

    def _remove_starter_weapon_row(self, button: QPushButton) -> None:
        """Removes the starter weapon row containing button."""

        _remove_table_row_by_button(self.starter_weapons_table, button)

    def _append_starter_armor_row(self, item: dict[str, Any]) -> None:
        """Adds a starter armor row to the wizard table."""

        _append_starter_armor_table_row(
            self.starter_armor_table,
            item,
            self._remove_starter_armor_row,
        )

    def _remove_starter_armor_row(self, button: QPushButton) -> None:
        """Removes the starter armor row containing button."""

        _remove_table_row_by_button(self.starter_armor_table, button)

    def _append_currency_row(self, denomination: dict[str, Any]) -> None:
        """Adds a currency denomination row to the wizard table."""

        _append_currency_table_row(
            self.currency_table,
            denomination,
            self._remove_currency_row,
        )

        row = self.currency_table.rowCount() - 1
        name_input = self.currency_table.cellWidget(row, 0)
        value_input = self.currency_table.cellWidget(row, 2)
        if isinstance(name_input, QLineEdit):
            name_input.textChanged.connect(self._sync_starting_wealth_currency_options)
        if isinstance(value_input, QSpinBox):
            value_input.valueChanged.connect(self._sync_starting_wealth_currency_options)
        self._sync_starting_wealth_currency_options()

    def _remove_currency_row(self, button: QPushButton) -> None:
        """Removes the currency row containing button."""

        if _row_for_cell_widget(self.currency_table, button) == 0:
            return

        if _remove_table_row_by_button(self.currency_table, button) >= 0:
            self._sync_currency_base_value_row()
            self._sync_starting_wealth_currency_options()

    def _sync_currency_base_value_row(self) -> None:
        """Keeps the first visible currency row as the baseline denomination."""

        _sync_currency_base_value_row(self.currency_table)

    def _currency_denominations_from_table(self) -> list[dict[str, Any]]:
        """Reads currency denomination rows from the wizard table."""

        return _currency_denominations_from_table(self.currency_table)

    def _starting_wealth_mode(self) -> str:
        """Returns the selected Basic/Advanced starting-wealth mode."""

        return (
            "advanced"
            if self.starting_wealth_advanced_button.isChecked()
            else "basic"
        )

    def _set_starting_wealth_mode(self, mode: str) -> None:
        """Selects the starting-wealth mode and matching editor page."""

        is_advanced = str(mode).casefold() == "advanced"
        self.starting_wealth_advanced_button.setChecked(is_advanced)
        self.starting_wealth_basic_button.setChecked(not is_advanced)
        self.starting_wealth_mode_stack.setCurrentIndex(1 if is_advanced else 0)

    def _append_starting_wealth_amount_row(
        self,
        amount: dict[str, Any],
    ) -> None:
        """Adds one exact denomination/count row to starting wealth."""

        row = self.starting_wealth_amounts_table.rowCount()
        self.starting_wealth_amounts_table.insertRow(row)
        self.starting_wealth_amounts_table.setRowHeight(row, 36)
        denomination_combo = _NoWheelComboBox()
        denomination_combo.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
        denomination_combo.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
        quantity_input = _table_spin_box(0, 1_000_000_000)
        quantity_input.setValue(_safe_int(amount.get("quantity"), 0))
        self.starting_wealth_amounts_table.setCellWidget(
            row, 0, denomination_combo
        )
        self.starting_wealth_amounts_table.setCellWidget(row, 1, quantity_input)
        _set_remove_row_button(
            self.starting_wealth_amounts_table,
            row,
            2,
            "starting currency amount",
            self._remove_starting_wealth_amount_row,
        )
        denomination_combo.setProperty(
            "requestedDenominationValue",
            _safe_int(amount.get("denomination_value"), 0),
        )
        denomination_combo.setProperty(
            "requestedDenominationName",
            str(amount.get("denomination_name", "")),
        )
        denomination_combo.currentIndexChanged.connect(
            self._sync_starting_wealth_summary
        )
        quantity_input.valueChanged.connect(self._sync_starting_wealth_summary)
        self._sync_starting_wealth_currency_options()

    def _remove_starting_wealth_amount_row(self, button: QPushButton) -> None:
        """Removes one exact starting-currency amount row."""

        _remove_table_row_by_button(self.starting_wealth_amounts_table, button)
        self._sync_starting_wealth_summary()

    def _sync_starting_wealth_currency_options(self, _value: Any = None) -> None:
        """Keeps exact-wealth dropdowns aligned with valid currency rows."""

        if not hasattr(self, "starting_wealth_amounts_table"):
            return
        denominations = self._currency_denominations_from_table()
        for row in range(self.starting_wealth_amounts_table.rowCount()):
            combo = self.starting_wealth_amounts_table.cellWidget(row, 0)
            if not isinstance(combo, QComboBox):
                continue
            selected_value = _safe_int(combo.currentData(), 0)
            if selected_value <= 0:
                selected_value = _safe_int(
                    combo.property("requestedDenominationValue"), 0
                )
            requested_name = str(
                combo.property("requestedDenominationName") or ""
            ).strip().casefold()
            combo.blockSignals(True)
            combo.clear()
            for denomination in denominations:
                combo.addItem(
                    str(denomination["name"]),
                    int(denomination["value"]),
                )
            selected_index = combo.findData(selected_value)
            if selected_index < 0 and requested_name:
                for index in range(combo.count()):
                    if combo.itemText(index).strip().casefold() == requested_name:
                        selected_index = index
                        break
            combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            combo.setEnabled(bool(denominations))
            combo.setProperty("requestedDenominationValue", 0)
            combo.setProperty("requestedDenominationName", "")
            combo.blockSignals(False)
        self.add_starting_wealth_amount_button.setEnabled(bool(denominations))
        self._sync_starting_wealth_summary()

    def _starting_wealth_from_controls(self) -> dict[str, Any]:
        """Reads the player-facing starting-wealth controls."""

        amounts: list[dict[str, Any]] = []
        for row in range(self.starting_wealth_amounts_table.rowCount()):
            combo = self.starting_wealth_amounts_table.cellWidget(row, 0)
            quantity_input = self.starting_wealth_amounts_table.cellWidget(row, 1)
            if not isinstance(combo, QComboBox) or not isinstance(
                quantity_input, QSpinBox
            ):
                continue
            denomination_value = _safe_int(combo.currentData(), 0)
            if denomination_value <= 0:
                continue
            amounts.append(
                {
                    "denomination_name": combo.currentText().strip(),
                    "denomination_value": denomination_value,
                    "quantity": quantity_input.value(),
                }
            )
        return {
            "mode": self._starting_wealth_mode(),
            "guidance": self.starting_wealth_guidance_input.toPlainText(),
            "amounts": amounts,
        }

    def _load_starting_wealth(self, wealth: dict[str, Any]) -> None:
        """Loads a normalized starting-wealth contract into the Wizard."""

        self.starting_wealth_guidance_input.setPlainText(
            str(wealth.get("guidance") or DEFAULT_STARTING_WEALTH_GUIDANCE)
        )
        self.starting_wealth_amounts_table.setRowCount(0)
        for amount in wealth.get("amounts", []):
            if isinstance(amount, dict):
                self._append_starting_wealth_amount_row(amount)
        self._set_starting_wealth_mode(str(wealth.get("mode", "basic")))
        self._sync_starting_wealth_currency_options()

    def _sync_starting_wealth_summary(self, _value: Any = None) -> None:
        """Displays the exact base-unit total represented by Advanced rows."""

        if not hasattr(self, "starting_wealth_summary_label"):
            return
        total = sum(
            int(amount["denomination_value"]) * int(amount["quantity"])
            for amount in self._starting_wealth_from_controls()["amounts"]
        )
        denominations = self._currency_denominations_from_table()
        formatted = (
            format_currency_amount(total, denominations)
            if denominations
            else "0 base units"
        )
        self.starting_wealth_summary_label.setText(
            f"Total: {formatted} ({total} base units)"
        )

    def _append_economy_example_row(self, example: dict[str, Any]) -> None:
        """Adds a common-price example row to the wizard table."""

        _append_economy_example_table_row(
            self.economy_examples_table,
            example,
            self._remove_economy_example_row,
        )

    def _remove_economy_example_row(self, button: QPushButton) -> None:
        """Removes the common-price example row containing button."""

        _remove_table_row_by_button(self.economy_examples_table, button)

    def _economy_examples_from_table(self) -> list[dict[str, Any]]:
        """Reads common-price examples from the wizard table."""

        return _economy_examples_from_table(self.economy_examples_table)

    def _build_calendar_page(self) -> None:
        """Builds the calendar and time page."""

        page = QWizardPage()
        page.setTitle("Calendar and Time")
        page.setSubTitle("Choose whether to use a standard, custom, or AI-generated calendar.")

        self.calendar_type_combo = _NoWheelComboBox()
        self.calendar_type_combo.addItem("Default Gregorian Calendar", "gregorian")
        self.calendar_type_combo.addItem("Custom Calendar", "custom")
        self.calendar_type_combo.addItem("AI-Generated Calendar", "ai_generated")
        self.calendar_type_combo.currentIndexChanged.connect(
            lambda _index: self._sync_calendar_settings_button()
        )

        self.calendar_start_season_input = QLineEdit()
        self.calendar_start_season_input.setPlaceholderText(
            "Optional season name, such as Spring or Autumn"
        )
        self.calendar_start_year_input = QSpinBox()
        self.calendar_start_year_input.setRange(0, 9999)
        self.calendar_start_year_input.setSpecialValueText("Use calendar default")
        self.calendar_start_month_input = QSpinBox()
        self.calendar_start_month_input.setRange(0, 24)
        self.calendar_start_month_input.setSpecialValueText("Use calendar default")
        self.calendar_start_day_input = QSpinBox()
        self.calendar_start_day_input.setRange(0, 366)
        self.calendar_start_day_input.setSpecialValueText("Use calendar default")
        self.calendar_start_day_input.setValue(0)
        self.calendar_start_time_checkbox = QCheckBox("Specify an exact starting time")
        self.calendar_start_time_checkbox.toggled.connect(
            lambda checked: self.calendar_start_time_input.setVisible(checked)
        )
        self.calendar_start_time_input = QTimeEdit(QTime(8, 0))
        self.calendar_start_time_input.setDisplayFormat("h:mm AP")
        self.calendar_start_time_input.setVisible(False)
        self.calendar_start_weather_input = QLineEdit()
        self.calendar_start_weather_input.setPlaceholderText(
            "Optional exact weather, such as Clear, Rain, Snow, or Fog"
        )

        self.calendar_generation_guidance_label = QLabel("AI Calendar Details:")
        self.calendar_generation_guidance_input = QTextEdit()
        self.calendar_generation_guidance_input.setPlaceholderText(
            "Optional: requested month names, starting season/day, calendar traditions, "
            "or other details for the A.I. to honor."
        )
        self.calendar_generation_guidance_input.setMinimumHeight(90)

        self.calendar_settings_button = QPushButton("Calendar Settings...")
        self.calendar_settings_button.clicked.connect(self._open_wizard_calendar_settings)
        self.calendar_settings_label = QLabel("Custom Settings:")

        layout = QFormLayout()
        _configure_responsive_form(layout)
        layout.addRow("Calendar:", self.calendar_type_combo)
        layout.addRow("Starting Season:", self.calendar_start_season_input)
        layout.addRow("Starting Year:", self.calendar_start_year_input)
        layout.addRow("Starting Month:", self.calendar_start_month_input)
        layout.addRow("Starting Day of Month:", self.calendar_start_day_input)
        layout.addRow("Starting Time:", self.calendar_start_time_checkbox)
        layout.addRow("", self.calendar_start_time_input)
        layout.addRow("Starting Weather:", self.calendar_start_weather_input)
        layout.addRow(self.calendar_settings_label, self.calendar_settings_button)
        layout.addRow(
            self.calendar_generation_guidance_label,
            self.calendar_generation_guidance_input,
        )
        page.setLayout(layout)

        self.addPage(page)
        self._sync_calendar_settings_button()

    def _calendar_settings_for_setup(self, calendar_type: str) -> dict[str, Any]:
        """Returns wizard calendar settings for the selected calendar mode."""

        if calendar_type == "ai_generated":
            return {
                **GREGORIAN_CALENDAR_SETTINGS,
                "calendar_type": "ai_generated",
                "ai_generated": True,
                "generation_guidance": self.calendar_generation_guidance_input.toPlainText().strip(),
            }

        if calendar_type == "custom":
            return {
                **self._custom_calendar_settings,
                "calendar_type": "custom",
                "ai_generated": False,
            }

        return {
            **GREGORIAN_CALENDAR_SETTINGS,
            "calendar_type": "gregorian",
            "ai_generated": False,
        }

    def _sync_calendar_settings_button(self) -> None:
        """Shows custom calendar editing only for custom new-game calendars."""

        if not hasattr(self, "calendar_settings_button"):
            return

        is_custom = self.calendar_type_combo.currentData() == "custom"
        self.calendar_settings_label.setVisible(is_custom)
        self.calendar_settings_button.setVisible(is_custom)
        is_ai_generated = self.calendar_type_combo.currentData() == "ai_generated"
        self.calendar_generation_guidance_label.setVisible(is_ai_generated)
        self.calendar_generation_guidance_input.setVisible(is_ai_generated)

    def _open_wizard_calendar_settings(self) -> None:
        """Opens the shared calendar settings dialog for the custom wizard calendar."""

        dialog = _main_window_override("CalendarSettingsDialog", CalendarSettingsDialog)(self._custom_calendar_settings, self)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._custom_calendar_settings = dialog.build_settings()
