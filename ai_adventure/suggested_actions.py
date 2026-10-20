"""Player-facing action labels and separately composed submission prompts."""
import re
from typing import Any

ACTION_SCHEMA = {"anyOf": [
    {"type": "object", "properties": {
        "label": {"type": "string", "maxLength": 80, "description": "Short, friendly button label, e.g. Rest and eat."},
        "prompt": {"type": "string", "maxLength": 600, "description": "Complete player action expressing only this option's intent. Use I will for first/second-person narration; the character name followed by will for third person. Never assert success or rewards."},
    }, "required": ["label", "prompt"], "additionalProperties": False},
    {"type": "string"},
]}

ACTION_RULE = (
    "Return suggested_actions as objects with label and prompt. label is a short, friendly "
    "button caption (e.g. Rest and eat); prompt is the fuller player action to submit on click. "
    "Use the selected narration point of view: first/second person submissions start with "
    "'I will'; third person submissions start with the player character's name and 'will'. "
    "Describe intent, never guaranteed success, invented dialogue, rewards, or additional "
    "unrequested actions. Keep labels and prompts faithful to the same option. "
    "Example: label='Rest and eat', prompt='I will take some time to rest and eat.'"
)


def action_prompts(raw_actions: Any) -> dict[str, str]:
    return {
        str(action["label"]).strip(): str(action["prompt"]).strip()
        for action in raw_actions if isinstance(action, dict)
        and str(action.get("label", "")).strip() and str(action.get("prompt", "")).strip()
    } if isinstance(raw_actions, list) else {}


def compose_action_prompt(text: str, *, style: str = "second_person_limited", player_name: str = "") -> str:
    """Compose intent without confusing second-person narration with user speech."""
    text = str(text).strip()
    name = str(player_name).strip() or "The player character"
    actors = r"I|you|the player character|" + re.escape(name)
    intent = re.sub(r"^(?:" + actors + r")\s+(?:will\s+|intend to\s+)", "", text, flags=re.I)
    if intent == text:
        intent = re.sub(r"^(?:I|you)\s+", "", intent, flags=re.I)
    if not intent:
        return ""
    # Preserve proper names and acronyms; ordinary imperative verbs become clauses.
    words = intent.split(" ", 1)
    if words[0] in {"Look", "Ask", "Rest", "Eat", "Take", "Inspect", "Check", "Head", "Search", "Go", "Talk", "Follow", "Examine", "Open", "Wait", "Continue", "Review", "Put"}:
        intent = intent[0].lower() + intent[1:]
    actor = name if str(style).startswith("third_person") else "I"
    return f"{actor} will {intent.rstrip('.')}."


def saved_actions(
    labels: list[str], prompts: dict[str, str], *, style: str = "second_person_limited", player_name: str = "",
) -> list[dict[str, str]]:
    return [{"label": str(label), "prompt": compose_action_prompt(prompts.get(label, label), style=style, player_name=player_name)}
            for label in labels[:4] if str(label).strip()]
