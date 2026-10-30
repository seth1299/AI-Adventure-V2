# NPC continuity

NPC context explicitly maps the repository's canonical `id` into Gemini's
`npc_id` field. Quoted dialogue, questions, acknowledgments and dialogue verbs
can inherit NPC subjects from the most recent visible live-game response. They
also retain explicitly referenced item subjects. Explicitly naming another NPC
or starting a new scene prevents inheriting an unrelated conversation partner.

For existing NPCs, ordinary NpcUpsertedEvent proposals preserve the internal
name and nonblank display name, role, appearance, gender identity, age and
species. New facts, player-facing information and location still update.
Intentional corrections or newly revealed identities require an explicit
`profile_change_reason`; the stable internal name remains unchanged. This
reason is model-provided evidence, not a guarantee against model error.
Preserved proposals and effective values remain in the mechanical event audit.
Manual profile editing through repository APIs is unaffected.

Automatic image scans retain an NPC's existing ready portrait when its file is
present, regardless of rewritten appearance, display name or image style. The
existing asset is linked to later turns without refreshing its priority. Scans
also avoid queuing a second portrait while the first is queued or generating.
Explicit uploads, generation requests and retries remain available. Missing
portrait files can be regenerated. No migration or edits to existing saves are
needed; the current portrait is retained rather than automatically restoring an
older version.
