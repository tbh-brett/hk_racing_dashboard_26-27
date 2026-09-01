"""Writing a bet the user placed, and the guardrail settings entry reads.

Separate from `upsert.py`, which owns scraped truth. A scrape is idempotent by
construction — the same meeting fetched twice must land on the same rows — but a
bet is an event. Placing the same ticket twice is two bets, not one, and that
difference is why they do not share a module.

Design brief 06 Part 2 and 07 §3 govern what is stored:

  * `source` is `manual` here, and the ledger keeps it visually distinct from an
    imported statement row until the two are reconciled.
  * A guardrail never blocks. When one fires and the user goes past it, the
    override is recorded — that record is the whole point, because "reviewing
    which flags were overridden and how those bets performed is a genuine
    analysis, and it's only possible if the override is logged".
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from hkrd.store.connect import Connection, transaction

__all__ = ["new_bet_id", "insert_bet", "settings", "set_setting",
           "normalise_accounts",
           "overrides_for", "DEFAULT_SETTINGS"]

# What the guardrails compare against until the user sets their own. These are
# thresholds to warn at, never limits to enforce -- see `jobs/place_bet.py`.
DEFAULT_SETTINGS: dict[str, float] = {
    "raceday_ceiling": 2000.0,     # HK$ staked across one meeting
    "max_combinations": 60.0,      # combinations on a single ticket
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_bet_id() -> str:
    """A fresh id for a manually placed bet.

    Statement imports carry the bookie's own reference and dedup on it. A manual
    bet has no such handle before it appears on a statement, so it gets a uuid
    and reconciliation matches it on the meeting, race, type and stake instead.
    """
    return f"manual-{uuid.uuid4().hex[:16]}"


def insert_bet(conn: Connection, bet: dict[str, Any],
               selections: Sequence[dict[str, Any]],
               overrides: Sequence[dict[str, Any]] = ()) -> str:
    """Write one bet, its legs, and any guardrail the user chose to go past.

    All three land in a single transaction: a bet whose selections failed to
    write is worse than no bet at all, because the ledger would show a stake
    against nothing and the reconciliation would never match it.

    Returns the `bet_id`.
    """
    bet_id = str(bet.get("bet_id") or new_bet_id())
    placed = str(bet.get("placed_at") or _now())
    with transaction(conn):
        conn.execute(
            """INSERT INTO bets (bet_id, bookie_ref, account, race_date, venue,
                                 race_no, bet_type, all_up_formula, stake,
                                 returned, pnl, status, hit, settle_method,
                                 placed_at, settled_at, source, notes)
               VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?,
                       NULL, NULL, 'open', NULL, NULL, ?, NULL, 'manual', ?)""",
            (bet_id, bet.get("account"), bet["race_date"], bet.get("venue"),
             bet.get("race_no"), bet["bet_type"], bet.get("all_up_formula"),
             float(bet["stake"]), placed, bet.get("notes")))
        for s in selections:
            conn.execute(
                """INSERT INTO bet_selections
                       (bet_id, race_no, horse_no, leg_no, is_banker)
                   VALUES (?, ?, ?, ?, ?)""",
                (bet_id, int(s["race_no"]), int(s["horse_no"]),
                 int(s.get("leg_no", 0)), 1 if s.get("is_banker") else 0))
        for o in overrides:
            conn.execute(
                """INSERT INTO bet_overrides (bet_id, flag, detail, overridden_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(bet_id, flag) DO UPDATE SET
                       detail = excluded.detail,
                       overridden_at = excluded.overridden_at""",
                (bet_id, str(o["flag"]), o.get("detail"), placed))
    return bet_id


def settings(conn: Connection) -> dict[str, float]:
    """Guardrail thresholds, defaults filled in for keys never set.

    A missing row means "never configured", not "no limit" — returning the
    default rather than nothing is what keeps a fresh database warning at a
    sensible number instead of silently warning at nothing.
    """
    out = dict(DEFAULT_SETTINGS)
    for r in conn.execute("SELECT key, value FROM bet_settings"):
        out[r["key"]] = float(r["value"])
    return out


def set_setting(conn: Connection, key: str, value: float) -> None:
    """Change one threshold. Unknown keys are refused rather than stored."""
    if key not in DEFAULT_SETTINGS:
        raise ValueError(
            f"unknown setting {key!r}; known: {', '.join(sorted(DEFAULT_SETTINGS))}")
    with transaction(conn):
        conn.execute(
            """INSERT INTO bet_settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value, updated_at = excluded.updated_at""",
            (key, float(value), _now()))


def overrides_for(conn: Connection, bet_ids: Sequence[str]) -> dict[str, list[dict]]:
    """Which guardrails each of these bets was placed past, keyed by bet_id."""
    ids = [str(b) for b in bet_ids if b]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out: dict[str, list[dict]] = {}
    for r in conn.execute(
            f"""SELECT bet_id, flag, detail, overridden_at FROM bet_overrides
                WHERE bet_id IN ({marks}) ORDER BY bet_id, flag""", ids):
        out.setdefault(r["bet_id"], []).append(
            {"flag": r["flag"], "detail": r["detail"],
             "overridden_at": r["overridden_at"]})
    return out


# The two books the interface knows. A bet filed under anything else is a bet
# no page can show — see `query/prebet.ACCOUNTS`, which is the same pair.
KNOWN_ACCOUNTS = ("brett", "kelvin")


def normalise_accounts(conn: Connection, *, default: str = "brett") -> int:
    """Move bets filed under an unknown account into a known one.

    The legacy ledger predates the two-account split and filed all 1,078 bets
    under "personal". Left alone they are invisible: every account view asks
    for brett or kelvin, so the whole of the history reads as "no bets" and the
    Blackbook then calls every one of those runs a missed chance — a wrong
    answer that looks like a confident one.

    Returns how many moved, so a run that changed nothing says so.
    """
    marks = ",".join("?" * len(KNOWN_ACCOUNTS))
    return conn.execute(
        f"UPDATE bets SET account = ? "
        f"WHERE account IS NULL OR lower(account) NOT IN ({marks})",
        (default, *KNOWN_ACCOUNTS)).rowcount
