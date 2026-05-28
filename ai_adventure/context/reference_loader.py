from __future__ import annotations

import json
import logging
from importlib.resources import files
from pathlib import Path
from typing import Any

from ai_adventure.context.models import ContextLibrary, ContextSection


LOGGER = logging.getLogger(__name__)


class ContextReferenceLoader:
    """Loads structured AI reference context from JSON files."""

    def load_default_library(self) -> ContextLibrary:
        """
        Loads the packaged default context library.

        Returns:
            Validated context library.
        """

        package = files("ai_adventure.data.context")
        libraries: list[ContextLibrary] = []

        for filename in ["default_context.json", "default_rules.json"]:
            resource = package.joinpath(filename)
            raw_text = resource.read_text(encoding="utf-8")
            libraries.append(self.load_from_text(raw_text, source_name=str(resource)))

        return self._merge_libraries(libraries)

    def load_from_path(self, path: Path) -> ContextLibrary:
        """
        Loads a context library from a JSON file.

        Args:
            path: JSON file path.

        Returns:
            Validated context library.
        """

        return self.load_from_text(path.read_text(encoding="utf-8"), source_name=str(path))

    def load_from_text(self, raw_text: str, *, source_name: str) -> ContextLibrary:
        """
        Loads a context library from JSON text.

        Args:
            raw_text: JSON document.
            source_name: Human-readable source name for logging.

        Returns:
            Validated context library.
        """

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError:
            LOGGER.exception("Failed to parse context JSON: %s", source_name)
            raise

        library = self._parse_library(raw_data, source_name=source_name)
        LOGGER.info(
            "Loaded %s AI context sections from %s.",
            len(library.sections),
            source_name,
        )

        return library

    def _parse_library(self, raw_data: Any, *, source_name: str) -> ContextLibrary:
        """Validates raw JSON data into a context library."""

        if not isinstance(raw_data, dict):
            raise ValueError(f"Context library must be an object: {source_name}")

        schema_version = _read_int(raw_data, "schema_version", source_name=source_name)
        raw_sections = raw_data.get("sections")

        if not isinstance(raw_sections, list):
            raise ValueError(f"Context library sections must be a list: {source_name}")

        sections = tuple(
            self._parse_section(section, source_name=source_name)
            for section in raw_sections
        )

        section_ids = [section.id for section in sections]

        if len(section_ids) != len(set(section_ids)):
            raise ValueError(f"Context section ids must be unique: {source_name}")

        return ContextLibrary(schema_version=schema_version, sections=sections)

    def _parse_section(self, raw_section: Any, *, source_name: str) -> ContextSection:
        """Validates one raw JSON section."""

        if not isinstance(raw_section, dict):
            raise ValueError(f"Context section must be an object: {source_name}")

        section_id = _read_string(raw_section, "id", source_name=source_name)
        title = _read_string(raw_section, "title", source_name=source_name)
        category = _read_string(raw_section, "category", source_name=source_name)
        priority = _read_int(raw_section, "priority", source_name=source_name)

        raw_tags = raw_section.get("tags", [])

        if not isinstance(raw_tags, list) or not all(
            isinstance(tag, str) and tag.strip() for tag in raw_tags
        ):
            raise ValueError(f"Context section tags must be strings: {section_id}")

        content = raw_section.get("content", {})

        if not isinstance(content, dict):
            raise ValueError(f"Context section content must be an object: {section_id}")

        return ContextSection(
            id=section_id,
            title=title,
            category=category,
            tags=tuple(tag.strip().lower() for tag in raw_tags),
            priority=priority,
            content=content,
        )

    def _merge_libraries(self, libraries: list[ContextLibrary]) -> ContextLibrary:
        """Merges compatible context libraries into one library."""

        sections: list[ContextSection] = []
        section_ids: set[str] = set()

        for library in libraries:
            for section in library.sections:
                if section.id in section_ids:
                    raise ValueError(f"Duplicate context section id: {section.id}")

                sections.append(section)
                section_ids.add(section.id)

        LOGGER.info("Loaded %s merged AI context sections.", len(sections))

        return ContextLibrary(schema_version=1, sections=tuple(sections))


def _read_string(raw_data: dict[str, Any], key: str, *, source_name: str) -> str:
    """Reads and validates a required string value."""

    value = raw_data.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Context field '{key}' must be a non-empty string: {source_name}")

    return value.strip()


def _read_int(raw_data: dict[str, Any], key: str, *, source_name: str) -> int:
    """Reads and validates a required integer value."""

    value = raw_data.get(key)

    if not isinstance(value, int):
        raise ValueError(f"Context field '{key}' must be an integer: {source_name}")

    return value
