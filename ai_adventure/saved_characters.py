"""Portable character profiles for starting a fresh adventure."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from ai_adventure.character_identity import normalize_character_age
from ai_adventure.template_images import template_image_bytes


CHARACTER_PROFILE_FIELDS = ("name", "name_pronunciation", "pronouns", "appearance", "backstory", "notes")
_FORMAT = "ai_adventure_character"
_VERSION = 1
_MAX_FILE_BYTES = 16 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


def _profile_fields(character: Any) -> dict[str, Any]:
    if not isinstance(character, dict) or any(not isinstance(character.get(field), str)
                                              for field in CHARACTER_PROFILE_FIELDS):
        raise ValueError("The file must contain the Character page's profile fields.")
    profile = {field: character[field] for field in CHARACTER_PROFILE_FIELDS}
    species = character.get("species")
    if isinstance(species, str) and species.strip():
        profile["species"] = species.strip()
    if (age := normalize_character_age(character.get("age"))) is not None:
        profile["age"] = age
    return profile


def _portrait_record(name: str, png_base64: Any) -> dict[str, str]:
    if not name.strip() or not isinstance(png_base64, str) or not png_base64:
        raise ValueError("The saved character portrait is invalid.")
    record = {"subject_type": "player", "display_name": name.strip(), "png_base64": png_base64}
    request = SimpleNamespace(subject_type="player", display_name=name)
    if template_image_bytes([record], request) is None:
        raise ValueError("The saved character portrait must be a valid PNG image.")
    return record


def load_saved_character(path: Path) -> tuple[dict[str, Any], dict[str, str] | None, str]:
    """Validate a profile and its portrait before the caller replaces any fields."""
    with path.open("rb") as file:
        data = file.read(_MAX_FILE_BYTES + 1)
    if len(data) > _MAX_FILE_BYTES:
        raise ValueError("The saved character file exceeds 16 MB.")
    try:
        payload = json.loads(data)
    except (ValueError, RecursionError) as error:
        raise ValueError("The file is not a valid saved character.") from error
    if not isinstance(payload, dict) or payload.get("format") != _FORMAT:
        raise ValueError("Choose an AI Adventure saved character file.")
    if type(payload.get("version")) is not int or payload["version"] != _VERSION:
        raise ValueError("This saved character file uses an unsupported version.")
    character = _profile_fields(payload.get("character"))
    portrait = _portrait_record(character["name"], payload["portrait_png"]) if "portrait_png" in payload else None
    library_name = payload.get("library_name", "")
    if not isinstance(library_name, str):
        raise ValueError("The saved character library name is invalid.")
    return character, portrait, library_name.strip()


def save_character(path: Path, character: dict[str, Any], portrait: dict[str, str] | None = None, *,
                   library_name: str = "") -> None:
    """Atomically replace the portable profile, including any portrait and library name."""
    character = _profile_fields(character)
    payload: dict[str, Any] = {"format": _FORMAT, "version": _VERSION, "character": character}
    if library_name := library_name.strip():
        payload["library_name"] = library_name
    if portrait is not None:
        if portrait.get("subject_type") != "player" or portrait.get("display_name", "").strip().casefold() != character["name"].strip().casefold():
            raise ValueError("The portrait does not belong to this character.")
        record = _portrait_record(character["name"], portrait.get("png_base64"))
        payload["portrait_png"] = record["png_base64"]
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(data) > _MAX_FILE_BYTES:
        raise ValueError("The saved character file exceeds 16 MB.")
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class SavedCharacter:
    id: str
    profile: dict[str, Any]
    portrait: dict[str, str] | None
    modified_at: datetime
    library_name: str = ""

    @property
    def display_name(self) -> str:
        return self.library_name or self.profile["name"]


class SavedCharacterLibrary:
    """Discover and store profiles in the application's character directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @staticmethod
    def id_for_player(player_id: str) -> str:
        return uuid5(NAMESPACE_URL, f"ai-adventure:saved-character:{player_id}").hex

    def _path(self, character_id: str) -> Path:
        if UUID(character_id).hex != character_id:
            raise ValueError("Invalid saved character ID.")
        path = self.directory / f"{character_id}.character.json"
        if path.is_symlink():
            raise ValueError("Saved character files must be stored in the library.")
        return path

    def list(self) -> list[SavedCharacter]:
        entries = []
        for path in self.directory.glob("*.character.json"):
            character_id = path.name.removesuffix(".character.json")
            try:
                if entry := self.get(character_id):
                    entries.append(entry)
            except (OSError, ValueError):
                LOGGER.warning("Could not read saved character %s.", path.name)
        return sorted(entries, key=lambda entry: (entry.display_name.casefold(), -entry.modified_at.timestamp(), entry.id))

    def get(self, character_id: str) -> SavedCharacter | None:
        path = self._path(character_id)
        try:
            profile, portrait, library_name = load_saved_character(path)
            modified_at = datetime.fromtimestamp(path.stat().st_mtime)
        except FileNotFoundError:
            return None
        return SavedCharacter(character_id, profile, portrait, modified_at, library_name)

    def save(self, profile: dict[str, Any], portrait: dict[str, str] | None = None, *,
             character_id: str | None = None, library_name: str = "") -> str:
        if not _profile_fields(profile)["name"].strip():
            raise ValueError("Enter a character name before saving.")
        character_id = character_id or uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=True)
        save_character(self._path(character_id), profile, portrait, library_name=library_name)
        return character_id

    def delete(self, character_id: str) -> None:
        self._path(character_id).unlink()
