from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ai_adventure.new_game_setup import normalize_new_game_setup


LOGGER = logging.getLogger(__name__)
TEMPLATE_SCHEMA_VERSION = 1


def load_new_game_template(template_path: Path) -> dict[str, Any] | None:
    """Loads the last successful new-game setup template, if available."""

    if not template_path.exists():
        return None

    try:
        data = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.exception("Failed to load new-game template from %s.", template_path)
        return None

    if not isinstance(data, dict):
        LOGGER.warning("Ignored malformed new-game template at %s.", template_path)
        return None

    raw_setup = data.get("setup", data)

    if not isinstance(raw_setup, dict):
        LOGGER.warning("Ignored new-game template without setup at %s.", template_path)
        return None

    return normalize_new_game_setup(raw_setup)


def save_new_game_template(template_path: Path, setup: dict[str, Any]) -> bool:
    """Saves the latest successful new-game setup as the reusable template."""

    clean_setup = normalize_new_game_setup(setup)
    payload = {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "setup": clean_setup,
    }

    try:
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        LOGGER.exception("Failed to save new-game template to %s.", template_path)
        return False

    return True
