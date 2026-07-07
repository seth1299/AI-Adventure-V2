from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_MODEL_INTELLIGENCE = "faster"
DEFAULT_MODEL_TONE = "neutral"
DEFAULT_RESPONSE_LENGTH = "normal"
NEW_GAME_MAX_OUTPUT_TOKENS = {
    "super_brief": 6144,
    "brief": 8192,
    "normal": None,
    "descriptive": 12288,
    "verbose": 16384,
}

MODEL_INTELLIGENCE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": "faster",
        "label": "Faster",
        "description": (
            "Uses minimal model thinking for lower latency while preserving the "
            "usual game-master behavior."
        ),
        "thinking_level": "minimal",
    },
    {
        "value": "smarter",
        "label": "Smarter",
        "description": (
            "Uses high model thinking for more deliberate reasoning. Responses may "
            "take noticeably longer."
        ),
        "thinking_level": "high",
    },
)

MODEL_TONE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": "neutral",
        "label": "Neutral",
        "description": "Uses the AI's normal voice without a special tonal adjustment.",
        "instruction": (
            "Use a neutral, natural narrative voice without a special tonal adjustment."
        ),
    },
    {
        "value": "professional",
        "label": "Professional",
        "description": "Uses elevated vocabulary and more formal prose.",
        "instruction": (
            "Use formal, polished prose and a more sophisticated vocabulary while "
            "remaining clear."
        ),
    },
    {
        "value": "friendly",
        "label": "Friendly",
        "description": "Uses warm, encouraging language and an approachable voice.",
        "instruction": (
            "Use warm, encouraging, approachable language without becoming patronizing."
        ),
    },
    {
        "value": "serious",
        "label": "Serious",
        "description": "Uses colder, restrained prose without jokes or comic asides.",
        "instruction": (
            "Use a cold, restrained, serious voice. Do not make jokes or add comic asides."
        ),
    },
    {
        "value": "cynical",
        "label": "Cynical",
        "description": "Uses a dry voice and frequent sarcasm without attacking the player.",
        "instruction": (
            "Use a dry, cynical voice with frequent sarcasm, but do not become hostile "
            "or spiteful toward the player."
        ),
    },
    {
        "value": "efficient",
        "label": "Efficient",
        "description": "Uses plain, direct wording with fewer flourishes and digressions.",
        "instruction": (
            "Use plain, direct wording. Avoid digressions, euphemistic circling, and "
            "unnecessary flowery description."
        ),
    },
    {
        "value": "quirky",
        "label": "Quirky",
        "description": "Uses a playful, zany voice with jokes and gentle teasing.",
        "instruction": (
            "Use a playful, occasionally zany voice. Jokes and gentle teasing are "
            "welcome, but never obscure important game information."
        ),
    },
)

RESPONSE_LENGTH_OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "value": "super_brief",
        "label": "Super Brief",
        "description": (
            "Uses the shortest practical descriptions, no flowery prose, and the "
            "smallest response cap."
        ),
        "instruction": (
            "Make the response field as short as practical. Use only essential action, "
            "consequence, and scene information; use no flowery prose."
        ),
        "max_output_tokens": 1536,
    },
    {
        "value": "brief",
        "label": "Brief",
        "description": "Uses shorter descriptions, limited flourishes, and a smaller cap.",
        "instruction": (
            "Keep the response field concise. Use short descriptions and very little "
            "flowery prose."
        ),
        "max_output_tokens": 3072,
    },
    {
        "value": "normal",
        "label": "Normal",
        "description": (
            "Uses the game's normal response detail and leaves Gemini's output-token "
            "cap unchanged."
        ),
        "instruction": (
            "Make no special response-length adjustment; use the normal amount of "
            "detail for the scene."
        ),
        "max_output_tokens": None,
    },
    {
        "value": "descriptive",
        "label": "Descriptive",
        "description": (
            "Adds somewhat richer descriptions of NPCs, locations, items, and scenes."
        ),
        "instruction": (
            "Use somewhat richer sensory and physical description, especially for NPCs, "
            "locations, items, and newly encountered details."
        ),
        "max_output_tokens": 8192,
    },
    {
        "value": "verbose",
        "label": "Verbose",
        "description": (
            "Uses highly detailed, flowery prose and the largest response cap."
        ),
        "instruction": (
            "Use highly detailed, evocative, and flowery prose. Describe characters, "
            "locations, items, actions, and consequences generously."
        ),
        "max_output_tokens": 12288,
    },
)

CONTENT_HARM_CATEGORY_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": "HARM_CATEGORY_HARASSMENT",
        "label": "Harassment",
        "description": "Harassing, insulting, bullying, or threatening fictional content.",
    },
    {
        "value": "HARM_CATEGORY_HATE_SPEECH",
        "label": "Hate Speech",
        "description": "Hateful fictional speech or conduct targeting protected groups.",
    },
    {
        "value": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "label": "Sexually Explicit",
        "description": "Sexually explicit fictional material.",
    },
    {
        "value": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "label": "Dangerous Content",
        "description": "Dangerous acts or content that could facilitate physical harm.",
    },
    {
        "value": "HARM_CATEGORY_CIVIC_INTEGRITY",
        "label": "Civic Integrity",
        "description": (
            "Content that could harm civic or election integrity. Gemini marks this "
            "category as deprecated, but still exposes it in GenerateContent."
        ),
    },
)

ALL_CONTENT_HARM_CATEGORIES = tuple(
    option["value"] for option in CONTENT_HARM_CATEGORY_OPTIONS
)
DEFAULT_ALLOWED_CONTENT_HARM_CATEGORIES = ALL_CONTENT_HARM_CATEGORIES

_INTELLIGENCE_BY_VALUE = {
    str(option["value"]): option for option in MODEL_INTELLIGENCE_OPTIONS
}
_TONE_BY_VALUE = {str(option["value"]): option for option in MODEL_TONE_OPTIONS}
_RESPONSE_LENGTH_BY_VALUE = {
    str(option["value"]): option for option in RESPONSE_LENGTH_OPTIONS
}
_CONTENT_BY_VALUE = {
    str(option["value"]): option for option in CONTENT_HARM_CATEGORY_OPTIONS
}


def default_ai_mode_settings() -> dict[str, Any]:
    """Returns JSON-safe defaults for save-specific AI mode settings."""

    return {
        "model_intelligence": DEFAULT_MODEL_INTELLIGENCE,
        "model_tone": DEFAULT_MODEL_TONE,
        "response_length": DEFAULT_RESPONSE_LENGTH,
        "allowed_content_categories": list(DEFAULT_ALLOWED_CONTENT_HARM_CATEGORIES),
    }


def normalize_ai_mode_preferences(raw_preferences: Any) -> dict[str, Any]:
    """Normalizes AI mode preferences and adds their runtime metadata."""

    raw = raw_preferences if isinstance(raw_preferences, Mapping) else {}
    intelligence = _normalize_option_value(
        raw.get("model_intelligence"),
        _INTELLIGENCE_BY_VALUE,
        DEFAULT_MODEL_INTELLIGENCE,
    )
    tone = _normalize_option_value(
        raw.get("model_tone"),
        _TONE_BY_VALUE,
        DEFAULT_MODEL_TONE,
    )
    response_length = _normalize_option_value(
        raw.get("response_length"),
        _RESPONSE_LENGTH_BY_VALUE,
        DEFAULT_RESPONSE_LENGTH,
    )
    allowed_categories = _normalize_allowed_content_categories(
        raw.get("allowed_content_categories")
    )

    intelligence_option = _INTELLIGENCE_BY_VALUE[intelligence]
    tone_option = _TONE_BY_VALUE[tone]
    response_length_option = _RESPONSE_LENGTH_BY_VALUE[response_length]
    raw_max_output_tokens = response_length_option["max_output_tokens"]
    allowed_labels = [
        _CONTENT_BY_VALUE[category]["label"] for category in allowed_categories
    ]
    blocked_categories = [
        category
        for category in ALL_CONTENT_HARM_CATEGORIES
        if category not in allowed_categories
    ]
    blocked_labels = [
        _CONTENT_BY_VALUE[category]["label"] for category in blocked_categories
    ]

    return {
        "model_intelligence": intelligence,
        "model_intelligence_label": intelligence_option["label"],
        "model_intelligence_description": intelligence_option["description"],
        "thinking_level": intelligence_option["thinking_level"],
        "model_tone": tone,
        "model_tone_label": tone_option["label"],
        "model_tone_description": tone_option["description"],
        "model_tone_instruction": tone_option["instruction"],
        "response_length": response_length,
        "response_length_label": response_length_option["label"],
        "response_length_description": response_length_option["description"],
        "response_length_instruction": response_length_option["instruction"],
        "max_output_tokens": (
            int(raw_max_output_tokens)
            if raw_max_output_tokens is not None
            else None
        ),
        "new_game_max_output_tokens": NEW_GAME_MAX_OUTPUT_TOKENS[
            response_length
        ],
        "allowed_content_categories": allowed_categories,
        "allowed_content_labels": allowed_labels,
        "blocked_content_categories": blocked_categories,
        "blocked_content_labels": blocked_labels,
        "model_content_rules": _content_prompt_instruction(
            allowed_labels=allowed_labels,
            blocked_labels=blocked_labels,
        ),
    }


def ai_mode_preferences_from_settings(settings: Any) -> dict[str, Any]:
    """Reads the four AI mode values from a save settings mapping."""

    values = settings if isinstance(settings, Mapping) else {}
    return normalize_ai_mode_preferences(
        {
            "model_intelligence": values.get(
                "ai.model_intelligence",
                DEFAULT_MODEL_INTELLIGENCE,
            ),
            "model_tone": values.get("ai.model_tone", DEFAULT_MODEL_TONE),
            "response_length": values.get(
                "ai.response_length",
                DEFAULT_RESPONSE_LENGTH,
            ),
            "allowed_content_categories": values.get(
                "ai.allowed_content_categories",
                list(DEFAULT_ALLOWED_CONTENT_HARM_CATEGORIES),
            ),
        }
    )


def ai_mode_preferences_from_context_packet(
    context_packet: Any,
) -> dict[str, Any]:
    """Reads AI mode preferences from a story context packet."""

    if not isinstance(context_packet, Mapping):
        return normalize_ai_mode_preferences({})

    state = context_packet.get("state")
    if isinstance(state, Mapping):
        preferences = state.get("player_ai_preferences")
        if isinstance(preferences, Mapping):
            return normalize_ai_mode_preferences(preferences)

    preferences = context_packet.get("player_ai_preferences")
    if isinstance(preferences, Mapping):
        return normalize_ai_mode_preferences(preferences)

    return normalize_ai_mode_preferences({})


def build_ai_mode_prompt_guidance(context_packet: Any) -> str:
    """Builds model-facing tone, length, and content instructions."""

    preferences = ai_mode_preferences_from_context_packet(context_packet)
    return (
        "Player-selected AI modes (apply these to player-facing prose):\n"
        f"- Model tone — {preferences['model_tone_label']}: "
        f"{preferences['model_tone_instruction']}\n"
        f"- Response length — {preferences['response_length_label']}: "
        f"{preferences['response_length_instruction']}\n"
        f"- Model content: {preferences['model_content_rules']}\n"
        "- These preferences never override JSON completeness, durable-state accuracy, "
        "NPC knowledge boundaries, or hidden-information rules."
    )


def _normalize_option_value(
    raw_value: Any,
    options_by_value: Mapping[str, Mapping[str, Any]],
    default: str,
) -> str:
    clean_value = str(raw_value or "").strip().casefold().replace("-", "_").replace(" ", "_")

    if clean_value in options_by_value:
        return clean_value

    for value, option in options_by_value.items():
        label_value = str(option.get("label", "")).strip().casefold().replace(" ", "_")
        if clean_value == label_value:
            return value

    return default


def _normalize_allowed_content_categories(raw_categories: Any) -> list[str]:
    if raw_categories is None:
        return list(DEFAULT_ALLOWED_CONTENT_HARM_CATEGORIES)

    if isinstance(raw_categories, str):
        if raw_categories.strip().casefold() in {
            "no restrictions",
            "no_restrictions",
            "all",
        }:
            return list(ALL_CONTENT_HARM_CATEGORIES)
        values: list[Any] = [raw_categories]
    elif isinstance(raw_categories, (list, tuple, set, frozenset)):
        values = list(raw_categories)
    else:
        return list(DEFAULT_ALLOWED_CONTENT_HARM_CATEGORIES)

    selected: set[str] = set()
    for raw_value in values:
        clean_value = str(raw_value or "").strip()
        if clean_value in _CONTENT_BY_VALUE:
            selected.add(clean_value)
            continue

        clean_label = clean_value.casefold().replace("_", " ")
        for category, option in _CONTENT_BY_VALUE.items():
            if clean_label == str(option["label"]).casefold():
                selected.add(category)
                break

    return [
        category
        for category in ALL_CONTENT_HARM_CATEGORIES
        if category in selected
    ]


def _content_prompt_instruction(
    *,
    allowed_labels: list[str],
    blocked_labels: list[str],
) -> str:
    if not blocked_labels:
        return (
            "No Restrictions is selected. Mature fictional content is allowed when "
            "it fits the scene, genre, and player choices. Assume the player and "
            "player character are adults of legal drinking age unless the character "
            "profile explicitly says otherwise. Taverns and other adult fictional "
            "locations need not be sanitized into harmless substitutes. Alcohol, "
            "drunken patrons, gambling, brawls, shady deals, violence, injury, blood, "
            "corpses, criminality, cruelty, corruption, and oppressive fictional social "
            "attitudes may appear when appropriate. Keep dangerous content story-focused "
            "rather than instructional. Use fictional in-world terms, including "
            "fictional in-world slurs, only for fictional cultures, species, factions, "
            "classes, guilds, or regions; do not use real-world slurs against protected "
            "classes, and do not invent or use real-world slurs or insert harmful "
            "content gratuitously."
        )

    blocked_text = ", ".join(blocked_labels)
    if allowed_labels:
        allowed_text = ", ".join(allowed_labels)
        return (
            f"The checked categories may appear when story-appropriate: {allowed_text}. "
            f"Do not generate content in the unchecked categories: {blocked_text}."
        )

    return (
        "No harm categories are checked. Do not generate content in these categories: "
        f"{blocked_text}."
    )
