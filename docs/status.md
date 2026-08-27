# Build status

Where the rebuild stands, what is verified, and what is not. Figures here were
measured against the real data, not estimated.

---

## Done

### Backend

| Layer | Modules | State |
|---|---|---|
| `store/` | schema, connect, coerce, upsert | Complete. 20 tables, WAL enforced, FK on |
| `ingest/` | `_client`, `results`, `corunning`, `odds`, `statement` | Parsers built and fixture-tested |
| `derive/` | `probability`, `pace`, `et`, `tags`, `trial_quality` | Complete, all run over the full database |
| `model/` | `sarr` | Complete |
| `query/` | `types`, `race`, `formguide`, `model`, `bets`, `bet_analysis`, `blackbook`, `lookup`, `slices`, `market`, `trials`, `results` | Complete for the pages built so far |
| `api/` | `app` + `routes/` (5 routers) | 54 routes |
| `web/` | tokens, overlay, palette, context, Model Analysis, Form Guide, Race Day, Blackbook, Bets, Lookup, Trials, Results | **8 of 8 pages** |

546 tests pass.

### Data in the database

Everything below is populated from the real archive:

| Table | Rows | Source |
|---|---|---|
| `races` | 1,712 | legacy migration |
| `runners` | 21,280 | legacy migration |
| `runner_et` | 21,045 | `rebuild_et` |
| `runner_pace` | 20,903 | `derive/pace` |
| `runner_sarr` | 17,262 | `rebuild_sarr` |
| `runner_tags` | 20,449 | `rebuild_tags` |
| `runner_comments` | 10,775 | `import_legacy_reports` |
| `dividends` | 8,021 | `import_legacy_reports` |
| `trials` | 7,750 | `import_legacy_reports` |
| `odds_snapshots` | 4,289 | `import_legacy_odds` |
| `odds_pairs` | 47,385 | `import_legacy_odds` |
| `bets` | 1,078 | `import_bets` + `import_statement` |
| `bet_selections` | 4,116 | ditto |
| `blackbook` | 196 | `import_blackbook` |

### Measured improvements

| | Before | After |
|---|---|---|
| Form guide build | 15,330 ms | **32 ms** |
| `lbw` values recovered | 20.9% | **90.5%** (14,894 margins rescued) |
| Races with one par time | 23 of 51 | **1,712 of 1,712** |
| Within-race figure ordering correct | 1 of 51 | **all races, 0 violations** |
| Dead heats preserved | 0 | **84** |

### Bugs found and fixed

- **`parse_corunning` was shifted one column** for its entire life. 10,690
  records across 87 meetings, every one carrying a horse number where the name
  belonged, and not one containing any comment prose. This is why lane position
  was being read by OCR from photographs — the objective source was already
  being downloaded and silently discarded.
- **`pd.to_numeric` on `lbw`** discards 79.1% of values, not the 66% estimated.
- **`place` is not an integer** — 302 non-finisher codes and 84 dead heats
  written as `8 DH`, all of which `to_numeric` drops.
- **`horse_id` degrades from April 2026**, not July: 90% → 55% → 0%.
- **The 2026 scraper regression is two failures, not one** — the Chinese/detail
  path died in May, `horse_id` decayed separately to zero by July.
- **The legacy settler missed two winning blocks.** Refs 2217 and 2218 sit in
  the bets log at $0 returned while their own notes quote statement credits of
  $80 and $40. The reconciliation found them; $120 of real returns had been
  missing from the ledger.
- **The statement parser double-counted every split credit.** A "Quinella -
  Quinella Place" block pays one credit across two pools; the original wrote
  the full figure to both halves. On 22 April that made $364.50 of credits read
  as $729.
- **Importing a statement duplicated bets already in the ledger.** The log was
  written from the same statements but under its own ids, and its
  `_bookie_ref` field had been stripped before writing — the reference survived
  only inside the note text. Recovering it from there and matching on it turned
  49 duplicate bets on 26 April back into 0.
- **SARR crashes on a race with no distance** — 5 legacy races, 55 runners,
  whose `venue` column holds a course code rather than ST/HV.

---

## Not done

### Ingest — the largest gap

Ported: `results`, `corunning`, `odds`, `statement`.
Remaining: **`racecard`, `dividends`, `trials`, `vet`**.
(The trials TABLE is populated from the legacy archive; the live scraper is not.)

All are marked green in the extraction map (adapt, do not rewrite) and follow
the pattern the three ported ones establish.

### Pages

All eight are built: Race Day, Form Guide, Lookup, Bets, Blackbook, Results,
Trials, Model Analysis.

Two pieces of a page are deliberately absent, both for the same reason — they
need live prices for an upcoming meeting, and no scraper here has met a live
page:

- the Bets page's pre-bet ticket builder (the ledger, analysis and
  reconciliation ARE built);
- Race Day's live odds refresh, which reads the archive instead.

The token layer, shared overlay, row grammar and API client are all in place,
so these follow an established pattern rather than starting fresh.

### Not started

- `derive/sectionals.py` — sectional decomposition
- `model/fuse.py`, `model/staking.py`, `model/backtest.py`
- `export/pdf.py` — the form guide PDF builder ports directly
- Blackbook storage and the note → blackbook promotion flow
- Deployment: Litestream is configured but never run

---

## Verified vs unverified

**Verified against real data:** every derive module, both models, the query
layer, the migration, both pages, and all three import jobs. Where a documented
figure existed, the rebuild reproduces it — SARR's rank-1 win rate, ET's cell
counts, the form guide's race-quality retrospective matching the design brief's
hand-checked example exactly.

**Not verified:** every scraper's behaviour against live HKJC HTML. The gateway
in the build environment refuses `racing.hkjc.com:443` by policy, so no parser
here has met a real page.

This is mitigated but not resolved. Columns are located by header text rather
than by position, and `ingest/results.py` validates that every field still looks
like itself before returning. A layout change therefore raises, naming the
meeting, the race and each bad column:

```
2026-07-15 HV R1: columns look misaligned — horse_no holds
'FASHION LEGEND (J080)'; draw holds '---'; finish_time holds '11'
```

That is the generalisable form of the corunning lesson: the failure announces
itself instead of producing years of valid-looking nonsense. But it still has to
be run somewhere with network access before the scrapers can be trusted.

### To close that gap

```bash
pip install -e ".[dev]"
python -m hkrd.jobs.scrape_corunning --date 2026-07-15
python -m hkrd.jobs.scrape_meeting --date 2026-07-15 --venue HV --post-race
```

Either it prints row counts, or it raises naming the column that moved.

---

## Open questions

| Item | Status |
|---|---|
| **A1 — security** | The My Bets password is still live in the old repo's `dashboard.py:17770`, alongside two account statements and a 1,078-row bet log, on a public repo. Deferred by decision; still live. |
| **B2 — odds pruning** | `prune_old_snapshots` is still called in the old repo. Every meeting that passes loses data permanently. Independent of this rebuild. |
| **Odds movement figure** | Measured 23% favourite change across 102 races against a documented 44%. Probably methodological; unresolved, so neither figure should be quoted as settled. |
| **C1 — Lab** | Settled: Model Analysis is its own page, making the nav eight items. |
| **C2 — Trials RESULT** | Settled: empty at source, not merely unwired. Place derives from the last running position. |
