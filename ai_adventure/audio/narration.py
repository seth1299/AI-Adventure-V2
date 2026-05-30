from __future__ import annotations

import logging
import queue
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_adventure.audio.tts.tts_manager import TTSManager, TTSRequest


LOGGER = logging.getLogger(__name__)

TTS_CHANNEL_INDEX = 1
MAX_CHUNK_LENGTH = 260


@dataclass(frozen=True)
class NarrationChunk:
    """Paired display and TTS text for one narration segment."""

    display_text: str
    tts_text: str


@dataclass(frozen=True)
class GeneratedNarrationChunk:
    """A generated audio file and the display text it speaks."""

    audio_path: Path
    display_text: str


class NarrationPlayer:
    """Generates and plays text-to-speech narration in queued chunks."""

    def __init__(self, tts_manager: TTSManager) -> None:
        self.tts_manager = tts_manager
        self.enabled = True
        self.volume = 0.9
        self.voice = tts_manager.get_default_voice()
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

    def narrate(
        self,
        text: str,
        *,
        on_chunk_start: Callable[[str], None] | None = None,
        on_complete: Callable[[], None] | None = None,
    ) -> bool:
        """Starts narrating text in generated chunks."""

        if not self.enabled or not self.tts_manager.is_available:
            return False

        chunks = build_narration_chunks(text)

        if not chunks:
            return False

        if not self._initialized or self._pygame is None:
            LOGGER.warning("Cannot narrate because audio playback is unavailable.")
            return False

        self.stop()

        with self._state_lock:
            self._session_id += 1
            session_id = self._session_id

        audio_queue: queue.Queue[GeneratedNarrationChunk | None] = queue.Queue(maxsize=2)
        producer = threading.Thread(
            target=self._produce_chunks,
            args=(session_id, chunks, audio_queue),
            daemon=True,
        )
        consumer = threading.Thread(
            target=self._play_chunks,
            args=(session_id, audio_queue, on_chunk_start, on_complete),
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
                            voice=self.voice,
                            speed=self.speed,
                        )
                    )

                if audio_path is None:
                    continue

                queue_item = GeneratedNarrationChunk(
                    audio_path=audio_path,
                    display_text=chunk.display_text,
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
                if on_chunk_start is not None:
                    on_chunk_start(queue_item.display_text)
                self._play_file_blocking(queue_item.audio_path, session_id)
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
                self._pygame.time.wait(50)
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
    """Removes prompt artifacts and UI-only suggestions before TTS synthesis."""

    clean_text = sanitize_narration_display_text(text)
    return normalize_tts_time_text(clean_text).strip()


def sanitize_narration_display_text(text: str) -> str:
    """Returns visible prose that should be revealed while narration plays."""

    clean_text = re.sub(r"\[\[[^\]]+\]\]", " ", str(text or ""))
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
    clean_text = re.sub(r"\s+", " ", clean_text)
    return clean_text.strip()


def build_narration_chunks(
    text: str,
    *,
    max_length: int = MAX_CHUNK_LENGTH,
) -> list[NarrationChunk]:
    """Builds display/TTS chunk pairs from one story response."""

    display_chunks = chunk_tts_text(
        sanitize_narration_display_text(text),
        max_length=max_length,
    )

    return [
        NarrationChunk(
            display_text=display_chunk,
            tts_text=normalize_tts_time_text(display_chunk),
        )
        for display_chunk in display_chunks
    ]


def chunk_tts_text(text: str, *, max_length: int = MAX_CHUNK_LENGTH) -> list[str]:
    """Splits sanitized text into small ordered narration chunks."""

    clean_text = str(text or "").strip()

    if not clean_text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+|\n+", clean_text)
    chunks: list[str] = []

    for sentence in sentences:
        sentence = sentence.strip()

        if not sentence:
            continue

        if len(sentence) > max_length:
            chunks.extend(_split_long_text(sentence, max_length=max_length))
            continue

        chunks.append(sentence)

    return chunks


def normalize_tts_time_text(text: str) -> str:
    """Converts clock-style times into text that TTS engines pronounce naturally."""

    clean_text = str(text or "")
    clean_text = _TWELVE_HOUR_TIME_RE.sub(_replace_12_hour_time, clean_text)
    clean_text = _TWENTY_FOUR_HOUR_TIME_RE.sub(_replace_24_hour_time, clean_text)
    return clean_text


_TWELVE_HOUR_TIME_RE = re.compile(
    r"\b(1[0-2]|0?[1-9]):([0-5]\d)\s*([AaPp])\.?\s*[Mm]\.?(?=$|[^A-Za-z0-9_])"
)
_TWENTY_FOUR_HOUR_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_NUMBER_WORDS_0_TO_59 = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
}


def _replace_12_hour_time(match: re.Match[str]) -> str:
    """Returns spoken text for one 12-hour clock match."""

    hour = int(match.group(1))
    minute = int(match.group(2))
    suffix = match.group(3).lower()
    period = "morning" if suffix == "a" else "afternoon"

    if suffix == "p":
        if hour == 12:
            period = "afternoon"
        elif 5 <= hour <= 11:
            period = "evening"

    if suffix == "a" and hour == 12:
        period = "at night"
        return _spoken_time(hour, minute, period)

    return _spoken_time(hour, minute, f"in the {period}")


def _replace_24_hour_time(match: re.Match[str]) -> str:
    """Returns spoken text for one 24-hour clock match."""

    hour_24 = int(match.group(1))
    minute = int(match.group(2))
    display_hour = hour_24 % 12 or 12

    if 5 <= hour_24 < 12:
        period = "in the morning"
    elif 12 <= hour_24 < 17:
        period = "in the afternoon"
    elif 17 <= hour_24 < 22:
        period = "in the evening"
    else:
        period = "at night"

    return _spoken_time(display_hour, minute, period)


def _spoken_time(hour: int, minute: int, period: str) -> str:
    """Formats an already-parsed clock time for speech."""

    if hour == 12 and minute == 0 and period == "at night":
        return "midnight"

    if hour == 12 and minute == 0 and period == "in the afternoon":
        return "noon"

    hour_text = _number_word(hour)

    if minute == 0:
        return f"{hour_text} {period}"

    if minute < 10:
        return f"{hour_text} oh {_number_word(minute)} {period}"

    return f"{hour_text} {_number_word(minute)} {period}"


def _number_word(number: int) -> str:
    """Returns a plain English word for numbers from 0 through 59."""

    if number in _NUMBER_WORDS_0_TO_59:
        return _NUMBER_WORDS_0_TO_59[number]

    tens = number - (number % 10)
    ones = number % 10
    return f"{_NUMBER_WORDS_0_TO_59[tens]} {_NUMBER_WORDS_0_TO_59[ones]}"


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
