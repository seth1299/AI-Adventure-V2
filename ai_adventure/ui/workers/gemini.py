"""Background Qt workers for Gemini and visual-asset requests.

Workers run provider orchestration. Signals return results and inventory-review
proposals to the GUI thread, which owns repositories, widgets, and player choices.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from ai_adventure.ai.model_catalog import normalize_text_model
from ai_adventure.ai.request_metrics import benchmark_message
from ai_adventure.application.story_turn_service import StoryTurnService
from ai_adventure.app.features import is_ai_enabled
from ai_adventure.infrastructure.images import GeminiVisualAssetService, VisualAssetRequest

LOGGER = logging.getLogger(__name__)

if is_ai_enabled():
    from ai_adventure.infrastructure.gemini import (
        GeminiConfigurationError,
        GeminiNarrationService,
        GeminiRequestError,
    )
else:
    GeminiNarrationService = None
    GeminiConfigurationError = RuntimeError
    GeminiRequestError = RuntimeError


def _text_model_from_ai_packet(packet: dict[str, Any]) -> str:
    preferences: Any = packet.get("player_ai_preferences")
    state = packet.get("state")
    if isinstance(state, dict) and isinstance(state.get("player_ai_preferences"), dict):
        preferences = state["player_ai_preferences"]
    if not isinstance(preferences, dict):
        preferences = {}
    return normalize_text_model(preferences.get("text_model"))


class _TravelAssessmentSignals(QObject):
    completed = Signal(object)


class GeminiTravelAssessmentWorker(QRunnable):
    """No repository or widget access on this background thread."""

    def __init__(self, packet: dict[str, Any], api_key_path: Path | None):
        super().__init__()
        self.packet = packet
        self.api_key_path = api_key_path
        self.signals = _TravelAssessmentSignals()

    def run(self) -> None:
        result = None
        try:
            if GeminiNarrationService is not None:
                with benchmark_message(None, message_type="Travel Assessments"):
                    result = GeminiNarrationService(
                        api_key_path=self.api_key_path, model=_text_model_from_ai_packet(self.packet),
                    ).assess_travel_routes(self.packet)
        except Exception:
            LOGGER.exception("Private travel assessment failed; normal travel remains available.")
        self.signals.completed.emit(result)


class GeminiStoryWorker(QObject):
    """Runs one Gemini story request away from the Qt UI thread."""

    completed = Signal(object)
    configuration_error = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(self, context_packet: dict[str, Any], api_key_path: Path | None = None,
                 message_id: str | None = None) -> None:
        super().__init__()
        self._context_packet = context_packet
        self._api_key_path = api_key_path
        self._message_id = message_id

    @Slot()
    def run(self) -> None:
        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")
            with benchmark_message(self._message_id):
                result = StoryTurnService(
                    api_key_path=self._api_key_path,
                    model=_text_model_from_ai_packet(self._context_packet),
                ).generate_response(self._context_packet)
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


class GeminiSurvivalRecoveryWorker(QObject):
    """Generate a recovery scene; the GUI thread commits its bounded result."""

    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, packet: dict[str, Any], api_key_path: Path | None) -> None:
        super().__init__()
        self.packet = packet
        self.api_key_path = api_key_path

    @Slot()
    def run(self) -> None:
        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")
            with benchmark_message(self.packet["death"]["incident_id"]):
                result = GeminiNarrationService(
                    api_key_path=self.api_key_path, model=_text_model_from_ai_packet(self.packet),
                ).generate_survival_recovery(self.packet)
        except Exception as error:
            LOGGER.warning("Survival recovery generation failed: %s", error)
            self.failed.emit(str(error))
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()


class GeminiD20TestPlanWorker(QObject):
    """Runs one lightweight skill-check planning request away from the UI."""

    completed = Signal(object)
    configuration_error = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(self, context_packet: dict[str, Any], api_key_path: Path | None = None,
                 message_id: str | None = None) -> None:
        super().__init__()
        self._context_packet = context_packet
        self._api_key_path = api_key_path
        self._message_id = message_id

    @Slot()
    def run(self) -> None:
        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")
            with benchmark_message(self._message_id):
                result = StoryTurnService(
                    api_key_path=self._api_key_path,
                    model=_text_model_from_ai_packet(self._context_packet),
                ).plan_d20_tests(self._context_packet)
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


class GeminiNewGameWorker(QObject):
    """Runs staged Gemini new-game generation away from the Qt UI thread."""

    completed = Signal(object)
    inventory_review_requested = Signal(object)
    phase_changed = Signal(str)
    configuration_error = Signal(str)
    request_failed = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(self, setup_packet: dict[str, Any], api_key_path: Path | None = None,
                 *, opening_request_path: Path | None = None) -> None:
        super().__init__()
        self._setup_packet = setup_packet
        self._api_key_path = api_key_path
        self._opening_request_path = opening_request_path

    def _review_starting_inventory(self, state, setup_packet, approved_items):
        from ai_adventure.application.starting_inventory import StartingInventoryReview
        proposal = StartingInventoryReview.from_generated_state(state, setup_packet, approved_items)
        # MainWindow connects with BlockingQueuedConnection: the provider worker
        # waits while the GUI reviews possessions, with no transaction held open.
        self.inventory_review_requested.emit(proposal)
        return proposal.approved_items

    @Slot()
    def run(self) -> None:
        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")
            service = GeminiNarrationService(
                api_key_path=self._api_key_path,
                model=_text_model_from_ai_packet(self._setup_packet),
            )
            staged_generator = getattr(service, "generate_new_game_world_staged", None)
            if callable(staged_generator):
                options = {}
                if self._opening_request_path is not None:
                    from ai_adventure.application.opening_request import OpeningRequest
                    options["opening_request"] = OpeningRequest(self._opening_request_path)
                    options["inventory_review_callback"] = self._review_starting_inventory
                result = staged_generator(
                    self._setup_packet,
                    progress_callback=self.phase_changed.emit,
                    **options,
                )
            else:
                # Keeps lightweight test doubles and older integrations usable.
                result = service.generate_new_game_world(self._setup_packet)
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


class GeminiVisualAssetWorker(QObject):
    """Runs one separately billed image-only request away from the UI."""

    completed = Signal(object, bytes, str)
    failed = Signal(object, str)
    finished = Signal()

    def __init__(self, request: VisualAssetRequest, *, api_key_path: Path, model: str) -> None:
        super().__init__()
        self._request = request
        self._api_key_path = api_key_path
        self._model = model

    @Slot()
    def run(self) -> None:
        try:
            with benchmark_message(None, message_type="Image Generation"):
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
