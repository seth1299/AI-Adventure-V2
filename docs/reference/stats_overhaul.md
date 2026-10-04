# Stats rules version: stats-v1

The Skills screen is now Stats. Character creation uses six attributes with rank-based point buy; Player Level/XP and skill training remain separate. `ai_adventure/stats.py` is the shared source for attribute modifiers, point costs, ranks, health, carrying capacity, and model guidance.

Fighting is narrative. Gemini proposes attribute-based checks, attacks, saves, health changes, and milestone significance. The application rolls fair dice, resolves totals, validates proposals, and persists outcomes. Weapon and Armor categories retain descriptions and held/worn equipment without a combat engine. Physical ammunition still uses real inventory records.

## Persistence and transactions

- `player_stats.py` owns Player XP, banked rewards, purchased skill advances, health, and d20 history.
- `progression_records` deduplicates achievements and health consequences using persistent source IDs, and distinguishes purchased skill advances from training XP.
- Objective achievements use the completed task's exact persistent ID. In a response that completes a task and awards XP, `ActiveTaskCompletedEvent` precedes `PlayerAchievementRecordedEvent`. Milestones retain stable identifiers across retries and retellings.
- `SkillXpAddedEvent.source_id` is optional and identifies a particular skill training award. Messages deduplicate one award per skill per message even when an explicit source ID is supplied; calls without a message ID have no message fallback deduplication.
- `event_receipts` reuses committed event and story results on retries. Receipts, inventory effects, health, rewards, and narration share the repository transaction and message snapshot.
- Purchased skill advances shift cumulative XP by eight, retaining partial training even when reaching Master. Master skills gain no subsequent training XP or levels.
- Equipping accessible gear moves it into actively carried inventory. Dropping or storing it clears equipment. Carried containers add capacity; grounded containers and vehicles use their own storage rules.
- Supply consumption must reference sufficient, accessible inventory. A contradictory consumption proposal cannot commit its narration or health consequences.

## Compatibility

New saves store `meta.rules_version = stats-v1`. Existing saves with another or missing rules version are checked through a read-only connection and rejected before schema initialization or gameplay loading. Their files are left unchanged. Templates carry the same rules marker; older templates are identified as incompatible. Current templates may remain unfinished until completed in the wizard. There is no legacy conversion.

The retired mechanical engine and its behavior are preserved in [mechanical_combat_archive.md](mechanical_combat_archive.md).

## Equipment and location storage

The Character sheet scrolls vertically instead of compressing profile fields when Equipment needs more room. Vitals displays carried weight, total capacity, and the base/bag breakdown. A directly carried backpack contributes its declared `carrying_capacity_lb` before it is equipped; equipping it does not add that bonus a second time. Missing capacity metadata contributes no bonus. New Gemini item contracts request realistic positive capacities for usable bags and containers.

Hand slots accept Weapons, with two-handed weapons restricted to Main Hand; Off Hand also accepts shield coverage. Armor fills its declared body coverage, and Back accepts containers declared for that slot. Ordinary food and supplies have no equipment slot. Existing multi-slot coverage and owned-copy limits remain enforced.

Known locations carry `location_scope`: `broad` for cities, regions, districts and continents, or `specific` for a precise recoverable site such as a room, campsite or hideout. Missing or invalid scope defaults to broad. Sublocation status is independent of scope. Playtesting templates and Travel expose this classification; the standard build keeps it as backend metadata and asks Gemini to classify locations automatically. Gemini's creation and location-event contracts carry it. Authored classifications survive finalization, including renamed suggestion locations.

Leaving or retrieving inventory requires the exact current specific site, or an accessible carried container. A broad-area name never makes stored items immediately accessible. Story preflight and durable commit validation use the persisted classification, including ordered site creation, player movement and item storage in one event batch. No existing saves are repaired or converted.

## Validation on October 2, 2026

Contract cleanup validation used the project `.venv` with PySide6 and `QT_QPA_PLATFORM=offscreen`:

- Before cleanup, full discovery passed **653 tests** in 244.787 seconds, but omitted five progression tests defined below the `__main__` guard. That result did not cover those five cases.
- All five tests now belong to `StatsProgressionTests`; the objective fixture uses keyword arguments and the returned task ID. Five additional regressions cover progression transactions, training deduplication, schemas, packaged defaults, and prompt projection.
- Focused validation: **257 tests passed** in 38.990 seconds, covering progression, provider contracts, prompt/context construction, skill auditing, New Game, Stats UI, and repository transactions.
- Full offscreen unittest discovery after cleanup: **663 tests passed** in 246.738 seconds, including the five restored cases and five new regressions.
- Python compilation and `git diff --check` passed. Story prompts transmit one canonical Stats contract while retaining health/progression guidance, authoritative player attributes, and fighting-focus preferences. Packaged references no longer instruct tests to create skills or contain the obsolete combat-started shell.

The earlier overhaul notes reported three native Windows Qt walkthroughs and reviewed widget captures. Those native checks were not rerun during this cleanup; current UI validation was offscreen. Provider contract tests used local responses and mocks; no live Gemini gameplay session was run.

## Validation on October 3, 2026

Full offscreen discovery passed **680 tests** in 254.348 seconds after the equipment, Character layout and location-scope changes. Nine new regressions cover slot rejection, bag capacity, scrolling, broad-area access, specific-site storage, scope persistence and authoring, provider contracts, and ordered site creation/movement/storage. Existing capacity, healing and equipment fixtures now explicitly classify their storage sites. Packaged container guidance matches the shared runtime contract, including Strength-based capacity. Compilation and `git diff --check` passed.

The Character sheet was also inspected in an offscreen rendered capture at 1100 by 800 pixels with a 14-point font; scrolling exposed every equipment row and the profile action. This is offscreen Qt validation, not a native Windows walkthrough. Gemini validation remained mocked; existing saves were not repaired or migrated.

## Scene audio and scope visibility on October 3, 2026

The supplied standard-build log records a forest transition with only `LocationUpsertedEvent` and `StatusUpdatedEvent`; both were applied, with no skipped or dropped events. No music event was returned. Story responses with an available music catalog now require an explicit `music_filename` choice from that catalog. The shared guidance evaluates the final environment (including daytime forests), mood and danger. A changed valid choice becomes one `MusicChangedEvent` before final status; unchanged choices preserve playback, and out-of-game choices do not change music. Music choices cannot use sound-effect or ambience filenames.

Location scope controls, the New Game/template column and Travel storage metadata are restricted to Playtesting. Ordinary location setup leaves scope unspecified for Gemini classification; explicit authored metadata remains preserved. Backend storage restrictions remain in both builds, with player-facing errors requesting a precise place instead of exposing classification labels. No existing saves are altered.

Validation: full offscreen discovery passed **689 tests** in **268.036 seconds**. The focused provider/context/New Game/Playtesting/UI regression run passed **227 tests** in **29.845 seconds**, including the actual Wizard and template manager in both build modes. Compilation and `git diff --check` passed. This is offscreen/static validation, not a live Gemini call, packaged executable rebuild or native audio playback test.

Recent d20 Tests is also restricted to Playtesting. Its collapsible, vertically scrollable panel shows each saved roll/result, reason and associated `message_id`; audit text is selectable plain text. The standard Stats screen does not load or display this audit history. Existing d20 audit persistence is unchanged. Validation: **30 focused offscreen tests passed** in **12.900 seconds**, including persisted details, both visibility modes, empty/unassociated history and long-history scrolling. Compilation and `git diff --check` passed; no packaged executable was rebuilt.

## Skill use and learning on October 3, 2026

Read-only inspection of the reported save confirmed Stealth at level 1 with zero XP, including one successful and one failed Stealth check. The application log contained no corresponding `SkillXpAddedEvent`. The runtime also gated explicit training XP on any earlier failed test in a command.

Resolved meaningful use of an existing skill now awards 1 training XP automatically, once per skill per message, on either success or failure. The roll uses the pre-training bonus; XP can advance the skill afterward. Stable request IDs and training source/message keys prevent retries and duplicate Gemini awards from adding XP again. Master skills receive no more XP. Rolls, XP and deduplication records share a transaction. Player XP continues to require completed objectives or milestones.

Meaningful instruction or practice can teach a new level-1 skill with a scope description and training reason even after a failed untrained roll. Attribute-only tests themselves do not create skills. Explicit training XP is no longer gated by failed rolls; outcome-dependent rewards retain their existing failure gate. Safe routine practice needs no test. Story prompts, routing, schemas and packaged guidance carry the same rules. Existing saves are not backfilled.

Validation: **698 tests passed** in full offscreen discovery (**365.562 seconds**). Focused progression/Stats UI/context checks passed **47 tests**, and skill/audit/transaction checks passed **49 tests**. Seven new regressions cover success/failure XP, retries and duplicate awards, failed-practice learning with reward gating, advancement/caps, rollback, transmitted contracts and visible progress. Compilation and `git diff --check` passed. Gemini behavior was validated through mocked contracts, not live generation; no packaged executable was rebuilt and no existing save was changed.
