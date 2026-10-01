# Gemini contracts and scenario evaluations

Run from the repository root in PowerShell:

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:QT_QPA_PLATFORM = 'offscreen'
& .\.venv\Scripts\python.exe -m unittest tests.test_gemini_contracts tests.test_gemini_service tests.test_context_builder tests.test_new_game_service
```

These evaluations use recorded responses and fake clients. They do not contact
Gemini, spend tokens, or prove that a model reliably writes good prose. The versioned
`tests/fixtures/gemini_scenarios.json` dataset exercises accepted and rejected
responses through the real service, contract validation, and runtime policies.
It covers routine turns, inventory receipts, locked containers, failed checks,
out-of-game authority, weather consistency, event allowlists, missing fields,
invalid types, unexpected fields, oversized action lists, malformed JSON,
duplicate keys, and non-finite numbers. Each scenario needs a unique ID, context,
recorded response, and expected outcomes. Add a regression scenario when fixing a
model-response failure. Keep failed examples rather than replacing all cases with
successful responses.

The companion service tests cover new-game exact fields, staged generation,
planning, audio boundaries, crafting, spell authorization, and repair policies.
Run this suite before changing prompts, schemas, repairs, or the text-model catalog.
For a model or prompt change, separately collect representative live responses
through the application, inspect prose and state proposals, and add anonymized
examples to the dataset. Never check API keys or private playthroughs into fixtures.

## Request behavior

The transport moves the leading application-authored identity and behavioral
sections into `system_instruction`, alongside shared application authority.
Request-specific context, UI mode, preferences, examples, and task remain in
`contents`. Embedded context tags are never promoted into system instructions.
System instructions reinforce authority; Python still enforces it.

The API receives a compact schema. After repairs, the service validates the full
request-specific local schema before permissive parsing or event normalization.
Staged new games validate each phase and the merged result. Invalid responses raise
`GeminiRequestError`; they do not produce a result for committing generated changes.
Standalone parsers retain their normalization behavior for imported/manual data.

A schema rejection can trigger one schema-free retry. This is logged as degraded,
keeps JSON MIME mode and system rules, and validates against the original schema.
It must also pass full local validation at the service boundary. Exhausted new-game
quality retries fail validation rather than saving the most complete invalid candidate.
This follows Google's guidance to [validate structured output in the application](https://ai.google.dev/gemini-api/docs/structured-output).

## Metrics

Normal application logs include content-free request and operation metrics:

- Operation ID links individual API attempts to a complete story/planning/new-game operation.
- Request count includes failed API attempts. Retry count includes transport retries
  and new-game quality regenerations; repair count counts repair requests, including
  failed ones, without counting their transport retries twice.
- Latency uses a monotonic clock. Per-attempt latency measures the SDK call;
  operation latency also includes backoff, repairs, parsing, and validation.
- Prompt, candidate, thinking, cached, and total token counts come from SDK
  `usage_metadata`. Missing counts are `null`, never estimates or zero. Aggregated
  counts are totals of reported values; `usage_missing_count` identifies attempts
  with no metadata, including failures whose billing is unknown.
- Degraded count records attempts made without the rejected server schema.
  Success/failure is recorded for both the SDK call and complete operation.

Operation metrics are isolated by execution context so concurrent workers do not
mix counts. These metrics do not include generated audio or image API usage.
Raw prompts/responses remain restricted to the existing playtesting debug logging.
