"""Typed player intent shared by the conversation, planner, and narrator."""

import re
from dataclasses import asdict, dataclass
from typing import Any


ACTION_LABELS = {"say": "Say", "do": "Do", "retcon": "Retcon", "ask_gm": "Ask the GM"}
PRESENT_ACTIONS = {"say", "do"}


@dataclass(frozen=True)
class TurnAction:
    action_type: str
    text: str


def action_packet(actions: list[TurnAction]) -> list[dict[str, Any]]:
    return [{"action_index": index, **asdict(action)} for index, action in enumerate(actions, 1)]


def action_text(actions: list[TurnAction]) -> str:
    if len(actions) == 1:
        return actions[0].text
    return "\n\n".join(
        f"{index}. {ACTION_LABELS[action.action_type]}: {action.text}"
        for index, action in enumerate(actions, 1)
    )


def explicit_action_condition(action: TurnAction, index: int) -> tuple[int, str] | None:
    """Recognize direct references to prior action results in the player's text."""
    match = re.match(
        r"\s*(?:only\s+)?if\s+(?:\[?action\s+#?(\d+)\]?|(?:the\s+)?previous action|that)\s+(fails|succeeds)\b",
        action.text, re.IGNORECASE,
    )
    if not match:
        return None
    number = match[1]
    if number is None and index == 1:
        return None
    previous = index - 1
    if number is not None:
        previous = int(number) if len(number) < 10 else 0
    return previous, "failure" if match[2].casefold() == "fails" else "success"


def has_present_actions(actions: list[dict[str, Any]], inactive_actions: dict[int, str]) -> bool:
    return not actions or any(action["action_type"] in PRESENT_ACTIONS and action["action_index"] not in inactive_actions
                              for action in actions)


def turn_events(events: list[dict[str, Any]], actions: list[dict[str, Any]],
                inactive_actions: dict[int, str]) -> list[dict[str, Any]]:
    """Allow only events attributable to an active in-world entry."""
    if not actions or all(action["action_type"] == "ask_gm" for action in actions):
        return events
    allowed = []
    for event in events:
        index = event.get("action_index", 1 if len(actions) == 1 else None)
        if type(index) is not int or index in inactive_actions or not 1 <= index <= len(actions):
            continue
        kind = actions[index - 1]["action_type"]
        event_type = event.get("type")
        if kind == "ask_gm" or event_type == "D20TestRequestedEvent":
            continue  # Checks are rolled only during planning.
        if kind == "retcon":
            reason = event.get("retcon_correction_reason")
            if not isinstance(reason, str) or not reason.strip() or event_type not in {
                "StatusUpdatedEvent", "InventoryItemAddedEvent", "InventoryItemModifiedEvent",
                "InventoryItemRemovedEvent", "CurrencyChangedEvent", "NpcUpsertedEvent", "FlagSetEvent",
            }:
                continue
            if event_type == "StatusUpdatedEvent":
                proposed = event.get("payload")
                if not isinstance(proposed, dict):
                    continue
                payload = {key: value for key, value in proposed.items() if key in {"location", "weather"}
                           and isinstance(value, str) and value.strip().upper() not in {"AUTO", "SAME", "SKIP", ""}}
                if not payload:
                    continue
                event = {**event, "payload": payload}
        allowed.append({**event, "action_index": index})
    return sorted(allowed, key=lambda event: event["action_index"])
