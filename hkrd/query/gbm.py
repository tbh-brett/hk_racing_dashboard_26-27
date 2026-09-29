"""The fundamental model as the pages read it: `runner_gbm` and `gbm_models`.

Race Day, the Briefing's Screen and Model Analysis all read here. Every chance
was computed when the card was scored (`jobs/score_gbm`); this module never
loads the model (gbm-SPEC §6), and adds only three things at read time:

- a race whose field has changed since its score is renormalised over the
  runners still in it -- a scratched horse must not hold probability (§3);
- the flags (`model/gbm_flags`), at most two a runner, each with its measured
  record from the live model's walk-forward, never a typed number (§14.4);
- what was assumed: a body weight carried from the last run, the going.

THE GAP IS SHOWN, NEVER RECOMMENDED. Nothing here sorts or ranks by it, and
its note says what five seasons found: where the model is keener than the
price, the price has been right (§10, §14.5).
"""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from hkrd.derive.features import GROUPS, TIME
from hkrd.derive.probability import place_from_win
from hkrd.model.gbm_flags import FLAGS, flags
from hkrd.query import pools
from hkrd.store.connect import Connection, get_conn

__all__ = ["live", "scores", "race", "record", "version_fit", "GROUPS", "MAX_FLAGS"]

MAX_FLAGS = 2

_SCORES = """
SELECT g.race_no, g.horse_no, u.horse_name, g.p_win, g.p_place, g.contrib_json,
       g.facts_json, g.model_version, g.scored_at, c.p_win AS p_card,
       coalesce(u.place_code, '') LIKE 'W%' AS scratched
  FROM runner_gbm g
  JOIN runners u USING (race_date, race_no, horse_no)
  LEFT JOIN runner_gbm c ON c.race_date = g.race_date AND c.race_no = g.race_no
       AND c.horse_no = g.horse_no AND c.stage = 'card'
 WHERE g.race_date = ? AND g.stage = 'latest' {race}
 ORDER BY g.race_no, g.horse_no
"""


def live(conn: Connection) -> dict[str, Any] | None:
    """The promoted model: its version, dates and record (not its text)."""
    r = conn.execute("SELECT version, trained_through, promoted_at, rounds, params_json, "
                     "features_version, record_json FROM gbm_models WHERE promoted = 1"
                     ).fetchone()
    if r is None:
        return None
    return {"version": r["version"], "trained_through": r["trained_through"],
            "promoted_at": r["promoted_at"], "rounds": r["rounds"],
            "features_version": r["features_version"],
            "lightgbm": json.loads(r["params_json"]).get("lightgbm"),
            "record": json.loads(r["record_json"])}


def version_fit(conn: Connection, version: str | None) -> dict[str, Any]:
    """What the model that scored a meeting was trained through, and how
    often its top four held the winner walk-forward -- keyed as the Briefing
    has always read the Screen's (`screen` for the model's four)."""
    r = (conn.execute("SELECT trained_through, record_json FROM gbm_models WHERE version = ?",
                      (version,)).fetchone() if version else None)
    if r is None:
        return {"fitted": None, "top4_has_winner": None, "test_races": None}
    wf = json.loads(r["record_json"]).get("walk_forward", {})
    top4 = wf.get("top4_has_winner")
    return {"fitted": r["trained_through"], "test_races": wf.get("test_races"),
            "top4_has_winner": {"screen": top4["model"], "market": top4["market"]} if top4 else None}


def _flag_records(model: dict[str, Any] | None) -> dict[str, dict]:
    rows = ((model or {}).get("record", {}).get("walk_forward", {}).get("flags", []))
    return {r["flag"]: r for r in rows}


def _runner_flags(facts: pd.DataFrame, records: dict[str, dict]) -> list[list[dict]]:
    fl = flags(facts)
    out = []
    for _, row in fl.iterrows():
        on = [k for k in FLAGS if bool(row[k])][:MAX_FLAGS]
        out.append([{"key": k, **FLAGS[k],
                     "runs": records.get(k, {}).get("runs"),
                     "model_ae": records.get(k, {}).get("model", {}).get("ae"),
                     "price_ae": records.get(k, {}).get("price", {}).get("ae")} for k in on])
    return out


def scores(date: str, race_no: int | None = None, *,
           conn: Connection) -> dict[tuple[int, int], dict[str, Any]]:
    """Each runner still in its race: the model's win and place chance (as
    percentages), the first read when the card landed, the twelve groups as
    "x vs this field", its flags and what was assumed. Empty when unscored."""
    rows = conn.execute(_SCORES.format(race="AND g.race_no = ?" if race_no else ""),
                        (date, race_no) if race_no else (date,)).fetchall()
    if not rows:
        return {}
    df = pd.DataFrame([dict(r) for r in rows])
    df = df[df["scratched"] == 0].reset_index(drop=True)
    total = df.groupby("race_no")["p_win"].transform("sum")
    df["p"] = df["p_win"] / total
    df["p_place_now"] = df["p_place"]
    for rn, x in df.groupby("race_no"):
        if abs(total[x.index[0]] - 1.0) > 1e-9:       # someone came out after the score
            df.loc[x.index, "p_place_now"] = place_from_win(
                x["p"].to_numpy(), places=3 if len(x) >= 7 else 2)
    facts = pd.DataFrame([json.loads(f) for f in df["facts_json"]])
    chips = _runner_flags(facts, _flag_records(live(conn)))
    out = {}
    for i, r in df.iterrows():
        f = facts.iloc[i]
        assumed = []
        if f.get("body_weight") == "last run":
            assumed.append("body weight: last run's")
        elif f.get("body_weight") == "median":
            assumed.append("body weight: none on record, a debutant's median")
        out[(int(r["race_no"]), int(r["horse_no"]))] = {
            "horse_no": int(r["horse_no"]), "horse_name": r["horse_name"],
            "model_pct": round(100 * r["p"], 1),
            "place_pct": round(100 * float(r["p_place_now"]), 1),
            "card_pct": None if r["p_card"] is None else round(100 * r["p_card"], 1),
            "groups": {k: round(math.exp(v), 2)
                       for k, v in json.loads(r["contrib_json"] or "{}").items()},
            "flags": chips[i], "assumed": assumed,
            "going_assumed": f.get("going") == "assumed",
            "model_version": r["model_version"], "scored_at": r["scored_at"]}
    return out


def _draw_note(conn: Connection, date: str, race_no: int, model: dict | None) -> str | None:
    """The race-level note for the two courses whose draw the model gets wrong."""
    r = conn.execute("SELECT venue, surface, distance FROM races WHERE race_date = ? "
                     "AND race_no = ?", (date, race_no)).fetchone()
    if r is None or not model:
        return None
    course = {("HV", "Turf", 1200): "HV 1200", ("ST", "Turf", 1000): "ST 1000 straight"}.get(
        (r["venue"], r["surface"], r["distance"]))
    rows = [x for x in model["record"].get("walk_forward", {}).get("draw_courses", [])
            if x["course"] == course]
    if not rows:
        return None
    said = "; ".join(f"gates {x['gates']} won {x['won']} where the model expected "
                     f"{x['model']['expected_wins']:.0f} and the price "
                     f"{x['price']['expected_wins']:.0f}" for x in rows)
    return f"The model reads the draw here wrongly ({course}, five seasons): {said}."


def race(date: str, race_no: int, *, conn: Connection | None = None) -> dict[str, Any]:
    """One race for GET /api/model/gbm/{date}/{race_no}: the model beside the
    latest de-vigged market (none before prices exist) and the gap between."""
    own = conn is None
    conn = conn or get_conn()
    try:
        got = scores(date, race_no, conn=conn)
        market = {m["horse_no"]: m["win_pct"] for m in
                  pools.place_probabilities(date, race_no, conn=conn)["runners"]}
        model = live(conn)
        runners = []
        for (_, no), x in sorted(got.items()):
            mkt = market.get(no)
            runners.append({**x, "market_pct": mkt,
                            "gap": None if mkt is None else round(x["model_pct"] - mkt, 1)})
        first = runners[0] if runners else {}
        return {"race_date": date, "race_no": race_no,
                "model_version": first.get("model_version"), "scored_at": first.get("scored_at"),
                "going_assumed": bool(first.get("going_assumed")),
                "draw_note": _draw_note(conn, date, race_no, model), "runners": runners}
    finally:
        if own:
            conn.close()


def _gap_note(wf: dict[str, Any]) -> str | None:
    named = wf.get("gap_named") or {}
    a, b = named.get("model_1_5x_price"), named.get("market_15_model_two_thirds")
    if not a or not b:
        return None
    return (f"Horses the model rates at 1.5x the price or more won {a['won']:,} where the "
            f"price expected {a['price']['expected_wins']:,.0f} (A/E {a['price']['ae']:.2f}). "
            f"Horses the market backs to 15%+ that the model marks down by a third won "
            f"{b['won']:,} where the price expected {b['price']['expected_wins']:,.0f} "
            f"(A/E {b['price']['ae']:.2f}). A gap is a question about what the market "
            f"knows, not a bet.")


def record(*, conn: Connection | None = None) -> dict[str, Any] | None:
    """GET /api/model/gbm/record: what the live model has earned, which fit is
    live, and what the gate said about the newest fit. None before the first."""
    own = conn is None
    conn = conn or get_conn()
    try:
        model = live(conn)
        if model is None:
            return None
        newest = conn.execute("SELECT version, created_at, promoted, record_json FROM "
                              "gbm_models ORDER BY created_at DESC LIMIT 1").fetchone()
        wf = model["record"].get("walk_forward", {})
        return {"live": {k: model[k] for k in ("version", "trained_through", "promoted_at",
                                               "rounds", "features_version", "lightgbm")},
                "last_fit": {"version": newest["version"], "created_at": newest["created_at"],
                             "promoted": bool(newest["promoted"]),
                             "gate": json.loads(newest["record_json"]).get("gate")},
                "walk_forward": wf, "meetings": model["record"].get("meetings", []),
                "gap_note": _gap_note(wf), "groups": list(GROUPS), "time_inputs": TIME,
                "flags": FLAGS}
    finally:
        if own:
            conn.close()
