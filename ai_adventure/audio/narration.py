from __future__ import annotations

import logging
import queue
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ai_adventure.audio.ssmd import (
    apply_ssmd_say_as_tags,
    apply_structural_pause_markers,
    normalize_tts_time_text as _normalize_plain_tts_time_text,
)
from ai_adventure.audio.tts_settings import (
    normalize_narrator_voice_spec,
    tts_speed_multiplier,
)
from ai_adventure.audio.voices import (
    DEFAULT_NARRATOR_VOICE,
    NARRATOR_SAMPLE_TEXT,
)
from ai_adventure.text_sanitization import sanitize_english_text


LOGGER = logging.getLogger(__name__)

TTS_CHANNEL_INDEX = 1
MAX_CHUNK_LENGTH = 900
PLAYBACK_POLL_INTERVAL_MS = 10


@dataclass(frozen=True)
class TTSRequest:
    """A single text-to-speech synthesis request."""

    text: str
    voice: str
    speed: float = 1.0
    language: str = "en-us"


class TTSManagerProtocol(Protocol):
    """Runtime surface NarrationPlayer needs from a TTS manager."""

    @property
    def is_available(self) -> bool:
        """Returns True when a TTS engine is ready."""
        ...

    def synthesize_to_file(self, request: TTSRequest) -> Path | None:
        """Synthesizes speech using the active engine, if available."""
        ...

    def get_default_voice(self) -> str:
        """Returns the active engine's default voice."""
        ...

    def get_available_voices(self) -> dict[str, str]:
        """Returns display-name-to-engine voice mappings."""
        ...


@dataclass(frozen=True)
class NarrationChunk:
    """Paired display and TTS text for one narration segment."""

    display_text: str
    tts_text: str
    voice_id: str = ""
    sound_effects_before: tuple[str, ...] = ()
    sound_effects_after: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedNarrationChunk:
    """A generated audio file and the display text it speaks."""

    audio_path: Path
    display_text: str
    sound_effects_before: tuple[str, ...] = ()
    sound_effects_after: tuple[str, ...] = ()


class NarrationPlayer:
    """Generates and plays text-to-speech narration in queued chunks."""

    def __init__(self, tts_manager: TTSManagerProtocol) -> None:
        self.tts_manager = tts_manager
        self.enabled = True
        self.volume = 0.9
        self.voice = normalize_narrator_voice_spec(tts_manager.get_default_voice())
        self.speed = 1.0
        self._pygame: Any = None
        self._initialized = False
        self._session_id = 0
        self._state_lock = threading.Lock()
        self._generation_lock = threading.Lock()

        self._initialize_audio()

    def _initialize_audio(self) -> None:
        """Initializes pygame audio for narration playback."""

        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init()

            self._pygame = pygame
            self._initialized = True
        except Exception as error:
            self._pygame = None
            self._initialized = False
            LOGGER.warning("Narrator playback is unavailable: %s", error)

    def set_enabled(self, enabled: bool) -> None:
        """Enables or disables narration."""

        self.enabled = bool(enabled)

        if not self.enabled:
            self.stop()

    def set_volume(self, volume: float | int | None) -> None:
        """Sets TTS playback volume as either 0.0-1.0 or 0-100."""

        if volume is None:
            return

        try:
            parsed_volume = float(volume)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid TTS volume value: %r", volume)
            return

        if parsed_volume > 1.0:
            parsed_volume = parsed_volume / 100.0

        self.volume = max(0.0, min(1.0, parsed_volume))

        if self._initialized and self._pygame is not None:
            try:
                self._pygame.mixer.Channel(TTS_CHANNEL_INDEX).set_volume(self.volume)
            except Exception as error:
                LOGGER.warning("Failed to update active narrator volume: %s", error)

    def set_voice(self, voice: str | None) -> None:
        """Sets the active narrator voice."""

        self.voice = normalize_narrator_voice_spec(voice or self.get_default_voice())

    def set_speed(self, speed: float | int | None) -> None:
        """Sets the TTS generation speed."""

        if speed is None:
            return

        try:
            parsed_speed = float(speed)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid TTS speed value: %r", speed)
            return

        if parsed_speed > 2.0:
            parsed_speed = tts_speed_multiplier(parsed_speed)

        self.speed = max(0.5, min(2.0, parsed_speed))

    def get_default_voice(self) -> str:
        """Returns the active engine's default voice."""

        return normalize_narrator_voice_spec(self.tts_manager.get_default_voice())

    def get_available_voices(self) -> dict[str, str]:
        """Returns display-name-to-engine voice mappings."""

        try:
            return self.tts_manager.get_available_voices()
        except Exception as error:
            LOGGER.warning("Failed to read available narrator voices: %s", error)
            return {}

    def play_sample(
        self,
        *,
        voice: str | None = None,
        volume: float | int | None = None,
        speed: float | int | None = None,
        text: str = NARRATOR_SAMPLE_TEXT,
        sound_effect_cues: list[dict[str, str]] | None = None,
        speaker_cues: list[dict[str, str]] | None = None,
        tts_text_transform: Callable[[str], str] | None = None,
        on_sound_effect: Callable[[str], None] | None = None,
    ) -> bool:
        """Plays local narration without contacting the AI."""

        previous_enabled = self.enabled

        if volume is not None:
            self.set_volume(volume)
        if speed is not None:
            self.set_speed(speed)

        self.set_enabled(True)

        def restore_enabled() -> None:
            if not previous_enabled:
                self.set_enabled(False)

        started = self.narrate(
            text,
            voice=voice,
            sound_effect_cues=sound_effect_cues,
            speaker_cues=speaker_cues,
            tts_text_transform=tts_text_transform,
            on_sound_effect=on_sound_effect,
            on_complete=restore_enabled,
        )

        if not started:
            restore_enabled()

        return started

    def narrate(
        self,
        text: str,
        *,
        voice: str | None = None,
        sound_effect_cues: list[dict[str, str]] | None = None,
        speaker_cues: list[dict[str, str]] | None = None,
        tts_text_transform: Callable[[str], str] | None = None,
        on_chunk_start: Callable[[str], None] | None = None,
        on_sound_effect: Callable[[str], None] | None = None,
        on_complete: Callable[[], None] | None = None,
    ) -> bool:
        """Starts narrating text in generated chunks."""

        if not self.enabled or not self.tts_manager.is_available:
            return False

        chunks = _merge_compatible_narration_chunks(
            build_narration_chunks(
                text,
                sound_effect_cues=sound_effect_cues,
                speaker_cues=speaker_cues,
                tts_text_transform=tts_text_transform,
            )
        )

        if not chunks:
            return False

        if not self._initialized or self._pygame is None:
            LOGGER.warning("Cannot narrate because audio playback is unavailable.")
            return False

        self.stop()

        with self._state_lock:
            self._session_id += 1
            session_id = self._session_id

        session_voice = normalize_narrator_voice_spec(
            voice or self.voice or DEFAULT_NARRATOR_VOICE
        )
        audio_queue: queue.Queue[GeneratedNarrationChunk | None] = queue.Queue(
            maxsize=max(2, len(chunks))
        )
        producer = threading.Thread(
            target=self._produce_chunks,
            args=(session_id, chunks, audio_queue, session_voice),
            daemon=True,
        )
        consumer = threading.Thread(
            target=self._play_chunks,
            args=(
                session_id,
                audio_queue,
                on_chunk_start,
                on_sound_effect,
                on_complete,
            ),
            daemon=True,
        )
        producer.start()
        consumer.start()
        return True

    def stop(self) -> None:
        """Stops active narration and invalidates pending generated chunks."""

        with self._state_lock:
            self._session_id += 1

        if self._initialized and self._pygame is not None:
            try:
                self._pygame.mixer.Channel(TTS_CHANNEL_INDEX).stop()
            except Exception as error:
                LOGGER.warning("Failed to stop narrator playback: %s", error)

    def _produce_chunks(
        self,
        session_id: int,
        chunks: list[NarrationChunk],
        audio_queue: queue.Queue[GeneratedNarrationChunk | None],
        voice: str,
    ) -> None:
        """Generates audio files while earlier chunks are being played."""

        try:
            for chunk in chunks:
                if not self._is_active_session(session_id):
                    return

                with self._generation_lock:
                    if not self._is_active_session(session_id):
                        return

                    audio_path = self.tts_manager.synthesize_to_file(
                        TTSRequest(
                            text=chunk.tts_text,
                            voice=normalize_narrator_voice_spec(
                                chunk.voice_id or voice
                            ),
                            speed=self.speed,
                        )
                    )

                if audio_path is None:
                    continue

                queue_item = GeneratedNarrationChunk(
                    audio_path=audio_path,
                    display_text=chunk.display_text,
                    sound_effects_before=chunk.sound_effects_before,
                    sound_effects_after=chunk.sound_effects_after,
                )

                if not self._put_queue_item(session_id, audio_queue, queue_item):
                    _delete_file(audio_path)
                    return
        finally:
            self._put_queue_item(session_id, audio_queue, None)

    def _play_chunks(
        self,
        session_id: int,
        audio_queue: queue.Queue[GeneratedNarrationChunk | None],
        on_chunk_start: Callable[[str], None] | None,
        on_sound_effect: Callable[[str], None] | None,
        on_complete: Callable[[], None] | None,
    ) -> None:
        """Plays generated chunks in order."""

        while self._is_active_session(session_id):
            try:
                queue_item = audio_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            if queue_item is None:
                if self._is_active_session(session_id) and on_complete is not None:
                    on_complete()
                return

            try:
                if on_sound_effect is not None:
                    for filename in queue_item.sound_effects_before:
                        on_sound_effect(filename)
                if on_chunk_start is not None:
                    on_chunk_start(queue_item.display_text)
                self._play_file_blocking(queue_item.audio_path, session_id)
                if on_sound_effect is not None:
                    for filename in queue_item.sound_effects_after:
                        on_sound_effect(filename)
            finally:
                _delete_file(queue_item.audio_path)

    def _play_file_blocking(self, audio_path: Path, session_id: int) -> None:
        """Plays one generated narration file and waits for it to finish."""

        if self._pygame is None:
            return

        try:
            sound = self._pygame.mixer.Sound(str(audio_path))
            channel = self._pygame.mixer.Channel(TTS_CHANNEL_INDEX)
            channel.set_volume(self.volume)
            channel.play(sound)

            while self._is_active_session(session_id) and channel.get_busy():
                self._pygame.time.wait(PLAYBACK_POLL_INTERVAL_MS)
        except Exception as error:
            LOGGER.warning("Failed to play narrator chunk %s: %s", audio_path, error)

    def _is_active_session(self, session_id: int) -> bool:
        """Returns True when the session is still current and enabled."""

        with self._state_lock:
            return self.enabled and session_id == self._session_id

    def _put_queue_item(
        self,
        session_id: int,
        audio_queue: queue.Queue[GeneratedNarrationChunk | None],
        item: GeneratedNarrationChunk | None,
    ) -> bool:
        """Puts a queue item while allowing disabled sessions to exit."""

        while self._is_active_session(session_id):
            try:
                audio_queue.put(item, timeout=0.25)
                return True
            except queue.Full:
                continue

        return False


def sanitize_tts_text(text: str) -> str:
    """Removes UI-only text and prepares SSMD text for TTS synthesis."""

    clean_text = sanitize_narration_display_text(text)
    return prepare_ssmd_tts_text(clean_text)


def prepare_ssmd_tts_text(text: str) -> str:
    """Adds supported SSMD normalization and pause markers."""

    clean_text = str(text or "").strip()

    if not clean_text:
        return ""

    return apply_ssmd_say_as_tags(apply_structural_pause_markers(clean_text)).strip()


def sanitize_narration_display_text(text: str) -> str:
    """Returns visible prose that should be revealed while narration plays."""

    marker_pattern = r"\[" r"\[[^\]]+\]" r"\]"
    clean_text = re.sub(marker_pattern, " ", sanitize_english_text(text))
    clean_text = re.sub(r"`([^`]+)`", r"\1", clean_text)
    clean_text = clean_text.replace("*", "")
    clean_text = clean_text.replace("_", "")

    lines = [line.rstrip() for line in clean_text.splitlines()]

    while lines and not lines[-1].strip():
        lines.pop()

    while lines and lines[-1].strip().startswith("- "):
        lines.pop()

        while lines and not lines[-1].strip():
            lines.pop()

    clean_text = "\n".join(lines)
    clean_text = re.sub(r"^\s{0,3}#{1,6}\s*", "", clean_text, flags=re.MULTILINE)
    paragraphs = [
        re.sub(r"[ \t]+", " ", paragraph.strip())
        for paragraph in re.split(r"\n\s*\n+", clean_text)
        if paragraph.strip()
    ]
    return "\n\n".join(paragraphs).strip()


def build_narration_chunks(
    text: str,
    *,
    max_length: int = MAX_CHUNK_LENGTH,
    sound_effect_cues: list[dict[str, str]] | None = None,
    speaker_cues: list[dict[str, str]] | None = None,
    tts_text_transform: Callable[[str], str] | None = None,
) -> list[NarrationChunk]:
    """Builds display/TTS chunks with exact effect and speaker boundaries."""

    clean_text = sanitize_narration_display_text(text)
    if not clean_text:
        return []

    before_at: dict[int, list[str]] = {}
    after_at: dict[int, list[str]] = {}
    speaker_ranges: list[tuple[int, int, str]] = []
    forced_offsets = {0, len(clean_text)}

    for raw_cue in sound_effect_cues or []:
        if not isinstance(raw_cue, dict):
            continue
        filename = str(raw_cue.get("filename", "") or "").strip()
        anchor_text = sanitize_narration_display_text(
            str(raw_cue.get("anchor_text", "") or "")
        )
        position = str(raw_cue.get("position", "after") or "after").casefold()
        if not filename or not anchor_text or position not in {"before", "after"}:
            continue
        if clean_text.count(anchor_text) != 1:
            LOGGER.warning(
                "Skipped narration sound cue %r because its anchor is not unique.",
                filename,
            )
            continue
        anchor_start = clean_text.index(anchor_text)
        boundary = (
            anchor_start if position == "before" else anchor_start + len(anchor_text)
        )
        forced_offsets.add(boundary)
        target = before_at if position == "before" else after_at
        target.setdefault(boundary, []).append(filename)

    for raw_cue in speaker_cues or []:
        if not isinstance(raw_cue, dict):
            continue
        anchor_text = sanitize_narration_display_text(
            str(raw_cue.get("anchor_text", "") or "")
        )
        voice_id = str(raw_cue.get("voice_id", "") or "").strip()
        if not anchor_text or not voice_id or clean_text.count(anchor_text) != 1:
            LOGGER.warning(
                "Skipped narration speaker cue because its anchor is not unique "
                "or its voice ID is missing."
            )
            continue
        start = clean_text.index(anchor_text)
        end = start + len(anchor_text)
        if any(start < existing_end and end > existing_start for existing_start, existing_end, _ in speaker_ranges):
            LOGGER.warning("Skipped overlapping narration speaker cue %r.", anchor_text)
            continue
        speaker_ranges.append((start, end, voice_id))
        forced_offsets.update({start, end})

    ranges = _narration_chunk_ranges(
        clean_text,
        forced_offsets=forced_offsets,
        max_length=max_length,
    )
    chunks: list[NarrationChunk] = []
    for start, end in ranges:
        display_text = clean_text[start:end]
        spoken_text = display_text.strip()
        if not spoken_text:
            if chunks:
                previous = chunks[-1]
                chunks[-1] = NarrationChunk(
                    display_text=previous.display_text + display_text,
                    tts_text=previous.tts_text,
                    voice_id=previous.voice_id,
                    sound_effects_before=previous.sound_effects_before,
                    sound_effects_after=previous.sound_effects_after,
                )
            continue
        chunks.append(
            NarrationChunk(
                display_text=display_text,
                tts_text=prepare_ssmd_tts_text(
                    tts_text_transform(spoken_text)
                    if tts_text_transform is not None
                    else spoken_text
                ),
                voice_id=next(
                    (
                        voice_id
                        for speaker_start, speaker_end, voice_id in speaker_ranges
                        if start >= speaker_start and end <= speaker_end
                    ),
                    "",
                ),
                sound_effects_before=tuple(before_at.get(start, ())),
                sound_effects_after=tuple(after_at.get(end, ())),
            )
        )

    if chunks:
        if before_at.get(len(clean_text)):
            last = chunks[-1]
            chunks[-1] = NarrationChunk(
                display_text=last.display_text,
                tts_text=last.tts_text,
                voice_id=last.voice_id,
                sound_effects_before=last.sound_effects_before,
                sound_effects_after=(
                    last.sound_effects_after + tuple(before_at[len(clean_text)])
                ),
            )
        if after_at.get(0):
            first = chunks[0]
            chunks[0] = NarrationChunk(
                display_text=first.display_text,
                tts_text=first.tts_text,
                voice_id=first.voice_id,
                sound_effects_before=tuple(after_at[0]) + first.sound_effects_before,
                sound_effects_after=first.sound_effects_after,
            )
    return chunks


def _narration_chunk_ranges(
    text: str,
    *,
    forced_offsets: set[int],
    max_length: int,
) -> list[tuple[int, int]]:
    """Returns ordered chunk ranges while preserving every display character."""

    boundaries = set(forced_offsets)
    boundaries.update(match.end() for match in re.finditer(r"\n\s*\n+", text))
    ordered_boundaries = sorted(
        offset for offset in boundaries if 0 <= offset <= len(text)
    )
    ranges: list[tuple[int, int]] = []

    for boundary_index in range(len(ordered_boundaries) - 1):
        start = ordered_boundaries[boundary_index]
        region_end = ordered_boundaries[boundary_index + 1]
        while region_end - start > max_length:
            search_end = start + max_length
            comma_at = text.rfind(",", start, search_end)
            space_at = text.rfind(" ", start, search_end)
            if comma_at > start:
                cut = comma_at + 1
            elif space_at > start:
                cut = space_at
            else:
                cut = search_end
            ranges.append((start, cut))
            start = cut
        if start < region_end:
            ranges.append((start, region_end))

    return ranges


def chunk_tts_text(text: str, *, max_length: int = MAX_CHUNK_LENGTH) -> list[str]:
    """Splits sanitized text into small ordered narration chunks."""

    clean_text = str(text or "").strip()

    if not clean_text:
        return []

    blocks = re.split(r"\n\s*\n+", clean_text)
    chunks: list[str] = []

    for block in blocks:
        block = block.strip()

        if not block:
            continue

        if len(block) > max_length:
            chunks.extend(_split_long_text(block, max_length=max_length))
            continue

        chunks.append(block)

    return chunks


def normalize_tts_time_text(text: str) -> str:
    """Converts clock-style times into text that TTS engines pronounce naturally."""

    return _normalize_plain_tts_time_text(text)


def _split_long_text(text: str, *, max_length: int) -> list[str]:
    """Splits long text on commas or spaces."""

    chunks: list[str] = []
    remaining = text.strip()

    while len(remaining) > max_length:
        split_at = max(
            remaining.rfind(",", 0, max_length),
            remaining.rfind(" ", 0, max_length),
        )

        if split_at <= 0:
            split_at = max_length

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].lstrip(" ,")

    if remaining:
        chunks.append(remaining)

    return chunks


def _delete_file(path: str | Path | None) -> None:
    """Deletes a generated narration file if possible."""

    if path is None:
        return

    try:
        Path(path).unlink(missing_ok=True)
    except Exception as error:
        LOGGER.warning("Failed to delete generated narration file %s: %s", path, error)


def _merge_compatible_narration_chunks(
    chunks: list[NarrationChunk],
    *,
    max_length: int = MAX_CHUNK_LENGTH,
) -> list[NarrationChunk]:
    """Combines adjacent same-voice chunks when no cue needs the boundary.

    Paragraph boundaries are useful for display, but synthesizing every short
    paragraph as a separate file creates avoidable model and file-playback
    gaps.  Keep boundaries required by speaker or sound-effect cues, and keep
    the display text byte-for-byte intact when merging the others.
    """

    merged: list[NarrationChunk] = []
    for chunk in chunks:
        if not merged:
            merged.append(chunk)
            continue

        previous = merged[-1]
        can_merge = (
            previous.voice_id == chunk.voice_id
            and not previous.sound_effects_after
            and not chunk.sound_effects_before
            and len(previous.display_text) + len(chunk.display_text) <= max_length
        )
        if not can_merge:
            merged.append(chunk)
            continue

        merged[-1] = NarrationChunk(
            display_text=previous.display_text + chunk.display_text,
            tts_text=f"{previous.tts_text} ...p {chunk.tts_text}".strip(),
            voice_id=previous.voice_id,
            sound_effects_before=previous.sound_effects_before,
            sound_effects_after=chunk.sound_effects_after,
        )

    return merged
