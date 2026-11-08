"""Internal, portable teaching subjects for instructional inventory items."""
import re
from typing import Any

from ai_adventure.stats import ATTRIBUTES


SKILL_BOOK_RULE = (
    "Instructional books/manuals may carry internal skill_book metadata with skill_name, "
    "attribute and description (the skill's uses/scope). Assign the actual subject taught: "
    "Dyeing covers preparing/applying dyes and mordants; Foraging covers identifying and "
    "gathering wild resources. Do not substitute a familiar learned skill for a distinct "
    "unlearned subject. For new books set skill_book on InventoryItemAddedEvent; for an "
    "existing unlinked book use InventoryItemModifiedEvent in the same response as study. "
    "Base the link on established instructional content, not the title alone. Preserve "
    "an established link through ordinary updates and item moves. Meaningful study of "
    "an accessible linked book uses SkillBookStudiedEvent with item_name and a concrete "
    "reason describing the material studied. Python teaches its linked skill at level 1 "
    "when unknown, or awards 1 training XP when known, subject to the usual once-per-skill "
    "per-message and Master limits. Do not also emit SkillXpAddedEvent or SkillUpsertedEvent "
    "for that study. No proficiency or successful roll is required. Mere possession, "
    "opening, skimming, rereading the title, or checking inventory is not training. "
    "These links are internal metadata, not secret story facts or UI labels."
)


SKILL_BOOK_SCHEMA = {
    "type": "object",
    "properties": {"skill_name": {"type": "string", "minLength": 1},
                   "attribute": {"type": "string", "enum": list(ATTRIBUTES)},
                   "description": {"type": "string", "minLength": 1}},
    "required": ["skill_name", "attribute", "description"], "additionalProperties": False,
}


def normalize_skill_book(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("skill_book must contain skill_name, attribute and scope description.")
    result = {key: str(value.get(key, "") or "").strip()
              for key in ("skill_name", "attribute", "description")}
    if not all(result.values()) or result["attribute"] not in ATTRIBUTES:
        raise ValueError("skill_book requires a skill name, valid related stat and scope description.")
    return result


def selected_study_book(items: list[dict], command: str) -> dict | None:
    """Require explicit study intent and an unambiguous accessible book reference."""
    text = " ".join(re.findall(r"\w+", command.casefold()))
    if not re.search(r"\b(?:read|reading|study|studying)\b", text):
        return None
    if re.search(r"\b(?:not|never|don t|won t|glance|skim|skimming|title|cover)\b", text):
        return None
    words = set(text.split())
    matches = []
    for item in items:
        if not item.get("quantity", 0):
            continue
        title = " ".join(re.findall(r"\w+", item["name"].casefold()))
        title_words = set(title.split()) - {"the", "a", "of", "on"}
        if f" {title} " in f" {text} " or len(words & title_words) >= 2:
            matches.append(item)
    return matches[0] if len(matches) == 1 else None


def reading_context(items: list[dict], command: str) -> dict:
    books = [item for item in items if item.get("metadata", {}).get("skill_book")
             or str(item.get("category", "")).casefold() in {"book", "manual", "scroll", "document"}]
    selected = selected_study_book(books, command)
    if not selected:
        return {}
    return {"item_name": selected["name"], "item_uuid": selected.get("metadata", {}).get("item_uuid", ""),
            "description": selected.get("description", ""),
            "skill_book": selected.get("metadata", {}).get("skill_book")}


def normalize_book_training(events: list[dict], items: list[dict], command: str) -> list[dict]:
    """Route legacy awards for a simple book-study turn through its saved subject.

    Leave mixed actions alone: they can legitimately train additional skills.
    Newly defined links are visible to this normalization before events are applied.
    """
    target = reading_context(items, command)
    if not target:
        return events
    link = target.get("skill_book")
    for event in events:
        payload = event.get("payload", {})
        if (event.get("type") == "InventoryItemModifiedEvent"
                and str(payload.get("target_name", "")).casefold() == target["item_name"].casefold()
                and payload.get("skill_book") and not link):
            link = normalize_skill_book(payload["skill_book"])
    if not link:
        return events
    training = {"SkillXpAddedEvent", "SkillUpsertedEvent", "SkillBookStudiedEvent"}
    if re.search(r"\b(?:practice|practise|practicing|practising|train|training)\b", command, re.I):
        return events
    proposed_skills = {str(event.get("payload", {}).get("skill_name", event.get("payload", {}).get("name", ""))).casefold()
                       for event in events if event.get("type") in {"SkillXpAddedEvent", "SkillUpsertedEvent"}}
    if len(proposed_skills) > 1:
        return events
    harmless = {"StatusUpdatedEvent", "MusicChangedEvent", "SoundEffectChangedEvent", "BackgroundAmbienceChangedEvent", "FlagSetEvent"}
    # Inventory changes may represent other actions. Only a link-only update is
    # part of this simple study turn; all other actions retain their own rewards.
    def link_update(event):
        payload = event.get("payload", {})
        return (event.get("type") == "InventoryItemModifiedEvent" and payload.get("skill_book")
                and set(payload) <= {"target_name", "skill_book"})
    if any(event.get("type") not in training | harmless and not link_update(event) for event in events):
        return events
    if not any(event.get("type") in training for event in events):
        return events
    updates = [event for event in events if link_update(event)]
    others = [event for event in events if event.get("type") not in training and not link_update(event)]
    study = {"type": "SkillBookStudiedEvent", "payload": {"item_name": target["item_name"],
             "reason": f"Meaningful study of {target['item_name']}: {link['description']}",
             "original_training_events": [event for event in events if event.get("type") in training]}}
    return updates + [study] + others
