# Conversation memory

SQLite `history_entries` stores the full conversation. Each row retains its own
message ID. A `turn_id` connects a submitted player action to its narrator response.
The normal story context includes the latest eight history entries.

Optional, multiple subject references live in `history_subjects`. Each reference
is a `(subject_type, subject_id)` pair: currently `npc` with the saved NPC ID or
`item` with the item catalog ID. Item catalog identity outlives inventory ownership.
The subject lookup index supports retrieving recent messages without scanning the
conversation text. The foreign key removes links when their history row is deleted.

New live conversation messages are tagged by unambiguous complete NPC names,
display names, item names, explicit IDs, and NPC speaker IDs. At story commit,
subjects are shared across the player/narrator exchange, after events have created
any new entities. Tags and narration commit in the same repository transaction.
Snapshots include the subject table, so regeneration restores the original links.
Callers may also pass explicit `subjects` to `append_history`.

For a new action, named subjects take priority. Simple dialogue follow-ups without
an explicit subject can inherit subjects from the preceding live narrator message.
Merely being in the same location does not retrieve every resident's conversation.
Matching is deliberately conservative: partial names, synonyms, implicit references
outside simple dialogue follow-ups, and ambiguous aliases may not match. Subjects
mark relevance, not who witnessed or learned every fact in a message.

`StoryTurnService.build_context_packet` adds `subject_history` when matching older
messages exist. Limits are three subjects, four older messages per subject, six
messages total, 1,200 characters per excerpt, and 6,000 content characters total.
Selection balances subjects, deduplicates by history row ID, and emits chronological
excerpts. It excludes messages already in the recent-history window, hidden rows,
out-of-game chat, and non-conversation logs. Truncation is explicitly marked.
The prompt tells Gemini to treat recalled messages as past interactions, respect
NPC knowledge boundaries, and prefer current authoritative state over old text.
No additional model call or embedding service is used.

Existing untagged messages are not backfilled. This improves recall for future
exchanges; it is bounded retrieval, not a complete long-term narrative summary.

## Suggested action buttons

Story and opening responses can supply `suggested_actions` as `{label, prompt}`
objects. The parser retains labels for existing narration formatting and a separate
label-to-prompt map. Guards preserve prompts only for surviving options. A string
option still receives a simple composed fallback. Neither field guarantees a
successful outcome or pre-applies events.

History stores the final label/prompt pairs in `suggested_actions_json`. First- and
second-person player submissions use "I will"; third-person submissions use the
character's name plus "will". The current saved preference is also applied at click
time. The latest live narrator response supplies up to four buttons above the text
input. Hovering shows the composed prompt; clicking fills and focuses the editable
player input without sending it. Submit or Enter sends the edited draft through
the ordinary player-action path, including check planning, persistence, and subject
tagging. Stale, hidden, out-of-game, pending, and still-revealing choices cannot
replace a draft.
The hidden prompts are omitted from recent-history context to avoid repeating them.
