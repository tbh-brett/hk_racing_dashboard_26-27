"""Lookup routes — filtered exploration, and the slices computed over it.

The filter vocabulary is declared once in `query/lookup.FILTERS` and read from
there by `_lookup_filters`, so the route signature and the query layer cannot
drift apart. Every panel is computed over the whole filtered slice rather than
the page of rows on screen — the artboard's own line, and it has to be true.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from hkrd.query import lookup as lookup_q, slices as slices_q

router = APIRouter()


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


@router.get("/api/lookup")
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


@router.get("/api/lookup/insight")
def lookup_insight(request: Request, source: str = "race") -> dict:
    """What the slice shows, with its own weakness beside it — n on every
    figure, and the count that would look notable by chance."""
    return lookup_q.insight(source=source, **_lookup_filters(request))


@router.get("/api/lookup/filters")
def lookup_filters() -> dict:
    """The filter vocabulary, so the page renders it from one definition."""
    return {"groups": lookup_q.FILTERS, "sources": list(lookup_q.SOURCES),
            "dimensions": list(slices_q.DIMENSIONS),
            "metrics": list(slices_q.METRICS),
            "min_sample": slices_q.MIN_SAMPLE,
            "outlier_delta": slices_q.OUTLIER_DELTA}


@router.get("/api/lookup/breakdown")
def lookup_breakdown(request: Request, dimension: str = "venue",
                     min_sample: int = slices_q.MIN_SAMPLE) -> dict:
    """One dimension against the filtered slice's own baseline, with an
    interval on every row and the count expected to clear by chance."""
    try:
        return slices_q.breakdown(dimension, min_sample=min_sample,
                                  **_lookup_filters(request))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/lookup/pivot")
def lookup_pivot(request: Request, rows: str = "venue", cols: str = "draw",
                 metric: str = "strike_rate",
                 min_sample: int = slices_q.MIN_SAMPLE) -> dict:
    """Two dimensions crossed, every cell carrying its n."""
    try:
        return slices_q.pivot(rows, cols, metric=metric, min_sample=min_sample,
                              **_lookup_filters(request))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/lookup/outliers")
def lookup_outliers(request: Request, delta: int = slices_q.OUTLIER_DELTA,
                    limit: int = 200) -> dict:
    """Runs whose finish most disagrees with the market's ranking. One run is
    a story, not a signal — so repeats are counted and named."""
    return slices_q.outliers(delta=delta, limit=limit,
                             **_lookup_filters(request))


@router.get("/api/lookup/corpus")
def lookup_corpus() -> dict:
    """What the database holds, for the line every page carries."""
    return slices_q.corpus()
