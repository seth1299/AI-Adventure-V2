from __future__ import annotations

from typing import Any


def split_story_bubble_segments(
    text: str,
    *,
    sound_effect_cues: Any = None,
    speaker_cues: Any = None,
) -> list[dict[str, Any]]:
    """Splits one saved story turn into narrator and character bubble passages."""

    source_text = str(text or "")
    if not source_text.strip():
        return []

    speaker_ranges: list[tuple[int, int, dict[str, str]]] = []
    for raw_cue in speaker_cues if isinstance(speaker_cues, list) else []:
        if not isinstance(raw_cue, dict):
            continue
        anchor_text = str(raw_cue.get("anchor_text", "") or "").strip()
        speaker_name = str(raw_cue.get("speaker_name", "") or "").strip()
        if (
            not anchor_text
            or not speaker_name
            or source_text.count(anchor_text) != 1
        ):
            continue
        start = source_text.index(anchor_text)
        end = start + len(anchor_text)
        if any(
            start < old_end and end > old_start
            for old_start, old_end, _old_cue in speaker_ranges
        ):
            continue
        speaker_ranges.append(
            (
                start,
                end,
                {
                    str(key): str(value)
                    for key, value in raw_cue.items()
                    if isinstance(key, str)
                },
            )
        )
    speaker_ranges.sort(key=lambda item: item[0])

    clean_sound_cues = [
        cue
        for cue in (sound_effect_cues if isinstance(sound_effect_cues, list) else [])
        if isinstance(cue, dict)
    ]
    segments: list[dict[str, Any]] = []

    def append_segment(
        start: int,
        end: int,
        speaker_cue: dict[str, str] | None = None,
    ) -> None:
        raw_segment = source_text[start:end]
        leading_whitespace = len(raw_segment) - len(raw_segment.lstrip())
        trailing_whitespace = len(raw_segment) - len(raw_segment.rstrip())
        clean_start = start + leading_whitespace
        clean_end = end - trailing_whitespace
        if clean_end <= clean_start:
            return
        content = source_text[clean_start:clean_end]
        segment_sound_cues: list[dict[str, str]] = []
        for raw_sound_cue in clean_sound_cues:
            anchor_text = str(raw_sound_cue.get("anchor_text", "") or "").strip()
            if not anchor_text or source_text.count(anchor_text) != 1:
                continue
            cue_start = source_text.index(anchor_text)
            cue_end = cue_start + len(anchor_text)
            if cue_start >= clean_start and cue_end <= clean_end:
                segment_sound_cues.append(dict(raw_sound_cue))
        segments.append(
            {
                "content": content,
                "speaker_name": (
                    str(speaker_cue.get("speaker_name", "") or "").strip()
                    if speaker_cue is not None
                    else ""
                ),
                "sound_effect_cues": segment_sound_cues,
                "speaker_cues": [dict(speaker_cue)] if speaker_cue is not None else [],
            }
        )

    cursor = 0
    for start, end, cue in speaker_ranges:
        append_segment(cursor, start)
        append_segment(start, end, cue)
        cursor = end
    append_segment(cursor, len(source_text))

    if not segments:
        append_segment(0, len(source_text))
    return segments
