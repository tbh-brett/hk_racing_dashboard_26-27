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
