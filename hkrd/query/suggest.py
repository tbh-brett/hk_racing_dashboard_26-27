"""The ticket desk: a QIN + QPL banker ticket built around the owner's horse.

Asked for on 2026-11-16: something that reads the Briefing, the form and the
book and suggests banker tickets, which the owner accepts or rejects, and which
are scored once the result is in. Measured on production's database the next
day (docs/ticket-desk.md), and the measurement decided who does what:

  THE OWNER PICKS THE BANKER. Of this season's banker tickets, nine losses in
  ten were the banker missing the frame, so the banker is the bet. His bankers
  placed 62 where the place pool expected 51.7 (147) -- and what there is of
  that sat in the horses he had WRITTEN about before race day (`query/book_tier`
  as at the race): 37 against 28.3 over 83, where the 55 with nothing of his on
  record ran at the price, 21 against 20.3. Over 4,307 walk-forward races no
  machine banker did better than the price: the favourite 0.97, the model's top
  pick 0.98, the model's biggest disagreement with the market 1.01. So the
  candidates here are the runners `query/book_tier` gives a tier, and nothing
  else is ever proposed; any other runner can be nominated and is flagged,
  never refused.

  A CANDIDATE IS NOT A TIP. The list is a menu. Rebuilt over the same 20
  meetings, all 621 runners carrying his evidence placed 187 where the pool
  expected 181.1: the price. Whatever his bankers have over it is in which of
  them he chose, and that is one season -- by the interval this repo uses, 1.31
  [0.89, 1.73] is not yet clear of 1.00. `query/ticket_ledger.bankers` reads it
  live, so the page never has to quote these.

  THE ENGINE PICKS THE PARTNERS, ranked by the pool that pays them (AGENTS.md):
  the quinella place pool's own price of each pair with the banker, the quinella
  pool where there is no QPL, and the win market only where no pair pool was
  captured. With the same banker and stakes the market's next runners returned
  14 points more than the owner's partners at closing prices over 281 tickets
  -- but at prices he could have seen five minutes out the gap was 2.5 points
  and not distinguishable from nothing. His partners are no better than the
  market's order, so the market's order is the default and a swap is recorded as
  an edit (`jobs/decide_ticket`): that comparison is what the record is for.

  NOTHING HERE IS A REASON TO BET. Each line's price and chance come from the
  same pool, so the expected return of every ticket is that pool's payout -- the
  takeout, about 17.5% -- and no figure below says otherwise. What the desk adds
  is the cost in front of the bet, the lines that would not cover the ticket if
  they were the only one to land (16 of this season's 58 paying tickets returned
  less than they cost), and a record of what was taken, changed and passed.

A FLAG WARNS AND NEVER BLOCKS, as `jobs/place_bet` has it. The one thing refused
is a ticket that describes nothing: a banker not in the race, a partner twice.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from hkrd.query import banker_brief, banker_record, book_tier
from hkrd.query.banker_facts import evidence
from hkrd.query.market import NO_PRICE
from hkrd.query.ticket_build import (ENGINE, ENGINE_FROM, FLAGS, PARTNERS, UNIT_STAKE,
                                     NotOnCard, TicketError, book, build, from_book)
from hkrd.store import tickets as store
from hkrd.store.connect import Connection, get_conn
from hkrd.store.tips import races_gone_off

__all__ = ["ENGINE", "ENGINE_FROM", "UNIT_STAKE", "PARTNERS", "LEAD_MINUTES", "FLAGS",
           "TicketError", "NotOnCard", "book", "build", "evidence", "ticket", "paper",
           "meeting"]

# The paper ticket reads prices from at least this long before the pool shut:
# what a person could still have bet on. The same comparison read at the close
# flatters the market's order (+8.7 points against +2.5 five minutes out).
LEAD_MINUTES = 5.0

_HKT = dt.timezone(dt.timedelta(hours=8))
_TIER_ORDER = {t: i for i, t in enumerate(book_tier.TIERS)}


def _ts(stamp: str) -> dt.datetime:
    """A capture time on Hong Kong's clock. Captures are stored naive in HK
    time; an aware one (the legacy import) is converted, never compared across
    kinds (`market.snapshot_age_hours` has the incident)."""
    t = dt.datetime.fromisoformat(stamp)
    return t.astimezone(_HKT).replace(tzinfo=None) if t.tzinfo else t


def _field(conn: Connection, date: str, race_no: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT horse_no, horse_name, draw, jockey FROM runners "
        "WHERE race_date = ? AND race_no = ? AND coalesce(place_code, '') NOT LIKE 'W%' "
        "ORDER BY horse_no", (date, race_no))]


def _win_capture(conn: Connection, date: str, race_no: int, before: str | None
                 ) -> tuple[str | None, dict[int, float], dict[int, float]]:
    """The latest capture holding a real win price, at or before `before`."""
    sql = ("SELECT max(captured_at) FROM odds_snapshots WHERE race_date = ? "
           "AND race_no = ? AND win_odds IS NOT NULL AND win_odds < ?")
    args: list[Any] = [date, race_no, NO_PRICE]
    if before:
        sql += " AND captured_at <= ?"
        args.append(before)
    captured = conn.execute(sql, args).fetchone()[0]
    if not captured:
        return None, {}, {}
    win, place = {}, {}
    for r in conn.execute(
            "SELECT horse_no, win_odds, place_odds FROM odds_snapshots "
            "WHERE race_date = ? AND race_no = ? AND captured_at = ?",
            (date, race_no, captured)):
        if r["win_odds"] is not None and r["win_odds"] < NO_PRICE:
            win[r["horse_no"]] = r["win_odds"]
        if r["place_odds"] is not None and r["place_odds"] < NO_PRICE:
            place[r["horse_no"]] = r["place_odds"]
    return captured, win, place


def _pair_capture(conn: Connection, date: str, race_no: int, pool: str,
                  before: str | None) -> tuple[str | None, dict[tuple[int, int], float]]:
    """One whole capture of a pair pool, never two mixed (`pools.latest_pair_odds`)."""
    sql = ("SELECT max(captured_at) FROM odds_pairs WHERE race_date = ? "
           "AND race_no = ? AND pool = ?")
    args: list[Any] = [date, race_no, pool]
    if before:
        sql += " AND captured_at <= ?"
        args.append(before)
    captured = conn.execute(sql, args).fetchone()[0]
    if not captured:
        return None, {}
    return captured, {(r["horse_a"], r["horse_b"]): r["odds"] for r in conn.execute(
        "SELECT horse_a, horse_b, odds FROM odds_pairs WHERE race_date = ? "
        "AND race_no = ? AND pool = ? AND captured_at = ? AND odds IS NOT NULL",
        (date, race_no, pool, captured))}


def _closed_at(conn: Connection, date: str, race_no: int) -> str | None:
    """When the pool shut: HKJC's own signal, else the last capture taken (the
    capture stops at the close, so for a race with a result that is the close)."""
    row = conn.execute("SELECT closed_at FROM market_close WHERE race_date = ? "
                       "AND race_no = ?", (date, race_no)).fetchone()
    if row:
        return row[0]
    return conn.execute("SELECT max(captured_at) FROM odds_snapshots "
                        "WHERE race_date = ? AND race_no = ?", (date, race_no)).fetchone()[0]


def _read(conn: Connection, date: str, race_no: int, field: list[dict[str, Any]],
          before: str | None) -> dict[str, Any]:
    """One race's prices at one moment, read and worked out once for every
    banker asked about in it."""
    win_at, win, place = _win_capture(conn, date, race_no, before)
    qin_at, qin = _pair_capture(conn, date, race_no, "QIN", before)
    qpl_at, qpl = _pair_capture(conn, date, race_no, "QPL", before)
    return {"book": book(field, win, place, qin, qpl), "win_at": win_at,
            "pair_at": {"QPL pool": qpl_at, "QIN pool": qin_at}}


def _assemble(date: str, race_no: int, banker_no: int, prices: dict[str, Any],
              tier: dict[str, Any] | None, *, k: int | None = None,
              unit_stake: float | None = None,
              partners: list[int] | None = None) -> dict[str, Any]:
    t = from_book(banker_no, prices["book"], k=k, unit_stake=unit_stake,
                partners=partners)
    ev = evidence(tier)
    if not (ev and ev["own"]):
        t["flags"].insert(0, {
            "code": "no_own_evidence",
            "detail": ("in the system's book only" if ev else
                       "not in your book, and no trial or run note of yours since "
                       "its last run"),
            "basis": FLAGS["no_own_evidence"]})
    return {"race_date": date, "race_no": race_no, **t, "evidence": ev,
            "captured": {"win": prices["win_at"],
                         "pairs": prices["pair_at"].get(t["partner_source"])}}


def ticket(date: str, race_no: int, banker_no: int, *, k: int | None = None,
           partners: list[int] | None = None, unit_stake: float | None = None,
           conn: Connection | None = None) -> dict[str, Any]:
    """The ticket for one banker from the latest capture, with the owner's
    evidence on the horse and any decision already on record. Any declared
    runner may be the banker; one without his evidence is flagged."""
    own = conn is None
    conn = conn or get_conn()
    try:
        field = _field(conn, date, race_no)
        if not field:
            raise NotOnCard(f"no card stored for {date} race {race_no}")
        tiers = book_tier.for_meeting(date, conn=conn)
        prices = _read(conn, date, race_no, field, None)
        out = _assemble(date, race_no, banker_no, prices,
                        tiers.get((race_no, banker_no)), k=k, unit_stake=unit_stake,
                        partners=partners)
        out["gone"] = race_no in races_gone_off(conn, date)
        out["decision"] = _decision(store.decision(conn, date, race_no, banker_no))
        banker_brief.attach(conn, [out], _market(date, race_no, prices),
                            banker_record.record(conn=conn))
        return out
    finally:
        if own:
            conn.close()


def _market(date: str, race_no: int, prices: dict[str, Any]) -> dict[tuple, int]:
    """Each runner's place in the market at the capture the ticket was built on."""
    return {(date, race_no, h): r for h, r in prices["book"]["market_rank"].items()}


def _runners(bk: dict[str, Any], tiers: dict[tuple, dict], race_no: int
             ) -> list[dict[str, Any]]:
    """The field, for nominating a banker or choosing partners: number, name,
    price, place in the market and tier."""
    out = []
    for h, r in sorted(bk["by_no"].items()):
        t = tiers.get((race_no, h))
        out.append({"horse_no": h, "horse_name": r.get("horse_name"), "draw": r.get("draw"),
                    "jockey": r.get("jockey"), "win_odds": bk["win"].get(h),
                    "market_rank": bk["market_rank"].get(h),
                    "tier": t["tier"] if t else None})
    return out


def _decision(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """A stored decision without the snapshot it carries: the page asking has
    the live ticket in front of it, and the record reads the snapshot itself."""
    if d is None:
        return None
    return {k: (bool(v) if k == "late" else v) for k, v in d.items() if k != "seen_json"}


def paper(date: str, race_no: int, *, lead: float = LEAD_MINUTES,
          conn: Connection | None = None) -> list[dict[str, Any]]:
    """The paper tickets for a race that has gone off: every runner the owner
    had a tier on, and every banker he decided on, each built from the last
    capture at least `lead` minutes before the pool shut. Deterministic from
    stored captures, so it reads the same whenever it is asked."""
    own = conn is None
    conn = conn or get_conn()
    try:
        field = _field(conn, date, race_no)
        closed = _closed_at(conn, date, race_no)
        tiers = {h: t for (n, h), t in
                 book_tier.for_meeting(date, conn=conn, with_record=False).items()
                 if n == race_no}
        decided = {r[0] for r in conn.execute(
            "SELECT banker_no FROM ticket_decision WHERE race_date = ? AND race_no = ?",
            (date, race_no))}
        on_card = {r["horse_no"] for r in field}
        bankers = sorted(h for h in set(tiers) | decided if h in on_card)
        if not bankers:
            return []
        before = lead_used = None
        if closed:
            cut = (_ts(closed) - dt.timedelta(minutes=lead)).isoformat(timespec="seconds")
            got = _win_capture(conn, date, race_no, cut)[0]
            # A race captured too thinly to have a price that early is read at
            # its last one, and says how late that was.
            before = cut if got else closed
            used = got or _win_capture(conn, date, race_no, closed)[0]
            lead_used = (round((_ts(closed) - _ts(used)).total_seconds() / 60.0, 1)
                         if used else None)
        prices = _read(conn, date, race_no, field, before)
        out = []
        for h in bankers:
            t = _assemble(date, race_no, h, prices, tiers.get(h))
            t["closed_at"], t["lead_minutes"] = closed, lead_used
            out.append(t)
        return out
    finally:
        if own:
            conn.close()


def meeting(date: str, *, conn: Connection | None = None) -> dict[str, Any]:
    """Every race on a card with its candidate bankers -- the runners the owner
    has a tier on, his own evidence first -- each with the ticket the engine
    builds from the latest capture. A race that has gone off lists none: what
    was said about it is the record's to show (`query/ticket_record`)."""
    own = conn is None
    conn = conn or get_conn()
    try:
        races = [dict(r) for r in conn.execute(
            "SELECT race_no, off_time, venue, distance, race_class FROM races "
            "WHERE race_date = ? ORDER BY race_no", (date,))]
        gone = races_gone_off(conn, date) if races else set()
        tiers = book_tier.for_meeting(date, conn=conn) if races else {}
        decided = {(r["race_no"], r["banker_no"]) for r in conn.execute(
            "SELECT race_no, banker_no FROM ticket_decision WHERE race_date = ?", (date,))}
        out, every, market = [], [], {}
        for race in races:
            n = race["race_no"]
            field = _field(conn, date, n)
            entry = {**race, "field_size": len(field), "gone": n in gone,
                     "captured_at": None, "runners": [], "candidates": []}
            out.append(entry)
            if n in gone or not field:
                continue
            prices = _read(conn, date, n, field, None)
            entry["captured_at"] = prices["win_at"]
            entry["runners"] = _runners(prices["book"], tiers, n)
            market.update(_market(date, n, prices))
            on_card = {r["horse_no"] for r in field}
            for h in {h for (rn, h) in set(tiers) | decided if rn == n and h in on_card}:
                t = _assemble(date, n, h, prices, tiers.get((n, h)))
                t["decision"] = (_decision(store.decision(conn, date, n, h))
                                 if (n, h) in decided else None)
                entry["candidates"].append(t)
                every.append(t)
            entry["candidates"].sort(key=_candidate_order)
        if every:
            banker_brief.attach(conn, every, market, banker_record.record(conn=conn))
        return {"race_date": date, "engine": ENGINE, "unit_stake": UNIT_STAKE,
                "partners": PARTNERS, "breakeven": banker_record.BREAKEVEN,
                "reasons": [{"key": k, "label": v} for k, v in banker_record.REASONS],
                "races": out}
    finally:
        if own:
            conn.close()


def _candidate_order(t: dict[str, Any]) -> tuple:
    ev = t["evidence"]
    return (not (ev and ev["own"]), _TIER_ORDER.get(ev["tier"], 99) if ev else 99,
            t["banker"]["horse_no"])
