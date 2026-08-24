from __future__ import annotations

import hashlib
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

VOICE_PROFILE_OPTIONS: tuple[str, ...] = (
    "feminine",
    "masculine",
    "deep_feminine",
    "deep_masculine",
    "neutral",
)

VOICE_IDS_BY_PROFILE: dict[str, tuple[str, ...]] = {
    "feminine": (
        "af_heart", "af_bella", "af_jessica", "af_kore", "af_nova",
        "af_sky", "bf_alice", "bf_emma", "bf_lily", "af_sarah",
    ),
    "masculine": (
        "am_michael", "am_eric", "am_liam", "bm_daniel", "bm_lewis",
        "am_puck", "am_echo", "bm_fable",
    ),
    "deep_feminine": (
        "af_nicole", "af_kore", "bf_isabella", "af_bella", "bf_alice",
    ),
    "deep_masculine": (
        "am_onyx", "am_fenrir", "am_adam", "bm_george", "am_michael",
    ),
    "neutral": (
        "af_river", "af_alloy", "am_echo", "bm_fable", "af_aoede",
        "am_puck",
    ),
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


def assign_speaker_voices(
    speaker_cues: Any,
    *,
    narrator_voice: str,
    available_voice_ids: Any,
    existing_assignments: Any = None,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Resolves durable, distinct installed voices for anchored speakers."""

    supported_voice_ids = set(KOKORO_VOICES.values())
    available = list(
        dict.fromkeys(
            str(voice_id or "").strip()
            for voice_id in (
                available_voice_ids
                if isinstance(available_voice_ids, (list, tuple, set))
                else []
            )
            if str(voice_id or "").strip() in supported_voice_ids
        )
    )
    if not available:
        available = list(KOKORO_VOICES.values())

    assignments = {
        str(speaker_id or "").strip().casefold(): str(voice_id or "").strip()
        for speaker_id, voice_id in (
            existing_assignments.items()
            if isinstance(existing_assignments, dict)
            else []
        )
        if str(speaker_id or "").strip()
        and str(voice_id or "").strip() in available
    }
    used_voice_ids = set(assignments.values())
    clean_narrator_voice = str(narrator_voice or "").strip()
    resolved: list[dict[str, str]] = []

    for raw_cue in speaker_cues if isinstance(speaker_cues, list) else []:
        if not isinstance(raw_cue, dict):
            continue
        anchor_text = str(raw_cue.get("anchor_text", "") or "").strip()
        speaker_id = str(raw_cue.get("speaker_id", "") or "").strip().casefold()
        speaker_name = str(raw_cue.get("speaker_name", "") or "").strip()
        voice_profile = str(
            raw_cue.get("voice_profile", "neutral") or "neutral"
        ).strip().casefold()
        if voice_profile not in VOICE_PROFILE_OPTIONS:
            voice_profile = "neutral"
        if not anchor_text or not speaker_id:
            continue

        voice_id = assignments.get(speaker_id, "")
        if not voice_id:
            profile_candidates = [
                candidate
                for candidate in VOICE_IDS_BY_PROFILE[voice_profile]
                if candidate in available
            ]
            candidates = _preferred_unused_voices(
                profile_candidates,
                used_voice_ids=used_voice_ids,
                narrator_voice=clean_narrator_voice,
            )
            if not candidates:
                candidates = _preferred_unused_voices(
                    available,
                    used_voice_ids=used_voice_ids,
                    narrator_voice=clean_narrator_voice,
                )
            if not candidates:
                candidates = profile_candidates or available
            voice_id = _stable_voice_choice(speaker_id, candidates)
            assignments[speaker_id] = voice_id
            used_voice_ids.add(voice_id)

        resolved.append(
            {
                "anchor_text": anchor_text,
                "speaker_id": speaker_id,
                "speaker_name": speaker_name or speaker_id,
                "voice_profile": voice_profile,
                "voice_id": voice_id,
            }
        )

    return resolved, assignments


def _preferred_unused_voices(
    candidates: list[str],
    *,
    used_voice_ids: set[str],
    narrator_voice: str,
) -> list[str]:
    """Prefers voices unused by the narrator or another durable speaker."""

    distinct = [
        voice_id
        for voice_id in candidates
        if voice_id != narrator_voice and voice_id not in used_voice_ids
    ]
    if distinct:
        return distinct
    return [voice_id for voice_id in candidates if voice_id != narrator_voice]


def _stable_voice_choice(speaker_id: str, candidates: list[str]) -> str:
    """Chooses deterministically so assignment is stable before persistence."""

    if not candidates:
        return DEFAULT_NARRATOR_VOICE
    digest = hashlib.sha256(speaker_id.encode("utf-8")).digest()
    return candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
