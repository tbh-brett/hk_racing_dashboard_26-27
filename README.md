# hkrd — Hong Kong racing dashboard, 2026-27

Rebuild of the previous Streamlit dashboard (`tbh-brett/hk_race_dashboard`), which reached
135 files and ~119k lines with a 22,898-line monolith at the centre.

**Read `AGENTS.md` first.** It is the working contract, and `tests/test_smoke.py` enforces
the parts of it that can be checked mechanically.

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

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env
python -m pytest tests/ -q
uvicorn hkrd.api.app:app --reload      # once Phase 3 lands
```

## Status

Phase 0.1 — scaffold. No logic yet.
