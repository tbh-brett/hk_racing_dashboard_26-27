"""The meeting, rather than one race on it.

Two questions whose answer is the whole card: what is booked ANYWHERE today,
and how does each race compare with the others. Both are read while looking at
a single race -- the band that carries them is sticky -- but neither is a
property of the race on screen, and `query/raceday` is the module for the race
on screen.

They were carved out of it at 568 of a 600-line cap, which is the immediate
reason, and the seam was already there: these two touch nothing `build_card`
uses. The four helpers that sat below them in that file did not come, because
`_days_between` and `_place_ratio_range` are called from `build_card` and
belong to the card. (The head-to-head pairs have since moved to `query/h2h`.)
"""
from __future__ import annotations

from typing import Any

from hkrd.query import (blackbook as bb_q, book_tier, market as market_q,
                        movement as movement_q)
from hkrd.store.connect import Connection, get_conn

__all__ = ["meeting_blackbook", "meeting_summary"]


def meeting_blackbook(date: str, *, conn: Connection | None = None
                      ) -> dict[str, Any]:
    """Every booked horse declared across the meeting, for the sticky band.

    The band is meeting-wide by design: the entries in OTHER races are what
    make it worth keeping on screen, since they are the ones you would
    otherwise miss. Each carries its race so the chip can jump there.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        entries = bb_q.declared_on(date, conn=conn)
        # A trial note counts whether or not the horse is booked (owner, 5 Oct
        # 2026; query/book_tier), so a noted horse outside the book is in the
        # band too, as a row of its own with its note as the reason.
        tiers = book_tier.for_meeting(date, conn=conn)
        entries += _noted(conn, date, tiers, {(e["race_no"], e["horse_no"])
                                              for e in entries})
        if not entries:
            return {"race_date": date, "entries": [], "count": 0}

        off = {r["race_no"]: r["off_time"] for r in conn.execute(
            "SELECT race_no, off_time FROM races WHERE race_date = ?", (date,))}
        # One movement query per race that actually has a booked runner, not
        # one per runner and not one for the whole card.
        #
        # Live prices come the same way and for the same reason the card's do:
        # `blackbook.declared_on` reads `runners.win_odds`, which is the
        # STARTING price and is NULL until the results scrape writes it. So the
        # band showed a booked horse drifting 6% with no price beside it to
        # drift FROM — a movement without a market, which is the one thing on
        # this band you cannot act on.
        moves: dict[int, dict[int, dict]] = {}
        live: dict[int, dict[int, dict]] = {}
        for race_no in sorted({e["race_no"] for e in entries}):
            moves[race_no] = {m["horse_no"]: m for m in
                              movement_q.price_movement(date, race_no, conn=conn)}
            live[race_no] = market_q.live_prices(date, race_no, conn=conn)

        out = []
        for e in entries:
            move = moves.get(e["race_no"], {}).get(e["horse_no"])
            now = live.get(e["race_no"], {}).get(e["horse_no"]) or {}
            # Fill, never overwrite: a race that has been run keeps the
            # starting price, which is what it actually paid.
            win = e["win_odds"] if e["win_odds"] is not None else now.get("win_odds")
            out.append({
                "id": e["id"], "race_no": e["race_no"],
                "horse_no": e["horse_no"], "horse_name": e["horse_name"],
                "draw": e["draw"], "win_odds": win,
                "place_odds": now.get("place_odds"),
                "off_time": off.get(e["race_no"]),
                "status": e["status"], "confidence": e["confidence"],
                "added_date": e["added_date"],
                # Who booked it: the band draws a system entry in its own
                # colour until the owner adopts it (`jobs/auto_book`).
                "origin": e["origin"], "adopted_date": e["adopted_date"],
                "closed_date": e["closed_date"],
                "closed_reason": e["closed_reason"],
                "reasoning": e["reasoning"],
                # Whether TODAY is the race this thesis was written for, and
                # what it asked for. The band's whole claim to the space it
                # takes: "runs today" is a reminder, "runs today at the trip
                # you booked it for" is a reason to look.
                "on_conditions": e["on_conditions"],
                "conditions_text": e["conditions_text"],
                "live_at_race": bool(e["live_at_race"]),
                "booked_before_race": bool(e["booked_before_race"]),
                "tags": sorted((e["tag_csv"] or "").split(","))
                        if e["tag_csv"] else [],
                # None, not 0. A runner with one captured price has no movement
                # to report, and 0% would read as a market that held steady.
                "change_pct": move["change_pct"] if move else None,
                "observed": bool(move and move["observed"]),
                "book_tier": tiers.get((e["race_no"], e["horse_no"])),
            })
        return {"race_date": date, "entries": out, "count": len(out)}
    finally:
        if own:
            conn.close()


def _noted(conn: Connection, date: str, tiers: dict, booked: set) -> list[dict[str, Any]]:
    """Runners with the owner's trial note since their last run and no book
    entry, shaped as the band's entries: live, owner's, the note the reason."""
    want = [k for k, t in tiers.items() if t["tier"] == "NOTE" and k not in booked]
    if not want:
        return []
    cards = {(r["race_no"], r["horse_no"]): dict(r) for r in conn.execute(
        "SELECT race_no, horse_no, horse_name, draw, win_odds FROM runners "
        "WHERE race_date = ?", (date,))}
    return [{**cards[k], "id": None, "status": "active", "confidence": None,
             "added_date": tiers[k]["note"]["written_at"][:10],
             "closed_date": None, "closed_reason": None, "origin": "note",
             "adopted_date": None, "reasoning": tiers[k]["note"]["note"],
             "on_conditions": False, "conditions_text": "", "live_at_race": True,
             "booked_before_race": True, "tag_csv": "trial_note"}
            for k in want if k in cards]


def meeting_summary(date: str, *, conn: Connection | None = None) -> dict[str, Any]:
    """Race-by-race header for the meeting: field size and concentration.

    Concentration carries the age of the price it was computed from, because
    read early it understates the band in about 60% of races -- and every
    surviving snapshot in the archive is hours before racing.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT race_no, distance, race_class, going, course, "
            "(SELECT count(*) FROM runners r WHERE r.race_date = a.race_date "
            " AND r.race_no = a.race_no) AS field_size "
            "FROM races a WHERE race_date = ? ORDER BY race_no", (date,)).fetchall()
        out = []
        for r in rows:
            conc = market_q.concentration(date, r["race_no"], conn=conn)
            out.append({"race_no": r["race_no"], "distance": r["distance"],
                        "race_class": r["race_class"], "going": r["going"],
                        "course": r["course"], "field_size": r["field_size"],
                        "concentration": conc["value"], "band": conc["band"],
                        "stale": conc["stale"]})
        return {"race_date": date, "races": out}
    finally:
        if own:
            conn.close()
