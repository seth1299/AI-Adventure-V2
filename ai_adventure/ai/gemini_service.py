from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_adventure.currency import normalize_currency_denominations
from ai_adventure.locations import clean_player_location_name


LOGGER = logging.getLogger(__name__)


DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
KNOWN_TEXT_MODELS = {
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
}

KNOWN_EVENT_TYPE_NAMES = [
    "StatusUpdatedEvent",
    "SkillCheckRequestedEvent",
    "SkillUpsertedEvent",
    "SkillXpAddedEvent",
    "InventoryItemAddedEvent",
    "InventoryItemRemovedEvent",
    "InventoryItemModifiedEvent",
    "RecipeDiscoveredEvent",
    "ReagentDiscoveredEvent",
    "CurrencyChangedEvent",
    "CurrencyDefinedEvent",
    "MusicChangedEvent",
    "FlagSetEvent",
    "LocationChangedEvent",
    "PlayerNoteAddedEvent",
    "WorldLoreAddedEvent",
    "WorldLoreChangedEvent",
    "WorldLoreUpdatedEvent",
    "QuestAddedEvent",
    "QuestCompletedEvent",
    "ActiveTaskUpsertedEvent",
    "ActiveTaskUpdatedEvent",
    "ActiveTaskCompletedEvent",
    "SpellLearnedEvent",
    "NpcUpsertedEvent",
    "NpcKnowledgeAddedEvent",
]
STRING_LIST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
}
NONEMPTY_STRING_LIST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "minItems": 1,
}
INT_OR_AUTO_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "integer"},
        {"type": "string", "enum": ["AUTO", "SAME", "SKIP"]},
    ]
}
INT_OR_SKIP_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "integer"},
        {"type": "string", "enum": ["SAME", "SKIP"]},
    ]
}
JSON_PRIMITIVE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
    ]
}


def _event_response_schema(
    event_type: str,
    properties: dict[str, Any],
    required: list[str],
    *,
    description: str = "",
) -> dict[str, Any]:
    """Builds one strict event schema branch."""

    return {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": [event_type],
                "description": description or event_type,
            },
            "payload": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
        "required": ["type", "payload"],
        "additionalProperties": False,
    }


EVENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        _event_response_schema(
            "StatusUpdatedEvent",
            {
                "location": {"type": "string"},
                "minutes_passed": INT_OR_AUTO_SCHEMA,
                "weather": {"type": "string"},
            },
            ["location", "minutes_passed", "weather"],
            description="Updates location, weather, and elapsed time.",
        ),
        _event_response_schema(
            "LocationChangedEvent",
            {
                "location": {"type": "string"},
                "minutes_passed": INT_OR_AUTO_SCHEMA,
                "weather": {"type": "string"},
            },
            ["location"],
            description="Legacy-compatible status update focused on location.",
        ),
        _event_response_schema(
            "SkillCheckRequestedEvent",
            {
                "skill_name": {"type": "string"},
                "dc": {"type": "integer", "minimum": 1},
                "difficulty": {"type": "string"},
            },
            ["skill_name"],
        ),
        _event_response_schema(
            "SkillUpsertedEvent",
            {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "level": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            ["name", "description", "level"],
        ),
        _event_response_schema(
            "SkillXpAddedEvent",
            {
                "skill_name": {"type": "string"},
                "xp_amount": {"type": "integer", "minimum": 1},
            },
            ["skill_name", "xp_amount"],
            description="Awards XP to an existing skill. Do not use skill_id.",
        ),
        _event_response_schema(
            "InventoryItemAddedEvent",
            {
                "item_type": {"type": "string"},
                "item_name": {"type": "string"},
                "description": {"type": "string"},
                "amount": {"type": "integer", "minimum": 1},
                "value_base_units": {"type": "integer", "minimum": 1},
            },
            ["item_type", "item_name", "description", "amount", "value_base_units"],
        ),
        _event_response_schema(
            "InventoryItemRemovedEvent",
            {
                "item_name": {"type": "string"},
                "amount": {"type": "integer", "minimum": 1},
            },
            ["item_name", "amount"],
        ),
        _event_response_schema(
            "InventoryItemModifiedEvent",
            {
                "target_name": {"type": "string"},
                "new_name": {"type": "string"},
                "new_description": {"type": "string"},
                "new_amount": INT_OR_SKIP_SCHEMA,
                "new_value_base_units": INT_OR_SKIP_SCHEMA,
            },
            ["target_name"],
        ),
        _event_response_schema(
            "RecipeDiscoveredEvent",
            {
                "name": {"type": "string"},
                "ingredients": NONEMPTY_STRING_LIST_SCHEMA,
                "result": {"type": "string"},
                "motions": STRING_LIST_SCHEMA,
                "virtues": STRING_LIST_SCHEMA,
                "notes": {"type": "string"},
            },
            ["name", "ingredients", "result"],
        ),
        _event_response_schema(
            "ReagentDiscoveredEvent",
            {
                "name": {"type": "string"},
                "material_type": {"type": "string"},
                "qualities": NONEMPTY_STRING_LIST_SCHEMA,
                "motions": NONEMPTY_STRING_LIST_SCHEMA,
                "virtues": NONEMPTY_STRING_LIST_SCHEMA,
                "uses": NONEMPTY_STRING_LIST_SCHEMA,
                "notes": {"type": "string"},
            },
            ["name", "material_type", "qualities", "motions", "virtues", "uses", "notes"],
            description="Stores a structured alchemy reagent; name-only payloads are incomplete.",
        ),
        _event_response_schema(
            "CurrencyChangedEvent",
            {
                "base_unit_amount": {
                    "type": "integer",
                    "description": (
                        "Required net money change in the world's smallest currency "
                        "unit. Negative for spending, positive for gains."
                    ),
                }
            },
            ["base_unit_amount"],
        ),
        _event_response_schema(
            "CurrencyDefinedEvent",
            {
                "name": {"type": "string"},
                "plural_name": {"type": "string"},
                "base_unit_value": {"type": "integer", "minimum": 1},
            },
            ["name", "base_unit_value"],
        ),
        _event_response_schema(
            "MusicChangedEvent",
            {"filename": {"type": "string"}},
            ["filename"],
        ),
        _event_response_schema(
            "FlagSetEvent",
            {
                "key": {"type": "string"},
                "value": JSON_PRIMITIVE_SCHEMA,
            },
            ["key", "value"],
        ),
        _event_response_schema(
            "PlayerNoteAddedEvent",
            {"content": {"type": "string"}},
            ["content"],
        ),
        _event_response_schema(
            "WorldLoreAddedEvent",
            {
                "section": {"type": "string"},
                "key": {"type": "string"},
                "text": {"type": "string"},
            },
            ["section", "key", "text"],
        ),
        _event_response_schema(
            "WorldLoreChangedEvent",
            {
                "section": {"type": "string"},
                "key": {"type": "string"},
                "replacement_lore": {"type": "string"},
            },
            ["section", "key", "replacement_lore"],
        ),
        _event_response_schema(
            "WorldLoreUpdatedEvent",
            {
                "section": {"type": "string"},
                "key": {"type": "string"},
                "replacement_lore": {"type": "string"},
            },
            ["section", "key", "replacement_lore"],
        ),
        _event_response_schema(
            "QuestAddedEvent",
            {
                "name": {"type": "string"},
                "giver": {"type": "string"},
                "description": {"type": "string"},
                "turn_in": {"type": "string"},
                "reward": {"type": "string"},
                "notes": {"type": "string"},
            },
            ["name", "description"],
        ),
        _event_response_schema(
            "QuestCompletedEvent",
            {
                "name": {"type": "string"},
                "notes": {"type": "string"},
                "resolution": {"type": "string"},
                "outcome": {"type": "string"},
            },
            ["name"],
        ),
        _event_response_schema(
            "ActiveTaskUpsertedEvent",
            {
                "name": {"type": "string"},
                "category": {"type": "string"},
                "status": {"type": "string"},
                "description": {"type": "string"},
                "requester": {"type": "string"},
                "location": {"type": "string"},
                "reward": {"type": "string"},
                "due_date": {"type": "string"},
                "notes": {"type": "string"},
            },
            ["name", "description"],
        ),
        _event_response_schema(
            "ActiveTaskUpdatedEvent",
            {
                "name": {"type": "string"},
                "category": {"type": "string"},
                "status": {"type": "string"},
                "description": {"type": "string"},
                "requester": {"type": "string"},
                "location": {"type": "string"},
                "reward": {"type": "string"},
                "due_date": {"type": "string"},
                "notes": {"type": "string"},
            },
            ["name"],
        ),
        _event_response_schema(
            "ActiveTaskCompletedEvent",
            {
                "name": {"type": "string"},
                "notes": {"type": "string"},
            },
            ["name"],
        ),
        _event_response_schema(
            "SpellLearnedEvent",
            {
                "name": {"type": "string"},
                "level": {"type": "integer", "minimum": 0, "maximum": 9},
                "school": {"type": "string"},
                "description": {"type": "string"},
            },
            ["name"],
        ),
        _event_response_schema(
            "NpcUpsertedEvent",
            {
                "npc_id": {"type": "string"},
                "name": {"type": "string"},
                "display_name": {"type": "string"},
                "role": {"type": "string"},
                "location": {"type": "string"},
                "public_description": {"type": "string"},
                "player_facing_information": {"type": "string"},
                "knowledge_scope": STRING_LIST_SCHEMA,
                "known_facts": STRING_LIST_SCHEMA,
                "disposition": {"type": "string"},
            },
            ["display_name", "player_facing_information"],
        ),
        _event_response_schema(
            "NpcKnowledgeAddedEvent",
            {
                "npc_id": {"type": "string"},
                "name": {"type": "string"},
                "facts": NONEMPTY_STRING_LIST_SCHEMA,
                "role": {"type": "string"},
                "location": {"type": "string"},
            },
            ["facts"],
        ),
    ]
}
STORY_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "response": {
            "type": "string",
            "description": "Player-facing narration only, with no legacy tags.",
        },
        "suggested_actions": {
            "type": "array",
            "description": "Three or four short player-facing action options, or empty for out-of-game answers.",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "events": {
            "type": "array",
            "description": "Structured event suggestions. Empty when no state change is proposed.",
            "items": EVENT_RESPONSE_SCHEMA,
        },
        "out_of_game": {
            "type": "boolean",
            "description": "True only when the response is fully out-of-game.",
        },
    },
    "required": ["response", "suggested_actions", "events", "out_of_game"],
    "additionalProperties": False,
}
NEW_GAME_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selected_genre": {"type": "string"},
        "world_summary": {"type": "string"},
        "world_lore": {
            "type": "object",
            "description": "Player-known lore grouped by category and durable entry name.",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        "start_location": {"type": "string"},
        "starting_calendar": {
            "type": "object",
            "properties": {
                "elapsed_minutes": {"type": "integer"},
                "year": {"type": "integer"},
                "month_name": {"type": "string"},
                "month_number": {"type": "integer"},
                "season_name": {"type": "string"},
                "season_hint": {"type": "string"},
                "day_of_month": {"type": "integer"},
                "time_of_day_minutes": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "weather": {"type": "string"},
        "character": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "appearance": {"type": "string"},
                "backstory": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name", "appearance", "backstory", "notes"],
            "additionalProperties": False,
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "level": {"type": "integer", "minimum": 1},
                },
                "required": ["name", "description", "level"],
                "additionalProperties": False,
            },
        },
        "starting_items": {
            "type": "array",
            "minItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1},
                    "description": {"type": "string"},
                    "value_base_units": {"type": "integer", "minimum": 0},
                },
                "required": [
                    "name",
                    "category",
                    "quantity",
                    "description",
                    "value_base_units",
                ],
                "additionalProperties": False,
            },
        },
        "currency_denominations": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "plural_name": {"type": "string"},
                    "value": {"type": "integer", "minimum": 1},
                },
                "required": ["name", "plural_name", "value"],
                "additionalProperties": False,
            },
        },
        "currency_description": {"type": "string"},
        "starting_currency_balance_base_units": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Player character's starting money, stored in game_state/currency.balance "
                "as base currency units."
            ),
        },
        "introductory_message": {"type": "string"},
        "events": {"type": "array", "items": EVENT_RESPONSE_SCHEMA},
    },
    "required": [
        "selected_genre",
        "world_summary",
        "world_lore",
        "start_location",
        "starting_calendar",
        "weather",
        "character",
        "skills",
        "starting_items",
        "currency_denominations",
        "currency_description",
        "starting_currency_balance_base_units",
        "introductory_message",
        "events",
    ],
    "additionalProperties": False,
}

UNCERTAIN_ACTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Alchemy": (
        "alchemy",
        "brew",
        "distill",
        "elixir",
        "experiment",
        "identify reagent",
        "mix",
        "potion",
        "prepare",
        "recipe",
        "reagent",
        "tincture",
    ),
    "Foraging": (
        "basket",
        "bounty",
        "brimming",
        "collection",
        "forage",
        "gather",
        "geological find",
        "harvest",
        "herb",
        "mushroom",
        "plant",
        "search for",
        "specimen",
    ),
    "Fieldcraft": (
        "camp",
        "flora",
        "forage",
        "harvest",
        "hunt",
        "scout",
        "track",
        "trail",
        "wild",
    ),
    "Investigation": (
        "clue",
        "examine",
        "inspect",
        "investigate",
        "research",
        "search",
        "study",
    ),
    "Awareness": (
        "listen",
        "look for",
        "notice",
        "observe",
        "scan",
        "spot",
        "watch",
    ),
    "Persuasion": (
        "ask",
        "bargain",
        "convince",
        "haggle",
        "negotiate",
        "persuade",
    ),
    "Stealth": (
        "hide",
        "sneak",
        "stealth",
        "steal",
    ),
    "Athletics": (
        "balance",
        "climb",
        "jump",
        "lift",
        "run",
        "swim",
    ),
    "Melee": (
        "attack",
        "block",
        "duel",
        "fight",
        "parry",
        "strike",
    ),
}
TRIVIAL_ACTION_KEYWORDS = {
    "close",
    "go",
    "head",
    "leave",
    "look around",
    "move",
    "open",
    "return",
    "talk",
    "walk",
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


@dataclass(frozen=True)
class AiWorldSetupResult:
    """Parsed result from an AI new-game world setup request."""

    world_summary: str
    introductory_message: str
    start_location: str = ""
    starting_calendar: dict[str, Any] = field(default_factory=dict)
    start_weather: str = ""
    selected_genre: str = ""
    world_lore: dict[str, dict[str, str]] = field(default_factory=dict)
    finalized_character: dict[str, str] = field(default_factory=dict)
    finalized_skills: list[dict[str, Any]] = field(default_factory=list)
    finalized_starter_items: list[dict[str, Any]] = field(default_factory=list)
    finalized_currency_denominations: list[dict[str, Any]] = field(default_factory=list)
    finalized_currency_description: str = ""
    finalized_starting_currency_balance_base_units: int | None = None
    suggested_events: list[dict[str, Any]] = field(default_factory=list)
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
            config=_structured_output_config(STORY_RESPONSE_JSON_SCHEMA),  # type: ignore[arg-type]
        )
        raw_text = str(getattr(response, "text", "") or "").strip()
        LOGGER.info("Gemini raw story response:\n%s", raw_text)

        if not raw_text:
            LOGGER.warning("Gemini returned an empty story response.")
            return AiNarrationResult(
                narrative_text="The narrator falls silent for a moment.",
                raw_text=raw_text,
            )

        result = parse_gemini_story_response(raw_text)
        result = _ensure_skill_check_for_uncertain_player_command(result, context_packet)
        result = _ensure_inventory_for_collected_reagents(result, context_packet)
        return _ensure_inventory_for_narrated_collection(result, context_packet)

    def generate_new_game_world(
        self,
        setup_packet: dict[str, Any],
    ) -> AiWorldSetupResult:
        """
        Sends a new-game setup packet to Gemini.

        Args:
            setup_packet: Structured setup packet.

        Returns:
            Parsed world setup result.
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

        prompt = build_gemini_new_game_prompt(setup_packet)
        client = genai.Client(api_key=self.settings.api_key)

        LOGGER.info("Sending new-game setup packet to Gemini model %s.", self.settings.model)
        response = client.models.generate_content(
            model=self.settings.model,
            contents=prompt,
            config=_structured_output_config(NEW_GAME_RESPONSE_JSON_SCHEMA),  # type: ignore[arg-type]
        )
        raw_text = str(getattr(response, "text", "") or "").strip()
        LOGGER.info("Gemini raw new-game response:\n%s", raw_text)

        if not raw_text:
            LOGGER.warning("Gemini returned an empty new-game response.")
            return AiWorldSetupResult(
                world_summary="The world is still taking shape.",
                introductory_message="The adventure begins.\n\nWhat do you do now?",
                raw_text=raw_text,
            )

        return parse_gemini_new_game_response(raw_text)


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
    banned_terms = _banned_terms_from_context(context_packet)
    banned_terms_text = ", ".join(banned_terms) if banned_terms else "(none provided)"

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
        "with the NPC's internal name, player-visible display_name, internal role, "
        "location, public description, player_facing_information, and plausible "
        "knowledge scope. display_name is the name shown in the NPCs tab; use a "
        "generic label such as 'Shady Character' when the player has not learned "
        "the NPC's actual name or role. role is for AI memory and should not be "
        "treated as the player-facing summary.\n"
        "- Before creating an NPC, inspect state.npcs.relevant. If the person is "
        "already listed there, reuse that exact npc_id/internal identifier and "
        "update the existing profile instead of inventing a second identifier. "
        "A different wording of the same role at the same location is not a new NPC.\n"
        "- The events array may contain multiple events with the same type. If the "
        "current turn introduces multiple distinct meaningful NPCs, suggest one "
        "NpcUpsertedEvent for each of them instead of only the first one.\n"
        "- NpcUpsertedEvent.player_facing_information is shown directly in the NPCs "
        "tab under Notes. Write it as player-known information about a person, not "
        "as a mechanical service role. Never put secret identities, hidden motives, "
        "mystery solutions, private plans, or GM-only facts in "
        "player_facing_information. Do not put hidden NPC or mystery information "
        "in suggested events unless the player has learned it.\n\n"
        "Creative naming boundary:\n"
        "- If the context packet includes creative_ideas, treat those examples as "
        "high-priority style seeds for newly invented names and setting details.\n"
        "- Prefer creative_ideas examples or close stylistic relatives over broad "
        "training-data fantasy defaults, especially for NPCs, settlements, taverns, "
        "factions, religions, regions, ingredients, species, food, and drinks.\n"
        "- Never use creative_ideas.banned_terms, close spelling variants, "
        "hyphenation variants, or obvious reskins for newly invented proper nouns. "
        "Banned terms may appear only when already established in saved state or "
        "explicitly provided by the player.\n\n"
        f"Exact banned proper nouns for newly invented content: {banned_terms_text}\n\n"
        "Return one JSON object and no surrounding Markdown. The API response "
        "schema defines the required top-level fields.\n\n"
        "Rules:\n"
        "- response must be a non-empty string.\n"
        "- response must not contain legacy double-bracket tags.\n"
        "- Spoken dialogue must use double quotation marks around the speaker's "
        "full spoken sentence or paragraph. Do not use single quotation marks as "
        "the outer boundary of dialogue. Use single quotation marks only when an "
        "already-double-quoted speaker mentions a named item, title, shop, place, "
        "phrase, nickname, inscription, or other quoted specific inside that "
        "dialogue.\n"
        "- suggested_actions must be a list, even when empty.\n"
        "- events must be a list, even when empty.\n"
        "- events may include multiple entries of the same event type when multiple "
        "distinct state changes happen in the same turn.\n"
        "- If suggesting events, use the event_shape, known_event_types, and selected event contracts from the packet.\n"
        "- For uncertain actions, suggest SkillCheckRequestedEvent before any "
        "final outcome event. This is required for foraging, harvesting, "
        "searching, researching, identifying, crafting, alchemy experiments, "
        "persuasion, stealth, combat, and named skill use unless the action is "
        "trivial and risk-free. Do not use SkillXpAddedEvent as a substitute "
        "for a check.\n"
        "- Every InventoryItemAddedEvent payload must include value_base_units "
        "as an integer of at least 1.\n"
        "- ReagentDiscoveredEvent records Alchemy Notebook knowledge only. If "
        "the player physically collects, harvests, picks up, or stores that "
        "reagent, also suggest InventoryItemAddedEvent for the same reagent.\n"
        "- If the narration says the player physically gains, collects, harvests, "
        "finds and keeps, or fills a basket/container with usable items, also "
        "suggest InventoryItemAddedEvent for those items. Do not describe a "
        "successful bounty, haul, stash, brimming basket, or collected specimens "
        "without adding inventory.\n"
        "- Currency is stored as one integer, state.currency.balance_base_units, "
        "which is loaded from game_state/currency.balance, not as coin items in "
        "inventory. For a completed purchase, sale, fee, reward, refund, or other "
        "money movement, suggest CurrencyChangedEvent with payload.base_unit_amount "
        "as the one net money change. Never use net_base_unit_amount. If the "
        "player buys an item, also suggest the InventoryItemAddedEvent for that "
        "item; do not create coin inventory items for payment or change.\n"
        "- Do not invent hidden state, inventory, recipes, or flags as confirmed facts.\n\n"
        "Context packet:\n"
        f"{packet_json}"
    )


def build_gemini_new_game_prompt(setup_packet: dict[str, Any]) -> str:
    """
    Builds the plain-text prompt for new-game world synthesis.

    Args:
        setup_packet: Structured setup packet.

    Returns:
        Prompt text.
    """

    packet_json = json.dumps(setup_packet, indent=2)

    return (
        "You are creating the initial world setup for AI Adventure.\n"
        "Use only the structured setup packet below as confirmed setup input. "
        "Synthesize the player's choices into a coherent playable world.\n\n"
        "Requirements:\n"
        "- Return one JSON object and no surrounding Markdown.\n"
        "- If the setup packet includes fields_requiring_ai_invention, treat those "
        "fields as blank/default placeholders rather than confirmed facts. Invent "
        "coherent specifics for them, while preserving any custom player-provided "
        "values that are not listed there.\n"
        "- If the setup packet includes creative_ideas, treat them as high-priority "
        "style seeds for invented names and setting details. Strongly prefer these "
        "examples or close stylistic relatives over broad training-data fantasy "
        "defaults, while adapting them so the new game feels distinct.\n"
        "- Never use creative_ideas.banned_terms, close spelling variants, "
        "hyphenation variants, or obvious reskins for newly invented proper nouns. "
        "This includes the player character name, NPC names, locations, taverns, "
        "regions, factions, religions, shops, guilds, and landmarks. Banned terms "
        "may appear only when explicitly provided by the player as confirmed setup "
        "input.\n"
        "- If the setup packet includes character_generation_guidance, follow its "
        "gender_presentation_hint when inventing blank/default player character "
        "fields. A blank/default player character does not imply male. Vary gender "
        "presentation, pronouns, names, appearance, and backstory across new games, "
        "and use creative_ideas.player_character_name_examples as a balanced name "
        "pool when useful.\n"
        "- If setup.specified_genre is blank/default, choose a specific genre or "
        "premise and return it as selected_genre. Do not default to fantasy; "
        "genre_generation_guidance.genre_hint is available as inspiration. If the "
        "player provided setup.specified_genre, preserve it as selected_genre.\n"
        "- Treat the player character's class, profession, backstory, and skills as "
        "facts about the player character, not as instructions that the entire "
        "world must share the same theme. Use them to shape the character, "
        "starting inventory, personal contacts, and immediate opportunities. Do "
        "not make the city's politics, religions, economy, factions, locations, "
        "NPCs, conflicts, and mysteries all revolve around the character's "
        "specialty unless setup.game_style, setup.world_context, or "
        "setup.specified_genre explicitly requests that focus. For example, a "
        "merchant character can live in a city whose religion is about storms, "
        "ancestry, law, harvests, stars, or anything else coherent; the economy "
        "can matter without every institution being coin-themed.\n"
        "- world_summary must be a few paragraphs describing at least the basics "
        "of the world or city, prominent NPCs, locations of interest, religions, "
        "and economy.\n"
        "- world_lore must group player-known starting lore into keyed category "
        "objects where each key is the durable entry name and each value is the "
        "player-facing lore text. Include useful categories such as Locations, Religions, Economy, "
        "Culture and Laws, Factions and Guilds, Prominent NPCs, and Current Rumors "
        "when they fit the game. Do not include secrets, mystery solutions, hidden "
        "villains, or GM-only facts in world_lore.\n"
        "- introductory_message must be player-facing narration for the first "
        "scene at start_location and must end with exactly "
        "'What do you do now?'\n"
        "- introductory_message must match setup_packet.current_calendar and "
        "setup_packet.current_weather unless you intentionally return "
        "starting_calendar and/or weather fields to change the starting date, "
        "season, time, or weather. For example, do not mention autumn winds while "
        "starting_calendar/current_calendar says Spring unless you return a "
        "starting_calendar for Autumn.\n"
        "- start_location must be the actual named location where the player starts. "
        "If setup.start_location is blank/default, choose any coherent starting "
        "location for the selected genre and character. The player does not need "
        "to start in a tavern; a frozen sea, deserted island, ruined store, crime "
        "scene, crashed ship, wilderness trail, city checkpoint, or similar premise "
        "is valid when it fits. Use the same start_location consistently in "
        "introductory_message and events. Keep start_location short and broad: "
        "use the room, building, street, district, ship, campsite, or landmark "
        "name only. Put scenic details such as floor, view, nearby landmarks, "
        "weather, and exact position in introductory_message instead. Example: "
        "use \"Y/N's Office\", not \"Y/N's Office, high up near the penthouse, "
        "overlooking the Hudson River\".\n"
        "- character must finalize the player character profile. If character name, "
        "appearance, backstory, or notes are blank/default placeholders, replace "
        "them with coherent player-facing details suitable for the world. Preserve "
        "explicit custom player input.\n"
        "- skills must contain every starting skill with name, description, and level. "
        "For any skill whose name or description is blank/default/placeholder or "
        "whose setup entry has requires_ai_invention=true, invent a distinct "
        "setting-appropriate name and a concrete description matching that skill. "
        "Skill names must be generalized gameplay capabilities useful across many "
        "checks, not one-off lore phrases, proper nouns, tiny item-maintenance "
        "tasks, or narrow setting trivia. Put local flavor, culture, equipment, "
        "and backstory specifics in the description. Good shapes include "
        "Weather-Reading, Arcana, Navigation, Tinkering, Stealth, Investigation, "
        "Medicine, Performance, Persuasion, Survival, Melee, and Lore (Specific "
        "Domain). Convert specific lore skills to parenthetical domain names, such "
        "as Lore (Syndicate), Lore (Flijosha), or Lore (Merchant Law), rather than "
        "Syndicate Lore or Flijosha Observance. "
        "Never return placeholder descriptions such as 'Player-selected level 1 "
        "starting skill.' Do not reuse generic default names like Athletics, "
        "Awareness, Crafting, Fieldcraft, Investigation, Lore, Medicine, Melee, "
        "Performance, Persuasion, Primary Training, Secondary Training, Signature "
        "Expertise, Stealth, or Survival unless the player explicitly typed that "
        "name. Preserve the exact level spread from setup.skills.\n"
        "- starting_items must contain at least five concrete starting inventory "
        "items. Preserve any player-provided setup.starter_items and add fitting "
        "items as needed. If setup.starter_items is blank or sparse, invent items "
        "that fit the finalized character backstory, finalized skills, selected "
        "genre, starting location, weather, and economy. Each item must include "
        "name, category, quantity, description, and value_base_units.\n"
        "- If setup.currency_denominations is empty, currency_denominations must "
        "contain at least one and at most four concrete denominations that fit "
        "the selected genre, world, and economy. One denomination must have "
        "value=1 as the baseline unit. Other values are exchange rates measured "
        "in that baseline unit and do not need to be multiples or powers of 10. "
        "For example, fantasy worlds may use copper/silver/gold-style coinage, "
        "realistic modern worlds may use dollars, and futuristic or space worlds "
        "may use credits. If setup.currency_denominations already contains "
        "player-provided values, preserve them.\n"
        "- starting_currency_balance_base_units must be a reasonable starting money "
        "amount for the finalized character, genre, and economy. This is the "
        "player's actual starting money stored in game_state/currency.balance as "
        "one integer in the baseline currency unit. Do not create coin or purse "
        "items in starting_items to represent spendable money.\n"
        "- The API response schema defines the required output fields and event "
        "envelope. Use type and payload for each event; do not use event_type as "
        "the top-level event type key.\n"
        "- Use only player-known information in player-facing event fields.\n"
        "- Use NpcUpsertedEvent for prominent NPCs the player can know about at "
        "setup. Use ActiveTaskUpsertedEvent for initial active obligations. Use "
        "currency_denominations for initial generated money instead of "
        "CurrencyDefinedEvent. Use CurrencyDefinedEvent only when a story event "
        "establishes a new denomination after initial setup. If "
        "setup_packet.audio.valid_music_tracks is non-empty, "
        "use one MusicChangedEvent to choose fitting opening background music; "
        "its filename must exactly match one listed track. The API response "
        "schema defines the required JSON fields.\n\n"
        "Setup packet:\n"
        f"{packet_json}"
    )


def _structured_output_config(schema: dict[str, Any]) -> dict[str, Any]:
    """Builds the Gemini structured-output config for a JSON response schema."""

    return {
        "response_mime_type": "application/json",
        "response_json_schema": schema,
    }


def _banned_terms_from_context(context_packet: dict[str, Any]) -> list[str]:
    """Reads banned generated-name terms from a story context packet."""

    creative_ideas = context_packet.get("creative_ideas", {})

    if not isinstance(creative_ideas, dict):
        return []

    raw_terms = creative_ideas.get("banned_terms", [])

    if not isinstance(raw_terms, list):
        return []

    terms: list[str] = []
    seen: set[str] = set()

    for raw_term in raw_terms:
        term = str(raw_term).strip()
        folded = term.casefold()

        if term and folded not in seen:
            terms.append(term)
            seen.add(folded)

    return terms


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

    _log_json_schema_warnings(data, STORY_RESPONSE_JSON_SCHEMA, "story response")

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


def _ensure_skill_check_for_uncertain_player_command(
    result: AiNarrationResult,
    context_packet: dict[str, Any],
) -> AiNarrationResult:
    """Adds a fallback skill-check event when Gemini skips a clearly uncertain action."""

    if result.out_of_game:
        return result

    if any(_raw_event_type(event) == "SkillCheckRequestedEvent" for event in result.suggested_events):
        return result

    command = str(context_packet.get("player_command", "")).strip()
    action_text = " ".join([command, result.narrative_text]).strip()

    if not _looks_like_uncertain_action(action_text):
        return result

    skill_name = _infer_skill_check_name(action_text, context_packet)

    if not skill_name:
        return result

    skill_check_event = {
        "type": "SkillCheckRequestedEvent",
        "payload": {
            "skill_name": skill_name,
            "difficulty": "normal",
        },
    }
    filtered_events = [
        event
        for event in result.suggested_events
        if _raw_event_type(event) != "SkillXpAddedEvent"
    ]
    LOGGER.warning(
        "Gemini omitted SkillCheckRequestedEvent for uncertain player command %r; "
        "injecting %s check.",
        command,
        skill_name,
    )

    return AiNarrationResult(
        narrative_text=result.narrative_text,
        suggested_actions=result.suggested_actions,
        suggested_events=[skill_check_event, *filtered_events],
        out_of_game=result.out_of_game,
        raw_text=result.raw_text,
    )


def _ensure_inventory_for_collected_reagents(
    result: AiNarrationResult,
    context_packet: dict[str, Any],
) -> AiNarrationResult:
    """Adds inventory events for reagents Gemini says the player physically collected."""

    if result.out_of_game:
        return result

    reagent_events = [
        event
        for event in result.suggested_events
        if _raw_event_type(event) == "ReagentDiscoveredEvent"
    ]

    if not reagent_events:
        return result

    collection_text = " ".join(
        [
            result.narrative_text,
            str(context_packet.get("player_command", "")),
        ]
    )

    if not _text_suggests_physical_collection(collection_text):
        return result

    event_names_with_inventory = {
        _event_payload_text(event, "item_name", "name").casefold()
        for event in result.suggested_events
        if _raw_event_type(event) == "InventoryItemAddedEvent"
    }
    updated_events: list[dict[str, Any]] = []
    added_events: list[dict[str, Any]] = []

    for event in result.suggested_events:
        updated_events.append(event)

        if _raw_event_type(event) != "ReagentDiscoveredEvent":
            continue

        payload = event.get("payload", {})

        if not isinstance(payload, dict):
            continue

        name = str(payload.get("name", payload.get("reagent_name", ""))).strip()

        if not name or name.casefold() in event_names_with_inventory:
            continue

        inventory_event = {
            "type": "InventoryItemAddedEvent",
            "payload": {
                "item_type": str(payload.get("material_type", "")).strip() or "Reagent",
                "item_name": name,
                "description": _reagent_inventory_description(payload),
                "amount": 1,
                "value_base_units": 1,
            },
        }
        updated_events.append(inventory_event)
        added_events.append(inventory_event)
        event_names_with_inventory.add(name.casefold())

    if not added_events:
        return result

    LOGGER.warning(
        "Gemini omitted InventoryItemAddedEvent for collected reagent(s): %s",
        [
            event["payload"]["item_name"]
            for event in added_events
        ],
    )

    return AiNarrationResult(
        narrative_text=result.narrative_text,
        suggested_actions=result.suggested_actions,
        suggested_events=updated_events,
        out_of_game=result.out_of_game,
        raw_text=result.raw_text,
    )


def _ensure_inventory_for_narrated_collection(
    result: AiNarrationResult,
    context_packet: dict[str, Any],
) -> AiNarrationResult:
    """Adds a generic inventory item when Gemini narrates loot but emits none."""

    if result.out_of_game:
        return result

    if any(_raw_event_type(event) == "InventoryItemAddedEvent" for event in result.suggested_events):
        return result

    collection_text = " ".join(
        [
            result.narrative_text,
            str(context_packet.get("player_command", "")),
        ]
    )

    if not _text_suggests_physical_collection(collection_text):
        return result

    if not _text_suggests_narrated_inventory_reward(collection_text):
        return result

    inventory_event = {
        "type": "InventoryItemAddedEvent",
        "payload": {
            "item_type": "Foraged Goods",
            "item_name": "Assorted Foraged Specimens",
            "description": _narrated_collection_description(collection_text),
            "amount": 1,
            "value_base_units": 1,
        },
    }
    LOGGER.warning(
        "Gemini narrated collected inventory without InventoryItemAddedEvent; "
        "adding Assorted Foraged Specimens."
    )

    return AiNarrationResult(
        narrative_text=result.narrative_text,
        suggested_actions=result.suggested_actions,
        suggested_events=[*result.suggested_events, inventory_event],
        out_of_game=result.out_of_game,
        raw_text=result.raw_text,
    )


def _text_suggests_physical_collection(text: str) -> bool:
    """Returns true when narration or command says the player took an item."""

    clean_text = text.casefold()
    collection_phrases = (
        "basket brimming",
        "bounty of",
        "brimming with",
        "collect",
        "collected",
        "collection",
        "gather",
        "gathered",
        "geological find",
        "geological finds",
        "harvest",
        "harvested",
        "high-quality specimen",
        "high-quality specimens",
        "pick",
        "picked",
        "stow",
        "stowed",
        "take",
        "taken",
        "tuck",
        "tucked",
        "in your basket",
        "into your basket",
        "in her basket",
        "into her basket",
    )
    return any(phrase in clean_text for phrase in collection_phrases)


def _text_suggests_narrated_inventory_reward(text: str) -> bool:
    """Returns true when narration describes a physical reward pile."""

    clean_text = text.casefold()
    reward_phrases = (
        "basket brimming",
        "bounty of",
        "brimming with",
        "into a padded pocket",
        "popped free",
        "pops free",
        "fresh, high-quality specimens",
        "quite the collection",
        "tuck the",
        "tucked the",
        "your basket is brimming",
        "you have quite the collection",
    )
    return any(phrase in clean_text for phrase in reward_phrases)


def _narrated_collection_description(text: str) -> str:
    """Builds a conservative description for fallback generic collection loot."""

    clean_text = text.casefold()

    if "flora" in clean_text and "geological" in clean_text:
        return (
            "A mixed bounty of local flora and rare geological finds gathered "
            "during foraging."
        )

    if "geological" in clean_text:
        return "Assorted geological specimens gathered during exploration."

    if "flora" in clean_text or "specimen" in clean_text:
        return "Assorted local flora and field specimens gathered during foraging."

    return "Assorted useful specimens gathered during exploration."


def _reagent_inventory_description(payload: dict[str, Any]) -> str:
    """Builds an inventory description from a reagent-discovery payload."""

    notes = str(payload.get("notes", payload.get("description", ""))).strip()

    if notes:
        return notes

    qualities = _join_payload_list(payload.get("qualities", []))
    uses = _join_payload_list(payload.get("uses", []))
    details = []

    if qualities:
        details.append(f"Qualities: {qualities}")

    if uses:
        details.append(f"Uses: {uses}")

    return "; ".join(details) or "A discovered alchemical reagent."


def _join_payload_list(value: Any) -> str:
    """Formats a payload list as comma-separated text."""

    if not isinstance(value, list):
        return ""

    return ", ".join(str(item).strip() for item in value if str(item).strip())


def _looks_like_uncertain_action(command: str) -> bool:
    """Returns true when a player command likely needs a Python-resolved check."""

    clean_command = command.strip().casefold()

    if not clean_command:
        return False

    if clean_command.startswith(("oog", "out-of-game", "out of game")):
        return False

    if "skill check" in clean_command or "roll " in clean_command:
        return True

    for keywords in UNCERTAIN_ACTION_KEYWORDS.values():
        if any(keyword in clean_command for keyword in keywords):
            return True

    command_words = set(re.findall(r"[a-zA-Z]+", clean_command))

    if command_words and command_words.issubset(TRIVIAL_ACTION_KEYWORDS):
        return False

    return False


def _infer_skill_check_name(command: str, context_packet: dict[str, Any]) -> str:
    """Infers the most relevant skill for a fallback check."""

    clean_command = command.casefold()
    known_skills = _known_skill_names(context_packet)

    for skill_name in known_skills:
        if skill_name.casefold() in clean_command:
            return skill_name

    scored_candidates: list[tuple[int, str]] = []

    for candidate, keywords in UNCERTAIN_ACTION_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in clean_command)

        if score > 0:
            scored_candidates.append((score, candidate))

    if scored_candidates:
        scored_candidates.sort(key=lambda item: (-item[0], item[1]))
        candidate = scored_candidates[0][1]
        known_match = _find_known_skill(candidate, known_skills)
        return known_match or candidate

    return known_skills[0] if known_skills else "Awareness"


def _known_skill_names(context_packet: dict[str, Any]) -> list[str]:
    """Reads known skill names from a story context packet."""

    raw_skills = (
        context_packet.get("state", {})
        .get("skills", {})
        .get("known_skills", [])
    )

    if not isinstance(raw_skills, list):
        return []

    skill_names: list[str] = []

    for raw_skill in raw_skills:
        if not isinstance(raw_skill, dict):
            continue

        name = str(raw_skill.get("name", "")).strip()

        if name:
            skill_names.append(name)

    return skill_names


def _find_known_skill(candidate: str, known_skills: list[str]) -> str:
    """Returns a known skill matching a fallback candidate."""

    candidate_folded = candidate.casefold()

    for skill_name in known_skills:
        if skill_name.casefold() == candidate_folded:
            return skill_name

    if candidate_folded == "foraging":
        for skill_name in known_skills:
            if skill_name.casefold() == "fieldcraft":
                return skill_name

    return ""


def _raw_event_type(event: dict[str, Any]) -> str:
    """Reads a raw event type string."""

    return str(event.get("type", "")).strip()


def _event_payload_text(event: dict[str, Any], *keys: str) -> str:
    """Reads the first text value from a raw event payload."""

    payload = event.get("payload", {})

    if not isinstance(payload, dict):
        return ""

    for key in keys:
        value = str(payload.get(key, "")).strip()

        if value:
            return value

    return ""


def parse_gemini_new_game_response(raw_text: str) -> AiWorldSetupResult:
    """
    Parses Gemini new-game setup output.

    Args:
        raw_text: Raw Gemini response text.

    Returns:
        Parsed new-game setup result.
    """

    clean_text = _strip_json_fence(raw_text.strip())

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError:
        LOGGER.warning("Gemini returned non-JSON new-game setup. Using raw text fallback.")
        return AiWorldSetupResult(
            world_summary=raw_text.strip(),
            introductory_message="The adventure begins.\n\nWhat do you do now?",
            raw_text=raw_text,
        )

    if not isinstance(data, dict):
        LOGGER.warning("Gemini new-game JSON response was not an object.")
        return AiWorldSetupResult(
            world_summary=raw_text.strip(),
            introductory_message="The adventure begins.\n\nWhat do you do now?",
            raw_text=raw_text,
        )

    _log_json_schema_warnings(data, NEW_GAME_RESPONSE_JSON_SCHEMA, "new-game response")

    world_summary = str(data.get("world_summary", "")).strip()
    selected_genre = str(
        data.get("selected_genre", data.get("genre", ""))
    ).strip()
    start_location = clean_player_location_name(data.get("start_location", ""))
    starting_calendar = _parse_new_game_starting_calendar(
        data.get("starting_calendar", data.get("calendar"))
    )
    start_weather = str(data.get("weather", data.get("start_weather", ""))).strip()
    world_lore = _parse_new_game_world_lore(data.get("world_lore", data.get("lore")))
    introductory_message = str(
        data.get("introductory_message", data.get("response", ""))
    ).strip()
    finalized_character = _parse_new_game_character(data.get("character"))
    finalized_skills = _parse_new_game_skills(data.get("skills"))
    finalized_starter_items = _parse_new_game_starter_items(
        data.get("starting_items", data.get("starter_items", data.get("inventory")))
    )
    finalized_currency_denominations = _parse_new_game_currency_denominations(data)
    finalized_currency_description = _parse_new_game_currency_description(data)
    finalized_starting_currency_balance_base_units = (
        _parse_new_game_starting_currency_balance(data)
    )
    raw_events = data.get("events", data.get("suggested_events", []))

    if not world_summary:
        LOGGER.warning("Gemini new-game setup omitted world_summary.")
        world_summary = "The world is still taking shape."

    if not introductory_message:
        LOGGER.warning("Gemini new-game setup omitted introductory_message.")
        introductory_message = "The adventure begins.\n\nWhat do you do now?"

    if not introductory_message.rstrip().endswith("What do you do now?"):
        introductory_message = f"{introductory_message.rstrip()}\n\nWhat do you do now?"
    introductory_message = format_story_message(introductory_message)

    if not isinstance(raw_events, list):
        LOGGER.warning("Gemini new-game events was not a list. Ignoring it.")
        raw_events = []

    suggested_events = [
        event for event in raw_events if isinstance(event, dict)
    ]
    LOGGER.info(
        "Gemini parsed %s new-game event(s): payload=%s",
        len(suggested_events),
        json.dumps(suggested_events, ensure_ascii=False),
    )

    return AiWorldSetupResult(
        world_summary=world_summary,
        introductory_message=introductory_message,
        start_location=start_location,
        starting_calendar=starting_calendar,
        start_weather=start_weather,
        selected_genre=selected_genre,
        world_lore=world_lore,
        finalized_character=finalized_character,
        finalized_skills=finalized_skills,
        finalized_starter_items=finalized_starter_items,
        finalized_currency_denominations=finalized_currency_denominations,
        finalized_currency_description=finalized_currency_description,
        finalized_starting_currency_balance_base_units=(
            finalized_starting_currency_balance_base_units
        ),
        suggested_events=suggested_events,
        raw_text=raw_text,
    )


def _parse_new_game_character(raw_character: Any) -> dict[str, str]:
    """Parses finalized new-game character data from Gemini."""

    if not isinstance(raw_character, dict):
        return {}

    character: dict[str, str] = {}

    for key in ["name", "appearance", "backstory", "notes"]:
        value = str(raw_character.get(key, "")).strip()

        if value:
            character[key] = value

    return character


def _parse_new_game_world_lore(raw_lore: Any) -> dict[str, dict[str, str]]:
    """Parses grouped player-facing world lore from Gemini."""

    if not isinstance(raw_lore, dict):
        return {}

    world_lore: dict[str, dict[str, str]] = {}

    for raw_category, raw_entries in raw_lore.items():
        category = str(raw_category).strip()

        if not category:
            continue

        if isinstance(raw_entries, dict):
            entries = {
                str(key).strip(): str(value).strip()
                for key, value in raw_entries.items()
                if str(key).strip() and str(value).strip()
            }
        elif isinstance(raw_entries, str):
            clean_entry = raw_entries.strip()
            entries = {_derive_lore_key(clean_entry): clean_entry} if clean_entry else {}
        elif isinstance(raw_entries, list):
            entries = {}

            for entry in raw_entries:
                clean_entry = str(entry).strip()

                if clean_entry:
                    entries[_derive_lore_key(clean_entry)] = clean_entry
        else:
            entries = {}

        if entries:
            world_lore[category] = entries

    return world_lore


def _derive_lore_key(text: str) -> str:
    """Derives a lore key from list-shaped legacy AI lore."""

    return str(text).split(":", 1)[0].strip()[:80]


def _parse_new_game_starting_calendar(raw_calendar: Any) -> dict[str, Any]:
    """Parses optional AI-selected starting calendar fields."""

    if not isinstance(raw_calendar, dict):
        return {}

    calendar: dict[str, Any] = {}

    for key in [
        "elapsed_minutes",
        "year",
        "month_name",
        "month_number",
        "season_name",
        "season_hint",
        "day_of_month",
        "time_of_day_minutes",
    ]:
        value = raw_calendar.get(key)

        if isinstance(value, str):
            value = value.strip()

        if value not in {"", None}:
            calendar[key] = value

    return calendar


def _parse_new_game_skills(raw_skills: Any) -> list[dict[str, Any]]:
    """Parses finalized new-game skills from Gemini."""

    if not isinstance(raw_skills, list):
        return []

    skills: list[dict[str, Any]] = []

    for raw_skill in raw_skills:
        if not isinstance(raw_skill, dict):
            continue

        name = str(raw_skill.get("name", "")).strip()
        description = str(raw_skill.get("description", "")).strip()

        try:
            level = int(raw_skill.get("level", 0))
        except (TypeError, ValueError):
            level = 0

        if name and description and level > 0:
            skills.append(
                {
                    "name": name,
                    "description": description,
                    "level": level,
                }
            )

    return skills


def _parse_new_game_starter_items(raw_items: Any) -> list[dict[str, Any]]:
    """Parses finalized new-game starter inventory from Gemini."""

    if not isinstance(raw_items, list):
        return []

    items: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        name = str(raw_item.get("name", raw_item.get("item_name", ""))).strip()

        if not name or name.casefold() in seen_names:
            continue

        try:
            quantity = int(raw_item.get("quantity", raw_item.get("amount", 1)))
        except (TypeError, ValueError):
            quantity = 1

        try:
            value_base_units = int(
                raw_item.get(
                    "value_base_units",
                    raw_item.get("base_unit_value", raw_item.get("value", 0)),
                )
            )
        except (TypeError, ValueError):
            value_base_units = 0

        items.append(
            {
                "name": name,
                "category": str(raw_item.get("category", "Item")).strip() or "Item",
                "quantity": max(1, quantity),
                "description": str(raw_item.get("description", "")).strip(),
                "value_base_units": max(0, value_base_units),
            }
        )
        seen_names.add(name.casefold())

    return items


def _parse_new_game_currency_denominations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parses AI-finalized new-game currency denominations."""

    raw_denominations = data.get("currency_denominations", data.get("denominations"))

    if raw_denominations is None:
        raw_currency = data.get("currency", data.get("economy"))

        if isinstance(raw_currency, dict):
            raw_denominations = raw_currency.get(
                "denominations",
                raw_currency.get("currency_denominations"),
            )

    return normalize_currency_denominations(
        raw_denominations,
        fallback_denominations=[],
        max_denominations=4,
    )


def _parse_new_game_currency_description(data: dict[str, Any]) -> str:
    """Parses AI-finalized new-game currency description."""

    for key in ["currency_description", "currency_notes", "economy_description"]:
        value = str(data.get(key, "")).strip()

        if value:
            return value

    raw_currency = data.get("currency", data.get("economy"))

    if isinstance(raw_currency, dict):
        for key in ["description", "notes", "currency_description"]:
            value = str(raw_currency.get(key, "")).strip()

            if value:
                return value

    return ""


def _parse_new_game_starting_currency_balance(data: dict[str, Any]) -> int | None:
    """Parses AI-finalized starting money for game_state/currency.balance."""

    for key in [
        "starting_currency_balance_base_units",
        "currency_balance_base_units",
        "starting_money_base_units",
    ]:
        value = data.get(key)

        if value in {"", None}:
            continue

        try:
            return max(0, int(value)) # type: ignore
        except (TypeError, ValueError):
            LOGGER.warning("Gemini returned invalid starting currency balance: %r", value)
            return None

    raw_currency = data.get("currency")

    if isinstance(raw_currency, dict):
        for key in ["balance_base_units", "starting_balance_base_units"]:
            value = raw_currency.get(key)

            if value in {"", None}:
                continue

            try:
                return max(0, int(value)) # type: ignore
            except (TypeError, ValueError):
                LOGGER.warning(
                    "Gemini returned invalid nested starting currency balance: %r",
                    value,
                )
                return None

    return None


def _log_json_schema_warnings(
    data: Any,
    schema: dict[str, Any],
    label: str,
) -> None:
    """Logs local schema-shape warnings after Gemini structured output returns."""

    errors = _json_schema_shape_errors(data, schema)

    if errors:
        LOGGER.warning(
            "Gemini %s did not fully match the configured structured-output schema: %s",
            label,
            "; ".join(errors[:8]),
        )


def _json_schema_shape_errors(
    value: Any,
    schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Checks the JSON Schema subset used for Gemini response envelopes."""

    any_of = schema.get("anyOf")

    if isinstance(any_of, list):
        branch_errors = [
            _json_schema_shape_errors(value, branch_schema, path)
            for branch_schema in any_of
            if isinstance(branch_schema, dict)
        ]

        if any(not errors for errors in branch_errors):
            return []

        return [
            f"{path} did not match any allowed schema"
        ] + [
            error
            for errors in branch_errors[:2]
            for error in errors[:4]
        ]

    schema_type = schema.get("type")
    errors: list[str] = []

    if schema_type is not None and not _matches_json_schema_type(value, schema_type):
        return [f"{path} expected {_format_json_schema_type(schema_type)}"]

    if isinstance(value, dict):
        enum = schema.get("enum")

        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path} expected one of {enum}")

        properties = schema.get("properties", {})
        required = schema.get("required", [])

        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{path}.{key} is required")

        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    errors.extend(
                        _json_schema_shape_errors(value[key], child_schema, f"{path}.{key}")
                    )

        additional_properties = schema.get("additionalProperties", True)

        if additional_properties is False and isinstance(properties, dict):
            allowed_keys = set(properties)
            for key in value:
                if key not in allowed_keys:
                    errors.append(f"{path}.{key} is not allowed")
        elif isinstance(additional_properties, dict) and isinstance(properties, dict):
            for key, child_value in value.items():
                if key not in properties:
                    errors.extend(
                        _json_schema_shape_errors(
                            child_value,
                            additional_properties,
                            f"{path}.{key}",
                        )
                    )

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")

        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path} expected at least {min_items} item(s)")

        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path} expected at most {max_items} item(s)")

        items_schema = schema.get("items")

        if isinstance(items_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    _json_schema_shape_errors(item, items_schema, f"{path}[{index}]")
                )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        enum = schema.get("enum")

        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path} expected one of {enum}")

        minimum = schema.get("minimum")
        maximum = schema.get("maximum")

        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path} expected at least {minimum}")

        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} expected at most {maximum}")

    if isinstance(value, str):
        enum = schema.get("enum")

        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path} expected one of {enum}")

    return errors


def _matches_json_schema_type(value: Any, schema_type: Any) -> bool:
    """Returns True when a JSON value matches one of the configured schema types."""

    if isinstance(schema_type, list):
        return any(_matches_json_schema_type(value, item) for item in schema_type)

    if schema_type == "object":
        return isinstance(value, dict)

    if schema_type == "array":
        return isinstance(value, list)

    if schema_type == "string":
        return isinstance(value, str)

    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)

    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    if schema_type == "boolean":
        return isinstance(value, bool)

    if schema_type == "null":
        return value is None

    return True


def _format_json_schema_type(schema_type: Any) -> str:
    """Formats a JSON Schema type value for diagnostics."""

    if isinstance(schema_type, list):
        return " or ".join(str(item) for item in schema_type)

    return str(schema_type)


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

    formatted_response = format_story_message(response_text)

    if not suggested_actions:
        return formatted_response

    action_lines = [f"- {action}" for action in suggested_actions]
    question = "What do you do now?"

    if formatted_response.endswith(question):
        return f"{formatted_response}\n" + "\n".join(action_lines)

    return f"{formatted_response}\n\n{question}\n" + "\n".join(action_lines)


def format_story_message(text: str) -> str:
    """Formats player-facing story prose for immersive display."""

    clean_text = str(text).strip()

    if not clean_text:
        return ""

    action_lines = [
        line.strip()
        for line in clean_text.splitlines()
        if line.strip().startswith(("-", "*"))
    ]
    prose_lines = [
        line.strip()
        for line in clean_text.splitlines()
        if line.strip() and not line.strip().startswith(("-", "*"))
    ]
    prose = " ".join(prose_lines)
    sentences = _split_story_sentences(prose)
    formatted = "\n\n".join(sentences)

    if action_lines:
        question = "What do you do now?"

        if formatted.endswith(question):
            formatted = f"{formatted}\n" + "\n".join(action_lines)
        else:
            formatted = f"{formatted}\n\n" + "\n".join(action_lines)

    return formatted.strip()


def _split_story_sentences(text: str) -> list[str]:
    """Splits story prose without breaking common time abbreviations."""

    clean_text = re.sub(r"\s+", " ", text).strip()

    if not clean_text:
        return []

    replacements = {
        "A.M.": "A<prd>M.",
        "P.M.": "P<prd>M.",
        "a.m.": "a<prd>m.",
        "p.m.": "p<prd>m.",
        "Mr.": "Mr<prd>",
        "Mrs.": "Mrs<prd>",
        "Ms.": "Ms<prd>",
        "Dr.": "Dr<prd>",
    }
    protected_text = clean_text

    for original, replacement in replacements.items():
        protected_text = protected_text.replace(original, replacement)

    raw_sentences = _split_at_story_sentence_boundaries(protected_text)
    sentences: list[str] = []

    for raw_sentence in raw_sentences:
        sentence = raw_sentence.strip()

        for original, replacement in replacements.items():
            sentence = sentence.replace(replacement, original)

        if sentence:
            sentences.append(sentence)

    return sentences or [clean_text]


def _split_at_story_sentence_boundaries(text: str) -> list[str]:
    """Splits text into display paragraphs without breaking inside dialogue quotes."""

    sentence_endings = ".!?"
    opening_quotes = {'"', "\u201c"}
    closing_quotes = {'"', "\u201d"}
    sentences: list[str] = []
    start_index = 0
    in_quote = False

    for index, char in enumerate(text):
        if char == '"':
            in_quote = not in_quote
            if not in_quote and _previous_non_space(text, index) in sentence_endings:
                split_index = _quote_boundary_split_index(text, index)

                if split_index is not None:
                    sentences.append(text[start_index:split_index].strip())
                    start_index = split_index

            continue

        if char in opening_quotes:
            in_quote = True
            continue

        if char in closing_quotes:
            in_quote = False

            if _previous_non_space(text, index) in sentence_endings:
                split_index = _quote_boundary_split_index(text, index)

                if split_index is not None:
                    sentences.append(text[start_index:split_index].strip())
                    start_index = split_index

            continue

        if char in sentence_endings and not in_quote:
            split_index = _sentence_boundary_split_index(text, index)

            if split_index is not None:
                sentences.append(text[start_index:split_index].strip())
                start_index = split_index

    tail = text[start_index:].strip()

    if tail:
        sentences.append(tail)

    return [sentence for sentence in sentences if sentence]


def _quote_boundary_split_index(text: str, quote_index: int) -> int | None:
    """Returns a split position after a closing quote when the next token starts fresh."""

    next_index = _next_non_space_index(text, quote_index + 1)

    if next_index is None:
        return None

    if text[next_index] in {'"', "\u201c", "\u2018"} or text[next_index].isupper() or text[next_index].isdigit():
        return next_index

    return None


def _sentence_boundary_split_index(text: str, punctuation_index: int) -> int | None:
    """Returns a split position after sentence punctuation outside dialogue."""

    next_index = punctuation_index + 1

    while next_index < len(text) and text[next_index] in {'"', "'", "\u201d", "\u2019"}:
        next_index += 1

    if next_index >= len(text):
        return None

    if not text[next_index].isspace():
        return None

    next_token_index = _next_non_space_index(text, next_index)

    if next_token_index is None:
        return None

    if text[next_token_index] in {'"', "'", "\u201c", "\u2018"} or text[next_token_index].isupper() or text[next_token_index].isdigit():
        return next_token_index

    return None


def _previous_non_space(text: str, index: int) -> str:
    """Returns the previous non-space character before index."""

    cursor = index - 1

    while cursor >= 0:
        if not text[cursor].isspace():
            return text[cursor]

        cursor -= 1

    return ""


def _next_non_space_index(text: str, index: int) -> int | None:
    """Returns the index of the next non-space character."""

    cursor = index

    while cursor < len(text):
        if not text[cursor].isspace():
            return cursor

        cursor += 1

    return None


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
