# hkrd — Hong Kong racing dashboard, 2026-27

Rebuild of the previous Streamlit dashboard (`tbh-brett/hk_race_dashboard`), which reached
135 files and ~119k lines with a 22,898-line monolith at the centre.

**If you own this and want to use it, read [`docs/start-here.md`](docs/start-here.md).**
It is written in plain English: one command to open the dashboard, and what the
hosting is for.

**If you are an AI agent picking this up fresh, read
[`docs/handover.md`](docs/handover.md) first** — it says what is built, what is
missing, which decisions are already settled, and the one file you need to ask
the owner for before writing anything.

**Then read `AGENTS.md`.** It is the working
contract, and `tests/test_smoke.py` enforces the parts of it that can be checked
mechanically.

## Running it locally

You need this repo and the old one (`hk_race_dashboard`) side by side — the old
repo holds `hkjc.db`, `reports/`, `cache/live_odds` and `blackbook.json`, which
is where all the data comes from.

```bash
git clone https://github.com/tbh-brett/hk_racing_dashboard_26-27
cd hk_racing_dashboard_26-27
git checkout claude/racing-dashboard-review-t78ucx

python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,migrate]"

# Build the database. One command, about 20 seconds, safe to re-run.
python -m hkrd.jobs.bootstrap --legacy ../hk_race_dashboard

# Start it.
python -m hkrd.serve
```

Then open **http://127.0.0.1:8000** — it lands on Race Day. The API's own docs
are at `/docs`.

`bootstrap` prints what landed at each step, so a source file that is missing
says so rather than leaving a quietly empty table:

```
  migrate      1,712 races · 21,280 runners
  reports      10,775 comments · 8,021 dividends · 7,750 trials
  odds         4,366 snapshots · 48,313 pairs
  bets         1,078 bets · 4,116 selections
  blackbook    196 entries · 331 tag links
  derive       20,903 pace · 21,045 et · 17,262 sarr · 20,449 tags
```

### While I am changing things

`git pull` then re-run `python -m hkrd.serve`. The database only needs
rebuilding when the schema or a derive step changes — the commit message says
when. `python -m hkrd.serve --reload` restarts on code changes, which is what
you want if you are editing alongside.

Every view is a URL, so a page can be shared or bookmarked exactly as you left
it: `?date=2026-05-13&race=4`. **⌘K** (or `/`) opens the command palette, which
reaches every meeting, race, horse and page — it is also the only place the
meeting is chosen.

### Useful commands

```bash
python -m hkrd.jobs.bootstrap --legacy ../hk_race_dashboard   # rebuild everything
python -m hkrd.jobs.derive_all --only pace                    # one derive step
python -m hkrd.jobs.derive_all --date 2026-07-15              # one meeting
python -m hkrd.jobs.fit_blend                                 # re-derive the blend weights
python -m pytest -q                                           # the test suite
```

## The idea

A number appears in exactly one place and is computed exactly once.

Race lookup, form guide, horse performance and results are four **views over one object** —
a runner's line in a race. Build `RunnerLine` once and those four subsystems become four
query calls instead of four pipelines.

## Shape

FastAPI serves JSON; the Claude Design output is the frontend and talks to it over `fetch`.
SQLite sits on a persistent volume next to the API, with Litestream replicating it to object
storage so a single file is safe to depend on.

```
hkrd/
  ingest/    HKJC scrapers. Return plain dicts. Do not know the database exists.
  store/     The only module that imports sqlite3. Coercion happens here, at write time.
  derive/    Reads raw tables, writes derived tables. Every one is droppable.
  model/     ET, SARR, staking. Pure functions.
  query/     The only interface the API may use. Returns RunnerLine.
  api/       FastAPI routers. Import only from query/. Return JSON, never HTML.
  jobs/      CLIs — scrape a meeting, rebuild derived tables, migrate legacy data.
web/         The Design output. Static HTML/CSS/JS. No Python, no database.
ops/         Deployment — Litestream replication config.
```

Imports flow one way: `ingest → store → derive → query → api → web`.

Why this shape: the design specifies flyout filter overlays, viewport-fixed hover panels
with collision detection, live odds updates and persistent expansion state. Those are DOM
requirements, so the frontend stays real HTML. Queries measured 0.000s against SQLite on
this data versus 1.09s for a full-table read — local SQLite keeps that, where a hosted
database would add a network round-trip to every one of them.

## Hosting it

`docs/deploy.md` is the runbook — first deploy, restoring from backup, and what
to do when a scrape fails. The short version of what you sign up for:

| | Why | Cost |
|---|---|---|
| **Fly.io** | Runs the container and holds the volume the database sits on. | ~US$5–7/mo |
| **S3-compatible object storage** (Cloudflare R2, Backblaze B2, or Fly's Tigris) | Litestream streams the SQLite WAL here, so the machine is not the only copy. | free at 33 MB |
| a domain | Optional. `hkrd.fly.dev` already works and is HTTPS. | ~US$12/yr |

**No database service.** The whole thing is a 33 MB file with 342,065 rows, and
a targeted query on it measures 0.000s. Supabase or Neon would put a network
round-trip in front of each of the hundreds of queries a page makes, in
exchange for nothing this workload needs.

**Not serverless.** Vercel, Netlify and Lambda have no persistent writable
filesystem, and SQLite is a file.

```powershell
.\ops\deploy.ps1        # Windows: installs flyctl, creates everything, deploys, verifies
```

`docs/deploy.md` has the same sequence by hand for any other shell.

The dashboard serves the complete betting ledger and the blackbook, so it
refuses to start without `HKRD_PASSWORD` — a deploy that forgot its secret must
fail rather than publish them. `HKRD_ALLOW_NO_AUTH=1` is the explicit opt-out
for a local instance, and the test suite sets it.

## What runs on its own

`ops/crontab`, in Hong Kong time: `jobs.nightly` five times a day and
`jobs.scrape_trials` twice. Neither holds a race calendar. `nightly` reads the
database, works out which meetings are missing results or dividends, and asks
HKJC only about the dates it cannot already answer — a night with nothing
outstanding is a handful of requests and no writes. `scrape_trials` reads
HKJC's own list of trial days, because measured over the 2025-26 archive they
fall on Tue, Thu and Fri equally (26.4% of 159 days each), Mon 16.4%, and Sat
and Wed a handful of times: a Tuesday/Thursday cron would miss 47% of them
silently.

Whether that worked is on the page, not in a log file. `/api/ops/status`
carries `scrape_state` — `ok`, `failed`, `running` or `never` — because a
dashboard serving last week looks exactly like one serving today.
