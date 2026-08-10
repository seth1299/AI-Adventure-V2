from __future__ import annotations

import re
from html import unescape
from typing import Match


SSMD_BREAK_RE = re.compile(r"\s*\.\.\.(?:n|w|c|s|p|\d+(?:ms|s))\b\s*", re.IGNORECASE)
SSMD_SAY_AS_RE = re.compile(
    r"\[([^\]]+)\]\(\s*as\s*:\s*([a-z_]+)(?:\s*,[^\)]*)?\)",
    re.IGNORECASE,
)
SSMD_INLINE_VOICE_RE = re.compile(
    r"\[([^\]]+)\]\{\s*voice\s*=\s*['\"][^'\"]+['\"]\s*\}",
    re.IGNORECASE,
)
SSMD_PHONEME_RE = re.compile(
    r'\[([^\]]+)\]\{\s*(?:ph|phonemes)\s*=\s*[\'"][^\'"]+[\'"]\s*\}',
    re.IGNORECASE,
)
SSMD_DIV_TAG_RE = re.compile(r"</?div\b[^>]*>", re.IGNORECASE)


def apply_ssmd_say_as_tags(text: str) -> str:
    """Adds SSMD say-as tags for common story text that benefits from normalization."""

    clean_text = str(text or "")
    clean_text = _TWELVE_HOUR_TIME_RE.sub(
        lambda match: f"[{match.group(0)}](as: time)",
        clean_text,
    )
    clean_text = _TWENTY_FOUR_HOUR_TIME_RE.sub(
        _replace_24_hour_time_with_say_as,
        clean_text,
    )
    return clean_text


def apply_structural_pause_markers(text: str) -> str:
    """Converts paragraph boundaries into SSMD break markers without sentence splitting."""

    clean_text = str(text or "").strip()

    if not clean_text:
        return ""

    paragraphs = [
        re.sub(r"[ \t]+", " ", paragraph.strip())
        for paragraph in re.split(r"\n\s*\n+", clean_text)
        if paragraph.strip()
    ]

    if not paragraphs:
        return ""

    return " ...p ".join(paragraphs)


def strip_ssmd_markup_for_plain_tts(text: str) -> str:
    """Converts SSMD text into plain text for engines without SSMD support."""

    plain_text = str(text or "")
    plain_text = SSMD_PHONEME_RE.sub(lambda match: match.group(1), plain_text)
    plain_text = SSMD_INLINE_VOICE_RE.sub(lambda match: match.group(1), plain_text)
    plain_text = SSMD_DIV_TAG_RE.sub(" ", plain_text)
    plain_text = SSMD_SAY_AS_RE.sub(_replace_say_as_for_plain_tts, plain_text)
    plain_text = SSMD_BREAK_RE.sub(". ", plain_text)
    plain_text = re.sub(r"\s+", " ", plain_text)
    return unescape(plain_text).strip()


def normalize_tts_time_text(text: str) -> str:
    """Converts clock-style times into text that plain TTS engines pronounce naturally."""

    clean_text = str(text or "")
    clean_text = _TWELVE_HOUR_TIME_RE.sub(_replace_12_hour_time, clean_text)
    clean_text = _TWENTY_FOUR_HOUR_TIME_RE.sub(_replace_24_hour_time, clean_text)
    return clean_text


def _replace_24_hour_time_with_say_as(match: Match[str]) -> str:
    """Avoids wrapping already tagged time text a second time."""

    start = match.start()
    end = match.end()
    source = match.string

    if start >= 1 and source[start - 1] == "[":
        return match.group(0)

    if end < len(source) and source[end : end + 1] == "]":
        return match.group(0)

    return f"[{match.group(0)}](as: time)"


def _replace_say_as_for_plain_tts(match: Match[str]) -> str:
    """Expands supported say-as tags for plain fallback engines."""

    value = match.group(1)
    say_as_type = match.group(2).strip().casefold()

    if say_as_type == "time":
        return normalize_tts_time_text(value)

    return value


_TWELVE_HOUR_TIME_RE = re.compile(
    r"\b(1[0-2]|0?[1-9]):([0-5]\d)\s*([AaPp])\.?\s*[Mm]\.?(?=$|[^A-Za-z0-9_])"
)
_TWENTY_FOUR_HOUR_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_NUMBER_WORDS_0_TO_59 = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
}


def _replace_12_hour_time(match: Match[str]) -> str:
    """Returns spoken text for one 12-hour clock match."""

    hour = int(match.group(1))
    minute = int(match.group(2))
    suffix = match.group(3).lower()
    period = "morning" if suffix == "a" else "afternoon"

    if suffix == "p":
        if hour == 12:
            period = "afternoon"
        elif 5 <= hour <= 11:
            period = "evening"

    if suffix == "a" and hour == 12:
        period = "at night"
        return _spoken_time(hour, minute, period)

    return _spoken_time(hour, minute, f"in the {period}")


def _replace_24_hour_time(match: Match[str]) -> str:
    """Returns spoken text for one 24-hour clock match."""

    hour_24 = int(match.group(1))
    minute = int(match.group(2))
    display_hour = hour_24 % 12 or 12

    if 5 <= hour_24 < 12:
        period = "in the morning"
    elif 12 <= hour_24 < 17:
        period = "in the afternoon"
    elif 17 <= hour_24 < 22:
        period = "in the evening"
    else:
        period = "at night"

    return _spoken_time(display_hour, minute, period)


def _spoken_time(hour: int, minute: int, period: str) -> str:
    """Formats an already-parsed clock time for speech."""

    if hour == 12 and minute == 0 and period == "at night":
        return "midnight"

    if hour == 12 and minute == 0 and period == "in the afternoon":
        return "noon"

    hour_text = _number_word(hour)

    if minute == 0:
        return f"{hour_text} {period}"

    if minute < 10:
        return f"{hour_text} oh {_number_word(minute)} {period}"

    return f"{hour_text} {_number_word(minute)} {period}"


def _number_word(number: int) -> str:
    """Returns a plain English word for numbers from 0 through 59."""

    if number in _NUMBER_WORDS_0_TO_59:
        return _NUMBER_WORDS_0_TO_59[number]

    tens = number - (number % 10)
    ones = number % 10
    return f"{_NUMBER_WORDS_0_TO_59[tens]} {_NUMBER_WORDS_0_TO_59[ones]}"
