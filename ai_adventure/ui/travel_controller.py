"""Schedule private route assessments without delaying player travel."""

import logging

from PySide6.QtCore import QObject, QThreadPool, QTimer, Slot

from ai_adventure.application.travel_service import (
    assessment_key, build_assessment_context, commit_assessment, current_assessment,
    sync_location_music,
)
from ai_adventure.ui.workers.gemini import GeminiTravelAssessmentWorker


LOGGER = logging.getLogger(__name__)


class TravelAssessmentController(QObject):
    def __init__(self, shell):
        super().__init__(shell)
        self.shell = shell
        self._queued = False
        self._worker = None
        self._request = None
        self._attempted = None

    def schedule_check(self):
        if not self._queued:
            self._queued = True
            QTimer.singleShot(0, self, self.check)

    def check(self):
        self._queued = False
        repository = self.shell.repository
        if (self._worker is not None or repository is None or not self.shell.ai_enabled
                or self.shell.gemini_api_key_path is None or not repository.db_path.is_file()):
            return
        if (self.shell.story_screen._waiting_for_gm
                or self.shell.story_screen._initial_generation_pending):
            self._queued = True
            QTimer.singleShot(500, self, self.check)
            return
        try:
            if repository.survival_status()["fate"] != "alive":
                return
            manager = getattr(self.shell, "sound_manager", None)
            if manager is not None:
                sync_location_music(repository, manager.get_valid_track_names())
            if current_assessment(repository):
                return
            key = (str(repository.db_path), assessment_key(repository))
            if key == self._attempted:
                return  # Avoid an endless paid retry loop after a provider error.
            packet = build_assessment_context(repository)
            if not packet["origin"] or len(packet["locations"]) < 2:
                return
            worker = GeminiTravelAssessmentWorker(packet, self.shell.gemini_api_key_path)
            worker.signals.completed.connect(self._completed)
            self._request = (repository, packet)
            self._attempted = key
            self._worker = worker
            QThreadPool.globalInstance().start(worker)
        except Exception:
            LOGGER.exception("Could not prepare private travel assessment.")

    @Slot(object)
    def _completed(self, result):
        repository, packet = self._request
        self._worker = None
        self._request = None
        try:
            if self.shell.repository is repository and repository.db_path.is_file() and result is not None:
                if self.shell.story_screen._waiting_for_gm:
                    # Wait for the story commit, then assess its authoritative state.
                    self._attempted = None
                elif commit_assessment(repository, packet, result):
                    self.shell.travel_screen.refresh()
        except Exception:
            LOGGER.exception("Discarded invalid private travel assessment.")
        self.schedule_check()
