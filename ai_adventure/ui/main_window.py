from __future__ import annotations

from copy import deepcopy
import json
import re
import logging
import importlib
import random
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

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
from ai_adventure.inventory_sorting import sort_inventory_items
from ai_adventure.ui.story_bubbles import split_story_bubble_segments
from ai_adventure.visual_assets import (
    DEFAULT_IMAGE_LIMIT,
    DEFAULT_IMAGE_MODEL,
    DISPLAY_IMAGE_MAX_PIXELS,
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
    from ai_adventure.ai.gemini_service import (
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
    from ai_adventure.audio.narration import NarrationPlayer as _NarrationPlayerClass
    from ai_adventure.audio.sound_manager import (
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
    BODY_PARTS,
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
from ai_adventure.core.state_manager import StateManager
from ai_adventure.events.event_applier import EventApplier
from ai_adventure.new_game_setup import (
    CHARACTER_PRONOUN_OPTIONS,
    DEFAULT_CHARACTER_PRONOUNS,
    DEFAULT_STARTING_WEALTH_GUIDANCE,
    GREGORIAN_CALENDAR_SETTINGS,
    SKILL_LEVEL_PLAN,
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
from ai_adventure.persistence.save_repository import (
    DuplicateSaveTitleError,
    SaveFileOperationError,
    SaveRepository,
    SaveSummary,
)
from ai_adventure.skills.rules import MAX_SKILL_LEVEL, XP_THRESHOLDS_BY_LEVEL


LOGGER = logging.getLogger(__name__)


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
TABLE_INLINE_BUTTON_MIN_WIDTH = 96
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
    """Application-wide table defaults and reusable removable-row behavior."""

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

    def checked_rows(self, checkbox_column: int | None = None) -> list[int]:
        column = self.columnCount() - 1 if checkbox_column is None else checkbox_column
        rows: list[int] = []
        for row in range(self.rowCount()):
            cell_widget = self.cellWidget(row, column)
            checkbox = (
                cell_widget.findChild(QCheckBox)
                if cell_widget is not None
                else None
            )
            item = self.item(row, column)
            if (
                isinstance(checkbox, QCheckBox) and checkbox.isChecked()
            ) or (
                item is not None and item.checkState() == Qt.CheckState.Checked
            ):
                rows.append(row)
        return rows

    def remove_checked_rows(
        self,
        checkbox_column: int | None = None,
        *,
        preserve_first_row: bool = False,
    ) -> list[int]:
        rows = [
            row
            for row in self.checked_rows(checkbox_column)
            if not preserve_first_row or row != 0
        ]
        for row in reversed(rows):
            self.removeRow(row)
        return rows


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


class RefreshableScreen(Protocol):
    """Protocol for screens that can reload their data from the save repository."""

    def refresh(self) -> None:
        """Refreshes visible screen data."""
        ...


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


class _GeminiStoryWorker(QObject):
    """Runs one Gemini story request away from the Qt UI thread."""

    completed = Signal(object)
    configuration_error = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(
        self,
        context_packet: dict[str, Any],
        api_key_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._context_packet = context_packet
        self._api_key_path = api_key_path

    @Slot()
    def run(self) -> None:
        """Generates one story response and emits the result on completion."""

        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")

            result = GeminiNarrationService(api_key_path=self._api_key_path).generate_story_response(
                self._context_packet
            )
        except GeminiConfigurationError as error:
            LOGGER.warning("Gemini narration skipped: %s", error)
            self.configuration_error.emit(str(error))
        except GeminiRequestError as error:
            LOGGER.warning("Gemini narration request ended cleanly: %s", error)
            self.failed.emit()
        except Exception:
            LOGGER.exception("Gemini narration request failed.")
            self.failed.emit()
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()


class _GeminiSkillCheckPlanWorker(QObject):
    """Runs one lightweight skill-check planning request away from the UI thread."""

    completed = Signal(object)
    configuration_error = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(
        self,
        context_packet: dict[str, Any],
        api_key_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._context_packet = context_packet
        self._api_key_path = api_key_path

    @Slot()
    def run(self) -> None:
        """Generates one pre-narration skill-check plan."""

        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")

            result = GeminiNarrationService(api_key_path=self._api_key_path).plan_story_skill_checks(
                self._context_packet
            )
        except GeminiConfigurationError as error:
            LOGGER.warning("Gemini skill-check planning skipped: %s", error)
            self.configuration_error.emit(str(error))
        except GeminiRequestError as error:
            LOGGER.warning("Gemini skill-check request ended cleanly: %s", error)
            self.failed.emit()
        except Exception:
            LOGGER.exception("Gemini skill-check planning request failed.")
            self.failed.emit()
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()


class _GeminiNewGameWorker(QObject):
    """Runs one Gemini new-game request away from the Qt UI thread."""

    completed = Signal(object)
    configuration_error = Signal(str)
    request_failed = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(
        self,
        setup_packet: dict[str, Any],
        api_key_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._setup_packet = setup_packet
        self._api_key_path = api_key_path

    @Slot()
    def run(self) -> None:
        """Generates the initial world and emits the result on completion."""

        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")

            result = GeminiNarrationService(
                api_key_path=self._api_key_path,
            ).generate_new_game_world(self._setup_packet)
        except GeminiConfigurationError as error:
            LOGGER.warning("Gemini new-game synthesis skipped: %s", error)
            self.configuration_error.emit(str(error))
        except GeminiRequestError as error:
            LOGGER.warning("Gemini new-game synthesis ended cleanly: %s", error)
            self.request_failed.emit(str(error))
        except Exception:
            LOGGER.exception("Gemini new-game synthesis failed.")
            self.failed.emit()
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()


class _GeminiVisualAssetWorker(QObject):
    """Runs one separately billed image-only request away from the Qt UI thread."""

    completed = Signal(object, bytes, str)
    failed = Signal(object, str)
    finished = Signal()

    def __init__(
        self,
        request: VisualAssetRequest,
        *,
        api_key_path: Path,
        model: str,
    ) -> None:
        super().__init__()
        self._request = request
        self._api_key_path = api_key_path
        self._model = model

    @Slot()
    def run(self) -> None:
        """Generates one image without sharing the structured story request path."""

        try:
            image_bytes, mime_type = GeminiVisualAssetService(
                api_key_path=self._api_key_path,
                model=self._model,
            ).generate(self._request)
        except Exception as error:
            LOGGER.warning(
                "Gemini visual asset generation failed for %s %r: %s",
                self._request.subject_type,
                self._request.display_name,
                error,
            )
            self.failed.emit(self._request, str(error))
        else:
            self.completed.emit(self._request, image_bytes, mime_type)
        finally:
            self.finished.emit()


class _VisualAssetCoordinator(QObject):
    """Queues reusable image assets one at a time and applies results on the GUI thread."""

    assets_changed = Signal()
    initial_batch_finished = Signal(object)

    def __init__(
        self,
        *,
        images_dir: Path,
        api_key_path: Path,
        enabled: bool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.images_dir = images_dir.expanduser().resolve()
        self.api_key_path = api_key_path.expanduser().resolve()
        self.enabled = bool(enabled)
        self._queue: list[tuple[SaveRepository, VisualAssetRequest, str, int]] = []
        self._queued_asset_ids: set[str] = set()
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._initial_batch_repositories: set[str] = set()
        self._initial_batch_repository_objects: dict[str, SaveRepository] = {}
        if self.enabled:
            self.images_dir.mkdir(parents=True, exist_ok=True)

    def begin_initial_batch(self, repository: SaveRepository) -> None:
        """Defers the opening-scene reveal until this save's first images settle."""

        self._initial_batch_repositories.add(str(repository.db_path))
        self._initial_batch_repository_objects[str(repository.db_path)] = repository
        self.scan(repository)
        self._finish_initial_batch_if_ready(repository)

    def _is_initial_batch(self, repository: SaveRepository) -> bool:
        """Returns whether per-image refreshes are currently suppressed for a save."""

        return str(repository.db_path) in self._initial_batch_repositories

    def _initial_batch_has_pending_work(self, repository: SaveRepository) -> bool:
        """Checks queued, active, and still-generatable visual assets for a save."""

        if not self.enabled or not _bool_setting(
            repository.get_setting("images.enabled", True),
            True,
        ) or not read_api_key(self.api_key_path):
            return False
        limit = _clamped_int(
            repository.get_setting("images.maximum_generated", DEFAULT_IMAGE_LIMIT),
            DEFAULT_IMAGE_LIMIT,
            1,
            10_000,
        )
        if repository.visual_asset_generation_count() >= limit:
            return False

        repository_key = str(repository.db_path)
        if any(
            str(queued_repository.db_path) == repository_key
            for queued_repository, _request, _model, _limit in self._queue
        ):
            return True
        if self._thread is not None:
            worker_request = getattr(self._worker, "_request", None)
            if isinstance(worker_request, VisualAssetRequest):
                current_record = repository.get_visual_asset_by_id(
                    worker_request.asset_id
                )
                if current_record is not None and current_record.get("status") == "generating":
                    return True
        return any(
            str(record.get("status", "")) in {"queued", "generating"}
            for record in (
                repository.get_visual_asset_by_id(request.asset_id)
                for request in build_visual_asset_requests(repository)
            )
            if record is not None
        )

    def _finish_initial_batch_if_ready(self, repository: SaveRepository) -> None:
        """Emits one completion signal after the initial visual queue is drained."""

        repository_key = str(repository.db_path)
        if (
            repository_key in self._initial_batch_repositories
            and self._thread is None
            and not self._initial_batch_has_pending_work(repository)
        ):
            self._initial_batch_repositories.discard(repository_key)
            self._initial_batch_repository_objects.pop(repository_key, None)
            self.initial_batch_finished.emit(repository)

    def scan(self, repository: SaveRepository | None) -> None:
        """Registers cache hits and queues missing current entity images."""

        if repository is None or not self.enabled:
            return
        if not _bool_setting(repository.get_setting("images.enabled", True), True):
            return

        has_api_key = bool(read_api_key(self.api_key_path))
        model = str(
            repository.get_setting("images.model", DEFAULT_IMAGE_MODEL)
            or DEFAULT_IMAGE_MODEL
        ).strip()
        limit = _clamped_int(
            repository.get_setting("images.maximum_generated", DEFAULT_IMAGE_LIMIT),
            DEFAULT_IMAGE_LIMIT,
            1,
            10_000,
        )
        for request in build_visual_asset_requests(repository):
            relative_filename = save_relative_image_filename(repository, request)
            target_path = self.images_dir / relative_filename
            record = repository.ensure_visual_asset(
                asset_id=request.asset_id,
                subject_type=request.subject_type,
                subject_key=request.subject_key,
                display_name=request.display_name,
                descriptor_hash=request.descriptor_hash,
                filename=relative_filename,
                prompt=request.prompt,
                model=model,
                message_ids=request.message_ids,
                ready=target_path.is_file(),
            )
            if (
                target_path.is_file()
                or record.get("status") != "queued"
                or not has_api_key
            ):
                continue
            if request.asset_id in self._queued_asset_ids:
                continue
            reusable = find_reusable_inventory_asset(
                images_dir=self.images_dir,
                saves_dir=self.images_dir.parent / "saves",
                repository=repository,
                request=request,
            )
            if reusable is not None:
                try:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(reusable["source_path"], target_path)
                    repository.set_visual_asset_status(
                        request.asset_id,
                        "ready",
                        width=int(reusable.get("width", 0)),
                        height=int(reusable.get("height", 0)),
                    )
                    LOGGER.info(
                        "Reused visual asset %s for %s from another save (score %.1f).",
                        request.filename,
                        request.display_name,
                        float(reusable.get("score", 0.0)),
                    )
                    continue
                except OSError as error:
                    LOGGER.warning(
                        "Could not reuse visual asset for %s: %s",
                        request.display_name,
                        error,
                    )
            self._queue.append((repository, request, model, limit))
            self._queued_asset_ids.add(request.asset_id)
        self._start_next()
        self._finish_initial_batch_if_ready(repository)

    def retry_failed(self, repository: SaveRepository | None) -> int:
        """Requeues failed requests only after an explicit player action."""

        if repository is None:
            return 0
        reset_count = repository.reset_failed_visual_assets()
        if reset_count:
            self.scan(repository)
        return reset_count

    def _start_next(self) -> None:
        """Starts the next affordable queued request."""

        if self._thread is not None:
            return
        while self._queue:
            repository, request, model, limit = self._queue.pop(0)
            self._queued_asset_ids.discard(request.asset_id)
            record = repository.get_visual_asset_by_id(request.asset_id)
            if record is None or record.get("status") != "queued":
                continue
            if not _bool_setting(
                repository.get_setting("images.enabled", True),
                True,
            ):
                continue
            if repository.visual_asset_generation_count() >= limit:
                LOGGER.info(
                    "Visual asset generation limit reached for %s; leaving %s queued.",
                    repository.db_path,
                    request.asset_id,
                )
                continue

            repository.set_visual_asset_status(request.asset_id, "generating")
            thread = QThread(self)
            worker = _GeminiVisualAssetWorker(
                request,
                api_key_path=self.api_key_path,
                model=model,
            )
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.completed.connect(
                lambda completed_request, image_bytes, mime_type,
                repository=repository: self._handle_completed(
                    repository,
                    completed_request,
                    image_bytes,
                    mime_type,
                )
            )
            worker.failed.connect(
                lambda failed_request, message,
                repository=repository: self._handle_failed(
                    repository,
                    failed_request,
                    message,
                )
            )
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            # Use the coordinator's bound slot directly.  A lambda here can be
            # lost during Qt thread teardown, leaving the coordinator holding a
            # completed thread and preventing the next visual request from starting.
            thread.finished.connect(self._clear_worker)
            self._thread = thread
            self._worker = worker
            thread.start()
            return

    def _handle_completed(
        self,
        repository: SaveRepository,
        request: VisualAssetRequest,
        image_bytes: bytes,
        _mime_type: str,
    ) -> None:
        """Downscales and records one completed generated image."""

        try:
            width, height = save_scaled_jpeg(
                image_bytes,
                self.images_dir
                / save_relative_image_filename(repository, request),
            )
        except Exception as error:
            LOGGER.warning("Failed to save generated image %s: %s", request.filename, error)
            repository.set_visual_asset_status(
                request.asset_id,
                "failed",
                error_message=str(error),
            )
            return
        repository.set_visual_asset_status(
            request.asset_id,
            "ready",
            width=width,
            height=height,
        )
        record = repository.get_visual_asset_by_id(request.asset_id)
        LOGGER.info(
            "Generated visual asset %s (%sx%s) using %s.",
            request.filename,
            width,
            height,
            record.get("model", "") if record else DEFAULT_IMAGE_MODEL,
        )
        if not self._is_initial_batch(repository):
            self.assets_changed.emit()

    def _handle_failed(
        self,
        repository: SaveRepository,
        request: VisualAssetRequest,
        message: str,
    ) -> None:
        """Records one clean failure without automatic paid retries."""

        repository.set_visual_asset_status(
            request.asset_id,
            "failed",
            error_message=message,
        )
        if not self._is_initial_batch(repository):
            self.assets_changed.emit()

    @Slot()
    def _clear_worker(
        self,
        thread: QThread | None = None,
        worker: QObject | None = None,
    ) -> None:
        """Releases one worker and continues the serial queue."""

        if thread is None or self._thread is thread:
            self._thread = None
        if worker is None or self._worker is worker:
            self._worker = None
        LOGGER.debug(
            "Visual asset worker finished; %s request(s) remain queued.",
            len(self._queue),
        )
        self._start_next()
        if self._thread is None:
            for repository in tuple(self._initial_batch_repository_objects.values()):
                # Reconcile durable queued records in case a previous worker
                # exited before its in-memory queue entry was advanced.
                self.scan(repository)
                self._finish_initial_batch_if_ready(repository)


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


class MainWindow(QMainWindow):
    """
    Main application window.

    Owns the Main Menu and the in-game tab shell.
    """

    def __init__(self, app_paths: AppPaths) -> None:
        """
        Args:
            app_paths: Centralized application paths.
        """

        super().__init__()

        self.app_paths = app_paths
        self.active_repository: SaveRepository | None = None
        self._new_game_thread: QThread | None = None
        self._new_game_worker: QObject | None = None
        self._pending_new_game_repository: SaveRepository | None = None
        self._pending_new_game_setup: dict[str, Any] | None = None
        self._waiting_for_initial_visuals: SaveRepository | None = None
        self.playtesting_build = is_playtesting_build()
        self.ai_enabled = is_ai_enabled()
        self.tts_enabled = is_tts_enabled()
        self.app_settings = load_app_settings(
            self.app_paths.app_settings_path,
            fallback_theme=self._latest_saved_theme(),
            tts_enabled=self.tts_enabled,
        )
        self.menu_theme = _normalize_theme_name(self.app_settings["theme"])
        self.sound_manager = (
            None
            if self.playtesting_build or _SoundManagerClass is None
            else _SoundManagerClass(
                prepare_sound_directory(self.app_paths),
                prepare_sound_effect_directory(self.app_paths),
                prepare_background_ambience_directory(self.app_paths),
            )
        )
        self.narration_player = _create_narration_player(self.app_paths)

        self.application_name = (
            "AI Adventure Playtesting"
            if self.playtesting_build
            else "AI Adventure"
        )
        self.setWindowTitle(self.application_name)
        self._set_app_icon()
        self.resize(1100, 750)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.main_menu = MainMenuScreen(
            saves_dir=self.app_paths.saves_dir,
            on_new_game=self.start_new_game_wizard,
            on_load_game=self.load_game_from_path,
            on_settings=self.open_main_menu_settings,
            on_templates=self.open_new_game_templates,
            application_name=self.application_name,
            new_game_label="New Playtest" if self.playtesting_build else "New Game",
            show_templates=not self.playtesting_build,
        )

        self.game_shell = GameShell(
            on_return_to_menu=self.return_to_menu,
            on_theme_changed=self._apply_active_theme,
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
            tts_enabled=self.tts_enabled,
            on_app_tts_settings_saved=self._persist_app_tts_settings,
            global_tts_settings_provider=lambda: self.app_settings["audio"],
            custom_voice_storage_path=self.app_paths.app_settings_path,
            gemini_api_key_path=self.app_paths.gemini_api_key_path,
            generated_images_dir=self.app_paths.images_dir,
            playtesting_tools=self.playtesting_build,
            ai_enabled=self.ai_enabled,
        )
        self.game_shell.visual_asset_coordinator.initial_batch_finished.connect(
            self._handle_initial_visuals_ready
        )

        self.stack.addWidget(self.main_menu)
        self.stack.addWidget(self.game_shell)

        self.return_to_menu()

    def _set_app_icon(self) -> None:
        """Sets the main-window icon when the packaged icon is available."""

        icon_path = self.app_paths.app_icon_path

        if not icon_path.exists():
            LOGGER.warning("Application icon not found: %s", icon_path)
            return

        icon = QIcon(str(icon_path))

        if icon.isNull():
            LOGGER.warning("Application icon could not be loaded: %s", icon_path)
            return

        self.setWindowIcon(icon)

    def start_new_game_wizard(self) -> None:
        """Opens the New Game Wizard."""

        if self.playtesting_build:
            self._start_new_playtest()
            return

        should_continue, template_setup, loaded_template_name = (
            self._choose_new_game_template_setup()
        )

        if not should_continue:
            return

        wizard = NewGameWizard(
            self,
            template_setup=template_setup,
            tts_enabled=self.tts_enabled,
            audio_defaults=self.app_settings["audio"],
            voice_options=_narrator_voice_options(self.narration_player),
            on_sample_voice=self._play_narrator_sample,
            on_tts_settings_saved=self._persist_app_tts_settings,
            custom_voice_storage_path=self.app_paths.app_settings_path,
            api_key_path=self.app_paths.gemini_api_key_path,
            terms_acceptance_path=self.app_paths.gemini_terms_acceptance_path,
            sound_manager=self.sound_manager,
        )
        loaded_template_baseline = (
            wizard.build_setup() if loaded_template_name else None
        )

        while True:
            if wizard.exec() != QDialog.DialogCode.Accepted:
                return

            clean_setup = wizard.build_setup()
            template_save_name: str | None = None
            if (
                loaded_template_name
                and loaded_template_baseline is not None
                and template_setup_has_changes(loaded_template_baseline, clean_setup)
            ):
                template_action = self._prompt_for_modified_template_action(
                    loaded_template_name
                )
                if template_action is None:
                    continue
                if template_action == "overwrite":
                    template_save_name = loaded_template_name
                elif template_action == "save_as_new":
                    template_save_name = self._prompt_for_new_template_name(
                        loaded_template_name
                    )
                    if template_save_name is None:
                        continue

            while True:
                clean_setup = wizard.build_setup()
                try:
                    self._create_new_game_from_setup(
                        clean_setup,
                        template_save_name=template_save_name,
                        auto_save_template_if_available=loaded_template_name is None,
                    )
                    return
                except DuplicateSaveTitleError as error:
                    new_title = self._prompt_for_duplicate_save_title(
                        clean_setup["title"],
                        str(error),
                    )

                    if new_title is None:
                        break

                    wizard.title_input.setText(new_title)
                except Exception:
                    LOGGER.exception("Failed to create new game.")
                    QMessageBox.critical(
                        self,
                        "New Game Failed",
                        "Could not create a new game.",
                    )
                    return

    def _start_new_playtest(self) -> None:
        """Creates an isolated save without invoking setup generation or Gemini."""

        suggested_title = _next_available_save_title(
            self.app_paths.saves_dir,
            "Combat Playtest",
        )
        title, accepted = QInputDialog.getText(
            self,
            "New Playtest",
            "Playtest save name:",
            QLineEdit.EchoMode.Normal,
            suggested_title,
        )

        if not accepted:
            return

        clean_title = title.strip()

        if not clean_title:
            QMessageBox.warning(
                self,
                "Missing Playtest Name",
                "Enter a name for the playtest save.",
            )
            return

        try:
            repository = SaveRepository.create_new_save(
                self.app_paths.saves_dir,
                clean_title,
            )
        except DuplicateSaveTitleError as error:
            QMessageBox.warning(self, "Playtest Name Already Exists", str(error))
            return
        except Exception:
            LOGGER.exception("Failed to create playtest save.")
            QMessageBox.critical(
                self,
                "New Playtest Failed",
                "Could not create the playtest save.",
            )
            return

        repository.set_setting("theme", self.menu_theme)
        self.open_repository(repository)

    def open_main_menu_settings(self) -> None:
        """Opens app-level settings from the Main Menu."""

        dialog = MainMenuSettingsDialog(
            self,
            settings=self.app_settings,
            tts_enabled=self.tts_enabled,
            music_enabled=not self.playtesting_build,
            voice_options=_narrator_voice_options(self.narration_player),
            on_sample_voice=self._play_narrator_sample,
            custom_voice_storage_path=self.app_paths.app_settings_path,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._apply_app_settings(dialog.build_settings(), persist=True)

    def open_new_game_templates(self) -> None:
        """Opens the app-level new-game template manager."""

        dialog = NewGameTemplateManagerDialog(
            self,
            template_path=self.app_paths.new_game_templates_path,
            legacy_template_path=self.app_paths.legacy_new_game_template_path,
            sound_manager=self.sound_manager,
            audio_defaults=self.app_settings["audio"],
            voice_options=_narrator_voice_options(self.narration_player),
            on_sample_voice=self._play_narrator_sample,
            on_tts_settings_saved=self._persist_app_tts_settings,
            custom_voice_storage_path=self.app_paths.app_settings_path,
        )
        dialog.exec()

    def _choose_new_game_template_setup(
        self,
    ) -> tuple[bool, dict[str, Any] | None, str | None]:
        """Asks whether a new game should start blank or from a saved template."""

        choice = QMessageBox(self)
        choice.setWindowTitle("New Game")
        choice.setText("How would you like to start this new game?")
        scratch_button = choice.addButton(
            "Start From Scratch",
            QMessageBox.ButtonRole.AcceptRole,
        )
        template_button = choice.addButton(
            "Load Template",
            QMessageBox.ButtonRole.ActionRole,
        )
        cancel_button = choice.addButton(QMessageBox.StandardButton.Cancel)
        choice.exec()

        clicked_button = choice.clickedButton()

        if clicked_button == cancel_button:
            return False, None, None

        if clicked_button != template_button:
            return True, None, None

        templates = load_new_game_templates(
            self.app_paths.new_game_templates_path,
            legacy_template_path=self.app_paths.legacy_new_game_template_path,
        )

        if not templates:
            QMessageBox.information(
                self,
                "No Templates Found",
                "No saved new-game templates were found. Starting from scratch instead.",
            )
            return True, None, None

        template_names = [template.name for template in templates]
        selected_name, accepted = QInputDialog.getItem(
            self,
            "Load New Game Template",
            "Template:",
            template_names,
            0,
            False,
        )

        if not accepted:
            return False, None, None

        for template in templates:
            if template.name == selected_name:
                return (
                    True,
                    self._template_setup_with_available_title(
                        template.setup,
                        template_name=template.name,
                    ),
                    template.name,
                )

        return True, None, None

    def _prompt_for_modified_template_action(self, template_name: str) -> str | None:
        """Asks how changed wizard values should affect the loaded template."""

        choice = QMessageBox(self)
        choice.setWindowTitle("Template Changed")
        choice.setText(
            f'It looks like you made changes to the template "{template_name}".'
        )
        choice.setInformativeText("What would you like to do?")
        overwrite_button = choice.addButton(
            "Overwrite Existing Template",
            QMessageBox.ButtonRole.AcceptRole,
        )
        save_new_button = choice.addButton(
            "Save as New Template",
            QMessageBox.ButtonRole.ActionRole,
        )
        game_only_button = choice.addButton(
            "Create Game Only",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel_button = choice.addButton(QMessageBox.StandardButton.Cancel)
        choice.exec()
        clicked = choice.clickedButton()
        if clicked == overwrite_button:
            return "overwrite"
        if clicked == save_new_button:
            return "save_as_new"
        if clicked == game_only_button:
            return "game_only"
        if clicked == cancel_button:
            return None
        return None

    def _prompt_for_new_template_name(self, source_name: str) -> str | None:
        """Prompts until a non-empty, unused template name is supplied."""

        existing_names = {
            template.name.casefold()
            for template in load_new_game_templates(
                self.app_paths.new_game_templates_path,
                legacy_template_path=self.app_paths.legacy_new_game_template_path,
                normalize_setups=False,
            )
        }
        suggested_name = f"{source_name} Copy"
        suffix = 2
        while suggested_name.casefold() in existing_names:
            suggested_name = f"{source_name} Copy {suffix}"
            suffix += 1

        while True:
            template_name, accepted = QInputDialog.getText(
                self,
                "Save as New Template",
                "New template name:",
                QLineEdit.EchoMode.Normal,
                suggested_name,
            )
            if not accepted:
                return None
            clean_name = template_name.strip()
            if not clean_name:
                QMessageBox.warning(
                    self,
                    "Missing Template Name",
                    "Enter a template name.",
                )
                continue
            if clean_name.casefold() in existing_names:
                QMessageBox.warning(
                    self,
                    "Template Already Exists",
                    f'A template named "{clean_name}" already exists.',
                )
                continue
            return clean_name

    def _template_setup_with_available_title(
        self,
        setup: dict[str, Any],
        *,
        template_name: str = "",
    ) -> dict[str, Any]:
        """Returns template setup with a title that does not collide with saves."""

        template_setup = dict(setup)
        template_setup["title"] = _next_available_save_title(
            self.app_paths.saves_dir,
            template_name or str(template_setup.get("title", "")),
        )
        return template_setup

    def _prompt_for_duplicate_save_title(
        self,
        current_title: str,
        message: str,
    ) -> str | None:
        """Asks for a replacement save title after a duplicate title collision."""

        suggested_title = _next_available_save_title(
            self.app_paths.saves_dir,
            current_title,
        )

        while True:
            new_title, accepted = QInputDialog.getText(
                self,
                "Save Name Already Exists",
                f"{message}\n\nNew save name:",
                QLineEdit.EchoMode.Normal,
                suggested_title,
            )

            if not accepted:
                return None

            clean_title = new_title.strip()

            if clean_title:
                return clean_title

            QMessageBox.warning(
                self,
                "Missing Save Name",
                "Enter a save name before starting.",
            )

    def create_new_game(self, setup: dict[str, Any]) -> bool:
        """
        Creates a new save and opens it.

        Args:
            setup: New-game wizard setup dictionary.

        Returns:
            True when the save was created and opened.
        """

        clean_setup = self._normalize_new_game_setup_for_runtime(setup)

        try:
            self._create_new_game_from_setup(
                clean_setup,
                auto_save_template_if_available=True,
            )
        except DuplicateSaveTitleError as error:
            QMessageBox.warning(self, "Save Name Already Exists", str(error))
            return False
        except Exception:
            LOGGER.exception("Failed to create new game.")
            QMessageBox.critical(self, "New Game Failed", "Could not create a new game.")
            return False

        return True

    def _create_new_game_from_setup(
        self,
        clean_setup: dict[str, Any],
        *,
        template_save_name: str | None = None,
        auto_save_template_if_available: bool = False,
    ) -> None:
        """Creates a new save from normalized setup and opens the shell."""

        clean_setup = self._normalize_new_game_setup_for_runtime(clean_setup)

        repository = SaveRepository.create_new_save(
            self.app_paths.saves_dir,
            clean_setup["title"],
            setup=clean_setup,
        )
        repository.set_setting("theme", self.menu_theme)

        effective_template_name = template_save_name
        if effective_template_name is None and auto_save_template_if_available:
            effective_template_name = available_automatic_template_name(
                self.app_paths.new_game_templates_path,
                clean_setup,
                legacy_template_path=self.app_paths.legacy_new_game_template_path,
            )

        if effective_template_name and not save_new_game_template(
            self.app_paths.new_game_templates_path,
            {**clean_setup, "title": effective_template_name},
            template_name=effective_template_name,
        ):
            QMessageBox.warning(
                self,
                "Template Not Saved",
                "The game was created, but the template could not be saved.",
            )
        self.open_repository(repository, new_game=True)

        if not self.ai_enabled:
            return

        self.game_shell.story_screen.set_initial_generation_pending(True)
        self.game_shell.menu_button.setEnabled(False)
        self._start_new_game_generation(repository, clean_setup)

    def _start_new_game_generation(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
    ) -> None:
        """Starts initial world synthesis without blocking the Qt UI thread."""

        try:
            setup_packet = build_new_game_setup_packet(
                setup,
                valid_music_tracks=(
                    self.sound_manager.get_valid_track_names()
                    if self.sound_manager is not None
                    else []
                ),
                valid_sound_effect_tracks=(
                    self.sound_manager.get_valid_sound_effect_names()
                    if self.sound_manager is not None
                    else []
                ),
                valid_background_ambience_tracks=(
                    getattr(
                        self.sound_manager,
                        "get_valid_background_ambience_names",
                        lambda: [],
                    )()
                    if self.sound_manager is not None
                    else []
                ),
            )
        except Exception:
            LOGGER.exception("Failed to build the Gemini new-game request.")
            self._apply_new_game_fallback(repository, setup)
            self._complete_new_game_generation(repository)
            return

        thread = QThread(self)
        worker = _GeminiNewGameWorker(
            setup_packet,
            self.app_paths.gemini_api_key_path,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.completed.connect(self._handle_new_game_generation_result)
        worker.configuration_error.connect(
            self._handle_new_game_generation_configuration_error
        )
        worker.request_failed.connect(self._handle_new_game_generation_request_failure)
        worker.failed.connect(self._handle_new_game_generation_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda thread=thread, worker=worker: self._clear_new_game_worker(
                thread,
                worker,
            )
        )

        self._pending_new_game_repository = repository
        self._pending_new_game_setup = dict(setup)
        self._new_game_thread = thread
        self._new_game_worker = worker
        thread.start()

    @Slot(object)
    def _handle_new_game_generation_result(self, result: Any) -> None:
        """Applies a completed initial-world response on the Qt UI thread."""

        repository = self._pending_new_game_repository
        setup = self._pending_new_game_setup

        if repository is None or setup is None:
            return

        try:
            self._apply_new_game_generation_result(repository, setup, result)
        except Exception:
            LOGGER.exception("Failed to apply Gemini new-game synthesis.")
            self._apply_new_game_fallback(repository, setup)
        finally:
            self._complete_new_game_generation(repository)

    @Slot(str)
    def _handle_new_game_generation_configuration_error(self, _message: str) -> None:
        """Uses the ordinary local opening when Gemini is not configured."""

        self._finish_failed_new_game_generation(temporary_failure=False)

    @Slot(str)
    def _handle_new_game_generation_request_failure(self, _message: str) -> None:
        """Uses a request-failure opening when Gemini is temporarily unavailable."""

        self._finish_failed_new_game_generation(temporary_failure=True)

    @Slot()
    def _handle_new_game_generation_failure(self) -> None:
        """Uses the ordinary local opening after an unexpected worker failure."""

        self._finish_failed_new_game_generation(temporary_failure=False)

    def _finish_failed_new_game_generation(self, *, temporary_failure: bool) -> None:
        """Applies the appropriate local fallback after a worker error."""

        repository = self._pending_new_game_repository
        setup = self._pending_new_game_setup

        if repository is None or setup is None:
            return

        try:
            self._apply_new_game_fallback(
                repository,
                setup,
                temporary_failure=temporary_failure,
            )
        finally:
            self._complete_new_game_generation(repository)

    def _apply_new_game_fallback(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
        *,
        temporary_failure: bool = False,
    ) -> None:
        """Writes a local opening after Gemini could not initialize a new game."""

        self._apply_fallback_currency_if_needed(repository, setup)
        repository.set_world_summary(fallback_world_summary(setup))
        repository.append_history(
            "story",
            (
                "Gemini is temporarily unavailable, so this new game opened "
                "with a local fallback. Your save is safe; try another action "
                "shortly."
                if temporary_failure
                else fallback_introductory_message(setup)
            ),
        )

    def _apply_new_game_generation_result(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
        result: Any,
    ) -> None:
        """Persists one completed Gemini new-game result."""

        LOGGER.debug(f"INITIAL NEW GAME GEMINI PROMPT: \n\n{result}")
        self._apply_new_game_ai_state(repository, setup, result)
        repository.set_world_summary(
            _preserve_player_character_text(
                result.world_summary,
                setup,
                result.finalized_character,
            )
        )
        introductory_message = _preserve_player_character_text(
            _introductory_message_for_save(setup, result),
            setup,
            result.finalized_character,
        )
        speaker_cues = _resolve_speaker_cues_for_repository(
            repository,
            self.narration_player,
            getattr(result, "speaker_cues", []),
        )
        message_id = repository.append_history(
            "story",
            introductory_message,
            sound_effect_cues=result.sound_effect_cues,
            speaker_cues=speaker_cues,
        )

        if result.suggested_events:
            event_results = EventApplier(
                repository,
                message_id=message_id,
            ).apply_events(result.suggested_events)
            applied_count = sum(
                1 for event_result in event_results if event_result.status == "applied"
            )
            skipped_count = len(event_results) - applied_count
            LOGGER.info(
                "Applied %s new-game event(s); skipped %s.",
                applied_count,
                skipped_count,
            )

    def _complete_new_game_generation(self, repository: SaveRepository) -> None:
        """Refreshes the active game and prepares its opening visual batch."""

        self._pending_new_game_repository = None
        self._pending_new_game_setup = None
        self.game_shell.menu_button.setEnabled(True)

        if self.active_repository is not repository:
            return

        _apply_audio_settings_to_managers(
            repository,
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
        )
        self._waiting_for_initial_visuals = repository
        self.game_shell.visual_asset_coordinator.begin_initial_batch(repository)
        self.game_shell.refresh_screens()

    def _reveal_initial_story(self, repository: SaveRepository) -> None:
        """Shows and narrates the opening only after initial images are settled."""

        if self.active_repository is not repository:
            return

        self.game_shell.story_screen.set_initial_generation_pending(False)
        self.game_shell.story_screen.narrate_latest_story(
            reveal_progressively=True,
        )

    @Slot(object)
    def _handle_initial_visuals_ready(self, repository: SaveRepository) -> None:
        """Reveals the opening scene once its initial image batch is complete."""

        if self._waiting_for_initial_visuals is not repository:
            return
        self._waiting_for_initial_visuals = None
        self._reveal_initial_story(repository)

    @Slot()
    def _clear_new_game_worker(
        self,
        thread: QThread | None = None,
        worker: QObject | None = None,
    ) -> None:
        """Drops references after the new-game request thread exits."""

        if thread is None or self._new_game_thread is thread:
            self._new_game_thread = None

        if worker is None or self._new_game_worker is worker:
            self._new_game_worker = None

    def _normalize_new_game_setup_for_runtime(self, setup: dict[str, Any]) -> dict[str, Any]:
        """Normalizes setup and disables narrator settings when TTS is unavailable."""

        raw_setup = dict(setup) if isinstance(setup, dict) else {}
        audio = dict(self.app_settings["audio"])

        if isinstance(raw_setup.get("audio"), dict):
            audio.update(raw_setup["audio"])

        raw_setup["audio"] = audio
        clean_setup = normalize_new_game_setup(raw_setup)

        if self.tts_enabled:
            return clean_setup

        audio = dict(clean_setup["audio"])
        audio.update(normalize_tts_audio_fields(audio, tts_enabled=False))
        audio["tts_voice"] = DEFAULT_NARRATOR_VOICE
        audio["tts_voice_mode"] = "preset"

        clean_setup = dict(clean_setup)
        clean_setup["audio"] = audio
        return clean_setup

    def _apply_new_game_ai_state(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
        result,
    ) -> None:
        """Persists AI-finalized new-game character, skills, and start location."""

        start_location = _final_start_location_for_save(setup, result)

        if start_location:
            repository.set_state_value("location", start_location)

        finalized_location_aliases: dict[str, str] = {}
        if getattr(result, "locations", None):
            travel_locations = _travel_locations_for_save(
                result.locations,
                setup,
                result,
            )
            finalized_location_aliases = _finalized_location_aliases(
                travel_locations,
                setup,
            )
            repository.set_travel_locations(
                _replace_location_aliases_in_travel_locations(
                    travel_locations,
                    finalized_location_aliases,
                )
            )

        repository.ensure_travel_locations()

        for secret in getattr(result, "gm_secrets", []):
            repository.upsert_gm_secret(
                secret_id=str(secret.get("secret_id", "")),
                title=str(secret.get("title", "")),
                details=str(secret.get("details", "")),
                reveal_condition=str(secret.get("reveal_condition", "")),
                related_npc_ids=list(secret.get("related_npc_ids", [])),
                related_locations=list(secret.get("related_locations", [])),
                status=str(secret.get("status", "active")),
            )

        for entry in getattr(result, "miscellaneous", []):
            repository.upsert_miscellaneous(
                misc_id=str(entry.get("misc_id", "")),
                name=str(entry.get("name", "")),
                category=str(entry.get("category", "")),
                details=str(entry.get("details", "")),
            )

        setup_calendar = setup.get("calendar", {})
        if (
            isinstance(setup_calendar, dict)
            and bool(setup_calendar.get("ai_generated", False))
        ):
            repository.set_calendar_settings(
                ai_generated_calendar_settings_or_fallback(
                    getattr(result, "calendar_settings", {}),
                    genre_hint=_new_game_calendar_genre_hint(setup, result),
                )
            )

        setup_starting_calendar = setup.get("starting_calendar", {})
        result_starting_calendar = merge_authoritative_starting_calendar(
            result.starting_calendar,
            setup_starting_calendar,
        )

        if result_starting_calendar:
            current_minute = resolve_starting_calendar_minute(
                result_starting_calendar,
                repository.get_calendar_settings(),
                default_current_minute=DEFAULT_START_ELAPSED_MINUTES,
            )
            calendar_snapshot = build_calendar_snapshot(
                current_minute,
                repository.get_calendar_settings(),
            )
            repository.set_current_calendar_minute(current_minute)
            repository.set_state_value("time", calendar_snapshot["display_label"])

        authoritative_starting_weather = str(
            setup.get("starting_weather", "") or ""
        ).strip()
        if authoritative_starting_weather:
            repository.set_state_value("weather", authoritative_starting_weather)
        elif result.start_weather:
            repository.set_state_value("weather", result.start_weather)

        starting_wealth = setup.get("starting_wealth", {})
        starting_wealth_mode = (
            str(starting_wealth.get("mode", "basic")).strip().casefold()
            if isinstance(starting_wealth, dict)
            else "basic"
        )
        if (
            starting_wealth_mode == "basic"
            and result.finalized_starting_currency_balance_base_units is not None
        ):
            repository.set_state_value(
                "currency.balance",
                str(result.finalized_starting_currency_balance_base_units),
            )

        if not setup.get("currency_denominations"):
            if result.finalized_currency_denominations:
                repository.set_currency_denominations(result.finalized_currency_denominations)
                repository.set_setting(
                    "currency.description",
                    result.finalized_currency_description
                    or describe_currency_denominations(
                        result.finalized_currency_denominations,
                        fallback_denominations=[],
                    ),
                )
            else:
                LOGGER.warning("AI new-game setup omitted generated currency denominations.")
                self._apply_fallback_currency_if_needed(repository, setup)

        if result.selected_genre:
            repository.set_setting("world.genre", result.selected_genre)
            repository.set_setting(
                "ai.additional_context",
                _append_ai_context_line(
                    str(repository.get_setting("ai.additional_context", "")),
                    f"Selected genre: {result.selected_genre}",
                ),
            )

        character = _preserved_player_character_fields(
            setup,
            result.finalized_character,
        )

        if character:
            if character.get("name"):
                repository.set_setting("player_name", character["name"])
            if character.get("name_pronunciation"):
                repository.set_setting(
                    "player.name_pronunciation",
                    character["name_pronunciation"],
                )
            if character.get("pronouns"):
                repository.set_setting("player.pronouns", character["pronouns"])
            if character.get("appearance"):
                repository.set_setting("player.appearance", character["appearance"])
            if character.get("backstory"):
                repository.set_setting("player.backstory", character["backstory"])
            if character.get("notes"):
                repository.set_setting("player.notes", character["notes"])

        pronunciation_map = merge_pronunciation_maps(
            setup.get("pronunciation_map", {}),
            getattr(result, "pronunciation_map", {}),
        )
        setup_character = setup.get("character", {})
        if isinstance(setup_character, dict) and setup_character.get(
            "name_pronunciation"
        ):
            pronunciation_map = set_authoritative_pronunciation(
                pronunciation_map,
                setup_character.get("name", ""),
                setup_character.get("name_pronunciation", ""),
            )
        repository.set_setting("tts.pronunciation_map", pronunciation_map)

        finalized_skills = (
            []
            if setup.get("skill_preset") == "blank"
            else _finalized_skills_for_save(
                result.finalized_skills,
                setup.get("skills", []),
            )
        )

        if finalized_skills:
            repository.replace_skills(finalized_skills)
        elif result.finalized_skills:
            LOGGER.warning(
                "Skipped AI-finalized skills because they did not match the starting skill plan."
            )

        magic_setup = setup.get("magic", {})
        if not isinstance(magic_setup, dict):
            magic_setup = {}
        starting_spell_requests = magic_setup.get("starting_spell_requests", [])
        if not isinstance(starting_spell_requests, list):
            starting_spell_requests = []
        if (
            bool(magic_setup.get("enabled", False))
            and str(magic_setup.get("starting_spells_mode", "basic")).casefold()
            == "basic"
            and starting_spell_requests
        ):
            finalized_starting_spells = [
                spell
                for spell in getattr(result, "finalized_starting_spells", [])
                if isinstance(spell, dict)
                and 0
                <= _safe_int(spell.get("source_index"), -1)
                < len(starting_spell_requests)
            ]
            learned_spells = repository.learn_starting_spells(
                finalized_starting_spells,
                source="Gemini New Game",
            )
            if len(learned_spells) != len(starting_spell_requests):
                LOGGER.warning(
                    "Gemini finalized %s of %s requested starting spell(s).",
                    len(learned_spells),
                    len(starting_spell_requests),
                )

        finalized_starter_items = _starter_items_for_save(
            result.finalized_starter_items,
            setup,
        )

        if finalized_starter_items:
            repository.replace_inventory_items(finalized_starter_items)

        _apply_new_game_crafting_knowledge(
            repository,
            result,
            location_aliases=finalized_location_aliases,
        )

    def _apply_fallback_currency_if_needed(
        self,
        repository: SaveRepository,
        setup: dict[str, Any],
    ) -> None:
        """Stores a neutral currency when AI generation cannot run."""

        if setup.get("currency_denominations"):
            return

        repository.set_currency_denominations(FALLBACK_CURRENCY_DENOMINATIONS)
        repository.set_setting(
            "currency.description",
            describe_currency_denominations(
                FALLBACK_CURRENCY_DENOMINATIONS,
                fallback_denominations=[],
            ),
        )

    def load_game_from_path(self, db_path: Path) -> None:
        """
        Loads an existing save.

        Args:
            db_path: Path to the save database.
        """

        if not db_path.exists():
            LOGGER.error("Attempted to load missing save database: %s", db_path)
            QMessageBox.warning(self, "Load Failed", "That save file no longer exists.")
            self.main_menu.refresh_saves()
            return

        try:
            repository = SaveRepository(db_path)
        except Exception:
            LOGGER.exception("Failed to load save from %s.", db_path)
            QMessageBox.critical(self, "Load Failed", "Could not load that save.")
            return

        self.open_repository(repository)

    def open_repository(
        self,
        repository: SaveRepository,
        *,
        new_game: bool = False,
    ) -> None:
        """
        Opens a repository in the game shell.

        Args:
            repository: Loaded save repository.
        """

        self.active_repository = repository
        self.game_shell.set_repository(
            repository,
            initially_hide_empty_tabs=new_game,
        )
        self.game_shell.story_screen.set_initial_generation_pending(
            new_game and self.ai_enabled
        )
        self._apply_active_theme()
        self.stack.setCurrentWidget(self.game_shell)

        title = repository.get_meta("title", default="AI Adventure")
        self.setWindowTitle(f"{self.application_name} - {title}")

        LOGGER.info("Opened save: %s", repository.db_path)

    def return_to_menu(self) -> None:
        """Returns to the Main Menu."""

        self.active_repository = None
        self.game_shell.set_repository(None)
        self._apply_app_settings(self.app_settings, persist=False)
        self.main_menu.refresh_saves()
        self.stack.setCurrentWidget(self.main_menu)
        self.setWindowTitle(self.application_name)

    def _apply_active_theme(self) -> None:
        """Applies the theme saved for the currently loaded adventure."""

        if self.active_repository is None:
            self._apply_app_settings(self.app_settings, persist=False)
            return

        self.menu_theme = _normalize_theme_name(
            self.active_repository.get_setting("theme", "Light")
        )
        self.app_settings = normalize_app_settings(
            {
                **self.app_settings,
                "theme": self.menu_theme,
            },
            fallback_theme=self.menu_theme,
            tts_enabled=self.tts_enabled,
        )
        save_app_settings(self.app_paths.app_settings_path, self.app_settings)
        apply_application_theme(self.menu_theme)

    def _apply_app_settings(self, settings: dict[str, Any], *, persist: bool) -> None:
        """Applies app-level settings used when no save is active."""

        self.app_settings = normalize_app_settings(
            settings,
            fallback_theme=self.menu_theme,
            tts_enabled=self.tts_enabled,
        )
        self.menu_theme = _normalize_theme_name(self.app_settings["theme"])

        if persist:
            save_app_settings(self.app_paths.app_settings_path, self.app_settings)

        apply_application_theme(self.menu_theme)
        self._apply_menu_audio_settings()

    def _persist_app_tts_settings(self, audio_settings: dict[str, Any]) -> None:
        """Persists app-level TTS defaults from the New Game wizard."""

        self._apply_app_settings(
            {
                **self.app_settings,
                "audio": {
                    **self.app_settings["audio"],
                    **audio_settings,
                },
            },
            persist=True,
        )

    def _apply_menu_audio_settings(self) -> None:
        """Applies app-level audio settings while the Main Menu is active."""

        audio = self.app_settings["audio"]

        if self.sound_manager is not None:
            self.sound_manager.set_music_volume(audio["music_volume"])
            self.sound_manager.set_music_enabled(audio["music_enabled"])
            self.sound_manager.set_sound_effects_volume(audio["sound_effects_volume"])
            self.sound_manager.set_sound_effects_enabled(audio["sound_effects_enabled"])
            if hasattr(self.sound_manager, "set_background_ambience_volume"):
                self.sound_manager.set_background_ambience_volume(
                    audio["background_ambience_volume"]
                )
            if hasattr(self.sound_manager, "set_background_ambience_enabled"):
                self.sound_manager.set_background_ambience_enabled(
                    audio["background_ambience_enabled"]
                )

            if not audio["music_enabled"]:
                self.sound_manager.stop_music(clear_current=False)
            if (
                not audio["background_ambience_enabled"]
                and hasattr(self.sound_manager, "stop_background_ambience")
            ):
                self.sound_manager.stop_background_ambience(clear_current=False)

        if self.narration_player is not None:
            self.narration_player.set_volume(audio["tts_volume"])
            if hasattr(self.narration_player, "set_speed"):
                self.narration_player.set_speed(audio["tts_speed"])
            if hasattr(self.narration_player, "set_voice"):
                self.narration_player.set_voice(active_voice_spec_from_audio(audio))
            self.narration_player.set_enabled(audio["narrator_enabled"])

    def _play_narrator_sample(
        self,
        voice: str,
        volume: int,
        speed: int = DEFAULT_TTS_SPEED_PERCENT,
    ) -> bool:
        """Plays a local narrator voice sample."""

        if self.narration_player is None or not hasattr(self.narration_player, "play_sample"):
            return False

        return bool(
            self.narration_player.play_sample(
                voice=normalize_narrator_voice_spec(voice),
                volume=volume,
                speed=speed,
            )
        )

    def _latest_saved_theme(self) -> str:
        """Reads the most recent save's theme for the Main Menu."""

        for summary in SaveRepository.list_saves(self.app_paths.saves_dir):
            try:
                theme = SaveRepository.read_save_setting(
                    summary.db_path,
                    "theme",
                    "Light",
                )
            except Exception:
                LOGGER.exception("Failed to read theme from save: %s", summary.db_path)
                continue

            return _normalize_theme_name(theme)

        return "Light"


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

    def _narrator_enabled_value(self) -> bool:
        """Returns the requested narrator setting."""

        if self.narrator_enabled_checkbox is None:
            return False

        return self.narrator_enabled_checkbox.isChecked()

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


class MainMenuScreen(QWidget):
    """Main Menu with New Game, Load Game, and Settings actions."""

    def __init__(
        self,
        saves_dir: Path,
        on_new_game,
        on_load_game,
        on_settings,
        on_templates,
        *,
        application_name: str = "AI Adventure",
        new_game_label: str = "New Game",
        show_templates: bool = True,
    ) -> None:
        """
        Args:
            saves_dir: Directory containing save folders.
            on_new_game: Callback for creating a new game.
            on_load_game: Callback for loading a save by database path.
            on_settings: Callback for opening app-level settings.
            on_templates: Callback for managing reusable new-game templates.
        """

        super().__init__()

        self.saves_dir = saves_dir
        self.on_new_game = on_new_game
        self.on_load_game = on_load_game
        self.on_settings = on_settings
        self.on_templates = on_templates

        title_label = QLabel(application_name)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 32px; font-weight: bold;")

        new_game_button = QPushButton(new_game_label)
        new_game_button.clicked.connect(self._handle_new_game)

        self.save_combo = QComboBox()
        self.save_combo.currentIndexChanged.connect(
            lambda _index: self._sync_save_action_buttons()
        )

        self.load_button = QPushButton("Load Game")
        self.load_button.clicked.connect(self._handle_load_game)

        self.rename_save_button = QPushButton("Rename Save")
        self.rename_save_button.clicked.connect(self._handle_rename_save)

        self.delete_save_button = QPushButton("Delete Save")
        self.delete_save_button.clicked.connect(self._handle_delete_save)

        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.on_settings)

        self.templates_button = QPushButton("New Game Templates")
        self.templates_button.clicked.connect(self.on_templates)
        self.templates_button.setVisible(show_templates)

        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(title_label)
        layout.addSpacing(30)

        layout.addWidget(new_game_button)
        layout.addWidget(self.templates_button)
        layout.addWidget(self.settings_button)

        layout.addSpacing(30)
        layout.addWidget(QLabel("Existing Saves:"))
        layout.addWidget(self.save_combo)
        layout.addWidget(self.load_button)
        layout.addWidget(self.rename_save_button)
        layout.addWidget(self.delete_save_button)
        layout.addStretch()

        wrapper = QHBoxLayout()
        wrapper.addStretch()
        wrapper.addLayout(layout, stretch=2)
        wrapper.addStretch()

        self.setLayout(wrapper)
        self.refresh_saves()

    def refresh_saves(self) -> None:
        """Reloads save summaries into the load-game combo box."""

        self.save_combo.clear()

        saves = SaveRepository.list_saves(self.saves_dir)

        if not saves:
            self.save_combo.addItem("No saves found", None)
            self._sync_save_action_buttons()
            return

        for summary in saves:
            label = self._format_save_summary(summary)
            self.save_combo.addItem(label, summary.db_path)

        self._sync_save_action_buttons()

    def _handle_new_game(self) -> None:
        """Handles the New Game button."""

        self.on_new_game()

    def _handle_load_game(self) -> None:
        """Handles the Load Game button."""

        db_path = self.save_combo.currentData()

        if db_path is None:
            QMessageBox.information(self, "No Save Selected", "There is no save to load.")
            return

        self.on_load_game(Path(db_path))

    def _handle_rename_save(self) -> None:
        """Prompts for a new title for the selected save."""

        db_path = self.save_combo.currentData()

        if db_path is None:
            QMessageBox.information(self, "No Save Selected", "There is no save to rename.")
            return

        summary = self._selected_save_summary()
        current_title = summary.title if summary is not None else ""
        new_title, accepted = QInputDialog.getText(
            self,
            "Rename Save",
            "Save name:",
            text=current_title,
        )

        if not accepted:
            return

        clean_title = new_title.strip()

        if not clean_title:
            QMessageBox.warning(self, "Missing Save Name", "Enter a save name.")
            return

        try:
            SaveRepository.rename_save(self.saves_dir, Path(db_path), clean_title)
        except DuplicateSaveTitleError as error:
            QMessageBox.warning(self, "Save Name Already Exists", str(error))
            return
        except SaveFileOperationError as error:
            QMessageBox.warning(self, "Save Not Renamed", str(error))
            self.refresh_saves()
            return
        except OSError as error:
            QMessageBox.warning(self, "Save Not Renamed", f"Could not rename the save: {error}")
            self.refresh_saves()
            return

        self.refresh_saves()

    def _handle_delete_save(self) -> None:
        """Confirms and deletes the selected save."""

        db_path = self.save_combo.currentData()

        if db_path is None:
            QMessageBox.information(self, "No Save Selected", "There is no save to delete.")
            return

        summary = self._selected_save_summary()
        title = summary.title if summary is not None else "this save"
        result = QMessageBox.question(
            self,
            "Delete Save",
            f"Delete '{title}' permanently?",
        )

        if result != QMessageBox.StandardButton.Yes:
            return

        try:
            SaveRepository.delete_save(self.saves_dir, Path(db_path))
        except SaveFileOperationError as error:
            QMessageBox.warning(self, "Save Not Deleted", str(error))
            self.refresh_saves()
            return
        except OSError as error:
            QMessageBox.warning(self, "Save Not Deleted", f"Could not delete the save: {error}")
            self.refresh_saves()
            return

        self.refresh_saves()

    def _selected_save_summary(self) -> SaveSummary | None:
        """Returns the selected save summary, if it still exists."""

        db_path = self.save_combo.currentData()

        if db_path is None:
            return None

        selected_path = Path(db_path).resolve()

        for summary in SaveRepository.list_saves(self.saves_dir):
            if summary.db_path.resolve() == selected_path:
                return summary

        return None

    def _sync_save_action_buttons(self) -> None:
        """Enables save actions only when a real save is selected."""

        has_save = self.save_combo.currentData() is not None
        self.load_button.setEnabled(has_save)
        self.rename_save_button.setEnabled(has_save)
        self.delete_save_button.setEnabled(has_save)

    def _format_save_summary(self, summary: SaveSummary) -> str:
        """
        Formats a save summary for display.

        Args:
            summary: Save summary.

        Returns:
            Display label.
        """

        modified = summary.last_modified.strftime("%Y-%m-%d %I:%M %p")
        return f"{summary.title} - {modified}"


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
        self._new_game_ai_settings = {
            key: value for key, value in settings.items()
            if key not in {"narration_tense", "narration_style"}
        }
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
            "model_intelligence": default_modes["model_intelligence"],
            "model_tone": default_modes["model_tone"],
            "response_length": default_modes["response_length"],
            "allowed_content_categories": default_modes[
                "allowed_content_categories"
            ],
            "additional_context": "",
        }

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
            }
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

    def _open_new_game_ai_settings_dialog(self) -> None:
        """Opens the shared A.I. settings modal during new-game creation."""

        dialog = AISettingsDialog(
            self,
            settings={
                **self._new_game_ai_settings,
                "narration_tense": (
                    self.narration_tense_combo.currentData()
                    or DEFAULT_NARRATION_TENSE
                ),
                "narration_style": (
                    self.narration_style_combo.currentData()
                    or DEFAULT_NARRATION_STYLE
                ),
            },
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._apply_new_game_ai_settings(dialog.build_ai_settings())

    def _apply_new_game_ai_settings(self, raw_settings: dict[str, Any]) -> None:
        """Applies modal values to wizard state and its visible summary."""

        modes = normalize_ai_mode_preferences(raw_settings)
        narration = normalize_narration_preferences(
            {
                "tense": raw_settings.get("narration_tense"),
                "style": raw_settings.get("narration_style"),
            }
        )
        self._new_game_ai_settings = {
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
        _set_combo_to_data(self.narration_tense_combo, narration["tense"])
        _set_combo_to_data(self.narration_style_combo, narration["style"])
        self._refresh_new_game_ai_settings_summary()

    def _refresh_new_game_ai_settings_summary(self) -> None:
        """Shows the active wizard A.I. modes without expanding every control."""

        if not hasattr(self, "ai_settings_summary_label"):
            return

        modes = normalize_ai_mode_preferences(self._new_game_ai_settings)
        narration = normalize_narration_preferences(
            {
                "tense": self.narration_tense_combo.currentData(),
                "style": self.narration_style_combo.currentData(),
            }
        )
        content_label = (
            "No Restrictions"
            if not modes["blocked_content_labels"]
            else (
                ", ".join(modes["allowed_content_labels"])
                if modes["allowed_content_labels"]
                else "No Harm Categories"
            )
        )
        self.ai_settings_summary_label.setText(
            f"{modes['model_intelligence_label']} · "
            f"{modes['model_tone_label']} · "
            f"{modes['response_length_label']} · "
            f"{content_label}\n"
            f"{narration['tense_label']} · {narration['style_label']}"
        )

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

        self.narration_tense_combo = _NoWheelComboBox(page)
        _add_combo_options(self.narration_tense_combo, NARRATION_TENSE_OPTIONS)
        _set_combo_to_data(self.narration_tense_combo, DEFAULT_NARRATION_TENSE)
        self.narration_tense_combo.setVisible(False)

        self.narration_style_combo = _NoWheelComboBox(page)
        _add_combo_options(self.narration_style_combo, NARRATION_STYLE_OPTIONS)
        _set_combo_to_data(self.narration_style_combo, DEFAULT_NARRATION_STYLE)
        self.narration_style_combo.setVisible(False)

        self.ai_settings_button = QPushButton("A.I. Settings...")
        self.ai_settings_button.clicked.connect(
            self._open_new_game_ai_settings_dialog
        )
        self.ai_settings_summary_label = QLabel()
        self.ai_settings_summary_label.setWordWrap(True)
        self.ai_settings_summary_label.setStyleSheet("font-size: 11px;")
        self._refresh_new_game_ai_settings_summary()

        self.world_context_input = QTextEdit()
        self.world_context_input.setPlaceholderText(
            "Named locations, factions, guilds, religions, political tensions, tone, themes..."
        )

        layout = QFormLayout()
        _configure_responsive_form(layout)
        layout.addRow("Game Name:", self.title_input)
        layout.addRow("Genre:", self.genre_input)
        layout.addRow("Game Style:", self.game_style_input)
        layout.addRow("Artificial Intelligence:", self.ai_settings_button)
        layout.addRow("", self.ai_settings_summary_label)
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

    def _narrator_enabled_value(self) -> bool:
        """Returns the requested new-game narrator setting."""

        if self.narrator_enabled_checkbox is None:
            return False

        return self.narrator_enabled_checkbox.isChecked()

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

        dialog = CalendarSettingsDialog(self._custom_calendar_settings, self)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._custom_calendar_settings = dialog.build_settings()


class _DetachableTabBar(QTabBar):
    """Movable tab bar that requests detachment after an outside drag release."""

    detach_requested = Signal(str, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pressed_tab_key = ""
        self._press_global_position = QPoint()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Remembers the stable key for the tab being dragged."""

        index = self.tabAt(event.position().toPoint())
        self._pressed_tab_key = (
            str(self.tabData(index) or "") if index >= 0 else ""
        )
        self._press_global_position = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Detaches a dragged tab when the pointer is released outside the bar."""

        global_position = event.globalPosition().toPoint()
        dragged_far_enough = (
            global_position - self._press_global_position
        ).manhattanLength() >= QApplication.startDragDistance()
        released_outside = not self.rect().contains(event.position().toPoint())
        tab_key = self._pressed_tab_key
        super().mouseReleaseEvent(event)
        self._pressed_tab_key = ""

        if tab_key and dragged_far_enough and released_outside:
            self.detach_requested.emit(tab_key, global_position)


class _DetachedTabWindow(QMainWindow):
    """Independent, non-modal window containing one detached game screen."""

    return_requested = Signal(str)

    def __init__(
        self,
        tab_key: str,
        title: str,
        screen: QWidget,
        parent: QWidget,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.tab_key = tab_key
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCentralWidget(screen)
        # QTabWidget.removeTab() leaves its page explicitly hidden. Reparenting
        # the page as a central widget does not clear that hidden state, so make
        # the transferred screen visible before this window is shown.
        screen.show()
        self.resize(1000, 720)

    def closeEvent(self, event: Any) -> None:
        """Returns the screen to the main tab strip before destroying the window."""

        self.takeCentralWidget()
        self.return_requested.emit(self.tab_key)
        event.accept()


class GameShell(QWidget):
    """In-game shell containing the core play screens."""

    def __init__(
        self,
        on_return_to_menu,
        *,
        on_theme_changed=None,
        sound_manager: SoundManagerProtocol | None = None,
        narration_player: NarrationPlayerProtocol | None = None,
        tts_enabled: bool = True,
        on_app_tts_settings_saved: Callable[[dict[str, Any]], None] | None = None,
        global_tts_settings_provider: Callable[[], dict[str, Any]] | None = None,
        custom_voice_storage_path: Path | str | None = None,
        gemini_api_key_path: Path | str | None = None,
        generated_images_dir: Path | str | None = None,
        playtesting_tools: bool = False,
        ai_enabled: bool = True,
    ) -> None:
        """
        Args:
            on_return_to_menu: Callback for returning to the Main Menu.
        """

        super().__init__()

        self.on_return_to_menu = on_return_to_menu
        self.on_theme_changed = on_theme_changed
        self.sound_manager = sound_manager
        self.narration_player = narration_player
        self.tts_enabled = bool(tts_enabled)
        self.on_app_tts_settings_saved = on_app_tts_settings_saved
        self.global_tts_settings_provider = global_tts_settings_provider
        self.custom_voice_storage_path = custom_voice_storage_path
        self.gemini_api_key_path = (
            Path(gemini_api_key_path).expanduser().resolve()
            if gemini_api_key_path is not None
            else None
        )
        self.generated_images_dir = (
            Path(generated_images_dir).expanduser().resolve()
            if generated_images_dir is not None
            else Path.cwd() / "images"
        )
        self.playtesting_tools = bool(playtesting_tools)
        self.ai_enabled = bool(ai_enabled)
        self.repository: SaveRepository | None = None
        self.visual_asset_coordinator = _VisualAssetCoordinator(
            images_dir=self.generated_images_dir,
            api_key_path=(
                self.gemini_api_key_path or Path.cwd() / ".gemini_api_key.txt"
            ),
            enabled=(
                self.ai_enabled
                and not self.playtesting_tools
                and gemini_api_key_path is not None
                and generated_images_dir is not None
            ),
            parent=self,
        )
        self.visual_asset_coordinator.assets_changed.connect(
            self._handle_visual_assets_changed
        )
        self.title_label = QLabel("No Save Loaded")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")

        self.menu_button = QPushButton("Main Menu")
        self.menu_button.clicked.connect(self.on_return_to_menu)

        top_bar = QHBoxLayout()
        top_bar.addWidget(self.title_label)
        top_bar.addStretch()
        top_bar.addWidget(self.menu_button)

        self.tabs = QTabWidget()
        self.tab_bar = _DetachableTabBar(self.tabs)
        self.tabs.setTabBar(self.tab_bar)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self._tab_specs: dict[str, tuple[RepositoryBackedWidget, str, bool]] = {}
        self._tab_order: list[str] = []
        self._detached_windows: dict[str, _DetachedTabWindow] = {}
        self._tab_notifications: set[str] = set()
        self._tab_content_signatures: dict[str, str] = {}
        self._smart_hidden_tabs: set[str] = set()

        self.story_screen = StoryScreen(
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
            api_key_path=self.gemini_api_key_path,
        )
        self.character_screen = CharacterScreen(
            playtesting_tools=self.playtesting_tools,
            tts_enabled=self.tts_enabled,
        )
        self.travel_screen = TravelScreen(
            on_travel_requested=self._submit_travel_request,
        )
        self.bestiary_screen = BestiaryScreen()
        self.calendar_screen = CalendarScreen(playtesting_tools=self.playtesting_tools)
        self.inventory_screen = InventoryScreen(
            playtesting_tools=self.playtesting_tools,
        )
        self.combat_screen = CombatScreen(
            playtesting_tools=self.playtesting_tools,
        )
        self.npcs_screen = NpcsScreen()
        self.party_screen = PartyScreen()
        self.active_tasks_screen = ActiveTasksScreen()
        self.skills_screen = SkillsScreen()
        self.magic_screen = MagicScreen()
        self.alchemy_screen = AlchemyNotebookScreen(
            playtesting_tools=self.playtesting_tools,
        )
        self.notes_screen = NotesScreen()
        self.settings_screen = SettingsScreen(
            on_audio_settings_changed=self._apply_audio_settings,
            on_theme_changed=self._apply_theme,
            tts_enabled=self.tts_enabled,
            voice_options=_narrator_voice_options(self.narration_player),
            on_sample_voice=self._sample_narrator_voice,
            on_app_tts_settings_saved=self.on_app_tts_settings_saved,
            global_tts_settings_provider=self.global_tts_settings_provider,
            custom_voice_storage_path=self.custom_voice_storage_path,
            ai_enabled=self.ai_enabled,
            sound_manager=self.sound_manager,
            music_enabled=not self.playtesting_tools,
            playtesting_tools=self.playtesting_tools,
        )

        self.screens: list[RepositoryBackedWidget] = [
            self.story_screen,
            self.character_screen,
            self.travel_screen,
            self.bestiary_screen,
            self.calendar_screen,
            self.inventory_screen,
            self.combat_screen,
            self.npcs_screen,
            self.party_screen,
            self.active_tasks_screen,
            self.skills_screen,
            self.magic_screen,
            self.alchemy_screen,
            self.notes_screen,
            self.settings_screen,
        ]

        for screen in self.screens:
            screen.set_visual_assets_dir(self.generated_images_dir)
            screen.on_repository_changed = self._handle_screen_repository_changed

        visible_tabs = (
            [
                ("character", self.character_screen, "Character", True),
                ("calendar", self.calendar_screen, "Calendar", True),
                ("inventory", self.inventory_screen, "Inventory", True),
                ("magic", self.magic_screen, "Magic", True),
                ("combat", self.combat_screen, "Combat", True),
                ("party", self.party_screen, "Party", True),
                ("settings", self.settings_screen, "Settings", True),
            ]
            if self.playtesting_tools
            else [
                ("conversation", self.story_screen, "Conversation", False),
                ("character", self.character_screen, "Character", True),
                ("travel", self.travel_screen, "Travel", True),
                ("bestiary", self.bestiary_screen, "Bestiary", True),
                ("calendar", self.calendar_screen, "Calendar", True),
                ("inventory", self.inventory_screen, "Inventory", True),
                ("combat", self.combat_screen, "Combat", True),
                ("party", self.party_screen, "Party", True),
                ("npcs", self.npcs_screen, "NPCs", True),
                ("skills", self.skills_screen, "Skills", True),
                ("magic", self.magic_screen, "Magic", True),
                ("crafting", self.alchemy_screen, "Crafting", True),
                ("notes", self.notes_screen, "Notes", True),
                ("settings", self.settings_screen, "Settings", True),
            ]
        )
        for tab_key, screen, label, closable in visible_tabs:
            self._register_tab(tab_key, screen, label, closable)

        self.add_tab_button = QPushButton("+")
        self.add_tab_button.setToolTip("Restore a closed tab")
        self.add_tab_button.setFixedWidth(34)
        self.add_tab_button.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.add_tab_menu = QMenu(self.add_tab_button)
        self.add_tab_menu.aboutToShow.connect(self._rebuild_add_tab_menu)
        self.add_tab_button.setMenu(self.add_tab_menu)
        self.tabs.setCornerWidget(
            self.add_tab_button,
            Qt.Corner.TopRightCorner,
        )

        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tab_bar.detach_requested.connect(self._detach_tab)
        self.tab_bar.tabMoved.connect(lambda _from, _to: self._sync_protected_tab_button())
        self.tabs.currentChanged.connect(self._handle_tab_changed)
        self._sync_protected_tab_button()

        layout = QVBoxLayout()
        layout.addLayout(top_bar)
        layout.addWidget(self.tabs)

        self.setLayout(layout)

    def _register_tab(
        self,
        tab_key: str,
        screen: RepositoryBackedWidget,
        label: str,
        closable: bool,
    ) -> None:
        """Registers and initially opens one restorable game tab."""

        self._tab_specs[tab_key] = (screen, label, closable)
        self._tab_order.append(tab_key)
        index = self.tabs.addTab(screen, label)
        self.tab_bar.setTabData(index, tab_key)

    def _tab_key_for_screen(self, screen: RepositoryBackedWidget) -> str:
        """Returns the stable tab key registered for a screen."""

        for tab_key, (registered, _label, _closable) in self._tab_specs.items():
            if registered is screen:
                return tab_key
        return ""

    def _display_tab_label(self, tab_key: str) -> str:
        """Returns a tab label with its unread-change marker, when present."""

        spec = self._tab_specs.get(tab_key)
        if spec is None:
            return tab_key
        label = spec[1]
        return f"{label} •" if tab_key in self._tab_notifications else label

    def _sync_tab_notification_display(self, tab_key: str) -> None:
        """Updates the docked tab or detached title for one notification state."""

        index = self._tab_index_for_key(tab_key)
        if index >= 0:
            self.tabs.setTabText(index, self._display_tab_label(tab_key))
            self.tabs.setTabToolTip(
                index,
                "Updated since you last viewed this tab."
                if tab_key in self._tab_notifications
                else "",
            )
        window = self._detached_windows.get(tab_key)
        if window is not None:
            window.setWindowTitle(
                self._detached_window_title(self._display_tab_label(tab_key))
            )

    def _mark_tab_changed(self, tab_key: str) -> None:
        """Marks a non-focused tab as having newly changed visible content."""

        if not tab_key:
            return
        current_index = self.tabs.currentIndex()
        if current_index >= 0 and self._tab_index_for_key(tab_key) == current_index:
            return
        self._tab_notifications.add(tab_key)
        self._sync_tab_notification_display(tab_key)

    def _clear_tab_changed(self, tab_key: str) -> None:
        """Clears one tab's unread-change marker after it receives focus."""

        if tab_key not in self._tab_notifications:
            return
        self._tab_notifications.discard(tab_key)
        self._sync_tab_notification_display(tab_key)
        self._rebuild_add_tab_menu()

    def _tab_index_for_key(self, tab_key: str) -> int:
        """Returns the current docked index for a stable tab key."""

        for index in range(self.tabs.count()):
            if str(self.tab_bar.tabData(index) or "") == tab_key:
                return index
        return -1

    def _sync_protected_tab_button(self) -> None:
        """Removes the close affordance from the protected Conversation tab."""

        for index in range(self.tabs.count()):
            tab_key = str(self.tab_bar.tabData(index) or "")
            spec = self._tab_specs.get(tab_key)
            if spec is not None and not spec[2]:
                self.tab_bar.setTabButton(
                    index,
                    QTabBar.ButtonPosition.RightSide,
                    None,
                )

    @Slot(int)
    def _close_tab(self, index: int) -> None:
        """Hides one closable tab until the player restores it from the plus menu."""

        tab_key = str(self.tab_bar.tabData(index) or "")
        spec = self._tab_specs.get(tab_key)
        if spec is None or not spec[2]:
            return

        screen = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if screen is not None:
            screen.hide()
        self._rebuild_add_tab_menu()

    def _rebuild_add_tab_menu(self) -> None:
        """Lists every tab that is currently neither docked nor detached."""

        self.add_tab_menu.clear()
        missing_keys = [
            tab_key
            for tab_key in self._tab_order
            if self._tab_index_for_key(tab_key) < 0
            and tab_key not in self._detached_windows
        ]
        if not missing_keys:
            action = self.add_tab_menu.addAction("All tabs are open")
            action.setEnabled(False)
            return

        for tab_key in missing_keys:
            _screen, _label, _closable = self._tab_specs[tab_key]
            action = self.add_tab_menu.addAction(self._display_tab_label(tab_key))
            action.triggered.connect(
                lambda _checked=False, key=tab_key: self._restore_tab(key)
            )

    def _restore_tab(self, tab_key: str) -> None:
        """Returns one closed or detached screen to the main tab strip."""

        spec = self._tab_specs.get(tab_key)
        if spec is None:
            return

        existing_index = self._tab_index_for_key(tab_key)
        if existing_index >= 0:
            self.tabs.setCurrentIndex(existing_index)
            return

        screen, _label, _closable = spec
        screen.setParent(self.tabs)
        index = self.tabs.addTab(screen, self._display_tab_label(tab_key))
        self.tab_bar.setTabData(index, tab_key)
        screen.show()
        self.tabs.setCurrentIndex(index)
        self._smart_hidden_tabs.discard(tab_key)
        self._clear_tab_changed(tab_key)
        self._sync_protected_tab_button()

    @Slot(str, QPoint)
    def _detach_tab(self, tab_key: str, global_position: QPoint) -> None:
        """Moves one docked tab into an independent, maximizable window."""

        index = self._tab_index_for_key(tab_key)
        spec = self._tab_specs.get(tab_key)
        if index < 0 or spec is None or tab_key in self._detached_windows:
            return

        self._clear_tab_changed(tab_key)
        screen, label, _closable = spec
        self.tabs.removeTab(index)
        window = _DetachedTabWindow(
            tab_key,
            self._detached_window_title(label),
            screen,
            self,
        )
        window.return_requested.connect(self._return_detached_tab)
        self._detached_windows[tab_key] = window
        window.move(global_position - QPoint(80, 24))
        window.show()

    @Slot(str)
    def _return_detached_tab(self, tab_key: str) -> None:
        """Redocks a screen when its independent window is closed."""

        self._detached_windows.pop(tab_key, None)
        self._restore_tab(tab_key)

    def _detached_window_title(self, label: str) -> str:
        """Builds a useful title for a detached game-screen window."""

        adventure_title = self.title_label.text().strip()
        if not adventure_title or adventure_title == "No Save Loaded":
            return f"{label} - AI Adventure"
        return f"{label} - {adventure_title}"

    def _refresh_detached_window_titles(self) -> None:
        """Keeps detached windows aligned with the active save title."""

        for tab_key, window in self._detached_windows.items():
            spec = self._tab_specs.get(tab_key)
            if spec is not None:
                window.setWindowTitle(self._detached_window_title(spec[1]))

    def hideEvent(self, event: Any) -> None:
        """Hides detached screens when the game shell itself leaves view."""

        for window in self._detached_windows.values():
            window.hide()
        super().hideEvent(event)

    def showEvent(self, event: Any) -> None:
        """Restores detached screens when the player returns to the game shell."""

        super().showEvent(event)
        for window in self._detached_windows.values():
            window.show()

    def set_repository(
        self,
        repository: SaveRepository | None,
        *,
        initially_hide_empty_tabs: bool = False,
    ) -> None:
        """
        Sets the active save repository for every screen.

        Args:
            repository: Active save repository, or None when returning to menu.
        """

        self._restore_all_smart_hidden_tabs()
        self.repository = repository
        self._tab_notifications.clear()

        if repository is None:
            self.title_label.setText("No Save Loaded")
        else:
            title = repository.get_meta("title", default="Untitled Adventure")
            self.title_label.setText(title)

        self._refresh_detached_window_titles()

        for screen in self.screens:
            screen.set_repository(repository)

        for tab_key in self._tab_specs:
            self._sync_tab_notification_display(tab_key)

        if repository is not None and initially_hide_empty_tabs:
            self._hide_empty_starting_tabs(repository)

        self._tab_content_signatures = {
            tab_key: _screen_content_signature(screen)
            for tab_key, (screen, _label, _closable) in self._tab_specs.items()
        }
        self._apply_audio_settings()
        self.visual_asset_coordinator.scan(repository)

    def _hide_empty_starting_tabs(self, repository: SaveRepository) -> None:
        """Hides empty NPC, Party, and Magic tabs for a newly created game."""

        setup = repository.get_setting("new_game.setup", {})
        if not isinstance(setup, dict):
            setup = {}
        starting_npcs = setup.get("starting_npcs", [])
        starting_party = setup.get("starting_party_npc_ids", [])
        magic = setup.get("magic", {})
        if not isinstance(magic, dict):
            magic = {}
        requested_spells = magic.get("starting_spell_requests", [])
        starting_spells = magic.get("starting_spells", [])
        should_hide = {
            "npcs": not repository.list_player_visible_npcs()
            and not (isinstance(starting_npcs, list) and starting_npcs),
            "party": not repository.list_party_members()
            and not (isinstance(starting_party, list) and starting_party),
            "magic": not repository.list_character_spells()
            and not (isinstance(requested_spells, list) and requested_spells)
            and not (isinstance(starting_spells, list) and starting_spells),
        }
        for tab_key, hidden in should_hide.items():
            if not hidden:
                continue
            index = self._tab_index_for_key(tab_key)
            if index < 0 or tab_key in self._detached_windows:
                continue
            screen = self.tabs.widget(index)
            self.tabs.removeTab(index)
            if screen is not None:
                screen.hide()
            self._smart_hidden_tabs.add(tab_key)
        self._sync_protected_tab_button()
        self._rebuild_add_tab_menu()

    def _restore_all_smart_hidden_tabs(self) -> None:
        """Restores tabs hidden only by the previous new-game empty-state rule."""

        for tab_key in list(self._smart_hidden_tabs):
            self._dock_smart_hidden_tab(tab_key)
        self._smart_hidden_tabs.clear()

    def _dock_smart_hidden_tab(self, tab_key: str) -> None:
        """Docks a smart-hidden tab without stealing focus from the player."""

        spec = self._tab_specs.get(tab_key)
        if spec is None or self._tab_index_for_key(tab_key) >= 0:
            self._smart_hidden_tabs.discard(tab_key)
            return
        screen, _label, _closable = spec
        screen.setParent(self.tabs)
        index = self.tabs.addTab(screen, self._display_tab_label(tab_key))
        self.tab_bar.setTabData(index, tab_key)
        screen.show()
        self._smart_hidden_tabs.discard(tab_key)
        self._tab_content_signatures[tab_key] = _screen_content_signature(screen)
        self._sync_protected_tab_button()

    def _reveal_populated_smart_hidden_tabs(self) -> None:
        """Reveals startup-hidden tabs once their underlying content appears."""

        repository = self.repository
        if repository is None:
            return
        has_content = {
            "npcs": bool(repository.list_player_visible_npcs()),
            "party": bool(repository.list_party_members()),
            "magic": bool(repository.list_character_spells()),
        }
        for tab_key in list(self._smart_hidden_tabs):
            if has_content.get(tab_key, False):
                self._dock_smart_hidden_tab(tab_key)
        self._rebuild_add_tab_menu()

    def refresh_screens(
        self,
        *,
        exclude: set[RepositoryBackedWidget] | None = None,
    ) -> None:
        """Refreshes tabs from saved data while preserving each screen's local state."""

        excluded_screens = exclude or set()
        previous_signatures = dict(self._tab_content_signatures)

        for screen in self.screens:
            if screen in excluded_screens:
                continue

            screen.refresh()

        changed_source_keys = {
            self._tab_key_for_screen(screen) for screen in excluded_screens
        }
        for tab_key, (screen, _label, _closable) in self._tab_specs.items():
            signature = _screen_content_signature(screen)
            previous = previous_signatures.get(tab_key)
            self._tab_content_signatures[tab_key] = signature
            if (
                previous is not None
                and previous != signature
                and tab_key not in changed_source_keys
            ):
                self._mark_tab_changed(tab_key)
        self._reveal_populated_smart_hidden_tabs()
        self.visual_asset_coordinator.scan(self.repository)

    @Slot()
    def _handle_visual_assets_changed(self) -> None:
        """Refreshes image-bearing surfaces after one background generation completes."""

        self.refresh_screens()

    def _handle_screen_repository_changed(self, source: RepositoryBackedWidget) -> None:
        """Refreshes tabs after a screen or event changes repository data."""

        self._apply_audio_settings()
        self.refresh_screens(exclude={source})

    def _handle_tab_changed(self, index: int) -> None:
        """Handles tab-focus refreshes and clears unread-change markers."""

        if index < 0:
            return

        tab_key = str(self.tab_bar.tabData(index) or "")
        self._clear_tab_changed(tab_key)

        if self.tabs.widget(index) == self.calendar_screen:
            self.calendar_screen.return_to_current_month()

        if self.tabs.widget(index) == self.travel_screen:
            self.travel_screen.refresh()

        screen = self.tabs.widget(index)
        if isinstance(screen, RepositoryBackedWidget) and tab_key:
            self._tab_content_signatures[tab_key] = _screen_content_signature(screen)

    def _submit_travel_request(
        self,
        destination: dict[str, Any],
        player_context: str,
    ) -> bool:
        """Routes a Travel tab action through the ordinary story-turn pipeline."""

        return self.story_screen.submit_travel_request(destination, player_context)

    def _apply_audio_settings(self) -> None:
        """Applies saved audio settings to the active audio managers."""

        if self.repository is None:
            if self.sound_manager is not None:
                self.sound_manager.stop_music()
                self.sound_manager.stop_sound_effect()
            if self.narration_player is not None:
                self.narration_player.stop()
            return

        _apply_audio_settings_to_managers(
            self.repository,
            sound_manager=self.sound_manager,
            narration_player=self.narration_player,
        )

    def _sample_narrator_voice(
        self,
        voice: str,
        volume: int,
        speed: int = DEFAULT_TTS_SPEED_PERCENT,
    ) -> bool:
        """Plays a local narrator voice sample."""

        if self.narration_player is None or not hasattr(self.narration_player, "play_sample"):
            return False

        return bool(
            self.narration_player.play_sample(
                voice=normalize_narrator_voice_spec(voice),
                volume=volume,
                speed=speed,
            )
        )

    def _apply_theme(self) -> None:
        """Notifies the main window that save-specific theme settings changed."""

        if self.on_theme_changed is not None:
            self.on_theme_changed()


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
        repository.append_history(
            "player_oog" if clean_mode == "out_of_game" else "player",
            player_text,
            message_id=repository.create_message_id(),
        )
        if clean_mode == "live_game":
            repository.capture_message_snapshot(self._pending_message_id)
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

        state = StateManager(repository).load_state()
        relevant_npcs = repository.list_relevant_npcs(
            location=state.world.location,
            query_text=player_text,
        )
        party_members = repository.list_party_members()
        gm_secrets = repository.list_gm_secrets(active_only=True)
        miscellaneous = repository.list_miscellaneous()
        valid_music_tracks = (
            self.sound_manager.get_valid_track_names()
            if self.sound_manager is not None
            else []
        )
        valid_sound_effect_tracks = (
            self.sound_manager.get_valid_sound_effect_names()
            if self.sound_manager is not None
            else []
        )
        valid_background_ambience_tracks = (
            getattr(
                self.sound_manager,
                "get_valid_background_ambience_names",
                lambda: [],
            )()
            if self.sound_manager is not None
            else []
        )
        return AiContextBuilder.from_default_library().build_story_context(
            state,
            player_command=player_text,
            conversation_mode=conversation_mode,
            relevant_npcs=relevant_npcs,
            party_members=party_members,
            gm_secrets=gm_secrets,
            miscellaneous=miscellaneous,
            valid_music_tracks=valid_music_tracks,
            current_music=str(repository.get_setting("audio.current_music", "")),
            valid_sound_effect_tracks=valid_sound_effect_tracks,
            valid_background_ambience_tracks=valid_background_ambience_tracks,
            current_background_ambience=str(
                repository.get_setting("audio.current_background_ambience", "")
            ),
            resolved_skill_checks=resolved_skill_checks,
            planner_context_tags=planner_context_tags,
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

        planned_checks = [
            check
            for check in getattr(plan_result, "checks", [])
            if isinstance(check, dict) and str(check.get("skill_name", "")).strip()
        ]
        check_events = [
            {
                "type": "SkillCheckRequestedEvent",
                "payload": check,
            }
            for check in planned_checks
        ]

        if check_events:
            self._pending_skill_check_event_results = EventApplier(
                repository,
                message_id=self._pending_message_id,
            ).apply_events(
                check_events
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
        pronunciation_map = merge_pronunciation_maps(
            repository.get_setting("tts.pronunciation_map", {}),
            getattr(result, "pronunciation_map", {}),
        )
        player_name_pronunciation = repository.get_setting(
            "player.name_pronunciation",
            "",
        )
        if player_name_pronunciation:
            pronunciation_map = set_authoritative_pronunciation(
                pronunciation_map,
                repository.get_setting("player_name", ""),
                player_name_pronunciation,
            )
        repository.set_setting("tts.pronunciation_map", pronunciation_map)
        message_id = self._pending_message_id or (
            repository.create_message_id() if repository is not None else ""
        )
        speaker_cues = (
            []
            if is_out_of_game
            else _resolve_speaker_cues_for_repository(
                repository,
                self.narration_player,
                getattr(result, "speaker_cues", []),
            )
        )
        repository.append_history(
            "story_oog" if is_out_of_game else "story",
            result.narrative_text,
            message_id=message_id,
            sound_effect_cues=result.sound_effect_cues,
            speaker_cues=speaker_cues,
        )

        if result.suggested_events and not is_out_of_game:
            event_results = EventApplier(
                repository,
                message_id=message_id,
            ).apply_events(
                result.suggested_events,
                prior_results=self._pending_skill_check_event_results,
            )
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
            repository.append_history(
                "story_oog" if is_out_of_game else "story",
                (
                    "No Gemini API key is configured yet. "
                    "This action was recorded successfully."
                ),
                message_id=message_id,
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
            repository.append_history(
                "story_oog" if is_out_of_game else "story",
                (
                    "Gemini is temporarily unavailable. Your action was recorded "
                    "and your save is safe; please try again shortly."
                ),
                message_id=message_id,
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
        self._last_saved_character_payload: dict[str, Any] | None = None
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
        self.portrait_label = QLabel()
        self.portrait_label.setMinimumWidth(220)
        portrait_layout.addWidget(self.portrait_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.portrait_group.setLayout(portrait_layout)
        self.portrait_group.hide()

        left_layout = QVBoxLayout()
        left_layout.addWidget(self.portrait_group)
        left_layout.addWidget(self.condition_group)
        left_layout.addWidget(self.stats_group)
        left_layout.addWidget(self.equipment_group)
        left_layout.addStretch()

        right_layout = QVBoxLayout()
        right_layout.addWidget(identity_group)

        sheet_layout = QHBoxLayout()
        sheet_layout.addLayout(left_layout)
        sheet_layout.addLayout(right_layout, stretch=1)

        self.setLayout(sheet_layout)
        self._set_pronouns(DEFAULT_CHARACTER_PRONOUNS)
        self._sync_contextual_controls(None)

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
        self.condition_group.setVisible(narrative_combat)
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
            self.portrait_group.setVisible(
                _set_generated_image(
                    self.portrait_label,
                    self.visual_asset_path(portrait_asset),
                    maximum_width=280,
                    maximum_height=340,
                    accessible_name=f"Generated portrait of {state.player.name}",
                )
            )
            self._sync_contextual_controls(repository)
        finally:
            self._loading_character = False
            self._last_saved_character_payload = (
                self._character_payload(repository) if repository is not None else None
            )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Autosaves multi-line character fields when focus leaves them."""

        if (
            event.type() == QEvent.Type.FocusOut
            and watched
            in (
                self.appearance_input,
                self.backstory_input,
                self.notes_input,
            )
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


class CombatScreen(RepositoryBackedWidget):
    """Deterministic saved combat manager."""

    def __init__(self, *, playtesting_tools: bool = False) -> None:
        super().__init__()

        self.playtesting_tools = bool(playtesting_tools)
        self._scheduled_npc_actor_id = ""
        self._scheduled_npc_repository: SaveRepository | None = None
        self.npc_turn_timer = QTimer(self)
        self.npc_turn_timer.setSingleShot(True)
        self.npc_turn_timer.setInterval(NPC_TURN_DELAY_MS)
        self.npc_turn_timer.timeout.connect(self._resolve_scheduled_npc_turn)
        self.status_label = QLabel("No active combat.")
        self.combatants_table = _AppTableWidget(0, 11)
        self.combatants_table.setHorizontalHeaderLabels(
            [
                "Turn",
                "Name",
                "Team",
                "Initiative",
                "Health",
                "Armor",
                "To Hit",
                "Threat",
                "Ammo",
                "Damage",
                "Loot/Status",
            ]
        )
        self.combatants_table.horizontalHeader().setStretchLastSection(True)
        self.combatants_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.combatants_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        self.target_combo = QComboBox()
        self.attack_button = QPushButton("Attack / Resolve Turn")
        self.attack_button.clicked.connect(self._resolve_current_turn)
        self.end_turn_button = QPushButton("End Turn")
        self.end_turn_button.clicked.connect(self._end_turn_without_attack)
        self.reload_button = QPushButton("Reload / End Turn")
        self.reload_button.clicked.connect(self._reload_current_weapon)
        self.resolve_button = QPushButton("Mark Combat Resolved")
        self.resolve_button.clicked.connect(self._resolve_combat_manually)
        self.team_combo = QComboBox()
        self.team_combo.addItem("Enemy", "enemy")
        self.team_combo.addItem("Player Party", "party")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Bandit, wolf, guard ally...")
        self.health_input = QSpinBox()
        self.health_input.setRange(1, 9999)
        self.health_input.setValue(8)
        self.armor_input = QSpinBox()
        self.armor_input.setRange(1, 99)
        self.armor_input.setValue(10)
        self.to_hit_input = QSpinBox()
        self.to_hit_input.setRange(-99, 99)
        self.to_hit_input.setValue(0)
        self.initiative_input = QSpinBox()
        self.initiative_input.setRange(-99, 99)
        self.personality_combo = QComboBox()

        for personality in COMBAT_PERSONALITIES:
            self.personality_combo.addItem(personality.title(), personality)

        self.ammunition_type_input = QLineEdit()
        self.ammunition_type_input.setPlaceholderText(
            "Optional, e.g. 9mm Round"
        )
        self.clip_size_input = QSpinBox()
        self.clip_size_input.setRange(0, 9999)
        self.clip_ammo_input = QSpinBox()
        self.clip_ammo_input.setRange(0, 9999)
        self.clip_size_input.valueChanged.connect(self._sync_clip_inputs)
        self.bullets_per_attack_input = QSpinBox()
        self.bullets_per_attack_input.setRange(1, 9999)
        self.reserve_ammo_input = QSpinBox()
        self.reserve_ammo_input.setRange(0, 999999)
        self.damage_input = QLineEdit("1d6")
        self.loot_input = QLineEdit()
        self.loot_input.setPlaceholderText("Optional loot names separated by commas")
        self.add_combatant_button = QPushButton("Add Combatant")
        self.add_combatant_button.clicked.connect(self._add_combatant)
        self.start_button = QPushButton("Start Combat")
        self.start_button.clicked.connect(self._start_combat)

        self.adjust_target_combo = QComboBox()
        self.adjust_amount_input = QSpinBox()
        self.adjust_amount_input.setRange(1, 9999)
        self.adjust_amount_input.setValue(1)
        self.damage_button = QPushButton("Apply Damage")
        self.damage_button.clicked.connect(lambda: self._adjust_health(-self.adjust_amount_input.value()))
        self.heal_button = QPushButton("Heal")
        self.heal_button.clicked.connect(lambda: self._adjust_health(self.adjust_amount_input.value()))

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        action_group = QGroupBox("Current Turn")
        action_layout = QFormLayout()
        action_layout.addRow("Target:", self.target_combo)
        action_layout.addRow(
            _button_row(
                self.attack_button,
                self.reload_button,
                self.end_turn_button,
                self.resolve_button,
            )
        )
        action_group.setLayout(action_layout)

        self.add_group = QGroupBox("Combatants")
        add_layout = QFormLayout()
        add_layout.addRow("Team:", self.team_combo)
        add_layout.addRow("Name:", self.name_input)
        add_layout.addRow("Health:", self.health_input)
        add_layout.addRow("Armor Rating:", self.armor_input)
        add_layout.addRow("To-Hit Bonus:", self.to_hit_input)
        add_layout.addRow("Initiative Bonus:", self.initiative_input)
        add_layout.addRow("Personality:", self.personality_combo)
        add_layout.addRow("Ammunition Type:", self.ammunition_type_input)
        add_layout.addRow("Clip Size:", self.clip_size_input)
        add_layout.addRow("Loaded Ammo:", self.clip_ammo_input)
        add_layout.addRow("Bullets / Attack:", self.bullets_per_attack_input)
        add_layout.addRow("Reserve Ammo:", self.reserve_ammo_input)
        add_layout.addRow("Damage:", self.damage_input)
        add_layout.addRow("Loot:", self.loot_input)
        add_layout.addRow(_button_row(self.start_button, self.add_combatant_button))
        self.add_group.setLayout(add_layout)

        self.adjust_group = QGroupBox("Damage and Recovery")
        adjust_layout = QFormLayout()
        adjust_layout.addRow("Combatant:", self.adjust_target_combo)
        adjust_layout.addRow("Amount:", self.adjust_amount_input)
        adjust_layout.addRow(_button_row(self.damage_button, self.heal_button))
        self.adjust_group.setLayout(adjust_layout)

        self.resolve_button.setVisible(self.playtesting_tools)
        self.add_group.setVisible(self.playtesting_tools)
        self.adjust_group.setVisible(self.playtesting_tools)

        controls = QVBoxLayout()
        controls.addWidget(action_group)
        controls.addWidget(self.add_group)
        controls.addWidget(self.adjust_group)
        controls.addStretch()
        controls_widget = QWidget()
        controls_widget.setLayout(controls)
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setWidget(controls_widget)

        main_row = QHBoxLayout()
        main_row.addWidget(self.combatants_table, stretch=2)
        main_row.addWidget(controls_scroll, stretch=1)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addLayout(main_row)
        layout.addWidget(QLabel("Combat Log"))
        layout.addWidget(self.log_output)
        self.setLayout(layout)

    def set_repository(self, repository: SaveRepository | None) -> None:
        """Cancels delayed actions before changing the active save."""

        self._cancel_scheduled_npc_turn()
        super().set_repository(repository)

    def refresh(self) -> None:
        """Reloads saved combat state."""

        repository = self.repository()

        if repository is None:
            self._cancel_scheduled_npc_turn()
            self.status_label.setText("No active combat.")
            self.combatants_table.setRowCount(0)
            self.target_combo.clear()
            self.adjust_target_combo.clear()
            self.log_output.clear()
            self._sync_buttons(False)
            return

        combat_state = repository.get_combat_state()
        self._render_combat_state(combat_state)
        if not combat_state.get("active") and self._uses_narrative_combat(repository):
            self.status_label.setText(
                "Narrative combat is enabled. Gemini resolves fights in Story."
            )

    def _schedule_npc_turn(self, combat_state: dict[str, Any]) -> None:
        """Schedules the current NPC to act after the reading delay."""

        repository = self.repository()
        combatants = combat_state.get("combatants", [])

        if (
            repository is None
            or not combat_state.get("active")
            or not combatants
        ):
            self._cancel_scheduled_npc_turn()
            return

        actor = combatants[int(combat_state.get("turn_index", 0))]
        actor_id = str(actor.get("id", ""))

        if actor_id == "player" or actor.get("defeated"):
            self._cancel_scheduled_npc_turn()
            return

        if (
            self.npc_turn_timer.isActive()
            and self._scheduled_npc_actor_id == actor_id
            and self._scheduled_npc_repository is repository
        ):
            return

        self.npc_turn_timer.stop()
        self._scheduled_npc_actor_id = actor_id
        self._scheduled_npc_repository = repository
        self.npc_turn_timer.start(NPC_TURN_DELAY_MS)

    def _cancel_scheduled_npc_turn(self) -> None:
        """Cancels any NPC action waiting on the reading delay."""

        if hasattr(self, "npc_turn_timer"):
            self.npc_turn_timer.stop()
        self._scheduled_npc_actor_id = ""
        self._scheduled_npc_repository = None

    def _resolve_scheduled_npc_turn(self) -> None:
        """Resolves the still-current NPC after its delay expires."""

        self.npc_turn_timer.stop()
        repository = self.repository()
        expected_repository = self._scheduled_npc_repository
        expected_actor_id = self._scheduled_npc_actor_id
        self._scheduled_npc_actor_id = ""
        self._scheduled_npc_repository = None

        if repository is None or repository is not expected_repository:
            return

        combat_state = repository.get_combat_state()
        combatants = combat_state.get("combatants", [])

        if not combat_state.get("active") or not combatants:
            return

        actor = combatants[int(combat_state.get("turn_index", 0))]

        if (
            str(actor.get("id", "")) != expected_actor_id
            or expected_actor_id == "player"
            or actor.get("defeated")
        ):
            self.refresh()
            return

        self._resolve_current_turn()

    def _start_combat(self) -> None:
        """Starts deterministic combat with the player and first opponent."""

        repository = self.repository()

        if repository is None:
            return

        if self._uses_narrative_combat(repository):
            self.status_label.setText(
                "Narrative combat is enabled. Gemini resolves fights in Story."
            )
            return

        state = StateManager(repository).load_state()
        inventory_items = repository.list_inventory_items()
        equipment = repository.get_player_equipment()
        attack_skill = equipped_weapon_attack_skill(equipment, inventory_items)
        weapon_profile = equipped_weapon_combat_profile(
            equipment,
            inventory_items,
        )
        armor_rating = armor_rating_from_equipment(equipment, inventory_items)
        player = {
            "id": "player",
            "name": state.player.name or "Player",
            "team": "party",
            "current_health": max(0, int(state.player.health_current)),
            "max_health": max(1, int(state.player.health_max)),
            "armor_rating": armor_rating,
            "to_hit_bonus": attack_bonus_from_skills(
                attack_skill,
                repository.list_skills(),
            ),
            "initiative_bonus": _safe_int(
                repository.get_setting("player.initiative_bonus", 0),
                0,
            ),
            "personality": "balanced",
            **weapon_profile,
            "clip_ammo": self._stored_player_clip_ammo(
                repository,
                weapon_profile,
            ),
            "reserve_ammo": 0,
            "damage": equipped_weapon_damage(equipment, inventory_items),
            "status_effects": [],
            "loot": [],
            "defeated": int(state.player.health_current) <= 0,
        }
        enemy = self._combatant_from_inputs(
            default_team="enemy",
            fallback_name="Enemy",
            use_selected_team=False,
        )
        combatants = roll_combat_initiative(
            [player, enemy],
            rng=random,
        )
        initiative_order = ", ".join(
            (
                f"{combatant_display_name(combatant)} "
                f"({combatant['initiative_total']})"
            )
            for combatant in combatants
        )
        combat_state = {
            "active": True,
            "round": 1,
            "turn_index": 0,
            "combatants": combatants,
            "log": [
                f"Combat begins: {player['name']} faces {enemy['name']}.",
                f"Initiative order: {initiative_order}.",
            ],
        }
        repository.set_combat_state(combat_state)
        repository.append_history("system", "Combat started.")
        self.refresh()
        self.notify_repository_changed()

    def _add_combatant(self) -> None:
        """Adds a party member or enemy to active combat."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            self._start_combat()
            return

        current_actor_id = str(
            combat_state["combatants"][int(combat_state["turn_index"])].get(
                "id",
                "",
            )
        )
        combatant = self._combatant_from_inputs(
            default_team=str(self.team_combo.currentData() or "enemy"),
            fallback_name="Combatant",
            index=len(combat_state["combatants"]) + 1,
        )
        roll_combat_initiative([combatant], rng=random)
        combat_state["combatants"].append(combatant)
        combat_state["combatants"].sort(
            key=lambda entry: (
                -int(entry.get("initiative_total", 0)),
                -int(entry.get("initiative_bonus", 0)),
                str(entry.get("id", "")),
            )
        )
        combat_state = normalize_combat_state(combat_state)
        combat_state["turn_index"] = next(
            (
                index
                for index, entry in enumerate(combat_state["combatants"])
                if str(entry.get("id", "")) == current_actor_id
            ),
            0,
        )
        added_combatant = next(
            (
                entry
                for entry in combat_state["combatants"]
                if str(entry.get("id", "")) == str(combatant["id"])
            ),
            combatant,
        )
        combat_state["log"].append(
            f"{combatant_display_name(added_combatant)} joins the fight "
            f"with initiative {added_combatant.get('initiative_total', 0)}."
        )
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_current_turn(self) -> None:
        """Resolves the current combatant's attack."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        self._cancel_scheduled_npc_turn()
        combatants = combat_state["combatants"]
        turn_index = int(combat_state["turn_index"])
        actor = combatants[turn_index]

        if actor.get("defeated"):
            self._advance_turn(combat_state)
            repository.set_combat_state(combat_state)
            self.refresh()
            self.notify_repository_changed()
            return

        if str(actor.get("id", "")) != "player":
            self._resolve_npc_turn(repository, combat_state, actor)
            return

        target = self._target_for_actor(actor, combatants)

        if target is None:
            self._resolve_combat(repository, combat_state)
            return

        if not self._consume_attack_ammunition(repository, actor):
            combat_state["log"].append(
                f"{combatant_display_name(actor)} cannot attack: reload "
                f"{actor.get('ammunition_type_required', 'ammunition')} first."
            )
            repository.set_combat_state(combat_state)
            self.refresh()
            return

        self._perform_attack(combat_state, actor, target)
        self._finish_combat_action(repository, combat_state)

    def _perform_attack(
        self,
        combat_state: dict[str, Any],
        actor: dict[str, Any],
        target: dict[str, Any],
    ) -> None:
        """Rolls and applies one attack."""

        attack_roll = random.randint(1, 20)
        to_hit_bonus = int(actor.get("to_hit_bonus", 0))
        attack_total = attack_roll + to_hit_bonus
        target_armor = int(target.get("armor_rating", 10))
        hit = attack_roll == 20 or (
            attack_roll != 1
            and attack_total >= target_armor
        )
        roll_detail = (
            f"{attack_roll}{to_hit_bonus:+d}={attack_total}"
            if to_hit_bonus
            else str(attack_roll)
        )

        if hit:
            damage, damage_detail = roll_damage_expression(actor.get("damage", DEFAULT_UNARMED_DAMAGE))
            target["current_health"] = max(0, int(target["current_health"]) - damage)
            target["defeated"] = target["current_health"] <= 0
            combat_state["log"].append(
                f"{combatant_display_name(actor)} hits "
                f"{combatant_display_name(target)} with {roll_detail} vs AR {target_armor}, "
                f"dealing {damage} damage [{damage_detail}]."
            )

            if target["defeated"]:
                combat_state["log"].append(
                    f"{combatant_display_name(target)} is defeated."
                )
        else:
            combat_state["log"].append(
                f"{combatant_display_name(actor)} misses "
                f"{combatant_display_name(target)} with {roll_detail} "
                f"vs AR {target_armor}."
            )

    def _finish_combat_action(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Persists an action and advances unless combat ended."""

        combatants = combat_state["combatants"]
        self._sync_player_health_from_combat(repository, combat_state)

        if combat_team_defeated(combatants, "enemy") or combat_team_defeated(combatants, "party"):
            self._resolve_combat(repository, combat_state)
            return

        self._advance_turn(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_npc_turn(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        """Resolves one NPC turn with deterministic personality rules."""

        target = self._npc_target_for_actor(
            actor,
            combat_state["combatants"],
        )

        if target is None:
            self._resolve_combat(repository, combat_state)
            return

        if actor.get("personality") == "intelligent":
            hit_chance = attack_hit_probability(
                int(actor.get("to_hit_bonus", 0)),
                int(target.get("armor_rating", 10)),
            )
            max_health = max(1, int(target.get("max_health", 1)))
            wounded_percent = round(
                (1.0 - (int(target.get("current_health", 0)) / max_health))
                * 100
            )
            combat_state["log"].append(
                f"{combatant_display_name(actor)} selects "
                f"{combatant_display_name(target)}: "
                f"{round(hit_chance * 100)}% hit chance, "
                f"{wounded_percent}% wounded."
            )
        else:
            combat_state["log"].append(
                f"{combatant_display_name(actor)} targets "
                f"{combatant_display_name(target)} based on its "
                f"{target.get('threat_level', 0)}% Threat Level."
            )

        if not self._consume_attack_ammunition(repository, actor):
            loaded = self._reload_actor_ammunition(repository, actor)

            if loaded > 0:
                combat_state["log"].append(
                    f"{combatant_display_name(actor)} reloads {loaded} "
                    f"{actor.get('ammunition_type_required', 'rounds')}."
                )
            else:
                combat_state["log"].append(
                    f"{combatant_display_name(actor)} is out of "
                    f"{actor.get('ammunition_type_required', 'ammunition')}."
                )

            self._finish_combat_action(repository, combat_state)
            return

        self._perform_attack(combat_state, actor, target)
        self._finish_combat_action(repository, combat_state)

    def _npc_target_for_actor(
        self,
        actor: dict[str, Any],
        combatants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Selects by threat unless the NPC uses intelligent tactical targeting."""

        enemy_team = "party" if actor.get("team") == "enemy" else "enemy"
        candidates = [
            combatant
            for combatant in combatants
            if combatant.get("team") == enemy_team
            and not combatant.get("defeated")
        ]

        if not candidates:
            return None

        if actor.get("personality") != "intelligent":
            threat_levels = calculate_team_threat_levels(
                combatants,
                enemy_team,
            )
            roll = random.randint(1, 100)
            cumulative = 0

            for candidate in candidates:
                threat = threat_levels.get(
                    str(candidate.get("id", "")),
                    0,
                )
                candidate["threat_level"] = threat
                cumulative += threat

                if roll <= cumulative:
                    return candidate

            return candidates[-1]

        def target_score(target: dict[str, Any]) -> tuple[float, float, int]:
            hit_probability = attack_hit_probability(
                int(actor.get("to_hit_bonus", 0)),
                int(target.get("armor_rating", 10)),
            )
            max_health = max(1, int(target.get("max_health", 1)))
            current_health = max(0, int(target.get("current_health", 0)))
            wounded_ratio = 1.0 - (current_health / max_health)
            combined_score = (hit_probability * 0.65) + (wounded_ratio * 0.35)
            return combined_score, wounded_ratio, -current_health

        return max(candidates, key=target_score)

    def _consume_attack_ammunition(
        self,
        repository: SaveRepository,
        actor: dict[str, Any],
    ) -> bool:
        """Consumes loaded rounds for an attack when the weapon requires them."""

        ammunition_type = str(
            actor.get("ammunition_type_required", "")
        ).strip()

        if not ammunition_type:
            return True

        bullets_per_attack = max(1, int(actor.get("bullets_per_attack", 1)))
        clip_ammo = max(0, int(actor.get("clip_ammo", 0)))

        if clip_ammo < bullets_per_attack:
            return False

        actor["clip_ammo"] = clip_ammo - bullets_per_attack

        if str(actor.get("id", "")) == "player":
            self._persist_player_clip_ammo(repository, actor)

        return True

    def _reload_current_weapon(self) -> None:
        """Reloads the current actor and consumes the turn."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        actor = combat_state["combatants"][int(combat_state["turn_index"])]
        loaded = self._reload_actor_ammunition(repository, actor)

        if loaded <= 0:
            combat_state["log"].append(
                f"{combatant_display_name(actor)} cannot reload."
            )
            repository.set_combat_state(combat_state)
            self.refresh()
            return

        combat_state["log"].append(
            f"{combatant_display_name(actor)} reloads {loaded} "
            f"{actor.get('ammunition_type_required', 'rounds')}."
        )
        self._finish_combat_action(repository, combat_state)

    def _reload_actor_ammunition(
        self,
        repository: SaveRepository,
        actor: dict[str, Any],
    ) -> int:
        """Moves reserve ammunition into an actor's clip."""

        ammunition_type = str(
            actor.get("ammunition_type_required", "")
        ).strip()
        clip_size = max(0, int(actor.get("clip_size", 0)))
        clip_ammo = max(0, int(actor.get("clip_ammo", 0)))
        needed = max(0, clip_size - clip_ammo)

        if not ammunition_type or needed <= 0:
            return 0

        if str(actor.get("id", "")) == "player":
            loaded = self._consume_inventory_ammunition(
                repository,
                ammunition_type,
                needed,
            )
        else:
            reserve_ammo = max(0, int(actor.get("reserve_ammo", 0)))
            loaded = min(needed, reserve_ammo)
            actor["reserve_ammo"] = reserve_ammo - loaded

        actor["clip_ammo"] = clip_ammo + loaded

        if str(actor.get("id", "")) == "player":
            self._persist_player_clip_ammo(repository, actor)

        return loaded

    @staticmethod
    def _consume_inventory_ammunition(
        repository: SaveRepository,
        ammunition_type: str,
        amount: int,
    ) -> int:
        """Consumes matching ammunition stacks from inventory."""

        remaining = max(0, amount)
        consumed = 0

        for item in repository.list_inventory_items():
            metadata = item_metadata(item)

            if str(metadata.get("item_type", "")).casefold() != "ammunition":
                continue
            if (
                str(metadata.get("ammunition_type", "")).casefold()
                != ammunition_type.casefold()
            ):
                continue

            available = max(0, int(item.get("quantity", 0)))
            used = min(remaining, available)

            if used <= 0:
                continue

            repository.remove_inventory_item(str(item.get("name", "")), used)
            consumed += used
            remaining -= used

            if remaining <= 0:
                break

        return consumed

    @staticmethod
    def _stored_player_clip_ammo(
        repository: SaveRepository,
        weapon_profile: dict[str, Any],
    ) -> int:
        """Loads the durable clip count for the equipped player weapon."""

        clip_size = max(0, int(weapon_profile.get("clip_size", 0)))
        weapon_name = str(weapon_profile.get("weapon_name", "")).casefold()
        stored_clips = repository.get_setting("player.weapon_clip_ammo", {})

        if not isinstance(stored_clips, dict) or not weapon_name:
            return clip_size

        return max(
            0,
            min(
                clip_size,
                _safe_int(stored_clips.get(weapon_name, clip_size), clip_size),
            ),
        )

    @staticmethod
    def _persist_player_clip_ammo(
        repository: SaveRepository,
        actor: dict[str, Any],
    ) -> None:
        """Stores the player's loaded rounds by weapon name."""

        weapon_name = str(actor.get("weapon_name", "")).casefold()

        if not weapon_name:
            return

        stored_clips = repository.get_setting("player.weapon_clip_ammo", {})
        clean_clips = dict(stored_clips) if isinstance(stored_clips, dict) else {}
        clean_clips[weapon_name] = max(0, int(actor.get("clip_ammo", 0)))
        repository.set_setting("player.weapon_clip_ammo", clean_clips)

    def _end_turn_without_attack(self) -> None:
        """Skips the active combatant's turn."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        actor = combat_state["combatants"][int(combat_state["turn_index"])]
        combat_state["log"].append(
            f"{combatant_display_name(actor)} holds position."
        )
        self._advance_turn(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _resolve_combat_manually(self) -> None:
        """Marks combat resolved without more attacks."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("active"):
            return

        combat_state["log"].append("Combat is marked resolved.")
        self._sync_player_health_from_combat(repository, combat_state)
        self._clear_resolved_battlefield(combat_state)
        repository.set_combat_state(combat_state)
        repository.append_history("system", "Combat resolved.")
        self.refresh()
        self.notify_repository_changed()

    def _adjust_health(self, delta: int) -> None:
        """Applies direct damage or healing to a combatant."""

        repository = self.repository()

        if repository is None:
            return

        combat_state = repository.get_combat_state()

        if not combat_state.get("combatants"):
            return

        combatant_id = str(self.adjust_target_combo.currentData() or "")

        for combatant in combat_state["combatants"]:
            if combatant.get("id") != combatant_id:
                continue

            old_health = int(combatant["current_health"])
            new_health = max(0, min(old_health + delta, int(combatant["max_health"])))
            combatant["current_health"] = new_health
            combatant["defeated"] = new_health <= 0
            verb = "heals" if delta > 0 else "takes"
            combat_state["log"].append(
                f"{combatant_display_name(combatant)} {verb} {abs(delta)}; "
                f"health is now "
                f"{new_health}/{combatant['max_health']}."
            )
            break

        self._sync_player_health_from_combat(repository, combat_state)

        if combat_state.get("active") and (
            combat_team_defeated(combat_state["combatants"], "enemy")
            or combat_team_defeated(combat_state["combatants"], "party")
        ):
            self._resolve_combat(repository, combat_state)
            return

        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _combatant_from_inputs(
        self,
        *,
        default_team: str,
        fallback_name: str,
        index: int = 1,
        use_selected_team: bool = True,
    ) -> dict[str, Any]:
        """Builds a combatant from the input row."""

        name = self.name_input.text().strip() or fallback_name
        damage = normalize_damage_expression(self.damage_input.text(), default="1d6")
        team = str(self.team_combo.currentData() or default_team) if use_selected_team else default_team

        if team not in {"party", "enemy"}:
            team = default_team

        ammunition_type = self.ammunition_type_input.text().strip()
        clip_size = self.clip_size_input.value() if ammunition_type else 0
        return {
            "id": f"{team}-{index}-{_slug_for_id(name)}",
            "name": name,
            "team": team,
            "current_health": self.health_input.value(),
            "max_health": self.health_input.value(),
            "armor_rating": self.armor_input.value(),
            "to_hit_bonus": self.to_hit_input.value(),
            "initiative_bonus": self.initiative_input.value(),
            "personality": self.personality_combo.currentData() or "balanced",
            "weapon_name": "",
            "ammunition_type_required": ammunition_type,
            "clip_size": clip_size,
            "clip_ammo": min(self.clip_ammo_input.value(), clip_size),
            "bullets_per_attack": (
                min(self.bullets_per_attack_input.value(), clip_size)
                if ammunition_type and clip_size > 0
                else 0
            ),
            "reserve_ammo": self.reserve_ammo_input.value(),
            "damage": damage,
            "status_effects": [],
            "loot": _split_loot_items(self.loot_input.text()) if team == "enemy" else [],
            "defeated": False,
        }

    def _sync_clip_inputs(self, clip_size: int) -> None:
        """Keeps playtesting clip controls inside the selected capacity."""

        self.clip_ammo_input.setMaximum(max(0, clip_size))
        self.bullets_per_attack_input.setMaximum(max(1, clip_size))

        if clip_size > 0 and self.clip_ammo_input.value() == 0:
            self.clip_ammo_input.setValue(clip_size)

    def _target_for_actor(
        self,
        actor: dict[str, Any],
        combatants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Returns the selected or automatic attack target."""

        enemy_team = "party" if actor.get("team") == "enemy" else "enemy"

        if actor.get("team") == "party":
            selected_id = str(self.target_combo.currentData() or "")

            for combatant in combatants:
                if combatant.get("id") == selected_id and not combatant.get("defeated"):
                    return combatant

        for combatant in combatants:
            if combatant.get("team") == enemy_team and not combatant.get("defeated"):
                return combatant

        return None

    def _advance_turn(self, combat_state: dict[str, Any]) -> None:
        """Moves to the next living combatant."""

        old_index = int(combat_state["turn_index"])
        new_index = next_living_index(combat_state["combatants"], old_index)

        if new_index <= old_index:
            combat_state["round"] = int(combat_state.get("round", 1)) + 1

        combat_state["turn_index"] = new_index

    def _resolve_combat(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Finishes combat, stores state, and grants defeated-enemy loot."""

        party_defeated = combat_team_defeated(combat_state["combatants"], "party")
        enemies_defeated = combat_team_defeated(combat_state["combatants"], "enemy")

        if enemies_defeated and not party_defeated:
            granted_loot: list[str] = []

            for combatant in combat_state["combatants"]:
                if combatant.get("team") != "enemy" or not combatant.get("defeated"):
                    continue

                for loot_name in combatant.get("loot", []):
                    repository.add_inventory_item(
                        loot_name,
                        "Loot",
                        1,
                        (
                            "Loot recovered from "
                            f"{combatant_display_name(combatant)}."
                        ),
                        0,
                    )
                    granted_loot.append(loot_name)

            if granted_loot:
                combat_state["log"].append("Recovered loot: " + ", ".join(granted_loot) + ".")

            combat_state["log"].append("Combat resolved: victory.")
            repository.append_history("system", "Combat resolved: victory.")
        elif party_defeated:
            combat_state["log"].append("Combat resolved: party defeated.")
            repository.append_history("system", "Combat resolved: party defeated.")
        else:
            combat_state["log"].append("Combat resolved.")
            repository.append_history("system", "Combat resolved.")

        self._sync_player_health_from_combat(repository, combat_state)
        self._clear_resolved_battlefield(combat_state)
        repository.set_combat_state(combat_state)
        self.refresh()
        self.notify_repository_changed()

    def _sync_player_health_from_combat(
        self,
        repository: SaveRepository,
        combat_state: dict[str, Any],
    ) -> None:
        """Persists player health from the player combatant."""

        for combatant in combat_state.get("combatants", []):
            if combatant.get("id") == "player":
                repository.set_setting("player.health_current", int(combatant["current_health"]))
                repository.set_setting("player.health_max", int(combatant["max_health"]))
                repository.set_setting("player.armor_rating", int(combatant["armor_rating"]))
                repository.set_state_value(
                    "condition",
                    "Incapacitated" if int(combatant["current_health"]) <= 0 else "Healthy",
                )
                self._persist_player_clip_ammo(repository, combatant)
                continue

            npc_id = str(combatant.get("npc_id", "") or "").strip()
            if npc_id and combatant.get("team") == "party":
                current_health = int(combatant.get("current_health", -1))
                max_health = int(combatant.get("max_health", -1))
                repository.upsert_party_member(
                    npc_id,
                    status=(
                        "Incapacitated"
                        if current_health <= 0
                        else "Wounded"
                        if max_health >= 0 and current_health < max_health
                        else "Active"
                    ),
                    health_current=current_health,
                    health_max=max_health,
                    armor_class=int(combatant.get("armor_rating", -1)),
                )

    @staticmethod
    def _clear_resolved_battlefield(combat_state: dict[str, Any]) -> None:
        """Clears active participants while preserving the completed combat log."""

        combat_state["active"] = False
        combat_state["round"] = 1
        combat_state["turn_index"] = 0
        combat_state["combatants"] = []

    def _render_combat_state(self, combat_state: dict[str, Any]) -> None:
        """Renders saved combat state."""

        active = bool(combat_state.get("active", False))
        combatants = combat_state.get("combatants", []) if active else []
        current_id = ""

        if active and combatants:
            turn_index = int(combat_state.get("turn_index", 0))
            actor = combatants[turn_index]
            current_id = str(actor.get("id", ""))
            status = (
                f"Round {combat_state.get('round', 1)} - "
                f"{combatant_display_name(actor)}'s turn"
            )

            if current_id != "player":
                status += " (acting automatically in 2 seconds...)"

            self.status_label.setText(status)
        else:
            self.status_label.setText("No active combat.")

        self.combatants_table.setRowCount(len(combatants))

        for row_index, combatant in enumerate(combatants):
            current_marker = "->" if combatant.get("id") == current_id else ""
            status_bits = []

            if combatant.get("defeated"):
                status_bits.append("Defeated")

            if combatant.get("status_effects"):
                status_bits.extend(str(effect) for effect in combatant.get("status_effects", []))

            loot_text = ", ".join(str(item) for item in combatant.get("loot", []))

            if loot_text:
                status_bits.append(f"Loot: {loot_text}")

            self.combatants_table.setItem(row_index, 0, _table_item(current_marker))
            self.combatants_table.setItem(
                row_index,
                1,
                _table_item(combatant_display_name(combatant)),
            )
            self.combatants_table.setItem(row_index, 2, _table_item(str(combatant["team"])))
            self.combatants_table.setItem(
                row_index,
                3,
                _table_item(
                    f"{combatant.get('initiative_total', 0)} "
                    f"({combatant.get('initiative_roll', 0)}"
                    f"{int(combatant.get('initiative_bonus', 0)):+d})"
                ),
            )
            self.combatants_table.setItem(
                row_index,
                4,
                _table_item(f"{combatant['current_health']}/{combatant['max_health']}"),
            )
            self.combatants_table.setItem(row_index, 5, _table_item(str(combatant["armor_rating"])))
            to_hit_bonus = int(combatant.get("to_hit_bonus", 0))
            self.combatants_table.setItem(
                row_index,
                6,
                _table_item(f"{to_hit_bonus:+d}"),
            )
            self.combatants_table.setItem(
                row_index,
                7,
                _table_item(f"{combatant.get('threat_level', 0)}%"),
            )
            ammunition_type = str(
                combatant.get("ammunition_type_required", "")
            )
            ammo_text = (
                f"{combatant.get('clip_ammo', 0)}/"
                f"{combatant.get('clip_size', 0)} {ammunition_type}"
                if ammunition_type
                else "-"
            )
            self.combatants_table.setItem(
                row_index,
                8,
                _table_item(ammo_text),
            )
            self.combatants_table.setItem(row_index, 9, _table_item(str(combatant["damage"])))
            self.combatants_table.setItem(row_index, 10, _table_item("; ".join(status_bits)))

        self.combatants_table.resizeColumnsToContents()
        self._populate_target_combos(combat_state)
        self.log_output.setPlainText("\n".join(str(entry) for entry in combat_state.get("log", [])))
        self.log_output.moveCursor(self.log_output.textCursor().MoveOperation.End)
        self._sync_buttons(active)
        self._schedule_npc_turn(combat_state)

    def _populate_target_combos(self, combat_state: dict[str, Any]) -> None:
        """Reloads target dropdowns from combatants."""

        self.target_combo.clear()
        self.adjust_target_combo.clear()
        combatants = (
            combat_state.get("combatants", [])
            if combat_state.get("active")
            else []
        )
        actor = None

        if combat_state.get("active") and combatants:
            actor = combatants[int(combat_state.get("turn_index", 0))]

        for combatant in combatants:
            if combatant.get("defeated"):
                continue

            label = (
                f"{combatant_display_name(combatant)} "
                f"({combatant['team']})"
            )
            self.adjust_target_combo.addItem(label, combatant["id"])

            if actor is None:
                continue

            if combatant.get("team") != actor.get("team"):
                self.target_combo.addItem(label, combatant["id"])

    def _sync_buttons(self, combat_active: bool) -> None:
        """Enables combat controls for the active state."""

        repository = self.repository()
        narrative_combat = bool(
            repository
            and not combat_active
            and self._uses_narrative_combat(repository)
        )
        combat_state = (
            repository.get_combat_state()
            if repository is not None and combat_active
            else {}
        )
        combatants = combat_state.get("combatants", [])
        actor = (
            combatants[int(combat_state.get("turn_index", 0))]
            if combatants
            else None
        )
        player_turn = bool(
            combat_active
            and actor is not None
            and actor.get("id") == "player"
        )
        self.attack_button.setText("Attack / Resolve Turn")
        self.attack_button.setEnabled(player_turn)
        self.end_turn_button.setEnabled(player_turn)
        self.reload_button.setEnabled(player_turn)
        self.target_combo.setEnabled(player_turn)
        manual_action_visible = not combat_active or player_turn
        self.attack_button.setVisible(manual_action_visible)
        self.end_turn_button.setVisible(manual_action_visible)
        self.reload_button.setVisible(manual_action_visible)
        self.resolve_button.setEnabled(combat_active)
        self.add_combatant_button.setEnabled(
            repository is not None and not narrative_combat
        )
        self.start_button.setEnabled(
            repository is not None and not combat_active and not narrative_combat
        )
        self.damage_button.setEnabled(bool(self.adjust_target_combo.count()))
        self.heal_button.setEnabled(bool(self.adjust_target_combo.count()))

    @staticmethod
    def _uses_narrative_combat(repository: SaveRepository) -> bool:
        """Returns whether this save delegates combat resolution to Gemini."""

        preferences = normalize_combat_preferences(
            repository.get_setting(
                "combat.preferences",
                {
                    "resolution_mode": repository.get_setting(
                        "combat.resolution_mode", "strict"
                    ),
                    "focus": repository.get_setting("combat.focus", "balanced"),
                },
            )
        )
        return preferences["resolution_mode"] == "narrative"


class BestiaryScreen(RepositoryBackedWidget):
    """Player-facing collection of learned, non-secret creature lore."""

    def __init__(self) -> None:
        super().__init__()

        self.creature_list = QListWidget()
        self.creature_list.currentItemChanged.connect(
            self._display_selected_creature
        )

        self.details_output = QTextEdit()
        self.details_output.setReadOnly(True)

        list_layout = QVBoxLayout()
        list_layout.addWidget(QLabel("Known Creatures"))
        list_layout.addWidget(self.creature_list)

        details_layout = QVBoxLayout()
        details_layout.addWidget(self.details_output)

        layout = QHBoxLayout()
        layout.addLayout(list_layout, 1)
        layout.addLayout(details_layout, 2)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads learned creatures while preserving the visible selection."""

        repository = self.repository()
        selected_id = self._selected_creature_id()
        self.creature_list.blockSignals(True)
        self.creature_list.clear()

        if repository is None:
            self.creature_list.blockSignals(False)
            self.details_output.clear()
            return

        for creature in repository.list_bestiary_entries():
            name = str(creature.get("name", "")).strip()
            if not name:
                continue

            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, creature)
            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                str(creature.get("misc_id", "")).strip(),
            )
            self.creature_list.addItem(item)

        self.creature_list.blockSignals(False)

        if self.creature_list.count() == 0:
            self.details_output.setPlainText(
                "No creatures have been learned about yet."
            )
            return

        target_row = 0
        for row in range(self.creature_list.count()):
            item = self.creature_list.item(row)
            if item is not None and str(
                item.data(Qt.ItemDataRole.UserRole + 1) or ""
            ) == selected_id:
                target_row = row
                break

        self.creature_list.setCurrentRow(target_row)
        self._display_selected_creature()

    def _selected_creature_id(self) -> str:
        """Returns the selected creature's durable public-lore ID."""

        current_item = self.creature_list.currentItem()
        if current_item is None:
            return ""
        return str(
            current_item.data(Qt.ItemDataRole.UserRole + 1) or ""
        ).strip()

    def _display_selected_creature(self, *_args: Any) -> None:
        """Displays only the selected public miscellaneous record."""

        current_item = self.creature_list.currentItem()
        if current_item is None:
            self.details_output.clear()
            return

        raw_creature = current_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(raw_creature, dict):
            self.details_output.clear()
            return

        name = str(raw_creature.get("name", "")).strip()
        details = str(raw_creature.get("details", "")).strip()
        sections = [f"# {name}"] if name else []
        if details:
            sections.append(details)
        _set_markdown_text(self.details_output, "\n\n".join(sections))


class TravelScreen(RepositoryBackedWidget):
    """Player-facing map knowledge, route estimates, and travel requests."""

    def __init__(
        self,
        *,
        on_travel_requested: Callable[[dict[str, Any], str], bool] | None = None,
    ) -> None:
        super().__init__()

        self.on_travel_requested = on_travel_requested
        self.location_list = QListWidget()
        self.location_list.currentItemChanged.connect(self._display_selected_location)

        self.details_output = QTextEdit()
        self.details_output.setReadOnly(True)

        self.location_image_label = QLabel()
        self.location_image_label.setObjectName("travelLocationImage")
        self.location_image_label.hide()

        self.travel_context_input = QTextEdit()
        self.travel_context_input.setPlaceholderText("Optional details for the GM")
        self.travel_context_input.setMaximumHeight(92)

        self.travel_button = QPushButton("Travel")
        self.travel_button.clicked.connect(self._request_travel)
        self.travel_button.setEnabled(False)

        list_layout = QVBoxLayout()
        list_layout.addWidget(QLabel("Known Locations"))
        list_layout.addWidget(self.location_list)

        details_layout = QVBoxLayout()
        details_layout.addWidget(self.location_image_label)
        details_layout.addWidget(self.details_output)
        details_layout.addWidget(QLabel("Travel Context"))
        details_layout.addWidget(self.travel_context_input)
        details_layout.addWidget(self.travel_button)

        layout = QHBoxLayout()
        layout.addLayout(list_layout, 1)
        layout.addLayout(details_layout, 2)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads known locations while preserving the visible selection."""

        repository = self.repository()
        current_location_name = ""
        selected_name = self._selected_location_name()
        self.location_list.blockSignals(True)
        self.location_list.clear()

        if repository is None:
            self.location_list.blockSignals(False)
            self.location_image_label.hide()
            self.details_output.clear()
            self.travel_button.setEnabled(False)
            return

        current_location_name = StateManager(repository).load_state().world.location
        locations = repository.ensure_travel_locations()

        for location in sorted(
            locations,
            key=lambda value: str(value.get("name", "")).casefold(),
        ):
            name = str(location.get("name", "")).strip()

            if not name:
                continue

            display_name = name
            if name.casefold() == current_location_name.casefold():
                display_name = f"{name} (Currently here)"

            item = QListWidgetItem(display_name)
            item.setData(
                Qt.ItemDataRole.UserRole,
                location,
            )
            item.setData(Qt.ItemDataRole.UserRole + 1, name)
            self.location_list.addItem(item)

        self.location_list.blockSignals(False)

        if self.location_list.count() == 0:
            self.location_image_label.hide()
            self.details_output.setPlainText("No locations are known yet.")
            self.travel_button.setEnabled(False)
            return

        target_name = current_location_name or selected_name
        target_row = 0

        for row in range(self.location_list.count()):
            item = self.location_list.item(row)

            item_name = (
                str(item.data(Qt.ItemDataRole.UserRole + 1) or "")
                if item is not None
                else ""
            )

            if item_name.casefold() == target_name.casefold():
                target_row = row
                break

        self.location_list.setCurrentRow(target_row)
        self._display_selected_location()

    def _selected_location_name(self) -> str:
        """Returns the currently selected location name, when any."""

        current_item = self.location_list.currentItem()
        if current_item is None:
            return ""

        return str(
            current_item.data(Qt.ItemDataRole.UserRole + 1)
            or current_item.text()
        ).strip()

    def _selected_location_data(self) -> dict[str, Any] | None:
        """Returns the selected location's persisted data."""

        current_item = self.location_list.currentItem()

        if current_item is None:
            return None

        raw_location = current_item.data(Qt.ItemDataRole.UserRole)
        return dict(raw_location) if isinstance(raw_location, dict) else None

    def _display_selected_location(self, *_args: Any) -> None:
        """Shows selected details and a mathematically calculated route estimate."""

        repository = self.repository()
        raw_destination = self._selected_location_data()
        destination = normalize_known_location(raw_destination)

        if repository is None or destination is None:
            self.location_image_label.hide()
            self.details_output.clear()
            self.travel_button.setEnabled(False)
            return

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

        sections = [f"# {destination.name}"]

        if destination.description:
            sections.append(destination.description)

        travel_lines = [
            "## Travel",
            f"**From:** {state.world.location or 'Current location'}",
            f"**Distance:** {format_distance(estimate.distance_miles)}",
            f"**Estimated time:** {format_travel_time(estimate.estimated_minutes)}",
            (
                f"**Travel mode:** {estimate.travel_mode} "
                f"({estimate.effective_speed_mph:g} mph)"
            ),
        ]

        if not estimate.is_available:
            travel_lines.append(
                "A route estimate will be available once both locations have map positions."
            )

        sections.append("\n\n".join(travel_lines))

        conditions = []
        if destination.terrain:
            conditions.append(f"**Terrain:** {destination.terrain}")
        if destination.travel_notes:
            conditions.append(f"**Route notes:** {destination.travel_notes}")
        if conditions:
            sections.append("## Conditions\n\n" + "\n\n".join(conditions))

        _set_markdown_text(self.details_output, "\n\n".join(sections))
        location_asset = repository.get_visual_asset(
            "location",
            str(destination.location_id or destination.name).casefold(),
        )
        _set_generated_image(
            self.location_image_label,
            self.visual_asset_path(location_asset),
            maximum_width=560,
            maximum_height=280,
            accessible_name=f"Generated view of {destination.name}",
        )
        can_travel = (
            estimate.is_available
            and destination.name.casefold() != state.world.location.casefold()
            and self.on_travel_requested is not None
        )
        self.travel_button.setEnabled(can_travel)

    def _request_travel(self) -> None:
        """Sends the selected planned route through the normal story input flow."""

        destination = self._selected_location_data()

        if destination is None or self.on_travel_requested is None:
            return

        if self.on_travel_requested(
            destination,
            self.travel_context_input.toPlainText().strip(),
        ):
            self.travel_context_input.clear()


class CalendarScreen(RepositoryBackedWidget):
    """Player-facing custom calendar view."""

    def __init__(self, *, playtesting_tools: bool = False) -> None:
        super().__init__()

        self.playtesting_tools = bool(playtesting_tools)
        self.month_offset = 0
        self.month_label = QLabel("-")
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.month_label.setStyleSheet("font-size: 18px; font-weight: bold;")

        previous_button = QPushButton("Previous")
        previous_button.clicked.connect(self._show_previous_month)

        today_button = QPushButton("Today")
        today_button.clicked.connect(self.return_to_current_month)

        next_button = QPushButton("Next")
        next_button.clicked.connect(self._show_next_month)

        self.add_event_button = QPushButton("+ Event")
        self.add_event_button.setToolTip("Add a private player-created calendar event")
        self.add_event_button.clicked.connect(self._add_player_event)
        self.add_event_button.setEnabled(False)

        self.settings_button = QPushButton("Calendar Settings")
        self.settings_button.clicked.connect(self._open_calendar_settings_dialog)
        self.settings_button.setEnabled(False)
        self.settings_button.setVisible(self.playtesting_tools)

        navigation_left = QWidget()
        navigation_left_layout = QHBoxLayout(navigation_left)
        navigation_left_layout.setContentsMargins(0, 0, 0, 0)
        navigation_left_layout.addWidget(previous_button)
        navigation_left_layout.addStretch(1)

        navigation_right = QWidget()
        navigation_actions = QHBoxLayout(navigation_right)
        navigation_actions.setContentsMargins(0, 0, 0, 0)
        navigation_actions.addStretch(1)
        navigation_actions.addWidget(self.settings_button)
        navigation_actions.addWidget(self.add_event_button)
        navigation_actions.addWidget(today_button)
        navigation_actions.addWidget(next_button)

        side_width = max(
            navigation_left.sizeHint().width(),
            navigation_right.sizeHint().width(),
        )
        navigation_left.setMinimumWidth(side_width)
        navigation_right.setMinimumWidth(side_width)

        navigation_row = QGridLayout()
        navigation_row.addWidget(
            navigation_left,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        navigation_row.addWidget(
            self.month_label,
            0,
            1,
            alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )
        navigation_row.addWidget(
            navigation_right,
            0,
            2,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        navigation_row.setColumnStretch(0, 1)
        navigation_row.setColumnStretch(2, 1)

        self.summary_label = QLabel("-")
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary_label.setStyleSheet("font-size: 16px; font-weight: 600;")

        self.table = _AppTableWidget(0, 0)
        self.table.setObjectName("calendarGrid")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        _use_soft_table_selection(self.table)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self._pending_day_cell: tuple[int, int] | None = None
        self._day_click_timer = QTimer(self)
        self._day_click_timer.setSingleShot(True)
        self._day_click_timer.timeout.connect(self._open_pending_day_events)
        self.table.cellClicked.connect(self._schedule_open_day_events)
        self.table.cellDoubleClicked.connect(self._add_player_event_for_day)

        self.year_table = _AppTableWidget(0, 0)
        self.year_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.year_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        _use_soft_table_selection(self.year_table)
        self.year_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.year_table.verticalHeader().setVisible(False)

        self.tasks_table = _AppTableWidget(0, 6)
        self.tasks_table.setHorizontalHeaderLabels(
            ["Task", "Description", "Category", "Due", "Location", "Reward"]
        )
        self.tasks_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _use_soft_table_selection(self.tasks_table)
        _configure_wrapping_table(self.tasks_table, {1})

        self.views = QTabWidget()
        month_page = QWidget()
        month_layout = QVBoxLayout()
        month_layout.addLayout(navigation_row)
        month_layout.addWidget(self.summary_label, alignment=Qt.AlignmentFlag.AlignCenter)
        month_layout.addWidget(self.table)
        month_page.setLayout(month_layout)
        self.views.addTab(month_page, "Month")
        self.views.addTab(self.year_table, "Year Overview")
        self.views.addTab(self.tasks_table, "Tasks & Deadlines")

        layout = QVBoxLayout()
        layout.addWidget(self.views)

        self.setLayout(layout)

    def set_repository(self, repository: SaveRepository | None) -> None:
        """Sets the active repository and resets to the current month."""

        self.month_offset = 0
        super().set_repository(repository)

    def refresh(self) -> None:
        """Reloads the calendar grid."""

        repository = self.repository()

        if repository is None:
            self.month_label.setText("-")
            self.summary_label.setText("-")
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.settings_button.setEnabled(False)
            self.add_event_button.setEnabled(False)
            self.year_table.setRowCount(0)
            self.tasks_table.setRowCount(0)
            return

        self.settings_button.setEnabled(True)
        self.add_event_button.setEnabled(True)
        state = StateManager(repository).load_state()
        grid = build_month_grid(state.calendar.to_dict(), self.month_offset)
        calendar_events = repository.list_calendar_events()
        calendar_events.extend(
            self._task_deadline_events(
                repository.list_active_tasks(),
                repository.get_calendar_settings(),
            )
        )
        self.month_offset = int(grid["month_offset"])

        self.month_label.setText(f"{grid['month_name']} - Year {grid['year']}")
        self.summary_label.setText(f"Season: {state.calendar.season_name}")
        self.table.setColumnCount(int(grid["days_per_week"]))
        self.table.setRowCount(int(grid["weeks_per_month"]))
        self.table.setHorizontalHeaderLabels([str(name) for name in grid["day_names"]])

        for row_index, week in enumerate(grid["rows"]):
            for column_index, day in enumerate(week):
                self.table.removeCellWidget(row_index, column_index)
                events = self._events_for_day(
                    calendar_events,
                    int(grid["year"]),
                    int(grid["month_index"]),
                    int(day["day_of_month"]),
                    int(state.calendar.days_per_month),
                    int(state.calendar.days_per_year),
                )
                event_titles = [
                    self._event_display_title(event, state.calendar.settings)
                    for event in events[:3]
                ]
                label = str(day["day_of_month"])
                if event_titles:
                    label += "\n" + "\n".join(f"• {title}" for title in event_titles)
                    if len(events) > 3:
                        label += f"\n+{len(events) - 3} more"

                if day["is_current_day"]:
                    label = f"{day['day_of_month']} · Today" + ("\n" + "\n".join(f"• {title}" for title in event_titles) if event_titles else "")

                item = QTableWidgetItem(label)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, events)
                item.setData(
                    int(Qt.ItemDataRole.UserRole) + 1,
                    {
                        "year": int(grid["year"]),
                        "month": int(grid["month_index"]) + 1,
                        "day": int(day["day_of_month"]),
                    },
                )
                self.table.setItem(row_index, column_index, item)

                if day["is_current_day"]:
                    item.setToolTip("Current day")
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)

                if events:
                    item.setToolTip("\n".join(event_titles))

        self.table.resizeRowsToContents()
        self._refresh_year_overview(state.calendar.to_dict(), calendar_events)
        self._refresh_tasks(repository)

    @staticmethod
    def _task_deadline_events(
        tasks: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Projects dated active tasks into calendar day cells."""

        events: list[dict[str, Any]] = []
        for task in tasks:
            due_minute = _safe_int(task.get("due_elapsed_minutes", -1), -1)
            if due_minute < 0:
                continue
            due = build_calendar_snapshot(due_minute, settings)
            events.append(
                {
                    "event_id": f"active_task_{task.get('name', '')}",
                    "title": str(task.get("name", "Task Deadline")),
                    "description": str(task.get("description", "")),
                    "details": "\n".join(
                        value for value in [
                            str(task.get("description", "")),
                            f"Requester: {task.get('requester', '')}",
                            f"Reward: {task.get('reward', '')}",
                            f"Location: {task.get('location', '')}",
                        ] if value and not value.endswith(": ")
                    ),
                    "category": str(task.get("category", "Task")),
                    "month": int(due["month_number"]),
                    "day": int(due["day_of_month"]),
                    "duration_days": 1,
                    "recurrence": "none",
                    "year": int(due["year"]),
                }
            )
        return events

    @staticmethod
    def _events_for_day(
        events: list[dict[str, Any]],
        year: int,
        month_index: int,
        day: int,
        days_per_month: int,
        days_per_year: int,
    ) -> list[dict[str, Any]]:
        """Returns events whose date range includes the requested day."""

        target = month_index * days_per_month + day
        matches: list[dict[str, Any]] = []
        for event in events:
            if event.get("recurrence") != "yearly" and int(event.get("year", 1)) != year:
                continue
            start = (int(event.get("month", 1)) - 1) * days_per_month + int(event.get("day", 1))
            duration = max(1, int(event.get("duration_days", 1)))
            if any(((start - 1 + offset) % days_per_year) + 1 == target for offset in range(duration)):
                matches.append(event)
        return matches

    def _schedule_open_day_events(self, row: int, column: int) -> None:
        """Delays single-click details so a double-click can create an event."""

        self._pending_day_cell = (row, column)
        self._day_click_timer.start(max(1, QApplication.doubleClickInterval()))

    def _open_pending_day_events(self) -> None:
        """Opens the day selected by a completed single click."""

        pending = self._pending_day_cell
        self._pending_day_cell = None
        if pending is not None:
            self._open_day_events(*pending)

    def _open_day_events(self, row: int, column: int) -> None:
        """Shows details for calendar events listed in a day cell."""

        item = self.table.item(row, column)
        events = item.data(Qt.ItemDataRole.UserRole) if item is not None else []
        if not isinstance(events, list) or not events:
            return
        repository = self.repository()
        if repository is None:
            return
        dialog = CalendarDayEventsDialog(
            repository=repository,
            events=events,
            calendar_settings=repository.get_calendar_settings(),
            parent=self,
        )
        dialog.exec()
        if dialog.changed:
            self.refresh()
            self.notify_repository_changed()

    @staticmethod
    def _event_display_title(
        event: dict[str, Any],
        calendar_settings: dict[str, Any],
    ) -> str:
        """Returns a compact title prefixed by its time when one is known."""

        title = str(event.get("title", "Event") or "Event")
        time_label = _calendar_event_time_label(event, calendar_settings)
        if not time_label:
            return title
        return f"{time_label} — {title}"

    def _add_player_event(self) -> None:
        """Creates a private player-authored event for the viewed month."""

        repository = self.repository()
        if repository is None:
            return
        state = StateManager(repository).load_state()
        grid = build_month_grid(state.calendar.to_dict(), self.month_offset)
        self._open_player_event_dialog(
            default_year=int(grid["year"]),
            default_month=int(grid["month_index"]) + 1,
            default_day=1,
        )

    def _add_player_event_for_day(self, row: int, column: int) -> None:
        """Creates an event with a double-clicked calendar date preselected."""

        self._day_click_timer.stop()
        self._pending_day_cell = None
        item = self.table.item(row, column)
        date = (
            item.data(int(Qt.ItemDataRole.UserRole) + 1)
            if item is not None
            else None
        )
        if not isinstance(date, dict):
            return
        self._open_player_event_dialog(
            default_year=_safe_int(date.get("year"), 1),
            default_month=_safe_int(date.get("month"), 1),
            default_day=_safe_int(date.get("day"), 1),
        )

    def _open_player_event_dialog(
        self,
        *,
        default_year: int,
        default_month: int,
        default_day: int,
    ) -> None:
        """Opens and persists the shared player-created calendar event dialog."""

        repository = self.repository()
        if repository is None:
            return
        dialog = CalendarPlayerEventDialog(
            calendar_settings=repository.get_calendar_settings(),
            default_year=default_year,
            default_month=default_month,
            default_day=default_day,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        repository.upsert_calendar_event(dialog.build_event())
        self.refresh()
        self.notify_repository_changed()

    def _refresh_year_overview(
        self,
        calendar: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> None:
        """Builds a season-grouped overview of every month in the current year."""

        settings = calendar.get("settings", {})
        months = list(settings.get("month_names", []))
        seasons = list(settings.get("seasons", []))
        season_count = max(1, len(seasons))
        grouped: list[list[int]] = [[] for _ in range(season_count)]
        for month_index in range(len(months)):
            grouped[min(month_index * season_count // max(1, len(months)), season_count - 1)].append(month_index)
        self.year_table.setColumnCount(season_count)
        self.year_table.setHorizontalHeaderLabels([
            str(season.get("name", f"Season {index + 1}")) for index, season in enumerate(seasons)
        ] or ["Year"])
        self.year_table.setRowCount(max((len(group) for group in grouped), default=0))
        year = int(calendar.get("year", 1))
        for column, month_indexes in enumerate(grouped):
            for row, month_index in enumerate(month_indexes):
                month_events = [
                    event for event in events
                    if int(event.get("month", 1)) == month_index + 1
                    and (event.get("recurrence") == "yearly" or int(event.get("year", 1)) == year)
                ]
                text = str(months[month_index])
                if month_events:
                    text += "\n" + "\n".join(f"• {event['title']}" for event in month_events[:4])
                year_item = QTableWidgetItem(text)
                year_item.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
                self.year_table.setItem(row, column, year_item)
        self.year_table.resizeRowsToContents()

    def _refresh_tasks(self, repository: SaveRepository) -> None:
        """Projects active tasks and deadlines into the Calendar tab."""

        tasks = repository.list_active_tasks()
        self.tasks_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            values = [
                task.get("name", ""),
                task.get("description", "") or "No description recorded yet.",
                task.get("category", ""),
                task.get("due_date", "N/A"),
                task.get("location", ""),
                task.get("reward", ""),
            ]
            for column, value in enumerate(values):
                self.tasks_table.setItem(row, column, _table_item(str(value)))
        _resize_wrapping_table_rows(self.tasks_table)

    def return_to_current_month(self) -> None:
        """Returns the grid to the current month and refreshes."""

        self.month_offset = 0
        self.refresh()

    def _show_previous_month(self) -> None:
        """Shows the previous month."""

        self.month_offset -= 1
        self.refresh()

    def _show_next_month(self) -> None:
        """Shows the next month."""

        self.month_offset += 1
        self.refresh()

    def _open_calendar_settings_dialog(self) -> None:
        """Opens the save-specific calendar settings dialog."""

        repository = self.repository()

        if repository is None:
            return

        dialog = CalendarSettingsDialog(repository.get_calendar_settings(), self)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_calendar_settings(dialog.build_settings())

    def _save_calendar_settings(self, settings: dict[str, Any]) -> None:
        """Persists calendar settings and refreshes dependent screens."""

        repository = self.repository()

        if repository is None:
            return

        repository.set_calendar_settings(settings)
        _refresh_repository_calendar_time(repository)
        self.month_offset = 0
        self.refresh()
        self.notify_repository_changed()


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


_INVENTORY_INVARIANT_PLURALS = {
    "ammunition",
    "armor",
    "clothing",
    "equipment",
    "food",
    "footwear",
    "information",
    "oil",
    "pants",
    "rice",
    "scissors",
    "trousers",
    "water",
}
_INVENTORY_UNIT_ABBREVIATIONS = {
    "cl",
    "cm",
    "ft",
    "g",
    "gal",
    "in",
    "kg",
    "l",
    "lb",
    "lbs",
    "ml",
    "mm",
    "oz",
    "tbsp",
    "tsp",
}
_INVENTORY_IRREGULAR_PLURALS = {
    "child": "children",
    "foot": "feet",
    "goose": "geese",
    "knife": "knives",
    "leaf": "leaves",
    "loaf": "loaves",
    "man": "men",
    "mouse": "mice",
    "person": "people",
    "tooth": "teeth",
    "woman": "women",
    "wolf": "wolves",
}


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
        """Enables secondary direction only when a secondary field is selected."""

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

class PartyScreen(RepositoryBackedWidget):
    """Player-facing party roster backed by canonical NPC identities."""

    def __init__(self) -> None:
        super().__init__()

        self.table = _AppTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Name",
                "Status",
                "Health",
                "Armor Class",
                "Combat Style",
                "Skills",
                "Description",
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        _configure_wrapping_table(self.table, {4, 5, 6})

        explanation = QLabel(
            "Party members are shared NPC identities. Names and descriptions come "
            "from the NPC profile; this tab shows their current party-specific state."
        )
        explanation.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(explanation)
        layout.addWidget(self.table)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads current party records joined to their NPC profiles."""

        repository = self.repository()
        if repository is None:
            self.table.setRowCount(0)
            return

        members = repository.list_party_members()
        self.table.setRowCount(len(members))
        for row_index, member in enumerate(members):
            health_current = _safe_int(member.get("health_current"), -1)
            health_max = _safe_int(member.get("health_max"), -1)
            health = (
                f"{health_current}/{health_max}"
                if health_current >= 0 and health_max >= 0
                else "N/A"
            )
            armor_class = _safe_int(member.get("armor_class"), -1)
            values = (
                member.get("display_name") or member.get("name") or "Unknown NPC",
                member.get("status", "Active"),
                health,
                armor_class if armor_class >= 0 else "N/A",
                member.get("combat_style", ""),
                ", ".join(str(skill) for skill in member.get("skills", [])),
                member.get("description") or member.get("notes") or "",
            )
            for column, value in enumerate(values):
                item = _table_item(str(value))
                item.setData(Qt.ItemDataRole.UserRole, str(member.get("npc_id", "")))
                self.table.setItem(row_index, column, item)

        _resize_wrapping_table_rows(self.table)


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


class NpcsScreen(RepositoryBackedWidget):
    """Player-facing NPC journal."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._npcs_by_id: dict[str, dict[str, Any]] = {}
        self.table = _AppTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Location", "Notes", "Portrait"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.cellClicked.connect(self._open_npc_details)
        _configure_wrapping_table(self.table, {2})
        _enable_table_sorting(self.table, self._sort_by_column)
        self.table.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)

        layout = QVBoxLayout()
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads the player-visible NPC journal."""

        repository = self.repository()

        if repository is None:
            self._npcs_by_id.clear()
            self.table.setRowCount(0)
            return

        npcs = repository.list_player_visible_npcs()
        npcs.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self._npcs_by_id = {
            str(npc.get("npc_id", "") or "").strip(): dict(npc)
            for npc in npcs
            if str(npc.get("npc_id", "") or "").strip()
        }
        self.table.setRowCount(len(npcs))

        for row_index, npc in enumerate(npcs):
            npc_id = str(npc.get("npc_id", "") or "").strip()
            values = (
                str(npc.get("display_name", "Unknown NPC")),
                str(npc.get("location", "")),
                str(npc.get("notes", "")),
            )
            for column, value in enumerate(values):
                table_item = _table_item(value)
                table_item.setData(Qt.ItemDataRole.UserRole, npc_id)
                self.table.setItem(row_index, column, table_item)
            portrait = QLabel()
            portrait.setObjectName("npcGeneratedPortrait")
            portrait.setMargin(4)
            asset = repository.get_visual_asset(
                "npc",
                str(npc.get("npc_id", "") or "").casefold(),
            )
            if _set_generated_image(
                portrait,
                self.visual_asset_path(asset),
                maximum_width=96,
                maximum_height=96,
                accessible_name=(
                    f"Generated portrait of {npc.get('display_name', 'Unknown NPC')}"
                ),
            ):
                self.table.setCellWidget(row_index, 3, portrait)

        _resize_wrapping_table_rows(self.table)
        for row_index in range(self.table.rowCount()):
            if self.table.cellWidget(row_index, 3) is not None:
                self.table.setRowHeight(row_index, max(104, self.table.rowHeight(row_index)))

    def _open_npc_details(self, row: int, _column: int) -> None:
        """Opens the selected NPC's complete player-visible profile."""

        if row < 0:
            return
        table_item = self.table.item(row, 0)
        npc_id = (
            str(table_item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if table_item is not None
            else ""
        )
        npc = self._npcs_by_id.get(npc_id)
        repository = self.repository()
        if npc is None or repository is None:
            return
        asset = repository.get_visual_asset("npc", npc_id.casefold())
        dialog = NpcDetailsDialog(
            npc=npc,
            image_path=self.visual_asset_path(asset),
            parent=self,
        )
        dialog.exec()

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts NPCs by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, npc: dict[str, Any]) -> tuple[str, str]:
        """Returns the active NPC sort key."""

        name = str(npc.get("display_name", "Unknown NPC")).casefold()

        if self._sort_column == 1:
            return str(npc.get("location", "")).casefold(), name

        if self._sort_column == 2:
            return str(npc.get("notes", "")).casefold(), name

        return name, name


class ActiveTasksScreen(RepositoryBackedWidget):
    """Player-facing list of current quests, commissions, and obligations."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.table = _AppTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Task",
                "Type",
                "Status",
                "Details",
                "Contact",
                "Location",
                "Reward",
                "Due",
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _configure_wrapping_table(self.table, {0, 3, 6})
        _enable_table_sorting(self.table, self._sort_by_column)
        self.table.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        layout = QVBoxLayout()
        layout.addWidget(refresh_button)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads active tasks."""

        repository = self.repository()

        if repository is None:
            self.table.setRowCount(0)
            return

        tasks = repository.list_active_tasks()
        tasks.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self.table.setRowCount(len(tasks))

        for row_index, task in enumerate(tasks):
            details = str(task.get("description", ""))
            notes = str(task.get("notes", ""))

            if notes:
                details = f"{details}\n\n{notes}" if details else notes

            self.table.setItem(row_index, 0, _table_item(str(task.get("name", ""))))
            self.table.setItem(row_index, 1, _table_item(str(task.get("category", ""))))
            self.table.setItem(row_index, 2, _table_item(str(task.get("status", ""))))
            self.table.setItem(row_index, 3, _table_item(details))
            self.table.setItem(row_index, 4, _table_item(str(task.get("requester", ""))))
            self.table.setItem(row_index, 5, _table_item(str(task.get("location", ""))))
            self.table.setItem(row_index, 6, _table_item(str(task.get("reward", ""))))
            self.table.setItem(row_index, 7, _table_item(str(task.get("due_date", ""))))

        _resize_wrapping_table_rows(self.table)

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts active tasks by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, task: dict[str, Any]) -> tuple[str, str]:
        """Returns the active task sort key."""

        name = str(task.get("name", "")).casefold()

        if self._sort_column == 1:
            return str(task.get("category", "")).casefold(), name

        if self._sort_column == 2:
            return str(task.get("status", "")).casefold(), name

        if self._sort_column == 3:
            details = str(task.get("description", ""))
            notes = str(task.get("notes", ""))
            return f"{details}\n\n{notes}".casefold(), name

        if self._sort_column == 4:
            return str(task.get("requester", "")).casefold(), name

        if self._sort_column == 5:
            return str(task.get("location", "")).casefold(), name

        if self._sort_column == 6:
            return str(task.get("reward", "")).casefold(), name

        if self._sort_column == 7:
            due_elapsed_minutes = _safe_int(task.get("due_elapsed_minutes"), -1)

            if due_elapsed_minutes >= 0:
                return f"{due_elapsed_minutes:012d}", name

            return str(task.get("due_date", "")).casefold(), name

        return name, name


class SkillsScreen(RepositoryBackedWidget):
    """Read-only skills journal."""

    def __init__(self) -> None:
        super().__init__()

        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.skills_table = _AppTableWidget(0, 4)
        self.skills_table.setHorizontalHeaderLabels(
            ["Skill", "Training", "XP Progress", "Description"]
        )
        self.skills_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        _configure_wrapping_table(self.skills_table, set())
        _enable_table_sorting(self.skills_table, self._sort_by_column)
        self.skills_table.horizontalHeader().setSortIndicator(
            self._sort_column,
            self._sort_order,
        )

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Known Skills"))
        layout.addWidget(self.skills_table)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Reloads skills and recent checks."""

        repository = self.repository()

        if repository is None:
            self.skills_table.setRowCount(0)
            return

        skills = repository.list_skills()
        skills.sort(
            key=self._sort_key,
            reverse=_sort_descending(self._sort_order),
        )
        self.skills_table.setRowCount(len(skills))

        for row_index, skill in enumerate(skills):
            level = int(skill.get("level", 1))
            self.skills_table.setItem(row_index, 0, _table_item(str(skill.get("name", ""))))
            self.skills_table.setItem(
                row_index,
                1,
                _table_item(_skill_level_label(level), level),
            )
            self.skills_table.setItem(
                row_index,
                2,
                _table_item(_skill_xp_progress_label(skill), _safe_int(skill.get("xp", 0), 0)),
            )
            self.skills_table.setItem(
                row_index,
                3,
                _table_item(str(skill.get("description", ""))),
            )

        _resize_wrapping_table_rows(self.skills_table)

    def _sort_by_column(self, column_index: int) -> None:
        """Sorts skills by a clicked header column."""

        self._sort_column, self._sort_order = _update_sort_state(
            self.skills_table,
            self._sort_column,
            self._sort_order,
            column_index,
        )
        self.refresh()

    def _sort_key(self, skill: dict[str, Any]) -> tuple[Any, str]:
        """Returns the active skill sort key."""

        name = str(skill.get("name", "")).casefold()

        if self._sort_column == 1:
            return _safe_int(skill.get("level", 1), 1), name

        if self._sort_column == 2:
            return _safe_int(skill.get("xp", 0), 0), name

        if self._sort_column == 3:
            return str(skill.get("description", "")).casefold(), name

        return name, name


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
            repository.set_setting("images.model", DEFAULT_IMAGE_MODEL)
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

    def _handle_narrator_enabled_toggled(self, checked: bool) -> None:
        """Saves narrator enabled changes and syncs dependent controls."""

        self._sync_narrator_control_states(checked)
        self._save_settings()

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


def _refresh_repository_calendar_time(repository: SaveRepository) -> None:
    """Recomputes the saved display time from current calendar settings."""

    current_minute = repository.get_current_calendar_minute()
    calendar_snapshot = build_calendar_snapshot(
        current_minute,
        repository.get_calendar_settings(),
    )
    repository.set_state_value("time", calendar_snapshot["display_label"])


def _apply_audio_settings_to_managers(
    repository: SaveRepository,
    *,
    sound_manager: SoundManagerProtocol | None,
    narration_player: NarrationPlayerProtocol | None,
) -> None:
    """Applies saved music, one-shot effect, and narrator settings to managers."""

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

    base_title = str(requested_title or "").strip() or "New Adventure"

    if not SaveRepository.save_title_exists(saves_dir, base_title):
        return base_title

    base_title, existing_suffix = _split_save_title_suffix(base_title)
    suffix = max(2, existing_suffix + 1)

    while True:
        candidate = f"{base_title} {suffix}"

        if not SaveRepository.save_title_exists(saves_dir, candidate):
            return candidate

        suffix += 1


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

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().casefold()

        if normalized in {"true", "1", "yes", "on"}:
            return True

        if normalized in {"false", "0", "no", "off"}:
            return False

    return default


def _clamped_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Returns an integer clamped to the provided range."""

    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        parsed_value = default

    return max(minimum, min(maximum, parsed_value))


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


def _player_command_markdown(command: str) -> str:
    """Formats a player command with a normal Markdown speaker label."""

    lines = [line.strip() for line in str(command or "").splitlines() if line.strip()]

    if not lines:
        return "**You:**"

    first_line, *remaining_lines = lines
    formatted_lines = [f"**You:** {first_line}"]
    formatted_lines.extend(remaining_lines)
    return "\n\n".join(formatted_lines)


def _safe_int(value, default: int) -> int:
    """Converts a value to int with a fallback."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _split_day_time(raw_time: str) -> tuple[str, str]:
    """Splits a combined world time string into day and time labels."""

    clean_time = raw_time.strip()

    if not clean_time:
        return "-", "-"

    if "," in clean_time:
        day, time = clean_time.split(",", 1)
        return day.strip() or "-", time.strip() or "-"

    return "-", clean_time


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
