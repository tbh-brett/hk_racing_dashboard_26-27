"""Import the bet sheet — the spreadsheet the betting is planned in.

    python -m hkrd.jobs.import_betsheet --src "Bets with Kelvin 26_27.csv"
    python -m hkrd.jobs.import_betsheet --src sheets/ --account kelvin

The statement import is the record of what the bookie settled. This is the
record of what was INTENDED, kept in a spreadsheet alongside the betting, and
it is the only place an all-up exists before the meeting. Both write into one
ledger, and a bet that appears in both is one bet: the statement carries the
bookie's reference and settles it, this one carries the sheet's own bet number
and does not pretend to.

TWO THINGS THIS DOES THAT THE PARSER CANNOT.

Horse names become numbers. The sheet names horses because that is what a
person writes down; `bet_selections` stores numbers because that is what joins
to a card. A name with no runner row is reported, never guessed at — a fuzzy
match on a horse name is how a bet ends up recorded against the wrong horse,
and nothing downstream would ever show it.

The arithmetic is checked. `query/tickets` computes what the ticket costs from
its own structure, and the sheet states a total. On the file this was written
against the two agree to the cent on every row, including the 3x4 whose two
QQP legs and one WP leg make 20 lines rather than the 4 the formula code names.
Where they disagree the sheet's figure is what is stored — it is what was
actually staked — and the disagreement is reported, because it means one of the
two has the ticket wrong.
"""
from __future__ import annotations

# Whose book an import belongs to when nobody says — the same default the
# statement import uses, and one of the two accounts `query/prebet.ACCOUNTS`
# knows. An unlabelled import must never land in a third account no page shows.
DEFAULT_ACCOUNT = "brett"

import argparse
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hkrd.ingest import betsheet
from hkrd.query.tickets import (
    SINGLE_RACE_TYPES, allup_formula, allup_lines, combination_count,
)
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["run", "run_text", "SheetImportReport"]


@dataclass
class SheetImportReport:
    files: int = 0
    tickets: int = 0
    new_tickets: int = 0
    selections: int = 0
    unmatched: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  files read         {self.files:>6}",
                 f"  tickets            {self.tickets:>6}   "
                 f"({self.new_tickets} new)",
                 f"  selections         {self.selections:>6}"]
        for label, items in (("HORSES WITH NO RUNNER ROW", self.unmatched),
                             ("STAKE DISAGREES WITH THE STRUCTURE",
                              self.disagreements),
                             ("ROWS SKIPPED", self.skipped),
                             ("ERRORS", self.errors)):
            if items:
                lines.append(f"  {label:<34}{len(items):>6}")
                lines += [f"    {i}" for i in items[:10]]
        return "\n".join(lines)


def _bet_id(ticket: dict) -> str:
    """Stable across re-imports of the same sheet.

    The sheet's bet number restarts each meeting, so the date is part of it.
    Re-importing an edited sheet therefore UPDATES the ticket rather than
    writing a second copy of it — which is what a spreadsheet that gets
    corrected after the fact needs.
    """
    raw = f"sheet:{ticket['race_date']}:{ticket['bet_no']}"
    return "s" + hashlib.sha1(raw.encode()).hexdigest()[:9]


def _card(conn, date: str) -> dict[tuple[int, str], int]:
    """(race, HORSE NAME) -> horse number, for one meeting."""
    return {(r["race_no"], r["horse_name"]): r["horse_no"] for r in conn.execute(
        "SELECT race_no, horse_name, horse_no FROM runners WHERE race_date = ?",
        (date,))}


def _kind(raw: str) -> str:
    """The sheet's word for a pool, in the ledger's vocabulary."""
    text = str(raw or "").strip().upper().replace(" ", "")
    if text in ("ALLUP", "ALL-UP", "ALL_UP", "CHAIN"):
        return "ALLUP"
    if text in ("W", "WIN"):
        return "WIN"
    if text in ("P", "PLACE"):
        return "PLACE"
    return text


def _resolve(ticket: dict, card: dict, report: SheetImportReport
             ) -> list[dict[str, Any]] | None:
    """Legs with numbered horses, or None if any horse could not be found."""
    out: list[dict[str, Any]] = []
    ok = True
    for leg in ticket["legs"]:
        race_no = leg["race_no"]
        picks: list[int] = []
        for name in leg["selections"]:
            found = card.get((race_no, name))
            if found is None:
                report.unmatched.append(
                    f"{ticket['source']} line {leg['line']}: {name} is not a "
                    f"declared runner in {ticket['race_date']} R{race_no}")
                ok = False
                continue
            picks.append(found)
        banker = None
        if leg["banker"]:
            banker = card.get((race_no, leg["banker"]))
            if banker is None:
                report.unmatched.append(
                    f"{ticket['source']} line {leg['line']}: banker "
                    f"{leg['banker']} is not a declared runner in "
                    f"{ticket['race_date']} R{race_no}")
                ok = False
        kind = _kind(leg["bet_type"])
        if kind not in SINGLE_RACE_TYPES:
            report.skipped.append(
                f"{ticket['source']} line {leg['line']}: {leg['bet_type']!r} "
                f"is not a pool this ledger knows")
            ok = False
            continue
        out.append({"race_no": race_no, "bet_type": kind,
                    "banker": banker, "selections": picks})
    return out if ok else None


def _outlay(ticket: dict, legs: list[dict]) -> tuple[float | None, str | None]:
    """What the structure says this costs, and its formula code.

    The all-up multiplies twice: the formula names which multiples are bought,
    and each of those costs the product of its legs' own combination counts.
    See `query/tickets`.
    """
    unit = ticket.get("unit_stake")
    per_leg = [combination_count(l["bet_type"], len(l["selections"]),
                                 has_banker=l["banker"] is not None)
               for l in legs]
    if _kind(ticket["bet_type"]) != "ALLUP":
        return ((per_leg[0] * unit if unit else None), None) if per_leg \
            else (None, None)
    found = allup_formula(len(legs), ticket.get("structure"))
    if found is None:
        return None, None
    lines = allup_lines(found["sizes"], per_leg)
    return (lines * unit if unit else None), found["code"]


def _stored_type(ticket: dict, legs: list[dict], formula: str | None) -> str:
    """The type as the ledger records it, matching what manual entry writes."""
    kind = _kind(ticket["bet_type"])
    if kind == "ALLUP":
        return f"ALLUP_{formula or ticket.get('structure') or 'X'}"
    return f"{kind}_BANKER" if legs and legs[0]["banker"] is not None else kind


def _selection_rows(bet_id: str, legs: list[dict], *, all_up: bool
                    ) -> list[tuple]:
    rows = []
    for i, leg in enumerate(legs, start=1):
        leg_no = i if all_up else 0
        if leg["banker"] is not None:
            rows.append((bet_id, leg["race_no"], leg["banker"], leg_no, 1))
        for horse in leg["selections"]:
            if horse != leg["banker"]:
                rows.append((bet_id, leg["race_no"], horse, leg_no, 0))
    return rows


def run(src: Path, *, db: Path | None = None,
        account: str = DEFAULT_ACCOUNT) -> SheetImportReport:
    files = ([(src.name, src.read_text(encoding="utf-8-sig", errors="replace"))]
             if not src.is_dir() else
             [(p.name, p.read_text(encoding="utf-8-sig", errors="replace"))
              for p in sorted(src.iterdir()) if p.suffix.lower() == ".csv"])
    return _run(files, db=db, account=account)


def run_text(text: str, *, name: str = "upload", db: Path | None = None,
             account: str = DEFAULT_ACCOUNT) -> SheetImportReport:
    """Import a sheet handed over as TEXT rather than as a path.

    The dashboard runs on a machine in Singapore and the sheet is exported on
    whichever device the owner is holding, so "give me a path" is a route only
    the server itself can use.
    """
    return _run([(name, text)], db=db, account=account)


def _run(files: list[tuple[str, str]], *, db: Path | None = None,
         account: str = DEFAULT_ACCOUNT) -> SheetImportReport:
    report = SheetImportReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        cards: dict[str, dict] = {}
        bets: list[tuple] = []
        sels: list[tuple] = []

        for name, text in files:
            try:
                parsed = betsheet.parse_text(text, source=name)
            except betsheet.SheetError as exc:
                report.errors.append(str(exc))
                continue
            report.files += 1
            report.skipped += parsed.skipped

            for ticket in parsed.tickets:
                date = ticket["race_date"]
                card = cards.setdefault(date, _card(conn, date))
                if not card:
                    report.errors.append(
                        f"{name}: no card stored for {date} — scrape the "
                        f"meeting before importing bets on it")
                    continue
                legs = _resolve(ticket, card, report)
                if legs is None:
                    continue

                all_up = _kind(ticket["bet_type"]) == "ALLUP"
                computed, formula = _outlay(ticket, legs)
                stake = ticket.get("total_stake")
                if stake is None:
                    stake = computed
                if stake is None:
                    report.skipped.append(
                        f"{name}: bet {ticket['bet_no']} on {date} has no stake")
                    continue
                if computed is not None and abs(computed - stake) > 0.01:
                    report.disagreements.append(
                        f"{name}: bet {ticket['bet_no']} on {date} — the sheet "
                        f"says ${stake:,.0f}, the structure "
                        f"({formula or _stored_type(ticket, legs, None)}, legs "
                        f"of {', '.join(str(combination_count(l['bet_type'], len(l['selections']), has_banker=l['banker'] is not None)) for l in legs)}) "
                        f"comes to ${computed:,.0f}. Stored at the sheet's "
                        f"figure — it is what was staked.")

                bet_id = _bet_id(ticket)
                bets.append((
                    bet_id, account.lower(), date,
                    _venue(conn, date), None if all_up else legs[0]["race_no"],
                    _stored_type(ticket, legs, formula), formula,
                    float(stake),
                    f"Imported from bet sheet {name} (bet {ticket['bet_no']})."))
                sels += _selection_rows(bet_id, legs, all_up=all_up)

        before = conn.execute("SELECT count(*) FROM bets").fetchone()[0]
        with transaction(conn):
            conn.executemany(
                "INSERT INTO bets (bet_id, account, race_date, venue, race_no, "
                "bet_type, all_up_formula, stake, notes, returned, pnl, status, "
                "hit, settle_method, placed_at, settled_at, source) "
                "VALUES (?,?,?,?,?,?,?,?,?, NULL, NULL, 'open', NULL, NULL, "
                "  NULL, NULL, 'bet_sheet') "
                # A re-import of a corrected sheet updates the ticket. It never
                # touches settlement: the sheet has no return column, and a
                # bet the statement has already settled must not be reopened
                # by the plan it was struck from.
                "ON CONFLICT (bet_id) DO UPDATE SET "
                "account = excluded.account, race_no = excluded.race_no, "
                "bet_type = excluded.bet_type, "
                "all_up_formula = excluded.all_up_formula, "
                "stake = excluded.stake, notes = excluded.notes", bets)
            conn.executemany(
                "INSERT INTO bet_selections (bet_id, race_no, horse_no, "
                "leg_no, is_banker) VALUES (?,?,?,?,?) "
                "ON CONFLICT (bet_id, race_no, horse_no, leg_no) DO UPDATE SET "
                "is_banker = excluded.is_banker", sels)
        after = conn.execute("SELECT count(*) FROM bets").fetchone()[0]
        report.tickets, report.new_tickets = len(bets), after - before
        report.selections = len(sels)
    finally:
        conn.close()
    return report


def _venue(conn, date: str) -> str | None:
    row = conn.execute("SELECT venue FROM races WHERE race_date = ? LIMIT 1",
                       (date,)).fetchone()
    return row["venue"] if row else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, required=True,
                    help="a bet sheet .csv, or a directory of them")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--account", default=DEFAULT_ACCOUNT)
    args = ap.parse_args(argv)

    report = run(args.src, db=args.db, account=args.account)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
