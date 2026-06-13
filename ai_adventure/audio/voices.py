from __future__ import annotations

from typing import Any


DEFAULT_NARRATOR_VOICE = "af_sarah"
NARRATOR_SAMPLE_TEXT = (
    "The narrator is ready. This is a sample of the selected voice."
)
KOKORO_VOICES: dict[str, str] = {
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


def available_narrator_voices() -> dict[str, str]:
    """Returns display-name-to-engine voice mappings."""

    return dict(KOKORO_VOICES)


def normalize_narrator_voice(value: Any) -> str:
    """Returns a supported narrator voice id."""

    clean_value = str(value or "").strip()
    supported_voice_ids = set(KOKORO_VOICES.values())

    if clean_value in supported_voice_ids:
        return clean_value

    return DEFAULT_NARRATOR_VOICE
