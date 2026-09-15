"""What the database actually holds, month by month, and where it stops.

    python -m hkrd.jobs.coverage

The question this answers is "is the dashboard missing data, or is it broken?"
— and those look identical on screen. A page with no trials for July is the
same empty table whether July had no trials, the archive never had them, or a
scrape failed. This reads the tables and says which.

IT ALSO ANSWERS THE THIRD VERSION OF THAT QUESTION: is the data here and a
model generation behind the code? A derived table survives a deploy — the
volume is not rebuilt — so shipping a changed model leaves every existing row
written by the old one, and the page shows them without complaint. That is not
hypothetical: rebuilding one archive's `runner_sarr` from `sarr-1.0` to
`sarr-1.1` moved 99.8% of scores, 39.5% of ranks and the top-rated horse in
19.5% of races, and the only visible symptom was an owner saying the ratings
still looked wrong. `derive_version` is the cheap tell and this reads it.

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

__all__ = ["Coverage", "survey", "DERIVED", "current_versions"]

# table, the column holding its date, and what one row means in English.
SOURCES = [
    ("races",      "race_date",  "meetings"),
    ("runners",    "race_date",  "runners"),
    ("dividends",  "race_date",  "dividends"),
    ("runner_comments", "race_date", "comments on running"),
    ("trials",     "trial_date", "trial runs"),
    ("bets",       "placed_at",  "bets"),
]


# Derived table, the job that rebuilds it, and where the version it should
# carry is defined. The constant is read at run time rather than copied, so a
# model bumped in the code makes every existing row report itself as behind on
# the next run of this — which is the whole point of the column.
DERIVED = [
    ("runner_pace",       "hkrd.derive.pace",   "hkrd.jobs.derive_all"),
    ("runner_et",         "hkrd.derive.et",     "hkrd.jobs.rebuild_et"),
    ("runner_sarr",       "hkrd.model.sarr",    "hkrd.jobs.rebuild_sarr"),
    ("runner_projection", "hkrd.derive.settle", "hkrd.jobs.project_card --pending"),
]


def current_versions() -> dict[str, str]:
    """What the code writes today, per derived table."""
    from importlib import import_module
    return {table: getattr(import_module(module), "DERIVE_VERSION")
            for table, module, _ in DERIVED}


@dataclass
class Coverage:
    months: list[str] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    latest: dict[str, str | None] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    # table -> [(version, rows, last race_date)], every generation present.
    versions: dict[str, list[tuple[str, int, str | None]]] = \
        field(default_factory=dict)
    current: dict[str, str] = field(default_factory=dict)
    # Repeated verbatim into the rebuild commands below, so a report read off
    # the production machine does not print a line that would rebuild the
    # wrong database when it is pasted back.
    db_flag: str = ""

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

        lines += self._version_lines()

        gaps = self.gaps(today)
        if gaps:
            lines += ["", "  gaps worth filling"]
            lines += [f"    {g}" for g in gaps]
        else:
            lines += ["", "  no obvious gaps"]
        return "\n".join(lines)

    def _version_lines(self) -> list[str]:
        """Which model generation wrote each derived table.

        Every generation present is listed, not just the newest. A table
        rebuilt for two meetings under a changed model and left alone for the
        rest is the case the column exists to make visible, and a single
        "latest version" row would hide exactly that.
        """
        if not self.versions:
            return []
        names = [t for t, _, _ in DERIVED]
        width = max(len(n) for n in names) + 2
        lines = ["", "  model generation of each derived table"]
        for table, _, job in DERIVED:
            rows = self.versions.get(table, [])
            want = self.current.get(table, "?")
            if not rows:
                lines.append(f"    {table.ljust(width)} {'-':<14}"
                             f" {'0':>9} rows  {'-':<10}    "
                             f"nothing derived yet; code writes {want}")
                continue
            for version, n, last in rows:
                mark = "current" if version == want else f"BEHIND — code writes {want}"
                lines.append(f"    {table.ljust(width)} {version:<14}"
                             f" {n:>9,} rows  to {last or '-'}  {mark}")
        behind = {t: j for t, _, j in DERIVED
                  if any(v != self.current.get(t)
                         for v, _, _ in self.versions.get(t, []))}
        if behind:
            # Spelled out because the person who needs this line is the one who
            # would otherwise be told to compose it. `--db` is added by main()
            # so the command works where it is printed, on the machine.
            lines += ["", "  a generation behind the code. Rebuild with:"]
            for table, job in behind.items():
                lines.append(f"    python -m {job}{self.db_flag}   # {table}")
        return lines

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
        # A stale model is a gap in the same sense: the rows are there and the
        # figures they carry are not the ones the code would produce.
        for table, _, _ in DERIVED:
            want = self.current.get(table)
            for version, n, _ in self.versions.get(table, []):
                if want and version != want:
                    out.append(f"{table}: {n:,} rows still on {version}, "
                               f"code writes {want}")
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

        cov.current = current_versions()
        cov.db_flag = f" --db {db}" if db is not None else ""
        for table, _, _ in DERIVED:
            rows = conn.execute(
                f"SELECT derive_version AS v, count(*) AS n, "
                f"       max(race_date) AS d "
                # Biggest generation first, then by name, so two runs over
                # the same database print the same report.
                f"FROM {table} GROUP BY v ORDER BY n DESC, v").fetchall()
            cov.versions[table] = [(r["v"], r["n"], r["d"]) for r in rows]
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
