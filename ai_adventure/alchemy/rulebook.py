from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from importlib.resources import files
from typing import Any


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlchemyRuleTerm:
    """A named alchemy concept from the rulebook."""

    id: str
    name: str
    summary: str = ""
    behaviors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable term dictionary."""

        data = asdict(self)
        data["behaviors"] = list(self.behaviors)
        return data


@dataclass(frozen=True)
class RulebookReagent:
    """A setting-consistent example reagent from the alchemy rulebook."""

    name: str
    material_type: str
    description: str
    common_uses: tuple[str, ...] = field(default_factory=tuple)
    virtues: tuple[str, ...] = field(default_factory=tuple)
    qualities: tuple[str, ...] = field(default_factory=tuple)
    motions: tuple[str, ...] = field(default_factory=tuple)
    locations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Returns a JSON-serializable reagent dictionary."""

        data = asdict(self)
        for key in ["common_uses", "virtues", "qualities", "motions", "locations"]:
            data[key] = list(data[key])
        return data


@dataclass(frozen=True)
class AlchemyRulebook:
    """Structured alchemy rules and examples."""

    schema_version: int
    stages: tuple[AlchemyRuleTerm, ...]
    product_types: tuple[AlchemyRuleTerm, ...]
    qualities: tuple[AlchemyRuleTerm, ...]
    motions: tuple[AlchemyRuleTerm, ...]
    material_families: tuple[str, ...]
    gathering_rules: tuple[str, ...]
    preparation_principles: tuple[str, ...]
    refinement_methods: tuple[str, ...]
    example_reagents: tuple[RulebookReagent, ...]

    def to_context_summary(
        self,
        *,
        player_command: str,
        max_reagents: int = 8,
    ) -> dict[str, Any]:
        """
        Builds a compact rulebook summary for an AI context packet.

        Args:
            player_command: Current player command.
            max_reagents: Maximum example reagents to include.

        Returns:
            JSON-serializable rulebook summary.
        """

        return {
            "stages": [term.to_dict() for term in self.stages],
            "product_types": [term.to_dict() for term in self.product_types],
            "qualities": [term.to_dict() for term in self.qualities],
            "motions": [term.to_dict() for term in self.motions],
            "material_families": list(self.material_families),
            "gathering_rules": list(self.gathering_rules),
            "preparation_principles": list(self.preparation_principles),
            "refinement_methods": list(self.refinement_methods),
            "example_reagents": [
                reagent.to_dict()
                for reagent in self.select_reagents(
                    player_command,
                    max_reagents=max_reagents,
                )
            ],
        }

    def select_reagents(
        self,
        player_command: str,
        *,
        max_reagents: int,
    ) -> list[RulebookReagent]:
        """
        Selects relevant example reagents for a command.

        The matcher is intentionally simple and deterministic until a richer
        alchemy parser exists.
        """

        words = _tokenize(player_command)
        scored: list[tuple[int, str, RulebookReagent]] = []

        for reagent in self.example_reagents:
            haystack = _tokenize(
                " ".join(
                    [
                        reagent.name,
                        reagent.material_type,
                        reagent.description,
                        " ".join(reagent.common_uses),
                        " ".join(reagent.virtues),
                        " ".join(reagent.qualities),
                        " ".join(reagent.motions),
                        " ".join(reagent.locations),
                    ]
                )
            )
            score = len(words.intersection(haystack))

            if score > 0:
                scored.append((score, reagent.name, reagent))

        if not scored:
            return list(self.example_reagents[:max_reagents])

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[:max_reagents]]


class AlchemyRulebookLoader:
    """Loads packaged structured alchemy rulebook data."""

    def load_default_rulebook(self) -> AlchemyRulebook:
        """Loads the packaged default alchemy rulebook."""

        resource = files("ai_adventure.data.alchemy").joinpath("alchemy_rulebook.json")
        return self.load_from_text(resource.read_text(encoding="utf-8"), source=str(resource))

    def load_from_text(self, raw_text: str, *, source: str) -> AlchemyRulebook:
        """Loads and validates alchemy rulebook JSON text."""

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError:
            LOGGER.exception("Failed to parse alchemy rulebook JSON: %s", source)
            raise

        return self._parse_rulebook(raw_data, source=source)

    def _parse_rulebook(self, raw_data: Any, *, source: str) -> AlchemyRulebook:
        """Validates raw rulebook data."""

        if not isinstance(raw_data, dict):
            raise ValueError(f"Alchemy rulebook must be an object: {source}")

        return AlchemyRulebook(
            schema_version=_read_int(raw_data, "schema_version", source),
            stages=_parse_terms(raw_data.get("stages", []), source),
            product_types=_parse_terms(raw_data.get("product_types", []), source),
            qualities=_parse_terms(raw_data.get("qualities", []), source),
            motions=_parse_terms(raw_data.get("motions", []), source),
            material_families=tuple(_read_string_list(raw_data, "material_families", source)),
            gathering_rules=tuple(_read_string_list(raw_data, "gathering_rules", source)),
            preparation_principles=tuple(
                _read_string_list(raw_data, "preparation_principles", source)
            ),
            refinement_methods=tuple(_read_string_list(raw_data, "refinement_methods", source)),
            example_reagents=tuple(
                _parse_reagent(reagent, source)
                for reagent in _read_list(raw_data, "example_reagents", source)
            ),
        )


def _parse_terms(raw_terms: Any, source: str) -> tuple[AlchemyRuleTerm, ...]:
    """Parses rule terms."""

    if not isinstance(raw_terms, list):
        raise ValueError(f"Alchemy rule terms must be a list: {source}")

    return tuple(
        AlchemyRuleTerm(
            id=_read_string(raw_term, "id", source),
            name=_read_string(raw_term, "name", source),
            summary=str(raw_term.get("summary", "")).strip(),
            behaviors=tuple(_read_string_list(raw_term, "behaviors", source, required=False)),
        )
        for raw_term in raw_terms
    )


def _parse_reagent(raw_reagent: Any, source: str) -> RulebookReagent:
    """Parses one example reagent."""

    if not isinstance(raw_reagent, dict):
        raise ValueError(f"Alchemy reagent must be an object: {source}")

    return RulebookReagent(
        name=_read_string(raw_reagent, "name", source),
        material_type=_read_string(raw_reagent, "material_type", source),
        description=_read_string(raw_reagent, "description", source),
        common_uses=tuple(_read_string_list(raw_reagent, "common_uses", source)),
        virtues=tuple(_read_string_list(raw_reagent, "virtues", source)),
        qualities=tuple(_read_string_list(raw_reagent, "qualities", source)),
        motions=tuple(_read_string_list(raw_reagent, "motions", source)),
        locations=tuple(_read_string_list(raw_reagent, "locations", source)),
    )


def _read_int(raw_data: dict[str, Any], key: str, source: str) -> int:
    """Reads a required integer."""

    value = raw_data.get(key)

    if not isinstance(value, int):
        raise ValueError(f"Alchemy rulebook field '{key}' must be an integer: {source}")

    return value


def _read_string(raw_data: dict[str, Any], key: str, source: str) -> str:
    """Reads a required non-empty string."""

    value = raw_data.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Alchemy rulebook field '{key}' must be text: {source}")

    return value.strip()


def _read_list(raw_data: dict[str, Any], key: str, source: str) -> list[Any]:
    """Reads a required list."""

    value = raw_data.get(key)

    if not isinstance(value, list):
        raise ValueError(f"Alchemy rulebook field '{key}' must be a list: {source}")

    return value


def _read_string_list(
    raw_data: dict[str, Any],
    key: str,
    source: str,
    *,
    required: bool = True,
) -> list[str]:
    """Reads a list of strings."""

    value = raw_data.get(key, [] if not required else None)

    if not isinstance(value, list):
        raise ValueError(f"Alchemy rulebook field '{key}' must be a string list: {source}")

    return [
        str(item).strip()
        for item in value
        if str(item).strip()
    ]


def _tokenize(value: str) -> set[str]:
    """Tokenizes simple text for deterministic reagent matching."""

    return {
        token.strip(".,!?;:()[]{}\"'").lower()
        for token in value.replace("-", " ").split()
        if token.strip(".,!?;:()[]{}\"'")
    }
