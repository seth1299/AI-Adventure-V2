from __future__ import annotations

import os


DISABLE_TTS_ENV = "AI_ADVENTURE_DISABLE_TTS"
DISABLE_AI_ENV = "AI_ADVENTURE_DISABLE_AI"
LIGHTWEIGHT_BUILD_ENV = "AI_ADVENTURE_LIGHTWEIGHT_BUILD"
PLAYTESTING_BUILD_ENV = "AI_ADVENTURE_PLAYTESTING_BUILD"


def is_tts_enabled() -> bool:
    """Returns whether local text-to-speech features should be available."""

    return not (
        _truthy_env(os.getenv(DISABLE_TTS_ENV))
        or _truthy_env(os.getenv(LIGHTWEIGHT_BUILD_ENV))
        or is_playtesting_build()
    )


def is_ai_enabled() -> bool:
    """Returns whether Gemini-backed generation should be available."""

    return not (
        _truthy_env(os.getenv(DISABLE_AI_ENV))
        or is_playtesting_build()
    )


def is_playtesting_build() -> bool:
    """Returns whether the manual, AI-free playtesting flavor is active."""

    return _truthy_env(os.getenv(PLAYTESTING_BUILD_ENV))


def _truthy_env(value: str | None) -> bool:
    """Reads common truthy environment variable values."""

    if value is None:
        return False

    return value.strip().casefold() in {"1", "true", "yes", "on"}
