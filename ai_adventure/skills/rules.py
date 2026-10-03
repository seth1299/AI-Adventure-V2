from __future__ import annotations


SKILL_DESCRIPTION_RULE = (
    "Skill descriptions must describe the skill's uses and scope independently of the "
    "player's current training level. Do not describe current proficiency with wording "
    "such as 'basic capability', 'novice understanding', or 'expert mastery'; the level "
    "field records proficiency separately. For example, use 'Enduring harsh conditions "
    "and finding shelter' rather than 'Basic capability to endure harsh conditions'."
)
MAX_SKILL_XP_RULE = "Master (level 5) skills cannot gain further XP or levels. Do not propose XP awards for them."

SKILL_TRAINING_SOURCE_RULE = (
    "SkillXpAddedEvent may include an optional source_id identifying this particular "
    "skill training award. Reuse it on retries and retellings. When omitted and a "
    "message ID exists, Python deduplicates one training award per skill per message; "
    "without a message ID there is no fallback deduplication."
)

MAX_SKILL_LEVEL = 5
XP_THRESHOLDS_BY_LEVEL = {
    1: 0,
    2: 8,
    3: 16,
    4: 24,
    5: 32,
}
DIFFICULTY_DCS = {
    "trivial": 5,
    "easy": 10,
    "normal": 15,
    "moderate": 15,
    "hard": 20,
    "very hard": 25,
    "severe": 25,
    "extreme": 30,
}


def clamp_skill_level(level: int) -> int:
    """Clamps a skill level into the supported range."""

    return max(1, min(MAX_SKILL_LEVEL, int(level)))


def bonus_for_level(level: int) -> int:
    """Returns the clear skill bonus for a level."""

    return clamp_skill_level(level)


def level_for_xp(current_level: int, xp: int) -> int:
    """Returns the highest level earned by cumulative XP."""

    level = clamp_skill_level(current_level)

    for candidate_level, threshold in XP_THRESHOLDS_BY_LEVEL.items():
        if xp >= threshold:
            level = max(level, candidate_level)

    return clamp_skill_level(level)


def dc_for_difficulty(difficulty: str | int | None) -> int:
    """Returns a DC for a named or numeric difficulty."""

    if difficulty is None:
        return DIFFICULTY_DCS["normal"]

    if isinstance(difficulty, int):
        return difficulty

    clean_difficulty = str(difficulty).strip().lower()

    if clean_difficulty.isdigit():
        return int(clean_difficulty)

    return DIFFICULTY_DCS.get(clean_difficulty, DIFFICULTY_DCS["normal"])
