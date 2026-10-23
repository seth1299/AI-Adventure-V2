# Gemini Interactions integration

The app uses `google-genai==2.30.1` and `client.interactions.create()` for
story turns, D20 planning, New Game phases and repairs, and generated images.
Install the pinned dependencies with `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
The standard and lightweight packaging recipes collect the SDK's Interactions modules.

## Conversation and request behavior

Requests set `store=False`. SQLite saves and the existing context builder remain
authoritative: each request sends its current context, system rules, and settings.
No interaction IDs are used to chain conversations, so loading, branching, or
repairing a save does not depend on Google's retained conversation state.
Interaction IDs and status are recorded in content-free benchmark attempts.

Structured JSON uses `response_format` with a text MIME type and JSON schema.
Thinking level, token limits, and disabled tool choice use `generation_config`.
Safety settings translate the existing UI choices to Interactions category types
and lowercase thresholds. Story requests expose no tools and allow only text.
Only completed interactions can be accepted; unfinished responses cannot commit
state even if their partial output happens to contain valid JSON.

The existing compact story schema, full local validation, schema fallback,
and bounded repair behavior remain in place. Safety-blocked requests do not
trigger a schema fallback. New Game repairs remove the hard token cap while
preserving other settings. Images use an image response format with aspect ratio
and size controls; the SDK's `output_image` supplies base64 data and MIME type.
Speech continues to use the local narration engines.

## Models and accounting

Defaults remain `gemini-3.5-flash-lite` for text and
`gemini-3.1-flash-lite-image` for images. The picker adds Gemini 3.8 Flash and
Nano Banana 2.1 and omits Gemini 2.5 choices. Existing Gemini 2.5 text selections
normalize to Gemini 3.8 Flash; older Nano Banana selections normalize to
`gemini-nano-banana-2.1`. This uses current recommended choices without changing
the default models. Google's model documentation remains authoritative on
availability and deprecation dates.

The benchmark log retains its existing field names and categories. Values now
come from `interaction.usage`; missing values remain unknown. Output and thought
tokens remain separate, including in cost estimates. New models without a
reviewed price remain marked as having incomplete cost information.

The shared client factory owns API revision and SDK retry configuration. In
2.30.1, the parent client coerces `retry_options.attempts=0` to `1`, and
Interactions treats that number as retries. The factory sets the resource's
retry count to zero so only application retries occur and are measured. Recheck
this SDK-specific behavior when changing the dependency pin.

## Verification

`tests/AI_Tests/test_gemini_interactions.py` runs the installed SDK through an
in-memory HTTP transport. It checks serialized requests, response helpers,
errors and retry limits, schema fallback, completion status, images, and usage.
Existing service, repair, save, metrics, and UI tests cover the application flow.
These tests make no paid requests. Live provider behavior and a packaged
executable require separate validation.

References: [Interactions overview](https://ai.google.dev/gemini-api/docs/interactions-overview),
[migration guide](https://ai.google.dev/gemini-api/docs/migrate-to-interactions),
[model catalog](https://ai.google.dev/gemini-api/docs/models).
