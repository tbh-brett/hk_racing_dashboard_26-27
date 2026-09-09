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
- **Every change on a race card is measured from MIDNIGHT on the race day**, never from the
  first capture ever taken. HKJC opens a pool around midday the day before, and for hours it
  is nearly empty — an almost-empty tote quotes numbers that are real, finite, plausible and
  meaningless, because one $10 bet in a pool holding a few hundred dollars prices a runner
  at 2.2. On 2026-09-09 HV race 3 the card read MACANESE MASTER at **+354%** (2.2 → 10.0)
  where its race-day move was **+13.6%** (8.8 → 10.0), and the old baseline pointed the
  WRONG WAY on five of twelve runners. One definition, `market.day_start` /
  `market.opening_capture`, read by `price_movement`, `split_move`, `money_arrived` and
  `pool_turnover` — a page showing a runner firming while another shows it drifting is
  exactly what a second baseline would produce. The earlier rows are never deleted; they are
  the opening price of the pool and not the baseline for "how has this moved today".
- **999.0 is not a price**, and it is a different fault from the one above: 999.0 is HKJC
  saying "no price", 2.2 in an empty pool is HKJC saying "one person has bet". Only the
  first is detectable by its value, which is why the second needs a clock and not a
  threshold. HKJC's tote board has four digits and no way to say "nothing",
  so an open pool nobody has bet into quotes 999.0 on every runner. Stored as a price it
  becomes the FIRST price of the race, which is the one every movement figure is measured
  from, and the whole card reads as firming 98%. `ingest.odds.NO_PRICE` drops it on write
  and `market._priced` drops it on read, because nothing may delete the rows already
  written. Measured: the 2026-09-09 capture at 12:01 the day before racing was 86 rows of
  it across eight races; not one of the 8,716 rows on a raced day is 999.0.
- **HKJC publishes the gear change; do not reconstruct it.** The suffix is the fact — `B1`
  is blinkers first time, `B2` second time, `B-` removed, bare `B` no change. 737 `TT1`,
  584 `B1` and 651 `B-` sat in the archive being rendered as literal text. `derive/gear.py`
  reads it. Only two things need the record instead: RE-INSTATED (worn before, off last
  start, back on — HKJC writes a plain `B`) and the barrier-trial schooling step.
- **A NULL gear column is "this scrape did not carry gear", never "no gear".** The results
  write erased it for April to July 2026 and July has 0 of 641 runs on record. Diffed
  against a blank, every piece on every horse reads as newly applied — which is what a
  first cut of the gear panel did on a real card. `query/gear` reports `comparable: false`
  rather than a change it cannot support.

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
- **One movement figure is two different measurements.** The money arrives in the last five
  to ten minutes; the rest of the window is twenty hours of nothing, and averaging them
  together throws away the only part worth watching. On 2026-09-06 R9 runner 4 moved +1.6%
  over the whole day and +21.6% inside the final ten minutes, so the single figure said
  FLAT about a horse being let go. `query/movement.split_move` returns both — `change_pct`
  for sizing, `rush_pct` for attention — and neither is a reason to back anything.
- **Turnover is a denominator, never a tip.** `pool x de-vigged share` is the actual number
  of dollars rather than an estimate — in a pari-mutuel pool the dividend is
  `pool x (1 - takeout) / stake`, so the share IS the money share, in the win pool, the
  place pool (split into three equal parts, so the identity survives) and both pair pools.
  **Money share normalises to 1**; normalising to 3 gives a place probability, which is a
  different quantity. Raw turnover tracks field size and favourite shortness: 2026-09-06
  race 3 held $4,288,122 because KA YING RISING was 1.0 in a field of six, and following
  that money means backing an odds-on shot into a 17.5% takeout.
- **The pair pools are usually bigger than the win pool.** 2026-09-09 HV race 1, the day
  before racing: QPL $251,403, QIN $202,395, WIN $174,539, PLA $145,593. Any reading of
  "where the money is" that stops at the win pool misses more than half of it, and the half
  it misses is the half a QQP ticket is struck into.
- **A merged pool is one pool reported twice.** HKJC merges Quartet into First 4 and reports
  the same money under both ids with `mergedPoolId` naming the survivor. Summing every pool
  on 2026-09-09 HV race 1 overstates it by $28,006. `money.pool_turnover` marks the
  duplicate `counted_elsewhere` and returns a `race_total` that counts it once.
- **Money ARRIVING is the honest version of a price move.** A runner shortens when money
  comes for it and also when money comes for everything else, and the odds cannot tell those
  apart. `money.money_arrived` takes the pool at two moments times the share at those SAME
  two moments — using today's pool with the morning's share invents money that never
  arrived. Measured on 2026-09-09 HV race 1 between 15:01 and 17:29: $135,507 came in, and
  runner 5 took the largest single share of it while its price DRIFTED.
- **A double's betting shuts when its FIRST race goes off**, so from that moment the grid is
  frozen — and once that race is decided, the winner's row is a complete settled book on the
  second race, formed from different money and typically half an hour earlier. The first leg
  divides out EXACTLY: the price of (winner, X) is the two legs multiplied, so across X the
  first-leg probability is a constant and normalising removes it. No estimate of the first
  leg is needed and none may be made. `pools.doubles_after_leg`.
- **A double's combination separator is `/`, not `,`** — `"02/04"` where a quinella is
  `"02,04"`, and `odds._combination` returns nothing at all rather than erroring on one.
  Confirmed against a live selling pool on 2026-09-08: 936 rows across seven legs, none
  dropped. A double is also ORDERED — first-leg 3 with second-leg 7 is a different bet at a
  different price from 7 then 3 — so it must never be sorted the way a quinella pair is.
- **The place starting price is the last capture, not a column.** `runners` has `win_odds`
  and nothing beside it, so a place price lives only in `odds_snapshots`. It reaches every
  surface through `_LINE_SQL` now that the capture stops when HKJC shuts the pool, which
  makes the last row the settled price rather than one from half an hour before.
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

## Performance

- **Never compute a per-race constant per row.** Field size and the race's book are the same
  for every runner in a race. As correlated subqueries in a SELECT list they were recomputed
  per row per use — the Lookup insight panel ran ~43,000 scans to answer a question about
  21,493 rows and took 650 ms, against 20 ms for identical arithmetic grouped once into a
  CTE. SQLite cannot notice that a correlated subquery is constant within a group.
- **Responses are gzipped.** These payloads are JSON with the same field names on every row,
  which is the best case there is: a race card is 7.3x smaller, a 500-row Lookup answer 7.9x,
  the trials feed 15.6x. The machine is in Singapore and the dashboard is read in Hong Kong,
  so bytes on the wire are most of how fast it feels.
- **The page cache is sized for the database, not for SQLite's default.** 2 MB against a
  38 MB archive meant a query touching a fifth of it re-read most of it every time; it is
  64 MB with a 256 MB mmap ceiling, on a machine with 1 GB. See `store/connect`.
- **Measure before changing anything.** The card was assumed to be the slow page and is
  28 ms; the slow things were a panel nobody suspected and the absence of compression.

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
