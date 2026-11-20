"""The write path for the ticket desk. Coerced here, on the way in.

Two records, with opposite rules about being written twice:

  * a SUGGESTION is the engine's paper ticket for a race that has gone off. It
    is written once and a second write of the same banker changes nothing, so a
    job that runs again -- or runs under a later engine -- cannot rescore a
    race it has already been judged on.
  * a DECISION is the owner's, and he may change his mind until the pool shuts:
    the same key replaces the row and its partners together.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from . import coerce

__all__ = ["record_suggestions", "save_decision", "decision", "unrecorded_races",
           "FACT_COLUMNS", "record_facts", "unrecorded_fact_races"]

_KEY = ("race_date", "race_no", "banker_no")
_TEXT = ("tier", "partner_source", "place_source", "win_captured_at",
         "pair_captured_at", "engine", "recorded_at")
_REAL = ("unit_stake", "win_odds", "place_pct", "model_pct", "top2_pct", "lead_minutes")
_INT = ("field_size", "market_rank")
_SUGGESTION = _KEY + ("own",) + _TEXT + _REAL + _INT


def _real(v: object) -> float | None:
    return float(v) if v is not None else None


def _key(r: dict[str, Any]) -> tuple[str | None, int | None, int | None]:
    return (coerce.to_date(r["race_date"]),
            coerce.to_int(r["race_no"], field="race_no"),
            coerce.to_int(r["banker_no"], field="banker_no"))


def record_suggestions(conn: sqlite3.Connection, rows: Sequence[dict[str, Any]]) -> int:
    """Store the paper tickets not stored yet. Each row carries `partners`, a
    list of {horse_no, rank, win_odds, qin_odds, qpl_odds}. Returns how many
    tickets were added: a banker already recorded keeps the row AND the
    partners it has."""
    added = 0
    for r in rows:
        key = _key(r)
        values = [*key, int(bool(r.get("own"))),
                  *(r.get(c) for c in _TEXT),
                  *(_real(r.get(c)) for c in _REAL),
                  *(coerce.to_int(r.get(c), field=c) for c in _INT)]
        before = conn.total_changes
        conn.execute(
            f"INSERT INTO ticket_suggestion ({', '.join(_SUGGESTION)}) VALUES "
            f"({', '.join('?' for _ in _SUGGESTION)}) "
            "ON CONFLICT (race_date, race_no, banker_no) DO NOTHING", values)
        if conn.total_changes == before:
            continue                     # already on record: its partners stand too
        added += 1
        conn.executemany(
            "INSERT INTO ticket_suggestion_partner (race_date, race_no, banker_no, "
            "  horse_no, rank, win_odds, qin_odds, qpl_odds) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT (race_date, race_no, banker_no, horse_no) DO NOTHING",
            [(*key, coerce.to_int(p["horse_no"], field="horse_no"),
              coerce.to_int(p["rank"], field="rank"), _real(p.get("win_odds")),
              _real(p.get("qin_odds")), _real(p.get("qpl_odds")))
             for p in r.get("partners") or []])
    return added


def decision(conn: sqlite3.Connection, race_date: str, race_no: int,
             banker_no: int) -> dict[str, Any] | None:
    """The decision on one banker, with its partners in number order."""
    row = conn.execute(
        "SELECT * FROM ticket_decision WHERE race_date = ? AND race_no = ? "
        "AND banker_no = ?", (race_date, race_no, banker_no)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["partners"] = [r[0] for r in conn.execute(
        "SELECT horse_no FROM ticket_decision_partner WHERE race_date = ? "
        "AND race_no = ? AND banker_no = ? ORDER BY horse_no",
        (race_date, race_no, banker_no))]
    out["reasons"] = [r[0] for r in conn.execute(
        "SELECT reason FROM ticket_reason WHERE race_date = ? AND race_no = ? "
        "AND banker_no = ? ORDER BY reason", (race_date, race_no, banker_no))]
    # How sure he was is kept with the horse's claim on the banker review
    # (`jobs/review_banker`), where the desk writes it.
    sure = conn.execute(
        "SELECT confidence FROM banker_review WHERE race_date = ? AND race_no = ? "
        "AND horse_no = ?", (race_date, race_no, banker_no)).fetchone()
    out["confidence"] = sure[0] if sure else None
    return out


def save_decision(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Write one decision, its partners and its reasons, replacing what stood.
    The caller (jobs/decide_ticket) has already refused a late change to a
    decision made in time and checked the reasons against the vocabulary;
    this only writes."""
    key = _key(row)
    conn.execute(
        "INSERT INTO ticket_decision (race_date, race_no, banker_no, decision, "
        "  account, unit_stake, reason, late, seen_json, engine, decided_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (race_date, race_no, banker_no) DO UPDATE SET "
        "  decision = excluded.decision, account = excluded.account, "
        "  unit_stake = excluded.unit_stake, reason = excluded.reason, "
        "  late = excluded.late, seen_json = excluded.seen_json, "
        "  engine = excluded.engine, decided_at = excluded.decided_at",
        (*key, row["decision"], (row.get("account") or None),
         _real(row.get("unit_stake")), (row.get("reason") or None),
         int(bool(row.get("late"))), row["seen_json"], row["engine"],
         row["decided_at"]))
    conn.execute("DELETE FROM ticket_decision_partner WHERE race_date = ? "
                 "AND race_no = ? AND banker_no = ?", key)
    conn.executemany(
        "INSERT INTO ticket_decision_partner (race_date, race_no, banker_no, horse_no) "
        "VALUES (?,?,?,?)",
        [(*key, coerce.to_int(h, field="horse_no")) for h in row.get("partners") or []])
    conn.execute("DELETE FROM ticket_reason WHERE race_date = ? AND race_no = ? "
                 "AND banker_no = ?", key)
    conn.executemany(
        "INSERT INTO ticket_reason (race_date, race_no, banker_no, reason) VALUES (?,?,?,?)",
        [(*key, str(x).strip()) for x in sorted(set(row.get("reasons") or []))])


FACT_COLUMNS = ("own", "tier", "model", "course_trip", "market", "last_start", "close_last",
                "since_last", "class", "draw", "style", "rider", "trial", "gear", "tips")


def record_facts(conn: sqlite3.Connection, rows: Sequence[dict[str, Any]],
                 *, version: str, at: str) -> int:
    """Keep each runner's facts as at its race. Returns how many runners were
    added: one already kept under this version keeps the row it has."""
    cols = ("race_date", "race_no", "horse_no", "version", *FACT_COLUMNS, "recorded_at")
    prepared = [(coerce.to_date(r["race_date"]), coerce.to_int(r["race_no"], field="race_no"),
                 coerce.to_int(r["horse_no"], field="horse_no"), version,
                 *(r.get(c) for c in FACT_COLUMNS), at) for r in rows]
    if not prepared:
        return 0
    before = conn.total_changes
    conn.executemany(
        f"INSERT INTO runner_fact ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        "ON CONFLICT (race_date, race_no, horse_no, version) DO NOTHING", prepared)
    return conn.total_changes - before


def unrecorded_fact_races(conn: sqlite3.Connection, since: str, version: str
                          ) -> list[tuple[str, int]]:
    """Races on or after `since` with a result stored and no runner's facts
    kept under `version`."""
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT r.race_date, r.race_no FROM races r WHERE r.race_date >= ? "
        " AND EXISTS (SELECT 1 FROM runners u WHERE u.race_date = r.race_date"
        "             AND u.race_no = r.race_no AND u.place IS NOT NULL)"
        " AND NOT EXISTS (SELECT 1 FROM runner_fact f WHERE f.race_date = r.race_date"
        "                  AND f.race_no = r.race_no AND f.version = ?)"
        " ORDER BY r.race_date, r.race_no", (since, version))]


def unrecorded_races(conn: sqlite3.Connection, since: str) -> list[tuple[str, int]]:
    """Races on or after `since` that have gone off -- HKJC shut the pool, or a
    result is stored (`store/tips.races_gone_off`'s rule) -- and have no paper
    ticket on record. A race with no candidate banker is asked about again and
    answers nothing each time, which costs the caller one cheap read; `since`
    is what keeps that to a handful of meetings."""
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT r.race_date, r.race_no FROM races r WHERE r.race_date >= ? AND ("
        "  EXISTS (SELECT 1 FROM market_close m WHERE m.race_date = r.race_date"
        "          AND m.race_no = r.race_no)"
        "  OR EXISTS (SELECT 1 FROM runners u WHERE u.race_date = r.race_date"
        "             AND u.race_no = r.race_no AND u.place IS NOT NULL))"
        " AND NOT EXISTS (SELECT 1 FROM ticket_suggestion s"
        "                  WHERE s.race_date = r.race_date AND s.race_no = r.race_no)"
        " ORDER BY r.race_date, r.race_no", (since,))]
