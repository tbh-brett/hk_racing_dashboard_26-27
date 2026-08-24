"""Model transparency — what the figures are made of, for the Lab page.

Design brief 05 §5: show WHY each model ranked each horse where it did, not just
the rank. The page doubles as documentation of each model's own limitations,
which fits how everything else here has been built — nothing dressed up as more
than it has been shown to be.
"""
from __future__ import annotations


from hkrd.store.connect import Connection, get_conn

__all__ = ["et_breakdown", "et_reference_summary", "model_status"]


def et_breakdown(date: str, race_no: int, *,
                 conn: Connection | None = None) -> dict:
    """Per-runner ET components for one race.

    Exposes the two figures the v4 model conflated. len_vs_par answers "was this
    a fast time for these conditions"; len_vs_race answers "did this horse
    outrun the field it was in". They are different questions and the page shows
    both rather than a single number standing for both.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute("""
            SELECT r.horse_no, r.horse_name, r.place, r.finish_time,
                   e.et, e.et_level, e.et_n_eff, e.et_shrunk,
                   e.sec_vs_par, e.len_vs_par, e.sec_vs_race, e.len_vs_race,
                   e.figure, e.confidence, e.derive_version
            FROM runners r
            LEFT JOIN runner_et e USING (race_date, race_no, horse_no)
            WHERE r.race_date = ? AND r.race_no = ?
            ORDER BY e.figure IS NULL, e.figure DESC
        """, (date, race_no)).fetchall()

        runners = [dict(r) for r in rows]
        pars = {round((r["finish_time"] or 0) + r["sec_vs_par"], 4)
                for r in runners if r["sec_vs_par"] is not None and r["finish_time"]}
        return {
            "race_date": date, "race_no": race_no, "runners": runners,
            # A par is a property of a race. If this is ever not 1 the model is
            # broken, whatever its accuracy -- v4 produced up to 1.98s of spread
            # within a single race because weight_band was a lookup key.
            "distinct_pars": len(pars),
            "par_time": next(iter(pars), None) if len(pars) == 1 else None,
            "derive_version": next((r["derive_version"] for r in runners
                                    if r["derive_version"]), None),
        }
    finally:
        if own:
            conn.close()


def et_reference_summary(*, conn: Connection | None = None) -> dict:
    """Coverage and confidence across the whole ET table — is it current, and
    how much evidence is behind it."""
    own = conn is None
    conn = conn or get_conn()
    try:
        total = conn.execute("SELECT count(*) FROM runner_et").fetchone()[0]
        conf = {r["confidence"]: r["n"] for r in conn.execute(
            "SELECT confidence, count(*) n FROM runner_et GROUP BY 1")}
        span = conn.execute(
            "SELECT min(race_date), max(race_date) FROM runner_et").fetchone()
        runners = conn.execute("SELECT count(*) FROM runners").fetchone()[0]
        return {
            "rows": total,
            "runners": runners,
            "coverage": round(total / runners, 4) if runners else 0.0,
            "confidence": conf,
            "first_date": span[0], "last_date": span[1],
            # Never let a raw Connection escape query/ -- it is not
            # serialisable and the layer's contract is plain data.
            "version": (row[0] if (row := conn.execute(
                "SELECT derive_version FROM runner_et LIMIT 1").fetchone()) else None),
        }
    finally:
        if own:
            conn.close()


def model_status(*, conn: Connection | None = None) -> dict:
    """Freshness per derived table, for the data-freshness strip.

    Reports staleness rather than asking the user to remember what to run: the
    old failure mode was pace silently missing for weeks because nobody knew a
    step had not run.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        latest_race = conn.execute("SELECT max(race_date) FROM races").fetchone()[0]
        out = {"latest_meeting": latest_race, "tables": {}}
        for table in ("runner_et", "runner_pace", "runner_sarr", "runner_tags"):
            covered = conn.execute(
                f"SELECT max(race_date) FROM {table}").fetchone()[0]
            rows = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            out["tables"][table] = {
                "rows": rows,
                "through": covered,
                "current": bool(covered and covered == latest_race),
            }
        return out
    finally:
        if own:
            conn.close()
