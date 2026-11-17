"""The owner's banker scorecard: what he leans towards, and what has paid.

Asked for on 2026-11-17 (docs/banker-card.md). Every banker he has taken --
from the ledger, and from the desk once he decides there -- is read with the
facts `query/banker_facts` knows as at its race, and so is every other runner
in the same races. Three readings per fact:

  lean       how much more often his banker had it than the field did. 2.0x
             is "you pick these twice as often as they turn up".
  his A/E    his bankers with it: how often they placed, against the place
             chance the price gave them (`ticket_ledger.place_chances`: the
             place pool where it was captured, else Harville-Henery on the
             starting prices). 1.00 is the market.
  field A/E  the runners with it that he did NOT bank, in the same races,
             against the same baseline. His A/E above the field's is his
             choosing; a fact with both above 1.00 is the fact, not him.

Each value is also split by whether he had his OWN evidence on the horse,
because that is the one divide that held in both halves of the season when it
was measured (17 Nov 2026: 1.29 over 120 bankers with it, 1.05 over 133
without), and a fact can read differently on either side of it -- the horse
that had already placed at the course and trip, with nothing of his on it,
placed at 0.87 over 95.

THE LINE TO CLEAR is not 1.00. A quinella or quinella place ticket gives up
the takeout, so a banker has to land roughly 1/(1 - takeout) times as often as
the pools expect before its tickets pay: `BREAKEVEN`, about 1.21. Every line
says how it stands against both.

His REASONS -- the taps on a desk decision (`REASONS`) -- are scored the same
way, for the bankers he took and for the ones he passed. They start empty and
fill as he uses the desk.

A RECORD, NOT A RANKING (`query/tips_record`'s rule). Every line carries its
count and an interval, and one under `THIN` bankers is marked thin. Looked at
across fourteen facts, a few lines will clear 1.00 by luck; a lean is worth
acting on when it holds as the record grows, which is what this is for.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from hkrd.derive.probability import actual_over_expected
from hkrd.query import ticket_ledger
from hkrd.query.banker_facts import FACTS, VERSION, facts_for
from hkrd.store.connect import Connection, db_file, get_conn
from hkrd.store.tickets import FACT_COLUMNS

__all__ = ["REASONS", "REASON_LABEL", "OWN_ACCOUNTS", "TAKEOUT", "BREAKEVEN", "THIN",
           "SINCE", "record", "line"]

# What he can tell the desk that the database cannot see. Short on purpose: a
# tap is only worth having if it is quicker than not saying.
REASONS: tuple[tuple[str, str], ...] = (
    ("trial", "What I saw in a trial"),
    ("run", "What I saw in a run"),
    ("pace", "The pace and the map"),
    ("rider", "The rider"),
    ("stable", "The stable or connections"),
    ("market", "Market support"),
    ("weak", "A weak race"),
    ("draw", "The draw"),
    ("class", "Class or weight"),
    ("gut", "Gut"),
)
REASON_LABEL = dict(REASONS)

# Brett's own books: this season's account and the legacy log from April to
# June. Kelvin's sheet is his own betting and is not read as Brett's.
OWN_ACCOUNTS = ("brett", "personal")
TAKEOUT = 0.175                  # HKJC's deduction on the quinella and quinella place
BREAKEVEN = round(1.0 / (1.0 - TAKEOUT), 2)
THIN = 30                        # bet_analysis.THIN_BETS: below it a figure is a fact, not a finding
SINCE = "2026-04-01"             # the book, and so "your own evidence", begins here

# The heavy half -- every runner in his banker races with its facts and its
# price's place chance -- is kept until a bet, a result, a note or the book
# changes, an hour at most. A decision on the desk does not touch it: a race he
# decides on enters it only once it has a result, which moves the mark anyway.
_KEPT: dict[tuple, tuple[tuple, float, list]] = {}
_KEEP_FOR = 3600.0
_MARK_SQL = """
SELECT (SELECT count(*) || ',' || coalesce(max(rowid), 0) FROM bets),
       (SELECT count(*) || ',' || coalesce(max(race_date), '') FROM runners
         WHERE place IS NOT NULL AND race_date >= ?),
       (SELECT count(*) || ',' || coalesce(max(written_at), '') FROM trial_notes),
       (SELECT count(*) || ',' || coalesce(max(written_at), '') FROM run_notes),
       (SELECT count(*) || ',' || coalesce(max(added_date), '') || ','
               || coalesce(max(closed_date), '') || ',' || sum(status = 'active') FROM blackbook)
"""


def line(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Placed against the price's place chance, over `rows` each carrying
    `placed` and `chance`."""
    placed = sum(1 for r in rows if r["placed"])
    exp = sum(r["chance"] for r in rows)
    ae = actual_over_expected(exp, placed, len(rows))
    return {"bankers": len(rows), "placed": placed, "expected": ae["expected_wins"],
            "ae": ae["ae"], "ae_lo": ae["ae_lo"], "ae_hi": ae["ae_hi"],
            "thin": len(rows) < THIN,
            "clears": ("breakeven" if ae["ae"] is not None and ae["ae"] >= BREAKEVEN
                       else "market" if ae["ae"] is not None and ae["ae"] >= 1.0
                       else "below" if ae["ae"] is not None else None)}


def _bankers(conn: Connection, accounts: tuple[str, ...]) -> set[tuple[str, int, int]]:
    """One-banker tickets in his books, settled, and the desk's taken bankers."""
    marks = ",".join("?" * len(accounts))
    got = {tuple(r) for r in conn.execute(
        f"SELECT DISTINCT b.race_date, b.race_no, s.horse_no FROM bets b "
        f"JOIN bet_selections s ON s.bet_id = b.bet_id AND s.is_banker = 1 "
        f"WHERE b.bet_type IN ({', '.join(repr(t) for t in ticket_ledger.BANKER_TYPES)}) "
        f"  AND b.race_no IS NOT NULL AND b.race_date >= ? "
        f"  AND lower(b.account) IN ({marks}) "
        f"  AND (SELECT count(*) FROM bet_selections x WHERE x.bet_id = b.bet_id "
        f"       AND x.is_banker = 1) = 1", [SINCE, *accounts])}
    return got


def _decisions(conn: Connection, accounts: tuple[str, ...]
               ) -> tuple[dict[tuple, str], dict[tuple, list[str]]]:
    """The desk's decisions made in time, as taken or passed, and their taps."""
    decided: dict[tuple, str] = {}
    for d, n, h, kind, acct in conn.execute(
            "SELECT race_date, race_no, banker_no, decision, account FROM ticket_decision "
            "WHERE late = 0 AND race_date >= ?", (SINCE,)):
        if acct is None or acct.lower() in accounts:
            decided[(d, n, h)] = "taken" if kind in ("accepted", "edited") else "passed"
    reasons: dict[tuple, list[str]] = defaultdict(list)
    for d, n, h, why in conn.execute("SELECT race_date, race_no, banker_no, reason "
                                     "FROM ticket_reason WHERE race_date >= ?", (SINCE,)):
        reasons[(d, n, h)].append(why)
    return decided, reasons


def _kept_facts(conn: Connection, dates: list[str]) -> dict[tuple, dict[str, Any]]:
    """The facts `jobs/record_tickets` kept for every runner of these days."""
    out: dict[tuple, dict[str, Any]] = {}
    cols = ", ".join(FACT_COLUMNS)
    for i in range(0, len(dates), 500):
        chunk = dates[i:i + 500]
        for r in conn.execute(
                f"SELECT race_date, race_no, horse_no, {cols} FROM runner_fact "
                f"WHERE version = ? AND race_date IN ({','.join('?' * len(chunk))})",
                [VERSION, *chunk]):
            out[(r[0], r[1], r[2])] = dict(zip(FACT_COLUMNS, tuple(r)[3:]))
    return out


def _rows(conn: Connection, accounts: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every runner that started in a race he banked in -- from the ledger or
    on the desk -- with a result and a price, its facts as at the race and its
    place chance. The heavy half, kept between requests."""
    ledger = _bankers(conn, accounts)
    decided = {r[:3] for r in conn.execute(
        "SELECT race_date, race_no, banker_no FROM ticket_decision "
        "WHERE late = 0 AND race_date >= ?", (SINCE,))}
    races = {(d, n) for d, n, _ in ledger | decided}
    dates = sorted({d for d, _ in races})
    runs = []
    for i in range(0, len(dates), 500):
        chunk = dates[i:i + 500]
        runs += [dict(r) for r in conn.execute(
            f"SELECT race_date, race_no, horse_no, horse_name, place FROM runners "
            f"WHERE race_date IN ({','.join('?' * len(chunk))}) "
            f"  AND coalesce(place_code, '') NOT LIKE 'W%'", chunk)
            if (r["race_date"], r["race_no"]) in races]
    chances = ticket_ledger.place_chances(conn, dates[0], races) if dates else {}
    # Only races with a result and a price: the record of a race not yet run is
    # nothing, and a race nobody priced has no expectation to measure against.
    done = {(r["race_date"], r["race_no"]) for r in runs if r["place"] is not None}
    runs = [r for r in runs if (r["race_date"], r["race_no"]) in done & chances.keys()]
    facts = _kept_facts(conn, dates)
    missing = [r for r in runs if (r["race_date"], r["race_no"], r["horse_no"]) not in facts]
    if missing:
        # A race whose facts the record job has not kept yet (it runs after each
        # meeting's results): worked out here, the same function, so the
        # scorecard is never short of a race for want of a cron tick.
        facts.update(facts_for(conn, missing))
    rows = []
    for r in runs:
        key = (r["race_date"], r["race_no"], r["horse_no"])
        source, chance, places = chances[(r["race_date"], r["race_no"])]
        if r["horse_no"] in chance:
            rows.append({"key": key, "chance": chance[r["horse_no"]], "source": source,
                         "placed": r["place"] is not None and r["place"] <= places,
                         "ledger": key in ledger, "facts": facts.get(key, {})})
    return rows


def _aggregate(rows: list[dict[str, Any]], decided: dict[tuple, str],
               reasons: dict[tuple, list[str]], accounts: tuple[str, ...]) -> dict[str, Any]:
    """The light half: who he took and passed, and every line, from the kept rows."""
    mine, field, passed = [], [], []
    for r in rows:
        k = r["key"]
        if r["ledger"] or decided.get(k) == "taken":
            mine.append(r)
        else:
            field.append(r)
            if decided.get(k) == "passed":
                passed.append(r)

    out_facts = []
    for key, label, values in FACTS:
        known_mine = [r for r in mine if r["facts"].get(key) is not None]
        n_known = sum(1 for r in rows if r["facts"].get(key) is not None)
        all_by = defaultdict(int)
        for r in rows:
            all_by[r["facts"].get(key)] += 1
        mine_by, field_by = defaultdict(list), defaultdict(list)
        for r in known_mine:
            mine_by[r["facts"][key]].append(r)
        for r in field:
            field_by[r["facts"].get(key)].append(r)
        vals = []
        for v in values:
            m = mine_by[v]
            share_mine = len(m) / len(known_mine) if known_mine else 0.0
            share_all = all_by[v] / n_known if n_known else 0.0
            vals.append({
                "value": v, **line(m),
                "lean": round(share_mine / share_all, 2) if share_all else None,
                "field": line(field_by[v]),
                "with_own": line([r for r in m if r["facts"].get("own") == "yours"]),
                "without_own": line([r for r in m if r["facts"].get("own") != "yours"])})
        out_facts.append({"key": key, "label": label, "values": vals,
                          "known": len(known_mine), "unknown": len(mine) - len(known_mine)})

    out_reasons = [{"key": key, "label": label,
                    "taken": line([r for r in mine if key in reasons.get(r["key"], ())]),
                    "passed": line([r for r in passed if key in reasons.get(r["key"], ())])}
                   for key, label in REASONS]
    return {
        "accounts": list(accounts), "since": SINCE,
        "through": max((r["key"][0] for r in mine), default=None),
        "races": len({r["key"][:2] for r in mine}),
        "breakeven": BREAKEVEN, "takeout": TAKEOUT, "thin": THIN,
        "overall": line(mine), "field": line(field),
        # His usual on each side of the one divide that held: what a brief
        # reads a fact against (`banker_brief`).
        "sides": {"with_own": line([r for r in mine if r["facts"].get("own") == "yours"]),
                  "without_own": line([r for r in mine if r["facts"].get("own") != "yours"])},
        "from_pool": sum(1 for r in mine if r["source"] == "place pool"),
        "facts": out_facts, "reasons": out_reasons,
        "desk": {"taken": line([r for r in mine if decided.get(r["key"]) == "taken"]),
                 "passed": line(passed),
                 "with_reasons": sum(1 for r in mine + passed if reasons.get(r["key"]))}}


def record(*, account: str | None = None, conn: Connection | None = None) -> dict[str, Any]:
    """The scorecard over his books (or one of them)."""
    accounts = (account.lower(),) if account else OWN_ACCOUNTS
    own = conn is None
    conn = conn or get_conn()
    try:
        where = db_file(conn)
        mark = tuple(conn.execute(_MARK_SQL, (SINCE,)).fetchone())
        kept = _KEPT.get((where, accounts))
        if where and kept and kept[0] == mark and time.monotonic() - kept[1] < _KEEP_FOR:
            rows = kept[2]
        else:
            rows = _rows(conn, accounts)
            if where:
                _KEPT[(where, accounts)] = (mark, time.monotonic(), rows)
        decided, reasons = _decisions(conn, accounts)
    finally:
        if own:
            conn.close()
    return _aggregate(rows, decided, reasons, accounts)
