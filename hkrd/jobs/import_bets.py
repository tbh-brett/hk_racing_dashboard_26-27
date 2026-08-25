"""Import the legacy bets log into the ledger.

    python -m hkrd.jobs.import_bets --src ../hk_race_dashboard/reports/user_bets_log.jsonl

1,078 settled bets, April to July 2026. They are the record of what was
actually backed, and without them the Blackbook can only show hits — design
brief 06 Part 1: "Without it you only ever see the hits, and the book becomes a
scrapbook rather than a tracked signal."

Selections are normalised into `bet_selections` on the way in, one row per
horse per leg. A JSON list of horse numbers cannot be joined to a blackbook
entry, and that join is the entire point of importing this.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.store.coerce import CoerceError, to_date, to_int
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["run"]


@dataclass
class BetsReport:
    bets: int = 0
    selections: int = 0
    legs: int = 0
    unmatched: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  bets               {self.bets:>6,}",
                 f"  selections         {self.selections:>6,}",
                 f"  all-up legs        {self.legs:>6,}"]
        if self.unmatched:
            # Named because it is a data question, not a rounding error: a
            # selection with no runner row is a bet on a horse the database has
            # never heard of.
            lines.append(f"  selections with no runner row {self.unmatched:>6,}")
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def _race_date(value: str | None) -> str | None:
    """'20260422' or '2026-04-22'. The legacy log uses the compact form."""
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return to_date(text)


# The log's own `_bookie_ref` field was stripped before it was written, so the
# statement reference survives only inside the note: "Imported from bookie
# statement (ref 2209)." Recovering it fills `bookie_ref`, which is what stops
# a later import of the same statement from writing the bet a second time.
_REF_IN_NOTE = re.compile(r"\(ref\s+(\w+)\)")


def _bookie_ref(row: dict) -> str | None:
    if row.get("_bookie_ref"):
        return str(row["_bookie_ref"])
    found = _REF_IN_NOTE.search(row.get("notes") or "")
    return found.group(1) if found else None


def run(src: Path, *, db: Path | None = None, account: str = "personal",
        source: str = "legacy_log") -> BetsReport:
    report = BetsReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        bets: list[tuple] = []
        sels: list[tuple] = []

        for line_no, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                date = _race_date(r.get("meeting_date"))
                if not r.get("bet_id") or not date:
                    report.errors.append(f"line {line_no}: no bet_id or date")
                    continue
                race_no = to_int(r.get("race_number"), field="race_number")
                legs = r.get("legs") or []

                bets.append((
                    r["bet_id"], _bookie_ref(r), account, date,
                    r.get("venue"),
                    # An all-up spans races, so it has no single race number.
                    # A Quartet multi-banker does not span races and keeps its.
                    None if (legs and isinstance(legs[0], dict)) else race_no,
                    r.get("bet_type") or "UNKNOWN", r.get("all_up_formula"),
                    float(r.get("stake_hkd") or 0.0),
                    r.get("return_hkd"), r.get("pnl_hkd"),
                    r.get("status") or "settled",
                    1 if r.get("hit") else 0 if r.get("hit") is not None else None,
                    r.get("settle_method"),
                    r.get("_bookie_placed_at") or r.get("created_at"),
                    r.get("settled_at"), source, r.get("notes"),
                ))

                banker = r.get("banker")
                # `legs` carries two different meanings under one name, and
                # conflating them puts horses in the wrong race:
                #
                #   ALLUP_*  list of dicts, each a RACE in the parlay
                #            [{race_number, banker, selections}, ...]
                #   QTT_MB   list of lists, the position groups of a Quartet
                #            multi-banker — all inside the bet's OWN race
                if legs and isinstance(legs[0], dict):
                    report.legs += len(legs)
                    for i, leg in enumerate(legs, start=1):
                        leg_race = to_int(leg.get("race_number"), field="race_number")
                        leg_banker = leg.get("banker")
                        picks = list(leg.get("selections") or [])
                        if leg_banker is not None and leg_banker not in picks:
                            picks.append(leg_banker)
                        for h in picks:
                            horse = to_int(h, field="horse_no")
                            sels.append((r["bet_id"], leg_race, horse, i,
                                         1 if leg_banker == horse else 0))
                else:
                    # QTT_MB's groups are positions, not races, so the union in
                    # `selections` is what was backed and the groups add nothing
                    # the blackbook join can use.
                    if legs:
                        report.legs += len(legs)
                    picks = list(r.get("selections") or [])
                    if banker is not None and banker not in picks:
                        picks.append(banker)
                    for h in picks:
                        horse = to_int(h, field="horse_no")
                        sels.append((r["bet_id"], race_no, horse, 0,
                                     1 if banker == horse else 0))
            except (CoerceError, ValueError, TypeError, json.JSONDecodeError) as exc:
                report.errors.append(f"line {line_no}: {exc}")

        with transaction(conn):
            conn.executemany(
                "INSERT INTO bets (bet_id, bookie_ref, account, race_date, venue, "
                "race_no, bet_type, all_up_formula, stake, returned, pnl, status, "
                "hit, settle_method, placed_at, settled_at, source, notes) "
                "VALUES (" + ",".join("?" * 18) + ") "
                "ON CONFLICT (bet_id) DO UPDATE SET "
                "returned = excluded.returned, pnl = excluded.pnl, "
                "status = excluded.status, hit = excluded.hit, "
                "settled_at = excluded.settled_at, notes = excluded.notes, "
                # The log is the record for its own rows. Leaving
                # settle_method behind let a later statement import read a
                # stale 'statement_apportioned' as permission to overwrite a
                # return the log had settled properly.
                "settle_method = excluded.settle_method, "
                "bookie_ref = excluded.bookie_ref", bets)
            conn.executemany(
                "INSERT INTO bet_selections (bet_id, race_no, horse_no, leg_no, "
                "is_banker) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (bet_id, race_no, horse_no, leg_no) DO UPDATE SET "
                "is_banker = excluded.is_banker", sels)

            report.unmatched = conn.execute("""
                SELECT count(*) FROM bet_selections s
                JOIN bets b ON b.bet_id = s.bet_id
                LEFT JOIN runners r ON r.race_date = b.race_date
                                   AND r.race_no = s.race_no
                                   AND r.horse_no = s.horse_no
                WHERE r.horse_no IS NULL""").fetchone()[0]
            # The STORED count, not the attempted one: a banker that also
            # appears in `selections` is one horse backed, not two, and the
            # primary key collapses it. Reporting the attempt would overstate.
            report.bets = conn.execute("SELECT count(*) FROM bets").fetchone()[0]
            report.selections = conn.execute(
                "SELECT count(*) FROM bet_selections").fetchone()[0]
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--account", default="personal")
    a = ap.parse_args(argv)
    if not a.src.is_file():
        print(f"not found: {a.src}")
        return 1
    report = run(a.src, db=a.db, account=a.account)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
