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
| Results | `ingest/results.py` | `jobs/scrape_meeting` | 5×/day via `nightly` |
| Sectionals | `ingest/results.py` (own page) | `jobs/scrape_meeting` | with the results |
| Dividends | `ingest/dividends.py` | `jobs/scrape_meeting` (post-race) | 5×/day via `nightly` |
| Vet records | `ingest/vet.py` | `jobs/scrape_meeting` (post-race) | 5×/day via `nightly` |
| Comments on running | `ingest/corunning.py` | `jobs/scrape_corunning` | with the meeting |
| Barrier trials | `ingest/trials.py` | `jobs/scrape_trials` | 12:00 and 20:00 |
| Live odds | `ingest/odds.py` | `jobs/scrape_odds` | every minute; the job decides |
| Account statements | `ingest/statement.py` | `jobs/import_statement` | by hand — see below |

`ops/crontab` is the schedule; `docs/deploy.md` explains each line.

### Two things that are not scraped

**Bets.** HKJC does not know what anyone staked, so no scrape can recover them.
They arrive from account statements:

```powershell
.\.venv\Scripts\python -m hkrd.jobs.import_statement --src "C:\folder\statement.txt"
```

**Odds are the one JSON source.** They are rendered by JavaScript on
`bet.hkjc.com`, so no fetch of that HTML can see them — but the page is a
single-page app reading `info.cld.hkjc.com/graphql`, declared in the site's own
`/Config/GlobalConfig.js`, and `ingest/odds.py` reads the same endpoint. A whole
meeting is one request. It used to drive Chromium through Playwright, which is
why the deploy image (which carries no browser) could not run it and the cron
line spent a season commented out.

The endpoint whitelists queries: a syntactically valid one it has not seen is
refused with `WHITELIST_ERROR`. The two queries in `ingest/odds.py` are
reproduced from the site's bundle character for character, including fields
nothing reads. Editing one to drop an unused field does not make it smaller, it
makes it fail.

---

## The shape every scraper has

One module per source, and each one:

* **exposes `fetch_*` and `parse_*` separately.** Parsing is testable without a
  network, which is why the odds extraction is tested against recorded replies
  from the live endpoint rather than against a live one.
* **returns plain dicts and does not know the database exists.** Storing is
  `store/`'s job. This is what stops a scraper deciding what the interface may
  see — the old vet scraper scored records and dropped the low ones, so a
  record that existed on the page and failed that filter was simply not there,
  and nothing said so.
* **maps columns by HEADER TEXT and raises on a shape it does not recognise.**
  Never by position. A parser confident about positions it never verified put a
  trainer's name in the horse column and nothing looked wrong for three days.
  The odds capture's version of this is identity rather than headers: HKJC will
  answer about a *different meeting* than the one asked for and say nothing, so
  every pool is checked against the meeting id HKJC publishes for that date and
  venue, and a capture that does not match is refused whole.
* **never deletes.** `prune_old_snapshots` is why only 17 meetings of a full
  season of odds survived, and a test fails if anything like it comes back.

`ingest/_client.py` holds what they share: the session, the URL templates, and
a one-request-per-1.2-seconds throttle across all threads — `fetch_html` for
the pages, `fetch_json` for the odds endpoint, same policy for both. HKJC is a
public site run for punters, not an API with a quota; the courtesy is the point.
`fetch_json` raises on a GraphQL `errors` key as well as on a bad status, because
that endpoint answers 200 with `data: null` and a caller checking only the status
would store a successful capture of nothing.

---

## How you know it ran

Every job records what it wrote to `job_runs`, per source rather than per job —
so a vet scrape that failed while the card succeeded is visible as exactly
that. The freshness strip in the header reads it:

```
Card ✓2h   Odds ⚠47m   Results —   Trials ✓3d   Vet ✓2h
```

Each source is judged against what is normal **for that source**: odds go stale
in fifteen minutes, barrier trials are published weekly.

Race Day and Model Analysis do not wait for the strip. They poll their own
endpoint on an interval the server sets from the capture ladder, so a price
that lands is on screen within half a capture cycle — fifteen seconds inside
the last ten minutes before a race. A poll that finds nothing new is a 304 with
no body. See `hkrd/api/live.py`. Hovering a mark shows
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
