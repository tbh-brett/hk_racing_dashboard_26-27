"""Import bets from an HKJC account statement.

    python -m hkrd.jobs.import_statement --src "acctstmt (22 April).txt"
    python -m hkrd.jobs.import_statement --src stmts/ --account joint

Reads with `ingest/statement.py` and writes through the same path the legacy
import used, so a bet from a statement and a bet from the historical log are
one shape in one table.

Settlement is NOT invented here. A statement carries the credit the bookie
actually paid, so `returned` comes from that; a bet the statement shows no
credit for is settled at zero, which is a loss, not an unknown. Where a
statement has not yet been issued the bet stays `open` with a null return —
distinguishable from a loser, which is the distinction the Blackbook's
backed-versus-missed depends on.
"""
from __future__ import annotations

# Whose book an import belongs to when nobody says. a statement is one account's, and Brett's is the older of the two —
# `query/prebet.ACCOUNTS` is the pair the interface knows, and
# "personal" is not one of them, so an unlabelled import used to
# land in a third account no page could show.
DEFAULT_ACCOUNT = "brett"

import argparse
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hkrd.ingest import statement
from hkrd.store.coerce import to_date
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["run"]


@dataclass
class StatementImportReport:
    files: int = 0
    bets: int = 0
    selections: int = 0
    new_bets: int = 0
    cash_movements: int = 0
    unparsed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  files read         {self.files:>6}",
                 f"  bets               {self.bets:>6}   ({self.new_bets} new)",
                 f"  selections         {self.selections:>6}",
                 f"  cash movements     {self.cash_movements:>6}"]
        if self.unparsed:
            lines.append(f"  UNPARSED BLOCKS    {len(self.unparsed):>6}")
            lines += [f"    {u}" for u in self.unparsed[:10]]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def _bet_id(rec: dict) -> str:
    """Stable across re-imports of the same statement.

    The bookie reference identifies a BLOCK, and a Quinella/Quinella-Place
    block becomes two bets, so the reference alone would collide. The bet type
    joins it.
    """
    raw = f"{rec['bookie_ref']}:{rec['meeting_date']}:{rec['bet_type']}"
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def _block_returns(conn) -> dict[str, float]:
    """What the ledger currently has back on each bookie reference, summed.

    A "Quinella - Quinella Place" line is two ledger rows against one statement
    block, so the comparison that decides whether the ledger already knows
    better has to be made at block level.
    """
    return {r["bookie_ref"]: r["ret"] or 0.0 for r in conn.execute(
        "SELECT bookie_ref, sum(returned) ret FROM bets "
        "WHERE bookie_ref IS NOT NULL GROUP BY bookie_ref")}


def _existing_ids(conn) -> dict[tuple[str, str, str], str]:
    """(ref, date, type) -> the bet_id already in the ledger.

    The legacy log was itself written from these statements and carries their
    references, under ids of its own. Without this lookup, importing the two
    April statements wrote 49 bets that were already there under different ids
    -- the 26 April meeting appeared twice, $2,596 staked counted as $5,192.
    The ledger row is UPDATED in place instead, so a re-read of a statement
    corrects a settlement rather than duplicating a bet.
    """
    return {(r["bookie_ref"], r["race_date"], r["bet_type"]): r["bet_id"]
            for r in conn.execute(
                "SELECT bet_id, bookie_ref, race_date, bet_type FROM bets "
                "WHERE bookie_ref IS NOT NULL")}


def _race_date(compact: str) -> str | None:
    return to_date(f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
                   if len(compact) == 8 and compact.isdigit() else compact)


def run(src: Path, *, db: Path | None = None,
        account: str = DEFAULT_ACCOUNT) -> StatementImportReport:
    report = StatementImportReport()
    paths = (sorted(p for p in src.iterdir() if p.suffix.lower() == ".txt")
             if src.is_dir() else [src])

    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        known = _existing_ids(conn)
        held = _block_returns(conn)
        bets: list[tuple] = []
        sels: list[tuple] = []
        seen: list[tuple] = []          # what each statement actually covered
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for path in paths:
            try:
                records, parsed = statement.parse(path)
            except statement.StatementError as exc:
                report.errors.append(f"{path.name}: {exc}")
                continue
            report.files += 1
            report.cash_movements += parsed.cash_movements
            report.unparsed += [f"{path.name}: {u}" for u in parsed.unparsed]

            for rec in records:
                date = _race_date(rec["meeting_date"])
                if not date:
                    report.errors.append(f"{path.name}: unreadable date "
                                         f"{rec['meeting_date']!r}")
                    continue
                is_new = (rec["bookie_ref"], date, rec["bet_type"]) not in known
                bet_id = known.get((rec["bookie_ref"], date, rec["bet_type"]),
                                   _bet_id(rec))
                legs = rec.get("legs") or []
                is_all_up = bool(legs and isinstance(legs[0], dict))
                credit = rec.get("total_credit")
                stake = float(rec["stake_hkd"])
                # A statement is issued after settlement, so a block with no
                # credit line is a loser at zero -- not an open bet.
                returned = float(credit or 0.0)
                # A Quinella/Quinella-Place block pays ONE credit across two
                # pools and the statement does not break it down. Half is
                # carried on each so the block's money stays exact, but the
                # half is an apportionment, not a settled figure -- so `hit`
                # is left unknown rather than claiming both pools came in.
                apportioned = bool(rec.get("credit_apportioned"))
                hit = None if apportioned else (1 if returned > 0 else 0)
                method = ("statement_apportioned" if apportioned
                          else "bookie_statement")

                # An apportioned half is a worse figure than a pool settled
                # from its own dividend, so it does not overwrite one -- but
                # only where the ledger's block actually adds up. The legacy
                # log has refs 2217 and 2218 at $0 returned while its own notes
                # quote credits of $80 and $40, so "already settled" cannot be
                # assumed; where the block disagrees with the statement, the
                # bookie's own record wins.
                block_credit = float(rec.get("block_credit") or 0.0)
                ledger_has = held.get(rec["bookie_ref"])
                agrees = (ledger_has is not None
                          and abs(ledger_has - block_credit) <= 0.01)
                accept = is_new or not apportioned or not agrees
                # `status` is always 'settled' -- a statement is issued after
                # the meeting either way. The money columns come through as
                # NULL when this import has nothing better to say, and the
                # write below coalesces them onto what is already there.
                settled = ((returned, returned - stake, "settled", hit, method)
                           if accept else (None, None, "settled", None, None))

                bets.append((
                    bet_id, rec["bookie_ref"], account, date, rec.get("venue"),
                    None if is_all_up else rec["race_number"],
                    rec["bet_type"], rec.get("all_up_formula"),
                    stake, *settled,
                    rec.get("placed_at"), None, "statement",
                    f"Imported from statement (ref {rec['bookie_ref']})."))

                seen.append((bet_id, rec["bookie_ref"], path.name,
                             float(rec.get("block_debit") or stake),
                             float(rec.get("block_credit") or 0.0), stamp))

                if is_all_up:
                    for i, leg in enumerate(legs, start=1):
                        picks = list(leg.get("selections") or [])
                        leg_banker = leg.get("banker")
                        if leg_banker is not None and leg_banker not in picks:
                            picks.append(leg_banker)
                        for horse in picks:
                            sels.append((bet_id, int(leg["race_number"]),
                                         int(horse), i,
                                         1 if leg_banker == horse else 0))
                else:
                    banker = rec.get("banker")
                    picks = list(rec["selections"])
                    if banker is not None and banker not in picks:
                        picks.append(banker)
                    for horse in picks:
                        sels.append((bet_id, rec["race_number"], int(horse), 0,
                                     1 if banker == horse else 0))

        before = conn.execute("SELECT count(*) FROM bets").fetchone()[0]
        with transaction(conn):
            conn.executemany(
                "INSERT INTO bets (bet_id, bookie_ref, account, race_date, venue, "
                "race_no, bet_type, all_up_formula, stake, returned, pnl, status, "
                "hit, settle_method, placed_at, settled_at, source, notes) "
                "VALUES (" + ",".join("?" * 18) + ") "
                "ON CONFLICT (bet_id) DO UPDATE SET "
                # The statement carries the bookie's own timestamp for the
                # wager, and the reference it was written under. The legacy log
                # had both fields stripped and fell back to when the row was
                # written -- 495 of its 1,078 bets are stamped days or weeks
                # after the race, which is a logging time, not a betting one.
                # These are unconditional: the statement is the better record.
                "placed_at = excluded.placed_at, "
                "bookie_ref = excluded.bookie_ref, "
                # Settlement is decided per row before the write (see
                # `accept` above) and arrives as NULL where this import has
                # nothing better to say than what is already in the ledger.
                # coalesce keeps that, so the timestamp beside it still lands.
                + ", ".join(
                    f"{col} = coalesce(excluded.{col}, bets.{col})"
                    for col in ("returned", "pnl", "status", "hit",
                                "settle_method")), bets)
            conn.executemany(
                "INSERT INTO bet_selections (bet_id, race_no, horse_no, leg_no, "
                "is_banker) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (bet_id, race_no, horse_no, leg_no) DO UPDATE SET "
                "is_banker = excluded.is_banker", sels)
            # Which bets a statement was actually read for. A reference
            # recovered out of the legacy log's notes is not the same thing as
            # a statement confirming the bet, and the reconciliation has to be
            # able to tell them apart.
            conn.executemany(
                "INSERT INTO bet_statement_rows (bet_id, bookie_ref, "
                "source_file, stake, returned, imported_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (bet_id, source_file) DO UPDATE SET "
                "stake = excluded.stake, returned = excluded.returned, "
                "imported_at = excluded.imported_at", seen)
        after = conn.execute("SELECT count(*) FROM bets").fetchone()[0]
        report.bets, report.new_bets = len(bets), after - before
        report.selections = len(sels)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, required=True,
                    help="a statement .txt, or a directory of them")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--account", default=DEFAULT_ACCOUNT,
                    help="which book these bets belong to")
    a = ap.parse_args(argv)
    if not a.src.exists():
        print(f"not found: {a.src}")
        return 1
    report = run(a.src, db=a.db, account=a.account)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
