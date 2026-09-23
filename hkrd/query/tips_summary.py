"""One meeting, summarised: who backs which horse, and what the prices say.

Brett, 2026-09-23: "everything summarised — which race and pick, and the
sources and pundits supporting it", horses with the most support first, the
jockey interviews highlighted, and the fixed odds beside the tote.

WHAT COUNTS AS SUPPORT, per source, and nothing else does:

  pick         a tipster's selection — Racing & Sports' top four, the RTW
               pundit's numbers, 譚朗蔚's picks in the Fact Check tail
  featured     a horse 賽馬Fact Check makes a segment of. That show is a list
               of horses worth following; being featured IS the endorsement
  connections  the trainer or jockey in a Racing To Win interview

Racing & Sports is ONE source however many bookmakers print it: Ladbrokes'
race comment and Sportsbet's "Expert Tips by Racing & Sports" are the same
words and the same four horses (`ingest.racing_sports`). Its line on every
runner is form, not support, and travels as `form`.

全方位Bryan is not counted: his one quote on 23 Sep was about a trip being
too SHORT for the horse, and a mention with no way to read its direction
cannot be counted as support. The RTW preview's closing lines are the words
under its picks, not a second vote.

THE PRICES. A fixed price is locked when it is taken; the tote pays its final
dividend whatever it showed. So the comparison that means something is a
price against the OTHER market's fair chance — its odds with the margin
removed (`derive.probability.devig`):

  a bookmaker's value   its fixed win price × the tote's fair chance − 1:
                        what the price is worth if the HKJC tote is right
  the tote's value      the tote win price × the bookmakers' fair chance − 1,
                        their chance being the mean over the books pricing
                        the horse: what the tote is worth if they are right

Both are snapshots: the tote keeps moving until the off (AGENTS.md: the money
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

__all__ = ["summary", "EDGE_AT", "BOOKS"]

EDGE_AT = 0.05            # an edge worth listing: +5% or better
BOOKS = ("ladbrokes", "sportsbet", "unibet")
_HK = dt.timezone(dt.timedelta(hours=8))
_RS, _RS_FORM = "racing_sports", "racing_sports_form"
_LABEL = {"factcheck": "賽馬Fact Check", "rtw_interview": "Racing To Win interview",
          "rtw_preview": "Racing To Win preview", _RS: "Racing & Sports",
          _RS_FORM: "Racing & Sports", "bryan": "全方位Bryan", "oncc": "on.cc",
          "threads": "Horse Detective"}
# The sources the Briefing expects on every meeting, in the order it lists
# them. One that has not published yet is listed as pending, not left out.
EXPECTED = (_RS, "rtw_preview", "rtw_interview", "factcheck")


def _hk(stamp: str | None) -> str | None:
    """A capture time in Hong Kong time. HKJC captures are stored naive in
    HKT; a bookmaker's or a payload's carries its offset."""
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


def _pct(p: float | None) -> float | None:
    return round(100 * p, 1) if p is not None else None


def _odds_block(no: int, tote: dict, fair_t: dict, books: dict[str, dict],
                fair_b: dict[str, dict]) -> dict[str, Any]:
    """Every market's price for one runner, and what each is worth if the
    other side has the chance right."""
    t = tote.get(no) or {}
    out: dict[str, Any] = {}
    fairs = [fair_b[b][no] for b in books if no in fair_b[b]]
    consensus = sum(fairs) / len(fairs) if fairs else None
    tw, pt = t.get("win_odds"), fair_t.get(no)
    out["tote"] = {"win": tw, "place": t.get("place_odds"), "fair_pct": _pct(pt),
                   "value_pct": _pct(consensus * tw - 1)
                   if consensus and tw else None}
    for b, prices in books.items():
        f = prices.get(no) or {}
        fw = f.get("win")
        out[b] = {"win": fw, "place": f.get("place"),
                  "fair_pct": _pct(fair_b[b].get(no)),
                  "value_pct": _pct(pt * fw - 1) if pt and fw else None}
    wins = {k: v["win"] for k, v in out.items() if v["win"]}
    best = max(wins.values()) if len(wins) > 1 else None
    out["pays_most"] = (next(k for k, v in wins.items() if v == best)
                        if best and list(wins.values()).count(best) == 1
                        else None)
    out["books_fair_pct"] = _pct(consensus)
    return out


def summary(date: str, *, conn: Connection | None = None) -> dict[str, Any]:
    own = conn is None
    conn = conn or get_conn()
    try:
        return _summary(conn, date)
    finally:
        if own:
            conn.close()


def _fixed(conn: Connection, date: str) -> tuple[dict, dict]:
    """(book -> race -> horse -> price row, book -> latest capture)."""
    fixed: dict[str, dict[int, dict[int, dict]]] = {}
    at: dict[str, str] = {}
    for f in conn.execute(
            "SELECT f.bookmaker, f.race_no, f.horse_no, f.win, f.place, "
            "       f.captured_at FROM fixed_odds f "
            "JOIN (SELECT bookmaker, race_no, max(captured_at) m FROM fixed_odds "
            "      WHERE race_date = ? GROUP BY bookmaker, race_no) l "
            "  ON l.bookmaker = f.bookmaker AND l.race_no = f.race_no "
            " AND l.m = f.captured_at "
            "WHERE f.race_date = ? AND f.scratched = 0", (date, date)):
        book = f["bookmaker"]
        fixed.setdefault(book, defaultdict(dict))[f["race_no"]][f["horse_no"]] \
            = dict(f)
        at[book] = max(at.get(book, ""), f["captured_at"])
    return fixed, at


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

    # What Racing & Sports wrote: the race's paragraph, and a line on each
    # runner. Context for the race, not support for a horse.
    comments: dict[int, list[dict]] = defaultdict(list)
    form: dict[tuple[int, int], dict] = {}
    for q in conn.execute(
            "SELECT source, speaker, race_no, horse_no, quote, url "
            "FROM connections_quote WHERE race_date = ? AND race_no IS NOT NULL "
            "  AND source IN (?, ?) ORDER BY race_no, horse_no",
            (date, _RS, _RS_FORM)):
        if q["source"] == _RS and q["horse_no"] is None:
            comments[q["race_no"]].append({
                "source": q["source"], "source_label": _LABEL[_RS],
                "who": q["speaker"], "text": q["quote"], "url": q["url"]})
        elif q["source"] == _RS_FORM and q["horse_no"] is not None:
            form[(q["race_no"], q["horse_no"])] = {"text": q["quote"],
                                                   "url": q["url"]}

    fixed, fixed_at = _fixed(conn, date)
    books = [b for b in BOOKS if b in fixed]

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
        prices = {b: fixed[b].get(race_no, {}) for b in books}
        fair_b = {b: _fair({n: f["win"] for n, f in prices[b].items()})
                  for b in books}
        numbers = sorted(n for (r, n) in runners if r == race_no)

        picks = []
        for no in numbers:
            who = backed.get((race_no, no), [])
            odds = _odds_block(no, tote, fair_t, prices, fair_b)
            horse = runners[(race_no, no)]
            supporters = len({(b["source"], b["who"]) for b in who})
            if who:
                picks.append({
                    "horse_no": no, "horse_name": horse["horse_name"],
                    "name_zh": horse["name_zh"], "jockey": horse["jockey"],
                    "trainer": horse["trainer"], "draw": horse["draw"],
                    "supporters": supporters,
                    "top_picks": sum(1 for b in who if b["rank"] == 1),
                    "interviewed": any(b["kind"] == "connections" for b in who),
                    "backed_by": sorted(who, key=_by_weight),
                    "form": form.get((race_no, no)), "odds": odds})
            for side in ("tote", *books):
                ev = odds[side]["value_pct"]
                if ev is None or ev < 100 * EDGE_AT:
                    continue
                edges.append({
                    "race_no": race_no, "horse_no": no,
                    "horse_name": horse["horse_name"], "bet_at": side,
                    "price": odds[side]["win"],
                    "against": "books" if side == "tote" else "tote",
                    "fair_pct": odds["books_fair_pct"] if side == "tote"
                    else odds["tote"]["fair_pct"],
                    "ev_pct": ev, "supporters": supporters})
        picks.sort(key=lambda p: (-p["supporters"], -p["top_picks"],
                                  min((b["rank"] or 9) for b in p["backed_by"]),
                                  p["horse_no"]))
        out_races.append({
            "race_no": race_no, "distance": race["distance"],
            "race_class": race["race_class"], "off_time": race["off_time"],
            "tote_win_pool": pools.get(race_no),
            "overround": {"tote": _overround({n: p["win_odds"]
                                              for n, p in tote.items()}),
                          **{b: _overround({n: f["win"] for n, f
                                            in prices[b].items()})
                             for b in books}},
            "comments": comments.get(race_no, []),
            "form": [dict(v, horse_no=no) for (r, no), v in sorted(form.items())
                     if r == race_no],
            "interviews": [dict(i, horse_name=runners[(race_no, i["horse_no"])]
                                ["horse_name"]) for i in interviews[race_no]],
            "picks": picks})

    edges.sort(key=lambda e: -e["ev_pct"])
    return {"race_date": date,
            "venue": next((r["venue"] for r in races.values() if r["venue"]),
                          None),
            "books": books,
            "captured": {"tote": _hk(tote_at),
                         **{b: _hk(fixed_at.get(b)) for b in books}},
            "sources": sorted({b["source_label"] for who in backed.values()
                               for b in who}),
            "source_status": _source_status(conn, date),
            "races": out_races, "edges": edges}


def _source_status(conn: Connection, date: str) -> list[dict[str, Any]]:
    """Per source: when it last arrived and what it gave, including what was
    held back for review. The expected sources come first, in order, and are
    listed even before they have published."""
    stats: dict[str, dict[str, Any]] = {}

    def entry(source: str) -> dict[str, Any]:
        key = _RS if source == _RS_FORM else source
        return stats.setdefault(key, {
            "source": key, "label": _LABEL.get(key, key), "fetched_at": None,
            "picks": 0, "heard": 0, "quotes": 0, "featured": 0,
            "interviews": 0, "runner_lines": 0, "races": set(), "held": 0,
            "held_reasons": {}})

    for s in conn.execute(
            "SELECT source, count(*) n, sum(caption_kind = 'asr') heard, "
            "       max(fetched_at) at, group_concat(DISTINCT race_no) races "
            "FROM tipster_selection WHERE race_date = ? GROUP BY source",
            (date,)):
        e = entry(s["source"])
        e["picks"] += s["n"]
        e["heard"] += s["heard"] or 0
        e["fetched_at"] = max(e["fetched_at"] or "", s["at"] or "") or None
        e["races"].update(int(x) for x in (s["races"] or "").split(",") if x)
    for q in conn.execute(
            "SELECT source, count(*) n, max(fetched_at) at, "
            "       count(DISTINCT CASE WHEN horse_no IS NOT NULL "
            "             THEN race_no || '-' || horse_no END) horses, "
            "       count(DISTINCT CASE WHEN horse_no IS NOT NULL "
            "             THEN race_no || '-' || horse_no || '-' || speaker "
            "             END) voices, "
            "       group_concat(DISTINCT race_no) races "
            "FROM connections_quote WHERE race_date = ? GROUP BY source",
            (date,)):
        e = entry(q["source"])
        if q["source"] == _RS_FORM:
            e["runner_lines"] += q["n"]
        else:
            e["quotes"] += q["n"]
        if q["source"] == "factcheck":
            e["featured"] += q["horses"]
        elif q["source"] == "rtw_interview":
            e["interviews"] += q["voices"]
        e["fetched_at"] = max(e["fetched_at"] or "", q["at"] or "") or None
        e["races"].update(int(x) for x in (q["races"] or "").split(",") if x)
    for h in conn.execute(
            "SELECT source, reason, count(*) n FROM tips_quarantine "
            "WHERE race_date = ? GROUP BY source, reason", (date,)):
        e = entry(h["source"])
        e["held"] += h["n"]
        e["held_reasons"][h["reason"]] = h["n"]

    order = list(EXPECTED) + sorted(set(stats) - set(EXPECTED))
    out = []
    for key in order:
        e = stats.get(key) or entry(key)
        out.append(dict(e, races=len(e["races"]),
                        fetched_at=_hk(e["fetched_at"]),
                        published=bool(e["picks"] or e["quotes"]
                                       or e["runner_lines"])))
    return out


def _by_weight(b: dict) -> tuple:
    """Connections first, then picks by rank, then featured."""
    order = {"connections": 0, "pick": 1, "featured": 2}
    return (order.get(b["kind"], 3), b["rank"] or 9, b["source"])
