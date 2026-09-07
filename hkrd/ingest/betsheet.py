"""The bet sheet — the spreadsheet a ticket is written down in before it exists.

The HKJC statement in `ingest/statement.py` is the record of what was actually
struck, and it is authoritative about money. It is also only available AFTER the
meeting, and it is one account's. The sheet is the other half: it is kept
alongside the betting, it covers both accounts, and it is where a chain is
worked out before the ticket is placed.

    Bet no,Date,Race,Bet Type,All-Up Structure,All-Up bet type,Banker,
    Leg 1,...,Leg 5,Stakes per bet,Total stakes

ONE BET IS SEVERAL ROWS. An all-up carries one row per race, sharing a bet
number, and the stake sits on the last of them — so rows are grouped by
(date, bet number) and a group is one ticket. A single-race bet is one row and
the same grouping handles it without a second code path.

TWO USES FOR THE SAME COLUMNS. On a single-race row, `Banker` is the banker and
`Leg 1..5` are the horses it combines with. On an all-up row the same `Leg`
columns hold that leg's own selections. Nothing here guesses which: the row's
`Bet Type` says.

WHAT IS NOT PARSED HERE. Horse numbers, because the sheet has names and the
numbers are a fact about the card — resolving them needs the database and
belongs in the job. And settlement, because the sheet has no return column: a
bet imported from it is open until a statement or a dividend settles it, which
is the same state a bet typed into the dashboard starts in.

The sheet's own `Total stakes` is kept and CHECKED rather than trusted. The
combination arithmetic in `query/tickets` reproduces it exactly on every row of
the file this was written against — a 3x4 over two QQP legs and a WP leg is 20
lines at $200, which is the $4,000 the sheet says — so a disagreement means one
of the two is wrong about the ticket, and that is worth being told about rather
than importing quietly.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["parse_text", "SheetError", "SheetParse", "COLUMNS"]


class SheetError(ValueError):
    """The file is not a bet sheet. Names what was missing."""


# Matched case-insensitively and ignoring spaces, because a spreadsheet
# exported twice is not guaranteed to capitalise its headers the same way.
COLUMNS = {
    "bet no": "bet_no", "date": "date", "race": "race",
    "bet type": "bet_type", "all-up structure": "structure",
    "all-up bet type": "leg_type", "banker": "banker",
    "stakes per bet": "unit_stake", "total stakes": "total_stake",
}

_LEG = re.compile(r"^leg\s*(\d+)$", re.I)

# The sheet is written in the HK/US mixed style the spreadsheet exports:
# 9/6/2026 is the sixth of September. Unambiguous because the day is always
# second here, and a meeting date that parsed as June the ninth would file a
# whole card under a date with no runners — which is loud rather than silent.
_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


@dataclass
class SheetParse:
    """What one file yielded, and what it could not."""
    tickets: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def _date(value: str) -> str | None:
    found = _DATE.match(str(value or "").strip())
    if found:
        month, day, year = found.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    text = str(value or "").strip()
    return text if re.match(r"^\d{4}-\d{2}-\d{2}$", text) else None


def _money(value: Any) -> float | None:
    text = re.sub(r"[$,\s]", "", str(value or ""))
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _headers(row: list[str]) -> tuple[dict[int, str], dict[int, int]]:
    """Column index -> field name, and column index -> leg number."""
    fields: dict[int, str] = {}
    legs: dict[int, int] = {}
    for i, cell in enumerate(row):
        key = _norm(cell)
        if key in COLUMNS:
            fields[i] = COLUMNS[key]
        elif (found := _LEG.match(key)):
            legs[i] = int(found.group(1))
    missing = {"bet_no", "date", "race", "bet_type"} - set(fields.values())
    if missing or not legs:
        raise SheetError(
            "not a bet sheet: expected columns Bet no, Date, Race, Bet Type "
            "and at least one Leg column"
            + (f"; missing {', '.join(sorted(missing))}" if missing else ""))
    return fields, legs


def parse_text(text: str, *, source: str = "sheet") -> SheetParse:
    """Group a bet sheet's rows into tickets. Horses stay named."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise SheetError(f"{source}: the file is empty")

    header_at = next((i for i, r in enumerate(rows[:5])
                      if any(_norm(c) == "bet no" for c in r)), None)
    if header_at is None:
        raise SheetError(f"{source}: no header row with a 'Bet no' column")
    fields, legs = _headers(rows[header_at])

    out = SheetParse()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    for n, raw in enumerate(rows[header_at + 1:], start=header_at + 2):
        if not any(str(c).strip() for c in raw):
            continue
        cell = {fields[i]: raw[i].strip()
                for i in fields if i < len(raw)}
        date = _date(cell.get("date", ""))
        bet_no = cell.get("bet_no", "")
        if not date or not bet_no:
            out.skipped.append(f"{source} line {n}: no date or bet number")
            continue

        key = (date, bet_no)
        ticket = grouped.get(key)
        if ticket is None:
            ticket = grouped[key] = {
                "source": source, "bet_no": bet_no, "race_date": date,
                "bet_type": (cell.get("bet_type") or "").strip().upper(),
                "structure": (cell.get("structure") or "").strip() or None,
                "unit_stake": None, "total_stake": None, "legs": [],
            }
            order.append(key)

        # The stake sits on whichever row of the group carries it — the last
        # one, in the file this was written against. Taken from any row, so a
        # sheet that puts it on the first is read the same way.
        for money_key in ("unit_stake", "total_stake"):
            found = _money(cell.get(money_key))
            if found is not None:
                ticket[money_key] = found

        race = re.sub(r"\D", "", cell.get("race", ""))
        if not race:
            out.skipped.append(f"{source} line {n}: no race number")
            continue

        picks = [raw[i].strip().upper() for i in sorted(legs, key=legs.get)
                 if i < len(raw) and raw[i].strip()]
        banker = (cell.get("banker") or "").strip().upper() or None
        # An ALL-UP row names its own pool in a separate column; a single-race
        # row's pool IS its bet type. One or the other, never a default.
        leg_type = ((cell.get("leg_type") or "").strip().upper()
                    or ticket["bet_type"])
        if not picks and not banker:
            out.skipped.append(f"{source} line {n}: no horses named")
            continue
        ticket["legs"].append({
            "race_no": int(race), "bet_type": leg_type,
            "banker": banker, "selections": picks, "line": n,
        })

    out.tickets = [grouped[k] for k in order if grouped[k]["legs"]]
    return out
