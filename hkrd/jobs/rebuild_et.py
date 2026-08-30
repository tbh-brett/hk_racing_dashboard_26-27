"""Rebuild ET references and write runner_et.

Run after every meeting. The v4 table was built once in April and never
regenerated, so by August it was four months and ~3,400 runs stale and the par
times appeared frozen — a rolling window is only rolling if something rolls it.

    python -m hkrd.jobs.rebuild_et --window-months 24

References are held in memory and applied to the same window they were built
from, which is correct for a descriptive figure: ET says "how fast was this,
for these conditions", not "what will happen next". Anything predictive must be
validated walk-forward instead (see jobs/walkforward_et.py in the handoff).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from hkrd.derive import et
from hkrd.store.connect import db_path, get_conn, init_db, transaction

# The new schema splits race context from runner rows, so ET's inputs are
# assembled by a join rather than read off one flat sheet.
RUNS_SQL = """
SELECT r.race_date, r.race_no, r.horse_no, r.horse_name,
       r.place, r.finish_time, r.lengths_behind AS lbw, r.actual_weight,
       a.distance, a.going, a.race_class,
       a.venue  AS race_course,
       a.surface AS track_type
FROM runners r
JOIN races a ON r.race_date = a.race_date AND r.race_no = a.race_no
WHERE r.finish_time IS NOT NULL
"""


@dataclass
class ETReport:
    runs_loaded: int = 0
    rows_written: int = 0
    window: tuple[str, str] | None = None
    sec_per_length: float | None = None
    levels: pd.DataFrame | None = None
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  runs loaded      {self.runs_loaded:>7,}"]
        if self.window:
            lines.append(f"  window           {self.window[0]} to {self.window[1]}")
        if self.sec_per_length is not None:
            lines.append(f"  sec per length   {self.sec_per_length:.4f}"
                         f"   ({1/self.sec_per_length:.1f} lengths/sec)")
        if self.levels is not None:
            lines.append("  reference cells:")
            lines += ["    " + l for l in self.levels.to_string(index=False).splitlines()]
        lines.append(f"  runner_et rows   {self.rows_written:>7,}")
        if self.errors:
            lines.append(f"  ERRORS           {len(self.errors):>7,}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def load_runs(conn) -> pd.DataFrame:
    return pd.read_sql(RUNS_SQL, conn)


def rebuild(db: Path | None = None, *, window_months: int = 24,
            shrinkage_k: float = et.DEFAULT_SHRINKAGE_K) -> ETReport:
    """Rebuild into `db`, defaulting to the configured database.

    The default lives here rather than at the caller so api/ can trigger the
    job without importing store/.
    """
    report = ETReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        raw = load_runs(conn)
        report.runs_loaded = len(raw)
        if raw.empty:
            report.errors.append("no runs with a finish time; nothing to build")
            return report

        runs = et.prepare_runs(raw)
        refs = et.build_references(runs, window_months=window_months,
                                   shrinkage_k=shrinkage_k)
        report.window = (str(refs.built_from[0].date()), str(refs.built_from[1].date()))
        report.sec_per_length = refs.sec_per_length
        report.levels = refs.describe()

        figures = et.speed_figure(runs, refs)

        rows = [{
            "race_date": str(pd.Timestamp(r.race_date).date()),
            "race_no": int(r.race_no),
            "horse_no": int(r.horse_no),
            "et": _f(r.et), "et_level": r.et_level,
            "et_n_eff": _i(r.et_n), "et_shrunk": _i(getattr(r, "et_shrunk", None)),
            "sec_vs_par": _f(r.sec_vs_par), "len_vs_par": _f(r.len_vs_par),
            "sec_vs_race": _f(r.sec_vs_race), "len_vs_race": _f(r.len_vs_race),
            "figure": _f(r.figure), "confidence": r.confidence,
            "derive_version": et.DERIVE_VERSION,
        } for r in figures.itertuples()]

        cols = ["race_date", "race_no", "horse_no", "et", "et_level", "et_n_eff",
                "et_shrunk", "sec_vs_par", "len_vs_par", "sec_vs_race",
                "len_vs_race", "figure", "confidence", "derive_version"]
        sql = (f"INSERT INTO runner_et ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)}) "
               f"ON CONFLICT (race_date, race_no, horse_no) DO UPDATE SET "
               + ", ".join(f"{c} = excluded.{c}" for c in cols[3:]))
        with transaction(conn):
            conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        report.rows_written = len(rows)
    finally:
        conn.close()
    return report


def _f(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _i(v) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None,
                    help="defaults to $HKRD_DB, else ./hkrd.db")
    ap.add_argument("--window-months", type=int, default=24,
                    help="rolling window; 0 uses all history")
    ap.add_argument("--shrinkage-k", type=float, default=et.DEFAULT_SHRINKAGE_K)
    a = ap.parse_args(argv)

    report = rebuild(a.db, window_months=a.window_months,
                     shrinkage_k=a.shrinkage_k)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
