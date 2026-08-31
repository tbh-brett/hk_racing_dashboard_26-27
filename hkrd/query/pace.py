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

__all__ = ["race_pace", "race_pace_bulk", "early_speed_z", "PACE_BANDS", "MIN_PACE_PEERS",
           "band_from_z", "band_from_pressure", "measured_pace"]


PACE_BANDS = ("Very Slow", "Slow", "Neutral", "Fast", "Very Fast")

# A z-score against eleven races is not a tempo reading.
MIN_PACE_PEERS = 30

# A projection for a race not yet run has no sectionals to measure, so it is
# built from the field's running styles instead: leaders plus half the
# on-pacers, over the runners that HAVE a style. Cut so the bands mean the same
# thing either way -- one confirmed leader in a field of twelve is a soft lead,
# three is a contested one.
_PRESSURE_CUTS = ((0.10, 0), (0.20, 1), (0.32, 2), (0.45, 3))

# Measured pace is a z-score of the race's own early sectional against every
# other race at the same distance. +-0.5 sd is the ordinary spread; beyond 1.2
# is a race that was genuinely run at an unusual tempo.
_Z_CUTS = ((-1.2, 4), (-0.5, 3), (0.5, 2), (1.2, 1))


def band_from_pressure(pressure: float) -> str:
    for limit, index in _PRESSURE_CUTS:
        if pressure < limit:
            return PACE_BANDS[index]
    return PACE_BANDS[4]


def band_from_z(z: float) -> str:
    """Faster early sectional (a NEGATIVE z, since these are times) is a faster
    race, so the scale is read in reverse."""
    for limit, index in _Z_CUTS:
        if z < limit:
            return PACE_BANDS[index]
    return PACE_BANDS[0]


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
                    "pressure": None, "measured": False, "z": None,
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
            "band": band, "pressure": pressure, "measured": False, "z": None,
            "field_size": len(rows), "unknown": unknown, "counts": counts,
            # Half the field unclassified is not a pace read, it is a guess.
            "confident": known >= max(4, len(rows) * 0.6),
            "leaders": [r["horse_name"] for r in rows if r["style"] == "Leader"],
        }
    finally:
        if own:
            conn.close()


# ── race quality retrospective ───────────────────────────────────────────────

def measured_pace(conn: Connection, date: str, race_no: int) -> dict[str, Any] | None:
    """The race's own early sectional against every race at the distance.

    Returns None when the race has no sectionals, or when there are too few
    comparable races to say what "typical" is — a z-score against eleven races
    is not a tempo reading, and inventing one would be exactly the bare number
    the briefs forbid.
    """
    race = conn.execute(
        "SELECT distance FROM races WHERE race_date = ? AND race_no = ?",
        (date, race_no)).fetchone()
    if not race or not race["distance"]:
        return None

    own = conn.execute(
        "SELECT avg(early_pace) v FROM runner_pace "
        "WHERE race_date = ? AND race_no = ? AND early_pace IS NOT NULL",
        (date, race_no)).fetchone()
    if not own or own["v"] is None:
        return None

    peers = [r["v"] for r in conn.execute("""
        SELECT avg(p.early_pace) v
        FROM runner_pace p
        JOIN races a ON a.race_date = p.race_date AND a.race_no = p.race_no
        WHERE a.distance = ? AND p.early_pace IS NOT NULL
        GROUP BY p.race_date, p.race_no
    """, (race["distance"],)) if r["v"] is not None]
    if len(peers) < MIN_PACE_PEERS:
        return None

    mean = sum(peers) / len(peers)
    sd = (sum((v - mean) ** 2 for v in peers) / len(peers)) ** 0.5
    if not sd:
        return None
    z = (own["v"] - mean) / sd

    field = conn.execute(
        "SELECT count(*) n FROM runners WHERE race_date = ? AND race_no = ?",
        (date, race_no)).fetchone()["n"]
    styles = {r["pace_style"]: r["n"] for r in conn.execute(
        "SELECT pace_style, count(*) n FROM runner_pace "
        "WHERE race_date = ? AND race_no = ? GROUP BY pace_style",
        (date, race_no))}
    return {
        "race_date": date, "race_no": race_no,
        "band": band_from_z(z), "z": round(z, 2), "measured": True,
        "pressure": None, "field_size": field,
        "unknown": max(0, field - sum(styles.values())),
        "counts": styles, "confident": True, "peers": len(peers),
        "leaders": [r["horse_name"] for r in conn.execute(
            "SELECT r.horse_name FROM runners r "
            "JOIN runner_pace p USING (race_date, race_no, horse_no) "
            "WHERE r.race_date = ? AND r.race_no = ? AND p.pace_style = 'Leader'",
            (date, race_no))],
    }


# ── gear ─────────────────────────────────────────────────────────────────────

def race_pace_bulk(keys: Sequence[tuple[str, int]], *,
                   conn: Connection | None = None
                   ) -> dict[tuple[str, int], dict[str, Any]]:
    """Measured pace for many races at once, for a table that spans them.

    `race_pace` answers one race and costs three queries plus a scan of every
    race at that distance. A Lookup grid of 500 runs spans fifty races, and
    calling it per row would be fifty scans and well past the 500ms an endpoint
    is allowed. This does the same arithmetic in two queries regardless of how
    many races are asked for.

    Only MEASURED pace is returned. The projection `race_pace` falls back to is
    an estimate from running styles, and labelling a historic run with an
    estimate — beside its actual time — would read as a measurement.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        wanted = {(str(d), int(n)) for d, n in keys}
        if not wanted:
            return {}

        # Every race's own average early sectional, and its distance.
        rows = conn.execute("""
            SELECT p.race_date, p.race_no, a.distance, avg(p.early_pace) v
              FROM runner_pace p
              JOIN races a ON a.race_date = p.race_date AND a.race_no = p.race_no
             WHERE p.early_pace IS NOT NULL AND a.distance IS NOT NULL
             GROUP BY p.race_date, p.race_no
        """).fetchall()

        by_distance: dict[int, list[float]] = {}
        own_value: dict[tuple[str, int], tuple[int, float]] = {}
        for r in rows:
            by_distance.setdefault(r["distance"], []).append(r["v"])
            key = (r["race_date"], r["race_no"])
            if key in wanted:
                own_value[key] = (r["distance"], r["v"])

        stats: dict[int, tuple[float, float, int]] = {}
        for distance, values in by_distance.items():
            # Fewer than thirty comparable races is not a distribution, and a
            # z-score against eleven of them is a bare number wearing a label.
            if len(values) < MIN_PACE_PEERS:
                continue
            mean = sum(values) / len(values)
            sd = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
            if sd:
                stats[distance] = (mean, sd, len(values))

        out: dict[tuple[str, int], dict[str, Any]] = {}
        for key, (distance, value) in own_value.items():
            if distance not in stats:
                continue
            mean, sd, peers = stats[distance]
            z = (value - mean) / sd
            out[key] = {"band": band_from_z(z), "z": round(z, 2),
                        "measured": True, "peers": peers}
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
