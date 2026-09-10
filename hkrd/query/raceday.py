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

from dataclasses import replace
from typing import Any

from hkrd.derive.probability import devig
from hkrd.query import (blackbook as bb_q, formguide as fg_q,
                        gear as gear_q, market as market_q,
                        money as money_q, movement as movement_q,
                        pools as pools_q, vet as vet_q)
from hkrd.query.race import (get_horse_form, get_race, habitual_styles,
                             vet_form)
from hkrd.query.types import RaceLine

# How many pairs the card carries, ranked by the money on them. All 91 of a
# 14-runner field would be ~10 KB on a 36 KB card for a tail nobody reads; the
# panel says it is showing the top of a ranking rather than every pair, so a
# runner missing from it reads as "not where the money is" rather than as
# missing data.
PAIR_MONEY_SHOWN = 24
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


def _with_live_prices(conn: Connection, date: str, race_no: int,
                      race: RaceLine) -> RaceLine:
    """The card, repriced from the latest odds capture.

    `runners.win_odds` is the STARTING price. It is written by the results
    scrape and by nothing else, so on a card that has not been run it is NULL
    for every runner — which is how this page came to show an empty price
    column, no market rank, no de-vigged percentage and no overround at
    exactly the moment it exists for: twenty minutes before the off. The
    concentration figure was right all along, because it reads
    `odds_snapshots`; nothing else on the card did.

    So the live capture fills the gaps here, once, and everything downstream —
    the price, the rank, the de-vig, the overround, the place ratio — reads
    the same number rather than each reaching for its own.

    It FILLS rather than overwrites, and win and place fill independently.
    Where a starting price exists the race is over and that price is the final
    one, later than any snapshot could be. Place has no starting price at all
    — `runners` has no such column, only the settled `place_dividend` — so a
    place price on this page always comes from a capture, which is why the
    measured place/win ratio read as absent for every race until now.

    Only this page is repriced at all: `get_race` is left alone, because the
    Form Guide, Results and every backtest are asking what a run actually
    paid, which is a different question from what it is trading at now.
    """
    live = market_q.live_prices(date, race_no, conn=conn)
    if not live:
        return race

    def priced(r):
        got = live.get(r.horse_no)
        if got is None:
            return r
        return replace(
            r,
            win_odds=r.win_odds if r.win_odds is not None else got["win_odds"],
            place_odds=(r.place_odds if r.place_odds is not None
                        else got["place_odds"]))

    return replace(race, runners=tuple(priced(r) for r in race.runners))


def _odds_series(conn: Connection, date: str, race_no: int) -> dict[int, list[float]]:
    """The shape of the money, over the race day.

    THE SAME BASELINE AS THE FIGURE BESIDE IT. This line and the percentage in
    the same cell were reading different windows: the number was fixed to start
    at midnight and the line was still drawn from the first capture ever taken,
    so every runner's line opened with a near-vertical cliff and then flattened
    — the shape of a pool being opened, drawn on top of the shape of a race
    being bet on, at a scale that made the second invisible.
    """
    since = (market_q.opening_capture(conn, date, race_no)
             or market_q.day_start(date))
    rows = conn.execute(
        "SELECT horse_no, win_odds FROM odds_snapshots "
        "WHERE race_date = ? AND race_no = ? AND captured_at >= ? "
        # And never the placeholder. `win_odds IS NOT NULL` let 999.0 through,
        # so a line could open at the top of its own scale for a price HKJC
        # was using to say it had none.
        "  AND win_odds IS NOT NULL AND win_odds < ? "
        "ORDER BY horse_no, captured_at",
        (date, race_no, since, market_q.NO_PRICE)).fetchall()
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
        # Before anything reads a price off it. Every figure below — rank,
        # de-vig, overround, place ratio — has to come from one set of odds,
        # or the page disagrees with itself about what the market is doing.
        race = _with_live_prices(conn, date, race_no, race)

        conc = market_q.concentration(date, race_no, conn=conn)
        booked = {b["horse_name"]: b
                  for b in bb_q.for_race(date, race_no, conn=conn)}
        # `split_move`, not `price_movement`: same three fields the card
        # already reads, plus the last ten minutes on their own. A horse that
        # sat all day and was let go 22% in the run-in reads as FLAT on a
        # single first-to-last figure — 2026-09-06 R9 #4 is exactly that, and
        # it is the window the whole cadence ladder exists to sample.
        moves = {m["horse_no"]: m
                 for m in movement_q.split_move(date, race_no, conn=conn)}
        # Scraped since the first build and never read back until now.
        vet = vet_q.for_race(date, race_no, conn=conn)

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

        # Veterinary findings over each runner's last six starts — the same
        # call the Form Guide makes, so the two pages cannot disagree about
        # whether a horse has been found wrong. One query for the card.
        vet_recent = vet_form(
            [r.horse_name for r in race.runners], before=date, runs=6, conn=conn)

        # What HKJC already said about the gear, and what the record adds. The
        # suffix — B1 first time, B- off — has been in every card since the
        # first scrape and no page read it; re-instatement and the barrier-trial
        # schooling step need the archive and nothing was asking it.
        gear = gear_q.for_horses(
            [r.horse_name for r in race.runners],
            {r.horse_name: r.gear for r in race.runners},
            before=date, conn=conn)

        # HOW THE HORSE RUNS, not where it happened to sit last start. The
        # STYLE column read `last_run.pace_style` — one observation, and the
        # single worst estimator of the next one: a Leader ridden quietly once
        # showed as a Midfield on the card everyone was about to bet into,
        # while the Speed Map beside it drew the same horse on the lead,
        # because `runner_projection.style` has always been the habitual one.
        # One definition now, `derive.pace.habitual_style`, read here and by
        # SARR's profile — so the card, the model and the map cannot give three
        # answers about the same horse in the same race. One query for the card.
        styles = habitual_styles([r.horse_name for r in race.runners],
                                 before=date, conn=conn)

        # HOW MUCH MONEY, not just how it is divided. The column on this card
        # has been labelled MOVE · MONEY since the first build and the money
        # half of it was a sparkline of the PRICE — a ratio, which says nothing
        # about scale. A tenth of a $9,000 pool and a tenth of a $430,000 pool
        # are the same number describing amounts fifty times apart.
        #
        # The pair pools are here because they are where this book actually
        # bets and because they are the bigger market: 2026-09-09 HV race 1 held
        # $251,403 in quinella place and $202,395 in quinella against $174,539
        # in win. A win-only reading of "where the money is" misses more than
        # half of it.
        cash = money_q.runner_money(date, race_no, conn=conn)
        by_no = {r["horse_no"]: r for r in cash["runners"]}
        pairs = money_q.pair_money(date, race_no, top=PAIR_MONEY_SHOWN,
                                   conn=conn)
        # WHO THE NEW MONEY CAME FOR. A price move says the ratio changed and
        # cannot say why: a runner shortens when money arrives on it and also
        # when money arrives on everything else. Measured on 2026-09-09 HV race
        # 1 between 15:01 and 17:29, $135,507 came into the win pool and runner
        # 5 took the largest single share of it — $16,689 — while its price
        # DRIFTED, because the rest of the field took more. That distinction is
        # invisible in the odds.
        flow = {r["horse_no"]: r for r in
                money_q.money_arrived(date, race_no, conn=conn)["runners"]}

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
                # The horse's settled style, with the tally it was read off.
                # Never a bare badge: `n`, `counts` and the last classified run
                # travel with it, so the page can show a habit and say when the
                # most recent start disagreed with it.
                "running_style": styles.get(r.horse_name),
                "market_rank": m_rank,
                "movement": moves.get(r.horse_no),
                "gear_change": gear.get(r.horse_name),
                "money": (by_no.get(r.horse_no) or {}).get("pools"),
                "money_flow": flow.get(r.horse_no),
                "vet": vet.get(r.horse_name, []),
                "vet_form": vet_recent.get(r.horse_name, []),
                # Negative means the model likes it more than the market does.
                "rank_delta": (r.sarr_rank - m_rank
                               if r.sarr_rank and m_rank else None),
                "last_run": {
                    "race_date": last.race_date, "place": last.place,
                    "figure": last.et_figure, "figure_display": last.figure_display,
                    "pace_style": last.pace_style,
                    "days_ago": _days_between(last.race_date, date),
                    "tags": [t for t in last.tags if t not in _ROUTINE],
                    # The sentence the tags were derived from. A chip reading
                    # "bled" is the signal; the stewards' own words are what
                    # the reader wants the moment they hover it.
                    "comment": last.incident_comment or last.running_comment,
                    "lane_notes": list(last.lane_notes),
                } if last else None,
            })
            runners.append(row)

        return {
            "race_date": date, "race_no": race_no,
            "venue": race.venue, "course": race.course, "surface": race.surface,
            "going": race.going, "distance": race.distance,
            "off_time": race.off_time,
            "race_class": race.race_class, "field_size": race.field_size,
            "concentration": conc,
            "overround": overround,
            "place_ratio_range": _place_ratio_range(race.runners),
            "head_to_head": _pairs_meeting_again(conn, date, race.runners),
            # What each pool on this race holds, and the pairs the money is
            # actually on. Race-level rather than per-runner because a pool
            # size is a fact about the race.
            "pool_money": {"pools": {**cash["pools"], **pairs["pools"]},
                           "captured_at": cash["turnover_captured_at"],
                           "pairs": pairs["pairs"],
                           "pairs_priced": pairs["priced"]},
            # THE PREVIOUS RACE'S DOUBLE, once that race has been decided.
            # Betting on a double shuts when its FIRST race goes off, so from
            # that moment the grid is frozen — and the winner's row in it is a
            # complete, settled book on THIS race, formed from different money
            # and half an hour earlier than anything in this race's own pool.
            # The first leg divides out exactly, so no estimate of it is made.
            "doubles": (pools_q.doubles_after_leg(date, race_no - 1, conn=conn)
                        if race_no > 1 else None),
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


def _swing_favours(a, b, gap_then: int | None, gap_now: int | None
                   ) -> dict[str, Any]:
    """Which horse the weight change favours, and by how much.

    `gap` is A's weight minus B's. A FALL means A carries less relative to B
    than it did, so A is better off by that much; a rise favours B. Equal
    weights either side is not "no swing" — it is a real change if the gap
    moved, and the horse that came down is the one that benefits.
    """
    if gap_then is None or gap_now is None:
        return {"favours_no": None, "favours_name": None, "favours_lb": None}
    move = gap_now - gap_then
    if move == 0:
        return {"favours_no": None, "favours_name": None, "favours_lb": 0}
    better = b if move > 0 else a
    return {"favours_no": better.horse_no, "favours_name": better.horse_name,
            "favours_lb": abs(move)}


def _pairs_meeting_again(conn: Connection, date: str, runners,
                         limit: int = 40) -> list[dict[str, Any]]:
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
                # How each FINISHED and what each CARRIED, which is the pair
                # the weight swing is about: "2nd (126) · 6th (126)" says the
                # one that beat the other was on the same weight, and today's
                # gap is the change to that.
                "a_place": last["pa"], "b_place": last["pb"],
                "a_weight_then": last["wa"], "b_weight_then": last["wb"],
                "gap_then": h2h["last_weight_gap"], "gap_now": today_gap,
                "swing": swing,
                # WHO THE SWING FAVOURS, said outright. The card used to print
                # "-9 → 0" and leave the reader to work out which horse got
                # the better of it — which is arithmetic done wrong under
                # time pressure, on the one figure the pair is sorted by.
                #
                # The gap is A minus B. It FALLING means A is carrying less
                # relative to B than it was, so A is better off.
                **_swing_favours(a, b, h2h["last_weight_gap"], today_gap),
                # Escalating tiers at 4, 6 and 8 lb. Most pairs clear none of
                # them, which is correct rather than a bug.
                "swing_tier": (3 if swing is not None and swing >= 8
                               else 2 if swing is not None and swing >= 6
                               else 1 if swing is not None and swing >= 4 else 0),
                # BOTH gates, so the card can show the move. Only the gate at
                # the last meeting was carried, and today's draw was sitting
                # unused on the runner two lines up — so the card showed two
                # bare numbers that read as a pair of draws and were in fact
                # one horse's history each.
                "a_gate_then": last["da"], "a_gate_now": a.draw,
                "b_gate_then": last["db"], "b_gate_now": b.draw,
            })
    out.sort(key=lambda p: (-(p["swing"] or 0), -p["meetings"]))
    return out[:limit]
