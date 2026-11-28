"""JSON profiles for a world's inhabitants and the reusable species library."""

import json
import logging
import re
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5


LOGGER = logging.getLogger(__name__)
_BUNDLED_SPECIES = Path(__file__).with_name("data") / "species" / "default_species.json"

SPECIES_PROFILE_RULE = (
    "Use species profiles to guide ages, genders, height, weight, and descriptions. "
    "These are typical traits and genre associations, and explicit character facts take precedence."
)
SPECIES_ROSTER_RULE = (
    "Use the world roster for newly generated people and use its exact names in NPC "
    "species fields. The roster does not restrict wildlife or monsters."
)
WORLD_SPECIES_RULE = f"{SPECIES_ROSTER_RULE} {SPECIES_PROFILE_RULE}"


class SpeciesProfile(TypedDict):
    name: str
    genders: list[str]
    typical_maturation_age: str
    typical_lifespan: str
    typical_height: str
    typical_weight: str
    typical_genres: list[str]
    description: str


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _measurement(value: object) -> str:
    if type(value) in (int, float):
        return str(value)
    return _text(value)


def _labels(value: object) -> list[str]:
    entries = value.split(",") if isinstance(value, str) else value
    labels: dict[str, str] = {}
    if isinstance(entries, list):
        for entry in entries:
            if label := _text(entry):
                labels.setdefault(label.casefold(), label)
    return list(labels.values())


def normalize_world_species(value: object) -> list[SpeciesProfile]:
    """Return named profiles in roster order, taking the first occurrence of a name."""
    if not isinstance(value, list):
        return []
    profiles: dict[str, SpeciesProfile] = {}
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        if not name or name.casefold() in profiles:
            continue
        profiles[name.casefold()] = {
            "name": name,
            "genders": _labels(entry.get("genders")),
            "typical_maturation_age": _measurement(entry.get("typical_maturation_age")),
            "typical_lifespan": _measurement(entry.get("typical_lifespan")),
            "typical_height": _measurement(entry.get("typical_height")),
            "typical_weight": _measurement(entry.get("typical_weight")),
            "typical_genres": _labels(entry.get("typical_genres")),
            "description": _text(entry.get("description")),
        }
    return list(profiles.values())


@cache
def _bundled_species() -> list[SpeciesProfile]:
    try:
        return normalize_world_species(json.loads(_BUNDLED_SPECIES.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        LOGGER.exception("Could not read the bundled species catalogue.")
        return []


def bundled_species() -> list[SpeciesProfile]:
    return normalize_world_species(_bundled_species())


def relevant_species_profiles(
    roster: list[SpeciesProfile], species_names: Iterable[object], command: str = "",
) -> list[SpeciesProfile]:
    """Use saved world profiles first, with bundled profiles for known actors when needed."""
    roster_by_name = {profile["name"].casefold(): profile for profile in roster}
    defaults_by_name = {profile["name"].casefold(): profile for profile in bundled_species()}
    catalogue = {**defaults_by_name, **roster_by_name}
    selected = {_text(name).casefold() for name in species_names}
    human = roster_by_name.get("human", {})
    default_human = defaults_by_name.get("human", {})
    custom_human = any(
        value and value != default_human.get(key)
        for key, value in human.items() if key != "name"
    )
    if not custom_human:
        selected.discard("human")

    # A named species can be relevant even before the player meets a member of it.
    command = command.casefold()
    for name in roster_by_name or catalogue:
        variants = [re.escape(name) + r"(?:s|es)?"]
        if name.endswith("f"):
            variants.append(re.escape(name[:-1] + "ves"))
        if re.search(r"(?<!\w)(?:" + "|".join(variants) + r")(?!\w)", command):
            selected.add(name)

    return [profile for name, profile in catalogue.items() if name in selected]


class SpeciesLibrary:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def list(self) -> list[SpeciesProfile]:
        profiles = []
        for path in sorted(self.directory.glob("*.species.json")):
            try:
                profiles.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                LOGGER.warning("Could not read saved species %s.", path.name)
        return sorted(normalize_world_species(profiles), key=lambda profile: profile["name"].casefold())

    def save(self, profile: SpeciesProfile) -> None:
        clean_profiles = normalize_world_species([profile])
        if not clean_profiles:
            raise ValueError("Enter a species name before saving.")
        [clean_profile] = clean_profiles
        species_id = uuid5(NAMESPACE_URL, f"ai-adventure:species:{clean_profile['name'].casefold()}")
        path = self.directory / f"{species_id.hex}.species.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False) as file:
                temporary = Path(file.name)
                json.dump(clean_profile, file, ensure_ascii=False, indent=2)
                file.write("\n")
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
