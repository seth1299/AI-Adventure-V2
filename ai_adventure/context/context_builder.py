from __future__ import annotations

import re
from typing import Any

from ai_adventure.alchemy.ingredients import (
    COMMON_MEASUREMENT_UNITS,
    CRAFTING_INGREDIENT_CATEGORY_NAMES,
)
from ai_adventure.ai.modes import ai_mode_preferences_from_settings
from ai_adventure.audio.catalog import distinct_audio_track_catalogs_with_ambience
from ai_adventure.context.creative_ideas import CreativeIdeasLibrary
from ai_adventure.context.models import ContextLibrary
from ai_adventure.context.naming import GENERIC_PROPER_NOUN_PLACEHOLDER_RULE
from ai_adventure.context.reference_loader import ContextReferenceLoader
from ai_adventure.context.tags import PLANNABLE_CONTEXT_TAGS
from ai_adventure.combat import (
    COMBAT_FOCUS_INSTRUCTIONS,
    normalize_combat_preferences,
    normalize_combat_state,
)
from ai_adventure.currency import format_currency_amount
from ai_adventure.core.models import AdventureState
from ai_adventure.notes import note_entries_for_ai, normalize_note_entries
from ai_adventure.narration_preferences import normalize_narration_preferences


MAX_CONTEXT_TEXT_CHARS = 1200
MAX_SHORT_CONTEXT_TEXT_CHARS = 500
MAX_CONTEXT_DICT_ITEMS = 50
MAX_CONTEXT_LIST_ITEMS = 40
MAX_INVENTORY_CONTEXT_ITEMS = 50
MAX_CRAFTING_CONTEXT_ENTRIES = 40
MAX_ACTIVE_TASK_CONTEXT_ITEMS = 40


KEYWORD_TAGS: dict[str, set[str]] = {
    "alchemy": {
        "alchemy",
        "brew",
        "craft",
        "experiment",
        "mixture",
        "potion",
        "reagent",
        "reagents",
        "recipe",
        "recipes",
    },
    "inventory": {
        "drop",
        "equip",
        "get",
        "give",
        "inventory",
        "item",
        "take",
        "tool",
        "use",
    },
    "exploration": {
        "climb",
        "east",
        "enter",
        "examine",
        "explore",
        "go",
        "inspect",
        "leave",
        "look",
        "north",
        "search",
        "south",
        "travel",
        "west",
    },
    "travel": {
        "buggy",
        "carriage",
        "horse",
        "journey",
        "march",
        "ride",
        "sail",
        "ship",
        "travel",
        "wagon",
    },
    "combat": {
        "attack",
        "battle",
        "block",
        "combat",
        "duel",
        "fight",
        "parry",
        "strike",
    },
    "skill": {
        "check",
        "difficulty",
        "practice",
        "roll",
        "skill",
        "train",
        "training",
    },
    "crafting": {
        "build",
        "craft",
        "forge",
        "make",
        "project",
        "repair",
        "work",
    },
    "merchant": {
        "buy",
        "drink",
        "food",
        "merchant",
        "menu",
        "price",
        "purchase",
        "sell",
        "shop",
        "tavern",
        "trade",
    },
    "quest": {
        "commission",
        "contract",
        "objective",
        "quest",
        "reward",
        "task",
    },
    "magic": {
        "cantrip",
        "cast",
        "magic",
        "ritual",
        "spell",
    },
    "world": {
        "calendar",
        "country",
        "date",
        "day",
        "faction",
        "history",
        "lore",
        "month",
        "npc",
        "race",
        "region",
        "religion",
        "rumor",
        "season",
        "settlement",
        "species",
        "time",
        "weather",
        "world",
    },
    "music": {
        "ambience",
        "background",
        "mood",
        "music",
        "scene",
        "song",
        "soundtrack",
        "track",
    },
    "dialogue": {
        "ask",
        "name",
        "say",
        "speak",
        "talk",
        "tell",
    },
    "out_of_game": {
        "oog",
        "out-of-game",
        "rules",
    },
}


class AiContextBuilder:
    """Builds compact, structured context packets for future AI narration."""

    def __init__(
        self,
        library: ContextLibrary,
        *,
        creative_ideas: CreativeIdeasLibrary | None = None,
        max_history_entries: int = 8,
        max_reference_sections: int = 14,
    ) -> None:
        """
        Args:
            library: Validated reference context library.
            creative_ideas: Optional creative seed library.
            max_history_entries: Recent history entries to include.
            max_reference_sections: Maximum reference sections to include.
        """

        self.library = library
        self.creative_ideas = creative_ideas
        self.max_history_entries = max_history_entries
        self.max_reference_sections = max_reference_sections

    @classmethod
    def from_default_library(cls) -> "AiContextBuilder":
        """Creates a builder using the packaged default context library."""

        return cls(
            ContextReferenceLoader().load_default_library(),
            creative_ideas=CreativeIdeasLibrary.load_default(),
        )

    def build_story_context(
        self,
        state: AdventureState,
        *,
        player_command: str,
        conversation_mode: str = "live_game",
        relevant_npcs: list[dict[str, Any]] | None = None,
        party_members: list[dict[str, Any]] | None = None,
        gm_secrets: list[dict[str, Any]] | None = None,
        miscellaneous: list[dict[str, Any]] | None = None,
        valid_music_tracks: list[str] | None = None,
        current_music: str | None = None,
        valid_sound_effect_tracks: list[str] | None = None,
        valid_background_ambience_tracks: list[str] | None = None,
        current_background_ambience: str | None = None,
        resolved_skill_checks: list[dict[str, Any]] | None = None,
        planner_context_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Builds the context packet for one story turn.

        Args:
            state: Current composed adventure state.
            player_command: The player's pending command.
            conversation_mode: Explicit UI-selected mode: live_game or out_of_game.
            relevant_npcs: NPC memory profiles likely relevant this turn.
            party_members: Current party records joined to canonical NPC profiles.
            gm_secrets: Active private GM-memory records for every turn.
            miscellaneous: Every general world-lore record, always sent uncapped.
            valid_music_tracks: Playable background music filenames.
            current_music: Currently selected background music filename.
            valid_sound_effect_tracks: Playable one-shot sound-effect filenames.
            resolved_skill_checks: Skill checks already resolved for this command.
            planner_context_tags: Validated tags selected by the pre-narration
                planner. ``None`` falls back to keyword inference.

        Returns:
            JSON-serializable context packet.
        """

        clean_command = player_command.strip()
        clean_conversation_mode = (
            "out_of_game" if conversation_mode == "out_of_game" else "live_game"
        )
        selected_tags = (
            infer_context_tags(clean_command)
            if planner_context_tags is None
            else _normalize_planner_context_tags(planner_context_tags)
        )
        selected_tags.update(
            _hybrid_magic_relevance_tags(
                state,
                player_command=clean_command,
                relevant_npcs=relevant_npcs or [],
            )
        )
        selected_tags.add("story")
        if clean_conversation_mode == "out_of_game":
            selected_tags.add("out_of_game")
        (
            clean_music_tracks,
            clean_sound_effect_tracks,
            clean_background_ambience_tracks,
        ) = distinct_audio_track_catalogs_with_ambience(
            valid_music_tracks,
            valid_sound_effect_tracks,
            valid_background_ambience_tracks,
        )
        clean_current_music = str(
            current_music
            or state.settings.values.get("audio.current_music", "")
            or ""
        ).strip()
        if clean_current_music.casefold() not in {
            track.casefold() for track in clean_music_tracks
        }:
            clean_current_music = ""
        clean_current_background_ambience = str(
            current_background_ambience
            or state.settings.values.get("audio.current_background_ambience", "")
            or ""
        ).strip()
        if clean_current_background_ambience.casefold() not in {
            track.casefold() for track in clean_background_ambience_tracks
        }:
            clean_current_background_ambience = ""
        if clean_music_tracks or clean_sound_effect_tracks or clean_background_ambience_tracks:
            selected_tags.add("music")

        reference_sections = self.library.select_sections(
            selected_tags,
            max_sections=self.max_reference_sections,
        )
        disabled_audio_event_types: set[str] = set()
        if not clean_music_tracks:
            disabled_audio_event_types.add("MusicChangedEvent")
        if not clean_sound_effect_tracks:
            disabled_audio_event_types.add("SoundEffectChangedEvent")
        if not clean_background_ambience_tracks:
            disabled_audio_event_types.add("BackgroundAmbienceChangedEvent")
        reference_sections = [
            section
            for section in reference_sections
            if str(section.content.get("event_type", ""))
            not in disabled_audio_event_types
        ]
        audio_transition_rules: list[str] = []
        if clean_music_tracks:
            audio_transition_rules.append(
                "When StatusUpdatedEvent.location changes to a substantially different "
                "environment type, compare state.audio.current_music to "
                "state.audio.valid_music_tracks. If a listed track clearly better "
                "matches the new environment or mood, include MusicChangedEvent "
                "before the final StatusUpdatedEvent."
            )
        if clean_sound_effect_tracks:
            audio_transition_rules.append(
                "Use SoundEffectChangedEvent only for a brief, meaningful sound "
                "described by this response. Its filename must come from "
                "state.audio.valid_sound_effect_tracks, never valid_music_tracks. "
                "Provide anchor_text copied exactly from one unique place in the "
                "response and position before or after so Python can play the sound "
                "once at that exact narration boundary. Add as many separate cues as "
                "the response genuinely benefits from, provided an appropriate listed "
                "sound exists for every cue; do not add a cue merely to use a file. "
                "Never use it for ambience."
            )
        clean_relevant_npcs = [
            _npc_context_profile(npc)
            for npc in (relevant_npcs or [])
        ]
        clean_party_members = [
            _party_context_profile(member)
            for member in (party_members or [])
            if isinstance(member, dict)
        ]
        relevant_npc_ids = {
            str(npc.get("npc_id", "")) for npc in clean_relevant_npcs
        }
        for member in party_members or []:
            clean_member_npc = _npc_context_profile(member)
            member_npc_id = str(clean_member_npc.get("npc_id", ""))
            if member_npc_id and member_npc_id not in relevant_npc_ids:
                clean_relevant_npcs.append(clean_member_npc)
                relevant_npc_ids.add(member_npc_id)
        clean_gm_secrets = [
            _gm_secret_context_record(secret)
            for secret in (gm_secrets or [])
            if str(secret.get("status", "active")).strip().casefold() == "active"
        ]
        clean_miscellaneous = [
            _miscellaneous_context_record(entry)
            for entry in (miscellaneous or [])
            if isinstance(entry, dict)
        ]
        notes_share_with_ai = _coerce_bool(
            state.settings.values.get("notes.share_with_ai", False),
            default=False,
        )
        note_entries: list[dict[str, Any]] = []

        if notes_share_with_ai:
            note_entries = note_entries_for_ai(
                normalize_note_entries(state.settings.values.get("notes.entries", []))
            )
        if clean_background_ambience_tracks:
            audio_transition_rules.append(
                "Use BackgroundAmbienceChangedEvent to start or replace a quiet, "
                "persistent environmental loop when the scene warrants it. filename "
                "must exactly match state.audio.valid_background_ambience_tracks. "
                "Use filename STOP when the current ambience no longer fits and no "
                "replacement is appropriate. This is separate from music and one-shot "
                "sound effects."
            )
            note_entries = [
                {
                    "heading": _compact_text(entry["heading"], max_chars=300),
                    "body": _compact_text(entry["body"]),
                    "tags": entry["tags"],
                }
                for entry in note_entries[:MAX_CONTEXT_LIST_ITEMS]
            ]
        narration_preferences = normalize_narration_preferences(
            {
                "tense": state.settings.values.get("ai.narration_tense", ""),
                "style": state.settings.values.get("ai.narration_style", ""),
            }
        )
        ai_mode_preferences = ai_mode_preferences_from_settings(state.settings.values)
        combat_state = normalize_combat_state(state.settings.values.get("combat.state", {}))
        combat_preferences = normalize_combat_preferences(
            state.settings.values.get(
                "combat.preferences",
                {
                    "resolution_mode": state.settings.values.get(
                        "combat.resolution_mode", "strict"
                    ),
                    "focus": state.settings.values.get("combat.focus", "balanced"),
                },
            )
        )
        strict_combat = combat_preferences["resolution_mode"] == "strict"
        magic_context = _magic_context_packet(
            state.magic,
            include_progression="magic" in selected_tags or "spell" in selected_tags,
        )

        packet = {
            "schema_version": 1,
            "packet_type": "story_turn",
            "player_command": clean_command,
            "conversation_mode": clean_conversation_mode,
            "selection": {
                "tags": sorted(selected_tags),
                "max_history_entries": self.max_history_entries,
                "max_reference_sections": self.max_reference_sections,
            },
            "state": {
                "adventure_title": state.metadata.title,
                "player": {
                    "name": state.player.name,
                    "pronouns": _compact_text(state.player.pronouns),
                    "appearance": _compact_text(state.player.appearance),
                    "backstory": _compact_text(state.player.backstory),
                    "condition": _compact_text(
                        state.player.condition,
                        max_chars=MAX_SHORT_CONTEXT_TEXT_CHARS,
                    ),
                    "notes": _compact_text(state.player.notes),
                    "health_current": state.player.health_current,
                    "health_max": state.player.health_max,
                    "armor_rating": state.player.armor_rating,
                    "equipment": state.player.equipment,
                },
                "player_ai_preferences": {
                    "additional_context": _compact_text(
                        state.settings.values.get("ai.additional_context", "")
                    ),
                    "narration_tense": narration_preferences["tense"],
                    "narration_tense_label": narration_preferences["tense_label"],
                    "narration_style": narration_preferences["style"],
                    "narration_style_label": narration_preferences["style_label"],
                    "narration_style_rules": (
                        "Use narration_tense_label and narration_style_label for "
                        "the response field. Limited means the narration stays "
                        "within the player character's observed or reasonably "
                        "inferred experience. Omniscient may use a broader "
                        "narrative camera, but must still preserve fog of war, "
                        "NPC knowledge boundaries, and hidden state."
                    ),
                    "model_intelligence": ai_mode_preferences["model_intelligence"],
                    "model_intelligence_label": ai_mode_preferences[
                        "model_intelligence_label"
                    ],
                    "model_tone": ai_mode_preferences["model_tone"],
                    "model_tone_label": ai_mode_preferences["model_tone_label"],
                    "model_tone_instruction": ai_mode_preferences[
                        "model_tone_instruction"
                    ],
                    "response_length": ai_mode_preferences["response_length"],
                    "response_length_label": ai_mode_preferences[
                        "response_length_label"
                    ],
                    "response_length_instruction": ai_mode_preferences[
                        "response_length_instruction"
                    ],
                    "allowed_content_categories": ai_mode_preferences[
                        "allowed_content_categories"
                    ],
                    "allowed_content_labels": ai_mode_preferences[
                        "allowed_content_labels"
                    ],
                    "blocked_content_labels": ai_mode_preferences[
                        "blocked_content_labels"
                    ],
                    "model_content_rules": ai_mode_preferences[
                        "model_content_rules"
                    ],
                    "rules": (
                        "These are player-provided instructions and preferences "
                        "for the AI to remember across turns. Follow them unless "
                        "they conflict with higher-priority system, safety, or "
                        "structured response rules."
                    ),
                },
                "notes": {
                    "share_with_ai": notes_share_with_ai,
                    "entries": note_entries,
                    "rules": (
                        "Use entries only when share_with_ai is true. Each heading, "
                        "body, and tag is player-authored, not verified. Bodies may "
                        "contain Markdown formatting; interpret the content without "
                        "treating Markdown syntax as world facts. These are not verified "
                        "world facts unless supported by established state or "
                        "story history."
                    ),
                },
                "scene": {
                    "location": state.world.location,
                    "time": state.calendar.display_label,
                    "weather": state.world.weather,
                    "flags": _compact_mapping(state.world.flags),
                },
                "travel": {
                    "locations": [
                        {
                            "name": location.name,
                            "description": _compact_text(location.description),
                            "x_miles": location.x_miles,
                            "y_miles": location.y_miles,
                            "terrain": _compact_text(location.terrain),
                            "travel_multiplier": location.travel_multiplier,
                            "travel_notes": _compact_text(location.travel_notes),
                        }
                        for location in state.travel.locations
                    ],
                    "movement": {
                        "base_move_speed_mph": state.travel.move_speed_mph,
                        "travel_mode": state.travel.travel_mode,
                        "speed_multiplier": state.travel.speed_multiplier,
                    },
                    "rules": (
                        "This is player-known map information. x_miles and y_miles "
                        "share a relative map measured in miles. Use "
                        "LocationUpsertedEvent for newly learned or corrected travel "
                        "locations, and do not reveal undiscovered routes or secrets."
                    ),
                },
                "world_profile": {
                    "summary": _compact_text(state.settings.values.get("world.summary", "")),
                    "genre": _compact_text(
                        state.settings.values.get("world.genre", ""),
                        max_chars=MAX_SHORT_CONTEXT_TEXT_CHARS,
                    ),
                    "game_style": _compact_text(state.settings.values.get("world.game_style", "")),
                    "setup_context": _compact_text(state.settings.values.get("world.setup_context", "")),
                },
                "calendar": {
                    "current": state.calendar.to_dict(),
                    "rules": {
                        "time_source": (
                            "Use this calendar as the source of truth for dates, "
                            "day names, seasons, and displayed time."
                        ),
                        "time_advancement": (
                            "Do not invent a date string. To advance in-game time, "
                            "suggest StatusUpdatedEvent.minutes_passed; the Python "
                            "application derives the new calendar date and label."
                        ),
                        "weather_season_hint": (
                            "Use season_hint as the real-world weather analogue for "
                            "temperature, precipitation, plants, and daylight tone."
                        ),
                    },
                },
                "inventory": {
                    "items": [
                        {
                            "database_id": item.id,
                            "name": item.name,
                            "category": item.category,
                            "quantity": item.quantity,
                            "equipped": item.equipped,
                            "description": _compact_text(item.description),
                            "value_base_units": item.value_base_units,
                            "metadata": _compact_context_value(item.metadata),
                        }
                        for item in state.inventory.items[:MAX_INVENTORY_CONTEXT_ITEMS]
                    ],
                    "container_rule": (
                        "Container metadata is authoritative hidden state. Classify "
                        "an item as a Container only when its primary function is "
                        "holding physical contents that can be put in and taken out. "
                        "Items that store writing, records, instructions, or "
                        "information are not Containers merely because they store "
                        "information. Never "
                        "reveal or award a closed container's contents. Use "
                        "When the player attempts to access an unlocked container, or "
                        "has an unambiguous key or equivalent access item for a locked "
                        "container, infer the routine opening and use "
                        "ContainerOpenedEvent; no lockpick check is needed. "
                        "Otherwise use ContainerOpenedEvent only after required "
                        "lock/trap checks succeed, then ContainerContentsTakenEvent only when the "
                        "player explicitly takes the contents. Python transfers "
                        "the exact stored currency/items once."
                    ),
                    "category_rule": (
                        "Classify inventory by the finished item's present primary "
                        "function, not its origin or packaging. A ready-to-use poison "
                        "or toxin is Poison even when stored in a vial; reserve "
                        "Ingredient, Reagent, Material, and Crafting Item for recipe "
                        "inputs that still need processing."
                    ),
                },
                "item_catalog": {
                    "items": [
                        {
                            "database_id": item.id,
                            "name": item.name,
                            "category": item.category,
                            "description": _compact_text(item.description),
                            "value_base_units": item.value_base_units,
                            "metadata": _compact_context_value(item.metadata),
                        }
                        for item in state.item_catalog.items
                    ],
                    "rules": {
                        "purpose": (
                            "This is the durable master list of known item "
                            "definitions. It may include items the player no "
                            "longer owns. Prefer reusing a fitting existing "
                            "definition over inventing a near-duplicate."
                        ),
                        "possession_rule": (
                        "Only state.inventory.items are current possessions. Each "
                        "inventory item includes quantity, quantity_unit, and "
                        "storage_location, a free-text storage label independent of "
                        "Travel-tab locations; use actively_carried only when the "
                        "Player Character is carrying it. "
                            "Use item_catalog to remember descriptions, categories, "
                            "values for previously seen items. Each item also "
                            "has database_id, a globally unique database identity, and "
                            "metadata.item_uuid, a stable item identity; "
                            "reuse it for the same item and do not split one item "
                            "into duplicate definitions because of name variations."
                        ),
                    },
                },
                "currency": {
                    "balance": state.currency.balance_base_units,
                    "balance_base_units": state.currency.balance_base_units,
                    "game_state_key": "currency.balance",
                    "game_state_path": "game_state/currency.balance",
                    "display_balance": format_currency_amount(
                        state.currency.balance_base_units,
                        state.currency.denominations,
                    ),
                    "denominations": state.currency.denominations,
                    "world_description": _compact_text(
                        state.settings.values.get("currency.description", "")
                    ),
                    "baseline_unit": (
                        state.currency.denominations[0]["name"]
                        if state.currency.denominations
                        else "base currency unit"
                    ),
                    "item_value_rule": (
                        "Inventory item value_base_units is an integer measured "
                        "in the baseline currency unit."
                    ),
                    "transaction_rule": (
                        "The player's current money is state.currency.balance, "
                        "also shown as state.currency.balance_base_units, loaded "
                        "from game_state/currency.balance. Currency is not an "
                        "inventory item. For purchases, sales, rewards, fees, "
                        "refunds, change, or other money movement, suggest "
                        "CurrencyChangedEvent with payload.base_unit_amount as "
                        "the one net money change. Never use net_base_unit_amount. "
                        "Currency listed in container metadata is hidden, unowned "
                        "container contents until the container is open and the "
                        "player explicitly takes it. Use ContainerContentsTakenEvent "
                        "instead of CurrencyChangedEvent for that transfer. "
                        "In player-facing prose, use denomination names and natural "
                        "breakdowns such as 3 Silver Coins and 5 Copper Coins; do "
                        "not describe money as base units or as copper coins' worth "
                        "of a different metal. For example, buying an item whose "
                        "internal price is 35 while paying with a larger coin is "
                        "still base_unit_amount -35; the application displays the "
                        "remaining balance as the appropriate denominations."
                    ),
                },
                "combat": {
                    "resolution_mode": combat_preferences["resolution_mode"],
                    "focus": combat_preferences["focus"],
                    "focus_instruction": COMBAT_FOCUS_INSTRUCTIONS[
                        combat_preferences["focus"]
                    ],
                    "active": bool(combat_state.get("active", False)),
                    "round": combat_state.get("round", 1),
                    "turn_index": combat_state.get("turn_index", 0),
                    "combatants": [
                        _compact_context_value(combatant)
                        for combatant in combat_state.get("combatants", [])
                    ],
                    "rules": (
                        (
                            "When a fight starts, suggest CombatStartedEvent with "
                            "enemy/allied combatants, health, armor_rating, "
                            "to_hit_bonus, initiative_bonus, personality, ammunition/clip "
                            "fields, damage dice, and loot. The Python combat system "
                            "rolls initiative, calculates team Threat Levels, and resolves "
                            "attacks, damage, victory, defeat, and loot. Do not resolve "
                            "those mechanics in story prose after combat starts."
                        )
                        if strict_combat
                        else (
                            "Resolve and describe combat narratively in story prose. "
                            "Do not suggest CombatStartedEvent and do not hand the fight "
                            "to the Combat tab. Respect the player's declared actions, "
                            "use warranted skill checks before uncertain outcomes, give "
                            "all participants meaningful agency, and persist any actual "
                            "injuries, items, currency, status, or other durable changes "
                            "with the supported non-combat events."
                        )
                    ),
                },
                "alchemy": {
                    "known_reagents": [
                        _compact_context_value(reagent.to_dict())
                        for reagent in state.alchemy.known_reagents[
                            :MAX_CRAFTING_CONTEXT_ENTRIES
                        ]
                    ],
                    "known_recipes": [
                        _compact_context_value(recipe.to_dict())
                        for recipe in state.alchemy.known_recipes[
                            :MAX_CRAFTING_CONTEXT_ENTRIES
                        ]
                    ],
                    "rules": {
                        "reagent_fields": (
                              "Crafting items/materials use name, category, description, "
                              "location, uses, rarity, notes, and value_base_units as "
                              "player-known structured fields. location lists generalized "
                              "environments or source areas such as Forests or Caves, not "
                              "a specific Travel-tab place. notes ends with exactly one "
                              "rarity sentence, "
                              "and Rare or Very Rare items should be priced materially above "
                              "comparable Common items unless world context says otherwise. "
                            "The uses list describes generalized symptoms or effects, "
                            "such as sleep aid or pain relief, not detailed recipes "
                            "or procedures. Use Container for vials, bottles, jars, "
                            "and similar vessels."
                        ),
                        "recipe_ingredient_rule": (
                            "Recipe ingredients are structured entries that must "
                            "use item names from state.item_catalog.items whose "
                            "category is one of "
                            f"{CRAFTING_INGREDIENT_CATEGORY_NAMES}, plus quantity, "
                            "measure_amount, and a finite measure_unit. Never use "
                            "a measure_unit that conflicts with the matching inventory "
                            "item's quantity_unit. quantity times measure_amount is the "
                            "total consumed per crafted result. Match ingredients by "
                            "metadata.item_uuid, using the name only for display. Never use "
                            "vague units such as pinch or handful. Recipes also carry "
                            "value_base_units as the current or estimated result value. "
                            "Recipe notes must be self-contained: state the intended "
                            "purpose/effect, expected strength or outcome, onset, "
                            "duration, and important use conditions; say unknown or "
                            "not applicable when a detail is not established. "
                            "state.alchemy.known_reagents stores crafting knowledge, "
                            "but item_catalog categories decide whether an item can "
                            "be chosen as a recipe ingredient."
                        ),
                        "common_measurement_units": list(COMMON_MEASUREMENT_UNITS),
                    },
                },
                "skills": {
                    "rules": {
                        "check_formula": "d20 + bonus vs dc",
                        "bonus_formula": "level * 2",
                        "levels": "1-5",
                        "maximum_level_rule": (
                            "Level 5 is the absolute maximum. Never create a skill "
                            "requirement, progression target, or secret reveal condition "
                            "that requires a skill level above 5."
                        ),
                        "unknown_skill_rule": (
                            "Choose the most directly relevant known skill, not merely "
                            "a plausible broad skill. Locating or gathering wild plants, "
                            "herbs, or reagents uses known Foraging rather than "
                            "Investigation or Perception. skill_name may identify a "
                            "generalized capability absent from known_skills only when "
                            "no known skill fits. When it does, include skill_description "
                            "so Python can create the new skill at level 1 before rolling."
                        ),
                        "uncertain_action_rule": (
                            "Suggest SkillCheckRequestedEvent before narrating a "
                            "final outcome only when the current command has "
                            "meaningful uncertainty, opposition, hidden information, "
                            "danger, scarcity, time pressure, or consequences. Do "
                            "not request checks merely because an action could "
                            "theoretically vary in quality or take extra time. "
                            "Foraging, harvesting, searching, researching, identifying, "
                            "crafting experiments, persuasion, stealth, and "
                            "combat need checks only when those real stakes are present. "
                            "Routine movement, paying a known price, receiving "
                            "ordinary goods, eating, drinking, and casual conversation "
                            "are not checks unless the player adds a contested, risky, "
                            "hidden, time-sensitive, or deceptive goal. "
                            "The Python application resolves the roll."
                        ),
                        "resolved_check_rule": (
                            "When resolved_checks_this_turn is non-empty, narrate "
                            "this player command from those authoritative results. "
                            "Do not request duplicate checks for those skills. Low "
                            "failed rolls should create real setbacks or costs; very "
                            "high successful rolls should produce a cleaner, richer, "
                            "faster, or more advantageous result. Never mention dice "
                            "or roll numbers in player-facing narration."
                        ),
                        "xp_rule": (
                            "Suggest SkillXpAddedEvent only after meaningful use, "
                            "training, study, or practice; do not use XP as a "
                            "substitute for a check. Always include xp_amount; use "
                            "1 for a tiny meaningful gain if no stronger amount is obvious."
                        ),
                    },
                    "known_skills": [
                        skill.to_dict() for skill in state.skills.skills
                    ],
                    "recent_checks": [
                        check.to_dict() for check in state.skills.recent_checks
                    ],
                    "resolved_checks_this_turn": [
                        dict(check)
                        for check in (resolved_skill_checks or [])
                        if isinstance(check, dict)
                    ],
                },
                "magic": {
                    **magic_context,
                    "rules": {
                        "authority": (
                            "If configuration.world_contains_magic is false, the world "
                            "contains no magic: never introduce spells, magical powers, "
                            "enchanted items, magical creatures, or supernatural effects. "
                            "If world_contains_magic is true but enabled is false, "
                            "magic still exists in the world according to casting_mode "
                            "and tradition, but the Player Character cannot cast at the "
                            "start. Otherwise, the player chooses whether to cast. Python "
                            "validates known spells and deterministically consumes mana "
                            "or tiered slots. Never invent a player cast or calculate "
                            "remaining resources."
                        ),
                        "narrative": "Narrative casting consumes no tracked resource.",
                        "mana": "Mana casting consumes the authoritative mana_cost from the spell catalog.",
                        "tiered": (
                            "Tiered casting consumes one slot at the selected tier. Tier 0 "
                            "spells consume no slot."
                        ),
                        **(
                            {
                                "advancement": (
                                    "progression is authoritative durable evidence. Use "
                                    "MagicAdvancementRecordedEvent only for meaningful magical "
                                    "development established by this response, never routine "
                                    "repetition. The event records evidence only and does not "
                                    "change Mana, slots, tiers, or known spells."
                                )
                            }
                            if "progression" in magic_context
                            else {}
                        ),
                    },
                },
                "active_tasks": {
                    "rules": {
                        "purpose": (
                            "Active tasks are durable player-visible obligations, "
                            "quests, commissions, custom orders, pending purchases, "
                            "and promises that the AI should remember across turns."
                        ),
                        "upsert_rule": (
                            "Suggest ActiveTaskUpsertedEvent when a new task appears "
                            "or an existing task changes status, description, reward, "
                            "requester, location, due date, or exact due elapsed minute."
                        ),
                        "field_completion_rule": (
                            "For a new task, do not leave visible fields blank. An "
                            "update may omit fields that did not change. Use Self for "
                            "personal goals, N/A for no reward or no deadline, and "
                            "a logical player-known location for where the task is "
                            "done, picked up, completed, or turned in. Use Unknown "
                            "only when a real value exists but is unclear. Do not add "
                            "Notes or other extra active-task fields. For real "
                            "deadlines, include an exact due_elapsed_minutes value; "
                            "the app will display it with the current calendar and "
                            "time settings."
                        ),
                        "description_rule": (
                            "Every new task requires a complete player-visible "
                            "description explaining what must be done, all currently "
                            "known relevant people and places, and how the Player can "
                            "recognize completion. Include only player-known facts. "
                            "Preserve an existing description when an update does not "
                            "change it. When an existing task has a blank or incomplete "
                            "description, repair it with ActiveTaskUpsertedEvent as "
                            "soon as the task is relevant."
                        ),
                        "completion_rule": (
                            "Suggest ActiveTaskCompletedEvent when a task is fulfilled, "
                            "cancelled, resolved, delivered, or otherwise no longer active."
                        ),
                    },
                    "tasks": [
                        _active_task_context(task)
                        for task in state.active_tasks.tasks[:MAX_ACTIVE_TASK_CONTEXT_ITEMS]
                    ],
                },
                "calendar_events": {
                    "rules": {
                        "purpose": (
                            "Persistent player-visible dates, festivals, deadlines, "
                            "appointments, completions, and cultural observances."
                        ),
                        "upsert_rule": (
                            "Use CalendarEventUpsertedEvent to create or update a "
                            "stable event_id. Use recurrence yearly for annual events "
                            "and none for a specific year. duration_days may span "
                            "multiple consecutive days. Set time_of_day_minutes to an "
                            "exact local minute after midnight for a timed event, or -1 "
                            "for an all-day event or unknown time. Emit the event in the "
                            "same turn whenever narration establishes or reveals a "
                            "meaningful future date or exact time."
                        ),
                        "delete_rule": (
                            "Use CalendarEventDeletedEvent only when a stored event "
                            "is cancelled or should no longer exist."
                        ),
                    },
                    "events": [
                        _compact_context_value(
                            {
                                key: value
                                for key, value in event.items()
                                if key != "origin"
                            }
                        )
                        for event in [
                            candidate
                            for candidate in state.settings.values.get("calendar.events", [])
                            if isinstance(candidate, dict)
                            and str(candidate.get("origin", "game")).casefold() != "player"
                        ][:40]
                    ],
                },
                "audio": {
                    "current_music": clean_current_music,
                    "valid_music_tracks": clean_music_tracks,
                    "valid_sound_effect_tracks": clean_sound_effect_tracks,
                    "current_background_ambience": clean_current_background_ambience,
                    "valid_background_ambience_tracks": clean_background_ambience_tracks,
                    "rules": {
                        "music_change_rule": (
                            "When scene mood, location, danger level, or environment "
                            "changes enough that the current track no longer fits, "
                            "suggest MusicChangedEvent."
                        ),
                        "filename_rule": (
                            "MusicChangedEvent.filename must exactly match one entry "
                            "from valid_music_tracks. If valid_music_tracks is empty, "
                            "do not suggest MusicChangedEvent."
                        ),
                        "sound_effect_rule": (
                            "SoundEffectChangedEvent is a short one-shot narration cue, "
                            "never looping ambience. filename must exactly match "
                            "valid_sound_effect_tracks and must never come from "
                            "valid_music_tracks. anchor_text must be copied exactly from "
                            "one unique place in response; position must be before or "
                            "after. Use as many separately anchored events as are "
                            "meaningfully appropriate for the response, with no fixed "
                            "cue-count target; omit cues when no listed sound fits. The "
                            "app replays each saved cue at that same boundary."
                        ),
                        "background_ambience_rule": (
                            "BackgroundAmbienceChangedEvent controls a quiet persistent "
                            "environmental loop independent of music. filename must "
                            "exactly match valid_background_ambience_tracks, or be STOP "
                            "when ambience should end without replacement."
                        ),
                        "english_text_rule": (
                            "Every generated string value must use printable ASCII "
                            "English characters only. Transliterate accented Latin "
                            "letters to unaccented English and never emit foreign "
                            "scripts, IPA, phoneme strings, pronunciation annotations, "
                            "or pronunciation_map. Python enforces this before any "
                            "generated text reaches state, UI, persistence, or TTS."
                        ),
                        "speaker_voice_rule": (
                            "Return speaker_cues for every exact contiguous span of "
                            "non-narrator dialogue in response. Copy the complete span, "
                            "including outer double quotation marks, into a unique "
                            "anchor_text. Use an actual NPC's exact npc_id as speaker_id "
                            "and reuse it on later turns; use distinct stable "
                            "lower_snake_case IDs for other speakers. Choose only a "
                            "broad established voice_profile and use neutral when "
                            "unspecified. speaker_name is the visible chat-bubble label: "
                            "use the known name or a concise player-safe description "
                            "when the name is unknown. Python splits the response into "
                            "same-turn bubbles and durably remembers the installed "
                            "voice ID. Do not cue narrator prose or the Player Character."
                        ),
                    },
                },
                "npcs": {
                    "rules": {
                        "dialogue_knowledge_boundary": (
                            "The narrator can see this full context packet, but NPCs "
                            "cannot. NPC dialogue may use only observable facts, public "
                            "knowledge, facts the player told that NPC, facts in that "
                            "NPC's known_facts, or topics in that NPC's knowledge_scope."
                        ),
                        "private_state": (
                            "Inventory contents, exact currency, flags, hidden history, "
                            "quests, and player intent are private unless the NPC had a "
                            "clear in-world way to learn them."
                        ),
                        "new_npc_rule": (
                            "When introducing a meaningful new NPC, suggest "
                            "NpcUpsertedEvent with internal name, player-visible "
                            "display_name, internal role, location, public "
                            "description, player-facing information, knowledge_scope, "
                            "and known_facts. public description should include identifying "
                            "information about the NPC that the Player would know, such as "
                            "the NPC's gender or age. role is for AI memory and should not "
                            "replace player_facing_information. location should be "
                            "a meaningful player-known place, usually the current "
                            "scene location, and should not be blank. Do not add "
                            "unsupported NPC fields such as disposition."
                        ),
                        "multiple_npc_rule": (
                            "If one turn introduces multiple distinct meaningful NPCs, "
                            "suggest one NpcUpsertedEvent for each NPC. Do not collapse "
                            "separate visible NPCs into one event."
                        ),
                        "display_name_rule": (
                            "display_name is shown as the NPC's name in the NPCs tab. "
                            "Use a generic label such as Shady Character, Bartender, "
                            "Masked Duelist, or Unknown Traveler until the player "
                            "learns the NPC's actual name or role."
                        ),
                        "player_facing_information_rule": (
                            "player_facing_information is displayed directly to the "
                            "player in the NPCs tab under Notes. It must contain only "
                            "information the player has observed, heard, learned, or "
                            "reasonably deduced. Write it as information about a "
                            "person, not as a mechanical service role. Never put "
                            "secrets, hidden motives, mystery solutions, private NPC "
                            "plans, or GM-only facts there."
                        ),
                    },
                    "relevant": clean_relevant_npcs,
                },
                "party": {
                    "members": clean_party_members,
                    "rules": (
                        "Every party member is also a canonical NPC. party.members.npc_id "
                        "is the same stable identity used in state.npcs.relevant and the "
                        "NPCs tab. Never create a duplicate NPC when party membership or "
                        "party stats change. Use NpcUpsertedEvent with that same npc_id, "
                        "party_member=true, and the changed party fields. Use "
                        "party_member=false to remove someone from the party without "
                        "deleting their NPC profile. Keep health, armor class, status, "
                        "combat style, and skills consistent with narrated outcomes."
                    ),
                },
                "gm_secrets": {
                    "visibility": "AI-only; never display this section to the player.",
                    "rules": {
                        "continuity": (
                            "Treat active records as authoritative hidden truth for "
                            "mystery logic, clues, NPC behavior, and off-screen plans."
                        ),
                        "knowledge_boundary": (
                            "A GM secret must be unknown to both the player and the "
                            "Player Character. Their own conscious actions, firsthand "
                            "observations, memories, known possessions, and deliberately "
                            "hidden or stored items are not secrets unless established "
                            "state explicitly supplies a credible knowledge barrier."
                        ),
                        "non_disclosure": (
                            "Do not quote, summarize, or reveal an active secret in "
                            "response, suggested_actions, or player-visible event "
                            "fields until the player discovers it in the fiction."
                        ),
                        "updates": (
                            "Use SecretUpsertedEvent with the same secret_id and a "
                            "full current record when hidden truth changes. Mark it "
                            "revealed when the player learns it or retired when it is "
                            "no longer true or useful."
                        ),
                    },
                    "active": clean_gm_secrets,
                },
                "miscellaneous": {
                    "visibility": (
                        "Established non-secret world canon. This entire section is "
                        "included on every turn without relevance filtering or a cap."
                    ),
                    "rules": {
                        "continuity": (
                            "Treat every entry as authoritative established canon, even "
                            "when it is not obviously related to the current command."
                        ),
                        "updates": (
                            "Use MiscellaneousUpsertedEvent with the same misc_id and a "
                            "complete current record when general canon is created or "
                            "changes."
                        ),
                        "scope": (
                            "Use this only for durable concepts without a more specific "
                            "home, such as original creatures or species, cultures, "
                            "factions, religions, laws, historical events, phenomena, "
                            "or customs. Do not duplicate NPCs, locations, items, tasks, "
                            "or hidden GM secrets. Use category Creature for every "
                            "non-NPC creature or monster the Player learns about; its "
                            "details must contain only facts known to the Player or "
                            "Player Character because Creature records populate the "
                            "player-visible Bestiary."
                        ),
                    },
                    "entries": clean_miscellaneous,
                },
            },
            "rulebooks": {},
            "creative_ideas": self._build_creative_ideas_context(selected_tags),
            "recent_history": [
                _history_entry_context(entry)
                for entry in state.history.entries[-self.max_history_entries :]
            ],
            "reference_sections": [
                section.to_dict() for section in reference_sections
            ],
            "response_contract": {
                "response": (
                    "Required string. Player-facing narration only. Do not include "
                    "'What do you do now?' or any tense/person-specific "
                    "end-of-turn prompt; the Python application displays that "
                    "separately based on narration tense and style. Resolve the player's "
                    "submitted action instead of ending by restating the action, "
                    "intent, or search target. When the player asks an NPC to answer, "
                    "explain, reply, or tell their story, include the information that "
                    "NPC can presently provide instead of stopping before the reply. "
                    "Do not invent player-character "
                    "dialogue or decisions. Light Markdown is allowed for readable "
                    "player-facing prose: italics for inner thoughts, sensory "
                    "impressions, emphasis, or self-reflection; bold for important "
                    "NPCs, locations, factions, quests, or items; and headings or "
                    "bullet lists for longer summaries. Do not use Markdown tables, "
                    "code fences, HTML, or hidden text."
                ),
                "suggested_actions": (
                    "Array of 3-4 suggested player actions for in-game turns. "
                    "Use an empty array for fully out-of-game answers."
                ),
                "events": (
                    "Array of structured event suggestions. Empty when no state "
                    "change is proposed. The Python application validates and "
                    "applies events. Include multiple entries of the same event type "
                    "when multiple distinct state changes happen in one turn."
                ),
                "english_text": (
                    "Every string in the response object must use printable ASCII "
                    "English characters only. Use unaccented English transliterations "
                    "and never return pronunciation_map, IPA, phoneme strings, foreign "
                    "scripts, or inline pronunciation markup."
                ),
                "speaker_cues": (
                    "Array covering every contiguous non-narrator spoken span in "
                    "response for visible speaker bubbles and multi-voice TTS. Each "
                    "record must contain anchor_text copied exactly with outer double "
                    "quotes, speaker_id, speaker_name, and voice_profile. speaker_name "
                    "is the visible bubble label: use the known name or a concise "
                    "player-safe description when the name is unknown. Use the exact "
                    "canonical npc_id for an NPC, reuse one ID for the same speaker, "
                    "and use different IDs for different speakers. Return [] when only "
                    "the narrator speaks or for out_of_game. Python owns bubble "
                    "splitting, final installed voice assignment, and persistence."
                ),
                "conversation_mode": (
                    "conversation_mode is selected explicitly by the player in the UI "
                    "and is authoritative. For out_of_game, answer the player's question "
                    "or request directly, return out_of_game=true, suggested_actions=[], "
                    "and events=[]; do not advance time, turns, status, combat, skills, "
                    "inventory, tasks, NPC memory, secrets, miscellaneous canon, "
                    "music, or any durable state. "
                    "For live_game, return out_of_game=false and resolve the message as "
                    "an in-world action. Never infer or override the mode from wording."
                ),
                "status_event": (
                    "For every in-game response, include exactly one final "
                    "StatusUpdatedEvent. Its payload must always include all three "
                    "required fields: location, minutes_passed, and weather. Use "
                    "location='AUTO' when the player remains in the current "
                    "location, weather='AUTO' only when the narration preserves the "
                    "current weather, and minutes_passed "
                    "='AUTO' or 0 when appropriate. Never send a partial status "
                    "payload containing only minutes_passed. If narration introduces "
                    "rain, snow, fog, or any other different current weather, set "
                    "weather to that actual condition instead of AUTO or the old value."
                ),
                "skill_checks": (
                    "Suggest SkillCheckRequestedEvent with skill_name and either dc "
                    "or difficulty only for actions with meaningful uncertainty, "
                    "opposition, hidden information, danger, resource pressure, time "
                    "pressure, or consequences in the current scene. "
                    "Choose the most directly relevant known skill. Locating or "
                    "gathering wild plants, herbs, or reagents uses known Foraging "
                    "rather than Investigation or Perception; create a new skill "
                    "only when no known skill fits. "
                    "When state.skills.resolved_checks_this_turn is non-empty, those "
                    "checks are already resolved for the current player command; "
                    "narrate the outcome from those results and do not request "
                    "duplicate checks for those skills. "
                    "Do not narrate final success, failure, discoveries, harvested "
                    "items, crafted products, persuaded NPC outcomes, stealth results, "
                    "or combat results until the application has resolved the check. "
                    "Do not request checks for routine movement, ordinary purchases, "
                    "meals, drinking, or casual conversation unless the player adds "
                    "a contested, risky, hidden, time-sensitive, or deceptive goal."
                ),
                "calendar_time": (
                    "Use state.calendar.current for date, day names, seasons, and "
                    "displayed time. Advance time only by suggesting "
                    "StatusUpdatedEvent.minutes_passed; do not hand-write or guess "
                    "new date labels."
                ),
                "character_profile": (
                    "Use state.player.name, pronouns, appearance, backstory, and notes as "
                    "player-authored character context. Treat it as true for the "
                    "player character. state.player.pronouns is the canonical source "
                    "for referring to the player character: use it exactly and never "
                    "infer different pronouns from the name, appearance, voice, "
                    "backstory, or genre. Do not let NPCs know private profile "
                    "details unless they have observed them, been told, or have a "
                    "clear in-world reason to know."
                ),
                "character_scope": (
                    "The player character's class, profession, backstory, inventory, "
                    "and skills are facts about the player character, not proof that "
                    "the whole world shares that theme. Use them for personal "
                    "opportunities, plausible contacts, skill checks, and inventory, "
                    "but do not make every new location, religion, faction, political "
                    "conflict, NPC, mystery, or economy detail revolve around the "
                    "player's specialty unless the player explicitly requested that "
                    "focused premise."
                ),
                "player_ai_preferences": (
                    "Use state.player_ai_preferences.narration_tense_label and "
                    "state.player_ai_preferences.narration_style_label for the "
                    "response field. Apply model_tone_instruction, "
                    "response_length_instruction, and model_content_rules to "
                    "player-facing prose. Also use "
                    "state.player_ai_preferences.additional_context as persistent "
                    "player-provided guidance for boundaries and miscellaneous "
                    "preferences. This is always AI-facing; Notes are only AI-facing "
                    "when state.notes.share_with_ai is true."
                ),
                "notes": (
                    "When state.notes.share_with_ai is true, use state.notes.entries "
                    "headings, bodies, and tags as player-authored notes, "
                    "theories, reminders, and priorities. Treat them as the player's "
                    "perspective, not automatically true world facts. Interpret Markdown "
                    "formatting in note bodies as presentation syntax. Entry headings may "
                    "contain player-edited in-game date and time labels. When "
                    "share_with_ai is false, "
                    "ignore Notes because they are private."
                ),
                "mature_content": ai_mode_preferences["model_content_rules"],
                "active_tasks": (
                    "Use state.active_tasks.tasks to remember current quests, "
                    "commissions, custom orders, pending purchases, and other "
                    "ongoing obligations. Suggest ActiveTaskUpsertedEvent for new "
                    "or changed tasks and ActiveTaskCompletedEvent when one is no "
                    "longer active. Every new task needs a complete player-visible "
                    "description covering the objective, currently known relevant "
                    "people and places, and how to recognize completion. Use "
                    "due_elapsed_minutes for exact deadlines "
                    "instead of vague due-date prose."
                ),
                "item_catalog": (
                    "Use state.item_catalog.items as the master list of remembered "
                    "item definitions. Before inventing an item, reuse a fitting "
                    "existing catalog definition whenever one can serve the story. "
                "It preserves descriptions, categories, and values, "
                    "and metadata.item_uuid stable internal identities; reuse the same "
                    "item_uuid for the same item even when its display name changes. "
                    "It also preserves equipment metadata after items leave inventory. "
                    "Use Weapon metadata for weapon_hands, damage dice, attack range, "
                    "and optional ammunition_type_required, clip_size, and "
                    "bullets_per_attack. Ammunition items use matching "
                    "ammunition_type metadata. Use Armor "
                    "metadata for covers_body_parts and armor_rating. Container "
                    "metadata preserves exact hidden contents, open/taken state, "
                    "locks, traps, check skills/DCs, and failure consequences. "
                    "Do not treat "
                    "catalog entries as possessions unless they also appear in "
                    "state.inventory.items. Recipe ingredients may only use "
                    "catalog items whose category is one of "
                    f"{CRAFTING_INGREDIENT_CATEGORY_NAMES}."
                ),
                "background_music": (
                    " ".join(audio_transition_rules)
                ),
                "creative_ideas": (
                    "Treat creative_ideas as the preferred source of style seeds "
                    "when the current turn calls for names, locations, cultures, "
                    "food, drinks, magic, crafting ingredients, species, or similar "
                    "invented details. Prefer the provided examples or close "
                    "stylistic relatives over generic training-data fantasy "
                    "defaults. The banned_terms list is a hard exclusion list, "
                    "not optional style guidance: never use creative_ideas.banned_terms "
                    "or obvious spelling, hyphenation, or reskin variants for newly "
                    "invented proper nouns. Before returning JSON, scan every string "
                    "key and value and replace any newly invented banned term with "
                    "a fresh non-banned name. These examples are not established "
                    "canon and must not override player-provided or saved world facts. "
                    f"{GENERIC_PROPER_NOUN_PLACEHOLDER_RULE}"
                ),
                "npc_memory": (
                    "Use NpcUpsertedEvent when a new meaningful NPC appears or an "
                    "existing NPC profile needs correction. Use NpcKnowledgeAddedEvent "
                    "only for facts the NPC plausibly learned this turn. In "
                    "NpcUpsertedEvent, display_name and player_facing_information are "
                    "player-visible and must not include secrets or undiscovered names. "
                    "role, location, public_description, knowledge_scope, and known_facts "
                    "are required NPC memory fields; do not add unsupported fields such "
                    "as disposition. Make public_description concise, concrete, and "
                    "visually depictable using only player-observable traits. "
                    "Before creating an NPC, inspect state.npcs.relevant. If the same "
                    "person is already listed, reuse that existing npc_id/internal "
                    "identifier and update the one profile; do not create a second "
                    "internal name for the same role/person at the same location. Use "
                    "one NpcUpsertedEvent per distinct meaningful NPC introduced. "
                    "Every party member remains that same canonical NPC: reuse npc_id "
                    "with party_member=true and party_status, party_health_current, "
                    "party_health_max, party_armor_class, party_combat_style, and "
                    "party_skills when those visible details change. Use "
                    "party_member=false to remove membership without deleting the NPC."
                ),
                "generated_visuals": (
                    "Write new inventory descriptions, location descriptions, and NPC "
                    "public_description values with concise concrete player-visible "
                    "visual traits. Do not output image prompts, filenames, URLs, "
                    "base64, or extra image fields. The application separately derives "
                    "and reuses cached images from finalized ordinary state."
                ),
                "secret_memory": (
                    "Use state.gm_secrets.active as authoritative AI-only hidden "
                    "truth. Suggest SecretUpsertedEvent to create or replace a "
                    "durable secret, reusing its stable secret_id. Keep active "
                    "details out of narration and every player-visible field. A GM "
                    "secret must be unknown to both the player and the Player Character. "
                    "Never use the Player Character's own conscious actions, firsthand "
                    "observations, memories, known possessions, or deliberately hidden "
                    "or stored items unless established state explicitly provides a "
                    "credible knowledge barrier such as amnesia, memory alteration, "
                    "unconsciousness, or deception. A reveal_condition cannot be a "
                    "skill check or search that makes the Player Character rediscover "
                    "their own knowing act. If the Player Character knows a fact, keep "
                    "it in player-visible narrative or appropriate public state rather "
                    "than secret memory. Set "
                    "status to revealed when the player learns the truth and also "
                    "write the newly player-known fact through the appropriate "
                    "public NPC, Location, task, flag, item/material, or other "
                    "supported event. "
                    "Set status to retired when the record is no longer true or useful."
                ),
                "miscellaneous_memory": (
                    "state.miscellaneous.entries contains every miscellaneous canon "
                    "record and is always present without relevance filtering or a "
                    "count cap. Treat every entry as authoritative. Suggest "
                    "MiscellaneousUpsertedEvent with a stable misc_id, name, category, "
                    "and complete details when a durable non-secret creature, species, "
                    "culture, faction, religion, law, historical event, phenomenon, "
                    "custom, or other concept is established or changed and no more "
                    "specific state table fits. Reuse the same misc_id for updates. "
                    "Use category Creature for every non-NPC creature or monster the "
                    "Player learns about and include only player-known facts in its "
                    "details; these records populate the player-visible Bestiary. "
                    "Never duplicate NPC, Location, Item, task, or GM-secret records."
                ),
                "currency_transactions": (
                    "The player's money is state.currency.balance, also shown as "
                    "state.currency.balance_base_units, and is stored in "
                    "game_state/currency.balance. It is not inventory coin items. "
                    "When a purchase succeeds, include both the inventory event for "
                    "the item and a CurrencyChangedEvent with payload.base_unit_amount "
                    "as the negative net price. Never use net_base_unit_amount. Do "
                    "not model making change as separate coin items; the application "
                    "formats the resulting integer balance into coin denominations. "
                    "In player-facing prose, use denomination names and natural "
                    "breakdowns; do not say base units or awkward phrases like "
                    "copper coins' worth of silver. "
                    "Currency stored inside a container is not spendable money and "
                    "must not use CurrencyChangedEvent; Python adds it only when an "
                    "open container receives ContainerContentsTakenEvent."
                ),
                (
                    "combat_handoff" if strict_combat else "narrative_combat"
                ): (
                    (
                        "When a fight begins, suggest CombatStartedEvent with concrete "
                        "enemy/allied combatants, health, armor_rating, to_hit_bonus, "
                        "initiative_bonus, personality, complete ammunition/clip fields, "
                        "damage dice, and loot. Python rolls initiative, calculates team "
                        "Threat Levels from maximum health, armor rating, and average "
                        "damage, uses them for non-intelligent NPC targets, preserves "
                        "tactical targeting for intelligent NPCs, and owns attacks, "
                        "reloading, damage, victory, defeat, and loot in the Combat tab."
                    )
                    if strict_combat
                    else (
                        "Narrate and resolve the complete fight in the story without "
                        "CombatStartedEvent. Use warranted skill checks for uncertain "
                        "actions and supported non-combat events for durable consequences."
                    )
                ),
                "out_of_game": (
                    "Boolean. Must be true exactly when conversation_mode is out_of_game; "
                    "the explicit UI mode is authoritative."
                ),
                "event_shape": {
                    "type": "Required event type name.",
                    "payload": "Object containing event-specific data.",
                },
                "known_event_types": [
                    "StatusUpdatedEvent",
                    "SkillCheckRequestedEvent",
                    "SkillUpsertedEvent",
                    "SkillXpAddedEvent",
                    "InventoryItemAddedEvent",
                    "InventoryItemRemovedEvent",
                    "InventoryItemModifiedEvent",
                    "ContainerOpenedEvent",
                    "ContainerContentsTakenEvent",
                    "CombatStartedEvent",
                    "RecipeDiscoveredEvent",
                    "ReagentDiscoveredEvent",
                    "CurrencyChangedEvent",
                    "CurrencyDefinedEvent",
                    "MusicChangedEvent",
                    "SoundEffectChangedEvent",
                    "BackgroundAmbienceChangedEvent",
                    "FlagSetEvent",
                    "LocationUpsertedEvent",
                    "TravelModeChangedEvent",
                    "ActiveTaskUpsertedEvent",
                    "ActiveTaskCompletedEvent",
                    "SpellCatalogUpsertedEvent",
                    "CharacterSpellLearnedEvent",
                    "PlayerSpellCastEvent",
                    "MagicAdvancementRecordedEvent",
                    "MagicEffectUpsertedEvent",
                    "NpcUpsertedEvent",
                    "NpcKnowledgeAddedEvent",
                    "SecretUpsertedEvent",
                    "MiscellaneousUpsertedEvent",
                ],
            },
        }
        known_event_types = packet["response_contract"]["known_event_types"]
        packet["response_contract"]["known_event_types"] = [
            event_type
            for event_type in known_event_types
            if event_type not in disabled_audio_event_types
            and (strict_combat or event_type != "CombatStartedEvent")
        ]
        return packet

    def _build_creative_ideas_context(self, selected_tags: set[str]) -> dict[str, Any]:
        """Builds creative idea context for relevant story turns."""

        if self.creative_ideas is None:
            return {"categories": []}

        return self.creative_ideas.select_for_tags(selected_tags)


def _active_task_context(task) -> dict[str, Any]:
    """Returns only the AI-supported active-task fields."""

    return {
        "name": _compact_text(task.name, max_chars=MAX_SHORT_CONTEXT_TEXT_CHARS),
        "category": _compact_text(task.category, max_chars=MAX_SHORT_CONTEXT_TEXT_CHARS),
        "status": _compact_text(task.status, max_chars=MAX_SHORT_CONTEXT_TEXT_CHARS),
        "description": _compact_text(task.description),
        "requester": _compact_text(task.requester, max_chars=MAX_SHORT_CONTEXT_TEXT_CHARS),
        "location": _compact_text(task.location, max_chars=MAX_SHORT_CONTEXT_TEXT_CHARS),
        "reward": _compact_text(task.reward, max_chars=MAX_SHORT_CONTEXT_TEXT_CHARS),
        "due_date": _compact_text(task.due_date, max_chars=MAX_SHORT_CONTEXT_TEXT_CHARS),
        "due_elapsed_minutes": task.due_elapsed_minutes,
    }


def _history_entry_context(entry) -> dict[str, Any]:
    """Returns a compact history entry for AI context."""

    data = entry.to_dict()
    data["content"] = _compact_text(data.get("content", ""))
    return data


def infer_context_tags(player_command: str) -> set[str]:
    """
    Infers relevant context tags from a player command.

    This intentionally stays simple until a richer command parser exists.
    """

    tags = {"story"}
    words = {
        word.strip(".,!?;:()[]{}\"'").lower()
        for word in player_command.split()
        if word.strip()
    }

    for tag, keywords in KEYWORD_TAGS.items():
        if words.intersection(keywords):
            tags.add(tag)

    return tags


def _normalize_planner_context_tags(tags: list[str]) -> set[str]:
    """Keeps only known planner-selected tags before selecting rule sections."""

    return {
        tag.strip().casefold()
        for tag in tags
        if isinstance(tag, str) and tag.strip().casefold() in PLANNABLE_CONTEXT_TAGS
    }


def _npc_context_profile(npc: dict[str, Any]) -> dict[str, Any]:
    """Returns NPC fields that belong in AI memory context."""

    allowed_fields = {
        "npc_id",
        "name",
        "display_name",
        "role",
        "location",
        "public_description",
        "player_facing_information",
        "knowledge_scope",
        "known_facts",
        "created_at",
        "updated_at",
    }
    return {
        key: _compact_context_value(value)
        for key, value in npc.items()
        if key in allowed_fields
    }


def _hybrid_magic_relevance_tags(
    state: AdventureState,
    *,
    player_command: str,
    relevant_npcs: list[dict[str, Any]],
) -> set[str]:
    """Combines deterministic state signals with planner/keyword magic relevance."""

    command = player_command.casefold()
    command_words = {
        word.strip(".,!?;:()[]{}\"'") for word in command.split() if word.strip()
    }
    magic_words = {
        "arcane", "cantrip", "cast", "casting", "enchant", "magic", "magical",
        "mana", "ritual", "sorcery", "spell", "spellbook", "wizardry",
    }
    spell_words = {"cantrip", "cast", "casting", "spell", "spellbook"}
    tags: set[str] = set()
    if command_words.intersection(magic_words):
        tags.add("magic")
    if command_words.intersection(spell_words):
        tags.add("spell")

    known_spell_names = {
        spell.name.strip().casefold()
        for spell in state.magic.known_spells
        if spell.name.strip()
    }
    if _contains_known_spell_name(command, known_spell_names):
        tags.update({"magic", "spell"})

    if state.magic.active_effects:
        tags.add("magic")

    refers_back = bool(
        command_words.intersection(
            {
                "again", "continue", "exercise", "lesson", "practice", "resume",
                "same", "study", "train", "training", "try",
            }
        )
    )

    task_text = " ".join(
        " ".join(
            (
                task.name,
                task.category,
                task.description,
                task.requester,
                task.notes,
            )
        )
        for task in state.active_tasks.tasks
        if task.status.strip().casefold() not in {"completed", "cancelled", "failed"}
    ).casefold()
    npc_text = " ".join(str(npc) for npc in relevant_npcs).casefold()
    if refers_back and _contains_magic_relevance_signal(task_text, known_spell_names):
        tags.add("magic")
    if _contains_magic_relevance_signal(npc_text, known_spell_names):
        tags.add("magic")

    if refers_back:
        recent_text = " ".join(
            entry.content for entry in state.history.entries[-4:]
        ).casefold()
        if _contains_magic_relevance_signal(recent_text, known_spell_names):
            tags.add("magic")

    if "spell" in tags:
        tags.add("magic")
    return tags


def _contains_magic_relevance_signal(text: str, known_spell_names: set[str]) -> bool:
    """Returns whether text contains a strong magic-domain routing signal."""

    if not text:
        return False
    words = {
        word.strip(".,!?;:()[]{}\"'") for word in text.split() if word.strip()
    }
    if words.intersection(
        {
            "arcane", "cantrip", "cast", "casting", "enchant", "mage", "magic",
            "magical", "mana", "ritual", "sorcery", "spell", "spellbook", "wizard",
        }
    ):
        return True
    return _contains_known_spell_name(text, known_spell_names)


def _contains_known_spell_name(text: str, known_spell_names: set[str]) -> bool:
    """Matches exact known spell names without substring false positives."""

    return any(
        re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None
        for name in known_spell_names
    )


def _magic_context_packet(magic_state, *, include_progression: bool) -> dict[str, Any]:
    """Projects full magic state while conditionally attaching advancement evidence."""

    data = magic_state.to_dict()
    history = data.pop("advancement_history", [])
    summary = data.pop("advancement_summary", {})
    if not include_progression:
        return data
    recent = history[:8] if isinstance(history, list) else []
    milestones = (
        summary.get("important_milestones", []) if isinstance(summary, dict) else []
    )
    data["progression"] = {
        "summary": {
            "total_meaningful_advancements": summary.get(
                "total_meaningful_advancements", 0
            ) if isinstance(summary, dict) else 0,
            "counts_by_category": summary.get("counts_by_category", {})
            if isinstance(summary, dict) else {},
        },
        "recent_meaningful_advancements": recent,
        "important_milestones": milestones[:10]
        if isinstance(milestones, list) else [],
    }
    return data


def _party_context_profile(member: dict[str, Any]) -> dict[str, Any]:
    """Returns party fields plus the shared NPC identity sent to Gemini."""

    allowed_fields = {
        "npc_id",
        "name",
        "display_name",
        "role",
        "location",
        "description",
        "notes",
        "status",
        "health_current",
        "health_max",
        "armor_class",
        "combat_style",
        "skills",
    }
    return {
        key: _compact_context_value(value)
        for key, value in member.items()
        if key in allowed_fields
    }


def _gm_secret_context_record(secret: dict[str, Any]) -> dict[str, Any]:
    """Returns private secret fields that belong in AI context."""

    allowed_fields = {
        "secret_id",
        "title",
        "details",
        "reveal_condition",
        "related_npc_ids",
        "related_locations",
        "status",
        "created_at",
        "updated_at",
    }
    return {
        key: _compact_context_value(value)
        for key, value in secret.items()
        if key in allowed_fields
    }


def _miscellaneous_context_record(entry: dict[str, Any]) -> dict[str, Any]:
    """Returns the complete core fields for always-on miscellaneous context."""

    return {
        "misc_id": str(entry.get("misc_id", "")).strip(),
        "name": str(entry.get("name", "")).strip(),
        "category": str(entry.get("category", "")).strip(),
        "details": str(entry.get("details", "")).strip(),
    }


def _compact_text(value: Any, *, max_chars: int = MAX_CONTEXT_TEXT_CHARS) -> str:
    """Returns bounded text for AI context fields."""

    text = str(value or "").strip()

    if len(text) <= max_chars:
        return text

    return f"{text[: max_chars - 3].rstrip()}..."


def _compact_mapping(
    mapping: dict[str, Any],
    *,
    max_items: int = MAX_CONTEXT_DICT_ITEMS,
) -> dict[str, Any]:
    """Returns a bounded mapping with compact string/list values."""

    compact: dict[str, Any] = {}

    for index, (key, value) in enumerate(mapping.items()):
        if index >= max_items:
            break

        compact[str(key)] = _compact_context_value(value)

    return compact


def _compact_context_value(value: Any) -> Any:
    """Compacts nested context values without changing their broad JSON shape."""

    if isinstance(value, str):
        return _compact_text(value)

    if isinstance(value, list):
        return [
            _compact_context_value(item)
            for item in value[:MAX_CONTEXT_LIST_ITEMS]
        ]

    if isinstance(value, dict):
        return _compact_mapping(value)

    return value


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    """Returns a conservative boolean value for saved settings."""

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().casefold()

        if normalized in {"true", "1", "yes", "on"}:
            return True

        if normalized in {"false", "0", "no", "off", ""}:
            return False

    return default
