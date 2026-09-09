"""How big an effect the archive can actually resolve.

    python -m hkrd.model.power

WHY THIS EXISTS. Three model changes were measured and rejected in a row --
discounting a run a vet found something on, specifying the rating term against
today's field rather than a stale one, and correcting fmrp for the class a run
was earned in. Every one of them landed near +0.002 on held-out mean per-race
rank correlation, and every one came back "not significant" over 306 held-out
races. That is not the same finding as "the change does nothing", and the two
were indistinguishable because nobody had asked what the test could see.

This module asks. It is deliberately separate from `backtest`, which answers
"is the model calibrated and is there anything to bet on"; this one answers
"would I know if I had improved it", which has to be settled BEFORE a result
is called negative.

WHAT IT MEASURES. The unit is a RACE, never a runner: two runners in the same
race are one observation of the same draw, and counting them separately
inflates n by about eleven and shrinks every interval by the square root of
that. The statistic is the per-race difference in Spearman rank correlation
between two model variants, paired on the race, because the pairing removes
almost all the between-race variance -- a race the model reads well is read
well by both variants -- and that is what makes a +0.002 effect reachable at
all.

WHAT IT FOUND. On the 306-race held-out window the paired differences have a
standard deviation of roughly 0.035, so the smallest difference detectable at
80% power is about +0.0056 -- more than twice the size of anything measured.
Resolving +0.002 needs on the order of 2,400 held-out races. The archive held
1,732; two seasons of backfill roughly doubles it, which is the arithmetic
that made the backfill worth four hours.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from hkrd.store.connect import Connection, get_conn

__all__ = ["Power", "paired_power", "races_needed", "detectable_effect",
           "archive_size", "ALPHA", "POWER"]

# Two-sided, and the conventional 80%. Named rather than inlined because every
# number this module returns is a function of both, and a reader comparing two
# power statements has to be able to see they were computed under the same one.
ALPHA = 0.05
POWER = 0.80

# Normal quantiles for the defaults above. A t-based version would differ in
# the third decimal at these sample sizes and would suggest a precision the
# input standard deviation does not have.
_Z_ALPHA = 1.959963985
_Z_POWER = 0.841621234


@dataclass(frozen=True)
class Power:
    """What a test on `n_races` can and cannot see."""
    n_races: int
    sd_paired: float
    detectable: float
    observed: float | None = None
    needed: int | None = None

    def render(self) -> str:
        lines = [
            f"  held-out races          {self.n_races:>8,}",
            f"  sd of paired difference {self.sd_paired:>8.4f}",
            f"  smallest effect visible {self.detectable:>+8.4f}"
            f"   (alpha {ALPHA}, power {POWER:.0%})",
        ]
        if self.observed is not None:
            verdict = ("resolvable" if abs(self.observed) >= self.detectable
                       else "BELOW THE RESOLUTION OF THIS TEST")
            lines.append(f"  effect being tested     {self.observed:>+8.4f}   {verdict}")
        if self.needed is not None:
            lines.append(f"  races needed for it     {self.needed:>8,}")
        return "\n".join(lines)


def detectable_effect(n_races: int, sd_paired: float, *,
                      alpha: float = ALPHA, power: float = POWER) -> float:
    """The smallest paired difference a test on `n_races` would call significant.

    Anything below this comes back "not significant" whether it is real or not,
    so a negative result is only informative once the effect is above it.
    """
    if n_races < 2 or sd_paired <= 0:
        return float("inf")
    z_a = _Z_ALPHA if alpha == ALPHA else abs(_ppf(1 - alpha / 2))
    z_b = _Z_POWER if power == POWER else abs(_ppf(power))
    return (z_a + z_b) * sd_paired / math.sqrt(n_races)


def races_needed(effect: float, sd_paired: float, *,
                 alpha: float = ALPHA, power: float = POWER) -> int:
    """How many races it would take to resolve `effect`."""
    if effect == 0 or sd_paired <= 0:
        return 0 if sd_paired <= 0 else 2 ** 31 - 1
    z_a = _Z_ALPHA if alpha == ALPHA else abs(_ppf(1 - alpha / 2))
    z_b = _Z_POWER if power == POWER else abs(_ppf(power))
    return int(math.ceil(((z_a + z_b) * sd_paired / abs(effect)) ** 2))


def paired_power(diffs: Sequence[float], *, alpha: float = ALPHA,
                 power: float = POWER) -> Power:
    """Read the resolution straight off the per-race differences themselves.

    `diffs` is one number per race: variant rho minus baseline rho. Its own
    spread is the only honest estimate of what the next comparison can see, so
    it is measured rather than assumed -- and it is measured from the paired
    differences, not from the rho values, because pairing is what removes the
    between-race variance that would otherwise swamp everything.
    """
    d = np.asarray([x for x in diffs if x is not None and not np.isnan(x)],
                   dtype=float)
    n = len(d)
    if n < 2:
        return Power(n_races=n, sd_paired=float("nan"),
                     detectable=float("inf"), observed=None, needed=None)
    sd = float(d.std(ddof=1))
    obs = float(d.mean())
    return Power(n_races=n, sd_paired=sd,
                 detectable=detectable_effect(n, sd, alpha=alpha, power=power),
                 observed=obs,
                 needed=races_needed(obs, sd, alpha=alpha, power=power))


def archive_size(*, conn: Connection | None = None,
                 since: str | None = None) -> dict[str, int]:
    """Races and runners available to score, which is not the same as rows.

    A race is only usable if it has a result and a scored field, so this counts
    what an evaluation would actually get rather than what the tables hold.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        where = "WHERE r.race_date >= ?" if since else ""
        args = (since,) if since else ()
        row = conn.execute(f"""
            SELECT count(*) races, coalesce(sum(n), 0) runners FROM (
                SELECT r.race_date, r.race_no, count(*) n
                  FROM runners r
                  JOIN runner_sarr s USING (race_date, race_no, horse_no)
                 {where}
                 GROUP BY r.race_date, r.race_no
                HAVING sum(CASE WHEN r.place = 1 THEN 1 ELSE 0 END) > 0
                   AND count(*) >= 4)
        """, args).fetchone()
        span = conn.execute(
            "SELECT min(race_date), max(race_date) FROM runner_sarr").fetchone()
        return {"races": row[0], "runners": row[1],
                "first": span[0], "last": span[1]}
    finally:
        if own:
            conn.close()


def _ppf(p: float) -> float:
    """Normal quantile, Acklam's rational approximation.

    Here so the module carries no dependency for two constants it needs only
    when someone changes alpha or power away from the defaults. Accurate to
    about 1e-9, which is far beyond what an estimated standard deviation
    justifies.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > hi:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q, r = p - 0.5, (p - 0.5) ** 2
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sd", type=float, default=0.035,
                    help="sd of the per-race paired difference (measured: 0.035)")
    ap.add_argument("--effect", type=float, default=0.002,
                    help="the effect size you want to be able to see")
    ap.add_argument("--holdout", type=float, default=0.5,
                    help="share of races held out for testing")
    a = ap.parse_args(argv)

    size = archive_size()
    held = int(size["races"] * a.holdout)
    print(f"  archive        {size['races']:>8,} scored races "
          f"({size['first']} .. {size['last']})")
    print(Power(n_races=held, sd_paired=a.sd,
                detectable=detectable_effect(held, a.sd),
                observed=a.effect,
                needed=races_needed(a.effect, a.sd)).render())
    need = races_needed(a.effect, a.sd)
    print(f"\n  at a {a.holdout:.0%} split that is "
          f"{math.ceil(need / max(a.holdout, 1e-9)):,} scored races in total, "
          f"against {size['races']:,} today.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
