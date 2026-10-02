"""Shared validation for container proposals before narration is committed."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from ai_adventure.items import normalize_item_metadata
from ai_adventure.container_access import has_immediate_container_unlock_method
from ai_adventure.inventory_storage import INVENTORY_STORAGE_RULE, inventory_access, move_error, capacity_error, inventory_load, carry_priority


CONTAINER_FLOW_RULE = (
    INVENTORY_STORAGE_RULE + " "
    "ContainerOpenedEvent and ContainerContentsTakenEvent may occur in the same turn, "
    "in that order. Unknown contents are not empty: when contents_initialized is false, "
    "include a complete contents manifest (currency_base_units and items) in "
    "ContainerOpenedEvent. Initialize it once, after access checks succeed. Never "
    "replace already initialized contents. container_authority is private GM state; "
    "reveal its contents only after a permitted opening. Transfer rewards exclusively "
    "through ContainerContentsTakenEvent, including nested containers such as vials or "
    "coin pouches; never add the same loot with InventoryItemAddedEvent or "
    "CurrencyChangedEvent. Use item_names or exact item_ids, and take_currency for "
    "selective taking; omitting them takes all stored contents. "
    "New contents definitions in an initialization manifest are materialized into "
    "catalog-backed items once; saved manifests only contain their database IDs. "
    "Narrate only the manifest and the actual "
    "selected transfer. Leave untaken items inside the container. Untrained lockpicking or trap disarming uses a Dexterity check (or the saved lockpick_attribute/trap_disarm_attribute), without a skill bonus; its reason must name the target container."
)


class ContainerFlowError(ValueError):
    """An inconsistent container turn must be repaired rather than committed."""


def selected_contents(contents: dict[str, Any], payload: dict[str, Any], item_records: list[dict[str, Any]] | None = None) -> tuple[int, list[dict], dict]:
    """Select whole stored item records, returning an immutable remainder."""
    items = contents.get("items", [])
    records = {str(item.get("id", item.get("database_id", ""))): item for item in item_records or []}
    resolved = [records.get(item) if isinstance(item, str) else item for item in items]
    if any(not isinstance(item, dict) for item in resolved):
        raise ContainerFlowError("Container contents reference a missing catalog item.")
    requested_ids = payload.get("item_ids")
    if requested_ids is not None:
        if "item_names" in payload or not isinstance(requested_ids, list) or any(not isinstance(value, str) or not value for value in requested_ids):
            raise ContainerFlowError("Use item_ids or item_names, not both.")
        if set(requested_ids) - {str(item.get("id", item.get("database_id", ""))) for item in resolved}:
            raise ContainerFlowError("Requested item_ids are not stored in this container.")
    requested = payload.get("item_names")
    if requested is not None:
        if not isinstance(requested, list) or any(not isinstance(name, str) or not name.strip() for name in requested):
            raise ContainerFlowError("item_names must be a list of nonblank stored item names.")
        wanted = {name.strip().casefold() for name in requested}
        available = [{str(item.get("name", "")).strip().casefold(),
                      str(item.get("metadata", {}).get("manifest_name", "")).strip().casefold()} - {""}
                     for item in resolved]
        if any(not any(name in names for names in available) for name in wanted):
            raise ContainerFlowError("Requested item_names are not in the stored container contents.")
        if any(sum(name in names for names in available) != 1 for name in wanted):
            raise ContainerFlowError("Requested item_names are ambiguous in the stored contents.")
    else:
        wanted = {str(item.get("name", "")).strip().casefold() for item in resolved}
    take_currency = payload.get("take_currency", True)
    if not isinstance(take_currency, bool):
        raise ContainerFlowError("take_currency must be a boolean.")
    currency = int(contents.get("currency_base_units", 0)) if take_currency else 0
    mask = [str(item.get("id", item.get("database_id", ""))) in requested_ids if requested_ids is not None else
            bool(wanted & {str(item.get("name", "")).strip().casefold(), str(item.get("metadata", {}).get("manifest_name", "")).strip().casefold()})
            for item in resolved]
    taken = [deepcopy(item) for item, selected in zip(resolved, mask) if selected]
    remaining = {
        "currency_base_units": 0 if take_currency else int(contents.get("currency_base_units", 0)),
        "items": [deepcopy(item) for item, selected in zip(items, mask) if not selected],
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
        if isinstance(item, str) and item.strip():
            continue
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
    base_capacity = inventory.get("carrying", {}).get("base_capacity_lb", 50)
    items = inventory.get("items", [])
    authority = inventory.get("container_authority", items)
    item_records = deepcopy(inventory.get("capacity_items") or inventory.get("container_items") or items)
    for item in item_records:
        item.setdefault("id", item.get("database_id", ""))
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
                        and str(e.get("payload", {}).get("item_type", "")).casefold() in {"container", "vehicle"}}
    command = str(context_packet.get("player_command", "")).casefold()
    command_words = set(re.findall(r"[a-z0-9]+", command))
    container_kinds = {"bag", "box", "case", "chest", "container", "crate", "pouch", "purse", "sack", "satchel", "vial"}
    target_names = {name for name in containers if name in command or (
        set(re.findall(r"[a-z0-9]+", name)) & command_words & container_kinds
    )} if re.search(r"\b(open|unlock|inspect|search|take|loot|empty|contents|inside)\b", command) else set()
    blocked_access = False
    state = context_packet.get("state", {})
    current_location = state.get("scene", {}).get("location", state.get("world", {}).get("location"))
    access = inventory_access(item_records, current_location) if isinstance(current_location, str) else {}
    accessible_items = [item for item in item_records if access.get(str(item.get("id", item.get("database_id", ""))), {}).get("available", item.get("available", True))]
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
                if flag == "is_locked" and has_immediate_container_unlock_method(accessible_items, name):
                    continue
                skill = str(container.get(skill_key) or default).casefold()
                dc = int(container.get(dc_key) or 10)
                blocked_access |= not any(container_test_succeeded(check, skill=skill, dc=dc, target=name, attribute=str(container.get(skill_key.replace("_skill", "_attribute"), "Dexterity"))) for check in checks)
    protects = bool(interactions or closed_additions or blocked_access)
    issues = []
    revealed_currency = sum(int(container.get("contents", {}).get("currency_base_units", 0))
        for name, container in containers.items() if name in interactions and container.get("is_open"))
    transferred_currency = 0
    for event in events:
        kind = event.get("type")
        payload = event.get("payload", {})
        name = str(payload.get("container_name", "")).strip().casefold()
        before_event = deepcopy(item_records)
        try:
            if kind in {"InventoryItemModifiedEvent", "ItemModifiedEvent"} and not payload.get("owner_npc_id"):
                destination = str(payload.get("new_storage_location", payload.get("storage_location", "")) or "").strip()
                if destination and destination.upper() not in {"SAME", "SKIP"} and isinstance(current_location, str):
                    target_name = str(payload.get("target_name", payload.get("item_name", ""))).casefold()
                    target = next((item for item in item_records if item.get("metadata", {}).get("item_uuid") == payload.get("item_uuid") and payload.get("item_uuid")), None)
                    target = target or next((item for item in item_records if str(item.get("name", "")).casefold() == target_name), None)
                    if target is None:
                        raise ContainerFlowError("The item to move is missing from authoritative inventory.")
                    destinations = [item for item in item_records
                                    if isinstance(item.get("metadata", {}).get("container"), dict)
                                    and (str(item["id"]) == destination
                                         or str(item.get("name", "")).casefold() == destination.casefold()
                                         or str(item.get("name", "")).casefold().endswith(" " + destination.casefold()))]
                    destination_id = str(destinations[0]["id"]) if len(destinations) == 1 else destination
                    error = move_error(str(target["id"]), destination_id, item_records, current_location, base_capacity)
                    if error:
                        raise ContainerFlowError(error)
                    old_parent = str(target["metadata"].pop("container_id", "") or "")
                    if old_parent:
                        parent = next(item for item in item_records if str(item["id"]) == old_parent)
                        parent["metadata"]["container"]["contents"]["items"] = [item_id for item_id in parent["metadata"]["container"]["contents"]["items"] if item_id != str(target["id"])]
                        containers[str(parent["name"]).casefold()] = parent["metadata"]["container"]
                    if len(destinations) == 1:
                        target["metadata"]["container_id"] = destination_id
                        parent = destinations[0]
                        parent["metadata"]["container"]["contents"]["items"].append(str(target["id"]))
                        containers[str(parent["name"]).casefold()] = parent["metadata"]["container"]
                        target["storage_location"] = parent["name"]
                    else:
                        target["storage_location"] = destination
                    target["metadata"]["storage_location"] = target["storage_location"]
                    access = inventory_access(item_records, current_location)
                target_name = str(payload.get("target_name", payload.get("item_name", ""))).casefold()
                target = next((item for item in item_records if str(item.get("name", "")).casefold() == target_name), None)
                if target:
                    updates = {key: payload[key] for key in ("weight_lb", "carrying_capacity_lb", "item_type", "moveable", "storable") if key in payload}
                    target["metadata"].update(normalize_item_metadata({**target.get("metadata", {}), **updates}, name=target.get("name", ""), category=target.get("category", "")))
                    quantity = payload.get("new_amount", payload.get("new_quantity", payload.get("quantity")))
                    if isinstance(quantity, int) and quantity > 0:
                        target["quantity"] = quantity
            elif kind in {"InventoryItemRemovedEvent", "ItemRemovedEvent"} and not payload.get("owner_npc_id") and isinstance(current_location, str):
                target_name = str(payload.get("item_name", payload.get("name", ""))).casefold()
                target = next((item for item in item_records if str(item.get("name", "")).casefold() == target_name), None)
                if target is None:
                    raise ContainerFlowError("Consumed item is missing from authoritative inventory.")
                quantity = int(payload.get("amount", payload.get("quantity", 1)))
                if quantity <= 0 or quantity > int(target.get("quantity", 1)):
                    raise ContainerFlowError("Consumed quantity must be positive and currently available.")
                if not access[str(target["id"])]["available"]:
                    raise ContainerFlowError("Cannot use or remove an inaccessible item.")
                contents = target.get("metadata", {}).get("container", {}).get("contents", {})
                if quantity == target["quantity"] and (contents.get("items") or contents.get("currency_base_units")):
                    raise ContainerFlowError("Empty the container first, or move it with its contents.")
                if target:
                    target["quantity"] = max(0, int(target.get("quantity", 1)) - int(payload.get("amount", payload.get("quantity", 1))))
                    if not target["quantity"]:
                        item_records.remove(target)
            elif kind == "InventoryItemAddedEvent":
                added_name = str(payload.get("item_name", "")).strip().casefold()
                is_container = str(payload.get("item_type", "")).casefold() in {"container", "vehicle"}
                if protects and (not is_container or (interactions and added_name not in interactions)):
                    raise ContainerFlowError("Direct loot additions, including vials and pouches, bypass the stored transfer.")
                if is_container:
                    if added_name in containers and added_name in interactions:
                        raise ContainerFlowError("An existing container cannot be reacquired or reset while accessing its contents.")
                    metadata = normalize_item_metadata(payload, name=payload.get("item_name", ""), category="Container")
                    containers[added_name] = metadata["container"]
                if not payload.get("owner_npc_id"):
                    target = next((item for item in item_records if str(item.get("name", "")).casefold() == added_name), None)
                    if target:
                        target["quantity"] = int(target.get("quantity", 1)) + int(payload.get("amount", payload.get("quantity", 1)))
                        target["metadata"].update(normalize_item_metadata({**target.get("metadata", {}), **payload}, name=target.get("name", ""), category=target.get("category", "")))
                    else:
                        metadata = normalize_item_metadata(payload, name=payload.get("item_name", ""), category=payload.get("category", "Item"))
                        destination = str(payload.get("storage_location", "actively_carried"))
                        parent = next((item for item in item_records if str(item["id"]) == destination or str(item.get("name", "")).casefold() == destination.casefold()), None)
                        if parent:
                            metadata["container_id"] = str(parent["id"])
                        item_records.append({"id": f"proposed_{len(item_records)}", "name": payload.get("item_name", ""), "quantity": int(payload.get("amount", payload.get("quantity", 1))), "metadata": metadata, "storage_location": destination})
            elif kind == "CurrencyChangedEvent" and protects and int(payload.get("base_unit_amount", payload.get("amount", 0))) > 0:
                raise ContainerFlowError("Direct currency awards bypass the stored transfer.")
            elif kind in {"ContainerOpenedEvent", "ContainerContentsTakenEvent"}:
                if name not in containers:
                    raise ContainerFlowError(f"Container is missing from authoritative inventory: {name}.")
                container = containers[name]
                authoritative_item = next((item for item in item_records if str(item.get("name", "")).casefold() == name), {})
                item_id = str(authoritative_item.get("id", authoritative_item.get("database_id", "")))
                if item_id in access and not access[item_id]["available"]:
                    raise ContainerFlowError("Container is not at the player's current location or is inside an inaccessible container.")
                if kind == "ContainerOpenedEvent":
                    for flag, skill_key, dc_key, default in (
                        ("is_locked", "lockpick_skill", "lockpick_dc", "Lockpicking"),
                        ("is_trapped", "trap_disarm_skill", "trap_disarm_dc", "Sleight of Hand"),
                    ):
                        if not container.get(flag):
                            continue
                        if flag == "is_locked" and has_immediate_container_unlock_method(accessible_items, payload.get("container_name", "")):
                            continue
                        skill = str(container.get(skill_key) or default).casefold()
                        dc = int(container.get(dc_key) or 10)
                        if not any(container_test_succeeded(check, skill=skill, dc=dc, target=name, attribute=str(container.get(skill_key.replace("_skill", "_attribute"), "Dexterity"))) for check in checks):
                            raise ContainerFlowError(f"Opening {name} requires a successful {skill} check against DC {dc}.")
                    initialized = container.get("contents_initialized", "contents" in container)
                    if not initialized:
                        validate_contents_manifest(payload.get("contents"))
                        container["contents"] = deepcopy(payload["contents"])
                        container["contents_initialized"] = True
                    elif "contents" in payload:
                        raise ContainerFlowError("Already initialized contents cannot be replaced by an opening event.")
                    container["is_open"] = True
                    container["contents_known"] = True
                    if authoritative_item:
                        authoritative_item = deepcopy(authoritative_item)
                        authoritative_item["metadata"]["container"] = container
                        item_records = [authoritative_item if str(item.get("id", item.get("database_id", ""))) == item_id else item for item in item_records]
                        if isinstance(current_location, str):
                            access = inventory_access(item_records, current_location)
                    container["is_locked"] = container["is_trapped"] = False
                    revealed_currency += int(container.get("contents", {}).get("currency_base_units", 0))
                else:
                    if not container.get("is_open"):
                        raise ContainerFlowError(f"{name} must be opened before taking contents.")
                    if container.get("contents_taken"):
                        raise ContainerFlowError(f"{name}'s contents have already been taken.")
                    if not container.get("contents_initialized", "contents" in container):
                        raise ContainerFlowError(f"{name}'s contents have not been initialized.")
                    currency, taken, remainder = selected_contents(container.get("contents", {}), payload, item_records)
                    if any(item.get("metadata", {}).get("moveable", item.get("moveable", True)) is not True for item in taken):
                        raise ContainerFlowError("Selected contents include an item that cannot be moved/stored.")
                    prospective = deepcopy(item_records)
                    cargo = inventory_load(prospective, base_capacity)["cargo"]
                    taken.sort(key=lambda selected: carry_priority(selected, cargo), reverse=True)
                    for selected in taken:
                        selected_id = str(selected.get("id", ""))
                        if not selected_id:
                            continue  # First-opening definitions are materialized by the repository.
                        error = move_error(selected_id, "actively_carried", prospective, current_location or "", base_capacity)
                        if error:
                            raise ContainerFlowError(error)
                        moved = next(item for item in prospective if str(item["id"]) == selected_id)
                        moved["metadata"].pop("container_id", None)
                        moved["metadata"]["storage_location"] = moved["storage_location"] = "actively_carried"
                    item_records = prospective
                    transferred_currency += currency
                    container["contents"] = remainder
                    container["contents_taken"] = not remainder["currency_base_units"] and not remainder["items"]
            error = capacity_error(before_event, item_records, base_capacity)
            if error:
                raise ContainerFlowError(error)
        except (ContainerFlowError, TypeError, ValueError) as error:
            item_records = before_event
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


def container_test_succeeded(check: dict[str, Any], *, skill: str, dc: int, target: str, attribute: str = "Dexterity") -> bool:
    """Allow scoped trained checks or an untrained attribute test for this container."""
    if check.get("outcome") != "success" or int(check.get("total", 0)) < dc or check.get("test_kind", "check") != "check":
        return False
    selected_skill = str(check.get("skill_name", "")).strip().casefold()
    if selected_skill:
        return selected_skill == skill.strip().casefold()
    return (str(check.get("attribute", "")).casefold() == attribute.casefold()
            and target.casefold() in str(check.get("reason", "")).casefold())
