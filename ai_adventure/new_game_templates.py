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
    normalize_setups: bool = True,
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

    templates = _parse_template_payload(data, source_path, normalize_setups=normalize_setups)
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
    normalize_setup: bool = True,
) -> bool:
    """Adds or updates a reusable new-game setup template."""

    clean_setup = _template_setup_payload(setup, normalize_setup=normalize_setup)
    clean_name = _template_name(template_name, clean_setup)
    templates = [
        template
        for template in load_new_game_templates(template_path, normalize_setups=False)
        if template.name.casefold() != clean_name.casefold()
    ]
    templates.append(NewGameTemplate(clean_name, clean_setup))
    templates.sort(key=lambda template: template.name.casefold())

    return write_new_game_templates(template_path, templates)


def delete_new_game_template(template_path: Path, template_name: str) -> bool:
    """Removes a reusable new-game setup template by display name."""

    clean_name = str(template_name or "").strip().casefold()

    if not clean_name:
        return False

    templates = [
        template
        for template in load_new_game_templates(template_path, normalize_setups=False)
        if template.name.casefold() != clean_name
    ]
    return write_new_game_templates(template_path, templates)


def write_new_game_templates(
    template_path: Path,
    templates: list[NewGameTemplate],
) -> bool:
    """Writes reusable new-game setup templates to disk."""

    clean_templates = [
        NewGameTemplate(
            _template_name(template.name, template.setup),
            _template_setup_payload(template.setup, normalize_setup=False),
        )
        for template in templates
    ]
    clean_templates.sort(key=lambda template: template.name.casefold())

    payload = {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "templates": [
            {
                "name": template.name,
                "setup": template.setup,
            }
            for template in clean_templates
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


def _parse_template_payload(
    data: Any,
    source_path: Path,
    *,
    normalize_setups: bool,
) -> list[NewGameTemplate]:
    """Normalizes both current multi-template and legacy single-template files."""

    if not isinstance(data, dict):
        LOGGER.warning("Ignored malformed new-game templates at %s.", source_path)
        return []

    raw_templates = data.get("templates")

    if isinstance(raw_templates, list):
        templates: list[NewGameTemplate] = []

        for index, raw_template in enumerate(raw_templates):
            template = _parse_template_entry(
                raw_template,
                source_path,
                index,
                normalize_setups=normalize_setups,
            )

            if template is not None:
                templates.append(template)

        return templates

    raw_setup = data.get("setup", data)

    if not isinstance(raw_setup, dict):
        LOGGER.warning("Ignored new-game template without setup at %s.", source_path)
        return []

    clean_setup = _template_setup_payload(raw_setup, normalize_setup=normalize_setups)
    return [NewGameTemplate(_template_name(data.get("name"), clean_setup), clean_setup)]


def _parse_template_entry(
    raw_template: Any,
    source_path: Path,
    index: int,
    *,
    normalize_setups: bool,
) -> NewGameTemplate | None:
    """Parses one template entry."""

    if not isinstance(raw_template, dict):
        LOGGER.warning("Ignored malformed new-game template %s in %s.", index, source_path)
        return None

    raw_setup = raw_template.get("setup")

    if not isinstance(raw_setup, dict):
        LOGGER.warning("Ignored new-game template %s without setup in %s.", index, source_path)
        return None

    clean_setup = _template_setup_payload(raw_setup, normalize_setup=normalize_setups)
    return NewGameTemplate(_template_name(raw_template.get("name"), clean_setup), clean_setup)


def _template_setup_payload(setup: Any, *, normalize_setup: bool) -> dict[str, Any]:
    """Returns a template setup payload, optionally keeping partial fields partial."""

    if normalize_setup:
        return normalize_new_game_setup(setup)

    if not isinstance(setup, dict):
        return {}

    clean_setup = _json_safe_value(setup)
    return clean_setup if isinstance(clean_setup, dict) else {}


def _json_safe_value(value: Any) -> Any:
    """Returns a JSON-safe copy without filling missing template fields."""

    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    return str(value)


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
