"""The blackbook — a hypothesis tracker, read back against what actually ran.

Design brief 06 §1: every entry is a claim that this horse will run better than
its public form suggests, for a stated reason. The page's job is to show whether
those claims pay off, so the two columns that matter are "runs since" and
"record since".

THE OPTIMISATION. The legacy export recorded a subsequent run for only 25 of its
196 entries, because logging one relied on remembering to. That is exactly the
failure the brief names -- "the user shouldn't have to remember to record an
absence" -- and it makes the book a scrapbook: you see the runs someone bothered
to write down, which are the ones that went well.

So runs since booking are not read from the export. They are derived from the
runners table, which holds every run of every horse. Against the same 196
entries that yields 355 subsequent runs rather than 27. The hand-written records
survive as `blackbook_notes` -- an observation about a run is worth keeping, it
just isn't the record OF the run.

The derivation also excludes the run each entry was written FROM, which the
naive version did not. Over the real book that correction moves the flat-stake
return from -5.1% to -16.6%: ten of the forty-two apparent wins since booking
were the source runs themselves.
"""
from __future__ import annotations

import datetime as dt

from typing import Any

from hkrd.derive.probability import actual_over_expected
from hkrd.query import period
from hkrd.query.period import Window
from hkrd.store.connect import Connection, get_conn

__all__ = ["list_entries", "entry_detail", "for_race", "declared_on",
           "tag_performance", "tag_definitions", "book_summary",
           "entry_bets"]

# HK pays three places in fields of seven or more, two in smaller fields. Using
# a flat top-3 would credit the book with places that never paid.
_PLACES_SQL = """
    CASE WHEN r.place <= (CASE WHEN f.field_size >= 7 THEN 3 ELSE 2 END)
         THEN 1 ELSE 0 END"""

# Every run of a booked horse after the day it was booked. `f` carries the field
# size because the place rule depends on it, and the race's book — the sum of
# 1/odds over the whole field — because an implied probability has to be
# de-vigged against the race it came from, not against 1.0.
_RUNS_SINCE_FROM = """
    FROM blackbook b
    JOIN runners r ON r.horse_name = b.horse_name AND r.race_date > b.added_date
                  -- The run the entry is ANCHORED to is not a test of it. In
                  -- 71 of the 193 legacy entries with a source date the source
                  -- run falls after the booking date -- the entry was written
                  -- off a trial or the card, naming the engagement it was
                  -- booked for -- so without this the run that created the
                  -- thesis is counted as evidence for it. It is shown in full
                  -- as the source run instead.
                  AND NOT (b.source_date IS NOT NULL
                           AND r.race_date = b.source_date
                           AND r.race_no = b.source_race_no)
    JOIN (SELECT race_date, race_no, count(*) field_size,
                 sum(CASE WHEN win_odds > 0 THEN 1.0 / win_odds END) book,
                 count(win_odds) priced
            FROM runners GROUP BY race_date, race_no) f
      ON f.race_date = r.race_date AND f.race_no = r.race_no"""

# The market's own estimate that THIS runner wins, with the overround divided
# out. NULL when the race is not fully priced -- a book summed over part of a
# field is not a book, and dividing by it would quietly inflate every A/E.
_IMPLIED_SQL = """
    CASE WHEN r.win_odds > 0 AND f.book > 0 AND f.priced = f.field_size
         THEN (1.0 / r.win_odds) / f.book END"""


def _entry_rows(conn: Connection, where: str = "", params: Any = ()) -> list[dict]:
    """Entries with their derived record. One query, not one per entry."""
    rows = conn.execute(f"""
        WITH since AS (
            SELECT b.id,
                   count(*) runs,
                   sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) wins,
                   sum({_PLACES_SQL}) places,
                   min(r.race_date) first_run,
                   max(r.race_date) last_run
            {_RUNS_SINCE_FROM}
            WHERE r.place IS NOT NULL
            GROUP BY b.id
        )
        SELECT b.*,
               coalesce(s.runs, 0) runs_since,
               coalesce(s.wins, 0) wins_since,
               coalesce(s.places, 0) places_since,
               s.first_run, s.last_run,
               (SELECT group_concat(t.tag) FROM blackbook_tags t
                 WHERE t.id = b.id) tag_csv,
               (SELECT count(*) FROM blackbook_notes n WHERE n.id = b.id) notes
        FROM blackbook b
        LEFT JOIN since s ON s.id = b.id
        {where}
        ORDER BY b.added_date DESC, b.id
    """, params).fetchall()

    today = dt.date.today().isoformat()
    out = []
    for row in rows:
        d = dict(row)
        d["tags"] = sorted((d.pop("tag_csv") or "").split(",")) if d["tag_csv"] else []
        # `status` is a snapshot taken when the JSON was last exported, and
        # nothing recomputes it on the way in. An entry whose expiry has passed
        # therefore keeps reading "active" until somebody exports the file
        # again — the book quietly claims a horse is live because a file is
        # stale, which is the opposite of what an expiry date is for.
        #
        # The date is the fact; the flag is a cache of it. An entry still
        # inside its window keeps whatever the file said, so a horse retired
        # early by hand stays retired.
        if d.get("expiry_date") and d["expiry_date"] < today:
            d["status"] = "expired"
        # Four runs without resolution is the brief's prompt-for-review
        # threshold. A book that only grows is unusable within a season.
        d["review_due"] = d["status"] == "active" and d["runs_since"] >= 4
        out.append(d)
    return out


def list_entries(*, status: str | None = None, tag: str | None = None,
                 conn: Connection | None = None) -> list[dict[str, Any]]:
    """The list view. Tag is the primary filter, per the brief."""
    own = conn is None
    conn = conn or get_conn()
    try:
        clauses, params = [], []
        if status:
            # The same rule the rows are read by, or the filter and the list
            # disagree: asking for "active" would hand back an entry the row
            # itself then prints as expired.
            today = dt.date.today().isoformat()
            if status == "active":
                clauses.append("b.status = 'active' AND (b.expiry_date IS NULL "
                               "OR b.expiry_date >= ?)")
                params.append(today)
            elif status == "expired":
                clauses.append("(b.status = 'expired' OR (b.expiry_date IS NOT NULL "
                               "AND b.expiry_date < ?))")
                params.append(today)
            else:
                clauses.append("b.status = ?")
                params.append(status)
        if tag:
            clauses.append("EXISTS (SELECT 1 FROM blackbook_tags t "
                           "WHERE t.id = b.id AND t.tag = ?)")
            params.append(tag)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return _entry_rows(conn, where, params)
    finally:
        if own:
            conn.close()


def entry_detail(entry_id: str, *, conn: Connection | None = None
                 ) -> dict[str, Any] | None:
    """One entry: the thesis, the source run, and every run since -- derived."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = _entry_rows(conn, "WHERE b.id = ?", (entry_id,))
        if not rows:
            return None
        entry = rows[0]

        entry["runs"] = [dict(r) for r in conn.execute(f"""
            SELECT r.race_date, r.race_no, r.horse_no, r.place, r.place_code,
                   r.draw, r.jockey, r.trainer, r.win_odds,
                   a.venue, a.distance, a.going, a.surface, a.course,
                   a.race_class,
                   f.field_size, e.figure et_figure, e.confidence et_confidence,
                   p.pace_style, {_PLACES_SQL} placed
            {_RUNS_SINCE_FROM}
            JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
            LEFT JOIN runner_et e USING (race_date, race_no, horse_no)
            LEFT JOIN runner_pace p USING (race_date, race_no, horse_no)
            WHERE b.id = ?
            ORDER BY r.race_date DESC, r.race_no DESC
        """, (entry_id,)).fetchall()]

        # The run the thesis was written from, in full and on its own. Design
        # brief 06 asks for it ("the same expanded run row from the Form Guide,
        # so the reasoning is visible without navigating away") and keeping it
        # out of the record above is what stops it counting as a test.
        entry["source_run"] = None
        if entry["source_date"] and entry["source_race_no"]:
            row = conn.execute("""
                SELECT r.race_date, r.race_no, r.horse_no, r.place, r.place_code,
                       r.draw, r.jockey, r.trainer, r.win_odds,
                       a.venue, a.distance, a.going, a.surface, a.course,
                       a.race_class, e.figure et_figure,
                       e.confidence et_confidence, p.pace_style,
                       (SELECT count(*) FROM runners x
                         WHERE x.race_date = r.race_date
                           AND x.race_no = r.race_no) field_size
                FROM runners r
                JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
                LEFT JOIN runner_et e USING (race_date, race_no, horse_no)
                LEFT JOIN runner_pace p USING (race_date, race_no, horse_no)
                WHERE r.horse_name = ? AND r.race_date = ? AND r.race_no = ?
            """, (entry["horse_name"], entry["source_date"],
                  entry["source_race_no"])).fetchone()
            entry["source_run"] = dict(row) if row else None

        # Hand-written observations, kept apart from the derived runs above so
        # it stays clear which is a record and which is a remark.
        entry["notes_written"] = [dict(r) for r in conn.execute(
            "SELECT race_date, race_no, finish, model_rank, verdict, notes "
            "FROM blackbook_notes WHERE id = ? ORDER BY race_date DESC",
            (entry_id,)).fetchall()]
        return entry
    finally:
        if own:
            conn.close()


def for_race(date: str, race_no: int, *, conn: Connection | None = None
             ) -> list[dict[str, Any]]:
    """Booked horses declared in one race — the Race Day blackbook band.

    Expired entries are returned too, flagged: a thesis that ran out of time is
    still worth knowing about when the horse turns up, and hiding it is how the
    band silently under-reports.

    `status` is the entry's state NOW. On an archived race that is not the same
    question as "was I watching this horse that day", so `live_at_race` is
    returned alongside it — a booking made in July must not render as a live
    thesis over a race run in May.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        return [dict(r) for r in conn.execute("""
            SELECT b.id, b.horse_name, b.status, b.confidence, b.reasoning,
                   b.added_date, b.expiry_date, b.source_race,
                   r.horse_no, r.draw, r.jockey, r.win_odds,
                   b.added_date <= r.race_date AS booked_before_race,
                   (b.added_date <= r.race_date
                    AND (b.expiry_date IS NULL
                         OR b.expiry_date >= r.race_date)) AS live_at_race,
                   (SELECT group_concat(t.tag) FROM blackbook_tags t
                     WHERE t.id = b.id) tag_csv
            FROM blackbook b
            JOIN runners r ON r.horse_name = b.horse_name
            WHERE r.race_date = ? AND r.race_no = ?
            ORDER BY r.horse_no
        """, (date, race_no)).fetchall()]
    finally:
        if own:
            conn.close()


def declared_on(date: str, *, conn: Connection | None = None
                ) -> list[dict[str, Any]]:
    """Every booked horse declared across one meeting.

    "Highlight prominently: entries with a horse declared to run today. That's
    the moment the page earns its keep." — design brief 06.

    Carries the same `live_at_race` flag as `for_race`, and for the same
    reason: over an archived meeting a booking made months later is not a live
    thesis, and the band must be able to show that rather than imply it was.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        return [dict(r) for r in conn.execute("""
            SELECT b.id, b.horse_name, b.status, b.confidence, b.added_date,
                   b.expiry_date, b.reasoning,
                   r.race_no, r.horse_no, r.draw, r.win_odds,
                   b.added_date <= r.race_date AS booked_before_race,
                   (b.added_date <= r.race_date
                    AND (b.expiry_date IS NULL
                         OR b.expiry_date >= r.race_date)) AS live_at_race,
                   (SELECT group_concat(t.tag) FROM blackbook_tags t
                     WHERE t.id = b.id) tag_csv
            FROM blackbook b
            JOIN runners r ON r.horse_name = b.horse_name
            WHERE r.race_date = ?
            ORDER BY r.race_no, r.horse_no
        """, (date,)).fetchall()]
    finally:
        if own:
            conn.close()


def tag_performance(*, window: Window | None = None,
                    conn: Connection | None = None) -> list[dict[str, Any]]:
    """Strike and place rate per booking reason, with the sample size beside it.

    "Weak evidence must look weak." Across 153 condition cells in the prior
    analysis, 8 cleared significance where 7.0 were expected by chance. A tag
    working on 6 entries probably isn't, so `thin` is returned as a fact about
    the row rather than left to the reader to notice.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        # The window is on the RUN, not on when the entry was booked: "how did
        # this tag do last month" is a question about the runs that happened
        # last month, whatever month the horse was booked in.
        _win, _wp = period.clause(window, "r.race_date")
        rows = conn.execute(f"""
            SELECT t.tag,
                   count(DISTINCT b.id) entries,
                   count(*) runs,
                   sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) wins,
                   sum({_PLACES_SQL}) places,
                   sum(CASE WHEN r.place = 1 AND r.win_odds IS NOT NULL
                            THEN r.win_odds ELSE 0 END) win_return,
                   sum(CASE WHEN r.win_odds IS NOT NULL THEN 1 ELSE 0 END) priced,
                   sum({_IMPLIED_SQL}) expected_wins,
                   sum(CASE WHEN {_IMPLIED_SQL} IS NOT NULL THEN 1 ELSE 0 END) ae_runs
            {_RUNS_SINCE_FROM}
            JOIN blackbook_tags t ON t.id = b.id
            WHERE r.place IS NOT NULL AND {_win}
            GROUP BY t.tag
            ORDER BY count(*) DESC
        """, _wp).fetchall()

        # Entries carrying a tag but no subsequent run yet — they belong in the
        # count, otherwise a tag looks better tested than it is.
        booked = {r["tag"]: r["n"] for r in conn.execute(
            "SELECT tag, count(*) n FROM blackbook_tags GROUP BY tag")}
        defs = tag_definitions(conn=conn)

        out = []
        for row in rows:
            d = dict(row)
            runs = d["runs"] or 0
            d["entries_booked"] = booked.get(d["tag"], d["entries"])
            d["strike_rate"] = round(d["wins"] / runs, 3) if runs else None
            d["place_rate"] = round(d["places"] / runs, 3) if runs else None
            # Flat-stake return on a level win bet at the recorded price. Only
            # over runs that carried a price, or the denominator lies.
            priced = d.pop("priced") or 0
            d["roi_win"] = (round((d.pop("win_return") - priced) / priced, 3)
                            if priced else None)
            d["priced_runs"] = priced
            d["thin"] = runs < 20
            d["definition"] = defs.get(d["tag"])
            d.update(actual_over_expected(d.pop("expected_wins"),
                                           d["wins"], d.pop("ae_runs")))
            out.append(d)
        for tag, n in booked.items():
            if not any(o["tag"] == tag for o in out):
                out.append({"tag": tag, "entries": 0, "entries_booked": n,
                            "runs": 0, "wins": 0, "places": 0,
                            "strike_rate": None, "place_rate": None,
                            "roi_win": None, "priced_runs": 0, "thin": True,
                            "definition": defs.get(tag),
                            **actual_over_expected(None, 0, 0)})
        return out
    finally:
        if own:
            conn.close()


def tag_definitions(*, conn: Connection | None = None) -> dict[str, str]:
    own = conn is None
    conn = conn or get_conn()
    try:
        return {r["tag"]: r["definition"] for r in conn.execute(
            "SELECT tag, definition FROM blackbook_tag_definitions")}
    finally:
        if own:
            conn.close()


def book_summary(*, today: str | None = None,
                 conn: Connection | None = None) -> dict[str, Any]:
    """The header strip: how big the book is, and whether it resolves.

    "Retiring an entry must be as easy as creating one. A blackbook that only
    ever grows becomes unusable within a season." — design brief 06. So the
    health metric here is RESOLUTION, not size: how many entries have been
    settled one way or the other rather than left running.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, count(*) n FROM blackbook GROUP BY status")}
        total = sum(status.values())

        declared = 0
        if today:
            declared = conn.execute(
                "SELECT count(DISTINCT b.id) FROM blackbook b "
                "JOIN runners r ON r.horse_name = b.horse_name "
                "WHERE r.race_date = ?", (today,)).fetchone()[0]

        # Every run since booking, priced, as if each had been backed to a flat
        # stake. Not a claim about what was bet -- see `bets_ledger` below.
        row = conn.execute(f"""
            SELECT count(*) runs,
                   sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) wins,
                   sum(CASE WHEN r.place = 1 THEN r.win_odds ELSE 0 END) returned,
                   sum(CASE WHEN r.win_odds IS NOT NULL THEN 1 ELSE 0 END) priced,
                   sum({_IMPLIED_SQL}) expected_wins,
                   sum(CASE WHEN {_IMPLIED_SQL} IS NOT NULL THEN 1 ELSE 0 END) ae_runs
            {_RUNS_SINCE_FROM}
            WHERE r.place IS NOT NULL AND r.win_odds IS NOT NULL
        """).fetchone()

        priced = row["priced"] or 0
        review = conn.execute(f"""
            SELECT count(*) FROM (
              SELECT b.id FROM blackbook b
              JOIN runners r ON r.horse_name = b.horse_name
                            AND r.race_date > b.added_date
              WHERE b.status = 'active' AND r.place IS NOT NULL
              GROUP BY b.id HAVING count(*) >= 4)""").fetchone()[0]

        return {
            "total": total, "status": status,
            "active": status.get("active", 0),
            "resolved": total - status.get("active", 0),
            "declared_today": declared, "today": today,
            "review_due": review,
            "runs_since": row["runs"] or 0,
            "wins_since": row["wins"] or 0,
            "flat_roi": (round(((row["returned"] or 0) - priced) / priced, 3)
                         if priced else None),
            **actual_over_expected(row["expected_wins"], row["wins"] or 0,
                                    row["ae_runs"] or 0),
            # The ledger exists now, so backed-versus-missed is a join
            # rather than something the user had to remember to log --
            # brief 06: "the user shouldn't have to remember to record an
            # absence."
            "bets_ledger": conn.execute(
                "SELECT count(*) FROM bets").fetchone()[0] > 0,
        }
    finally:
        if own:
            conn.close()


def entry_bets(entry_id: str, *, conn: Connection | None = None) -> dict[str, Any]:
    """One entry's money, run by run, with the balance carried forward.

    The panel this feeds shows what was ACTUALLY staked and returned, not a
    notional flat bet on every run — a horse's single run can attract eight
    tickets at different stakes, so a fixed-stake figure describes a bet that
    was never placed.

    A run with no tickets stays in the timeline, marked. That is the honest way
    to show a missed chance: the run happened, here is what it did, and there
    is no money against it. Nothing is invented about what a bet would have
    returned, because no bet was made.

    A multi-leg ticket is counted in full against the leg this horse was on.
    An all-up cannot be divided between its legs, and a horse runs once a
    meeting so it is never counted twice within one entry -- but the money was
    riding on other horses too, so `multi_leg_bets` says how many and the page
    prints it.

    Unlike `entry_record`, this timeline KEEPS the run the entry was written
    from, flagged `is_source`. That run is not a test of the thesis, so it is
    excluded there -- but the money on it was real, and dropping it stranded
    133 tickets across the book, 8 of them on one entry whose panel then read
    "no bets" against a horse that had been backed eight times.
    """
    from hkrd.query import bets as bets_q

    own = conn is None
    conn = conn or get_conn()
    try:
        entry = conn.execute(
            "SELECT horse_name, added_date, source_date, source_race_no "
            "FROM blackbook WHERE id = ?", (entry_id,)).fetchone()
        if not entry:
            return {}

        runs = [dict(r) for r in conn.execute("""
            SELECT r.race_date, r.race_no, r.place, r.place_code, r.win_odds,
                   a.venue, a.distance, a.going,
                   (? IS NOT NULL AND r.race_date = ? AND r.race_no = ?) is_source,
                   (SELECT count(*) FROM runners f
                     WHERE f.race_date = r.race_date
                       AND f.race_no = r.race_no) field_size
            FROM runners r
            JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
            WHERE r.horse_name = ? AND r.race_date > ?
            ORDER BY r.race_date, r.race_no
        """, (entry["source_date"], entry["source_date"],
              entry["source_race_no"], entry["horse_name"],
              entry["added_date"]))]

        placed = bets_q.bets_for_horse(entry["horse_name"],
                                       since=entry["added_date"], conn=conn)
        by_run: dict[tuple[str, int], list[dict]] = {}
        for b in placed:
            by_run.setdefault((b["race_date"], b["race_no"]), []).append(b)

        balance = 0.0
        staked = returned = 0.0
        backed_runs = won_runs = multi_leg = 0
        for run in runs:
            tickets = by_run.get((run["race_date"], run["race_no"]), [])
            run["bets"] = tickets
            run["staked"] = round(sum(t["stake"] for t in tickets), 2)
            run["returned"] = round(sum(t["returned"] or 0 for t in tickets), 2)
            run["pnl"] = round(run["returned"] - run["staked"], 2)
            run["backed"] = bool(tickets)
            run["is_source"] = bool(run["is_source"])
            balance += run["pnl"]
            run["balance"] = round(balance, 2)
            staked += run["staked"]
            returned += run["returned"]
            if tickets:
                backed_runs += 1
                if run["returned"] > 0:
                    won_runs += 1
            multi_leg += sum(1 for t in tickets if (t.get("legs") or 1) > 1)

        # Bets on this horse from BEFORE it was booked are not part of the
        # entry's record, and bets on a run the archive has no row for would
        # otherwise vanish — count them rather than dropping them silently.
        matched = {(b["race_date"], b["race_no"]) for r in runs
                   for b in r["bets"]}
        orphaned = [b for b in placed
                    if (b["race_date"], b["race_no"]) not in matched]

        return {
            "runs_since": runs,
            "totals": {
                "runs": len(runs),
                "backed_runs": backed_runs,
                "missed_runs": len(runs) - backed_runs,
                "bets": sum(len(r["bets"]) for r in runs),
                "winning_runs": won_runs,
                "staked": round(staked, 2),
                "returned": round(returned, 2),
                "pnl": round(returned - staked, 2),
                "roi": round((returned - staked) / staked, 3) if staked else None,
                "multi_leg_bets": multi_leg,
            },
            "unmatched_bets": len(orphaned),
        }
    finally:
        if own:
            conn.close()
