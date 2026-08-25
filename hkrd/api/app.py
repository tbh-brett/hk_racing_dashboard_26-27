"""FastAPI application. Routers reach data through query/ and return JSON.

No SQL here, no HTML here. The Design output in web/ is served statically and
talks to these endpoints over fetch.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from hkrd.query import (bets as bets_q, blackbook as bb_q,
                        formguide as fg_q, lookup as lookup_q,
                        market as market_q, model, race as race_q,
                        raceday as raceday_q)

WEB = Path(__file__).resolve().parent.parent.parent / "web"

app = FastAPI(title="hkrd", version="0.1.0")


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
def health() -> dict:
    return {"ok": True}


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
    return out


@app.get("/api/pace/{date}/{race_no}")
def race_pace(date: str, race_no: int) -> dict:
    """One pace value for the whole race, on the Very Slow → Very Fast scale.

    Measured from the race's own sectionals where it has been run; projected
    from the field's running styles where it has not, and flagged as such.
    """
    out = fg_q.race_pace(date, race_no)
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


@app.get("/api/head-to-head/{horse_a}/{horse_b}")
def head_to_head(horse_a: str, horse_b: str, before: str | None = None) -> dict:
    return fg_q.head_to_head(horse_a, horse_b, before=before)


# ── lookup ───────────────────────────────────────────────────────────────────

# Declared once so the route signature and the query layer cannot drift apart.
def _lookup_filters(request: Request) -> dict:
    known = {k for group in lookup_q.FILTERS.values() for k in group}
    out: dict = {}
    for key, value in request.query_params.items():
        if key not in known or value == "":
            continue
        out[key] = (value.lower() in ("1", "true", "yes")
                    if key in ("placed", "won")
                    else int(value) if value.lstrip("-").isdigit()
                    else value)
    return out


@app.get("/api/lookup")
def lookup(request: Request, source: str = "race", limit: int = 500,
           order: str = "recent") -> dict:
    """Filtered runs, as the same RunnerLine every other page renders."""
    filters = _lookup_filters(request)
    try:
        runs = lookup_q.search_runs(source=source, limit=limit, order=order,
                                    **filters)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"runs": [r.to_dict() for r in runs], "count": len(runs),
            "filters": filters, "source": source,
            "truncated": len(runs) >= limit}


@app.get("/api/lookup/insight")
def lookup_insight(request: Request, source: str = "race") -> dict:
    """What the slice shows, with its own weakness beside it — n on every
    figure, and the count that would look notable by chance."""
    return lookup_q.insight(source=source, **_lookup_filters(request))


@app.get("/api/lookup/filters")
def lookup_filters() -> dict:
    """The filter vocabulary, so the page renders it from one definition."""
    return {"groups": lookup_q.FILTERS, "sources": list(lookup_q.SOURCES)}


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
    """Per booking reason: strike, place, ROI and A/E with a 95% interval.

    A/E is the figure that says whether a tag beats the PRICE rather than
    merely wins sometimes, so `cleared` counts the tags whose interval excludes
    1.00 and `expected_by_chance` says how many would at 5%. Publishing both is
    what stops a tag that looks like it is working from reading as one.
    """
    tags = bb_q.tag_performance()
    scored = [t for t in tags if t["ae"] is not None]
    cleared = [t["tag"] for t in scored
               if t["ae_lo"] > 1.0 or t["ae_hi"] < 1.0]
    return {"tags": tags, "scored": len(scored), "cleared": cleared,
            "expected_by_chance": round(len(scored) * 0.05, 1)}


@app.get("/api/blackbook/summary")
def blackbook_summary(today: str | None = None) -> dict:
    """How big the book is, and whether it resolves."""
    return bb_q.book_summary(today=today)


@app.post("/api/blackbook/{entry_id}/status")
def set_blackbook_status(entry_id: str, body: dict = Body(...)) -> dict:
    """Resolve an entry. One call, because a book that only grows is unusable."""
    from hkrd.jobs import write_notes

    try:
        return write_notes.set_status(entry_id, body.get("status", ""))
    except KeyError as exc:
        raise HTTPException(404, f"no blackbook entry {exc.args[0]}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/blackbook/backed-vs-missed")
def blackbook_backed_vs_missed(entry_id: str | None = None) -> dict:
    """What was backed, what was not, and how each did.

    Design brief 06 calls this "the single most important feature on the page":
    without it only the hits are visible. It is a join over the bets ledger, so
    nothing has to be logged by hand.
    """
    return bets_q.backed_and_missed(entry_id=entry_id)


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
    entry.update(bb_q.entry_bets(entry_id))
    return entry


# ── bets ─────────────────────────────────────────────────────────────────────

@app.get("/api/bets")
def bets_ledger(date: str | None = None, account: str | None = None,
                limit: int = 500) -> dict:
    rows = bets_q.ledger(date=date, account=account, limit=limit)
    return {"bets": rows, "count": len(rows)}


@app.get("/api/bets/summary")
def bets_summary(account: str | None = None) -> dict:
    return bets_q.summary(account=account)


@app.get("/api/bets/race/{date}/{race_no}")
def bets_for_race(date: str, race_no: int) -> dict:
    return {"race_date": date, "race_no": race_no,
            "bets": bets_q.bets_for_race(date, race_no)}


@app.get("/api/bets/horse/{name}")
def bets_for_horse(name: str, since: str | None = None) -> dict:
    return {"horse_name": name.upper(),
            "bets": bets_q.bets_for_horse(name, since=since)}



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


# The root is Race Day. Landing on a directory listing, or a 404, is not a
# useful first impression of a dashboard.
@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/pages/raceday.html")


if WEB.is_dir():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
