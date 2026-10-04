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

The story API schema constrains the response envelope, enabled event type names,
and object payloads. The complete per-event payload schema is included in the
application system instruction, avoiding compilation of a large nested event union
by Gemini. The detailed schema builder remains available for prompt guidance and
validation. After repairs, the service validates the full request-specific local
schema before permissive parsing or event normalization.
Staged new games validate each phase and the merged result. Invalid responses raise
`GeminiRequestError`; they do not produce a result for committing generated changes.
Standalone parsers retain their normalization behavior for imported/manual data.

A 400 INVALID_ARGUMENT with a configured schema can trigger one schema-free retry.
This is a diagnostic fallback: a generic 400 alone does not prove the schema was the
cause. The retry is logged as degraded, keeps JSON MIME mode and system rules, and
receives the original response schema in its system instruction. Responses with
locally enforced payloads or degraded responses get one complete regeneration when
contract validation fails. Regeneration keeps the authoritative request context and
receives bounded validation feedback, without replaying the rejected response text.
There are at most two transport attempts plus one contract repair per transport call.
Repairs must pass the same validation; missing or malformed payloads are never
silently accepted. Story requests use the full local contract for these checks.
They must also pass full local validation at the service boundary. Exhausted new-game
quality retries fail validation rather than saving the most complete invalid candidate.
This follows Google's guidance to [validate structured output in the application](https://ai.google.dev/gemini-api/docs/structured-output).

## Metrics

Skill-check filtering is conservative: only complete, clearly routine commands
qualify; compound goals and check reasons describing danger preserve the planner's
checks. Inflected theft and stealth actions are recognized. Planner rejections and
story policy removals are saved in `mechanical_events` with status `dropped`, their
original payload, turn message ID, filter stage, and reason. These are audit records
and never execute checks or award XP. Applied/skipped/failed application outcomes
retain their existing statuses. Story audit writes roll back with a failed commit.
Run `tests.test_skill_check_audit` for the pouch-lifting regression and audit behavior.

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
- Degraded count records attempts made without the rejected server schema,
  including contract repairs in JSON MIME mode. Compact story envelope requests
  retain server schema enforcement and do not count as degraded.
  Success/failure is recorded for both the SDK call and complete operation.

Operation metrics are isolated by execution context so concurrent workers do not
mix counts. These metrics do not include generated audio or image API usage.
Raw prompts/responses remain restricted to the existing playtesting debug logging.
Contract failures log bounded field paths and validation reasons. Event union
diagnostics select the branch matching the proposed type rather than reporting
unrelated event payload requirements. Request diagnostics include server-schema
character count without logging prompt or response text.

## Container consistency

Uninitialized contents are distinct from a known empty container. The first valid
opening must supply a complete manifest; an initialized manifest cannot be replaced.
Private container authority supplies saved contents to the GM even while ordinary
inventory rows hide them. Taking selects stored records and currency, including
nested containers, without separate inventory or currency awards.

Container proposals are simulated before returning narration. Invalid proposals,
and obvious unsupported coin claims, trigger at most two complete response repairs.
A valid response needs no repair call. Failed repairs stop the turn; commit checks
the current saved state again and rolls back narration and rewards if application
fails. Repairs occur outside the database transaction and count in request metrics.
These checks enforce stored transfers; they do not prove all natural-language prose
accurate. Run `tests.test_container_consistency` alongside the suites above for
manifest initialization, selective transfers, retries, duplicate prevention, and
transaction rollback with a real temporary SQLite save.
