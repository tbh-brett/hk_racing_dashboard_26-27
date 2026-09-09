"""Score the archive under a changed model, and say whether the change shows.

    from hkrd.model import evaluate
    base = evaluate.score(conn)
    variant = evaluate.score(conn, adjust=my_change)
    print(evaluate.compare(base, variant).render())

WHY THIS IS SEPARATE FROM `backtest`. That module asks whether the model is
calibrated and whether there is anything to bet on. This one asks a narrower
question that has to be answered first: given two versions of the model, can
this archive tell them apart? Four candidate changes were measured one at a
time and all four came back "not significant", which was read as four negative
results when it was one fact about the test -- see `model/power`.

HOW A COMPARISON IS MADE HONESTLY.

- **The unit is a race.** Two runners in the same race are one observation of
  one draw. Counting them separately multiplies n by about eleven and shrinks
  every interval by the square root of that, which turns noise into findings.
- **The comparison is PAIRED on the race.** A race the model reads well is
  read well by both variants, so pairing removes nearly all the between-race
  variance. Unpaired, the standard deviation is about 0.30 and nothing under
  +0.03 is ever visible; paired it is about 0.035.
- **Both arms come from `rebuild_sarr.score_runners`**, the same function the
  job writes rows with. Not a copy of it. A harness with its own loop reports
  the difference between two loops as though it were a difference between two
  models, and there is no way to see that from the output.
- **Walk-forward is not optional and not configurable.** `score_runners`
  builds every profile from runs strictly before the race it scores, so a
  variant cannot opt out of it here.

WHAT A NEGATIVE RESULT MEANS. Nothing, until `Comparison.resolvable` is true.
Until then "not significant" and "no effect" are the same output.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from hkrd.jobs import rebuild_sarr as job
from hkrd.model import power as power_m
from hkrd.model import sarr
from hkrd.store.connect import Connection, get_conn

__all__ = ["score", "per_race_rho", "compare", "Comparison", "MIN_FIELD"]

# Under four runners a rank correlation is one of three values and carries no
# information about a model. Dropped rather than averaged in, because a race
# that can only return -1, 0 or +1 dominates the variance of everything else.
MIN_FIELD = 4


@dataclass(frozen=True)
class Comparison:
    """Two variants, judged on the same races."""
    name: str
    n_races: int
    baseline: float
    variant: float
    delta: float
    t: float
    p: float
    wilcoxon_p: float
    power: power_m.Power

    @property
    def inert(self) -> bool:
        """The variant changed no ranking anywhere.

        Its own answer, and not the same as an effect too small to see: a
        variant that never fired is usually a wiring mistake, and reporting it
        as "not significant" hides that behind a plausible number.
        """
        return self.delta == 0.0 and self.baseline == self.variant

    @property
    def resolvable(self) -> bool:
        """Could this archive see an effect of the size observed?"""
        return abs(self.delta) >= self.power.detectable

    def render(self) -> str:
        verdict = ("INERT -- the variant changed no ranking in any race"
                   if self.inert else
                   "significant" if self.p < power_m.ALPHA else
                   "not significant" if self.resolvable else
                   "NOT SIGNIFICANT AND NOT RESOLVABLE -- the archive is too "
                   "small to tell this apart from zero")
        return "\n".join([
            f"  {self.name}",
            f"    races compared        {self.n_races:>8,}",
            f"    baseline rho          {self.baseline:>+8.4f}",
            f"    variant  rho          {self.variant:>+8.4f}",
            f"    difference            {self.delta:>+8.4f}",
            f"    paired t              {self.t:>+8.2f}   p {self.p:.4f}",
            f"    Wilcoxon                       p {self.wilcoxon_p:.4f}",
            f"    smallest visible here {self.power.detectable:>+8.4f}",
            f"    races needed for this {self.power.needed or 0:>8,}",
            f"    -> {verdict}",
        ])


def score(conn: Connection | None = None, *, adjust=None,
          min_prior: int = 2, since: str | None = None) -> pd.DataFrame:
    """Walk-forward SARR for every runnable race, without writing anything.

    Returns one row per scored runner: the race, the horse, its score and where
    it actually finished. `adjust` is the variant seam described in
    `rebuild_sarr.ProfileAdjust`.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        raw = pd.read_sql(job.RUNS_SQL, conn)
        if raw.empty:
            return pd.DataFrame(columns=["race_date", "race_no", "horse_no",
                                         "sarr", "place"])
        runs = sarr.annotate_runs(raw)
        runs["vet_category"] = job._vet_flags(conn, runs)
        targets = runs[runs["race_date"] >= since] if since else runs
        rows, _ = job.score_runners(runs, targets, min_prior=min_prior,
                                    adjust=adjust)
        scored = pd.DataFrame(
            [(r[0], r[1], r[2], r[3]) for r in rows],
            columns=["race_date", "race_no", "horse_no", "sarr"])
        # The finishing position comes from the same frame the scores were
        # built from, so a runner cannot be scored against a result that was
        # filtered out from under it.
        return scored.merge(
            runs[["race_date", "race_no", "horse_no", "place"]],
            on=["race_date", "race_no", "horse_no"], how="left")
    finally:
        if own:
            conn.close()


def per_race_rho(scored: pd.DataFrame) -> pd.Series:
    """Spearman correlation of score against finishing position, per race.

    Positive is right: SARR is lower-is-better and `place` is lower-is-better,
    so a model that ranks correctly correlates POSITIVELY here.
    """
    out: dict[tuple[str, int], float] = {}
    usable = scored.dropna(subset=["sarr", "place"])
    for key, g in usable.groupby(["race_date", "race_no"]):
        if len(g) < MIN_FIELD:
            continue
        rho = stats.spearmanr(g["sarr"], g["place"]).statistic
        if not np.isnan(rho):
            out[key] = float(rho)
    return pd.Series(out, dtype=float)


def compare(baseline: pd.DataFrame, variant: pd.DataFrame, *,
            name: str = "variant vs baseline") -> Comparison:
    """Paired on the race, and reported with what the test could have seen."""
    a, b = per_race_rho(baseline), per_race_rho(variant)
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    if j.empty:
        raise ValueError("no races in common between the two runs")
    diffs = (j["b"] - j["a"]).to_numpy()
    if np.allclose(diffs, 0.0):
        # Identical output is a real answer -- usually that the variant never
        # fired -- and must not be dressed up as p = 1.0 from a test that was
        # not run.
        tstat, pval, wp = 0.0, 1.0, 1.0
    else:
        res = stats.ttest_rel(j["b"], j["a"])
        tstat, pval = float(res.statistic), float(res.pvalue)
        wp = float(stats.wilcoxon(j["b"], j["a"]).pvalue)
    return Comparison(
        name=name, n_races=len(j),
        baseline=float(j["a"].mean()), variant=float(j["b"].mean()),
        delta=float(diffs.mean()), t=tstat, p=pval, wilcoxon_p=wp,
        power=power_m.paired_power(diffs))
