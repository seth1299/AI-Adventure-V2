from __future__ import annotations

import random
from typing import Any

from ai_adventure.calendar_system import (
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    normalize_calendar_settings,
)
from ai_adventure.context.creative_ideas import CreativeIdeasLibrary
from ai_adventure.currency import (
    describe_currency_denominations,
    normalize_currency_denominations,
)


SKILL_LEVEL_PLAN = [5, 4, 4, 3, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1, 1]

CHARACTER_GENDER_PRESENTATION_HINTS = [
    "female-coded",
    "male-coded",
    "androgynous or nonbinary-coded",
]

GENRE_VARIETY_HINTS = [
    "gritty survival",
    "post-apocalyptic scavenging",
    "realistic detective mystery",
    "cozy merchant life",
    "space frontier",
    "urban supernatural mystery",
    "historical intrigue",
    "seafaring exploration",
    "science-fantasy expedition",
    "low-magic political drama",
]

GREGORIAN_CALENDAR_SETTINGS: dict[str, Any] = {
    "days_per_week": 7,
    "weeks_per_month": 4,
    "months_per_year": 12,
    "seasons_per_year": 4,
    "day_names": [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ],
    "month_names": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "seasons": [
        {"name": "Spring", "weather_hint": "spring"},
        {"name": "Summer", "weather_hint": "summer"},
        {"name": "Autumn", "weather_hint": "autumn"},
        {"name": "Winter", "weather_hint": "winter"},
    ],
    "time_display": "12_hour",
}


def normalize_new_game_setup(raw_setup: Any) -> dict[str, Any]:
    """Returns a complete, safe new-game setup dictionary."""

    if not isinstance(raw_setup, dict):
        raw_setup = {}

    character = raw_setup.get("character", {})

    if not isinstance(character, dict):
        character = {}

    start_location = _clean_text(raw_setup.get("start_location"))
    specified_genre = _clean_text(
        raw_setup.get("specified_genre", raw_setup.get("genre"))
    )
    game_style = _clean_text(raw_setup.get("game_style"))
    world_context = _clean_text(raw_setup.get("world_context"))
    currency_denominations = normalize_currency_denominations(
        raw_setup.get("currency_denominations", []),
        fallback_denominations=[],
    )
    currency_description = (
        _clean_text(raw_setup.get("currency_description"))
        or describe_currency_denominations(
            currency_denominations,
            fallback_denominations=[],
        )
    )
    calendar_settings = _calendar_from_setup(raw_setup.get("calendar", {}))
    skills = _normalize_skills(raw_setup.get("skills", []))
    starter_items = _normalize_starter_items(raw_setup.get("starter_items", []))

    return {
        "title": _clean_text(raw_setup.get("title")) or "New Adventure",
        "character": {
            "name": _clean_text(character.get("name")) or "Player Name",
            "appearance": _clean_text(character.get("appearance")),
            "backstory": _clean_text(character.get("backstory")),
            "notes": _clean_text(character.get("notes")),
        },
        "skills": skills,
        "starter_items": starter_items,
        "calendar": calendar_settings,
        "time_display": calendar_settings["time_display"],
        "currency_denominations": currency_denominations,
        "currency_description": currency_description,
        "specified_genre": specified_genre,
        "game_style": game_style,
        "start_location": start_location,
        "world_context": world_context,
        "ai_additional_context": _build_ai_additional_context(
            specified_genre=specified_genre,
            game_style=game_style,
            world_context=world_context,
        ),
    }


def parse_starter_items_text(raw_text: str) -> list[dict[str, Any]]:
    """
    Parses starter items from newline text.

    Each line may be either a plain item name or:
    name | category | quantity | description | value_base_units
    """

    items: list[dict[str, Any]] = []

    for line in str(raw_text).splitlines():
        clean_line = line.strip()

        if not clean_line:
            continue

        parts = [part.strip() for part in clean_line.split("|")]
        name = parts[0] if parts else ""

        if not name:
            continue

        items.append(
            {
                "name": name,
                "category": parts[1] if len(parts) > 1 else "Item",
                "quantity": _safe_int(parts[2] if len(parts) > 2 else 1, 1),
                "description": parts[3] if len(parts) > 3 else "",
                "value_base_units": _safe_int(parts[4] if len(parts) > 4 else 0, 0),
            }
        )

    return items


def build_new_game_setup_packet(
    setup: dict[str, Any],
    *,
    valid_music_tracks: list[str] | None = None,
) -> dict[str, Any]:
    """Builds a compact AI-facing setup packet for world synthesis."""

    clean_setup = normalize_new_game_setup(setup)
    current_calendar = build_calendar_snapshot(
        DEFAULT_START_ELAPSED_MINUTES,
        clean_setup["calendar"],
    )
    clean_music_tracks = [
        str(track).strip()
        for track in (valid_music_tracks or [])
        if str(track).strip()
    ]
    creative_ideas = CreativeIdeasLibrary.load_default().select_for_new_game()

    return {
        "schema_version": 1,
        "packet_type": "new_game_setup",
        "setup": clean_setup,
        "current_calendar": current_calendar,
        "current_weather": "Clear",
        "requirements": {
            "world_summary": (
                "Write a few paragraphs describing at least the basics of the "
                "world or city, prominent NPCs, locations of interest, religions, "
                "and economy. Incorporate player-provided names, factions, "
                "guilds, locations, style, calendar, and currency when present."
            ),
            "opening_scene": (
                "Write an introductory player-facing scene at the requested "
                "starting location. End with 'What do you do now?'"
            ),
            "calendar_weather_consistency": (
                "Opening prose must match current_calendar and current_weather unless "
                "you return starting_calendar and/or weather fields that intentionally "
                "change them. If you mention autumn, winter, cold nights, summer heat, "
                "rain, snow, storms, dawn, evening, or similar seasonal/time/weather "
                "details, those details must match the structured starting_calendar "
                "and weather you return."
            ),
            "events": (
                "Use structured events for any initial NPCs, active tasks, "
                "or starter-world facts "
                "that should be durable."
            ),
            "starting_music": (
                "If valid background music tracks are available, suggest one "
                "MusicChangedEvent for the opening scene. The filename must exactly "
                "match one entry from audio.valid_music_tracks."
            ),
            "ai_invention_policy": (
                "Default, placeholder, or blank fields are not confirmed world facts. "
                "Treat them as permission to invent suitable specifics during world "
                "setup. Preserve explicit player-provided custom values, but fill "
                "empty/default character, world, location, economy, NPC, religion, "
                "faction, and starting-scene details with coherent original content."
            ),
            "character_generation": (
                "If character name, appearance, backstory, or notes are blank/default "
                "placeholders, invent them. Blank/default character fields do not "
                "mean the player character should default to male. Follow "
                "character_generation_guidance.gender_presentation_hint for invented "
                "character details, and use creative_ideas.player_character_name_examples "
                "as a balanced name pool when useful."
            ),
            "genre_generation": (
                "If setup.specified_genre is blank/default, choose a specific genre "
                "or premise for this new game. Do not default to fantasy. Use "
                "genre_generation_guidance.genre_hint as inspiration when present, "
                "or choose another coherent genre if it better fits the player "
                "setup. Return the final genre in selected_genre and use it when "
                "creating the world, character, skills, inventory, and opening scene."
            ),
            "character_scope": (
                "Treat the player character's class, profession, backstory, and skills "
                "as facts about the player character, not as instructions that the "
                "entire world must share the same theme. Use them to shape the "
                "character, starting inventory, personal contacts, and immediate "
                "opportunities. Do not make the city's politics, religions, economy, "
                "factions, locations, NPCs, conflicts, and mysteries all revolve "
                "around the character's specialty unless setup.game_style, "
                "setup.world_context, or setup.specified_genre explicitly requests "
                "that focus. A merchant character can live in a city whose religion "
                "is about storms, ancestry, law, harvests, stars, or anything else "
                "coherent; a detective can investigate a world not wholly built "
                "around detective work."
            ),
            "starting_location": (
                "If setup.start_location is blank/default, choose any fitting "
                "starting location for the selected genre and character. The player "
                "does not need to start in a tavern; they can start on a frozen sea, "
                "a deserted island, a crashed ship, a crime scene, a ruined store, "
                "a city checkpoint, a wilderness trail, or anywhere else coherent. "
                "Return a short, broad place name only, such as a room, building, "
                "street, district, ship, campsite, or landmark. Put scenic details "
                "like floor, view, nearby landmarks, weather, and exact position in "
                "the opening scene instead of start_location."
            ),
            "skill_generation": (
                "If a setup.skills entry has blank name, blank description, or "
                "requires_ai_invention=true, invent a distinct setting-appropriate "
                "skill name and concrete description for that slot. Preserve the "
                "slot's level exactly. Skill names should be generalized gameplay "
                "capabilities useful across many checks, not one-off lore phrases, "
                "proper nouns, tiny item-maintenance tasks, or narrow setting trivia. "
                "Good shapes include Weather-Reading, Arcana, Navigation, Tinkering, "
                "Stealth, Investigation, Medicine, Performance, Persuasion, Survival, "
                "Melee, or Lore (Specific Domain). Put local flavor and backstory "
                "specifics in the description. Convert specific lore skills to the "
                "parenthetical form, such as Lore (Syndicate), Lore (Flijosha), or "
                "Lore (Merchant Law), rather than Syndicate Lore or Flijosha "
                "Observance. Do not reuse generic defaults such as "
                "Athletics, Awareness, Crafting, Fieldcraft, Investigation, Lore, "
                "Medicine, Melee, Performance, Persuasion, Primary Training, "
                "Secondary Training, Signature Expertise, Stealth, or Survival "
                "unless the player explicitly typed that skill name."
            ),
            "starter_inventory": (
                "If setup.starter_items is empty or has fewer than five items, "
                "create a finalized starting_items list with at least five concrete "
                "items that fit the finalized character backstory, skills, selected "
                "genre, starting location, weather, and economy. Preserve any "
                "player-provided starter items and add fitting extra items as needed. "
                "Do not use a generic fantasy kit unless the character and genre "
                "actually justify it. Each item must include name, category, quantity, "
                "description, and value_base_units."
            ),
            "currency_generation": (
                "If setup.currency_denominations is empty, create a finalized "
                "currency_denominations list with at least one and at most four "
                "denominations that fit the selected genre, world, and economy. "
                "Use names that make sense for the premise, such as copper/silver/gold "
                "coins for some fantasy worlds, dollars for realistic modern worlds, "
                "or credits for futuristic and space settings. One denomination must "
                "be the baseline unit with value=1. Other values are exchange rates "
                "in that baseline unit and do not need to be multiples or powers of "
                "10. Preserve explicit player-provided setup.currency_denominations "
                "instead of replacing them."
            ),
            "starting_currency_balance": (
                "Return starting_currency_balance_base_units as the player "
                "character's actual starting money. It will be written to "
                "game_state/currency.balance as one integer in the baseline "
                "currency unit. Choose an amount that fits the finalized "
                "character, genre, starting situation, and economy. Do not create "
                "coin, purse, cash, wallet, or credit inventory items to represent "
                "spendable money."
            ),
            "creative_ideas": (
                "Treat creative_ideas as high-priority style seeds when inventing "
                "names, locations, cultures, religions, foods, drinks, species, "
                "alchemy ingredients, magic styles, and other world details. "
                "Strongly prefer the examples or close stylistic relatives over "
                "generic training-data fantasy defaults. Never use any term listed "
                "in creative_ideas.banned_terms, nor obvious spelling, hyphenation, "
                "or reskin variants, for newly generated player characters, NPCs, "
                "locations, factions, religions, taverns, regions, or similar "
                "proper nouns."
            ),
        },
        "fields_requiring_ai_invention": _fields_requiring_ai_invention(clean_setup),
        "character_generation_guidance": _character_generation_guidance(clean_setup),
        "genre_generation_guidance": _genre_generation_guidance(clean_setup),
        "audio": {
            "valid_music_tracks": clean_music_tracks,
            "current_music": "",
        },
        "creative_ideas": creative_ideas,
    }


def fallback_world_summary(setup: dict[str, Any]) -> str:
    """Builds a deterministic world summary when AI setup is unavailable."""

    clean_setup = normalize_new_game_setup(setup)
    title = clean_setup["title"]
    location = clean_setup["start_location"]
    style = (
        clean_setup["specified_genre"]
        or clean_setup["game_style"]
        or "new adventure"
    )
    world_context = clean_setup["world_context"] or "No additional world details were provided."
    currency = (
        clean_setup["currency_description"]
        or "The local currency should be established during world setup."
    )

    return (
        f"{title} begins as a {style} centered on {location}. The world is shaped "
        f"by the player setup details: {world_context}\n\n"
        f"{location} has enough local life to support rumors, trade, faith, and "
        "conflict. Prominent NPCs, factions, guilds, and locations of interest "
        "should emerge from play and be recorded as the player discovers them.\n\n"
        f"The local economy uses this currency premise: {currency}. Religion, "
        "customs, social tensions, and major institutions should be established "
        "through player-visible discoveries rather than hidden exposition."
    )


def fallback_introductory_message(setup: dict[str, Any]) -> str:
    """Builds a deterministic opening scene when AI setup is unavailable."""

    clean_setup = normalize_new_game_setup(setup)
    character_name = clean_setup["character"]["name"]
    location = clean_setup["start_location"] or "the opening scene"
    style = (
        clean_setup["specified_genre"]
        or clean_setup["game_style"]
        or "adventure"
    )

    return (
        f"{character_name} begins in {location}, at the first quiet edge of a new "
        f"{style}. The immediate scene is ready, but the full AI-generated world "
        "introduction could not be created because Gemini is not configured yet.\n\n"
        "What do you do now?"
    )


def _fields_requiring_ai_invention(clean_setup: dict[str, Any]) -> list[str]:
    """Identifies setup values that are defaults/placeholders for the AI to flesh out."""

    invention_fields: list[str] = []
    character = clean_setup["character"]

    if clean_setup["title"] == "New Adventure":
        invention_fields.append("game title/theme identity")

    if character["name"] == "Player Name":
        invention_fields.append("character name")

    if not character["appearance"]:
        invention_fields.append("character appearance")

    if not character["backstory"]:
        invention_fields.append("character backstory")

    if not character["notes"]:
        invention_fields.append("character notes/personality hooks")

    if not clean_setup["game_style"]:
        invention_fields.append("game style and genre tone")

    if not clean_setup["specified_genre"]:
        invention_fields.append("specific genre or premise")

    if not clean_setup["start_location"]:
        invention_fields.append("specific starting location")

    if not clean_setup["world_context"]:
        invention_fields.append("world context, factions, religions, and locations")

    if _has_ai_skill_placeholders(clean_setup["skills"]):
        invention_fields.append("distinct starting skill identities")

    if len(clean_setup["starter_items"]) < 5:
        invention_fields.append("starter inventory based on character and skills")

    if not clean_setup["currency_denominations"]:
        invention_fields.append("economy and currency denominations")

    return invention_fields


def _character_generation_guidance(clean_setup: dict[str, Any]) -> dict[str, str]:
    """Builds a small randomized guidance hint for blank/default player characters."""

    character = clean_setup["character"]
    needs_character_invention = (
        character["name"] == "Player Name"
        or not character["appearance"]
        or not character["backstory"]
        or not character["notes"]
    )

    if not needs_character_invention:
        return {
            "rule": "Preserve the player-provided character identity and details.",
            "gender_presentation_hint": "player-provided",
        }

    return {
        "rule": (
            "Use this only for blank/default character fields. It is a creative "
            "variety hint, not a claim about player identity."
        ),
        "gender_presentation_hint": random.SystemRandom().choice(
            CHARACTER_GENDER_PRESENTATION_HINTS
        ),
        "anti_default_rule": (
            "Do not assume a blank/default player character is male. Vary names, "
            "pronouns, appearance, and backstory across new games."
        ),
    }


def _genre_generation_guidance(clean_setup: dict[str, Any]) -> dict[str, str]:
    """Builds genre guidance for blank/default new games."""

    specified_genre = clean_setup["specified_genre"]

    if specified_genre:
        return {
            "rule": "Preserve the player-provided genre.",
            "genre_hint": specified_genre,
        }

    return {
        "rule": (
            "Use this as a variety hint when the player did not provide a genre. "
            "It is inspiration, not a constraint."
        ),
        "genre_hint": random.SystemRandom().choice(GENRE_VARIETY_HINTS),
        "anti_default_rule": (
            "Do not default to fantasy or tavern openings. Pick a coherent genre "
            "and opening situation that makes the new game feel distinct."
        ),
    }


def _has_ai_skill_placeholders(skills: list[dict[str, Any]]) -> bool:
    """Returns True when at least one starting skill needs AI invention."""

    return any(bool(skill.get("requires_ai_invention")) for skill in skills)


def _normalize_skills(raw_skills: Any) -> list[dict[str, Any]]:
    """Normalizes skills into the required level spread."""

    input_skills = raw_skills if isinstance(raw_skills, list) else []
    normalized: list[dict[str, Any]] = []

    for index, level in enumerate(SKILL_LEVEL_PLAN):
        raw_skill = input_skills[index] if index < len(input_skills) else {}

        if not isinstance(raw_skill, dict):
            raw_skill = {"name": str(raw_skill)}

        name = _clean_text(raw_skill.get("name"))
        description = _clean_text(raw_skill.get("description"))
        requires_ai_invention = bool(raw_skill.get("requires_ai_invention"))

        if not name or not description:
            requires_ai_invention = True

        normalized.append(
            {
                "name": name,
                "description": description,
                "level": level,
                "requires_ai_invention": requires_ai_invention,
            }
        )

    return normalized


def _normalize_starter_items(raw_items: Any) -> list[dict[str, Any]]:
    """Normalizes player-requested starter inventory without adding defaults."""

    input_items = raw_items if isinstance(raw_items, list) else []
    items: list[dict[str, Any]] = []

    for raw_item in input_items:
        if not isinstance(raw_item, dict):
            raw_item = {"name": str(raw_item)}

        name = _clean_text(raw_item.get("name"))

        if not name:
            continue

        items.append(
            {
                "name": name,
                "category": _clean_text(raw_item.get("category")) or "Item",
                "quantity": max(1, _safe_int(raw_item.get("quantity"), 1)),
                "description": _clean_text(raw_item.get("description")),
                "value_base_units": max(0, _safe_int(raw_item.get("value_base_units"), 0)),
            }
        )

    return items


def _calendar_from_setup(raw_calendar: Any) -> dict[str, Any]:
    """Normalizes setup calendar settings, defaulting to Gregorian-style names."""

    if not isinstance(raw_calendar, dict):
        raw_calendar = {}

    if not raw_calendar or str(raw_calendar.get("calendar_type", "")).casefold() == "gregorian":
        settings = dict(GREGORIAN_CALENDAR_SETTINGS)
    else:
        settings = {**GREGORIAN_CALENDAR_SETTINGS, **raw_calendar}

    settings["time_display"] = str(
        raw_calendar.get("time_display")
        or raw_calendar.get("time_format")
        or settings.get("time_display")
        or "12_hour"
    )

    return normalize_calendar_settings(settings)


def _build_ai_additional_context(
    *,
    specified_genre: str,
    game_style: str,
    world_context: str,
) -> str:
    """Builds AI-facing setup instructions from wizard inputs."""

    lines: list[str] = []

    if specified_genre:
        lines.append(f"Specified genre: {specified_genre}")

    if game_style:
        lines.append(f"Game style: {game_style}")

    if world_context:
        lines.append(f"World creation context: {world_context}")

    return "\n\n".join(lines)


def _clean_text(value: Any) -> str:
    """Returns stripped string text."""

    if value is None:
        return ""

    return str(value).strip()


def _safe_int(value: Any, default: int) -> int:
    """Converts a value to int with fallback."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default
