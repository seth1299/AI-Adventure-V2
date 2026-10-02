from __future__ import annotations
from typing import Any

FIGHTING_FOCUS_LEVELS = ("low", "balanced", "high")
FIGHTING_FOCUS_LABELS = {"low": "Low - Fighting is uncommon", "balanced": "Balanced - Fighting when the story calls for it", "high": "High - Fighting is a major focus"}
FIGHTING_FOCUS_INSTRUCTIONS = {
    "low": "Keep fighting uncommon. Prefer negotiation, evasion, investigation, and travel unless violence follows from established stakes or player choices.",
    "balanced": "Use fighting when it follows naturally from the story and player choices; respect established danger.",
    "high": "Make fighting a recurring part of the adventure with varied opponents and meaningful stakes; respect player choices."
}

def normalize_fighting_preferences(raw: Any) -> dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    focus = str(raw.get("focus", raw.get("frequency", "balanced"))).strip().casefold()
    return {"focus": focus if focus in FIGHTING_FOCUS_LEVELS else "balanced"}
