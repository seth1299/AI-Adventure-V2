from __future__ import annotations

import random
from typing import Any

from ai_adventure.ai.modes import normalize_ai_mode_preferences
from ai_adventure.calendar_system import (
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    normalize_calendar_settings,
)
from ai_adventure.audio.tts_settings import normalize_tts_audio_fields
from ai_adventure.context.creative_ideas import CreativeIdeasLibrary
from ai_adventure.context.naming import GENERIC_PROPER_NOUN_PLACEHOLDER_RULE
from ai_adventure.currency import (
    describe_currency_denominations,
    normalize_currency_denominations,
)
from ai_adventure.narration_preferences import normalize_narration_preferences


SKILL_LEVEL_PLAN = [5, 4, 4, 3, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1, 1]
STARTER_INVENTORY_MIN_ITEMS = 5

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
AI_GENERATED_CALENDAR_FALLBACK_SETTINGS: dict[str, Any] = {
    "days_per_week": 8,
    "weeks_per_month": 5,
    "months_per_year": 10,
    "seasons_per_year": 5,
    "day_names": [
        "Dawn",
        "Bell",
        "Pulse",
        "Aster",
        "Crossing",
        "Tide",
        "Signal",
        "Rest",
    ],
    "month_names": [
        "First Light",
        "Bluewake",
        "High Zenith",
        "Goldline",
        "Long Drift",
        "Deep Frost",
        "Rain Signal",
        "Bloomrise",
        "Red Reach",
        "Yearsend",
    ],
    "seasons": [
        {"name": "Thaw", "weather_hint": "spring"},
        {"name": "Zenith", "weather_hint": "summer"},
        {"name": "Drift", "weather_hint": "autumn"},
        {"name": "Frost", "weather_hint": "winter"},
        {"name": "Storm", "weather_hint": "rainy"},
    ],
    "time_display": "narrative",
}
AI_GENERATED_SCI_FI_CALENDAR_FALLBACK_SETTINGS: dict[str, Any] = {
    "days_per_week": 8,
    "weeks_per_month": 5,
    "months_per_year": 10,
    "seasons_per_year": 5,
    "day_names": [
        "Launch",
        "Vector",
        "Relay",
        "Apex",
        "Drift",
        "Orbit",
        "Signal",
        "Rest",
    ],
    "month_names": [
        "Perihelion",
        "Blue Shift",
        "Aphelion",
        "Gold Transit",
        "Long Drift",
        "Deep Night",
        "Signal Rain",
        "Bloom Cycle",
        "Red Return",
        "Year Lock",
    ],
    "seasons": [
        {"name": "Thaw", "weather_hint": "spring"},
        {"name": "Zenith", "weather_hint": "summer"},
        {"name": "Drift", "weather_hint": "autumn"},
        {"name": "Shadow", "weather_hint": "winter"},
        {"name": "Storm", "weather_hint": "rainy"},
    ],
    "time_display": "24_hour",
}
GENERIC_FANTASY_ARTISAN_CALENDAR_NAMES = {
    "hearth",
    "market",
    "lantern",
    "greenwake",
    "goldleaf",
    "longshade",
    "deepfrost",
    "raincall",
    "bloomturn",
    "redharvest",
    "rainmoot",
}
SCI_FI_CONTEXT_MARKERS = {
    "sci-fi",
    "science fiction",
    "futuristic",
    "future",
    "far-off year",
    "space",
    "starship",
    "spaceship",
    "planet",
    "alien",
    "orbital",
    "interstellar",
    "colony",
    "colonial",
    "station",
    "cyberpunk",
    "android",
    "research expedition",
    "crash-land",
    "crash landing",
    "crash-landed",
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
    start_location_mode = _normalize_start_location_mode(raw_setup)
    currency_denominations = normalize_currency_denominations(
        raw_setup.get("currency_denominations", []),
        fallback_denominations=[],
    )
    economy_examples = normalize_economy_examples(
        raw_setup.get("economy_examples", raw_setup.get("economy_notes", []))
    )
    economy_examples_description = describe_economy_examples(economy_examples)
    raw_currency_description = _clean_text(raw_setup.get("currency_description"))
    currency_description = (
        _combine_currency_description(raw_currency_description, economy_examples_description)
        or describe_currency_denominations(
            currency_denominations,
            fallback_denominations=[],
        )
    )
    calendar_settings = _calendar_from_setup(raw_setup.get("calendar", {}))
    audio_settings = _audio_from_setup(raw_setup.get("audio", {}))
    narration_preferences = normalize_narration_preferences(
        raw_setup.get("narration", {})
    )
    raw_ai_settings = raw_setup.get(
        "ai_settings",
        raw_setup.get("ai_modes", {}),
    )
    if not isinstance(raw_ai_settings, dict):
        raw_ai_settings = {}
    ai_mode_preferences = normalize_ai_mode_preferences(raw_ai_settings)
    custom_ai_context = _clean_text(raw_ai_settings.get("additional_context"))
    skills = _normalize_skills(raw_setup.get("skills", []))
    starter_items = _normalize_starter_items(raw_setup.get("starter_items", []))
    starting_npcs = _normalize_starting_npcs(raw_setup.get("starting_npcs", []))
    starting_task = _normalize_starting_task(
        raw_setup.get("starting_task", raw_setup.get("starting_quest", {}))
    )

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
        "starting_npcs": starting_npcs,
        "starting_task": starting_task,
        "calendar": calendar_settings,
        "audio": audio_settings,
        "narration": narration_preferences,
        "ai_settings": {
            "model_intelligence": ai_mode_preferences["model_intelligence"],
            "model_tone": ai_mode_preferences["model_tone"],
            "response_length": ai_mode_preferences["response_length"],
            "allowed_content_categories": ai_mode_preferences[
                "allowed_content_categories"
            ],
            "additional_context": custom_ai_context,
        },
        "time_display": calendar_settings["time_display"],
        "currency_denominations": currency_denominations,
        "currency_description": currency_description,
        "economy_examples": economy_examples,
        "specified_genre": specified_genre,
        "game_style": game_style,
        "start_location": start_location,
        "start_location_mode": start_location_mode,
        "world_context": world_context,
        "ai_additional_context": _build_ai_additional_context(
            specified_genre=specified_genre,
            game_style=game_style,
            world_context=world_context,
            additional_context=custom_ai_context,
        ),
    }


def _audio_from_setup(raw_audio: Any) -> dict[str, Any]:
    """Returns normalized new-game audio preferences."""

    if not isinstance(raw_audio, dict):
        raw_audio = {}

    return {
        "music_enabled": _safe_bool(raw_audio.get("music_enabled"), True),
        "music_volume": _clamped_int(raw_audio.get("music_volume"), 25, 0, 100),
        **normalize_tts_audio_fields(raw_audio),
    }


def parse_starter_items_text(raw_text: str) -> list[dict[str, Any]]:
    """
    Parses starter items from newline text.

    Each line may be a natural-language item request, a plain item name, or:
    name | category | quantity | description | value_base_units
    """

    items: list[dict[str, Any]] = []

    for line in str(raw_text).splitlines():
        clean_line = line.strip()

        if not clean_line:
            continue

        if "|" not in clean_line:
            if _looks_like_item_request(clean_line):
                items.append(
                    {
                        "name": "",
                        "category": "Item",
                        "quantity": 1,
                        "description": "",
                        "value_base_units": 0,
                        "item_request": clean_line,
                        "requires_ai_invention": True,
                    }
                )
            else:
                items.append(
                    {
                        "name": clean_line,
                        "category": "Item",
                        "quantity": 1,
                        "description": "",
                        "value_base_units": 0,
                        "item_request": "",
                        "requires_ai_invention": False,
                    }
                )
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
                "item_request": "",
                "requires_ai_invention": False,
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
    starter_item_count = len(clean_setup["starter_items"])
    ai_mode_preferences = normalize_ai_mode_preferences(clean_setup["ai_settings"])

    return {
        "schema_version": 1,
        "packet_type": "new_game_setup",
        "setup": clean_setup,
        "player_ai_preferences": ai_mode_preferences,
        "current_calendar": current_calendar,
        "current_weather": "Clear",
        "requirements": {
            "world_summary": (
                "Follow player_ai_preferences.response_length_instruction while "
                "describing the basics of the world or city, prominent NPCs, "
                "locations of interest, religions, and economy. Incorporate "
                "player-provided names, factions, guilds, locations, style, "
                "calendar, and currency when present. Use light Markdown headings, "
                "bold important names, italics, and bullet lists when they improve "
                "readability."
            ),
            "opening_scene": (
                "Write an introductory player-facing scene at the requested "
                "starting location. Use setup.narration.tense_label and "
                "setup.narration.style_label for the prose. Light Markdown is "
                "allowed for italics, bold important names, and readable lists. "
                "End with the prompt in setup.turn_prompt."
            ),
            "start_location": (
                "setup.start_location_mode controls how to treat the requested "
                "starting location. suggestion means use it as inspiration and "
                "you may replace it with a more specific fitting location. exact "
                "means return setup.start_location unchanged as start_location and "
                "use that exact name consistently in introductory_message, "
                "locations, and any opening events."
            ),
            "narration_preferences": (
                "Use setup.narration.tense_label and setup.narration.style_label "
                "for introductory_message and other player-facing prose. Do not "
                "fall back to second-person wording unless the selected style is "
                "Second-Person. First-person styles should use I/me/my; "
                "third-person styles should use the player character's name or "
                "pronouns instead of you/your. Limited styles should stay within "
                "the player character's observed or reasonably inferred experience. "
                "Omniscient styles may use a broader narrative camera, but must "
                "not reveal secrets, hidden state, mystery solutions, or "
                "NPC-private facts."
            ),
            "ai_modes": (
                "Apply player_ai_preferences.model_tone_instruction, "
                "response_length_instruction, and model_content_rules to "
                "world_summary, introductory_message, location descriptions, NPC "
                "profile text, and all other "
                "player-facing prose. These preferences do not relax schema "
                "completeness or hidden-information rules."
            ),
            "calendar_weather_consistency": (
                "Opening prose must match current_calendar and current_weather unless "
                "you return starting_calendar and/or weather fields that intentionally "
                "change them. If you mention autumn, winter, cold nights, summer heat, "
                "rain, snow, storms, dawn, evening, or similar seasonal/time/weather "
                "details, those details must match the structured starting_calendar "
                "and weather you return."
            ),
            "calendar_generation": (
                "If setup.calendar.ai_generated is true, invent calendar_settings "
                "for the new world using clear day names, month names, seasons, "
                "season weather hints, and time_display. The calendar should fit "
                "the selected genre, world culture, climate, and playstyle. Do "
                "not copy the default Gregorian calendar, weekday names, January-"
                "through-December month names, generic Month 1/Month 2 style "
                "placeholder names, or generic fantasy/artisan defaults when AI "
                "generation is requested. For futuristic, space, cyberpunk, or "
                "science-fiction settings, use calendar names that fit that "
                "premise, such as orbital, colonial, corporate, astronomical, "
                "technical, station, mission, or local alien-cultural terms, not "
                "hearth, market, lantern, harvest, or village-craft naming. If "
                "setup.calendar.ai_generated is false, use the provided calendar "
                "settings and return calendar_settings as an empty object."
            ),
            "events": (
                "Use structured events for any setup.starting_npcs rows or "
                "requested active tasks that should be durable. The number of "
                "NpcUpsertedEvent entries can be zero, one, or many; choose the "
                "count from setup.starting_npcs and what the player character "
                "would actually know at setup. Do not parse NPCs out of "
                "ordinary setup prose or plaintext fields. For each "
                "setup.starting_npcs row, create one NpcUpsertedEvent. Fill blank "
                "name, location, or description fields with fitting specifics. "
                "If description_mode is exact, copy description into "
                "payload.public_description unchanged; if description_mode is "
                "suggestion, treat description as inspiration and put the final "
                "description you choose into payload.public_description. Return AI-only "
                "hidden identities, motives, mystery solutions, off-screen plans, "
                "and other concealed truths in the dedicated gm_secrets setup "
                "field; never place those truths in player-facing setup fields."
            ),
            "starting_task": (
                "setup.starting_task.mode controls the initial active quest. If "
                "mode is none, do not create an initial ActiveTaskUpsertedEvent "
                "unless another explicit setup field independently asks for one. "
                "If mode is ai, create one fitting starting quest with "
                "ActiveTaskUpsertedEvent. If mode is custom, create exactly one "
                "ActiveTaskUpsertedEvent using the player's provided fields as "
                "authoritative anchors, filling any blank/default fields from the "
                "rest of the setup. Use category Quest unless the player provided "
                "a different category."
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
                "as a balanced name pool when useful. If the player supplied a custom "
                "character name, appearance, backstory, or notes value, preserve that "
                "field exactly instead of rewriting, renaming, embellishing, or "
                "reinterpreting it."
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
            "mature_content": ai_mode_preferences["model_content_rules"],
            "starting_location": (
                "If setup.start_location is blank/default, choose any fitting "
                "starting location for the selected genre and character. The player "
                "does not need to start in a tavern; they can start on a frozen sea, "
                "a deserted island, a crashed ship, a crime scene, a ruined store, "
                "a city checkpoint, a wilderness trail, or anywhere else coherent. "
                "Return a short, broad place name only, such as a room, building, "
                "street, district, ship, campsite, or landmark. Put scenic details "
                "like floor, view, nearby landmarks, weather, and exact position in "
                "the opening scene instead of start_location. If setup."
                "start_location_mode is exact, return setup.start_location "
                "unchanged as the final start_location. If it is suggestion, treat "
                "setup.start_location as inspiration that may be replaced."
            ),
            "travel_locations": (
                "Return a locations array for the Travel tab containing exactly "
                "the places the player character plausibly knows at setup. There "
                "is no minimum or maximum count beyond the current starting place. "
                "For an unknown crash-landing, isolated survival, amnesia, or "
                "new-arrival premise, it is valid for the only known location to "
                "be the finalized starting location at x_miles=0 and y_miles=0. "
                "For a ranger, courier, trader, local resident, or well-traveled "
                "character, include every important place they would reasonably "
                "know, even six or more. Coordinates are relative map miles. Each "
                "returned location needs player-facing description, terrain, "
                "travel_multiplier, and route notes. Do not include hidden routes, "
                "secrets, or GM-only information, and do not include the name of "
                "an unknown world, planet, region, or settlement unless the player "
                "character would know that name."
            ),
            "setup_scope_counts": (
                "Use zero, one, or many setup entries according to the actual "
                "premise instead of forcing a default count. Known locations, "
                "known NPCs, known crafting items/materials, known crafting "
                "recipes, active tasks, secrets, and starter possessions should "
                "all scale with the character's backstory, profession, current "
                "situation, and player-provided context."
            ),
            "crafting_knowledge": (
                "known_crafting_items and known_crafting_recipes are player-known "
                "Crafting tab knowledge, not physical inventory. Return empty "
                "arrays for a character with no relevant training or discoveries. "
                "For an alchemist, cook, engineer, herbalist, survivalist, medic, "
                "scientist, crafter, or other profession that logically starts "
                "with practical making knowledge, return as many useful known "
                "items/materials and recipes as fit the backstory. Recipe "
                "ingredients must use item names from known_crafting_items or "
                "other known item catalog entries and use quantity, measure_amount, "
                "and measure_unit."
            ),
            "skill_generation": (
                "If a setup.skills entry has a nonblank name, copy that exact name "
                "unchanged and fill only missing description text for that slot. "
                "Invent a distinct setting-appropriate skill name only when the "
                "setup skill name is blank/default/placeholder. Preserve each "
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
                "Return finalized inventory in the starting_items field, never in "
                "starting_inventory. starting_items must contain at least five "
                "total tracked possessions and has no maximum count. Include any "
                "player-requested items, then invent enough additional concrete "
                "items that fit the finalized character, genre, starting location, "
                "weather, and opening situation to reach the minimum. "
                "Preserve any player-provided starter items with "
                "requires_ai_invention=false. "
                "When a setup.starter_items entry has requires_ai_invention=true or "
                "item_request text, treat it as a player-authored item concept and "
                "convert it into the number of concrete, setting-appropriate tracked "
                "items that best fits the concept, rather than copying the request "
                "verbatim. Set source_index to the zero-based setup.starter_items "
                "index for any item based on that setup entry; for extra items not "
                "based on a specific setup.starter_items entry, set source_index to "
                "-1. Do not "
                "downgrade setup weapons or armor into generic items: preserve "
                "Weapon fields such as weapon_hands, damage, attack_skill, "
                "attack_range_feet, ammunition_type_required, clip_size, and "
                "bullets_per_attack, and preserve Armor fields such as "
                "covers_body_parts and armor_rating. "
                "Do not include setup bookkeeping words such as Starting, Starter, Initial, "
                "Amount, Quantity, Count, or Total in item names. Generalize resource "
                "names to the actual inventory item, such as Fuel instead of Starting "
                "Fuel Amount, Food instead of Starting Food Amount, and Water instead "
                "of Starting Water Quantity. Put quantities in quantity, not name. Do not "
                "use a generic fantasy kit unless the character and genre actually "
                "justify it. Each item must include name, category, quantity, "
                "description, value_base_units, and source_index."
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
                "10. Use setup.economy_examples as common-price calibration for "
                "the value of ordinary goods when it is present. Preserve explicit "
                "player-provided setup.currency_denominations instead of replacing them."
            ),
            "starting_currency_balance": (
                "Return starting_currency_balance_base_units as the player "
                "character's actual starting money. It will be written to "
                "game_state/currency.balance as one integer in the baseline "
                "currency unit. Choose an amount that fits the finalized "
                "character, genre, starting situation, economy, and any "
                "setup.economy_examples common-price rows. Do not create coin, "
                "purse, cash, wallet, or credit inventory items to represent "
                "spendable money."
            ),
            "creative_ideas": (
                "Treat creative_ideas as high-priority style seeds when inventing "
                "names, locations, cultures, religions, foods, drinks, species, "
                "crafting ingredients, magic styles, and other world details. "
                "Strongly prefer the examples or close stylistic relatives over "
                "generic training-data fantasy defaults. The banned_terms list is "
                "a hard exclusion list, not optional style guidance: never use any "
                "term listed in creative_ideas.banned_terms, nor obvious spelling, "
                "hyphenation, or reskin variants, for newly generated player "
                "characters, NPCs, locations, factions, religions, taverns, regions, "
                "items, skills, calendar names, event payload names, or similar "
                "proper nouns. Before returning JSON, scan every string key and "
                "value and replace any newly invented banned term with a fresh "
                "non-banned name. "
                f"{GENERIC_PROPER_NOUN_PLACEHOLDER_RULE}"
            ),
        },
        "fields_requiring_ai_invention": _fields_requiring_ai_invention(clean_setup),
        "starter_inventory_contract": {
            "requested_item_count": starter_item_count,
            "minimum_finalized_item_count": STARTER_INVENTORY_MIN_ITEMS,
            "count_rule": (
                "At least 5 finalized starting items are required; there is no "
                "maximum starting item count."
            ),
            "output_field": "starting_items",
            "alias_not_allowed": "starting_inventory",
            "source_index_rule": (
                "Use the zero-based setup.starter_items index for items based on "
                "a setup starter-item entry and -1 only for extra invented items."
            ),
        },
        "starting_task_contract": {
            "mode": clean_setup["starting_task"]["mode"],
            "task": clean_setup["starting_task"]["task"],
            "rules": (
                "none means no requested opening quest; ai means invent a fitting "
                "opening quest; custom means use provided fields and have the AI "
                "fill blanks from the rest of the setup."
            ),
        },
        "turn_prompt": _turn_prompt_for_setup(clean_setup),
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
        +
        _turn_prompt_for_setup(clean_setup)
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

    if any(not str(skill.get("name", "")).strip() for skill in clean_setup["skills"]):
        invention_fields.append("blank starting skill names")

    if any(
        not str(skill.get("description", "")).strip()
        for skill in clean_setup["skills"]
    ):
        invention_fields.append("blank starting skill descriptions")

    if any(bool(item.get("requires_ai_invention")) for item in clean_setup["starter_items"]):
        invention_fields.append("starter inventory based on character and skills")

    if clean_setup["starting_task"]["mode"] == "ai":
        invention_fields.append("opening quest/task")
    elif clean_setup["starting_task"]["mode"] == "custom" and clean_setup[
        "starting_task"
    ]["task"].get("requires_ai_invention"):
        invention_fields.append("blank starting quest/task fields")

    if not clean_setup["currency_denominations"]:
        invention_fields.append("economy and currency denominations")

    return invention_fields


def _normalize_start_location_mode(raw_setup: dict[str, Any]) -> str:
    """Returns how strictly the AI should treat the requested start location."""

    raw_mode = (
        raw_setup.get("start_location_mode")
        or raw_setup.get("starting_location_mode")
        or raw_setup.get("start_location_kind")
        or "suggestion"
    )
    mode = _clean_text(raw_mode).casefold().replace("-", "_").replace(" ", "_")

    if mode in {"exact", "exactly_this", "fixed", "locked", "required"}:
        return "exact"

    return "suggestion"


def _turn_prompt_for_setup(clean_setup: dict[str, Any]) -> str:
    """Builds the visible end-of-turn prompt for the narration preferences."""

    narration = clean_setup.get("narration", {})
    character = clean_setup.get("character", {})
    tense = str(narration.get("tense", "present"))
    style = str(narration.get("style", "second_person_limited"))
    character_name = _clean_text(character.get("name")) or "the player character"

    if tense == "past":
        if style.startswith("first_person"):
            return "What did I do next?"
        if style.startswith("third_person"):
            return f"What did {character_name} do next?"
        return "What did you do next?"

    if tense == "future":
        if style.startswith("first_person"):
            return "What will I do next?"
        if style.startswith("third_person"):
            return f"What will {character_name} do next?"
        return "What will you do next?"

    if style.startswith("first_person"):
        return "What do I do now?"
    if style.startswith("third_person"):
        return f"What does {character_name} do now?"
    return "What do you do now?"


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
    normalized: list[dict[str, Any] | None] = [None for _level in SKILL_LEVEL_PLAN]
    next_position = 0

    for raw_skill in input_skills[: len(SKILL_LEVEL_PLAN)]:
        if not isinstance(raw_skill, dict):
            raw_skill = {"name": str(raw_skill)}

        requested_level = _safe_int(raw_skill.get("level"), 0)
        target_index = -1

        if requested_level in SKILL_LEVEL_PLAN:
            for index, level in enumerate(SKILL_LEVEL_PLAN):
                if level == requested_level and normalized[index] is None:
                    target_index = index
                    break

        if target_index < 0:
            while next_position < len(normalized) and normalized[next_position] is not None:
                next_position += 1

            if next_position >= len(normalized):
                break

            target_index = next_position

        level = SKILL_LEVEL_PLAN[target_index]
        name = _clean_text(raw_skill.get("name"))
        description = _clean_text(raw_skill.get("description"))
        requires_ai_invention = bool(raw_skill.get("requires_ai_invention"))

        if not name or not description:
            requires_ai_invention = True

        normalized[target_index] = {
            "name": name,
            "description": description,
            "level": level,
            "requires_ai_invention": requires_ai_invention,
        }

    for index, skill in enumerate(normalized):
        if skill is not None:
            continue

        level = SKILL_LEVEL_PLAN[index]
        normalized[index] = {
            "name": "",
            "description": "",
            "level": level,
            "requires_ai_invention": True,
        }

    return [skill for skill in normalized if skill is not None]


def _normalize_starter_items(raw_items: Any) -> list[dict[str, Any]]:
    """Normalizes player-requested starter inventory without adding defaults."""

    input_items = raw_items if isinstance(raw_items, list) else []
    items: list[dict[str, Any]] = []

    for raw_item in input_items:
        if not isinstance(raw_item, dict):
            raw_text = str(raw_item)
            if _looks_like_item_request(raw_text):
                raw_item = {
                    "item_request": raw_text,
                    "requires_ai_invention": True,
                }
            else:
                raw_item = {"name": raw_text}

        name = _clean_text(raw_item.get("name"))
        item_request = (
            _clean_text(raw_item.get("item_request"))
            or _clean_text(raw_item.get("request"))
            or _clean_text(raw_item.get("narrative_description"))
        )
        requires_ai_invention = bool(raw_item.get("requires_ai_invention")) or (
            bool(item_request) and not name
        )

        if not name and not item_request:
            continue

        items.append(
            _starter_item_with_metadata(
                raw_item,
                name=name,
                category=_clean_text(raw_item.get("category")) or "Item",
                quantity=max(1, _safe_int(raw_item.get("quantity"), 1)),
                description=_clean_text(raw_item.get("description")),
                value_base_units=max(0, _safe_int(raw_item.get("value_base_units"), 0)),
                item_request=item_request,
                requires_ai_invention=requires_ai_invention,
            )
        )

    return items


def _normalize_starting_npcs(raw_npcs: Any) -> list[dict[str, Any]]:
    """Normalizes structured requested starting NPC rows."""

    if not isinstance(raw_npcs, list):
        return []

    npcs: list[dict[str, Any]] = []

    for raw_npc in raw_npcs:
        if not isinstance(raw_npc, dict):
            continue

        name = _clean_text(raw_npc.get("name", raw_npc.get("display_name")))
        location = _clean_text(raw_npc.get("location"))
        description = _clean_text(
            raw_npc.get("description", raw_npc.get("public_description"))
        )
        description_mode = _clean_text(
            raw_npc.get("description_mode", raw_npc.get("mode"))
        ).casefold()

        if description_mode not in {"suggestion", "exact"}:
            description_mode = "suggestion"

        npcs.append(
            {
                "name": name,
                "location": location,
                "description": description,
                "description_mode": description_mode,
                "requires_ai_invention": not name or not location or not description,
            }
        )

    return npcs


def _starter_item_with_metadata(
    raw_item: dict[str, Any],
    **base_item: Any,
) -> dict[str, Any]:
    """Preserves starter equipment fields that matter after AI finalization."""

    category = _clean_text(base_item.get("category")).title()
    metadata = raw_item.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    item_type = (
        _clean_text(raw_item.get("item_type"))
        or _clean_text(metadata.get("item_type"))
        or category
    ).title()

    if item_type == "Weapon" or category == "Weapon":
        base_item["category"] = "Weapon"
        base_item["item_type"] = "Weapon"
        base_item["weapon_hands"] = (
            _clean_text(raw_item.get("weapon_hands"))
            or _clean_text(metadata.get("weapon_hands"))
            or "one-handed"
        )
        base_item["damage"] = (
            _clean_text(raw_item.get("damage"))
            or _clean_text(metadata.get("damage"))
            or "1d6"
        )
        base_item["damage_type"] = (
            _clean_text(raw_item.get("damage_type"))
            or _clean_text(metadata.get("damage_type"))
        )
        base_item["attack_skill"] = (
            _clean_text(raw_item.get("attack_skill"))
            or _clean_text(metadata.get("attack_skill"))
            or "Melee"
        )
        base_item["attack_range_feet"] = max(
            0,
            _safe_int(
                raw_item.get(
                    "attack_range_feet",
                    metadata.get("attack_range_feet", 5),
                ),
                5,
            ),
        )
        ammunition_type_required = (
            _clean_text(raw_item.get("ammunition_type_required"))
            or _clean_text(metadata.get("ammunition_type_required"))
        )
        base_item["ammunition_type_required"] = ammunition_type_required
        base_item["clip_size"] = max(
            0,
            _safe_int(raw_item.get("clip_size", metadata.get("clip_size", 0)), 0),
        )
        base_item["bullets_per_attack"] = max(
            0,
            _safe_int(
                raw_item.get(
                    "bullets_per_attack",
                    metadata.get("bullets_per_attack", 0),
                ),
                0,
            ),
        )
        return base_item

    if item_type == "Armor" or category in {"Armor", "Armour", "Shield"}:
        base_item["category"] = "Armor"
        base_item["item_type"] = "Armor"
        base_item["covers_body_parts"] = _normalize_text_list(
            raw_item.get("covers_body_parts", metadata.get("covers_body_parts", []))
        )
        base_item["armor_rating"] = max(
            0,
            _safe_int(
                raw_item.get("armor_rating", metadata.get("armor_rating", 0)),
                0,
            ),
        )
        return base_item

    if item_type in {"Ammunition", "Ammo"}:
        base_item["category"] = "Ammunition"
        base_item["item_type"] = "Ammunition"
        base_item["ammunition_type"] = (
            _clean_text(raw_item.get("ammunition_type"))
            or _clean_text(metadata.get("ammunition_type"))
            or str(base_item.get("name", ""))
        )

    return base_item


def _normalize_text_list(raw_values: Any) -> list[str]:
    """Returns a clean list of strings from a list or comma-separated text."""

    if isinstance(raw_values, list):
        return [
            _clean_text(value)
            for value in raw_values
            if _clean_text(value)
        ]

    return [
        part.strip()
        for part in str(raw_values or "").split(",")
        if part.strip()
    ]


def _normalize_starting_task(raw_task_setup: Any) -> dict[str, Any]:
    """Normalizes requested starting active-task setup."""

    if not isinstance(raw_task_setup, dict):
        raw_task_setup = {}

    mode = _clean_text(
        raw_task_setup.get("mode", raw_task_setup.get("task_mode", "none"))
    ).casefold()
    if mode not in {"none", "ai", "custom"}:
        mode = "none"

    raw_task = raw_task_setup.get("task", raw_task_setup)
    if not isinstance(raw_task, dict):
        raw_task = {}

    task = {
        "name": _clean_text(raw_task.get("name", raw_task.get("title"))),
        "category": _clean_text(raw_task.get("category")) or "Quest",
        "description": _clean_text(raw_task.get("description", raw_task.get("details"))),
        "requester": _clean_text(raw_task.get("requester", raw_task.get("contact"))),
        "location": _clean_text(raw_task.get("location")),
        "reward": _clean_text(raw_task.get("reward")),
        "due_date": _clean_text(raw_task.get("due_date")),
        "due_elapsed_minutes": _safe_int(raw_task.get("due_elapsed_minutes"), -1),
    }

    if mode != "custom":
        return {
            "mode": mode,
            "task": {
                "name": "",
                "category": "Quest",
                "description": "",
                "requester": "",
                "location": "",
                "reward": "",
                "due_date": "",
                "due_elapsed_minutes": -1,
                "requires_ai_invention": mode == "ai",
            },
        }

    task["requires_ai_invention"] = any(
        not str(task[field]).strip()
        for field in [
            "name",
            "description",
            "requester",
            "location",
            "reward",
            "due_date",
        ]
    )
    return {"mode": mode, "task": task}


def normalize_economy_examples(raw_examples: Any) -> list[dict[str, Any]]:
    """Returns clean common-price examples measured in base currency units."""

    if not isinstance(raw_examples, list):
        return []

    examples: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for raw_example in raw_examples:
        if not isinstance(raw_example, dict):
            continue

        name = _clean_text(
            raw_example.get("name", raw_example.get("item_name", raw_example.get("item")))
        )
        value = _safe_int(
            raw_example.get(
                "value_base_units",
                raw_example.get("base_unit_value", raw_example.get("value")),
            ),
            0,
        )
        name_key = name.casefold()

        if not name or value <= 0 or name_key in seen_names:
            continue

        examples.append({"name": name, "value_base_units": value})
        seen_names.add(name_key)

    return examples


def describe_economy_examples(examples: Any) -> str:
    """Returns concise AI-facing prose for common-price examples."""

    clean_examples = normalize_economy_examples(examples)

    if not clean_examples:
        return ""

    parts = [
        f"{example['name']} costs {example['value_base_units']} base units"
        for example in clean_examples
    ]
    return "Common price examples: " + "; ".join(parts) + "."


def _combine_currency_description(description: str, economy_examples_description: str) -> str:
    """Combines legacy currency notes with structured price examples."""

    if description and economy_examples_description:
        if economy_examples_description in description:
            return description

        return f"{description}\n{economy_examples_description}"

    return description or economy_examples_description


def _looks_like_item_request(text: str) -> bool:
    """Returns True when a starter-item line reads like an AI design brief."""

    clean_text = str(text or "").strip()

    if not clean_text:
        return False

    words = clean_text.split()

    if len(words) >= 5:
        return True

    lowered = clean_text.casefold()

    if lowered.startswith(("a ", "an ", "the ", "my ", "his ", "her ", "their ", "our ")):
        return True

    return any(marker in clean_text for marker in [".", ",", ";", ":"])


def _calendar_from_setup(raw_calendar: Any) -> dict[str, Any]:
    """Normalizes setup calendar settings."""

    if not isinstance(raw_calendar, dict):
        raw_calendar = {}

    calendar_type = str(raw_calendar.get("calendar_type", "")).strip().casefold()

    if calendar_type == "ai_generated":
        settings = _copy_calendar_settings(AI_GENERATED_CALENDAR_FALLBACK_SETTINGS)
        settings["calendar_type"] = "ai_generated"
        settings["ai_generated"] = True
    elif not raw_calendar or calendar_type == "gregorian":
        settings = dict(GREGORIAN_CALENDAR_SETTINGS)
        settings["calendar_type"] = "gregorian"
        settings["ai_generated"] = False
    else:
        settings = {**GREGORIAN_CALENDAR_SETTINGS, **raw_calendar}
        settings["calendar_type"] = "custom"
        settings["ai_generated"] = False

    settings["time_display"] = str(
        raw_calendar.get("time_display")
        or raw_calendar.get("time_format")
        or settings.get("time_display")
        or "12_hour"
    )

    clean_settings = normalize_calendar_settings(settings)
    clean_settings["calendar_type"] = str(settings.get("calendar_type", "custom"))
    clean_settings["ai_generated"] = bool(settings.get("ai_generated", False))
    return clean_settings


def calendar_looks_like_default_gregorian(raw_calendar: Any) -> bool:
    """Returns True when calendar output is the default Gregorian placeholder."""

    if not isinstance(raw_calendar, dict) or not raw_calendar:
        return False

    calendar = normalize_calendar_settings(raw_calendar)
    gregorian = normalize_calendar_settings(GREGORIAN_CALENDAR_SETTINGS)
    same_shape = all(
        int(calendar[key]) == int(gregorian[key])
        for key in [
            "days_per_week",
            "weeks_per_month",
            "months_per_year",
            "seasons_per_year",
        ]
    )

    if not same_shape:
        return False

    day_names = _folded_names(calendar["day_names"])
    month_names = _folded_names(calendar["month_names"])
    season_names = _folded_names([season["name"] for season in calendar["seasons"]])
    gregorian_days = _folded_names(gregorian["day_names"])
    gregorian_months = _folded_names(gregorian["month_names"])
    generic_months = _folded_names([f"Month {index}" for index in range(1, 13)])
    gregorian_seasons = _folded_names([season["name"] for season in gregorian["seasons"]])

    return (
        day_names == gregorian_days
        and (month_names == gregorian_months or month_names == generic_months)
        and season_names == gregorian_seasons
    )


def ai_generated_calendar_settings_or_fallback(
    raw_calendar: Any,
    *,
    genre_hint: str = "",
) -> dict[str, Any]:
    """Returns AI calendar settings, replacing incompatible placeholder output."""

    fallback = _ai_generated_calendar_fallback_for_genre(genre_hint)

    if calendar_looks_like_default_gregorian(raw_calendar):
        return _copy_calendar_settings(fallback)

    clean_calendar = normalize_calendar_settings(raw_calendar)

    if calendar_looks_like_default_gregorian(clean_calendar):
        return _copy_calendar_settings(fallback)

    if (
        _looks_like_sci_fi_context(genre_hint)
        and calendar_looks_like_generic_fantasy_artisan(clean_calendar)
    ):
        return _copy_calendar_settings(AI_GENERATED_SCI_FI_CALENDAR_FALLBACK_SETTINGS)

    return clean_calendar


def calendar_looks_like_generic_fantasy_artisan(raw_calendar: Any) -> bool:
    """Returns True for the old Hearth/Market-style AI calendar fallback."""

    if not isinstance(raw_calendar, dict) or not raw_calendar:
        return False

    calendar = normalize_calendar_settings(raw_calendar)
    names = set(_folded_names(calendar["day_names"]))
    names.update(_folded_names(calendar["month_names"]))
    names.update(_folded_names([season["name"] for season in calendar["seasons"]]))

    return bool(names.intersection(GENERIC_FANTASY_ARTISAN_CALENDAR_NAMES))


def _ai_generated_calendar_fallback_for_genre(genre_hint: str) -> dict[str, Any]:
    """Returns a fallback calendar suited to broad setup genre hints."""

    if _looks_like_sci_fi_context(genre_hint):
        return AI_GENERATED_SCI_FI_CALENDAR_FALLBACK_SETTINGS

    return AI_GENERATED_CALENDAR_FALLBACK_SETTINGS


def _looks_like_sci_fi_context(value: str) -> bool:
    """Returns True when setup prose clearly asks for a futuristic premise."""

    clean_value = str(value or "").casefold()

    return any(marker in clean_value for marker in SCI_FI_CONTEXT_MARKERS)


def _copy_calendar_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Returns a deep-enough copy of calendar settings for mutation."""

    return {
        **settings,
        "day_names": list(settings.get("day_names", [])),
        "month_names": list(settings.get("month_names", [])),
        "seasons": [
            dict(season)
            for season in settings.get("seasons", [])
            if isinstance(season, dict)
        ],
    }


def _folded_names(values: Any) -> list[str]:
    """Returns case-insensitive names for calendar comparisons."""

    if not isinstance(values, list):
        return []

    return [str(value).strip().casefold() for value in values]


def _build_ai_additional_context(
    *,
    specified_genre: str,
    game_style: str,
    world_context: str,
    additional_context: str = "",
) -> str:
    """Builds AI-facing setup instructions from wizard inputs."""

    lines: list[str] = []

    if specified_genre:
        lines.append(f"Specified genre: {specified_genre}")

    if game_style:
        lines.append(f"Game style: {game_style}")

    if world_context:
        lines.append(f"World creation context: {world_context}")

    if additional_context:
        lines.append(additional_context)

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


def _clamped_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Converts a value to int and clamps it to an inclusive range."""

    parsed = _safe_int(value, default)
    return max(minimum, min(maximum, parsed))


def _safe_bool(value: Any, default: bool) -> bool:
    """Converts common setting values into a bool."""

    if isinstance(value, bool):
        return value

    if value is None:
        return default

    if isinstance(value, str):
        clean_value = value.strip().casefold()
        if clean_value in {"1", "true", "yes", "on", "enabled"}:
            return True
        if clean_value in {"0", "false", "no", "off", "disabled"}:
            return False

    return bool(value)
