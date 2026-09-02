from __future__ import annotations

from copy import deepcopy
import json
import re
import logging
import importlib
import random
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar, cast

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QSize,
    QStringListModel,
    Qt,
    QThread,
    QTime,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QIcon,
    QMouseEvent,
    QPalette,
    QPixmap,
    QResizeEvent,
    QStandardItem,
    QStandardItemModel,
    QTextCursor,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QHeaderView,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ai_adventure.alchemy.ingredients import (
    COMMON_MEASUREMENT_UNITS,
    CRAFTING_INGREDIENT_CATEGORY_NAMES,
    CRAFTING_INGREDIENT_CATEGORIES,
    CRAFTING_ITEM_RARITIES,
    format_recipe_ingredients,
    is_crafting_ingredient_category,
    normalize_recipe_ingredient,
    normalize_recipe_ingredients,
)
from ai_adventure.app.api_key_store import (
    read_api_key,
    record_terms_acceptance,
    write_api_key,
)
from ai_adventure.app.app_paths import AppPaths
from ai_adventure.app.features import (
    is_ai_enabled,
    is_playtesting_build,
    is_tts_enabled,
)
from ai_adventure.app.user_settings import (
    load_app_settings,
    normalize_app_settings,
    save_app_settings,
)
from ai_adventure.ai.modes import (
    ALL_CONTENT_HARM_CATEGORIES,
    CONTENT_HARM_CATEGORY_OPTIONS,
    DEFAULT_ALLOWED_CONTENT_HARM_CATEGORIES,
    DEFAULT_MODEL_INTELLIGENCE,
    DEFAULT_MODEL_TONE,
    DEFAULT_RESPONSE_LENGTH,
    MODEL_INTELLIGENCE_OPTIONS,
    MODEL_TONE_OPTIONS,
    RESPONSE_LENGTH_OPTIONS,
    normalize_ai_mode_preferences,
)
from ai_adventure.ai.model_catalog import (
    DEFAULT_IMAGE_MODEL,
    IMAGE_MODEL_OPTIONS,
    MODEL_CATALOG_REVIEWED_DATE,
    TEXT_MODEL_OPTIONS,
    image_model_metadata,
    normalize_image_model,
    normalize_image_preferences,
    normalize_text_model,
    text_model_metadata,
)
from ai_adventure.ai.image_styles import (
    IMAGE_STYLE_OPTIONS,
    image_style_metadata,
)
from ai_adventure.inventory_sorting import sort_inventory_items
from ai_adventure.ui.story_bubbles import split_story_bubble_segments
from ai_adventure.application.asset_generation_service import AssetGenerationService
from ai_adventure.application.audio_preferences_service import AudioPreferencesService
from ai_adventure.application.new_game_service import NewGameService
from ai_adventure.application.save_game_service import SaveGameService
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.domain.rules.values import (
    bool_setting as domain_bool_setting,
    clamped_int as domain_clamped_int,
    safe_int as domain_safe_int,
)
from ai_adventure.domain.services.state_projection import refresh_calendar_time_projection
from ai_adventure.infrastructure.images import (
    DEFAULT_IMAGE_LIMIT,
    GeminiVisualAssetService,
    VisualAssetRequest,
    build_visual_asset_requests,
    find_reusable_inventory_asset,
    save_relative_image_filename,
    save_scaled_jpeg,
)


class SoundManagerProtocol(Protocol):
    """Runtime surface used from the background music manager."""

    def get_valid_track_names(self) -> list[str]: ...

    def get_valid_sound_effect_names(self) -> list[str]: ...

    def get_valid_background_ambience_names(self) -> list[str]: ...

    def set_music_enabled(self, enabled: bool) -> None: ...

    def set_music_volume(self, volume: float | int | None) -> None: ...

    def set_sound_effects_enabled(self, enabled: bool) -> None: ...

    def set_sound_effects_volume(self, volume: float | int | None) -> None: ...

    def set_background_ambience_enabled(self, enabled: bool) -> None: ...

    def set_background_ambience_volume(self, volume: float | int | None) -> None: ...

    def play_music(self, track_name_or_path: str | Path | None) -> None: ...

    def play_music_preview(self, track_name_or_path: str | Path | None) -> None: ...

    def play_sound_effect(self, track_name_or_path: str | Path | None) -> None: ...

    def play_background_ambience(self, track_name_or_path: str | Path | None) -> None: ...

    def stop_music(self, *, clear_current: bool = True) -> None: ...

    def stop_sound_effect(self, *, clear_current: bool = True) -> None: ...

    def stop_background_ambience(self, *, clear_current: bool = True) -> None: ...


class NarrationPlayerProtocol(Protocol):
    """Runtime surface used from the narrator player."""

    def set_enabled(self, enabled: bool) -> None: ...

    def set_volume(self, volume: float | int | None) -> None: ...

    def set_voice(self, voice: str | None) -> None: ...

    def set_speed(self, speed: float | int | None) -> None: ...

    def play_sample(
        self,
        *,
        voice: str | None = None,
        volume: float | int | None = None,
        speed: float | int | None = None,
        text: str = ...,
        sound_effect_cues: list[dict[str, str]] | None = None,
        speaker_cues: list[dict[str, str]] | None = None,
        tts_text_transform: Callable[[str], str] | None = None,
        on_sound_effect: Callable[[str], None] | None = None,
    ) -> bool: ...

    def narrate(
        self,
        text: str,
        *,
        voice: str | None = None,
        sound_effect_cues: list[dict[str, str]] | None = None,
        speaker_cues: list[dict[str, str]] | None = None,
        tts_text_transform: Callable[[str], str] | None = None,
        on_chunk_start: Callable[[str], None] | None = None,
        on_sound_effect: Callable[[str], None] | None = None,
        on_complete: Callable[[], None] | None = None,
    ) -> bool: ...

    def stop(self) -> None: ...

    def get_available_voices(self) -> dict[str, str]: ...


if is_ai_enabled():
    from ai_adventure.infrastructure.gemini import (
        GeminiConfigurationError,
        GeminiNarrationService,
        GeminiRequestError,
        format_story_message,
    )
else:
    GeminiNarrationService = None
    GeminiConfigurationError = RuntimeError
    GeminiRequestError = RuntimeError

    def format_story_message(text: str) -> str:
        """Returns plain story text when the AI presentation layer is excluded."""

        return text


if not is_playtesting_build():
    from ai_adventure.infrastructure.audio import NarrationPlayer as _NarrationPlayerClass
    from ai_adventure.infrastructure.audio import (
        SoundManager as _SoundManagerClass,
        prepare_background_ambience_directory,
        prepare_sound_directory,
        prepare_sound_effect_directory,
    )
else:
    _NarrationPlayerClass = None
    _SoundManagerClass = None

    def prepare_sound_directory(app_paths: Any) -> Path:
        """Returns a harmless placeholder path in the audio-free build."""

        return Path(app_paths.sounds_dir)

    def prepare_sound_effect_directory(app_paths: Any) -> Path:
        """Returns a harmless placeholder effect path in the audio-free build."""

        return Path(app_paths.sound_effects_dir)

    def prepare_background_ambience_directory(app_paths: Any) -> Path:
        """Returns a harmless placeholder ambience path in the audio-free build."""

        return Path(app_paths.background_ambience_dir)
from ai_adventure.audio.tts_settings import (
    DEFAULT_TTS_SPEED_PERCENT,
    active_voice_spec_from_audio,
    merge_custom_voices,
    normalize_custom_voices,
    normalize_narrator_voice_spec,
    normalize_tts_audio_fields,
    normalize_tts_speed_percent,
    normalize_tts_voice_mode,
    normalize_voice_blend,
    voice_display_name,
)
from ai_adventure.audio.pronunciation import (
    PronunciationMap,
    apply_pronunciation_map,
    merge_pronunciation_maps,
    normalize_pronunciation_map,
    set_authoritative_pronunciation,
)
from ai_adventure.audio.voices import (
    DEFAULT_NARRATOR_VOICE,
    assign_speaker_voices,
    available_narrator_voices,
    normalize_narrator_voice,
)
from ai_adventure.calendar_system import (
    DEFAULT_CALENDAR_SETTINGS,
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    build_month_grid,
    format_time_of_day,
    resolve_starting_calendar_minute,
)
from ai_adventure.combat import (
    COMBAT_FOCUS_LABELS,
    COMBAT_FOCUS_LEVELS,
    COMBAT_RESOLUTION_MODE_LABELS,
    COMBAT_RESOLUTION_MODES,
    COMBAT_PERSONALITIES,
    DEFAULT_ATTACK_RANGE_FEET,
    DEFAULT_BASE_ARMOR_RATING,
    DEFAULT_PLAYER_MAX_HEALTH,
    DEFAULT_UNARMED_DAMAGE,
    EQUIPMENT_SLOTS,
    attack_hit_probability,
    attack_bonus_from_skills,
    armor_rating_from_equipment,
    calculate_team_threat_levels,
    combatant_display_name,
    combat_team_defeated,
    empty_equipment,
    equipment_item_counts,
    equipped_weapon_attack_skill,
    equipped_weapon_combat_profile,
    equipped_weapon_damage,
    item_is_valid_for_slot,
    item_metadata,
    next_living_index,
    normalize_combat_preferences,
    normalize_combat_state,
    normalize_damage_expression,
    normalize_equipment,
    roll_combat_initiative,
    roll_damage_expression,
)
from ai_adventure.context.context_builder import AiContextBuilder
from ai_adventure.currency import (
    FALLBACK_CURRENCY_DENOMINATIONS,
    describe_currency_denominations,
    format_currency_amount,
)
from ai_adventure.locations import (
    calculate_travel_estimate,
    format_distance,
    format_travel_time,
    normalize_known_location,
    normalize_known_locations,
)
from ai_adventure.magic import MAGIC_CASTING_MODE_LABELS, MAGIC_CASTING_MODES
from ai_adventure.domain.events import EventApplier
from ai_adventure.domain.services.state_manager import StateManager
from ai_adventure.new_game_setup import (
    CHARACTER_PRONOUN_OPTIONS,
    DEFAULT_CHARACTER_PRONOUNS,
    DEFAULT_STARTING_WEALTH_GUIDANCE,
    GREGORIAN_CALENDAR_SETTINGS,
    SKILL_PRESET_LEVEL_PLANS,
    STARTER_INVENTORY_MIN_ITEMS,
    ai_generated_calendar_settings_or_fallback,
    build_new_game_setup_packet,
    describe_economy_examples,
    fallback_introductory_message,
    fallback_world_summary,
    merge_authoritative_starting_calendar,
    normalize_economy_examples,
    normalize_character_pronouns,
    normalize_new_game_setup,
    parse_starter_items_text,
)
from ai_adventure.narration_preferences import (
    DEFAULT_NARRATION_STYLE,
    DEFAULT_NARRATION_TENSE,
    NARRATION_STYLE_OPTIONS,
    NARRATION_TENSE_OPTIONS,
    normalize_narration_preferences,
)
from ai_adventure.notes import parse_note_tags, prefix_markdown_lines, wrap_markdown_text
from ai_adventure.new_game_templates import (
    NewGameTemplate,
    available_automatic_template_name,
    delete_new_game_template,
    load_new_game_templates,
    save_new_game_template,
    template_setup_has_changes,
)
from ai_adventure.infrastructure.sqlite import (
    DuplicateSaveTitleError,
    SaveFileOperationError,
    SaveRepository,
    SaveSummary,
)
from ai_adventure.skills.rules import MAX_SKILL_LEVEL, XP_THRESHOLDS_BY_LEVEL


LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


class _NoWheelComboBox(QComboBox):
    """Combo box that does not change selection from mouse-wheel scrolling."""

    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class _NoWheelSpinBox(QSpinBox):
    """Spin box that does not change value from mouse-wheel scrolling."""

    def wheelEvent(self, event: Any) -> None:
        event.ignore()




GM_THINKING_FRAMES = (
    "GM is thinking.",
    "GM is thinking..",
    "GM is thinking...",
)
GM_THINKING_TIMER_INTERVAL_MS = 500
UNRESOLVED_STATUS_TEXT = "---"
# Kokoro can spend well over eight seconds on its first local synthesis while
# loading the model and phonemizer.  Keep the fallback as a true failure guard
# instead of racing normal cold-start work.
STORY_REVEAL_STALL_TIMEOUT_MS = 30_000
NPC_TURN_DELAY_MS = 2000
CONTINUE_STORY_INSTRUCTION = (
    "Continue the previous story response as though it had been longer originally. "
    "Do not treat this as a new player action. Do not invent new player-character "
    "dialogue or choices. Add concrete scene detail, NPC reaction, immediate "
    "outcome, obstacles, discoveries, or consequences already implied by the "
    "previous action."
)
TABLE_INLINE_EDITOR_HEIGHT = 30
TABLE_INLINE_EDITOR_MIN_WIDTH = 132
TABLE_CELL_HORIZONTAL_PADDING = 10
TABLE_CELL_VERTICAL_PADDING = 4
STARTER_ITEM_COLUMN_WIDTHS = (140, 132, 140, 220, 132, 150, 100)
STARTER_WEAPON_COLUMN_WIDTHS = (150, 132, 100, 96, 120, 120, 132, 132, 100)
STARTER_ARMOR_COLUMN_WIDTHS = (150, 132, 220, 132, 132, 100)
STARTING_NPC_COLUMN_WIDTHS = (150, 160, 260, 132, 100)
STARTING_LOCATION_COLUMN_WIDTHS = (180, 320, 132, 110, 180, 120)
CURRENCY_COLUMN_WIDTHS = (150, 160, 132, 100)
ECONOMY_EXAMPLE_COLUMN_WIDTHS = (220, 132, 100)
STARTING_WEALTH_COLUMN_WIDTHS = (220, 132, 100)
THEME_NAMES = {"Light", "Dark"}
SampleVoiceCallback = Callable[[str, int, int], bool]
SKILL_LEVEL_DESCRIPTIONS = {
    5: "Signature expertise - the character's strongest, defining capability.",
    4: "Expert - a major specialty the character can rely on often.",
    3: "Skilled - solid professional competence in meaningful situations.",
    2: "Trained - useful practice, but not a primary specialty.",
    1: "Familiar - basic exposure that can still matter in the right moment.",
}


def apply_application_theme(theme: str) -> None:
    """Applies the selected app-wide theme to the active QApplication."""

    app = QApplication.instance()

    if not isinstance(app, QApplication):
        return

    clean_theme = _normalize_theme_name(theme)

    if clean_theme == "Dark":
        app.setPalette(_dark_theme_palette())
        app.setStyleSheet(_dark_theme_stylesheet())
        return

    if clean_theme == "Light":
        app.setPalette(_light_theme_palette())
        app.setStyleSheet(_light_theme_stylesheet())
        return

    app.setPalette(_light_theme_palette())
    app.setStyleSheet(_light_theme_stylesheet())


class _NoCellFocusDelegate(QStyledItemDelegate):
    """Draws data table cells with clean selection and readable padding."""

    def paint(self, painter, option, index) -> None:
        clean_option = QStyleOptionViewItem(option)
        clean_option.state &= ~QStyle.StateFlag.State_HasFocus
        clean_option.rect = clean_option.rect.adjusted(
            TABLE_CELL_HORIZONTAL_PADDING,
            TABLE_CELL_VERTICAL_PADDING,
            -TABLE_CELL_HORIZONTAL_PADDING,
            -TABLE_CELL_VERTICAL_PADDING,
        )
        super().paint(painter, clean_option, index)

    def sizeHint(self, option, index) -> QSize:
        clean_option = QStyleOptionViewItem(option)
        clean_option.rect = clean_option.rect.adjusted(
            TABLE_CELL_HORIZONTAL_PADDING,
            TABLE_CELL_VERTICAL_PADDING,
            -TABLE_CELL_HORIZONTAL_PADDING,
            -TABLE_CELL_VERTICAL_PADDING,
        )
        size = super().sizeHint(clean_option, index)
        return QSize(
            size.width() + (TABLE_CELL_HORIZONTAL_PADDING * 2),
            size.height() + (TABLE_CELL_VERTICAL_PADDING * 2),
        )


def _use_soft_table_selection(table: QTableWidget) -> None:
    """Keeps table selection while hiding the gaudy per-cell focus cursor."""

    table.setItemDelegate(_NoCellFocusDelegate(table))
    _allow_selected_row_deselection(table)


class _DeselectSelectedRowFilter(QObject):
    """Clears a table row when the user clicks its already-selected row."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self.table = table

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            index = self.table.indexAt(event.position().toPoint())
            if index.isValid() and self.table.selectionModel().isRowSelected(
                index.row(), index.parent()
            ):
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
                return True
        return super().eventFilter(watched, event)


def _allow_selected_row_deselection(table: QTableWidget) -> None:
    """Lets a second click on the selected row return the table to no selection."""

    if hasattr(table, "_deselect_selected_row_filter"):
        return
    deselect_filter = _DeselectSelectedRowFilter(table)
    table.viewport().installEventFilter(deselect_filter)
    table._deselect_selected_row_filter = deselect_filter  # type: ignore[attr-defined]


class _TableEditorWheelFilter(QObject):
    """Prevents wheel events from changing editors embedded in tables."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(
            watched, (QComboBox, QAbstractSpinBox)
        ):
            event.ignore()
            return True
        return super().eventFilter(watched, event)


class _AppTableWidget(QTableWidget):
    """Application-wide table defaults and embedded-editor wheel behavior."""

    def __init__(self, rows: int = 0, columns: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(rows, columns, parent)
        self._editor_wheel_filter = _TableEditorWheelFilter(self)
        _use_soft_table_selection(self)

    def setCellWidget(self, row: int, column: int, widget: QWidget) -> None:
        """Installs shared wheel protection on every embedded table editor."""

        super().setCellWidget(row, column, widget)
        editors = [widget, *widget.findChildren(QWidget)]
        for editor in editors:
            if isinstance(editor, (QComboBox, QAbstractSpinBox)):
                editor.installEventFilter(self._editor_wheel_filter)

def _table_item(text: Any, sort_value: Any | None = None) -> QTableWidgetItem:
    """Builds a read-only table item with an optional hidden sort value."""

    display_text = str(text)
    item = QTableWidgetItem(display_text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setToolTip(display_text)

    if sort_value is not None:
        item.setData(Qt.ItemDataRole.UserRole, sort_value)

    return item


def _enable_table_sorting(table: QTableWidget, on_section_clicked) -> None:
    """Makes a data table sortable by clicking its column headers."""

    _use_soft_table_selection(table)
    table.setSortingEnabled(False)
    header = table.horizontalHeader()
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(True)
    header.sectionClicked.connect(on_section_clicked)


def _configure_wrapping_table(
    table: QTableWidget,
    stretch_columns: set[int],
) -> None:
    """Configures a read-only table to wrap long text into taller rows."""

    table.setWordWrap(True)
    table.horizontalHeader().setStretchLastSection(False)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setMinimumSectionSize(28)

    for column_index in range(table.columnCount()):
        resize_mode = (
            QHeaderView.ResizeMode.Stretch
            if column_index in stretch_columns
            else QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setSectionResizeMode(column_index, resize_mode)


def _resize_wrapping_table_rows(table: QTableWidget) -> None:
    """Refreshes row heights after wrapped table content changes."""

    table.resizeRowsToContents()


def _update_sort_state(
    table: QTableWidget,
    current_column: int,
    current_order: Qt.SortOrder,
    clicked_column: int,
) -> tuple[int, Qt.SortOrder]:
    """Returns the next sort column/order and updates the header indicator."""

    if clicked_column == current_column:
        next_order = (
            Qt.SortOrder.DescendingOrder
            if current_order == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
    else:
        next_order = Qt.SortOrder.AscendingOrder

    table.horizontalHeader().setSortIndicator(clicked_column, next_order)
    return clicked_column, next_order


def _sort_descending(order: Qt.SortOrder) -> bool:
    """Returns True when table data should be sorted descending."""

    return order == Qt.SortOrder.DescendingOrder


class RepositoryBackedWidget(QWidget):
    """
    Base widget for screens that need save access.

    This keeps every screen from directly knowing how save loading works.
    """

    def __init__(self) -> None:
        super().__init__()
        self._repository: SaveRepository | None = None
        self._visual_assets_dir: Path | None = None
        self.on_repository_changed: Callable[["RepositoryBackedWidget"], None] | None = None

    def set_visual_assets_dir(self, directory: Path | str) -> None:
        """Sets the device-local cache root used by image-bearing screens."""

        self._visual_assets_dir = Path(directory).expanduser().resolve()

    def visual_asset_path(self, asset: dict[str, Any] | None) -> Path | None:
        """Resolves a safe cached filename beneath the configured images directory."""

        if self._visual_assets_dir is None or not isinstance(asset, dict):
            return None
        filename = Path(str(asset.get("filename", "") or ""))
        if not filename or filename.is_absolute() or ".." in filename.parts:
            return None
        root = self._visual_assets_dir.resolve()
        path = (root / filename).resolve()
        if root not in path.parents and path != root:
            return None
        return path if path.is_file() else None

    def set_repository(self, repository: SaveRepository | None) -> None:
        """
        Sets the active save repository.

        Args:
            repository: Active save repository, or None when no save is loaded.
        """

        self._repository = repository
        self.refresh()

    def repository(self) -> SaveRepository | None:
        """
        Gets the active save repository.

        Returns:
            Active repository, or None if no save is loaded.
        """

        return self._repository

    def refresh(self) -> None:
        """Refreshes screen data. Subclasses may override this."""

    def notify_repository_changed(self) -> None:
        """Notifies the shell that saved data changed and other tabs should refresh."""

        if self.on_repository_changed is not None:
            self.on_repository_changed(self)


def _set_generated_image(
    label: QLabel,
    path: Path | None,
    *,
    maximum_width: int,
    maximum_height: int,
    accessible_name: str = "Generated image",
) -> bool:
    """Loads and scales one cached image without distorting its aspect ratio."""

    if path is None:
        label.clear()
        label.hide()
        return False
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        label.clear()
        label.hide()
        return False
    scaled = pixmap.scaled(
        max(64, maximum_width),
        max(64, maximum_height),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    label.setPixmap(scaled)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setAccessibleName(accessible_name)
    label.setToolTip(accessible_name)
    label.show()
    return True


def _screen_content_signature(screen: QWidget) -> str:
    """Returns a stable signature of player-visible values on one game screen."""

    values: list[Any] = []
    for widget in [screen, *screen.findChildren(QWidget)]:
        identity = (widget.metaObject().className(), widget.objectName())

        if isinstance(widget, QTableWidget):
            cells: list[Any] = []
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    cells.append(
                        None
                        if item is None
                        else (
                            item.text(),
                            item.checkState().value,
                        )
                    )
            values.append((identity, widget.rowCount(), widget.columnCount(), cells))
        elif isinstance(widget, QListWidget):
            values.append(
                (
                    identity,
                    [
                        widget.item(index).text()
                        for index in range(widget.count())
                    ],
                )
            )
        elif isinstance(widget, QComboBox):
            values.append(
                (
                    identity,
                    widget.currentIndex(),
                    [widget.itemText(index) for index in range(widget.count())],
                    widget.isEnabled(),
                    widget.isHidden(),
                )
            )
        elif isinstance(widget, QSpinBox):
            values.append(
                (identity, widget.value(), widget.isEnabled(), widget.isHidden())
            )
        elif isinstance(widget, QSlider):
            values.append(
                (identity, widget.value(), widget.isEnabled(), widget.isHidden())
            )
        elif isinstance(widget, QCheckBox):
            values.append(
                (
                    identity,
                    widget.text(),
                    widget.isChecked(),
                    widget.isEnabled(),
                    widget.isHidden(),
                )
            )
        elif isinstance(widget, QLineEdit):
            values.append(
                (identity, widget.text(), widget.isEnabled(), widget.isHidden())
            )
        elif isinstance(widget, (QTextEdit, QPlainTextEdit)):
            values.append(
                (identity, widget.toPlainText(), widget.isEnabled(), widget.isHidden())
            )
        elif isinstance(widget, QLabel):
            values.append((identity, widget.text(), widget.isHidden()))
        elif isinstance(widget, QGroupBox):
            values.append((identity, widget.title(), widget.isHidden()))
        elif isinstance(widget, QPushButton):
            values.append(
                (
                    identity,
                    widget.text(),
                    widget.isEnabled(),
                    widget.isHidden(),
                )
            )

    return json.dumps(values, sort_keys=True, default=str, separators=(",", ":"))


def _text_model_from_ai_packet(packet: dict[str, Any]) -> str:
    """Reads the per-save text model from a story or new-game packet."""

    preferences: Any = packet.get("player_ai_preferences")
    state = packet.get("state")
    if isinstance(state, dict) and isinstance(state.get("player_ai_preferences"), dict):
        preferences = state["player_ai_preferences"]
    if not isinstance(preferences, dict):
        preferences = {}
    return normalize_text_model(preferences.get("text_model"))


def _create_narration_player(app_paths: AppPaths) -> NarrationPlayerProtocol | None:
    """Creates the narration player only when TTS support is enabled."""

    if not is_tts_enabled():
        LOGGER.info("TTS narration disabled by application configuration.")
        return None

    try:
        tts_module = importlib.import_module("ai_adventure.audio.tts.tts_manager")
        create_tts_manager = getattr(tts_module, "create_tts_manager")
    except Exception as error:
        LOGGER.warning("Narrator is unavailable because TTS could not be loaded: %s", error)
        return None

    if _NarrationPlayerClass is None:
        return None

    return _NarrationPlayerClass(
        create_tts_manager(
            model_path=app_paths.kokoro_model_path,
            voices_path=app_paths.kokoro_voices_path,
            output_directory=app_paths.tts_output_dir,
        )
    )


def _refresh_repository_calendar_time(repository: SaveRepository) -> None:
    """Recomputes the saved display time from current calendar settings."""

    refresh_calendar_time_projection(repository)


def _apply_audio_settings_to_managers(
    repository: SaveRepository,
    *,
    sound_manager: SoundManagerProtocol | None,
    narration_player: NarrationPlayerProtocol | None,
) -> None:
    """Applies saved music, one-shot effect, and narrator settings to managers."""

    AudioPreferencesService.apply(
        repository,
        sound_manager=sound_manager,
        narration_player=narration_player,
    )
    return

    music_enabled = _bool_setting(repository.get_setting("audio.music_enabled", True), True)
    sound_effects_enabled = _bool_setting(
        repository.get_setting("audio.sound_effects_enabled", True),
        True,
    )
    background_ambience_enabled = _bool_setting(
        repository.get_setting("audio.background_ambience_enabled", True),
        True,
    )
    narrator_enabled = _bool_setting(
        repository.get_setting("audio.narrator_enabled", True),
        True,
    )
    music_volume = _clamped_int(repository.get_setting("audio.music_volume", 25), 25, 0, 100)
    sound_effects_volume = _clamped_int(
        repository.get_setting("audio.sound_effects_volume", 35),
        35,
        0,
        100,
    )
    background_ambience_volume = _clamped_int(
        repository.get_setting("audio.background_ambience_volume", 15),
        15,
        0,
        100,
    )
    tts_audio = normalize_tts_audio_fields(
        {
            "narrator_enabled": narrator_enabled,
            "tts_volume": repository.get_setting("audio.tts_volume", 90),
            "tts_voice": repository.get_setting("audio.tts_voice", DEFAULT_NARRATOR_VOICE),
            "tts_speed": repository.get_setting("audio.tts_speed", DEFAULT_TTS_SPEED_PERCENT),
            "tts_voice_mode": repository.get_setting("audio.tts_voice_mode", "preset"),
            "tts_voice_blend": repository.get_setting("audio.tts_voice_blend", {}),
            "tts_custom_voices": repository.get_setting("audio.tts_custom_voices", []),
        }
    )
    tts_volume = int(tts_audio["tts_volume"])
    tts_voice = active_voice_spec_from_audio(tts_audio)
    tts_speed = int(tts_audio["tts_speed"])

    if sound_manager is not None:
        sound_manager.set_music_volume(music_volume)
        sound_manager.set_music_enabled(music_enabled)
        sound_manager.set_sound_effects_volume(sound_effects_volume)
        sound_manager.set_sound_effects_enabled(sound_effects_enabled)
        if hasattr(sound_manager, "set_background_ambience_volume"):
            sound_manager.set_background_ambience_volume(background_ambience_volume)
        if hasattr(sound_manager, "set_background_ambience_enabled"):
            sound_manager.set_background_ambience_enabled(background_ambience_enabled)

        current_music = str(repository.get_setting("audio.current_music", "") or "").strip()

        if music_enabled and current_music:
            sound_manager.play_music(current_music)
        else:
            sound_manager.stop_music(clear_current=False)

        if not sound_effects_enabled:
            sound_manager.stop_sound_effect(clear_current=False)

        current_background_ambience = str(
            repository.get_setting("audio.current_background_ambience", "") or ""
        ).strip()
        if (
            background_ambience_enabled
            and current_background_ambience
            and hasattr(sound_manager, "play_background_ambience")
        ):
            sound_manager.play_background_ambience(current_background_ambience)
        elif hasattr(sound_manager, "stop_background_ambience"):
            sound_manager.stop_background_ambience(clear_current=False)

    if narration_player is not None and hasattr(narration_player, "set_volume"):
        narration_player.set_volume(tts_volume)
    if narration_player is not None and hasattr(narration_player, "set_speed"):
        narration_player.set_speed(tts_speed)
    if narration_player is not None and hasattr(narration_player, "set_voice"):
        narration_player.set_voice(tts_voice)
    if narration_player is not None and hasattr(narration_player, "set_enabled"):
        narration_player.set_enabled(narrator_enabled)


def _resolve_speaker_cues_for_repository(
    repository: SaveRepository,
    narration_player: NarrationPlayerProtocol | None,
    speaker_cues: Any,
) -> list[dict[str, str]]:
    """Assigns installed voices and persists stable speaker-to-voice IDs."""

    tts_audio = normalize_tts_audio_fields(
        {
            "tts_voice": repository.get_setting(
                "audio.tts_voice", DEFAULT_NARRATOR_VOICE
            ),
            "tts_voice_mode": repository.get_setting(
                "audio.tts_voice_mode", "preset"
            ),
            "tts_voice_blend": repository.get_setting(
                "audio.tts_voice_blend", {}
            ),
        }
    )
    voice_options = _narrator_voice_options(narration_player)
    existing_assignments = repository.get_setting(
        "audio.speaker_voice_assignments",
        {},
    )
    resolved, assignments = assign_speaker_voices(
        speaker_cues,
        narrator_voice=active_voice_spec_from_audio(tts_audio),
        available_voice_ids=list(voice_options.values()),
        existing_assignments=existing_assignments,
    )
    if assignments != existing_assignments:
        repository.set_setting("audio.speaker_voice_assignments", assignments)
    return resolved


def _preserved_player_character_fields(
    setup: dict[str, Any],
    ai_character: Any,
) -> dict[str, str]:
    """Returns character fields while preserving explicit player setup values."""

    clean_setup = normalize_new_game_setup(setup)
    setup_character = clean_setup["character"]
    ai_character = ai_character if isinstance(ai_character, dict) else {}
    preserved: dict[str, str] = {}

    for key in (
        "name",
        "name_pronunciation",
        "pronouns",
        "appearance",
        "backstory",
        "notes",
    ):
        setup_value = str(setup_character.get(key, "")).strip()
        ai_value = str(ai_character.get(key, "")).strip()

        if _is_player_provided_character_field(key, setup_value):
            preserved[key] = setup_value
        elif ai_value:
            preserved[key] = ai_value
        elif setup_value:
            preserved[key] = setup_value

    return preserved


def _preserve_player_character_text(
    value: Any,
    setup: dict[str, Any],
    ai_character: Any,
) -> Any:
    """Repairs generated text that renamed an explicitly supplied character."""

    replacements = _player_character_name_replacements(setup, ai_character)

    if not replacements:
        return value

    if isinstance(value, str):
        clean_value = value

        for source, target in replacements:
            clean_value = _replace_whole_name(clean_value, source, target)

        return clean_value

    if isinstance(value, list):
        return [
            _preserve_player_character_text(item, setup, ai_character)
            for item in value
        ]

    if isinstance(value, dict):
        return {
            _preserve_player_character_text(key, setup, ai_character)
            if isinstance(key, str)
            else key: _preserve_player_character_text(item, setup, ai_character)
            for key, item in value.items()
        }

    return value


def _player_character_name_replacements(
    setup: dict[str, Any],
    ai_character: Any,
) -> list[tuple[str, str]]:
    """Builds safe character-name replacements for AI-renamed setup text."""

    clean_setup = normalize_new_game_setup(setup)
    player_name = str(clean_setup["character"].get("name", "")).strip()

    if not _is_player_provided_character_field("name", player_name):
        return []

    if not isinstance(ai_character, dict):
        return []

    ai_name = str(ai_character.get("name", "")).strip()

    if not ai_name or ai_name.casefold() == player_name.casefold():
        return []

    replacements = [(ai_name, player_name)]
    ai_first = ai_name.split()[0] if ai_name.split() else ""
    player_first = player_name.split()[0] if player_name.split() else player_name

    if ai_first and player_first and ai_first.casefold() != player_first.casefold():
        replacements.append((ai_first, player_first))

    return replacements


def _is_player_provided_character_field(key: str, value: str) -> bool:
    """Returns True when a character field is a custom player value."""

    clean_value = str(value or "").strip()

    if not clean_value:
        return False

    if key == "name" and clean_value == "Player Name":
        return False

    return True


def _replace_whole_name(text: str, source: str, target: str) -> str:
    """Replaces a generated name without touching substrings inside words."""

    if not source:
        return text

    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(source)}(?![A-Za-z0-9])",
        flags=re.IGNORECASE,
    )
    return pattern.sub(target, text)


def _normalize_theme_name(theme: str) -> str:
    """Returns a supported theme name."""

    clean_theme = str(theme or "Light").strip()
    return clean_theme if clean_theme in THEME_NAMES else "Light"


def _application_uses_dark_theme() -> bool:
    """Returns True when the active Qt application palette is dark."""

    app = QApplication.instance()

    if not isinstance(app, QApplication):
        return False

    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def _next_available_save_title(saves_dir: Path, requested_title: str) -> str:
    """Returns the first non-conflicting player-facing save title."""

    return SaveGameService(saves_dir).next_available_title(requested_title)


def _split_save_title_suffix(title: str) -> tuple[str, int]:
    """Splits a trailing numeric save suffix from a title."""

    match = re.match(r"^(?P<base>.*?)(?:\s+(?P<suffix>\d+))?$", title.strip())

    if match is None:
        return title.strip() or "New Adventure", 1

    base_title = str(match.group("base") or "").strip() or "New Adventure"
    suffix_text = match.group("suffix")

    if suffix_text is None:
        return base_title, 1

    return base_title, int(suffix_text)


def _light_theme_palette() -> QPalette:
    """Builds a light, high-contrast application palette."""

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f5f7fb"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#eef2f7"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#4b5563"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#e5e7eb"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#1d4ed8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#111827"))
    return palette


def _dark_theme_palette() -> QPalette:
    """Builds a dark, high-contrast application palette."""

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#121416"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2a2d30"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#a9b0b6"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#303437"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4c8fcb"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#303437"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f1f3f4"))
    return palette


def _light_theme_stylesheet() -> str:
    """Returns stylesheet rules that make the light theme visible on all platforms."""

    return """
        QWidget {
            background-color: #f5f7fb;
            color: #111827;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {
            background-color: #ffffff;
            color: #111827;
            border: 1px solid #64748b;
            selection-background-color: #1d4ed8;
            selection-color: #ffffff;
        }
        QComboBox, QSpinBox {
            min-height: 24px;
            padding: 3px 6px;
        }
        QComboBox {
            padding-right: 40px;
        }
        QSpinBox {
            padding-right: 36px;
        }
        QComboBox::drop-down {
            background-color: #e2e8f0;
            border-left: 1px solid #94a3b8;
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 32px;
        }
        QComboBox::down-arrow {
            image: none;
            width: 0;
            height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #111827;
        }
        QComboBox QAbstractItemView {
            background-color: #ffffff;
            color: #111827;
            selection-background-color: #1d4ed8;
            selection-color: #ffffff;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            background-color: #e2e8f0;
            border-left: 1px solid #94a3b8;
            subcontrol-origin: border;
            width: 28px;
        }
        QSpinBox::up-button {
            border-bottom: 1px solid #94a3b8;
            subcontrol-position: top right;
            height: 14px;
        }
        QSpinBox::down-button {
            subcontrol-position: bottom right;
            height: 14px;
        }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
            border-color: #1d4ed8;
        }
        QLabel, QCheckBox {
            background-color: transparent;
            color: #111827;
        }
        QCheckBox::indicator {
            background-color: #ffffff;
            border: 1px solid #64748b;
            border-radius: 3px;
            height: 16px;
            width: 16px;
        }
        QCheckBox::indicator:hover {
            border-color: #1d4ed8;
        }
        QCheckBox::indicator:checked {
            background-color: #2563eb;
            border-color: #1d4ed8;
        }
        QSlider::groove:horizontal {
            background-color: #d1d9e6;
            border: 1px solid #94a3b8;
            border-radius: 4px;
            height: 8px;
        }
        QSlider::handle:horizontal {
            background-color: #2563eb;
            border: 1px solid #1e40af;
            border-radius: 7px;
            margin: -4px 0;
            width: 14px;
        }
        QPushButton {
            background-color: #e5e7eb;
            color: #111827;
            border: 1px solid #64748b;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #dbe4ee;
            border-color: #475569;
        }
        QPushButton:default {
            background-color: #2563eb;
            border-color: #1d4ed8;
            color: #ffffff;
        }
        QTabWidget::pane {
            border: 1px solid #94a3b8;
        }
        QTabBar::tab {
            background-color: #e5e7eb;
            color: #111827;
            border: 1px solid #94a3b8;
            padding: 6px 10px;
        }
        QTabBar::tab:selected {
            background-color: #ffffff;
            border-bottom-color: #ffffff;
        }
        QHeaderView::section {
            background-color: #e2e8f0;
            color: #111827;
            border: 1px solid #cbd5e1;
            padding: 4px;
        }
        QTableWidget#calendarGrid QLabel#currentCalendarDay {
            background-color: transparent;
            color: #111827;
            border: 2px solid #1d4ed8;
        }
    """


def _dark_theme_stylesheet() -> str:
    """Returns stylesheet rules that make the dark theme visible on all platforms."""

    return """
        QWidget {
            background-color: #202124;
            color: #f1f3f4;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTableWidget {
            background-color: #121416;
            color: #f1f3f4;
            border: 1px solid #4b5258;
            selection-background-color: #4c8fcb;
            selection-color: #ffffff;
        }
        QComboBox, QSpinBox {
            min-height: 24px;
            padding: 3px 6px;
        }
        QComboBox {
            padding-right: 40px;
        }
        QSpinBox {
            padding-right: 36px;
        }
        QComboBox::drop-down {
            background-color: #303437;
            border-left: 1px solid #5b6268;
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 32px;
        }
        QComboBox::down-arrow {
            image: none;
            width: 0;
            height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 6px solid #f1f3f4;
        }
        QComboBox QAbstractItemView {
            background-color: #121416;
            color: #f1f3f4;
            selection-background-color: #4c8fcb;
            selection-color: #ffffff;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            background-color: #303437;
            border-left: 1px solid #5b6268;
            subcontrol-origin: border;
            width: 28px;
        }
        QSpinBox::up-button {
            border-bottom: 1px solid #5b6268;
            subcontrol-position: top right;
            height: 14px;
        }
        QSpinBox::down-button {
            subcontrol-position: bottom right;
            height: 14px;
        }
        QLabel, QCheckBox {
            background-color: transparent;
            color: #f1f3f4;
        }
        QCheckBox::indicator {
            background-color: #121416;
            border: 1px solid #5b6268;
            border-radius: 3px;
            height: 16px;
            width: 16px;
        }
        QCheckBox::indicator:hover {
            border-color: #4c8fcb;
        }
        QCheckBox::indicator:checked {
            background-color: #4c8fcb;
            border-color: #2f6fb0;
        }
        QPushButton {
            background-color: #303437;
            color: #f1f3f4;
            border: 1px solid #5b6268;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #3c4247;
        }
        QTabWidget::pane {
            border: 1px solid #4b5258;
        }
        QTabBar::tab {
            background-color: #303437;
            color: #f1f3f4;
            border: 1px solid #4b5258;
            padding: 6px 10px;
        }
        QTabBar::tab:selected {
            background-color: #202124;
            border-bottom-color: #202124;
        }
        QHeaderView::section {
            background-color: #303437;
            color: #f1f3f4;
            border: 1px solid #4b5258;
            padding: 4px;
        }
        QTableWidget#calendarGrid QLabel#currentCalendarDay {
            background-color: transparent;
            color: #f1f3f4;
            border: 2px solid #6b7280;
        }
    """


def _slider_row(slider: QSlider, value_label: QLabel) -> QWidget:
    """Builds a compact slider row with a fixed-width value label."""

    value_label.setFixedWidth(42)
    row = QHBoxLayout()
    row.addWidget(slider)
    row.addWidget(value_label)

    widget = QWidget()
    widget.setLayout(row)
    return widget


def _spin_pair_row(current_spin: QSpinBox, max_spin: QSpinBox) -> QWidget:
    """Builds a compact current/max spin-box row."""

    row = QHBoxLayout()
    row.addWidget(current_spin)
    row.addWidget(QLabel("/"))
    row.addWidget(max_spin)
    row.addStretch()
    widget = QWidget()
    widget.setLayout(row)
    return widget


def _button_row(*widgets: QWidget) -> QWidget:
    """Builds a compact horizontal control row."""

    row = QHBoxLayout()

    for widget in widgets:
        row.addWidget(widget)

    row.addStretch()

    container = QWidget()
    container.setLayout(row)
    return container


def _invoke_sample_voice_callback(
    callback: SampleVoiceCallback | None,
    voice: str,
    volume: int,
    speed: int,
) -> bool:
    """Calls a sample-voice callback with its complete voice settings."""

    if callback is None:
        return False

    return bool(callback(voice, volume, speed))


def _narrator_voice_options(narration_player: Any | None = None) -> dict[str, str]:
    """Returns known narrator voice display options."""

    if narration_player is not None and hasattr(narration_player, "get_available_voices"):
        try:
            voices = narration_player.get_available_voices()
        except Exception as error:
            LOGGER.warning("Could not read narrator voice options: %s", error)
        else:
            if isinstance(voices, dict) and voices:
                return {
                    str(label): str(voice_id)
                    for label, voice_id in voices.items()
                    if str(label).strip() and str(voice_id).strip()
                }

    return available_narrator_voices()


def _custom_voice_display_text(blend: dict[str, Any]) -> str:
    """Returns a compact custom voice summary."""

    clean_blend = normalize_voice_blend(blend)
    return (
        f"{clean_blend['name']} ({voice_display_name(clean_blend['voice_a'])} "
        f"{clean_blend['voice_a_weight']}% / "
        f"{voice_display_name(clean_blend['voice_b'])} "
        f"{clean_blend['voice_b_weight']}%)"
    )


def _populate_narrator_voice_combo(
    combo: QComboBox,
    selected_voice: Any,
    *,
    voice_options: dict[str, str] | None = None,
) -> None:
    """Fills a narrator voice combo and selects a supported voice id."""

    combo.clear()
    combo.setMinimumWidth(220)
    voices = voice_options or available_narrator_voices()

    for label, voice_id in voices.items():
        combo.addItem(label, voice_id)

    _set_combo_to_data(combo, normalize_narrator_voice(selected_voice))


def _combo_current_data_text(combo: QComboBox | None, default: str) -> str:
    """Returns a combo box's current data as text."""

    if combo is None:
        return default

    value = combo.currentData()
    return str(value or default)


def _scrollable_widget(content: QWidget) -> QScrollArea:
    """Wraps tall dialog content so outer action buttons remain visible."""

    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll_area.setWidget(content)
    return scroll_area


def _row_for_cell_widget(table: QTableWidget, widget: QWidget) -> int:
    """Returns the table row containing widget, or -1 when not found."""

    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            if table.cellWidget(row, column) is widget:
                return row

    return -1


def _table_line_edit(text: str) -> QLineEdit:
    """Builds an inline table editor that focuses like a native text box."""

    line_edit = QLineEdit()
    line_edit.setText(text)
    line_edit.setFrame(False)
    line_edit.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    line_edit.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    return line_edit


def _table_spin_box(minimum: int, maximum: int) -> QSpinBox:
    """Builds an inline table number editor with table-wide sizing."""

    spin_box = _NoWheelSpinBox()
    spin_box.setMinimum(minimum)
    spin_box.setMaximum(maximum)
    spin_box.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    spin_box.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    return spin_box


def _table_combo_box(options: dict[str, str], current_value: str) -> QComboBox:
    """Builds an inline table combo box."""

    combo = _NoWheelComboBox()
    _add_combo_options(combo, options)
    _set_combo_to_data(combo, current_value)
    combo.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    combo.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    return combo


def _set_table_column_widths(table: QTableWidget, widths: tuple[int, ...]) -> None:
    """Applies stable table column widths so inline editors do not autoshrink."""

    for column, width in enumerate(widths):
        if column < table.columnCount():
            table.setColumnWidth(column, width)


def _append_starting_location_table_row(
    table: QTableWidget,
    location: dict[str, Any],
    row_id: int,
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable starting location row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name_input = _table_line_edit(str(location.get("name", "")))
    name_input.setProperty("starting_location_row_id", str(row_id))
    description_input = _table_line_edit(str(location.get("description", "")))
    mode_input = _table_combo_box(
        {"suggestion": "Suggestion", "exact": "Exact"},
        str(location.get("location_mode", "suggestion") or "suggestion"),
    )
    sublocation_input = QCheckBox()
    sublocation_input.setChecked(bool(location.get("is_sublocation", False)))
    parent_input = _NoWheelComboBox()
    parent_input.setMinimumWidth(TABLE_INLINE_EDITOR_MIN_WIDTH)
    parent_input.setMinimumHeight(TABLE_INLINE_EDITOR_HEIGHT)
    parent_input.setProperty(
        "pending_parent_location",
        str(location.get("parent_location", "") or ""),
    )
    parent_input.setVisible(sublocation_input.isChecked())

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, description_input)
    table.setCellWidget(row, 2, mode_input)
    table.setCellWidget(row, 3, sublocation_input)
    table.setCellWidget(row, 4, parent_input)
    _set_remove_row_button(
        table,
        row,
        5,
        "location",
        remove_callback,
    )
    parent_input.setVisible(sublocation_input.isChecked())
    _set_table_column_widths(table, STARTING_LOCATION_COLUMN_WIDTHS)


def _starting_locations_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads requested starting location rows from table."""

    locations: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        description_widget = table.cellWidget(row, 1)
        mode_widget = table.cellWidget(row, 2)
        sublocation_widget = table.cellWidget(row, 3)
        parent_widget = table.cellWidget(row, 4)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""
        description = (
            description_widget.text().strip()
            if isinstance(description_widget, QLineEdit)
            else ""
        )
        location_mode = (
            str(mode_widget.currentData())
            if isinstance(mode_widget, QComboBox)
            else "suggestion"
        )
        if location_mode not in {"suggestion", "exact"}:
            location_mode = "suggestion"
        is_sublocation = (
            sublocation_widget.isChecked()
            if isinstance(sublocation_widget, QCheckBox)
            else False
        )
        parent_location = (
            str(parent_widget.currentText()).strip()
            if isinstance(parent_widget, QComboBox)
            and parent_widget.currentData() not in (None, "")
            else ""
        )

        locations.append(
            {
                "name": name,
                "description": description,
                "location_mode": location_mode,
                "is_sublocation": is_sublocation,
                "parent_location": parent_location if is_sublocation else "",
                "requires_ai_invention": (
                    location_mode == "suggestion" or not name or not description
                ),
            }
        )

    return locations


def _starting_location_row_id_for_row(table: QTableWidget, row: int) -> str:
    """Returns the stable id assigned to a starting-location row."""

    name_widget = table.cellWidget(row, 0)

    if not isinstance(name_widget, QLineEdit):
        return ""

    return str(name_widget.property("starting_location_row_id") or "")


def _starting_location_row_for_id(table: QTableWidget, row_id: Any) -> int:
    """Returns the row matching row_id, or -1."""

    target_id = str(row_id)

    for row in range(table.rowCount()):
        if _starting_location_row_id_for_row(table, row) == target_id:
            return row

    return -1


def _starting_location_options_from_table(
    table: QTableWidget,
) -> list[tuple[str, str]]:
    """Returns nonblank starting-location names keyed by stable row id."""

    options: list[tuple[str, str]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        row_id = _starting_location_row_id_for_row(table, row)
        name_widget = table.cellWidget(row, 0)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if row_id and name:
            options.append((row_id, name))

    return options


def _sync_starting_npc_location_dropdowns(
    npc_table: QTableWidget,
    locations: list[tuple[str, str]],
) -> None:
    """Keeps NPC location choices tied to the live starting-location rows."""

    valid_ids = {row_id for row_id, _name in locations}
    for row in range(npc_table.rowCount()):
        if npc_table.isRowHidden(row):
            continue
        location_widget = npc_table.cellWidget(row, 1)
        if not isinstance(location_widget, QComboBox):
            continue
        selected_id = str(location_widget.currentData() or "")
        location_widget.blockSignals(True)
        location_widget.clear()
        location_widget.addItem("Select a location", "")
        for row_id, name in locations:
            location_widget.addItem(name, row_id)
        if selected_id in valid_ids:
            _set_combo_to_data(location_widget, selected_id)
        else:
            location_widget.setCurrentIndex(0)
        location_widget.blockSignals(False)


def _sync_starting_location_parent_dropdowns(
    table: QTableWidget,
    locations: list[tuple[str, str]],
) -> None:
    """Keeps each sublocation parent dropdown hidden until needed and up to date."""

    valid_ids = {row_id for row_id, _name in locations}

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        row_id = _starting_location_row_id_for_row(table, row)
        sublocation_widget = table.cellWidget(row, 3)
        parent_widget = table.cellWidget(row, 4)
        parent_selected = (
            parent_widget.currentData()
            if isinstance(parent_widget, QComboBox)
            else ""
        )
        is_sublocation = (
            sublocation_widget.isChecked()
            if isinstance(sublocation_widget, QCheckBox)
            else False
        )

        if not isinstance(parent_widget, QComboBox):
            continue

        previous_parent_text = parent_widget.currentText().strip()
        pending_parent = str(
            parent_widget.property("pending_parent_location") or ""
        ).strip()
        if not pending_parent and parent_widget.currentData() in (None, ""):
            pending_parent = previous_parent_text

        parent_widget.blockSignals(True)
        parent_widget.clear()
        parent_widget.addItem("Select containing location", "")

        for option_id, name in locations:
            if option_id == row_id:
                continue
            parent_widget.addItem(name, option_id)

        if pending_parent:
            _set_combo_to_text(parent_widget, pending_parent)
            if parent_widget.currentData() not in (None, ""):
                parent_widget.setProperty("pending_parent_location", "")
        elif str(parent_selected or "") in valid_ids:
            _set_combo_to_data(parent_widget, str(parent_selected))

        parent_widget.blockSignals(False)
        parent_widget.setVisible(is_sublocation)


def _append_starting_npc_table_row(
    table: QTableWidget,
    npc: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
    *,
    location_options: list[tuple[str, str]] | None = None,
    change_callback: Callable[[], None] | None = None,
) -> None:
    """Adds one editable starting NPC row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name_input = _table_line_edit(str(npc.get("name", npc.get("display_name", ""))))
    name_input.setProperty(
        "npc_id",
        str(npc.get("npc_id", "")).strip() or f"starting_npc_{uuid.uuid4().hex}",
    )
    location_input = _NoWheelComboBox()
    location_input.addItem("Select a location", "")
    for row_id, location_name in location_options or []:
        location_input.addItem(location_name, row_id)
    requested_location = str(npc.get("location", "")).strip().casefold()
    if requested_location:
        for index in range(1, location_input.count()):
            if location_input.itemText(index).strip().casefold() == requested_location:
                location_input.setCurrentIndex(index)
                break
    description_input = _table_line_edit(
        str(npc.get("description", npc.get("public_description", "")))
    )
    mode_input = _table_combo_box(
        {"suggestion": "Suggestion", "exact": "Exact"},
        str(npc.get("description_mode", "suggestion") or "suggestion"),
    )
    if change_callback is not None:
        name_input.textChanged.connect(change_callback)
        location_input.currentIndexChanged.connect(change_callback)

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, location_input)
    table.setCellWidget(row, 2, description_input)
    table.setCellWidget(row, 3, mode_input)
    _set_remove_row_button(
        table,
        row,
        4,
        "NPC",
        remove_callback,
    )
    _set_table_column_widths(table, STARTING_NPC_COLUMN_WIDTHS)


def _starting_npcs_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads requested starting NPC rows from table."""

    npcs: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        location_widget = table.cellWidget(row, 1)
        description_widget = table.cellWidget(row, 2)
        mode_widget = table.cellWidget(row, 3)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""
        npc_id = (
            str(name_widget.property("npc_id") or "").strip()
            if isinstance(name_widget, QLineEdit)
            else ""
        )
        location = (
            location_widget.currentText().strip()
            if isinstance(location_widget, QComboBox)
            and location_widget.currentData() not in (None, "")
            else ""
        )
        description = (
            description_widget.text().strip()
            if isinstance(description_widget, QLineEdit)
            else ""
        )

        description_mode = (
            str(mode_widget.currentData())
            if isinstance(mode_widget, QComboBox)
            else "suggestion"
        )
        if description_mode not in {"suggestion", "exact"}:
            description_mode = "suggestion"

        npcs.append(
            {
                "npc_id": npc_id,
                "name": name,
                "location": location,
                "location_source_index": (
                    location_widget.currentIndex() - 1
                    if isinstance(location_widget, QComboBox)
                    and location_widget.currentData() not in (None, "")
                    else -1
                ),
                "description": description,
                "description_mode": description_mode,
                "requires_ai_invention": (
                    description_mode == "suggestion"
                    or not name
                    or not location
                    or not description
                ),
            }
        )

    return npcs


def _configure_inline_table(
    table: QTableWidget,
    widths: tuple[int, ...],
    *,
    minimum_height: int,
) -> None:
    """Applies the shared inline-editing table behavior."""

    table.setMinimumHeight(minimum_height)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(36)
    table.horizontalHeader().setStretchLastSection(False)
    table.setAlternatingRowColors(True)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    _use_soft_table_selection(table)
    _set_table_column_widths(table, widths)


def _configure_responsive_form(layout: QFormLayout) -> None:
    """Lets wizard form fields grow and wrap cleanly at narrow widths."""

    layout.setFieldGrowthPolicy(
        QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
    )
    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)


def _configure_responsive_table(
    table: QTableWidget,
    *,
    stretch_columns: set[int],
    compact_columns: set[int],
) -> None:
    """Makes an inline editor table consume available width responsively."""

    table.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Expanding,
    )
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(72)
    for column in range(table.columnCount()):
        if column in compact_columns:
            resize_mode = QHeaderView.ResizeMode.ResizeToContents
        elif column in stretch_columns:
            resize_mode = QHeaderView.ResizeMode.Stretch
        else:
            resize_mode = QHeaderView.ResizeMode.Interactive
        header.setSectionResizeMode(column, resize_mode)
    _configure_auto_height_table(table)
    _configure_table_wheel_passthrough(table)


def _configure_auto_height_table(
    table: QTableWidget,
    *,
    maximum_visible_rows: int = 5,
) -> None:
    """Fits a wizard table to its rows until its scrollbar is actually needed."""

    if hasattr(table, "_auto_height_refresh"):
        return

    def refresh_height() -> None:
        visible_row_count = min(
            max(1, table.rowCount()),
            max(1, maximum_visible_rows),
        )
        row_heights = [
            max(table.rowHeight(row), table.verticalHeader().defaultSectionSize())
            for row in range(min(table.rowCount(), visible_row_count))
        ]
        while len(row_heights) < visible_row_count:
            row_heights.append(table.verticalHeader().defaultSectionSize())
        target_height = (
            table.horizontalHeader().sizeHint().height()
            + sum(row_heights)
            + (table.frameWidth() * 2)
            + 6
        )
        table.setMinimumHeight(target_height)
        table.setMaximumHeight(target_height)

    def schedule_refresh(*_args: Any) -> None:
        QTimer.singleShot(0, refresh_height)

    model = table.model()
    model.rowsInserted.connect(schedule_refresh)
    model.rowsRemoved.connect(schedule_refresh)
    model.modelReset.connect(schedule_refresh)
    table._auto_height_refresh = refresh_height  # type: ignore[attr-defined]
    refresh_height()
    schedule_refresh()


class _TableWheelPassthroughFilter(QObject):
    """Routes table wheel input to the enclosing page instead of the table."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self.table = table

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel or not isinstance(event, QWheelEvent):
            return super().eventFilter(watched, event)

        parent = self.table.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()

        if isinstance(parent, QScrollArea):
            page_scrollbar = parent.verticalScrollBar()
            pixel_delta = event.pixelDelta().y()
            if pixel_delta:
                scroll_amount = -pixel_delta
            else:
                wheel_steps = event.angleDelta().y() / 120.0
                scroll_amount = int(
                    -wheel_steps * max(1, page_scrollbar.singleStep()) * 3
                )
            page_scrollbar.setValue(page_scrollbar.value() + scroll_amount)

        event.accept()
        return True


def _configure_table_wheel_passthrough(table: QTableWidget) -> None:
    """Disables wheel scrolling for a table while preserving scrollbar dragging."""

    if hasattr(table, "_wheel_passthrough_filter"):
        return
    wheel_filter = _TableWheelPassthroughFilter(table)
    for watched in (
        table,
        table.viewport(),
        table.verticalScrollBar(),
        table.horizontalScrollBar(),
    ):
        watched.installEventFilter(wheel_filter)
    table._wheel_passthrough_filter = wheel_filter  # type: ignore[attr-defined]


def _table_row_display_name(
    table: QTableWidget,
    row: int,
    column: int,
) -> str:
    """Returns the current user-facing name for a table row."""

    widget = table.cellWidget(row, column)
    if isinstance(widget, QLineEdit):
        return widget.text().strip()
    if isinstance(widget, QComboBox):
        return widget.currentText().strip()

    item = table.item(row, column)
    return item.text().strip() if item is not None else ""


def _set_remove_row_button(
    table: QTableWidget,
    row: int,
    column: int,
    item_label: str,
    remove_callback: Callable[[QPushButton], None],
    *,
    name_column: int = 0,
    protected: bool = False,
) -> QPushButton:
    """Adds one confirmed, row-local Remove action to an editor table."""

    button = QPushButton("Remove")
    button.setObjectName("rowRemoveButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setToolTip(f"Remove this {item_label}.")
    button.setEnabled(not protected)
    button.setVisible(not protected)

    def confirm_remove() -> None:
        current_row = _row_for_cell_widget(table, button)
        if current_row < 0:
            return

        display_name = _table_row_display_name(table, current_row, name_column)
        target = f'"{display_name}"' if display_name else f"this {item_label}"
        result = QMessageBox.question(
            table,
            f"Remove {item_label.title()}",
            f"Are you sure you want to remove {target}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            remove_callback(button)

    button.clicked.connect(confirm_remove)
    table.setCellWidget(row, column, button)
    return button


def _build_starter_suggestion_table(kind: str) -> _AppTableWidget:
    """Builds the compact single-column table used for AI item concepts."""

    table = _AppTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["Suggestion", "Remove"])
    # Give the table a real viewport.  With the old 70px minimum, the header
    # consumed nearly all available space and the suggestion editor was
    # effectively unreadable.  The maximum keeps the wizard compact while
    # allowing the table's own vertical scrollbar to handle longer lists.
    _configure_inline_table(table, (520, 90), minimum_height=190)
    table.setMaximumHeight(240)
    table.setToolTip(
        f"Enter a {kind.lower()} concept such as 'Iron Sword'. Gemini will create "
        "the item's description, value, and other details."
    )
    return table


def _append_starter_suggestion_table_row(
    table: QTableWidget,
    kind: str,
    suggestion: str = "",
    remove_callback: Callable[[QPushButton], None] | None = None,
) -> None:
    """Adds one natural-language starter-item suggestion row."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)
    suggestion_input = _table_line_edit(suggestion)
    suggestion_input.setPlaceholderText(f"e.g. {'Iron Sword' if kind == 'Weapon' else 'Leather Satchel'}")
    table.setCellWidget(row, 0, suggestion_input)
    callback: Callable[[QPushButton], None]
    if remove_callback is None:
        def remove_default(button: QPushButton) -> None:
            _remove_table_row_by_button(table, button)
        callback = remove_default
    else:
        callback = remove_callback
    _set_remove_row_button(
        table,
        row,
        1,
        f"{kind.lower()} idea",
        callback,
    )


def _starter_suggestions_from_table(
    table: QTableWidget,
    kind: str,
) -> list[dict[str, Any]]:
    """Reads natural-language starter-item suggestions from a compact table."""

    suggestions: list[dict[str, Any]] = []
    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        widget = table.cellWidget(row, 0)
        suggestion = widget.text().strip() if isinstance(widget, QLineEdit) else ""
        if not suggestion:
            continue
        item: dict[str, Any] = {
            "name": "",
            "category": kind,
            "quantity": 1,
            "description": "",
            "value_base_units": 0,
            "item_request": suggestion,
            "requires_ai_invention": True,
        }
        if kind in {"Weapon", "Armor"}:
            item["item_type"] = kind
        suggestions.append(item)
    return suggestions


def _append_starter_item_table_row(
    table: QTableWidget,
    item: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable starter-item row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)
    name_input = _table_line_edit(str(item.get("name", "")))
    category_input = _table_line_edit(str(item.get("category", "Item") or "Item"))
    description_input = _table_line_edit(str(item.get("description", "")))

    quantity_input = _table_spin_box(1, 999_999)
    quantity_input.setValue(_safe_int(item.get("quantity", 1), 1))

    value_input = _table_spin_box(0, 1_000_000_000)
    value_input.setValue(_safe_int(item.get("value_base_units", 0), 0))

    storage_input = QComboBox()
    storage_input.setEditable(True)
    storage_input.addItem("Actively Carried", "actively_carried")
    storage_input.addItem("Home", "home")
    storage_value = str(item.get("storage_location", "actively_carried") or "actively_carried").strip()
    if storage_value.casefold() in {"home", "actively_carried"}:
        _set_combo_to_data(storage_input, storage_value)
    else:
        storage_input.setEditText(storage_value)

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, quantity_input)
    table.setCellWidget(row, 2, category_input)
    table.setCellWidget(row, 3, description_input)
    table.setCellWidget(row, 4, value_input)
    table.setCellWidget(row, 5, storage_input)
    _set_remove_row_button(table, row, 6, "item", remove_callback)
    _set_table_column_widths(table, STARTER_ITEM_COLUMN_WIDTHS)


def _starter_items_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads starter-item rows from table."""

    items: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        quantity_widget = table.cellWidget(row, 1)
        category_widget = table.cellWidget(row, 2)
        description_widget = table.cellWidget(row, 3)
        value_widget = table.cellWidget(row, 4)
        storage_widget = table.cellWidget(row, 5)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        items.append(
            {
                "name": name,
                "category": (
                    category_widget.text().strip()
                    if isinstance(category_widget, QLineEdit)
                    and category_widget.text().strip()
                    else "Item"
                ),
                "quantity": quantity_widget.value() if isinstance(quantity_widget, QSpinBox) else 1,
                "description": (
                    description_widget.text().strip()
                    if isinstance(description_widget, QLineEdit)
                    else ""
                ),
                "value_base_units": value_widget.value() if isinstance(value_widget, QSpinBox) else 0,
                "storage_location": (
                    str(storage_widget.currentData() or "actively_carried")
                    if isinstance(storage_widget, QComboBox)
                    else "actively_carried"
                ),
                "item_request": "",
                "requires_ai_invention": False,
            }
        )

    return items


def _starter_item_kind(item: dict[str, Any]) -> str:
    """Returns the starter item table kind for a normalized item."""

    category = str(item.get("category", "") or "").strip().casefold()
    item_type = str(item.get("item_type", "") or "").strip().casefold()

    if not item_type and isinstance(item.get("metadata"), dict):
        item_type = str(item["metadata"].get("item_type", "") or "").strip().casefold()

    if category == "weapon" or item_type == "weapon":
        return "Weapon"
    if category in {"armor", "armour", "shield"} or item_type == "armor":
        return "Armor"
    return "Item"


def _metadata_text(item: dict[str, Any], key: str, default: str = "") -> str:
    """Reads a top-level or metadata-backed text value."""

    value = item.get(key, None)

    if (value is None or value == "") and isinstance(item.get("metadata"), dict):
        value = item["metadata"].get(key, default)

    return str(default if value is None else value).strip()


def _metadata_int(item: dict[str, Any], key: str, default: int = 0) -> int:
    """Reads a top-level or metadata-backed integer value."""

    value = item.get(key, None)

    if value is None and isinstance(item.get("metadata"), dict):
        value = item["metadata"].get(key, default)

    return _safe_int(value, default)


def _append_starter_weapon_table_row(
    table: QTableWidget,
    item: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable starter-weapon row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name_input = _table_line_edit(str(item.get("name", "")))
    quantity_input = _table_spin_box(1, 999_999)
    quantity_input.setValue(_safe_int(item.get("quantity", 1), 1))
    hands_input = _table_combo_box(
        {"One-handed": "one-handed", "Two-handed": "two-handed"},
        _metadata_text(item, "weapon_hands", "one-handed") or "one-handed",
    )
    damage_input = _table_line_edit(_metadata_text(item, "damage", "1d6") or "1d6")
    attack_skill_input = _table_line_edit(
        _metadata_text(item, "attack_skill", "Melee") or "Melee"
    )
    range_input = _table_spin_box(0, 10_000)
    range_input.setValue(max(0, _metadata_int(item, "attack_range_feet", 5)))
    ammo_input = _table_line_edit(_metadata_text(item, "ammunition_type_required"))
    clip_size_input = _table_spin_box(0, 999)
    clip_size_input.setValue(max(0, _metadata_int(item, "clip_size", 0)))

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, quantity_input)
    table.setCellWidget(row, 2, hands_input)
    table.setCellWidget(row, 3, damage_input)
    table.setCellWidget(row, 4, attack_skill_input)
    table.setCellWidget(row, 5, range_input)
    table.setCellWidget(row, 6, ammo_input)
    table.setCellWidget(row, 7, clip_size_input)
    _set_remove_row_button(table, row, 8, "weapon", remove_callback)
    _set_table_column_widths(table, STARTER_WEAPON_COLUMN_WIDTHS)


def _starter_weapons_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads starter-weapon rows from table."""

    items: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        quantity_widget = table.cellWidget(row, 1)
        hands_widget = table.cellWidget(row, 2)
        damage_widget = table.cellWidget(row, 3)
        attack_skill_widget = table.cellWidget(row, 4)
        range_widget = table.cellWidget(row, 5)
        ammo_widget = table.cellWidget(row, 6)
        clip_size_widget = table.cellWidget(row, 7)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        ammunition_type_required = (
            ammo_widget.text().strip() if isinstance(ammo_widget, QLineEdit) else ""
        )
        clip_size = clip_size_widget.value() if isinstance(clip_size_widget, QSpinBox) else 0

        items.append(
            {
                "name": name,
                "category": "Weapon",
                "quantity": quantity_widget.value() if isinstance(quantity_widget, QSpinBox) else 1,
                "description": "",
                "value_base_units": 0,
                "item_type": "Weapon",
                "weapon_hands": (
                    str(hands_widget.currentData())
                    if isinstance(hands_widget, QComboBox)
                    else "one-handed"
                ),
                "damage": (
                    damage_widget.text().strip()
                    if isinstance(damage_widget, QLineEdit)
                    and damage_widget.text().strip()
                    else "1d6"
                ),
                "attack_skill": (
                    attack_skill_widget.text().strip()
                    if isinstance(attack_skill_widget, QLineEdit)
                    and attack_skill_widget.text().strip()
                    else "Melee"
                ),
                "attack_range_feet": (
                    range_widget.value() if isinstance(range_widget, QSpinBox) else 5
                ),
                "ammunition_type_required": ammunition_type_required,
                "clip_size": clip_size if ammunition_type_required else 0,
                "bullets_per_attack": 1 if ammunition_type_required and clip_size > 0 else 0,
                "item_request": "",
                "requires_ai_invention": False,
            }
        )

    return items


def _append_starter_armor_table_row(
    table: QTableWidget,
    item: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable starter-armor row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name_input = _table_line_edit(str(item.get("name", "")))
    quantity_input = _table_spin_box(1, 999_999)
    quantity_input.setValue(_safe_int(item.get("quantity", 1), 1))
    raw_covers_body_parts = item.get("covers_body_parts")

    if not isinstance(raw_covers_body_parts, list) and isinstance(
        item.get("metadata"), dict
    ):
        raw_covers_body_parts = item["metadata"].get("covers_body_parts")

    covers_body_parts = (
        raw_covers_body_parts if isinstance(raw_covers_body_parts, list) else []
    )
    covers_input = _table_line_edit(
        ", ".join(str(part) for part in covers_body_parts if part is not None)
    )
    armor_rating_input = _table_spin_box(0, 99)
    armor_rating_input.setValue(max(0, _metadata_int(item, "armor_rating", 1)))
    value_input = _table_spin_box(0, 1_000_000_000)
    value_input.setValue(_safe_int(item.get("value_base_units", 0), 0))

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, quantity_input)
    table.setCellWidget(row, 2, covers_input)
    table.setCellWidget(row, 3, armor_rating_input)
    table.setCellWidget(row, 4, value_input)
    _set_remove_row_button(table, row, 5, "armor", remove_callback)
    _set_table_column_widths(table, STARTER_ARMOR_COLUMN_WIDTHS)


def _starter_armor_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads starter-armor rows from table."""

    items: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        quantity_widget = table.cellWidget(row, 1)
        covers_widget = table.cellWidget(row, 2)
        armor_rating_widget = table.cellWidget(row, 3)
        value_widget = table.cellWidget(row, 4)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        items.append(
            {
                "name": name,
                "category": "Armor",
                "quantity": quantity_widget.value() if isinstance(quantity_widget, QSpinBox) else 1,
                "description": "",
                "value_base_units": value_widget.value() if isinstance(value_widget, QSpinBox) else 0,
                "item_type": "Armor",
                "covers_body_parts": (
                    _split_list(covers_widget.text())
                    if isinstance(covers_widget, QLineEdit)
                    else []
                ),
                "armor_rating": (
                    armor_rating_widget.value()
                    if isinstance(armor_rating_widget, QSpinBox)
                    else 1
                ),
                "item_request": "",
                "requires_ai_invention": False,
            }
        )

    return items


def _append_currency_table_row(
    table: QTableWidget,
    denomination: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one editable currency denomination row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name = str(denomination.get("name", ""))
    plural_name = str(denomination.get("plural_name", ""))
    name_input = _table_line_edit(name)
    plural_name_input = _table_line_edit(plural_name)
    value_input = _table_spin_box(1, 1_000_000_000)
    if "value" in denomination:
        default_value = _safe_int(denomination.get("value"), 1)
    elif row > 0:
        previous_value_input = table.cellWidget(row - 1, 2)
        previous_value = (
            previous_value_input.value()
            if isinstance(previous_value_input, QSpinBox)
            else 1
        )
        default_value = min(1_000_000_000, max(1, previous_value) * 10)
    else:
        default_value = 1
    value_input.setValue(default_value)

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, plural_name_input)
    table.setCellWidget(row, 2, value_input)
    _set_remove_row_button(
        table,
        row,
        3,
        "currency",
        remove_callback,
        protected=row == 0,
    )
    _sync_currency_base_value_row(table)
    _set_table_column_widths(table, CURRENCY_COLUMN_WIDTHS)


def _sync_currency_base_value_row(table: QTableWidget) -> None:
    """Keeps the first visible currency row as the baseline denomination."""

    for row in range(table.rowCount()):
        value_widget = table.cellWidget(row, 2)

        if not isinstance(value_widget, QSpinBox):
            continue

        if row == 0:
            value_widget.setValue(1)
            value_widget.setEnabled(False)
            value_widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        else:
            value_widget.setEnabled(True)
            value_widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)

        remove_button = table.cellWidget(row, 3)
        if isinstance(remove_button, QPushButton):
            remove_button.setEnabled(row != 0)
            remove_button.setVisible(row != 0)


def _currency_denominations_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads currency denomination rows from table."""

    denominations: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        plural_widget = table.cellWidget(row, 1)
        value_widget = table.cellWidget(row, 2)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        denominations.append(
            {
                "name": name,
                "plural_name": (
                    plural_widget.text().strip()
                    if isinstance(plural_widget, QLineEdit)
                    else ""
                )
                or name,
                "value": value_widget.value() if isinstance(value_widget, QSpinBox) else 1,
            }
        )

    return denominations


def _append_economy_example_table_row(
    table: QTableWidget,
    example: dict[str, Any],
    remove_callback: Callable[[QPushButton], None],
) -> None:
    """Adds one common-price example row to table."""

    row = table.rowCount()
    table.insertRow(row)
    table.setRowHeight(row, 36)

    name_input = _table_line_edit(str(example.get("name", "")))
    value_input = _table_spin_box(1, 1_000_000_000)
    value_input.setValue(_safe_int(example.get("value_base_units", 1), 1))

    table.setCellWidget(row, 0, name_input)
    table.setCellWidget(row, 1, value_input)
    _set_remove_row_button(
        table,
        row,
        2,
        "economy item",
        remove_callback,
    )
    _set_table_column_widths(table, ECONOMY_EXAMPLE_COLUMN_WIDTHS)


def _economy_examples_from_table(table: QTableWidget) -> list[dict[str, Any]]:
    """Reads common-price examples from table."""

    examples: list[dict[str, Any]] = []

    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        name_widget = table.cellWidget(row, 0)
        value_widget = table.cellWidget(row, 1)
        name = name_widget.text().strip() if isinstance(name_widget, QLineEdit) else ""

        if not name:
            continue

        examples.append(
            {
                "name": name,
                "value_base_units": (
                    value_widget.value()
                    if isinstance(value_widget, QSpinBox)
                    else 1
                ),
            }
        )

    return normalize_economy_examples(examples)


def _remove_table_row_by_button(table: QTableWidget, button: QPushButton) -> int:
    """Removes the table row containing button and returns the removed row."""

    row = _row_for_cell_widget(table, button)

    if row >= 0:
        table.removeRow(row)

    return row


def _bool_setting(value: Any, default: bool) -> bool:
    """Reads a flexible boolean setting."""

    return domain_bool_setting(value, default)


def _clamped_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Returns an integer clamped to the provided range."""

    return domain_clamped_int(value, default, minimum, maximum)


def _final_start_location_for_save(setup: dict[str, Any], result: Any) -> str:
    """Returns the location that should be persisted as the current scene."""

    requested_location = str(setup.get("start_location", "") or "").strip()

    if (
        str(setup.get("start_location_mode", "suggestion")).casefold() == "exact"
        and requested_location
    ):
        return requested_location

    return str(getattr(result, "start_location", "") or "").strip()


def _introductory_message_for_save(setup: dict[str, Any], result: Any) -> str:
    """Returns opening narration corrected for exact start-location requests."""

    message = str(getattr(result, "introductory_message", "") or "")
    requested_location = str(setup.get("start_location", "") or "").strip()
    ai_location = str(getattr(result, "start_location", "") or "").strip()

    if (
        str(setup.get("start_location_mode", "suggestion")).casefold() == "exact"
        and requested_location
        and ai_location
        and ai_location.casefold() != requested_location.casefold()
    ):
        return message.replace(ai_location, requested_location)

    return message


def _travel_locations_for_save(
    raw_locations: Any,
    setup: dict[str, Any],
    result: Any,
) -> list[dict[str, Any]]:
    """Merges AI locations with every structured player-requested location."""

    locations = [
        location.to_dict()
        for location in normalize_known_locations(raw_locations)
    ]
    source_indexes_by_name: dict[str, int] = {}
    if isinstance(raw_locations, list):
        for raw_location in raw_locations:
            if not isinstance(raw_location, dict):
                continue
            name = str(raw_location.get("name", "") or "").strip().casefold()
            if not name:
                continue
            source_indexes_by_name[name] = _safe_int(
                raw_location.get("source_index", -1),
                -1,
            )
    for location in locations:
        location["source_index"] = source_indexes_by_name.get(
            str(location.get("name", "")).casefold(),
            -1,
        )

    requested_locations = setup.get("starting_locations", [])
    if isinstance(requested_locations, list):
        for source_index, raw_requested_location in enumerate(requested_locations):
            if not isinstance(raw_requested_location, dict):
                continue

            requested_name = str(
                raw_requested_location.get("name", "") or ""
            ).strip()
            requested_description = str(
                raw_requested_location.get("description", "") or ""
            ).strip()
            mode = str(
                raw_requested_location.get("location_mode", "suggestion")
                or "suggestion"
            ).casefold()
            matched_location = next(
                (
                    location
                    for location in locations
                    if _safe_int(location.get("source_index", -1), -1)
                    == source_index
                ),
                None,
            )
            if matched_location is None and requested_name:
                matched_location = next(
                    (
                        location
                        for location in locations
                        if str(location.get("name", "")).strip().casefold()
                        == requested_name.casefold()
                    ),
                    None,
                )

            if matched_location is None:
                if not requested_name:
                    continue
                if mode != "exact":
                    LOGGER.warning(
                        "Gemini omitted suggested location %r; not persisting its "
                        "unfinalized placeholder name.",
                        requested_name,
                    )
                    continue
                matched_location = {
                    "name": requested_name,
                    "description": requested_description,
                    "x_miles": None,
                    "y_miles": None,
                    "terrain": "",
                    "travel_multiplier": 1.0,
                    "travel_notes": "",
                    "source_index": source_index,
                }
                locations.append(matched_location)

            if mode == "exact":
                if requested_name:
                    matched_location["name"] = requested_name
                matched_location["description"] = requested_description
            elif requested_description and str(
                matched_location.get("description", "")
            ).strip().casefold() in {"", "starting location."}:
                matched_location["description"] = requested_description

            parent_location = str(
                raw_requested_location.get("parent_location", "") or ""
            ).strip()
            if bool(raw_requested_location.get("is_sublocation")) and parent_location:
                relationship_note = f"Located within {parent_location}."
                existing_notes = str(
                    matched_location.get("travel_notes", "") or ""
                ).strip()
                has_finalized_parent_note = "located within " in existing_notes.casefold()
                if (
                    not has_finalized_parent_note
                    and relationship_note.casefold() not in existing_notes.casefold()
                ):
                    matched_location["travel_notes"] = " ".join(
                        value
                        for value in [existing_notes, relationship_note]
                        if value
                    )

    requested_location = str(setup.get("start_location", "") or "").strip()
    ai_location = str(getattr(result, "start_location", "") or "").strip()

    if (
        str(setup.get("start_location_mode", "suggestion")).casefold() != "exact"
        or not requested_location
    ):
        return locations

    for location in locations:
        name = str(location.get("name", "") or "").strip()
        is_ai_start = bool(ai_location) and name.casefold() == ai_location.casefold()
        is_origin = (
            _coerce_float(location.get("x_miles")) == 0.0
            and _coerce_float(location.get("y_miles")) == 0.0
        )

        if is_ai_start or is_origin:
            location["name"] = requested_location
            if not str(location.get("description", "") or "").strip():
                location["description"] = "Starting location."
            location["x_miles"] = 0.0
            location["y_miles"] = 0.0
            return locations

    return [
        {
            "name": requested_location,
            "description": "Starting location.",
            "x_miles": 0.0,
            "y_miles": 0.0,
            "terrain": "",
            "travel_multiplier": 1.0,
            "travel_notes": "",
        },
        *locations,
    ]


def _finalized_location_aliases(
    locations: list[dict[str, Any]],
    setup: dict[str, Any],
) -> dict[str, str]:
    """Maps wizard suggestion names to their finalized AI location names."""

    requested_locations = setup.get("starting_locations", [])
    if not isinstance(requested_locations, list):
        return {}

    finalized_names_by_source_index = {
        _safe_int(location.get("source_index", -1), -1): str(
            location.get("name", "") or ""
        ).strip()
        for location in locations
        if _safe_int(location.get("source_index", -1), -1) >= 0
        and str(location.get("name", "") or "").strip()
    }
    aliases: dict[str, str] = {}

    for source_index, raw_requested_location in enumerate(requested_locations):
        if not isinstance(raw_requested_location, dict):
            continue
        requested_name = str(raw_requested_location.get("name", "") or "").strip()
        finalized_name = finalized_names_by_source_index.get(source_index, "")
        if (
            requested_name
            and finalized_name
            and requested_name.casefold() != finalized_name.casefold()
        ):
            aliases[requested_name] = finalized_name

    return aliases


def _replace_location_aliases(text: Any, aliases: dict[str, str]) -> str:
    """Reconciles free-text setup location references with finalized names."""

    clean_text = str(text or "")
    for old_name, finalized_name in sorted(
        aliases.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        clean_text = re.sub(
            rf"(?<!\w){re.escape(old_name)}(?!\w)",
            lambda _match, replacement=finalized_name: replacement,
            clean_text,
            flags=re.IGNORECASE,
        )
    return clean_text


def _replace_location_aliases_in_travel_locations(
    locations: list[dict[str, Any]],
    aliases: dict[str, str],
) -> list[dict[str, Any]]:
    """Updates player-facing location prose after suggestion names are finalized."""

    if not aliases:
        return locations

    for location in locations:
        for field_name in ("description", "travel_notes"):
            location[field_name] = _replace_location_aliases(
                location.get(field_name, ""),
                aliases,
            )
    return locations


def _apply_new_game_crafting_knowledge(
    repository: SaveRepository,
    result: Any,
    *,
    location_aliases: dict[str, str] | None = None,
) -> None:
    """Persists AI-finalized starting Crafting tab knowledge."""

    for raw_item in getattr(result, "known_crafting_items", []):
        if not isinstance(raw_item, dict):
            continue

        name = str(raw_item.get("name", "") or "").strip()

        if not name:
            continue

        description = str(raw_item.get("description", "") or "").strip()
        category = str(raw_item.get("category", "Material") or "Material").strip()

        if not is_crafting_ingredient_category(category):
            category = "Material"

        uses = [
            str(value).strip()
            for value in raw_item.get("uses", [])
            if str(value).strip()
        ] if isinstance(raw_item.get("uses"), list) else []

        repository.add_crafting_item(
            name=name,
            category=category,
            description=description,
            location=str(raw_item.get("location", "") or "").strip(),
            uses=uses,
            rarity=str(raw_item.get("rarity", "Common") or "Common"),
            notes=str(raw_item.get("notes", "") or "").strip(),
            value_base_units=max(
                0,
                _safe_int(raw_item.get("value_base_units", 0), 0),
            ),
            item_uuid=str(raw_item.get("item_uuid", "") or "").strip(),
        )
        repository.upsert_item_catalog_entry(
            name=name,
            category=category,
            description=description,
            value_base_units=max(
                0,
                _safe_int(raw_item.get("value_base_units", 0), 0),
            ),
            metadata={
                "item_uuid": raw_item.get("item_uuid", ""),
            },
        )

    allowed_ingredient_names = {
        str(item.get("name", "") or "").casefold()
        for item in repository.list_item_catalog()
        if str(item.get("name", "") or "").strip()
        and is_crafting_ingredient_category(item.get("category", ""))
    }

    for raw_recipe in getattr(result, "known_crafting_recipes", []):
        if not isinstance(raw_recipe, dict):
            continue

        name = str(raw_recipe.get("name", "") or "").strip()
        ingredients = normalize_recipe_ingredients(raw_recipe.get("ingredients", []))
        result_text = str(raw_recipe.get("result", "") or "").strip()

        if not name or not ingredients or not result_text:
            continue

        unknown_ingredients = [
            ingredient["reagent_name"]
            for ingredient in ingredients
            if ingredient["reagent_name"].casefold() not in allowed_ingredient_names
        ]

        if unknown_ingredients:
            LOGGER.warning(
                "Skipped new-game crafting recipe %s because ingredient knowledge is missing: %s",
                name,
                ", ".join(unknown_ingredients),
            )
            continue

        repository.add_crafting_recipe(
            name=name,
            ingredients=ingredients,
            result=result_text,
            notes=str(raw_recipe.get("notes", "") or "").strip(),
            value_base_units=max(
                0,
                _safe_int(raw_recipe.get("value_base_units", 0), 0),
            ),
        )


def _new_game_calendar_genre_hint(setup: dict[str, Any], result: Any) -> str:
    """Combines setup and AI-selected genre text for calendar fallback checks."""

    parts = [
        str(setup.get("specified_genre", "") or ""),
        str(setup.get("game_style", "") or ""),
        str(setup.get("world_context", "") or ""),
        str(setup.get("ai_additional_context", "") or ""),
        str(getattr(result, "selected_genre", "") or ""),
    ]
    return "\n".join(part for part in parts if part.strip())


def _finalized_skills_for_save(
    ai_skills: list[dict[str, Any]],
    setup_skills: Any,
) -> list[dict[str, Any]]:
    """Merges AI skill descriptions while preserving player-provided skill names."""

    if not isinstance(setup_skills, list) or not setup_skills:
        return _deduplicated_ai_skills(ai_skills) if ai_skills else []

    merged_skills: list[dict[str, Any]] = []
    ai_by_name = {
        str(skill.get("name", "")).strip().casefold(): skill
        for skill in ai_skills
        if isinstance(skill, dict)
    }

    for index, raw_setup_skill in enumerate(setup_skills):
        if not isinstance(raw_setup_skill, dict):
            raw_setup_skill = {"name": str(raw_setup_skill)}

        setup_name = str(raw_setup_skill.get("name", "") or "").strip()
        if not setup_name:
            ai_skill = ai_skills[index] if index < len(ai_skills) else {}
            merged_skills.append(dict(ai_skill) if isinstance(ai_skill, dict) else {})
            continue

        ai_skill = ai_by_name.get(setup_name.casefold())
        if ai_skill is None and index < len(ai_skills) and isinstance(ai_skills[index], dict):
            ai_skill = ai_skills[index]
        if ai_skill is None:
            ai_skill = {}

        description = str(
            ai_skill.get("description")
            or raw_setup_skill.get("description")
            or f"Player-selected {setup_name} skill."
        ).strip()
        merged_skills.append(
            {
                **dict(ai_skill),
                "name": setup_name,
                "description": description,
                "level": _safe_int(raw_setup_skill.get("level"), _safe_int(ai_skill.get("level"), 1)),
            }
        )

    return _deduplicated_ai_skills(
        [
            skill
            for skill in merged_skills
            if str(skill.get("name", "")).strip()
        ]
    )


def _coerce_float(value: Any) -> float | None:
    """Returns a float or None when the value is not numeric."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _deduplicated_ai_skills(ai_skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Returns AI-finalized skills with unique names suitable for persistence."""

    deduplicated_skills: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for raw_skill in ai_skills:
        skill = dict(raw_skill)
        name = str(skill.get("name", "")).strip()
        folded_name = name.casefold()

        if folded_name in seen_names:
            try:
                level = int(skill.get("level", 0))
            except (TypeError, ValueError):
                level = 0

            name = _unique_ai_skill_name(
                name,
                seen_names,
                suffix=_duplicate_skill_suffix(level),
            )
            skill["name"] = name
            LOGGER.info("Renamed duplicate AI-finalized skill to %s.", name)

        seen_names.add(name.casefold())
        deduplicated_skills.append(skill)

    return deduplicated_skills


def _unique_ai_skill_name(base_name: str, seen_names: set[str], *, suffix: str) -> str:
    """Builds a unique generated skill name from an AI duplicate."""

    clean_base = base_name.strip() or "Generated Skill"
    clean_suffix = suffix.strip() or "Alternate"
    candidate = f"{clean_base} ({clean_suffix})"

    if candidate.casefold() not in seen_names:
        return candidate

    index = 2

    while True:
        candidate = f"{clean_base} ({clean_suffix} {index})"

        if candidate.casefold() not in seen_names:
            return candidate

        index += 1


def _duplicate_skill_suffix(level: int) -> str:
    """Returns a compact descriptor for duplicate generated skill names."""

    return {
        5: "Signature",
        4: "Expert",
        3: "Skilled",
        2: "Trained",
        1: "Familiar",
    }.get(level, "Alternate")


def _starter_items_for_save(
    ai_items: list[dict[str, Any]],
    setup: dict[str, Any],
) -> list[dict[str, Any]]:
    """Returns AI starter items while preserving explicit named setup items."""

    setup_items = setup.get("starter_items", [])

    if not isinstance(setup_items, list):
        setup_items = []

    completed_items = [item for item in ai_items if isinstance(item, dict)]
    for item in completed_items:
        source_index = _optional_int(item.get("source_index"))
        if source_index is None or not (0 <= source_index < len(setup_items)):
            continue
        setup_item = setup_items[source_index]
        if not isinstance(setup_item, dict) or bool(setup_item.get("requires_ai_invention")):
            continue
        item["storage_location"] = (
            " ".join(
                str(setup_item.get("storage_location", "actively_carried") or "actively_carried")
                .strip()
                .split()
            )[:120]
            or "actively_carried"
        )
    original_completed_count = len(completed_items)
    used_source_indexes = {
        source_index
        for source_index in (
            _optional_int(item.get("source_index"))
            for item in completed_items
            if isinstance(item, dict)
        )
        if source_index is not None and source_index >= 0
    }
    seen_names = {
        str(item.get("name", "")).strip().casefold()
        for item in completed_items
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }

    for index, setup_item in enumerate(setup_items):
        if index in used_source_indexes:
            continue

        if not isinstance(setup_item, dict):
            continue

        requires_ai_invention = bool(setup_item.get("requires_ai_invention"))

        if requires_ai_invention and len(completed_items) >= STARTER_INVENTORY_MIN_ITEMS:
            continue

        fallback_item = _fallback_starter_item_from_setup(setup_item, source_index=index)

        if not fallback_item:
            continue

        folded_name = fallback_item["name"].casefold()

        if folded_name in seen_names:
            continue

        completed_items.append(fallback_item)
        seen_names.add(folded_name)

    while len(completed_items) < STARTER_INVENTORY_MIN_ITEMS:
        fallback_item = _starter_inventory_top_up_item(seen_names)

        if fallback_item is None:
            break

        completed_items.append(fallback_item)
        seen_names.add(fallback_item["name"].casefold())

    if len(completed_items) > original_completed_count:
        added_count = len(completed_items) - original_completed_count

        if original_completed_count < STARTER_INVENTORY_MIN_ITEMS:
            LOGGER.warning(
                "Gemini returned fewer than %s complete starter item(s); added %s "
                "fallback item(s) so the new save starts with enough inventory.",
                STARTER_INVENTORY_MIN_ITEMS,
                added_count,
            )
        else:
            LOGGER.warning(
                "Gemini omitted %s explicit starter item(s); preserved named setup item(s).",
                added_count,
            )

    return completed_items


def _fallback_starter_item_from_setup(
    raw_item: Any,
    *,
    source_index: int = -1,
) -> dict[str, Any] | None:
    """Builds a structured starter item from a wizard entry when AI output is partial."""

    if not isinstance(raw_item, dict):
        return None

    name = str(raw_item.get("name", "")).strip()
    item_request = str(raw_item.get("item_request", "")).strip()
    description = str(raw_item.get("description", "")).strip()

    if not name:
        name = _starter_item_name_from_request(item_request)

    if not name:
        return None

    return {
        "name": name,
        "category": str(raw_item.get("category", "Item")).strip() or "Item",
        "quantity": max(1, _safe_int(raw_item.get("quantity"), 1)),
        "description": description
        or item_request
        or "Player-requested starter item awaiting AI detail.",
        "value_base_units": max(0, _safe_int(raw_item.get("value_base_units"), 0)),
            "storage_location": (
                " ".join(
                    str(raw_item.get("storage_location", "actively_carried") or "actively_carried")
                    .strip()
                    .split()
                )[:120]
                or "actively_carried"
            ),
        "source_index": source_index,
        **{
            field_name: raw_item[field_name]
            for field_name in (
                "item_type",
                "weapon_hands",
                "damage",
                "damage_type",
                "attack_skill",
                "attack_range_feet",
                "ammunition_type_required",
                "clip_size",
                "bullets_per_attack",
                "ammunition_type",
                "covers_body_parts",
                "armor_rating",
            )
            if field_name in raw_item
        },
    }


def _starter_inventory_top_up_item(seen_names: set[str]) -> dict[str, Any] | None:
    """Returns a neutral fallback item for short AI starter inventories."""

    fallback_items = [
        {
            "name": "Personal Pack",
            "category": "Container",
            "description": "A sturdy pack for keeping essential belongings close.",
            "value_base_units": 5,
        },
        {
            "name": "Packed Meal",
            "category": "Supply",
            "description": "Simple food set aside for the first stretch of travel.",
            "value_base_units": 2,
        },
        {
            "name": "Water Flask",
            "category": "Supply",
            "description": "A refillable flask of clean drinking water.",
            "value_base_units": 2,
        },
        {
            "name": "Utility Tool",
            "category": "Tool",
            "description": "A compact everyday tool for small repairs and practical tasks.",
            "value_base_units": 4,
        },
        {
            "name": "Weather-Ready Clothes",
            "category": "Clothing",
            "description": "Durable clothing suitable for uncertain conditions.",
            "value_base_units": 6,
        },
        {
            "name": "Personal Keepsake",
            "category": "Personal",
            "description": "A small memento connecting the character to their past.",
            "value_base_units": 1,
        },
    ]

    for fallback_item in fallback_items:
        if fallback_item["name"].casefold() in seen_names:
            continue

        return {
            **fallback_item,
            "quantity": 1,
            "source_index": -1,
        }

    return None


def _starter_item_name_from_request(item_request: str) -> str:
    """Derives a compact item name from a natural-language starter item request."""

    clean_request = str(item_request or "").strip()

    if not clean_request:
        return ""

    candidate = clean_request

    for separator in [" that ", " which ", " with ", " used ", " for ", ".", ",", ";", ":"]:
        before_separator = candidate.split(separator, 1)[0].strip()

        if before_separator:
            candidate = before_separator

    words = [
        word.strip("'\"()[]{}")
        for word in candidate.split()
        if word.strip("'\"()[]{}")
    ]

    while words and words[0].casefold() in {"a", "an", "the", "my", "his", "her", "their", "our"}:
        words.pop(0)

    if not words:
        return ""

    return " ".join(words[:5]).title()


def _resolved_skill_checks_for_context(event_results: list[Any]) -> list[dict[str, Any]]:
    """Converts applied skill-check results into serializable narration context."""

    resolved_checks: list[dict[str, Any]] = []

    for result in event_results:
        if getattr(result, "event_type", "") != "SkillCheckRequestedEvent":
            continue

        if getattr(result, "status", "") != "applied":
            continue

        payload = getattr(result, "payload", {})

        if not isinstance(payload, dict):
            continue

        resolved_checks.append(
            {
                **payload,
                "status": getattr(result, "status", ""),
                "message": getattr(result, "message", ""),
            }
        )

    return resolved_checks


def _optional_int(value: Any) -> int | None:
    """Parses an optional integer."""

    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _append_ai_context_line(existing_context: str, line: str) -> str:
    """Appends an AI-facing setup context line if it is not already present."""

    clean_existing = str(existing_context or "").strip()
    clean_line = str(line or "").strip()

    if not clean_line:
        return clean_existing

    if clean_line in clean_existing.splitlines():
        return clean_existing

    if clean_existing:
        return f"{clean_existing}\n\n{clean_line}"

    return clean_line


def _calendar_type_from_settings(settings: dict[str, Any]) -> str:
    """Infers which calendar option should be selected for saved settings."""

    if bool(settings.get("ai_generated", False)):
        return "ai_generated"

    if str(settings.get("calendar_type", "")).strip().casefold() == "ai_generated":
        return "ai_generated"

    for key, value in GREGORIAN_CALENDAR_SETTINGS.items():
        if key == "time_display":
            continue

        if settings.get(key) != value:
            return "custom"

    return "gregorian"


def _build_season_settings(
    *,
    names: list[str],
    hints: list[str],
    count: int,
) -> list[dict[str, str]]:
    """Builds season setting dictionaries from comma-separated UI lists."""

    seasons: list[dict[str, str]] = []

    for index in range(max(1, count)):
        name = names[index] if index < len(names) else ""
        hint = hints[index] if index < len(hints) else ""
        seasons.append(
            {
                "name": name,
                "weather_hint": hint,
            }
        )

    return seasons


def _set_combo_to_data(combo: QComboBox, value: str) -> None:
    """Selects a combo-box item by its stored data value."""

    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return

    combo.setCurrentIndex(0)


def _set_combo_to_text(combo: QComboBox, text: str) -> None:
    """Selects a combo-box item by its visible text."""

    clean_text = str(text or "").strip().casefold()

    for index in range(combo.count()):
        if combo.itemText(index).strip().casefold() == clean_text:
            combo.setCurrentIndex(index)
            return

    combo.setCurrentIndex(0)


def _add_combo_options(combo: QComboBox, options: dict[str, str]) -> None:
    """Adds keyed display options to a combo box."""

    for value, label in options.items():
        combo.addItem(label, value)


def _set_markdown_text(text_edit: QTextEdit, markdown_text: str) -> None:
    """Sets read-only body text as Markdown when the Qt runtime supports it."""

    if hasattr(text_edit, "setMarkdown"):
        text_edit.setMarkdown(str(markdown_text or ""))
        return

    text_edit.setPlainText(str(markdown_text or ""))


def _safe_int(value, default: int) -> int:
    """Converts a value to int with a fallback."""

    return domain_safe_int(value, default)


def _split_list(raw_text: str) -> list[str]:
    """Splits comma-separated UI text into a clean string list."""

    return [
        value.strip()
        for value in raw_text.split(",")
        if value.strip()
    ]


def _split_loot_items(text: str) -> list[str]:
    """Splits comma/semicolon-delimited loot names."""

    return [
        part.strip()
        for part in re.split(r"[,;]+", str(text or ""))
        if part.strip()
    ]


def _slug_for_id(value: str) -> str:
    """Returns a compact identifier fragment."""

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "combatant"


def _join_list(values) -> str:
    """Formats a list-like value for table display."""

    if not isinstance(values, list):
        return ""

    return ", ".join(str(value) for value in values if str(value).strip())


def _crafting_ingredient_catalog_choices(
    catalog_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Returns sorted catalog items that may be used as recipe ingredients."""

    choices = [
        item
        for item in catalog_items
        if str(item.get("name", "")).strip()
        and is_crafting_ingredient_category(item.get("category", ""))
    ]
    choices.sort(
        key=lambda item: (
            str(item.get("name", "")).casefold(),
            str(item.get("category", "")).casefold(),
        )
    )
    return choices


def _status_label(label: str, value_label: QLabel) -> QWidget:
    """Builds a compact story status display item."""

    wrapper = QWidget()
    layout = QVBoxLayout()
    title = QLabel(label)
    title.setStyleSheet("font-size: 11px; color: #666;")
    value_label.setStyleSheet("font-weight: bold;")
    layout.addWidget(title)
    layout.addWidget(value_label)
    layout.setContentsMargins(0, 0, 24, 8)
    wrapper.setLayout(layout)
    return wrapper


def _skill_level_label(level: int) -> str:
    """Formats skill level as an in-world-friendly training label."""

    labels = {
        1: "Novice",
        2: "Practiced",
        3: "Skilled",
        4: "Expert",
        5: "Master",
    }
    return labels.get(level, "Unknown")


def _skill_xp_progress_label(skill: dict[str, Any]) -> str:
    """Formats cumulative skill XP against the next level threshold."""

    level = max(1, min(MAX_SKILL_LEVEL, _safe_int(skill.get("level", 1), 1)))
    xp = max(0, _safe_int(skill.get("xp", 0), 0))
    if level >= MAX_SKILL_LEVEL:
        return f"{xp} / MAX"
    return f"{xp} / {XP_THRESHOLDS_BY_LEVEL[level + 1]}"


def _calendar_event_time_label(
    event: dict[str, Any],
    calendar_settings: dict[str, Any],
) -> str:
    """Formats an exact event time without reducing it to a narrative day part."""

    time_minutes = _safe_int(event.get("time_of_day_minutes", -1), -1)
    if time_minutes < 0:
        return ""
    display_mode = str(calendar_settings.get("time_display", "12_hour"))
    if display_mode == "narrative":
        display_mode = "12_hour"
    return format_time_of_day(time_minutes, display_mode)


_INVENTORY_INVARIANT_PLURALS = {
    "ammunition", "armor", "clothing", "equipment", "food", "footwear",
    "information", "oil", "pants", "rice", "scissors", "trousers", "water",
}
_INVENTORY_UNIT_ABBREVIATIONS = {
    "cl", "cm", "ft", "g", "gal", "in", "kg", "l", "lb", "lbs", "ml",
    "mm", "oz", "tbsp", "tsp",
}
_INVENTORY_IRREGULAR_PLURALS = {
    "child": "children", "foot": "feet", "goose": "geese", "knife": "knives",
    "leaf": "leaves", "loaf": "loaves", "man": "men", "mouse": "mice",
    "person": "people", "tooth": "teeth", "woman": "women", "wolf": "wolves",
}

def _selectable_label(value: Any) -> QLabel:
    """Creates a selectable, wrapping value label for a detail form."""

    label = QLabel(str(value or "—"))
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setWordWrap(True)
    return label

def _inventory_location_label(value: Any) -> str:
    """Returns a player-facing label without collapsing custom storage names."""

    location = " ".join(str(value or "actively_carried").strip().split())
    if location.casefold() == "actively_carried":
        return "Actively Carried"
    if location.casefold() == "home":
        return "Home"
    return location or "Actively Carried"

def _pluralize_inventory_phrase(value: Any, *, unit: bool = False) -> str:
    """Pluralizes the final word of a player-facing inventory label."""

    text = " ".join(str(value or "").strip().split())
    if not text:
        return text
    prefix, separator, word = text.rpartition(" ")
    leading = f"{prefix}{separator}" if separator else ""
    folded = word.casefold()
    if folded in _INVENTORY_INVARIANT_PLURALS:
        return text
    if unit and folded in _INVENTORY_UNIT_ABBREVIATIONS:
        return text
    irregular = _INVENTORY_IRREGULAR_PLURALS.get(folded)
    if irregular is not None:
        plural = irregular
    elif folded.endswith("s") and not folded.endswith(("ss", "us", "is")):
        return text
    elif folded.endswith("y") and len(word) > 1 and folded[-2] not in "aeiou":
        plural = f"{word[:-1]}ies"
    elif folded.endswith(("s", "x", "z", "ch", "sh")):
        plural = f"{word}es"
    else:
        plural = f"{word}s"
    if word.isupper():
        plural = plural.upper()
    elif word[:1].isupper():
        plural = plural[:1].upper() + plural[1:]
    return f"{leading}{plural}"

def _inventory_quantity_display(quantity: Any, quantity_unit: Any) -> str:
    """Formats an inventory amount as xN, omitting the implied `each` unit."""

    count = max(0, _safe_int(quantity, 0))
    unit = " ".join(str(quantity_unit or "each").strip().split()) or "each"
    if unit.casefold() == "each":
        return f"x{count}"
    display_unit = (
        _pluralize_inventory_phrase(unit, unit=True)
        if count != 1
        else unit
    )
    return f"x{count} {display_unit}"

def _inventory_item_display_name(name: Any, quantity: Any, quantity_unit: Any) -> str:
    """Pluralizes countable item names for multi-item `each` stacks."""

    clean_name = str(name or "Unnamed Item").strip() or "Unnamed Item"
    count = max(0, _safe_int(quantity, 0))
    unit = str(quantity_unit or "each").strip().casefold() or "each"
    if count > 1 and unit == "each":
        return _pluralize_inventory_phrase(clean_name)
    return clean_name


def _main_window_override(name: str, default: _T) -> _T:
    root = sys.modules.get("ai_adventure.ui.main_window")
    return cast(_T, getattr(root, name, default)) if root is not None else default


__all__ = [
    "ALL_CONTENT_HARM_CATEGORIES",
    "AiContextBuilder",
    "Any",
    "AppPaths",
    "AssetGenerationService",
    "AudioPreferencesService",
    "CHARACTER_PRONOUN_OPTIONS",
    "COMBAT_FOCUS_LABELS",
    "COMBAT_FOCUS_LEVELS",
    "COMBAT_PERSONALITIES",
    "COMBAT_RESOLUTION_MODES",
    "COMBAT_RESOLUTION_MODE_LABELS",
    "COMMON_MEASUREMENT_UNITS",
    "CONTENT_HARM_CATEGORY_OPTIONS",
    "CONTINUE_STORY_INSTRUCTION",
    "CRAFTING_INGREDIENT_CATEGORIES",
    "CRAFTING_INGREDIENT_CATEGORY_NAMES",
    "CRAFTING_ITEM_RARITIES",
    "CURRENCY_COLUMN_WIDTHS",
    "Callable",
    "DEFAULT_ALLOWED_CONTENT_HARM_CATEGORIES",
    "DEFAULT_ATTACK_RANGE_FEET",
    "DEFAULT_BASE_ARMOR_RATING",
    "DEFAULT_CALENDAR_SETTINGS",
    "DEFAULT_CHARACTER_PRONOUNS",
    "DEFAULT_IMAGE_LIMIT",
    "DEFAULT_IMAGE_MODEL",
    "DEFAULT_MODEL_INTELLIGENCE",
    "DEFAULT_MODEL_TONE",
    "DEFAULT_NARRATION_STYLE",
    "DEFAULT_NARRATION_TENSE",
    "DEFAULT_NARRATOR_VOICE",
    "DEFAULT_PLAYER_MAX_HEALTH",
    "DEFAULT_RESPONSE_LENGTH",
    "DEFAULT_STARTING_WEALTH_GUIDANCE",
    "DEFAULT_START_ELAPSED_MINUTES",
    "DEFAULT_TTS_SPEED_PERCENT",
    "DEFAULT_UNARMED_DAMAGE",
    "DuplicateSaveTitleError",
    "ECONOMY_EXAMPLE_COLUMN_WIDTHS",
    "EQUIPMENT_SLOTS",
    "EventApplier",
    "FALLBACK_CURRENCY_DENOMINATIONS",
    "GM_THINKING_FRAMES",
    "GM_THINKING_TIMER_INTERVAL_MS",
    "GREGORIAN_CALENDAR_SETTINGS",
    "GeminiConfigurationError",
    "GeminiNarrationService",
    "GeminiRequestError",
    "GeminiVisualAssetService",
    "IMAGE_MODEL_OPTIONS",
    "IMAGE_STYLE_OPTIONS",
    "LOGGER",
    "MAGIC_CASTING_MODES",
    "MAGIC_CASTING_MODE_LABELS",
    "MAX_SKILL_LEVEL",
    "MODEL_CATALOG_REVIEWED_DATE",
    "MODEL_INTELLIGENCE_OPTIONS",
    "MODEL_TONE_OPTIONS",
    "NARRATION_STYLE_OPTIONS",
    "NARRATION_TENSE_OPTIONS",
    "NPC_TURN_DELAY_MS",
    "NarrationPlayerProtocol",
    "NewGameService",
    "NewGameTemplate",
    "Path",
    "PronunciationMap",
    "Protocol",
    "QAbstractSpinBox",
    "QApplication",
    "QButtonGroup",
    "QCheckBox",
    "QColor",
    "QComboBox",
    "QCompleter",
    "QDialog",
    "QDialogButtonBox",
    "QEvent",
    "QFormLayout",
    "QFrame",
    "QGridLayout",
    "QGroupBox",
    "QHBoxLayout",
    "QHeaderView",
    "QIcon",
    "QInputDialog",
    "QLabel",
    "QLineEdit",
    "QListWidget",
    "QListWidgetItem",
    "QMainWindow",
    "QMenu",
    "QMessageBox",
    "QMouseEvent",
    "QObject",
    "QPalette",
    "QPixmap",
    "QPlainTextEdit",
    "QPoint",
    "QPushButton",
    "QResizeEvent",
    "QScrollArea",
    "QSize",
    "QSizePolicy",
    "QSlider",
    "QSpinBox",
    "QStackedWidget",
    "QStandardItem",
    "QStandardItemModel",
    "QStringListModel",
    "QStyle",
    "QStyleOptionViewItem",
    "QStyledItemDelegate",
    "QTabBar",
    "QTabWidget",
    "QTableWidget",
    "QTableWidgetItem",
    "QTextCursor",
    "QTextEdit",
    "QThread",
    "QTime",
    "QTimeEdit",
    "QTimer",
    "QTreeWidget",
    "QTreeWidgetItem",
    "QVBoxLayout",
    "QWheelEvent",
    "QWidget",
    "QWizard",
    "QWizardPage",
    "Qt",
    "RESPONSE_LENGTH_OPTIONS",
    "RepositoryBackedWidget",
    "SKILL_LEVEL_DESCRIPTIONS",
    "SKILL_PRESET_LEVEL_PLANS",
    "STARTER_ARMOR_COLUMN_WIDTHS",
    "STARTER_INVENTORY_MIN_ITEMS",
    "STARTER_ITEM_COLUMN_WIDTHS",
    "STARTER_WEAPON_COLUMN_WIDTHS",
    "STARTING_LOCATION_COLUMN_WIDTHS",
    "STARTING_NPC_COLUMN_WIDTHS",
    "STARTING_WEALTH_COLUMN_WIDTHS",
    "STORY_REVEAL_STALL_TIMEOUT_MS",
    "SampleVoiceCallback",
    "SaveFileOperationError",
    "SaveGameService",
    "SaveRepository",
    "SaveSummary",
    "Signal",
    "Slot",
    "SoundManagerProtocol",
    "StateManager",
    "StoryTurnService",
    "TABLE_CELL_HORIZONTAL_PADDING",
    "TABLE_CELL_VERTICAL_PADDING",
    "TABLE_INLINE_EDITOR_HEIGHT",
    "TABLE_INLINE_EDITOR_MIN_WIDTH",
    "TEXT_MODEL_OPTIONS",
    "THEME_NAMES",
    "UNRESOLVED_STATUS_TEXT",
    "VisualAssetRequest",
    "XP_THRESHOLDS_BY_LEVEL",
    "_AppTableWidget",
    "_DeselectSelectedRowFilter",
    "_INVENTORY_INVARIANT_PLURALS",
    "_INVENTORY_IRREGULAR_PLURALS",
    "_INVENTORY_UNIT_ABBREVIATIONS",
    "_NarrationPlayerClass",
    "_NoCellFocusDelegate",
    "_NoWheelComboBox",
    "_NoWheelSpinBox",
    "_SoundManagerClass",
    "_TableEditorWheelFilter",
    "_TableWheelPassthroughFilter",
    "_add_combo_options",
    "_allow_selected_row_deselection",
    "_append_ai_context_line",
    "_append_currency_table_row",
    "_append_economy_example_table_row",
    "_append_starter_armor_table_row",
    "_append_starter_item_table_row",
    "_append_starter_suggestion_table_row",
    "_append_starter_weapon_table_row",
    "_append_starting_location_table_row",
    "_append_starting_npc_table_row",
    "_application_uses_dark_theme",
    "_apply_audio_settings_to_managers",
    "_apply_new_game_crafting_knowledge",
    "_bool_setting",
    "_build_season_settings",
    "_build_starter_suggestion_table",
    "_button_row",
    "_calendar_event_time_label",
    "_calendar_type_from_settings",
    "_clamped_int",
    "_coerce_float",
    "_combo_current_data_text",
    "_configure_auto_height_table",
    "_configure_inline_table",
    "_configure_responsive_form",
    "_configure_responsive_table",
    "_configure_table_wheel_passthrough",
    "_configure_wrapping_table",
    "_crafting_ingredient_catalog_choices",
    "_create_narration_player",
    "_currency_denominations_from_table",
    "_custom_voice_display_text",
    "_dark_theme_palette",
    "_dark_theme_stylesheet",
    "_deduplicated_ai_skills",
    "_duplicate_skill_suffix",
    "_economy_examples_from_table",
    "_enable_table_sorting",
    "_fallback_starter_item_from_setup",
    "_final_start_location_for_save",
    "_finalized_location_aliases",
    "_finalized_skills_for_save",
    "_introductory_message_for_save",
    "_inventory_item_display_name",
    "_inventory_location_label",
    "_inventory_quantity_display",
    "_invoke_sample_voice_callback",
    "_is_player_provided_character_field",
    "_join_list",
    "_light_theme_palette",
    "_light_theme_stylesheet",
    "_main_window_override",
    "_metadata_int",
    "_metadata_text",
    "_narrator_voice_options",
    "_new_game_calendar_genre_hint",
    "_next_available_save_title",
    "_normalize_theme_name",
    "_optional_int",
    "_player_character_name_replacements",
    "_pluralize_inventory_phrase",
    "_populate_narrator_voice_combo",
    "_preserve_player_character_text",
    "_preserved_player_character_fields",
    "_refresh_repository_calendar_time",
    "_remove_table_row_by_button",
    "_replace_location_aliases",
    "_replace_location_aliases_in_travel_locations",
    "_replace_whole_name",
    "_resize_wrapping_table_rows",
    "_resolve_speaker_cues_for_repository",
    "_resolved_skill_checks_for_context",
    "_row_for_cell_widget",
    "_safe_int",
    "_screen_content_signature",
    "_scrollable_widget",
    "_selectable_label",
    "_set_combo_to_data",
    "_set_combo_to_text",
    "_set_generated_image",
    "_set_markdown_text",
    "_set_remove_row_button",
    "_set_table_column_widths",
    "_skill_level_label",
    "_skill_xp_progress_label",
    "_slider_row",
    "_slug_for_id",
    "_sort_descending",
    "_spin_pair_row",
    "_split_list",
    "_split_loot_items",
    "_split_save_title_suffix",
    "_starter_armor_from_table",
    "_starter_inventory_top_up_item",
    "_starter_item_kind",
    "_starter_item_name_from_request",
    "_starter_items_for_save",
    "_starter_items_from_table",
    "_starter_suggestions_from_table",
    "_starter_weapons_from_table",
    "_starting_location_options_from_table",
    "_starting_location_row_for_id",
    "_starting_location_row_id_for_row",
    "_starting_locations_from_table",
    "_starting_npcs_from_table",
    "_status_label",
    "_sync_currency_base_value_row",
    "_sync_starting_location_parent_dropdowns",
    "_sync_starting_npc_location_dropdowns",
    "_table_combo_box",
    "_table_item",
    "_table_line_edit",
    "_table_row_display_name",
    "_table_spin_box",
    "_text_model_from_ai_packet",
    "_travel_locations_for_save",
    "_unique_ai_skill_name",
    "_update_sort_state",
    "_use_soft_table_selection",
    "active_voice_spec_from_audio",
    "ai_generated_calendar_settings_or_fallback",
    "annotations",
    "apply_application_theme",
    "apply_pronunciation_map",
    "armor_rating_from_equipment",
    "assign_speaker_voices",
    "attack_bonus_from_skills",
    "attack_hit_probability",
    "available_automatic_template_name",
    "available_narrator_voices",
    "build_calendar_snapshot",
    "build_month_grid",
    "build_new_game_setup_packet",
    "build_visual_asset_requests",
    "calculate_team_threat_levels",
    "calculate_travel_estimate",
    "combat_team_defeated",
    "combatant_display_name",
    "deepcopy",
    "delete_new_game_template",
    "describe_currency_denominations",
    "describe_economy_examples",
    "domain_bool_setting",
    "domain_clamped_int",
    "domain_safe_int",
    "empty_equipment",
    "equipment_item_counts",
    "equipped_weapon_attack_skill",
    "equipped_weapon_combat_profile",
    "equipped_weapon_damage",
    "fallback_introductory_message",
    "fallback_world_summary",
    "find_reusable_inventory_asset",
    "format_currency_amount",
    "format_distance",
    "format_recipe_ingredients",
    "format_story_message",
    "format_time_of_day",
    "format_travel_time",
    "image_model_metadata",
    "image_style_metadata",
    "importlib",
    "is_ai_enabled",
    "is_crafting_ingredient_category",
    "is_playtesting_build",
    "is_tts_enabled",
    "item_is_valid_for_slot",
    "item_metadata",
    "json",
    "load_app_settings",
    "load_new_game_templates",
    "logging",
    "merge_authoritative_starting_calendar",
    "merge_custom_voices",
    "merge_pronunciation_maps",
    "next_living_index",
    "normalize_ai_mode_preferences",
    "normalize_app_settings",
    "normalize_character_pronouns",
    "normalize_combat_preferences",
    "normalize_combat_state",
    "normalize_custom_voices",
    "normalize_damage_expression",
    "normalize_economy_examples",
    "normalize_equipment",
    "normalize_image_model",
    "normalize_image_preferences",
    "normalize_known_location",
    "normalize_known_locations",
    "normalize_narration_preferences",
    "normalize_narrator_voice",
    "normalize_narrator_voice_spec",
    "normalize_new_game_setup",
    "normalize_pronunciation_map",
    "normalize_recipe_ingredient",
    "normalize_recipe_ingredients",
    "normalize_text_model",
    "normalize_tts_audio_fields",
    "normalize_tts_speed_percent",
    "normalize_tts_voice_mode",
    "normalize_voice_blend",
    "parse_note_tags",
    "parse_starter_items_text",
    "prefix_markdown_lines",
    "prepare_background_ambience_directory",
    "prepare_sound_directory",
    "prepare_sound_effect_directory",
    "random",
    "re",
    "read_api_key",
    "record_terms_acceptance",
    "refresh_calendar_time_projection",
    "resolve_starting_calendar_minute",
    "roll_combat_initiative",
    "roll_damage_expression",
    "save_app_settings",
    "save_new_game_template",
    "save_relative_image_filename",
    "save_scaled_jpeg",
    "set_authoritative_pronunciation",
    "shutil",
    "sort_inventory_items",
    "split_story_bubble_segments",
    "sys",
    "template_setup_has_changes",
    "text_model_metadata",
    "uuid",
    "voice_display_name",
    "wrap_markdown_text",
    "write_api_key",
]
