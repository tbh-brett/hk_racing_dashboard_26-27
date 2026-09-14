"""Correcting the ledger: editing a bet, deleting one, and putting one back.

Separate from `store/bets.py`, which writes a bet the owner PLACED. This is the
owner changing their mind about what the record says, and it has two
obligations a first write does not.

**A CORRECTION MUST SURVIVE A RE-IMPORT.** The statement importer, the bet-sheet
importer and the legacy-log importer all re-derive bets from a file every time
they read it. A bet deleted by hand comes straight back on the next statement
import, and an edited stake is overwritten by the file's — silently, which is
the one failure this rebuild exists to remove. So every importer asks this
module which bets it has been told to leave alone (`deleted_identities`) and,
after it writes, re-applies the owner's edits (`reassert_edits`). Only the
fields the owner actually touched: a bet whose account was corrected still
takes a new settlement from the next statement.

**NOTHING IS LOST.** A deletion archives the bet and every child row — its
selections, the statement that confirmed it, the guardrails overridden on it,
the blackbook entry it was placed on — so it can be restored exactly. An edit
records what each field was. Both are facts about the ledger worth having later,
for the same reason an overridden guardrail is logged rather than the bet
blocked.

The derived columns — `pnl`, `hit`, `status` — are never edited directly. The
owner says what was staked and what came back; the arithmetic follows, so the
two cannot be left disagreeing.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from hkrd.store.connect import Connection, transaction

__all__ = ["EDITABLE", "STATUSES", "BetEditError", "edit_bet", "delete_bet",
           "restore_bet", "deleted_bets", "edit_history", "deleted_identities",
           "reassert_edits", "CHILD_TABLES"]

# The columns an owner may set. Everything else on `bets` is either identity
# (`bet_id`, `bookie_ref`, `source`, `placed_at`) or derived from these.
EDITABLE: tuple[str, ...] = (
    "account", "race_date", "venue", "race_no", "bet_type", "all_up_formula",
    "stake", "returned", "status", "notes",
)

# What a bet can be. `void` is a bookie refund — a scratching, an abandoned race
# — and is a real event with the stake returned. It is not "deleted", which
# means the record should never have existed, and the two must not share a
# word: a voided bet belongs in the analysis and a deleted one does not.
STATUSES: tuple[str, ...] = ("open", "settled", "void")

# Every table that hangs a row off a bet, in the order they must be emptied
# before the bet itself can go (foreign keys are on).
CHILD_TABLES: tuple[str, ...] = (
    "bet_selections", "bet_statement_rows", "bet_overrides",
    "bet_blackbook_links",
)

_MONEY = {"stake", "returned", "status"}


class BetEditError(ValueError):
    """A correction that cannot be applied. Names what is wrong with it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(conn: Connection, bet_id: str) -> dict[str, Any] | None:
    found = conn.execute("SELECT * FROM bets WHERE bet_id = ?",
                         (bet_id,)).fetchone()
    return dict(found) if found else None


def _selections(conn: Connection, bet_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT race_no, horse_no, leg_no, is_banker FROM bet_selections "
        "WHERE bet_id = ? ORDER BY leg_no, race_no, horse_no", (bet_id,))]


def _settle(stake: float, returned: float | None, status: str | None
            ) -> dict[str, Any]:
    """The columns that follow from what was staked and what came back.

    Void is a refund: the stake comes back and the bet neither won nor lost, so
    `hit` is unknown rather than a loss. Anything with a return is settled; no
    return is open, which is different from a return of zero — a loser.
    """
    if status == "void":
        back = stake if returned is None else returned
        return {"returned": back, "pnl": round(back - stake, 2), "hit": None,
                "status": "void"}
    if returned is None:
        return {"returned": None, "pnl": None, "hit": None, "status": "open"}
    return {"returned": returned, "pnl": round(returned - stake, 2),
            "hit": 1 if returned > 0 else 0, "status": "settled"}


def _clean(field: str, value: Any, conn: Connection, known_accounts: set[str]
           ) -> Any:
    """One incoming value, checked and in the type the column holds."""
    if field in ("stake", "returned"):
        if value in (None, ""):
            if field == "stake":
                raise BetEditError("a bet must have a stake")
            return None
        try:
            amount = round(float(value), 2)
        except (TypeError, ValueError):
            raise BetEditError(f"{field} must be a number, not {value!r}") from None
        if amount < 0 or (field == "stake" and amount == 0):
            raise BetEditError(f"{field} cannot be {amount:g}")
        return amount
    if field == "race_no":
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise BetEditError(f"race must be a number, not {value!r}") from None
    if field == "race_date":
        text = str(value or "").strip()
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            raise BetEditError(f"race date must be YYYY-MM-DD, not {value!r}") from None
        return text
    if field == "account":
        text = str(value or "").strip().lower()
        if text not in known_accounts:
            raise BetEditError(
                f"unknown account {value!r}; known: {', '.join(sorted(known_accounts))}")
        return text
    if field == "status":
        text = str(value or "").strip().lower()
        if text not in STATUSES:
            raise BetEditError(f"status must be one of {', '.join(STATUSES)}")
        return text
    if field == "bet_type":
        text = str(value or "").strip().upper()
        if not text:
            raise BetEditError("a bet must have a type")
        return text
    text = None if value is None else str(value).strip()
    return text or None


def _check_selections(conn: Connection, race_date: str,
                      selections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The selections, normalised — and refused if they name a horse the card
    does not have.

    A selection is a join key into `runners`. One that matches nothing is a bet
    the Blackbook can never see and the ledger can never name, so it is refused
    and named, unless the race has no card stored at all — an old meeting
    outside the archive, where there is nothing to check against and refusing
    would make the bet uneditable.
    """
    if not selections:
        raise BetEditError("a bet must back at least one horse")
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    bankers: dict[int, int] = {}
    for s in selections:
        try:
            race_no = int(s["race_no"])
            horse_no = int(s["horse_no"])
            leg_no = int(s.get("leg_no") or 0)
        except (KeyError, TypeError, ValueError):
            raise BetEditError(f"unreadable selection {s!r}") from None
        key = (leg_no, race_no, horse_no)
        if key in seen:
            continue
        seen.add(key)
        banker = bool(s.get("is_banker"))
        if banker:
            bankers[leg_no] = bankers.get(leg_no, 0) + 1
        out.append({"race_no": race_no, "horse_no": horse_no,
                    "leg_no": leg_no, "is_banker": 1 if banker else 0})

    for leg, n in bankers.items():
        if n > 1:
            raise BetEditError(
                f"{'leg ' + str(leg) if leg else 'the bet'} has {n} bankers; "
                f"a banker is the one horse every combination runs through")

    for race_no in sorted({s["race_no"] for s in out}):
        field = {r["horse_no"] for r in conn.execute(
            "SELECT horse_no FROM runners WHERE race_date = ? AND race_no = ?",
            (race_date, race_no))}
        if not field:
            continue
        missing = sorted(s["horse_no"] for s in out
                         if s["race_no"] == race_no and s["horse_no"] not in field)
        if missing:
            raise BetEditError(
                f"race {race_no} on {race_date} has no runner "
                f"{', '.join(str(m) for m in missing)}")
    return out


def _record(conn: Connection, bet_id: str, when: str, field: str,
            old: Any, new: Any) -> None:
    conn.execute(
        "INSERT INTO bet_edits (bet_id, edited_at, field, old_value, new_value) "
        "VALUES (?, ?, ?, ?, ?)",
        (bet_id, when, field, json.dumps(old), json.dumps(new)))


def edit_bet(conn: Connection, bet_id: str, *,
             changes: dict[str, Any] | None = None,
             selections: Sequence[dict[str, Any]] | None = None,
             accounts: Iterable[str] = ("brett", "kelvin")) -> dict[str, Any]:
    """Apply a correction. Returns the bet as it now stands and what changed.

    Only fields that actually differ are written and recorded, so saving a form
    nobody changed is not an edit — and does not start protecting a field from
    the next import that the owner never meant to take ownership of.
    """
    changes = dict(changes or {})
    unknown = sorted(set(changes) - set(EDITABLE))
    if unknown:
        raise BetEditError(f"not an editable field: {', '.join(unknown)}")

    with transaction(conn):
        before = _row(conn, bet_id)
        if before is None:
            raise BetEditError(f"no bet {bet_id!r} — it may have been deleted")
        known = {a.lower() for a in accounts}
        cleaned = {k: _clean(k, v, conn, known) for k, v in changes.items()}
        diff = {k: v for k, v in cleaned.items() if before.get(k) != v}

        when = _now()
        after = {**before, **diff}
        if _MONEY & set(diff):
            derived = _settle(after["stake"], after.get("returned"),
                              after.get("status"))
            # The status the owner chose wins over the one the arithmetic would
            # pick only for `void`; otherwise a return settles a bet and no
            # return opens it, and letting the two disagree is how a ledger ends
            # up with an open bet that has a profit.
            after.update(derived)
            if derived.get("status") != before.get("status"):
                diff["status"] = derived["status"]
            if derived.get("returned") != before.get("returned"):
                diff["returned"] = derived["returned"]
            if after["status"] != "open" and before.get("status") == "open":
                after["settled_at"] = when
            after["settle_method"] = "manual"

        for field, value in diff.items():
            _record(conn, bet_id, when, field, before.get(field), value)
        if diff:
            cols = [c for c in ("account", "race_date", "venue", "race_no",
                                "bet_type", "all_up_formula", "stake",
                                "returned", "pnl", "hit", "status",
                                "settle_method", "settled_at", "notes")
                    if after.get(c) != before.get(c)]
            if cols:
                conn.execute(
                    f"UPDATE bets SET {', '.join(f'{c} = ?' for c in cols)} "
                    f"WHERE bet_id = ?",
                    [after[c] for c in cols] + [bet_id])

        changed_selections = False
        if selections is not None:
            new = _check_selections(conn, after["race_date"], selections)
            old = _selections(conn, bet_id)
            if _canon(old) != _canon(new):
                _replace_selections(conn, bet_id, new)
                _record(conn, bet_id, when, "selections", _canon(old), _canon(new))
                changed_selections = True

        return {"bet_id": bet_id, "changed": sorted(diff)
                + (["selections"] if changed_selections else []),
                "bet": _row(conn, bet_id),
                "selections": _selections(conn, bet_id)}


def _canon(rows: Sequence[dict[str, Any]]) -> list[list[int]]:
    """Selections as a sorted list of plain lists, so two sets compare equal
    however they were ordered and survive a JSON round trip unchanged."""
    return sorted([int(r["leg_no"]), int(r["race_no"]), int(r["horse_no"]),
                   int(r["is_banker"])] for r in rows)


def _replace_selections(conn: Connection, bet_id: str,
                        rows: Sequence[Sequence[int] | dict[str, Any]]) -> None:
    conn.execute("DELETE FROM bet_selections WHERE bet_id = ?", (bet_id,))
    for r in rows:
        if isinstance(r, dict):
            leg, race, horse, banker = (r["leg_no"], r["race_no"],
                                        r["horse_no"], r["is_banker"])
        else:
            leg, race, horse, banker = r
        conn.execute(
            "INSERT INTO bet_selections (bet_id, race_no, horse_no, leg_no, "
            "is_banker) VALUES (?, ?, ?, ?, ?)",
            (bet_id, int(race), int(horse), int(leg), 1 if banker else 0))


def delete_bet(conn: Connection, bet_id: str, *, reason: str | None = None
               ) -> dict[str, Any]:
    """Take a bet off the ledger, keeping everything needed to put it back."""
    with transaction(conn):
        bet = _row(conn, bet_id)
        if bet is None:
            raise BetEditError(f"no bet {bet_id!r} — it may already be deleted")
        children = {t: [dict(r) for r in conn.execute(
                        f"SELECT * FROM {t} WHERE bet_id = ?", (bet_id,))]
                    for t in CHILD_TABLES}
        conn.execute(
            "INSERT INTO bet_deletions (bet_id, deleted_at, reason, source, "
            "bookie_ref, race_date, bet_type, snapshot) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT (bet_id) DO UPDATE SET deleted_at = excluded.deleted_at, "
            "reason = excluded.reason, snapshot = excluded.snapshot",
            (bet_id, _now(), (reason or "").strip() or None, bet.get("source"),
             bet.get("bookie_ref"), bet.get("race_date"), bet.get("bet_type"),
             json.dumps({"bet": bet, "children": children})))
        for table in CHILD_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE bet_id = ?", (bet_id,))
        conn.execute("DELETE FROM bets WHERE bet_id = ?", (bet_id,))
    return {"bet_id": bet_id, "deleted": True, "bet": bet,
            "children": {t: len(v) for t, v in children.items()}}


def restore_bet(conn: Connection, bet_id: str) -> dict[str, Any]:
    """Put a deleted bet back exactly as it was, children and all."""
    with transaction(conn):
        found = conn.execute(
            "SELECT snapshot FROM bet_deletions WHERE bet_id = ?",
            (bet_id,)).fetchone()
        if found is None:
            raise BetEditError(f"no deleted bet {bet_id!r} to restore")
        if _row(conn, bet_id) is not None:
            raise BetEditError(
                f"a bet {bet_id!r} is already on the ledger; restoring would "
                f"overwrite it")
        snap = json.loads(found["snapshot"])
        bet = snap["bet"]
        cols = list(bet)
        conn.execute(
            f"INSERT INTO bets ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})", [bet[c] for c in cols])
        for table in CHILD_TABLES:
            for row in snap["children"].get(table, []):
                keys = list(row)
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(keys)}) "
                    f"VALUES ({', '.join('?' * len(keys))})",
                    [row[k] for k in keys])
        conn.execute("DELETE FROM bet_deletions WHERE bet_id = ?", (bet_id,))
    return {"bet_id": bet_id, "restored": True, "bet": bet}


def deleted_bets(conn: Connection, *, account: str | None = None,
                 limit: int = 200) -> list[dict[str, Any]]:
    """What has been taken off the ledger, newest first, with enough of each
    bet to recognise it."""
    out = []
    for r in conn.execute(
            "SELECT bet_id, deleted_at, reason, snapshot FROM bet_deletions "
            "ORDER BY deleted_at DESC LIMIT ?", (limit,)):
        bet = json.loads(r["snapshot"])["bet"]
        if account and (bet.get("account") or "").lower() != account.lower():
            continue
        out.append({
            "bet_id": r["bet_id"], "deleted_at": r["deleted_at"],
            "reason": r["reason"],
            **{k: bet.get(k) for k in ("account", "race_date", "venue",
                                        "race_no", "bet_type", "stake",
                                        "returned", "pnl", "source",
                                        "bookie_ref")},
        })
    return out


def edit_history(conn: Connection, bet_id: str) -> list[dict[str, Any]]:
    return [{"edited_at": r["edited_at"], "field": r["field"],
             "old": json.loads(r["old_value"]) if r["old_value"] else None,
             "new": json.loads(r["new_value"]) if r["new_value"] else None}
            for r in conn.execute(
                "SELECT edited_at, field, old_value, new_value FROM bet_edits "
                "WHERE bet_id = ? ORDER BY edit_id", (bet_id,))]


# ── what the importers ask ───────────────────────────────────────────────────

def deleted_identities(conn: Connection
                       ) -> tuple[set[str], set[tuple[str, str, str]]]:
    """Every bet an importer must not bring back, two ways.

    By `bet_id`, which catches the bet sheet and the legacy log — both derive
    their ids deterministically, so a re-read produces the same one. And by
    (bookie_ref, race_date, bet_type), which catches a statement: it matches a
    bet by reference and, finding none on the ledger, would otherwise mint a
    fresh id and write the deleted bet straight back under it.
    """
    ids: set[str] = set()
    refs: set[tuple[str, str, str]] = set()
    for r in conn.execute(
            "SELECT bet_id, bookie_ref, race_date, bet_type FROM bet_deletions"):
        ids.add(r["bet_id"])
        if r["bookie_ref"]:
            refs.add((r["bookie_ref"], r["race_date"], r["bet_type"]))
    return ids, refs


def reassert_edits(conn: Connection, bet_ids: Iterable[str]) -> int:
    """Re-apply the owner's latest correction of every field they touched.

    Called by an importer INSIDE its own transaction, after it has written, so
    the file's value lands and is immediately replaced by the owner's where the
    owner has one. Fields the owner never touched keep whatever the import
    wrote. Returns how many bets had something re-applied.
    """
    wanted = sorted({b for b in bet_ids if b})
    if not wanted:
        return 0
    marks = ",".join("?" * len(wanted))
    latest: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
            f"SELECT bet_id, field, new_value FROM bet_edits "
            f"WHERE bet_id IN ({marks}) ORDER BY edit_id", wanted):
        latest.setdefault(r["bet_id"], {})[r["field"]] = (
            json.loads(r["new_value"]) if r["new_value"] else None)

    applied = 0
    for bet_id, fields in latest.items():
        bet = _row(conn, bet_id)
        if bet is None:
            continue
        scalars = {k: v for k, v in fields.items() if k in EDITABLE}
        if scalars:
            after = {**bet, **scalars}
            if _MONEY & set(scalars):
                after.update(_settle(after["stake"], after.get("returned"),
                                     after.get("status")))
                after["settle_method"] = "manual"
            cols = [c for c in (*EDITABLE, "pnl", "hit", "settle_method")
                    if after.get(c) != bet.get(c)]
            if cols:
                conn.execute(
                    f"UPDATE bets SET {', '.join(f'{c} = ?' for c in cols)} "
                    f"WHERE bet_id = ?", [after[c] for c in cols] + [bet_id])
        if "selections" in fields and fields["selections"] is not None:
            if _canon(_selections(conn, bet_id)) != fields["selections"]:
                _replace_selections(conn, bet_id, fields["selections"])
        applied += 1
    return applied
