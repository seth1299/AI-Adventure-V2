from __future__ import annotations

from typing import Any


MAX_ASCII_ART_CHARACTERS = 4000


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
