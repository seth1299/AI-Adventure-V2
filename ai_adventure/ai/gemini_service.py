from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ai_adventure.alchemy.ingredients import (
    COMMON_MEASUREMENT_UNITS,
    CRAFTING_INGREDIENT_CATEGORY_NAMES,
)
from ai_adventure.calendar_system import normalize_calendar_settings
from ai_adventure.context.creative_guardrails import (
    default_banned_creative_terms,
    find_banned_creative_terms,
    sanitize_banned_creative_terms_in_data,
)
from ai_adventure.context.naming import GENERIC_PROPER_NOUN_PLACEHOLDER_RULE
from ai_adventure.currency import normalize_currency_denominations
from ai_adventure.locations import clean_player_location_name
from ai_adventure.new_game_setup import STARTER_INVENTORY_MIN_ITEMS

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz
except ImportError:  # pragma: no cover - exercised only in lean dev environments.
    _rapidfuzz_fuzz = None


LOGGER = logging.getLogger(__name__)


DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
CREATIVE_TERM_REPAIR_ATTEMPTS = 4
FALLBACK_SUGGESTED_ACTIONS = [
    "Look around and take stock of the situation.",
    "Check your inventory, tasks, or surroundings.",
    "Choose the next thing to focus on.",
]
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
    "CombatStartedEvent",
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
TEXT_SAFETY_HARM_CATEGORIES = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
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
RECIPE_INGREDIENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reagent_name": {"type": "string"},
        "quantity": {"type": "integer", "minimum": 1},
        "measure_amount": {"type": "integer", "minimum": 1},
        "measure_unit": {
            "type": "string",
            "enum": list(COMMON_MEASUREMENT_UNITS),
        },
    },
    "required": [
        "reagent_name",
        "quantity",
        "measure_amount",
        "measure_unit",
    ],
    "additionalProperties": False,
}
NONEMPTY_RECIPE_INGREDIENT_LIST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": RECIPE_INGREDIENT_SCHEMA,
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
                "weapon_hands": {
                    "type": "string",
                    "enum": ["one-handed", "two-handed", ""],
                },
                "damage": {"type": "string"},
                "damage_type": {"type": "string"},
                "covers_body_parts": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "armor_rating": INT_OR_SKIP_SCHEMA,
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
                "new_category": {"type": "string"},
                "new_description": {"type": "string"},
                "new_amount": INT_OR_SKIP_SCHEMA,
                "new_value_base_units": INT_OR_SKIP_SCHEMA,
                "weapon_hands": {
                    "type": "string",
                    "enum": ["one-handed", "two-handed", ""],
                },
                "damage": {"type": "string"},
                "damage_type": {"type": "string"},
                "covers_body_parts": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "armor_rating": INT_OR_SKIP_SCHEMA,
            },
            ["target_name"],
        ),
        _event_response_schema(
            "CombatStartedEvent",
            {
                "description": {"type": "string"},
                "enemies": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "health": {"type": "integer", "minimum": 1},
                            "armor_rating": {"type": "integer", "minimum": 1},
                            "damage": {"type": "string"},
                            "loot": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "status_effects": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["name", "health", "armor_rating", "damage", "loot"],
                        "additionalProperties": False,
                    },
                },
                "allies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "health": {"type": "integer", "minimum": 1},
                            "armor_rating": {"type": "integer", "minimum": 1},
                            "damage": {"type": "string"},
                            "status_effects": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["name", "health", "armor_rating", "damage"],
                        "additionalProperties": False,
                    },
                },
            },
            ["description", "enemies"],
        ),
        _event_response_schema(
            "RecipeDiscoveredEvent",
            {
                "name": {"type": "string"},
                "ingredients": NONEMPTY_RECIPE_INGREDIENT_LIST_SCHEMA,
                "result": {"type": "string"},
                "notes": {"type": "string"},
            },
            ["name", "ingredients", "result", "notes"],
        ),
        _event_response_schema(
            "ReagentDiscoveredEvent",
            {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "uses": NONEMPTY_STRING_LIST_SCHEMA,
            },
            ["name", "description", "location", "uses"],
            description=(
                "Stores a simplified useful crafting item/material; name-only "
                "payloads are incomplete."
            ),
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
                "due_elapsed_minutes": {
                    "type": "integer",
                    "minimum": -1,
                    "description": (
                        "Absolute in-world minute when the task is due, or -1 "
                        "when there is no known deadline."
                    ),
                },
            },
            [
                "name",
                "category",
                "status",
                "description",
                "requester",
                "location",
                "reward",
                "due_date",
                "due_elapsed_minutes",
            ],
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
                "due_elapsed_minutes": {
                    "type": "integer",
                    "minimum": -1,
                },
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
                "knowledge_scope": NONEMPTY_STRING_LIST_SCHEMA,
                "known_facts": NONEMPTY_STRING_LIST_SCHEMA,
            },
            [
                "display_name",
                "role",
                "location",
                "public_description",
                "player_facing_information",
                "knowledge_scope",
                "known_facts",
            ],
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
            "description": (
                "Player-facing narration only. Do not include the app prompt "
                "'What do you do now?'; the application appends that separately."
            ),
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
SKILL_CHECK_PLAN_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "description": (
                "Skill checks the Python application should resolve before the "
                "full narration request."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"},
                    "difficulty": {"type": "string"},
                    "dc": {"type": "integer", "minimum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["skill_name"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["checks"],
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
        "calendar_settings": {
            "type": "object",
            "properties": {
                "days_per_week": {"type": "integer", "minimum": 1, "maximum": 14},
                "weeks_per_month": {"type": "integer", "minimum": 1, "maximum": 12},
                "months_per_year": {"type": "integer", "minimum": 1, "maximum": 24},
                "seasons_per_year": {"type": "integer", "minimum": 1, "maximum": 12},
                "day_names": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "month_names": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "seasons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "weather_hint": {"type": "string"},
                        },
                        "required": ["name", "weather_hint"],
                        "additionalProperties": False,
                    },
                },
                "time_display": {
                    "type": "string",
                    "enum": ["narrative", "12_hour", "24_hour"],
                },
            },
            "additionalProperties": False,
        },
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
            "minItems": STARTER_INVENTORY_MIN_ITEMS,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1},
                    "description": {"type": "string"},
                    "value_base_units": {"type": "integer", "minimum": 0},
                    "source_index": {
                        "type": "integer",
                        "minimum": -1,
                        "description": (
                            "Zero-based setup.starter_items index for player-requested "
                            "items, or -1 for extra invented items."
                        ),
                    },
                },
                "required": [
                    "name",
                    "category",
                    "quantity",
                    "description",
                    "value_base_units",
                    "source_index",
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
        "suggested_actions": {
            "type": "array",
            "description": "Three or four short player-facing action options for the opening scene.",
            "items": {"type": "string"},
        },
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
    "Crafting": (
        "alchemy",
        "brew",
        "craft",
        "crafting",
        "distill",
        "elixir",
        "experiment",
        "identify reagent",
        "make",
        "mix",
        "potion",
        "recipe",
        "reagent",
        "repair",
        "tincture",
    ),
    "Foraging": (
        "forage",
        "foraging",
        "geological find",
        "harvest",
        "herb",
        "mushroom",
        "plant",
        "search for",
        "specimen",
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
    "Mining": (
        "dig",
        "mine",
        "mining",
        "mineral",
        "ore",
        "pickaxe",
        "quarry",
        "vein",
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
    "Perception": (
        "listen",
        "notice",
        "observe",
        "scan",
        "spot",
    ),
    "Persuasion": (
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
        "conceal",
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
SKILL_NAME_MATCH_THRESHOLD = 82.0
SKILL_DESCRIPTION_MATCH_THRESHOLD = 78.0
SKILL_KEYWORD_ALIASES: dict[str, tuple[str, ...]] = {
    "Foraging": ("Fieldcraft", "Survival"),
    "Mining": ("Prospecting",),
    "Perception": ("Awareness",),
    "Prospecting": ("Mining",),
}
SKILL_MATCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "to",
    "with",
    "your",
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
class SkillCheckPlanResult:
    """Parsed result from a lightweight pre-narration skill-check request."""

    checks: list[dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""


@dataclass(frozen=True)
class AiWorldSetupResult:
    """Parsed result from an AI new-game world setup request."""

    world_summary: str
    introductory_message: str
    start_location: str = ""
    calendar_settings: dict[str, Any] = field(default_factory=dict)
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
    suggested_actions: list[str] = field(default_factory=list)
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

        LOGGER.info(
            "Story context packet summary: %s",
            json.dumps(
                _context_packet_stats(context_packet, prompt_chars=len(prompt)),
                sort_keys=True,
            ),
        )
        LOGGER.info("Sending story context packet to Gemini model %s.", self.settings.model)
        response = client.models.generate_content(
            model=self.settings.model,
            contents=prompt,
            config=_structured_output_config(STORY_RESPONSE_JSON_SCHEMA),  # type: ignore[arg-type]
        )
        raw_text = _repair_gemini_creative_terms(
            client,
            self.settings.model,
            str(getattr(response, "text", "") or "").strip(),
            "story response",
            STORY_RESPONSE_JSON_SCHEMA,
        )
        LOGGER.info("Gemini raw story response:\n%s", raw_text)

        if not raw_text:
            LOGGER.warning("Gemini returned an empty story response.")
            return AiNarrationResult(
                narrative_text="The narrator falls silent for a moment.",
                raw_text=raw_text,
            )

        result = parse_gemini_story_response(raw_text)
        result = _drop_duplicate_resolved_skill_check_events(result, context_packet)
        result = _ensure_in_game_suggested_actions(result, context_packet)
        result = _ensure_status_event_for_in_game_response(result, context_packet)
        result = _ensure_skill_check_for_uncertain_player_command(result, context_packet)
        result = _ensure_inventory_for_collected_reagents(result, context_packet)
        return _ensure_inventory_for_narrated_collection(result, context_packet)

    def plan_story_skill_checks(
        self,
        context_packet: dict[str, Any],
    ) -> SkillCheckPlanResult:
        """
        Asks Gemini which checks should be resolved before full narration.

        Args:
            context_packet: Structured context packet from AiContextBuilder.

        Returns:
            Parsed skill-check plan.
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

        prompt = build_skill_check_plan_prompt(context_packet)
        client = genai.Client(api_key=self.settings.api_key)

        LOGGER.info(
            "Skill-check planning packet summary: %s",
            json.dumps(
                _context_packet_stats(context_packet, prompt_chars=len(prompt)),
                sort_keys=True,
            ),
        )
        LOGGER.info("Sending skill-check planning packet to Gemini model %s.", self.settings.model)
        response = client.models.generate_content(
            model=self.settings.model,
            contents=prompt,
            config=_structured_output_config(SKILL_CHECK_PLAN_RESPONSE_JSON_SCHEMA),  # type: ignore[arg-type]
        )
        raw_text = _repair_gemini_creative_terms(
            client,
            self.settings.model,
            str(getattr(response, "text", "") or "").strip(),
            "skill-check plan response",
            SKILL_CHECK_PLAN_RESPONSE_JSON_SCHEMA,
        )
        LOGGER.info("Gemini raw skill-check plan response:\n%s", raw_text)

        if not raw_text:
            LOGGER.warning("Gemini returned an empty skill-check plan response.")
            return SkillCheckPlanResult(raw_text=raw_text)

        return parse_skill_check_plan_response(raw_text)

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
        raw_text = _repair_gemini_creative_terms(
            client,
            self.settings.model,
            str(getattr(response, "text", "") or "").strip(),
            "new-game response",
            NEW_GAME_RESPONSE_JSON_SCHEMA,
        )
        LOGGER.info("Gemini raw new-game response:\n%s", raw_text)

        if not raw_text:
            LOGGER.warning("Gemini returned an empty new-game response.")
            return AiWorldSetupResult(
                world_summary="The world is still taking shape.",
                introductory_message=_format_visible_response(
                    "The adventure begins.",
                    FALLBACK_SUGGESTED_ACTIONS,
                ),
                suggested_actions=list(FALLBACK_SUGGESTED_ACTIONS),
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


def build_skill_check_plan_prompt(context_packet: dict[str, Any]) -> str:
    """
    Builds the lightweight prompt used before full narration.

    Args:
        context_packet: Structured story context packet.

    Returns:
        Prompt text.
    """

    planning_packet = _skill_check_planning_packet(context_packet)
    packet_json = json.dumps(planning_packet, indent=2)

    return (
        "You are a game master deciding whether the player's latest action "
        "needs one or more skill checks before narration is written.\n"
        "Return one JSON object and no surrounding Markdown.\n\n"
        "Rules:\n"
        "- Return checks as an array. Use [] when no check is needed.\n"
        "- Choose only checks needed to resolve meaningful uncertainty in the "
        "latest player_command.\n"
        "- Request checks for actions that could plausibly fail, go poorly, take "
        "extra time, vary in quality, consume resources, reveal misleading "
        "information, attract attention, cause harm, or miss something.\n"
        "- Named skill use, foraging, harvesting, searching, researching, "
        "identifying, crafting, alchemy experiments, persuasion, stealth, and "
        "combat usually need checks unless trivial and risk-free.\n"
        "- Routine movement, paying a known price, receiving ordinary goods, "
        "eating, drinking, and casual conversation do not need checks unless the "
        "player adds a contested, risky, hidden, time-sensitive, or deceptive goal.\n"
        "- Prefer an existing known skill_name when one fits. If no existing skill "
        "fits, use a clear new skill_name.\n"
        "- Include difficulty or dc when the action's risk is clear. Use reason "
        "to briefly explain why the check is needed.\n"
        "- Do not narrate the outcome. Do not roll. The Python application rolls.\n\n"
        "Planning packet:\n"
        f"{packet_json}"
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
        "location, public description, player_facing_information, knowledge_scope, "
        "and known_facts. display_name is the name shown in the NPCs tab; use a "
        "generic label such as 'Shady Character' when the player has not learned "
        "the NPC's actual name or role. role is for AI memory and should not be "
        "treated as the player-facing summary. location must be a meaningful "
        "player-known place, usually the current scene location where the NPC was "
        "encountered; do not leave it blank. known_facts should list what this NPC "
        "personally knows; if nothing private is established yet, use a clear "
        "public/observable fact instead of omitting the field.\n"
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
        "Creative naming boundary (hard requirement):\n"
        "- This is a highest-priority output rule, not optional style guidance.\n"
        "- If the context packet includes creative_ideas, treat those examples as "
        "high-priority style seeds for newly invented names and setting details.\n"
        "- Prefer creative_ideas examples or close stylistic relatives over broad "
        "training-data fantasy defaults, especially for NPCs, settlements, taverns, "
        "factions, religions, regions, ingredients, species, food, and drinks.\n"
        "- Never use creative_ideas.banned_terms, close spelling variants, "
        "hyphenation variants, or obvious reskins for newly invented proper nouns. "
        "Banned terms may appear only when already established in saved state or "
        "explicitly provided by the player.\n\n"
        "- Before returning JSON, scan every string key and value you wrote. If any "
        "newly invented name contains a banned term or close variant, replace it "
        "with a fresh non-banned name before responding.\n\n"
        f"- {GENERIC_PROPER_NOUN_PLACEHOLDER_RULE}\n\n"
        f"Exact banned proper nouns for newly invented content: {banned_terms_text}\n\n"
        "Return one JSON object and no surrounding Markdown. The API response "
        "schema defines the required top-level fields.\n\n"
        "Rules:\n"
        "- response must be a non-empty string.\n"
        "- Mature fictional content is allowed when it fits the scene, genre, and "
        "player choices. Assume the player character and player are adults of legal "
        "drinking age unless the saved character profile explicitly says otherwise. "
        "Do not sanitize taverns, inns, feasts, criminal districts, battlefields, "
        "or political conflicts into harmless substitutes. Alcohol, drunken "
        "patrons, gambling, threats, intimidation, violence, injury, blood, corpses, "
        "crime, cruelty, corruption, and oppressive social attitudes may appear as "
        "ordinary fictional world content. Keep descriptions story-focused rather "
        "than instructional. For prejudice or slurs, use fictional in-world terms "
        "for fictional cultures, species, factions, classes, or guilds; do not use "
        "real-world slurs against protected classes.\n"
        "- Spoken dialogue must use double quotation marks around the speaker's "
        "full spoken sentence or paragraph. Do not use single quotation marks as "
        "the outer boundary of dialogue. Use single quotation marks only when an "
        "already-double-quoted speaker mentions a named item, title, shop, place, "
        "phrase, nickname, inscription, or other quoted specific inside that "
        "dialogue.\n"
        "- The response field may use light Markdown for player-facing prose: "
        "italics for inner thoughts, sensory impressions, emphasis, or quiet "
        "self-reflection; bold for the first mention of important NPCs, locations, "
        "factions, quests, or items; and headings or bullet lists for longer "
        "summaries. Do not use Markdown tables, code fences, HTML, or Markdown "
        "that hides text from the player.\n"
        "- Use state.player_ai_preferences.narration_tense_label and "
        "state.player_ai_preferences.narration_style_label for the response "
        "field. Limited narration stays within the player character's observed "
        "or reasonably inferred experience. Omniscient narration may use a "
        "broader narrative camera, but it must not reveal secrets, hidden state, "
        "mystery solutions, or NPC-private facts, and NPCs still obey the NPC "
        "knowledge boundary.\n"
        "- Resolve the player's submitted action in the narration. Do not end by "
        "merely restating the action, intent, or search target. For example, if "
        "the player opens a book to search for clues, describe what the book "
        "contains, what they notice, what blocks them, or why the result remains "
        "uncertain.\n"
        "- Do not speak for the player character. Do not invent player dialogue, "
        "questions, promises, purchases, attacks, or choices that the player did "
        "not explicitly provide. NPCs may speak, react, refuse, answer, or ask "
        "questions, but the player character's next words and decisions belong "
        "to the player.\n"
        "- The response field must not include 'What do you do now?' or any "
        "variant of an end-of-turn prompt. The Python application displays that "
        "prompt after your response when appropriate.\n"
        "- Do not end the response with a player-character action or player-"
        "character dialogue as though the player still needs to finish the same "
        "thought. End on the world's response, a resolved immediate outcome, a "
        "clear obstacle, an NPC reaction, or a concrete new detail.\n"
        "- If context_packet.continuation_request.active is true, continue the "
        "latest story response as though it had been longer originally. Do not "
        "treat this as a new player action, do not invent a new player decision, "
        "and do not advance time or add durable events unless the previous "
        "response already clearly described a current-turn state change that "
        "needs an event.\n"
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
        "- Routine movement, paying a known price, receiving ordinary goods, "
        "eating, drinking, and casual conversation are not skill checks unless "
        "the player adds a contested, risky, hidden, time-sensitive, or "
        "deceptive goal.\n"
        "- If state.skills.resolved_checks_this_turn is non-empty, those are "
        "the authoritative skill-check results for this player_command. Do not "
        "request duplicate SkillCheckRequestedEvent entries for those skills. "
        "Narrate the action from the resolved outcomes: low failed rolls should "
        "produce real setbacks, costs, missed information, danger, or slower "
        "progress; ordinary failures should fail or partly succeed with a clear "
        "complication; ordinary successes should make real progress; very high "
        "rolls or totals that beat the DC by 5 or more should produce a notably "
        "cleaner, faster, richer, or more advantageous result. Do not mention "
        "dice, raw roll numbers, totals, DCs, or game mechanics in the story.\n"
        "- Every InventoryItemAddedEvent payload must include value_base_units "
        "as an integer of at least 1.\n"
        "- For weapons, set item_type='Weapon' and include weapon_hands "
        "('one-handed' or 'two-handed') plus damage as a dice expression such "
        "as 1d6, 1d8, or 2d6. For armor or shields, set item_type='Armor', "
        "include covers_body_parts, and include armor_rating as the armor bonus "
        "that item contributes. Use category/item_type values of Weapon and "
        "Armor clearly so the Character sheet can equip them.\n"
        "- state.item_catalog.items is the master list of known item definitions. "
        "Use it to remember item descriptions after items leave inventory, but "
        "only state.inventory.items are current possessions.\n"
        "- If a situation becomes an actual fight, suggest CombatStartedEvent "
        "with concrete enemies, optional allied combatants, health, armor_rating, "
        "damage dice, and loot. Do not narrate attack rolls, turn-by-turn combat, "
        "damage totals, deaths, victory, defeat, or loot recovery after combat "
        "starts. The Python application handles combat deterministically in the "
        "Combat tab and blocks Story input until combat is resolved.\n"
        "- ReagentDiscoveredEvent records Crafting tab knowledge for useful "
        "items/materials only and uses exactly name, description, location, "
        "and uses. If the player physically collects, harvests, picks up, or "
        "stores that item/material, also suggest InventoryItemAddedEvent for "
        "the same item/material.\n"
        "- RecipeDiscoveredEvent ingredients must be structured entries using "
        "item names from state.item_catalog.items in the reagent_name field. "
        "Only items with category "
        f"{CRAFTING_INGREDIENT_CATEGORY_NAMES} may be used as recipe ingredients. "
        "Include quantity, measure_amount, and measure_unit from the listed "
        "common measurement units.\n"
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
        "- ActiveTaskUpsertedEvent is shown directly in the Active Tasks tab. Fill "
        "only category, status, description, requester, location, reward, due_date, "
        "and due_elapsed_minutes with useful player-facing values. Do not add "
        "notes, Notes, or any other extra active-task fields. Use requester='Self' "
        "for personal goals, reward='N/A' when nobody is paying or trading for "
        "the task, due_date='N/A' and due_elapsed_minutes=-1 when no deadline is "
        "known, and location as the relevant place for doing, picking up, "
        "completing, or turning in the task. For any real deadline, due_date must "
        "be an exact player-facing date and time, not vague prose, and "
        "due_elapsed_minutes must be the absolute in-world elapsed minute for "
        "that deadline. Use 'Unknown' only when a value exists but is genuinely "
        "unclear.\n"
        "Do not use a fixed sentence count. Scale response length to the "
        "importance, risk, and consequences of the action; provide enough text "
        "to make the outcome feel earned without being padded. "
        "Concisely describe the scene, and make sure to properly address all parts of the user's query. \n"
        "When creating items, ensure that you give a quantifiable amount or size for the item, rather than using phrases such as \"a pile of [ore/apples/etc.]\".\n"
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
    banned_terms = _banned_terms_from_context(setup_packet)
    banned_terms_text = ", ".join(banned_terms) if banned_terms else "(none provided)"

    return (
        "You are creating the initial world setup for AI Adventure.\n"
        "Use only the structured setup packet below as confirmed setup input. "
        "Synthesize the player's choices into a coherent playable world.\n\n"
        "Creative naming boundary (hard requirement):\n"
        "- This is a highest-priority output rule, not optional style guidance.\n"
        "- Never use creative_ideas.banned_terms, close spelling variants, "
        "hyphenation variants, or obvious reskins for newly invented proper nouns.\n"
        "- This includes the player character name, NPC names, locations, taverns, "
        "regions, factions, religions, shops, guilds, landmarks, items, skills, "
        "calendar names, and event payload names.\n"
        "- Before returning JSON, scan every string key and value you wrote. If any "
        "newly invented name contains a banned term or close variant, replace it "
        "with a fresh non-banned name before responding.\n"
        "- Banned terms may appear only when explicitly provided by the player as "
        "confirmed setup input.\n\n"
        f"Exact banned proper nouns for newly invented content: {banned_terms_text}\n\n"
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
        "hyphenation variants, or obvious reskins for newly invented proper nouns.\n"
        f"- {GENERIC_PROPER_NOUN_PLACEHOLDER_RULE}\n"
        "- Mature fictional content is allowed when it fits the selected genre, "
        "world, opening location, and player setup. Assume the player character "
        "and player are adults of legal drinking age unless the character setup "
        "explicitly says otherwise. Taverns may include alcohol, bartenders, "
        "drunken patrons, gambling, brawls, shady deals, and adult social texture. "
        "Violence, blood, corpses, criminality, cruelty, corruption, and oppressive "
        "social attitudes may be part of the world when genre-appropriate. Use "
        "fictional in-world slurs or insults only for fictional cultures, species, "
        "factions, classes, or guilds; do not invent or use real-world slurs "
        "against protected classes.\n"
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
        "and economy. It may use light Markdown headings, bold names, italics, "
        "and bullet lists when that improves readability.\n"
        "- world_lore must group player-known starting lore into keyed category "
        "objects where each key is the durable entry name and each value is the "
        "player-facing lore text. Include useful categories such as Locations, Religions, Economy, "
        "Culture and Laws, Factions and Guilds, Prominent NPCs, and Current Rumors "
        "when they fit the game. Do not include secrets, mystery solutions, hidden "
        "villains, or GM-only facts in world_lore. Lore text may use light "
        "Markdown such as italics, bold important names, and short lists.\n"
        "- introductory_message must be player-facing narration for the first "
        "scene at start_location and must end with exactly "
        "'What do you do now?'\n"
        "- suggested_actions must contain three or four short opening-scene "
        "actions the player can take next. Keep them concrete, immediate, and "
        "consistent with the introductory_message.\n"
        "- introductory_message may use light Markdown for player-facing prose: "
        "italics for inner thoughts or sensory emphasis, and bold for the first "
        "mention of important NPCs, locations, factions, or items. Do not use "
        "Markdown tables, code fences, or HTML.\n"
        "- introductory_message and other player-facing setup prose must use "
        "setup.narration.tense_label and setup.narration.style_label. Limited "
        "styles stay within the player character's observed or reasonably "
        "inferred experience. Omniscient styles may use a broader narrative "
        "camera, but must not reveal secrets, hidden state, mystery solutions, "
        "or NPC-private facts.\n"
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
        "- If setup.calendar.ai_generated is true, invent calendar_settings that "
        "fit the selected world, genre, culture, climate, and playstyle. Use "
        "clear day names, month names, season names, season weather hints, and "
        "a time_display value. Keep the calendar playable: days_per_week 1-14, "
        "weeks_per_month 1-12, months_per_year 1-24, and seasons_per_year 1-12. "
        "Do not copy the default Gregorian calendar, weekday names, January-"
        "through-December month names, Spring/Summer/Autumn/Winter as the full "
        "season list, or generic Month 1/Month 2 placeholder names when AI "
        "generation is requested. "
        "If setup.calendar.ai_generated is false, return calendar_settings as an "
        "empty object and use the provided calendar.\n"
        "- character must finalize the player character profile. If character name, "
        "appearance, backstory, or notes are blank/default placeholders, replace "
        "them with coherent player-facing details suitable for the world. Preserve "
        "explicit custom player input exactly. For each character field, if the "
        "corresponding setup.character value is not blank/default, copy that field "
        "unchanged in character and use that exact identity in world_summary, "
        "introductory_message, and events. Do not rename, partially rename, "
        "embellish, paraphrase, or reinterpret a player-provided character name, "
        "appearance, backstory, or notes field.\n"
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
        "starting skill.'\n"
        "- starting_items must contain at least five total tracked possessions "
        "and has no maximum item count. Include any player-requested items, then "
        "invent enough additional concrete items that naturally fit the finalized "
        "character, genre, starting location, weather, and economy to reach the "
        "minimum. Return the finalized inventory in the starting_items field; "
        "do not use the alias starting_inventory. Preserve "
        "any player-provided setup.starter_items entries whose requires_ai_invention "
        "field is false. Set source_index to the zero-based setup.starter_items "
        "index for items based on a setup starter-item entry, and -1 for extra "
        "invented items. "
        "If a setup.starter_items entry has requires_ai_invention=true or "
        "item_request text, treat it as a player-authored item concept and "
        "convert it into the number of concrete, setting-appropriate tracked "
        "items that best fits the concept rather than copying the request "
        "verbatim. If setup.starter_items is blank, invent at least five items "
        "that fit the finalized character backstory, finalized skills, selected "
        "genre, starting location, weather, and economy. Do not include setup "
        "bookkeeping words such as Starting, Starter, Initial, Amount, Quantity, "
        "Count, or Total in item names. Generalize resource names to the actual "
        "inventory item, such as Fuel instead of Starting Fuel Amount, Food instead "
        "of Starting Food Amount, and Water instead of Starting Water Quantity. Put "
        "quantities in quantity, not name. Each item must include "
        "name, category, quantity, description, value_base_units, and source_index.\n"
        "- If setup.currency_denominations is empty, currency_denominations must "
        "contain at least one and at most four concrete denominations that fit "
        "the selected genre, world, and economy. One denomination must have "
        "value=1 as the baseline unit. Other values are exchange rates measured "
        "in that baseline unit and do not need to be multiples or powers of 10. "
        "For example, fantasy worlds may use copper/silver/gold-style coinage, "
        "realistic modern worlds may use dollars, and futuristic or space worlds "
        "may use credits. Use setup.economy_examples as common-price calibration "
        "for ordinary goods when it is present. If setup.currency_denominations "
        "already contains player-provided values, preserve them.\n"
        "- starting_currency_balance_base_units must be a reasonable starting money "
        "amount for the finalized character, genre, and economy. This is the "
        "player's actual starting money stored in game_state/currency.balance as "
        "one integer in the baseline currency unit. Account for any "
        "setup.economy_examples common-price rows when choosing it. Do not create "
        "coin or purse items in starting_items to represent spendable money.\n"
        "- The API response schema defines the required output fields and event "
        "envelope. Use type and payload for each event; do not use event_type as "
        "the top-level event type key.\n"
        "- Use only player-known information in player-facing event fields.\n"
        "- Use NpcUpsertedEvent for prominent NPCs the player can know about at "
        "setup. Remember that if the Player requested more than one NPC, or that you think that the Player would know more than one NPC, then you can pass more than one NpcUpsertedEvent.\n"
        "Use ActiveTaskUpsertedEvent for initial active obligations. Use "
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
        "safety_settings": _permissive_text_safety_settings(),
    }


def _permissive_text_safety_settings() -> list[dict[str, str]]:
    """Returns explicit Gemini safety settings for mature fictional storytelling."""

    return [
        {
            "category": category,
            "threshold": "OFF",
        }
        for category in TEXT_SAFETY_HARM_CATEGORIES
    ]


def _repair_gemini_creative_terms(
    client: Any,
    model: str,
    raw_text: str,
    response_label: str,
    schema: dict[str, Any],
) -> str:
    """Asks Gemini to rewrite a response when it uses banned generated names."""

    candidate_text = raw_text
    banned_terms = find_banned_creative_terms(candidate_text)

    if not banned_terms:
        return raw_text

    forbidden_terms = _forbidden_creative_terms_for_repair(banned_terms)

    for attempt in range(1, CREATIVE_TERM_REPAIR_ATTEMPTS + 1):
        LOGGER.warning(
            "Gemini %s contained banned creative term(s): %s. "
            "Requesting repair attempt %s/%s.",
            response_label,
            ", ".join(banned_terms),
            attempt,
            CREATIVE_TERM_REPAIR_ATTEMPTS,
        )
        repair_prompt = _creative_terms_repair_prompt(
            candidate_text,
            response_label=response_label,
            observed_terms=banned_terms,
            forbidden_terms=forbidden_terms,
            attempt=attempt,
        )

        try:
            response = client.models.generate_content(
                model=model,
                contents=repair_prompt,
                config=_structured_output_config(schema),  # type: ignore[arg-type]
            )
        except Exception:
            LOGGER.exception(
                "Gemini %s repair attempt %s/%s failed.",
                response_label,
                attempt,
                CREATIVE_TERM_REPAIR_ATTEMPTS,
            )
            return str(_sanitize_gemini_creative_terms(candidate_text, response_label))

        repaired_text = str(getattr(response, "text", "") or "").strip()

        if not repaired_text:
            LOGGER.warning(
                "Gemini %s repair attempt %s/%s returned an empty response.",
                response_label,
                attempt,
                CREATIVE_TERM_REPAIR_ATTEMPTS,
            )
            continue

        repaired_banned_terms = find_banned_creative_terms(repaired_text)

        if not repaired_banned_terms:
            LOGGER.info(
                "Gemini %s repair attempt %s/%s removed banned creative terms.",
                response_label,
                attempt,
                CREATIVE_TERM_REPAIR_ATTEMPTS,
            )
            return repaired_text

        LOGGER.warning(
            "Gemini %s repair attempt %s/%s still contained banned creative term(s): %s.",
            response_label,
            attempt,
            CREATIVE_TERM_REPAIR_ATTEMPTS,
            ", ".join(repaired_banned_terms),
        )
        candidate_text = repaired_text
        banned_terms = repaired_banned_terms

    LOGGER.warning(
        "Gemini %s still contained banned creative terms after %s repair attempts. "
        "Sanitizing the latest response.",
        response_label,
        CREATIVE_TERM_REPAIR_ATTEMPTS,
    )
    return str(_sanitize_gemini_creative_terms(candidate_text, response_label))


def _forbidden_creative_terms_for_repair(observed_terms: list[str]) -> list[str]:
    """Returns the full repair-time forbidden list, preserving observed terms."""

    terms = list(default_banned_creative_terms()) or list(observed_terms)
    seen = {term.casefold() for term in terms}

    for term in observed_terms:
        folded = term.casefold()

        if folded not in seen:
            terms.append(term)
            seen.add(folded)

    return terms


def _creative_terms_repair_prompt(
    raw_text: str,
    *,
    response_label: str,
    observed_terms: list[str],
    forbidden_terms: list[str],
    attempt: int,
) -> str:
    """Builds a compact repair prompt with the full banned-name list."""

    return (
        f"Repair AI Adventure {response_label} JSON. Attempt {attempt}.\n\n"
        "Hard rule: the repaired JSON must not contain any forbidden term, close "
        "spelling variant, hyphenation variant, or obvious reskin anywhere in a "
        "string key or value.\n"
        "When replacing a forbidden NPC, location, faction, item, skill, calendar, "
        "or other proper noun, invent a fresh genre-appropriate name from scratch. "
        "Do not use placeholders such as unnamed place, unnamed person, the city, "
        "the person, Local Item, or Local Skill unless the original text was already "
        "generic.\n"
        "Preserve the same facts, tone, structure, and player-facing intent. Return "
        "only one JSON object that matches the configured schema.\n\n"
        f"Observed offending terms in the current JSON: {', '.join(observed_terms)}\n"
        f"Full forbidden terms list: {', '.join(forbidden_terms)}\n\n"
        "JSON response to repair:\n"
        f"{raw_text}"
    )


def _sanitize_gemini_creative_terms(value: Any, response_label: str) -> Any:
    """Removes banned generated-name terms before AI output reaches state or UI."""

    banned_terms = find_banned_creative_terms(value)

    if not banned_terms:
        return value

    LOGGER.warning(
        "Gemini %s contained banned creative term(s): %s. "
        "Sanitized before display, logging, or persistence.",
        response_label,
        ", ".join(banned_terms),
    )

    if isinstance(value, str):
        clean_text = _strip_json_fence(value.strip())

        try:
            data = json.loads(clean_text)
        except json.JSONDecodeError:
            return sanitize_banned_creative_terms_in_data(value)

        return json.dumps(
            sanitize_banned_creative_terms_in_data(data),
            ensure_ascii=False,
        )

    return sanitize_banned_creative_terms_in_data(value)


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


def _skill_check_planning_packet(context_packet: dict[str, Any]) -> dict[str, Any]:
    """Builds the small packet for pre-narration skill-check planning."""

    state = context_packet.get("state", {})

    if not isinstance(state, dict):
        state = {}

    skills = state.get("skills", {})

    if not isinstance(skills, dict):
        skills = {}

    recent_history = context_packet.get("recent_history", [])

    if not isinstance(recent_history, list):
        recent_history = []

    return {
        "packet_type": "skill_check_planning",
        "player_command": str(context_packet.get("player_command", "")).strip(),
        "scene": state.get("scene", {}) if isinstance(state.get("scene"), dict) else {},
        "player": state.get("player", {}) if isinstance(state.get("player"), dict) else {},
        "known_skills": skills.get("known_skills", []),
        "recent_checks": skills.get("recent_checks", []),
        "recent_history": recent_history[-2:],
    }


def _context_packet_stats(
    context_packet: dict[str, Any],
    *,
    prompt_chars: int,
) -> dict[str, int]:
    """Builds compact telemetry for context-size drift diagnostics."""

    state = context_packet.get("state", {})

    if not isinstance(state, dict):
        state = {}

    return {
        "prompt_chars": prompt_chars,
        "packet_chars": len(json.dumps(context_packet, ensure_ascii=False)),
        "recent_history": _list_len(context_packet.get("recent_history")),
        "reference_sections": _list_len(context_packet.get("reference_sections")),
        "inventory_items": _list_len(_nested_value(state, "inventory", "items")),
        "item_catalog_items": _list_len(_nested_value(state, "item_catalog", "items")),
        "crafting_items": _list_len(_nested_value(state, "alchemy", "known_reagents")),
        "crafting_recipes": _list_len(_nested_value(state, "alchemy", "known_recipes")),
        "known_skills": _list_len(_nested_value(state, "skills", "known_skills")),
        "recent_checks": _list_len(_nested_value(state, "skills", "recent_checks")),
        "active_tasks": _list_len(_nested_value(state, "active_tasks", "tasks")),
        "relevant_npcs": _list_len(_nested_value(state, "npcs", "relevant")),
        "valid_music_tracks": _list_len(_nested_value(state, "audio", "valid_music_tracks")),
    }


def _list_len(value: Any) -> int:
    """Returns list length for packet telemetry."""

    return len(value) if isinstance(value, list) else 0


def _nested_value(mapping: dict[str, Any], *keys: str) -> Any:
    """Reads a nested dict value without raising on malformed packets."""

    current: Any = mapping

    for key in keys:
        if not isinstance(current, dict):
            return None

        current = current.get(key)

    return current


def _state_subpacket(context_packet: dict[str, Any], name: str) -> dict[str, Any]:
    """Reads a named state subpacket."""

    state = context_packet.get("state", {})

    if not isinstance(state, dict):
        return {}

    subpacket = state.get(name, {})

    return subpacket if isinstance(subpacket, dict) else {}


def parse_skill_check_plan_response(raw_text: str) -> SkillCheckPlanResult:
    """
    Parses Gemini skill-check planning output.

    Args:
        raw_text: Raw Gemini response text.

    Returns:
        Parsed skill-check plan. Invalid or non-JSON output becomes an empty plan.
    """

    clean_text = _strip_json_fence(raw_text.strip())

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError:
        LOGGER.warning("Gemini returned non-JSON skill-check plan. Using empty plan.")
        guarded_raw_text = str(
            _sanitize_gemini_creative_terms(raw_text, "skill-check plan response")
        )
        return SkillCheckPlanResult(raw_text=guarded_raw_text)

    if not isinstance(data, dict):
        LOGGER.warning("Gemini skill-check plan JSON response was not an object.")
        guarded_raw_text = str(
            _sanitize_gemini_creative_terms(raw_text, "skill-check plan response")
        )
        return SkillCheckPlanResult(raw_text=guarded_raw_text)

    data = _sanitize_gemini_creative_terms(data, "skill-check plan response")
    guarded_raw_text = json.dumps(data, ensure_ascii=False)

    _log_json_schema_warnings(
        data,
        SKILL_CHECK_PLAN_RESPONSE_JSON_SCHEMA,
        "skill-check plan response",
    )

    raw_checks = data.get("checks", [])

    if not isinstance(raw_checks, list):
        LOGGER.warning("Gemini skill-check plan checks was not a list. Ignoring it.")
        raw_checks = []

    checks: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for raw_check in raw_checks:
        if not isinstance(raw_check, dict):
            continue

        skill_name = str(raw_check.get("skill_name", raw_check.get("name", ""))).strip()

        if not skill_name:
            continue

        folded_name = skill_name.casefold()

        if folded_name in seen_names:
            continue

        payload: dict[str, Any] = {"skill_name": skill_name}
        difficulty = str(raw_check.get("difficulty", "")).strip()
        reason = str(raw_check.get("reason", "")).strip()
        dc = _optional_positive_int(raw_check.get("dc"))

        if dc is not None:
            payload["dc"] = dc
        elif difficulty:
            payload["difficulty"] = difficulty

        if reason:
            payload["reason"] = reason

        checks.append(payload)
        seen_names.add(folded_name)

    return SkillCheckPlanResult(checks=checks, raw_text=guarded_raw_text)


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
        guarded_raw_text = str(
            _sanitize_gemini_creative_terms(raw_text, "story response")
        )
        return AiNarrationResult(
            narrative_text=guarded_raw_text.strip(),
            raw_text=guarded_raw_text,
        )

    if not isinstance(data, dict):
        LOGGER.warning("Gemini JSON response was not an object. Using raw text fallback.")
        guarded_raw_text = str(
            _sanitize_gemini_creative_terms(raw_text, "story response")
        )
        return AiNarrationResult(
            narrative_text=guarded_raw_text.strip(),
            raw_text=guarded_raw_text,
        )

    data = _sanitize_gemini_creative_terms(data, "story response")
    guarded_raw_text = json.dumps(data, ensure_ascii=False)

    _log_json_schema_warnings(data, STORY_RESPONSE_JSON_SCHEMA, "story response")

    response_text = data.get("response", data.get("narrative_text"))

    if not isinstance(response_text, str) or not response_text.strip():
        LOGGER.warning("Gemini JSON response omitted response text.")
        response_text = "The narrator has no clear response."

    suggested_actions = _parse_suggested_actions(
        data.get("suggested_actions", []),
        response_label="story response",
    )

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
        raw_text=guarded_raw_text,
    )


def _parse_suggested_actions(raw_actions: Any, *, response_label: str) -> list[str]:
    """Parses suggested player action labels from Gemini response data."""

    if not isinstance(raw_actions, list):
        LOGGER.warning("Gemini %s suggested_actions was not a list. Ignoring it.", response_label)
        return []

    return [
        str(action).strip()
        for action in raw_actions
        if str(action).strip()
    ]


def _ensure_in_game_suggested_actions(
    result: AiNarrationResult,
    context_packet: dict[str, Any],
) -> AiNarrationResult:
    """Adds generic action choices when Gemini leaves an in-game turn with none."""

    if result.out_of_game or result.suggested_actions:
        return result

    if str(context_packet.get("packet_type", "")).strip() != "story_turn":
        return result

    LOGGER.warning(
        "Gemini omitted suggested_actions for an in-game story turn; "
        "using fallback suggestions."
    )
    fallback_actions = list(FALLBACK_SUGGESTED_ACTIONS)

    return AiNarrationResult(
        narrative_text=_format_visible_response(result.narrative_text, fallback_actions),
        suggested_actions=fallback_actions,
        suggested_events=result.suggested_events,
        out_of_game=result.out_of_game,
        raw_text=result.raw_text,
    )


def _ensure_status_event_for_in_game_response(
    result: AiNarrationResult,
    context_packet: dict[str, Any],
) -> AiNarrationResult:
    """Adds a no-advance status event when Gemini omits one for an in-game turn."""

    if result.out_of_game or _is_continuation_request(context_packet):
        return result

    if str(context_packet.get("packet_type", "")).strip() != "story_turn":
        return result

    if any(
        _raw_event_type(event) in {"StatusUpdatedEvent", "LocationChangedEvent"}
        for event in result.suggested_events
    ):
        return result

    scene = _state_subpacket(context_packet, "scene")
    payload = {
        "location": str(scene.get("location", "AUTO") or "AUTO"),
        "minutes_passed": "AUTO",
        "weather": str(scene.get("weather", "AUTO") or "AUTO"),
    }
    status_event = {
        "type": "StatusUpdatedEvent",
        "payload": payload,
    }
    LOGGER.warning(
        "Gemini omitted StatusUpdatedEvent for an in-game story turn; "
        "injecting no-advance status event."
    )

    return AiNarrationResult(
        narrative_text=result.narrative_text,
        suggested_actions=result.suggested_actions,
        suggested_events=[*result.suggested_events, status_event],
        out_of_game=result.out_of_game,
        raw_text=result.raw_text,
    )


def _ensure_skill_check_for_uncertain_player_command(
    result: AiNarrationResult,
    context_packet: dict[str, Any],
) -> AiNarrationResult:
    """Adds a fallback skill-check event for a clearly uncertain player command."""

    if result.out_of_game or _is_continuation_request(context_packet):
        return result

    if _resolved_skill_names_from_context(context_packet):
        return result

    if any(_raw_event_type(event) == "SkillCheckRequestedEvent" for event in result.suggested_events):
        return result

    command = str(context_packet.get("player_command", "")).strip()
    looks_uncertain = _looks_like_uncertain_action(command)
    skill_name = _infer_skill_check_name(
        command,
        context_packet,
        result.suggested_events,
    )

    if not skill_name:
        return result

    if not looks_uncertain and not _skill_text_matches_command(skill_name, command):
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


def _drop_duplicate_resolved_skill_check_events(
    result: AiNarrationResult,
    context_packet: dict[str, Any],
) -> AiNarrationResult:
    """Removes SkillCheckRequestedEvent entries for checks already resolved this turn."""

    resolved_skill_names = _resolved_skill_names_from_context(context_packet)

    if not resolved_skill_names:
        return result

    filtered_events = [
        event
        for event in result.suggested_events
        if not (
            _raw_event_type(event) == "SkillCheckRequestedEvent"
            and _event_payload_text(event, "skill_name", "name").casefold()
            in resolved_skill_names
        )
    ]

    if len(filtered_events) == len(result.suggested_events):
        return result

    LOGGER.warning(
        "Gemini requested already-resolved skill check(s); dropped duplicate event(s)."
    )
    return AiNarrationResult(
        narrative_text=result.narrative_text,
        suggested_actions=result.suggested_actions,
        suggested_events=filtered_events,
        out_of_game=result.out_of_game,
        raw_text=result.raw_text,
    )


def _resolved_skill_names_from_context(context_packet: dict[str, Any]) -> set[str]:
    """Reads resolved skill-check names from a story context packet."""

    state = context_packet.get("state", {})

    if not isinstance(state, dict):
        return set()

    skills = state.get("skills", {})

    if not isinstance(skills, dict):
        return set()

    resolved_checks = skills.get("resolved_checks_this_turn", [])

    if not isinstance(resolved_checks, list):
        return set()

    return {
        str(check.get("skill_name", check.get("name", ""))).strip().casefold()
        for check in resolved_checks
        if isinstance(check, dict)
        and str(check.get("skill_name", check.get("name", ""))).strip()
    }


def _is_continuation_request(context_packet: dict[str, Any]) -> bool:
    """Returns True when the UI asked to expand the latest story response."""

    continuation = context_packet.get("continuation_request")

    if not isinstance(continuation, dict):
        return False

    return bool(continuation.get("active", False))


def _ensure_inventory_for_collected_reagents(
    result: AiNarrationResult,
    context_packet: dict[str, Any],
) -> AiNarrationResult:
    """Adds inventory events for useful materials Gemini says the player collected."""

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
                "item_type": "Item",
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
        "Gemini omitted InventoryItemAddedEvent for collected crafting item(s): %s",
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
    """Builds an inventory description from a useful-item discovery payload."""

    description = str(payload.get("description", payload.get("notes", ""))).strip()

    if description:
        return description

    uses = _join_payload_list(payload.get("uses", []))
    location = str(payload.get("location", "")).strip()
    details = []

    if location:
        details.append(f"Found in {location}")

    if uses:
        details.append(f"Uses: {uses}")

    return "; ".join(details) or "A discovered useful crafting item/material."


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


def _infer_skill_check_name(
    command: str,
    context_packet: dict[str, Any],
    suggested_events: list[dict[str, Any]] | None = None,
) -> str:
    """Infers the most relevant skill for a fallback check."""

    clean_command = command.casefold()
    known_skill_records = _known_skill_records(context_packet)
    known_skills = [record["name"] for record in known_skill_records]

    known_match = _find_known_skill_for_text(command, known_skill_records)

    if known_match:
        return known_match

    event_match = _find_event_skill_for_text(
        command,
        suggested_events or [],
        known_skills,
    )

    if event_match:
        return event_match

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

    if "skill check" in clean_command or "roll " in clean_command:
        return _find_known_skill("Awareness", known_skills) or "Awareness"

    return ""


def _known_skill_names(context_packet: dict[str, Any]) -> list[str]:
    """Reads known skill names from a story context packet."""

    return [record["name"] for record in _known_skill_records(context_packet)]


def _known_skill_records(context_packet: dict[str, Any]) -> list[dict[str, str]]:
    """Reads known skill names and descriptions from a story context packet."""

    raw_skills = (
        context_packet.get("state", {})
        .get("skills", {})
        .get("known_skills", [])
    )

    if not isinstance(raw_skills, list):
        return []

    skill_records: list[dict[str, str]] = []

    for raw_skill in raw_skills:
        if not isinstance(raw_skill, dict):
            continue

        name = str(raw_skill.get("name", "")).strip()

        if name:
            skill_records.append(
                {
                    "name": name,
                    "description": str(raw_skill.get("description", "")).strip(),
                }
            )

    return skill_records


def _find_known_skill(candidate: str, known_skills: list[str]) -> str:
    """Returns a known skill matching a fallback candidate."""

    candidate_folded = candidate.casefold()

    for skill_name in known_skills:
        if skill_name.casefold() == candidate_folded:
            return skill_name

    for skill_name in _skill_keyword_aliases(candidate):
        for known_skill in known_skills:
            if known_skill.casefold() == skill_name.casefold():
                return known_skill

    return _find_known_skill_for_text(
        candidate,
        [{"name": skill_name, "description": ""} for skill_name in known_skills],
    )


def _skill_keyword_aliases(candidate: str) -> tuple[str, ...]:
    """Returns explicit broad-category aliases for legacy keyword buckets."""

    candidate_folded = candidate.casefold()

    for skill_name, aliases in SKILL_KEYWORD_ALIASES.items():
        if skill_name.casefold() == candidate_folded:
            return aliases

    return ()


def _find_known_skill_for_text(
    text: str,
    known_skill_records: list[dict[str, str]],
) -> str:
    """Returns the known skill whose name or description best matches text."""

    scored_skills: list[tuple[float, int, str]] = []

    for record in known_skill_records:
        name = record["name"]
        name_score = _skill_text_match_score(name, text)
        description_score = _skill_description_match_score(
            record.get("description", ""),
            text,
        )
        score = max(name_score, description_score)

        if (
            name_score >= SKILL_NAME_MATCH_THRESHOLD
            or description_score >= SKILL_DESCRIPTION_MATCH_THRESHOLD
        ):
            scored_skills.append((score, len(name), name))

    if not scored_skills:
        return ""

    scored_skills.sort(key=lambda item: (-item[0], item[1], item[2].casefold()))
    return scored_skills[0][2]


def _find_event_skill_for_text(
    command: str,
    suggested_events: list[dict[str, Any]],
    known_skills: list[str],
) -> str:
    """Returns a skill from Gemini's XP/upsert events when it matches the command."""

    scored_skills: list[tuple[float, int, str]] = []

    for skill_name in _skill_names_from_events(suggested_events):
        score = _skill_text_match_score(skill_name, command)

        if score < SKILL_NAME_MATCH_THRESHOLD:
            continue

        known_match = _find_known_skill(skill_name, known_skills)
        matched_name = known_match or skill_name
        scored_skills.append((score, len(matched_name), matched_name))

    if not scored_skills:
        return ""

    scored_skills.sort(key=lambda item: (-item[0], item[1], item[2].casefold()))
    return scored_skills[0][2]


def _skill_names_from_events(events: list[dict[str, Any]]) -> list[str]:
    """Reads skill names Gemini used in skill-related non-check events."""

    skill_names: list[str] = []

    for event in events:
        event_type = _raw_event_type(event)

        if event_type not in {"SkillXpAddedEvent", "SkillUpsertedEvent"}:
            continue

        payload = event.get("payload", {})

        if not isinstance(payload, dict):
            continue

        skill_name = str(payload.get("skill_name", payload.get("name", ""))).strip()

        if skill_name:
            skill_names.append(skill_name)

    return skill_names


def _skill_text_matches_command(skill_name: str, command: str) -> bool:
    """Returns True when the player command directly resembles a skill name."""

    return _skill_text_match_score(skill_name, command) >= SKILL_NAME_MATCH_THRESHOLD


def _skill_text_match_score(skill_text: str, command: str) -> float:
    """Scores whether a command text is close enough to a skill name."""

    clean_skill = _normalized_match_text(skill_text)
    clean_command = _normalized_match_text(command)

    if not clean_skill or not clean_command:
        return 0.0

    skill_tokens = _word_tokens(clean_skill)
    coverage_score = _token_coverage_score(clean_skill, clean_command)
    sequence_score = _sequence_window_score(clean_skill, clean_command)
    rapidfuzz_score = 0.0

    if _rapidfuzz_fuzz is not None:
        rapidfuzz_score = max(
            float(_rapidfuzz_fuzz.WRatio(clean_skill, clean_command)),
            float(_rapidfuzz_fuzz.partial_ratio(clean_skill, clean_command)),
        )

        if len(skill_tokens) > 1 and coverage_score < 60.0:
            rapidfuzz_score = min(rapidfuzz_score, coverage_score)

    return max(coverage_score, sequence_score, rapidfuzz_score)


def _skill_description_match_score(description: str, command: str) -> float:
    """Scores a skill description against a command using significant word overlap."""

    description_terms = _expanded_token_set(description, significant=True)
    command_terms = _expanded_token_set(command, significant=True)

    if not description_terms or not command_terms:
        return 0.0

    overlap_count = len(description_terms.intersection(command_terms))

    if overlap_count >= 3:
        return 86.0

    if overlap_count == 2:
        return SKILL_DESCRIPTION_MATCH_THRESHOLD

    return 0.0


def _token_coverage_score(skill_text: str, command: str) -> float:
    """Scores how many skill-name tokens or simple variants appear in the command."""

    skill_tokens = [
        token
        for token in _word_tokens(skill_text)
        if token not in SKILL_MATCH_STOPWORDS
    ]

    if not skill_tokens:
        return 0.0

    command_terms = _expanded_token_set(command)
    covered_tokens = 0

    for token in skill_tokens:
        if _token_variants(token).intersection(command_terms):
            covered_tokens += 1

    return (covered_tokens / len(skill_tokens)) * 100.0


def _sequence_window_score(skill_text: str, command: str) -> float:
    """Scores fuzzy similarity against command word windows."""

    skill_tokens = _word_tokens(skill_text)
    command_tokens = _word_tokens(command)

    if not skill_tokens or not command_tokens:
        return 0.0

    best_score = 0.0
    min_window = max(1, len(skill_tokens) - 1)
    max_window = min(len(command_tokens), len(skill_tokens) + 1)

    for window_size in range(min_window, max_window + 1):
        for index in range(0, len(command_tokens) - window_size + 1):
            phrase = " ".join(command_tokens[index : index + window_size])
            best_score = max(
                best_score,
                SequenceMatcher(None, skill_text, phrase).ratio() * 100.0,
            )

    return best_score


def _normalized_match_text(text: str) -> str:
    """Normalizes free text before skill-name matching."""

    return " ".join(_word_tokens(text))


def _word_tokens(text: str) -> list[str]:
    """Splits text into lower-case word tokens for matching."""

    return re.findall(r"[a-zA-Z0-9]+", str(text).casefold())


def _expanded_token_set(text: str, *, significant: bool = False) -> set[str]:
    """Returns word tokens plus simple inflection variants."""

    terms: set[str] = set()

    for token in _word_tokens(text):
        if significant and token in SKILL_MATCH_STOPWORDS:
            continue

        terms.update(_token_variants(token))

    return terms


def _token_variants(token: str) -> set[str]:
    """Returns simple word-form variants for skill matching."""

    clean_token = token.casefold().strip()

    if not clean_token:
        return set()

    variants = {clean_token}

    if clean_token.endswith("ies") and len(clean_token) > 4:
        variants.add(f"{clean_token[:-3]}y")

    if clean_token.endswith("s") and len(clean_token) > 3:
        variants.add(clean_token[:-1])

    if clean_token.endswith("ing") and len(clean_token) > 5:
        stem = clean_token[:-3]
        variants.add(stem)
        variants.add(f"{stem}e")

        if len(stem) > 2 and stem[-1] == stem[-2]:
            variants.add(stem[:-1])

    if clean_token.endswith("ed") and len(clean_token) > 4:
        stem = clean_token[:-2]
        variants.add(stem)
        variants.add(f"{stem}e")

    return variants


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
        guarded_raw_text = str(
            _sanitize_gemini_creative_terms(raw_text, "new-game response")
        )
        return AiWorldSetupResult(
            world_summary=guarded_raw_text.strip(),
            introductory_message=_format_visible_response(
                "The adventure begins.",
                FALLBACK_SUGGESTED_ACTIONS,
            ),
            suggested_actions=list(FALLBACK_SUGGESTED_ACTIONS),
            raw_text=guarded_raw_text,
        )

    if not isinstance(data, dict):
        LOGGER.warning("Gemini new-game JSON response was not an object.")
        guarded_raw_text = str(
            _sanitize_gemini_creative_terms(raw_text, "new-game response")
        )
        return AiWorldSetupResult(
            world_summary=guarded_raw_text.strip(),
            introductory_message=_format_visible_response(
                "The adventure begins.",
                FALLBACK_SUGGESTED_ACTIONS,
            ),
            suggested_actions=list(FALLBACK_SUGGESTED_ACTIONS),
            raw_text=guarded_raw_text,
        )

    data = _sanitize_gemini_creative_terms(data, "new-game response")
    guarded_raw_text = json.dumps(data, ensure_ascii=False)

    _log_json_schema_warnings(data, NEW_GAME_RESPONSE_JSON_SCHEMA, "new-game response")

    world_summary = str(data.get("world_summary", "")).strip()
    selected_genre = str(
        data.get("selected_genre", data.get("genre", ""))
    ).strip()
    start_location = clean_player_location_name(data.get("start_location", ""))
    calendar_settings = _parse_new_game_calendar_settings(data.get("calendar_settings"))
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
        _new_game_starter_items_payload(data)
    )
    finalized_currency_denominations = _parse_new_game_currency_denominations(data)
    finalized_currency_description = _parse_new_game_currency_description(data)
    finalized_starting_currency_balance_base_units = (
        _parse_new_game_starting_currency_balance(data)
    )
    raw_events = data.get("events", data.get("suggested_events", []))
    suggested_actions = _parse_suggested_actions(
        data.get("suggested_actions", []),
        response_label="new-game response",
    )

    if not world_summary:
        LOGGER.warning("Gemini new-game setup omitted world_summary.")
        world_summary = "The world is still taking shape."

    if not introductory_message:
        LOGGER.warning("Gemini new-game setup omitted introductory_message.")
        introductory_message = "The adventure begins."

    if not suggested_actions:
        LOGGER.warning(
            "Gemini new-game setup omitted suggested_actions; using fallback suggestions."
        )
        suggested_actions = list(FALLBACK_SUGGESTED_ACTIONS)

    introductory_message = _format_visible_response(
        introductory_message,
        suggested_actions,
    )

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
        calendar_settings=calendar_settings,
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
        suggested_actions=suggested_actions,
        suggested_events=suggested_events,
        raw_text=guarded_raw_text,
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


def _parse_new_game_calendar_settings(raw_calendar_settings: Any) -> dict[str, Any]:
    """Parses optional AI-generated calendar settings."""

    if not isinstance(raw_calendar_settings, dict) or not raw_calendar_settings:
        return {}

    return normalize_calendar_settings(raw_calendar_settings)


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

        name = _generalized_starter_item_name(
            raw_item.get("name", raw_item.get("item_name", ""))
        )

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
                "source_index": _parse_optional_source_index(raw_item),
            }
        )
        seen_names.add(name.casefold())

    return items


def _generalized_starter_item_name(raw_name: Any) -> str:
    """Removes setup bookkeeping words from finalized starter item names."""

    clean_name = str(raw_name or "").strip()

    if not clean_name:
        return ""

    words = [
        word.strip()
        for word in re.split(r"\s+", clean_name)
        if word.strip()
    ]

    if len(words) <= 1:
        return clean_name

    removed_setup_prefix = False

    while len(words) > 1 and words[0].strip(":-_").casefold() in {
        "starting",
        "initial",
        "beginning",
    }:
        words.pop(0)
        removed_setup_prefix = True

    if (
        len(words) > 1
        and words[0].strip(":-_").casefold() == "starter"
        and words[-1].strip(":-_").casefold()
        in {"amount", "quantity", "count", "total"}
    ):
        words.pop(0)
        removed_setup_prefix = True

    while (
        removed_setup_prefix
        and len(words) > 1
        and words[-1].strip(":-_").casefold()
        in {"amount", "quantity", "count", "total"}
    ):
        words.pop()

    return " ".join(words).strip()


def _new_game_starter_items_payload(data: dict[str, Any]) -> Any:
    """Reads finalized starter inventory from current and legacy response keys."""

    if "starting_items" in data:
        return data.get("starting_items")

    for alias in ["starter_items", "starting_inventory", "inventory"]:
        if alias in data:
            LOGGER.warning(
                "Gemini new-game setup used %s; treating it as starting_items.",
                alias,
            )
            return data.get(alias)

    return None


def _parse_optional_source_index(raw_item: dict[str, Any]) -> int | None:
    """Parses optional setup starter-item source index metadata."""

    raw_source_index = raw_item.get("source_index")

    if raw_source_index is None:
        return None

    try:
        return int(raw_source_index)
    except (TypeError, ValueError):
        return None


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


def _optional_positive_int(value: Any) -> int | None:
    """Parses a positive integer or returns None."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    if parsed < 1:
        return None

    return parsed


def _format_visible_response(response_text: str, suggested_actions: list[str]) -> str:
    """Combines response text and suggested actions for current UI display."""

    formatted_response = format_story_message(_strip_terminal_turn_prompt(response_text))

    if not suggested_actions:
        return formatted_response

    action_lines = [f"- {action}" for action in suggested_actions]
    question = "What do you do now?"

    if formatted_response.endswith(question):
        return f"{formatted_response}\n" + "\n".join(action_lines)

    if not formatted_response:
        return f"{question}\n" + "\n".join(action_lines)

    return f"{formatted_response}\n\n{question}\n" + "\n".join(action_lines)


def _strip_terminal_turn_prompt(text: str) -> str:
    """Removes Gemini-supplied end-of-turn prompt text before app formatting."""

    clean_text = str(text).strip()

    if not clean_text:
        return ""

    return re.sub(
        r"(?:\s*\n*)what\s+do\s+you\s+do\s+now\?\s*$",
        "",
        clean_text,
        flags=re.IGNORECASE,
    ).strip()


def format_story_message(text: str) -> str:
    """Formats player-facing story prose for immersive display."""

    clean_text = str(text).strip()

    if not clean_text:
        return ""

    formatted_blocks: list[str] = []

    for block in _split_markdown_blocks(clean_text):
        for sub_block in _split_mixed_markdown_block(block):
            if _is_markdown_structural_block(sub_block):
                formatted_blocks.append(sub_block.strip())
                continue

            sentences = _split_story_sentences(" ".join(_clean_block_lines(sub_block)))

            if sentences:
                formatted_blocks.append("\n\n".join(sentences))

    return _join_formatted_story_blocks(formatted_blocks)


def _split_markdown_blocks(text: str) -> list[str]:
    """Splits text into blank-line-separated Markdown blocks."""

    blocks: list[str] = []
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        if raw_line.strip():
            current_lines.append(raw_line.rstrip())
            continue

        if current_lines:
            blocks.append("\n".join(current_lines))
            current_lines = []

    if current_lines:
        blocks.append("\n".join(current_lines))

    return blocks


def _clean_block_lines(block: str) -> list[str]:
    """Returns non-empty stripped lines from one Markdown block."""

    return [line.strip() for line in block.splitlines() if line.strip()]


def _split_mixed_markdown_block(block: str) -> list[str]:
    """Splits blocks that mix prose with Markdown list/heading lines."""

    sub_blocks: list[str] = []
    current_lines: list[str] = []
    current_is_structural: bool | None = None
    in_fence = False

    for raw_line in block.splitlines():
        clean_line = raw_line.strip()
        is_structural = _is_markdown_structural_line(clean_line, in_fence)

        if clean_line.startswith("```"):
            in_fence = not in_fence

        if current_is_structural is None:
            current_is_structural = is_structural
        elif current_is_structural != is_structural:
            if current_lines:
                sub_blocks.append("\n".join(current_lines))
            current_lines = []
            current_is_structural = is_structural

        current_lines.append(raw_line.rstrip())

    if current_lines:
        sub_blocks.append("\n".join(current_lines))

    return sub_blocks


def _is_markdown_structural_block(block: str) -> bool:
    """Returns True when a block should keep its Markdown line structure."""

    in_fence = False

    for line in block.splitlines():
        clean_line = line.strip()

        if _is_markdown_structural_line(clean_line, in_fence):
            return True

        if clean_line.startswith("```"):
            in_fence = not in_fence

    return False


def _is_markdown_structural_line(clean_line: str, in_fence: bool = False) -> bool:
    """Returns True for Markdown lines whose structure should be preserved."""

    return bool(
        in_fence
        or clean_line.startswith("```")
        or re.match(r"^(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+|---+$)", clean_line)
    )


def _join_formatted_story_blocks(blocks: list[str]) -> str:
    """Joins formatted story blocks while keeping action lists tight."""

    clean_blocks = [block.strip() for block in blocks if block.strip()]

    if not clean_blocks:
        return ""

    formatted = clean_blocks[0]

    for block in clean_blocks[1:]:
        separator = "\n\n"

        if formatted.endswith("What do you do now?") and _is_markdown_structural_line(
            block.splitlines()[0].strip()
        ):
            separator = "\n"

        formatted = f"{formatted}{separator}{block}"

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
