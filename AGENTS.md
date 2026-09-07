# AGENTS.md

Working rules for any AI coding agent on this repo. `AGENTS.md` is read automatically by
several tools; if yours doesn't, paste this as your first message each session.

**Project:** Hong Kong racing analytics. Python + SQLite + FastAPI, with the Claude Design
output served as the frontend. Single maintainer. Race meetings happen twice a week and the
system must work on those days.

---

## Architecture — not negotiable

Imports flow one direction only:

```
ingest → store → derive → query → api → web
```

- `store/` is the **only** module that may `import sqlite3`. Everything else calls a
  function in `store/` or `query/`.
- `api/` imports from `query/` for reads and `jobs/` for actions, and nothing else.
  Never `store/`, never `derive/`, never `ingest/`. No SQL, no `requests`, no
  `subprocess` in any router, ever. Routers return JSON; they never build HTML.

  The read path is `... → query → api`. Triggering work is a different motion: a
  refresh button has to run a job, so `api/ → jobs/` is allowed and is the only
  write path out of a router. A job owns its own defaults (including which
  database it writes) so a router never reaches into `store/` to supply them.
- `web/` is static — the Design HTML, CSS and JS. It talks to `api/` over `fetch`. It has
  no Python and no database access of any kind.
- `ingest/` knows about HKJC and returns plain dicts. It does not know the database exists.
- `derive/` reads raw tables, writes derived tables. Every derived table must be safe to
  `DROP` and rebuild from raw.
- Never call our own Python via `subprocess`. Import it. Nothing in this codebase
  needs a browser: the odds are the one JavaScript-rendered source and `ingest/odds.py`
  reads the JSON endpoint the betting site itself reads.

If a task seems to need a violation, stop and say so rather than working around it.

## Data rules

- **One SQLite file.** Not a database plus spreadsheets plus a folder of JSON. If a value is
  worth keeping, it goes in a table.
- **Types are coerced at write time in `store/`, never at read time.** Odds are `REAL`,
  places are `INTEGER`, dates are `TEXT` as `YYYY-MM-DD`.
- **All writes are idempotent:** `INSERT ... ON CONFLICT DO UPDATE`. Re-running any scrape
  must never duplicate a row.
- **Primary keys:** races `(race_date, race_no)`; runners `(race_date, race_no, horse_no)`;
  horses `(horse_name)`.
- **WAL mode, always.** The scraper writes while the API reads. Without WAL they block each
  other on race day, which is the one day it cannot happen.
- **Join history on `horse_name`, never `horse_id`.** Verified against the legacy data:
  `horse_id` is 0% populated in July 2026, 54.6% in June, 90% in April — the degradation
  starts in **April, not July**. Any join on it silently returns partial history for every
  month from April on. The same regression killed `horsename_zh`, `positions` and `lbws`
  outright from May. Same applies to `rating`.
- **Never parse `lbw` with `pd.to_numeric`.** Measured on all 21,423 legacy rows: it drops
  **79.1%** of values, not the 66% the handoff estimated. 1,482 of those are legitimate `-`
  (winners); excluding them it still loses **77.5%** of real margins, which are fractions
  like `3-1/4`. Use the shared parser.
- **`positions` is a duplicate of `running_positions`** in the legacy schema (`4; 4; 4; 1`
  vs `4 4 4 1`) and it died in May while the other kept working. Store one representation.
- **Never delete odds snapshots.** Odds movement is the most informative signal in the
  dataset and cannot be reconstructed. `prune_old_snapshots` must not be ported. Only 17
  meetings of a full season survived it.

## Numerical rules

- **Read the pool that pays the bet, and fall back to the model only where there is no
  pool.** The capture carries WIN, PLA, QIN and QPL since the move to the JSON endpoint, and
  the place pool IS a place probability while the quinella-place pool IS a "both in the
  first three" probability. `query/pools.py` de-vigs each to how many of its outcomes come
  true — 1 for win and quinella, 3 for place and quinella place — and every figure it
  returns says whether it came from a pool or from the model. Ranking a quinella-place
  ticket by a number derived from the WIN market recommends a different set of pairs.
- **Harville-Henery is the benchmark now, not the answer.** It is still correct and still
  used wherever no pool was captured — an unopened market, a field too small for HKJC to run
  a QPL pool, and the seasons of archive that hold win odds only. Where both exist they
  agree to ~2.6 points a runner and disagree most on the favourite (2026-09-06 R1: model
  62.4%, pool 54.2%). That gap is the only reading on the model that does not have to wait
  for a result.
- **Never convert win probability to place probability by linear scaling.** `p / sum(p) * 3`
  is not a valid transform and overstates the favourite's place chance by ~34 points. It is
  gone from the screen entirely; the comparison shown beside the pool is the model.
- **A par time is a property of a race, not a runner.** Every horse in a race gets the same
  par. If a change produces more than one par per race, the change is wrong.
- **A faster time must always produce a better figure** within the same race. Any rating
  that violates this is broken regardless of its accuracy.
- **Running style is field-size scaled** (decision A2). The Closer cutoff is
  `max(8, field_size * 0.7)`, not a fixed `> 7`. The fixed-threshold version in
  `pace_utils.py` disagrees on 663 legacy runs — every one of them first-call position 8 in
  a 13- or 14-runner field — and it over-calls Closer by 8%. The scaled version is what SARR
  was built and backtested on. One definition, used by both the model and the display.
- **Style sorts positionally, never alphabetically:** Leader → On-Pace → Midfield → Closer.
  Encode it as an ordinal.
- **Pace, style and trend are three different things.** Pace is one value per *race*; style
  is one value per *horse per run*; trend is the direction of a horse's recent figures.
  They get separate columns and never substitute for one another.
- Never present a bare number. Every figure carries context — rank in field, percentile, or
  delta vs par — and a sample size.

## Betting rules

- **Settlement is HKJC tote (pari-mutuel).** Verified: 142 of 160 matched historical bets
  reproduce exactly as `stake ÷ combinations ÷ 10 × final dividend`. You are paid the final
  dividend regardless of when the bet was struck.
- **Therefore early-price value is not capturable.** Odds movement is a sizing input and an
  operational signal, never a timing edge. Do not build selection rules on drift.
- **Every odds-dependent output must use the latest snapshot, never the morning's.** Market
  concentration moves from a mean of 0.539 in the morning to 0.637 at post time, and 60% of
  races land in a different band — always making a race look weaker than it is.
- **A ticket multiplies twice, and the second one is easy to miss.** Within a race, WP and
  QQP are one selection struck into two pools and cost twice the lines. Across races, an All
  Up formula names WHICH multiples it buys — 4x11 is every double, every treble and the
  quadruple, and the 11 IS 6 + 4 + 1 — and each of those costs the PRODUCT of its legs' own
  combination counts. A 2X1 over a QQP banker-with-four and a single place is eight lines,
  not one. All of it is in `query/tickets.py`, checked against the account statement.
- **A race is over when HKJC shuts the pool, not when the card said it would go off.** The
  capture used to run for thirty minutes past the SCHEDULED off on every race — thousands of
  rows a meeting recording a market that could no longer move, and still wrong for a delayed
  start. `pmPools[].sellStatus` is the signal; the first capture that sees it stop selling
  records the close in `market_close` and the race is never asked about again. The clock
  stays as the backstop for a close nobody observed.

## Error handling

- **Never write `except: pass` or a bare `except Exception: pass`.** The previous system had
  66 of them in `dashboard.py` alone (of 351 except clauses) and it is why bugs went
  unnoticed for months.
- On the analysis path, let exceptions raise. A visible traceback beats a silently empty column.
- If a failure genuinely must be tolerated, log it and record it in a visible errors list.
- **A missing minor input must never void a whole result.** Degrade the affected term and
  label the output, don't return `None`.
- **Every job reports row counts, never silence.** A zero must be visible immediately.
  Silent success and silent failure must never look the same.

## API rules

- Every endpoint returns JSON shaped by the `RunnerLine` grammar. A run looks the same in
  Race Day, Form Guide, Lookup and Results because it is the same serialised object.
- **No endpoint may exceed 500ms** on current data. Add an index rather than optimising
  Python. Caching belongs in `query/`, not in the router.
- Errors return a real status code and a message naming what failed. Never a 200 with an
  empty list.

## Secrets

- Nothing secret is committed — no password, account number, or bet log. Local config lives
  in `.env`, which is gitignored.

## File size

- Hard cap 600 lines per file. At 500, propose a split before adding.
- One page per file in `web/pages/`, one router per file in `hkrd/api/`.

## Testing

- `tests/test_smoke.py` must pass before any commit. If a change breaks it, fix the change,
  not the test.
- Any model change must be validated **walk-forward**: for each meeting, train only on races
  strictly before that date. Never evaluate on data the model has seen.

## Style

- Type hints on every public function.
- No new dependencies without asking.
- Ask before adding a feature that isn't in the current task.
