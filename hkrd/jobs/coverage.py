"""What the database actually holds, month by month, and where it stops.

    python -m hkrd.jobs.coverage

The question this answers is "is the dashboard missing data, or is it broken?"
— and those look identical on screen. A page with no trials for July is the
same empty table whether July had no trials, the archive never had them, or a
scrape failed. This reads the tables and says which.

It makes no network requests. It is a description of what is here, not a
comparison against HKJC — telling you a month is thin is enough to know to run
`jobs.nightly` at it.
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.store.connect import db_path, get_conn, init_db

__all__ = ["Coverage", "survey"]

# table, the column holding its date, and what one row means in English.
SOURCES = [
    ("races",      "race_date",  "meetings"),
    ("runners",    "race_date",  "runners"),
    ("dividends",  "race_date",  "dividends"),
    ("runner_comments", "race_date", "comments on running"),
    ("trials",     "trial_date", "trial runs"),
    ("bets",       "placed_at",  "bets"),
]


@dataclass
class Coverage:
    months: list[str] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    latest: dict[str, str | None] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    def render(self, today: dt.date | None = None) -> str:
        today = today or dt.date.today()
        names = [t for t, _, _ in SOURCES]
        width = max(len(n) for n in names) + 2

        # Column width from the widest NAME, not a guess. `runner_comments` is
        # fifteen characters and ran into the column beside it at a fixed 12.
        col = max(len(n) for n in names) + 2

        head = "  " + "month".ljust(9) + "".join(f"{n:>{col}}" for n in names)
        lines = ["", head, "  " + "-" * (9 + col * len(names))]
        for m in self.months:
            row = "  " + m.ljust(9)
            for n in names:
                v = self.counts.get(n, {}).get(m, 0)
                row += f"{'-' if v == 0 else format(v, ','):>{col}}"
            lines.append(row)

        lines += ["", "  most recent row in each table"]
        for n in names:
            last = self.latest.get(n)
            if not last:
                lines.append(f"    {n.ljust(width)} nothing at all")
                continue
            try:
                age = (today - dt.date.fromisoformat(last[:10])).days
            except ValueError:
                age = None
            note = f"{age} days ago" if age is not None else ""
            lines.append(f"    {n.ljust(width)} {last[:10]}   {note}")

        gaps = self.gaps(today)
        if gaps:
            lines += ["", "  gaps worth filling"]
            lines += [f"    {g}" for g in gaps]
        else:
            lines += ["", "  no obvious gaps"]
        return "\n".join(lines)

    def gaps(self, today: dt.date | None = None) -> list[str]:
        """Months where one table has rows and a table that should accompany
        it does not. A meeting without dividends is a meeting whose results
        were never fetched, not a meeting where nothing paid."""
        today = today or dt.date.today()
        out = []
        for m in self.months:
            races = self.counts.get("races", {}).get(m, 0)
            if not races:
                continue
            for partner, why in (("dividends", "no dividends"),
                                 ("runner_comments", "no comments on running"),
                                 ("trials", "no trials")):
                if self.counts.get(partner, {}).get(m, 0) == 0:
                    out.append(f"{m}: {races} meetings but {why}")
        # And the plain "it stops here" case, which is the usual one.
        for name, _, label in SOURCES:
            last = self.latest.get(name)
            if not last:
                continue
            try:
                age = (today - dt.date.fromisoformat(last[:10])).days
            except ValueError:
                continue
            if age > 21:
                out.append(f"{name}: nothing since {last[:10]} — {age} days")
        return out


def survey(db: Path | None = None, *, months: int = 8) -> Coverage:
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        cov = Coverage()
        seen: set[str] = set()
        for table, column, label in SOURCES:
            cov.labels[table] = label
            rows = conn.execute(
                f"SELECT substr({column}, 1, 7) AS m, count(*) AS n "
                f"FROM {table} WHERE {column} IS NOT NULL "
                f"GROUP BY m").fetchall()
            cov.counts[table] = {r["m"]: r["n"] for r in rows if r["m"]}
            seen.update(cov.counts[table])
            last = conn.execute(
                f"SELECT max({column}) AS d FROM {table}").fetchone()
            cov.latest[table] = last["d"] if last else None
        cov.months = sorted(seen, reverse=True)[:months][::-1]
        return cov
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--months", type=int, default=8)
    a = ap.parse_args(argv)
    cov = survey(a.db, months=a.months)
    print(cov.render())
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
