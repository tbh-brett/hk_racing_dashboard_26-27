"""Every trial in the archive, rated, for the fit job's measurement.

`jobs/fit_gbm` measures the Briefing's trial lines (`model/gbm_unseen`) against
the model's walk-forward chances, so it needs every horse's trials with the
same rating every other surface shows -- `query/trials`' own `_runner`, not a
second copy of it. Kept apart because `query/trials` is past 500 lines.
"""
from __future__ import annotations

from typing import Any

from hkrd.query.trials import _runner
from hkrd.store.connect import Connection, get_conn

__all__ = ["bands"]

# Every batch's size and best time grouped once: `_BATCH_SQL`'s per-row
# subqueries are right for one batch and wrong for the whole archive (AGENTS.md,
# a per-race constant is never computed per row).
_ALL_SQL = """
WITH b AS (SELECT trial_date, trial_no, venue, count(*) AS n, min(finish_time) AS best
             FROM trials GROUP BY trial_date, trial_no, venue)
SELECT t.*, b.n AS field_size, b.best AS best_time
  FROM trials t
  JOIN b ON b.trial_date = t.trial_date AND b.trial_no = t.trial_no AND b.venue IS t.venue
 ORDER BY t.horse_name, t.trial_date DESC, t.trial_no DESC
"""


def bands(*, conn: Connection | None = None) -> dict[str, list[dict[str, Any]]]:
    """Per horse, newest first: date, number, placing, field and band."""
    own = conn is None
    conn = conn or get_conn()
    try:
        out: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute(_ALL_SQL):
            r = _runner(row, row["field_size"], row["best_time"])
            out.setdefault(r["horse_name"], []).append(
                {"trial_date": r["trial_date"], "trial_no": r["trial_no"],
                 "place": r["place"], "field_size": r["field_size"],
                 "band": r["quality_band"]})
        return out
    finally:
        if own:
            conn.close()
