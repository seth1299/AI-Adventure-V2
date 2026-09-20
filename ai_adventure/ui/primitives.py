"""Shared, non-domain-specific Qt widgets and display helpers."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, TypeVar, cast

from PySide6.QtCore import QObject, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QImageReader, QPixmap, QTextCursor, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QFormLayout, QGraphicsPixmapItem,
    QGraphicsScene, QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QFileDialog, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox,
    QTableWidget, QTextBrowser, QTextEdit, QTimeEdit, QVBoxLayout, QWidget,
    QFrame,
)

from ai_adventure.app.app_paths import AppPaths
from ai_adventure.ai.model_catalog import normalize_text_model
from ai_adventure.app.features import is_tts_enabled
from ai_adventure.domain.rules.values import (
    bool_setting as domain_bool_setting,
    clamped_int as domain_clamped_int,
    safe_int as domain_safe_int,
)
from ai_adventure.calendar_system import format_time_of_day
from ai_adventure.infrastructure.sqlite import SaveRepository
from ai_adventure.skills.rules import MAX_SKILL_LEVEL, XP_THRESHOLDS_BY_LEVEL

_T = TypeVar("_T")

class _NoWheelComboBox(QComboBox):
    """Combo box that does not change selection from mouse-wheel scrolling."""

    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class _NoWheelSpinBox(QSpinBox):
    """Spin box that does not change value from mouse-wheel scrolling."""

    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class ClickableImageLabel(QLabel):
    """Small image preview that opens the original asset in a viewer when clicked."""

    clicked = Signal()

    def mousePressEvent(self, event: Any) -> None:
        pixmap = self.pixmap()
        if (
            event.button() == Qt.MouseButton.LeftButton
            and pixmap is not None
            and not pixmap.isNull()
        ):
            self.clicked.emit()
        super().mousePressEvent(event)


class MarkdownDisplay(QTextBrowser):
    """Borderless, read-only Markdown content for player-facing detail panels."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("background: transparent; border: none; padding: 0;")
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.document().setDocumentMargin(0)


class _ImageViewerGraphicsView(QGraphicsView):
    """Graphics view whose mouse wheel is an intuitive image zoom control."""

    zoomed = Signal(float)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.scale(1.25 if delta > 0 else 0.8, 1.25 if delta > 0 else 0.8)
            self.zoomed.emit(self.transform().m11())
            event.accept()
            return
        super().wheelEvent(event)


class ImageViewerDialog(QDialog):
    """Resizable viewer for generated images with fit, zoom, and pan support."""

    def __init__(
        self,
        *,
        pixmap: QPixmap,
        title: str = "Image",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or "Image")
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setMinimumSize(520, 420)
        self.setSizeGripEnabled(True)

        self._pixmap = pixmap
        self._scene = QGraphicsScene(self)
        self._image_item: QGraphicsPixmapItem | None = None
        if not pixmap.isNull():
            self._image_item = self._scene.addPixmap(pixmap)

        self._view = _ImageViewerGraphicsView(self._scene)
        self._view.setObjectName("imageViewerGraphicsView")
        self._view.setBackgroundBrush(Qt.GlobalColor.black)
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._view.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self._view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._view.zoomed.connect(lambda _zoom: self._update_zoom_label())
        self._view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._zoom_label = QLabel("100%")
        zoom_out = QPushButton("−")
        zoom_out.setToolTip("Zoom out")
        zoom_out.clicked.connect(lambda: self._zoom_by(0.8))
        zoom_in = QPushButton("+")
        zoom_in.setToolTip("Zoom in")
        zoom_in.clicked.connect(lambda: self._zoom_by(1.25))
        fit_button = QPushButton("Fit")
        fit_button.setToolTip("Fit the image in the viewer")
        fit_button.clicked.connect(self._fit_image)
        actual_button = QPushButton("100%")
        actual_button.setToolTip("Show the image at its original size")
        actual_button.clicked.connect(self._show_actual_size)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        controls = QHBoxLayout()
        controls.addWidget(zoom_out)
        controls.addWidget(zoom_in)
        controls.addWidget(fit_button)
        controls.addWidget(actual_button)
        controls.addWidget(self._zoom_label)
        controls.addStretch()
        controls.addWidget(close_button)

        layout = QVBoxLayout()
        layout.addWidget(self._view, 1)
        layout.addLayout(controls)
        self.setLayout(layout)
        self.resize(900, 700)
        QTimer.singleShot(0, self._fit_image)

    def _zoom_by(self, factor: float) -> None:
        """Zooms around the current view anchor."""

        if self._image_item is None:
            return
        self._view.scale(factor, factor)
        self._update_zoom_label()

    def _fit_image(self) -> None:
        """Fits the complete image within the available viewer area."""

        if self._image_item is None:
            return
        self._view.resetTransform()
        self._view.fitInView(
            self._image_item,
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self._update_zoom_label()

    def _show_actual_size(self) -> None:
        """Displays the image at its native pixel size."""

        if self._image_item is None:
            return
        self._view.resetTransform()
        self._view.centerOn(self._image_item)
        self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        """Updates the approximate zoom percentage shown beside the controls."""

        transform = self._view.transform()
        self._zoom_label.setText(f"{transform.m11() * 100:.0f}%")


def _open_image_viewer(label: ClickableImageLabel) -> None:
    """Opens a clickable preview's original image in the shared viewer."""

    image_path = str(label.property("imageViewerPath") or "").strip()
    if not image_path:
        return
    pixmap = QPixmap(image_path)
    if pixmap.isNull():
        return
    dialog = ImageViewerDialog(
        pixmap=pixmap,
        title=str(label.property("imageViewerTitle") or "Image"),
        parent=label.window(),
    )
    dialog.exec()




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
        self.on_visual_asset_upload: (
            Callable[[str, str, Path], tuple[bool, str]] | None
        ) = None
        self.on_visual_asset_generate: (
            Callable[[str, str], tuple[bool, str]] | None
        ) = None

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

    def choose_visual_asset(self, subject_type: str, subject_key: str) -> bool:
        """Lets the player replace one entity image with a local file."""

        if self.on_visual_asset_upload is None:
            QMessageBox.warning(self, "Images Unavailable", "Image selection is unavailable.")
            return False
        dialog = QFileDialog(self, "Select Image")
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilters(
            ["Images (*.png *.jpg *.jpeg *.webp *.bmp)", "All Files (*)"]
        )
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        selected_files = dialog.selectedFiles()
        if not selected_files:
            return False
        success, message = self.on_visual_asset_upload(
            subject_type,
            subject_key,
            Path(selected_files[0]),
        )
        if not success:
            QMessageBox.warning(self, "Image Selection Failed", message)
        return success

    def create_visual_asset(self, subject_type: str, subject_key: str) -> bool:
        """Queues an explicit AI image request for one visible entity."""

        if self.on_visual_asset_generate is None:
            QMessageBox.warning(self, "Images Unavailable", "Image creation is unavailable.")
            return False
        success, message = self.on_visual_asset_generate(subject_type, subject_key)
        if not success:
            QMessageBox.warning(self, "Image Creation Failed", message)
        else:
            QMessageBox.information(
                self,
                "Image Creation Queued",
                "The new image is being created. This view will refresh when it is ready.",
            )
        return success

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
    """Lazily decodes a scaled preview without loading the full image."""

    if path is None:
        label.clear()
        label.hide()
        return False
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    source_size = reader.size()
    if source_size.isValid() and not source_size.isEmpty():
        scale = min(
            1.0,
            max(64, maximum_width) / source_size.width(),
            max(64, maximum_height) / source_size.height(),
        )
        reader.setScaledSize(
            QSize(
                max(1, round(source_size.width() * scale)),
                max(1, round(source_size.height() * scale)),
            )
        )
    image = reader.read()
    if image.isNull():
        label.clear()
        label.hide()
        return False
    scaled = QPixmap.fromImage(image)
    label.setPixmap(scaled)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setAccessibleName(accessible_name)
    label.setProperty("imageViewerPath", str(path))
    label.setProperty("imageViewerTitle", accessible_name)
    if isinstance(label, ClickableImageLabel):
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.setToolTip(f"{accessible_name} — click to enlarge")
        if not bool(label.property("imageViewerConnected")):
            label.clicked.connect(lambda label=label: _open_image_viewer(label))
            label.setProperty("imageViewerConnected", True)
    else:
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


def _bool_setting(value: Any, default: bool) -> bool:
    """Reads a flexible boolean setting."""

    return domain_bool_setting(value, default)


def _clamped_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Returns an integer clamped to the provided range."""

    return domain_clamped_int(value, default, minimum, maximum)


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


def _set_markdown_text(
    text_edit: QTextEdit,
    markdown_text: str,
    *,
    preserve_blank_lines: bool = False,
) -> None:
    """Sets read-only body text as Markdown when the Qt runtime supports it.

    ``QTextDocument.setMarkdown`` intentionally collapses runs of blank lines
    into one paragraph break.  Story prose uses blank lines as a deliberate
    readability cue, so callers can request equivalent block spacing after the
    Markdown has been parsed without sacrificing headings, emphasis, or lists.
    """

    if hasattr(text_edit, "setMarkdown"):
        text_edit.setMarkdown(str(markdown_text or ""))
        if preserve_blank_lines:
            _preserve_markdown_blank_lines(text_edit)
        return

    text_edit.setPlainText(str(markdown_text or ""))


def _preserve_markdown_blank_lines(text_edit: QTextEdit) -> None:
    """Restores visible blank-line spacing between Markdown prose blocks."""

    block = text_edit.document().begin()
    while block.isValid():
        cursor = QTextCursor(block)
        # Keep the paragraph separator out of the selection. Qt otherwise
        # applies the next block's format to the preceding paragraph as well.
        cursor.setPosition(block.position())
        cursor.movePosition(
            QTextCursor.MoveOperation.EndOfBlock,
            QTextCursor.MoveMode.KeepAnchor,
        )
        block_format = cursor.blockFormat()
        is_list_item = block.textList() is not None
        next_block_is_list = (
            block.next().isValid() and block.next().textList() is not None
        )

        # A list item is already visually grouped with its neighboring items.
        # The question immediately before the action list also uses one line
        # break, while prose paragraphs use the larger intentional separation.
        block_format.setTopMargin(0)
        block_format.setBottomMargin(
            0 if is_list_item or next_block_is_list else 12
        )
        cursor.setBlockFormat(block_format)
        block = block.next()


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
    root = sys.modules.get("ai_adventure.ui.main_window")
    return cast(_T, getattr(root, name, default)) if root is not None else default


__all__ = [
    "_NoWheelComboBox",
    "_NoWheelSpinBox",
    "RepositoryBackedWidget",
    "ClickableImageLabel",
    "MarkdownDisplay",
    "ImageViewerDialog",
    "_set_generated_image",
    "_screen_content_signature",
    "_text_model_from_ai_packet",
    "_bool_setting",
    "_clamped_int",
    "_resolved_skill_checks_for_context",
    "_set_combo_to_data",
    "_set_combo_to_text",
    "_add_combo_options",
    "_set_markdown_text",
    "_safe_int",
    "_split_list",
    "_split_loot_items",
    "_slug_for_id",
    "_join_list",
    "_join_list",
    "_status_label",
    "_skill_level_label",
    "_skill_xp_progress_label",
    "_calendar_event_time_label",
    "_selectable_label",
    "_inventory_location_label",
    "_pluralize_inventory_phrase",
    "_inventory_quantity_display",
    "_inventory_item_display_name",
    "_INVENTORY_INVARIANT_PLURALS",
    "_INVENTORY_IRREGULAR_PLURALS",
    "_INVENTORY_UNIT_ABBREVIATIONS",
    "_main_window_override"
]
