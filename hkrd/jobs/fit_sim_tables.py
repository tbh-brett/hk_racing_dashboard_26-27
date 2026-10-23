"""The simulator's tables for a card date -> `sim_tables` (racesim-SPEC §7).

    python -m hkrd.jobs.fit_sim_tables --date 2026-10-25
    python -m hkrd.jobs.fit_sim_tables --pending        # every stored card with none at this version

Everything from settled races strictly before the card (model/sim_tables): the borrowed races'
clocks, the middle calls, the trip costs, traffic, slow starts, the noise, and this season's
stewards' contacts. About half a minute of one core, so `sim_card --next` runs it as a tick of its
own when the next race's card has no tables yet, and simulates from the tick after. A row a stored
simulation names is never rewritten: the simulation must always re-run exactly.
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from hkrd.derive.tempo import pick_standard
from hkrd.model import shape_frame, sim_tables
from hkrd.store import gbm as gbm_store
from hkrd.store import sim as store
from hkrd.store.connect import Connection, get_conn, init_db, transaction
from hkrd.store.shape import load_runs, trip_gap

__all__ = ["fit", "pending_dates", "main", "TablesReport"]


@dataclass
class TablesReport:
    as_of: str = ""
    written: bool = False
    trained_through: str = ""
    templates: int = 0
    seconds: float = 0.0
    note: str = ""

    def render(self) -> str:
        if self.note:
            return f"fit_sim_tables {self.as_of}: {self.note}"
        return (f"fit_sim_tables {self.as_of}: {self.templates:,} template races, settled races through "
                f"{self.trained_through} ({self.seconds:.0f}s)")


def _std_cum(conn: Connection):
    stds, races = store.standards(conn), store.tempo_races(conn)

    def std_cum(race_id: str) -> list[float] | None:
        """The race's own class standard at each mark, cumulative, scaled by its day's track speed."""
        r = races.get(race_id)
        if not r:
            return None
        std, _ = pick_standard(stds, r["venue"], r["surface"] or "Turf", r["distance"], r["race_class"])
        if not std:
            return None
        x = [float(v) for v in str(std["sections"]).split(";")]
        return list(np.cumsum(x) * (1 + (r["variant"] or 0)))
    return std_cum


def fit(date: str, db: Path | None = None, *, conn: Connection | None = None) -> TablesReport:
    """Fit and store one card date's tables, unless a simulation already names them."""
    t0 = time.time()
    report = TablesReport(as_of=date)
    own = conn is None
    conn = conn or get_conn(db)
    try:
        init_db(conn)
        if store.tables_named(conn, date, sim_tables.VERSION):
            report.note = "a stored simulation names these tables; they are kept as they are"
            return report
        gap = trip_gap(conn, before=date)
        if gap:
            report.note = f"not fitted: {gap}"
            return report
        t = shape_frame.build(load_runs(conn, before=date))
        season = int(shape_frame.season_of(pd.Series(pd.to_datetime([date])))[0])
        contacts = store.season_contacts(conn, start=f"{season}-08-01", before=date)
        tables = sim_tables.fit(t, date, std_cum=_std_cum(conn), contact_races=contacts, last_season=season - 1)
        report.trained_through = str(t.race_date.max())
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        with transaction(conn):
            report.written = store.save_tables(conn, as_of=date, version=sim_tables.VERSION,
                                               trained_through=report.trained_through, tables=tables, created_at=now)
        report.templates = sum(tables["sizes"].values())
    finally:
        if own:
            conn.close()
    report.seconds = time.time() - t0
    return report


def pending_dates(conn: Connection, *, today: str) -> list[str]:
    """Stored cards from today on with a race still to run and no tables at this version."""
    return [d for d in gbm_store.unrun_dates(conn, today=today)
            if store.tables_row(conn, d, sim_tables.VERSION) is None]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--date", help="YYYY-MM-DD")
    which.add_argument("--pending", action="store_true")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.date:
        dates = [a.date]
    else:
        conn = get_conn(a.db)
        try:
            init_db(conn)
            dates = pending_dates(conn, today=dt.date.today().isoformat())
        finally:
            conn.close()
    for d in dates:
        print(fit(d, a.db).render())
    if not dates:
        print("fit_sim_tables: every stored card has its tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
