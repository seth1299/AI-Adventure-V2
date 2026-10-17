"""Content-free Gemini metrics, scoped to a complete synchronous operation."""

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import json
import logging
from pathlib import Path
import threading
import time
from uuid import uuid4

from ai_adventure.app.app_paths import AppPaths


LOGGER = logging.getLogger(__name__)
TOKEN_FIELDS = (
    "prompt_token_count", "candidates_token_count", "total_token_count",
    "thoughts_token_count", "cached_content_token_count", "tool_use_prompt_token_count",
)
CURRENT_BENCHMARK_MESSAGE_ID: ContextVar[str | None] = ContextVar(
    "gemini_benchmark_message_id", default=None
)
_BENCHMARK_LOG_LOCK = threading.Lock()

# Standard paid Gemini API text rates, USD per million tokens, last reviewed
# 2026-10-16. Unknown models deliberately have no cost estimate.
_GEMINI_STANDARD_RATES = {
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50, "cached_input": None},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50, "cached_input": 0.025},
    "gemini-3.6-flash": {"input": 0.75, "output": 3.75, "cached_input": 0.075},
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75, "cached_input": 0.075},
}
_GEMINI_IMAGE_RATES = {
    "gemini-3.1-flash-lite-image": {"input": 0.25, "output": 1.50, "image": 30.0, "image_1k_usd": 0.0336},
    "gemini-3.1-flash-image": {"input": 0.50, "output": 3.00, "image": 60.0, "image_1k_usd": 0.067},
    "gemini-3-pro-image": {"input": 2.00, "output": 12.00, "image": 120.0, "image_1k_usd": 0.134},
}


@dataclass
class RequestMetrics:
    operation: str
    operation_id: str = field(default_factory=lambda: uuid4().hex)
    message_id: str | None = None
    message_type: str = "Other AI Operation"
    request_count: int = 0
    retry_count: int = 0
    repair_count: int = 0
    degraded_count: int = 0
    usage_missing_count: int = 0
    tokens: dict[str, int | None] = field(default_factory=lambda: {key: None for key in TOKEN_FIELDS})
    models: list[str] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)


CURRENT_METRICS: ContextVar[RequestMetrics | None] = ContextVar(
    "gemini_request_metrics", default=None
)
CURRENT_BENCHMARK_MESSAGE_TYPE: ContextVar[str | None] = ContextVar(
    "gemini_benchmark_message_type", default=None
)


def message_type_for_operation(operation: str) -> str:
    """Map internal provider operations to stable, player-facing categories."""
    if operation == "plan_story_d20_tests":
        return "D20 Tests"
    if operation == "generate_story_response":
        return "Normal Messages"
    if operation.startswith("generate_new_game_world"):
        return "New Game Setup"
    if operation == "generate":
        return "Image Generation"
    return "Other AI Operation"


def measured_operation(function):
    """Log aggregate cost and time on success and failure, without response text."""
    @wraps(function)
    def measured(*args, **kwargs):
        if CURRENT_METRICS.get() is not None:
            return function(*args, **kwargs)
        metrics = RequestMetrics(
            function.__name__,
            message_id=CURRENT_BENCHMARK_MESSAGE_ID.get(),
            message_type=(CURRENT_BENCHMARK_MESSAGE_TYPE.get()
                          or message_type_for_operation(function.__name__)),
        )
        token = CURRENT_METRICS.set(metrics)
        started = time.perf_counter()
        succeeded = False
        try:
            result = function(*args, **kwargs)
            succeeded = True
            return result
        finally:
            try:
                finished = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                benchmark_record = _build_benchmark_record(
                    metrics, finished_at=finished, latency_ms=latency_ms, succeeded=succeeded,
                )
                _append_benchmark_record(benchmark_record)
                LOGGER.info("Gemini operation metrics: %s", json.dumps({
                    "operation": metrics.operation,
                    "operation_id": metrics.operation_id,
                    "request_count": metrics.request_count,
                    "retry_count": metrics.retry_count,
                    "repair_count": metrics.repair_count,
                    "degraded_count": metrics.degraded_count,
                    "usage_missing_count": metrics.usage_missing_count,
                    "tokens": metrics.tokens,
                    "latency_ms": latency_ms,
                    "succeeded": succeeded,
                }, sort_keys=True))
            finally:
                CURRENT_METRICS.reset(token)
    return measured


def record_attempt(*, label, model, attempt, started, response=None, degraded=False, details=None):
    """Record SDK-reported tokens; missing usage is unknown, never zero."""
    metrics = CURRENT_METRICS.get()
    metadata = getattr(response, "usage_metadata", None)
    usage = {
        key: (metadata.get(key) if isinstance(metadata, dict) else getattr(metadata, key, None))
        for key in TOKEN_FIELDS
    }
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    if metrics is not None:
        metrics.request_count += 1
        metrics.retry_count += int(attempt > 1)
        metrics.repair_count += int(attempt == 1 and "repair" in label)
        metrics.retry_count += int(attempt == 1 and "quality retry" in label)
        metrics.degraded_count += int(degraded)
        metrics.usage_missing_count += int(metadata is None)
        if model not in metrics.models:
            metrics.models.append(model)
        metrics.attempts.append({
            "request_label": label,
            "provider": "google_gemini",
            "model": model,
            "attempt": attempt,
            "latency_ms": latency_ms,
            "succeeded": response is not None,
            "degraded": degraded,
            **(details or {}),
            **usage,
        })
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                metrics.tokens[key] = (metrics.tokens[key] or 0) + value
    LOGGER.info("Gemini request metrics: %s", json.dumps({
        "operation_id": metrics.operation_id if metrics else None,
        "request_label": label, "model": model, "attempt": attempt,
        "latency_ms": latency_ms,
        "succeeded": response is not None, "degraded": degraded, **usage,
    }, sort_keys=True))


@contextmanager
def benchmark_message(message_id, message_type=None):
    """Attach a persisted ID and optional category to worker-thread operations."""
    token = CURRENT_BENCHMARK_MESSAGE_ID.set(message_id)
    type_token = CURRENT_BENCHMARK_MESSAGE_TYPE.set(message_type)
    try:
        yield
    finally:
        CURRENT_BENCHMARK_MESSAGE_TYPE.reset(type_token)
        CURRENT_BENCHMARK_MESSAGE_ID.reset(token)


def _attempt_cost(attempt: dict, *, timestamp: str) -> tuple[float | None, dict | None]:
    model = attempt.get("model")
    if not isinstance(model, str):
        return None, None
    image_rates = _GEMINI_IMAGE_RATES.get(model)
    if image_rates is not None and isinstance(attempt.get("generated_image_count"), int):
        prompt = attempt.get("prompt_token_count")
        if not isinstance(prompt, int):
            return None, None
        count = attempt["generated_image_count"]
        image_size = str(attempt.get("image_size", "1K")).upper()
        if image_size != "1K":
            return None, None
        input_cost = prompt * image_rates["input"] / 1_000_000
        image_cost = count * image_rates["image_1k_usd"]
        return input_cost + image_cost, {
            "currency": "USD",
            "tier": "standard",
            "input_usd_per_million": image_rates["input"],
            "image_usd_per_1k_image": image_rates["image_1k_usd"],
            "effective_date": "2026-10-16",
        }
    rates = dict(_GEMINI_STANDARD_RATES.get(model, {})) or None
    if model in {"gemini-3.6-flash", "gemini-3.8-flash"}:
        if timestamp[:10] >= "2027-01-01":
            rates = {"input": 1.50, "output": 7.50, "cached_input": 0.15}
    prompt = attempt.get("prompt_token_count")
    candidates = attempt.get("candidates_token_count")
    thoughts = attempt.get("thoughts_token_count")
    if rates is None or not isinstance(prompt, int) or not isinstance(candidates, int):
        return None, None
    cached = attempt.get("cached_content_token_count") or 0
    if cached and rates["cached_input"] is None:
        return None, None
    uncached = max(prompt - cached, 0)
    output = candidates + (thoughts if isinstance(thoughts, int) else 0)
    input_cost = uncached * rates["input"] / 1_000_000
    cached_cost = cached * (rates["cached_input"] or 0) / 1_000_000
    output_cost = output * rates["output"] / 1_000_000
    scheduled_rate_model = model in {"gemini-3.6-flash", "gemini-3.8-flash"}
    effective_date = (
        "2027-01-01"
        if scheduled_rate_model and timestamp[:10] >= "2027-01-01"
        else "2026-10-16"
    )
    return input_cost + cached_cost + output_cost, {
        "currency": "USD",
        "tier": "standard",
        "input_usd_per_million": rates["input"],
        "cached_input_usd_per_million": rates["cached_input"],
        "output_usd_per_million": rates["output"],
        "effective_date": effective_date,
    }


def _build_benchmark_record(metrics, *, finished_at, latency_ms, succeeded):
    per_attempt_costs = []
    pricing = None
    for attempt in metrics.attempts:
        cost, applied_pricing = _attempt_cost(attempt, timestamp=finished_at)
        attempt["estimated_cost_usd"] = round(cost, 10) if cost is not None else None
        attempt["pricing"] = applied_pricing
        if applied_pricing is not None:
            pricing = applied_pricing
            per_attempt_costs.append(cost)
    cost_complete = (
        bool(metrics.attempts)
        and len(per_attempt_costs) == len(metrics.attempts)
        and metrics.usage_missing_count == 0
        and all(attempt.get("cost_complete", True) is True for attempt in metrics.attempts)
    )
    known_cost = round(sum(per_attempt_costs), 10) if per_attempt_costs else None
    return {
        "record_type": "gemini_operation",
        "provider": "google_gemini",
        "schema_version": 1,
        "timestamp_utc": finished_at,
        "operation_id": metrics.operation_id,
        "message_id": metrics.message_id,
        "operation": metrics.operation,
        "message_type": metrics.message_type,
        "models": metrics.models,
        "succeeded": succeeded,
        "latency_ms": latency_ms,
        "request_count": metrics.request_count,
        "retry_count": metrics.retry_count,
        "repair_count": metrics.repair_count,
        "degraded_count": metrics.degraded_count,
        "usage_missing_count": metrics.usage_missing_count,
        "tokens": metrics.tokens,
        "attempts": metrics.attempts,
        "pricing": pricing,
        "estimated_cost_usd": known_cost if cost_complete else None,
        "known_attempt_cost_usd": known_cost,
        "cost_complete": cost_complete,
        "cost_basis": "standard Gemini API token and image-unit rates; estimate, excluding account-level fees",
    }


def _append_benchmark_record(record: dict) -> None:
    """Append one complete operation record without ever resetting prior data."""
    try:
        path: Path = AppPaths.create().benchmark_log_file
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        with _BENCHMARK_LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as benchmark_file:
                benchmark_file.write(line + "\n")
                benchmark_file.flush()
    except Exception:
        # Benchmark logging must never turn a valid or failed game request into
        # a different application outcome.
        LOGGER.exception("Could not append Gemini benchmark metrics.")
