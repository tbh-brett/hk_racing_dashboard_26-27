"""Fund API routes, /fund/v1 -- mounted on `api/fund_app`, never on the dashboard.

Deliberately NOT in `routes.__all__`: that list is what the public app mounts,
and these must not be reachable from the internet. `tests/test_fund_api`
checks both halves of that.

Each answer carries its `version` as a weak ETag, and a request that sends it
back gets a 304 with no body. Weak, because the body also carries
`updated_at`, which moves when hkrd re-reads an unchanged card: the content is
the same, the bytes are not.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from hkrd.query import fund as fund_q
from hkrd.query import fund_results as fund_r

router = APIRouter(prefix="/fund/v1")

_HEADERS = {"Cache-Control": "no-cache, private"}


def _answer(request: Request, build: Callable[[], dict[str, Any]]) -> Response:
    """Build, then 304 if the caller already holds this version."""
    try:
        body = build()
    except fund_q.BadQuery as exc:
        raise HTTPException(400, str(exc)) from exc
    except fund_q.NotHeld as exc:
        raise HTTPException(404, str(exc)) from exc
    etag = f'W/"{body["version"]}"'
    held = {t.strip() for t in request.headers.get("if-none-match", "").split(",")}
    if etag in held or etag[2:] in held:
        return Response(status_code=304, headers={**_HEADERS, "ETag": etag})
    return JSONResponse(body, headers={**_HEADERS, "ETag": etag})


@router.get("/health")
def health() -> dict:
    """Can hkrd read its database, and when did it last read HKJC."""
    return fund_q.health()


@router.get("/meetings")
def meetings(request: Request, start: str = Query(alias="from"),
             end: str = Query(alias="to")) -> Response:
    """Every meeting hkrd holds a card for, between two dates inclusive."""
    return _answer(request, lambda: fund_q.meetings(start, end))


@router.get("/meetings/{date}/{venue}/card")
def card(request: Request, date: str, venue: str) -> Response:
    """The declared field, brand numbers and scratchings."""
    return _answer(request, lambda: fund_q.card(date, venue))


@router.get("/meetings/{date}/{venue}/odds")
def odds(request: Request, date: str, venue: str, at: str) -> Response:
    """Each race's latest win odds at or before `at`; 404 if none yet."""
    return _answer(request, lambda: fund_r.odds_at(date, venue, at))


@router.get("/meetings/{date}/{venue}/pools")
def pools(request: Request, date: str, venue: str) -> Response:
    """The Six Up legs and investment, from the latest capture."""
    return _answer(request, lambda: fund_q.pools(date, venue))


@router.get("/meetings/{date}/{venue}/results")
def results(request: Request, date: str, venue: str) -> Response:
    """The races run so far; 404 before the first."""
    return _answer(request, lambda: fund_r.results(date, venue))


@router.get("/horses/{brand_no}/runs")
def horse_runs(request: Request, brand_no: str, since: str) -> Response:
    """A horse's runs on or after `since`, by brand number."""
    return _answer(request, lambda: fund_r.horse_runs(brand_no, since))
