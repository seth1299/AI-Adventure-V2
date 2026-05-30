from __future__ import annotations

import logging
import queue
import re
import threading
from pathlib import Path
from typing import Any

from ai_adventure.audio.tts.tts_manager import TTSManager, TTSRequest


LOGGER = logging.getLogger(__name__)

TTS_CHANNEL_INDEX = 1
MAX_CHUNK_LENGTH = 420


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

    def narrate(self, text: str) -> None:
        """Starts narrating text in generated chunks."""

        if not self.enabled or not self.tts_manager.is_available:
            return

        clean_text = sanitize_tts_text(text)
        chunks = chunk_tts_text(clean_text)

        if not chunks:
            return

        if not self._initialized or self._pygame is None:
            LOGGER.warning("Cannot narrate because audio playback is unavailable.")
            return

        self.stop()

        with self._state_lock:
            self._session_id += 1
            session_id = self._session_id

        audio_queue: queue.Queue[Path | None] = queue.Queue(maxsize=2)
        producer = threading.Thread(
            target=self._produce_chunks,
            args=(session_id, chunks, audio_queue),
            daemon=True,
        )
        consumer = threading.Thread(
            target=self._play_chunks,
            args=(session_id, audio_queue),
            daemon=True,
        )
        producer.start()
        consumer.start()

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
        chunks: list[str],
        audio_queue: queue.Queue[Path | None],
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
                            text=chunk,
                            voice=self.voice,
                            speed=self.speed,
                        )
                    )

                if audio_path is None:
                    continue

                if not self._put_queue_item(session_id, audio_queue, audio_path):
                    _delete_file(audio_path)
                    return
        finally:
            self._put_queue_item(session_id, audio_queue, None)

    def _play_chunks(
        self,
        session_id: int,
        audio_queue: queue.Queue[Path | None],
    ) -> None:
        """Plays generated chunks in order."""

        while self._is_active_session(session_id):
            try:
                audio_path = audio_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            if audio_path is None:
                return

            try:
                self._play_file_blocking(audio_path, session_id)
            finally:
                _delete_file(audio_path)

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
        audio_queue: queue.Queue[Path | None],
        item: Path | None,
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


def chunk_tts_text(text: str, *, max_length: int = MAX_CHUNK_LENGTH) -> list[str]:
    """Splits sanitized text into narration chunks."""

    clean_text = str(text or "").strip()

    if not clean_text:
        return []

    sentences = re.split(r"(?<=[.!?])\s+", clean_text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()

        if not sentence:
            continue

        if len(sentence) > max_length:
            if current:
                chunks.append(current)
                current = ""

            chunks.extend(_split_long_text(sentence, max_length=max_length))
            continue

        proposed = f"{current} {sentence}".strip()

        if len(proposed) <= max_length:
            current = proposed
        else:
            if current:
                chunks.append(current)
            current = sentence

    if current:
        chunks.append(current)

    return chunks


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
