"""The pre-race speed map — one gate ladder per race.

Reads `runner_projection`, which `jobs/project_card` writes. No model code and
no pandas: the query layer's contract is plain data, and every figure on this
page was computed by the job that filled the table.

TWO NUMBERS THAT LOOK ALIKE AND ARE NOT. `bar` is how quickly the horse
habitually GETS AWAY -- the ESZ trait, as a within-race rank. `settle` is where
it is projected to BE at the first call, which folds in the gate and the style.
A horse can be quick away and still settle midfield from gate 14. The page draws
them as different things for that reason, and the brief that specified it spends
a paragraph insisting pace and style are independent axes that must not share a
hue family.

UNPROJECTED RUNNERS ARE RETURNED, NOT DROPPED. A horse with no gate or with
fewer than two prior runs comes back with `settle: None` and a `reason`, and the
page shows it on the ladder with no bar and that reason written out.
`query/model.py:_unscored` is the pattern: name them, do not count them. A zero
bar would read as "breaks at field average", which is a claim about a horse we
know nothing about.
"""
from __future__ import annotations

from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["speed_map", "meeting_speed_map", "MAE"]

# The out-of-sample mean absolute error of the settle projection, on the
# normalised scale. The page states it, converted to positions, because a speed
# map that implies more precision than this is lying.
MAE = 0.198

_SQL = """
SELECT p.race_no, p.horse_no, n.horse_name, n.draw, n.jockey, n.trainer,
       p.esz, p.esz_rank, p.ndraw, p.draw_score, p.settle, p.settle_band,
       p.style, p.n_prior, p.field_size, p.derive_version
FROM runner_projection p
JOIN runners n ON n.race_date = p.race_date
              AND n.race_no  = p.race_no
              AND n.horse_no = p.horse_no
WHERE p.race_date = ?{race_clause}
ORDER BY p.race_no, n.draw IS NULL, n.draw, p.horse_no
"""


def _reason(row: dict[str, Any]) -> str | None:
    """Why this runner has no projection. None when it has one."""
    if row["settle"] is not None:
        return None
    if row["draw"] is None:
        return "no gate declared"
    if (row["n_prior"] or 0) < 2:
        return "fewer than two prior runs"
    return "no early-sectional history"


def _runner(row: Any) -> dict[str, Any]:
    r = dict(row)
    settle = r["settle"]
    return {
        "horse_no": r["horse_no"],
        "horse_name": r["horse_name"],
        "draw": r["draw"],
        "jockey": r["jockey"],
        "trainer": r["trainer"],
        "style": r["style"],
        "esz": round(r["esz"], 4) if r["esz"] is not None else None,
        # 0 = quickest away. The bar length on the page.
        "esz_rank": round(r["esz_rank"], 4) if r["esz_rank"] is not None else None,
        "draw_score": round(r["draw_score"], 4) if r["draw_score"] is not None else None,
        "settle": round(settle, 4) if settle is not None else None,
        "settle_band": r["settle_band"],
        "n_prior": r["n_prior"],
        "reason": _reason(r),
    }


def speed_map(date: str, race_no: int, *,
              conn: Connection | None = None) -> dict[str, Any]:
    """One race's gate ladder, innermost gate first."""
    out = meeting_speed_map(date, race_no=race_no, conn=conn)
    races = out["races"]
    return races[0] if races else {"race_no": race_no, "runners": [],
                                   "unprojected": [], "field_size": 0}


def meeting_speed_map(date: str, *, race_no: int | None = None,
                      conn: Connection | None = None) -> dict[str, Any]:
    """Every race on the card, or one of them."""
    own = conn is None
    conn = conn or get_conn()
    try:
        clause = " AND p.race_no = ?" if race_no is not None else ""
        params: tuple = (date, race_no) if race_no is not None else (date,)
        rows = conn.execute(_SQL.format(race_clause=clause), params).fetchall()
    finally:
        if own:
            conn.close()

    races: list[dict[str, Any]] = []
    by_race: dict[int, list[dict[str, Any]]] = {}
    order: list[int] = []
    version = None
    for row in rows:
        r = dict(row)
        version = version or r["derive_version"]
        if r["race_no"] not in by_race:
            by_race[r["race_no"]] = []
            order.append(r["race_no"])
        by_race[r["race_no"]].append(r)

    for rno in order:
        group = by_race[rno]
        runners = [_runner(r) for r in group]
        projected = [r for r in runners if r["settle"] is not None]
        races.append({
            "race_no": rno,
            "field_size": group[0]["field_size"],
            "runners": runners,
            # Named, not counted -- the page prints these under the ladder.
            "unprojected": [{"horse_no": r["horse_no"],
                             "horse_name": r["horse_name"],
                             "draw": r["draw"],
                             "reason": r["reason"]}
                            for r in runners if r["settle"] is None],
            "projected": len(projected),
        })

    return {
        "race_date": date,
        "races": races,
        "derive_version": version,
        "mae": MAE,
        # Stated in the units a reader thinks in, and recomputed per race on the
        # page because a 14-runner field and an 8-runner field are not the same
        # number of positions.
        "mae_note": "projections carry about +/- 2.4 positions of error "
                    "in a 13-horse field",
    }
