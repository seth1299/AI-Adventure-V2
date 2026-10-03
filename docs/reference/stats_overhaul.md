# Stats rules version: stats-v1

The Skills screen is now Stats. Character creation uses six attributes with rank-based point buy; Player Level/XP and skill training remain separate. `ai_adventure/stats.py` is the shared source for attribute modifiers, point costs, ranks, health, carrying capacity, and model guidance.

Fighting is narrative. Gemini proposes attribute-based checks, attacks, saves, health changes, and milestone significance. The application rolls fair dice, resolves totals, validates proposals, and persists outcomes. Weapon and Armor categories retain descriptions and held/worn equipment without a combat engine. Physical ammunition still uses real inventory records.

## Persistence and transactions

- `player_stats.py` owns Player XP, banked rewards, purchased skill advances, health, and d20 history.
- `progression_records` deduplicates achievements and health consequences using persistent source IDs, and distinguishes purchased skill advances from training XP.
- Objective achievements use the completed task's exact persistent ID. In a response that completes a task and awards XP, `ActiveTaskCompletedEvent` precedes `PlayerAchievementRecordedEvent`. Milestones retain stable identifiers across retries and retellings.
- `SkillXpAddedEvent.source_id` is optional and identifies a particular skill training award. Without it, messages deduplicate one award per skill per message; calls without a message ID have no fallback deduplication.
- `event_receipts` reuses committed event and story results on retries. Receipts, inventory effects, health, rewards, and narration share the repository transaction and message snapshot.
- Purchased skill advances shift cumulative XP by eight, retaining partial training even when reaching Master. Master skills gain no subsequent training XP or levels.
- Equipping accessible gear moves it into actively carried inventory. Dropping or storing it clears equipment. Carried containers add capacity; grounded containers and vehicles use their own storage rules.
- Supply consumption must reference sufficient, accessible inventory. A contradictory consumption proposal cannot commit its narration or health consequences.

## Compatibility

New saves store `meta.rules_version = stats-v1`. Existing saves with another or missing rules version are checked through a read-only connection and rejected before schema initialization or gameplay loading. Their files are left unchanged. Templates carry the same rules marker; older templates are identified as incompatible. Current templates may remain unfinished until completed in the wizard. There is no legacy conversion.

The retired mechanical engine and its behavior are preserved in [mechanical_combat_archive.md](mechanical_combat_archive.md).

## Validation on October 2, 2026

Contract cleanup validation used the project `.venv` with PySide6 and `QT_QPA_PLATFORM=offscreen`:

- Before cleanup, full discovery passed **653 tests** in 244.787 seconds, but omitted five progression tests defined below the `__main__` guard. That result did not cover those five cases.
- All five tests now belong to `StatsProgressionTests`; the objective fixture uses keyword arguments and the returned task ID. Five additional regressions cover progression transactions, training deduplication, schemas, packaged defaults, and prompt projection.
- Focused validation: **257 tests passed** in 38.990 seconds, covering progression, provider contracts, prompt/context construction, skill auditing, New Game, Stats UI, and repository transactions.
- Full offscreen unittest discovery after cleanup: **663 tests passed** in 246.738 seconds, including the five restored cases and five new regressions.
- Python compilation and `git diff --check` passed. Story prompts transmit one canonical Stats contract while retaining health/progression guidance, authoritative player attributes, and fighting-focus preferences. Packaged references no longer instruct tests to create skills or contain the obsolete combat-started shell.

The earlier overhaul notes reported three native Windows Qt walkthroughs and reviewed widget captures. Those native checks were not rerun during this cleanup; current UI validation was offscreen. Provider contract tests used local responses and mocks; no live Gemini gameplay session was run.
