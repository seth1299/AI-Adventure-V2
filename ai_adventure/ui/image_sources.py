from __future__ import annotations

from PySide6.QtWidgets import QFileDialog

from ai_adventure.ui.common import *  # noqa: F401,F403


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
    ) -> None:
        super().__init__(parent)
        self._choose_file_callback = choose_file
        self._create_image_callback = create_image
        self._skip_image_callback = skip_image
        self._requests = list(requests)
        self._rows: dict[
            str,
            tuple[QLabel, QPushButton, QPushButton | None, QPushButton],
        ] = {}
        self._resolved: set[str] = set()
        self._allow_close = False

        self.setWindowTitle("Choose New Game Images")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)
        self.resize(860, 620)

        title = QLabel("Choose an image source for each new-game subject.")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        title.setWordWrap(True)
        explanation = QLabel(
            "Choose a local image or skip a subject for now."
            if create_image is None
            else "Choose a local image, ask Gemini to create one using the existing "
            "image prompt, or skip a subject for now. Nothing is generated until "
            "you press Create an Image for me."
        )
        explanation.setWordWrap(True)

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

        self.continue_button = QPushButton("Continue")
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self._continue)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(explanation)
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
            QPushButton("Create an Image for me")
            if self._create_image_callback is not None
            else None
        )
        skip_button = QPushButton("Skip for now")
        upload_button.clicked.connect(
            lambda _checked=False, asset_request=request: self._choose_file(asset_request)
        )
        if generate_button is not None:
            generate_button.clicked.connect(
                lambda _checked=False, asset_request=request: self._create_image(
                    asset_request
                )
            )
        skip_button.clicked.connect(
            lambda _checked=False, asset_request=request: self._skip(asset_request)
        )

        buttons = _button_row(
            *(
                [upload_button, generate_button, skip_button]
                if generate_button is not None
                else [upload_button, skip_button]
            )
        )
        frame_layout.addWidget(heading)
        frame_layout.addWidget(description)
        frame_layout.addWidget(status)
        frame_layout.addWidget(buttons)
        self._rows[request.asset_id] = (
            status,
            upload_button,
            generate_button,
            skip_button,
        )
        return frame

    def _choose_file(self, request: VisualAssetRequest) -> None:
        """Prompts for and stores one player-selected image."""

        # Use Qt's editable file dialog so a complete path can be pasted into
        # the location/file-name field. The native Windows picker exposes a
        # breadcrumb address bar but does not reliably allow path editing.
        dialog = QFileDialog(self, f"Choose an image for {request.display_name}")
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
        """Queues one explicit Gemini image-generation request."""

        if self._create_image_callback is None:
            return
        success, message = self._create_image_callback(request)
        if not success:
            self._set_status(request, message or "The image could not be queued.")
            return
        status, upload_button, generate_button, skip_button = self._rows[request.asset_id]
        status.setText("Image generation queued...")
        upload_button.setEnabled(False)
        if generate_button is not None:
            generate_button.setEnabled(False)
        skip_button.setEnabled(False)

    def _skip(self, request: VisualAssetRequest) -> None:
        """Leaves one subject without an image for now."""

        self._skip_image_callback(request)
        self._set_resolved(request, "Skipped for now")

    def set_asset_status(self, asset_id: str, status: str, message: str = "") -> None:
        """Updates one row after the coordinator finishes an explicit request."""

        request = next(
            (candidate for candidate in self._requests if candidate.asset_id == asset_id),
            None,
        )
        if request is None:
            return
        if status == "ready":
            self._set_resolved(request, "Image ready")
            return
        if status == "failed":
            self._set_status(request, message or "Image generation failed.")
            row = self._rows[asset_id]
            row[1].setEnabled(True)
            if row[2] is not None:
                row[2].setEnabled(True)
            row[3].setEnabled(True)

    def _set_status(self, request: VisualAssetRequest, message: str) -> None:
        """Displays a non-terminal status without resolving the row."""

        self._rows[request.asset_id][0].setText(message)

    def _set_resolved(self, request: VisualAssetRequest, message: str) -> None:
        """Marks a row complete and refreshes the Continue button."""

        status, upload_button, generate_button, skip_button = self._rows[request.asset_id]
        status.setText(message)
        upload_button.setEnabled(False)
        if generate_button is not None:
            generate_button.setEnabled(False)
        skip_button.setEnabled(False)
        self._resolved.add(request.asset_id)
        self.continue_button.setEnabled(
            all(candidate.asset_id in self._resolved for candidate in self._requests)
        )

    def _continue(self) -> None:
        """Closes the dialog after every subject has an explicit disposition."""

        self._allow_close = True
        self.accept()

    def reject(self) -> None:
        """Prevents Escape or the window close button from bypassing source choices."""

        if self._allow_close:
            super().reject()
