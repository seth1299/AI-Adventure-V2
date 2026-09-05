from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any, Protocol

from PIL import Image

try:
    from rapidfuzz.fuzz import token_set_ratio as _rapidfuzz_token_set_ratio

    def token_set_ratio(left: str, right: str) -> float:
        return float(_rapidfuzz_token_set_ratio(left, right))
except ImportError:  # pragma: no cover - the packaged build includes RapidFuzz.
    from difflib import SequenceMatcher

    def token_set_ratio(left: str, right: str) -> float:
        return SequenceMatcher(None, left.casefold(), right.casefold()).ratio() * 100

from ai_adventure.app.api_key_store import read_api_key
from ai_adventure.ai.image_styles import (
    DEFAULT_IMAGE_STYLE,
    KNOWN_IMAGE_STYLES,
    image_style_metadata,
    normalize_image_style,
)
from ai_adventure.ai.model_catalog import DEFAULT_IMAGE_MODEL, normalize_image_model
from ai_adventure.context.creative_guardrails import (
    default_banned_creative_terms,
    find_banned_creative_terms,
)


LOGGER = logging.getLogger(__name__)

DEFAULT_IMAGE_LIMIT = 100
DISPLAY_IMAGE_MAX_PIXELS = 384


class VisualAssetRepository(Protocol):
    """Read surface used to discover player-visible image subjects."""

    def get_setting(self, key: str, default: Any = None) -> Any: ...

    def list_history(self) -> list[dict[str, Any]]: ...

    def list_mechanical_events(self) -> list[dict[str, Any]]: ...

    def ensure_travel_locations(self) -> list[dict[str, Any]]: ...

    def list_inventory_items(self) -> list[dict[str, Any]]: ...

    def list_player_visible_npcs(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def list_bestiary_entries(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class VisualAssetRequest:
    """One stable, player-visible subject that may need a generated image."""

    subject_type: str
    subject_key: str
    display_name: str
    description: str
    world_context: str = ""
    message_ids: tuple[str, ...] = ()
    image_style: str = DEFAULT_IMAGE_STYLE
    text_instructions: str = ""
    banned_terms: tuple[str, ...] = ()

    @property
    def descriptor_hash(self) -> str:
        """Returns a stable fingerprint for this entity's visible appearance."""

        canonical = "\n".join(
            (
                self.subject_type.strip().casefold(),
                self.subject_key.strip().casefold(),
                self.display_name.strip().casefold(),
                " ".join(self.description.split()).casefold(),
                normalize_image_style(self.image_style),
            )
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def asset_id(self) -> str:
        """Returns the database identity for this exact visible version."""

        return f"img_{self.descriptor_hash[:24]}"

    @property
    def aspect_ratio(self) -> str:
        """Returns the most useful composition for this subject type."""

        if self.subject_type in {"player", "npc"}:
            return "4:5"
        if self.subject_type == "location":
            return "16:9"
        return "1:1"

    @property
    def filename(self) -> str:
        """Returns a descriptive, bounded, collision-resistant cache filename."""

        stem = descriptive_image_stem(self.display_name)
        return f"{self.subject_type}_{stem}_{self.descriptor_hash[:8]}.jpg"

    @property
    def prompt(self) -> str:
        """Builds an image-only prompt from already finalized visible state."""

        subject_instruction = {
            "player": (
                "Create a single player-character portrait, waist-up, with the face, "
                "clothing, carried equipment, and other visible traits matching the description."
            ),
            "npc": (
                "Create a single NPC portrait, waist-up, with the face, clothing, role, "
                "and other visible traits matching the description."
            ),
            "location": (
                "Create an establishing view of this location that clearly communicates "
                "its architecture, terrain, atmosphere, scale, and current visible appearance. "
                "The location itself is the only foreground subject: do not include any "
                "people, human figures, portraits, crowds, silhouettes, or visible body "
                "parts. Background objects and environmental details are welcome only when "
                "they support the location and do not introduce a person as a subject. "
                "Interpret words such as homely, welcoming, busy, or town/city as qualities "
                "of the place, never as a request to depict a human."
            ),
            "inventory": (
                "Create a clear inventory illustration of this one unique item by itself. "
                "The item is the only foreground subject: do not show a person, face, body, "
                "hand, arm, or someone holding or using it. Make its materials, condition, "
                "color, scale, and distinctive visible features easy to recognize. "
                "The subject name may be fictional or unfamiliar: never substitute a familiar "
                "real-world object based on the name alone. Use every concrete visual trait in "
                "the supplied description, including state-dependent changes such as translucency "
                "or opacity, and make those traits visually obvious. A simple neutral setting "
                "or supporting surface is acceptable, but it must not compete with or obscure "
                "the item."
            ),
            "bestiary": (
                "Create a single non-human creature illustration based only on the "
                "player-known description. Show the creature as the only foreground "
                "subject, with no extra creatures, people, character portraits, text, "
                "labels, or invented hidden anatomy. Make its silhouette, scale, "
                "coloration, texture, and distinctive traits easy to recognize."
            ),
        }[self.subject_type]
        style = image_style_metadata(self.image_style)
        banned_terms = self.banned_terms or default_banned_creative_terms()
        banned_text = ", ".join(banned_terms) or "None"
        text_instructions = self.text_instructions or (
            "No readable text is permitted because the subject does not call for it."
        )
        return (
            "Generate one cohesive image for AI Adventure. "
            f"Selected visual style: {style['value']} ({style['label']}). "
            f"Style direction: {style['prompt']} "
            f"{subject_instruction} Use a coherent centered composition suitable for a compact "
            "desktop game UI. Do not add unapproved words, captions, labels, signatures, "
            "watermarks, UI frames, split panels, unrelated duplicate subjects, or extraneous "
            "foreground characters. Only depict player-visible information; do not invent hidden identities "
            "or secret facts. Make the selected style feel intentional and specific rather than "
            "like a generic AI image. Unless the selected style explicitly calls for one of these "
            "traits, avoid excessive drop shadows, perfect symmetry, unnaturally perfect lighting, "
            "glossy studio staging, dramatic cinematic color grading, lens flare, overly saturated "
            "colors, extreme contrast, plastic-looking materials, and unnaturally clean or flawless "
            "surfaces. Preserve believable variation, small imperfections, and style-appropriate "
            "texture, materials, and lighting.\n\n"
            f"Subject name: {self.display_name}\n"
            f"Player-visible description: {self.description}\n"
            "Text and label instructions (follow exactly; never invent readable text):\n"
            f"{text_instructions}\n"
            "Forbidden words and names: do not render any of these exact terms, close "
            f"spelling variants, hyphenation variants, or reskins: {banned_text}\n"
            "World context for visual consistency (honor this when relevant, especially "
            "historical era, technology level, architecture, clothing, vehicles, and "
            "materials; do not default to modern designs when the context establishes an "
            "earlier period):\n"
            f"{self.world_context or 'No additional world context is available.'}"
        )


class GeminiVisualAssetService:
    """Dedicated image-output client, intentionally separate from story JSON calls."""

    def __init__(
        self,
        *,
        api_key_path: Path,
        model: str = DEFAULT_IMAGE_MODEL,
    ) -> None:
        self.api_key_path = api_key_path.expanduser().resolve()
        self.model = normalize_image_model(model)

    def generate(self, request: VisualAssetRequest) -> tuple[bytes, str]:
        """Generates exactly one image and returns its bytes and MIME type."""

        api_key = read_api_key(self.api_key_path)
        if not api_key:
            raise RuntimeError("A Google Gemini API key is not configured.")

        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise RuntimeError(
                "google-genai is not installed. Install project requirements first."
            ) from error

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=request.prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=request.aspect_ratio),
            ),
        )
        for part in getattr(response, "parts", []) or []:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if not data:
                continue
            if isinstance(data, str):
                import base64

                data = base64.b64decode(data)
            return bytes(data), str(
                getattr(inline_data, "mime_type", "image/png") or "image/png"
            )
        raise RuntimeError("Gemini returned no image data.")


def build_visual_asset_requests(
    repository: VisualAssetRepository,
) -> list[VisualAssetRequest]:
    """Builds deduplicated requests from all durable player-visible image surfaces."""

    event_messages = _visual_event_message_ids(repository.list_mechanical_events())
    opening_story_message_id = _first_story_message_id(repository.list_history())
    world_context = _visible_world_context(repository)
    image_style = normalize_image_style(
        repository.get_setting("images.style", DEFAULT_IMAGE_STYLE)
    )
    banned_terms = default_banned_creative_terms()
    known_location_positions = _known_location_positions(
        repository,
        banned_terms=banned_terms,
    )
    requests: list[VisualAssetRequest] = []

    player_name = str(repository.get_setting("player_name", "Player Character") or "").strip()
    player_appearance = str(repository.get_setting("player.appearance", "") or "").strip()
    if player_name and player_appearance:
        player_id = str(
            getattr(repository, "get_player_id", lambda: "")() or ""
        ).strip() or str(
            repository.get_setting("player.id", "player_character") or "player_character"
        ).strip()
        requests.append(
            VisualAssetRequest(
                subject_type="player",
                subject_key=player_id,
                display_name=player_name,
                description=player_appearance,
                world_context=world_context,
                message_ids=(opening_story_message_id,) if opening_story_message_id else (),
                image_style=image_style,
                banned_terms=banned_terms,
            )
        )

    for location in repository.ensure_travel_locations():
        name = str(location.get("name", "") or "").strip()
        description = str(location.get("description", "") or "").strip()
        if not name or not description:
            continue
        subject_key = str(location.get("location_id", "") or "").strip() or name.casefold()
        requests.append(
            VisualAssetRequest(
                subject_type="location",
                subject_key=subject_key,
                display_name=name,
                description=description,
                world_context=world_context,
                message_ids=tuple(
                    event_messages.get(("location", subject_key), ())
                    or event_messages.get(("location", name.casefold()), ())
                ),
                image_style=image_style,
                banned_terms=banned_terms,
            )
        )

    item_catalog = _item_catalog_by_identity(repository)
    for item in repository.list_inventory_items():
        name = str(item.get("name", "") or "").strip()
        description = _item_visual_description(item, item_catalog)
        category = str(item.get("category", "Item") or "Item").strip()
        if not name or not description:
            continue
        item_uuid = str(
            (item.get("metadata") or {}).get("item_uuid", "")
            if isinstance(item.get("metadata"), dict)
            else ""
        ).strip()
        subject_key = item_uuid or name.casefold()
        requests.append(
            VisualAssetRequest(
                subject_type="inventory",
                subject_key=subject_key,
                display_name=name,
                description=f"{category}. {description}",
                world_context=world_context,
                message_ids=tuple(
                    event_messages.get(("inventory", subject_key), ())
                    or event_messages.get(("inventory", name.casefold()), ())
                ),
                image_style=image_style,
                text_instructions=_text_instructions_for_subject(
                    name,
                    description,
                    known_location_positions,
                ),
                banned_terms=banned_terms,
            )
        )

    for npc in repository.list_player_visible_npcs(limit=500):
        npc_id = str(npc.get("npc_id", "") or "").strip().casefold()
        display_name = str(npc.get("display_name", "Unknown NPC") or "").strip()
        description = str(
            npc.get("description") or npc.get("notes") or ""
        ).strip()
        if not npc_id or not display_name or not description:
            continue
        requests.append(
            VisualAssetRequest(
                subject_type="npc",
                subject_key=npc_id,
                display_name=display_name,
                description=description,
                world_context=world_context,
                message_ids=tuple(event_messages.get(("npc", npc_id), ())),
                image_style=image_style,
                banned_terms=banned_terms,
            )
        )

    for creature in getattr(repository, "list_bestiary_entries", lambda: [])():
        creature_id = str(creature.get("creature_id", "") or "").strip().casefold()
        display_name = str(creature.get("name", "") or "").strip()
        description = str(creature.get("details", "") or "").strip()
        if not creature_id:
            creature_id = display_name.casefold()
        if not creature_id or not display_name or not description:
            continue
        requests.append(
            VisualAssetRequest(
                subject_type="bestiary",
                subject_key=creature_id,
                display_name=display_name,
                description=description,
                world_context=world_context,
                message_ids=tuple(
                    event_messages.get(("bestiary", creature_id), ())
                    or event_messages.get(("bestiary", display_name.casefold()), ())
                ),
                image_style=image_style,
                banned_terms=banned_terms,
            )
        )

    deduplicated: dict[str, VisualAssetRequest] = {}
    for request in requests:
        deduplicated[request.asset_id] = request
    return list(deduplicated.values())


_TEXT_BEARING_HINTS = (
    "book",
    "chart",
    "document",
    "engraving",
    "inscription",
    "journal",
    "label",
    "letter",
    "map",
    "note",
    "parchment",
    "scroll",
    "sign",
    "text",
    "writing",
)


def _known_location_positions(
    repository: VisualAssetRepository,
    *,
    banned_terms: tuple[str, ...],
) -> tuple[tuple[str, float | None, float | None], ...]:
    """Returns safe Travel-tab names and coordinates for exact map labels."""

    locations: list[tuple[str, float | None, float | None]] = []
    for location in repository.ensure_travel_locations():
        name = " ".join(str(location.get("name", "") or "").split()).strip()
        if (
            name
            and not find_banned_creative_terms(name, terms=banned_terms)
            and name.casefold() not in {existing[0].casefold() for existing in locations}
        ):
            locations.append(
                (
                    name,
                    _optional_float(location.get("x_miles")),
                    _optional_float(location.get("y_miles")),
                )
            )
    return tuple(locations)


def _text_instructions_for_subject(
    display_name: str,
    description: str,
    known_location_positions: tuple[tuple[str, float | None, float | None], ...],
) -> str:
    """Builds strict exact-label rules for subjects that visibly carry writing."""

    combined = f"{display_name} {description}".casefold()
    if not any(hint in combined for hint in _TEXT_BEARING_HINTS):
        return "No readable text is permitted because the subject does not call for it."

    approved = [
        display_name.strip(),
        *(name for name, _x, _y in known_location_positions),
    ]
    unique_approved = list(dict.fromkeys(name for name in approved if name))
    labels = ", ".join(f'"{name}"' for name in unique_approved)
    coordinate_lines = [
        f'"{name}": x_miles={x:g}, y_miles={y:g}'
        for name, x, y in known_location_positions
        if x is not None and y is not None
    ]
    relation_lines: list[str] = []
    for index, (left_name, left_x, left_y) in enumerate(known_location_positions):
        if left_x is None or left_y is None:
            continue
        for right_name, right_x, right_y in known_location_positions[index + 1 :]:
            if right_x is None or right_y is None:
                continue
            horizontal = "east" if right_x > left_x else "west" if right_x < left_x else "same longitude as"
            vertical = "north of" if right_y > left_y else "south of" if right_y < left_y else "same latitude as"
            if horizontal.startswith("same"):
                relation_lines.append(f'"{right_name}" is {vertical} "{left_name}".')
            elif vertical.startswith("same"):
                relation_lines.append(f'"{right_name}" is {horizontal} of "{left_name}".')
            else:
                relation_lines.append(
                    f'"{right_name}" is {vertical.replace(" of", "")} and '
                    f'{horizontal} of "{left_name}".'
                )
    directional_rules = (
        "Use a north-facing compass rose with north at the top: x_miles increases "
        "eastward (right) and y_miles increases northward (up). Preserve these exact "
        "coordinates and pairwise relationships; never mirror, rotate, or rearrange "
        "the map.\n"
        + ("Coordinate anchors: " + "; ".join(coordinate_lines) + "\n" if coordinate_lines else "")
        + ("Directional relationships: " + " ".join(relation_lines) if relation_lines else "")
    )
    return (
        "This subject may contain readable writing only when it is visually appropriate. "
        f"If text is shown, use only these exact labels, copied literally: {labels}. "
        "For a map, label only these established Travel-tab locations; do not add, "
        "rename, or imply any other place. Do not use decorative pseudo-writing, random "
        "letters, or invented labels. "
        f"{directional_rules}"
    )


def _optional_float(value: Any) -> float | None:
    """Returns a finite coordinate when the saved value is numeric."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def save_relative_image_filename(repository: Any, request: VisualAssetRequest) -> str:
    """Returns the save-grouped relative filename stored in visual-asset records."""

    save_name = descriptive_image_stem(repository.db_path.parent.name, maximum_length=96)
    return f"{save_name}/{request.filename}"


def find_reusable_inventory_asset(
    *,
    images_dir: Path,
    saves_dir: Path,
    repository: Any,
    request: VisualAssetRequest,
) -> dict[str, Any] | None:
    """Finds a conservative cross-save item-image match by name and description.

    Exact entity IDs are handled by the normal asset ID first. This fallback is
    intentionally limited to inventory items and only considers ready assets
    recorded by another save; it never searches the web or uses an image's
    pixels to infer identity.
    """

    if request.subject_type != "inventory" or not saves_dir.is_dir():
        return None

    target_description = " ".join(request.description.split()).casefold()
    target_name = " ".join(request.display_name.split()).casefold()
    current_db = Path(repository.db_path).resolve()
    best: dict[str, Any] | None = None

    for candidate_db in saves_dir.rglob("adventure.db"):
        try:
            if candidate_db.resolve() == current_db:
                continue
            connection = sqlite3.connect(candidate_db)
            try:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT display_name, filename, prompt, width, height
                    FROM visual_assets
                    WHERE subject_type = 'inventory' AND status = 'ready'
                    """
                ).fetchall()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            LOGGER.debug("Skipped unreadable visual-asset database %s.", candidate_db)
            continue

        for row in rows:
            candidate_name = " ".join(str(row["display_name"] or "").split()).casefold()
            name_score = token_set_ratio(target_name, candidate_name)
            if name_score < 86:
                continue
            candidate_prompt = str(row["prompt"] or "")
            if _image_style_from_prompt(candidate_prompt) != normalize_image_style(
                request.image_style
            ):
                continue
            description_match = re.search(
                r"Player-visible description:\s*(.*?)(?:\nWorld context|$)",
                candidate_prompt,
                flags=re.IGNORECASE | re.DOTALL,
            )
            candidate_description = " ".join(
                (description_match.group(1) if description_match else candidate_prompt).split()
            ).casefold()
            description_score = token_set_ratio(target_description, candidate_description)
            if not (
                name_score >= 94 and description_score >= 48
                or name_score >= 86 and description_score >= 62
            ):
                continue

            stored_filename = Path(str(row["filename"] or ""))
            source_candidates = (
                images_dir / stored_filename,
                images_dir / candidate_db.parent.name / stored_filename.name,
                images_dir / stored_filename.name,
            )
            source_path = next((path for path in source_candidates if path.is_file()), None)
            if source_path is None:
                continue
            score = (name_score * 0.65) + (description_score * 0.35)
            if best is None or score > float(best["score"]):
                best = {
                    "source_path": source_path,
                    "display_name": str(row["display_name"] or ""),
                    "width": int(row["width"] or 0),
                    "height": int(row["height"] or 0),
                    "score": score,
                }

    return best


def _image_style_from_prompt(prompt: str) -> str:
    """Returns the style identity stored in a generated-asset prompt."""

    style_match = re.search(
        r"Selected visual style:\s*([a-z0-9_]+)\s*\(",
        prompt,
        flags=re.IGNORECASE,
    )
    if style_match:
        value = style_match.group(1).casefold()
        return value if value in KNOWN_IMAGE_STYLES else ""
    if "semi-realistic digital game illustration" in prompt.casefold():
        return DEFAULT_IMAGE_STYLE
    return ""


def _item_visual_description(
    item: dict[str, Any],
    item_catalog: dict[str, dict[str, Any]],
) -> str:
    """Combines player-visible item and catalog traits for image generation."""

    name = str(item.get("name", "") or "").strip()
    metadata = item.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    item_uuid = str(metadata.get("item_uuid", "") or "").strip()
    catalog_entry = item_catalog.get(item_uuid) or item_catalog.get(name.casefold())

    parts: list[str] = []
    for value in (
        item.get("description", ""),
        catalog_entry.get("description", "") if catalog_entry else "",
    ):
        text = " ".join(str(value or "").split()).strip()
        if text and text.casefold() not in {part.casefold() for part in parts}:
            parts.append(text)

    visual_keys = (
        "appearance",
        "visual_description",
        "physical_description",
        "color",
        "shape",
        "size",
        "material",
        "texture",
        "condition",
        "traits",
    )
    for key in visual_keys:
        value = metadata.get(key)
        if isinstance(value, list):
            value = ", ".join(str(entry).strip() for entry in value if str(entry).strip())
        text = " ".join(str(value or "").split()).strip()
        if text:
            parts.append(f"{key.replace('_', ' ').title()}: {text}")

    return " ".join(parts)


def _item_catalog_by_identity(
    repository: VisualAssetRepository,
) -> dict[str, dict[str, Any]]:
    """Indexes optional catalog records without requiring older repository adapters."""

    raw_catalog = getattr(repository, "list_item_catalog", lambda: [])()
    indexed: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_catalog, list):
        return indexed
    for entry in raw_catalog:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "") or "").strip()
        metadata = entry.get("metadata", {})
        item_uuid = (
            str(metadata.get("item_uuid", "") or "").strip()
            if isinstance(metadata, dict)
            else ""
        )
        if name:
            indexed[name.casefold()] = entry
        if item_uuid:
            indexed[item_uuid] = entry
    return indexed


def _visible_world_context(repository: VisualAssetRepository) -> str:
    """Collects player-known setting details that should constrain generated art."""

    parts: list[str] = []
    setup = repository.get_setting("new_game.setup", {})
    if isinstance(setup, dict):
        labels = (
            ("Genre", setup.get("specified_genre")),
            ("Game style", setup.get("game_style")),
            ("World context", setup.get("world_context")),
            ("Additional player context", setup.get("ai_additional_context")),
        )
        for label, value in labels:
            text = " ".join(str(value or "").split()).strip()
            if text:
                parts.append(f"{label}: {text}")

        starting_calendar = setup.get("starting_calendar")
        if isinstance(starting_calendar, dict):
            calendar_details = ", ".join(
                f"{key}={value}"
                for key in (
                    "year",
                    "month_name",
                    "month_number",
                    "season_name",
                    "day_of_month",
                    "time_of_day_minutes",
                )
                if (value := starting_calendar.get(key)) not in (None, "", -1)
            )
            if calendar_details:
                parts.append(f"Starting calendar: {calendar_details}")

    world_summary = getattr(repository, "get_world_summary", lambda: "")()
    world_summary_text = " ".join(str(world_summary or "").split()).strip()
    if world_summary_text:
        parts.append(f"Player-known world summary: {world_summary_text[:3000]}")

    for key, label in (("world.genre", "Established genre"), ("world.game_style", "Established style"), ("world.setup_context", "Established setup context")):
        text = " ".join(str(repository.get_setting(key, "") or "").split()).strip()
        if text and not any(text.casefold() in part.casefold() for part in parts):
            parts.append(f"{label}: {text}")

    return "\n".join(parts)


def save_scaled_jpeg(
    image_bytes: bytes,
    target_path: Path,
    *,
    max_pixels: int = DISPLAY_IMAGE_MAX_PIXELS,
) -> tuple[int, int]:
    """Writes a compact RGB JPEG and returns the final dimensions."""

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
        image.thumbnail(
            (max(64, int(max_pixels)), max(64, int(max_pixels))),
            Image.Resampling.LANCZOS,
        )
        image.save(target_path, format="JPEG", quality=88, optimize=True)
        return image.size


def descriptive_image_stem(value: str, *, maximum_length: int = 64) -> str:
    """Returns a readable snake-case filename stem without path-sensitive text."""

    words = re.findall(r"[a-zA-Z0-9]+", str(value).casefold())
    stem = "_".join(words[:8]).strip("_") or "generated_image"
    return stem[: max(16, maximum_length)].rstrip("_") or "generated_image"


def _first_story_message_id(history: list[dict[str, Any]]) -> str:
    """Returns the opening live-story message ID for the player portrait."""

    for entry in history:
        if str(entry.get("kind", "")) != "story":
            continue
        message_id = str(entry.get("message_id", "") or "").strip()
        if message_id:
            return message_id
    return ""


def _visual_event_message_ids(
    events: list[dict[str, Any]],
) -> dict[tuple[str, str], list[str]]:
    """Maps entity-changing event records to the story messages that introduced them."""

    mappings: dict[tuple[str, str], list[str]] = {}
    for event in events:
        if str(event.get("status", "")).casefold() != "applied":
            continue
        message_id = str(event.get("message_id", "") or "").strip()
        payload = event.get("payload", {})
        if not message_id or not isinstance(payload, dict):
            continue
        event_type = str(event.get("event_type", "") or "")
        subjects: list[tuple[str, str]] = []
        if event_type == "LocationUpsertedEvent":
            name = str(payload.get("name", "") or "").strip().casefold()
            location_id = str(payload.get("location_id", "") or "").strip().casefold()
            subjects = [("location", key) for key in (location_id, name) if key]
        elif event_type == "InventoryItemAddedEvent":
            name = str(payload.get("item_name", payload.get("name", "")) or "").strip().casefold()
            item_uuid = str(payload.get("item_uuid", "") or "").strip().casefold()
            subjects = [("inventory", key) for key in (item_uuid, name) if key]
        elif event_type == "NpcUpsertedEvent":
            npc_id = str(payload.get("npc_id", "") or "").strip().casefold()
            subjects = [("npc", npc_id)] if npc_id else []
        elif event_type == "BestiaryEntryUpsertedEvent":
            creature_id = str(payload.get("creature_id", "") or "").strip().casefold()
            name = str(payload.get("name", "") or "").strip().casefold()
            subjects = [("bestiary", key) for key in (creature_id, name) if key]
        if not subjects:
            continue
        for subject in subjects:
            message_ids = mappings.setdefault(subject, [])
            if message_id not in message_ids:
                message_ids.append(message_id)
    return mappings
