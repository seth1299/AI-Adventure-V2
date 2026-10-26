from __future__ import annotations

from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.image_file_dialog import ImageFileDialog as QFileDialog


class NewGameImageSourceDialog(QDialog):
    """Lets the player choose an upload or explicit generation for each asset."""

    def __init__(
        self,
        requests: list[VisualAssetRequest],
        *,
        choose_file: Callable[[VisualAssetRequest, Path], tuple[bool, str]],
        create_image: Callable[[VisualAssetRequest], tuple[bool, str]] | None,
        skip_image: Callable[[VisualAssetRequest], None],
        parent: QWidget | None = None,
        image_directory: str = "",
        preview_path: Callable[[VisualAssetRequest], Path | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._choose_file_callback = choose_file
        self._image_directory = image_directory
        self._preview_path = preview_path
        self._preview_labels: dict[str, ClickableImageLabel] = {}
        self._create_image_callback = create_image
        self._skip_image_callback = skip_image
        self._requests = list(requests)
        self.copy_prompt_buttons: dict[str, QPushButton] = {}
        self._rows: dict[
            str,
            tuple[QLabel, QPushButton, QCheckBox | None, QPushButton],
        ] = {}
        self._resolved: set[str] = set()
        self._allow_close = False
        self._pending: set[str] = set()
        self._started = False
        self._dispatching = False

        self.setWindowTitle("Choose New Game Images")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)
        self.resize(860, 620)

        title = QLabel("Choose an image source for each new-game subject.")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        title.setWordWrap(True)
        explanation = QLabel(
            "Choose a local image, copy its prompt for another image generator, or skip a subject for now."
            if create_image is None
            else "Choose a local image, ask Gemini to create one using the existing "
            "image prompt, copy its prompt for another image generator, or skip a subject for now. Nothing is generated until "
            "you press Next. Unselected subjects will be skipped."
        )
        explanation.setWordWrap(True)
        self.skip_all_button = QPushButton("Skip All for now")
        self.skip_all_button.clicked.connect(self._skip_all)
        self.create_all_button = QPushButton("Create all images for me")
        self.create_all_button.setVisible(create_image is not None)
        self.create_all_button.clicked.connect(self._select_all)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(10)
        for request in self._requests:
            content_layout.addWidget(self._build_request_row(request))
        content_layout.addStretch()

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(content)

        self.continue_button = QPushButton("Next")
        self.continue_button.clicked.connect(self._continue)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(explanation)
        layout.addWidget(_button_row(self.create_all_button, self.skip_all_button),
                         alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(scroll_area, 1)
        layout.addWidget(
            _button_row(self.continue_button),
            alignment=Qt.AlignmentFlag.AlignRight,
        )

    def _build_request_row(self, request: VisualAssetRequest) -> QWidget:
        """Builds one source-choice row for a finalized visual subject."""

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(10, 8, 10, 8)

        subject_type = {
            "player": "Player character",
            "location": "Location",
            "inventory": "Inventory item",
            "npc": "NPC",
            "bestiary": "Creature",
        }.get(request.subject_type, request.subject_type.title())
        heading = QLabel(f"{subject_type}: {request.display_name}")
        heading.setStyleSheet("font-weight: bold;")
        heading.setWordWrap(True)
        description = QLabel(request.description)
        description.setWordWrap(True)
        description.setStyleSheet("font-size: 11px;")

        status = QLabel("No image source selected")
        status.setWordWrap(True)
        upload_button = QPushButton("Choose File to Upload")
        generate_button = (
            QCheckBox("Create an Image for me")
            if self._create_image_callback is not None
            else None
        )
        skip_button = QPushButton("Skip for now")
        copy_button = QPushButton("Copy Image Prompt")
        copy_button.setToolTip("Copy the selected art style, subject details, and image instructions to the clipboard.")
        copy_button.clicked.connect(
            lambda _checked=False, asset_request=request: self._copy_image_prompt(asset_request)
        )
        self.copy_prompt_buttons[request.asset_id] = copy_button
        upload_button.clicked.connect(
            lambda _checked=False, asset_request=request: self._choose_file(asset_request)
        )
        if generate_button is not None:
            generate_button.toggled.connect(
                lambda _checked=False, asset_request=request: self._create_image(
                    asset_request
                )
            )
        skip_button.clicked.connect(
            lambda _checked=False, asset_request=request: self._skip(asset_request)
        )

        buttons = _button_row(
            *(
                [upload_button, generate_button, copy_button, skip_button]
                if generate_button is not None
                else [upload_button, copy_button, skip_button]
            )
        )
        frame_layout.addWidget(heading)
        frame_layout.addWidget(description)
        preview = ClickableImageLabel(frame)
        preview.setObjectName("newGameImagePreview")
        self._preview_labels[request.asset_id] = preview
        frame_layout.addWidget(preview, alignment=Qt.AlignmentFlag.AlignCenter)
        if self._refresh_preview(request):
            status.setText("Image available; kept unless replaced")
        frame_layout.addWidget(status)
        frame_layout.addWidget(buttons)
        self._rows[request.asset_id] = (
            status,
            upload_button,
            generate_button,
            skip_button,
        )
        return frame

    def _refresh_preview(self, request: VisualAssetRequest) -> bool:
        """Shows the same cached or bundled artwork used by the game screens."""

        path = self._preview_path(request) if self._preview_path is not None else None
        return _set_generated_image(
            self._preview_labels[request.asset_id], path,
            maximum_width=128, maximum_height=128,
            accessible_name=f"Image preview for {request.display_name}",
        )

    def _copy_image_prompt(self, request: VisualAssetRequest) -> None:
        """Export a concise art brief for any image generator."""
        QApplication.clipboard().setText(request.external_prompt)
        self.copy_prompt_buttons[request.asset_id].setText("Image Prompt Copied")

    def _choose_file(self, request: VisualAssetRequest) -> None:
        """Prompts for and stores one player-selected image."""

        dialog = QFileDialog(self, f"Choose an image for {request.display_name}",
                             self._image_directory)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilters(
            ["Images (*.png *.jpg *.jpeg *.webp *.bmp)", "All Files (*)"]
        )
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setModal(True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_files = dialog.selectedFiles()
        if not selected_files:
            return
        success, message = self._choose_file_callback(
            request,
            Path(selected_files[0]),
        )
        if success:
            self._set_resolved(request, "Using uploaded image")
        else:
            self._set_status(request, message or "The image could not be stored.")

    def _create_image(self, request: VisualAssetRequest) -> None:
        """Records a choice; paid work starts only after Next."""
        checkbox = self._rows[request.asset_id][2]
        message = "Will generate after Next"
        if not checkbox.isChecked():
            message = ("Will keep existing image after Next"
                       if self._refresh_preview(request) else "Will skip after Next")
        self._set_status(request, message)

    def _select_all(self) -> None:
        answer = QMessageBox.warning(
            self, "Create All Images?",
            "Generating every image may take a long time and use many API requests. "
            "Select all remaining images for generation after you press Next?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            for request in self._requests:
                checkbox = self._rows[request.asset_id][2]
                if checkbox is not None and checkbox.isEnabled():
                    checkbox.setChecked(True)

    def _skip(self, request: VisualAssetRequest) -> None:
        """Leaves one subject without an image for now."""

        checkbox = self._rows[request.asset_id][2]
        if checkbox is not None:
            checkbox.setChecked(False)
        self._set_status(request, "Will keep existing image after Next"
                         if self._refresh_preview(request) else "Will skip after Next")

    def _skip_all(self) -> None:
        """Clear generation choices while preserving uploaded images."""

        for request in self._requests:
            if request.asset_id not in self._resolved and self._rows[request.asset_id][3].isEnabled():
                self._skip(request)

    def set_asset_status(self, asset_id: str, status: str, message: str = "") -> None:
        """Updates one row after the coordinator finishes an explicit request."""

        request = next(
            (candidate for candidate in self._requests if candidate.asset_id == asset_id),
            None,
        )
        if request is None:
            return
        if status == "ready":
            self._pending.discard(asset_id)
            self._set_resolved(request, "Image ready")
            return
        if status == "failed":
            self._pending.discard(asset_id)
            self._set_status(request, message or "Image generation failed.")
            row = self._rows[asset_id]
            row[1].setEnabled(True)
            if row[2] is not None:
                row[2].setChecked(False)
                row[2].setEnabled(True)
            row[3].setEnabled(True)
            self._set_status(request, (message or "Image generation failed.") +
                             " Select generation to retry, upload an image, or press Next to skip.")
            self._finish_if_settled()

    def _set_status(self, request: VisualAssetRequest, message: str) -> None:
        """Displays a non-terminal status without resolving the row."""

        self._rows[request.asset_id][0].setText(message)

    def _set_resolved(self, request: VisualAssetRequest, message: str) -> None:
        """Marks a row complete and waits for any remaining selected images."""

        status, upload_button, generate_button, skip_button = self._rows[request.asset_id]
        if self._refresh_preview(request) and message == "Skipped for now":
            message = "Using existing image"
        status.setText(message)
        upload_button.setEnabled(False)
        if generate_button is not None:
            generate_button.setEnabled(False)
        skip_button.setEnabled(False)
        self._resolved.add(request.asset_id)
        self._finish_if_settled()

    def _finish_if_settled(self) -> None:
        if self._dispatching:
            return
        self.continue_button.setEnabled(not self._pending)
        self.skip_all_button.setEnabled(not self._pending)
        self.create_all_button.setEnabled(not self._pending)
        if self._started and len(self._resolved) == len(self._requests):
            self._allow_close = True
            self.accept()

    def _continue(self) -> None:
        """Commit all skips before queueing only the checked subjects."""
        if self._pending:
            return
        selected = [request for request in self._requests
                    if request.asset_id not in self._resolved
                    and self._rows[request.asset_id][2] is not None
                    and self._rows[request.asset_id][2].isChecked()]
        self._started = self._dispatching = True
        self.skip_all_button.setEnabled(False)
        self.create_all_button.setEnabled(False)
        self.continue_button.setEnabled(False)
        for request in self._requests:
            if request.asset_id not in self._resolved and request not in selected:
                self._skip_image_callback(request)
                self._set_resolved(request, "Skipped for now")
        self._pending.update(request.asset_id for request in selected)
        for request in selected:
            status, upload, checkbox, skip = self._rows[request.asset_id]
            status.setText("Image generation queued...")
            for control in (upload, checkbox, skip):
                control.setEnabled(False)
            success, message = self._create_image_callback(request)
            if not success:
                self.set_asset_status(request.asset_id, "failed", message)
        self._dispatching = False
        self._finish_if_settled()

    def reject(self) -> None:
        """Prevents Escape or the window close button from bypassing source choices."""

        if self._allow_close:
            super().reject()
