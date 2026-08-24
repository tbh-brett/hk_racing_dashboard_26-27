# hkrd — Hong Kong racing dashboard, 2026-27

Rebuild of the previous Streamlit dashboard (`tbh-brett/hk_race_dashboard`), which reached
135 files and ~119k lines with a 22,898-line monolith at the centre.

**Read `AGENTS.md` first.** It is the working contract, and `tests/test_smoke.py` enforces
the parts of it that can be checked mechanically.

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
