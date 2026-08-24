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

## Layout

```
hkrd/
  ingest/    HKJC scrapers. Return plain dicts. Do not know the database exists.
  store/     The only module that talks to Postgres. Coercion happens here.
  derive/    Reads raw tables, writes derived tables. Every one is droppable.
  model/     ET, SARR, staking. Pure functions.
  query/     The only interface the UI may use. Returns RunnerLine.
  ui/        Streamlit. Imports only from query/.
  jobs/      CLIs — scrape a meeting, rebuild derived tables, migrate legacy data.
```

Imports flow one way: `ingest → store → derive → query → ui`.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env          # then fill in the Supabase connection string
python -m pytest tests/ -q
```

## Status

Phase 0.1 — scaffold. No logic yet.
