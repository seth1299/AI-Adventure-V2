# New Game repair routing

Initial generation uses world, entities/mechanics, and opening-prose phases.
Phases reduce the initial request's scope, but an entities phase still includes
many unrelated domains. Contract repairs therefore have a finer boundary.

Local validator errors identify top-level response fields by JSON paths, such as
`$.bestiary[0].name` or `$.known_crafting_recipes[0].stages[1].work_amount`.
`ai/new_game_repair_context.py` explicitly maps supported fields to their needed
setup inputs, previously generated context, and applicable requirements.

For a supported field, Python rebuilds the prompt from that dependency whitelist.
It sends the rejected section, current validation errors, and related facts only.
The repair schema permits only the affected top-level fields. For example, a
Bestiary repair receives relevant world/locations/magic context, but no character
biography or starter inventory. Recipe repairs may need known materials, skill
information, and tool names. Several invalid supported fields use the union of
their dependencies. The routing map is the place to add future repair domains.

Each response replaces complete affected sections; this is not a record-level
JSON patch. Valid records and their IDs within a rejected section are supplied
and the model is instructed to preserve them. Other sections are retained in
Python and cannot be overwritten by a partial repair. Python validates both the
partial response and the merged complete candidate. Targeted creative/setup
checks still run, and their output is revalidated before acceptance.

Malformed initial JSON, root-level errors, and unsupported paths fall back to
full-phase regeneration because the damaged section cannot be routed safely.
Malformed partial repairs retain their original section scope and candidate.
Both paths share the same budget: one initial attempt plus four repairs, with
the latest failure reason included each time. Approved prior phases are retained.
No generated world is committed until the whole result passes validation.

Logs record repair fields and prompt character counts compared with the original
phase prompt. Transport retries remain separate from this response-repair budget.

New games retain `adventure.new_game_prompt.txt` beside their save database until
the opening is delivered. This UTF-8 JSON text file contains the setup packet,
exact phase prompts, model/configuration/contracts, and approved phase responses;
it contains no API key. Writes replace the file atomically. A failed opening
offers **Retry Opening Message**, including after reopening the save. Retries
read this file, reuse approved phases, and resend the unfinished phase's saved
prompt without rebuilding it. Response repairs still use the normal validation
budget. The generated world and opening-ready marker are committed together.

Image preparation starts only after a validated, nonempty opening is committed.
The source dialog collects checkbox choices until **Next**; uploads are retained,
unchecked subjects are skipped, and only checked subjects enter the paid queue.
**Create all images for me** asks for confirmation about time and API usage before
selecting the remaining subjects. Automatic scans cannot add work during source
selection or while an opening is pending. Text display and narration wait until
the selected images finish or the player skips failed images. Only then is the
opening revealed and the temporary prompt file deleted.
