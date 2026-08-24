"""FastAPI application. Routers reach data through query/ and return JSON.

No SQL here, no HTML here. The Design output in web/ is served statically and
talks to these endpoints over fetch.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from hkrd.query import formguide as fg_q, model, race as race_q

WEB = Path(__file__).resolve().parent.parent.parent / "web"

app = FastAPI(title="hkrd", version="0.1.0")


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


# ── model transparency (Lab / Model Analysis) ────────────────────────────────

@app.get("/api/model/et/{date}/{race_no}")
def et_race(date: str, race_no: int) -> dict:
    out = model.et_breakdown(date, race_no)
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
