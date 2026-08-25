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

import argparse
import hashlib
from dataclasses import dataclass, field
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
        account: str = "personal") -> StatementImportReport:
    report = StatementImportReport()
    paths = (sorted(p for p in src.iterdir() if p.suffix.lower() == ".txt")
             if src.is_dir() else [src])

    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        known = _existing_ids(conn)
        bets: list[tuple] = []
        sels: list[tuple] = []

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

                bets.append((
                    bet_id, rec["bookie_ref"], account, date, rec.get("venue"),
                    None if is_all_up else rec["race_number"],
                    rec["bet_type"], rec.get("all_up_formula"),
                    stake, returned, returned - stake, "settled",
                    hit, method,
                    rec.get("placed_at"), None, "statement",
                    f"Imported from statement (ref {rec['bookie_ref']})."))

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
                "returned = excluded.returned, pnl = excluded.pnl, "
                "status = excluded.status, hit = excluded.hit, "
                "settle_method = excluded.settle_method "
                # A half apportioned off one block credit is a GUESS at how a
                # single credit split between two pools. Any return already in
                # the ledger was settled properly -- the legacy log has ref
                # 2209 as $90.00 win and $39.50 place, which sums to the same
                # $129.50 the apportionment can only halve -- so an apportioned
                # figure never overwrites one that exists.
                "WHERE excluded.settle_method != 'statement_apportioned' "
                "   OR bets.returned IS NULL "
                "   OR bets.settle_method = 'statement_apportioned'", bets)
            conn.executemany(
                "INSERT INTO bet_selections (bet_id, race_no, horse_no, leg_no, "
                "is_banker) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (bet_id, race_no, horse_no, leg_no) DO UPDATE SET "
                "is_banker = excluded.is_banker", sels)
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
    ap.add_argument("--account", default="personal")
    a = ap.parse_args(argv)
    if not a.src.exists():
        print(f"not found: {a.src}")
        return 1
    report = run(a.src, db=a.db, account=a.account)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
