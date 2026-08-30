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

from hkrd.derive.probability import devig
from hkrd.query import blackbook as bb_q, formguide as fg_q, market as market_q
from hkrd.query.race import get_horse_form, get_race
from hkrd.store.connect import Connection, get_conn

__all__ = ["build_card", "meeting_blackbook", "meeting_summary",
           "spark_points"]

# Routine stewards' notes are stored but never surfaced as a flag. A passed
# veterinary examination rendering like a real finding is how a badge becomes
# noise and gets ignored.
_ROUTINE = {"sampling", "vet_routine", "no_report", "jumped_fairly"}


def spark_points(series: list[float], *, width: int = 66, height: int = 18
                 ) -> tuple[str, float, float]:
    """An odds series as an SVG polyline, plus the final point.

    The design draws the shape of the money per runner in the row. With one
    price there is no shape, so it returns a flat line rather than a
    misleading spike.
    """
    if not series:
        return "", 0.0, height / 2
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1.0
    step = width / max(len(series) - 1, 1)
    pts = []
    for i, v in enumerate(series):
        x = i * step
        # Shorter price = money arriving = drawn higher.
        y = height - 2 - ((hi - v) / span) * (height - 4)
        pts.append((round(x, 1), round(y, 1)))
    return (" ".join(f"{x},{y}" for x, y in pts), pts[-1][0], pts[-1][1])


def _odds_series(conn: Connection, date: str, race_no: int) -> dict[int, list[float]]:
    rows = conn.execute(
        "SELECT horse_no, win_odds FROM odds_snapshots "
        "WHERE race_date = ? AND race_no = ? AND win_odds IS NOT NULL "
        "ORDER BY horse_no, captured_at", (date, race_no)).fetchall()
    out: dict[int, list[float]] = {}
    for r in rows:
        out.setdefault(r["horse_no"], []).append(r["win_odds"])
    return out


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
        booked = {b["horse_name"]: b
                  for b in bb_q.for_race(date, race_no, conn=conn)}
        moves = {m["horse_no"]: m
                 for m in market_q.price_movement(date, race_no, conn=conn)}

        # Market rank by price, so model-versus-market disagreement is explicit
        # rather than something the reader has to work out.
        priced = sorted((r for r in race.runners if r.win_odds),
                        key=lambda r: r.win_odds)
        market_rank = {r.horse_no: i + 1 for i, r in enumerate(priced)}

        series = _odds_series(conn, date, race_no)

        # De-vigged win probability, shown as a percentage beside the price.
        # The market's own estimate, not a model's.
        priced_odds = [r.win_odds for r in race.runners if r.win_odds]
        win_pct: dict[int, float] = {}
        overround = None
        if priced_odds:
            probs = devig(priced_odds)
            for r, p in zip((x for x in race.runners if x.win_odds), probs):
                win_pct[r.horse_no] = round(100 * float(p), 1)
            # Over 100% is the bookmaker's margin. A NEGATIVE value means the
            # field is not fully priced -- scratchings, or a pre-market
            # capture -- which is worth seeing rather than hiding.
            overround = round(100 * (sum(1 / o for o in priced_odds) - 1), 1)

        runners: list[dict[str, Any]] = []
        for r in race.runners:
            prior = get_horse_form(r.horse_name, limit=1, before=date, conn=conn)
            last = prior[0] if prior else None
            m_rank = market_rank.get(r.horse_no)
            pts, dot_x, dot_y = spark_points(series.get(r.horse_no, []))
            # A trainer change since the horse's last run is a real signal, and
            # the comparison that matters is today against ONE run back.
            trainer_changed = bool(
                last and last.trainer and r.trainer and last.trainer != r.trainer)
            row = r.to_dict()
            book = booked.get(r.horse_name)
            row.update({
                # The band above the card lists these; the row carries the flag
                # so the marker and the band cannot disagree.
                "blackbook": {
                    "id": book["id"], "status": book["status"],
                    "confidence": book["confidence"],
                    "added_date": book["added_date"],
                    "reasoning": book["reasoning"],
                    "live_at_race": bool(book["live_at_race"]),
                    "booked_before_race": bool(book["booked_before_race"]),
                    "tags": sorted((book["tag_csv"] or "").split(","))
                            if book["tag_csv"] else [],
                } if book else None,
                "win_pct": win_pct.get(r.horse_no),
                "spark": pts, "spark_dot": [dot_x, dot_y],
                "spark_points_n": len(series.get(r.horse_no, [])),
                "trainer_changed": trainer_changed,
                "trainer_prev": last.trainer if trainer_changed else None,
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
            "overround": overround,
            "place_ratio_range": _place_ratio_range(race.runners),
            "head_to_head": _pairs_meeting_again(conn, date, race.runners),
            "blackbook": [
                {**{k: v for k, v in b.items() if k != "tag_csv"},
                 "tags": sorted((b["tag_csv"] or "").split(","))
                         if b["tag_csv"] else []}
                for b in booked.values()],
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
        if not entries:
            return {"race_date": date, "entries": [], "count": 0}

        off = {r["race_no"]: r["off_time"] for r in conn.execute(
            "SELECT race_no, off_time FROM races WHERE race_date = ?", (date,))}
        # One movement query per race that actually has a booked runner, not
        # one per runner and not one for the whole card.
        moves: dict[int, dict[int, dict]] = {}
        for race_no in sorted({e["race_no"] for e in entries}):
            moves[race_no] = {m["horse_no"]: m for m in
                              market_q.price_movement(date, race_no, conn=conn)}

        out = []
        for e in entries:
            move = moves.get(e["race_no"], {}).get(e["horse_no"])
            out.append({
                "id": e["id"], "race_no": e["race_no"],
                "horse_no": e["horse_no"], "horse_name": e["horse_name"],
                "draw": e["draw"], "win_odds": e["win_odds"],
                "off_time": off.get(e["race_no"]),
                "status": e["status"], "confidence": e["confidence"],
                "added_date": e["added_date"],
                "reasoning": e["reasoning"],
                "live_at_race": bool(e["live_at_race"]),
                "booked_before_race": bool(e["booked_before_race"]),
                "tags": sorted((e["tag_csv"] or "").split(","))
                        if e["tag_csv"] else [],
                # None, not 0. A runner with one captured price has no movement
                # to report, and 0% would read as a market that held steady.
                "change_pct": move["change_pct"] if move else None,
                "observed": bool(move and move["observed"]),
            })
        return {"race_date": date, "entries": out, "count": len(out)}
    finally:
        if own:
            conn.close()


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


def _place_ratio_range(runners) -> str | None:
    """The spread of place-to-win ratios actually on this card.

    Place odds cannot be derived from win odds: there is no fixed
    relationship, it depends on how concentrated the market is. The common
    "a third of the win odds" rule is structurally invalid, and showing the
    real range is how that stays obvious.
    """
    ratios = [r.place_odds / r.win_odds for r in runners
              if r.place_odds and r.win_odds]
    if len(ratios) < 2:
        return None
    return f"{min(ratios):.2f}–{max(ratios):.2f}"


def _pairs_meeting_again(conn: Connection, date: str, runners,
                         limit: int = 8) -> list[dict[str, Any]]:
    """Runners in today's field who have met before.

    Sorted by weight swing, because the swing is the gap BETWEEN them and a
    pair both going up 5lb has not changed relative to one another.
    """
    out: list[dict[str, Any]] = []
    for i, a in enumerate(runners):
        for b in runners[i + 1:]:
            h2h = fg_q.head_to_head(a.horse_name, b.horse_name,
                                    before=date, conn=conn)
            if not h2h["meetings"]:
                continue
            today_gap = (a.actual_weight - b.actual_weight
                         if a.actual_weight and b.actual_weight else None)
            swing = fg_q.weight_swing(h2h["last_weight_gap"], today_gap)
            last = h2h["meetings"][0]
            out.append({
                "a_no": a.horse_no, "a_name": a.horse_name,
                "b_no": b.horse_no, "b_name": b.horse_name,
                "record": f"{h2h['record']['a']}-{h2h['record']['b']}",
                "meetings": len(h2h["meetings"]),
                "last_date": last["race_date"],
                "last_cond": f"{last['distance']}m {last['going']}",
                "last_line": f"{last['pa']} v {last['pb']}",
                "gap_then": h2h["last_weight_gap"], "gap_now": today_gap,
                "swing": swing,
                # Escalating tiers at 4, 6 and 8 lb. Most pairs clear none of
                # them, which is correct rather than a bug.
                "swing_tier": (3 if swing is not None and swing >= 8
                               else 2 if swing is not None and swing >= 6
                               else 1 if swing is not None and swing >= 4 else 0),
                "a_gate": last["da"], "b_gate": last["db"],
            })
    out.sort(key=lambda p: (-(p["swing"] or 0), -p["meetings"]))
    return out[:limit]
