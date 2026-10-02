"""Materialize legacy/proposed contents into catalog-backed inventory records."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any, Callable

from ai_adventure.items import normalize_item_metadata


def synchronize_container_records(connection: sqlite3.Connection, upsert_catalog: Callable[..., str]) -> None:
    """Run within the writer's transaction, keeping membership and IDs consistent."""
    columns = connection.execute("PRAGMA table_info(inventory_items)").fetchall()
    if any(column["name"] == "id" and str(column["type"]).upper() == "INTEGER" for column in columns):
        # Older saves used SQLite rowids. Preserve every column and value while
        # permitting the catalog's durable text IDs in the inventory table.
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'inventory_items'").fetchone()["sql"]
        dependent_schema = [row["sql"] for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name = 'inventory_items' AND type IN ('index', 'trigger') AND sql IS NOT NULL"
        )]
        schema = re.sub(r'(?i)(?:\bid\b|"id"|\[id\])\s+INTEGER\s+PRIMARY\s+KEY(?:\s+AUTOINCREMENT)?', "id TEXT PRIMARY KEY NOT NULL DEFAULT ('rec_' || lower(hex(randomblob(16))))", schema, count=1)
        schema = re.sub(r'(?i)^CREATE\s+TABLE\s+(?:"inventory_items"|inventory_items)', "CREATE TABLE inventory_items_text_ids", schema, count=1)
        connection.execute(schema)
        column_names = ", ".join('"' + column["name"].replace('"', '""') + '"' for column in columns)
        connection.execute(f"INSERT INTO inventory_items_text_ids ({column_names}) SELECT {column_names} FROM inventory_items")
        connection.execute("DROP TABLE inventory_items")
        connection.execute("ALTER TABLE inventory_items_text_ids RENAME TO inventory_items")
        for statement in dependent_schema:
            connection.execute(statement)
    rows: dict[str, dict[str, Any]] = {}
    remapped: dict[str, str] = {}
    for row in connection.execute("SELECT * FROM inventory_items").fetchall():
        item = dict(row)
        raw_metadata = json.loads(item.pop("metadata_json") or "{}")
        item["metadata"] = {**raw_metadata, **normalize_item_metadata(raw_metadata, name=item["name"], category=item["category"], description=item["description"])}
        catalog_id = upsert_catalog(connection, name=item["name"], category=item["category"], description=item["description"], value_base_units=item["value_base_units"], metadata=item["metadata"])
        old_id = str(item["id"])
        if old_id != catalog_id:
            connection.execute("UPDATE inventory_items SET id = ? WHERE id = ?", (catalog_id, old_id))
            remapped[old_id] = catalog_id
        item["id"] = catalog_id
        rows[catalog_id] = item

    processed: set[str] = set()
    while pending := [item for key, item in rows.items() if key not in processed]:
        for parent in pending:
            parent_id = str(parent["id"])
            processed.add(parent_id)
            container = parent["metadata"].get("container")
            if not isinstance(container, dict):
                continue
            references = []
            for raw in container.get("contents", {}).get("items", []):
                if isinstance(raw, str):
                    child_id = remapped.get(raw, raw)
                    child = rows.get(child_id)
                    if child is None:
                        raise ValueError(f"Container {parent['name']} references a missing owned catalog item: {raw}.")
                elif isinstance(raw, dict):
                    # Definitions may arrive from Gemini or an old save, but are never
                    # retained as a second, independent copy of an inventory item.
                    child_metadata = {**raw, **(raw.get("metadata") or {})}
                    child_name = str(raw["name"])
                    owned_names = {item["name"].casefold() for item in rows.values()}
                    owned_uuids = {item["metadata"].get("item_uuid") for item in rows.values()}
                    requested_uuid = str(child_metadata.get("item_uuid", "") or "")
                    if child_name.casefold() in owned_names or (requested_uuid and requested_uuid in owned_uuids):
                        # Preserve separately located copies instead of merging them
                        # into an already owned stack or making an old save unloadable.
                        base_name = f"{child_name} ({parent['name']})"
                        child_name = base_name
                        suffix = 2
                        while child_name.casefold() in owned_names:
                            child_name = f"{base_name} {suffix}"
                            suffix += 1
                        child_metadata["item_uuid"] = str(uuid.uuid4())
                    child_id = upsert_catalog(connection, name=child_name, category=raw.get("category", "Item"), description=raw.get("description", ""), value_base_units=raw.get("value_base_units", 0), metadata=child_metadata)
                    if child_id in rows:
                        raise ValueError(f"Container contents duplicate the existing item {raw['name']!r}; use its database ID or a distinct item name.")
                    child = {"id": child_id, "name": child_name, "category": raw.get("category", "Item"), "description": raw.get("description", ""),
                             "quantity": raw.get("quantity", 1), "equipped": False, "value_base_units": raw.get("value_base_units", 0),
                             "metadata": normalize_item_metadata(child_metadata, name=child_name, category=raw.get("category", "Item"), description=raw.get("description", "")),
                             "storage_location": parent["name"]}
                    catalog = connection.execute("SELECT metadata_json FROM item_catalog WHERE id = ?", (child_id,)).fetchone()
                    child["metadata"]["item_uuid"] = json.loads(catalog["metadata_json"])["item_uuid"]
                    child["metadata"]["quantity_unit"] = child_metadata.get("quantity_unit", "each")
                    if child_name != raw["name"]:
                        child["metadata"]["manifest_name"] = raw["name"]
                    rows[child_id] = child
                    connection.execute("INSERT INTO inventory_items (id, name, category, quantity, storage_location, description, value_base_units, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                       (child_id, child["name"], child["category"], child["quantity"], parent["name"], child["description"], child["value_base_units"], json.dumps(child["metadata"])))
                else:
                    raise ValueError("Container contents must reference real catalog item IDs.")
                if child_id == parent_id or child_id in references:
                    raise ValueError("Container contents contain a self-reference or duplicate item ID.")
                other_parent = str(child["metadata"].get("container_id", "") or "")
                if other_parent and remapped.get(other_parent, other_parent) != parent_id:
                    raise ValueError("An item cannot belong to two containers.")
                child["metadata"]["container_id"] = parent_id
                references.append(child_id)
            container["contents"]["items"] = references

    by_name = {item["name"].casefold(): item for item in rows.values() if isinstance(item["metadata"].get("container"), dict)}
    for item in rows.values():
        item_metadata = item["metadata"]
        parent_id = str(item_metadata.get("container_id", "") or "")
        if parent_id:
            item_metadata["container_id"] = remapped.get(parent_id, parent_id)
        else:
            # Backfill old free-text container labels. Deliberately stored items
            # are known; a hidden loot manifest takes the explicit reference path.
            parent = by_name.get(str(item["storage_location"]).casefold())
            if parent is not None and parent["id"] != item["id"]:
                item_metadata["container_id"] = str(parent["id"])
                parent["metadata"]["container"]["contents_known"] = True
                parent["metadata"]["container"]["contents_initialized"] = True

    for item in rows.values():
        container = item["metadata"].get("container")
        if isinstance(container, dict):
            container["contents"]["items"] = []
    for item in rows.values():
        parent_id = str(item["metadata"].get("container_id", "") or "")
        if parent_id:
            if item["metadata"].get("item_type") == "Vehicle":
                raise ValueError("Vehicles cannot be stored inside containers.")
            parent = rows.get(parent_id)
            if parent is None or not isinstance(parent["metadata"].get("container"), dict):
                raise ValueError("Inventory parent must reference an existing container.")
            parent["metadata"]["container"]["contents"]["items"].append(str(item["id"]))
            item["storage_location"] = parent["name"]
            cursor = parent_id
            visited = {str(item["id"])}
            while cursor:
                if cursor in visited:
                    raise ValueError("A container cannot be stored inside itself or its descendants.")
                visited.add(cursor)
                cursor = str(rows[cursor]["metadata"].get("container_id", "") or "")
        item["metadata"]["storage_location"] = item["storage_location"]

    for item in rows.values():
        item_metadata = item["metadata"]
        container = item_metadata.get("container")
        if isinstance(container, dict) and (container["contents"]["items"] or container["contents"]["currency_base_units"]):
            if int(item["quantity"]) != 1:
                raise ValueError("Each tracked container must be a single physical item; give separate containers distinct names.")
            container["contents_taken"] = False
        connection.execute("UPDATE inventory_items SET storage_location = ?, metadata_json = ? WHERE id = ?", (item["storage_location"], json.dumps(item_metadata, ensure_ascii=False), item["id"]))
        upsert_catalog(connection, name=item["name"], category=item["category"], description=item["description"], value_base_units=item["value_base_units"], metadata=item_metadata)
