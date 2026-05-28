from __future__ import annotations


MAX_SKILL_LEVEL = 5
XP_THRESHOLDS_BY_LEVEL = {
    1: 0,
    2: 10,
    3: 25,
    4: 45,
    5: 70,
}
DIFFICULTY_DCS = {
    "trivial": 6,
    "easy": 10,
    "normal": 14,
    "moderate": 14,
    "hard": 18,
    "very hard": 22,
    "severe": 22,
    "extreme": 26,
}


def clamp_skill_level(level: int) -> int:
    """Clamps a skill level into the supported range."""

    return max(1, min(MAX_SKILL_LEVEL, int(level)))


def bonus_for_level(level: int) -> int:
    """Returns the clear skill bonus for a level."""

    return clamp_skill_level(level) * 2


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
