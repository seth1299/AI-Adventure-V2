# Time-sensitive events

The main **Time-Sensitive Events** tab combines durable timed processes, active
task deadlines and application-managed crafting processes. Gardening, incubation,
construction, recovery and deliveries can be tracked independently of quests.

`TimedEventUpsertedEvent` creates or updates a record. New records require a name
and a finalized known location. The database assigns `event_id`; subsequent updates
can use it, or reuse the same name and location. Separate plants/batches have
distinct names; the same plant at different locations is a different record.
Related locations retain their stable location identity across renaming.

Fields include quantity, quantity_unit, quantity_is_estimate, category, current_state,
next_state, notes, is_estimate (timing) and lifecycle (active/completed/cancelled). A timer
uses `ready_at_minute` (absolute in-game minute, -1 for unknown) **or**
`duration_minutes` (nonnegative time from application), never both. Omitted fields
remain unchanged. A notes update must not restart a growth timer.

Remaining time and the effective state are derived from the in-game clock, so
waiting, travel, reloading, and custom calendar dates stay consistent. At the
milestone the record displays next_state, e.g. Mature, but remains active. Harvest
or resolution requires a subsequent update. Reaching a timer does not award items
or rewards; those remain authoritative inventory/mechanics events. Estimates are
marked with `~`; unknown times are shown as Unknown. Calendar retains its Month
and Year Overview views, including dated milestones for these processes.

New Game and story prompts require separate records for named plants or processes
established by player-authored location names/descriptions and other setup prose.
New Game's entities phase requires a `timed_events` array (empty when no processes
are established); the parser turns those records into the same authoritative
events used during play. Initial records use absolute `ready_at_minute` against
the approved starting calendar. Scoped repairs retain location prose and clock
context. Invalid generated timer changes roll back their surrounding world/turn
commit instead of leaving narration with no corresponding durable state.
Explicit counts are preserved. For unstated counts, Gemini infers a modest,
contextually reasonable positive quantity and unit, marks quantity_is_estimate=true,
and explains the assumption in notes. These counts display with `~` and remain stable
across updates until new evidence changes them. Count estimates are independent of
timing estimates. Quantity -1 remains available only when a reasonable estimate is
impossible; it displays as Unknown.
Gemini is responsible for interpretation, stage/estimate selection, and narrative
updates; the application handles storage, identity, countdowns and milestone state.
Crafting uses its existing stage engine and is displayed without duplicating its
timers into the new table. Active crafting stages show unknown remaining time
until a passive stage has a deadline.

Records participate in the existing save transactions and message snapshots.
This change does not backfill crops or infer new records in existing saves.
