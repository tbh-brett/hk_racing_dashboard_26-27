"""Every declared runner's recent trials, race by race: the Trials page's
DECLARED view.

Asked for on 2026-10-08: *the recent trials of all declared runners across
the races, for the coming race day.* The reason it earns a page of its own is
measured (docs/book-tiers.md): the owner's trial note, written before the
race, is the one note in the book with a record clear of the price, and a
note can only be written on a trial somebody watched. The batch view is laid
out by trial morning; the question before a meeting is laid out by RACE --
which of Sunday's runners trialled since they last ran, have I watched those,
and what did I make of them.

So each runner carries its last `RECENT` trials, each marked `since_last`
(run after the horse's last start, or the horse has never started), with the
owner's note on it if there is one, rated by the one engine every page uses
(`query/trials`). The book's tier rides along from `query/book_tier`, so a
horse already flagged TRIAL NOTE says so here too. AS AT THE CARD: nothing on
or after the meeting date is read.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from hkrd.query import blackbook_band, book_tier, trials as trials_q
from hkrd.store.connect import Connection, get_conn

__all__ = ["RECENT", "meeting"]

# Trials per horse. A horse's preparation between two runs is usually one or
# two trials; four reaches back past the last run for most of the field, so
# the view can show what came before as well as what is new.
RECENT = 4

# What a trial line on this view reads. The rating engine returns its whole
# working (every clause it read, each factor's points); the batch view shows
# that on expansion, this view links to it, and carrying it for ~80 trials made
# the answer 513 KB.
_TRIAL_KEYS = ("trial_date", "trial_no", "venue", "surface", "going", "place",
               "field_size", "margin", "finish_time", "gear", "jockey", "trainer",
               "draw", "comment", "running_positions", "quality_band",
               "quality_mark", "quality_reasons", "note", "archived")

_CARD_SQL = """
    SELECT r.race_no, r.horse_no, r.horse_name, r.draw, r.jockey, r.trainer,
           a.venue, a.course, a.surface, a.distance, a.race_class, a.off_time
    FROM runners r JOIN races a USING (race_date, race_no)
    WHERE r.race_date = ? AND coalesce(r.place_code, '') NOT LIKE 'W%'
    ORDER BY r.race_no, r.horse_no
"""


def _last_runs(conn: Connection, names: list[str], before: str
               ) -> dict[str, dict[str, Any]]:
    """Each horse's last finished start before the card, and how many it has
    had. Field sizes grouped once per race, never counted per row."""
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    out: dict[str, dict[str, Any]] = {}
    for r in conn.execute(f"""
            WITH mine AS (
                SELECT horse_name, race_date, race_no, place FROM runners
                WHERE horse_name IN ({marks}) AND race_date < ? AND place IS NOT NULL),
            field AS (
                SELECT f.race_date, f.race_no, count(*) n FROM runners f
                JOIN (SELECT DISTINCT race_date, race_no FROM mine) m
                  USING (race_date, race_no)
                WHERE f.place IS NOT NULL GROUP BY f.race_date, f.race_no)
            SELECT m.horse_name, m.race_date, m.race_no, m.place, f.n field_size,
                   count(*) OVER (PARTITION BY m.horse_name) starts,
                   row_number() OVER (PARTITION BY m.horse_name
                                      ORDER BY m.race_date DESC, m.race_no DESC) k
            FROM mine m JOIN field f USING (race_date, race_no)""",
            [*names, before]):
        if r["k"] == 1:
            out[r["horse_name"]] = {k: r[k] for k in ("race_date", "race_no", "place",
                                                       "field_size", "starts")}
    return out


def _days(a: str, b: str) -> int:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def meeting(date: str, *, recent: int = RECENT,
            conn: Connection | None = None) -> dict[str, Any]:
    """{race_date, races: [{race..., runners: [{runner..., last_run, book_tier,
    blackbook, trials: [...]}]}], counts}. `races` is empty when no card is
    stored for the date."""
    own = conn is None
    conn = conn or get_conn()
    try:
        card = [dict(r) for r in conn.execute(_CARD_SQL, (date,))]
        if not card:
            return {"race_date": date, "races": [], "counts": None}
        names = sorted({r["horse_name"] for r in card})
        last = _last_runs(conn, names, date)
        trials = trials_q.for_horses(names, before=date, limit=recent, conn=conn)
        tiers = book_tier.for_meeting(date, conn=conn)
        books = {(b["race_no"], b["horse_no"]): b
                 for b in blackbook_band.declared_on(date, conn=conn)
                 if b["live_at_race"]}
    finally:
        if own:
            conn.close()

    races: dict[int, dict[str, Any]] = {}
    counts = {"runners": 0, "trialled": 0, "trials": 0, "noted": 0,
              "to_watch": 0, "standout": 0, "debut": 0}
    for r in card:
        race = races.setdefault(r["race_no"], {
            **{k: r[k] for k in ("race_no", "venue", "course", "surface",
                                 "distance", "race_class", "off_time")},
            "runners": []})
        lr = last.get(r["horse_name"])
        mine = [{k: t.get(k) for k in _TRIAL_KEYS} for t in trials.get(r["horse_name"], [])]
        for t in mine:
            t["since_last"] = lr is None or t["trial_date"] > lr["race_date"]
            # The system writes "System: ..." onto a trial it booked; that is
            # not the owner having watched it.
            t["owner_note"] = bool(t["note"]) and not t["note"]["note"].startswith("System:")
        fresh = [t for t in mine if t["since_last"]]
        book = books.get((r["race_no"], r["horse_no"]))
        race["runners"].append({
            "horse_no": r["horse_no"], "horse_name": r["horse_name"],
            "draw": r["draw"], "jockey": r["jockey"], "trainer": r["trainer"],
            "last_run": {**lr, "days_ago": _days(lr["race_date"], date)} if lr else None,
            "book_tier": tiers.get((r["race_no"], r["horse_no"])),
            "blackbook": {k: book[k] for k in ("id", "status", "added_date", "reasoning",
                                               "origin", "adopted_date", "confidence")}
                         if book else None,
            "trials": mine,
            "since_last": len(fresh),
        })
        counts["runners"] += 1
        counts["debut"] += lr is None
        counts["trialled"] += bool(fresh)
        counts["trials"] += len(fresh)
        counts["noted"] += sum(t["owner_note"] for t in fresh)
        counts["to_watch"] += sum(not t["owner_note"] for t in fresh)
        counts["standout"] += sum(t["quality_band"] == "STANDOUT" for t in fresh)
    for race in races.values():
        race["field_size"] = len(race["runners"])
        race["trialled"] = sum(bool(x["since_last"]) for x in race["runners"])
    return {"race_date": date, "recent": recent,
            "races": [races[k] for k in sorted(races)], "counts": counts}
