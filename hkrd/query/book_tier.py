"""Which of the book's horses in a race are worth a bet: the book's tiers.

Asked for on 2026-10-05. The book had grown to 20-34 horses declared at every
meeting and 42 of the season's 54 races held two or more, so "this horse is in
my book" no longer told the owner which one to back. Measured that day
(docs/book-tiers.md), every book run since April against the closing tote:

    tier        what it is                                   A/E    runs
    NOTE        the owner wrote a trial note since its       1.81     25
                last run, before the race
    STANDOUT    a STANDOUT trial since its last run           1.34     23
    EXCUSE      booked for trouble: traffic, the draw, a      1.13    242
                wide trip, a slow start, a riding error
    QUIET       booked for anything else (improvement, a      0.86    197
                closing sectional, outran its price ...)
    STALE       three starts without a win since it was       0.82     75
                booked, renewed or last won

and HKJC's own words, read phrase by phrase, beat the price no more often than
chance. What the owner sees in a trial video is the one thing here the market
has not already priced.

A TRIAL NOTE COUNTS WHETHER OR NOT THE HORSE IS BOOKED (the owner's decision,
5 Oct). CAVAMAZING had a note on its 19 Sep trial, was never booked, and ran
on 1 Oct with nothing on any page to say so. So NOTE is read off `trial_notes`
directly; the book is not consulted for it. A note the system wrote ("System:
...") is not the owner's eye and never counts.

ONE TIER PER RUNNER, in the order above: a stale entry with a fresh trial note
is NOTE. A horse outside the book with the owner's note on its last start is
RUN_NOTE -- shown, quiet, unmeasured as a class (18 runs).

AS AT THE RACE. Everything is read as it stood at the off: the last run before
the race, a note written before the off, an entry live that day, and its starts
counted from the day it was booked (or renewed) to the day before. The record
is the same function over every settled race since `RECORD_FROM`, so the
figure beside a chip is the one its rule has earned, and it moves with results.
"""
from __future__ import annotations

import bisect
import datetime as dt
import time
from collections import defaultdict
from typing import Any

from hkrd.query import trials as trials_q
from hkrd.store.connect import Connection, db_file, get_conn

__all__ = ["TIERS", "LABEL", "ACTION", "EXCUSE_TAGS", "TESTED_STARTS",
           "RECORD_FROM", "classify", "for_meeting", "race_line", "record",
           "stale_entries"]

TIERS = ("NOTE", "STANDOUT", "EXCUSE", "QUIET", "STALE", "RUN_NOTE")
LABEL = {"NOTE": "Trial note", "STANDOUT": "Standout trial", "EXCUSE": "Excuse",
         "QUIET": "Quiet", "STALE": "Stale", "RUN_NOTE": "Run note"}
# What the tier asks of the owner. Only NOTE is a bet: it is the one tier whose
# record is clear of the price, and a win bet is where that edge was (the 5 Oct
# betting review: place only -9.5%, exotics keyed on the same horses lost).
ACTION = {"NOTE": "back", "STANDOUT": "consider", "EXCUSE": "watch",
          "QUIET": "quiet", "STALE": "quiet", "RUN_NOTE": "quiet"}
EXCUSE_TAGS = frozenset({"traffic", "bad_draw", "bad_run", "slow_start",
                         "jockey_error"})
# An entry with this many starts without a win -- counted from the day it was
# booked, renewed, or last won since booking -- has been asked its question
# and not answered it; the same number the system's own entries are tested
# over (`query/auto_book.TESTED_RUNS`). A WIN restarts the count (owner, 6 Oct:
# LUCK IS BACK won at 32.0 on 13 Sep and was still marked stale for its six
# starts since May). Measured on results to 4 Oct, stale by this rule ran at
# A/E 0.90 against 0.99 for the rest of the book; without the reset, 1.03 --
# it is housekeeping, not a signal. Stale is muted, never closed: closing is
# the owner's button, renewing restarts the count too.
TESTED_STARTS = 3
# The owner's book as it stands began in April 2026; nothing before is a run
# of this book.
RECORD_FROM = "2026-04-01"

_SYSTEM_NOTE = "System:"
_HKT = dt.timedelta(hours=8)


def _off_utc(race_date: str, off_time: str | None) -> dt.datetime:
    """The off in UTC, which is what `written_at` is in. A card with no off
    time is taken at noon, before any meeting's first race."""
    hh, mm = (off_time or "12:00").split(":")[:2]
    return (dt.datetime.fromisoformat(race_date)
            + dt.timedelta(hours=int(hh), minutes=int(mm)) - _HKT)


def _written(ts: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t.replace(tzinfo=None) - (t.utcoffset() or dt.timedelta(0))


def classify(*, entry: dict | None, starts: int, notes: list[dict],
             standout: dict | None, run_note: dict | None) -> dict[str, Any] | None:
    """One runner's tier from what stood at the off, or None when it has none.

    `entry` is the live book entry (with `tags`), `starts` its starts since it
    was booked or renewed, `notes` the owner's trial notes since the last run
    (newest first), `standout` a STANDOUT trial since the last run, `run_note`
    the owner's note on the last start.
    """
    if notes:
        tier = "NOTE"
    elif entry and standout:
        tier = "STANDOUT"
    elif entry and starts >= TESTED_STARTS:
        tier = "STALE"
    elif entry and EXCUSE_TAGS & set(entry["tags"]):
        tier = "EXCUSE"
    elif entry:
        tier = "QUIET"
    elif run_note:
        tier = "RUN_NOTE"
    else:
        return None
    return {"tier": tier, "label": LABEL[tier], "action": ACTION[tier],
            "note": notes[0] if notes else None,
            "notes": len(notes),
            "standout": standout, "run_note": run_note if tier == "RUN_NOTE" else None,
            "entry_id": entry["id"] if entry else None,
            "in_book": entry is not None, "starts": starts if entry else None,
            "excuse": sorted(EXCUSE_TAGS & set(entry["tags"])) if entry else []}


# --- reading the database ---------------------------------------------------

def _marks(n: int) -> str:
    return ",".join("?" * n)


def _runs(conn: Connection, names: list[str], before: str) -> dict[str, list[tuple]]:
    """Every finished run per horse before `before`, oldest first, as
    (race_date, race_no, place)."""
    out: dict[str, list[tuple]] = defaultdict(list)
    for r in conn.execute(
            f"SELECT horse_name, race_date, race_no, place FROM runners "
            f"WHERE horse_name IN ({_marks(len(names))}) AND race_date < ? "
            f"AND place IS NOT NULL ORDER BY race_date, race_no", [*names, before]):
        out[r[0]].append((r[1], r[2], r[3]))
    return out


def _starts(entry: dict, past: list[tuple], before: str) -> int:
    """Starts without a win before `before`, counted from the day the entry
    was booked (the run it was written from left out), or renewed -- a renewal
    only once it has happened -- and restarted by every win since. One count
    for the race-day tier and the Blackbook page's STALE mark."""
    renewed = entry["renewed_date"] if (entry["renewed_date"]
                                        and entry["renewed_date"] < before) else None
    n = 0
    for d, no, place in past:
        if d >= before:
            break
        if (d <= renewed if renewed else d < entry["added_date"]):
            continue
        if d == entry["source_date"] and no == entry["source_race_no"]:
            continue
        n = 0 if place == 1 else n + 1
    return n


def _entries(conn: Connection, names: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(f"""
            SELECT b.id, b.horse_name, b.added_date, b.renewed_date, b.status,
                   b.closed_date, b.source_date, b.source_race_no, b.origin,
                   b.adopted_date,
                   (SELECT group_concat(t.tag) FROM blackbook_tags t
                     WHERE t.id = b.id) tag_csv
            FROM blackbook b WHERE b.horse_name IN ({_marks(len(names))})""", names):
        e = dict(r)
        e["tags"] = (e.pop("tag_csv") or "").split(",") if r["tag_csv"] else []
        out[e["horse_name"]].append(e)
    return out


def _notes(conn: Connection, names: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(
            f"SELECT horse_name, trial_date, trial_no, note, written_at, asked, "
            f"response FROM trial_notes WHERE horse_name IN ({_marks(len(names))}) "
            f"AND note NOT LIKE '{_SYSTEM_NOTE}%' ORDER BY trial_date DESC, trial_no DESC",
            names):
        out[r["horse_name"]].append(dict(r))
    return out


def _run_notes(conn: Connection, names: list[str]) -> dict[tuple[str, str, int], dict]:
    return {(r["horse_name"], r["race_date"], r["race_no"]): dict(r)
            for r in conn.execute(
                f"SELECT horse_name, race_date, race_no, note, written_at FROM run_notes "
                f"WHERE horse_name IN ({_marks(len(names))}) "
                f"AND note NOT LIKE '{_SYSTEM_NOTE}%'", names)}


def _standouts(conn: Connection, names: list[str], since: str) -> dict[str, list[dict]]:
    """STANDOUT trials per horse, rated by the one engine every page uses."""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(
            f"{trials_q._BATCH_SQL} WHERE t.horse_name IN ({_marks(len(names))}) "
            f"AND t.trial_date >= ?", [*names, since]):
        q = trials_q._runner(r, r["field_size"], r["best_time"])
        if q["quality_band"] == "STANDOUT":
            out[q["horse_name"]].append({"trial_date": q["trial_date"],
                                         "trial_no": q["trial_no"], "venue": q["venue"],
                                         "comment": q["comment"]})
    return out


def _live(e: dict, date: str) -> bool:
    """`blackbook_band.LIVE_AT_RACE_SQL`, in Python: booked by the race day and
    not closed before it."""
    return e["added_date"] <= date and (
        e["status"] == "active" or (e["closed_date"] or "9999") > date)


def _tiers(conn: Connection, runs: list[dict]) -> dict[tuple, dict]:
    """Tier for each of `runs` (race_date, race_no, horse_no, horse_name,
    off_time), keyed (race_date, race_no, horse_no). Five queries however many
    meetings are asked about."""
    if not runs:
        return {}
    names = sorted({r["horse_name"] for r in runs})
    latest = max(r["race_date"] for r in runs)
    earliest = min(r["race_date"] for r in runs)
    history = _runs(conn, names, latest)
    entries = _entries(conn, names)
    notes = _notes(conn, names)
    run_notes = _run_notes(conn, names)
    book_names = [n for n in names if entries.get(n)]
    # A trial since the last run is at most a few months before the race.
    since = (dt.date.fromisoformat(earliest) - dt.timedelta(days=400)).isoformat()
    standouts = _standouts(conn, book_names, since) if book_names else {}
    out: dict[tuple, dict] = {}
    for r in runs:
        name, date = r["horse_name"], r["race_date"]
        past = history.get(name, [])
        i = bisect.bisect_left(past, (date, 0))
        last = past[i - 1][0] if i else None
        off = _off_utc(date, r.get("off_time"))
        mine = [n for n in notes.get(name, [])
                if (last is None or n["trial_date"] > last) and n["trial_date"] < date
                and _written(n["written_at"]) < off]
        # The owner's own (or adopted) entry before the system's, the newest
        # first: two sorts, the second stable.
        live = sorted((e for e in entries.get(name, []) if _live(e, date)),
                      key=lambda e: e["added_date"], reverse=True)
        live.sort(key=lambda e: e["origin"] != "owner" and not e["adopted_date"])
        entry = live[0] if live else None
        starts = _starts(entry, past, date) if entry else 0
        standout = next((s for s in standouts.get(name, [])
                         if (last is None or s["trial_date"] > last)
                         and s["trial_date"] < date), None) if entry else None
        run_note = run_notes.get((name, last, past[i - 1][1])) if last else None
        t = classify(entry=entry, starts=starts, notes=mine, standout=standout,
                     run_note=run_note)
        if t:
            out[(date, r["race_no"], r["horse_no"])] = t
    return out


_CARD_SQL = """
    SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, a.off_time
    FROM runners r JOIN races a USING (race_date, race_no)
    WHERE r.race_date = ? AND coalesce(r.place_code, '') NOT LIKE 'W%'
"""


def for_meeting(date: str, *, conn: Connection | None = None,
                with_record: bool = True) -> dict[tuple[int, int], dict[str, Any]]:
    """{(race_no, horse_no): tier} for every runner on a card that has one,
    each carrying its tier's record when `with_record`."""
    own = conn is None
    conn = conn or get_conn()
    try:
        runs = [dict(r) for r in conn.execute(_CARD_SQL, (date,))]
        tiers = _tiers(conn, runs)
        rec = record(conn=conn) if with_record and tiers else {}
    finally:
        if own:
            conn.close()
    return {(k[1], k[2]): {**t, "record": rec.get(t["tier"])} for k, t in tiers.items()}


def race_line(runners: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One race's book in a sentence: what to back, and how much is quiet.

    `runners` each carry `horse_no`, `horse_name` and `book_tier` (or None).
    None when nothing in the race has a tier.
    """
    tiered = [(r, r["book_tier"]) for r in runners if r.get("book_tier")]
    if not tiered:
        return None
    by = defaultdict(list)
    for r, t in tiered:
        by[t["tier"]].append(r)
    back = by["NOTE"]
    consider = by["STANDOUT"]
    watch = by["EXCUSE"]
    quiet = len(by["QUIET"]) + len(by["STALE"]) + len(by["RUN_NOTE"])
    booked = sum(1 for _, t in tiered if t["in_book"])
    nos = lambda rs: ", ".join(f"#{r['horse_no']}" for r in sorted(rs, key=lambda r: r["horse_no"]))
    parts = []
    if back:
        parts.append(f"back {nos(back)} to win, flat"
                     + (" (your trial note since its last run)" if len(back) == 1
                        else " (your trial notes since their last run)"))
    if consider:
        parts.append(f"consider {nos(consider)} (standout trial)")
    if watch:
        parts.append(f"watch {nos(watch)}")
    if quiet:
        parts.append(f"{quiet} quiet")
    if not back and not consider:
        parts.insert(0, "no trial note or standout trial: no book bet in this race")
    return {"booked": booked, "back": [r["horse_no"] for r in back],
            "consider": [r["horse_no"] for r in consider],
            "watch": [r["horse_no"] for r in watch], "quiet": quiet,
            "tone": "back" if back else "consider" if consider else "quiet",
            "text": (f"Book {booked} here" if booked else "Not in your book")
                    + ": " + "; ".join(parts) + "."}


# --- the record ---------------------------------------------------------------

_RECORD_RUNS_SQL = f"""
    WITH book AS (
        SELECT race_date, race_no,
               sum(CASE WHEN win_odds > 1 THEN 1.0 / win_odds END) overround
        FROM runners WHERE race_date >= ? AND place IS NOT NULL
        GROUP BY race_date, race_no)
    SELECT r.race_date, r.race_no, r.horse_no, r.horse_name, a.off_time,
           r.place, r.win_odds, k.overround
    FROM runners r
    JOIN races a USING (race_date, race_no)
    JOIN book k USING (race_date, race_no)
    WHERE r.race_date >= ? AND r.place IS NOT NULL
      AND r.horse_name IN (
          SELECT horse_name FROM blackbook
          UNION SELECT horse_name FROM trial_notes WHERE note NOT LIKE '{_SYSTEM_NOTE}%'
          UNION SELECT horse_name FROM run_notes WHERE note NOT LIKE '{_SYSTEM_NOTE}%')
"""

_RECORD_MARK_SQL = """
SELECT (SELECT max(rowid) FROM runners),
       (SELECT count(*) || ',' || coalesce(max(written_at), '') FROM trial_notes),
       (SELECT count(*) || ',' || coalesce(max(written_at), '') FROM run_notes),
       (SELECT count(*) || ',' || coalesce(max(added_date), '') || ','
               || coalesce(max(closed_date), '') || ',' || coalesce(max(renewed_date), '')
               || ',' || sum(status = 'active') FROM blackbook),
       (SELECT count(*) FROM blackbook_tags),
       (SELECT max(rowid) FROM trials)
"""
_KEPT: dict[str, tuple[tuple, float, dict]] = {}
_KEEP_FOR = 3600.0


def record(*, conn: Connection | None = None) -> dict[str, dict[str, Any]]:
    """{tier: {runs, won, expected, ae, top3}} over every settled run since
    RECORD_FROM, tiered as at its own off. Kept until a result, a note or the
    book changes (an hour at most)."""
    own = conn is None
    conn = conn or get_conn()
    try:
        where = db_file(conn)
        mark = tuple(conn.execute(_RECORD_MARK_SQL).fetchone())
        kept = _KEPT.get(where)
        if where and kept and kept[0] == mark and time.monotonic() - kept[1] < _KEEP_FOR:
            return kept[2]
        rows = [dict(r) for r in conn.execute(_RECORD_RUNS_SQL, (RECORD_FROM, RECORD_FROM))]
        tiers = _tiers(conn, rows)
    finally:
        if own:
            conn.close()
    acc: dict[str, dict[str, Any]] = {t: {"runs": 0, "won": 0, "expected": 0.0, "top3": 0}
                                      for t in TIERS}
    for r in rows:
        t = tiers.get((r["race_date"], r["race_no"], r["horse_no"]))
        if not t or not r["win_odds"] or r["win_odds"] <= 1 or not r["overround"]:
            continue
        a = acc[t["tier"]]
        a["runs"] += 1
        a["won"] += r["place"] == 1
        a["top3"] += r["place"] <= 3
        a["expected"] += 1.0 / r["win_odds"] / r["overround"]
    out = {t: {**a, "expected": round(a["expected"], 1),
               "ae": round(a["won"] / a["expected"], 2) if a["expected"] else None,
               "since": RECORD_FROM}
           for t, a in acc.items()}
    if where:
        _KEPT[where] = (mark, time.monotonic(), out)
    return out


def stale_entries(*, today: str | None = None, conn: Connection | None = None
                  ) -> dict[str, int]:
    """{entry id: starts without a win since booked, renewed or last won}
    for every live entry that has reached TESTED_STARTS -- the Blackbook
    page's Stale mark."""
    today = today or dt.date.today().isoformat()
    own = conn is None
    conn = conn or get_conn()
    try:
        live = [dict(r) for r in conn.execute(
            "SELECT id, horse_name, added_date, renewed_date, source_date, "
            "source_race_no FROM blackbook WHERE status = 'active'")]
        if not live:
            return {}
        history = _runs(conn, sorted({e["horse_name"] for e in live}),
                        (dt.date.fromisoformat(today) + dt.timedelta(days=1)).isoformat())
    finally:
        if own:
            conn.close()
    end = (dt.date.fromisoformat(today) + dt.timedelta(days=1)).isoformat()
    out = {}
    for e in live:
        n = _starts(e, history.get(e["horse_name"], []), end)
        if n >= TESTED_STARTS:
            out[e["id"]] = n
    return out
