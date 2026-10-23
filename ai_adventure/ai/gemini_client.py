"""Shared configuration for locally managed Gemini Interactions requests."""

from typing import Any


def create_gemini_client(api_key: str, *, http_options: dict[str, Any] | None = None) -> Any:
    from google import genai

    options = dict(http_options or {})
    options["headers"] = {**options.get("headers", {}), "Api-Revision": "2026-05-20"}
    options["retry_options"] = {"attempts": 1}
    client = genai.Client(api_key=api_key, http_options=options)
    # In google-genai 2.30.1 the parent client's attempts=0 becomes 1, and
    # Interactions interprets that value as retries, rather than attempts.
    # Configure its resource directly so only our measured retry loop retries.
    client.interactions.sdk_configuration.retry_config.max_retries = 0
    return client
