from __future__ import annotations

import random
from typing import Any

from ai_adventure.ai.modes import normalize_ai_mode_preferences
from ai_adventure.ai.model_catalog import normalize_image_preferences
from ai_adventure.calendar_system import (
    DEFAULT_START_ELAPSED_MINUTES,
    build_calendar_snapshot,
    normalize_calendar_settings,
    resolve_starting_calendar_minute,
)
from ai_adventure.audio.tts_settings import normalize_tts_audio_fields
from ai_adventure.audio.catalog import distinct_audio_track_catalogs_with_ambience
from ai_adventure.audio.pronunciation import (
    normalize_pronunciation_map,
    set_authoritative_pronunciation,
)
from ai_adventure.context.creative_ideas import CreativeIdeasLibrary
from ai_adventure.context.naming import GENERIC_PROPER_NOUN_PLACEHOLDER_RULE
from ai_adventure.currency import (
    describe_currency_denominations,
    normalize_currency_denominations,
)
from ai_adventure.narration_preferences import normalize_narration_preferences
from ai_adventure.magic import normalize_magic_setup
from ai_adventure.combat import COMBAT_FOCUS_INSTRUCTIONS, normalize_combat_preferences


SKILL_PRESET_LEVEL_PLANS: dict[str, list[int]] = {
    "professional": [5, 4, 4, 3, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1, 1],
    "experienced": [4, 3, 3, 2, 2, 2, 1, 1, 1, 1],
    "average": [3, 2, 2, 1, 1, 1],
    "beginner": [2, 1, 1],
    "blank": [],
}
SKILL_LEVEL_PLAN = SKILL_PRESET_LEVEL_PLANS["professional"]
STARTER_INVENTORY_MIN_ITEMS = 5
DEFAULT_STARTING_WEALTH_GUIDANCE = (
    "They should have enough money to cover a few meals."
)

CHARACTER_PRONOUN_OPTIONS = ("He/Him", "She/Her", "They/Them")
DEFAULT_CHARACTER_PRONOUNS = "They/Them"

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
GREGORIAN_WEEKDAY_NAMES = frozenset(
    str(name).strip().casefold()
    for name in GREGORIAN_CALENDAR_SETTINGS["day_names"]
)
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
    opening_scene_request = _clean_text(raw_setup.get("opening_scene_request"))
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
    starting_wealth = normalize_starting_wealth(
        raw_setup.get("starting_wealth", {}),
        currency_denominations,
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
    starting_calendar = _starting_calendar_from_setup(
        raw_setup.get("starting_calendar", {})
    )
    audio_settings = _audio_from_setup(raw_setup.get("audio", {}))
    starting_weather = _clean_text(raw_setup.get("starting_weather"))[:120]
    raw_character_name_pronunciation = _clean_text(
        character.get("name_pronunciation")
    )
    pronunciation_map = normalize_pronunciation_map(
        raw_setup.get("pronunciation_map", {})
    )
    if raw_character_name_pronunciation and character.get("name"):
        pronunciation_map = set_authoritative_pronunciation(
            pronunciation_map,
            character["name"],
            raw_character_name_pronunciation,
        )
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
    image_preferences = normalize_image_preferences(raw_setup.get("images", {}))
    custom_ai_context = _clean_text(raw_ai_settings.get("additional_context"))
    skill_preset = _normalize_skill_preset(raw_setup.get("skill_preset"))
    skill_level_plan = _skill_level_plan_for_setup(raw_setup, skill_preset)
    skills = _normalize_skills(raw_setup.get("skills", []), skill_level_plan)
    starter_inventory_mode = _normalize_starter_inventory_mode(
        raw_setup.get("starter_inventory_mode", "basic")
    )
    starter_items = _normalize_starter_items(raw_setup.get("starter_items", []))
    no_starting_npcs = bool(raw_setup.get("no_starting_npcs", False))
    starting_npcs = (
        []
        if no_starting_npcs
        else _normalize_starting_npcs(raw_setup.get("starting_npcs", []))
    )
    starting_party_npc_ids = _normalize_starting_party_npc_ids(
        raw_setup.get("starting_party_npc_ids", raw_setup.get("starting_party", [])),
        starting_npcs,
    )
    starting_locations = _normalize_starting_locations(
        raw_setup.get("starting_locations", [])
    )
    starting_task = _normalize_starting_task(
        raw_setup.get("starting_task", raw_setup.get("starting_quest", {}))
    )
    magic = normalize_magic_setup(raw_setup.get("magic", {}))
    combat = normalize_combat_preferences(raw_setup.get("combat", {}))

    return {
        "title": _clean_text(raw_setup.get("title")) or "New Adventure",
        "character": {
            "name": _clean_text(character.get("name")) or "Player Name",
            "name_pronunciation": raw_character_name_pronunciation,
            "pronouns": normalize_character_pronouns(character.get("pronouns")),
            "appearance": _clean_text(character.get("appearance")),
            "backstory": _clean_text(character.get("backstory")),
            "notes": _clean_text(character.get("notes")),
        },
        "skills": skills,
        "skill_preset": skill_preset,
        "skill_level_plan": skill_level_plan,
        "starter_inventory_mode": starter_inventory_mode,
        "starter_items": starter_items,
        "starting_npcs": starting_npcs,
        "no_starting_npcs": no_starting_npcs,
        "starting_party_npc_ids": starting_party_npc_ids,
        "starting_locations": starting_locations,
        "starting_task": starting_task,
        "magic": magic,
        "combat": combat,
        "calendar": calendar_settings,
        "starting_calendar": starting_calendar,
        "starting_weather": starting_weather,
        "audio": audio_settings,
        "pronunciation_map": pronunciation_map,
        "narration": narration_preferences,
        "ai_settings": {
            "text_model": ai_mode_preferences["text_model"],
            "model_intelligence": ai_mode_preferences["model_intelligence"],
            "model_tone": ai_mode_preferences["model_tone"],
            "response_length": ai_mode_preferences["response_length"],
            "allowed_content_categories": ai_mode_preferences[
                "allowed_content_categories"
            ],
            "additional_context": custom_ai_context,
        },
        "images": image_preferences,
        "time_display": calendar_settings["time_display"],
        "currency_denominations": currency_denominations,
        "starting_wealth": starting_wealth,
        "currency_description": currency_description,
        "economy_examples": economy_examples,
        "specified_genre": specified_genre,
        "game_style": game_style,
        "start_location": start_location,
        "start_location_mode": start_location_mode,
        "opening_scene_request": opening_scene_request,
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
        "sound_effects_enabled": _safe_bool(
            raw_audio.get("sound_effects_enabled"), True
        ),
        "sound_effects_volume": _clamped_int(
            raw_audio.get("sound_effects_volume"), 35, 0, 100
        ),
        "background_ambience_enabled": _safe_bool(
            raw_audio.get("background_ambience_enabled"), True
        ),
        "background_ambience_volume": _clamped_int(
            raw_audio.get("background_ambience_volume"), 15, 0, 100
        ),
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
    valid_sound_effect_tracks: list[str] | None = None,
    valid_background_ambience_tracks: list[str] | None = None,
) -> dict[str, Any]:
    """Builds a compact AI-facing setup packet for world synthesis."""

    clean_setup = normalize_new_game_setup(setup)
    calendar_is_ai_generated = bool(clean_setup["calendar"].get("ai_generated"))
    packet_setup = dict(clean_setup)
    if calendar_is_ai_generated:
        packet_setup["calendar"] = {
            "calendar_type": "ai_generated",
            "ai_generated": True,
        }
        guidance = clean_setup["calendar"].get("generation_guidance", "")
        if guidance:
            packet_setup["calendar"]["generation_guidance"] = guidance
    (
        clean_music_tracks,
        clean_sound_effect_tracks,
        clean_background_ambience_tracks,
    ) = distinct_audio_track_catalogs_with_ambience(
        valid_music_tracks,
        valid_sound_effect_tracks,
        valid_background_ambience_tracks,
    )
    creative_ideas = CreativeIdeasLibrary.load_default().select_for_new_game()
    starter_item_count = len(clean_setup["starter_items"])
    starting_spell_request_count = len(
        clean_setup["magic"]["starting_spell_requests"]
    )
    ai_mode_preferences = normalize_ai_mode_preferences(clean_setup["ai_settings"])

    packet = {
        "schema_version": 1,
        "packet_type": "new_game_setup",
        "setup": packet_setup,
        "player_ai_preferences": ai_mode_preferences,
        "current_weather": clean_setup["starting_weather"] or "Clear",
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
                "If setup.opening_scene_request is non-empty, use it as player-"
                "authored creative direction for the situation, mood, event, or "
                "hook at that starting location. Honor its intent when coherent, "
                "but turn it into finalized in-world narration rather than copying "
                "the request or exposing meta-instructions. Keep the result "
                "consistent with the finalized start_location, character, world, "
                "and player knowledge. End with the prompt in setup.turn_prompt."
            ),
            "start_location": (
                "setup.start_location_mode controls how to treat the requested "
                "starting location. suggestion means use it only as inspiration "
                "and return a materially different finalized location name; never "
                "copy the suggested text unchanged. exact "
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
                "setup.character.pronouns instead of you/your. The pronouns field "
                "is canonical: use it exactly and never infer replacements from "
                "the character's name, appearance, voice, or backstory. Limited "
                "styles should stay within "
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
            "english_text": (
                "Return printable ASCII English characters in every generated string. "
                "Transliterate accented Latin letters to unaccented English and never "
                "return foreign scripts, IPA, phoneme strings, pronunciation markup, "
                "or pronunciation_map."
            ),
            "speaker_cues": (
                "Return one opening_cues record with kind speaker for every exact "
                "contiguous non-narrator spoken span in introductory_message for "
                "visible speaker bubbles and multi-voice TTS. anchor_text must copy "
                "the complete span including outer double "
                "quotation marks from one unique place. Use an actual NPC's exact "
                "npc_id as speaker_id, reuse IDs for the same speaker, and use distinct "
                "stable lower_snake_case IDs for incidental speakers. speaker_name is "
                "the visible bubble label: use the known name or a concise player-safe "
                "description when the name is unknown. Choose voice_profile from "
                "established audible traits or neutral when unspecified. Return [] "
                "when only the narrator speaks. Python chooses and durably stores the "
                "installed voice ID."
            ),
            "calendar_weather_consistency": (
                "When current_calendar is present, opening prose must match it unless "
                "you return starting_calendar that intentionally changes it. When "
                "setup.calendar.ai_generated is true, current_calendar is deliberately "
                "omitted: invent calendar_settings and starting_calendar first, then "
                "make opening prose match those generated values. Opening prose must "
                "also match current_weather unless the returned weather changes it. "
                "When setup.starting_weather is non-empty, it is authoritative: use "
                "that exact current condition in the opening prose and weather field. "
                "If the opening scene or actual starting-location description establishes "
                "rain, drizzle, snow, fog, or another current condition, return that "
                "condition in weather instead of Clear or another contradictory default."
            ),
            "calendar_generation": (
                "If setup.calendar.ai_generated is true, invent calendar_settings "
                "for the new world using clear day names, month names, seasons, "
                "season weather hints, and time_display. The calendar should fit "
                "the selected genre, world culture, climate, and playstyle. Do "
                "your best to honor setup.calendar.generation_guidance when it is "
                "non-empty: treat requested month names, season names, starting "
                "day/season, or other calendar details as player requirements, "
                "while filling in unspecified details coherently. "
                "When setup.starting_calendar is non-empty, honor its explicit "
                "year, month_number, season_name, day_of_month, and "
                "time_of_day_minutes in the returned starting_calendar for every "
                "calendar type. These player-entered values are authoritative. "
                "Do not copy the default Gregorian calendar. Never use Monday, "
                "Tuesday, Wednesday, Thursday, Friday, Saturday, or Sunday as any "
                "day name. Do not use January-through-December month names, generic "
                "Month 1/Month 2 style "
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
                "Use top-level starting_npcs for setup.starting_npcs rows that "
                "should be durable. The number of starting_npcs entries can be zero, "
                "one, or many; choose the "
                "count from setup.starting_npcs and what the player character "
                "would actually know at setup. Do not parse NPCs out of "
                "ordinary setup prose or plaintext fields. For each "
                "setup.starting_npcs row, create one starting_npcs record and copy its "
                "npc_id exactly. Set party_member true "
                "exactly when that npc_id appears in setup.starting_party_npc_ids, "
                "and false otherwise. When location_source_index is nonnegative, "
                "location must use the finalized name of the corresponding "
                "setup.starting_locations row, including when its suggestion-mode "
                "name changes. Fill blank "
                "name, location, or description fields with fitting specifics. "
                "If description_mode is exact, copy description into "
                "public_description unchanged; if description_mode is "
                "suggestion, use description only as inspiration and write a "
                "materially different public_description; never copy the "
                "suggested description unchanged. Return AI-only "
                "hidden identities, motives, mystery solutions, off-screen plans, "
                "and other concealed truths in the dedicated gm_secrets setup "
                "field; never place those truths in player-facing setup fields."
            ),
            "gm_secrets": (
                "A GM secret must be unknown to both the player and the Player "
                "Character at the start. Never invent a past action the Player "
                "Character consciously performed, something they directly witnessed, "
                "a memory or choice they retain, a possession they know about, or an "
                "item they deliberately hid or stored and treat it as secret. The only "
                "exception is when setup explicitly establishes a credible knowledge "
                "barrier such as amnesia, memory alteration, unconsciousness, or "
                "deception. A reveal condition must discover an externally hidden "
                "truth; it cannot be a skill check or search that makes the Player "
                "Character rediscover their own knowing act. Valid examples include "
                "an NPC's hidden identity or motive, a conspiracy, a trap, someone "
                "else's off-screen plan, or an object planted without the Player "
                "Character's knowledge. Player-known facts belong in backstory, notes, "
                "inventory, or other public state; if setup did not establish such a "
                "fact, omit it rather than secretly inventing it. Every returned "
                "record must use exactly secret_id, title, details, reveal_condition, "
                "related_npc_ids, and related_locations; do not use legacy keys such "
                "as gm_secret_id or secret."
            ),
            "miscellaneous": (
                "Return established non-secret world canon that does not fit a "
                "Location, NPC, Item, active task, creature, or GM secret in the top-level "
                "miscellaneous array. Use stable misc_id values and complete name, "
                "category, and details fields. This includes original creatures or "
                "species, cultures, factions, religions, laws, historical events, "
                "and supernatural or scientific phenomena. Creature records belong "
                "in the separate bestiary array. "
                "Do not duplicate records "
                "that belong in another structured field. Return an empty array when "
                "no such starting canon is needed."
            ),
            "bestiary": (
                "Return starting player-known non-NPC creatures in the top-level "
                "bestiary array. Use stable creature_id values and complete details "
                "containing only facts known to the Player or Player Character. "
                "Return an empty array when no starting creature lore is needed."
            ),
            "starting_task": (
                "setup.starting_task.mode controls the initial active quest. If "
                "mode is none, do not return top-level starting_task "
                "unless another explicit setup field independently asks for one. "
                "If mode is ai, return one fitting top-level starting_task, including "
                "a complete player-visible "
                "description of what must be done, currently known relevant people "
                "and places, and how to recognize completion. Treat "
                "setup.starting_task.guidance as "
                "optional player inspiration: honor its idea while inventing all "
                "unspecified quest details. If mode is custom, return exactly one "
                "top-level starting_task using the player's provided fields as "
                "authoritative anchors, filling any blank/default fields from the "
                "rest of the setup. Use category Quest unless the player provided "
                "a different category."
            ),
            "starting_music": (
                "Return one opening_cues record with kind music and one exact filename "
                "from audio.valid_music_tracks when a listed track fits the opening; "
                "otherwise return no music cue."
            ),
            "starting_sound_effect": (
                "For every specific moment in the opening narration that would "
                "genuinely benefit from a short sound, return a separate object in "
                "opening_cues with kind sound_effect when an appropriate listed "
                "effect exists. "
                "There is no fixed cue-count target. Each filename must exactly match one entry "
                "from audio.valid_sound_effect_tracks and must never come from "
                "audio.valid_music_tracks. "
                "For every cue, copy a unique exact excerpt from introductory_message "
                "into anchor_text and set position to before or after. Each plays once and "
                "must never be used for looping ambience."
            ),
            "starting_background_ambience": (
                "When a listed environmental ambience fits the opening location, "
                "return one opening_cues record with kind background_ambience and its "
                "exact filename; otherwise return no background_ambience cue. "
                "This is a quiet, persistent loop separate from music and one-shot "
                "sound effects."
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
                "mean the player character should default to male. "
                "setup.character.pronouns is always player-selected and canonical; "
                "copy and use it exactly without inferring different pronouns from "
                "the name, appearance, voice, genre, or invented details. Follow "
                "character_generation_guidance.gender_presentation_hint only for "
                "details that do not conflict with those pronouns, and use "
                "creative_ideas.player_character_name_examples "
                "as a balanced name pool when useful. If the player supplied a custom "
                "character name, appearance, backstory, or notes value, preserve that "
                "field exactly instead of rewriting, renaming, embellishing, or "
                "reinterpreting it. When inventing appearance, make it concise, "
                "concrete, and visually depictable using player-visible traits. The "
                "application generates images separately; never add image prompts, "
                "filenames, URLs, encoded data, or image-specific response fields."
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
                "setup.start_location only as inspiration and return a materially "
                "different final name; never copy the suggestion unchanged."
            ),
            "travel_locations": (
                "Return a locations array for the Travel tab containing exactly "
                "the places the player character plausibly knows at setup. There "
                "is no minimum or maximum count beyond the current starting place. "
                "Use setup.starting_locations as structured player-requested "
                "starting Travel-tab locations; do not parse starting locations "
                "out of ordinary setup prose or plaintext fields. For each "
                "setup.starting_locations row, include one corresponding entry in "
                "locations and set source_index to that row's zero-based index. "
                "Use source_index=-1 only for extra locations not based on a "
                "requested row. Fill blank name or description fields with fitting "
                "specifics. If location_mode is exact, copy name and description "
                "into the locations entry unchanged, while still filling terrain, "
                "coordinates, travel_multiplier, and route notes. If location_mode "
                "is suggestion, use name and description only as inspiration and "
                "return materially different finalized values for both nonblank "
                "fields; never copy either suggestion unchanged. Once "
                "a suggested location name is finalized, use that finalized name "
                "consistently in every other returned field, including descriptions, "
                "travel_notes, NPC details, tasks, "
                "secrets, and opening prose; never reuse the superseded setup "
                "placeholder or suggestion name. "
                "If is_sublocation is true and parent_location is set, treat the "
                "location as existing inside that parent location; reflect that "
                "relationship in the returned location description and travel_notes "
                "without creating a separate hidden route. "
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
                "items/materials and recipes as fit the backstory. "
                "For each known crafting item, location must be a comma-separated "
                "list of generalized environments or source areas such as Forests, "
                "Caves, Wetlands, Workshops, or Urban Scrap, never a specific named "
                "Travel-tab location. Include rarity as Common, Uncommon, Rare, or "
                "Very Rare; notes must end with exactly one sentence in the form "
                "Rarity: <rarity>. after any other player-known notes. Include "
                "value_base_units as a reasonable per-unit price in the baseline "
                "currency. Rare and Very Rare items must be materially more expensive "
                "than comparable Common items unless the setting explicitly provides "
                "a scarcity-independent reason otherwise. "
                "ingredients must use item names from known_crafting_items or "
                "other known item catalog entries and use quantity, measure_amount, "
                "and a finite measure_unit from the supplied enum. Do not use vague "
                "units, and keep each ingredient measure_unit equal to the matching "
                "starting item's quantity_unit. quantity times measure_amount is the "
                "total amount consumed per crafted result. Do not use vague "
                "units such as pinch or handful. Categorize vials, bottles, jars, "
                "and similar vessels as Container. Each recipe must include "
                "self-contained notes stating its intended purpose/effect, expected "
                "strength or outcome, onset, duration, and important use conditions; "
                "say unknown or not applicable when a detail is not established. "
                "value_base_units as a reasonable estimated result value in the "
                "world's baseline currency unit."
            ),
            "skill_limits": (
                "Skills use levels 1 through 5, with 5 as the absolute maximum. "
                "Never create a skill, prerequisite, progression target, or GM-secret "
                "reveal condition that requires a skill level above 5."
            ),
            "skill_generation": (
                "Return exactly the starting skill slots supplied in setup.skills, "
                "preserving every slot's level. If setup.skills is empty for Blank "
                "Slate / Hardcore Mode, return no starting skills. "
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
                "First identify the activities, responsibilities, and goals that "
                "the player emphasizes in the character description, backstory, "
                "notes, profession, and skills. Prioritize concrete tools and "
                "supplies that enable those emphasized activities before generic "
                "apparel, comfort items, or genre-standard kit. Infer function "
                "from the whole character concept rather than matching a fixed "
                "keyword list. Assign each item's category from its actual primary "
                "function. A Container must primarily hold physical contents that "
                "can be put in and taken out; an object that stores writing, records, "
                "instructions, or information is not a Container merely because it "
                "stores information. "
                "Preserve any player-provided starter items with "
                "requires_ai_invention=false. "
                "setup.starter_inventory_mode is basic or advanced. In basic "
                "mode, each setup starter-item row is a narrative suggestion "
                "that the AI must turn into fitting concrete item details. In "
                "advanced mode, preserve the player's exact structured values. "
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
                "description, value_base_units, and source_index. Every item must "
                "also include quantity_unit, such as each, bundle, bottle, vial, "
                "gram, kilogram, ounce, liter, or meter. Classify a physical journal, "
                "notebook, ledger, manual, or other book as Book or Document, not "
                "Information; Information describes content, not a physical item. "
                "Every finalized starting item must also include storage_location. "
                "Use home for items kept in the player's house, workshop, base, room, "
                "or other home storage, and actively_carried only for items the Player "
                "Character is actually carrying. Interpret phrases such as 'kept in "
                "their house' in item_request as home storage. For every fictional, "
                "unfamiliar, or newly invented item, make description explicitly "
                "state concrete visible traits beyond the name: form, approximate "
                "size, color, material, texture, markings, condition, opacity or "
                "translucency, and any visible changes under relevant conditions such "
                "as sunlight, darkness, heat, or moisture. Never make the name alone "
                "carry the item's visual identity."
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
                "setup.starting_wealth is authoritative. In basic mode, return "
                "starting_currency_balance_base_units as the player character's "
                "actual starting money, choosing one nonnegative integer amount "
                "that follows setup.starting_wealth.guidance and fits the finalized "
                "economy and setup.economy_examples. In advanced mode, Python "
                "already calculated setup.starting_wealth.balance_base_units from "
                "the exact denomination/count rows; preserve that amount and do not "
                "return or replace it. Starting wealth is stored only in "
                "game_state/currency.balance. Never create coin, purse, cash, wallet, "
                "or credit inventory items to represent spendable money."
            ),
            "magic": (
                "setup.magic is authoritative. If world_contains_magic is false, "
                "the setting contains no magic: do not introduce spells, magical "
                "creatures, supernatural powers, enchanted items, magical traditions, "
                "or other magic anywhere in the world or opening scene. If the world "
                "contains magic, use casting_mode and tradition to define how magic "
                "works in the world even when enabled is false; enabled=false means "
                "only that the Player Character cannot cast spells at the start. "
                "Keep the opening world consistent with casting_mode and tradition. "
                "When enabled is true, also keep the Player Character consistent with "
                "mana_maximum and tier_slots. Narrative casting has "
                "no consumable resource; Mana casting uses each spell's mana_cost; "
                "Tiered casting uses one slot of the selected tier except for tier 0. "
                "starting_spells_mode is basic or advanced. In basic mode, convert "
                "each setup.magic.starting_spell_requests entry into exactly one "
                "complete, setting-appropriate spell in the top-level starting_spells "
                "output, with matching source_index; invent its name and mechanics "
                "instead of copying spell_request as its name. In advanced mode, the "
                "application stores the exact setup.magic.starting_spells directly. "
                "Never use any other new-game field to add, remove, rename, or alter starting "
                "spells."
            ),
            "combat": (
                "setup.combat is authoritative. Follow its focus instruction when "
                "deciding how prominent fights should be. strict resolution means "
                "the Python Combat tab resolves actual fights; narrative resolution "
                "means Gemini describes and resolves fights in story prose without "
                "CombatStartedEvent or the deterministic Combat tab."
            ),
            "creative_ideas": (
                "Treat creative_ideas as high-priority style seeds when inventing "
                "names, locations, cultures, religions, foods, drinks, species, "
                "crafting ingredients, magic styles, and other world details. "
                "Strongly prefer the examples or close stylistic relatives over "
                "generic training-data fantasy defaults. The banned_terms list is "
                "a hard exclusion list, not optional style guidance: never use any "
                "term listed in creative_ideas.banned_terms, nor obvious spelling, "
                "hyphenation, or reskin variants, for newly generated NPC or location "
                "names or references to those names in other returned fields. Calendar "
                "settings are exempt and follow the separate calendar-generation rules. "
                "Before returning JSON, replace any banned NPC or location name with a "
                "fresh non-banned name. "
                f"{GENERIC_PROPER_NOUN_PLACEHOLDER_RULE}"
            ),
        },
        "fields_requiring_ai_invention": _fields_requiring_ai_invention(clean_setup),
        "starter_inventory_contract": {
            "mode": clean_setup["starter_inventory_mode"],
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
            "guidance": clean_setup["starting_task"]["guidance"],
            "task": clean_setup["starting_task"]["task"],
            "rules": (
                "none means no requested opening quest; ai means invent a fitting "
                "opening quest, using guidance as optional inspiration when it is "
                "present; custom means use provided fields and have the AI fill "
                "blanks from the rest of the setup. Every finalized starting quest "
                "must include a complete player-visible description explaining the "
                "objective, currently known relevant people and places, and how to "
                "recognize completion."
            ),
            "category_rule": (
                "Classify each finalized item by its present primary function, not "
                "its origin or packaging. A ready-to-use poison or toxin is Poison "
                "even in a vial; Ingredient or Reagent is for a recipe input that "
                "still needs processing."
            ),
        },
        "starting_wealth_contract": {
            "mode": clean_setup["starting_wealth"]["mode"],
            "guidance": clean_setup["starting_wealth"]["guidance"],
            "amounts": clean_setup["starting_wealth"]["amounts"],
            "balance_base_units": clean_setup["starting_wealth"][
                "balance_base_units"
            ],
            "requires_ai_invention": clean_setup["starting_wealth"][
                "requires_ai_invention"
            ],
            "storage_rule": (
                "The finalized amount is one integer in game_state/currency.balance, "
                "never an inventory item."
            ),
        },
        "magic_contract": {
            "enabled": clean_setup["magic"]["enabled"],
            "casting_mode": clean_setup["magic"]["casting_mode"],
            "tradition": clean_setup["magic"]["tradition"],
            "starting_spells_mode": clean_setup["magic"]["starting_spells_mode"],
            "starting_spell_request_count": starting_spell_request_count,
            "starting_spell_count": len(clean_setup["magic"]["starting_spells"]),
            "rules": (
                "Basic spell requests require one finalized top-level starting_spells "
                "record per request. Advanced spells are confirmed player input. "
                "Reflect either mode in prose, but never synthesize replacement spell "
                "events during new-game generation."
            ),
        },
        "combat_contract": {
            "resolution_mode": clean_setup["combat"]["resolution_mode"],
            "focus": clean_setup["combat"]["focus"],
            "focus_instruction": COMBAT_FOCUS_INSTRUCTIONS[clean_setup["combat"]["focus"]],
            "rules": (
                "Strict mode hands actual fights to Python with CombatStartedEvent. "
                "Narrative mode lets Gemini resolve fights in prose and forbids "
                "CombatStartedEvent. The selected focus guides frequency, not a "
                "requirement to force implausible encounters."
            ),
        },
        "turn_prompt": _turn_prompt_for_setup(clean_setup),
        "character_generation_guidance": _character_generation_guidance(clean_setup),
        "genre_generation_guidance": _genre_generation_guidance(clean_setup),
        "audio": {
            "valid_music_tracks": clean_music_tracks,
            "current_music": "",
            "valid_sound_effect_tracks": clean_sound_effect_tracks,
            "valid_background_ambience_tracks": clean_background_ambience_tracks,
            "current_background_ambience": "",
        },
        "creative_ideas": creative_ideas,
    }
    if not clean_music_tracks:
        packet["requirements"].pop("starting_music", None)
    if not clean_sound_effect_tracks:
        packet["requirements"].pop("starting_sound_effect", None)
    if not clean_background_ambience_tracks:
        packet["requirements"].pop("starting_background_ambience", None)
    if not calendar_is_ai_generated:
        packet["current_calendar"] = build_calendar_snapshot(
            resolve_starting_calendar_minute(
                clean_setup["starting_calendar"],
                clean_setup["calendar"],
                default_current_minute=DEFAULT_START_ELAPSED_MINUTES,
            ),
            clean_setup["calendar"],
        )
    return packet


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

    if (
        not clean_setup["start_location"]
        or clean_setup["start_location_mode"] == "suggestion"
    ):
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

    if clean_setup["magic"]["starting_spell_requests"]:
        invention_fields.append("starting spells from player descriptions")

    if any(
        bool(location.get("requires_ai_invention"))
        for location in clean_setup["starting_locations"]
    ):
        invention_fields.append("suggested or incomplete starting locations")

    if any(
        bool(npc.get("requires_ai_invention"))
        for npc in clean_setup["starting_npcs"]
    ):
        invention_fields.append("suggested or incomplete starting NPCs")

    if clean_setup["starting_task"]["mode"] == "ai":
        invention_fields.append("opening quest/task")
    elif clean_setup["starting_task"]["mode"] == "custom" and clean_setup[
        "starting_task"
    ]["task"].get("requires_ai_invention"):
        invention_fields.append("blank starting quest/task fields")

    if not clean_setup["currency_denominations"]:
        invention_fields.append("economy and currency denominations")

    if clean_setup["starting_wealth"]["mode"] == "basic":
        invention_fields.append("starting wealth from player guidance")

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
    canonical_pronouns = normalize_character_pronouns(character.get("pronouns"))
    gender_presentation_hint = {
        "He/Him": "male-coded",
        "She/Her": "female-coded",
        "They/Them": "androgynous or nonbinary-coded",
    }.get(canonical_pronouns, "player-defined; do not infer a gender")
    needs_character_invention = (
        character["name"] == "Player Name"
        or not character["appearance"]
        or not character["backstory"]
        or not character["notes"]
    )

    if not needs_character_invention:
        return {
            "rule": "Preserve the player-provided character identity and details.",
            "canonical_pronouns": canonical_pronouns,
            "gender_presentation_hint": gender_presentation_hint,
        }

    return {
        "rule": (
            "Use this only for blank/default character fields. It is a creative "
            "variety hint, not permission to replace the canonical pronouns."
        ),
        "canonical_pronouns": canonical_pronouns,
        "gender_presentation_hint": gender_presentation_hint,
        "anti_default_rule": (
            "Do not infer different pronouns from a blank/default name or character "
            "description. Vary names, appearance, and backstory without conflicting "
            "with canonical_pronouns."
        ),
    }


def normalize_starting_wealth(
    raw_wealth: Any,
    denominations: Any,
) -> dict[str, Any]:
    """Returns the canonical Basic/Advanced new-game wealth contract."""

    source = raw_wealth if isinstance(raw_wealth, dict) else {}
    mode = (
        "advanced"
        if str(source.get("mode", "basic")).strip().casefold() == "advanced"
        else "basic"
    )
    guidance = _clean_text(source.get("guidance")) or DEFAULT_STARTING_WEALTH_GUIDANCE
    clean_denominations = normalize_currency_denominations(
        denominations,
        fallback_denominations=[],
    )
    denominations_by_value = {
        int(denomination["value"]): denomination
        for denomination in clean_denominations
    }
    denominations_by_name = {
        str(denomination["name"]).strip().casefold(): denomination
        for denomination in clean_denominations
    }
    raw_amounts = source.get("amounts", [])
    if not isinstance(raw_amounts, list):
        raw_amounts = []
    quantities_by_value: dict[int, int] = {}
    for raw_amount in raw_amounts:
        if not isinstance(raw_amount, dict):
            continue
        value = _safe_int(raw_amount.get("denomination_value"), 0)
        denomination = denominations_by_value.get(value)
        if denomination is None:
            name = _clean_text(raw_amount.get("denomination_name")).casefold()
            denomination = denominations_by_name.get(name)
        if denomination is None:
            continue
        denomination_value = int(denomination["value"])
        quantity = _clamped_int(raw_amount.get("quantity"), 0, 0, 1_000_000_000)
        quantities_by_value[denomination_value] = min(
            1_000_000_000,
            quantities_by_value.get(denomination_value, 0) + quantity,
        )

    amounts = [
        {
            "denomination_name": str(denominations_by_value[value]["name"]),
            "denomination_value": value,
            "quantity": quantity,
        }
        for value, quantity in sorted(quantities_by_value.items())
        if quantity > 0
    ]
    balance_base_units = min(
        9_223_372_036_854_775_807,
        sum(
            int(amount["denomination_value"]) * int(amount["quantity"])
            for amount in amounts
        ),
    )
    return {
        "mode": mode,
        "guidance": guidance if mode == "basic" else "",
        "amounts": amounts if mode == "advanced" else [],
        "balance_base_units": balance_base_units if mode == "advanced" else None,
        "requires_ai_invention": mode == "basic",
    }


def normalize_character_pronouns(value: Any) -> str:
    """Returns standard or player-authored canonical character pronouns."""

    clean_value = _clean_text(value)
    if not clean_value:
        return DEFAULT_CHARACTER_PRONOUNS

    standard_values = {
        option.casefold(): option for option in CHARACTER_PRONOUN_OPTIONS
    }
    return standard_values.get(clean_value.casefold(), clean_value)


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


def _normalize_skill_preset(value: Any) -> str:
    clean_value = _clean_text(value).casefold().replace(" ", "_")
    aliases = {"blank_slate": "blank", "hardcore": "blank", "custom_mode": "custom"}
    clean_value = aliases.get(clean_value, clean_value)
    return clean_value if clean_value in {*SKILL_PRESET_LEVEL_PLANS, "custom"} else "professional"


def _skill_level_plan_for_setup(raw_setup: dict[str, Any], preset: str) -> list[int]:
    if preset != "custom":
        return list(SKILL_PRESET_LEVEL_PLANS[preset])
    raw_plan = raw_setup.get("skill_level_plan", [])
    if not isinstance(raw_plan, list):
        raw_plan = []
    return [max(1, min(5, _safe_int(level, 1))) for level in raw_plan]


def _normalize_skills(raw_skills: Any, level_plan: list[int]) -> list[dict[str, Any]]:
    """Normalizes skills into the selected preset or custom level spread."""

    input_skills = raw_skills if isinstance(raw_skills, list) else []
    normalized: list[dict[str, Any] | None] = [None for _level in level_plan]
    next_position = 0

    for raw_skill in input_skills[: len(level_plan)]:
        if not isinstance(raw_skill, dict):
            raw_skill = {"name": str(raw_skill)}

        requested_level = _safe_int(raw_skill.get("level"), 0)
        target_index = -1

        if requested_level in level_plan:
            for index, level in enumerate(level_plan):
                if level == requested_level and normalized[index] is None:
                    target_index = index
                    break

        if target_index < 0:
            while next_position < len(normalized) and normalized[next_position] is not None:
                next_position += 1

            if next_position >= len(normalized):
                break

            target_index = next_position

        level = level_plan[target_index]
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

        level = level_plan[index]
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

    used_npc_ids: set[str] = set()
    for source_index, raw_npc in enumerate(raw_npcs):
        if not isinstance(raw_npc, dict):
            continue

        name = _clean_text(raw_npc.get("name", raw_npc.get("display_name")))
        npc_id = _clean_text(raw_npc.get("npc_id")) or f"starting_npc_{source_index + 1}"
        base_npc_id = npc_id
        suffix = 2
        while npc_id in used_npc_ids:
            npc_id = f"{base_npc_id}_{suffix}"
            suffix += 1
        used_npc_ids.add(npc_id)
        location = _clean_text(raw_npc.get("location"))
        description = _clean_text(
            raw_npc.get("description", raw_npc.get("public_description"))
        )
        description_mode = _clean_text(
            raw_npc.get("description_mode", raw_npc.get("mode"))
        ).casefold()
        location_source_index = max(
            -1,
            _safe_int(raw_npc.get("location_source_index"), -1),
        )

        if description_mode not in {"suggestion", "exact"}:
            description_mode = "suggestion"

        npcs.append(
            {
                "npc_id": npc_id,
                "name": name,
                "location": location,
                "location_source_index": location_source_index,
                "description": description,
                "description_mode": description_mode,
                "requires_ai_invention": (
                    description_mode == "suggestion"
                    or not name
                    or not location
                    or not description
                ),
            }
        )

    return npcs


def _normalize_starting_party_npc_ids(
    raw_party: Any,
    starting_npcs: list[dict[str, Any]],
) -> list[str]:
    """Keeps unique starting-party selections that still exist in the NPC list."""

    available_ids = {
        str(npc.get("npc_id", "")).strip()
        for npc in starting_npcs
        if str(npc.get("npc_id", "")).strip()
    }
    if not isinstance(raw_party, list):
        return []

    selected_ids: list[str] = []
    for raw_member in raw_party:
        npc_id = (
            _clean_text(raw_member.get("npc_id"))
            if isinstance(raw_member, dict)
            else _clean_text(raw_member)
        )
        if npc_id in available_ids and npc_id not in selected_ids:
            selected_ids.append(npc_id)
    return selected_ids


def _normalize_starting_locations(raw_locations: Any) -> list[dict[str, Any]]:
    """Normalizes structured requested starting Travel-tab location rows."""

    if not isinstance(raw_locations, list):
        return []

    locations: list[dict[str, Any]] = []

    for raw_location in raw_locations:
        if not isinstance(raw_location, dict):
            continue

        name = _clean_text(raw_location.get("name"))
        description = _clean_text(raw_location.get("description"))
        location_mode = _clean_text(
            raw_location.get("location_mode", raw_location.get("mode"))
        ).casefold()

        if location_mode not in {"suggestion", "exact"}:
            location_mode = "suggestion"
        is_sublocation = _safe_bool(
            raw_location.get("is_sublocation", raw_location.get("sublocation")),
            False,
        )
        parent_location = _clean_text(
            raw_location.get(
                "parent_location",
                raw_location.get("containing_location"),
            )
        )

        locations.append(
            {
                "name": name,
                "description": description,
                "location_mode": location_mode,
                "is_sublocation": is_sublocation,
                "parent_location": parent_location if is_sublocation else "",
                "requires_ai_invention": (
                    location_mode == "suggestion" or not name or not description
                ),
            }
        )

    return locations


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
    base_item["storage_location"] = _normalize_starter_storage_location(
        raw_item.get("storage_location", metadata.get("storage_location", "actively_carried"))
    )

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


def _normalize_starter_storage_location(raw_value: Any) -> str:
    """Normalizes a starter item's independent free-text storage label."""

    value = " ".join(_clean_text(raw_value).split())
    return value[:120] or "actively_carried"


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

    guidance = _clean_text(
        raw_task_setup.get("guidance", raw_task_setup.get("ai_guidance"))
    )

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
            "guidance": guidance if mode == "ai" else "",
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
    return {"mode": mode, "guidance": "", "task": task}


def _normalize_starter_inventory_mode(raw_mode: Any) -> str:
    """Returns the requested starter-inventory editing depth."""

    return "advanced" if _clean_text(raw_mode).casefold() == "advanced" else "basic"


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
    if clean_settings["ai_generated"]:
        generation_guidance = _clean_text(
            raw_calendar.get("generation_guidance", "")
        )[:2000]
        if generation_guidance:
            clean_settings["generation_guidance"] = generation_guidance
    return clean_settings


def _starting_calendar_from_setup(raw_starting_calendar: Any) -> dict[str, Any]:
    """Normalizes player-requested starting calendar hints."""

    if not isinstance(raw_starting_calendar, dict):
        return {}

    starting_calendar: dict[str, Any] = {}
    season_name = _clean_text(raw_starting_calendar.get("season_name"))[:120]
    if season_name:
        starting_calendar["season_name"] = season_name

    year = _safe_int(raw_starting_calendar.get("year"), 0)
    if year > 0:
        starting_calendar["year"] = min(9999, year)

    month_number = _safe_int(raw_starting_calendar.get("month_number"), 0)
    if month_number > 0:
        starting_calendar["month_number"] = min(24, month_number)

    day_of_month = _safe_int(raw_starting_calendar.get("day_of_month"), 0)
    if day_of_month > 0:
        starting_calendar["day_of_month"] = min(366, day_of_month)

    raw_time = raw_starting_calendar.get("time_of_day_minutes")
    if raw_time is not None:
        starting_calendar["time_of_day_minutes"] = max(
            0,
            min(1439, _safe_int(raw_time, DEFAULT_START_ELAPSED_MINUTES)),
        )

    return starting_calendar


def merge_authoritative_starting_calendar(
    ai_starting_calendar: Any,
    player_starting_calendar: Any,
) -> dict[str, Any]:
    """Merges a generated start with player-entered date/time fields taking priority."""

    merged = dict(ai_starting_calendar) if isinstance(ai_starting_calendar, dict) else {}
    authoritative = _starting_calendar_from_setup(player_starting_calendar)
    if authoritative:
        merged.pop("current_minute", None)
        if "season_name" in authoritative and "month_number" not in authoritative:
            merged.pop("month_name", None)
            merged.pop("month_number", None)
            merged.pop("season_hint", None)
        merged.update(authoritative)
    return merged


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


def calendar_uses_gregorian_weekday_names(raw_calendar: Any) -> bool:
    """Returns True when any standard Gregorian weekday leaked into a calendar."""

    if not isinstance(raw_calendar, dict):
        return False

    raw_day_names = raw_calendar.get("day_names", [])
    if not isinstance(raw_day_names, list):
        return False

    return any(
        str(day_name).strip().casefold() in GREGORIAN_WEEKDAY_NAMES
        for day_name in raw_day_names
    )


def ai_generated_calendar_settings_or_fallback(
    raw_calendar: Any,
    *,
    genre_hint: str = "",
) -> dict[str, Any]:
    """Returns AI calendar settings, replacing incompatible placeholder output."""

    fallback = _ai_generated_calendar_fallback_for_genre(genre_hint)

    if calendar_looks_like_default_gregorian(
        raw_calendar
    ) or calendar_uses_gregorian_weekday_names(raw_calendar):
        return _copy_calendar_settings(fallback)

    clean_calendar = normalize_calendar_settings(raw_calendar)

    if calendar_looks_like_default_gregorian(
        clean_calendar
    ) or calendar_uses_gregorian_weekday_names(clean_calendar):
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
