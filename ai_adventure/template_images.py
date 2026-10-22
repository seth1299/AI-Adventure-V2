"""Portable, local-only uploads retained by reusable New Game templates."""

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

SUBJECT_TYPES = {"player", "npc", "inventory", "skill", "location", "bestiary"}


def normalize_template_images(value: Any) -> list[dict[str, str]]:
    """Keep a portable attachment list, never external file paths or runtime IDs."""
    images = {}
    for record in value if isinstance(value, list) else []:
        if not isinstance(record, dict):
            continue
        kind = str(record.get("subject_type", "")).casefold()
        name = str(record.get("display_name", "")).strip()
        data = record.get("png_base64")
        if kind not in SUBJECT_TYPES or not name or not isinstance(data, str) or not data:
            continue
        images[(kind, name.casefold())] = {
            "subject_type": kind, "display_name": name, "png_base64": data,
        }
    return list(images.values())


def capture_template_image(request, cached_path: Path) -> dict[str, str]:
    """Embed the normalized cache copy; the original upload is never modified."""
    return {"subject_type": request.subject_type, "display_name": request.display_name,
            "png_base64": base64.b64encode(cached_path.read_bytes()).decode("ascii")}


def template_image_bytes(records: Any, request) -> bytes | None:
    # Keep setup/template handling usable in the minimal playtesting build.
    from PIL import Image

    for record in normalize_template_images(records):
        if record["subject_type"] != request.subject_type or record["display_name"].casefold() != request.display_name.strip().casefold():
            continue
        try:
            data = base64.b64decode(record["png_base64"], validate=True)
            with Image.open(BytesIO(data)) as image:
                if image.format != "PNG":
                    return None
                image.verify()
            return data
        except (ValueError, OSError, Image.DecompressionBombError):
            return None
    return None
