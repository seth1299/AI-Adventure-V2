from __future__ import annotations

import logging
import os
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
import importlib
from pathlib import Path
from collections.abc import Callable
from typing import Any, ClassVar, Literal, cast

from ai_adventure.audio.ssmd import strip_ssmd_markup_for_plain_tts
from ai_adventure.audio.pronunciation import (
    strip_phoneme_overrides,
)
from ai_adventure.audio.tts_settings import (
    normalize_narrator_voice_spec,
    parse_voice_blend_spec,
)
from ai_adventure.audio.voices import (
    DEFAULT_NARRATOR_VOICE,
    KOKORO_VOICES,
)
from ai_adventure.text_sanitization import sanitize_english_text


LOGGER = logging.getLogger(__name__)
PYKOKORO_MODEL_QUALITY = "q8"
PYKOKORO_SPACY_MODEL = "en_core_web_sm"
PykokoroModelQuality = Literal[
    "fp32",
    "fp16",
    "fp16-gpu",
    "q8",
    "q8f16",
    "q4",
    "q4f16",
    "uint8",
    "uint8f16",
]


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


class PyKokoroTTSEngine(TTSEngine):
    """PyKokoro pipeline TTS engine with SSMD and voice blending support."""

    DEFAULT_VOICE: ClassVar[str] = DEFAULT_NARRATOR_VOICE
    AVAILABLE_VOICES: ClassVar[dict[str, str]] = KOKORO_VOICES

    def __init__(
        self,
        *,
        output_directory: str | Path | None = None,
        model_quality: str = PYKOKORO_MODEL_QUALITY,
    ) -> None:
        """Initializes the PyKokoro pipeline wrappers."""

        from pykokoro import KokoroPipeline, PipelineConfig
        from pykokoro.generation_config import GenerationConfig
        from pykokoro.tokenizer import TokenizerConfig
        from pykokoro.voice_manager import VoiceBlend

        self.spacy_model = _resolve_pykokoro_spacy_model()
        self.output_directory = Path(output_directory or tempfile.gettempdir())
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self._KokoroPipeline = KokoroPipeline
        self._PipelineConfig = PipelineConfig
        self._GenerationConfig = GenerationConfig
        self._TokenizerConfig = TokenizerConfig
        self._VoiceBlend = VoiceBlend
        self.model_quality: PykokoroModelQuality = _normalize_pykokoro_model_quality(
            model_quality
        )
        self._pipeline_cache: dict[tuple[str, float, str], Any] = {}
        LOGGER.info("PyKokoro TTS initialized with %s model quality.", self.model_quality)

    def get_available_voices(self) -> dict[str, str]:
        """Returns Kokoro voice choices."""

        return dict(self.AVAILABLE_VOICES)

    def synthesize_to_file(self, request: TTSRequest) -> Path:
        """Synthesizes SSMD-aware text into a WAV file."""

        import soundfile as sf

        clean_text = sanitize_english_text(strip_phoneme_overrides(request.text))
        if not clean_text:
            raise ValueError("Cannot synthesize empty text.")

        output_path = self.output_directory / f"ai_adventure_tts_{uuid.uuid4().hex}.wav"
        voice_spec = normalize_narrator_voice_spec(request.voice or self.DEFAULT_VOICE)
        speed = max(0.5, min(2.0, float(request.speed)))
        language_code = str(request.language or "en-us").strip() or "en-us"
        pipeline = self._pipeline_for(voice_spec, speed, language_code)
        result = pipeline.run(clean_text)
        sf.write(
            str(output_path),
            result.audio,
            int(getattr(result, "sample_rate", 24000) or 24000),
        )
        return output_path

    def _pipeline_for(self, voice_spec: str, speed: float, language_code: str) -> Any:
        """Returns a cached pipeline for one voice/speed/language tuple."""

        cache_key = (voice_spec, round(speed, 3), language_code)

        if cache_key in self._pipeline_cache:
            return self._pipeline_cache[cache_key]

        generation = self._GenerationConfig(
            lang=language_code,
            speed=speed,
            pause_mode="auto",
            pause_clause=0.25,
            pause_sentence=0.55,
            pause_paragraph=0.95,
            pause_variance=0.05,
        )
        config = self._PipelineConfig(
            voice=self._voice_for_spec(voice_spec),
            model_quality=self.model_quality,
            generation=generation,
            tokenizer_config=self._TokenizerConfig(
                spacy_model=self.spacy_model,
                spacy_model_size="sm",
            ),
        )
        pipeline = self._KokoroPipeline(config)
        self._pipeline_cache[cache_key] = pipeline
        return pipeline

    def _voice_for_spec(self, voice_spec: str) -> Any:
        """Returns a PyKokoro voice id or VoiceBlend."""

        blend = parse_voice_blend_spec(voice_spec)

        if blend is None:
            return voice_spec

        return self._VoiceBlend.parse(
            f"{blend['voice_a']}:{blend['voice_a_weight']},"
            f"{blend['voice_b']}:{blend['voice_b_weight']}"
        )


class KokoroOnnxTTSEngine(TTSEngine):
    """Local Kokoro-ONNX TTS engine."""

    DEFAULT_VOICE: ClassVar[str] = DEFAULT_NARRATOR_VOICE
    AVAILABLE_VOICES: ClassVar[dict[str, str]] = KOKORO_VOICES
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

        annotated_text = sanitize_english_text(strip_phoneme_overrides(request.text))
        clean_text = strip_ssmd_markup_for_plain_tts(annotated_text)
        if not clean_text:
            raise ValueError("Cannot synthesize empty text.")

        output_path = self.output_directory / f"ai_adventure_tts_{uuid.uuid4().hex}.wav"
        voice_id = normalize_narrator_voice_spec(request.voice or self.DEFAULT_VOICE)
        voice = self._voice_for_spec(voice_id)
        blend = parse_voice_blend_spec(voice_id)
        language_voice_id = str(blend["voice_a"]) if blend is not None else voice_id
        language_code = self.LANGUAGE_BY_VOICE_PREFIX.get(
            language_voice_id[:1].lower(),
            str(request.language or "en-us").strip() or "en-us",
        )

        create_kwargs: dict[str, Any] = {
            "voice": voice,
            "speed": max(0.5, min(2.0, float(request.speed))),
            "lang": language_code,
        }

        samples, sample_rate = self._kokoro.create(
            clean_text,
            **create_kwargs,
        )
        sf.write(str(output_path), samples, sample_rate)
        return output_path

    def _voice_for_spec(self, voice_spec: str) -> Any:
        """Returns either a voice id or a blended Kokoro style vector."""

        blend = parse_voice_blend_spec(voice_spec)

        if blend is None:
            return voice_spec

        voice_a = self._kokoro.get_voice_style(str(blend["voice_a"]))
        voice_b = self._kokoro.get_voice_style(str(blend["voice_b"]))
        weight_a = float(blend["voice_a_weight"]) / 100.0
        weight_b = 1.0 - weight_a
        return (voice_a * weight_a + voice_b * weight_b).astype("float32")


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
        engine_factory=lambda: _create_preferred_tts_engine(
            model_path=model_path,
            voices_path=voices_path,
            output_directory=output_directory,
        ),
        default_voice=KokoroOnnxTTSEngine.DEFAULT_VOICE,
        available_voices=KokoroOnnxTTSEngine.AVAILABLE_VOICES,
    )


def _create_preferred_tts_engine(
    *,
    model_path: str | Path,
    voices_path: str | Path,
    output_directory: str | Path,
) -> TTSEngine:
    """Creates the configured Kokoro engine, defaulting to kokoro-onnx."""

    if _preferred_tts_engine_name() == "pykokoro":
        try:
            return PyKokoroTTSEngine(output_directory=output_directory)
        except ImportError as error:
            LOGGER.info("PyKokoro is unavailable; using kokoro-onnx fallback: %s", error)
        except Exception as error:
            LOGGER.warning(
                "PyKokoro initialization failed; using kokoro-onnx fallback: %s",
                error,
            )

    try:
        return KokoroOnnxTTSEngine(
            model_path=model_path,
            voices_path=voices_path,
            output_directory=output_directory,
        )
    except Exception as onnx_error:
        LOGGER.warning(
            "Kokoro-ONNX initialization failed; trying PyKokoro fallback: %s",
            onnx_error,
        )

    return PyKokoroTTSEngine(output_directory=output_directory)


def _preferred_tts_engine_name() -> str:
    """Returns the configured TTS engine preference."""

    value = str(os.getenv("AI_ADVENTURE_TTS_ENGINE", "")).strip().casefold()

    if value in {"pykokoro", "py-kokoro", "py_kokoro"}:
        return "pykokoro"

    return "kokoro_onnx"


def _normalize_pykokoro_model_quality(value: str) -> PykokoroModelQuality:
    """Returns a supported PyKokoro model quality, defaulting to Q8."""

    clean_value = str(value or "").strip().casefold()

    if clean_value in {
        "fp32",
        "fp16",
        "fp16-gpu",
        "q8",
        "q8f16",
        "q4",
        "q4f16",
        "uint8",
        "uint8f16",
    }:
        return cast(PykokoroModelQuality, clean_value)

    return cast(PykokoroModelQuality, PYKOKORO_MODEL_QUALITY)


def _resolve_pykokoro_spacy_model() -> str:
    """Returns a spaCy model name or path PyKokoro can load for tokenization."""

    import spacy

    configured_path = str(os.getenv("AI_ADVENTURE_SPACY_MODEL_PATH", "")).strip()

    if configured_path:
        spacy_path = Path(configured_path)
        if spacy_path.exists():
            spacy.load(spacy_path)
            return str(spacy_path)

        LOGGER.warning("Configured spaCy model path does not exist: %s", spacy_path)

    try:
        spacy.load(PYKOKORO_SPACY_MODEL)
        return PYKOKORO_SPACY_MODEL
    except OSError:
        model_package = importlib.import_module(PYKOKORO_SPACY_MODEL)
        package_path = Path(str(model_package.__file__)).resolve().parent
        spacy.load(package_path)
        return str(package_path)
