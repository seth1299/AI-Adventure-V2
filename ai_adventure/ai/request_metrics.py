"""Content-free Gemini metrics, scoped to a complete synchronous operation."""

from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from functools import wraps
import json
import logging
import time
from uuid import uuid4


LOGGER = logging.getLogger(__name__)
TOKEN_FIELDS = (
    "prompt_token_count", "candidates_token_count", "total_token_count",
    "thoughts_token_count", "cached_content_token_count",
)


@dataclass
class RequestMetrics:
    operation: str
    operation_id: str = field(default_factory=lambda: uuid4().hex)
    request_count: int = 0
    retry_count: int = 0
    repair_count: int = 0
    degraded_count: int = 0
    usage_missing_count: int = 0
    tokens: dict[str, int | None] = field(default_factory=lambda: {key: None for key in TOKEN_FIELDS})


CURRENT_METRICS = ContextVar("gemini_request_metrics", default=None)


def measured_operation(function):
    """Log aggregate cost and time on success and failure, without response text."""
    @wraps(function)
    def measured(*args, **kwargs):
        if CURRENT_METRICS.get() is not None:
            return function(*args, **kwargs)
        metrics = RequestMetrics(function.__name__)
        token = CURRENT_METRICS.set(metrics)
        started = time.perf_counter()
        succeeded = False
        try:
            result = function(*args, **kwargs)
            succeeded = True
            return result
        finally:
            try:
                LOGGER.info("Gemini operation metrics: %s", json.dumps({
                    **asdict(metrics), "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "succeeded": succeeded,
                }, sort_keys=True))
            finally:
                CURRENT_METRICS.reset(token)
    return measured


def record_attempt(*, label, model, attempt, started, response=None, degraded=False):
    """Record SDK-reported tokens; missing usage is unknown, never zero."""
    metrics = CURRENT_METRICS.get()
    metadata = getattr(response, "usage_metadata", None)
    usage = {
        key: (metadata.get(key) if isinstance(metadata, dict) else getattr(metadata, key, None))
        for key in TOKEN_FIELDS
    }
    if metrics is not None:
        metrics.request_count += 1
        metrics.retry_count += int(attempt > 1)
        metrics.repair_count += int(attempt == 1 and "repair" in label)
        metrics.retry_count += int(attempt == 1 and "quality retry" in label)
        metrics.degraded_count += int(degraded)
        metrics.usage_missing_count += int(metadata is None)
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                metrics.tokens[key] = (metrics.tokens[key] or 0) + value
    LOGGER.info("Gemini request metrics: %s", json.dumps({
        "operation_id": metrics.operation_id if metrics else None,
        "request_label": label, "model": model, "attempt": attempt,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "succeeded": response is not None, "degraded": degraded, **usage,
    }, sort_keys=True))
