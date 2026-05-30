from __future__ import annotations

import logging
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import ClassVar


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TTSRequest:
    """A single text-to-speech synthesis request."""

    text: str
    voice: str
    speed: float = 1.0
    language: str = "en-us"


class TTSEngine(ABC):
    """Abstract text-to-speech engine."""

    DEFAULT_VOICE: ClassVar[str] = ""

    def get_available_voices(self) -> dict[str, str]:
        """Returns display-name-to-engine-voice mappings."""

        return {}

    def get_default_voice(self) -> str:
        """Returns the engine's default voice identifier."""

        return self.DEFAULT_VOICE

    @abstractmethod
    def synthesize_to_file(self, request: TTSRequest) -> Path:
        """Synthesizes request text to an audio file."""


class KokoroOnnxTTSEngine(TTSEngine):
    """Local Kokoro-ONNX TTS engine."""

    DEFAULT_VOICE: ClassVar[str] = "af_sarah"
    AVAILABLE_VOICES: ClassVar[dict[str, str]] = {
        "Heart (Female, US)": "af_heart",
        "Alloy (Female, US)": "af_alloy",
        "Aoede (Female, US)": "af_aoede",
        "Bella (Female, US)": "af_bella",
        "Jessica (Female, US)": "af_jessica",
        "Kore (Female, US)": "af_kore",
        "Nicole (Female, US)": "af_nicole",
        "Nova (Female, US)": "af_nova",
        "River (Female, US)": "af_river",
        "Sarah (Female, US)": "af_sarah",
        "Sky (Female, US)": "af_sky",
        "Adam (Male, US)": "am_adam",
        "Echo (Male, US)": "am_echo",
        "Eric (Male, US)": "am_eric",
        "Fenrir (Male, US)": "am_fenrir",
        "Liam (Male, US)": "am_liam",
        "Michael (Male, US)": "am_michael",
        "Onyx (Male, US)": "am_onyx",
        "Puck (Male, US)": "am_puck",
        "Santa (Male, US)": "am_santa",
        "Alice (Female, UK)": "bf_alice",
        "Emma (Female, UK)": "bf_emma",
        "Isabella (Female, UK)": "bf_isabella",
        "Lily (Female, UK)": "bf_lily",
        "Daniel (Male, UK)": "bm_daniel",
        "Fable (Male, UK)": "bm_fable",
        "George (Male, UK)": "bm_george",
        "Lewis (Male, UK)": "bm_lewis",
    }
    LANGUAGE_BY_VOICE_PREFIX: ClassVar[dict[str, str]] = {
        "a": "en-us",
        "b": "en-gb",
        "e": "es",
        "f": "fr-fr",
        "h": "hi",
        "i": "it",
        "p": "pt-br",
        "z": "zh",
    }

    def __init__(
        self,
        *,
        model_path: str | Path,
        voices_path: str | Path,
        output_directory: str | Path | None = None,
    ) -> None:
        """Initializes Kokoro once so chunked narration can reuse it."""

        from kokoro_onnx import Kokoro

        self.model_path = Path(model_path)
        self.voices_path = Path(voices_path)
        self.output_directory = Path(output_directory or tempfile.gettempdir())
        self.output_directory.mkdir(parents=True, exist_ok=True)

        if not self.model_path.exists():
            raise FileNotFoundError(f"Kokoro model file not found: {self.model_path}")

        if not self.voices_path.exists():
            raise FileNotFoundError(f"Kokoro voices file not found: {self.voices_path}")

        self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))
        LOGGER.info("Kokoro-ONNX TTS initialized.")

    def get_available_voices(self) -> dict[str, str]:
        """Returns Kokoro voice choices."""

        return dict(self.AVAILABLE_VOICES)

    def synthesize_to_file(self, request: TTSRequest) -> Path:
        """Synthesizes text into a WAV file."""

        import soundfile as sf

        clean_text = str(request.text or "").strip()
        if not clean_text:
            raise ValueError("Cannot synthesize empty text.")

        output_path = self.output_directory / f"ai_adventure_tts_{uuid.uuid4().hex}.wav"
        voice_id = str(request.voice or "").strip() or self.DEFAULT_VOICE
        language_code = self.LANGUAGE_BY_VOICE_PREFIX.get(
            voice_id[:1].lower(),
            str(request.language or "en-us").strip() or "en-us",
        )

        samples, sample_rate = self._kokoro.create(
            clean_text,
            voice=voice_id,
            speed=max(0.25, min(2.0, float(request.speed))),
            lang=language_code,
        )
        sf.write(str(output_path), samples, sample_rate)
        return output_path


class TTSManager:
    """Facade for whichever TTS engine is currently available."""

    def __init__(
        self,
        engine: TTSEngine | None = None,
        disabled_reason: str = "",
        *,
        engine_factory: Callable[[], TTSEngine] | None = None,
        default_voice: str = "",
        available_voices: dict[str, str] | None = None,
    ) -> None:
        self.engine = engine
        self.disabled_reason = disabled_reason.strip()
        self._engine_factory = engine_factory
        self._default_voice = default_voice
        self._available_voices = available_voices or {}

    @property
    def is_available(self) -> bool:
        """Returns True when a TTS engine is ready."""

        return self.engine is not None or self._engine_factory is not None

    def synthesize_to_file(self, request: TTSRequest) -> Path | None:
        """Synthesizes speech using the active engine, if available."""

        self._ensure_engine()

        if self.engine is None:
            if self.disabled_reason:
                LOGGER.warning("TTS is unavailable: %s", self.disabled_reason)
            return None

        try:
            return self.engine.synthesize_to_file(request)
        except Exception as error:
            LOGGER.warning("TTS synthesis failed: %s", error)
            return None

    def get_available_voices(self) -> dict[str, str]:
        """Returns voices supported by the active engine."""

        if self.engine is None:
            return dict(self._available_voices)

        return self.engine.get_available_voices()

    def get_default_voice(self) -> str:
        """Returns the active engine's default voice."""

        if self.engine is None:
            return self._default_voice

        return self.engine.get_default_voice()

    def _ensure_engine(self) -> None:
        """Lazily initializes an engine before first synthesis."""

        if self.engine is not None or self._engine_factory is None:
            return

        try:
            self.engine = self._engine_factory()
            self._engine_factory = None
            self.disabled_reason = ""
        except Exception as error:
            self.disabled_reason = f"Failed to initialize TTS engine: {error}"
            self._engine_factory = None
            LOGGER.warning(self.disabled_reason)


def create_tts_manager(
    *,
    model_path: str | Path,
    voices_path: str | Path,
    output_directory: str | Path,
) -> TTSManager:
    """Creates the default local Kokoro TTS manager."""

    return TTSManager(
        engine_factory=lambda: KokoroOnnxTTSEngine(
            model_path=model_path,
            voices_path=voices_path,
            output_directory=output_directory,
        ),
        default_voice=KokoroOnnxTTSEngine.DEFAULT_VOICE,
        available_voices=KokoroOnnxTTSEngine.AVAILABLE_VOICES,
    )
