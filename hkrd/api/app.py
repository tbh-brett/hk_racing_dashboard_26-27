"""FastAPI application. Routers reach data through query/ and return JSON.

No SQL here, no HTML here. The Design output in web/ is served statically and
talks to these endpoints over fetch.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from hkrd.query import model, race as race_q

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
