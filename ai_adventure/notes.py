from __future__ import annotations

import re
from typing import Any


def wrap_markdown_text(
    text: str,
    start: int,
    end: int,
    prefix: str,
    suffix: str,
    *,
    placeholder: str = "text",
) -> tuple[str, int, int]:
    """Wraps a selection and returns text plus the resulting selection bounds."""

    clean_text = str(text)
    clean_start = max(0, min(int(start), len(clean_text)))
    clean_end = max(clean_start, min(int(end), len(clean_text)))
    selected = clean_text[clean_start:clean_end] or placeholder
    replacement = f"{prefix}{selected}{suffix}"
    updated = clean_text[:clean_start] + replacement + clean_text[clean_end:]
    return (
        updated,
        clean_start + len(prefix),
        clean_start + len(prefix) + len(selected),
    )


def prefix_markdown_lines(text: str, prefix: str, *, numbered: bool = False) -> str:
    """Prefixes every selected line for Markdown lists, headings, or quotes."""

    lines = str(text).split("\n")
    return "\n".join(
        f"{index + 1}. {line}" if numbered else f"{prefix}{line}"
        for index, line in enumerate(lines)
    )


def normalize_note_tags(raw_tags: Any) -> list[str]:
    """Returns unique, display-ready tags using case-insensitive identity."""

    candidates = raw_tags if isinstance(raw_tags, list) else []
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in candidates:
        tag = " ".join(str(raw_tag).strip().lstrip("#").split())
        identity = tag.casefold()
        if tag and identity not in seen:
            tags.append(tag)
            seen.add(identity)
    return tags


def parse_note_tags(text: str) -> list[str]:
    """Parses comma-separated or hash-prefixed tags from the editor."""

    return normalize_note_tags(re.split(r",|(?=\s*#)", str(text)))


def normalize_note_entries(raw_entries: Any) -> list[dict[str, Any]]:
    """Returns clean, ordered player notes."""

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    if not isinstance(raw_entries, list):
        return entries
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        entry_id = str(raw_entry.get("entry_id", "")).strip()
        if not entry_id or entry_id in seen_ids:
            continue
        heading = str(raw_entry.get("heading", "")).strip()
        body = str(raw_entry.get("body", ""))
        if not heading and not body.strip():
            continue
        entries.append(
            {
                "entry_id": entry_id,
                "heading": heading,
                "body": body,
                "tags": normalize_note_tags(raw_entry.get("tags", [])),
            }
        )
        seen_ids.add(entry_id)
    return entries


def note_entries_for_ai(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Removes storage-only identity before notes enter AI context."""

    return [
        {
            "heading": str(entry.get("heading", "")).strip(),
            "body": str(entry.get("body", "")).strip(),
            "tags": normalize_note_tags(entry.get("tags", [])),
        }
        for entry in entries
        if str(entry.get("heading", "")).strip() or str(entry.get("body", "")).strip()
    ]
