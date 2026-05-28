from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
KNOWN_TEXT_MODELS = {
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
}


@dataclass(frozen=True)
class GeminiSettings:
    """Runtime settings for the Gemini API integration."""

    api_key: str = ""
    model: str = DEFAULT_GEMINI_MODEL

    @property
    def is_configured(self) -> bool:
        """Returns True when an API key is available."""

        return bool(self.api_key.strip())


@dataclass(frozen=True)
class AiNarrationResult:
    """Parsed result from an AI narration request."""

    narrative_text: str
    suggested_actions: list[str] = field(default_factory=list)
    suggested_events: list[dict[str, Any]] = field(default_factory=list)
    out_of_game: bool = False
    raw_text: str = ""


class GeminiConfigurationError(RuntimeError):
    """Raised when Gemini is requested without required configuration."""


class GeminiNarrationService:
    """Calls Gemini with structured story context packets."""

    def __init__(self, settings: GeminiSettings | None = None) -> None:
        """
        Args:
            settings: Gemini runtime settings. Defaults to environment settings.
        """

        self.settings = settings or load_gemini_settings()

    def generate_story_response(
        self,
        context_packet: dict[str, Any],
    ) -> AiNarrationResult:
        """
        Sends a story context packet to Gemini.

        Args:
            context_packet: Structured context packet from AiContextBuilder.

        Returns:
            Parsed narration result.
        """

        if not self.settings.is_configured:
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not configured. Add it to .env or the environment."
            )

        try:
            from google import genai
        except ImportError as error:
            raise GeminiConfigurationError(
                "google-genai is not installed. Install project requirements first."
            ) from error

        prompt = build_gemini_story_prompt(context_packet)
        client = genai.Client(api_key=self.settings.api_key)

        LOGGER.info("Sending story context packet to Gemini model %s.", self.settings.model)
        response = client.models.generate_content(
            model=self.settings.model,
            contents=prompt,
        )
        raw_text = str(getattr(response, "text", "") or "").strip()
        LOGGER.info("Gemini raw story response:\n%s", raw_text)

        if not raw_text:
            LOGGER.warning("Gemini returned an empty story response.")
            return AiNarrationResult(
                narrative_text="The narrator falls silent for a moment.",
                raw_text=raw_text,
            )

        return parse_gemini_story_response(raw_text)


def load_gemini_settings(env_path: Path | None = None) -> GeminiSettings:
    """
    Loads Gemini settings from .env and environment variables.

    Args:
        env_path: Optional explicit .env path.

    Returns:
        Gemini settings.
    """

    env_values = _read_env_file(env_path or Path(".env"))

    model = (
        os.getenv("GEMINI_MODEL")
        or env_values.get("GEMINI_MODEL")
        or DEFAULT_GEMINI_MODEL
    ).strip() or DEFAULT_GEMINI_MODEL

    if model not in KNOWN_TEXT_MODELS:
        LOGGER.warning(
            "Gemini model '%s' is not in the known supported text model list: %s.",
            model,
            ", ".join(sorted(KNOWN_TEXT_MODELS)),
        )

    return GeminiSettings(
        api_key=(
            os.getenv("GEMINI_API_KEY")
            or env_values.get("GEMINI_API_KEY")
            or ""
        ).strip(),
        model=model,
    )


def build_gemini_story_prompt(context_packet: dict[str, Any]) -> str:
    """
    Builds the plain-text prompt sent to Gemini.

    Args:
        context_packet: Structured context packet.

    Returns:
        Prompt text.
    """

    packet_json = json.dumps(context_packet, indent=2)

    return (
        "You are the AI narrator for AI Adventure.\n"
        "Use only the structured context packet below as confirmed adventure state.\n"
        "The Python application is the source of truth for state. You may suggest "
        "events, but do not claim that durable state changed unless an event is "
        "suggested for validation.\n\n"
        "NPC knowledge boundary:\n"
        "- The narrator can see the full context packet, but NPCs cannot.\n"
        "- NPCs must not reference private player state such as exact inventory, "
        "currency, flags, quests, hidden history, recent off-screen actions, or "
        "inner thoughts unless they observed it, were told it, or have explicit "
        "NPC knowledge in state.npcs.relevant.\n"
        "- NPCs may infer from visible behavior, but uncertain inferences must sound "
        "uncertain. For example, a bartender may notice careful coin-counting, but "
        "must not know that a coin is the player's last coin unless the player says "
        "so or the bartender saw the purse emptied.\n"
        "- When introducing a meaningful new NPC, suggest NpcUpsertedEvent in events "
        "with the NPC's internal name, player-visible display_name, role, location, "
        "public description, and plausible knowledge scope. display_name is the name "
        "shown in the NPCs tab; use a generic label such as 'Shady Character' when "
        "the player has not learned the NPC's actual name or role.\n"
        "- The events array may contain multiple events with the same type. If the "
        "current turn introduces multiple distinct meaningful NPCs, suggest one "
        "NpcUpsertedEvent for each of them instead of only the first one.\n"
        "- NpcUpsertedEvent.player_facing_information is shown directly in the NPCs "
        "tab. Never put secret identities, hidden motives, mystery solutions, private "
        "plans, or GM-only facts in player_facing_information. Store hidden NPC or "
        "mystery information with private fields or SecretAddedEvent instead.\n\n"
        "Return one JSON object and no surrounding Markdown. The object must match "
        "this shape:\n"
        "{\n"
        '  "response": "Player-facing narration only, with no legacy tags.",\n'
        '  "suggested_actions": ["Action option 1", "Action option 2", "Action option 3"],\n'
        '  "events": [],\n'
        '  "out_of_game": false\n'
        "}\n\n"
        "Rules:\n"
        "- response must be a non-empty string.\n"
        "- response must not contain legacy double-bracket tags.\n"
        "- suggested_actions must be a list, even when empty.\n"
        "- events must be a list, even when empty.\n"
        "- events may include multiple entries of the same event type when multiple "
        "distinct state changes happen in the same turn.\n"
        "- If suggesting events, use the event_shape, known_event_types, and selected event contracts from the packet.\n"
        "- Do not invent hidden state, inventory, recipes, or flags as confirmed facts.\n\n"
        "Context packet:\n"
        f"{packet_json}"
    )


def parse_gemini_story_response(raw_text: str) -> AiNarrationResult:
    """
    Parses Gemini narration output.

    Args:
        raw_text: Raw Gemini response text.

    Returns:
        Parsed narration result. Non-JSON output is kept as narrative text.
    """

    clean_text = _strip_json_fence(raw_text.strip())

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError:
        LOGGER.warning("Gemini returned non-JSON narration. Using raw text fallback.")
        return AiNarrationResult(narrative_text=raw_text.strip(), raw_text=raw_text)

    if not isinstance(data, dict):
        LOGGER.warning("Gemini JSON response was not an object. Using raw text fallback.")
        return AiNarrationResult(narrative_text=raw_text.strip(), raw_text=raw_text)

    response_text = data.get("response", data.get("narrative_text"))

    if not isinstance(response_text, str) or not response_text.strip():
        LOGGER.warning("Gemini JSON response omitted response text.")
        response_text = "The narrator has no clear response."

    raw_actions = data.get("suggested_actions", [])

    if not isinstance(raw_actions, list):
        LOGGER.warning("Gemini suggested_actions was not a list. Ignoring it.")
        raw_actions = []

    suggested_actions = [
        str(action).strip()
        for action in raw_actions
        if str(action).strip()
    ]

    raw_events = data.get("events", data.get("suggested_events", []))

    if not isinstance(raw_events, list):
        LOGGER.warning("Gemini events was not a list. Ignoring it.")
        raw_events = []

    suggested_events = [
        event for event in raw_events if isinstance(event, dict)
    ]
    event_types = [
        str(event.get("type", "UnknownEvent")).strip() or "UnknownEvent"
        for event in suggested_events
    ]
    LOGGER.info(
        "Gemini parsed %s suggested event(s): types=%s payload=%s",
        len(suggested_events),
        event_types,
        json.dumps(suggested_events, ensure_ascii=False),
    )
    narrative_text = _format_visible_response(response_text.strip(), suggested_actions)

    return AiNarrationResult(
        narrative_text=narrative_text,
        suggested_actions=suggested_actions,
        suggested_events=suggested_events,
        out_of_game=bool(data.get("out_of_game", False)),
        raw_text=raw_text,
    )


def _strip_json_fence(raw_text: str) -> str:
    """Removes a common Markdown JSON fence if the model includes one."""

    if not raw_text.startswith("```"):
        return raw_text

    lines = raw_text.splitlines()

    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()

    return raw_text


def _format_visible_response(response_text: str, suggested_actions: list[str]) -> str:
    """Combines response text and suggested actions for current UI display."""

    if not suggested_actions:
        return response_text

    action_lines = [f"- {action}" for action in suggested_actions]
    return f"{response_text}\n\n" + "\n".join(action_lines)


def _read_env_file(env_path: Path) -> dict[str, str]:
    """Reads simple KEY=VALUE pairs from a .env file without mutating os.environ."""

    if not env_path.exists():
        return {}

    values: dict[str, str] = {}

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        LOGGER.exception("Failed to read .env file at %s.", env_path)
        return values

    for line in lines:
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()

        if not key:
            continue

        values[key] = value.strip().strip("\"'")

    return values
