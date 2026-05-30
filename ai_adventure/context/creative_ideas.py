from __future__ import annotations

import json
import logging
import random
from importlib.resources import files
from typing import Any


LOGGER = logging.getLogger(__name__)


class CreativeIdeasLibrary:
    """Loads and selects prioritized creative seed ideas for AI prompts."""

    def __init__(
        self,
        raw_data: dict[str, Any],
        *,
        banned_terms_data: dict[str, Any] | None = None,
        max_ideas_per_category: int = 12,
        rng: random.Random | None = None,
    ) -> None:
        """
        Args:
            raw_data: Parsed creative ideas JSON.
            banned_terms_data: Parsed banned creative terms JSON.
            max_ideas_per_category: Maximum examples included per category.
            rng: Optional random generator, primarily for tests.
        """

        self.raw_data = raw_data
        self.banned_terms_data = banned_terms_data or {}
        self.max_ideas_per_category = max_ideas_per_category
        self.rng = rng or random.SystemRandom()
        self.banned_terms = _parse_banned_terms(self.banned_terms_data)
        self.categories = _parse_categories(raw_data, banned_terms=self.banned_terms)

    @classmethod
    def load_default(cls) -> "CreativeIdeasLibrary":
        """Loads packaged creative idea seeds."""

        package = files("ai_adventure.data.context")
        creative_resource = package.joinpath("creative_ideas.json")
        banned_terms_resource = package.joinpath("banned_terms.json")
        raw_data = json.loads(creative_resource.read_text(encoding="utf-8"))
        banned_terms_data = json.loads(
            banned_terms_resource.read_text(encoding="utf-8")
        )
        return cls(raw_data, banned_terms_data=banned_terms_data)

    def select_for_tags(
        self,
        tags: set[str],
        *,
        max_categories: int = 4,
    ) -> dict[str, Any]:
        """
        Selects creative ideas relevant to context tags.

        Args:
            tags: Inferred context tags.
            max_categories: Maximum matching categories to include.

        Returns:
            JSON-serializable creative idea context.
        """

        matching_categories = [
            category
            for category in self.categories
            if set(category["tags"]).intersection(tags)
        ]
        selected = self._randomized_subset(matching_categories, max_categories)

        return self._build_context(selected)

    def select_for_new_game(self) -> dict[str, Any]:
        """Returns a broad sample set for initial world and character creation."""

        return self._build_context(
            self._randomized_subset(self.categories, len(self.categories)),
            max_ideas_per_category=8,
        )

    def _build_context(
        self,
        categories: list[dict[str, Any]],
        *,
        max_ideas_per_category: int | None = None,
    ) -> dict[str, Any]:
        """Builds a compact prompt-ready payload."""

        idea_limit = max_ideas_per_category or self.max_ideas_per_category

        return {
            "purpose": str(
                self.raw_data.get("usage", {}).get(
                    "purpose",
                    "Prioritized inspiration seeds for names and setting details.",
                )
            ),
            "rules": str(
                self.raw_data.get("usage", {}).get(
                    "rule",
                    "Prefer these examples or similar variants over generic defaults.",
                )
            ),
            "priority": (
                "High. When inventing names or setting details, use these examples "
                "or close stylistic relatives before relying on broad training-data "
                "fantasy defaults."
            ),
            "banned_terms_policy": str(
                self.banned_terms_data.get("usage", {}).get(
                    "rule",
                    "Never use banned_terms for newly generated proper nouns.",
                )
            ),
            "banned_terms": list(self.banned_terms),
            "player_character_name_examples": self._build_player_character_name_examples(
                max_ideas_per_category=idea_limit,
            ),
            "categories": [
                {
                    "id": str(category["id"]),
                    "title": str(category["title"]),
                    "ideas": self._randomized_subset(category["ideas"], idea_limit),
                }
                for category in categories
            ],
        }

    def _randomized_subset(self, values: list[Any], limit: int) -> list[Any]:
        """Returns a shuffled subset without always biasing toward early entries."""

        if limit <= 0 or not values:
            return []

        if len(values) <= limit:
            selected = list(values)
            self.rng.shuffle(selected)
            return selected

        return self.rng.sample(values, limit)

    def _build_player_character_name_examples(
        self,
        *,
        max_ideas_per_category: int,
    ) -> dict[str, Any]:
        """Builds a balanced mixed name pool for new player characters."""

        male_names = self._ideas_for_category("male_character_names")
        female_names = self._ideas_for_category("female_character_names")
        per_category = max(1, max_ideas_per_category // 2)
        ideas = (
            self._randomized_subset(female_names, per_category)
            + self._randomized_subset(male_names, per_category)
        )
        self.rng.shuffle(ideas)

        return {
            "purpose": (
                "Balanced player-character name examples for blank/default new-game "
                "character creation."
            ),
            "rule": (
                "Use this mixed pool when the player did not provide a character "
                "name. Do not treat male-coded names as the default."
            ),
            "ideas": ideas,
        }

    def _ideas_for_category(self, category_id: str) -> list[str]:
        """Returns parsed idea strings for one category id."""

        for category in self.categories:
            if category["id"] == category_id:
                return list(category["ideas"])

        return []


def _parse_categories(
    raw_data: dict[str, Any],
    *,
    banned_terms: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Validates creative idea categories enough for prompt construction."""

    raw_categories = raw_data.get("categories", [])
    banned_lookup = {term.casefold() for term in banned_terms}

    if not isinstance(raw_categories, list):
        LOGGER.warning("Creative ideas JSON categories field is not a list.")
        return []

    categories: list[dict[str, Any]] = []

    for raw_category in raw_categories:
        if not isinstance(raw_category, dict):
            continue

        category_id = str(raw_category.get("id", "")).strip()
        title = str(raw_category.get("title", "")).strip()
        raw_tags = raw_category.get("tags", [])
        raw_ideas = raw_category.get("ideas", [])

        if (
            not category_id
            or not title
            or not isinstance(raw_tags, list)
            or not isinstance(raw_ideas, list)
        ):
            continue

        tags = [str(tag).strip().lower() for tag in raw_tags if str(tag).strip()]
        ideas = [
            str(idea).strip()
            for idea in raw_ideas
            if str(idea).strip()
            and str(idea).strip().casefold() not in banned_lookup
        ]

        if tags and ideas:
            categories.append(
                {
                    "id": category_id,
                    "title": title,
                    "tags": tags,
                    "ideas": ideas,
                }
            )

    return categories


def _parse_banned_terms(raw_data: dict[str, Any]) -> tuple[str, ...]:
    """Validates banned creative terms enough for prompt construction."""

    raw_terms = raw_data.get("terms", [])

    if not isinstance(raw_terms, list):
        LOGGER.warning("Banned creative terms JSON terms field is not a list.")
        return ()

    seen: set[str] = set()
    terms: list[str] = []

    for raw_term in raw_terms:
        term = str(raw_term).strip()
        folded = term.casefold()

        if term and folded not in seen:
            terms.append(term)
            seen.add(folded)

    return tuple(terms)
