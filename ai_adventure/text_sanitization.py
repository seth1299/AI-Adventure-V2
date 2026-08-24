"""Shared normalization for English-only generated and narrated text."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_ASCII_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u00a0": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2026": "...",
        "\u2032": "'",
        "\u2033": '"',
    }
)
_HORIZONTAL_WHITESPACE_RE = re.compile(r"[^\S\r\n]+")
_SPACE_AROUND_NEWLINE_RE = re.compile(r" *\n *")


def sanitize_english_text(
    raw_text: Any,
    *,
    strip: bool = True,
    preserve_whitespace: bool = False,
) -> str:
    """Returns readable ASCII English text with foreign scripts removed."""

    translated = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    translated = translated.translate(_ASCII_PUNCTUATION_TRANSLATION)
    decomposed = unicodedata.normalize("NFKD", translated)

    characters: list[str] = []
    replacing_unsupported_run = False
    for character in decomposed:
        if unicodedata.combining(character):
            continue
        if character == "\n":
            characters.append(character)
            replacing_unsupported_run = False
            continue
        if character == "\t" or character == " ":
            characters.append(" ")
            replacing_unsupported_run = False
            continue
        if 32 <= ord(character) <= 126:
            characters.append(character)
            replacing_unsupported_run = False
            continue

        # One separating space prevents a removed foreign-script run from
        # joining the English words on either side into a new word.
        if not replacing_unsupported_run:
            characters.append(" ")
            replacing_unsupported_run = True

    clean_text = "".join(characters)
    if not preserve_whitespace:
        clean_text = _HORIZONTAL_WHITESPACE_RE.sub(" ", clean_text)
        clean_text = _SPACE_AROUND_NEWLINE_RE.sub("\n", clean_text)
    return clean_text.strip() if strip else clean_text


def sanitize_english_text_in_data(
    value: Any,
    *,
    preserve_whitespace: bool = False,
) -> Any:
    """Recursively sanitizes string values in generated structured data."""

    if isinstance(value, str):
        return sanitize_english_text(
            value,
            strip=False,
            preserve_whitespace=preserve_whitespace,
        )
    if isinstance(value, list):
        return [sanitize_english_text_in_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_english_text_in_data(item) for item in value)
    if isinstance(value, dict):
        return {
            key: sanitize_english_text_in_data(
                item,
                preserve_whitespace=(str(key).casefold() == "ascii_art"),
            )
            for key, item in value.items()
        }
    return value
