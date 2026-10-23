"""The simulation's record (racesim-SPEC §9.3): each race's simulation as it stood at the off, scored
once the race was run. A port of the lab's race-sim/score_19oct.py, reading the tables instead of the
lab's files.

  P(lead)        log loss of the horse that led at the first call     against 1 in the field
                 and whose single top pick led                        against the Speed Map's
  first call     mean miss as a share of the field; front and back    against mid-field for everyone,
                 third, Brier; the 80% band's cover                   and the Speed Map's miss
  3+ wide, no    Brier, truth from the Comments on Running (the       against the season before's rate
  cover, held up trip table, runs with a comment)
  pace           the share of the simulated races at the band the     against how often a race was
                 race was run at                                      run at that band
  win, top 3     log loss / Brier                                     against v2: they should tie

THE RECORD ROW is the newest simulation made before the race's off (its pool shut, else the card's
off time): a race is never simulated after its off, so that is its last row. A horse simulated that
did not run, or a number a different horse ran under, is left out and the rest renormalised.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
from collections import defaultdict
from typing import Any

from hkrd.store.connect import Connection, db_file, get_conn

__all__ = ["record", "score_race", "summarise"]

_HKT = dt.timezone(dt.timedelta(hours=8))
_BANDS = ((0, .05), (.05, .15), (.15, .35), (.35, 1.01))


def _mean(v: list[float]) -> float | None:
    return sum(v) / len(v) if v else None


def _r(v: float | None, n: int = 3) -> float | None:
    return None if v is None else round(v, n)


def score_race(sim: dict, ran: list[dict], *, base: dict[str, float], band: str | None,
               band_base: dict[str, float], straight_course: bool) -> dict[str, list]:
    """One race's scored rows. `sim`: {runners: [{no, name, p_lead, sim_win, v2_win, sim_top3, v2_place,
    first_call_pos, p_front_third, p_back_third, p_nocover, p_wide3, p_held, first_call_dist,
    speed_map}], pace}; `ran`: who ran -- {no, name, first, place, comment, wide3, nocover, held}."""
    by_no = {h["no"]: h for h in ran}
    keep = [r for r in sim["runners"] if r["no"] in by_no and by_no[r["no"]]["name"] == r["name"]]
    out: dict[str, list] = defaultdict(list)
    if not keep:
        return out
    n_act, n_sim = len(ran), len(sim["runners"])
    s_lead, s_win, s_v2 = (sum(r[k] for r in keep) for k in ("p_lead", "sim_win", "v2_win"))
    leader = next((h["no"] for h in ran if h["first"] == 1), None)
    winner = next((h["no"] for h in ran if h["place"] == 1), None)
    kept = {r["no"]: r for r in keep}
    for r in keep:
        out["lead_rows"].append((r["p_lead"] / s_lead, int(r["no"] == leader)))
    if leader in kept:
        out["lead_ll"].append(-math.log(max(kept[leader]["p_lead"] / s_lead, 1e-6)))
        out["lead_u"].append(math.log(n_act))
        top = max(keep, key=lambda r: r["p_lead"])["no"]
        out["top_led"].append(int(top == leader))
        sm = [r for r in keep if r["speed_map"] is not None]
        if sm:
            out["top_led_speed_map"].append(int(min(sm, key=lambda r: r["speed_map"])["no"] == leader))
    if winner in kept:
        out["win_ll"].append(-math.log(max(kept[winner]["sim_win"] / s_win, 1e-6)))
        out["win_ll_v2"].append(-math.log(max(kept[winner]["v2_win"] / s_v2, 1e-6)))
        out["win_u"].append(math.log(n_act))
    for r in keep:
        h = by_no[r["no"]]
        if h["first"] is not None:
            a = (h["first"] - 1) / max(1, n_act - 1)
            e = (r["first_call_pos"] - 1) / max(1, n_sim - 1)
            out["pos_miss"].append(abs(a - e))
            out["pos_naive"].append(abs(a - 0.5))
            if r["speed_map"] is not None:
                out["pos_speed_map"].append(abs(a - r["speed_map"]))
            if r.get("first_call_dist"):
                cum, lo, hi = 0.0, None, None
                for k, q in enumerate(r["first_call_dist"]):
                    cum += q
                    lo = k if lo is None and cum >= 0.1 else lo
                    hi = k if hi is None and cum >= 0.9 else hi
                out["band80"].append(int(lo / max(1, n_sim - 1) - 1e-9 <= a <= hi / max(1, n_sim - 1) + 1e-9))
            out["front"].append(((r["p_front_third"] - (a < 1 / 3)) ** 2, (1 / 3 - (a < 1 / 3)) ** 2))
            out["back"].append(((r["p_back_third"] - (a >= 2 / 3)) ** 2, (1 / 3 - (a >= 2 / 3)) ** 2))
        if h["place"] is not None:
            t3 = int(h["place"] <= 3)
            out["top3"].append(((r["sim_top3"] - t3) ** 2, (r["v2_place"] - t3) ** 2, (3 / n_act - t3) ** 2))
        if h["comment"]:
            out["wide3"].append((r["p_wide3"], h["wide3"], base["wide3"]))
            out["nocover"].append((r["p_nocover"], h["nocover"], base["nocover"]))
            if not straight_course:
                out["held"].append((r["p_held"], h["held"], base["held"]))
    if band:
        out["pace"].append((sim["pace"].get(band, 0.0), band_base.get(band, 0.0)))
    return out


def _brier(rows: list[tuple]) -> dict | None:
    """Brier against the rate, horse by horse. `skill` only when the gain over the rate is more than
    twice its standard error: a few thousandths on one meeting is not a reading of anything."""
    if not rows:
        return None
    n = len(rows)
    gains = [(b - y) ** 2 - (p - y) ** 2 for p, y, b in rows]
    gain = sum(gains) / n
    se = math.sqrt(sum((g - gain) ** 2 for g in gains) / (n - 1) / n) if n > 1 else None
    return dict(horses=n, said=int(sum(y for _, y, _ in rows)), brier=_r(sum((p - y) ** 2 for p, y, _ in rows) / n, 4),
                base_rate_brier=_r(sum((b - y) ** 2 for _, y, b in rows) / n, 4), mean_p=_r(sum(p for p, _, _ in rows) / n),
                gain=_r(gain, 4), se=_r(se, 4), skill=bool(se and gain > 2 * se))


def summarise(rows: dict[str, list]) -> dict:
    """The measures of §9.3 over any set of scored races."""
    cal = []
    for lo, hi in _BANDS:
        xs = [(p, y) for p, y in rows["lead_rows"] if lo <= p < hi]
        if xs:
            cal.append(dict(band=f"{lo:.2f}-{min(hi, 1):.2f}", horses=len(xs), mean_p=_r(_mean([p for p, _ in xs])),
                            led=sum(y for _, y in xs)))
    f, b, t = rows["front"], rows["back"], rows["top3"]
    return dict(
        races=len(rows["lead_u"]) or len(rows["win_u"]), horses=len(rows["pos_miss"]),
        p_lead=dict(log_loss=_r(_mean(rows["lead_ll"])), uniform=_r(_mean(rows["lead_u"])),
                    top_pick_led=_r(_mean(rows["top_led"])), speed_map_top_pick_led=_r(_mean(rows["top_led_speed_map"])),
                    calibration=cal),
        first_call=dict(mean_miss=_r(_mean(rows["pos_miss"])), mid_field=_r(_mean(rows["pos_naive"])),
                        speed_map=_r(_mean(rows["pos_speed_map"])), band80_covered=_r(_mean(rows["band80"])),
                        front_third_brier=_r(_mean([x for x, _ in f]), 4), front_third_base=_r(_mean([y for _, y in f]), 4),
                        back_third_brier=_r(_mean([x for x, _ in b]), 4), back_third_base=_r(_mean([y for _, y in b]), 4)),
        wide3=_brier(rows["wide3"]), nocover=_brier(rows["nocover"]), held=_brier(rows["held"]),
        pace=dict(races=len(rows["pace"]), sim_share_at_the_band=_r(_mean([s for s, _ in rows["pace"]])),
                  base_share_at_the_band=_r(_mean([x for _, x in rows["pace"]]))),
        win=dict(log_loss_sim=_r(_mean(rows["win_ll"])), log_loss_v2=_r(_mean(rows["win_ll_v2"])),
                 uniform=_r(_mean(rows["win_u"]))),
        top3=dict(brier_sim=_r(_mean([x for x, _, _ in t]), 4), brier_v2_place=_r(_mean([y for _, y, _ in t]), 4),
                  base=_r(_mean([z for _, _, z in t]), 4)))


# ── from the tables ───────────────────────────────────────────────────────────────────────────
def _off(r: dict) -> str | None:
    """The race's off, UTC ISO: when its pool shut, else the card's off time."""
    if r["closed_at"]:
        return r["closed_at"]
    if r["off_time"]:
        t = dt.datetime.fromisoformat(f"{r['race_date']}T{r['off_time']}:00").replace(tzinfo=_HKT)
        return t.astimezone(dt.timezone.utc).isoformat()
    return None


_CACHE: dict[tuple, dict] = {}


def record(*, conn: Connection | None = None) -> dict:
    """Every run race with a simulation made before its off, scored; by meeting and the season."""
    conn = conn or get_conn()
    head = conn.execute("SELECT count(*), max(simulated_at) FROM race_sim").fetchone()
    settled = conn.execute("SELECT max(race_date) FROM runners WHERE place IS NOT NULL").fetchone()[0]
    key = (db_file(conn), head[0], head[1], settled)
    if key[0] and key in _CACHE:
        return _CACHE[key]
    races = [dict(r) for r in conn.execute("""
        SELECT a.race_date, a.race_no, a.venue, a.surface, a.distance, a.off_time, m.closed_at, t.band
          FROM races a LEFT JOIN market_close m USING (race_date, race_no) LEFT JOIN race_tempo t USING (race_date, race_no)
         WHERE EXISTS (SELECT 1 FROM race_sim s WHERE s.race_date = a.race_date AND s.race_no = a.race_no)
           AND EXISTS (SELECT 1 FROM runners u WHERE u.race_date = a.race_date AND u.race_no = a.race_no AND u.place IS NOT NULL)
         ORDER BY a.race_date, a.race_no""")]
    by_meeting: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    season: dict[str, list] = defaultdict(list)
    bases: dict[int, tuple] = {}
    for rc in races:
        off = _off(rc)
        sim = conn.execute("SELECT * FROM race_sim WHERE race_date = ? AND race_no = ?"
                           + (" AND simulated_at < ?" if off else "") + " ORDER BY simulated_at DESC LIMIT 1",
                           (rc["race_date"], rc["race_no"], *([off] if off else []))).fetchone()
        if sim is None:
            continue
        yr = int(rc["race_date"][:4]) - (int(rc["race_date"][5:7]) < 8)
        if yr not in bases:
            bases[yr] = _bases(conn, yr - 1)
        rows = score_race(_sim_rows(conn, dict(sim)), _ran(conn, rc["race_date"], rc["race_no"]),
                          base=bases[yr][0], band=rc["band"], band_base=bases[yr][1],
                          straight_course=rc["venue"] == "ST" and rc["surface"] == "Turf" and rc["distance"] == 1000)
        for k, v in rows.items():
            by_meeting[rc["race_date"]][k] += v
            season[k] += v
    out = dict(meetings=[dict(date=d, **summarise(rows)) for d, rows in sorted(by_meeting.items(), reverse=True)],
               season=summarise(season) if by_meeting else None)
    if key[0]:
        _CACHE.clear()
        _CACHE[key] = out
    return out


def _bases(conn: Connection, season: int) -> tuple[dict[str, float], dict[str, float]]:
    """The season before's rates: 3+ wide, no cover, held up (runs with a comment) and each pace band."""
    lo, hi = f"{season}-08-01", f"{season + 1}-08-01"
    r = conn.execute("SELECT avg(wide3_pre), avg(nocover_pre), avg(traffic_straight) FROM runner_trip "
                     "WHERE has_comment = 1 AND race_date >= ? AND race_date < ?", (lo, hi)).fetchone()
    bands = conn.execute("SELECT band, count(*) FROM race_tempo WHERE race_date >= ? AND race_date < ? GROUP BY band",
                         (lo, hi)).fetchall()
    n = sum(c for _, c in bands) or 1
    return (dict(wide3=r[0] or 0.0, nocover=r[1] or 0.0, held=r[2] or 0.0), {b: c / n for b, c in bands})


def _sim_rows(conn: Connection, sim: dict) -> dict:
    result = json.loads(gzip.decompress(sim["result_gz"]).decode("utf-8"))
    inputs = json.loads(gzip.decompress(sim["inputs_gz"]).decode("utf-8"))
    rc = next(r for r in inputs["card"]["races"] if r["no"] == sim["race_no"])
    names = {h["no"]: h["name"] for h in rc["runners"]}
    dist = {h["no"]: h.get("first_call_dist") for h in result["summary"]["runners"]}
    sm = {r[0]: r[1] for r in conn.execute("SELECT horse_no, settle FROM runner_projection WHERE race_date = ? "
                                           "AND race_no = ?", (sim["race_date"], sim["race_no"]))}
    runners = [dict(dict(r), no=r["horse_no"], name=names.get(r["horse_no"]), first_call_dist=dist.get(r["horse_no"]),
                    speed_map=sm.get(r["horse_no"])) for r in conn.execute(
        "SELECT * FROM runner_sim WHERE race_date = ? AND race_no = ? AND fingerprint = ? ORDER BY horse_no",
        (sim["race_date"], sim["race_no"], sim["fingerprint"]))]
    return dict(runners=runners, pace=result["summary"]["pace"])


def _ran(conn: Connection, date: str, race_no: int) -> list[dict]:
    out = []
    for r in conn.execute("""
        SELECT u.horse_no, u.horse_name, u.place, u.running_positions, t.has_comment, t.wide3_pre,
               t.nocover_pre, t.traffic_straight
          FROM runners u LEFT JOIN runner_trip t USING (race_date, race_no, horse_no)
         WHERE u.race_date = ? AND u.race_no = ? AND (u.win_odds > 0 OR u.place > 0)""", (date, race_no)):
        pos = [int(p) for p in (r["running_positions"] or "").split() if p.isdigit()]
        out.append(dict(no=r["horse_no"], name=r["horse_name"], first=pos[0] if pos else None, place=r["place"],
                        comment=bool(r["has_comment"]), wide3=r["wide3_pre"] or 0, nocover=r["nocover_pre"] or 0,
                        held=r["traffic_straight"] or 0))
    return out
