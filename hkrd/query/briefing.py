"""The Briefing for one meeting, in one answer: what the dashboard works out,
what people said, and what the markets price — per race and per runner.

Brett, 2026-09-25: a front page that gathers what matters so the card is not
screened by hand — read from before the odds are out, and following the day
up to the off. Three kinds of evidence, kept apart because they are different
things, and never blended into one score:

  COMPUTED  the Screen (`query/screen`): each runner's chance before any
            price, the measured reasons for and against, the pace, trials,
            the blackbook, the last start, rematches. There when the card is.
  SAID      the voices (`query/tips_summary`): picks, featured segments,
            jockey and trainer interviews, Racing & Sports' words. Each
            source arrives on its own clock — see CLOCK.
  PRICED    the markets: the tote from about midday the day before, the
            bookmakers from race-day morning, money moving until the off.

WHICH RACES FIRST. Every race carries its `reasons`: facts worth reading,
each of one kind, each measured elsewhere (the Screen's factors) or simply
observed (who said what, what is priced). The card is ordered by how many
DIFFERENT reasons a race has, then by race number. That is a count of things
to read and not a chance of anything, and it is unmeasured: a first rule for
the owner to judge against his own reading of the card.

A PRICE BEFORE RACE DAY IS SHOWN, NEVER REASONED FROM. The tote opens about
midday the day before on almost no money, and there one bet prices a runner
(AGENTS.md, 2026-09-09 HV R3). So the reasons read from prices — a gap
between markets, late money, the market ranking a horse far from the Screen
— fire only on race day, which is also the baseline every move is measured
from.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from hkrd.query import market, money, movement, screen, tips_summary
from hkrd.store.connect import Connection, get_conn

__all__ = ["meeting", "CLOCK", "Due", "LATE_FIRMING", "MARKET_APART"]

_HK = dt.timezone(dt.timedelta(hours=8))
LATE_FIRMING = 10.0     # % a price shortens in its last minutes to be a reason
MARKET_APART = 6        # the Screen's top two, this far down the betting
CONSENSUS = 3           # distinct sources on one horse
ORDER_RULE = ("races with more different things to read first, then race "
              "order; a count of reasons, not a chance of anything")
_MARKET = {"tote": "the tote", "ladbrokes": "Ladbrokes", "sportsbet": "Sportsbet",
           "unibet": "Unibet", "books": "the bookmakers"}


@dataclass(frozen=True)
class Due:
    """When a source normally lands, in days before the meeting and a Hong
    Kong clock time. `days` None: it posts when it posts."""
    key: str
    label: str
    kind: str              # said | priced
    days: int | None
    at: str | None
    usual: str


# Measured in the tips work (ops/tips.ps1 header) unless the note says once.
CLOCK = (
    Due("factcheck", "賽馬Fact Check", "said", 2, "20:00",
        "posts at 20:00 two nights before"),
    Due("rtw_preview", "Racing To Win preview", "said", 1, "16:00",
        "posts about 16:00 the day before"),
    Due("rtw_interview", "Racing To Win interviews", "said", 1, "16:00",
        "post with the preview, about 16:00 the day before"),
    Due("tote", "HKJC tote", "priced", 1, "12:00",
        "opens about midday the day before, thin until race day"),
    Due("racing_sports", "Racing & Sports", "said", 0, "09:00",
        "arrives with the bookmakers' markets; up by race-day morning once"),
    Due("ladbrokes", "Ladbrokes", "priced", 0, "09:00",
        "fixed odds; up by race-day morning once"),
    Due("sportsbet", "Sportsbet", "priced", 0, "09:00",
        "fixed odds, read from the PC only"),
    Due("threads", "神探賽馬 Horse Detective", "said", None, None,
        "posts on race-day morning when it posts at all"),
)


def _now(now: dt.datetime | None) -> dt.datetime:
    """Hong Kong wall time, naive, like every HKJC capture."""
    if now is None:
        return dt.datetime.now(_HK).replace(tzinfo=None, microsecond=0)
    return now.astimezone(_HK).replace(tzinfo=None) if now.tzinfo else now


def _clock(date: str, now: dt.datetime, status: dict[str, dict],
           captured: dict[str, str | None]) -> list[dict[str, Any]]:
    out = []
    for d in CLOCK:
        at = (captured.get(d.key) if d.kind == "priced"
              else (status.get(d.key) or {}).get("fetched_at")
              if (status.get(d.key) or {}).get("published") else None)
        due = (dt.datetime.fromisoformat(f"{date}T{d.at}")
               - dt.timedelta(days=d.days)) if d.days is not None else None
        state = ("in" if at else "irregular" if due is None
                 else "due" if now < due else "overdue")
        out.append({"key": d.key, "label": d.label, "kind": d.kind,
                    "state": state, "at": at,
                    "due": due.isoformat(timespec="minutes") if due else None,
                    "usual": d.usual,
                    "held": (status.get(d.key) or {}).get("held", 0)})
    return out


def _stage(date: str, now: dt.datetime, clock: list[dict],
           races: list[dict]) -> str:
    """cold (the card only) · voices (someone has spoken) · priced (a market
    is open before race day) · race_day · settled."""
    if races and all(r["run"] for r in races):
        return "settled"
    landed = {c["kind"] for c in clock if c["state"] == "in"}
    if now.date().isoformat() == date and "priced" in landed:
        return "race_day"
    return ("priced" if "priced" in landed
            else "voices" if "said" in landed else "cold")


def _runner(s: dict, tip: dict | None, form: dict | None, odds: dict | None,
            zh: str | None, move: dict | None, mrank: int | None,
            priced: bool) -> dict[str, Any]:
    ident = ("horse_no", "horse_name", "draw", "jockey", "trainer", "rating",
             "weight", "gear", "style")
    gear_first = [g[:-1] for g in (s["gear"] or "").replace(",", " ").split()
                  if g.endswith("1") and g[:-1].isalpha()]
    return {
        **{k: s[k] for k in ident}, "name_zh": zh, "gear_first": gear_first,
        "result": s["result"],
        "screen": {k: v for k, v in s.items()
                   if k not in ident and k not in ("blackbook", "result")},
        "blackbook": s["blackbook"],
        "support": None if not tip else {
            "sources": tip["supporters"], "top_picks": tip["top_picks"],
            "interviewed": tip["interviewed"], "backed_by": tip["backed_by"]},
        "form_line": form,
        "price": odds if priced else None,
        "market_rank": mrank,
        "move": None if not move or not move.get("observed") else {
            k: move.get(k) for k in ("change_pct", "direction", "rush_pct",
                                     "rush_direction", "rush_minutes")},
    }


def _reasons(race: dict, runners: list[dict], interviews: list[dict],
             edges: list[dict], race_day: bool, tipped: set[str]
             ) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(key: str, kind: str, text: str, horse: int | None = None) -> None:
        out.append({"key": key, "kind": kind, "text": text, "horse_no": horse})

    def who(r: dict) -> str:
        return f"#{r['horse_no']} {r['horse_name']}"

    pace = race["pace"]
    if len(pace["leaders"]) == 1:
        lead = pace["leaders"][0]
        add("lone_leader", "computed",
            f"#{lead['horse_no']} {lead['horse_name']} is the only habitual "
            f"leader (×{pace['leader_x']:.2f} measured)", lead["horse_no"])
    for r in runners:
        sc = r["screen"]
        # Every race holds a book horse or two; one is a reason to study the
        # race only when today suits it — its set-up, or the conditions the
        # owner wrote for it.
        book = r["blackbook"] or {}
        if book.get("live") and (sc["setup"] == "FAVOURABLE"
                                 or book.get("on_conditions")):
            add("book", "computed", f"Your book: {who(r)} — "
                + ("your conditions met" if book.get("on_conditions")
                   else "set-up FAVOURABLE")
                + f", the Screen's {sc['rank']} of {len(runners)}",
                r["horse_no"])
        trial = next((f for f in sc["for"] if f["key"] == "trial_good"), None)
        if trial and sc["tier"] != "FIELD":
            add("trial", "computed", f"{who(r)} trialled well since its last "
                f"run (×{trial['x']:.2f}, one season measured)", r["horse_no"])
    cases = [r for r in runners if r["screen"]["tier"] == "CASE"]
    if cases:
        add("case", "computed", f"{len(cases)} outside the Screen's four with "
            f"a case: " + ", ".join(who(r) for r in cases))

    for iv in interviews:
        add("interview", "said", f"{iv['speaker']} ({iv['role']}) on "
            f"#{iv['horse_no']} {iv['horse_name']}", iv["horse_no"])
    for r in runners:
        k = (r["support"] or {}).get("sources", 0)
        if k >= CONSENSUS:
            add("consensus", "said", f"{who(r)} backed by {k} sources",
                r["horse_no"])
        if k >= 2 and r["screen"]["rank"] > screen.SHORTLIST:
            add("talked_up", "said", f"{who(r)}: {k} sources back it, the "
                f"Screen ranks it {r['screen']['rank']} of {len(runners)}",
                r["horse_no"])
    first = next((r for r in runners if r["screen"]["rank"] == 1), None)
    if first and len(tipped) >= 2 and not first["support"]:
        add("screen_alone", "said", f"The Screen's first choice {who(first)} "
            f"is backed by none of the {len(tipped)} sources that tipped "
            f"this race", first["horse_no"])

    if not race_day:
        return out
    best = max((e for e in edges if e["race_no"] == race["race_no"]),
               key=lambda e: e["ev_pct"], default=None)
    if best:
        add("price_gap", "priced", f"#{best['horse_no']} {best['horse_name']}: "
            f"{_MARKET[best['bet_at']]} pays {best['price']:g}, +"
            f"{best['ev_pct']:.1f}% if {_MARKET[best['against']]} "
            f"{'have' if best['against'] == 'books' else 'has'} its "
            f"chance right ({best['fair_pct']}%)", best["horse_no"])
    for r in runners:
        m = r["move"]
        if m and m["rush_direction"] == "shortened" \
                and abs(m["rush_pct"] or 0) >= LATE_FIRMING:
            add("late_money", "priced", f"{who(r)} firmed "
                f"{abs(m['rush_pct']):.0f}% in the last {m['rush_minutes']:.0f} "
                f"minutes", r["horse_no"])
        if r["screen"]["rank"] <= 2 and (r["market_rank"] or 0) > MARKET_APART:
            add("market_apart", "priced", f"The Screen's {r['screen']['rank']} "
                f"{who(r)} is {r['market_rank']} in the betting — where the "
                f"two disagree, the tote has usually been right (A/E 0.85–0.91)",
                r["horse_no"])
    return out


def meeting(date: str, *, now: dt.datetime | None = None,
            conn: Connection | None = None) -> dict[str, Any]:
    """The whole Briefing for one date. `races` is empty when no card is
    stored; `now` is Hong Kong time and defaults to the clock."""
    own = conn is None
    conn = conn or get_conn()
    try:
        return _meeting(conn, date, _now(now))
    finally:
        if own:
            conn.close()


def _meeting(conn: Connection, date: str, now: dt.datetime) -> dict[str, Any]:
    scr = screen.meeting(date, conn=conn)
    if not scr["races"]:
        return {"race_date": date, "races": []}
    tips = tips_summary.summary(date, conn=conn, every_runner=True)
    cash = money.meeting_money(date, conn=conn)
    pools = {r["race_no"]: r for r in cash.get("races") or []}
    zh = {r["horse_name"]: r["name_zh"] for r in conn.execute(
        "SELECT z.horse_name, z.name_zh FROM horse_name_zh z JOIN runners u "
        "  ON u.horse_name = z.horse_name WHERE u.race_date = ?", (date,))}
    status = {s["source"]: s for s in tips["source_status"]}
    clock = _clock(date, now, status, tips["captured"])
    race_day = now.date().isoformat() == date
    tote_in = bool(tips["captured"].get("tote"))
    by_race = {r["race_no"]: r for r in tips["races"]}
    # A price is shown whenever one exists, and reasoned from only on race
    # day (module docstring).
    priced = any(tips["captured"].values())

    races = []
    for sr in scr["races"]:
        no = sr["race_no"]
        tr = by_race.get(no) or {"picks": [], "interviews": [], "comments": [],
                                 "form": [], "odds": {}, "overround": {}}
        picks = {p["horse_no"]: p for p in tr["picks"]}
        form = {f["horse_no"]: {"text": f["text"], "url": f["url"]}
                for f in tr["form"]}
        odds = tr.get("odds") or {}
        wins = sorted((o["tote"]["win"], n) for n, o in odds.items()
                      if o["tote"]["win"])
        mrank = {n: i + 1 for i, (_, n) in enumerate(wins)}
        moves = ({m["horse_no"]: m for m in movement.split_move(
            date, no, off_time=sr["off_time"], conn=conn)} if tote_in else {})
        runners = [_runner(s, picks.get(s["horse_no"]), form.get(s["horse_no"]),
                           odds.get(s["horse_no"]), zh.get(s["horse_name"]),
                           moves.get(s["horse_no"]), mrank.get(s["horse_no"]),
                           priced) for s in sr["runners"]]
        tipped = {b["source"] for p in tr["picks"] for b in p["backed_by"]}
        conc = market.concentration(date, no, conn=conn) if tote_in else None
        fav = (next(r for r in runners if r["horse_no"] == wins[0][1])
               if wins else None)
        race = {
            **{k: sr[k] for k in ("race_no", "venue", "course", "surface",
                                  "going", "distance", "race_class",
                                  "restricted", "off_time", "field_size",
                                  "run", "pace")},
            "off_time": sr["off_time"] or tr.get("off_time"),
            "minutes_to_off": _minutes_to(date, sr["off_time"]
                                          or tr.get("off_time"), now)
            if race_day else None,
            "market": None if not priced else {
                "win_pool": (pools.get(no) or {}).get("win_pool"),
                "overround": tr["overround"],
                "concentration": (conc or {}).get("value"),
                "band": (conc or {}).get("band"),
                "favourite": fav and {"horse_no": fav["horse_no"],
                                      "horse_name": fav["horse_name"],
                                      "win": wins[0][0]}},
            "voices": {"sources": sorted(tipped),
                       "interviews": tr["interviews"],
                       "comments": tr["comments"]},
            "runners": runners,
        }
        race["reasons"] = ([] if sr["run"] else _reasons(
            race, runners, tr["interviews"], tips["edges"], race_day, tipped))
        races.append(race)

    live = [r for r in races if not r["run"]]
    order = sorted(live, key=lambda r: (-len({x["key"] for x in r["reasons"]}),
                                        r["race_no"]))
    return {
        "race_date": date, "venue": races[0]["venue"],
        "as_of": now.isoformat(timespec="minutes"),
        "stage": _stage(date, now, clock, races),
        "clock": clock,
        "order": [r["race_no"] for r in order]
        + [r["race_no"] for r in races if r["run"]],
        "order_rule": ORDER_RULE,
        "fit": scr["fit"], "factors": scr["factors"],
        "meeting_total": cash.get("meeting_total"),
        "books": tips["books"], "captured": tips["captured"],
        "edges": tips["edges"] if race_day else [],
        "source_status": tips["source_status"],
        "races": races,
    }


def _minutes_to(date: str, off: str | None, now: dt.datetime) -> int | None:
    if not off:
        return None
    at = dt.datetime.fromisoformat(f"{date}T{off}")
    return int((at - now).total_seconds() // 60)
