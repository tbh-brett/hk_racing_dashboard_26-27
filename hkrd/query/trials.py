"""Barrier trials — the batches, and what each run was worth.

Design note 11 and the Trials artboard both put the same requirement on this:

    "One engine, two surfaces: the same finish + margin + comment rating used
     inline on a horse's own trial line, aggregated here as a live feed — not a
     separately curated list."

So the rating is `derive/trial_quality.rate`, called here and nowhere else, and
the Form Guide's inline band reads the same function through `for_horses`. A
second rating in the page would drift from this one within a season.

HKJC publishes no trial margin and no trial distance. Margin is derived from
the batch winner's time at the 0.16s per length HKJC's own race margins imply,
and is SHOWN but does not enter the rating -- it was measured and does not
carry (see derive/trial_quality). Distance is not derivable and stays null
rather than being guessed from the clock: a horse's trial over an unknown trip
is still a fact; a trip inferred from a time is not.
"""
from __future__ import annotations

from typing import Any

from hkrd.derive.trial_quality import BANDS, rate
from hkrd.store.connect import Connection, get_conn

__all__ = ["recent_batches", "batch", "for_horses", "standouts",
           "calibration", "SECONDS_PER_LENGTH"]

# What a length is worth in a trial. HKJC's published race margins imply about
# this over sprint trips, and every trial in the archive is a sprint.
SECONDS_PER_LENGTH = 0.16


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


def _margin(time: float | None, best: float | None) -> float | None:
    if time is None or best is None:
        return None
    return round(max(0.0, time - best) / SECONDS_PER_LENGTH, 1)


def _split(text: str | None, cast) -> tuple:
    if not text:
        return ()
    out = []
    for part in str(text).replace(";", " ").split():
        try:
            out.append(cast(part))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _runner(row, field_size: int, best: float | None) -> dict[str, Any]:
    margin = _margin(row["finish_time"], best)
    quality = rate(place=row["place"], field_size=field_size, margin=margin,
                   comment=row["comment_text"])
    return {
        "trial_date": row["trial_date"], "trial_no": row["trial_no"],
        "horse_name": row["horse_name"], "place": row["place"],
        "finish_time": row["finish_time"], "margin": margin,
        "venue": row["venue"], "surface": row["surface"],
        "gear": row["gear"], "comment": row["comment_text"],
        "section_times": list(_split(row["section_times"], float)),
        "running_positions": list(_split(row["running_positions"], int)),
        "field_size": field_size,
        **{f"quality_{k}": v for k, v in quality.items()},
    }


_BATCH_SQL = """
    SELECT t.*,
           (SELECT count(*) FROM trials f
             WHERE f.trial_date = t.trial_date AND f.trial_no = t.trial_no) field_size,
           (SELECT min(f.finish_time) FROM trials f
             WHERE f.trial_date = t.trial_date AND f.trial_no = t.trial_no) best_time
    FROM trials t
"""


def _next_starts(conn: Connection, pairs: list[tuple[str, str]]
                 ) -> dict[tuple[str, str], dict[str, Any]]:
    """What each horse did at the RACES after its trial.

    "NEXT ACTUAL START SHOWS WHAT THE HORSE DID AT THE RACES AFTER THIS TRIAL,
    NOT ANOTHER TRIAL" -- the artboard, in capitals, because the two are easy
    to conflate and only one of them settles anything.
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for horse, after in pairs:
        row = conn.execute("""
            SELECT r.race_date, r.race_no, r.place, r.place_code, r.win_odds,
                   a.venue, a.distance, a.race_class,
                   (SELECT count(*) FROM runners f
                     WHERE f.race_date = r.race_date
                       AND f.race_no = r.race_no) field_size
            FROM runners r
            JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
            WHERE r.horse_name = ? AND r.race_date > ?
            ORDER BY r.race_date, r.race_no LIMIT 1""", (horse, after)).fetchone()
        out[(horse, after)] = dict(row) if row else None
    return out


def recent_batches(*, limit: int = 12, venue: str | None = None,
                   conn: Connection | None = None) -> list[dict[str, Any]]:
    """The most recent trial batches, newest first, each with its runners."""
    own = conn is None
    conn = conn or get_conn()
    try:
        where, params = ("WHERE venue = ?", [venue]) if venue else ("", [])
        keys = conn.execute(
            f"SELECT DISTINCT trial_date, trial_no FROM trials {where} "
            f"ORDER BY trial_date DESC, trial_no LIMIT ?",
            [*params, limit]).fetchall()
        return [batch(k["trial_date"], k["trial_no"], conn=conn) for k in keys]
    finally:
        if own:
            conn.close()


def batch(date: str, trial_no: int, *,
          conn: Connection | None = None) -> dict[str, Any]:
    """One batch: its runners in finishing order, each rated."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            f"{_BATCH_SQL} WHERE t.trial_date = ? AND t.trial_no = ? "
            f"ORDER BY t.place IS NULL, t.place, t.horse_name",
            (date, trial_no)).fetchall()
        if not rows:
            return {}
        field_size = rows[0]["field_size"]
        best = rows[0]["best_time"]
        runners = [_runner(r, field_size, best) for r in rows]

        nxt = _next_starts(conn, [(r["horse_name"], date) for r in runners])
        for r in runners:
            r["next_start"] = nxt[(r["horse_name"], date)]

        # Splits are the batch's, not the runner's: HKJC publishes one set of
        # sectionals per trial and repeats it on every row.
        splits = runners[0]["section_times"]
        return {
            "trial_date": date, "trial_no": trial_no,
            "venue": rows[0]["venue"], "surface": rows[0]["surface"],
            "field_size": field_size,
            "winning_time": best,
            "section_times": splits,
            "runners": runners,
            # No distance is published for a trial, and inferring one from the
            # clock would be a guess dressed as a fact.
            "distance": None,
        }
    finally:
        if own:
            conn.close()


def for_horses(names: list[str], *, before: str | None = None, limit: int = 2,
               conn: Connection | None = None) -> dict[str, list[dict[str, Any]]]:
    """Each horse's most recent trials, rated — the Form Guide's inline band.

    `before` keeps the band honest on a past race: a trial run AFTER the race
    being reviewed was not available when the race was run, and showing it
    would let hindsight into a form guide.
    """
    if not names:
        return {}
    own = conn is None
    conn = conn or get_conn()
    try:
        marks = ",".join("?" * len(names))
        clause = " AND t.trial_date < ?" if before else ""
        params: list[Any] = [n.strip().upper() for n in names]
        if before:
            params.append(before)
        rows = conn.execute(
            f"{_BATCH_SQL} WHERE t.horse_name IN ({marks}){clause} "
            f"ORDER BY t.horse_name, t.trial_date DESC", params).fetchall()
        out: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            bucket = out.setdefault(row["horse_name"], [])
            if len(bucket) < limit:
                bucket.append(_runner(row, row["field_size"], row["best_time"]))
        return out
    finally:
        if own:
            conn.close()


def standouts(*, days: int = 21, bands: tuple[str, ...] = ("STANDOUT", "POSITIVE"),
              limit: int = 40, conn: Connection | None = None) -> dict[str, Any]:
    """The live feed: recent trials that rated well, with what the rest of the
    batch did next.

    Not a curated list -- the same rating every trial gets, filtered. A list
    somebody maintained by hand would say more about who maintained it than
    about the trials.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        latest = conn.execute("SELECT max(trial_date) FROM trials").fetchone()[0]
        if not latest:
            return {"since": None, "latest": None, "runs": [], "considered": 0,
                    "bands": list(bands), "days": days}
        since = conn.execute("SELECT date(?, ?)", (latest, f"-{days} days")).fetchone()[0]
        rows = conn.execute(
            f"{_BATCH_SQL} WHERE t.trial_date >= ? "
            f"ORDER BY t.trial_date DESC, t.trial_no, t.place", (since,)).fetchall()

        considered = len(rows)
        rated = [_runner(r, r["field_size"], r["best_time"]) for r in rows]
        picked = [r for r in rated if r["quality_band"] in bands][:limit]

        nxt = _next_starts(conn, [(r["horse_name"], r["trial_date"])
                                  for r in picked])
        for r in picked:
            r["next_start"] = nxt[(r["horse_name"], r["trial_date"])]
            # What the REST of the batch did next is the check on the rating:
            # a standout out of a batch whose other five all won next start
            # says more about the batch than about the horse.
            r["batch_next"] = _batch_next(conn, r["trial_date"], r["trial_no"],
                                          exclude=r["horse_name"])
        booked = _booked(conn, [r["horse_name"] for r in picked])
        for r in picked:
            r["blackbook"] = booked.get(r["horse_name"])
        return {
            "since": since, "latest": latest, "days": days,
            "runs": picked, "considered": considered,
            "shown": len(picked), "bands": list(bands),
        }
    finally:
        if own:
            conn.close()


def _batch_next(conn: Connection, date: str, trial_no: int, *,
                exclude: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT horse_name, place FROM trials "
        "WHERE trial_date = ? AND trial_no = ? AND horse_name != ? "
        "ORDER BY place IS NULL, place", (date, trial_no, exclude)).fetchall()
    nxt = _next_starts(conn, [(r["horse_name"], date) for r in rows])
    return [{"horse_name": r["horse_name"], "place": r["place"],
             "next_start": nxt[(r["horse_name"], date)]} for r in rows]


def _booked(conn: Connection, names: list[str]) -> dict[str, dict[str, Any]]:
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    return {r["horse_name"]: dict(r) for r in conn.execute(
        f"SELECT horse_name, id, status, added_date FROM blackbook "
        f"WHERE horse_name IN ({marks})", [n.upper() for n in names])}


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
            out[name] = row
        return {"bands": out, "overall": base, "order": list(BANDS)}
    finally:
        if own:
            conn.close()
