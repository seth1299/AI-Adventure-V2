# Travel assessments

Known locations persist their own `weather`. StatusUpdatedEvent preserves the
departure site's weather and records weather at the resulting current location.
LocationUpsertedEvent can also update another known location's weather.

Each location also persists `music_filename`. Background assessment selects it
from the available music catalog, including genre/folder descriptions. Existing
fitting tracks are preserved. Safe arrival applies MusicChangedEvent in the same
transaction as the status update; playback uses normal music toggle and volume
settings. With no available destination track, arrival clears the old location's
music. Assessing future destinations does not change current playback.

The Travel tab reads a private directional assessment from `travel.assessment`.
Only an explicit boolean `safe: true`, a matching origin/arrival and current route
conditions, destination weather, and an available mathematical route estimate
permit local travel. The application records the player action, applies a
StatusUpdatedEvent with the estimated minutes, and records a factual arrival in
one transaction. It does not run story generation or a d20 planner for that trip.
Unsafe, unknown or stale routes use the ordinary Gemini story/check workflow.

The background assessor runs on save load and after arrival or changed route
conditions. It receives known locations and their weather, current calendar,
movement mode, player condition, genre and model preferences. It does not receive
GM secrets, notes or conversation history. Its safety output is never displayed
to the player. Destination weather is saved and displayed in Travel.

Provider work runs outside save transactions. Results from an older arrival or
another active save are discarded; incomplete or malformed assessments are
rejected atomically. Failures leave normal travel available and do not trigger
an endless request loop. Background operations appear under `Travel Assessments`
in the structured Gemini benchmark log.
