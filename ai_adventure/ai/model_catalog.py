from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ai_adventure.ai.image_styles import DEFAULT_IMAGE_STYLE, normalize_image_style


# Curated from the stable (GA) entries on Google's Gemini API model pages.
# Ratings are comparative five-point UI guidance, not exact pricing or benchmarks.
# Preview, experimental, audio-only, embedding, and video-only models are excluded.
MODEL_CATALOG_REVIEWED_DATE = "2026-08-31"

DEFAULT_TEXT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-lite-image"

TEXT_MODEL_OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "value": "gemini-3.7-flash",
        "label": "Gemini 3.7 Flash",
        "description": (
            "Gemini 3.7 Flash is the next iteration in the Gemini 3 series of "
            "highly-capable, natively multimodal, reasoning models."
        ),
        "cost_rating": 4,
        "intelligence_rating": 5,
        "speed_rating": 3,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash",
    },
    {
        "value": "gemini-3.6-flash",
        "label": "Gemini 3.6 Flash",
        "description": (
            "Gemini 3.6 Flash provides sustained frontier-level intelligence "
            "optimized for real-world tasks at a higher speed and lower cost."
        ),
        "cost_rating": 3,
        "intelligence_rating": 4,
        "speed_rating": 4,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash",
    },
    {
        "value": "gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "description": (
            "Gemini 3.5 Flash provides sustained frontier-level intelligence "
            "optimized for real-world tasks at a higher speed and lower cost."
        ),
        "cost_rating": 3,
        "intelligence_rating": 4,
        "speed_rating": 4,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash",
    },
    {
        "value": "gemini-3.5-flash-lite",
        "label": "Gemini 3.5 Flash-Lite",
        "description": (
            "Gemini 3.5 Flash-Lite is a low-latency, cost-effective multimodal "
            "model optimized for high-throughput, low-cost execution."
        ),
        "cost_rating": 1,
        "intelligence_rating": 2,
        "speed_rating": 5,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite",
    },
    {
        "value": "gemini-3.1-flash-lite",
        "label": "Gemini 3.1 Flash-Lite",
        "description": (
            "Gemini 3.1 Flash-Lite is a low-latency, cost-effective multimodal "
            "model optimized for high-frequency, lightweight tasks."
        ),
        "cost_rating": 1,
        "intelligence_rating": 2,
        "speed_rating": 5,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite",
    },
    {
        "value": "gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "description": (
            "Our state-of-the-art thinking model, capable of reasoning over "
            "complex problems in code, math, and STEM."
        ),
        "cost_rating": 5,
        "intelligence_rating": 5,
        "speed_rating": 2,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro",
    },
    {
        "value": "gemini-2.5-flash",
        "label": "Gemini 2.5 Flash",
        "description": (
            "Our best model in terms of price-performance, offering well-rounded "
            "capabilities."
        ),
        "cost_rating": 3,
        "intelligence_rating": 3,
        "speed_rating": 4,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash",
    },
    {
        "value": "gemini-2.5-flash-lite",
        "label": "Gemini 2.5 Flash-Lite",
        "description": (
            "Our most cost-efficient multimodal model, offering the fastest "
            "performance for high-frequency, lightweight tasks."
        ),
        "cost_rating": 1,
        "intelligence_rating": 2,
        "speed_rating": 5,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-lite",
    },
)

IMAGE_MODEL_OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "value": "gemini-3.1-flash-image",
        "label": "Gemini 3.1 Flash Image (Nano Banana 2)",
        "description": (
            "Nano Banana 2 provides high-quality image generation and "
            "conversational editing at a mainstream price point and low latency."
        ),
        "cost_rating": 3,
        "quality_rating": 4,
        "speed_rating": 4,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-image",
    },
    {
        "value": "gemini-3.1-flash-lite-image",
        "label": "Gemini 3.1 Flash-Lite Image (Nano Banana 2 Lite)",
        "description": (
            "Nano Banana 2 Lite is the efficiency specialist, offering ultra-low "
            "latency and cost-effective image generation and editing."
        ),
        "cost_rating": 1,
        "quality_rating": 3,
        "speed_rating": 5,
        "url": (
            "https://ai.google.dev/gemini-api/docs/models/"
            "gemini-3.1-flash-lite-image"
        ),
    },
    {
        "value": "gemini-3-pro-image",
        "label": "Gemini 3 Pro Image (Nano Banana Pro)",
        "description": (
            "Nano Banana Pro is a sophisticated reasoning-driven engine for "
            "professional-grade image editing and generation."
        ),
        "cost_rating": 5,
        "quality_rating": 5,
        "speed_rating": 2,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-3-pro-image",
    },
    {
        "value": "gemini-2.5-flash-image",
        "label": "Gemini 2.5 Flash Image (Nano Banana)",
        "description": (
            "Our best engine for high-velocity visual creation, offering "
            "state-of-the-art speed and efficiency."
        ),
        "cost_rating": 2,
        "quality_rating": 3,
        "speed_rating": 4,
        "url": "https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-image",
    },
)

KNOWN_TEXT_MODELS = frozenset(option["value"] for option in TEXT_MODEL_OPTIONS)
KNOWN_IMAGE_MODELS = frozenset(option["value"] for option in IMAGE_MODEL_OPTIONS)

_TEXT_MODEL_BY_VALUE = {option["value"]: option for option in TEXT_MODEL_OPTIONS}
_IMAGE_MODEL_BY_VALUE = {option["value"]: option for option in IMAGE_MODEL_OPTIONS}


def normalize_text_model(value: Any) -> str:
    """Returns an approved GA text-output model id."""

    model = _normalize_model_id(value)
    return model if model in KNOWN_TEXT_MODELS else DEFAULT_TEXT_MODEL


def normalize_image_model(value: Any) -> str:
    """Returns an approved GA image-output model id."""

    model = _normalize_model_id(value)
    return model if model in KNOWN_IMAGE_MODELS else DEFAULT_IMAGE_MODEL


def text_model_metadata(value: Any) -> Mapping[str, Any]:
    """Returns display metadata for one normalized text model."""

    return _TEXT_MODEL_BY_VALUE[normalize_text_model(value)]


def image_model_metadata(value: Any) -> Mapping[str, Any]:
    """Returns display metadata for one normalized image model."""

    return _IMAGE_MODEL_BY_VALUE[normalize_image_model(value)]


def thinking_config_for_text_model(model: Any, *, smarter: bool) -> dict[str, Any]:
    """Returns the supported thinking control for one GA text model.

    Gemini 2.5 accepts the legacy numeric budget, while Gemini 3 uses the level
    enum. Gemini 3.7 rejects ``minimal``, so its low-latency setting is ``low``.
    """

    clean_model = normalize_text_model(model)
    if clean_model.startswith("gemini-2.5"):
        return {"thinking_budget": 24576 if smarter else 1024}
    return {
        "thinking_level": (
            "high"
            if smarter
            else "low"
            if clean_model == "gemini-3.7-flash"
            else "minimal"
        )
    }


def normalize_image_preferences(value: Any) -> dict[str, Any]:
    """Normalizes the new-game image-generation choices."""

    raw = value if isinstance(value, Mapping) else {}
    return {
        "enabled": _bool_value(raw.get("enabled"), True),
        "model": normalize_image_model(raw.get("model")),
        "style": normalize_image_style(raw.get("style", DEFAULT_IMAGE_STYLE)),
    }


def _normalize_model_id(value: Any) -> str:
    model = str(value or "").strip().casefold()
    if model.startswith("models/"):
        model = model.removeprefix("models/")
    return model


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    return default
