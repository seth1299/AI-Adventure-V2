"""Authoritative character rules shared by setup, persistence, UI, and Gemini."""
from __future__ import annotations

ATTRIBUTES = ("Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma")
RULES_VERSION = "stats-v1"
MAX_PLAYER_LEVEL = 20
DEFAULT_PLAYER_MAX_HEALTH = 20
POINT_COSTS = dict(zip(range(8, 19), (0, 1, 2, 3, 4, 5, 7, 9, 12, 15, 19)))
RANK_STATS = {"blank": (12, 1), "beginner": (18, 1), "average": (27, 3), "experienced": (36, 6), "professional": (45, 10)}
PLAYER_XP_AWARDS = {"minor": 10, "standard": 25, "major": 50}
PLAYER_ACHIEVEMENT_RULE = (
    "For PlayerAchievementRecordedEvent with source_kind=objective, source_id must be "
    "the exact persistent ID of the completed task, never its name. Emit "
    "ActiveTaskCompletedEvent before its achievement award in the same response. "
    "For source_kind=milestone, use a stable identifier for the distinct completed "
    "accomplishment. Reuse the same source_id on retries and retellings; never relabel "
    "an old achievement or award Player XP for individual rolls."
)

STATS_RULE = (
    "Six attributes use floor((score-10)/2) modifiers. A d20 test is an app-rolled "
    "d20 + attribute modifier + at most one existing relevant skill's level (+1 per level). "
    "Choose the most directly relevant known skill by its description; its attribute can vary with the action. "
    "For locating or gathering wild plants, choose known Foraging rather than Investigation or Perception when applicable. "
    "Untrained tests have zero skill bonus and never automatically learn a skill. "
    "Checks, attacks, and saves all compare totals to DC; natural 1 and 20 are not automatic outcomes. "
    "Advantage/disadvantage use two fair dice, do not stack, and cancel each other. "
    "DCs: trivial 5, easy 10, moderate 15, hard 20, very hard 25, extreme 30. "
    "Request tests only for meaningful uncertainty and consequences, before narrating the outcome; "
    "use authoritative resolved tests, do not request duplicate checks, and never reroll them. Fighting is narrative: "
    "no initiative, turns, encounter engine, armor ratings, damage dice, or enemy stat blocks. "
    "Health changes require PlayerHealthChangedEvent with delta, reason and source_id. "
    "Max health is twice Constitution; zero health incapacitates without automatic death. "
    "Base carrying capacity is five times Strength in pounds; carried bags add bonuses, vehicles remain separate. "
    "Player XP rewards distinct completed objectives/milestones once: minor 10, standard 25, major 50. "
    "Individual rolls never earn Player XP. Reuse persistent source IDs; do not relabel old achievements. "
    "Every 100 Player XP gains a level, capped at 20, banking a choice of +1 attribute or two skill advances. "
    "Only the player spends rewards. Attributes cap at 20; skills cap at 5 with separate training XP. "
    "Meaningful instruction/practice may unlock a level-1 skill; upserts cannot directly promote skills."
)


def attribute_modifier(score: int) -> int:
    return (int(score) - 10) // 2


def normalize_attributes(raw: object, *, maximum: int = 20) -> dict[str, int]:
    values = raw if isinstance(raw, dict) else {}
    result = {}
    for attribute in ATTRIBUTES:
        value = values.get(attribute, values.get(attribute.lower(), 10))
        if isinstance(value, bool) or not isinstance(value, int) or not 8 <= value <= maximum:
            raise ValueError(f"{attribute} must be an integer from 8 to {maximum}.")
        result[attribute] = value
    return result


def rank_stats(rank: str, baseline: str = "professional") -> tuple[int, int]:
    return RANK_STATS.get(baseline if rank == "custom" else rank, RANK_STATS["professional"])


def point_buy_cost(attributes: dict[str, int]) -> int:
    return sum(POINT_COSTS[value] for value in normalize_attributes(attributes, maximum=18).values())


def starting_attributes(budget: int) -> dict[str, int]:
    """Pick an exact, balanced default allocation; ties follow attribute order."""
    states: dict[int, tuple[int, ...]] = {0: ()}
    for _ in ATTRIBUTES:
        next_states = {}
        for spent, scores in states.items():
            for score, cost in POINT_COSTS.items():
                total = spent + cost
                if total > budget:
                    continue
                candidate = scores + (score,)
                old = next_states.get(total)
                key = lambda seq: (sum(s*s for s in seq) - sum(seq)**2 / len(seq), -sum(seq), tuple(-s for s in seq))
                if old is None or key(candidate) < key(old):
                    next_states[total] = candidate
        states = next_states
    return dict(zip(ATTRIBUTES, states[budget]))


def health_max(attributes: dict[str, int]) -> int:
    return 2 * attributes["Constitution"]


def carrying_capacity(attributes: dict[str, int]) -> float:
    return float(5 * attributes["Strength"])
