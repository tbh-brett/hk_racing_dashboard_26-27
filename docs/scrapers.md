# Where the scrapers are

Short answer: **`hkrd/ingest/` — one module per source, all of them, nowhere
else.** Nothing scrapes from anywhere but that folder, and a test enforces it.

The old repo had scraping spread across `scrape_hkjc_racecard.py`,
`scrape_hkjc_results.py`, `scrape_hkjc_dividends.py`, `scrape_hkjc_vet.py`,
`scrape_hkjc_trials.py`, `scrape_hkjc_live_odds.py`, `hkjc_client.py` and a
dozen scratch files at the repo root, plus `run_meeting.py` which wrote a fresh
Python file per meeting and executed it. Finding "the scraper" meant knowing
which of those was the real one. That is the thing this layout removes.

---

## The map

Every source, what fetches it, what runs that, and when.

| Source | Module | Job that runs it | Schedule |
|---|---|---|---|
| Race card | `ingest/racecard.py` | `jobs/scrape_meeting` | 5×/day via `nightly` |
| Results + sectionals | `ingest/results.py` | `jobs/scrape_meeting` | 5×/day via `nightly` |
| Dividends | `ingest/dividends.py` | `jobs/scrape_meeting` (post-race) | 5×/day via `nightly` |
| Vet records | `ingest/vet.py` | `jobs/scrape_meeting` (post-race) | 5×/day via `nightly` |
| Comments on running | `ingest/corunning.py` | `jobs/scrape_corunning` | with the meeting |
| Barrier trials | `ingest/trials.py` | `jobs/scrape_trials` | 12:00 and 20:00 |
| Live odds (win, place, QIN, QPL) | `ingest/odds.py` | `jobs/scrape_odds` | every 15 min, 12:00–23:59 |
| Pool turnover | `ingest/turnover.py` | `jobs/scrape_odds` | with the odds |
| Doubles | `ingest/doubles.py` | `jobs/scrape_odds` | with the odds |
| Account statements | `ingest/statement.py` | `jobs/import_statement` | by hand — see below |

`ops/crontab` is the schedule; `docs/deploy.md` explains each line.

### What one odds run reads

Three pages per meeting, through one browser:

| Page | Gives | Table |
|---|---|---|
| `/wpq/<date>/<venue>/<race>` | win, place, and the QIN/QPL matrices | `odds_snapshots`, `odds_pairs` |
| `/turnover/<date>/<venue>/<race>` | money in every pool | `odds_pool_turnover` |
| `/dbl/<date>/<venue>/<leg>` | the doubles grid | `odds_doubles` |

A doubles leg N couples race N with race N+1, so a ten-race card has nine legs
and leg N closes when race N goes off — not race N+1. That, plus dropping races
30 minutes past their off time, is what makes the cost fall through the
afternoon: 29 page loads at midday, 2 by the last race.

`--no-doubles` and `--no-turnover` switch the extra two off. Drop doubles
first: turnover is the denominator every other figure needs, because a price
is a ratio and says nothing about how much money is behind it.

Two things the pages tell you that are easy to misread:

- **A pool with no line is not offered.** Race 3 of 2026-09-06 had six declared
  starters, so HKJC ran no quinella place pool and the turnover page simply
  omitted the row. That absence is a fact. `odds.py` knows the same rule and
  no longer reports an empty QPL matrix as a render failure below seven
  runners.
- **`999` on the doubles grid is a display cap, not a price.** Three digits is
  all the column can hold, so a combination worth several thousand prints 999.
  `query/pools.py` excludes capped cells rather than inverting them.

### Two things that are not scraped

**Bets.** HKJC does not know what anyone staked, so no scrape can recover them.
They arrive from account statements:

```powershell
.\.venv\Scripts\python -m hkrd.jobs.import_statement --src "C:\folder\statement.txt"
```

**Odds need a browser.** They are rendered by JavaScript on `bet.hkjc.com`, so
`ingest/odds.py` drives a real Chromium through Playwright. That is the single
sanctioned use of a browser in this codebase, and Playwright is an optional
extra rather than a dependency — every other page works without it.

---

## The shape every scraper has

One module per source, and each one:

* **exposes `fetch_*` and `parse_*` separately.** Parsing is testable without a
  network or a browser, which is why the odds extraction has tests despite the
  fetch needing Chromium.
* **returns plain dicts and does not know the database exists.** Storing is
  `store/`'s job. This is what stops a scraper deciding what the interface may
  see — the old vet scraper scored records and dropped the low ones, so a
  record that existed on the page and failed that filter was simply not there,
  and nothing said so.
* **maps columns by HEADER TEXT and raises on a shape it does not recognise.**
  Never by position. A parser confident about positions it never verified put a
  trainer's name in the horse column and nothing looked wrong for three days.
* **never deletes.** `prune_old_snapshots` is why only 17 meetings of a full
  season of odds survived, and a test fails if anything like it comes back.

`ingest/_client.py` holds what they share: the session, the URL templates, and
a one-request-per-1.2-seconds throttle across all threads. HKJC is a public
site run for punters, not an API with a quota; the courtesy is the point.

---

## How you know it ran

Every job records what it wrote to `job_runs`, per source rather than per job —
so a vet scrape that failed while the card succeeded is visible as exactly
that. The freshness strip in the header reads it:

```
Card ✓2h   Odds ⚠47m   Results —   Trials ✓3d   Vet ✓2h
```

Each source is judged against what is normal **for that source**: odds go stale
in fifteen minutes, barrier trials are published weekly. Hovering a mark shows
what the last run actually wrote. A zero is visible immediately, because a
scrape that silently captured nothing must never look like one that worked.

---

## Running one by hand

```powershell
.\.venv\Scripts\python -m hkrd.jobs.nightly          # whatever is outstanding
.\.venv\Scripts\python -m hkrd.jobs.scrape_odds      # today's meeting, if any
.\.venv\Scripts\python -m hkrd.jobs.scrape_trials    # trial days not yet held
.\ops\catch-up.ps1 -ShowOnly                         # what the database is missing
```

Every one of them prints row counts. None of them prints "done".
