"""Does the trial rating actually separate anything?

Split out of `query/trials` when that file passed the 600-line cap. It belongs
apart anyway: everything there answers "what happened in this batch", and this
answers "is the band worth printing at all" — a question about the RATING,
asked once over the whole archive rather than per batch.

The rating is only worth showing if the bands separate, which is why the page
prints this beside them rather than asking anyone to take it on trust.
"""
from __future__ import annotations

from typing import Any

from hkrd.derive.trial_quality import BANDS, rate
from hkrd.query.trials import _BATCH_SQL, _margin
from hkrd.store.connect import Connection, get_conn

__all__ = ["calibration"]


def _wilson(hits: int, n: int) -> tuple[float, float] | None:
    """95% Wilson interval on a rate. Small bands here -- STANDOUT is 233 runs
    with a next start -- and Wilson keeps a near-zero cell from claiming a
    certainty its sample cannot support."""
    if n <= 0:
        return None
    z = 1.96
    phat = hits / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * ((phat * (1 - phat) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def _hold_sentence(band: str, row: dict[str, Any],
                   base: dict[str, Any]) -> str:
    """What this band went on to do, in a sentence, from the live numbers.

    The artboard hard-codes one of these per band. Hard-coded, it is a claim
    about the archive that stops being true the first time the archive grows —
    and a calibration figure nobody recomputes is exactly the kind of number
    this page exists to argue against.
    """
    n, win = row["with_next"], row["next_win_rate"]
    if not n or win is None:
        return f"{band} has no next start on record yet."
    pct, basis = f"{win:.1%}", f"{base['next_win_rate']:.1%}"
    if band == "UNTESTED":
        return (f"UNTESTED sits on the baseline at {pct} against {basis} over "
                f"{n:,} next starts — the trial told you nothing either way, "
                f"which is the intent rather than a shortcoming.")
    if not row["clears_baseline"]:
        return (f"{band} went {pct} next-start wins over {n:,}, against a "
                f"baseline of {basis}. The interval contains the baseline, so "
                f"this band is not separating.")
    better = win > (base["next_win_rate"] or 0)
    tail = ("Screening it out is the point." if not better
            else f"{row['next_place_rate']:.1%} placed.")
    return (f"{band} went {pct} next-start wins over {n:,} next starts, "
            f"against a baseline of {basis}. {tail}")


def calibration(*, conn: Connection | None = None) -> dict[str, Any]:
    """What each band actually went on to do at the races.

    The rating is only worth showing if the bands separate, so the page shows
    this beside them rather than asking anyone to take the mark on trust. Over
    the archive:

        STANDOUT  next-win 15.6%   next-place 34.6%
        POSITIVE  next-win 13.1%   next-place 31.9%
        NEUTRAL   next-win  7.8%   next-place 22.1%
        NEGATIVE  next-win  4.2%   next-place 12.0%
        UNTESTED  next-win  7.7%   next-place 23.3%
        baseline  next-win  8.2%   next-place 21.9%

    Recomputed here rather than quoted, so the table on the page is the table
    the archive currently supports.

    UNTESTED landing on the baseline is the design intent, not a shortcoming: a
    trial the horse was not asked to win says nothing about it either way.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(f"""
            SELECT t.trial_date, t.trial_no, t.horse_name, t.place,
                   t.finish_time, t.comment_text, t.field_size, t.best_time,
                   n.place next_place, n.field_size next_field
            FROM ({_BATCH_SQL}) t
            LEFT JOIN (
              SELECT r.horse_name, r.race_date, r.place,
                     (SELECT count(*) FROM runners f
                       WHERE f.race_date = r.race_date
                         AND f.race_no = r.race_no) field_size
              FROM runners r WHERE r.place IS NOT NULL
            ) n ON n.horse_name = t.horse_name
               AND n.race_date = (SELECT min(r2.race_date) FROM runners r2
                                   WHERE r2.horse_name = t.horse_name
                                     AND r2.race_date > t.trial_date
                                     AND r2.place IS NOT NULL)
        """).fetchall()

        buckets: dict[str, dict[str, int]] = {
            b: {"trials": 0, "with_next": 0, "wins": 0, "places": 0}
            for b in BANDS}
        overall = {"trials": 0, "with_next": 0, "wins": 0, "places": 0}
        for row in rows:
            band = rate(place=row["place"], field_size=row["field_size"],
                        margin=_margin(row["finish_time"], row["best_time"]),
                        comment=row["comment_text"])["band"]
            for target in (buckets[band], overall):
                target["trials"] += 1
                if row["next_place"] is None:
                    continue
                target["with_next"] += 1
                target["wins"] += 1 if row["next_place"] == 1 else 0
                placed = 3 if (row["next_field"] or 0) >= 7 else 2
                target["places"] += 1 if row["next_place"] <= placed else 0

        def finish(d: dict[str, int]) -> dict[str, Any]:
            n = d["with_next"]
            return {**d,
                    "next_win_rate": round(d["wins"] / n, 4) if n else None,
                    "next_place_rate": round(d["places"] / n, 4) if n else None,
                    "next_win_ci": list(_wilson(d["wins"], n) or ())}

        base = finish(overall)
        out = {}
        for name, value in buckets.items():
            row = finish(value)
            # Whether the band is DIFFERENT from the baseline, not merely on
            # the other side of it. NEUTRAL at 7.9% against a baseline of 8.2%
            # is the same number; painting it as a shortfall would invent a
            # finding out of a rounding difference.
            # Named `base_rate`, not `rate`: assigning `rate` here would make
            # the imported rating function a local of this whole scope, and
            # the call above it would fail with an UnboundLocalError.
            ci = row["next_win_ci"]
            base_rate = base["next_win_rate"]
            row["clears_baseline"] = bool(
                ci and base_rate is not None
                and (ci[0] > base_rate or ci[1] < base_rate))
            row["hold"] = _hold_sentence(name, row, base)
            out[name] = row
        return {"bands": out, "overall": base, "order": list(BANDS)}
    finally:
        if own:
            conn.close()
