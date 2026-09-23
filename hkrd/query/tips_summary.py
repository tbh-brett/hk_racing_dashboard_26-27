"""One meeting, summarised: who backs which horse, and what the prices say.

Brett, 2026-09-23: "everything summarised — which race and pick, and the
sources and pundits supporting it", horses with the most support first, the
jockey interviews highlighted, and the fixed odds beside the tote.

WHAT COUNTS AS SUPPORT, per source, and nothing else does:

  pick         a tipster's selection — Ladbrokes' top four, the RTW pundit's
               numbers, 譚朗蔚's picks in the Fact Check tail
  featured     a horse 賽馬Fact Check makes a segment of. That show is a list
               of horses worth following; being featured IS the endorsement
  connections  the trainer or jockey in a Racing To Win interview

全方位Bryan is not counted: his one quote on 23 Sep was about a trip being
too SHORT for the horse, and a mention with no way to read its direction
cannot be counted as support. The RTW preview's closing lines are the words
under its picks, not a second vote.

THE PRICES. A fixed price is locked when it is taken; the tote pays its final
dividend whatever it showed. So the comparison that means something is a
fixed price against the tote's FAIR price — its win odds with the takeout
removed (`derive.probability.devig`). Two readings, both shown:

  ev_fixed   what the Ladbrokes price is worth if the HKJC tote has the
             chance right:  fair_tote × fixed − 1
  ev_tote    what the tote price is worth if Ladbrokes has it right:
             fair_fixed × tote − 1

The tote is the bigger and local market, so ev_fixed is the headline. Both
are snapshots: the tote keeps moving until the off (AGENTS.md: the money
arrives in the last ten minutes), and every figure carries its capture time.
The original spec warned that ordering by consensus points at short prices;
the price beside each pick is the counterweight, not a footnote.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from hkrd.derive.probability import devig
from hkrd.query import market
from hkrd.store.connect import Connection, get_conn

__all__ = ["summary", "EDGE_AT"]

EDGE_AT = 0.05            # an edge worth listing: +5% or better
_HK = dt.timezone(dt.timedelta(hours=8))
_LABEL = {"factcheck": "賽馬Fact Check", "rtw_interview": "Racing To Win interview",
          "rtw_preview": "Racing To Win preview", "ladbrokes": "Ladbrokes",
          "bryan": "全方位Bryan", "oncc": "on.cc", "threads": "Horse Detective"}


def _hk(stamp: str | None) -> str | None:
    """A capture time in Hong Kong time. HKJC captures are stored naive in
    HKT; a bookmaker's carries its offset."""
    if not stamp:
        return None
    when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if when.tzinfo is not None:
        when = when.astimezone(_HK).replace(tzinfo=None)
    return when.isoformat(timespec="minutes")


def _fair(odds: dict[int, float | None]) -> dict[int, float]:
    """horse -> fair win probability, from one market's win prices."""
    priced = [(no, o) for no, o in odds.items() if o and o > 1.0]
    if len(priced) < 2:
        return {}
    probs = devig([o for _, o in priced])
    return {no: float(p) for (no, _), p in zip(priced, probs)}


def _overround(odds: dict[int, float | None]) -> float | None:
    priced = [o for o in odds.values() if o and o > 1.0]
    return round(100 * (sum(1 / o for o in priced) - 1), 1) if priced else None


def _odds_block(no: int, tote: dict, fixed: dict, fair_t: dict,
                fair_f: dict) -> dict[str, Any]:
    t, f = tote.get(no) or {}, fixed.get(no) or {}
    tw, fw = t.get("win_odds"), f.get("win")
    pt, pf = fair_t.get(no), fair_f.get(no)
    ev_fixed = round(pt * fw - 1, 3) if pt and fw else None
    ev_tote = round(pf * tw - 1, 3) if pf and tw else None
    best = None
    if tw and fw:
        best = "ladbrokes" if fw > tw else "tote" if tw > fw else "same"
    return {"tote_win": tw, "tote_place": t.get("place_odds"),
            "fixed_win": fw, "fixed_place": f.get("place"),
            "fair_tote_pct": round(100 * pt, 1) if pt else None,
            "fair_fixed_pct": round(100 * pf, 1) if pf else None,
            "ev_fixed_pct": round(100 * ev_fixed, 1) if ev_fixed is not None else None,
            "ev_tote_pct": round(100 * ev_tote, 1) if ev_tote is not None else None,
            "pays_more": best}


def summary(date: str, *, conn: Connection | None = None) -> dict[str, Any]:
    own = conn is None
    conn = conn or get_conn()
    try:
        return _summary(conn, date)
    finally:
        if own:
            conn.close()


def _summary(conn: Connection, date: str) -> dict[str, Any]:
    races = {r["race_no"]: dict(r) for r in conn.execute(
        "SELECT race_no, venue, distance, race_class, off_time FROM races "
        "WHERE race_date = ? ORDER BY race_no", (date,))}
    runners: dict[tuple[int, int], dict] = {}
    for r in conn.execute(
            "SELECT u.race_no, u.horse_no, u.horse_name, u.jockey, u.trainer, "
            "       u.draw, z.name_zh FROM runners u "
            "LEFT JOIN horse_name_zh z ON z.horse_name = u.horse_name "
            "WHERE u.race_date = ?", (date,)):
        runners[(r["race_no"], r["horse_no"])] = dict(r)

    backed: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for s in conn.execute(
            "SELECT source, tipster, race_no, horse_no, pick_rank, note, url, "
            "       caption_kind FROM tipster_selection WHERE race_date = ?",
            (date,)):
        # `words` is always a list of {text, en, t, url}, whatever the
        # source: a pick's reason is one entry, an interview is one per
        # answer. One shape, so a page never has to ask which it got.
        backed[(s["race_no"], s["horse_no"])].append({
            "kind": "pick", "source": s["source"],
            "source_label": _LABEL.get(s["source"], s["source"]),
            "who": s["tipster"], "role": None, "rank": s["pick_rank"],
            "words": ([{"text": s["note"], "en": None, "t": None,
                        "url": s["url"]}] if s["note"] else []),
            "url": s["url"], "heard": s["caption_kind"] == "asr"})

    interviews: dict[int, list[dict]] = defaultdict(list)
    spoken: dict[tuple, dict] = {}
    for q in conn.execute(
            "SELECT source, speaker, role, race_no, horse_no, quote, quote_en, "
            "       t_start, url FROM connections_quote "
            "WHERE race_date = ? AND horse_no IS NOT NULL "
            "  AND source IN ('factcheck', 'rtw_interview') "
            "ORDER BY race_no, horse_no, t_start", (date,)):
        key = (q["race_no"], q["horse_no"], q["source"], q["speaker"])
        entry = spoken.get(key)
        if entry is None:
            kind = "connections" if q["source"] == "rtw_interview" else "featured"
            entry = spoken[key] = {
                "kind": kind, "source": q["source"],
                "source_label": _LABEL.get(q["source"], q["source"]),
                "who": q["speaker"], "role": q["role"], "rank": None,
                "words": [], "url": q["url"], "heard": False}
            backed[(q["race_no"], q["horse_no"])].append(entry)
            if kind == "connections":
                interviews[q["race_no"]].append({
                    "horse_no": q["horse_no"], "speaker": q["speaker"],
                    "role": q["role"], "words": entry["words"]})
        entry["words"].append({"text": q["quote"], "en": q["quote_en"],
                               "t": q["t_start"], "url": q["url"]})

    fixed = defaultdict(dict)
    fixed_at = None
    for f in conn.execute(
            "SELECT f.race_no, f.horse_no, f.win, f.place, f.captured_at "
            "FROM fixed_odds f JOIN (SELECT race_no, max(captured_at) m "
            "  FROM fixed_odds WHERE race_date = ? AND bookmaker = 'ladbrokes' "
            "  GROUP BY race_no) l ON l.race_no = f.race_no AND l.m = f.captured_at "
            "WHERE f.race_date = ? AND f.bookmaker = 'ladbrokes' "
            "  AND f.scratched = 0", (date, date)):
        fixed[f["race_no"]][f["horse_no"]] = dict(f)
        fixed_at = max(fixed_at or "", f["captured_at"])

    # How much money is in each win pool at the capture the prices came
    # from. A tote price out of a thin pool is a real number that means
    # little (AGENTS.md), and an "edge" against it is mostly that.
    pools = {r["race_no"]: r["turnover"] for r in conn.execute(
        "SELECT t.race_no, t.turnover FROM odds_pool_turnover t "
        "JOIN (SELECT race_no, max(captured_at) m FROM odds_pool_turnover "
        "      WHERE race_date = ? AND pool = 'WIN' GROUP BY race_no) l "
        "  ON l.race_no = t.race_no AND l.m = t.captured_at "
        "WHERE t.race_date = ? AND t.pool = 'WIN'", (date, date))}

    out_races, edges, tote_at = [], [], None
    for race_no, race in races.items():
        tote = market.live_prices(date, race_no, conn=conn)
        for p in tote.values():
            tote_at = max(tote_at or "", p.get("captured_at") or "")
        fair_t = _fair({n: p["win_odds"] for n, p in tote.items()})
        fair_f = _fair({n: f["win"] for n, f in fixed[race_no].items()})
        numbers = {n for (r, n) in runners if r == race_no}

        picks = []
        for no in numbers:
            who = backed.get((race_no, no), [])
            odds = _odds_block(no, tote, fixed[race_no], fair_t, fair_f)
            horse = runners[(race_no, no)]
            if who:
                picks.append({
                    "horse_no": no, "horse_name": horse["horse_name"],
                    "name_zh": horse["name_zh"], "jockey": horse["jockey"],
                    "trainer": horse["trainer"], "draw": horse["draw"],
                    "supporters": len({(b["source"], b["who"]) for b in who}),
                    "top_picks": sum(1 for b in who if b["rank"] == 1),
                    "interviewed": any(b["kind"] == "connections" for b in who),
                    "backed_by": sorted(who, key=_by_weight), "odds": odds})
            for side, ev in (("ladbrokes", odds["ev_fixed_pct"]),
                             ("tote", odds["ev_tote_pct"])):
                if ev is not None and ev >= 100 * EDGE_AT:
                    edges.append({
                        "race_no": race_no, "horse_no": no,
                        "horse_name": horse["horse_name"], "bet_at": side,
                        "price": odds["fixed_win"] if side == "ladbrokes"
                        else odds["tote_win"],
                        "fair_pct": odds["fair_tote_pct"] if side == "ladbrokes"
                        else odds["fair_fixed_pct"],
                        "ev_pct": ev,
                        "supporters": len({(b["source"], b["who"])
                                           for b in who})})
        picks.sort(key=lambda p: (-p["supporters"], -p["top_picks"],
                                  min((b["rank"] or 9) for b in p["backed_by"]),
                                  p["horse_no"]))
        out_races.append({
            "race_no": race_no, "distance": race["distance"],
            "race_class": race["race_class"], "off_time": race["off_time"],
            "tote_win_pool": pools.get(race_no),
            "overround": {"tote": _overround({n: p["win_odds"]
                                              for n, p in tote.items()}),
                          "ladbrokes": _overround({n: f["win"] for n, f
                                                   in fixed[race_no].items()})},
            "interviews": [dict(i, horse_name=runners[(race_no, i["horse_no"])]
                                ["horse_name"]) for i in interviews[race_no]],
            "picks": picks})

    edges.sort(key=lambda e: -e["ev_pct"])
    return {"race_date": date,
            "venue": next((r["venue"] for r in races.values() if r["venue"]),
                          None),
            "captured": {"tote": _hk(tote_at), "ladbrokes": _hk(fixed_at)},
            "sources": sorted({b["source_label"] for who in backed.values()
                               for b in who}),
            "races": out_races, "edges": edges}


def _by_weight(b: dict) -> tuple:
    """Connections first, then picks by rank, then featured."""
    order = {"connections": 0, "pick": 1, "featured": 2}
    return (order.get(b["kind"], 3), b["rank"] or 9, b["source"])
