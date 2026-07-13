from __future__ import annotations

from typing import Any

from ai_adventure.alchemy.ingredients import (
    COMMON_MEASUREMENT_UNITS,
    CRAFTING_INGREDIENT_CATEGORY_NAMES,
)
from ai_adventure.ai.modes import ai_mode_preferences_from_settings
from ai_adventure.context.creative_ideas import CreativeIdeasLibrary
from ai_adventure.context.models import ContextLibrary
from ai_adventure.context.naming import GENERIC_PROPER_NOUN_PLACEHOLDER_RULE
from ai_adventure.context.reference_loader import ContextReferenceLoader
from ai_adventure.context.tags import PLANNABLE_CONTEXT_TAGS
from ai_adventure.combat import normalize_combat_state
from ai_adventure.currency import format_currency_amount
from ai_adventure.core.models import AdventureState
from ai_adventure.narration_preferences import normalize_narration_preferences


MAX_CONTEXT_TEXT_CHARS = 1200
MAX_SHORT_CONTEXT_TEXT_CHARS = 500
MAX_CONTEXT_DICT_ITEMS = 50
MAX_CONTEXT_LIST_ITEMS = 40
MAX_INVENTORY_CONTEXT_ITEMS = 50
MAX_ITEM_CATALOG_CONTEXT_ITEMS = 60
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
        relevant_npcs: list[dict[str, Any]] | None = None,
        gm_secrets: list[dict[str, Any]] | None = None,
        valid_music_tracks: list[str] | None = None,
        current_music: str | None = None,
        resolved_skill_checks: list[dict[str, Any]] | None = None,
        planner_context_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Builds the context packet for one story turn.

        Args:
            state: Current composed adventure state.
            player_command: The player's pending command.
            relevant_npcs: NPC memory profiles likely relevant this turn.
            gm_secrets: Active private GM-memory records for every turn.
            valid_music_tracks: Playable background music filenames.
            current_music: Currently selected background music filename.
            resolved_skill_checks: Skill checks already resolved for this command.
            planner_context_tags: Validated tags selected by the pre-narration
                planner. ``None`` falls back to keyword inference.

        Returns:
            JSON-serializable context packet.
        """

        clean_command = player_command.strip()
        selected_tags = (
            infer_context_tags(clean_command)
            if planner_context_tags is None
            else _normalize_planner_context_tags(planner_context_tags)
        )
        selected_tags.add("story")
        clean_music_tracks = [
            str(track).strip()
            for track in (valid_music_tracks or [])
            if str(track).strip()
        ]

        if clean_music_tracks:
            selected_tags.add("music")

        reference_sections = self.library.select_sections(
            selected_tags,
            max_sections=self.max_reference_sections,
        )
        clean_relevant_npcs = [
            _npc_context_profile(npc)
            for npc in (relevant_npcs or [])
        ]
        clean_gm_secrets = [
            _gm_secret_context_record(secret)
            for secret in (gm_secrets or [])
            if str(secret.get("status", "active")).strip().casefold() == "active"
        ]
        journal_share_with_ai = _coerce_bool(
            state.settings.values.get("journal.share_with_ai", False),
            default=False,
        )
        journal_notes = ""

        if journal_share_with_ai:
            journal_notes = _compact_text(
                state.settings.values.get("journal.private_notes", "")
            )
        narration_preferences = normalize_narration_preferences(
            {
                "tense": state.settings.values.get("ai.narration_tense", ""),
                "style": state.settings.values.get("ai.narration_style", ""),
            }
        )
        ai_mode_preferences = ai_mode_preferences_from_settings(state.settings.values)
        combat_state = normalize_combat_state(state.settings.values.get("combat.state", {}))

        return {
            "schema_version": 1,
            "packet_type": "story_turn",
            "player_command": clean_command,
            "selection": {
                "tags": sorted(selected_tags),
                "max_history_entries": self.max_history_entries,
                "max_reference_sections": self.max_reference_sections,
            },
            "state": {
                "adventure_title": state.metadata.title,
                "player": {
                    "name": state.player.name,
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
                "journal": {
                    "share_with_ai": journal_share_with_ai,
                    "player_notes": journal_notes,
                    "rules": (
                        "Use player_notes only when share_with_ai is true. "
                        "These are player-authored journal notes, not verified "
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
                        "ContainerOpenedEvent only after required lock/trap checks "
                        "succeed, then ContainerContentsTakenEvent only when the "
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
                            "name": item.name,
                            "category": item.category,
                            "description": _compact_text(item.description),
                            "value_base_units": item.value_base_units,
                            "metadata": _compact_context_value(item.metadata),
                        }
                        for item in state.item_catalog.items[:MAX_ITEM_CATALOG_CONTEXT_ITEMS]
                    ],
                    "rules": {
                        "purpose": (
                            "This is the durable master list of known item "
                            "definitions. It may include items the player no "
                            "longer owns."
                        ),
                        "possession_rule": (
                        "Only state.inventory.items are current possessions. Each "
                        "inventory item includes quantity, quantity_unit, and "
                        "storage_location (home or actively_carried); use the latter "
                        "to distinguish Home storage from what the Player Character "
                        "is carrying. "
                            "Use item_catalog to remember descriptions, categories, "
                            "and values for previously seen items. Each item also "
                            "has metadata.item_uuid, a stable internal identity; "
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
                    "active": bool(combat_state.get("active", False)),
                    "round": combat_state.get("round", 1),
                    "turn_index": combat_state.get("turn_index", 0),
                    "combatants": [
                        _compact_context_value(combatant)
                        for combatant in combat_state.get("combatants", [])
                    ],
                    "rules": (
                        "When a fight starts, suggest CombatStartedEvent with "
                        "enemy/allied combatants, health, armor_rating, "
                        "to_hit_bonus, initiative_bonus, personality, ammunition/clip "
                        "fields, damage dice, and loot. The Python combat system "
                        "rolls initiative, calculates each team's Threat Levels "
                        "from health, armor, and average damage, and uses those "
                        "percentages for non-intelligent NPC targeting. Intelligent "
                        "NPCs target tactically. Do not resolve attacks, turns, damage, "
                        "victory, defeat, or loot in story prose after combat "
                        "starts; the Python Combat tab handles those mechanics."
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
                            "location, and uses as player-known structured fields. "
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
                            "skill_name may identify a generalized capability absent "
                            "from known_skills. When it does, include skill_description "
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
                            "multiple consecutive days."
                        ),
                        "delete_rule": (
                            "Use CalendarEventDeletedEvent only when a stored event "
                            "is cancelled or should no longer exist."
                        ),
                    },
                    "events": [
                        _compact_context_value(event)
                        for event in state.settings.values.get("calendar.events", [])[:40]
                        if isinstance(event, dict)
                    ],
                },
                "audio": {
                    "current_music": str(
                        current_music
                        or state.settings.values.get("audio.current_music", "")
                        or ""
                    ),
                    "valid_music_tracks": clean_music_tracks,
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
                "gm_secrets": {
                    "visibility": "AI-only; never display this section to the player.",
                    "rules": {
                        "continuity": (
                            "Treat active records as authoritative hidden truth for "
                            "mystery logic, clues, NPC behavior, and off-screen plans."
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
                    "intent, or search target. Do not invent player-character "
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
                "skill_checks": (
                    "Suggest SkillCheckRequestedEvent with skill_name and either dc "
                    "or difficulty only for actions with meaningful uncertainty, "
                    "opposition, hidden information, danger, resource pressure, time "
                    "pressure, or consequences in the current scene. "
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
                    "Use state.player.name, appearance, backstory, and notes as "
                    "player-authored character context. Treat it as true for the "
                    "player character, but do not let NPCs know private profile "
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
                    "preferences. This is always AI-facing; Journal notes are only "
                    "AI-facing when state.journal.share_with_ai is true."
                ),
                "journal": (
                    "When state.journal.share_with_ai is true, use "
                    "state.journal.player_notes as player-authored notes, theories, "
                    "reminders, and priorities. Treat them as the player's perspective, "
                    "not automatically true world facts. When share_with_ai is false, "
                    "ignore Journal notes because they are private."
                ),
                "mature_content": ai_mode_preferences["model_content_rules"],
                "active_tasks": (
                    "Use state.active_tasks.tasks to remember current quests, "
                    "commissions, custom orders, pending purchases, and other "
                    "ongoing obligations. Suggest ActiveTaskUpsertedEvent for new "
                    "or changed tasks and ActiveTaskCompletedEvent when one is no "
                    "longer active. Use due_elapsed_minutes for exact deadlines "
                    "instead of vague due-date prose."
                ),
                "item_catalog": (
                    "Use state.item_catalog.items as the master list of remembered "
                    "item definitions. It preserves descriptions, categories, values, "
                    "and metadata.item_uuid stable internal identities; reuse the same "
                    "item_uuid for the same item even when its display name changes. "
                    "and equipment metadata for items even after they leave inventory. "
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
                    "When StatusUpdatedEvent.location changes to a substantially different environment type, compare state.audio.current_music to state.audio.valid_music_tracks."
                    "If a listed track clearly better matches the new environment or mood, include MusicChangedEvent before the final StatusUpdatedEvent."
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
                    "as disposition. "
                    "Before creating an NPC, inspect state.npcs.relevant. If the same "
                    "person is already listed, reuse that existing npc_id/internal "
                    "identifier and update the one profile; do not create a second "
                    "internal name for the same role/person at the same location. Use "
                    "one NpcUpsertedEvent per distinct meaningful NPC introduced."
                ),
                "secret_memory": (
                    "Use state.gm_secrets.active as authoritative AI-only hidden "
                    "truth. Suggest SecretUpsertedEvent to create or replace a "
                    "durable secret, reusing its stable secret_id. Keep active "
                    "details out of narration and every player-visible field. Set "
                    "status to revealed when the player learns the truth and also "
                    "write the newly player-known fact through the appropriate "
                    "public NPC, Location, task, flag, item/material, or other "
                    "supported event. "
                    "Set status to retired when the record is no longer true or useful."
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
                "combat_handoff": (
                    "When a fight begins, suggest CombatStartedEvent with concrete "
                    "enemy/allied combatants, health, armor_rating, to_hit_bonus, "
                    "initiative_bonus, personality, complete ammunition/clip fields, "
                    "damage dice, and loot. Python rolls initiative, calculates "
                    "team Threat Levels from maximum health, armor rating, and average "
                    "damage, uses them for non-intelligent NPC targets, and preserves "
                    "tactical targeting for intelligent NPCs. After that, do not resolve "
                    "attacks, turns, reloading, damage, victory, "
                    "defeat, or loot in story prose; the Combat tab owns those mechanics."
                ),
                "out_of_game": "Boolean. True only for fully out-of-game answers.",
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
                    "FlagSetEvent",
                    "LocationUpsertedEvent",
                    "TravelModeChangedEvent",
                    "ActiveTaskUpsertedEvent",
                    "ActiveTaskCompletedEvent",
                    "SpellLearnedEvent",
                    "NpcUpsertedEvent",
                    "NpcKnowledgeAddedEvent",
                    "SecretUpsertedEvent",
                ],
            },
        }

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
