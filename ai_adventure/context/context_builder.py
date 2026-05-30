from __future__ import annotations

from typing import Any

from ai_adventure.alchemy.rulebook import AlchemyRulebook, AlchemyRulebookLoader
from ai_adventure.context.creative_ideas import CreativeIdeasLibrary
from ai_adventure.context.models import ContextLibrary
from ai_adventure.context.reference_loader import ContextReferenceLoader
from ai_adventure.core.models import AdventureState


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
        alchemy_rulebook: AlchemyRulebook | None = None,
        creative_ideas: CreativeIdeasLibrary | None = None,
        max_history_entries: int = 8,
        max_reference_sections: int = 14,
        max_rulebook_reagents: int = 8,
    ) -> None:
        """
        Args:
            library: Validated reference context library.
            alchemy_rulebook: Optional structured alchemy rules.
            creative_ideas: Optional creative seed library.
            max_history_entries: Recent history entries to include.
            max_reference_sections: Maximum reference sections to include.
            max_rulebook_reagents: Maximum example rulebook reagents to include.
        """

        self.library = library
        self.alchemy_rulebook = alchemy_rulebook
        self.creative_ideas = creative_ideas
        self.max_history_entries = max_history_entries
        self.max_reference_sections = max_reference_sections
        self.max_rulebook_reagents = max_rulebook_reagents

    @classmethod
    def from_default_library(cls) -> "AiContextBuilder":
        """Creates a builder using the packaged default context library."""

        return cls(
            ContextReferenceLoader().load_default_library(),
            alchemy_rulebook=AlchemyRulebookLoader().load_default_rulebook(),
            creative_ideas=CreativeIdeasLibrary.load_default(),
        )

    def build_story_context(
        self,
        state: AdventureState,
        *,
        player_command: str,
        relevant_npcs: list[dict[str, Any]] | None = None,
        valid_music_tracks: list[str] | None = None,
        current_music: str | None = None,
    ) -> dict[str, Any]:
        """
        Builds the context packet for one story turn.

        Args:
            state: Current composed adventure state.
            player_command: The player's pending command.
            relevant_npcs: NPC memory profiles likely relevant this turn.
            valid_music_tracks: Playable background music filenames.
            current_music: Currently selected background music filename.

        Returns:
            JSON-serializable context packet.
        """

        clean_command = player_command.strip()
        selected_tags = infer_context_tags(clean_command)
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
                    "appearance": state.player.appearance,
                    "backstory": state.player.backstory,
                    "condition": state.player.condition,
                    "notes": state.player.notes,
                },
                "player_ai_preferences": {
                    "additional_context": str(
                        state.settings.values.get("ai.additional_context", "")
                    ),
                    "rules": (
                        "These are player-provided instructions and preferences "
                        "for the AI to remember across turns. Follow them unless "
                        "they conflict with higher-priority system, safety, or "
                        "structured response rules."
                    ),
                },
                "scene": {
                    "location": state.world.location,
                    "time": state.calendar.display_label,
                    "weather": state.world.weather,
                    "flags": state.world.flags,
                },
                "world_profile": {
                    "summary": str(state.settings.values.get("world.summary", "")),
                    "genre": str(state.settings.values.get("world.genre", "")),
                    "game_style": str(state.settings.values.get("world.game_style", "")),
                    "setup_context": str(state.settings.values.get("world.setup_context", "")),
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
                            "description": item.description,
                            "value_base_units": item.value_base_units,
                        }
                        for item in state.inventory.items
                    ],
                },
                "currency": {
                    "balance_base_units": state.currency.balance_base_units,
                    "denominations": state.currency.denominations,
                    "world_description": str(
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
                },
                "alchemy": {
                    "known_reagents": [
                        reagent.to_dict() for reagent in state.alchemy.known_reagents
                    ],
                    "known_recipes": [
                        recipe.to_dict() for recipe in state.alchemy.known_recipes
                    ],
                },
                "skills": {
                    "rules": {
                        "check_formula": "d20 + bonus vs dc",
                        "bonus_formula": "level * 2",
                        "levels": "1-5",
                        "uncertain_action_rule": (
                            "When success, failure, speed, quality, or consequences "
                            "are uncertain, suggest SkillCheckRequestedEvent. The "
                            "Python application resolves the roll."
                        ),
                        "xp_rule": (
                            "Suggest SkillXpAddedEvent only after meaningful use, "
                            "training, study, or practice; do not use XP as a "
                            "substitute for a check."
                        ),
                    },
                    "known_skills": [
                        skill.to_dict() for skill in state.skills.skills
                    ],
                    "recent_checks": [
                        check.to_dict() for check in state.skills.recent_checks
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
                            "requester, location, due date, or notes."
                        ),
                        "completion_rule": (
                            "Suggest ActiveTaskCompletedEvent or QuestCompletedEvent "
                            "when a task is fulfilled, cancelled, resolved, delivered, "
                            "or otherwise no longer active."
                        ),
                    },
                    "tasks": [
                        task.to_dict() for task in state.active_tasks.tasks
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
                            "description, player-facing information, and plausible "
                            "knowledge scope. role is for AI memory and should not "
                            "replace player_facing_information."
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
                    "relevant": relevant_npcs or [],
                },
            },
            "rulebooks": self._build_rulebook_context(
                selected_tags,
                player_command=clean_command,
            ),
            "creative_ideas": self._build_creative_ideas_context(selected_tags),
            "recent_history": [
                entry.to_dict()
                for entry in state.history.entries[-self.max_history_entries :]
            ],
            "reference_sections": [
                section.to_dict() for section in reference_sections
            ],
            "response_contract": {
                "response": (
                    "Required string. Player-facing narration only. "
                    "Never include legacy double-bracket tags."
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
                    "For uncertain actions, suggest SkillCheckRequestedEvent with "
                    "skill_name and either dc or difficulty. Do not narrate final "
                    "success or failure until the application has resolved the check."
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
                    "Use state.player_ai_preferences.additional_context as persistent "
                    "player-provided guidance for narration style, boundaries, and "
                    "miscellaneous preferences. This is intentionally AI-facing, "
                    "unlike the private Journal."
                ),
                "active_tasks": (
                    "Use state.active_tasks.tasks to remember current quests, "
                    "commissions, custom orders, pending purchases, and other "
                    "ongoing obligations. Suggest ActiveTaskUpsertedEvent for new "
                    "or changed tasks and ActiveTaskCompletedEvent when one is no "
                    "longer active."
                ),
                "background_music": (
                    "Use state.audio.valid_music_tracks for scene-aware background "
                    "music. Suggest MusicChangedEvent when a meaningful scene, mood, "
                    "location, environment, or danger shift calls for a different "
                    "track. The filename must exactly match a valid track."
                ),
                "creative_ideas": (
                    "Treat creative_ideas as the preferred source of style seeds "
                    "when the current turn calls for names, locations, cultures, "
                    "food, drinks, magic, alchemy ingredients, species, or similar "
                    "invented details. Prefer the provided examples or close "
                    "stylistic relatives over generic training-data fantasy "
                    "defaults. Never use creative_ideas.banned_terms or obvious "
                    "spelling, hyphenation, or reskin variants for newly invented "
                    "proper nouns. These examples are not established canon and "
                    "must not override player-provided or saved world facts."
                ),
                "npc_memory": (
                    "Use NpcUpsertedEvent when a new meaningful NPC appears or an "
                    "existing NPC profile needs correction. Use NpcKnowledgeAddedEvent "
                    "only for facts the NPC plausibly learned this turn. In "
                    "NpcUpsertedEvent, display_name and player_facing_information are "
                    "player-visible and must not include secrets or undiscovered names. "
                    "Use one NpcUpsertedEvent per distinct meaningful NPC introduced."
                ),
                "out_of_game": "Boolean. True only for fully out-of-game answers.",
                "event_shape": {
                    "type": "Required event type name.",
                    "payload": "Object containing event-specific data.",
                },
                "known_event_types": [
                    "StoryAdvancedEvent",
                    "StatusUpdatedEvent",
                    "SkillCheckRequestedEvent",
                    "SkillUpsertedEvent",
                    "SkillXpAddedEvent",
                    "InventoryItemAddedEvent",
                    "InventoryItemRemovedEvent",
                    "InventoryItemModifiedEvent",
                    "RecipeDiscoveredEvent",
                    "ReagentDiscoveredEvent",
                    "CurrencyChangedEvent",
                    "CurrencyDefinedEvent",
                    "MusicChangedEvent",
                    "FlagSetEvent",
                    "LocationChangedEvent",
                    "PlayerNoteAddedEvent",
                    "WorldLoreAddedEvent",
                    "WorldLoreChangedEvent",
                    "WorldLoreUpdatedEvent",
                    "SecretAddedEvent",
                    "QuestAddedEvent",
                    "QuestCompletedEvent",
                    "ActiveTaskUpsertedEvent",
                    "ActiveTaskUpdatedEvent",
                    "ActiveTaskCompletedEvent",
                    "MerchantInterfaceRequestedEvent",
                    "SpellLearnedEvent",
                    "NpcUpsertedEvent",
                    "NpcKnowledgeAddedEvent",
                ],
            },
        }

    def _build_rulebook_context(
        self,
        selected_tags: set[str],
        *,
        player_command: str,
    ) -> dict[str, Any]:
        """Builds relevant rulebook context sections."""

        rulebooks: dict[str, Any] = {}

        if "alchemy" in selected_tags and self.alchemy_rulebook is not None:
            rulebooks["alchemy"] = self.alchemy_rulebook.to_context_summary(
                player_command=player_command,
                max_reagents=self.max_rulebook_reagents,
            )

        return rulebooks

    def _build_creative_ideas_context(self, selected_tags: set[str]) -> dict[str, Any]:
        """Builds creative idea context for relevant story turns."""

        if self.creative_ideas is None:
            return {"categories": []}

        return self.creative_ideas.select_for_tags(selected_tags)


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
