"""Shared rules for durable NPC identity, appearance and conversation memory."""

NPC_PROFILE_CONTINUITY_RULE = (
    "An existing NPC's stable npc_id and internal name identify the same person across "
    "turns and locations. Ordinary conversation is not a new introduction. Reuse the "
    "saved profile without paraphrasing its identity or appearance. Use "
    "NpcKnowledgeAddedEvent for newly learned facts. For a genuine correction, newly "
    "revealed identity, or observable in-world change to an established display_name, "
    "role, public_description, gender_identity, age or species, supply "
    "profile_change_reason in NpcUpsertedEvent explaining the specific established "
    "evidence or change; otherwise Python preserves nonblank established fields. "
    "Blank fields may be filled, and location, player-facing information and learned "
    "facts may be updated normally. Never invent a change merely to refresh a profile. "
    "Existing NPC portraits are retained during automatic scans, including after a "
    "profile correction; only the player's explicit image selection or generation "
    "action replaces an established portrait."
)
