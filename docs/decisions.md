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

## A3 — Where does the database live? **Settled: Postgres on Supabase**

Chosen over local SQLite, Turso and a VPS. Consequences, all reflected in `AGENTS.md`:

- The schema in `REBUILD.md §2` needs porting off SQLite DDL (`REAL` → `DOUBLE PRECISION`,
  `TEXT` dates → `DATE`).
- `store/` is a `psycopg` wrapper, not a `sqlite3` one. The smoke test enforces that no
  other module imports a driver.
- `INSERT ... ON CONFLICT DO UPDATE` carries over unchanged — it is native Postgres syntax.
- `jobs/migrate_legacy.py` is the one sanctioned `sqlite3` importer, for reading the legacy
  file offline.

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
| **A4 — stack** | Streamlit vs the Design HTML on a FastAPI backend. Much of the design (hover panels, inline editing, live odds, sticky headers) is what Streamlit's rerun model fights. Shapes Phase 4. |
| **B2 — odds pruning** | `prune_old_snapshots` is still called at `scrape_hkjc_live_odds.py:500` in the old repo. Every meeting that passes loses data permanently. Independent of this rebuild. |
| **C1 — where does Lab go?** | Design brief 08 dropped Lab from the nav; brief 05 then specified its first content (SARR component breakdown, FUSE blend). Nav currently resolves to seven items with Lab unhomed. |
| **C2 — Trials `RESULT` column** | Empty on every row. Populate with the horse's next start, or remove. |
