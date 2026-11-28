"""Normalization for character species and years of age."""

from ai_adventure.world_species import SpeciesProfile


MAX_CHARACTER_AGE = 2_147_483_647


def normalize_character_age(value: object) -> int | None:
    if isinstance(value, str) and value.strip().isdecimal():
        try:
            value = int(value)
        except ValueError:
            return None
    if type(value) is int and 1 <= value <= MAX_CHARACTER_AGE:
        return value
    return None


def resolve_character_species(value: object, roster: list[SpeciesProfile]) -> str:
    """Use a matching roster name, otherwise prefer Human or the first species."""
    name = value.strip() if isinstance(value, str) else ""
    if not roster:
        return name
    names = {profile["name"].casefold(): profile["name"] for profile in roster}
    return names.get(name.casefold()) or names.get("human") or roster[0]["name"]
