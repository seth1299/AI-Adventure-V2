from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_adventure.new_game_setup import normalize_new_game_setup


LOGGER = logging.getLogger(__name__)
TEMPLATE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class NewGameTemplate:
    """A reusable new-game wizard setup."""

    name: str
    setup: dict[str, Any]


def load_new_game_templates(
    template_path: Path,
    *,
    legacy_template_path: Path | None = None,
) -> list[NewGameTemplate]:
    """Loads reusable new-game setup templates."""

    source_path = template_path

    if not source_path.exists():
        if legacy_template_path is None or not legacy_template_path.exists():
            return []

        source_path = legacy_template_path

    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.exception("Failed to load new-game templates from %s.", source_path)
        return []

    templates = _parse_template_payload(data, source_path)
    templates.sort(key=lambda template: template.name.casefold())
    return templates


def load_new_game_template(template_path: Path) -> dict[str, Any] | None:
    """Loads the first reusable new-game setup template, if available."""

    templates = load_new_game_templates(template_path)

    if not templates:
        return None

    return templates[0].setup


def save_new_game_template(
    template_path: Path,
    setup: dict[str, Any],
    *,
    template_name: str | None = None,
) -> bool:
    """Adds or updates a reusable new-game setup template."""

    clean_setup = normalize_new_game_setup(setup)
    clean_name = _template_name(template_name, clean_setup)
    templates = [
        template
        for template in load_new_game_templates(template_path)
        if template.name.casefold() != clean_name.casefold()
    ]
    templates.append(NewGameTemplate(clean_name, clean_setup))
    templates.sort(key=lambda template: template.name.casefold())

    payload = {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "templates": [
            {
                "name": template.name,
                "setup": template.setup,
            }
            for template in templates
        ],
    }

    try:
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        LOGGER.exception("Failed to save new-game templates to %s.", template_path)
        return False

    return True


def _parse_template_payload(data: Any, source_path: Path) -> list[NewGameTemplate]:
    """Normalizes both current multi-template and legacy single-template files."""

    if not isinstance(data, dict):
        LOGGER.warning("Ignored malformed new-game templates at %s.", source_path)
        return []

    raw_templates = data.get("templates")

    if isinstance(raw_templates, list):
        templates: list[NewGameTemplate] = []

        for index, raw_template in enumerate(raw_templates):
            template = _parse_template_entry(raw_template, source_path, index)

            if template is not None:
                templates.append(template)

        return templates

    raw_setup = data.get("setup", data)

    if not isinstance(raw_setup, dict):
        LOGGER.warning("Ignored new-game template without setup at %s.", source_path)
        return []

    clean_setup = normalize_new_game_setup(raw_setup)
    return [NewGameTemplate(_template_name(data.get("name"), clean_setup), clean_setup)]


def _parse_template_entry(
    raw_template: Any,
    source_path: Path,
    index: int,
) -> NewGameTemplate | None:
    """Parses one template entry."""

    if not isinstance(raw_template, dict):
        LOGGER.warning("Ignored malformed new-game template %s in %s.", index, source_path)
        return None

    raw_setup = raw_template.get("setup")

    if not isinstance(raw_setup, dict):
        LOGGER.warning("Ignored new-game template %s without setup in %s.", index, source_path)
        return None

    clean_setup = normalize_new_game_setup(raw_setup)
    return NewGameTemplate(_template_name(raw_template.get("name"), clean_setup), clean_setup)


def _template_name(template_name: Any, setup: dict[str, Any]) -> str:
    """Returns a stable display name for a template."""

    candidates = [
        template_name,
        setup.get("title"),
        setup.get("character", {}).get("name"),
        "New Game Template",
    ]

    for candidate in candidates:
        clean_candidate = str(candidate or "").strip()

        if clean_candidate:
            return clean_candidate

    return "New Game Template"
