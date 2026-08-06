from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav"}
SOUND_EFFECT_CHANNEL_INDEX = 2


def prepare_sound_directory(app_paths: Any) -> Path:
    """
    Finds or prepares the playable sound directory.

    Preference order:
    - App-managed sounds copied/extracted into AppData.
    - V2 packaged audio/music_tracks folder.
    - V2 source-tree sounds folder.
    - Legacy sibling app sounds folder.
    - Legacy sibling app sounds.zip extracted into AppData.
    """

    managed_sounds_dir = Path(app_paths.sounds_dir)
    managed_sounds_dir.mkdir(parents=True, exist_ok=True)

    if _contains_audio_files(managed_sounds_dir):
        return managed_sounds_dir

    package_music_tracks_dir = Path(app_paths.package_music_tracks_dir)
    if _contains_audio_files(package_music_tracks_dir):
        return package_music_tracks_dir

    repo_sounds_dir = Path(app_paths.repo_root) / "sounds"
    if _contains_audio_files(repo_sounds_dir):
        return repo_sounds_dir

    legacy_sounds_dir = Path(app_paths.legacy_app_dir) / "sounds"
    if _contains_audio_files(legacy_sounds_dir):
        return legacy_sounds_dir

    legacy_zip = Path(app_paths.legacy_app_dir) / "sounds.zip"
    if legacy_zip.exists():
        _extract_sounds_zip(legacy_zip, managed_sounds_dir)

    return managed_sounds_dir


class SoundManager:
    """Manages independent looping music and ambient sound-effect tracks."""

    def __init__(self, sounds_directory: str | Path) -> None:
        self.sounds_directory = Path(sounds_directory).expanduser()
        self.current_music: str | None = None
        self.current_sound_effect: str | None = None
        self.music_volume: float = 0.25
        self.sound_effects_volume: float = 0.35
        self.music_enabled = True
        self.sound_effects_enabled = True
        self._initialized = False
        self._pygame: Any = None
        self._sound_effect: Any = None
        self._track_cache: dict[str, Path] = {}

        self._initialize_audio()
        self.refresh_tracks()

    @property
    def is_available(self) -> bool:
        """Returns True when the underlying audio backend is ready."""

        return self._initialized

    def _initialize_audio(self) -> None:
        """Initializes pygame audio without making audio a hard dependency."""

        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init()

            self._pygame = pygame
            self._initialized = True
        except Exception as error:
            self._pygame = None
            self._initialized = False
            LOGGER.warning("Background music is unavailable: %s", error)

    def refresh_tracks(self) -> None:
        """Refreshes the known playable track cache."""

        self._track_cache.clear()

        try:
            if not self.sounds_directory.exists() or not self.sounds_directory.is_dir():
                LOGGER.warning("Sound directory does not exist: %s", self.sounds_directory)
                return

            for file_path in self.sounds_directory.iterdir():
                if (
                    file_path.is_file()
                    and file_path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
                ):
                    self._track_cache[file_path.name.lower()] = file_path
        except Exception as error:
            LOGGER.warning("Failed to refresh sound files: %s", error)

    def get_valid_track_names(self) -> list[str]:
        """Returns known playable audio filenames."""

        self.refresh_tracks()
        return sorted(path.name for path in self._track_cache.values())

    def get_valid_sound_effect_names(self) -> list[str]:
        """Returns audio files that may play on the independent effect channel."""

        return self.get_valid_track_names()

    def set_music_enabled(self, enabled: bool) -> None:
        """Enables or disables looping music playback."""

        self.music_enabled = bool(enabled)

        if not self.music_enabled:
            self.stop_music(clear_current=False)

    def set_music_volume(self, volume: float | int | None) -> None:
        """Sets background music volume as either 0.0-1.0 or 0-100."""

        if volume is None:
            return

        try:
            parsed_volume = float(volume)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid music volume value: %r", volume)
            return

        if parsed_volume > 1.0:
            parsed_volume = parsed_volume / 100.0

        self.music_volume = max(0.0, min(1.0, parsed_volume))

        if self._initialized and self._pygame is not None:
            self._pygame.mixer.music.set_volume(self.music_volume)

    def play_music(self, track_name_or_path: str | Path | None) -> None:
        """Plays background music, replacing the currently playing track."""

        if not self.music_enabled:
            return

        if not self._initialized or self._pygame is None:
            LOGGER.warning("Cannot play music because the audio backend is unavailable.")
            return

        track_path = self._resolve_track_path(track_name_or_path)
        if track_path is None:
            return

        try:
            if (
                self.current_music == track_path.name
                and self._pygame.mixer.music.get_busy()
            ):
                return

            self._pygame.mixer.music.stop()
            self._pygame.mixer.music.load(str(track_path))
            self._pygame.mixer.music.set_volume(self.music_volume)
            self._pygame.mixer.music.play(-1)
            self.current_music = track_path.name
            LOGGER.info("Playing background music: %s", track_path.name)
        except Exception as error:
            LOGGER.warning("Failed to play background music %s: %s", track_path, error)

    def play_music_preview(self, track_name_or_path: str | Path | None) -> None:
        """Plays one track once for a short UI volume preview."""

        if not self._initialized or self._pygame is None:
            LOGGER.warning("Cannot play music preview because the audio backend is unavailable.")
            return

        track_path = self._resolve_track_path(track_name_or_path)
        if track_path is None:
            return

        try:
            self._pygame.mixer.music.stop()
            self._pygame.mixer.music.load(str(track_path))
            self._pygame.mixer.music.set_volume(self.music_volume)
            self._pygame.mixer.music.play(0)
            self.current_music = track_path.name
            LOGGER.info("Playing music preview: %s", track_path.name)
        except Exception as error:
            LOGGER.warning("Failed to play music preview %s: %s", track_path, error)

    def set_sound_effects_enabled(self, enabled: bool) -> None:
        """Enables or disables looping ambient sound effects."""

        self.sound_effects_enabled = bool(enabled)
        if not self.sound_effects_enabled:
            self.stop_sound_effect(clear_current=False)

    def set_sound_effects_volume(self, volume: float | int | None) -> None:
        """Sets ambient sound-effect volume as either 0.0-1.0 or 0-100."""

        parsed_volume = _normalized_volume(volume, label="sound-effect")
        if parsed_volume is None:
            return

        self.sound_effects_volume = parsed_volume
        if self._initialized and self._pygame is not None:
            try:
                self._pygame.mixer.Channel(SOUND_EFFECT_CHANNEL_INDEX).set_volume(
                    self.sound_effects_volume
                )
            except Exception as error:
                LOGGER.warning("Failed to update sound-effect volume: %s", error)

    def stop_music(self, *, clear_current: bool = True) -> None:
        """Stops currently playing background music."""

        if not self._initialized or self._pygame is None:
            return

        try:
            self._pygame.mixer.music.stop()
        except Exception as error:
            LOGGER.warning("Failed to stop music: %s", error)

        if clear_current:
            self.current_music = None

    def play_sound_effect(self, track_name_or_path: str | Path | None) -> None:
        """Loops one ambient effect without interrupting background music."""

        if not self.sound_effects_enabled:
            return
        if not self._initialized or self._pygame is None:
            LOGGER.warning("Cannot play a sound effect because audio is unavailable.")
            return

        track_path = self._resolve_track_path(track_name_or_path, label="Sound effect")
        if track_path is None:
            return

        try:
            channel = self._pygame.mixer.Channel(SOUND_EFFECT_CHANNEL_INDEX)
            if self.current_sound_effect == track_path.name and channel.get_busy():
                return

            channel.stop()
            self._sound_effect = self._pygame.mixer.Sound(str(track_path))
            channel.set_volume(self.sound_effects_volume)
            channel.play(self._sound_effect, loops=-1)
            self.current_sound_effect = track_path.name
            LOGGER.info("Playing ambient sound effect: %s", track_path.name)
        except Exception as error:
            LOGGER.warning("Failed to play sound effect %s: %s", track_path, error)

    def stop_sound_effect(self, *, clear_current: bool = True) -> None:
        """Stops the ambient effect channel without interrupting music."""

        if self._initialized and self._pygame is not None:
            try:
                self._pygame.mixer.Channel(SOUND_EFFECT_CHANNEL_INDEX).stop()
            except Exception as error:
                LOGGER.warning("Failed to stop sound effect: %s", error)

        self._sound_effect = None
        if clear_current:
            self.current_sound_effect = None

    def _resolve_track_path(
        self,
        track_name_or_path: str | Path | None,
        *,
        label: str = "Music track",
    ) -> Path | None:
        """Resolves either a filename or direct path to a playable audio file."""

        if not track_name_or_path:
            LOGGER.warning("No %s was provided.", label.casefold())
            return None

        raw_path = Path(str(track_name_or_path)).expanduser()

        if (
            raw_path.exists()
            and raw_path.is_file()
            and raw_path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        ):
            return raw_path

        self.refresh_tracks()
        cached_path = self._track_cache.get(raw_path.name.lower())

        if cached_path is None:
            LOGGER.warning("%s not found: %s", label, track_name_or_path)

        return cached_path


def _normalized_volume(value: float | int | None, *, label: str) -> float | None:
    """Normalizes a 0-1 or 0-100 volume value."""

    if value is None:
        return None
    try:
        parsed_volume = float(value)
    except (TypeError, ValueError):
        LOGGER.warning("Invalid %s volume value: %r", label, value)
        return None
    if parsed_volume > 1.0:
        parsed_volume /= 100.0
    return max(0.0, min(1.0, parsed_volume))


def _contains_audio_files(directory: Path) -> bool:
    """Returns True if a directory contains at least one supported audio file."""

    try:
        return directory.exists() and directory.is_dir() and any(
            child.is_file() and child.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
            for child in directory.iterdir()
        )
    except OSError:
        return False


def _extract_sounds_zip(zip_path: Path, target_directory: Path) -> None:
    """Extracts legacy sounds.zip while avoiding path traversal."""

    try:
        with zipfile.ZipFile(zip_path) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue

                entry_path = Path(entry.filename)
                file_name = entry_path.name

                if not file_name or Path(file_name).suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
                    continue

                target_path = target_directory / file_name

                with archive.open(entry) as source, target_path.open("wb") as destination:
                    destination.write(source.read())

        LOGGER.info("Extracted background music from %s.", zip_path)
    except Exception as error:
        LOGGER.warning("Failed to extract %s: %s", zip_path, error)
