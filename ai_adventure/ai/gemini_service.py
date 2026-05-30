from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_adventure.currency import normalize_currency_denominations
from ai_adventure.locations import clean_player_location_name


LOGGER = logging.getLogger(__name__)


DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
KNOWN_TEXT_MODELS = {
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
}


@dataclass(frozen=True)
class GeminiSettings:
    """Runtime settings for the Gemini API integration."""

    api_key: str = ""
    model: str = DEFAULT_GEMINI_MODEL

    @property
    def is_configured(self) -> bool:
        """Returns True when an API key is available."""

        return bool(self.api_key.strip())


@dataclass(frozen=True)
class AiNarrationResult:
    """Parsed result from an AI narration request."""

    narrative_text: str
    suggested_actions: list[str] = field(default_factory=list)
    suggested_events: list[dict[str, Any]] = field(default_factory=list)
    out_of_game: bool = False
    raw_text: str = ""


@dataclass(frozen=True)
class AiWorldSetupResult:
    """Parsed result from an AI new-game world setup request."""

    world_summary: str
    introductory_message: str
    start_location: str = ""
    starting_calendar: dict[str, Any] = field(default_factory=dict)
    start_weather: str = ""
    selected_genre: str = ""
    world_lore: dict[str, dict[str, str]] = field(default_factory=dict)
    finalized_character: dict[str, str] = field(default_factory=dict)
    finalized_skills: list[dict[str, Any]] = field(default_factory=list)
    finalized_starter_items: list[dict[str, Any]] = field(default_factory=list)
    finalized_currency_denominations: list[dict[str, Any]] = field(default_factory=list)
    finalized_currency_description: str = ""
    suggested_events: list[dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""


class GeminiConfigurationError(RuntimeError):
    """Raised when Gemini is requested without required configuration."""


class GeminiNarrationService:
    """Calls Gemini with structured story context packets."""

    def __init__(self, settings: GeminiSettings | None = None) -> None:
        """
        Args:
            settings: Gemini runtime settings. Defaults to environment settings.
        """

        self.settings = settings or load_gemini_settings()

    def generate_story_response(
        self,
        context_packet: dict[str, Any],
    ) -> AiNarrationResult:
        """
        Sends a story context packet to Gemini.

        Args:
            context_packet: Structured context packet from AiContextBuilder.

        Returns:
            Parsed narration result.
        """

        if not self.settings.is_configured:
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not configured. Add it to .env or the environment."
            )

        try:
            from google import genai
        except ImportError as error:
            raise GeminiConfigurationError(
                "google-genai is not installed. Install project requirements first."
            ) from error

        prompt = build_gemini_story_prompt(context_packet)
        client = genai.Client(api_key=self.settings.api_key)

        LOGGER.info("Sending story context packet to Gemini model %s.", self.settings.model)
        response = client.models.generate_content(
            model=self.settings.model,
            contents=prompt,
        )
        raw_text = str(getattr(response, "text", "") or "").strip()
        LOGGER.info("Gemini raw story response:\n%s", raw_text)

        if not raw_text:
            LOGGER.warning("Gemini returned an empty story response.")
            return AiNarrationResult(
                narrative_text="The narrator falls silent for a moment.",
                raw_text=raw_text,
            )

        return parse_gemini_story_response(raw_text)

    def generate_new_game_world(
        self,
        setup_packet: dict[str, Any],
    ) -> AiWorldSetupResult:
        """
        Sends a new-game setup packet to Gemini.

        Args:
            setup_packet: Structured setup packet.

        Returns:
            Parsed world setup result.
        """

        if not self.settings.is_configured:
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not configured. Add it to .env or the environment."
            )

        try:
            from google import genai
        except ImportError as error:
            raise GeminiConfigurationError(
                "google-genai is not installed. Install project requirements first."
            ) from error

        prompt = build_gemini_new_game_prompt(setup_packet)
        client = genai.Client(api_key=self.settings.api_key)

        LOGGER.info("Sending new-game setup packet to Gemini model %s.", self.settings.model)
        response = client.models.generate_content(
            model=self.settings.model,
            contents=prompt,
        )
        raw_text = str(getattr(response, "text", "") or "").strip()
        LOGGER.info("Gemini raw new-game response:\n%s", raw_text)

        if not raw_text:
            LOGGER.warning("Gemini returned an empty new-game response.")
            return AiWorldSetupResult(
                world_summary="The world is still taking shape.",
                introductory_message="The adventure begins.\n\nWhat do you do now?",
                raw_text=raw_text,
            )

        return parse_gemini_new_game_response(raw_text)


def load_gemini_settings(env_path: Path | None = None) -> GeminiSettings:
    """
    Loads Gemini settings from .env and environment variables.

    Args:
        env_path: Optional explicit .env path.

    Returns:
        Gemini settings.
    """

    env_values = _read_env_file(env_path or Path(".env"))

    model = (
        os.getenv("GEMINI_MODEL")
        or env_values.get("GEMINI_MODEL")
        or DEFAULT_GEMINI_MODEL
    ).strip() or DEFAULT_GEMINI_MODEL

    if model not in KNOWN_TEXT_MODELS:
        LOGGER.warning(
            "Gemini model '%s' is not in the known supported text model list: %s.",
            model,
            ", ".join(sorted(KNOWN_TEXT_MODELS)),
        )

    return GeminiSettings(
        api_key=(
            os.getenv("GEMINI_API_KEY")
            or env_values.get("GEMINI_API_KEY")
            or ""
        ).strip(),
        model=model,
    )


def build_gemini_story_prompt(context_packet: dict[str, Any]) -> str:
    """
    Builds the plain-text prompt sent to Gemini.

    Args:
        context_packet: Structured context packet.

    Returns:
        Prompt text.
    """

    packet_json = json.dumps(context_packet, indent=2)

    return (
        "You are the AI narrator for AI Adventure.\n"
        "Use only the structured context packet below as confirmed adventure state.\n"
        "The Python application is the source of truth for state. You may suggest "
        "events, but do not claim that durable state changed unless an event is "
        "suggested for validation.\n\n"
        "NPC knowledge boundary:\n"
        "- The narrator can see the full context packet, but NPCs cannot.\n"
        "- NPCs must not reference private player state such as exact inventory, "
        "currency, flags, quests, hidden history, recent off-screen actions, or "
        "inner thoughts unless they observed it, were told it, or have explicit "
        "NPC knowledge in state.npcs.relevant.\n"
        "- NPCs may infer from visible behavior, but uncertain inferences must sound "
        "uncertain. For example, a bartender may notice careful coin-counting, but "
        "must not know that a coin is the player's last coin unless the player says "
        "so or the bartender saw the purse emptied.\n"
        "- When introducing a meaningful new NPC, suggest NpcUpsertedEvent in events "
        "with the NPC's internal name, player-visible display_name, internal role, "
        "location, public description, player_facing_information, and plausible "
        "knowledge scope. display_name is the name shown in the NPCs tab; use a "
        "generic label such as 'Shady Character' when the player has not learned "
        "the NPC's actual name or role. role is for AI memory and should not be "
        "treated as the player-facing summary.\n"
        "- The events array may contain multiple events with the same type. If the "
        "current turn introduces multiple distinct meaningful NPCs, suggest one "
        "NpcUpsertedEvent for each of them instead of only the first one.\n"
        "- NpcUpsertedEvent.player_facing_information is shown directly in the NPCs "
        "tab under Notes. Write it as player-known information about a person, not "
        "as a mechanical service role. Never put secret identities, hidden motives, "
        "mystery solutions, private plans, or GM-only facts in "
        "player_facing_information. Store hidden NPC or mystery information with "
        "private fields or SecretAddedEvent instead.\n\n"
        "Creative naming boundary:\n"
        "- If the context packet includes creative_ideas, treat those examples as "
        "high-priority style seeds for newly invented names and setting details.\n"
        "- Prefer creative_ideas examples or close stylistic relatives over broad "
        "training-data fantasy defaults, especially for NPCs, settlements, taverns, "
        "factions, religions, regions, ingredients, species, food, and drinks.\n"
        "- Never use creative_ideas.banned_terms, close spelling variants, "
        "hyphenation variants, or obvious reskins for newly invented proper nouns. "
        "Banned terms may appear only when already established in saved state or "
        "explicitly provided by the player.\n\n"
        "Return one JSON object and no surrounding Markdown. The object must match "
        "this shape:\n"
        "{\n"
        '  "response": "Player-facing narration only, with no legacy tags.",\n'
        '  "suggested_actions": ["Action option 1", "Action option 2", "Action option 3"],\n'
        '  "events": [],\n'
        '  "out_of_game": false\n'
        "}\n\n"
        "Rules:\n"
        "- response must be a non-empty string.\n"
        "- response must not contain legacy double-bracket tags.\n"
        "- suggested_actions must be a list, even when empty.\n"
        "- events must be a list, even when empty.\n"
        "- events may include multiple entries of the same event type when multiple "
        "distinct state changes happen in the same turn.\n"
        "- If suggesting events, use the event_shape, known_event_types, and selected event contracts from the packet.\n"
        "- Do not invent hidden state, inventory, recipes, or flags as confirmed facts.\n\n"
        "Context packet:\n"
        f"{packet_json}"
    )


def build_gemini_new_game_prompt(setup_packet: dict[str, Any]) -> str:
    """
    Builds the plain-text prompt for new-game world synthesis.

    Args:
        setup_packet: Structured setup packet.

    Returns:
        Prompt text.
    """

    packet_json = json.dumps(setup_packet, indent=2)

    return (
        "You are creating the initial world setup for AI Adventure.\n"
        "Use only the structured setup packet below as confirmed setup input. "
        "Synthesize the player's choices into a coherent playable world.\n\n"
        "Requirements:\n"
        "- Return one JSON object and no surrounding Markdown.\n"
        "- If the setup packet includes fields_requiring_ai_invention, treat those "
        "fields as blank/default placeholders rather than confirmed facts. Invent "
        "coherent specifics for them, while preserving any custom player-provided "
        "values that are not listed there.\n"
        "- If the setup packet includes creative_ideas, treat them as high-priority "
        "style seeds for invented names and setting details. Strongly prefer these "
        "examples or close stylistic relatives over broad training-data fantasy "
        "defaults, while adapting them so the new game feels distinct.\n"
        "- Never use creative_ideas.banned_terms, close spelling variants, "
        "hyphenation variants, or obvious reskins for newly invented proper nouns. "
        "This includes the player character name, NPC names, locations, taverns, "
        "regions, factions, religions, shops, guilds, and landmarks. Banned terms "
        "may appear only when explicitly provided by the player as confirmed setup "
        "input.\n"
        "- If the setup packet includes character_generation_guidance, follow its "
        "gender_presentation_hint when inventing blank/default player character "
        "fields. A blank/default player character does not imply male. Vary gender "
        "presentation, pronouns, names, appearance, and backstory across new games, "
        "and use creative_ideas.player_character_name_examples as a balanced name "
        "pool when useful.\n"
        "- If setup.specified_genre is blank/default, choose a specific genre or "
        "premise and return it as selected_genre. Do not default to fantasy; "
        "genre_generation_guidance.genre_hint is available as inspiration. If the "
        "player provided setup.specified_genre, preserve it as selected_genre.\n"
        "- Treat the player character's class, profession, backstory, and skills as "
        "facts about the player character, not as instructions that the entire "
        "world must share the same theme. Use them to shape the character, "
        "starting inventory, personal contacts, and immediate opportunities. Do "
        "not make the city's politics, religions, economy, factions, locations, "
        "NPCs, conflicts, and mysteries all revolve around the character's "
        "specialty unless setup.game_style, setup.world_context, or "
        "setup.specified_genre explicitly requests that focus. For example, a "
        "merchant character can live in a city whose religion is about storms, "
        "ancestry, law, harvests, stars, or anything else coherent; the economy "
        "can matter without every institution being coin-themed.\n"
        "- world_summary must be a few paragraphs describing at least the basics "
        "of the world or city, prominent NPCs, locations of interest, religions, "
        "and economy.\n"
        "- world_lore must group player-known starting lore into keyed category "
        "objects where each key is the durable entry name and each value is the "
        "player-facing lore text. Include useful categories such as Locations, Religions, Economy, "
        "Culture and Laws, Factions and Guilds, Prominent NPCs, and Current Rumors "
        "when they fit the game. Do not include secrets, mystery solutions, hidden "
        "villains, or GM-only facts in world_lore.\n"
        "- introductory_message must be player-facing narration for the first "
        "scene at start_location and must end with exactly "
        "'What do you do now?'\n"
        "- introductory_message must match setup_packet.current_calendar and "
        "setup_packet.current_weather unless you intentionally return "
        "starting_calendar and/or weather fields to change the starting date, "
        "season, time, or weather. For example, do not mention autumn winds while "
        "starting_calendar/current_calendar says Spring unless you return a "
        "starting_calendar for Autumn.\n"
        "- start_location must be the actual named location where the player starts. "
        "If setup.start_location is blank/default, choose any coherent starting "
        "location for the selected genre and character. The player does not need "
        "to start in a tavern; a frozen sea, deserted island, ruined store, crime "
        "scene, crashed ship, wilderness trail, city checkpoint, or similar premise "
        "is valid when it fits. Use the same start_location consistently in "
        "introductory_message and events. Keep start_location short and broad: "
        "use the room, building, street, district, ship, campsite, or landmark "
        "name only. Put scenic details such as floor, view, nearby landmarks, "
        "weather, and exact position in introductory_message instead. Example: "
        "use \"Y/N's Office\", not \"Y/N's Office, high up near the penthouse, "
        "overlooking the Hudson River\".\n"
        "- character must finalize the player character profile. If character name, "
        "appearance, backstory, or notes are blank/default placeholders, replace "
        "them with coherent player-facing details suitable for the world. Preserve "
        "explicit custom player input.\n"
        "- skills must contain every starting skill with name, description, and level. "
        "For any skill whose name or description is blank/default/placeholder or "
        "whose setup entry has requires_ai_invention=true, invent a distinct "
        "setting-appropriate name and a concrete description matching that skill. "
        "Skill names must be generalized gameplay capabilities useful across many "
        "checks, not one-off lore phrases, proper nouns, tiny item-maintenance "
        "tasks, or narrow setting trivia. Put local flavor, culture, equipment, "
        "and backstory specifics in the description. Good shapes include "
        "Weather-Reading, Arcana, Navigation, Tinkering, Stealth, Investigation, "
        "Medicine, Performance, Persuasion, Survival, Melee, and Lore (Specific "
        "Domain). Convert specific lore skills to parenthetical domain names, such "
        "as Lore (Syndicate), Lore (Flijosha), or Lore (Merchant Law), rather than "
        "Syndicate Lore or Flijosha Observance. "
        "Never return placeholder descriptions such as 'Player-selected level 1 "
        "starting skill.' Do not reuse generic default names like Athletics, "
        "Awareness, Crafting, Fieldcraft, Investigation, Lore, Medicine, Melee, "
        "Performance, Persuasion, Primary Training, Secondary Training, Signature "
        "Expertise, Stealth, or Survival unless the player explicitly typed that "
        "name. Preserve the exact level spread from setup.skills.\n"
        "- starting_items must contain at least five concrete starting inventory "
        "items. Preserve any player-provided setup.starter_items and add fitting "
        "items as needed. If setup.starter_items is blank or sparse, invent items "
        "that fit the finalized character backstory, finalized skills, selected "
        "genre, starting location, weather, and economy. Each item must include "
        "name, category, quantity, description, and value_base_units.\n"
        "- If setup.currency_denominations is empty, currency_denominations must "
        "contain at least one and at most four concrete denominations that fit "
        "the selected genre, world, and economy. One denomination must have "
        "value=1 as the baseline unit. Other values are exchange rates measured "
        "in that baseline unit and do not need to be multiples or powers of 10. "
        "For example, fantasy worlds may use copper/silver/gold-style coinage, "
        "realistic modern worlds may use dollars, and futuristic or space worlds "
        "may use credits. If setup.currency_denominations already contains "
        "player-provided values, preserve them.\n"
        "- events must be a list, even when empty.\n"
        "- Each event object should use this shape: "
        "{\"type\": \"EventTypeName\", \"payload\": {\"field\": \"value\"}}. "
        "Do not use event_type as the top-level event type key.\n"
        "- Use only player-known information in player-facing event fields.\n"
        "- Use NpcUpsertedEvent for prominent NPCs the player can know about at "
        "setup. Use ActiveTaskUpsertedEvent for initial active obligations. Use "
        "currency_denominations for initial generated money instead of "
        "CurrencyDefinedEvent. Use CurrencyDefinedEvent only when a story event "
        "establishes a new denomination after initial setup. If "
        "setup_packet.audio.valid_music_tracks is non-empty, "
        "use one MusicChangedEvent to choose fitting opening background music; "
        "its filename must exactly match one listed track.\n\n"
        "Return this JSON shape:\n"
        "{\n"
        '  "selected_genre": "Specific genre or premise.",\n'
        '  "world_summary": "Several player-known paragraphs.",\n'
        '  "world_lore": {\n'
        '    "Locations": {"The Gilded Tankard": "Known player-facing location facts."},\n'
        '    "Religions": {"Temple Name": "Known player-facing religion facts."},\n'
        '    "Economy": {"Coinage": "Known player-facing economy facts."},\n'
        '    "Culture and Laws": {"Local Law": "Known player-facing culture or law facts."}\n'
        "  },\n"
        '  "start_location": "Short broad starting location name.",\n'
        '  "starting_calendar": {\n'
        '    "season_name": "Autumn",\n'
        '    "season_hint": "autumn",\n'
        '    "month_name": "September",\n'
        '    "day_of_month": 1,\n'
        '    "time_of_day_minutes": 480\n'
        "  },\n"
        '  "weather": "Clear, cold autumn wind.",\n'
        '  "character": {\n'
        '    "name": "Final character name.",\n'
        '    "appearance": "Final character appearance.",\n'
        '    "backstory": "Final character backstory.",\n'
        '    "notes": "Final character notes/personality hooks."\n'
        "  },\n"
        '  "skills": [\n'
        '    {"name": "Skill name", "description": "Concrete skill description.", "level": 1}\n'
        "  ],\n"
        '  "starting_items": [\n'
        '    {"name": "Item name", "category": "Tool", "quantity": 1, "description": "Concrete item description.", "value_base_units": 10}\n'
        "  ],\n"
        '  "currency_denominations": [\n'
        '    {"name": "Credit", "plural_name": "Credits", "value": 1}\n'
        "  ],\n"
        '  "currency_description": "Player-known description of local money and exchange customs.",\n'
        '  "introductory_message": "Opening scene. What do you do now?",\n'
        '  "events": []\n'
        "}\n\n"
        "Setup packet:\n"
        f"{packet_json}"
    )


def parse_gemini_story_response(raw_text: str) -> AiNarrationResult:
    """
    Parses Gemini narration output.

    Args:
        raw_text: Raw Gemini response text.

    Returns:
        Parsed narration result. Non-JSON output is kept as narrative text.
    """

    clean_text = _strip_json_fence(raw_text.strip())

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError:
        LOGGER.warning("Gemini returned non-JSON narration. Using raw text fallback.")
        return AiNarrationResult(narrative_text=raw_text.strip(), raw_text=raw_text)

    if not isinstance(data, dict):
        LOGGER.warning("Gemini JSON response was not an object. Using raw text fallback.")
        return AiNarrationResult(narrative_text=raw_text.strip(), raw_text=raw_text)

    response_text = data.get("response", data.get("narrative_text"))

    if not isinstance(response_text, str) or not response_text.strip():
        LOGGER.warning("Gemini JSON response omitted response text.")
        response_text = "The narrator has no clear response."

    raw_actions = data.get("suggested_actions", [])

    if not isinstance(raw_actions, list):
        LOGGER.warning("Gemini suggested_actions was not a list. Ignoring it.")
        raw_actions = []

    suggested_actions = [
        str(action).strip()
        for action in raw_actions
        if str(action).strip()
    ]

    raw_events = data.get("events", data.get("suggested_events", []))

    if not isinstance(raw_events, list):
        LOGGER.warning("Gemini events was not a list. Ignoring it.")
        raw_events = []

    suggested_events = [
        event for event in raw_events if isinstance(event, dict)
    ]
    event_types = [
        str(event.get("type", "UnknownEvent")).strip() or "UnknownEvent"
        for event in suggested_events
    ]
    LOGGER.info(
        "Gemini parsed %s suggested event(s): types=%s payload=%s",
        len(suggested_events),
        event_types,
        json.dumps(suggested_events, ensure_ascii=False),
    )
    narrative_text = _format_visible_response(response_text.strip(), suggested_actions)

    return AiNarrationResult(
        narrative_text=narrative_text,
        suggested_actions=suggested_actions,
        suggested_events=suggested_events,
        out_of_game=bool(data.get("out_of_game", False)),
        raw_text=raw_text,
    )


def parse_gemini_new_game_response(raw_text: str) -> AiWorldSetupResult:
    """
    Parses Gemini new-game setup output.

    Args:
        raw_text: Raw Gemini response text.

    Returns:
        Parsed new-game setup result.
    """

    clean_text = _strip_json_fence(raw_text.strip())

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError:
        LOGGER.warning("Gemini returned non-JSON new-game setup. Using raw text fallback.")
        return AiWorldSetupResult(
            world_summary=raw_text.strip(),
            introductory_message="The adventure begins.\n\nWhat do you do now?",
            raw_text=raw_text,
        )

    if not isinstance(data, dict):
        LOGGER.warning("Gemini new-game JSON response was not an object.")
        return AiWorldSetupResult(
            world_summary=raw_text.strip(),
            introductory_message="The adventure begins.\n\nWhat do you do now?",
            raw_text=raw_text,
        )

    world_summary = str(data.get("world_summary", "")).strip()
    selected_genre = str(
        data.get("selected_genre", data.get("genre", ""))
    ).strip()
    start_location = clean_player_location_name(data.get("start_location", ""))
    starting_calendar = _parse_new_game_starting_calendar(
        data.get("starting_calendar", data.get("calendar"))
    )
    start_weather = str(data.get("weather", data.get("start_weather", ""))).strip()
    world_lore = _parse_new_game_world_lore(data.get("world_lore", data.get("lore")))
    introductory_message = str(
        data.get("introductory_message", data.get("response", ""))
    ).strip()
    finalized_character = _parse_new_game_character(data.get("character"))
    finalized_skills = _parse_new_game_skills(data.get("skills"))
    finalized_starter_items = _parse_new_game_starter_items(
        data.get("starting_items", data.get("starter_items", data.get("inventory")))
    )
    finalized_currency_denominations = _parse_new_game_currency_denominations(data)
    finalized_currency_description = _parse_new_game_currency_description(data)
    raw_events = data.get("events", data.get("suggested_events", []))

    if not world_summary:
        LOGGER.warning("Gemini new-game setup omitted world_summary.")
        world_summary = "The world is still taking shape."

    if not introductory_message:
        LOGGER.warning("Gemini new-game setup omitted introductory_message.")
        introductory_message = "The adventure begins.\n\nWhat do you do now?"

    if not introductory_message.rstrip().endswith("What do you do now?"):
        introductory_message = f"{introductory_message.rstrip()}\n\nWhat do you do now?"
    introductory_message = format_story_message(introductory_message)

    if not isinstance(raw_events, list):
        LOGGER.warning("Gemini new-game events was not a list. Ignoring it.")
        raw_events = []

    suggested_events = [
        event for event in raw_events if isinstance(event, dict)
    ]
    LOGGER.info(
        "Gemini parsed %s new-game event(s): payload=%s",
        len(suggested_events),
        json.dumps(suggested_events, ensure_ascii=False),
    )

    return AiWorldSetupResult(
        world_summary=world_summary,
        introductory_message=introductory_message,
        start_location=start_location,
        starting_calendar=starting_calendar,
        start_weather=start_weather,
        selected_genre=selected_genre,
        world_lore=world_lore,
        finalized_character=finalized_character,
        finalized_skills=finalized_skills,
        finalized_starter_items=finalized_starter_items,
        finalized_currency_denominations=finalized_currency_denominations,
        finalized_currency_description=finalized_currency_description,
        suggested_events=suggested_events,
        raw_text=raw_text,
    )


def _parse_new_game_character(raw_character: Any) -> dict[str, str]:
    """Parses finalized new-game character data from Gemini."""

    if not isinstance(raw_character, dict):
        return {}

    character: dict[str, str] = {}

    for key in ["name", "appearance", "backstory", "notes"]:
        value = str(raw_character.get(key, "")).strip()

        if value:
            character[key] = value

    return character


def _parse_new_game_world_lore(raw_lore: Any) -> dict[str, dict[str, str]]:
    """Parses grouped player-facing world lore from Gemini."""

    if not isinstance(raw_lore, dict):
        return {}

    world_lore: dict[str, dict[str, str]] = {}

    for raw_category, raw_entries in raw_lore.items():
        category = str(raw_category).strip()

        if not category:
            continue

        if isinstance(raw_entries, dict):
            entries = {
                str(key).strip(): str(value).strip()
                for key, value in raw_entries.items()
                if str(key).strip() and str(value).strip()
            }
        elif isinstance(raw_entries, str):
            clean_entry = raw_entries.strip()
            entries = {_derive_lore_key(clean_entry): clean_entry} if clean_entry else {}
        elif isinstance(raw_entries, list):
            entries = {}

            for entry in raw_entries:
                clean_entry = str(entry).strip()

                if clean_entry:
                    entries[_derive_lore_key(clean_entry)] = clean_entry
        else:
            entries = {}

        if entries:
            world_lore[category] = entries

    return world_lore


def _derive_lore_key(text: str) -> str:
    """Derives a lore key from list-shaped legacy AI lore."""

    return str(text).split(":", 1)[0].strip()[:80]


def _parse_new_game_starting_calendar(raw_calendar: Any) -> dict[str, Any]:
    """Parses optional AI-selected starting calendar fields."""

    if not isinstance(raw_calendar, dict):
        return {}

    calendar: dict[str, Any] = {}

    for key in [
        "elapsed_minutes",
        "year",
        "month_name",
        "month_number",
        "season_name",
        "season_hint",
        "day_of_month",
        "time_of_day_minutes",
    ]:
        value = raw_calendar.get(key)

        if isinstance(value, str):
            value = value.strip()

        if value not in {"", None}:
            calendar[key] = value

    return calendar


def _parse_new_game_skills(raw_skills: Any) -> list[dict[str, Any]]:
    """Parses finalized new-game skills from Gemini."""

    if not isinstance(raw_skills, list):
        return []

    skills: list[dict[str, Any]] = []

    for raw_skill in raw_skills:
        if not isinstance(raw_skill, dict):
            continue

        name = str(raw_skill.get("name", "")).strip()
        description = str(raw_skill.get("description", "")).strip()

        try:
            level = int(raw_skill.get("level", 0))
        except (TypeError, ValueError):
            level = 0

        if name and description and level > 0:
            skills.append(
                {
                    "name": name,
                    "description": description,
                    "level": level,
                }
            )

    return skills


def _parse_new_game_starter_items(raw_items: Any) -> list[dict[str, Any]]:
    """Parses finalized new-game starter inventory from Gemini."""

    if not isinstance(raw_items, list):
        return []

    items: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        name = str(raw_item.get("name", raw_item.get("item_name", ""))).strip()

        if not name or name.casefold() in seen_names:
            continue

        try:
            quantity = int(raw_item.get("quantity", raw_item.get("amount", 1)))
        except (TypeError, ValueError):
            quantity = 1

        try:
            value_base_units = int(
                raw_item.get(
                    "value_base_units",
                    raw_item.get("base_unit_value", raw_item.get("value", 0)),
                )
            )
        except (TypeError, ValueError):
            value_base_units = 0

        items.append(
            {
                "name": name,
                "category": str(raw_item.get("category", "Item")).strip() or "Item",
                "quantity": max(1, quantity),
                "description": str(raw_item.get("description", "")).strip(),
                "value_base_units": max(0, value_base_units),
            }
        )
        seen_names.add(name.casefold())

    return items


def _parse_new_game_currency_denominations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parses AI-finalized new-game currency denominations."""

    raw_denominations = data.get("currency_denominations", data.get("denominations"))

    if raw_denominations is None:
        raw_currency = data.get("currency", data.get("economy"))

        if isinstance(raw_currency, dict):
            raw_denominations = raw_currency.get(
                "denominations",
                raw_currency.get("currency_denominations"),
            )

    return normalize_currency_denominations(
        raw_denominations,
        fallback_denominations=[],
        max_denominations=4,
    )


def _parse_new_game_currency_description(data: dict[str, Any]) -> str:
    """Parses AI-finalized new-game currency description."""

    for key in ["currency_description", "currency_notes", "economy_description"]:
        value = str(data.get(key, "")).strip()

        if value:
            return value

    raw_currency = data.get("currency", data.get("economy"))

    if isinstance(raw_currency, dict):
        for key in ["description", "notes", "currency_description"]:
            value = str(raw_currency.get(key, "")).strip()

            if value:
                return value

    return ""


def _strip_json_fence(raw_text: str) -> str:
    """Removes a common Markdown JSON fence if the model includes one."""

    if not raw_text.startswith("```"):
        return raw_text

    lines = raw_text.splitlines()

    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()

    return raw_text


def _format_visible_response(response_text: str, suggested_actions: list[str]) -> str:
    """Combines response text and suggested actions for current UI display."""

    formatted_response = format_story_message(response_text)

    if not suggested_actions:
        return formatted_response

    action_lines = [f"- {action}" for action in suggested_actions]
    question = "What do you do now?"

    if formatted_response.endswith(question):
        return f"{formatted_response}\n" + "\n".join(action_lines)

    return f"{formatted_response}\n\n{question}\n" + "\n".join(action_lines)


def format_story_message(text: str) -> str:
    """Formats player-facing story prose for immersive display."""

    clean_text = str(text).strip()

    if not clean_text:
        return ""

    action_lines = [
        line.strip()
        for line in clean_text.splitlines()
        if line.strip().startswith(("-", "*"))
    ]
    prose_lines = [
        line.strip()
        for line in clean_text.splitlines()
        if line.strip() and not line.strip().startswith(("-", "*"))
    ]
    prose = " ".join(prose_lines)
    sentences = _split_story_sentences(prose)
    formatted = "\n\n".join(sentences)

    if action_lines:
        question = "What do you do now?"

        if formatted.endswith(question):
            formatted = f"{formatted}\n" + "\n".join(action_lines)
        else:
            formatted = f"{formatted}\n\n" + "\n".join(action_lines)

    return formatted.strip()


def _split_story_sentences(text: str) -> list[str]:
    """Splits story prose without breaking common time abbreviations."""

    clean_text = re.sub(r"\s+", " ", text).strip()

    if not clean_text:
        return []

    replacements = {
        "A.M.": "A<prd>M.",
        "P.M.": "P<prd>M.",
        "a.m.": "a<prd>m.",
        "p.m.": "p<prd>m.",
        "Mr.": "Mr<prd>",
        "Mrs.": "Mrs<prd>",
        "Ms.": "Ms<prd>",
        "Dr.": "Dr<prd>",
    }
    protected_text = clean_text

    for original, replacement in replacements.items():
        protected_text = protected_text.replace(original, replacement)

    raw_sentences = re.split(r"(?<=[.!?])\s+(?=[\"'A-Z0-9])", protected_text)
    sentences: list[str] = []

    for raw_sentence in raw_sentences:
        sentence = raw_sentence.strip()

        for original, replacement in replacements.items():
            sentence = sentence.replace(replacement, original)

        if sentence:
            sentences.append(sentence)

    return sentences or [clean_text]


def _read_env_file(env_path: Path) -> dict[str, str]:
    """Reads simple KEY=VALUE pairs from a .env file without mutating os.environ."""

    if not env_path.exists():
        return {}

    values: dict[str, str] = {}

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        LOGGER.exception("Failed to read .env file at %s.", env_path)
        return values

    for line in lines:
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()

        if not key:
            continue

        values[key] = value.strip().strip("\"'")

    return values
