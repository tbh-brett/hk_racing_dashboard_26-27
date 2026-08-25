"""The bets ledger, and what it says about the blackbook.

Design brief 06 Part 1 names the feature this exists for:

    "A blackbook horse ran and was not backed. This must be recorded, and it is
     the single most important feature on the page. Without it you only ever see
     the hits, and the book becomes a scrapbook rather than a tracked signal.
     The system knows when a booked horse is declared, so it can detect a
     non-bet automatically after the race and log it — the user shouldn't have
     to remember to record an absence."

That is a join, not a logging discipline: a run since booking either has a
matching row in `bet_selections` or it does not. Nothing has to be remembered,
which is the whole point.

The comparison the brief asks for is backed-versus-missed RETURN, and it is a
comparison of unlike things unless the missed side is counted the same way as
the backed side. So a missed run is priced at a flat notional win stake and
said to be notional wherever it is shown.
"""
from __future__ import annotations

from typing import Any

from hkrd.store.connect import Connection, get_conn

__all__ = ["ledger", "bets_for_race", "bets_for_horse", "backed_and_missed",
           "summary"]

# What a missed run is priced at, so the two sides of the comparison are
# commensurable. A round number, stated on the page, never silently applied.
NOTIONAL_STAKE = 100.0


def _rows(conn: Connection, sql: str, params: Any = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def ledger(*, date: str | None = None, account: str | None = None,
           limit: int = 500, conn: Connection | None = None) -> list[dict[str, Any]]:
    """Bets, newest first, with the horses each one backed."""
    own = conn is None
    conn = conn or get_conn()
    try:
        where, params = ["1 = 1"], []
        if date:
            where.append("b.race_date = ?")
            params.append(date)
        if account:
            where.append("b.account = ?")
            params.append(account)
        params.append(limit)
        bets = _rows(conn, f"""
            SELECT b.* FROM bets b
            WHERE {' AND '.join(where)}
            ORDER BY b.race_date DESC, b.race_no DESC, b.placed_at DESC
            LIMIT ?""", params)
        if not bets:
            return []

        ids = [b["bet_id"] for b in bets]
        marks = ",".join("?" * len(ids))
        picks: dict[str, list[dict]] = {}
        for r in conn.execute(f"""
            SELECT s.bet_id, s.race_no, s.horse_no, s.leg_no, s.is_banker,
                   r.horse_name, r.place, r.win_odds
            FROM bet_selections s
            JOIN bets b ON b.bet_id = s.bet_id
            LEFT JOIN runners r ON r.race_date = b.race_date
                               AND r.race_no = s.race_no
                               AND r.horse_no = s.horse_no
            WHERE s.bet_id IN ({marks})
            ORDER BY s.leg_no, s.horse_no""", ids):
            picks.setdefault(r["bet_id"], []).append(dict(r))

        # Which selections were booked horses, which statement confirmed the
        # bet, and what price it was struck at against the close. All three are
        # columns the ledger table renders, and all three are joins rather than
        # anything the user has to have recorded.
        booked: dict[str, list[str]] = {}
        for r in conn.execute(f"""
            SELECT DISTINCT s.bet_id, k.id entry_id, k.horse_name
            FROM bet_selections s
            JOIN bets b ON b.bet_id = s.bet_id
            JOIN runners r ON r.race_date = b.race_date
                          AND r.race_no = s.race_no AND r.horse_no = s.horse_no
            JOIN blackbook k ON k.horse_name = r.horse_name
                            AND b.race_date >= k.added_date
            WHERE s.bet_id IN ({marks})""", ids):
            booked.setdefault(r["bet_id"], []).append(
                {"entry_id": r["entry_id"], "horse_name": r["horse_name"]})

        confirmed = {r[0] for r in conn.execute(
            f"SELECT DISTINCT bet_id FROM bet_statement_rows "
            f"WHERE bet_id IN ({marks})", ids)}

        # CLV per selection, averaged over the ones that can be priced. A
        # ticket has no single price it was struck at, so this is a mean over
        # its legs and is None when none of them can be priced.
        clv: dict[str, list[float]] = {}
        for r in conn.execute(f"""
            SELECT s.bet_id, r.win_odds close_odds,
                   (SELECT o.win_odds FROM odds_snapshots o
                     WHERE o.race_date = b.race_date AND o.race_no = s.race_no
                       AND o.horse_no = s.horse_no
                       AND o.captured_at <= b.placed_at
                       AND o.win_odds IS NOT NULL
                     ORDER BY o.captured_at DESC LIMIT 1) taken
            FROM bet_selections s
            JOIN bets b ON b.bet_id = s.bet_id
            JOIN runners r ON r.race_date = b.race_date
                          AND r.race_no = s.race_no AND r.horse_no = s.horse_no
            WHERE s.bet_id IN ({marks})
              AND substr(b.placed_at, 1, 10) = b.race_date
              AND r.win_odds IS NOT NULL""", ids):
            if r["taken"] and r["close_odds"]:
                clv.setdefault(r["bet_id"], []).append(
                    r["taken"] / r["close_odds"] - 1)

        for b in bets:
            b["selections"] = picks.get(b["bet_id"], [])
            b["roi"] = (round((b["returned"] - b["stake"]) / b["stake"], 3)
                        if b["stake"] and b["returned"] is not None else None)
            b["blackbook"] = booked.get(b["bet_id"], [])
            b["statement_confirmed"] = b["bet_id"] in confirmed
            legs = clv.get(b["bet_id"])
            b["clv"] = round(sum(legs) / len(legs), 4) if legs else None
            b["clv_legs"] = len(legs or ())
        return bets
    finally:
        if own:
            conn.close()


def bets_for_race(date: str, race_no: int, *,
                  conn: Connection | None = None) -> list[dict[str, Any]]:
    """Every bet touching one race, including the all-up legs that pass
    through it — an all-up has no race number of its own, so a bet on race 4
    would otherwise vanish from race 4."""
    own = conn is None
    conn = conn or get_conn()
    try:
        return _rows(conn, """
            SELECT DISTINCT b.*, s.leg_no
            FROM bets b
            JOIN bet_selections s ON s.bet_id = b.bet_id
            WHERE b.race_date = ? AND s.race_no = ?
            ORDER BY b.placed_at""", (date, race_no))
    finally:
        if own:
            conn.close()


def bets_for_horse(horse_name: str, *, since: str | None = None,
                   conn: Connection | None = None) -> list[dict[str, Any]]:
    """Every bet that included this horse. The join is on the runner row, so a
    horse is matched by its number IN ITS RACE rather than by a name typed onto
    a betting slip.

    `race_no` is the LEG's race, not the ticket's. An all-up carries no race
    number of its own -- its 29 rows here have `bets.race_no` NULL -- so keying
    off the ticket would strand every all-up leg away from the run it was
    actually on. `legs` says how many races the ticket spanned, because a
    three-leg all-up's stake is not money on this horse alone.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = """
            SELECT b.bet_id, b.race_date, s.race_no, b.bet_type, b.stake,
                   b.returned, b.pnl, b.hit, b.status, b.account,
                   s.is_banker, s.leg_no, r.place, r.win_odds,
                   (SELECT count(DISTINCT l.race_no) FROM bet_selections l
                     WHERE l.bet_id = b.bet_id) legs
            FROM bets b
            JOIN bet_selections s ON s.bet_id = b.bet_id
            JOIN runners r ON r.race_date = b.race_date
                          AND r.race_no = s.race_no
                          AND r.horse_no = s.horse_no
            WHERE r.horse_name = ?"""
        params: list[Any] = [horse_name.strip().upper()]
        if since:
            sql += " AND b.race_date > ?"
            params.append(since)
        return _rows(conn, sql + " ORDER BY b.race_date DESC, s.race_no", params)
    finally:
        if own:
            conn.close()


# Every run of a booked horse after the day it was booked, excluding the run
# the entry was written from, with whether a bet touched it. Mirrors the FROM
# clause in query/blackbook.py deliberately -- the two must agree on what "a
# run since booking" is or backed + missed will not sum to it.
_RUNS_SINCE = """
    FROM blackbook b
    JOIN runners r ON r.horse_name = b.horse_name AND r.race_date > b.added_date
                  AND NOT (b.source_date IS NOT NULL
                           AND r.race_date = b.source_date
                           AND r.race_no = b.source_race_no)
    LEFT JOIN (
        SELECT DISTINCT bt.race_date, s.race_no, s.horse_no
        FROM bets bt JOIN bet_selections s ON s.bet_id = bt.bet_id
    ) backed ON backed.race_date = r.race_date
            AND backed.race_no = r.race_no
            AND backed.horse_no = r.horse_no
"""


def backed_and_missed(*, entry_id: str | None = None,
                      conn: Connection | None = None) -> dict[str, Any]:
    """The falsifiability requirement: what was backed, what was not, and how
    each did.

    "If the missed ones outperform the backed ones, that's a finding about the
    user's own selection, not about the horses." — design brief 06.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        where = "WHERE r.place IS NOT NULL"
        params: list[Any] = []
        if entry_id:
            where += " AND b.id = ?"
            params.append(entry_id)

        runs = _rows(conn, f"""
            SELECT b.id, b.horse_name, r.race_date, r.race_no, r.horse_no,
                   r.place, r.win_odds,
                   backed.horse_no IS NOT NULL AS was_backed
            {_RUNS_SINCE}
            {where}
            ORDER BY r.race_date DESC""", params)

        def side(rows: list[dict], *, notional: bool) -> dict[str, Any]:
            priced = [x for x in rows if x["win_odds"]]
            wins = [x for x in priced if x["place"] == 1]
            staked = NOTIONAL_STAKE * len(priced)
            returned = NOTIONAL_STAKE * sum(x["win_odds"] for x in wins)
            odds = sorted(x["win_odds"] for x in priced)
            median = odds[len(odds) // 2] if odds else None
            # A strike rate on its own reads as skill. Beside the chance the
            # PRICE implied, it reads as what it is. Over the real book the
            # backed side strikes 12.8% at a median 7.9 (implied 12.7%) and the
            # missed side 5.1% at a median 21.0 (implied 4.8%) -- so the gap
            # between them is the price, not the picking.
            return {
                "runs": len(rows), "priced": len(priced), "wins": len(wins),
                "strike_rate": round(len(wins) / len(rows), 3) if rows else None,
                "median_odds": median,
                "implied_rate": round(1 / median, 3) if median else None,
                "staked": round(staked, 2), "returned": round(returned, 2),
                "roi": round((returned - staked) / staked, 3) if staked else None,
                "notional": notional,
            }

        backed = [x for x in runs if x["was_backed"]]
        missed = [x for x in runs if not x["was_backed"]]

        # The backed side ALSO priced at the notional flat win stake, not at
        # what was actually staked. Otherwise the comparison is a flat win bet
        # against a book of quinellas and multi-leg tickets, and any difference
        # is the bet type rather than the selection.
        out = {
            "runs": len(runs),
            "backed": side(backed, notional=True),
            "missed": side(missed, notional=True),
            "notional_stake": NOTIONAL_STAKE,
        }
        b_roi, m_roi = out["backed"]["roi"], out["missed"]["roi"]
        out["verdict"] = (
            None if b_roi is None or m_roi is None
            else "missed outperformed backed" if m_roi > b_roi
            else "backed outperformed missed")

        # Whether either side beat the price it was offered, which is the
        # question the ROI gap only looks like it answers. A side striking at
        # its implied rate has been selected no better than by reading the
        # board, however far apart the two strike rates are.
        for name in ("backed", "missed"):
            d = out[name]
            d["vs_implied"] = (
                round(d["strike_rate"] - d["implied_rate"], 3)
                if d["strike_rate"] is not None and d["implied_rate"] else None)

        # What was ACTUALLY staked and returned on those runs, kept separate so
        # the page can show the real ledger beside the like-for-like comparison.
        if backed:
            keys = {(x["race_date"], x["race_no"], x["horse_no"]) for x in backed}
            marks = ",".join("(?,?,?)" for _ in keys)
            flat = [v for k in keys for v in k]
            actual = conn.execute(f"""
                SELECT count(DISTINCT b.bet_id) bets, sum(b.stake) staked,
                       sum(b.returned) returned
                FROM bets b JOIN bet_selections s ON s.bet_id = b.bet_id
                WHERE (b.race_date, s.race_no, s.horse_no) IN ({marks})""",
                flat).fetchone()
            out["actual"] = {
                "bets": actual["bets"] or 0,
                "staked": round(actual["staked"] or 0, 2),
                "returned": round(actual["returned"] or 0, 2),
            }
            staked = out["actual"]["staked"]
            out["actual"]["roi"] = (
                round((out["actual"]["returned"] - staked) / staked, 3)
                if staked else None)
        else:
            out["actual"] = {"bets": 0, "staked": 0.0, "returned": 0.0, "roi": None}
        return out
    finally:
        if own:
            conn.close()


def summary(*, account: str | None = None,
            conn: Connection | None = None) -> dict[str, Any]:
    """The ledger's headline: turnover, return, strike rate, by bet type."""
    own = conn is None
    conn = conn or get_conn()
    try:
        where, params = ("WHERE account = ?", [account]) if account else ("", [])
        total = conn.execute(f"""
            SELECT count(*) bets, sum(stake) staked, sum(returned) returned,
                   sum(CASE WHEN hit = 1 THEN 1 ELSE 0 END) hits,
                   min(race_date) first_date, max(race_date) last_date,
                   count(DISTINCT race_date) meetings
            FROM bets {where}""", params).fetchone()
        by_type = _rows(conn, f"""
            SELECT bet_type, count(*) bets, sum(stake) staked,
                   sum(returned) returned,
                   sum(CASE WHEN hit = 1 THEN 1 ELSE 0 END) hits
            FROM bets {where}
            GROUP BY bet_type ORDER BY sum(stake) DESC""", params)
        for row in by_type:
            row["roi"] = (round((row["returned"] - row["staked"]) / row["staked"], 3)
                          if row["staked"] else None)
            row["strike_rate"] = (round(row["hits"] / row["bets"], 3)
                                  if row["bets"] else None)

        staked = total["staked"] or 0
        return {
            "bets": total["bets"] or 0,
            "staked": round(staked, 2),
            "returned": round(total["returned"] or 0, 2),
            "pnl": round((total["returned"] or 0) - staked, 2),
            "roi": round(((total["returned"] or 0) - staked) / staked, 3) if staked else None,
            "hits": total["hits"] or 0,
            "strike_rate": (round(total["hits"] / total["bets"], 3)
                            if total["bets"] else None),
            "meetings": total["meetings"] or 0,
            "span": [total["first_date"], total["last_date"]],
            "by_type": by_type,
            "accounts": [r["account"] for r in conn.execute(
                "SELECT DISTINCT account FROM bets ORDER BY account")],
        }
    finally:
        if own:
            conn.close()
