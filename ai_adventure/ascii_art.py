from __future__ import annotations

from typing import Any


MAX_ASCII_ART_CHARACTERS = 4000
MIN_ASCII_ART_LINES = 3
MAX_ASCII_ART_LINES = 12
MAX_ASCII_ART_LINE_CHARACTERS = 40
_DRAWING_CHARACTERS = frozenset("_/\\|()[]{}<>-=+*#.'`~^:")

_CAMERA_ART = (
    "   ___________\n"
    "  /  _______  \\\n"
    " |  /  ___  \\  |\n"
    " | |  (___)  | |\n"
    " |  \\_______/  |\n"
    "  \\___________/"
)
_BOOK_ART = (
    "    ______  ______\n"
    "   /     /\\     /\n"
    "  /_____/  \\___/\n"
    "  |     |  |   |\n"
    "  |_____|  |___|"
)
_KEY_ART = (
    "      __\n"
    "  ___/  \\___\n"
    " (___    ___  )====\n"
    "     \\__/  |_|"
)
_LENS_ART = (
    "      _____\n"
    "    /       \\\n"
    "   |         |\n"
    "    \\_______/\n"
    "        \\\n"
    "         \\"
)
_HAT_ART = (
    "       ______\n"
    "    .-'      '-.\n"
    "   /____________\\\n"
    "      \\______/"
)
_COAT_ART = (
    "      __/\\__\n"
    "     /      \\\n"
    "    /|      |\\\n"
    "     |      |\n"
    "     |______|"
)
_WRITING_TOOL_ART = (
    "        /\\\n"
    "       /  \\\n"
    "      |    |\n"
    "      |    |\n"
    "      |____|\n"
    "        ||"
)
_AMMUNITION_ART = (
    "     /\\   /\\   /\\\n"
    "    |  | |  | |  |\n"
    "    |  | |  | |  |\n"
    "    |__| |__| |__|"
)
_VEHICLE_ART = (
    "      _________\n"
    "  ___/  _   _  \\___\n"
    " /___|_________|___\\\n"
    "    O           O"
)
_WEAPON_ART = (
    "  __________________\n"
    " /_________________/====\n"
    "        ||\n"
    "        ||"
)
_BOTTLE_ART = (
    "      __\n"
    "     |  |\n"
    "    /    \\\n"
    "   |      |\n"
    "   |______|"
)
_CONTAINER_ART = (
    "     __________\n"
    "   /__________/|\n"
    "  |          | |\n"
    "  |__________|/"
)
_GENERIC_ITEM_ART = (
    "      ________\n"
    "    /________/|\n"
    "   |        | |\n"
    "   |________|/"
)


def normalize_ascii_art(
    raw_value: Any,
    *,
    max_characters: int = MAX_ASCII_ART_CHARACTERS,
) -> str:
    """Returns display-ready fixed-width art from Gemini or saved state."""

    text = str(raw_value or "")
    if not text:
        return ""

    # Gemini occasionally double-escapes JSON line endings, leaving visible
    # backslash-n text after JSON parsing. Interpret only newline escapes; other
    # backslashes are meaningful drawing characters and remain untouched.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")

    lines = text.strip("\n").split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    normalized = "\n".join(lines).strip("\n")
    return normalized[: max(0, int(max_characters))]


def is_substantive_ascii_art(raw_value: Any) -> bool:
    """Returns whether a value is a real multi-line drawing rather than a label."""

    art = normalize_ascii_art(raw_value, max_characters=1200)
    lines = art.splitlines()
    if not MIN_ASCII_ART_LINES <= len(lines) <= MAX_ASCII_ART_LINES:
        return False
    if any(len(line) > MAX_ASCII_ART_LINE_CHARACTERS for line in lines):
        return False

    drawing_lines = sum(
        1 for line in lines if sum(character in _DRAWING_CHARACTERS for character in line) >= 2
    )
    drawing_characters = sum(
        character in _DRAWING_CHARACTERS
        for line in lines
        for character in line
    )
    return drawing_lines >= 2 and drawing_characters >= 6


def fallback_ascii_art(item_name: str, category: str = "") -> str:
    """Returns a small deterministic pictogram when generated art is unusable."""

    identity = f"{item_name} {category}".casefold()
    if any(word in identity for word in ("camera", "photograph")):
        return _CAMERA_ART
    if any(word in identity for word in ("book", "journal", "notebook", "ledger", "manual")):
        return _BOOK_ART
    if "key" in identity:
        return _KEY_ART
    if any(word in identity for word in ("magnifying", "lens", "loupe")):
        return _LENS_ART
    if any(word in identity for word in ("hat", "fedora")):
        return _HAT_ART
    if any(word in identity for word in ("coat", "trenchcoat", "jacket")):
        return _COAT_ART
    if any(word in identity for word in ("pen", "pencil", "stylus")):
        return _WRITING_TOOL_ART
    if any(word in identity for word in ("ammo", "ammunition", "cartridge", "bullet")):
        return _AMMUNITION_ART
    if any(word in identity for word in ("car", "sedan", "truck", "vehicle")):
        return _VEHICLE_ART
    if any(word in identity for word in ("weapon", "gun", "pistol", "revolver", "rifle", "sword")):
        return _WEAPON_ART
    if any(word in identity for word in ("bottle", "vial", "potion", "poison", "flask")):
        return _BOTTLE_ART
    if any(word in identity for word in ("container", "case", "kit", "bag", "box", "wallet")):
        return _CONTAINER_ART
    return _GENERIC_ITEM_ART


def ensure_substantive_ascii_art(
    raw_value: Any,
    *,
    item_name: str,
    category: str = "",
) -> str:
    """Preserves valid generated art or supplies a pictorial local fallback."""

    normalized = normalize_ascii_art(raw_value, max_characters=1200)
    if is_substantive_ascii_art(normalized):
        return normalized
    return fallback_ascii_art(item_name, category)
