"""Job routes — running a scrape or a rebuild from the page that noticed.

Design brief 07 §6: the interface for scraping should not be a page of
buttons, because that makes the mechanism the interface and leaves the user
remembering what to run and when. The freshness strip is the inversion — it
already says which source is stale, and these make that diagnosis actionable
in the same place.

Every response carries what the run WROTE. A job that succeeded and stored
nothing is the failure the whole design exists to make visible, so there is no
bare "ok" anywhere in here.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()


_SCRAPE_JOBS = {
    "card": "meeting", "results": "meeting", "vet": "meeting",
    "odds": "odds", "trials": "trials",
}


@router.post("/api/jobs/scrape")
def scrape_job(body: dict = Body(...)) -> JSONResponse:
    """Run one source's scrape now.

    Design brief 07 §6 argues the interface for scraping should not be a page
    of buttons, because that makes the mechanism the interface and leaves the
    user remembering what to run and when. The freshness strip is the
    inversion: it already says which source is stale, and this is what makes
    that diagnosis actionable in the same place — you refresh the thing the
    strip is complaining about, not "a scraper" in the abstract.

    Every response carries what the run WROTE. A job that succeeded and stored
    nothing is the failure this whole design exists to make visible, so there
    is no bare "ok" anywhere in here.
    """
    derived: dict[str, int] = {}
    source = str(body.get("source", "")).lower()
    job = _SCRAPE_JOBS.get(source)
    if job is None:
        raise HTTPException(
            400, f"unknown source {source!r}; expected one of "
                 f"{', '.join(sorted(_SCRAPE_JOBS))}")

    date = body.get("date") or None
    venue = body.get("venue") or None
    try:
        if job == "meeting":
            from hkrd.jobs import scrape_meeting as mj
            if not date or not venue:
                raise HTTPException(
                    400, "a meeting scrape needs a date and a venue; the "
                         "header knows both for the meeting on screen")
            r = mj.scrape_meeting(date, venue, post_race=True)
            # `declared` FIRST, and it was missing entirely. A card scraped
            # before its meeting is run stores a full field and increments
            # nothing else, so the reply said "wrote nothing" about a card that
            # had just landed twelve runners — which is what "I clicked Card
            # and nothing happened" looked like from the outside.
            wrote = {"declared": r.declared, "races": r.races,
                     "runners": r.runners, "comments": r.comments,
                     "dividends": r.dividends, "vet_records": r.vet_records}
            errors, warnings, ok = r.errors, r.warnings, r.ok
            # THE ANALYSIS, which the old dashboard ran automatically and this
            # one left to the nightly job — so a card fetched on the day sat
            # there with an empty SARR column, no edge and no rating until
            # something else happened to run.
            #
            # Which steps are worth running depends on whether the meeting has
            # been run. Before it there are no sectionals, no finishing times
            # and no stewards' reports, so pace, ET and tags have nothing to
            # read; SARR rates today's field from its HISTORY and is the whole
            # of the pre-race analysis. After it, everything applies.
            if ok and (r.declared or r.races):
                from hkrd.jobs import derive_all
                steps = ("sarr",) if r.races == 0 else derive_all.STEPS
                d = derive_all.run(date=date, only=steps)
                # Kept apart from `wrote`, which counts rows FETCHED. A rebuild
                # touches the whole archive, so folding its 17,262 SARR rows
                # into the same total would drown the twelve runners that were
                # actually scraped and make the number mean nothing.
                derived = {k: v for k, v in d.written.items() if v}
                errors = [*errors, *d.errors]
                ok = ok and not d.errors
        elif job == "odds":
            from hkrd.jobs import scrape_odds as oj
            r = oj.run(date, venue)
            wrote = {"races": r.races, "win_place": r.win_place,
                     "pairs": r.pairs}
            errors, warnings = [], [*r.notes, *r.skipped]
            # Nothing to price is not a failure. Most days have no meeting.
            ok = bool(r.races) or not r.attempted
        else:
            from hkrd.jobs import scrape_trials as tj
            r = tj.scrape(date) if date else tj.catch_up(limit=4)
            reports = r if isinstance(r, list) else [r]
            wrote = {"batches": sum(x.batches for x in reports),
                     "runners": sum(x.runners for x in reports),
                     "days": len(reports)}
            errors = [e for x in reports for e in x.errors]
            warnings = [f"{x.date}: none published"
                        for x in reports if x.no_such_day]
            ok = not errors
    except HTTPException:
        raise
    except Exception as exc:
        # A scrape reaches the network and a browser, so it fails for reasons
        # that are about the HOST rather than about the code, and the person
        # clicking the strip is the one who can fix them. A bare 500 tells them
        # nothing; these say which thing is missing and what to do about it.
        text = f"{type(exc).__name__}: {exc}"
        missing_browser = ("Executable doesn't exist" in text
                           or "playwright install" in text.lower()
                           or isinstance(exc, ImportError))
        if missing_browser:
            raise HTTPException(503, (
                "live odds need a real browser and this host has none. "
                "Install one with `python -m playwright install chromium`, or "
                "add it to the image — see docs/deploy.md. Every other source "
                "scrapes without a browser and still works."))
        if "getaddrinfo" in text or "Max retries" in text or "Timeout" in text:
            raise HTTPException(503, (
                f"could not reach HKJC to fetch {source}. The site is up or "
                f"this host cannot get out to it — {text[:160]}"))
        raise HTTPException(500, f"{source} scrape failed — {text[:400]}")

    # 200 whenever the job RAN. Its outcome — landed nothing, HKJC has not
    # published that card yet, three races were short — is data, and it is in
    # the payload. Returning 500 for it said the server broke, and because the
    # payload has no `detail` key the browser fell back to the status line and
    # showed "Card: 500 Internal Server Error": the one message that contains
    # none of what the run actually reported. A genuine fault still raises
    # above this line, where the reason is known and can be said.
    #
    # `detail` is carried too, so any client that reads only that — the shape
    # FastAPI uses for its own errors — still gets the sentence rather than a
    # status code.
    payload = {"source": source, "job": job, "date": date, "venue": venue,
               "wrote": wrote, "total": sum(wrote.values()),
               "derived": derived,
               "ok": ok, "errors": errors, "warnings": warnings}
    if not ok:
        payload["detail"] = "; ".join(errors) or (
            f"{source} scrape ran and stored nothing for "
            f"{date or 'the default date'}"
            + (f" {venue}" if venue else ""))
    return JSONResponse(payload, status_code=200)


@router.post("/api/jobs/rebuild-et")
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
