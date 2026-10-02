# Stats rules version: stats-v1

The Skills screen is now Stats. Character creation uses six attributes with rank-based point buy; Player Level/XP and skill training remain separate. `ai_adventure/stats.py` is the shared source for attribute modifiers, point costs, ranks, health, carrying capacity, and model guidance.

Fighting is narrative. Gemini proposes attribute-based checks, attacks, saves, health changes, and milestone significance. The application rolls fair dice, resolves totals, validates proposals, and persists outcomes. Weapon and Armor categories retain descriptions and held/worn equipment without a combat engine. Physical ammunition still uses real inventory records.

## Persistence and transactions

- `player_stats.py` owns Player XP, banked rewards, purchased skill advances, health, and d20 history.
- `progression_records` deduplicates achievements and health consequences using persistent source IDs, and distinguishes purchased skill advances from training XP.
- `event_receipts` reuses committed event and story results on retries. Receipts, inventory effects, health, rewards, and narration share the repository transaction and message snapshot.
- Purchased skill advances shift cumulative XP by eight, retaining partial training even when reaching Master. Master skills gain no subsequent training XP or levels.
- Equipping accessible gear moves it into actively carried inventory. Dropping or storing it clears equipment. Carried containers add capacity; grounded containers and vehicles use their own storage rules.
- Supply consumption must reference sufficient, accessible inventory. A contradictory consumption proposal cannot commit its narration or health consequences.

## Compatibility

New saves store `meta.rules_version = stats-v1`. Existing saves with another or missing rules version are checked through a read-only connection and rejected before schema initialization or gameplay loading. Their files are left unchanged. Templates carry the same rules marker; older templates are identified as incompatible. Current templates may remain unfinished until completed in the wizard. There is no legacy conversion.

The retired mechanical engine and its behavior are preserved in [mechanical_combat_archive.md](mechanical_combat_archive.md).

## Validation on October 2, 2026

Using the project `.venv` with PySide6:

- Full offscreen unittest discovery: **653 tests passed** in 260.817 seconds.
- Regression run after the final supply and Master-training fixes: **231 tests passed** in 73.451 seconds. It covered Stats, point buy, progression, inventory/container capacity and access, provider contracts, story retries, and repository transactions.
- Native Windows Qt: **three GUI walkthrough tests passed**, covering point buy and rank previews, Stats and reward spending, equipment pickup, and the item's Move dialog. Native widget captures were visually reviewed.
- Python compilation and `git diff --check` passed. A source audit found no live retired combat events, engine imports, resolution settings, initiative/damage/armor fields, or SkillCheck request contracts.

Provider contract tests used local responses and mocks; no live Gemini gameplay session was run.
