"""The Race Day card — everything one race needs, in one call.

Design brief 01 names the moment this page is for: twenty minutes before a
race, with money about to go down. Four questions have to be answerable fast.

  Has the market moved since I last looked, and on which horse?
  Do my models disagree with the price, and where?
  Is anything here in my blackbook?
  Is this race concentrated enough to be worth covering?

The market price is the best predictor available -- its win odds rank horses
better (AUC 0.785) than every model here, the best of which reaches 0.727. So
odds are a first-class citizen and the models sit beside them as a second
opinion, never instead of them. Where a model disagrees with the price, that
disagreement is the interesting thing on the screen, and it is computed here
rather than left for the eye to find.
"""
from __future__ import annotations

from typing import Any

from hkrd.query import market as market_q
from hkrd.query.race import get_horse_form, get_race
from hkrd.store.connect import Connection, get_conn

__all__ = ["build_card", "meeting_summary"]

# Routine stewards' notes are stored but never surfaced as a flag. A passed
# veterinary examination rendering like a real finding is how a badge becomes
# noise and gets ignored.
_ROUTINE = {"sampling", "vet_routine", "no_report", "jumped_fairly"}


def build_card(date: str, race_no: int, *,
               conn: Connection | None = None) -> dict[str, Any]:
    """One race, assembled for the card."""
    own = conn is None
    conn = conn or get_conn()
    try:
        race = get_race(date, race_no, conn=conn)
        if not race.runners:
            return {"race_date": date, "race_no": race_no, "runners": []}

        conc = market_q.concentration(date, race_no, conn=conn)
        moves = {m["horse_no"]: m
                 for m in market_q.price_movement(date, race_no, conn=conn)}

        # Market rank by price, so model-versus-market disagreement is explicit
        # rather than something the reader has to work out.
        priced = sorted((r for r in race.runners if r.win_odds),
                        key=lambda r: r.win_odds)
        market_rank = {r.horse_no: i + 1 for i, r in enumerate(priced)}

        runners: list[dict[str, Any]] = []
        for r in race.runners:
            prior = get_horse_form(r.horse_name, limit=1, before=date, conn=conn)
            last = prior[0] if prior else None
            m_rank = market_rank.get(r.horse_no)
            row = r.to_dict()
            row.update({
                "market_rank": m_rank,
                "movement": moves.get(r.horse_no),
                # Negative means the model likes it more than the market does.
                "rank_delta": (r.sarr_rank - m_rank
                               if r.sarr_rank and m_rank else None),
                "last_run": {
                    "race_date": last.race_date, "place": last.place,
                    "figure": last.et_figure, "figure_display": last.figure_display,
                    "pace_style": last.pace_style,
                    "days_ago": _days_between(last.race_date, date),
                    "tags": [t for t in last.tags if t not in _ROUTINE],
                    "lane_notes": list(last.lane_notes),
                } if last else None,
            })
            runners.append(row)

        return {
            "race_date": date, "race_no": race_no,
            "venue": race.venue, "course": race.course, "surface": race.surface,
            "going": race.going, "distance": race.distance,
            "race_class": race.race_class, "field_size": race.field_size,
            "concentration": conc,
            "runners": runners,
        }
    finally:
        if own:
            conn.close()


def _days_between(earlier: str, later: str) -> int | None:
    from datetime import date as _date
    try:
        a = _date.fromisoformat(earlier)
        b = _date.fromisoformat(later)
    except ValueError:
        return None
    return (b - a).days


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
