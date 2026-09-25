"""Race pace as a reading — how fast a race was RUN, and how fast a runner
began inside it.

Split out of `formguide.py`, which grew past the 600-line cap. The split is by
subject rather than by convenience: everything here answers a question about
tempo, and everything here is a property of a RACE or of one runner's place
inside one race, never of a horse across its career.

That distinction is the one this module exists to protect. `derive/pace.py`
computes a runner's own pace figures; `model/sarr.py` carries an `esz` weight
that is a horse's TYPICAL early speed across its history. Neither is what these
functions return, and merging any two of them would make a horse that usually
begins well read as fast away in the run where it missed the kick.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["race_pace", "race_pace_bulk", "early_speed_z", "PACE_BANDS",
           "band_from_pressure", "measured_pace"]


PACE_BANDS = ("Very Slow", "Slow", "Neutral", "Fast", "Very Fast")

# A projection for a race not yet run has no sectionals to measure, so it is
# built from the field's running styles instead: leaders plus half the
# on-pacers, over the runners that HAVE a style. Cut so the bands mean the same
# thing either way -- one confirmed leader in a field of twelve is a soft lead,
# three is a contested one.
_PRESSURE_CUTS = ((0.10, 0), (0.20, 1), (0.32, 2), (0.45, 3))

def band_from_pressure(pressure: float) -> str:
    for limit, index in _PRESSURE_CUTS:
        if pressure < limit:
            return PACE_BANDS[index]
    return PACE_BANDS[4]


def race_pace(date: str, race_no: int, *,
              conn: Connection | None = None) -> dict[str, Any]:
    """How fast this race was run — measured where it can be, projected where
    it cannot.

    Design note 03 §7: pace is one value for the whole race, on the five-step
    Very Slow → Very Fast scale, and it is a different axis from a horse's
    running style. A race that HAS been run is measured from its own early
    sectional against every other race at the distance. A race that has not is
    projected from the field's running styles, which is standard pace
    handicapping — and labelled a projection, never presented as a measurement.

    The projection is only as good as its coverage, so the number of runners
    with no established style travels with it. A field where half the runners
    have never been classified has a pace estimate worth very little, and the
    header has to be able to say so.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        measured = measured_pace(conn, date, race_no)
        if measured:
            return measured
        rows = conn.execute("""
            SELECT r.horse_no, r.horse_name,
                   (SELECT p.pace_style
                      FROM runners h
                      JOIN runner_pace p USING (race_date, race_no, horse_no)
                     WHERE h.horse_name = r.horse_name AND h.race_date < ?
                       AND p.pace_style IS NOT NULL
                     ORDER BY h.race_date DESC, h.race_no DESC LIMIT 1) style
            FROM runners r
            WHERE r.race_date = ? AND r.race_no = ?
            ORDER BY r.horse_no
        """, (date, date, race_no)).fetchall()
        if not rows:
            return {"race_date": date, "race_no": race_no, "band": None,
                    "pressure": None, "measured": False, "early_dev": None,
                    "field_size": 0, "unknown": 0,
                    "counts": {}, "confident": False, "leaders": []}

        counts: dict[str, int] = {}
        for r in rows:
            counts[r["style"] or "Unknown"] = counts.get(r["style"] or "Unknown", 0) + 1
        unknown = counts.get("Unknown", 0)
        known = len(rows) - unknown

        pressure = None
        band = None
        if known:
            # Over KNOWN runners, not the whole field: an unclassified runner is
            # missing evidence, and counting it as "not a leader" would make
            # every thin field look slow.
            pressure = round(
                (counts.get("Leader", 0) + 0.5 * counts.get("On-Pace", 0)) / known, 3)
            band = band_from_pressure(pressure)

        return {
            "race_date": date, "race_no": race_no,
            "band": band, "pressure": pressure, "measured": False, "early_dev": None,
            "field_size": len(rows), "unknown": unknown, "counts": counts,
            # Half the field unclassified is not a pace read, it is a guess.
            "confident": known >= max(4, len(rows) * 0.6),
            "leaders": [r["horse_name"] for r in rows if r["style"] == "Leader"],
        }
    finally:
        if own:
            conn.close()


# ── race quality retrospective ───────────────────────────────────────────────

def _standard_name(key: str | None) -> str:
    return {"G": "Group", "Griffin Race": "Griffin"}.get(key or "", f"Class {key}")


def _reading(row) -> dict[str, Any]:
    """A `race_tempo` row as the pages read it: the band, and the seconds
    that make it checkable."""
    std = _standard_name(row["standard_class"])
    dev = row["early_dev"]
    word = "faster" if dev < 0 else "slower"
    day = row["variant"]
    note = (f"leader {row['early_time']:.2f}s to the {row['early_to']}m, "
            f"{abs(dev):.2f}s {word} than the {std} standard"
            + ("" if row["standard_exact"] else " (no HKJC figure for its own class)")
            + (f"; the day's track ran {100 * day:+.1f}% on standard"
               if day is not None else "; too few races to read the day's track"))
    return {"band": row["band"], "measured": True,
            "early_dev": dev, "early_dev_400": row["early_dev_400"],
            "early_to": row["early_to"], "early_time": row["early_time"],
            "early_std": row["early_std"], "late_dev": row["late_dev"],
            "variant": day, "standard": std,
            "standard_exact": bool(row["standard_exact"]), "note": note}


def measured_pace(conn: Connection, date: str, race_no: int) -> dict[str, Any] | None:
    """How fast the race was run, from `race_tempo` (derive/tempo).

    The leader's time to the 800m against HKJC's standard for the race's own
    course, distance and class, scaled by that day's track. It replaced a
    z-score against every race at the distance, which pooled the two courses,
    every class and every going: on 23 Sep 2026 it called a Happy Valley 1000m
    run exactly to standard "Fast". None when the race has no reading.
    """
    row = conn.execute("SELECT * FROM race_tempo WHERE race_date = ? AND race_no = ?",
                       (date, race_no)).fetchone()
    if row is None:
        return None
    field = conn.execute(
        "SELECT count(*) n FROM runners WHERE race_date = ? AND race_no = ?",
        (date, race_no)).fetchone()["n"]
    styles = {r["pace_style"]: r["n"] for r in conn.execute(
        "SELECT pace_style, count(*) n FROM runner_pace "
        "WHERE race_date = ? AND race_no = ? GROUP BY pace_style",
        (date, race_no))}
    return {
        "race_date": date, "race_no": race_no, **_reading(row),
        "pressure": None, "field_size": field,
        "unknown": max(0, field - sum(styles.values())),
        "counts": styles, "confident": True,
        "leaders": [r["horse_name"] for r in conn.execute(
            "SELECT r.horse_name FROM runners r "
            "JOIN runner_pace p USING (race_date, race_no, horse_no) "
            "WHERE r.race_date = ? AND r.race_no = ? AND p.pace_style = 'Leader'",
            (date, race_no))],
    }


def race_pace_bulk(keys: Sequence[tuple[str, int]], *,
                   conn: Connection | None = None
                   ) -> dict[tuple[str, int], dict[str, Any]]:
    """Measured pace for many races at once, for a table that spans them.

    One indexed read of `race_tempo` for the races asked for. The version this
    replaced read every race's average early sectional in the archive on every
    call to build a z-score, which a stored reading does not need.

    Only MEASURED pace is returned. The projection `race_pace` falls back to is
    an estimate from running styles, and labelling a historic run with an
    estimate — beside its actual time — would read as a measurement.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        wanted = sorted({(str(d), int(n)) for d, n in keys})
        if not wanted:
            return {}
        out: dict[tuple[str, int], dict[str, Any]] = {}
        # In chunks: SQLite binds at most 32,766 values in one statement.
        for i in range(0, len(wanted), 2000):
            chunk = wanted[i:i + 2000]
            values = ",".join("(?,?)" for _ in chunk)
            for row in conn.execute(
                    f"WITH k(race_date, race_no) AS (VALUES {values}) "
                    f"SELECT t.* FROM race_tempo t JOIN k "
                    f"ON k.race_date = t.race_date AND k.race_no = t.race_no",
                    [x for pair in chunk for x in pair]):
                out[(row["race_date"], row["race_no"])] = _reading(row)
        return out
    finally:
        if own:
            conn.close()


def early_speed_z(keys: Sequence[tuple[str, int]], *,
                  conn: Connection | None = None
                  ) -> dict[tuple[str, int, int], float]:
    """How fast away each runner got, standardised inside its own race.

    The old dashboard's ESZ column, and the design's "JUMP z -0.89 slow away".
    Positive is fast away: `early_pace` is a TIME, so the sign is flipped —
    a smaller early sectional is a quicker beginning.

    Standardised WITHIN the race, not against the archive, because a field's
    early sectional is dominated by distance and grade. Compared across races
    every sprinter would read as fast away and every stayer as slow, which
    says nothing about how any of them began relative to what they were beaten
    away by on the day.

    NOT the same quantity as SARR's `esz` component, and deliberately not
    reusing that number. SARR's is a weighted mean of a horse's early
    deviation across its HISTORY — a trait of the horse. This is one
    observation of one run. Merging them would make a horse that usually
    begins well read as fast away in a run where it missed the kick.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        wanted = {(str(d), int(n)) for d, n in keys}
        if not wanted:
            return {}
        rows = conn.execute("""
            SELECT race_date, race_no, horse_no, early_pace
              FROM runner_pace WHERE early_pace IS NOT NULL
        """).fetchall()

        by_race: dict[tuple[str, int], list[tuple[int, float]]] = {}
        for r in rows:
            key = (r["race_date"], r["race_no"])
            if key in wanted:
                by_race.setdefault(key, []).append(
                    (r["horse_no"], r["early_pace"]))

        out: dict[tuple[str, int, int], float] = {}
        for (date, race_no), entries in by_race.items():
            values = [v for _, v in entries]
            # Three runners is not a distribution. A z-score over two of them
            # is arithmetic, not a reading, and it would render as one.
            if len(values) < 4:
                continue
            mean = sum(values) / len(values)
            sd = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
            if not sd:
                continue
            for horse_no, value in entries:
                out[(date, race_no, horse_no)] = round(-(value - mean) / sd, 2)
        return out
    finally:
        if own:
            conn.close()
