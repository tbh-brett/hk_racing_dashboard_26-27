"""The simulator's tables for a card date, all from settled races strictly before it (racesim-SPEC §7).

A port of the lab's race-sim/fit_v0.py and the two regressions it reads (race-shape-2d/partA3.py and
the one-turn fits of turns/turns_check.py). The keys are the lab's, because the vendored simulator
(web/assets/shape/sim-core.js) reads them:

  templates  real races that lend a simulated race its clock: the leader's time at each section mark,
             lengths behind the leader by rank at every mark, and the race's pace against its own
             class standard on its own day (`ratio`, tempo-1.0). The latest 400 per course and trip
             from 2022/23 on; the simulator takes those within a class of the card race.
  calls      where a horse sits at the middle calls, between its first call and its finish
  costs      lengths a trip costs at the finish: lane at the home turn and while settled, no cover,
             early position x pace, the price as a control, by venue and trip group (the one-turn
             Sha Tin chutes apart from the two-turn trips)
  traffic    P(held up in the straight) by position turning in and field size, pooled and by venue
             (`by_venue`); its cost is a placeholder (below)
  slow       how often a horse starts slowly, and what it costs
  noise      the spread of finishing lengths once the price is known, pooled and by trip group
             (`by_group`); the engine reads one number, `for_race` chooses it
  contacts   the stewards' contacts per runner by part of the race, this season, and their direction

The study's two statsmodels fits are least squares on the same dummy columns here (numpy), with
standard errors clustered by race as statsmodels computes them (its small-sample factor, normal
quantiles). Fitted on the lab's inputs they match its tables (tests/test_shape_models.py).

Pure: frames and dicts in, the tables out. jobs/fit_sim_tables reads the database and stores them.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from hkrd.derive.pace import section_lengths
from hkrd.model.shape_frame import LENGTH_M, floats

__all__ = ["VERSION", "SLOW_PRIOR_RUNS", "cost_group", "fit", "for_race", "group_noise", "venue_of", "slow_base",
           "trip_regressions", "trip_costs", "templates", "calls", "contacts"]

VERSION = "simtables-1.2"     # 1.1 (26 Oct): the finishing spread by trip group; 1.2 (29 Oct): hold-ups by venue
RECENT_FROM = 2022            # templates, calls and the traffic rates: 2022/23 on
NOISE_FROM = 2019             # the costs' controls and the noise: 2019/20 on
PER_COURSE = 400
# Places a slow start costs at the first call beyond what was expected, from the 16 Oct study's
# walk-forward settle model (partC.py, hours of CPU); the one number the tables take from it.
SLOW_EXTRA = {"early_frac_extra": 0.115, "early_frac_extra_n": 8417}
# The regression says held-up runs finished better than their price: the comments note a hold-up for
# horses still travelling, which tells who gets held up, not what it costs. So the cost is a
# placeholder until it can be measured.
TRAFFIC_COST = [1.0, 0.5, 2.0]
SLOW_PRIOR_RUNS = 8           # a horse's slow-start record is shrunk towards the population's by 8 runs
NOISE_MIN_RUNS = 1000         # a trip group's own spread needs this many runs; fewer take the pooled one
TRAFFIC_MIN_RUNS = 2000       # a venue's own hold-up rates need this many stated runs
_Z = float(stats.norm.ppf(0.975))


def cost_group(venue: str, surface: str, distance: int) -> str:
    """The simulator's trip group: Sha Tin's 1600 and 1800 start in a chute and meet one bend, like
    the 1400; the 2000 and beyond have two (18 Oct)."""
    if venue == "ST" and surface == "AWT":
        return "ST AWT 1200" if distance <= 1200 else "ST AWT 1650+"
    if venue == "ST":
        return {1000: "ST 1000 straight", 1200: "ST 1200", 1400: "ST 1400",
                1600: "ST 1600-1800", 1800: "ST 1600-1800"}.get(distance, "ST 2000+")
    return {1000: "HV 1000", 1200: "HV 1200", 1650: "HV 1650"}.get(distance, "HV 1800+")


def venue_of(group: str) -> str:
    """A trip group's venue and surface, as `traffic.by_venue` keys them."""
    return "HV Turf" if group.startswith("HV") else "ST AWT" if group.startswith("ST AWT") else "ST Turf"


def for_race(T: Mapping, group: str) -> dict:
    """The tables as the engine reads them for one race: `noise.sd_lengths` is the race's trip
    group's own spread where it has one, the pooled one otherwise; `traffic.rate` and `.base` its
    venue's hold-up rates where it has them. The engine takes one table for every race, so the
    choice is made here and the vendored file is untouched."""
    noise = dict(T.get("noise") or {})
    own = (noise.get("by_group") or {}).get(group)
    if own:
        noise.update(sd_lengths=own["sd_lengths"], group=group, pooled_sd_lengths=T["noise"]["sd_lengths"])
    traffic = dict(T.get("traffic") or {})
    mine = (traffic.get("by_venue") or {}).get(venue_of(group))
    if mine:
        traffic.update(rate=mine["rate"], base=mine["base"], venue=venue_of(group))
    return {**T, "noise": noise, "traffic": traffic}


def _study_group(venue: str, surface: str, distance: int) -> str:
    """Part A3's groups, which pooled every Sha Tin turf trip from 1600 m as "ST 1600+"."""
    g = cost_group(venue, surface, distance)
    return "ST 1600+" if g in ("ST 1600-1800", "ST 2000+") else g


# ── least squares on dummy columns ─────────────────────────────────────────────────────────────
def _ols(df: pd.DataFrame, y: str, terms: Sequence[tuple], cluster: str | None = None
         ) -> tuple[dict[str, float], dict[str, tuple[float, float]], float, int]:
    """OLS with an intercept. `terms`: ("num", col) | ("cat", col, reference or None for the first
    level) | ("x", a, b) for a product. Returns params, 95% intervals (clustered by `cluster`),
    the residual variance and the row count. Names are statsmodels' formula names."""
    cols, names = [np.ones(len(df))], ["Intercept"]
    for t in terms:
        if t[0] == "num":
            cols.append(df[t[1]].to_numpy(float)); names.append(t[1])
        elif t[0] == "x":
            cols.append(df[t[1]].to_numpy(float) * df[t[2]].to_numpy(float)); names.append(f"{t[1]}:{t[2]}")
        else:
            col, ref = t[1], t[2]
            levels = sorted(df[col].unique())
            ref = levels[0] if ref is None else ref
            label = f"C({col}, Treatment('{ref}'))" if t[2] is not None else f"C({col})"
            for lev in levels:
                if lev != ref:
                    cols.append((df[col] == lev).to_numpy(float))
                    names.append(f"{label}[T.{lev}]")
    X, Y = np.column_stack(cols), df[y].to_numpy(float)
    beta = np.linalg.lstsq(X, Y, rcond=None)[0]
    u = Y - X @ beta
    n, k = X.shape
    scale = float(u @ u) / (n - k)
    bread = np.linalg.pinv(X.T @ X)
    if cluster is not None:
        codes = pd.Categorical(df[cluster]).codes
        g = int(codes.max()) + 1
        score = np.zeros((g, k))
        np.add.at(score, codes, X * u[:, None])
        cov = bread @ (score.T @ score) @ bread * (g / (g - 1)) * ((n - 1) / (n - k))
    else:
        cov = bread * scale
    se = np.sqrt(np.diag(cov))
    params = dict(zip(names, beta.tolist()))
    ci = {nm: (b - _Z * s, b + _Z * s) for nm, b, s in zip(names, beta.tolist(), se.tolist())}
    return params, ci, scale, n


def _r2(v: float) -> float:
    return round(float(v), 2)


# ── trip costs (Part A3, and the one-turn Sha Tin trips apart) ─────────────────────────────────
_A3_TERMS = [("cat", "turn", "1rail"), ("cat", "settle", "1rail"), ("num", "nocover_pre"),
             ("num", "early_frac"), ("x", "early_frac", "tempo"), ("num", "tempo"), ("num", "logp"),
             ("num", "field"), ("cat", "cls", None)]
_KEEP = ("nocover_pre", "early_frac", "early_frac:tempo")


def _lane_cat(x: float) -> str:
    if pd.isna(x):
        return "ns"
    return {1: "1rail", 2: "2wide", 3: "3wide"}.get(int(x), "4plus")


def _a3_record(d: pd.DataFrame) -> dict:
    terms = _A3_TERMS + ([("cat", "dist", None)] if d.dist.nunique() > 1 else [])
    params, ci, _, _ = _ols(d, "lens_vs_med", terms, cluster="race_id")
    rec: dict = {"runs": int(len(d))}
    for k, v in params.items():
        if "turn" in k or "settle" in k or k in _KEEP:
            rec[k] = [_r2(v), _r2(ci[k][0]), _r2(ci[k][1])]
    return rec


def trip_regressions(t: pd.DataFrame, *, last_season: int) -> dict[str, dict]:
    """Part A3's record per trip group -- each trip term's lengths and its 95% interval, clustered
    by race -- on every settled season to `last_season`."""
    df = t[(t.season <= last_season) & t.lens_vs_med.notna() & t.early_frac.notna()].copy()
    df = df[df.lens_vs_med.between(-15, 30)]
    df["turn"], df["settle"] = df.lane_turn.map(_lane_cat), df.lane_settle.map(_lane_cat)
    df["logp"] = np.log(df.p_mkt)
    df["cls"] = df.race_class.fillna("unk").astype(object).astype(str)
    df["tempo"] = df.tempo_dev.fillna(0)
    need = ["lens_vs_med", "early_frac", "tempo", "logp", "field", "nocover_pre", "dist"]
    a3: dict[str, dict] = {}
    df["grp"] = [_study_group(v, s, int(x)) for v, s, x in zip(df.venue, df.surface, df.distance)]
    for g, d in df.groupby("grp"):
        a3[g] = _a3_record(d.dropna(subset=need))
    # one turn and two: the Sha Tin turf chutes (1600, 1800) apart from 2000 m and beyond (18 Oct),
    # on runs with a draw, as turns_check.py fitted them
    st = df[(df.venue == "ST") & (df.surface == "Turf") & df.draw.notna()]
    for g, trips in (("ST 1600-1800", lambda x: x.isin((1600, 1800))), ("ST 2000+", lambda x: x >= 2000)):
        a3[g] = _a3_record(st[trips(st.distance)].dropna(subset=need))
    return a3


def trip_costs(a3: Mapping[str, dict]) -> dict[str, dict]:
    """The costs dict the simulator reads, from `trip_regressions`."""
    costs = {}
    for g, rec in a3.items():
        c = lambda k: rec.get(k, [0, 0, 0])[0]   # noqa: E731
        lane = lambda part: {n: c(f"C({part}, Treatment('1rail'))[T.{lab}]")   # noqa: E731
                             for n, lab in (("2", "2wide"), ("3", "3wide"), ("4", "4plus"))}
        costs[g] = dict(settle=lane("settle"), turn=lane("turn"), nocover=c("nocover_pre"),
                        early=c("early_frac"), early_tempo=c("early_frac:tempo"), runs=rec["runs"])
        if g == "ST 1000 straight":           # no bends: a lane on the straight course isn't ground lost
            costs[g]["settle"] = {"2": 0, "3": 0, "4": 0}
            costs[g]["turn"] = {"2": 0, "3": 0, "4": 0}
    return costs


# ── templates ──────────────────────────────────────────────────────────────────────────────────
def _template(g: pd.DataFrame, std_cum: Callable[[str], list[float] | None]) -> dict | None:
    D = int(g.distance.iloc[0])
    L = section_lengths(D)
    secs = [floats(s) for s in g.section_times]
    if any(s is None or len(s) != len(L) for s in secs) or len(g) < 6:
        return None
    cum = np.cumsum(np.array(secs), axis=1)                       # runner x mark
    lead = cum.min(axis=0)
    speed = D / float(np.median(cum[:, -1]))
    gaps = np.sort((cum - lead) * speed / LENGTH_M, axis=0)       # lengths behind the leader, by rank
    if gaps[-1, -1] > 60 or not np.all(np.diff(lead) > 0):
        return None
    race_id = g.race_id.iloc[0]
    sc = std_cum(race_id)
    ratio = [round(float(a / b), 5) for a, b in zip(lead, sc)] if sc and len(sc) == len(lead) else None
    going = g.going.iloc[0]
    return dict(id=race_id, cls=str(g.race_class.iloc[0]), going=going if isinstance(going, str) else None,
                n=len(g), lead=[round(float(x), 2) for x in lead], **({"ratio": ratio} if ratio else {}),
                gaps=[[round(float(x), 2) for x in gaps[:, k]] for k in range(len(L))],
                tempo=None if pd.isna(g.tempo_dev.iloc[0]) else round(float(g.tempo_dev.iloc[0]), 2),
                band=g.tempo_band.iloc[0] if isinstance(g.tempo_band.iloc[0], str) else None)


def templates(recent: pd.DataFrame, std_cum: Callable[[str], list[float] | None]) -> dict[str, list]:
    """The latest 400 template races per course and trip."""
    out = {}
    for (v, s, d), grp in recent.groupby(["venue", "surface", "distance"]):
        rows = [r for r in (_template(g, std_cum) for _, g in grp.groupby("race_id")) if r]
        if rows:
            out[f"{v}_{s}_{int(d)}"] = rows[-PER_COURSE:]
    return out


# ── the middle calls ───────────────────────────────────────────────────────────────────────────
def _call_rows(recent: pd.DataFrame) -> list[tuple]:
    """(first call, finish, share of the run from the first call, metres to go, position): each a
    share of the field, 0 first."""
    rows = []
    for r in recent.itertuples(index=False):
        pos = floats(r.running_positions)
        if not pos or r.field < 6 or pd.isna(r.place):
            continue
        L = section_lengths(int(r.distance))
        if len(pos) != len(L):
            continue
        s = np.cumsum(L)
        fn = r.field - 1
        e, f = (pos[0] - 1) / fn, (r.place - 1) / fn
        for k in range(1, len(L) - 1):
            rows.append((e, f, (s[k] - s[0]) / (s[-1] - s[0]), s[-1] - s[k], (pos[k] - 1) / fn))
    return rows


def calls(recent: pd.DataFrame) -> dict:
    """Position at a middle call = (1 - w) x first call + w x finish: one power of the share run,
    and better, one weight per call by its distance from the post."""
    rows = _call_rows(recent)
    M = np.array([(e, f, u, m) for e, f, u, _, m in rows])
    best = None
    for p in np.arange(0.4, 8.01, 0.05):
        w = M[:, 2] ** p
        res = M[:, 3] - ((1 - w) * M[:, 0] + w * M[:, 1])
        sse = float((res ** 2).sum())
        if best is None or sse < best[1]:
            best = (round(float(p), 2), sse, float(res.std()))
    out = dict(power=best[0], noise_sd=round(best[2], 3), n=len(M), by_togo={})
    M2 = pd.DataFrame([(min(1600, int(round(togo / 400.0)) * 400), e, f, m) for e, f, _, togo, m in rows],
                      columns=["togo", "e", "f", "m"])
    for g, d in M2.groupby("togo"):
        x, y = (d.f - d.e).values, (d.m - d.e).values
        w = float((x * y).sum() / (x * x).sum())
        res = d.m - ((1 - w) * d.e + w * d.f)
        out["by_togo"][str(g)] = dict(w=round(w, 3), sd=round(float(res.std()), 3), n=int(len(d)))
    return out


# ── contacts ───────────────────────────────────────────────────────────────────────────────────
def contacts(races: Sequence[Mapping]) -> dict:
    """Stewards' contacts that moved a horse, per runner, by part of the race. `races`: {distance,
    venue, surface, runners: how many ran, events: race_trip's}."""
    cnt = {"start": 0, "mid": 0, "straight": 0}
    dirs = {"out": 0, "in": 0, "none": 0}
    runners = 0
    for r in races:
        D = r["distance"]
        Lh = 310 if r["venue"] == "HV" else (380 if r["surface"] == "AWT" else 430)
        runners += r["runners"]
        for e in r["events"]:
            if not e.get("push"):
                continue
            ph = "start" if e["d"] >= D - 150 else ("straight" if e["d"] < Lh else "mid")
            cnt[ph] += 1
            dirs["out" if e["dir"] > 0 else "in" if e["dir"] < 0 else "none"] += 1
    return dict(per_runner={k: round(v / runners, 4) for k, v in cnt.items()}, direction=dirs,
                runners=runners, races=len(races))


# ── the whole set ──────────────────────────────────────────────────────────────────────────────
def _settled(t: pd.DataFrame, last_season: int) -> pd.DataFrame:
    """Settled runs from 2019/20 to `last_season` with a price, a first call and a believable margin:
    what the noise, the traffic cost and the slow-start rate are measured on."""
    f = t[(t.season >= NOISE_FROM) & (t.season <= last_season) & t.lens_vs_med.between(-15, 30)
          & t.p_mkt.notna() & t.early_frac.notna()].copy()
    f["logp"] = np.log(f.p_mkt)
    f["grp"] = [_study_group(v, s, int(d)) for v, s, d in zip(f.venue, f.surface, f.distance)]
    f["cls"] = f.race_class.fillna("unk").astype(str)
    return f


def group_noise(f: pd.DataFrame) -> dict[str, dict]:
    """The pooled noise's fit on each trip group's runs alone (`_settled` rows): the spread of
    finishing lengths once the price, the field and the class are known. Measured 26 Oct, the AWT
    spreads a field more than any turf trip -- 4.9 to 5.7 lengths against 3.2 to 4.7, and its
    winners win by two lengths or more in 22 to 28% of races against 12 to 17% -- so one spread
    for every trip made the trip decide too much of an AWT result and drew its margins as turf's."""
    groups = [cost_group(v, s, int(d)) for v, s, d in zip(f.venue, f.surface, f.distance)]
    out = {}
    for g, d in f.groupby(pd.Series(groups, index=f.index)):
        if len(d) < NOISE_MIN_RUNS:
            continue
        terms = [("num", "logp"), ("num", "field")] + ([("cat", "cls", None)] if d.cls.nunique() > 1 else [])
        _, _, scale, n = _ols(d, "lens_vs_med", terms)
        out[g] = dict(sd_lengths=round(float(np.sqrt(scale)), 2), n=n)
    return out


def _traffic_by_venue(stated: pd.DataFrame) -> dict[str, dict]:
    """The pooled hold-up rates fitted on each venue's runs alone. Measured 29 Oct: a Happy Valley
    runner on the rail in the front 60% turning in is held up in the straight 5 to 6% of the time,
    against 10 to 13% at Sha Tin, and midfield on the rail 7% against 15 to 18%: the shorter
    straight and the smaller fields. Pooled, the simulation held HV runners up as often as Sha
    Tin's."""
    out = {}
    for v, d in stated.groupby(stated.venue + " " + stated.surface):
        if len(d) < TRAFFIC_MIN_RUNS:
            continue
        r = d.groupby(["tb", "fb"]).traffic_straight.agg(["mean", "size"]).reset_index()
        out[v] = dict(rate={f"{int(x.tb)}_{int(x.fb)}": round(float(x["mean"]), 4) for _, x in r.iterrows() if x["size"] >= 50},
                      base=round(float(d.traffic_straight.mean()), 4), runs=int(len(d)))
    return out


def slow_base(t: pd.DataFrame, *, last_season: int) -> float:
    """How often a run with a comment starts slowly: the population a horse's own record is shrunk
    towards (`slow.base`)."""
    f = _settled(t, last_season)
    return round(float(f[f.has_comment == 1].slow_bad.mean()), 4)


def fit(t: pd.DataFrame, card: str, *, std_cum: Callable[[str], list[float] | None],
        contact_races: Sequence[Mapping], last_season: int) -> dict:
    """Every table for a card on `card` from the trip frame `t` (model/shape_frame), runs strictly
    before the card. `last_season`: the last fully settled season (the costs' and the noise's)."""
    t = t[t.race_date < card]
    recent = t[t.season >= RECENT_FROM]
    tpl = templates(recent, std_cum)

    f = _settled(t, last_season)
    stated = f[f.has_comment == 1]
    tbins, fbins = [-0.01, 0.2, 0.4, 0.6, 0.8, 1.01], [0, 10, 12, 14]
    stated = stated.assign(tb=pd.cut(stated.turn_frac, tbins, labels=False),
                           fb=pd.cut(stated.field, fbins, labels=False))
    recent_stated = stated[stated.season >= RECENT_FROM]      # the comments mention traffic more often lately
    rate = recent_stated.groupby(["tb", "fb"]).traffic_straight.agg(["mean", "size"]).reset_index()
    traffic = dict(turn_bins=tbins, field_bins=fbins,
                   rate={f"{int(r.tb)}_{int(r.fb)}": round(float(r["mean"]), 4)
                         for _, r in rate.iterrows() if r["size"] >= 50},
                   base=round(float(recent_stated.traffic_straight.mean()), 4),
                   seasons=f"{RECENT_FROM}-{str(RECENT_FROM + 1)[2:]} to {last_season}-{str(last_season + 1)[2:]}",
                   definition="the Comments on Running say held up, no clear run, checked or similar in the straight",
                   by_venue=_traffic_by_venue(recent_stated))
    s = stated.dropna(subset=["turn_frac"])
    mt, ci, _, _ = _ols(s, "lens_vs_med", [("num", c) for c in ("traffic_straight", "traffic_pre", "slow_bad",
                                                                "early_frac", "turn_frac", "logp", "field")]
                        + [("cat", "cls", None), ("cat", "grp", None)], cluster="race_id")
    traffic["cost_lengths_regression"] = [_r2(mt["traffic_straight"]), *map(_r2, ci["traffic_straight"])]
    traffic["cost_lengths"] = list(TRAFFIC_COST)
    traffic["cost_is_placeholder"] = True
    slow = dict(base=round(float(stated.slow_bad.mean()), 4), prior_runs=SLOW_PRIOR_RUNS,
                cost_lengths=[_r2(mt["slow_bad"]), *map(_r2, ci["slow_bad"])], **SLOW_EXTRA)
    _, _, scale, n = _ols(f, "lens_vs_med", [("num", "logp"), ("num", "field"), ("cat", "cls", None),
                                             ("cat", "grp", None)])
    last = f[f.place == f.field]
    noise = dict(sd_lengths=round(float(np.sqrt(scale)), 2), n=n,
                 last_horse_lengths_median=round(float(last.lengths_behind.median()), 2), by_group=group_noise(f))
    return dict(card=card, made_from=f"settled races before {card}", templates=tpl, calls=calls(recent),
                costs=trip_costs(trip_regressions(t, last_season=last_season)), traffic=traffic, slow=slow, noise=noise,
                contacts=contacts(contact_races), sizes={k: len(v) for k, v in tpl.items()})
