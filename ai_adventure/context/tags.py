"""Shared context-tag vocabulary and concise planner guidance."""

from __future__ import annotations


CONTEXT_TAG_DESCRIPTIONS: dict[str, str] = {
    "alchemy": "potions, mixtures, reagents, recipes, and crafting experiments",
    "character": "character-specific background, class, profession, and identity boundaries",
    "combat": "fights, combatants, damage, and combat-state rules",
    "crafting": "making, repairing, or building items",
    "crime": "theft, trespass, deception, law, and criminal consequences",
    "currency": "money, denominations, prices, and monetary changes",
    "dialogue": "speaking with NPCs, knowledge boundaries, and conversation",
    "events": "durable game-state event requirements",
    "exploration": "searching, moving through, examining, or discovering places",
    "guardrails": "core narrator safety and consistency constraints",
    "inventory": "possessions, equipment, consuming, giving, or receiving items",
    "lore": "world facts, factions, history, and player-known information",
    "magic": "magic, rituals, magical effects, and spellcasting",
    "merchant": "shops, buying, selling, meals, and ordinary services",
    "music": "background music and ambience changes",
    "naming": "new names for people, places, creatures, and items",
    "out_of_game": "out-of-game questions or rule discussion",
    "quest": "quests, commissions, objectives, and rewards",
    "reagent": "discovering, collecting, or recording alchemical reagents",
    "recipe": "recipes, recipe discovery, and recipe ingredients",
    "scene": "scene framing, setting, and immediate narrative situation",
    "skill": "skills, training, experience, and skill checks",
    "spell": "individual spells and their learned details",
    "state": "general persistent state changes and their event representation",
    "story": "general narration and player-command handling",
    "task": "active tasks, promises, obligations, and their progress",
    "time": "elapsed time, calendars, schedules, and time passage",
    "travel": "routes, journeys, arrival, and travel-time consequences",
    "uncertainty": "actions with meaningful risk, uncertainty, or variable outcomes",
    "world": "worldbuilding, weather, locations, and broader setting details",
}

PLANNABLE_CONTEXT_TAGS = frozenset(CONTEXT_TAG_DESCRIPTIONS)
