from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextSection:
    """A reusable piece of reference context for AI prompt construction."""

    id: str
    title: str
    category: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    priority: int = 0
    content: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable section dictionary."""

        data = asdict(self)
        data["tags"] = list(self.tags)
        return data


@dataclass(frozen=True)
class ContextLibrary:
    """A validated collection of reusable context sections."""

    schema_version: int
    sections: tuple[ContextSection, ...]

    def select_sections(
        self,
        tags: set[str],
        *,
        max_sections: int,
    ) -> list[ContextSection]:
        """
        Selects sections matching the requested tags.

        Sections tagged ``always`` are included for every packet.
        """

        selected: list[ContextSection] = []

        for section in self.sections:
            section_tags = set(section.tags)

            if "always" in section_tags or section_tags.intersection(tags):
                selected.append(section)

        selected.sort(key=lambda section: (-section.priority, section.id))
        return selected[:max_sections]

