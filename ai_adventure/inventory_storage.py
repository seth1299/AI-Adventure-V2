"""Shared possession, discovery, and physical access rules for inventory."""

from __future__ import annotations

from typing import Any, Iterable
from copy import deepcopy
import math

DEFAULT_CARRYING_CAPACITY_LB = 50.0

LOCATION_STORAGE_RULE = (
    "Every known location has location_scope: broad or specific. Broad covers continents, "
    "regions, cities and districts; specific identifies a room, campsite, hideout or similarly "
    "precise recoverable storage site. Unknown scope is broad. Sublocation status does not "
    "imply specific scope. Never leave items at a broad location or make items there "
    "immediately accessible on arrival. Establish a specific site with LocationUpsertedEvent "
    "and move the player there before storing or retrieving items. Preserve authored scope. "
    "Classify locations automatically from their described extent. This is backend metadata: "
    "never announce scope labels or ask the player to select a classification. Describe "
    "a concrete storage place naturally when one is needed."
)


def location_allows_storage(name: str, locations: Iterable[dict[str, Any]]) -> bool:
    return any(str(row.get("name", "")).strip().casefold() == name.strip().casefold()
               and row.get("location_scope") == "specific" for row in locations)



def pounds(value: Any, default: float = 0.0) -> float:
    """Accept finite, nonnegative pounds; legacy missing weights remain unknown."""
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else default
    except (TypeError, ValueError):
        return default


def is_vehicle(item: dict[str, Any]) -> bool:
    return str(metadata(item).get("item_type", "")).casefold() == "vehicle"


def inventory_load(items: Iterable[dict[str, Any]], base_capacity_lb: float = DEFAULT_CARRYING_CAPACITY_LB) -> dict[str, Any]:
    """Count physical load irrespective of whether a container is open or known."""
    rows = {str(item["id"]): item for item in items}
    children: dict[str, list[str]] = {}
    for key, item in rows.items():
        children.setdefault(str(metadata(item).get("container_id", "") or ""), []).append(key)

    def weight(key: str, seen: set[str]) -> float:
        if key in seen:
            return 0.0
        item = rows[key]
        return pounds(metadata(item).get("weight_lb")) * max(0, int(item.get("quantity", 1))) + sum(weight(child, seen | {key}) for child in children.get(key, []))

    pools = {}
    load = bonus = 0.0
    for key, item in rows.items():
        if is_container(item):
            limit = metadata(item).get("carrying_capacity_lb")
            pools[key] = {"name": item["name"], "vehicle": is_vehicle(item), "weight_lb": sum(weight(child, {key}) for child in children.get(key, [])), "capacity_lb": pounds(limit) if limit is not None else None}
        if not metadata(item).get("container_id") and storage_location(item).casefold() in {"actively_carried", "actively carried", "on_person", "on person"} and not is_vehicle(item):
            load += weight(key, set())
            if is_container(item):
                bonus += pounds(metadata(item).get("carrying_capacity_lb"))
    base = pounds(base_capacity_lb, DEFAULT_CARRYING_CAPACITY_LB)
    return {"weight_unit": "lb", "weight_lb": load, "base_capacity_lb": base, "container_bonus_lb": bonus, "capacity_lb": base + bonus, "remaining_lb": base + bonus - load, "cargo": pools,
            "unweighed_item_count": sum("weight_lb" not in metadata(item) for item in rows.values())}


def capacity_error(before: list[dict[str, Any]], after: list[dict[str, Any]], base_capacity_lb: float = DEFAULT_CARRYING_CAPACITY_LB) -> str:
    """Reject increased overage, while allowing old overloaded saves to unload."""
    old, new = inventory_load(before, base_capacity_lb), inventory_load(after, base_capacity_lb)
    if max(0.0, new["weight_lb"] - new["capacity_lb"]) > max(0.0, old["weight_lb"] - old["capacity_lb"]) + 1e-8:
        return f"Player carrying capacity exceeded ({new['weight_lb']:g} / {new['capacity_lb']:g} lb)."
    for key, pool in new["cargo"].items():
        if pool["capacity_lb"] is None:
            continue
        prior = old["cargo"].get(key, {})
        over = max(0.0, pool["weight_lb"] - pool["capacity_lb"])
        previous = max(0.0, prior.get("weight_lb", 0.0) - (prior.get("capacity_lb") or 0.0)) if prior.get("capacity_lb") is not None else 0.0
        if over > previous + 1e-8:
            return f"{pool['name']} cargo capacity exceeded ({pool['weight_lb']:g} / {pool['capacity_lb']:g} lb)."
    return ""


def carry_priority(item: dict[str, Any], cargo: dict[str, Any]) -> float:
    """Pick up capacity-providing vessels before heavier cargo in one transfer."""
    if is_vehicle(item):
        return 0.0
    own_weight = pounds(metadata(item).get("weight_lb")) * int(item.get("quantity", 1))
    contents_weight = cargo.get(str(item.get("id", "")), {}).get("weight_lb", 0.0)
    bonus = pounds(metadata(item).get("carrying_capacity_lb")) if is_container(item) else 0.0
    return bonus - own_weight - contents_weight


INVENTORY_STORAGE_RULE = (
    LOCATION_STORAGE_RULE + " " +
    "Containers are physical items, including ordinary empty boxes and bags. "
    "Their saved contents.items contain only database IDs referencing item_catalog; "
    "quantities and definitions belong to the referenced inventory records. "
    "An item's container_id identifies its parent; moving a container moves its entire "
    "contents with it. available=false means remembered possession, not usable equipment, "
    "ingredients, keys, or sale stock. Physical access requires actively_carried or the "
    "player's exact current specific storage site, and every enclosing container must be open, "
    "unlocked, and safe. Never use or move remote items. moveable=false prevents moving "
    "or storing; storable=false prevents putting the item in a container. A forge or "
    "other fixed installation should have both flags false. Move existing items with "
    "InventoryItemModifiedEvent, preserving identity, rather than remove/add. Never "
    "put a container inside itself or its descendants. Unopened contents remain private; "
    "contents_known becomes true only after a permitted opening or deliberate storage "
    "in an already accessible container. Closing it preserves remembered contents."
    " Record realistic weight_lb in pounds per quantity unit, and carrying_capacity_lb "
    "for Containers and Vehicles (0 for ordinary items). Player base capacity is five times Strength in pounds. All carried weight, including nested contents and "
    "empty containers, counts. Only directly carried/worn containers add their cargo "
    "capacity to player capacity; nested containers do not add bonuses. Every container "
    "also has its own cargo limit. Vehicle cargo and chassis never count against the "
    "player: actively_carried means the vehicle accompanies them; an exact Location "
    "means parked there. Vehicles cannot nest inside containers. Putting a container "
    "down removes its weight, contents weight, and bonus while preserving contents. "
    "Do not exceed capacity or narrate a rejected transfer as successful. Unknown legacy "
    "weights are unweighed, not truly weightless; provide weights when establishing items."
)

NEW_GAME_STORAGE_RULE = (
    "moveable/storable flags; forges false. Storage: actively_carried, container, "
    "specific Location. Containers: known empty/private; IDs only. weight_lb per "
    "unit; carrying_capacity_lb cargo/bag bonus. Vehicles separate. Obey capacity."
)


def metadata(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("metadata", {})
    return value if isinstance(value, dict) else {}


def is_container(item: dict[str, Any]) -> bool:
    return isinstance(metadata(item).get("container"), dict)


def storage_location(item: dict[str, Any]) -> str:
    return str(item.get("storage_location", metadata(item).get("storage_location", "actively_carried")) or "actively_carried").strip()


def inventory_access(items: Iterable[dict[str, Any]], current_location: str, locations: Iterable[dict[str, Any]] = ()) -> dict[str, dict[str, Any]]:
    """Resolve nesting without trusting display labels as container identity."""
    storage_allowed = location_allows_storage(current_location, locations)
    rows = {str(item.get("id", item.get("database_id", ""))): item for item in items}
    results: dict[str, dict[str, Any]] = {}

    def resolve(item_id: str, visiting: set[str]) -> dict[str, Any]:
        if item_id in results:
            return results[item_id]
        if item_id in visiting or item_id not in rows:
            return {"known": False, "available": False, "physical_location": "Unknown", "access_reason": "Invalid container reference."}
        item = rows[item_id]
        parent_id = str(metadata(item).get("container_id", "") or "")
        if parent_id:
            parent = rows.get(parent_id)
            parent_access = resolve(parent_id, visiting | {item_id})
            container = metadata(parent or {}).get("container", {})
            known = parent_access["known"] and bool(container.get("contents_known", container.get("is_open", False)))
            available = known and parent_access["available"] and container.get("is_open") is True and not container.get("is_locked") and not container.get("is_trapped")
            result = {**parent_access, "known": known, "available": bool(available),
                      "access_reason": "" if available else (parent_access["access_reason"] or "The enclosing container is closed, locked, or trapped.")}
        else:
            location = storage_location(item)
            carried = location.casefold() in {"actively_carried", "actively carried", "on_person", "on person"}
            available = carried or bool(storage_allowed and current_location.strip() and location.casefold() == current_location.strip().casefold())
            result = {"known": True, "available": available, "physical_location": current_location if carried else location,
                      "access_reason": "" if available else (
                          "Choose a precise place here, such as a room or campsite, to store and retrieve items."
                          if location.casefold() == current_location.strip().casefold() and not storage_allowed
                          else f"Stored at {location}; you are at {current_location or 'an unknown location'}."
                      )}
        results[item_id] = result
        return result

    for item_id in rows:
        resolve(item_id, set())
    return results


def move_error(item_id: str, destination: str, items: list[dict[str, Any]], current_location: str, base_capacity_lb: float = DEFAULT_CARRYING_CAPACITY_LB, locations: Iterable[dict[str, Any]] = ()) -> str:
    """Validate a whole-stack move, including source access and nesting."""
    rows = {str(item["id"]): item for item in items}
    item = rows.get(item_id)
    access = inventory_access(items, current_location, locations)
    if item is None:
        return "This item is no longer in inventory."
    if metadata(item).get("moveable", True) is not True:
        return "Item cannot be moved/stored."
    if not access[item_id]["available"] or not access[item_id]["known"]:
        return access[item_id]["access_reason"] or "Item is not currently accessible."
    parent = rows.get(destination)
    if parent is not None:
        if is_vehicle(item):
            return "Vehicles cannot be stored inside containers."
        container = metadata(parent).get("container", {})
        if int(parent.get("quantity", 1)) != 1:
            return "Choose a single physical container; stacked containers need distinct names."
        if not is_container(parent) or not access[destination]["available"] or not access[destination]["known"]:
            return "Destination container is not currently accessible."
        if not container.get("is_open") or container.get("is_locked") or container.get("is_trapped") or not container.get("contents_initialized"):
            return "Open the destination container before storing items."
        if metadata(item).get("storable", True) is not True:
            return "Item cannot be stored in a container."
        cursor = destination
        seen: set[str] = set()
        while cursor:
            if cursor == item_id or cursor in seen:
                return "A container cannot be placed inside itself or its contents."
            seen.add(cursor)
            cursor = str(metadata(rows.get(cursor, {})).get("container_id", "") or "")
    elif destination.casefold() not in {"actively_carried", "on_person"} and destination.casefold() != current_location.strip().casefold():
        return "Destination is not the player's current location."
    elif destination.casefold() not in {"actively_carried", "on_person"} and not location_allows_storage(destination, locations):
        return "Choose a precise place, such as a room or campsite, before leaving items here."
    # Carrying a box must not bypass the restrictions on a fixed item inside it.
    for child_id, child in rows.items():
        cursor = str(metadata(child).get("container_id", "") or "")
        seen = set()
        while cursor and cursor not in seen:
            if cursor == item_id and metadata(child).get("moveable", True) is not True:
                return "The container holds an item that cannot be moved/stored."
            seen.add(cursor)
            cursor = str(metadata(rows.get(cursor, {})).get("container_id", "") or "")
    after = deepcopy(items)
    moved = next(row for row in after if str(row["id"]) == item_id)
    moved.setdefault("metadata", {}).pop("container_id", None)
    if parent is not None:
        moved["metadata"]["container_id"] = destination
    moved["storage_location"] = destination
    return capacity_error(items, after, base_capacity_lb)


def move_destinations(item_id: str, items: list[dict[str, Any]], current_location: str, base_capacity_lb: float = DEFAULT_CARRYING_CAPACITY_LB, locations: Iterable[dict[str, Any]] = ()) -> list[tuple[str, str]]:
    """Return labels and stable IDs for the player's accessible destinations."""
    item = next((item for item in items if str(item["id"]) == item_id), {})
    parent_id = str(metadata(item).get("container_id", "") or "")
    destinations = []
    if current_location.strip() and (parent_id or storage_location(item).casefold() != current_location.casefold()):
        destinations.append((f"Leave at {current_location}", current_location))
    if parent_id or storage_location(item).casefold() not in {"actively_carried", "on_person"}:
        destinations.append(("Actively Carried", "actively_carried"))
    for container in items:
        container_id = str(container["id"])
        if is_container(container) and container_id != parent_id:
            destinations.append((str(container["name"]), container_id))
    return [(label, value) for label, value in destinations if not move_error(item_id, value, items, current_location, base_capacity_lb, locations)]
