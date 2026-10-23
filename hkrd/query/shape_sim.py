"""The race simulation as node reads it and as the pages read it (racesim-SPEC §5.2, §8).

THE CARD. `card_inputs` assembles a meeting the way the lab's card_inputs.py did -- the declared
field, v2's 'latest' chances, the shape models' predictions and each horse's slow-start rate --
for every race whose inputs are complete. The settle spread is set on the card as a whole, so
races that have gone off stay in it; only races still to run are ever simulated.

THE FINGERPRINT. A race is simulated again only when its fingerprint changes, and it is built
from rounded inputs so a trivial re-score does not move it: v2's chance and the shape predictions
to 0.01, the declared field exact (who, draw, rider), plus the four versions and the settings.

THE PAGES. `race` is one race's newest simulation with what the browser needs to draw a run of it
(the borrowed races' clocks, the tables the simulator reads); `card` is a meeting's list. Every
figure was computed by jobs/sim_card; nothing here simulates. SIM WIN equals v2's by construction:
nothing here sorts, colours or ranks by the difference.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from hkrd.derive.pace import section_lengths
from hkrd.model.sim_tables import VERSION as TABLES_VERSION
from hkrd.model.sim_tables import cost_group
from hkrd.store import sim as store
from hkrd.store.connect import Connection, db_file, get_conn

__all__ = ["card_inputs", "fingerprint", "race_inputs", "race", "card", "TABLES_VERSION"]

_NEED = ("draw", "p_win", "pred_early", "p_lead", "exp_lane", "p_nocover", "slow_rate", "n_prior")


def _par(stds: dict, venue: str, surface: str, distance: int, cls: str) -> list[float] | None:
    keys = {k[3] for k in stds if k[:3] == (venue, surface, distance)}
    want = cls if cls in keys else "G" if cls.startswith("G") else cls
    row = stds.get((venue, surface, distance, want))
    return None if row is None else [float(x) for x in str(row["sections"]).split(";")]


def _r(v: Any, n: int) -> float | None:
    return None if v is None else round(float(v), n)


def card_inputs(conn: Connection, date: str) -> dict:
    """{meta, races, incomplete}: the card as node reads it, and the races left out with why."""
    got = store.card(conn, date)
    stds = store.standards(conn)
    by_race: dict[int, list[dict]] = {}
    for u in got["runners"]:
        by_race.setdefault(u["race_no"], []).append(u)
    races, incomplete, v2 = [], [], []
    for r in got["races"]:
        field = by_race.get(r["race_no"], [])
        missing = sorted({k for u in field for k in _NEED if u[k] is None})
        if not field or missing:
            incomplete.append(dict(no=r["race_no"], why="no declared field" if not field
                                   else "no " + ", ".join(missing), gone=bool(r["gone"])))
            continue
        D, cls = int(r["distance"]), str(r["race_class"])
        v2 += [(u["v2_version"], u["v2_scored_at"]) for u in field]
        races.append(dict(
            id=f"{date}_{int(r['race_no']):02d}", date=date, no=int(r["race_no"]), venue=r["venue"],
            course=r["course"], surface=r["surface"], going=r["going"], distance=D, cls=cls,
            name=r["race_name"], off=r["off_time"], sections=list(section_lengths(D)),
            template_key=f"{r['venue']}_{r['surface']}_{D}", cost_group=cost_group(r["venue"], r["surface"], D),
            par=_par(stds, r["venue"], r["surface"], D, cls),
            runners=[dict(no=int(h["horse_no"]), name=h["horse_name"], draw=int(h["draw"]), jockey=h["jockey"],
                          trainer=h["trainer"], wt=None if h["actual_weight"] is None else int(h["actual_weight"]),
                          rating=None if h["rating"] is None else int(h["rating"]), gear=h["gear"],
                          p_win=_r(h["p_win"], 5), p_place=_r(h["p_place"], 5),
                          pred_early=_r(h["pred_early"], 4), p_lead=_r(h["p_lead"], 4),
                          exp_lane=_r(h["exp_lane"], 3), p_nocover=_r(h["p_nocover"], 4),
                          slow_rate=_r(h["slow_rate"], 4), n_prior=int(h["n_prior"]),
                          prod_style=h["prod_style"], prod_band=h["prod_band"]) for h in field],
            gone=bool(r["gone"]), versions=dict(v2=field[0]["v2_version"], shape=field[0]["shape_version"])))
    meta = dict(card=date, races=len(races), runners=sum(len(r["runners"]) for r in races),
                v2=max(v2)[0] if v2 else None, v2_scored_at=max((s for _, s in v2), default=None))
    return dict(meta=meta, races=races, incomplete=incomplete)


def race_inputs(card_doc: dict) -> dict:
    """The card exactly as node reads it: the lab's keys, nothing of ours."""
    keep = ("id", "date", "no", "venue", "course", "surface", "going", "distance", "cls", "name", "off",
            "sections", "template_key", "cost_group", "par", "runners")
    return dict(meta=card_doc["meta"], races=[{k: r[k] for k in keep} for r in card_doc["races"]])


def fingerprint(race_doc: dict, *, versions: dict, settings: dict) -> str:
    """sha256 of a race's rounded inputs, the four versions and the settings."""
    fp = dict(race=[race_doc["id"], race_doc["venue"], race_doc["surface"], race_doc["course"],
                    race_doc["distance"], race_doc["cls"]],
              field=[[h["no"], h["name"], h["draw"], h["jockey"], round(h["p_win"], 2),
                      *(round(h[k], 2) for k in ("pred_early", "p_lead", "exp_lane", "p_nocover", "slow_rate"))]
                     for h in race_doc["runners"]],
              versions=versions, settings=settings)
    return hashlib.sha256(json.dumps(fp, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ── the pages ─────────────────────────────────────────────────────────────────────────────────
_TABLES: dict[tuple, dict] = {}


def _tables(conn: Connection, as_of: str, version: str) -> dict | None:
    """A card date's tables, parsed once per (database, as_of, version)."""
    key = (db_file(conn), as_of, version)
    if key[0] and key in _TABLES:
        return _TABLES[key]
    row = store.tables_row(conn, as_of, version)
    if row is None:
        return None
    if key[0]:
        _TABLES.clear()                      # one card date at a time is all a page asks for
        _TABLES[key] = row["tables"]
    return row["tables"]


def _changed(then: dict, now: dict | None) -> list[str]:
    """What has moved since the simulation: the declared field, or v2."""
    if now is None:
        return ["the race's inputs are no longer complete"]
    out = []
    a = {(h["no"], h["draw"], h["jockey"]) for h in then["runners"]}
    b = {(h["no"], h["draw"], h["jockey"]) for h in now["runners"]}
    if a != b:
        out.append("the declared field or a rider has changed")
    pw = {h["no"]: h["p_win"] for h in now["runners"]}
    if any(h["no"] in pw and abs(round(pw[h["no"]], 2) - round(h["p_win"], 2)) >= 0.01 for h in then["runners"]):
        out.append("v2's chances have moved")
    return out


def _actual(conn: Connection, date: str, race_no: int, runners: list[dict]) -> dict | None:
    """After the race: the pace it was run at, who led at the first call, the first three, and any
    simulated horse that did not run."""
    ran = [dict(r) for r in conn.execute(
        "SELECT horse_no, horse_name, place, running_positions FROM runners WHERE race_date = ? AND race_no = ? "
        "AND (win_odds > 0 OR place > 0)", (date, race_no))]
    if not any(r["place"] is not None for r in ran):
        return None
    band = conn.execute("SELECT band FROM race_tempo WHERE race_date = ? AND race_no = ?", (date, race_no)).fetchone()
    first = {r["horse_no"]: int(r["running_positions"].split()[0]) for r in ran
             if (r["running_positions"] or "").split()[:1] and r["running_positions"].split()[0].isdigit()}
    lead = next((n for n, p in first.items() if p == 1), None)
    sim = {h["no"]: h for h in runners}
    names = {r["horse_no"]: r["horse_name"] for r in ran}
    return dict(band=band[0] if band else None,
                leader=None if lead is None else dict(no=lead, name=names[lead], p_lead=(sim.get(lead) or {}).get("p_lead")),
                result=[dict(no=r["horse_no"], name=r["horse_name"], place=r["place"])
                        for r in sorted((r for r in ran if r["place"] is not None), key=lambda r: r["place"])[:3]],
                missing=[dict(no=h["no"], name=h["name"]) for h in runners if names.get(h["no"]) != h["name"]])


def race(date: str, race_no: int, *, conn: Connection | None = None) -> dict | None:
    """One race's newest simulation, or None when it has none."""
    conn = conn or get_conn()
    sim = store.latest_sim(conn, date, race_no)
    if sim is None:
        return None
    res = json.loads(sim["result_text"])
    rc = next(r for r in sim["inputs"]["card"]["races"] if r["no"] == race_no)
    now = next((r for r in card_inputs(conn, date)["races"] if r["no"] == race_no), None)
    T = _tables(conn, sim["tables_as_of"], sim["tables_version"]) or {}
    v2_now = {h["no"]: h["p_win"] for h in (now or {}).get("runners", [])}
    cols = {r["horse_no"]: r for r in conn.execute(
        "SELECT * FROM runner_sim WHERE race_date = ? AND race_no = ? AND fingerprint = ?",
        (date, race_no, sim["fingerprint"]))}
    runners = [dict(no=h["no"], name=h["name"], draw=h["draw"], jockey=h["jockey"],
                    v2_win_now=v2_now.get(h["no"]),
                    **{k: cols[h["no"]][k] for k in store.SIM_RUNNER_COLUMNS}) for h in rc["runners"]]
    templates = T.get("templates", {}).get(rc["template_key"], [])
    used = set(res["templates"])
    gone = store.gone(conn, date, race_no)
    return dict(
        # after the off the row is the record: nothing has "changed since", it is never simulated again
        meta=dict(simulated_at=sim["simulated_at"], fingerprint=sim["fingerprint"], cpu_seconds=sim["cpu_seconds"],
                  engine_version=sim["engine_version"], tables_as_of=sim["tables_as_of"],
                  tables_version=sim["tables_version"], v2_version=sim["v2_version"],
                  shape_version=sim["shape_version"], settings=sim["settings"], borrowed=len(used),
                  changed_since=[] if gone else _changed(rc, now), gone=gone),
        race=rc, runners=runners, summary=res["summary"], engine=res["engine"], coupling=res["coupling"],
        coupled_check=res["coupled_check"], anchor=res["anchor"], settle_sd=res["settle_sd"],
        templates=[t for t in templates if t["id"] in used],
        tables={k: T[k] for k in ("calls", "traffic", "slow", "noise", "contacts") if k in T}
        | {"costs": {rc["cost_group"]: T.get("costs", {}).get(rc["cost_group"])}},
        actual=_actual(conn, date, race_no, runners))


def card(date: str, *, conn: Connection | None = None) -> dict:
    """A meeting's races: simulated or not, changed since or not, the pace mix, the likeliest leader."""
    conn = conn or get_conn()
    doc = card_inputs(conn, date)
    sims = {s["race_no"]: s for s in store.sim_rows(conn, date)}
    out = []
    for r in doc["races"]:
        s = sims.get(r["no"])
        row = dict(no=r["no"], off=r["off"], distance=r["distance"], cls=r["cls"], gone=r["gone"],
                   simulated=s is not None)
        if s is not None:
            full = store.latest_sim(conn, date, r["no"])
            res = json.loads(full["result_text"])
            then = next(x for x in full["inputs"]["card"]["races"] if x["no"] == r["no"])
            lead = max(res["summary"]["runners"], key=lambda x: x["lead"])
            row.update(simulated_at=s["simulated_at"], changed_since=[] if r["gone"] else _changed(then, r),
                       pace=res["summary"]["pace"],
                       likeliest_leader=dict(no=lead["no"], p=lead["lead"],
                                             name=next(h["name"] for h in then["runners"] if h["no"] == lead["no"])))
        out.append(row)
    for r in doc["incomplete"]:
        out.append(dict(no=r["no"], gone=r["gone"], simulated=r["no"] in sims, incomplete=r["why"]))
    return dict(date=date, races=sorted(out, key=lambda x: x["no"]))
