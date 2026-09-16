# Settled decisions

The handoff bundle's `pre_build_checklist.md` listed questions that blocked the build.
This records the ones now settled, and what settled them. Figures here were measured
against the legacy `hkjc.db` (21,423 rows, 2024-09-08 → 2026-07-15), not estimated.

---

## A2 — Which pace implementation is authoritative? **Settled**

The checklist called this "the single most important unresolved technical question" and
described four drifted copies. Measuring them against all 21,130 legacy runs with running
positions shows the framing was slightly off: the four candidates do not all compute the
same quantity, and two of them are identical.

| Implementation | Behaviour |
|---|---|
| `sarr_prototype.classify_style` | field-size scaled |
| `sarr_raceday.classify_style` | **byte-identical in behaviour to the above** |
| `race_day_analysis_v4.4._classify_running_style` | the same, plus a tactical-leader adjustment |
| `pace_utils.running_style_from_positions` | **fixed thresholds, no field-size scaling** |

So there are three distinct behaviours, not four, and they agree closely:

| Pair | Agreement |
|---|---|
| `pace_utils` vs `sarr_*` | 96.9% |
| `pace_utils` vs `v4.4` | 96.5% |
| `sarr_*` vs `v4.4` | 99.6% |

**Every one of the 663 disagreements between `pace_utils` and `sarr_*` is the same case:**
first-call position 8 in a 13- or 14-runner field. `pace_utils` calls it Closer (fixed
`> 7`); `sarr_*` calls it Midfield (cutoff scales to `max(8, field × 0.7)` = 9). Nothing
else differs. `v4.4` adds one refinement on top of `sarr_*`: a horse that leads at the first
call but drops back by the second is reclassified Leader → On-Pace (74 runs, 0.4%).

**Decision: the field-size-scaled version is canonical.**

- It is what SARR was built and backtested on, so adopting the other would silently change
  the model's own inputs.
- It is correct on the merits: position 8 of 14 is mid-division; position 8 of 9 is a closer.
- The cost is real and worth stating — `pace_utils` is what feeds the current dashboard and
  form guide (`dashboard.py`, `build_form_guide.py`, `build_pace_index.py`,
  `scrape_hkjc_results.py` all import it), so **3.1% of displayed styles will change**, and
  the legacy display over-calls Closer by 8% (9,155 vs 8,492).

`v4.4`'s tactical adjustment is a genuine refinement at low stakes. Adopt or drop on its
merits; it is not what A2 was asking about.

---

## A3 — Where does the database live? **Settled: SQLite on a persistent volume**

Superseded an earlier choice of Supabase Postgres, once the requirement was restated as
"accessible, faster, and newly scraped results integrated onto it."

Local SQLite wins on that phrasing. Measured on this data: a targeted query returns in
**0.000s**, a full-table read in 1.09s, the equivalent spreadsheet in 15.33s. A hosted
database would add a network round-trip to every one of those queries, against a rule that
no endpoint may exceed 500ms — for a single-user workload of 21,423 rows growing by roughly
650 twice a week. Postgres would have bought concurrency this project does not need.

Durability, which is the real argument for hosting, comes from **Litestream** instead: it
streams the WAL to S3-compatible storage continuously, so the recovery point is seconds.
It runs as a sidecar, not a dependency, and the application never knows about it.

Consequences:

- The schema in `REBUILD.md §2` is used **as written** — no DDL porting.
- `store/` is a thin `sqlite3` wrapper, as originally specified, and stays the only module
  that imports it.
- WAL mode is mandatory, not optional: the scraper writes while the API reads, and on race
  day they must not block each other.
- `jobs/migrate_legacy.py` becomes a SQLite→SQLite copy, which is markedly simpler than a
  cross-engine migration.

---

## A4 — Stack **Settled: FastAPI + the Design HTML**

Streamlit is not a requirement. The eleven design briefs specify, among other things:

- a flyout filter overlay that floats above the table while it live-updates underneath
  (brief 10)
- a viewport-fixed hover panel with collision detection and portal rendering, specified
  precisely because naive in-flow positioning caused a feedback loop that made the page
  vibrate (brief 09 §1)
- fixed row heights with popovers that must never reflow the row (brief 04 §1)
- expansion state persisting across race switches (brief 02)
- side-by-side scrolling All-Up panels (brief 08 §3)

These are DOM-level requirements. Streamlit's rerun-per-interaction model cannot express
them, so building there would mean shipping a materially reduced version of a design that
was deliberately drawn unconstrained.

FastAPI serves JSON; `web/` holds the Design output and talks to it over `fetch`. The
`RunnerLine` grammar becomes the serialisation format, which is what makes a run look
identical in Race Day, Form Guide, Lookup and Results — it is the same object.

This changes nothing in Phases 0–3. `ingest`, `store`, `derive` and `query` are identical
under either frontend; only the layer above `query/` differs.

---

## Legacy store reconciliation **Settled**

`extraction_map.md` flagged three stores of ostensibly the same results and asked for them
to be reconciled before migrating. Measured:

- `hkjc_24-26.db` — **does not exist**.
- `hkjc.db` and `hkjc_results_updated.xlsx` are **identical**: 21,423 rows, 57 columns, same
  date range, zero rows unique to either.

The spreadsheet is a pure duplicate that costs **13.8s** to read. `hkjc.db` is the source.
No reconciliation needed — just delete the `read_excel` paths, which the smoke test now
prevents from returning.

---

## Tote vs fixed odds **Settled: HKJC tote (pari-mutuel)**

`03_findings/00_synthesis.md §3` called this the question that decides whether odds movement
is the review's most important finding or an operational note. Three lines of evidence:

1. Every bet type in the 1,078-row log is an HKJC tote pool — QIN, QPL, QIN_BANKER,
   QPL_BANKER, QTT, ALLUP_QQP, PLACE. Fixed-odds books do not offer Quinella Place.
2. The account statements are HKJC's own "Account Records / Betting Account No." format.
3. Decisive: reconstructing returns from the recorded final dividends,
   **142 of 160 matched hit bets reproduce exactly** as
   `stake ÷ combinations ÷ 10 × final dividend`. (The 18 outliers are QPL multi-combo hits
   where the reconstruction under-counted winning pairs, not counter-evidence.)

**Consequence: you are paid the final dividend regardless of when you bet, so early-price
value is not capturable.** The A/E ≈ 1.00 column against the final price is reality. The
odds layer remains the highest-value thing to build, but as a *sizing input* — market
concentration from the latest snapshot — never as a timing edge. Do not build selection
rules on drift.

---

## A5 — Should SARR score the barrier draw? **Settled: yes, refitted**

SARR carried a `draw` component from the day it was written: the parameter, the
multiplier, the `runner_sarr_component` row and a column on the Model Analysis
page. Nothing ever supplied a value, so it contributed exactly 0.0 to all 17,262
scored runners. The old dashboard DID score it
(`sarr_raceday.py:598`, `get_draw_score(...) * 0.3`), so the rebuild dropped a
live term rather than declining to add one.

**Restoring it verbatim was rejected.** Measured walk-forward over 306 held-out
races after 29 Mar 2026, the legacy term made rank correlation WORSE — rho
0.3735 against 0.3786 with no draw term at all. Three reasons, all measured:

1. **A fixed mid-place.** Legacy scored `mean(place) - 6.5`. Gates 13 and 14
   only exist in 14-runner fields, so a neutral wide gate collected field-size
   artefact rather than effect. Normalising BOTH axes by field size is the whole
   correction.
2. **Venue-only keying.** The effect is strongly distance-specific, and at
   ST 1000 it **reverses** — fitted slope −0.093 raw against a +0.114 global. A
   venue-only table averages that away and penalises the gate it should reward.
3. **An unfitted multiplier.** `0.3` was hand-chosen, carried in a comment
   reading `# conservative draw weight`. Legacy's OLS ran on eight factors, none
   of them the draw.

**What replaced it.** One slope of normalised finishing position on normalised
draw per `(venue, distance)`, shrunk toward the global slope by `n/(n+200)`,
scored centred so the term reorders a field rather than shifting it:

```
npos  = (place - 1) / (field_size - 1)      ndraw = (draw - 1) / (field_size - 1)
slope = regress(npos ~ ndraw) per cell, shrunk to global
score = slope * (ndraw - 0.5)               contribution = score * 1.5
```

| variant | Spearman rho | vs none | t | p |
|---|---|---|---|---|
| none (what shipped) | 0.3786 | +0.0000 | — | — |
| legacy (venue, fixed 6.5, w=0.3) | 0.3735 | −0.0051 | −0.38 | 0.7018 |
| slope, w=1.0 | 0.3962 | +0.0176 | +2.89 | 0.0041 |
| **slope, w=1.5** | **0.4003** | **+0.0217** | **+2.77** | **0.0059** |
| slope, w=2.0 | 0.3956 | +0.0171 | +1.89 | 0.0603 |

The sweep turns over at 1.5 rather than running to the edge, so the optimum is
interior and not a boundary artefact. `DRAW_MULTIPLIER` is named apart from the
eight fitted `WEIGHTS` deliberately: those came from one OLS that never saw a
draw term, and folding a ninth in beside them would misrepresent how it was
obtained. Refitting all nine together is the cleaner design and a real piece of
work; it is not something to do by accident.

Post-rebuild, `draw` is the **third-largest term by realised influence** —
mean |contribution| 0.0464, behind `fmrp` (0.1033) and `wpr` (0.0689), range
±0.157. Components still sum to the score on all 17,376 rows.

**It creates no betting edge, and must not be framed as one.** This makes SARR a
better DESCRIPTIVE rating of who ran well relative to the field they met. The
corrected model still sits well short of the market, and `model/backtest.py`
documents why that road is closed.

---

## Corrections to the handoff figures

Verified against the data; the bundle's estimates were close but low in two places.

| Claim in bundle | Measured |
|---|---|
| `pd.to_numeric` drops 66% of `lbw` | **79.1%** (16,938 / 21,423). Excluding 1,482 legitimate `-` winners, 77.5% of real margins |
| `horse_id` 100% NULL "since July 2026" | July 0%, June 54.6%, May 99.4%, **April 90%** — degradation starts in April |
| 94 `except: pass` in `dashboard.py` | **66** (of 351 except clauses); zero bare `except:` |
| 8 backtesters | **15** |
| four pace implementations | **three** distinct behaviours (see A2) |
| ET module "39 tests" (`02_built_code/README.md`) | **42** pass |

One undocumented finding: the 2026 scraper regression is **two** separate failures, not one.
The Chinese/detail-page path died outright in May (taking `horsename_zh`, `positions` and
`lbws` to zero); `horse_id` degrades on its own curve to zero by July. `running_positions`
and `sectiontimes` survive intact throughout, so pace inputs are unaffected — what is
actually lost is `lbws` (per-section margins) from May.

---

## Still open

| Item | Why it matters |
|---|---|
| **A1 — security** | The My Bets password is live in `dashboard.py:17770` on a public repo, alongside two account statements and a 1,078-row bet log. Deferred by decision; still live. |
| **B2 — odds pruning** | `prune_old_snapshots` is still called at `scrape_hkjc_live_odds.py:500` in the old repo. Every meeting that passes loses data permanently. Independent of this rebuild. |
| **Repo hosting** | GitHub writes are refused for this account — the git proxy 403s and the app integration cannot create repos or push. Needs GitHub reconnected before any of this reaches a remote. |

---

## Settled since

**C1 — where does Lab go?** The Claude Design export answers it: Model Analysis
is its own artboard, so the nav is eight items and Lab has a home. Built.

**C2 — the Trials `RESULT` column.** Empty at source, not merely unwired in the
interface: the trials scrape's own `result` field is blank on every row across
159 files. Finishing position derives from the last running position instead.

---

## The seven legacy logic modules — **discarded**

`decision_engine.py`, `betting_strategy.py`, `form_screener.py`,
`horse_cycle.py`, `backtest_model.py`, `calibration_harness.py` and
`train_gbm.py` — 6,806 lines in the old repo — are **not** ported.

The owner's instruction, verbatim: "Disregard all seven logic completely, those
are all vibe-coded without thorough consideration, and plenty of newly
implemented functions and features replaces them."

What replaces each, so nothing is lost by accident:

| Legacy module | Replaced by |
|---|---|
| `backtest_model.py`, `calibration_harness.py` | `model/backtest.py` — walk-forward calibration and value, split by date |
| `train_gbm.py` | nothing, deliberately. See below. |
| `decision_engine.py`, `betting_strategy.py` | nothing, deliberately. See below. |
| `form_screener.py` | `query/formguide.py` + `query/slices.py` + the Form Guide and Lookup pages |
| `horse_cycle.py` | `derive/et.py` (figure vs par with an effective sample size) and `model/sarr.py` |

**Why the betting and training modules have no replacement.** `model/backtest`
measured the thing all three depend on: over 596 walk-forward races there is no
edge against the closing market at any blend weight, and the return gets worse
as more disagreement is required. A staking engine, a ticket builder and a
gradient-boosted head all exist to exploit an edge; the edge is not there. If a
future fundamental stream beats the price on this harness, they become worth
writing — and the harness will say so.


## Odds come from HKJC's JSON endpoint, not from a browser

**2026-09-05.** `ingest/odds.py` drove a real Chromium through Playwright,
because the odds on `bet.hkjc.com` are rendered by JavaScript and no fetch of
that HTML can see them. Both halves of that sentence are still true. What was
missed is that the page is a single-page app, and the thing it renders from is
`info.cld.hkjc.com/graphql` — declared in the site's own
`/Config/GlobalConfig.js`. Reading that directly is the same data one step
earlier.

What it removed, in order of how much it mattered:

* **The cron line could finally be armed.** The deploy image carries no browser
  and never did — Playwright wants a ~400MB Chromium and roughly 512MB of RAM
  while it runs — so `*/15 ... scrape_odds` stayed commented out for a season
  and the capture happened only when the owner ran it on their PC by hand.
  `odds_snapshots` therefore held 2,014 rows across nine meetings, all of them
  rescued by the legacy import. The endpoint needs nothing the image does not
  already have.
* **A meeting is one request and under a second**, against ~11 page loads and
  ~30 seconds. That is what makes a minute-by-minute ladder affordable at all.
* **The stale-DOM guard is gone**, because there is no DOM. So is the
  bounding-box reconstruction of the quinella matrix — a triangular grid packed
  into a square table, rebuilt from cell rectangles. Pairs now arrive as
  `{"combString": "02,04", "oddsValue": "7.9"}`.

**What replaced the stale-DOM guard.** `raceMeetings(date:, venueCode:)` does
not answer "no such meeting". Measured against the live endpoint on 2026-09-04:
asking for 2026-09-08 ST returned the 2026-09-05 S1 simulcast card, and asking
for 2026-09-06 HV returned the same, both with nothing to say the filter had
been ignored. Storing either would file one meeting's prices under another's
race numbers in the one table nothing prunes. So the job asks HKJC which
meeting id it files this date and venue under, and refuses the whole capture if
any pool does not carry that id as a prefix. Two id formats have been observed
— `20260905S1` and `MTG_20260906_0001` — and neither is parsed, because the
`MTG_` form carries no venue and appeared on a meeting whose market was open,
so any rule inferred from the shape would have been wrong.

**The parse half did not change.** `parse_snapshot`, `snapshot_rows` and
`pair_rows` take the same payload dict they always did, which is why
`jobs/import_legacy_odds` reads the old on-disk cache unchanged and the 17
rescued meetings stay readable.

## The capture cadence follows the money, not the clock

**2026-09-05.** The schedule was every 15 minutes, 12:00–23:59, pricing only
*today's* meeting. Two things were wrong with that.

**It missed the overnight market entirely.** HKJC opens a market at 13:00 the
day *before* racing. The first race-day tick at 12:00 found a price that had
already been moving for 23 hours, and race 1 goes off at 12:30. The job now
looks at today's meeting first and falls through to tomorrow's.

**It sampled the only interesting window three times.** The owner's
observation, and it is the right one: the late money does not show until the
final five to ten minutes; drift before that is comparatively flat. So the
schedule became the dumbest line in the crontab — every minute, no hour
restriction — and the decision moved into the job, where the database can
answer "how far is this race from its off". See `CADENCE_MINUTES`: hourly the
day before, 15 minutes from three hours out, 5 from thirty minutes, and every
minute through the last ten and past the scheduled off. Past the off because a
delayed start is real and the price that settles the bet is the one at the
actual off.

A tick with nothing due costs 0.3 seconds and no network. Simulated against a
real Sha Tin card, 1,103 of 1,501 ticks over a meeting's 25-hour cycle answer
from the database alone.

**Pair odds run on their own, coarser ladder, and the reason is arithmetic.** A
14-runner field has 14 win prices and 182 pair prices, so pairs are 88% of the
rows. Measured on disk at 106 bytes a row, pairs on the win ladder would be
0.93 GB a season against a 1 GB volume that nothing ever deletes from. The two
ladders differ where it costs least — overnight, every three hours instead of
hourly, which is where nearly half the saving comes from — and are identical
where the money is. A quinella box priced five minutes out is the same bet.

As built: ~63,000 rows and ~6.7 MB per meeting, ~0.6 GB a season. One season
fits the volume; two do not. `docs/deploy.md` carries the `fly volumes extend`
command.

## The Race Day card reads the capture, not the card

**2026-09-05.** `runners.win_odds` is written by the results scrape and by
nothing else, so it is the STARTING price and it does not exist until the race
has been run. Every runner of the 2026-09-06 Sha Tin card had it NULL — 120 of
120 — which meant the Race Day page showed an empty price column, no market
rank, no de-vigged win percentage, no overround and no sparkline at exactly the
moment brief 01 says the page is for: twenty minutes before the off, with money
about to go down. Market concentration was right all along, because it reads
`odds_snapshots`; nothing else on the card did.

`query/model.blend_breakdown` had the same fault and it cost more. With no
prices the market stream has nothing to normalise, so the blend degraded to
pure SARR — and the fitted weight on the fundamental stream is 0.00, so a blend
with no market term is not a weaker answer, it is a different model.

Both now fill gaps from `query/market.live_prices`. They FILL rather than
overwrite: where a starting price exists the race is over and that price is the
final one, later than any snapshot, and it is what the models were fitted and
backtested against. `get_race` is untouched, so the Form Guide, Results and
every backtest keep asking what a run actually paid.

Place is the exception that proves the rule: `runners` has no place-odds column
at all, only the settled `place_dividend`, so a place price on the card always
comes from a capture. That is why the measured place/win ratio read as absent
for every race until now.

## The two live pages poll; the server tells them how often

**2026-09-05.** Capturing a price every minute is only worth doing if
something reads it every minute. Race Day and Model Analysis were snapshots
from page load — a card opened twenty minutes before the off showed the price
from twenty minutes before the off, which is the window brief 01 says the page
exists for.

Both now poll, and three things stop that costing anything.

**The server owns the interval.** Every reply carries `X-Poll-After`, computed
from the same ladder in `query/market.CADENCE_MINUTES` that the capture runs
on — half the capture interval for that race. Overnight that is thirty
minutes; inside the last ten minutes before the off it is fifteen seconds. The
ladder moved out of `jobs/scrape_odds` and into `query/market` for exactly this
reason: two copies would drift, and the failure would be silent — a page asking
hourly questions every thirty seconds, or a minute-by-minute market read once
an hour.

**A poll that finds nothing new never builds an answer.** The Race Day card is
36 KB and ~220ms to assemble: form, veterinary history, head-to-head, blackbook,
one query per runner. The poll that discovers it has not changed answers from
two indexed `max()` lookups in under a millisecond and sends no body — measured
end to end from the browser at 5ms including the round trip. `api/live.py`
holds it; `build` is passed as a callable precisely so the unchanged path can
decline to call it.

The ETag covers the latest odds capture AND the latest job run, so a re-scraped
card — a scratching, a going change — invalidates it too. Blackbook edits are
deliberately outside it: they are made by the person looking at the page, which
has already re-rendered locally by the time any poll returns.

**A hidden tab does not poll.** A card left open on another desktop overnight
would otherwise ask a few hundred pointless questions. It polls immediately on
becoming visible instead, which is both cheaper and fresher.

The page also now says WHEN the price on screen was true (`PRICED 17:52` beside
the concentration chip), and says so differently if a poll has failed. Without
that there is no way to tell a market that has not moved from a page that has
stopped listening, and those need different reactions twenty minutes before the
off.

## One machine, a bigger volume

**2026-09-05.** With the capture armed, the measured growth is ~0.6 GB a
season against a 1 GB volume that nothing ever deletes from. The question asked
was whether to add a second Fly machine for scraping.

No — and the reason is the one `fly.toml` already gives. A Fly volume attaches
to exactly one machine and the database is a file on it, so a second machine
has either no data or a diverging copy of it. Two SQLite files both taking
writes are irreconcilable within one meeting: the scraper writing odds the
dashboard cannot see, the dashboard settling bets the scraper does not know
about.

It would also not buy anything. Splitting off a scraper is worth it when the
scraper is heavy; this one is one HTTP request and 0.6 seconds, and ~73% of its
one-minute ticks never leave the machine. The constraint was disk, and disk is
a volume setting.

So the volume went from 1 GB to **5 GB** — in place, no restart, no downtime,
US$0.75/month against US$0.15. About eight seasons.

**The replica needed the same arithmetic done to it.** Litestream retention was
30 days. Retention holds a snapshot plus its WAL for the whole window, and a
snapshot is a compressed copy of the whole database — measured on the live
replica, 32 MB compresses to 12.5 MB, about 2.6×. At a season-end database of
~1 GB, a 30-day window would hold roughly 30 snapshots of ~400 MB: 12 GB,
against R2's 10 GB free tier. Cutting it to 7 days is ~2.8 GB and still
generous for what that replica is for, which is "the machine died, restore the
latest" rather than archaeology. The volume is the primary copy and it now has
room.


## A Saturday meeting is scraped on Saturday

**2026-09-07.** 2026-09-06 ST ran on the Saturday. By Monday morning every
page still showed the meeting before it, and four faults had to line up for
that — each of which would have been survivable alone.

**The results were not fetched on the day.** `date >= _today()` called the
meeting "not published yet" at 19:00, 22:45 and 23:45, hours after a card
whose last race went off at 17:55. The guard it enforces is real: asked about
a meeting that has not run, HKJC serves a page rather than answering 404, and
`_store_race` stamps the date we asked for onto whatever came back. But the
defence against that is `_is_same_meeting`, which compares the runners that
came back against the field the card declared and is independent of any clock.
The date rule is now a race-clock rule using the same settling allowance the
odds capture uses, so the post-race scrape runs on the evening of the meeting.

**Then it crashed.** Race 5 carried a fifteenth row: STAR FIGURE, place `WV`,
no saddlecloth number — a horse withdrawn before the start never carried one —
and it was not on the declared card either, so there was no number to recover.
`runners` is keyed on (race_date, race_no, horse_no); it went in as NULL and
raised `NOT NULL constraint failed`.

**And the crash took the meeting with it**, because all ten races were stored
in one transaction. Race 5 rolled back races 1 to 4 and the card finished the
night with 0 of 120 results. One transaction per race now.

**And the derive would have failed anyway.** Race 7's INVINCIBLE SHIELD was
withdrawn at the start (`WV-A`), so its running positions came back as `---`
and `parse_running_positions` raised — taking pace, ET, SARR and tags down for
the whole archive over one horse that did not run. A run of dashes is HKJC's
"not applicable", which the gear parser three functions below already knew.

## Sectionals moved and nothing said so

**2026-09-07.** Section times used to be a table inside the results page, and
`parse_sectional_table` read it from there. HKJC stopped putting them there.
Coverage: 151 of 151 runners on 2026-06-27, 152 of 153 on 2026-07-12 — then
**0 of 107 on 2026-07-15 and 0 of 120 on 2026-09-06**. Nothing raised and
nothing was logged. The parser looked for a header saying "sectional", did not
find one, and returned `{}`; every meeting for two months had no sectionals and
the only trace was a column quietly full of nulls.

They are still published, at `displaysectionaltime`, which wants the date the
other way round — `racedate=06/09/2026` where the results page wants
`racedate=2026/09/06`. Same site, same meeting, two formats.

A section time is the first time-shaped token in its cell. The cell reads
`7 3 22.33 11.15 11.18`: position, margin behind the leader, the section time,
then the 100m splits inside it. Reading it positionally would take the margin,
and margins are lengths — `2-3/4`, `N`, `SH` — that sometimes look like small
numbers. The check that says the right column was read is that the winner's
sections sum to the winning time: 24.13 + 22.33 + 22.13 = 68.59, and R1 was won
in 1:08.59. That is a test.

The results page is still tried first, so a meeting whose page still carries
the table costs no extra request.

## A card that is gone is not a card that failed

**2026-09-07.** HKJC takes the race card down once a meeting has been run. So
every post-race scrape warned about the racecard, that warning logged
`scrape_meeting:card` with `ok=0`, and the freshness strip showed `Card —` for
a meeting whose full field had been stored days earlier — a success displayed
permanently as a failure, which is the rule this project is built on read
backwards.

Nothing is claimed either way now when the card is gone and nothing was
declared. That leaves the last run which DID fetch a card standing, and "when
did the card last land" is what the strip is asking.

## A model with an opinion about most of the field has an opinion

**2026-09-13.** `blend_breakdown` blanked the FUND PROB column unless every
runner in the race was scored, so the blended column was the de-vigged market
at every weight and the page's one control did nothing. The reasoning was
sound — a softmax over part of a field is normalised against a denominator
missing terms — and the remedy was not, because the case is not an edge case.
SARR rates nothing with fewer than `sarr.MIN_PRIOR` (two) prior runs and a
card always carries debutants: **1,116 of the archive's 1,712 races, 65.2%,
hold at least one runner it will not score.** The page built to put a model
beside the market showed no model on two races in three, and said so in a
footer nobody had reason to read as "this is normal".

The denominator was the fixable part. The rated runners now share the market's
OWN total on that group rather than the whole 1.0 — `fundamental_probability`
takes a `mass` — and a runner SARR cannot rate falls through to its market
price, because `w·m + (1−w)·m` is `m` at every weight. The column sums to that
share, not to 100%, and the footer names the runners it does not cover —
without a reason, because a blank rank is either too little history or a card
nobody scored, and Race Day is the page that tells those apart. An unrated
runner is not a horse with no chance, which is what dropping it said, and not
the field average, which is a number nobody measured.

The de-vig still needs the whole book and still blanks without it: the
overround IS the gap between the book and 100%, so a book missing a runner has
a gap that is partly the missing runner. One of the two shortfalls was real.

**What this changed about the published figures.** The same requirement had
been silently selecting the fitting population — `fit_blend` could only use
races with a fully rated field, so the weight was chosen on 660 races and
applied to all of them. It now fits on all 1,617 races with a complete book
and reports both. THE ANSWER DOES NOT MOVE: the fitted weight is 0.00 on
either population, and on the wider one every positive weight is still worse
(2.0155 at w=0.00, 2.0242 at 0.10, 2.0543 at 0.32, 2.2482 at 1.00, over 535
test races). Tripling the evidence did not rescue the fundamental stream,
which is a stronger statement of the finding than the page could make before.

One number moved for a reason worth recording: `_log_loss` took the first row
with `place = 1`, and a dead heat has two. Reading the archive through
`runners` rather than through `runner_sarr` therefore moved the market's test
loss from 2.0466 to 2.0545 with no model change at all — a published constant
that depended on a join order. A dead heat now contributes the mean of its
winners' log likelihoods. Three of 1,712 races are dead heats and two fall
after the split, which is enough to move the fourth decimal.

## The speed map explained a blank with its own number

**2026-09-15.** `sarr.MIN_PRIOR` exists because three places each carried a
literal 2 and a page explaining a blank with a different threshold from the one
that caused it explains nothing. `3b67054` moved the rebuild, the evaluator and
the Race Day card onto the constant. It did not reach the speed map, which
carried the literal twice more — once in `jobs/project_card`, which decides who
gets a projection, and once in `query/speedmap`, which writes the sentence
saying why a horse did not.

Those two are not two statements of one rule; they are a rule and a claim about
it, in different files, agreeing only by coincidence. Moving the model's minimum
to three demonstrates what that bought: `project_card` would still have built a
profile from two runs and drawn the horse a bar, while SARR refused to rate the
same horse on Race Day — and `query/speedmap`, asked why a horse HAD no
projection, would have fallen through to `"no early-sectional history"`, naming
a scrape gap for a horse whose history is simply short. Two wrong answers and
neither visible.

Both now read `sarr.MIN_PRIOR`, so the reason on the page is generated from the
number that caused it: `f"fewer than {sarr.MIN_PRIOR} prior runs"`. Changing the
constant in `model/sarr.py` alone now moves the rebuild, the evaluator, the
projection job, its CLI default and both pages together — verified by changing
it to 3 and reading all five back.

The test that was supposed to catch this already named the speed map in its own
docstring and did not check it, which is how the literals survived the commit
that removed the others. It checks both now, including the CLI default, which is
the value that actually runs: `ops/crontab` calls `project_card --pending` and
never passes the flag.

## "Is the model behind?" is a question about the database, so `coverage` answers it

**2026-09-15.** A derived table survives a deploy. The volume is not rebuilt, so
shipping a changed model leaves every existing row written by the old one, and
every page goes on showing them without a word. Rebuilding one archive's
`runner_sarr` from `sarr-1.0` to `sarr-1.1` moved **99.8% of scores, 39.5% of
ranks and the top-rated horse in 19.5% of races** — and the only symptom was the
owner reporting that the ratings still looked wrong while every fix was
committed.

`derive_version` was already the cheap tell and nothing read it. The handover
carried the check as a command instead:

```
fly ssh console -a hkrd -C "python -c \"import sqlite3;print(sqlite3.connect('/data/hkrd.db')...\""
```

which is three levels of quoting, answers one table, and is exactly the shape of
instruction this project has learned not to hand the owner. It is now a section
of `jobs/coverage`, whose stated question — "is the dashboard missing data, or
is it broken?" — this is the third version of. One command, no nested quotes,
the same on the machine, the first PC and a laptop:

```
python -m hkrd.jobs.coverage --db /data/hkrd.db
```

Four decisions inside it:

- **The wanted version is imported, never copied.** `current_versions()` reads
  `DERIVE_VERSION` off the module that stamps the table. A constant duplicated
  into the survey would report every fresh row as stale the day a model moved.
- **Every generation present is listed, not the newest.** A table rebuilt for two
  meetings under a changed model and left alone for the rest is the case the
  column exists to make visible, and a "latest version" row would hide precisely
  that: the new rows read current and the archive is never mentioned.
- **Empty is not stale.** Nothing derived yet needs a first run; a generation
  behind needs a rebuild. Different remedies, so different words.
- **The rebuild command it prints carries the `--db` it was given.** A report read
  off the production machine and pasted back would otherwise rebuild whatever
  `HKRD_DB` points at wherever it was pasted, which is not the database the
  report described.

A stale table is also a gap in `gaps()`, because the rows are there and the
figures they carry are not the ones the code would produce.

## The reason a runner has no rating belongs to the rating, not to a page

**2026-09-15.** Model Analysis named the runners the blend could not rate and
said nothing about why, on the argument that a blank is two different things —
too little history, a RULE that fires on 65.2% of cards, or a card nobody
scored, a FAULT — and that a footer explaining every blank as the two-run rule
would be wrong on exactly the day the fault recurs. That argument was right
about the danger and wrong about the remedy. The fix is to say which of the two
it is, not to say neither.

It could not say which, because the rule lived in `query/raceday._unrated` and
the count it needs was eleven lines of SQL inside `build_card`. A page reaching
into another page's module for a fact is how two pages start disagreeing about
the same horse on the same day, and `query/raceday.py` was 568 of a 600-line cap
so neither piece could grow there. Options A and C of
`docs/proposal-raceday-split.md` were taken.

**`query/rating.py` owns one question:** what does the model know about this
runner, and if nothing, why. `unrated_reason` is the rule, moved unchanged.
`prior_run_counts` is one grouped query for the whole field — per runner it is a
scan apiece, which is the performance rule this project already wrote down.
Race Day, the blend footer and the SARR panel all read it.

**Two panels on one page were answering differently.** `model._unscored` feeds
the SARR panel's NOT SCORED line a few inches above the blend footer, and it
returned bare names. With the footer distinguishing the two kinds and the panel
not, the page called the same debutant "NOT SCORED" and "DEBUT" at once. Both
read `query/rating` now and the panel's line reads NOT RATED, which is what it
means.

**A runner the rebuild did not score has no `runner_sarr` row at all**, so the
LEFT JOIN both pages already do carries a NULL `n_prior` in exactly the case the
footer needs it. That is why the count is a query and not a column, and it is
the thing option B would fix.

**`query/meeting.py`** took `meeting_blackbook` and `meeting_summary` — 93 lines
that touch nothing `build_card` uses. The four helpers below them did not go
with them: `_days_between`, `_place_ratio_range`, `_pairs_meeting_again` and
`_swing_favours` are all called from `build_card` and belong to the card, so the
seam is narrower than the file's shape suggests. `query/raceday.py` is **447**.

**What was NOT done, deliberately.** `jobs/rebuild_sarr` counts prior runs at
`(race_date, race_no) < (today, this_race)` — an earlier race on the SAME day
counts — where the pages count `race_date < today`. No horse runs twice on one
card, so they agree on every row in the archive, and they are still two rules.
Reconciling them changes who the model scores, which is a model change and needs
a walk-forward check of its own; folding it into a page fix is how a rating
silently moves. `rating.PRIOR_RUN_RULE` records the page-side rule and the
discrepancy.

Checked in the browser on a nine-runner card carrying both kinds of blank: the
SARR panel reads `NOT RATED: GOLDEN SIXTY (NOT SCORED), FIRST TIMER (DEBUT)`,
the blend footer names the same two the same way, and a separate line says
`1 OF THOSE HAS 2 OR MORE PRIOR RUNS AND NO RATING — A FAULT RATHER THAN A RULE`.
Race Day shows NOT SCORED in red against that horse and DEBUT in grey against
the other, which is what it showed before and now what the other page shows too.

## `runner_sarr` records what the model declined, not only what it rated

**2026-09-15.** The table held a row per RATED runner, so "no row" meant four
different things: the runner had too little history, the model could not build
a profile, the card had not been scored, or the horse was scratched after it
was. A page reading it could not tell them apart, and one of the four is a fault
somebody can fix.

`rebuild_sarr` now writes a row for every runner it looked at. A runner it
declined gets `sarr` and `sarr_rank` NULL and `n_prior` filled, so the table is
a complete statement of what the model did with a card.

**NOTHING ABOUT THE RATINGS MOVED, and that was checked rather than asserted.**
One synthetic archive, rebuilt twice — once with the job as it was, once with
the job as it is, `model/sarr` identical in both arms so only the change under
test differs. **960 rated rows either way; the same keys; not one score, rank or
`n_prior` different.** 28 unrated rows were added. Races with no row at all went
**2 → 0**, which is the distinction the change exists to create.

**SIX READERS TOOK "THERE IS A ROW" FOR "IT WAS RATED", not the four the audit
in `docs/proposal-raceday-split.md` found.** A LEFT JOIN is safe either way — a
missing row and a NULL column read the same through `s.sarr` — so the exposure
is the inner joins and the non-SQL readers:

- `model/power.py` inner-joined to count the population a variant is measured
  over; without a guard it becomes the whole declared field.
- `query/model.py:sarr_breakdown` inner-joins and orders by `sarr_rank`, and
  **NULL sorts FIRST in SQLite** — unrated runners would have headed a table
  ordered by merit.
- `query/model.py:model_status` prints row counts on the freshness strip. It
  reports `rated` beside `rows` now, or the figure jumps 23% overnight and reads
  as 23% more model.
- `jobs/rebuild_sarr`'s own `skipped_*` counters stopped meaning skipped.
- **`model/evaluate.score`** — missed by a grep for `runner_sarr`, because it
  reads `score_runners`' return rather than the table. A NaN score there would
  have widened the population every variant is measured over, inside the one
  module built to detect that kind of drift. `score_runners` returns the
  declined runners as a third value, so the unpack fails loudly rather than
  quietly carrying them.
- **`jobs/derive_all`** sums the skip counters into the freshness strip.

`tests/test_sarr.py` fails if an inner join on `runner_sarr` is added without
`sarr IS NOT NULL` nearby. Verified by removing the guard from `model/power.py`
and watching it fail.

**WHAT THIS BOUGHT ON THE PAGE.** A race with no rows at all now means one
thing: nothing has scored this card. It used to also mean "the card was scored
and nothing in it could be rated", which is ordinary — a maiden field of
first-starters is exactly that. `rating.race_was_scored` reads it, and
`unrated_reason` takes `card_scored`, which outranks the runner's own history: a
debutant on an unscored card used to read DEBUT in grey, telling the reader
nothing was wrong on a card the model had never looked at. It reads CARD NOT
SCORED in red now, on Race Day and on Model Analysis.

**WHAT IT DID NOT BUY, against the proposal's own prediction.** The proposal
said B would leave one definition of the prior-run count. It leaves two.

- The fault the whole thread is about — a card nobody scored — is defined by the
  job's output being ABSENT, so there is no stored `n_prior` in precisely the
  case the count is needed. `rating.prior_run_counts` stays.
- `rebuild_sarr` counts prior runs at `(race_date, race_no) < (today,
  this_race)`, where an earlier race on the same day counts; the pages count
  `race_date < today`. Reading the stored count on one page and the computed one
  on another mixes two rules instead of removing one. Reconciling them changes
  who the model scores, which is still a model change and still wants its own
  walk-forward check.

One piece of dead code went with it: `rebuild_sarr` stamped
`sarr.DERIVE_VERSION if hasattr(...) else "sarr-1.0"`. The fallback has been
unreachable since `model/sarr` defined the constant, and a version stamp that
can silently be wrong is the thing `jobs/coverage` was just taught to check.

## The table asking whether the model beats the price was answering about a third of races

**2026-09-15.** `races_for_backtest` required a complete book, a recorded
winner, AND every runner rated. The first two are real requirements. The third
was not, and it had already been removed from `fit_blend` on 2026-09-13 for
exactly the reason it should have been removed here.

**"Every runner rated" means "no debutant declared".** SARR rates nothing with
fewer than `sarr.MIN_PRIOR` prior runs, so the condition is a property of the
CARD and not of the model — and **65.2% of the archive's 1,712 races fail it**.
The page built to answer whether the model beats the price was answering on the
third of races that happen to carry no newcomer, and presenting that as the
archive. The same selection, in `fit_blend`, had chosen the published weight on
660 races of 1,712.

**The complete book stays required.** The overround IS the gap between the book
and 100%, so a book missing a runner has a gap that is partly the missing
runner; the de-vig cannot be done without every price. Nothing equivalent is
true of the model. It has an opinion about the runners it rated and none about
the rest, and `blend` already carries that: `w·m + (1−w)·m` is `m`, so an
unrated runner falls through to its market price at every weight. Two rated
runners is the floor, because a softmax over one is 1.0 whatever the score —
`fit_blend` draws the line in the same place.

**THREE PLACES BUILT THE SAME STREAM AND THE ONE THAT BUILT IT DIFFERENTLY WAS
THE ONE THAT DROPPED THE RACES.** The Model Analysis footer, the weight fit and
the backtest each needed "the softmax, scaled to the market's own share of the
runners it rated". Two worked it out; `backtest._probabilities` called the plain
softmax and then required a full field so the scale would be right. It is
`blend.fundamental_for_race` now, called by all three, and a test asserts each
module uses it. On a fully rated field the share is 1.0 and the call is the
plain softmax, which is why every constant fitted before this survives.

**Measured on a synthetic archive seeded to the real debutant rate** — a
newcomer in about two races in three. The selection went from **60 races to
178, 2.97x**, and **66.3% of the widened set carries a runner SARR did not
rate** against the archive's own 65.2%. Every figure moved with it. The
absolute brier and log-loss figures from that run are not transferable — the
finishing order is random — so they are not recorded here; what transfers is
that the population roughly triples and that the arithmetic holds: an unrated
runner's blended probability equals its market price at every weight, and the
column still sums to 1.

**`MEASURED` was NOT guessed at.** Every figure in it was produced under the old
selection and can only be recomputed against the real archive. It carries a
`population` key saying so, `jobs/fit_backtest` regenerates the whole block
ready to paste, and the page prints the population in red above the published
value table — which sits directly under a live calibration computed on a
different set of races, and read as one table before.

---

## Blackbook — an entry ends when it is retired, and only then. **Settled**

The page carried two words for one outcome. `RETIRE` sat on every row, and
beside it `EXPIRED` appeared on rows nobody had touched, because
`promote_to_blackbook` stamped `expiry_date = today + 90 days` on every entry
it created. The Blackbook's own artboard — `web/design-source/Blackbook.dc.html`,
which the rebuild was ported from — has no expiry in it at all: four statuses,
`ALL / ACTIVE / WON OUT / RETIRED`. The concept was introduced by the rebuild
and never went back to the design.

The two were not merely redundant. They said different things about who
decided:

- **RETIRE** is a judgement. The thesis was tested and it failed, or the horse
  left, or the reason it was written no longer applies.
- **EXPIRED** is a clock. Ninety days after an entry was written it closed
  itself, whatever had or had not happened in between.

And the flag was a cache of a date nothing recomputed, so `query/blackbook`
derived `expired` on every read to stop the row disagreeing with the file —
which meant the derivation existed only in the one place that ran it. Race Day
and the Form Guide never did. **Both pages lit a horse's name in the book
colour on the mere existence of an entry**, so a horse booked in March, retired
in June, went on reading as a live thesis on every card after it. That is the
one thing the colour is supposed to mean.

**Decision: `expiry_date` becomes `closed_date`, and closing is always a
decision.**

`closed_date` is the day the decision was taken. It is what an archived card
needs to answer "was I watching this horse THAT day", which is a different
question from "am I watching it now" and the one real job the expiry date was
doing — without it, retiring a horse in December would rewrite every September
card to say the thesis had never been standing.

One rule, `_LIVE_AT_RACE_SQL`, now answers it for both bands and both pages:

```sql
b.added_date <= r.race_date
AND (b.status = 'active'
     OR coalesce(b.closed_date, date('now')) > r.race_date)
```

An entry closed on a day nobody recorded reads as closed **today**. That is the
only reading that satisfies both halves at once: it is certainly not live over
a card being looked at now, and over an archived card it does not erase a
thesis that was, as far as anything knows, standing at the time. Every close
from here on carries its date, so the fallback only ever covers entries retired
before there was a column to record it in.

**The migration moves the meaning before it drops the column** (`store/connect._migrate_blackbook_close`):

| was | becomes |
|---|---|
| `active`, expiry passed | `retired`, closed on that date, reason "lapsed under the old 90-day expiry" |
| `active`, expiry ahead | `active`, no closing date — nobody has closed it |
| `expired`, no date | `retired`, no date, **no reason invented** |
| `won_out` | **untouched.** It is closed because the thesis PAID, and its expiry date is not when that happened |

**Reopening takes a new thesis, and keeps the old one.** `REOPEN` already
existed and set the status back to `active`; what it had no way to record was
that the horse is worth following again *for a different reason*. Writing the
new reason into `blackbook.reasoning` types over the reason the horse was
booked for in the first place — which is the record of a thesis that failed,
and the most useful thing in the book. `blackbook_status_log` holds every
transition with the thesis as it stood at the time; `reasoning` is the head of
that list rather than a substitute for it. A new thesis is refused on a
*closure*, because that would be rewriting history rather than recording it.

The log is keyed on its own autoincrement id, like `bet_edits`, and not on
`(id, changed_at)`: retiring an entry and reopening it in the same second are
two decisions, and a composite key on a second-resolution timestamp silently
kept only the first.

---

## Blackbook — a thesis is a condition, not just a sentence. **Settled**

`docs/proposal-blackbook.md` §2.1, built. The complaint it answers: the module
*"really only functions as a reminder module for myself when manually screening
through races."*

It read that way because it was one. An entry said a horse would run better than
its form suggested, and almost always that claim came with circumstances — at a
trip, on a surface, from a draw. The prose said so and nothing could read it, so
**every run counted equally against the thesis**. A horse booked for 1200m and
beaten four times at 1650m looked like a failed idea. It is an untested one, and
those are different facts.

**The first attempt at this was already in the schema and was write-only.**
`pref_distance`, `pref_surface` and `pref_jockey` were filled by the legacy
import and read by nothing — the only two lines in the codebase that named them
were the two that wrote them. They migrate into `blackbook_trigger`, which is
read by the record, both bands and the Form Guide.

**No new data.** Every `kind` in `query/triggers.KINDS` is a column the archive
already holds and `query/slices.DIMENSIONS` already groups by, so a condition
means the same thing here as it does on Lookup, and nothing has to be derived,
scraped or guessed to check one.

**The record is reported both ways.** `runs_since` over every run, and
`runs_on_conditions` over the runs that asked the question. The difference
between them is itself the finding: a thesis that never gets its race is a
different problem from one that gets it and loses. An entry stating no
conditions is met by every run, which is what the whole book was before this,
and is the right default — a claim with no stated circumstances is a claim about
the horse.

**Three rules worth naming, each with a test that fails without it:**

- **NULL is a failure, not a pass.** A race whose distance was never scraped
  cannot be shown to be the 1200m the entry asked for. `query/gear` draws the
  same line: a NULL column is "this scrape did not carry it", never "there was
  none". Crediting an unknown would quietly hand a thesis runs it never had.
- **A list is comma-wrapped on both sides.** `',11,12,' LIKE '%,1,%'` is false;
  `'11,12' LIKE '%1%'` is true. Without the wrapping a horse drawn 1 satisfies
  "drawn 11 or 12", and every list condition silently widens to anything whose
  text appears inside it. Caught by mutating the SQL and watching the test go
  red — the first version of that test passed against the broken code.
- **Text compares case-folded and trimmed.** "Turf" typed into a form and
  "TURF" off the scrape are one condition. A trigger failing on capitalisation
  would be the worst kind of bug available here: silent, and the horse simply
  stops appearing.

**A condition nothing can evaluate is refused at the point it is written**
(`query/triggers.validate`), and the vocabulary is served from
`/api/blackbook/conditions` rather than copied into the page — a form offering
something the band cannot check would never match, and the book would look empty
rather than broken.

**Cost, measured** on a synthetic archive at the real one's size (1,712 races,
20,544 runners, 196 entries, two thirds of them carrying two conditions each):

| | |
|---|---|
| `list_entries` | 36.7 ms |
| `book_summary` | 23.6 ms |
| `tag_performance` | 24.0 ms |
| `declared_on` / `for_race` | 0.1 ms |

Against a 500 ms budget. The shape is pinned in `tests/test_performance.py`:
one `NOT EXISTS` per run whatever the number of conditions, one query for the
whole book's conditions rather than one per entry, and an index on
`blackbook_trigger(id)`.

**`query/blackbook.py` was split at 628 of the 600-line cap.** `for_race` and
`declared_on` moved to `query/blackbook_band.py` — the same seam `query/meeting`
was carved on. They are the only two functions in the module that take a DATE
rather than an entry, and the only ones that answer "as at that race" rather
than "as it stands". Both are re-exported from `query/blackbook`, because they
ARE the blackbook to every caller outside it and a split in that module is not a
reason for `query/raceday` to learn a second import path.
