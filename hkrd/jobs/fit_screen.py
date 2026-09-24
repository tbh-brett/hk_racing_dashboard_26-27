"""Re-derive the Screen's weights in hkrd/model/screen.py, walk-forward.

    python -m hkrd.jobs.fit_screen
    python -m hkrd.jobs.fit_screen --db path/to/hkrd.db

Walks every settled meeting through `query/screen_inputs.gather` -- the reader
the Briefing uses -- turns each runner into `model/screen.features`, and fits
the exploded logit in `model/screen_fit`. So the weights are fitted on the
inputs the page will score, read the way the page reads them.

Then it scores the weights it WOULD have had: for each season from the third
on, fitted on the seasons before and tested on that one. Nothing in the
printed walk-forward figures has seen its own test season.

Prints; writes nothing. The constants live in model/screen.py, next to what
they mean, and this job exists so nobody has to take them on trust -- the
same arrangement as `jobs/fit_blend`. A few minutes: it reads ~700 meetings.
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from hkrd.derive.probability import devig
from hkrd.model import screen as model
from hkrd.model.screen_fit import fit, win_probabilities
from hkrd.query.screen_inputs import gather
from hkrd.store.connect import db_path, get_conn

# The archive opens in September 2019, so every horse in 2019-20 looks
# first-up with no history. Features that read a horse's past are not honest
# until the second season.
FIRST_SEASON = 2020
KEYS = tuple(f.key for f in model.FACTORS)
FORM = ("form", "unrated")
FORM_RIDER = ("form", "unrated", "jockey")


def season(date: str) -> int:
    """HK seasons run September to July: 2025-09-01 is season 2025."""
    d = dt.date.fromisoformat(date)
    return d.year if d.month >= 8 else d.year - 1


@dataclass
class Matrix:
    X: np.ndarray
    race: np.ndarray
    place: np.ndarray
    season: np.ndarray
    market: np.ndarray        # de-vigged closing win chance, NaN if unpriced


def build(db: Path | None, *, since: str) -> Matrix:
    conn = get_conn(db if db is not None else db_path())
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT race_date FROM runners WHERE place = 1 "
            "AND race_date >= ? ORDER BY race_date", (since,))]
        X, race, place, seas, mkt = [], [], [], [], []
        rid = 0
        for i, date in enumerate(dates):
            for block in gather(date, conn=conn):
                runners = block["runners"]
                if not any(r["place"] == 1 for r in runners):
                    continue
                ctx = model.race_context(block["race"], runners)
                odds = [r["win_odds"] for r in runners]
                fair = (devig(odds) if all(o and o > 1 for o in odds)
                        else [np.nan] * len(runners))
                for r, p in zip(runners, fair):
                    values, _ = model.features(r, ctx)
                    X.append([values[k] for k in KEYS])
                    race.append(rid)
                    place.append(np.nan if r["place"] is None else float(r["place"]))
                    seas.append(season(date))
                    mkt.append(p)
                rid += 1
            if (i + 1) % 100 == 0:
                print(f"  read {i + 1:>4} of {len(dates)} meetings", flush=True)
        return Matrix(np.asarray(X, dtype=float), np.asarray(race),
                      np.asarray(place), np.asarray(seas), np.asarray(mkt))
    finally:
        conn.close()


def _cols(names: tuple[str, ...]) -> list[int]:
    return [KEYS.index(n) for n in names]


def _winner_rows(m: Matrix, mask: np.ndarray) -> np.ndarray:
    return mask & (m.place == 1)


def _top_k_hit(m: Matrix, mask: np.ndarray, p: np.ndarray, k: int) -> float:
    """Share of races whose winner is inside the top k by `p`."""
    hits = races = 0
    idx = np.flatnonzero(mask)
    for rid in np.unique(m.race[idx]):
        rows = idx[m.race[idx] == rid]
        if np.isnan(p[rows]).any():
            continue
        top = rows[np.argsort(-p[rows], kind="stable")[:k]]
        hits += int((m.place[top] == 1).any())
        races += 1
    return hits / races if races else float("nan")


def walk_forward(m: Matrix) -> dict:
    seasons = sorted(set(m.season.tolist()))
    tests = [s for s in seasons if s >= seasons[0] + 2]
    table, pooled = [], {"screen": [], "market": [], "won": []}
    top = {"form": {3: [], 4: []}, "screen": {3: [], 4: []}, "market": {3: [], 4: []}}
    for s in tests:
        tr, te = m.season < s, m.season == s
        row = {"season": s, "races": len(np.unique(m.race[te]))}
        probs = {}
        for name, cols in (("form", _cols(FORM)), ("form+rider", _cols(FORM_RIDER)),
                           ("screen", list(range(len(KEYS))))):
            beta, _ = fit(m.X[tr][:, cols], m.race[tr], m.place[tr])
            p = np.full(len(m.place), np.nan)
            p[te] = win_probabilities(m.X[te][:, cols], m.race[te], beta)
            probs[name] = p
            row[name] = float(-np.log(p[_winner_rows(m, te)]).mean())
        w = _winner_rows(m, te) & ~np.isnan(m.market)
        row["market"] = float(-np.log(m.market[w]).mean()) if w.any() else None
        table.append(row)
        for k in (3, 4):
            top["form"][k].append((_top_k_hit(m, te, probs["form"], k), row["races"]))
            top["screen"][k].append((_top_k_hit(m, te, probs["screen"], k), row["races"]))
            top["market"][k].append((_top_k_hit(m, te, m.market, k), row["races"]))
        priced = te & ~np.isnan(m.market)
        pooled["screen"].append(probs["screen"][priced])
        pooled["market"].append(m.market[priced])
        pooled["won"].append((m.place[priced] == 1).astype(float))
    agg = {name: {k: sum(h * n for h, n in v[k]) / sum(n for _, n in v[k]) for k in (3, 4)}
           for name, v in top.items()}
    sc, mk, won = (np.concatenate(pooled[k]) for k in ("screen", "market", "won"))
    bands = []
    ratio = sc / mk
    for lo, hi in ((0, 0.8), (0.8, 1.25), (1.25, 2.0), (2.0, np.inf)):
        sel = (ratio >= lo) & (ratio < hi)
        bands.append({"lo": lo, "hi": hi, "runs": int(sel.sum()),
                      "ae": float(won[sel].sum() / mk[sel].sum()) if sel.any() else None})
    return {"seasons": table, "top": agg, "bands": bands,
            "test_races": int(sum(r["races"] for r in table))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--since", default=f"{FIRST_SEASON}-09-01")
    a = ap.parse_args(argv)

    m = build(a.db, since=a.since)
    n_races = len(np.unique(m.race))
    print(f"\n  runs {len(m.place):,}  races {n_races:,}  "
          f"seasons {m.season.min()}-{m.season.max()}")
    if n_races < 500:
        print("  too few settled races to fit the Screen")
        return 1

    wf = walk_forward(m)
    print("\n  walk-forward, win log loss (lower is better)")
    for r in wf["seasons"]:
        print(f"    {r['season']}  races {r['races']:>4}  form {r['form']:.4f}  "
              f"form+rider {r['form+rider']:.4f}  screen {r['screen']:.4f}  "
              f"market {r['market']:.4f}")
    print(f"\n  winner inside the top 3 / top 4, {wf['test_races']:,} test races")
    for name, v in wf["top"].items():
        print(f"    {name:<7} {100 * v[3]:.1f}% / {100 * v[4]:.1f}%")
    print("\n  where the screen and the closing tote disagree (A/E vs the tote)")
    for b in wf["bands"]:
        print(f"    screen/tote {b['lo']:.2f}-{b['hi']:.2f}  runs {b['runs']:>6,}  A/E {b['ae']:.3f}")

    beta, se = fit(m.X, m.race, m.place)
    print("\n  weights on every settled race -- paste into model/screen.FACTORS\n")
    for f, b, s in zip(model.FACTORS, beta, se):
        runs = int((m.X[:, KEYS.index(f.key)] != 0).sum()) if f.key not in ("form", "jockey") else 0
        print(f"    {f.key:<20} weight {b:+.3f}  (se {s:.3f}, x{np.exp(b):.2f})  runs {runs:,}")
    t = wf["top"]
    print(f"\n    FIT races={n_races} runs={len(m.place)} test_races={wf['test_races']} "
          f"top3 screen={t['screen'][3]:.3f} form={t['form'][3]:.3f} market={t['market'][3]:.3f} "
          f"top4 screen={t['screen'][4]:.3f} form={t['form'][4]:.3f} market={t['market'][4]:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
