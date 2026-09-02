"""Background Qt workers for Gemini and visual-asset requests.

Workers perform only the provider call.  Signals return results to the GUI
thread, where repositories and widgets remain under application control.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from ai_adventure.ai.model_catalog import normalize_text_model
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


class GeminiStoryWorker(QObject):
    """Runs one Gemini story request away from the Qt UI thread."""

    completed = Signal(object)
    configuration_error = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(self, context_packet: dict[str, Any], api_key_path: Path | None = None) -> None:
        super().__init__()
        self._context_packet = context_packet
        self._api_key_path = api_key_path

    @Slot()
    def run(self) -> None:
        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")
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


class GeminiSkillCheckPlanWorker(QObject):
    """Runs one lightweight skill-check planning request away from the UI."""

    completed = Signal(object)
    configuration_error = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(self, context_packet: dict[str, Any], api_key_path: Path | None = None) -> None:
        super().__init__()
        self._context_packet = context_packet
        self._api_key_path = api_key_path

    @Slot()
    def run(self) -> None:
        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")
            result = StoryTurnService(
                api_key_path=self._api_key_path,
                model=_text_model_from_ai_packet(self._context_packet),
            ).plan_skill_checks(self._context_packet)
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
    """Runs one Gemini new-game request away from the Qt UI thread."""

    completed = Signal(object)
    configuration_error = Signal(str)
    request_failed = Signal(str)
    failed = Signal()
    finished = Signal()

    def __init__(self, setup_packet: dict[str, Any], api_key_path: Path | None = None) -> None:
        super().__init__()
        self._setup_packet = setup_packet
        self._api_key_path = api_key_path

    @Slot()
    def run(self) -> None:
        try:
            if GeminiNarrationService is None:
                raise GeminiConfigurationError("AI generation is disabled in this build.")
            result = GeminiNarrationService(
                api_key_path=self._api_key_path,
                model=_text_model_from_ai_packet(self._setup_packet),
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
