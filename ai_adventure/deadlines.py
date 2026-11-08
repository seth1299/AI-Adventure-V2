"""Keep clearly narrated obligations aligned with durable task deadlines."""
import re


DEADLINE_RULE = (
    "When this turn establishes or accepts a future obligation, including borrowed-item "
    "returns, repayments, promises, commissions and deliveries, emit ActiveTaskUpsertedEvent "
    "in the SAME response, even if the player command and selected tags do not mention "
    "tasks or time. A deadline in narration alone is not recorded. Set a complete "
    "description, requester, relevant location, reward (N/A when none), due_date and "
    "absolute due_elapsed_minutes using state.calendar.current and its custom settings. "
    "Resolve relative durations from the moment the obligation is established, not from "
    "a later reminder or app restart; two fortnights means 28 days. Never reset an "
    "unchanged deadline. Dated active tasks automatically appear in BOTH Time-Sensitive "
    "Events and Calendar: do not duplicate them with CalendarEventUpsertedEvent or "
    "TimedEventUpsertedEvent. Use CalendarEventUpsertedEvent for standalone dated "
    "appointments, festivals and other milestones without a task, and TimedEventUpsertedEvent "
    "for processes such as growth or fermentation. Do not create obligations for "
    "unaccepted offers, hypothetical choices or cancelled commitments."
)


def missing_deadline_commitments(narrative: str, events: list[dict], tasks: list[dict]) -> list[str]:
    """Flag high-confidence deadline clauses, without guessing their date or payload.

    This is a conservative safety net, not a general natural-language date parser.
    Gemini resolves dates; Python checks that a related dated obligation exists.
    """
    dated = [task for task in tasks if _dated(task)]
    dated.extend(event.get("payload", {}) for event in events
                 if event.get("type") == "ActiveTaskUpsertedEvent" and _dated(event.get("payload", {})))
    missing = []
    for clause in re.split(r"(?<=[.!?])\s+|\n+", narrative):
        folded = clause.casefold()
        if re.search(r"\b(?:if|could|would|may|might|welcome to|no longer|already returned|need not)\b", folded):
            continue
        if not re.search(r"\b(?:return|repay|owe|submit|deliver|deadline|committ?ing|promis\w*|must|need to|due)\b", folded):
            continue
        if not re.search(
            r"\b(?:repay|owe|due|deadline|committ?ing|promis\w*|must|need to|borrowed|books?|volumes?|folios?|tomes?|loan)\b",
            folded,
        ):
            continue
        if re.search(r"\bdue\b", folded) and not re.search(
            r"\b(?:return|repay|owe|submit|deliver|deadline|committ?ing|promis\w*|must|need to|"
            r"borrowed|books?|volumes?|folios?|tomes?|loan|payments?|commissions?|orders?)\b", folded
        ):
            continue
        if not re.search(r"\b(?:within|by|before|due|on|in)\b", folded):
            continue
        if not re.search(
            r"\b(?:tomorrow|tonight|next\s+\w+|(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|a|an)"
            r"\s+(?:minutes?|hours?|days?|weeks?|fortnights?|months?|years?)|\w+\s+\d{1,2}(?:st|nd|rd|th)?)\b", folded
        ):
            continue
        tokens = _tokens(clause)
        if not any(tokens & _tokens(" ".join(str(task.get(key, "")) for key in ("name", "description")))
                   for task in dated):
            missing.append(clause.strip())
    return missing


def _dated(task: dict) -> bool:
    due = task.get("due_elapsed_minutes", -1)
    return isinstance(due, int) and not isinstance(due, bool) and due >= 0


def _tokens(text: str) -> set[str]:
    # Basic inflections suffice for conservative related-subject matching.
    ignored = {"this", "that", "with", "from", "your", "their", "within", "before", "after",
               "have", "will", "must", "into", "them", "days", "weeks", "fortnights", "minutes"}
    return {word.rstrip("s") for word in re.findall(r"[a-z]{4,}", text.casefold()) if word not in ignored}
