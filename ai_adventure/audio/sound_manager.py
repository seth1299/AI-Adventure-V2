from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any

from ai_adventure.audio.catalog import distinct_audio_track_catalogs_with_ambience


LOGGER = logging.getLogger(__name__)


SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav"}
SOUND_EFFECT_CHANNEL_INDEX = 2
BACKGROUND_AMBIENCE_CHANNEL_INDEX = 3


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


def prepare_sound_effect_directory(app_paths: Any) -> Path:
    """Finds or prepares the distinct one-shot sound-effect directory."""

    managed_effects_dir = Path(app_paths.sound_effects_dir)
    managed_effects_dir.mkdir(parents=True, exist_ok=True)
    if _contains_audio_files(managed_effects_dir):
        return managed_effects_dir

    package_effects_dir = Path(app_paths.package_sound_effects_dir)
    if _contains_audio_files(package_effects_dir):
        return package_effects_dir

    return managed_effects_dir


def prepare_background_ambience_directory(app_paths: Any) -> Path:
    """Finds or prepares the distinct looping background-ambience directory."""

    managed_ambience_dir = Path(app_paths.background_ambience_dir)
    managed_ambience_dir.mkdir(parents=True, exist_ok=True)
    if _contains_audio_files(managed_ambience_dir):
        return managed_ambience_dir

    package_ambience_dir = Path(app_paths.package_background_ambience_dir)
    if _contains_audio_files(package_ambience_dir):
        return package_ambience_dir

    return managed_ambience_dir


class SoundManager:
    """Manages music, one-shot effects, and independent looping ambience."""

    def __init__(
        self,
        sounds_directory: str | Path,
        sound_effects_directory: str | Path | None = None,
        background_ambience_directory: str | Path | None = None,
        *,
        user_music_directory: str | Path | None = None,
        user_sound_effects_directory: str | Path | None = None,
        user_background_ambience_directory: str | Path | None = None,
        packaged_music_directory: str | Path | None = None,
        packaged_sound_effects_directory: str | Path | None = None,
        packaged_background_ambience_directory: str | Path | None = None,
    ) -> None:
        self.sounds_directory = Path(sounds_directory).expanduser()
        self.sound_effects_directory = (
            Path(sound_effects_directory).expanduser()
            if sound_effects_directory is not None
            else self.sounds_directory / "sound_effects"
        )
        self.background_ambience_directory = (
            Path(background_ambience_directory).expanduser()
            if background_ambience_directory is not None
            else self.sounds_directory / "background_ambience_tracks"
        )
        self.user_music_directory = (
            Path(user_music_directory).expanduser()
            if user_music_directory is not None
            else self.sounds_directory
        )
        self.user_sound_effects_directory = (
            Path(user_sound_effects_directory).expanduser()
            if user_sound_effects_directory is not None
            else self.sound_effects_directory
        )
        self.user_background_ambience_directory = (
            Path(user_background_ambience_directory).expanduser()
            if user_background_ambience_directory is not None
            else self.background_ambience_directory
        )
        self.packaged_music_directory = (
            Path(packaged_music_directory).expanduser()
            if packaged_music_directory is not None
            else None
        )
        self.packaged_sound_effects_directory = (
            Path(packaged_sound_effects_directory).expanduser()
            if packaged_sound_effects_directory is not None
            else None
        )
        self.packaged_background_ambience_directory = (
            Path(packaged_background_ambience_directory).expanduser()
            if packaged_background_ambience_directory is not None
            else None
        )
        self.current_music: str | None = None
        self.current_background_ambience: str | None = None
        self.music_volume: float = 0.25
        self.sound_effects_volume: float = 0.35
        self.background_ambience_volume: float = 0.15
        self.music_enabled = True
        self.sound_effects_enabled = True
        self.background_ambience_enabled = True
        self._initialized = False
        self._pygame: Any = None
        self._sound_effect: Any = None
        self._background_ambience: Any = None
        self._music_track_cache: dict[str, Path] = {}
        self._sound_effect_track_cache: dict[str, Path] = {}
        self._background_ambience_track_cache: dict[str, Path] = {}

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

        self._music_track_cache = _audio_file_cache(
            self._audio_directories(
                self.user_music_directory,
                self.sounds_directory,
                *([self.packaged_music_directory] if self.packaged_music_directory else []),
            )
        )
        self._sound_effect_track_cache = _audio_file_cache(
            self._audio_directories(
                self.user_sound_effects_directory,
                self.sound_effects_directory,
                *(
                    [self.packaged_sound_effects_directory]
                    if self.packaged_sound_effects_directory
                    else []
                ),
            ),
        )
        self._background_ambience_track_cache = _audio_file_cache(
            self._audio_directories(
                self.user_background_ambience_directory,
                self.background_ambience_directory,
                *(
                    [self.packaged_background_ambience_directory]
                    if self.packaged_background_ambience_directory
                    else []
                ),
            ),
        )
        music_names, effect_names, ambience_names = distinct_audio_track_catalogs_with_ambience(
            (path.name for path in self._music_track_cache.values()),
            (path.name for path in self._sound_effect_track_cache.values()),
            (path.name for path in self._background_ambience_track_cache.values()),
        )
        music_keys = {name.casefold() for name in music_names}
        effect_keys = {name.casefold() for name in effect_names}
        ambience_keys = {name.casefold() for name in ambience_names}
        self._music_track_cache = {
            key: path
            for key, path in self._music_track_cache.items()
            if key in music_keys
        }
        self._sound_effect_track_cache = {
            key: path
            for key, path in self._sound_effect_track_cache.items()
            if key in effect_keys
        }
        self._background_ambience_track_cache = {
            key: path
            for key, path in self._background_ambience_track_cache.items()
            if key in ambience_keys
        }

    @staticmethod
    def _audio_directories(*directories: Path) -> tuple[Path, ...]:
        """Returns unique audio directories in priority order."""

        result: list[Path] = []
        seen: set[str] = set()
        for directory in directories:
            clean_directory = Path(directory).expanduser()
            key = str(clean_directory.resolve()).casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(clean_directory)
        return tuple(result)

    def import_audio_file(
        self,
        source_path: str | Path,
        category: str,
    ) -> tuple[bool, str]:
        """Copies one supported user audio file into the managed catalog."""

        source = Path(source_path).expanduser()
        clean_category = str(category or "").strip().casefold()
        target_directories = {
            "music": self.user_music_directory,
            "sound_effect": self.user_sound_effects_directory,
            "sound_effects": self.user_sound_effects_directory,
            "background_ambience": self.user_background_ambience_directory,
            "ambience": self.user_background_ambience_directory,
        }
        target_directory = target_directories.get(clean_category)
        if target_directory is None:
            return False, "Unknown audio category."
        if not source.is_file():
            return False, "The selected audio file no longer exists."
        if source.suffix.casefold() not in SUPPORTED_AUDIO_EXTENSIONS:
            return False, "Choose an MP3, OGG, or WAV file."
        try:
            if source.stat().st_size <= 0:
                return False, "The selected audio file is empty."
            target_directory.mkdir(parents=True, exist_ok=True)
            target_path = target_directory / source.name
            if source.resolve() != target_path.resolve():
                shutil.copy2(source, target_path)
            self.refresh_tracks()
        except (OSError, ValueError) as error:
            LOGGER.warning("Could not import user audio %s: %s", source, error)
            return False, f"Could not import that audio file: {error}"
        LOGGER.info("Imported user %s audio: %s", clean_category, target_path.name)
        return True, target_path.name

    def get_valid_track_names(self) -> list[str]:
        """Returns known playable audio filenames."""

        self.refresh_tracks()
        return sorted(path.name for path in self._music_track_cache.values())

    def get_valid_sound_effect_names(self) -> list[str]:
        """Returns audio files that may play as one-shot narration cues."""

        self.refresh_tracks()
        return sorted(path.name for path in self._sound_effect_track_cache.values())

    def get_valid_background_ambience_names(self) -> list[str]:
        """Returns audio files that may loop as quiet environmental ambience."""

        self.refresh_tracks()
        return sorted(
            path.name for path in self._background_ambience_track_cache.values()
        )

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

        track_path = self._resolve_track_path(
            track_name_or_path,
            cache=self._music_track_cache,
        )
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

        track_path = self._resolve_track_path(
            track_name_or_path,
            cache=self._music_track_cache,
        )
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
        """Enables or disables one-shot narration sound effects."""

        self.sound_effects_enabled = bool(enabled)
        if not self.sound_effects_enabled:
            self.stop_sound_effect(clear_current=False)

    def set_sound_effects_volume(self, volume: float | int | None) -> None:
        """Sets one-shot sound-effect volume as either 0.0-1.0 or 0-100."""

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

    def set_background_ambience_enabled(self, enabled: bool) -> None:
        """Enables or disables independent looping background ambience."""

        self.background_ambience_enabled = bool(enabled)
        if not self.background_ambience_enabled:
            self.stop_background_ambience(clear_current=False)

    def set_background_ambience_volume(self, volume: float | int | None) -> None:
        """Sets looping background-ambience volume as either 0.0-1.0 or 0-100."""

        parsed_volume = _normalized_volume(value=volume, label="background-ambience")
        if parsed_volume is None:
            return
        self.background_ambience_volume = parsed_volume
        if self._initialized and self._pygame is not None:
            try:
                self._pygame.mixer.Channel(BACKGROUND_AMBIENCE_CHANNEL_INDEX).set_volume(
                    self.background_ambience_volume
                )
            except Exception as error:
                LOGGER.warning("Failed to update background-ambience volume: %s", error)

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
        """Plays one short effect once without interrupting music or narration."""

        if not self.sound_effects_enabled:
            return
        if not self._initialized or self._pygame is None:
            LOGGER.warning("Cannot play a sound effect because audio is unavailable.")
            return

        track_path = self._resolve_track_path(
            track_name_or_path,
            cache=self._sound_effect_track_cache,
            label="Sound effect",
        )
        if track_path is None:
            return

        try:
            channel = self._pygame.mixer.Channel(SOUND_EFFECT_CHANNEL_INDEX)
            channel.stop()
            self._sound_effect = self._pygame.mixer.Sound(str(track_path))
            channel.set_volume(self.sound_effects_volume)
            channel.play(self._sound_effect, loops=0)
            LOGGER.info("Playing one-shot narration sound effect: %s", track_path.name)
        except Exception as error:
            LOGGER.warning("Failed to play sound effect %s: %s", track_path, error)

    def stop_sound_effect(self, *, clear_current: bool = True) -> None:
        """Stops the one-shot effect channel without interrupting music."""

        if self._initialized and self._pygame is not None:
            try:
                self._pygame.mixer.Channel(SOUND_EFFECT_CHANNEL_INDEX).stop()
            except Exception as error:
                LOGGER.warning("Failed to stop sound effect: %s", error)

        self._sound_effect = None

    def play_background_ambience(
        self,
        track_name_or_path: str | Path | None,
    ) -> None:
        """Loops one ambience track without interrupting music, effects, or narration."""

        if not self.background_ambience_enabled:
            return
        if not self._initialized or self._pygame is None:
            LOGGER.warning("Cannot play background ambience because audio is unavailable.")
            return
        track_path = self._resolve_track_path(
            track_name_or_path,
            cache=self._background_ambience_track_cache,
            label="Background ambience",
        )
        if track_path is None:
            return
        try:
            channel = self._pygame.mixer.Channel(BACKGROUND_AMBIENCE_CHANNEL_INDEX)
            if (
                self.current_background_ambience == track_path.name
                and getattr(channel, "get_busy", lambda: False)()
            ):
                return
            channel.stop()
            self._background_ambience = self._pygame.mixer.Sound(str(track_path))
            channel.set_volume(self.background_ambience_volume)
            channel.play(self._background_ambience, loops=-1)
            self.current_background_ambience = track_path.name
            LOGGER.info("Playing looping background ambience: %s", track_path.name)
        except Exception as error:
            LOGGER.warning("Failed to play background ambience %s: %s", track_path, error)

    def stop_background_ambience(self, *, clear_current: bool = True) -> None:
        """Stops only the looping background-ambience channel."""

        if self._initialized and self._pygame is not None:
            try:
                self._pygame.mixer.Channel(BACKGROUND_AMBIENCE_CHANNEL_INDEX).stop()
            except Exception as error:
                LOGGER.warning("Failed to stop background ambience: %s", error)
        self._background_ambience = None
        if clear_current:
            self.current_background_ambience = None

    def _resolve_track_path(
        self,
        track_name_or_path: str | Path | None,
        *,
        cache: dict[str, Path],
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
        cached_path = cache.get(raw_path.name.lower())

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


def _audio_file_cache(directory: Path | tuple[Path, ...]) -> dict[str, Path]:
    """Returns supported audio files in one catalog directory."""

    directories = directory if isinstance(directory, tuple) else (directory,)
    result: dict[str, Path] = {}
    for current_directory in directories:
        try:
            if not current_directory.exists() or not current_directory.is_dir():
                continue
            for file_path in current_directory.iterdir():
                if (
                    file_path.is_file()
                    and file_path.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
                ):
                    result.setdefault(file_path.name.casefold(), file_path)
        except OSError as error:
            LOGGER.warning(
                "Failed to refresh audio directory %s: %s",
                current_directory,
                error,
            )
    return result


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
