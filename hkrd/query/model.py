"""Model transparency — what the figures are made of, for the Lab page.

Design brief 05 §5: show WHY each model ranked each horse where it did, not just
the rank. The page doubles as documentation of each model's own limitations,
which fits how everything else here has been built — nothing dressed up as more
than it has been shown to be.
"""
from __future__ import annotations


from hkrd.model import blend as blend_m, sarr as sarr_m
from hkrd.store.connect import Connection, get_conn

__all__ = ["et_breakdown", "et_reference_summary", "model_status",
           "sarr_breakdown", "sarr_influence", "blend_breakdown"]


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


# ── SARR: what the score is made of ──────────────────────────────────────────

def sarr_breakdown(date: str, race_no: int, *,
                   conn: Connection | None = None) -> dict:
    """One row per runner, one column per weighted component.

    Design brief 05 §5: "show WHY each model ranked each horse where it did".
    The contributions are read from runner_sarr_component, which the rebuild
    writes from the same call that produces the score, so the columns on screen
    always sum to the number beside them.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute("""
            SELECT s.horse_no, r.horse_name, s.sarr, s.sarr_rank, s.n_prior,
                   r.win_odds, r.draw, r.jockey, s.derive_version
            FROM runner_sarr s
            JOIN runners r USING (race_date, race_no, horse_no)
            WHERE s.race_date = ? AND s.race_no = ?
            ORDER BY s.sarr_rank
        """, (date, race_no)).fetchall()
        if not rows:
            return {"race_date": date, "race_no": race_no, "runners": [],
                    "components": [], "unscored": _unscored(conn, date, race_no)}

        parts: dict[int, dict[str, float]] = {}
        for c in conn.execute(
                "SELECT horse_no, component, contribution "
                "FROM runner_sarr_component WHERE race_date = ? AND race_no = ?",
                (date, race_no)):
            parts.setdefault(c["horse_no"], {})[c["component"]] = c["contribution"]

        runners = []
        for r in rows:
            got = parts.get(r["horse_no"], {})
            runners.append({
                **{k: r[k] for k in ("horse_no", "horse_name", "sarr", "sarr_rank",
                                     "n_prior", "win_odds", "draw", "jockey",
                                     "derive_version")},
                "components": {k: got.get(k) for k in sarr_m.COMPONENTS},
                # The page states this rather than assuming it: a row whose
                # parts do not add up is a broken rebuild, not a rounding blur.
                "components_sum_to_score": (
                    abs(sum(got.values()) - r["sarr"]) < 1e-9 if got else False),
            })
        return {
            "race_date": date, "race_no": race_no, "runners": runners,
            "components": sarr_influence(conn=conn),
            "derive_version": rows[0]["derive_version"],
            "unscored": _unscored(conn, date, race_no),
        }
    finally:
        if own:
            conn.close()


def _unscored(conn: Connection, date: str, race_no: int) -> list[str]:
    """Runners in the race that SARR could not score.

    Named, not counted: a field of 12 showing 9 rows with no explanation is how
    the old page let a silent skip look like a short field.
    """
    return [r["horse_name"] for r in conn.execute("""
        SELECT r.horse_name FROM runners r
        LEFT JOIN runner_sarr s USING (race_date, race_no, horse_no)
        WHERE r.race_date = ? AND r.race_no = ? AND s.sarr IS NULL
        ORDER BY r.horse_no
    """, (date, race_no))]


def sarr_influence(*, conn: Connection | None = None) -> list[dict]:
    """Per component: its fitted weight, and how much it actually moves scores.

    Both, because they disagree. The brief asked for a bar sized by weight,
    noting that fmrp "carries roughly 3x the weight of the next-largest term".
    That is true of the COEFFICIENT and misleading on its own: measured across
    every scored runner, `wpr` carries the fifth-largest weight and the SECOND
    largest realised influence, and `draw` carries the largest multiplier of
    all (0.3) while contributing exactly zero, because nothing supplies a draw
    score. A page about why a model ranked a horse has to show the realised
    figure or it documents the intention rather than the model.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        stats = {r["component"]: dict(r) for r in conn.execute("""
            SELECT component,
                   count(*) n,
                   avg(abs(contribution)) mean_abs,
                   min(contribution) lo,
                   max(contribution) hi
            FROM runner_sarr_component GROUP BY component
        """)}
        widest = max((s["mean_abs"] or 0.0) for s in stats.values()) if stats else 0.0
        out = []
        for name in sarr_m.COMPONENTS:
            s = stats.get(name, {})
            mean_abs = s.get("mean_abs") or 0.0
            out.append({
                "component": name,
                "weight": sarr_m.COMPONENT_WEIGHTS[name],
                "rows": s.get("n", 0),
                "mean_abs": round(mean_abs, 4),
                "range": [round(s["lo"], 4), round(s["hi"], 4)] if s else None,
                # Bar widths for the header, one per reading of "how big is
                # this term". They are deliberately not the same bar.
                "weight_share": round(
                    abs(sarr_m.COMPONENT_WEIGHTS[name])
                    / max(abs(w) for w in sarr_m.COMPONENT_WEIGHTS.values()), 4),
                "influence_share": round(mean_abs / widest, 4) if widest else 0.0,
                "inert": bool(s) and mean_abs == 0.0,
            })
        return out
    finally:
        if own:
            conn.close()


# ── the blend, and what it is worth ──────────────────────────────────────────

def blend_breakdown(date: str, race_no: int, *, weight: float | None = None,
                    conn: Connection | None = None) -> dict:
    """The blend's components side by side, never just its output.

    Brief 05 §5 wants the page to make the mechanism self-evident. It does:
    the fitted weight on the fundamental stream is 0.00, so the blended column
    IS the de-vigged market column until the reader moves the weight. The
    alternatives ride along so that is checkable rather than asserted.
    """
    own = conn is None
    conn = conn or get_conn()
    weight = (blend_m.DEFAULT_BLEND_WEIGHT if weight is None
              else max(0.0, min(1.0, float(weight))))
    try:
        rows = conn.execute("""
            SELECT r.horse_no, r.horse_name, r.win_odds, s.sarr, s.sarr_rank
            FROM runners r
            LEFT JOIN runner_sarr s USING (race_date, race_no, horse_no)
            WHERE r.race_date = ? AND r.race_no = ?
            ORDER BY r.horse_no
        """, (date, race_no)).fetchall()
        # Both streams need the whole field: a softmax over some of the runners
        # and a de-vig over some of the prices are each normalised against a
        # denominator missing terms. Say which is short rather than blending
        # two numbers that mean different things.
        priced = [r for r in rows if r["win_odds"]]
        scored = [r for r in rows if r["sarr"] is not None]
        missing = {"unpriced": len(rows) - len(priced),
                   "unscored": len(rows) - len(scored)}

        market = blend_m.market_probability([r["win_odds"] for r in rows])             if len(priced) == len(rows) and rows else []
        fund = blend_m.fundamental_probability([r["sarr"] for r in rows])             if len(scored) == len(rows) and rows else []
        blended = blend_m.blend(fund, market, weight) if len(rows) else []

        overround = (round(100 * (sum(1 / r["win_odds"] for r in priced) - 1), 1)
                     if len(priced) == len(rows) and rows else None)

        runners = []
        for i, r in enumerate(rows):
            runners.append({
                "horse_no": r["horse_no"], "horse_name": r["horse_name"],
                "win_odds": r["win_odds"], "sarr": r["sarr"],
                "sarr_rank": r["sarr_rank"],
                # Raw is 1/odds unnormalised -- it is shown BECAUSE it does not
                # sum to 100%. That gap is the overround.
                "market_raw": (round(100 / r["win_odds"], 1)
                               if r["win_odds"] else None),
                "market_devig": (round(100 * float(market[i]), 1)
                                 if len(market) else None),
                "fundamental": (round(100 * float(fund[i]), 1)
                                if len(fund) else None),
                "blended": (round(100 * float(blended[i]), 1)
                            if len(blended) else None),
            })
        order = sorted((r for r in runners if r["blended"] is not None),
                       key=lambda r: -r["blended"])
        for rank, r in enumerate(order, start=1):
            r["blend_rank"] = rank

        return {
            "race_date": date, "race_no": race_no, "runners": runners,
            "weight": weight, "overround": overround, "missing": missing,
            "calibration": blend_m.CALIBRATION,
        }
    finally:
        if own:
            conn.close()
