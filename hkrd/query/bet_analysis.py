"""What the ledger says about how the betting is going.

The design brief prints one rule across the whole analysis section:

    "EVERY FIGURE CARRIES n AND AN INTERVAL · A 12-BET SLICE IS NOT A FINDING"

So no function here returns a bare ROI. Every slice comes back with its bet
count and a 95% interval, and one marked `thin` when the count is too small for
the figure to mean anything. A slice with a wide interval that straddles zero
is not a result, and the page is expected to say so rather than print the point
estimate in green.

ROI is not a proportion, so no Wilson or normal interval applies to it: a
quinella that pays 40-1 makes the per-bet return distribution heavy-tailed, and
an interval assuming symmetry understates the upside and overstates the
downside. The interval here is a percentile bootstrap over BETS -- resample the
bets, recompute total return over total stake -- which respects both the stake
weighting and the tail. It is seeded, so the same ledger gives the same
interval every time.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from hkrd.query.market import band as concentration_band
from hkrd.store.connect import Connection, get_conn

__all__ = ["roi_interval", "slice_stats", "cumulative_pnl", "clv", "by_type",
           "allup_vs_straight", "by_concentration", "favourite_split",
           "reconciliation", "analysis", "THIN_BETS", "BOOTSTRAP_DRAWS"]

# Below this the interval is so wide that the point estimate misleads more than
# it informs. The page dims the row rather than hiding it -- a slice with 6
# bets in it is a fact about the betting even when it is not a finding.
THIN_BETS = 30

BOOTSTRAP_DRAWS = 2000
_SEED = 20260815


def roi_interval(pairs: list[tuple[float, float]], *,
                 draws: int = BOOTSTRAP_DRAWS) -> tuple[float, float] | None:
    """95% percentile bootstrap on ROI, from (stake, returned) per bet.

    Returns None below two bets: an interval from one observation is not an
    interval. The draws are seeded so a figure quoted from the page is the same
    figure tomorrow.
    """
    n = len(pairs)
    if n < 2:
        return None
    stakes = np.array([p[0] for p in pairs], dtype=float)
    returns = np.array([p[1] for p in pairs], dtype=float)
    rng = np.random.default_rng(_SEED)
    idx = rng.integers(0, n, size=(draws, n))
    staked = stakes[idx].sum(axis=1)
    returned = returns[idx].sum(axis=1)
    live = staked > 0
    if not live.any():
        return None
    roi = (returned[live] - staked[live]) / staked[live]
    lo, hi = np.percentile(roi, [2.5, 97.5])
    return round(float(lo), 3), round(float(hi), 3)


def slice_stats(rows: list[dict[str, Any]], label: str = "") -> dict[str, Any]:
    """One slice of the ledger, with everything the page needs to be honest
    about it: n, turnover, return, strike rate, ROI and its interval."""
    pairs = [(float(r["stake"] or 0), float(r["returned"] or 0)) for r in rows]
    staked = sum(p[0] for p in pairs)
    returned = sum(p[1] for p in pairs)
    hits = sum(1 for r in rows if r.get("hit") == 1)
    # `hit` is NULL on a Quinella/Quinella-Place half whose credit could only
    # be apportioned. Counting those as misses would understate the strike
    # rate, so the denominator is the bets that actually resolved one way.
    resolved = sum(1 for r in rows if r.get("hit") is not None)
    interval = roi_interval(pairs) if staked else None
    return {
        "label": label,
        "bets": len(rows),
        "staked": round(staked, 2),
        "returned": round(returned, 2),
        "pnl": round(returned - staked, 2),
        "roi": round((returned - staked) / staked, 3) if staked else None,
        "roi_ci": list(interval) if interval else None,
        # A slice whose interval straddles zero has not shown a profit, however
        # green the point estimate looks.
        "clears_zero": bool(interval and (interval[0] > 0 or interval[1] < 0)),
        "hits": hits,
        "resolved": resolved,
        "unresolved": len(rows) - resolved,
        "strike_rate": round(hits / resolved, 3) if resolved else None,
        "thin": len(rows) < THIN_BETS,
    }


def _ledger_rows(conn: Connection, account: str | None) -> list[dict[str, Any]]:
    where, params = ("WHERE account = ?", [account]) if account else ("", [])
    return [dict(r) for r in conn.execute(
        f"SELECT bet_id, race_date, race_no, bet_type, stake, returned, pnl, "
        f"hit, status, account, source, all_up_formula, placed_at "
        f"FROM bets {where} ORDER BY race_date, placed_at", params)]


def cumulative_pnl(*, account: str | None = None,
                   conn: Connection | None = None) -> dict[str, Any]:
    """P/L carried forward by meeting, not by bet.

    A day is the unit the user bets in, and a per-bet curve on 1,078 points
    reads as noise. The turnover on each day rides with it, because a $40 day
    and a $2,000 day moving the line by the same amount would be misleading.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(f"""
            SELECT race_date, count(*) bets, sum(stake) staked,
                   sum(returned) returned
            FROM bets {"WHERE account = ?" if account else ""}
            GROUP BY race_date ORDER BY race_date""",
            [account] if account else []).fetchall()
        series, running = [], 0.0
        for r in rows:
            pnl = (r["returned"] or 0) - (r["staked"] or 0)
            running += pnl
            series.append({
                "race_date": r["race_date"], "bets": r["bets"],
                "staked": round(r["staked"] or 0, 2),
                "returned": round(r["returned"] or 0, 2),
                "pnl": round(pnl, 2), "cumulative": round(running, 2)})
        return {
            "series": series,
            "meetings": len(series),
            "bets": sum(s["bets"] for s in series),
            "final": round(running, 2),
            "peak": round(max((s["cumulative"] for s in series), default=0.0), 2),
            "trough": round(min((s["cumulative"] for s in series), default=0.0), 2),
        }
    finally:
        if own:
            conn.close()


# The price the bet was struck at, against the price it closed at. Only a bet
# stamped on the day of the race can be priced -- 426 of the 1,078 carry a
# `placed_at` from days or weeks after the meeting, which is when the row was
# written, not when the wager was made. Those are left out rather than priced
# off a timestamp that means something else.
_CLV_SQL = """
    SELECT b.bet_id, b.bet_type, b.race_date, s.race_no, s.horse_no,
           b.placed_at, r.win_odds close_odds,
           (SELECT o.win_odds FROM odds_snapshots o
             WHERE o.race_date = b.race_date AND o.race_no = s.race_no
               AND o.horse_no = s.horse_no AND o.captured_at <= b.placed_at
               AND o.win_odds IS NOT NULL
             ORDER BY o.captured_at DESC LIMIT 1) taken_odds
    FROM bets b
    JOIN bet_selections s ON s.bet_id = b.bet_id
    JOIN runners r ON r.race_date = b.race_date AND r.race_no = s.race_no
                  AND r.horse_no = s.horse_no
    WHERE substr(b.placed_at, 1, 10) = b.race_date
      AND r.win_odds IS NOT NULL
"""


def clv(*, account: str | None = None,
        conn: Connection | None = None) -> dict[str, Any]:
    """Closing line value, per priced SELECTION rather than per bet.

    A quinella backs three horses at three prices; there is no single price the
    ticket was struck at, so averaging per selection is the only figure that
    means what it says. Positive CLV is taking 13.0 about a horse that closed
    at 9.7 -- the market moved towards the bet after it was made.

    The interval is the ordinary one on a mean, because CLV per selection is
    bounded below at -100% and is not the heavy-tailed quantity ROI is.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        sql = _CLV_SQL + (" AND b.account = ?" if account else "")
        rows = [dict(r) for r in conn.execute(sql, [account] if account else [])]
        priced = [r for r in rows if r["taken_odds"] and r["close_odds"]]
        for r in priced:
            r["clv"] = round(r["taken_odds"] / r["close_odds"] - 1, 4)

        total_selections = conn.execute(
            "SELECT count(*) FROM bet_selections s JOIN bets b "
            "ON b.bet_id = s.bet_id" + (" WHERE b.account = ?" if account else ""),
            [account] if account else []).fetchone()[0]

        n = len(priced)
        if not n:
            return {"selections": 0, "of_selections": total_selections,
                    "average": None, "ci": None, "beat_close": 0,
                    "beat_share": None, "thin": True}
        values = [r["clv"] for r in priced]
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
        half = 1.96 * (var / n) ** 0.5 if n > 1 else None
        beat = sum(1 for v in values if v > 0)
        return {
            "selections": n,
            "of_selections": total_selections,
            "coverage": round(n / total_selections, 3) if total_selections else None,
            "average": round(mean, 4),
            "ci": [round(mean - half, 4), round(mean + half, 4)] if half else None,
            "beat_close": beat,
            "beat_share": round(beat / n, 3),
            "thin": n < THIN_BETS,
        }
    finally:
        if own:
            conn.close()


def by_type(*, account: str | None = None,
            conn: Connection | None = None) -> list[dict[str, Any]]:
    """Every bet type as its own slice, turnover first."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = _ledger_rows(conn, account)
        groups: dict[str, list[dict]] = {}
        for r in rows:
            groups.setdefault(r["bet_type"], []).append(r)
        out = [slice_stats(v, k) for k, v in groups.items()]
        out.sort(key=lambda s: s["staked"], reverse=True)
        return out
    finally:
        if own:
            conn.close()


def allup_vs_straight(*, account: str | None = None,
                      conn: Connection | None = None) -> dict[str, Any]:
    """The chain against the straight bets placed on the same days.

    The comparison is restricted to meetings where an all-up was actually
    struck. Comparing all-ups against every straight bet in the ledger would
    compare two different sets of days as well as two different bet shapes.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = _ledger_rows(conn, account)
        allup_days = {r["race_date"] for r in rows
                      if r["bet_type"].startswith("ALLUP")}
        chain = [r for r in rows if r["bet_type"].startswith("ALLUP")]
        straight = [r for r in rows if not r["bet_type"].startswith("ALLUP")
                    and r["race_date"] in allup_days]
        return {
            "all_up": slice_stats(chain, "ALL-UP"),
            "straight": slice_stats(straight, "STRAIGHT, SAME DAYS"),
            "meetings": len(allup_days),
            "scope": "straight bets on the days an all-up was struck",
        }
    finally:
        if own:
            conn.close()


# Concentration is read off the CLOSING prices here, not off a snapshot. Only
# 17 of the 56 meetings in the archive carry odds snapshots at all, and the
# retrospective question -- did the coverage rule hold -- is about the market
# that settled, which every race has.
_CONC_SQL = """
    SELECT race_date, race_no, win_odds FROM runners
    WHERE win_odds IS NOT NULL AND win_odds > 0
"""


def _closing_concentration(conn: Connection) -> dict[tuple[str, int], float]:
    """Top-3 share of the implied book, per race, from closing prices."""
    races: dict[tuple[str, int], list[float]] = {}
    for r in conn.execute(_CONC_SQL):
        races.setdefault((r["race_date"], r["race_no"]), []).append(
            1.0 / r["win_odds"])
    out = {}
    for key, implied in races.items():
        total = sum(implied)
        if total > 0 and len(implied) >= 3:
            out[key] = sum(sorted(implied, reverse=True)[:3]) / total
    return out


def by_concentration(*, account: str | None = None,
                     conn: Connection | None = None) -> dict[str, Any]:
    """Does the coverage rule hold — strong, moderate, weak markets.

    An all-up spans races and so has no single market to be classified by. It
    is counted separately rather than assigned to a band it does not have.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        conc = _closing_concentration(conn)
        rows = _ledger_rows(conn, account)
        bands: dict[str, list[dict]] = {}
        spanning = []
        for r in rows:
            if r["race_no"] is None:
                spanning.append(r)
                continue
            value = conc.get((r["race_date"], r["race_no"]))
            name = concentration_band(value) if value is not None else None
            bands.setdefault(name or "unpriced", []).append(r)
        order = ["strong", "moderate", "weak", "unpriced"]
        return {
            "bands": [slice_stats(bands[k], k.upper())
                      for k in order if k in bands],
            "spanning_races": slice_stats(spanning, "ALL-UP · NO SINGLE MARKET"),
            "source": "closing prices",
        }
    finally:
        if own:
            conn.close()


def favourite_split(*, account: str | None = None,
                    conn: Connection | None = None) -> dict[str, Any]:
    """Tickets that included the market favourite against those that did not.

    The favourite is the shortest closing price in the race. A bet is counted
    as including it if ANY of its selections was the favourite in that
    selection's own race — which for an all-up means any leg.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        favourites = {}
        for r in conn.execute("""
            SELECT race_date, race_no, horse_no,
                   min(win_odds) OVER (PARTITION BY race_date, race_no) best,
                   win_odds
            FROM runners WHERE win_odds IS NOT NULL AND win_odds > 0"""):
            if r["win_odds"] == r["best"]:
                favourites.setdefault((r["race_date"], r["race_no"]),
                                      set()).add(r["horse_no"])

        with_fav: set[str] = set()
        for r in conn.execute("""
            SELECT b.bet_id, b.race_date, s.race_no, s.horse_no
            FROM bets b JOIN bet_selections s ON s.bet_id = b.bet_id"""):
            if r["horse_no"] in favourites.get(
                    (r["race_date"], r["race_no"]), ()):
                with_fav.add(r["bet_id"])

        rows = _ledger_rows(conn, account)
        inc = [r for r in rows if r["bet_id"] in with_fav]
        exc = [r for r in rows if r["bet_id"] not in with_fav]
        total = len(rows)
        out = {"included": slice_stats(inc, "FAVOURITE INCLUDED"),
               "excluded": slice_stats(exc, "FAVOURITE EXCLUDED")}
        out["included"]["share"] = round(len(inc) / total, 3) if total else None
        out["excluded"]["share"] = round(len(exc) / total, 3) if total else None
        return out
    finally:
        if own:
            conn.close()


def reconciliation(*, account: str | None = None,
                   conn: Connection | None = None) -> dict[str, Any]:
    """Imported statement rows set against logged bets, so nothing is silently
    merged.

    A bet counts as `confirmed` only when a statement was actually READ for it
    — `bet_statement_rows` records that, one row per file. A bookie reference
    recovered out of the legacy log's own notes is not confirmation: it is the
    log quoting a statement nobody has imported. Reporting those as reconciled
    would have said all 1,078 bets were checked when two statements covering
    two of thirty meetings had been read.

    `disagrees` is the case the whole section exists for: a statement and a log
    entry that name the same bet and do not agree on the money.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        where = " WHERE b.account = ?" if account else ""
        params = [account] if account else []
        rows = [dict(r) for r in conn.execute(f"""
            SELECT b.bet_id, b.source, b.settle_method, b.bookie_ref,
                   b.stake, b.returned,
                   s.source_file, s.stake st_stake, s.returned st_returned
            FROM bets b
            LEFT JOIN bet_statement_rows s ON s.bet_id = b.bet_id
            {where}""", params)]

        confirmed = [r for r in rows if r["source_file"]]

        # Compare the BLOCK, not the halves. A "Quinella - Quinella Place"
        # line is one debit and one credit covering two pools, so the ledger
        # holds two rows against one statement row -- and the halves are this
        # system's own arithmetic. Summing the ledger side back to the block
        # is the only comparison that can catch a real disagreement.
        blocks: dict[tuple[str, str], dict[str, Any]] = {}
        for r in confirmed:
            key = (r["source_file"], r["bookie_ref"])
            blk = blocks.setdefault(key, {
                "source_file": r["source_file"], "bookie_ref": r["bookie_ref"],
                "bet_ids": [], "ledger_stake": 0.0, "ledger_returned": 0.0,
                "statement_stake": r["st_stake"] or 0.0,
                "statement_returned": r["st_returned"] or 0.0})
            blk["bet_ids"].append(r["bet_id"])
            blk["ledger_stake"] += r["stake"] or 0.0
            blk["ledger_returned"] += r["returned"] or 0.0

        disagree = [
            {"bookie_ref": b["bookie_ref"], "source_file": b["source_file"],
             "bets": len(b["bet_ids"]),
             "ledger": [round(b["ledger_stake"], 2), round(b["ledger_returned"], 2)],
             "statement": [round(b["statement_stake"], 2),
                           round(b["statement_returned"], 2)]}
            for b in blocks.values()
            if abs(b["ledger_stake"] - b["statement_stake"]) > 0.01
            or abs(b["ledger_returned"] - b["statement_returned"]) > 0.01]
        disagree.sort(key=lambda d: d["bookie_ref"])

        files = [dict(r) for r in conn.execute(
            "SELECT source_file, count(*) bets, min(imported_at) imported_at "
            "FROM bet_statement_rows GROUP BY source_file ORDER BY source_file")]
        apportioned = sum(1 for r in rows
                          if r["settle_method"] == "statement_apportioned")
        quoted = sum(1 for r in rows
                     if r["bookie_ref"] and not r["source_file"])
        return {
            "total": len(rows),
            "confirmed": len(confirmed),
            "statement_only": sum(1 for r in confirmed
                                  if r["source"] == "statement"),
            # A reference the log quotes from a statement that has not been
            # imported. Not an error — just a bet no statement has been read
            # for yet.
            "quoted_not_read": quoted,
            "no_reference": sum(1 for r in rows if not r["bookie_ref"]),
            "blocks": len(blocks),
            "disagrees": disagree,
            "files": files,
            "apportioned": apportioned,
            "apportioned_note": (
                f"{apportioned} bet(s) carry half of a block credit because "
                "the statement paid one figure across two pools."
                if apportioned else None),
        }
    finally:
        if own:
            conn.close()


def analysis(*, account: str | None = None,
             conn: Connection | None = None) -> dict[str, Any]:
    """Everything the analysis section of the Bets page renders, in one read."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = _ledger_rows(conn, account)
        return {
            "overall": slice_stats(rows, "ALL BETS"),
            "cumulative": cumulative_pnl(account=account, conn=conn),
            "clv": clv(account=account, conn=conn),
            "by_type": by_type(account=account, conn=conn),
            "all_up": allup_vs_straight(account=account, conn=conn),
            "concentration": by_concentration(account=account, conn=conn),
            "favourite": favourite_split(account=account, conn=conn),
            "reconciliation": reconciliation(account=account, conn=conn),
            "thin_bets": THIN_BETS,
            "account": account,
        }
    finally:
        if own:
            conn.close()
