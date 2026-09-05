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
