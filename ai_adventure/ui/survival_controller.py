"""Coordinate saved fate choices and asynchronous recovery across all game tabs."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, QTimer, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from ai_adventure.application.survival_service import build_recovery_context
from ai_adventure.survival import SURVIVAL_METERS
from ai_adventure.ui.survival import SurvivalDeathDialog
from ai_adventure.ui.workers.gemini import GeminiSurvivalRecoveryWorker

LOGGER = logging.getLogger(__name__)


class SurvivalController(QObject):
    def __init__(self, shell) -> None:
        super().__init__(shell)
        self.shell = shell
        self.thread: QThread | None = None
        self.worker = None
        self._recovery_repository = None
        self._recovery_incident_id = ""
        self._queued = False
        self._choosing = False
        self._needs_retry = False
        self.banner = QWidget(shell)
        row = QHBoxLayout(self.banner)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.retry_button = QPushButton("Retry Recovery")
        self.retry_button.clicked.connect(self._retry)
        row.addWidget(self.message, 1)
        row.addWidget(self.retry_button)
        self.banner.hide()

    def bind(self) -> None:
        self._needs_retry = False
        self.schedule_check()

    def schedule_check(self) -> None:
        if not self._queued:
            self._queued = True
            QTimer.singleShot(0, self, self.check)

    def check(self) -> None:
        self._queued = False
        repository = self.shell.repository
        if repository is None or not self.shell.isVisible() or not repository.db_path.is_file():
            self.banner.hide()
            return
        status = repository.survival_status()
        self.banner.setVisible(status["fate"] != "alive")
        self.retry_button.setVisible(status["fate"] == "recovering" and self._needs_retry)
        if status["fate"] == "accepted":
            self.message.setText("You accepted your fate. This adventure is read-only; Read Aloud and images remain available.")
            return
        if self._choosing:
            return
        if status["fate"] == "pending":
            repository.mark_survival_death()
            self._choosing = True
            try:
                dialog = SurvivalDeathDialog([meter.title() for meter in SURVIVAL_METERS if status[meter] == 0], self.shell)
                dialog.exec()
                repository.choose_survival_fate(dialog.choice)
            except Exception as error:
                LOGGER.exception("Could not persist the survival fate decision.")
                QMessageBox.warning(self.shell, "Could not save fate", str(error))
                self.schedule_check()
                return
            finally:
                self._choosing = False
            self.shell.refresh_screens()
            status = repository.survival_status()
        if status["fate"] == "recovering":
            if not self._needs_retry:
                self.message.setText("Preparing your recovery scene. Play is paused until you wake elsewhere.")
            if self.thread is None and not self._needs_retry:
                self._start_recovery(repository)

    def _retry(self) -> None:
        self._needs_retry = False
        self.schedule_check()

    def _start_recovery(self, repository) -> None:
        try:
            packet = build_recovery_context(repository)
        except Exception as error:
            self._failed(repository, str(error))
            return
        incident_id = packet["death"]["incident_id"]
        thread = QThread(self)
        worker = GeminiSurvivalRecoveryWorker(packet, self.shell.gemini_api_key_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._handle_recovery_result)
        worker.failed.connect(self._handle_recovery_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_worker)
        self._recovery_repository = repository
        self._recovery_incident_id = incident_id
        self.thread, self.worker = thread, worker
        thread.start()

    @Slot(object)
    def _handle_recovery_result(self, result) -> None:
        self._completed(self._recovery_repository, self._recovery_incident_id, result)

    @Slot(str)
    def _handle_recovery_failure(self, error: str) -> None:
        self._failed(self._recovery_repository, error)

    def _completed(self, repository, incident_id, result) -> None:
        try:
            repository.commit_survival_recovery(incident_id, result)
        except Exception as error:
            self._failed(repository, str(error))
            return
        if self.shell.repository is repository:
            self._needs_retry = False
            self.shell.refresh_screens()
            self.schedule_check()

    def _failed(self, repository, error: str) -> None:
        LOGGER.warning("Survival recovery remains pending: %s", error)
        if self.shell.repository is repository:
            self._needs_retry = True
            self.banner.show()
            self.message.setText("Recovery could not finish. Your save and skill XP are unchanged. "
                                 "Retry Recovery when Gemini is available, or return to the Main Menu.")
            self.message.setToolTip(error)
            self.retry_button.show()

    @Slot()
    def _clear_worker(self) -> None:
        self.thread = self.worker = self._recovery_repository = None
        self._recovery_incident_id = ""
        if (self.shell.repository is not None and self.shell.repository.db_path.is_file()
                and self.shell.repository.survival_status()["fate"] == "recovering" and not self._needs_retry):
            self.schedule_check()
