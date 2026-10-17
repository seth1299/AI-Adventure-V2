"""Summarize the append-only Gemini benchmark JSONL log.

Examples:
    python benchmark_report.py
    python benchmark_report.py --since 2026-10-01
    python benchmark_report.py --json --log C:/path/to/gemini_benchmark.jsonl

Cost fields are estimates at the standard Gemini API rates stored with each
operation. Unknown rates and operations with missing usage are reported as
incomplete rather than silently counted as zero.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import statistics
from typing import Any

from ai_adventure.ai.request_metrics import message_type_for_operation
from ai_adventure.app.app_paths import AppPaths


def read_records(path: Path, since: date | None = None) -> tuple[list[dict[str, Any]], int]:
    """Read valid operation rows, optionally keeping rows on/after a UTC date."""
    records: list[dict[str, Any]] = []
    malformed = 0
    if not path.exists():
        return records, malformed
    cutoff = datetime.combine(since, time.min, tzinfo=timezone.utc) if since else None
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(record, dict) or record.get("record_type") != "gemini_operation":
                malformed += 1
                continue
            try:
                timestamp = datetime.fromisoformat(str(record["timestamp_utc"]).replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                malformed += 1
                continue
            if cutoff is None or timestamp.astimezone(timezone.utc) >= cutoff:
                records.append(record)
    return records, malformed


def _sum_known(values: list[int | None]) -> int | None:
    present = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return sum(present) if present else None


def build_report(records: list[dict[str, Any]], *, since: date | None = None,
                 malformed_records: int = 0) -> dict[str, Any]:
    """Calculate operation and player-message summaries from stored records."""
    successful = [record for record in records if record.get("succeeded") is True]
    successful_story = [record for record in successful
                        if record.get("operation") == "generate_story_response"]
    latencies = [record["latency_ms"] for record in successful_story
                 if isinstance(record.get("latency_ms"), (int, float))]
    known_costs = [record["known_attempt_cost_usd"] for record in records
                   if isinstance(record.get("known_attempt_cost_usd"), (int, float))]
    complete_costs = [record["estimated_cost_usd"] for record in records
                      if record.get("cost_complete") is True
                      and isinstance(record.get("estimated_cost_usd"), (int, float))]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        message_id = record.get("message_id")
        # Older or manually generated rows without a message ID remain useful
        # as one-operation groups, but cannot be treated as a complete turn.
        group_id = str(message_id) if message_id else f"operation:{record.get('operation_id', len(grouped))}"
        grouped[group_id].append(record)

    message_costs: list[float] = []
    successful_message_count = 0
    incomplete_message_count = 0
    for message_records in grouped.values():
        has_successful_story = any(
            record.get("operation") == "generate_story_response" and record.get("succeeded") is True
            for record in message_records
        )
        if not has_successful_story:
            continue
        successful_message_count += 1
        if all(record.get("cost_complete") is True
               and isinstance(record.get("estimated_cost_usd"), (int, float))
               for record in message_records):
            message_costs.append(sum(float(record["estimated_cost_usd"]) for record in message_records))
        else:
            incomplete_message_count += 1

    model_totals: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "request_count": 0, "prompt_tokens": 0, "candidate_tokens": 0,
        "thought_tokens": 0, "estimated_cost_usd": 0.0,
        "known_cost_request_count": 0, "unknown_cost_request_count": 0,
    })
    type_totals: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "operation_count": 0, "successful_operation_count": 0,
        "request_count": 0, "estimated_cost_usd": 0.0,
        "known_attempt_cost_usd": 0.0,
        "complete_cost_operation_count": 0, "incomplete_cost_operation_count": 0,
        "prompt_tokens": 0, "candidate_tokens": 0, "thought_tokens": 0,
        "generated_image_count": 0,
    })
    for record in records:
        message_type = str(record.get("message_type") or message_type_for_operation(
            str(record.get("operation") or "")
        ))
        type_row = type_totals[message_type]
        type_row["operation_count"] += 1
        type_row["successful_operation_count"] += int(record.get("succeeded") is True)
        type_row["request_count"] += int(record.get("request_count") or 0)
        known_attempt_cost = record.get("known_attempt_cost_usd")
        if isinstance(known_attempt_cost, (int, float)):
            type_row["known_attempt_cost_usd"] += float(known_attempt_cost)
        if record.get("cost_complete") is True and isinstance(record.get("estimated_cost_usd"), (int, float)):
            type_row["estimated_cost_usd"] += float(record["estimated_cost_usd"])
            type_row["complete_cost_operation_count"] += 1
        else:
            type_row["incomplete_cost_operation_count"] += 1
        tokens = record.get("tokens") if isinstance(record.get("tokens"), dict) else {}
        for token_key, total_key in (
            ("prompt_token_count", "prompt_tokens"),
            ("candidates_token_count", "candidate_tokens"),
            ("thoughts_token_count", "thought_tokens"),
        ):
            if tokens != None:
                token_value = tokens.get(token_key)
            else:
                token_value = -999999
            if isinstance(token_value, int) and not isinstance(token_value, bool):
                type_row[total_key] += token_value
        for attempt in record.get("attempts", []):
            if not isinstance(attempt, dict):
                continue
            image_count = attempt.get("generated_image_count")
            if isinstance(image_count, int) and not isinstance(image_count, bool):
                type_row["generated_image_count"] += image_count
            model = str(attempt.get("model") or "unknown")
            row = model_totals[model]
            row["request_count"] += 1
            for source_key, target_key in (
                ("prompt_token_count", "prompt_tokens"),
                ("candidates_token_count", "candidate_tokens"),
                ("thoughts_token_count", "thought_tokens"),
            ):
                value = attempt.get(source_key)
                if isinstance(value, int) and not isinstance(value, bool):
                    row[target_key] += value
            cost = attempt.get("estimated_cost_usd")
            if isinstance(cost, (int, float)):
                row["estimated_cost_usd"] += float(cost)
                row["known_cost_request_count"] += 1
            else:
                row["unknown_cost_request_count"] += 1
    for row in model_totals.values():
        row["estimated_cost_usd"] = round(row["estimated_cost_usd"], 10)
    for row in type_totals.values():
        row["estimated_cost_usd"] = round(row["estimated_cost_usd"], 10)
        row["known_attempt_cost_usd"] = round(row["known_attempt_cost_usd"], 10)
        row["average_cost_per_complete_operation_usd"] = (
            round(row["estimated_cost_usd"] / row["complete_cost_operation_count"], 10)
            if row["complete_cost_operation_count"] else None
        )

    token_keys = ("prompt_token_count", "candidates_token_count", "thoughts_token_count",
                  "cached_content_token_count", "tool_use_prompt_token_count", "total_token_count")
    token_totals = {
        key: _sum_known([
            (record.get("tokens") or {}).get(key) for record in records
            if isinstance(record.get("tokens"), dict)
        ])
        for key in token_keys
    }
    return {
        "period_start_utc": since.isoformat() if since else None,
        "period_end_utc": datetime.now(timezone.utc).date().isoformat(),
        "operation_count": len(records),
        "successful_operation_count": len(successful),
        "failed_operation_count": len(records) - len(successful),
        "successful_story_message_count": successful_message_count,
        "average_response_time_ms": round(statistics.fmean(latencies), 2) if latencies else None,
        "average_cost_per_message_usd": round(statistics.fmean(message_costs), 10) if message_costs else None,
        "complete_cost_message_count": len(message_costs),
        "incomplete_cost_message_count": incomplete_message_count,
        "average_cost_per_operation_usd": round(statistics.fmean(complete_costs), 10) if complete_costs else None,
        "total_estimated_cost_usd": round(sum(known_costs), 10) if known_costs else 0.0,
        "incomplete_cost_operation_count": sum(record.get("cost_complete") is not True for record in records),
        "total_tokens": token_totals,
        "message_type_breakdown": dict(sorted(type_totals.items())),
        "model_breakdown": dict(sorted(model_totals.items())),
        "malformed_record_count": malformed_records,
        "notes": [
            "Response time averages successful generate_story_response operations.",
            "Per-message cost combines planning and narration operations sharing the persisted message ID.",
            "Message-type costs are grouped by operation category; image generation is reported separately because generated assets are not tied to one player message.",
            "Total estimated cost sums known attempt estimates; incomplete operations may add unknown cost.",
            "Costs use the standard paid Gemini API rates recorded with each attempt.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=date.fromisoformat, help="Include UTC records on/after YYYY-MM-DD.")
    parser.add_argument("--log", type=Path, default=AppPaths.create().benchmark_log_file,
                        help="Path to gemini_benchmark.jsonl.")
    parser.add_argument("--json", action="store_true", help="Print the complete report as JSON.")
    args = parser.parse_args()
    records, malformed = read_records(args.log, args.since)
    report = build_report(records, since=args.since, malformed_records=malformed)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print(f"Benchmark log: {args.log}")
    print(f"Period start (UTC): {report['period_start_utc'] or 'all available records'}")
    print(f"Operations: {report['operation_count']} ({report['successful_operation_count']} succeeded, "
          f"{report['failed_operation_count']} failed)")
    print(f"Successful story messages: {report['successful_story_message_count']}")
    print(f"Average response time: {_display(report['average_response_time_ms'], ' ms')}")
    print(f"Average cost per complete message: {_display(report['average_cost_per_message_usd'], ' USD')}")
    print(f"Average cost per complete operation: {_display(report['average_cost_per_operation_usd'], ' USD')}")
    print(f"Total known estimated cost: {report['total_estimated_cost_usd']:.10f} USD "
          f"({report['incomplete_cost_operation_count']} operations have incomplete costs)")
    print(f"Token totals: {json.dumps(report['total_tokens'], sort_keys=True)}")
    print("Models:")
    for model, stats in report["model_breakdown"].items():
        print(f"  {model}: {stats['request_count']} requests, {stats['prompt_tokens']} input tokens, "
              f"{stats['candidate_tokens']} candidate tokens, {stats['thought_tokens']} thought tokens, "
              f"{stats['estimated_cost_usd']:.10f} USD known estimate")
    print("Message types:")
    for message_type, stats in report["message_type_breakdown"].items():
        average = _display(stats["average_cost_per_complete_operation_usd"], " USD")
        print(f"  {message_type}: {stats['operation_count']} operations, "
              f"{stats['known_attempt_cost_usd']:.10f} USD known attempt cost, "
              f"{average} average per complete operation, {stats['generated_image_count']} images")
    if malformed:
        print(f"Malformed records skipped: {malformed}")
    return 0


def _display(value: Any, suffix: str) -> str:
    return "unknown" if value is None else f"{value}{suffix}"


if __name__ == "__main__":
    raise SystemExit(main())
