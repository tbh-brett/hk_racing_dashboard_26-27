"""FastAPI application. Routers reach data through query/ and return JSON.

No SQL here, no HTML here. The Design output in web/ is served statically and
talks to these endpoints over fetch.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from hkrd.query import (blackbook as bb_q, formguide as fg_q,
                        market as market_q, model, race as race_q,
                        raceday as raceday_q)

WEB = Path(__file__).resolve().parent.parent.parent / "web"

app = FastAPI(title="hkrd", version="0.1.0")


@app.on_event("startup")
def _warm() -> None:
    """Pay numpy's import cost at startup rather than on the first request.

    1,008ms cold against 5.6ms warm, and on race day the first request is the
    one that matters most.
    """
    market_q.warm()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/meetings")
def meetings(limit: int = 50) -> list[dict]:
    return race_q.list_meetings(limit=limit)


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
    return guide.to_dict()


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


# ── blackbook ────────────────────────────────────────────────────────────────

@app.get("/api/blackbook")
def blackbook_list(status: str | None = None, tag: str | None = None) -> dict:
    """The list view. `runs_since` and `record since` are derived from the
    runners table, not from what anyone remembered to log."""
    entries = bb_q.list_entries(status=status, tag=tag)
    return {"entries": entries, "count": len(entries),
            "filters": {"status": status, "tag": tag}}


@app.get("/api/blackbook/tags")
def blackbook_tags() -> dict:
    return {"tags": bb_q.tag_performance()}


@app.get("/api/blackbook/declared/{date}")
def blackbook_declared(date: str) -> dict:
    """Booked horses declared across one meeting."""
    rows = bb_q.declared_on(date)
    return {"race_date": date, "entries": rows, "count": len(rows)}


@app.get("/api/blackbook/{entry_id}")
def blackbook_entry(entry_id: str) -> dict:
    entry = bb_q.entry_detail(entry_id)
    if entry is None:
        raise HTTPException(404, f"no blackbook entry {entry_id}")
    return entry



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


if WEB.is_dir():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
