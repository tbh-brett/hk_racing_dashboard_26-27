"""Rebuild `race_tempo` -- how fast each race was run -- from raw.

    python -m hkrd.jobs.rebuild_tempo
    python -m hkrd.jobs.rebuild_tempo --date 2026-09-23

Derived and droppable: delete-then-insert for the dates it covers, like every
step in `jobs/derive_all`. Reads the runners' own sectionals, the races'
course, distance, class and winning time, and HKJC's standards
(`jobs/scrape_standards`). See `derive/tempo` for what is measured.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.derive import tempo as tempo_d
from hkrd.store.coerce import parse_section_times
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["rebuild", "TempoReport"]

_COLS = ("race_date", "race_no", "standard_class", "standard_exact",
         "leader_sections", "early_to", "early_time", "early_std", "variant",
         "early_dev", "early_dev_400", "late_time", "late_std", "late_dev",
         "band", "derive_version")


@dataclass
class TempoReport:
    rows_written: int = 0
    no_standard: int = 0
    no_sections: int = 0
    stood_in: int = 0
    # No HKJC standards stored yet: every race is skipped, and said to be.
    # Not an error -- a card scrape before the first weekly refresh has
    # nothing wrong with it -- but never silent either.
    missing_standards: bool = False
    errors: list[str] = field(default_factory=list)


def rebuild(db: Path | None = None, *, date: str | None = None) -> TempoReport:
    report = TempoReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        standards = {(r["venue"], r["surface"], r["distance"], r["class_key"]): dict(r)
                     for r in conn.execute("SELECT * FROM standard_times")}
        if not standards:
            report.missing_standards = True
            report.no_standard = conn.execute(
                "SELECT count(*) FROM races" + (" WHERE race_date = ?" if date else ""),
                [date] if date else []).fetchone()[0]
            return report
        where, params = ("AND a.race_date = ?", [date]) if date else ("", [])
        races = conn.execute(f"""
            SELECT a.race_date, a.race_no, a.venue, a.surface, a.distance, a.race_class,
                   (SELECT min(finish_time) FROM runners w WHERE w.race_date = a.race_date
                     AND w.race_no = a.race_no AND w.place = 1) winner_time
              FROM races a
             WHERE a.distance IS NOT NULL AND a.venue IS NOT NULL {where}
               AND EXISTS (SELECT 1 FROM runners r WHERE r.race_date = a.race_date
                              AND r.race_no = a.race_no AND r.place IS NOT NULL)
             ORDER BY a.race_date, a.race_no""", params).fetchall()
        sections: dict[tuple[str, int], list] = defaultdict(list)
        for r in conn.execute(f"""
            SELECT r.race_date, r.race_no, r.section_times FROM runners r
              JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
             WHERE r.place IS NOT NULL AND r.section_times IS NOT NULL
               AND r.section_times <> '' {where}""", params):
            try:
                sections[(r["race_date"], r["race_no"])].append(
                    parse_section_times(r["section_times"]))
            except ValueError as exc:
                report.errors.append(f"{r['race_date']} R{r['race_no']}: {exc}")

        picked = {}
        by_day: dict[tuple[str, str, str], list] = defaultdict(list)
        for race in races:
            surface = race["surface"] or "Turf"
            std, exact = tempo_d.pick_standard(standards, race["venue"], surface,
                                               race["distance"], race["race_class"])
            picked[(race["race_date"], race["race_no"])] = (std, exact)
            if std:
                by_day[(race["race_date"], race["venue"], surface)].append(
                    {"winner_time": race["winner_time"],
                     "standard_time": std["standard_time"]})
        variants = {k: tempo_d.meeting_variant(v) for k, v in by_day.items()}

        rows = []
        for race in races:
            key = (race["race_date"], race["race_no"])
            std, exact = picked[key]
            if not std:
                report.no_standard += 1
                continue
            got = tempo_d.tempo(dict(race), sections.get(key, []), std, exact,
                                variants.get((race["race_date"], race["venue"],
                                              race["surface"] or "Turf")))
            if got is None:
                report.no_sections += 1
                continue
            report.stood_in += int(not exact)
            rows.append(tuple(got[c] for c in _COLS))

        with transaction(conn):
            if date:
                conn.execute("DELETE FROM race_tempo WHERE race_date = ?", (date,))
            else:
                conn.execute("DELETE FROM race_tempo")
            conn.executemany(
                f"INSERT INTO race_tempo ({', '.join(_COLS)}) "
                f"VALUES ({', '.join('?' for _ in _COLS)})", rows)
        report.rows_written = len(rows)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--date", default=None)
    a = ap.parse_args(argv)
    r = rebuild(a.db, date=a.date)
    print(f"  race_tempo         {r.rows_written:>7,} rows")
    print(f"  no HKJC standard   {r.no_standard:>7,} races")
    print(f"  no sectionals      {r.no_sections:>7,} races")
    print(f"  neighbouring class {r.stood_in:>7,} races (HKJC publishes no figure for theirs)")
    if r.missing_standards:
        print("  no HKJC standard times stored — run python -m hkrd.jobs.scrape_standards")
    for e in r.errors[:10]:
        print(f"  ERROR {e}")
    return 1 if (r.errors or r.missing_standards) and not r.rows_written else 0


if __name__ == "__main__":
    raise SystemExit(main())
