"""Recover barrier draws that a racecard scrape missed.

    python -m hkrd.jobs.backfill_draw --cache ../hk_race_dashboard/cache --dry-run

WHY THIS EXISTS. 15 July 2026 — the last meeting of the 2025/26 season — carries
results and sectionals for all 107 runners and **zero gates**. The results scrape
succeeded and the racecard scrape did not. Re-running it now returns `race header
unreadable` for every race: HKJC has taken that season's racecards down, so the
gate is not recoverable from source at any price.

It IS recoverable from the legacy dashboard's `cache/form_guide_*.json`, written
while the card was still up. Those files carry the declared field — horse, number
and draw — for every meeting from 1 Apr 2026 on.

Without this, `derive/draw` is live everywhere except that meeting, where it
silently contributes zero. A term that does nothing on one card and something on
every other, with nothing on screen to say which, is the failure mode this
codebase exists to remove. `rebuild_sarr` counts the runners it scored without a
gate for the same reason.

WHAT IT WILL NOT DO. It writes ONE column, only where that column is already
NULL, and only when the horse named in the cache is the horse already stored
under that saddlecloth. That last guard is the point of the job rather than a
detail: saddlecloth numbers are reused when a horse is scratched and a reserve
takes its place, and the legacy cache was written before those late changes.
Writing a scratched horse's gate onto the reserve that replaced it would be
worse than the missing value, because a missing value is visible and a wrong one
is not.

This is NOT a general-purpose importer, and widening it to paper over a future
gap would be a mistake. If a future gap has no archive behind it, the honest move
is a named absence on the page.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["backfill", "BackfillReport", "normalise_name", "read_cache"]

_FILE = re.compile(r"form_guide_(\d{4}-\d{2}-\d{2})\.json$")
_WS = re.compile(r"\s+")


def normalise_name(name) -> str:
    """Upper-cased, whitespace-collapsed. Nothing else.

    Deliberately does NOT strip a bracketed suffix. One cached entry reads
    `CONSPIRATOR (SCRATCHED)`, and a normaliser that reduced it to `CONSPIRATOR`
    would match the scratched horse against whatever replaced it and then write
    the wrong gate — the single mistake this job is built to avoid.
    """
    return _WS.sub(" ", str(name or "").strip()).upper()


@dataclass
class BackfillReport:
    files_read: int = 0
    cache_rows: int = 0
    filled: int = 0
    already_had_a_draw: int = 0
    refused_name_mismatch: int = 0
    no_such_runner: int = 0
    cache_row_had_no_draw: int = 0
    dry_run: bool = False
    mismatches: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        head = "  DRY RUN — nothing written" if self.dry_run else "  written"
        lines = [
            head,
            f"  cache files read          {self.files_read:>7,}",
            f"  cache rows                {self.cache_rows:>7,}",
            f"  draws filled              {self.filled:>7,}",
            f"  already had a draw        {self.already_had_a_draw:>7,}",
            f"  REFUSED, name mismatch    {self.refused_name_mismatch:>7,}",
            f"  no such runner stored     {self.no_such_runner:>7,}",
            f"  cache row carried no draw {self.cache_row_had_no_draw:>7,}",
        ]
        if self.mismatches:
            lines.append("  refused:")
            lines += [f"    {m}" for m in self.mismatches[:20]]
            if len(self.mismatches) > 20:
                lines.append(f"    ... and {len(self.mismatches) - 20} more")
        if self.errors:
            lines.append(f"  ERRORS                    {len(self.errors):>7,}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def read_cache(path: Path) -> list[dict]:
    """One cache file as flat rows: race_date, race_no, horse_no, name, draw.

    The date comes from the file's own `date` field where it has one and from
    the filename otherwise. The two agree on every file in the archive; the
    filename is the fallback rather than the source because a file can be
    renamed and its contents cannot.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    date = doc.get("date")
    if not date:
        m = _FILE.search(path.name)
        if not m:
            raise ValueError(f"{path.name}: no date in the file or its name")
        date = m.group(1)

    rows = []
    for race in doc.get("races") or []:
        race_no = race.get("race_number")
        if race_no is None:
            continue
        for horse in race.get("horses") or []:
            rows.append({
                "race_date": str(date),
                "race_no": int(race_no),
                "horse_no": horse.get("horse_no"),
                "horse_name": horse.get("horse_name"),
                "draw": horse.get("draw"),
            })
    return rows


def _cache_files(cache: Path, date: str | None) -> list[Path]:
    if cache.is_file():
        return [cache]
    files = sorted(cache.glob("form_guide_*.json"))
    if date:
        files = [f for f in files if (m := _FILE.search(f.name)) and m.group(1) == date]
    return files


def backfill(cache: Path, *, db: Path | None = None, dry_run: bool = False,
             date: str | None = None) -> BackfillReport:
    report = BackfillReport(dry_run=dry_run)
    files = _cache_files(Path(cache), date)
    if not files:
        report.errors.append(f"no form_guide_*.json found under {cache}")
        return report

    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        writes: list[tuple] = []
        for path in files:
            try:
                rows = read_cache(path)
            except (ValueError, json.JSONDecodeError, OSError) as exc:
                report.errors.append(f"{path.name}: {exc}")
                continue
            report.files_read += 1
            report.cache_rows += len(rows)

            for row in rows:
                if row["draw"] in (None, "", 0):
                    report.cache_row_had_no_draw += 1
                    continue
                stored = conn.execute(
                    "SELECT horse_name, draw FROM runners "
                    "WHERE race_date = ? AND race_no = ? AND horse_no = ?",
                    (row["race_date"], row["race_no"], row["horse_no"])).fetchone()
                if stored is None:
                    report.no_such_runner += 1
                    continue
                if stored["draw"] is not None:
                    report.already_had_a_draw += 1
                    continue
                if normalise_name(stored["horse_name"]) != normalise_name(row["horse_name"]):
                    report.refused_name_mismatch += 1
                    report.mismatches.append(
                        f"{row['race_date']} R{row['race_no']} #{row['horse_no']}: "
                        f"cache {row['horse_name']!r} vs stored {stored['horse_name']!r}")
                    continue
                writes.append((int(row["draw"]), row["race_date"],
                               row["race_no"], row["horse_no"]))

        report.filled = len(writes)
        if writes and not dry_run:
            with transaction(conn):
                # `draw IS NULL` again in the statement, not only in the check
                # above: the read and the write are separate statements, and the
                # guard belongs where the write happens.
                conn.executemany(
                    "UPDATE runners SET draw = ? WHERE race_date = ? "
                    "AND race_no = ? AND horse_no = ? AND draw IS NULL", writes)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, required=True,
                    help="a form_guide_*.json file, or a directory of them")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--date", default=None, help="one meeting; omit for all")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    a = ap.parse_args(argv)
    report = backfill(a.cache, db=a.db, dry_run=a.dry_run, date=a.date)
    print(report.render())
    if report.filled and not a.dry_run:
        print("\n  runner_sarr is now stale for those meetings. Recompute:")
        print("    python -m hkrd.jobs.rebuild_sarr")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
