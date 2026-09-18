from __future__ import annotations

from ai_adventure.ui.common import *  # noqa: F401,F403
from ai_adventure.ui.workers.gemini import GeminiVisualAssetWorker as _GeminiVisualAssetWorker


class _VisualAssetCoordinator(QObject):
    """Queues reusable image assets one at a time and applies results on the GUI thread."""

    assets_changed = Signal()
    initial_batch_finished = Signal(object)
    asset_status_changed = Signal(str, str, str)

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
        LOGGER.info(
            "Visual asset coordinator initialized: enabled=%s, images_dir=%s, "
            "api_key_path=%s.",
            self.enabled,
            self.images_dir,
            self.api_key_path,
        )
        if self.enabled:
            self.images_dir.mkdir(parents=True, exist_ok=True)

    def begin_initial_batch(self, repository: SaveRepository) -> None:
        """Queues the first finalized visual assets for this save."""

        self._initial_batch_repositories.add(str(repository.db_path))
        self._initial_batch_repository_objects[str(repository.db_path)] = repository
        self.scan(repository)
        self._finish_initial_batch_if_ready(repository)

    def prepare_initial_batch(
        self, repository: SaveRepository
    ) -> list[VisualAssetRequest]:
        """Registers initial assets without starting any paid image requests."""

        if not self.enabled or not _bool_setting(
            repository.get_setting("images.enabled", True), True
        ):
            return []

        repository_key = str(repository.db_path)
        self._initial_batch_repositories.add(repository_key)
        self._initial_batch_repository_objects[repository_key] = repository
        model = normalize_image_model(
            repository.get_setting("images.model", DEFAULT_IMAGE_MODEL)
        )
        pending: list[VisualAssetRequest] = []
        for request in AssetGenerationService.requests_for(repository):
            target_path = AssetGenerationService.target_path(
                repository, request, self.images_dir
            )
            try:
                repository.ensure_visual_asset(
                    asset_id=request.asset_id,
                    subject_type=request.subject_type,
                    subject_key=request.subject_key,
                    display_name=request.display_name,
                    descriptor_hash=request.descriptor_hash,
                    filename=save_relative_image_filename(repository, request),
                    prompt=request.prompt,
                    model=model,
                    image_style=request.image_style,
                    visual_description=request.description,
                    basic_name=request.basic_name,
                    resolution_tier=request.image_size,
                    message_ids=request.message_ids,
                    ready=target_path.is_file(),
                )
            except ValueError as error:
                LOGGER.warning(
                    "Skipping invalid initial visual asset request %s: %s",
                    request.asset_id,
                    error,
                )
                continue
            if not target_path.is_file():
                pending.append(request)
        return pending

    def finish_initial_batch(self, repository: SaveRepository) -> None:
        """Ends source-selection mode after every initial asset has a disposition."""

        repository_key = str(repository.db_path)
        self._initial_batch_repositories.discard(repository_key)
        self._initial_batch_repository_objects.pop(repository_key, None)

    def upload_initial_image(
        self,
        repository: SaveRepository,
        request: VisualAssetRequest,
        source_path: Path,
    ) -> tuple[bool, str]:
        """Normalizes one player-selected image into the save image cache."""

        if not source_path.is_file():
            return False, "The selected file no longer exists."
        try:
            target_path = AssetGenerationService.target_path(
                repository, request, self.images_dir
            )
            width, height = save_scaled_png(
                source_path.read_bytes(),
                target_path,
                max_pixels=request.maximum_pixels,
            )
            repository.set_visual_asset_status(
                request.asset_id,
                "ready",
                width=width,
                height=height,
            )
        except Exception as error:
            LOGGER.warning("Could not store uploaded image %s: %s", source_path, error)
            repository.set_visual_asset_status(
                request.asset_id,
                "failed",
                error_message=str(error),
            )
            self.asset_status_changed.emit(request.asset_id, "failed", str(error))
            return False, f"Could not use that image: {error}"
        self.asset_status_changed.emit(request.asset_id, "ready", "")
        return True, ""

    def skip_initial_image(
        self, repository: SaveRepository, request: VisualAssetRequest
    ) -> None:
        """Persists an explicit no-image choice without making it a retryable failure."""

        repository.set_visual_asset_status(
            request.asset_id,
            "skipped",
            error_message="Skipped by player during new-game image selection.",
        )

    def create_initial_image(
        self, repository: SaveRepository, request: VisualAssetRequest
    ) -> tuple[bool, str]:
        """Queues one initial image only after the player requests generation."""

        if not self.enabled:
            return False, "Image generation is disabled for this build."
        if not read_api_key(self.api_key_path):
            return False, "A Google Gemini API key is required to create this image."
        model = normalize_image_model(
            repository.get_setting("images.model", DEFAULT_IMAGE_MODEL)
        )
        limit = _clamped_int(
            repository.get_setting("images.maximum_generated", DEFAULT_IMAGE_LIMIT),
            DEFAULT_IMAGE_LIMIT,
            1,
            10_000,
        )
        if repository.visual_asset_generation_count() >= limit:
            return False, "The image-generation limit for this save has been reached."
        queued_for_repository = sum(
            1
            for queued_repository, _queued_request, _queued_model, _queued_limit in self._queue
            if str(queued_repository.db_path) == str(repository.db_path)
        )
        if repository.visual_asset_generation_count() + queued_for_repository >= limit:
            return False, "The image-generation limit for this save has already been reserved."
        record = repository.get_visual_asset_by_id(request.asset_id)
        if record is None:
            return False, "The image asset is no longer available."
        if record.get("status") == "generating":
            return True, ""
        repository.set_visual_asset_status(request.asset_id, "queued")
        if request.asset_id not in self._queued_asset_ids:
            self._queue.append((repository, request, model, limit))
            self._queued_asset_ids.add(request.asset_id)
        self._start_next()
        return True, ""

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

        if repository is None:
            return
        if not self.enabled:
            LOGGER.info(
                "Visual asset scan skipped: coordinator disabled for %s.",
                repository.db_path,
            )
            return
        images_enabled = _bool_setting(
            repository.get_setting("images.enabled", True), True
        )
        if not images_enabled:
            LOGGER.info(
                "Visual asset scan skipped: images.enabled=false for %s.",
                repository.db_path,
            )
            return

        has_api_key = bool(read_api_key(self.api_key_path))
        model = normalize_image_model(
            repository.get_setting("images.model", DEFAULT_IMAGE_MODEL)
        )
        limit = _clamped_int(
            repository.get_setting("images.maximum_generated", DEFAULT_IMAGE_LIMIT),
            DEFAULT_IMAGE_LIMIT,
            1,
            10_000,
        )
        requests = AssetGenerationService.requests_for(repository)
        queued_count = 0
        reused_count = 0
        ready_count = 0
        skipped_record_count = 0
        skipped_key_count = 0
        for request in requests:
            relative_filename = save_relative_image_filename(repository, request)
            target_path = AssetGenerationService.target_path(
                repository, request, self.images_dir
            )
            try:
                record = repository.ensure_visual_asset(
                    asset_id=request.asset_id,
                    subject_type=request.subject_type,
                    subject_key=request.subject_key,
                    display_name=request.display_name,
                    descriptor_hash=request.descriptor_hash,
                    filename=relative_filename,
                    prompt=request.prompt,
                    model=model,
                    image_style=request.image_style,
                    visual_description=request.description,
                    basic_name=request.basic_name,
                    resolution_tier=request.image_size,
                    message_ids=request.message_ids,
                    ready=target_path.is_file(),
                )
            except ValueError as error:
                # A newly added entity type should not prevent the rest of a
                # save's visual batch from completing.  The repository owns
                # the canonical allowlist; this guard keeps an invalid or
                # future request from stranding the initial-generation modal.
                LOGGER.warning(
                    "Skipping invalid visual asset request: subject=%s/%s "
                    "asset_id=%s error=%s.",
                    request.subject_type,
                    request.display_name,
                    request.asset_id,
                    error,
                )
                continue
            if target_path.is_file():
                ready_count += 1
                continue
            if record.get("status") != "queued":
                skipped_record_count += 1
                LOGGER.debug(
                    "Visual asset not queued: subject=%s/%s status=%s.",
                    request.subject_type,
                    request.display_name,
                    record.get("status"),
                )
                continue
            if not has_api_key:
                skipped_key_count += 1
                continue
            if request.asset_id in self._queued_asset_ids:
                continue
            reusable = AssetGenerationService.find_local_reuse(
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
                    reused_count += 1
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
            queued_count += 1
        LOGGER.info(
            "Visual asset scan summary: save=%s enabled=%s api_key=%s model=%s "
            "limit=%s generated=%s discovered=%s ready=%s reused=%s queued=%s "
            "skipped_record=%s skipped_missing_key=%s queue_total=%s.",
            repository.db_path,
            images_enabled,
            has_api_key,
            model,
            limit,
            repository.visual_asset_generation_count(),
            len(requests),
            ready_count,
            reused_count,
            queued_count,
            skipped_record_count,
            skipped_key_count,
            len(self._queue),
        )
        self._start_next()
        self._finish_initial_batch_if_ready(repository)

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
        """Normalizes and records one completed generated image."""

        try:
            width, height = save_scaled_png(
                image_bytes,
                self.images_dir
                / save_relative_image_filename(repository, request),
                max_pixels=request.maximum_pixels,
            )
        except Exception as error:
            LOGGER.warning("Failed to save generated image %s: %s", request.filename, error)
            repository.set_visual_asset_status(
                request.asset_id,
                "failed",
                error_message=str(error),
            )
            self.asset_status_changed.emit(request.asset_id, "failed", str(error))
            return
        repository.set_visual_asset_status(
            request.asset_id,
            "ready",
            width=width,
            height=height,
        )
        self.asset_status_changed.emit(request.asset_id, "ready", "")
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
        self.asset_status_changed.emit(request.asset_id, "failed", message)
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
