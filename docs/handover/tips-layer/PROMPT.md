# PROMPT — paste this into Claude Code

Everything below the line. It assumes you are in the `hk_racing_dashboard_26-27`
working copy and that `docs/handover/tips-layer/` is present.

---

Read these three files before writing anything, in this order:

- `docs/handover/tips-layer/README.md`
- `docs/handover/tips-layer/SPEC.md`
- `docs/handover/tips-layer/payload.example.json`

Then read `AGENTS.md`, `hkrd/store/schema.sql`, `hkrd/store/connect.py`,
`hkrd/query/raceday.py` and `hkrd/api/app.py`. Do not skip these — the spec was
written against them and assumes you know what is already there.

## What to build

A qualitative layer on the race card that answers two questions and nothing else:
**what did the trainer or jockey say about this runner**, and **which tipsters
picked this runner**. `SPEC.md` has the schema, the module map, the payload
contract, the resolution rules, the endpoints and the panel layout.

Build **phases 1 and 2 only** in this first pass — `SPEC.md` §10. That is the
`horse_name_zh` bridge, the three tables, the payload validator, the store module,
the import job and `POST /api/tips/import`. Stop there and report. Phase 2 is
fully testable against the fixture with no network access at all, which is why it
comes before anything that scrapes.

## Non-negotiables

These are the ones most likely to be got wrong, so they are repeated here rather
than left in the spec.

1. **Nothing new goes into `hkrd/`.** No LLM SDK, no HTTP client for YouTube, no
   new package. Transcript fetching and LLM extraction run on Brett's PC, under
   `tools/`, because YouTube blocks datacenter IPs and Fly.io is one. The Fly app
   gains one router, one job, one store module and three tables — and zero
   dependencies. If a step seems to need otherwise, stop and say so.

2. **`api/ → jobs/` is the only write path out of a router.** The import endpoint
   calls `jobs.import_tips`. No SQL, no `requests`, no `subprocess` in any router.

3. **Never write a guessed `race_no` or `horse_no`.** Unresolved quotes keep NULLs
   and still get stored; anything that fails validation goes to `tips_quarantine`
   with a reason. A wrong horse carrying a trainer's endorsement is worse than no
   row, and the quarantine count on the ops page is how a source breaking
   announces itself.

4. **Every write is idempotent.** `INSERT ... ON CONFLICT DO UPDATE`. Posting the
   same payload twice must change nothing. Write that test.

5. **The panels must not reorder, rank or recolour any runner** when you get to
   phase 4. The market has already read the same tipsters; a consensus ranking in a
   pari-mutuel pool is a machine for backing short prices. Claims attach to the
   runner; the card decides the order.

6. **File sizes.** `web/assets/race-day.js` is 1,588 lines against a 600-line cap,
   so nothing goes in it. `hkrd/api/app.py` is 593 of 600 — adding `routes.tips`
   to the router tuple is one line and that is all the headroom there is. Check
   with `wc -l` before you extend any file, not after.

7. **`store/` is the only module that may `import sqlite3`.** Coerce types at
   write time in `store/`, never at read time.

## Tests

`tests/test_smoke.py` must pass. Add:

- fixtures in `tests/fixtures/` — copy `payload.example.json` there, and add one
  real harvested transcript JSON if `tools/harvest_youtube.py` has been run
- a validator test per rejection rule in `SPEC.md` §3
- an idempotence test: import the fixture twice, assert identical row counts
- a quarantine test: a `race_no` with no such race, and a `horse_no` not in that
  race, each produce a quarantine row and no quote or selection

Read the fixtures from disk. Do not inline invented rows.

## Before you start, ask Brett these

Do not guess at them — they change the build.

1. **Which model for the PC-side extraction, and is the Anthropic SDK in `tools/`
   acceptable?** It sits outside `hkrd/` so `AGENTS.md` does not forbid it, but he
   asked to be consulted on new dependencies and this is the one that counts.
2. **Has the `zh-hk` race card been checked for a parseable Chinese name table?**
   `SPEC.md` §5. It is unverified, it is load-bearing for phase 1, and the page
   only carries a card after noon on a Monday or a Thursday. If it turns out to be
   unparseable, the fallback is `ingest/odds.py`'s GraphQL `changeHistories`, which
   already returns `horseName_ch` beside `horseName_en` and is currently discarded.

## When you are done

Report: the files you added and their line counts, the table names created, the
endpoint's response shape, which tests you added, and anything in the spec you
disagreed with and why. If you hit something the spec got wrong about the repo,
say so plainly rather than working around it — the spec was written from a
read-only clone and the working copy is the authority.
