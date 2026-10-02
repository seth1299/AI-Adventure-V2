"""Shared validation for container proposals before narration is committed."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from ai_adventure.combat import normalize_item_metadata
from ai_adventure.container_access import has_immediate_container_unlock_method


CONTAINER_FLOW_RULE = (
    "ContainerOpenedEvent and ContainerContentsTakenEvent may occur in the same turn, "
    "in that order. Unknown contents are not empty: when contents_initialized is false, "
    "include a complete contents manifest (currency_base_units and items) in "
    "ContainerOpenedEvent. Initialize it once, after access checks succeed. Never "
    "replace already initialized contents. container_authority is private GM state; "
    "reveal its contents only after a permitted opening. Transfer rewards exclusively "
    "through ContainerContentsTakenEvent, including nested containers such as vials or "
    "coin pouches; never add the same loot with InventoryItemAddedEvent or "
    "CurrencyChangedEvent. Use item_names and take_currency for selective taking; "
    "omitting them takes all stored contents. Narrate only the manifest and the actual "
    "selected transfer. Leave untaken items inside the container."
)


class ContainerFlowError(ValueError):
    """An inconsistent container turn must be repaired rather than committed."""


def selected_contents(contents: dict[str, Any], payload: dict[str, Any]) -> tuple[int, list[dict], dict]:
    """Select whole stored item records, returning an immutable remainder."""
    items = contents.get("items", [])
    requested = payload.get("item_names")
    if requested is not None:
        if not isinstance(requested, list) or any(not isinstance(name, str) or not name.strip() for name in requested):
            raise ContainerFlowError("item_names must be a list of nonblank stored item names.")
        wanted = {name.strip().casefold() for name in requested}
        available = [str(item.get("name", "")).strip().casefold() for item in items]
        if wanted - set(available):
            raise ContainerFlowError("Requested item_names are not in the stored container contents.")
        if any(available.count(name) != 1 for name in wanted):
            raise ContainerFlowError("Requested item_names are ambiguous in the stored contents.")
    else:
        wanted = {str(item.get("name", "")).strip().casefold() for item in items}
    take_currency = payload.get("take_currency", True)
    if not isinstance(take_currency, bool):
        raise ContainerFlowError("take_currency must be a boolean.")
    currency = int(contents.get("currency_base_units", 0)) if take_currency else 0
    taken = [deepcopy(item) for item in items if str(item.get("name", "")).strip().casefold() in wanted]
    remaining = {
        "currency_base_units": 0 if take_currency else int(contents.get("currency_base_units", 0)),
        "items": [deepcopy(item) for item in items if str(item.get("name", "")).strip().casefold() not in wanted],
    }
    return currency, taken, remaining


def validate_contents_manifest(contents: Any) -> None:
    """Reject missing or malformed contents instead of normalizing them to zero."""
    if not isinstance(contents, dict) or set(contents) != {"currency_base_units", "items"}:
        raise ContainerFlowError("A complete contents manifest needs currency_base_units and items.")
    amount = contents["currency_base_units"]
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise ContainerFlowError("Contents currency_base_units must be a nonnegative integer.")
    if not isinstance(contents["items"], list):
        raise ContainerFlowError("Contents items must be a list.")
    for item in contents["items"]:
        if not isinstance(item, dict) or any(not str(item.get(key, "")).strip() for key in ("name", "category", "description")):
            raise ContainerFlowError("Every contents item needs a name, category, and description.")
        for key, minimum in (("quantity", 1), ("value_base_units", 0)):
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ContainerFlowError(f"Contents item {key} must be an integer >= {minimum}.")


def container_event_issues(
    events: list[dict[str, Any]], context_packet: dict[str, Any], *, narrative_text: str = "",
) -> list[str]:
    """Simulate container event ordering and transfers using saved facts only."""
    inventory = context_packet.get("state", {}).get("inventory", {})
    items = inventory.get("items", [])
    authority = inventory.get("container_authority", items)
    containers = {}
    for item in authority:
        metadata = item.get("metadata", {})
        if isinstance(metadata.get("container"), dict):
            containers[str(item.get("name", "")).strip().casefold()] = deepcopy(metadata["container"])
    checks = context_packet.get("state", {}).get("skills", {}).get("resolved_checks_this_turn", [])
    interactions = {str(e.get("payload", {}).get("container_name", "")).strip().casefold()
                    for e in events if e.get("type") in {"ContainerOpenedEvent", "ContainerContentsTakenEvent"}}
    closed_additions = {str(e.get("payload", {}).get("item_name", "")).strip().casefold()
                        for e in events if e.get("type") == "InventoryItemAddedEvent"
                        and str(e.get("payload", {}).get("item_type", "")).casefold() == "container"}
    command = str(context_packet.get("player_command", "")).casefold()
    command_words = set(re.findall(r"[a-z0-9]+", command))
    container_kinds = {"bag", "box", "case", "chest", "container", "crate", "pouch", "purse", "sack", "satchel", "vial"}
    target_names = {name for name in containers if name in command or (
        set(re.findall(r"[a-z0-9]+", name)) & command_words & container_kinds
    )} if re.search(r"\b(open|unlock|inspect|search|take|loot|empty|contents|inside)\b", command) else set()
    blocked_access = False
    if re.search(r"\b(open|unlock|inspect|search|take|loot|empty)\b", command):
        for name, container in containers.items():
            if name not in target_names or container.get("is_open"):
                continue
            for flag, skill_key, dc_key, default in (
                ("is_locked", "lockpick_skill", "lockpick_dc", "Lockpicking"),
                ("is_trapped", "trap_disarm_skill", "trap_disarm_dc", "Sleight of Hand"),
            ):
                if not container.get(flag):
                    continue
                if flag == "is_locked" and has_immediate_container_unlock_method(items, name):
                    continue
                skill = str(container.get(skill_key) or default).casefold()
                dc = int(container.get(dc_key) or 10)
                blocked_access |= not any(str(check.get("skill_name", "")).casefold() == skill
                    and check.get("outcome") == "success" and int(check.get("total", 0)) >= dc for check in checks)
    protects = bool(interactions or closed_additions or blocked_access)
    issues = []
    revealed_currency = sum(int(container.get("contents", {}).get("currency_base_units", 0))
        for name, container in containers.items() if name in interactions and container.get("is_open"))
    transferred_currency = 0
    for event in events:
        kind = event.get("type")
        payload = event.get("payload", {})
        name = str(payload.get("container_name", "")).strip().casefold()
        try:
            if kind == "InventoryItemAddedEvent":
                added_name = str(payload.get("item_name", "")).strip().casefold()
                is_container = str(payload.get("item_type", "")).casefold() == "container"
                if protects and (not is_container or (interactions and added_name not in interactions)):
                    raise ContainerFlowError("Direct loot additions, including vials and pouches, bypass the stored transfer.")
                if is_container:
                    if added_name in containers and added_name in interactions:
                        raise ContainerFlowError("An existing container cannot be reacquired or reset while accessing its contents.")
                    metadata = normalize_item_metadata(payload, name=payload.get("item_name", ""), category="Container")
                    containers[added_name] = metadata["container"]
            elif kind == "CurrencyChangedEvent" and protects and int(payload.get("base_unit_amount", payload.get("amount", 0))) > 0:
                raise ContainerFlowError("Direct currency awards bypass the stored transfer.")
            elif kind in {"ContainerOpenedEvent", "ContainerContentsTakenEvent"}:
                if name not in containers:
                    raise ContainerFlowError(f"Container is missing from authoritative inventory: {name}.")
                container = containers[name]
                if kind == "ContainerOpenedEvent":
                    for flag, skill_key, dc_key, default in (
                        ("is_locked", "lockpick_skill", "lockpick_dc", "Lockpicking"),
                        ("is_trapped", "trap_disarm_skill", "trap_disarm_dc", "Sleight of Hand"),
                    ):
                        if not container.get(flag):
                            continue
                        if flag == "is_locked" and has_immediate_container_unlock_method(items, payload.get("container_name", "")):
                            continue
                        skill = str(container.get(skill_key) or default).casefold()
                        dc = int(container.get(dc_key) or 10)
                        if not any(str(check.get("skill_name", "")).casefold() == skill
                                   and check.get("outcome") == "success" and int(check.get("total", 0)) >= dc for check in checks):
                            raise ContainerFlowError(f"Opening {name} requires a successful {skill} check against DC {dc}.")
                    initialized = container.get("contents_initialized", "contents" in container)
                    if not initialized:
                        validate_contents_manifest(payload.get("contents"))
                        container["contents"] = deepcopy(payload["contents"])
                        container["contents_initialized"] = True
                    elif "contents" in payload:
                        raise ContainerFlowError("Already initialized contents cannot be replaced by an opening event.")
                    container["is_open"] = True
                    container["is_locked"] = container["is_trapped"] = False
                    revealed_currency += int(container.get("contents", {}).get("currency_base_units", 0))
                else:
                    if not container.get("is_open"):
                        raise ContainerFlowError(f"{name} must be opened before taking contents.")
                    if container.get("contents_taken"):
                        raise ContainerFlowError(f"{name}'s contents have already been taken.")
                    if not container.get("contents_initialized", "contents" in container):
                        raise ContainerFlowError(f"{name}'s contents have not been initialized.")
                    currency, _, remainder = selected_contents(container.get("contents", {}), payload)
                    transferred_currency += currency
                    container["contents"] = remainder
                    container["contents_taken"] = not remainder["currency_base_units"] and not remainder["items"]
        except (ContainerFlowError, TypeError, ValueError) as error:
            issues.append(f"{kind}: {error}")
    if (interactions or target_names) and narrative_text:
        # Catch concrete unsupported coin claims without guessing their value from prose.
        for sentence in re.split(r"[.!?\n]+", narrative_text.casefold()):
            if re.search(r"\b(no|not|without|empty|nothing)\b", sentence):
                continue
            if not interactions and target_names and any(not containers[name].get("is_open") for name in target_names):
                if re.search(r"\b(?:you|he|she|they)\s+(?:carefully\s+)?open(?:ed)?\b", sentence):
                    issues.append("Narration opens a saved closed container without ContainerOpenedEvent.")
            coins = r"\b(?:coins?|currency)\b"
            if not re.search(coins, sentence):
                continue
            if not (revealed_currency or transferred_currency) and re.search(
                r"\b(?:find|found|reveal\w*|contain\w*|handful|pile|gleaming)\b", sentence,
            ):
                issues.append("Narration describes coins absent from the authoritative container manifest.")
            if not transferred_currency and re.search(
                r"\b(?:take|took|taken|pocket\w*|collect\w*|transfer\w*|gain\w*)\b.{0,100}" + coins, sentence,
            ):
                issues.append("Narration describes taking coins but the selected transfer contains no currency.")
    return issues
