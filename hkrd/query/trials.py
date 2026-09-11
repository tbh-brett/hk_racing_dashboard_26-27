"""Barrier trials — the batches, and what each run was worth.

Design note 11 and the Trials artboard both put the same requirement on this:

    "One engine, two surfaces: the same finish + margin + comment rating used
     inline on a horse's own trial line, aggregated here as a live feed — not a
     separately curated list."

So the rating is `derive/trial_quality.rate`, called here and nowhere else, and
the Form Guide's inline band reads the same function through `for_horses`. A
second rating in the page would drift from this one within a season.

HKJC publishes no trial margin. It is derived from the batch winner's time at
the 0.16s per length HKJC's own race margins imply, and is SHOWN but does not
enter the rating -- it was measured and does not carry (see
derive/trial_quality).

Distance IS published, in the batch header, and this module said otherwise
until `ingest/trials` was written against the page. The claim was wrong about
the SOURCE, not about the data: no trial in the 7,750-row archive carries a
distance because the legacy import dropped the field. That has been RECOVERED —
`import_legacy_reports` now reads it, along with the draw, rider and stable it
was also dropping — so the archive carries all four. New scrapes carry them, so
a batch reads its distance where it has one and returns None where the archive
never stored one -- which is a gap in the archive, not in HKJC.
"""
from __future__ import annotations

from typing import Any

from hkrd.derive.trial_quality import BANDS, rate
from hkrd.query.types import format_race_time
from hkrd.store.connect import Connection, get_conn

__all__ = ["recent_batches", "batch", "for_horses", "list_horses", "standouts",
           "calibration", "SECONDS_PER_LENGTH"]

# What a length is worth in a trial. HKJC's published race margins imply about
# this over sprint trips, and every trial in the archive is a sprint.
SECONDS_PER_LENGTH = 0.16


def _column(row, name: str):
    """Read a column that may predate this schema version.

    `sqlite3.Row` raises IndexError for a name it does not have, and a
    database written before `distance` was added is exactly the case a page
    must not 500 on.
    """
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


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
        "finish_time": row["finish_time"],
        # m:ss.xx, from the same function every race time on the site uses. A
        # trial is 1000-1200m and comes in around seventy seconds, so the bare
        # float read as "69.53" beside race times written "1:09.53" — the same
        # measurement in two notations on one screen.
        "finish_time_display": format_race_time(row["finish_time"]),
        "margin": margin,
        "venue": row["venue"], "surface": row["surface"],
        "gear": row["gear"], "comment": row["comment_text"],
        # Scraped since the first trials run, stored on every row, and dropped
        # here until now — so the page could not show a draw or a jockey even
        # though both were sitting in the table. The design asks for both.
        "draw": _column(row, "draw"), "jockey": _column(row, "jockey"),
        "trainer": _column(row, "trainer"), "going": _column(row, "going"),
        "section_times": list(_split(row["section_times"], float)),
        "running_positions": list(_split(row["running_positions"], int)),
        "field_size": field_size,
        **{f"quality_{k}": v for k, v in quality.items()},
    }


# A BATCH IS (DATE, VENUE, NUMBER), never (date, number).
#
# HKJC numbers each venue's batches from 1, and two venues run trials on the
# same day: 2026-08-25 had four batches at Conghua AND five at Sha Tin, both
# numbered from 1. Pooled on (date, number) alone, "trial 1" was twenty horses
# instead of ten — and `field_size` and `best_time` are computed here, so every
# margin in those batches was measured against the faster of two different
# tracks (Conghua turf against Sha Tin all-weather) and every quality rating
# was scored against a field twice its real size.
#
# Seven days in the archive have two venues, back to 2025-08-29.
_BATCH_SQL = """
    SELECT t.*,
           (SELECT count(*) FROM trials f
             WHERE f.trial_date = t.trial_date AND f.trial_no = t.trial_no
               AND f.venue IS t.venue) field_size,
           (SELECT min(f.finish_time) FROM trials f
             WHERE f.trial_date = t.trial_date AND f.trial_no = t.trial_no
               AND f.venue IS t.venue) best_time
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
            SELECT r.race_date, r.race_no, r.horse_no, r.place, r.place_code,
                   r.win_odds, a.venue, a.distance, a.race_class,
                   (SELECT count(*) FROM runners f
                     WHERE f.race_date = r.race_date
                       AND f.race_no = r.race_no) field_size
            FROM runners r
            JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
            WHERE r.horse_name = ? AND r.race_date > ?
            ORDER BY r.race_date, r.race_no LIMIT 1""", (horse, after)).fetchone()
        if row is None:
            out[(horse, after)] = None
            continue

        start = dict(row)
        # The tags the stewards' commentary produced for that run, and the
        # comment itself. "It trialled well and then ran 7th" is a different
        # fact from "it trialled well, ran 7th, and was checked at the 800m" —
        # and the second is the one that keeps a horse in the book.
        start["tags"] = [
            t["tag"] for t in conn.execute(
                "SELECT tag FROM runner_tags WHERE race_date = ? AND race_no = ? "
                "AND horse_no = ? ORDER BY tag",
                (row["race_date"], row["race_no"], row["horse_no"]))]
        comment = conn.execute(
            "SELECT comment_text FROM runner_comments WHERE race_date = ? "
            "AND race_no = ? AND horse_no = ?",
            (row["race_date"], row["race_no"], row["horse_no"])).fetchone()
        start["comment"] = comment["comment_text"] if comment else None
        out[(horse, after)] = start
    return out


def days(*, limit: int = 60, venue: str | None = None,
         conn: Connection | None = None) -> list[dict[str, Any]]:
    """The trial calendar, newest first.

    TRIALS ARE NOT MEETINGS. They are held on mornings that are mostly not race
    days, so the meeting in the header cannot address them — asking Layer 1 for
    "21 Aug" gets nothing, because no race was run that day. That is why this
    page needs a day list of its own, and it is not the per-page date picker
    brief 08 §1 forbids: that rule is about four pages disagreeing over which
    MEETING is on screen.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        where, params = ("WHERE venue = ?", [venue]) if venue else ("", [])
        rows = conn.execute(f"""
            SELECT trial_date,
                   count(DISTINCT trial_no) batches,
                   count(*) runners,
                   group_concat(DISTINCT venue) venues
              FROM trials {where}
             GROUP BY trial_date
             ORDER BY trial_date DESC LIMIT ?""", [*params, limit]).fetchall()
        return [{"trial_date": r["trial_date"], "batches": r["batches"],
                 "runners": r["runners"],
                 # group_concat has no ordering guarantee, so it is sorted here
                 # rather than rendered in whatever order SQLite happened to
                 # accumulate it — ST,HV one day and HV,ST the next reads as a
                 # difference between the days.
                 "venues": ",".join(sorted((r["venues"] or "").split(",")))}
                for r in rows]
    finally:
        if own:
            conn.close()


def _booked(conn: Connection, names: list[str]) -> dict[str, dict[str, Any]]:
    """Which of these horses the blackbook already follows."""
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    return {r["horse_name"]: dict(r) for r in conn.execute(
        f"SELECT horse_name, id, status, added_date, reasoning, confidence "
        f"FROM blackbook WHERE horse_name IN ({marks}) "
        f"ORDER BY added_date", [n.strip().upper() for n in names])}


def recent_batches(*, limit: int = 12, venue: str | None = None,
                   date: str | None = None,
                   conn: Connection | None = None) -> list[dict[str, Any]]:
    """The most recent trial batches, newest first, each with its runners.

    `date` pins it to one trial day; without it the feed is simply the newest
    `limit` batches, which may span several mornings.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        clauses, params = [], []
        if venue:
            clauses.append("venue = ?")
            params.append(venue)
        if date:
            clauses.append("trial_date = ?")
            params.append(date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        keys = conn.execute(
            f"SELECT DISTINCT trial_date, trial_no, venue FROM trials {where} "
            f"ORDER BY trial_date DESC, venue, trial_no LIMIT ?",
            [*params, limit]).fetchall()
        return [batch(k["trial_date"], k["trial_no"], venue=k["venue"],
                      conn=conn) for k in keys]
    finally:
        if own:
            conn.close()


def _notes(conn: Connection, date: str, trial_no: int) -> dict[str, dict]:
    """Notes written on one batch, by horse."""
    return {r["horse_name"]: {"note": r["note"], "written_at": r["written_at"]}
            for r in conn.execute(
                "SELECT horse_name, note, written_at FROM trial_notes "
                "WHERE trial_date = ? AND trial_no = ?", (date, trial_no))}


def notes_for_horses(names: list[str], *, conn: Connection | None = None
                     ) -> dict[str, list[dict[str, Any]]]:
    """Every trial note per horse, newest first — for the Form Guide's band.

    The note was written on the Trials page and belongs to the trial, but the
    place it earns its keep is beside that horse's trial line in the form: the
    reason you followed a trial is exactly what you want in front of you when
    the horse turns up in a race.
    """
    if not names:
        return {}
    own = conn is None
    conn = conn or get_conn()
    try:
        marks = ",".join("?" * len(names))
        out: dict[str, list[dict[str, Any]]] = {}
        for r in conn.execute(
                f"SELECT horse_name, trial_date, trial_no, note, written_at "
                f"FROM trial_notes WHERE horse_name IN ({marks}) "
                f"ORDER BY trial_date DESC, trial_no DESC",
                [n.strip().upper() for n in names]):
            out.setdefault(r["horse_name"], []).append(dict(r))
        return out
    finally:
        if own:
            conn.close()


def _is_archived(conn: Connection, trial_date: str) -> bool:
    """Has HKJC moved this trial day behind its archive page?

    The live barrier-trial page shows ONE day — the most recent one held — and
    everything before it is reachable only through the archive, whose URL
    carries the date. The video player takes that page as its return link, so
    which of the two it should be is a fact about the data, answered here
    rather than guessed in the browser from whatever the page happens to have
    loaded.
    """
    row = conn.execute("SELECT max(trial_date) v FROM trials").fetchone()
    return bool(row and row["v"] and str(trial_date) < str(row["v"]))


def batch(date: str, trial_no: int, *, venue: str | None = None,
          conn: Connection | None = None) -> dict[str, Any]:
    """One batch: its runners in finishing order, each rated.

    `venue` is what separates two batches that share a number. It is optional
    only so an existing link without one still resolves — with two venues on
    the date and no venue given, the FIRST is returned and the reply says which,
    rather than silently merging them into a twenty-horse trial.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        if venue is None:
            venues = [r[0] for r in conn.execute(
                "SELECT DISTINCT venue FROM trials "
                "WHERE trial_date = ? AND trial_no = ? ORDER BY venue",
                (date, trial_no))]
            venue = venues[0] if venues else None
        rows = conn.execute(
            f"{_BATCH_SQL} WHERE t.trial_date = ? AND t.trial_no = ? "
            f"AND t.venue IS ? "
            f"ORDER BY t.place IS NULL, t.place, t.horse_name",
            (date, trial_no, venue)).fetchall()
        if not rows:
            return {}
        field_size = rows[0]["field_size"]
        best = rows[0]["best_time"]
        runners = [_runner(r, field_size, best) for r in rows]

        nxt = _next_starts(conn, [(r["horse_name"], date) for r in runners])
        notes = _notes(conn, date, trial_no)
        booked = _booked(conn, [r["horse_name"] for r in runners])
        for r in runners:
            r["next_start"] = nxt[(r["horse_name"], date)]
            r["note"] = notes.get(r["horse_name"])
            # Already followed, so the page can say so rather than offering to
            # add a horse that is in the book twice over.
            r["blackbook"] = booked.get(r["horse_name"])

        # THE REST OF THE BATCH, AND WHAT THEY DID NEXT. Built in memory from
        # rows already fetched rather than a query per runner: a nine-horse
        # batch would otherwise cost nine round trips to say what one already
        # knows. It is the check on the mark — a standout out of a batch whose
        # other five all won next start says more about the batch.
        for r in runners:
            r["retro"] = [
                {"horse_name": o["horse_name"], "place": o["place"],
                 "margin": o["margin"], "next_start": o["next_start"]}
                for o in runners if o["horse_name"] != r["horse_name"]]

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
            # Published in the batch header, so it is read where a scrape
            # stored one. HKJC DOES publish it, in the batch header; the
            # legacy import used to drop it, which is now fixed. None here
            # means this batch's header was not captured, not that there is
            # no distance to capture.
            "distance": _column(rows[0], "distance"),
            "going": _column(rows[0], "going"),
            "course": _column(rows[0], "course"),
            "archived": _is_archived(conn, date),
            # How many in this batch rated above neutral. The batch header
            # says it, so a morning worth opening is visible before it is.
            "flagged": sum(1 for r in runners
                           if r["quality_band"] in ("STANDOUT", "POSITIVE")),
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
        latest = conn.execute("SELECT max(trial_date) v FROM trials").fetchone()
        latest = latest["v"] if latest else None
        # The note was written on the Trials page and belongs to the trial, but
        # the place it earns its keep is here: the reason you followed a trial
        # is what you want in front of you when the horse turns up in a race.
        notes = {(n["horse_name"], n["trial_date"], n["trial_no"]): dict(n)
                 for n in conn.execute(
                     f"SELECT horse_name, trial_date, trial_no, note, written_at "
                     f"FROM trial_notes WHERE horse_name IN ({marks})",
                     [n.strip().upper() for n in names])}
        for row in rows:
            bucket = out.setdefault(row["horse_name"], [])
            if len(bucket) < limit:
                r = _runner(row, row["field_size"], row["best_time"])
                r["note"] = notes.get(
                    (r["horse_name"], r["trial_date"], r["trial_no"]))
                # So the Form Guide's play control can address the video the
                # same way the Trials page does. It has no view of the trials
                # calendar otherwise.
                r["archived"] = bool(latest and str(r["trial_date"]) < str(latest))
                bucket.append(r)
        return out
    finally:
        if own:
            conn.close()


def list_horses(*, limit: int = 20, query: str | None = None,
                conn: Connection | None = None) -> list[dict[str, Any]]:
    """The horse index behind the Trials page search.

    Over `trials`, NOT over `runners`, and that is the whole point: 107 horses
    in the archive have trialled and never raced, and the palette's index --
    which is built from runs -- cannot see any of them. A trials page whose
    search cannot find a horse that has only ever trialled is the wrong index.

    Ordered by most recent trial, because the horse being looked up has usually
    trialled lately, and matched with LIKE on both ends so "sixty" finds
    "GOLDEN SIXTY".
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = ("SELECT horse_name, count(*) trials, max(trial_date) last_trial "
               "FROM trials WHERE horse_name IS NOT NULL AND horse_name != ''")
        params: list[Any] = []
        if query:
            sql += " AND horse_name LIKE ?"
            params.append(f"%{query.strip().upper()}%")
        sql += " GROUP BY horse_name ORDER BY last_trial DESC, trials DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params)]
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


