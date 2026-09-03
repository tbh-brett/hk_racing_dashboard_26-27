# Build status

Where the rebuild stands, what is verified, and what is not. Figures here were
measured against the real data, not estimated.

---

## Done

### Backend

| Layer | Modules | State |
|---|---|---|
| `store/` | schema, connect, coerce, upsert | Complete. 21 tables, WAL enforced, FK on |
| `ingest/` | `_client`, `results`, `corunning`, `odds`, `statement`, `racecard`, `dividends`, `trials`, `vet` | Parsers built and fixture-tested |
| `derive/` | `probability`, `pace`, `et`, `tags`, `trial_quality`, `sectionals` | Complete, all run over the full database |
| `model/` | `sarr`, `blend`, `backtest` | Complete |
| `query/` | `types`, `race`, `formguide`, `model`, `bets`, `bet_analysis`, `blackbook`, `lookup`, `slices`, `market`, `trials`, `results` | Complete for the pages built so far |
| `api/` | `app` + `routes/` (5 routers) | 55 routes |
| `web/` | tokens, overlay, palette, context, Model Analysis, Form Guide, Race Day, Blackbook, Bets, Lookup, Trials, Results | **8 of 8 pages** |

635 tests pass.

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

### Ingest — complete

All nine parsers are written: `results`, `corunning`, `odds`, `statement`,
`racecard`, `dividends`, `trials`, `vet`, and the shared `_client`.

Every one locates its columns by HEADER TEXT and validates the shape of what
it read before returning. **None has met a live page** — see "Verified vs
unverified" below.

Two things the parsers recovered that the archive does not have:

- ~~**Trial distance and going.**~~ **Recovered.** HKJC publishes both in the
  batch header, and the draw, rider and stable on every row; the legacy import
  read none of them, so all 7,750 archived trials came through without them
  and the columns showing them read as empty data rather than as a lossy
  import. `import_legacy_reports` now reads all six — distance, going, course,
  draw, jockey, trainer — plus lengths-beaten through a fraction parser rather
  than a numeric coercion that would have dropped 79% of it. Re-run the
  reports step to backfill: 7,750 of 7,750 for everything except jockey, where
  107 rows are genuinely blank at source.
- **Veterinary records at all.** There is no vet table in the archive. The old
  scraper computed a "concern score" and then filtered its own output by it,
  so a record that existed on the page and scored below a threshold was simply
  not there. `ingest/vet` returns every record with its category and its age
  and lets the caller decide.

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

### Deployed

Live at https://hkrd.fly.dev — one machine in `sin`, one 1 GB volume, the
database on it, Litestream replicating to R2 on a 10s interval. Verified
2026-09-03: health 200, root 303 to the sign-in page, `/api/ops/status` 401
without a session, HTTP 301 to HTTPS, and two generations in the bucket, the
live one a 12 MB snapshot of the real database.

`sin` and not `hkg`: Fly retired Hong Kong in its 2025 region consolidation.
The comment in fly.toml says why Singapore and not Tokyo.

Five things had to be fixed before it would deploy at all, and none of them
could have been caught by a test, because every one is about what LEAVES this
machine rather than what runs on it. 959 tests passed throughout.

- CRLF line endings, so the container read its shebang as an interpreter
  named "bash" with a carriage return on the end, and died naming a file
  that was plainly there
- No .dockerignore, so 345 MB of .venv and database went to the builder
- `fly sftp put` against a stopped machine, which cannot work: the SSH server
  is a process inside the VM
- `2>&1` on a native command being TERMINATING under
  `$ErrorActionPreference = "Stop"`, which killed the sign-in step in the exact
  case it existed to handle
- PowerShell prepending a byte order mark to anything piped to a native
  program, which made the first secret unnameable

The sixth was not ours. The first R2 token's access key id and secret were not
a pair, and R2 answers that with a 403 saying "check your secret access key" —
equally true of four other mistakes. The entrypoint refused to start rather
than come up empty and replicate that emptiness over the backup within ten
seconds. The guard worked exactly as designed; the diagnosis did not exist, and
now does.

`derive/sectionals.py` is built. `export/pdf.py` is **dropped** — the owner
confirmed the form-guide PDF is no longer wanted.

`model/fuse.py` is built, under the name `model/blend.py`. `model/backtest.py`
is built. **`model/staking.py` is deliberately NOT built** — see below.

Blackbook storage and the note → blackbook promotion flow ARE built. The form
lives in `web/assets/review.js` and is called by BOTH the Form Guide and
Results — the Results artboard asks for exactly that ("same form as the Form
Guide — reviewing and booking is one action"), and a smoke guard holds it to
one caller.

---

## The model does not beat the price

This is the largest finding in the rebuild and it is negative, so it is stated
here rather than left in a module docstring. Walk-forward over 596 usable
races (train: 327 before 2025-12-14, test: 269 from it, 3,321 test runners):

**The de-vigged market is well calibrated.** All nine reliability bins fall
inside the interval their outcomes support — none off. Brier 0.06788, log loss
2.0983.

| predicted band | n | model says | actual | 95% CI |
|---|---|---|---|---|
| 0–2% | 662 | 1.2% | 1.4% | [0.7%, 2.6%] |
| 2–5% | 817 | 3.4% | 2.5% | [1.6%, 3.8%] |
| 5–8% | 566 | 6.2% | 6.4% | [4.6%, 8.7%] |
| 8–12% | 553 | 9.5% | 10.7% | [8.4%, 13.5%] |
| 12–18% | 369 | 14.5% | 14.9% | [11.6%, 18.9%] |
| 18–25% | 213 | 21.1% | 19.3% | [14.5%, 25.1%] |
| 25–35% | 102 | 29.1% | 31.4% | [23.2%, 40.9%] |
| 35–50% | 33 | 41.3% | 42.4% | [27.2%, 59.2%] · thin |
| 50–100% | 6 | 66.6% | 66.7% | [30.0%, 90.3%] · thin |

**There is nothing to bet on.** At the fitted blend weight of zero the model IS
the market, so there are no value bets by construction. At every positive
weight the answer is a loss, and it gets *worse* as more disagreement is
required:

| weight on the model | edge required | bets | strike | ROI |
|---|---|---|---|---|
| 0.05 | 10% | 555 | 1.3% | −42.2% |
| 0.10 | 10% | 1,007 | 1.4% | −50.1% |
| 0.10 | 25% | 411 | 1.0% | −59.4% |
| 0.25 | 10% | 1,563 | 2.4% | −36.5% |
| 1.00 | 10% | 1,952 | 3.4% | −30.2% |

Demanding a bigger disagreement selects the horses the market is most
confident the model is wrong about, and the market is right about them. That
is the signature of no signal, not of a signal too small to exploit.

**This is why `model/staking.py` is not in the package.** Kelly sizing on an
edge that has not been shown to exist compounds the error rather than
exploiting it, and "is there an edge" has to be answered before "how much".

The finding is recomputed live at `/api/model/backtest` and rendered on Model
Analysis, so a rerun that disagrees with the table above is visible rather
than silent.

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
