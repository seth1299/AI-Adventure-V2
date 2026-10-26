"""Default stat associations for skill catalogs and normalized skill records."""

from ai_adventure.stats import ATTRIBUTES


_DEFAULTS = {
    **dict.fromkeys(("athletics", "melee weapons"), "Strength"),
    **dict.fromkeys(("acrobatics", "sleight of hand", "stealth", "ranged weapons"), "Dexterity"),
    **dict.fromkeys(("animal handling", "empathy", "first aid", "foraging", "insight",
                     "medicine", "perception", "survival", "tracking", "navigation"), "Wisdom"),
    **dict.fromkeys(("bluff", "deception", "persuasion", "negotiation", "customer relations"), "Charisma"),
}


def starting_skill_attribute(value=None) -> str:
    """Keep an explicit setup choice, or leave it empty for AI assignment."""
    return next((attribute for attribute in ATTRIBUTES
                 if str(value or "").strip().casefold() == attribute.casefold()), "")


def skill_attribute(value=None, name: str = "") -> str:
    """Canonicalize a stat, using a catalog default for records without one."""
    for attribute in ATTRIBUTES:
        if str(value or "").strip().casefold() == attribute.casefold():
            return attribute
    return _DEFAULTS.get(name.strip().casefold(), "Intelligence")
