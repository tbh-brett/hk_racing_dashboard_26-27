"""FastAPI application. Routers reach data through query/ and return JSON.

No SQL here, no HTML here. The Design output in web/ is served statically and
talks to these endpoints over fetch.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from hkrd.api import auth, routes
from hkrd.query import (blackbook as bb_q, formguide as fg_q,
                        health as health_q, market as market_q, model,
                        race as race_q, raceday as raceday_q,
                        vet as vet_q, freshness as fresh_q,
                        pace as pace_q)

WEB = Path(__file__).resolve().parent.parent.parent / "web"

app = FastAPI(title="hkrd", version="0.1.0")

# One shared password in front of everything. Configured at import so a deploy
# that forgot its secret fails here rather than serving the betting ledger to
# the internet — see api/auth for why this refuses to fail open.
auth.configure()
auth.install(app)

# One router per page's domain. Order is not significant between routers, but
# it is WITHIN blackbook's — see the note there.
for _module in (routes.lookup, routes.blackbook, routes.bets, routes.results,
                routes.trials):
    app.include_router(_module.router)


@app.on_event("startup")
def _warm() -> None:
    """Bring the schema up to date, then pay numpy's import cost.

    The schema step is what makes a table added in a later release reach a
    database that predates it — otherwise the page reading it 500s, which is
    the silent-failure class this rebuild exists to remove. Every statement is
    CREATE ... IF NOT EXISTS, so it is a no-op on a current database.

    The warm-up is 1,008ms cold against 5.6ms warm, and on race day the first
    request is the one that matters most.
    """
    from hkrd.jobs import init_store

    init_store.run()
    market_q.warm()


@app.get("/api/health")
def health() -> JSONResponse:
    """Liveness, for the platform. Open, so it says nothing about the data.

    503 rather than a 200 with ok:false — Fly reads the status code, and a
    machine that cannot reach its own database should stop taking traffic
    rather than serve every page as an empty table. Stale data is NOT a
    failure here: killing a healthy machine because Sunday has not happened
    yet turns a quiet week into an outage. Freshness is /api/ops/status.
    """
    try:
        return JSONResponse(health_q.liveness())
    except Exception as exc:            # noqa: BLE001 — reported, not swallowed
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                            status_code=503)


@app.get("/api/ops/status")
def ops_status() -> dict:
    """What is in the database and how old it is. Behind the password.

    The scrape runs at night with nobody watching, and a dashboard serving
    last week looks exactly like one serving today. This is where the
    difference is written down.
    """
    return health_q.status()


@app.get("/api/freshness")
def freshness() -> dict:
    """Per-source freshness, judged against what is normal FOR THAT SOURCE.

    Odds go stale in minutes and barrier trials are published weekly, so one
    shared threshold would call odds fine and trials broken, or the reverse.
    Design brief 07 §6: the system says what needs attention rather than the
    user remembering to check, which is how the pace column went missing for
    weeks in the old system.
    """
    return fresh_q.strip()


@app.get("/api/changes/{date}")
def changes(date: str, since: str | None = None) -> dict:
    """What moved across the meeting since the viewer last looked.

    `since` is per-person state and comes from the page. Without it there is
    nothing to diff, and the answer says so rather than inventing a baseline.
    """
    return market_q.changes_since(date, since)


@app.get("/api/meetings")
def meetings(limit: int = 50) -> list[dict]:
    return race_q.list_meetings(limit=limit)


@app.get("/api/horses")
def horses(limit: int = 400, q: str | None = None) -> dict:
    """The horse index the command palette searches."""
    return {"horses": race_q.list_horses(limit=limit, query=q)}


@app.get("/api/meeting/{date}")
def meeting(date: str) -> dict:
    races = race_q.get_meeting(date)
    if not races:
        raise HTTPException(404, f"no meeting on {date}")
    return {"race_date": date, "races": [r.to_dict() for r in races]}


@app.get("/api/race/{date}/{race_no}")
def one_race(date: str, race_no: int) -> dict:
    r = race_q.get_race(date, race_no)
    if not r.runners:
        raise HTTPException(404, f"no race {race_no} on {date}")
    return r.to_dict()


@app.get("/api/horse/{name}")
def horse(name: str, limit: int = 6, before: str | None = None) -> dict:
    runs = race_q.get_horse_form(name, limit=limit, before=before)
    return {"horse_name": name.upper(), "runs": [r.to_dict() for r in runs]}


# ── form guide ───────────────────────────────────────────────────────────────

@app.get("/api/formguide/{date}/{race_no}")
def form_guide(date: str, race_no: int, history: int = 6) -> dict:
    """One race's card plus each runner's recent form.

    Two query calls. The version this replaces read eight sources and cost
    15.33s; this measures 32ms on the same data.
    """
    guide = fg_q.build_form_guide(date, race_no, history=history)
    if not guide.race.runners:
        raise HTTPException(404, f"no race {race_no} on {date}")
    out = guide.to_dict()
    # When each piece of gear first appeared, over the WHOLE record rather than
    # the six runs shown — otherwise a blinker first worn eight runs back reads
    # as first-time. Design note 03 §3.
    out["gear_first"] = fg_q.gear_timeline(
        [r.horse_name for r in guide.race.runners], before=date)

    # How fast away each run was, standardised inside its own race. The old
    # dashboard's ESZ column and the design's "JUMP z". Computed for every run
    # on screen in one pass rather than per row.
    keys = {(r.race_date, r.race_no) for r in guide.race.runners}
    for runs in guide.history.values():
        keys |= {(r.race_date, r.race_no) for r in runs}
    out["esz"] = {f"{d}:{n}:{h}": v
                  for (d, n, h), v in pace_q.early_speed_z(sorted(keys)).items()}
    return out


@app.get("/api/pace/{date}/{race_no}")
def race_pace(date: str, race_no: int) -> dict:
    """One pace value for the whole race, on the Very Slow → Very Fast scale.

    Measured from the race's own sectionals where it has been run; projected
    from the field's running styles where it has not, and flagged as such.
    """
    out = pace_q.race_pace(date, race_no)
    if not out["field_size"]:
        raise HTTPException(404, f"no race {race_no} on {date}")
    return out


@app.get("/api/notes")
def run_notes(horses: str) -> dict:
    """Run notes for a comma-separated list of horses — one call per card."""
    return {"notes": fg_q.notes_for_horses(horses.split(","))}


@app.post("/api/notes")
def save_run_note(body: dict = Body(...)) -> dict:
    """Write a note on one run. This never creates a blackbook entry.

    Design brief 06 Part 0: most notes are records, not theses, and
    auto-promotion would fill the book with noise. Promotion is /api/blackbook.
    """
    from hkrd.jobs import write_notes

    try:
        return write_notes.save_note(
            body["horse_name"], body["race_date"], int(body["race_no"]),
            body.get("note", ""))
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/blackbook")
def create_blackbook_entry(body: dict = Body(...)) -> dict:
    """Promote a run to a blackbook entry — the deliberate step."""
    from hkrd.jobs import write_notes

    try:
        return write_notes.promote_to_blackbook(
            body["horse_name"],
            reasoning=body.get("reasoning", ""),
            source_date=body.get("source_date"),
            source_race_no=(int(body["source_race_no"])
                            if body.get("source_race_no") is not None else None),
            tags=body.get("tags") or [],
            confidence=body.get("confidence", "medium"))
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/race-quality/{date}/{race_no}")
def race_quality(date: str, race_no: int, top: int = 5) -> dict:
    """The top finishers of a past race and what each did next.

    Turns "won a race" into "won a race whose form held up".
    """
    return {"race_date": date, "race_no": race_no,
            "finishers": fg_q.race_quality(date, race_no, top=top)}


@app.get("/api/condition-fit/{name}")
def condition_fit(name: str, distance: int | None = None, course: str | None = None,
                  going: str | None = None, surface: str | None = None,
                  before: str | None = None) -> dict:
    """How a horse's record looks under a set of conditions.

    Context, not an edge indicator -- every cell carries its sample size and
    declares whether it is thin.
    """
    cells = fg_q.condition_fit(name, distance=distance, course=course, going=going,
                               surface=surface, before=before)
    return {"horse_name": name.upper(), "cells": [c.to_dict() for c in cells]}


@app.get("/api/vet/{name}")
def vet_history(name: str, before: str | None = None, limit: int = 20) -> dict:
    """One horse's veterinary record, newest first, unfiltered.

    Design brief 07 §2 wants recent records only ON THE CARD. Here the question
    is the horse's history, so nothing is dropped for age — the grade travels
    with each record and the page decides how loudly to draw it.
    """
    return {"horse_name": name.upper(),
            "records": vet_q.for_horse(name, before=before, limit=limit)}


@app.get("/api/head-to-head/{horse_a}/{horse_b}")
def head_to_head(horse_a: str, horse_b: str, before: str | None = None) -> dict:
    return fg_q.head_to_head(horse_a, horse_b, before=before)


# ── race day ─────────────────────────────────────────────────────────────────

@app.get("/api/raceday/{date}/blackbook")
def meeting_blackbook(date: str) -> dict:
    """The sticky band's data: booked horses across the whole meeting."""
    return raceday_q.meeting_blackbook(date)


@app.get("/api/raceday/{date}/{race_no}")
def race_card(date: str, race_no: int) -> dict:
    """One race assembled for the card: prices, movement, models, last run."""
    card = raceday_q.build_card(date, race_no)
    if not card["runners"]:
        raise HTTPException(404, f"no race {race_no} on {date}")
    return card


@app.get("/api/raceday/{date}")
def meeting_card(date: str) -> dict:
    summary = raceday_q.meeting_summary(date)
    if not summary["races"]:
        raise HTTPException(404, f"no meeting on {date}")
    return summary


# ── backtest ─────────────────────────────────────────────────────────────────

@app.get("/api/model/backtest")
def model_backtest(split_date: str | None = None, weight: float | None = None,
                   edge: float = 0.0) -> dict:
    """Walk-forward calibration and value, recomputed rather than quoted.

    Two questions, not one: is the model calibrated, and is there anything to
    bet on. A model can be well calibrated and unprofitable, and reporting
    only the second is how a filter that got lucky becomes a rule.
    """
    from hkrd.model import backtest as bt

    body = bt.walk_forward(split_date=split_date, weight=weight, edge=edge)
    body["measured"] = bt.MEASURED
    return body


# ── market ───────────────────────────────────────────────────────────────────

@app.get("/api/market/concentration/{date}/{race_no}")
def concentration(date: str, race_no: int, at: str = "latest") -> dict:
    """Top-3 de-vigged win probability, with the age of the price it used.

    Read early this understates the band in ~60% of races, so the figure
    carries its own staleness rather than presenting as post-time.
    """
    return market_q.concentration(date, race_no, at=at)


@app.get("/api/market/movement/{date}/{race_no}")
def movement(date: str, race_no: int) -> dict:
    return {"race_date": date, "race_no": race_no,
            "runners": market_q.price_movement(date, race_no)}


@app.get("/api/market/coverage")
def coverage() -> dict:
    """Which meetings have odds and which do not.

    A capture that silently did not run is what actually cost the odds history.
    """
    return market_q.odds_coverage()


# ── model transparency (Lab / Model Analysis) ────────────────────────────────

@app.get("/api/model/et/{date}/{race_no}")
def et_race(date: str, race_no: int) -> dict:
    out = model.et_breakdown(date, race_no)
    if not out["runners"]:
        raise HTTPException(404, f"no race {race_no} on {date}")
    return out


@app.get("/api/model/sarr/{date}/{race_no}")
def sarr_race(date: str, race_no: int) -> dict:
    """Per-runner SARR components — why each horse ranked where it did."""
    out = model.sarr_breakdown(date, race_no)
    if not out["runners"] and not out["unscored"]:
        raise HTTPException(404, f"no race {race_no} on {date}")
    return out


@app.get("/api/model/blend/{date}/{race_no}")
def blend_race(date: str, race_no: int, weight: float | None = None) -> dict:
    """The blend's components side by side. `weight` is the share carried by
    the fundamental stream; omit it for the fitted value (0.00)."""
    out = model.blend_breakdown(date, race_no, weight=weight)
    if not out["runners"]:
        raise HTTPException(404, f"no race {race_no} on {date}")
    return out


@app.get("/api/model/et/summary")
def et_summary() -> dict:
    return model.et_reference_summary()


@app.get("/api/status")
def status() -> dict:
    return model.model_status()


@app.post("/api/jobs/import-statement")
def import_statement_job(body: dict = Body(...)) -> JSONResponse:
    """Read an account statement and add its bets to the ledger.

    Reports counts rather than succeeding silently — a bet missing from the
    ledger reads as a bet never placed, and the Blackbook would then call that
    run a missed chance.
    """
    from hkrd.jobs import import_statement

    src = Path(body.get("path", "")).expanduser()
    if not src.exists():
        raise HTTPException(404, f"not found: {src}")
    report = import_statement.run(src, account=body.get("account", "personal"))
    payload = {
        "files": report.files, "bets": report.bets,
        "new_bets": report.new_bets, "selections": report.selections,
        "cash_movements": report.cash_movements,
        "unparsed": report.unparsed, "errors": report.errors,
    }
    return JSONResponse(payload, status_code=200 if not report.errors else 500)


@app.post("/api/jobs/rebuild-et")
def rebuild_et_job(window_months: int = 24) -> JSONResponse:
    """Rebuild ET references and runner_et.

    Reports row counts rather than succeeding silently: a zero must be visible
    immediately, because silent success and silent failure looking identical is
    what let the old pace column sit empty for weeks.
    """
    from hkrd.jobs import rebuild_et as job

    report = job.rebuild(window_months=window_months)
    payload = {
        "runs_loaded": report.runs_loaded,
        "rows_written": report.rows_written,
        "window": report.window,
        "sec_per_length": report.sec_per_length,
        "errors": report.errors,
    }
    return JSONResponse(payload, status_code=200 if not report.errors else 500)


# The root is Race Day. Landing on a directory listing, or a 404, is not a
# useful first impression of a dashboard.
@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/pages/raceday.html")


if WEB.is_dir():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
