"""How each source's picks have run: the record the Briefing keeps.

Brett, 2026-09-28: "I would want to track how good each source performs."

A RECORD, NOT A RANKING. Every figure carries how many it was measured on,
and at the start of a season that is a handful of races — enough to notice,
not enough to conclude. The lines come in a fixed order and nothing here
sorts them by how they have done.

WHAT IS SCORED, on races with a result only:

  top pick   one horse per race per source: the pick it ranked first, or the
             only horse it named in that race. Won, placed (in the first
             three), the return of $10 on it at the final tote price, and
             A/E: its wins against what the closing tote expected of the same
             horses, de-vigged. Above 1, the source found winners the market
             underrated; below 1, the market already knew.
  all picks  every horse it named. Won and placed.

Each source also carries what the tote favourite did IN THE SAME RACES, so a
source is never flattered by the races it chose to tip.

THE BENCHMARKS are scored the same way: the tote favourite (and the market's
four shortest as its "all picks") on every settled race, and the Screen — its
first choice and its four — on the races recorded for it after its weights
were fitted (`jobs/record_screen`), never on races it was fitted on.

INTERVIEWS AND FEATURED HORSES are all-picks only. A trainer asked about a
horse is not tipping it, and nor is Fact Check making a segment of one; what
is measured is how the horses they talked about ran. 全方位Bryan is left out,
as on the Briefing: a mention with no direction is not support.

WHAT IT CANNOT PROVE. A pick is only worth scoring if it was made before the
race. A race that has gone off keeps its tips as they stood (`store/tips`),
and a pick whose own publish time is after the off is not counted and is
reported as `late`. A pick on a horse that did not start is reported as
`scratched` and not scored.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from hkrd.derive.probability import devig
from hkrd.query.screen_inputs import RAN
from hkrd.store.connect import Connection, get_conn

__all__ = ["record", "LINES", "PLACED"]

PLACED = 3                          # "placed" is a finish in the first three
STAKE = 10.0
_HK = dt.timezone(dt.timedelta(hours=8))

# key -> (label, kind). Sources first, then the two benchmarks.
LINES: dict[str, tuple[str, str]] = {
    "racing_sports": ("Racing & Sports", "pick"),
    "rtw_preview": ("Racing To Win preview", "pick"),
    "factcheck": ("賽馬Fact Check · picks", "pick"),
    "threads": ("神探賽馬 Horse Detective", "pick"),
    "oncc": ("on.cc", "pick"),
    "rtw_interview": ("Racing To Win interviews · horses discussed", "connections"),
    "factcheck_featured": ("賽馬Fact Check · featured", "featured"),
    "screen": ("The Screen", "computed"),
    "favourite": ("Tote favourite", "market"),
}
_QUOTED = {"rtw_interview": "rtw_interview", "factcheck": "factcheck_featured"}


@dataclass
class _Race:
    off: dt.datetime | None
    runners: dict[int, dict[str, Any]] = field(default_factory=dict)
    fair: dict[int, float] = field(default_factory=dict)
    by_price: list[int] = field(default_factory=list)

    @property
    def favourite(self) -> int | None:
        return self.by_price[0] if self.by_price else None


@dataclass
class _Tally:
    n: int = 0
    won: int = 0
    placed: int = 0
    returned: float = 0.0
    expected: float = 0.0
    fav_won: int = 0
    fav_placed: int = 0

    def add(self, race: _Race, no: int, *, with_fav: bool = False) -> None:
        x = race.runners[no]
        self.n += 1
        self.won += x["place"] == 1
        self.placed += x["place"] is not None and x["place"] <= PLACED
        if x["place"] == 1 and x["win_odds"]:
            self.returned += STAKE * x["win_odds"]
        self.expected += race.fair.get(no, 0.0)
        fav = race.favourite
        if with_fav and fav is not None:
            f = race.runners[fav]
            self.fav_won += f["place"] == 1
            self.fav_placed += f["place"] is not None and f["place"] <= PLACED

    def as_dict(self, *, top: bool) -> dict[str, Any] | None:
        if not self.n:
            return None
        out: dict[str, Any] = {"n": self.n, "won": self.won,
                               "placed": self.placed}
        if top:
            staked = STAKE * self.n
            out |= {"return_pct": round(100 * (self.returned - staked)
                                        / staked, 1),
                    "ae": round(self.won / self.expected, 2)
                    if self.expected else None,
                    "fav_won": self.fav_won, "fav_placed": self.fav_placed}
        return out


@dataclass
class _Line:
    top: _Tally = field(default_factory=_Tally)
    all: _Tally = field(default_factory=_Tally)
    races: set = field(default_factory=set)
    scratched: int = 0
    late: int = 0


def _season_start(today: dt.date) -> str:
    """A Hong Kong season opens in September; August belongs to it."""
    return f"{today.year if today.month >= 8 else today.year - 1}-08-01"


def _when(stamp: str | None) -> dt.datetime | None:
    if not stamp:
        return None
    try:
        t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=_HK)


def _settled(conn: Connection, since: str, until: str) -> dict[tuple, _Race]:
    """Every race with a result in the window, its starters and their final
    prices. The settled set is grouped once, never tested per row."""
    races: dict[tuple, _Race] = {}
    for r in conn.execute(
            "WITH settled AS ("
            "  SELECT DISTINCT race_date, race_no FROM runners"
            "   WHERE race_date BETWEEN ? AND ? AND place IS NOT NULL)"
            "SELECT u.race_date, u.race_no, u.horse_no, u.place, u.win_odds,"
            "       a.off_time"
            "  FROM settled s"
            "  JOIN races a ON a.race_date = s.race_date AND a.race_no = s.race_no"
            "  JOIN runners u ON u.race_date = s.race_date AND u.race_no = s.race_no"
            f" WHERE {RAN.format(t='u')}", (since, until)):
        key = (r["race_date"], r["race_no"])
        race = races.get(key)
        if race is None:
            off = (dt.datetime.fromisoformat(f"{r['race_date']}T{r['off_time']}")
                   .replace(tzinfo=_HK) if r["off_time"] else None)
            race = races[key] = _Race(off=off)
        race.runners[r["horse_no"]] = {"place": r["place"],
                                       "win_odds": r["win_odds"]}
    for race in races.values():
        priced = [(no, x["win_odds"]) for no, x in race.runners.items()
                  if x["win_odds"] and x["win_odds"] > 1.0]
        if len(priced) >= 2:
            probs = devig([o for _, o in priced])
            race.fair = {no: float(p) for (no, _), p in zip(priced, probs)}
        race.by_price = [no for no, _ in sorted(priced, key=lambda p: (p[1], p[0]))]
    return races


def record(*, since: str | None = None, until: str | None = None,
           conn: Connection | None = None) -> dict[str, Any]:
    """Every source's record over `since`..`until` (default: this season)."""
    today = dt.datetime.now(_HK).date()
    since = since or _season_start(today)
    until = until or today.isoformat()
    own = conn is None
    conn = conn or get_conn()
    try:
        races = _settled(conn, since, until)
        picks = conn.execute(
            "SELECT source, race_date, race_no, horse_no, pick_rank, "
            "       published_at FROM tipster_selection "
            "WHERE race_date BETWEEN ? AND ?", (since, until)).fetchall()
        quoted = conn.execute(
            "SELECT DISTINCT source, race_date, race_no, horse_no "
            "FROM connections_quote WHERE race_date BETWEEN ? AND ? "
            f"  AND horse_no IS NOT NULL AND source IN "
            f"({', '.join('?' for _ in _QUOTED)})",
            (since, until, *_QUOTED)).fetchall()
        screened = conn.execute(
            "SELECT race_date, race_no, horse_no, rank FROM screen_pick "
            "WHERE race_date BETWEEN ? AND ? AND rank <= 4",
            (since, until)).fetchall()
    finally:
        if own:
            conn.close()

    lines: dict[str, _Line] = defaultdict(_Line)

    # The tipsters: every pick, then one top pick per race.
    by_race: dict[tuple, list] = defaultdict(list)
    for p in picks:
        key = (p["race_date"], p["race_no"])
        race = races.get(key)
        if race is None:
            continue
        line = lines[p["source"]]
        said = _when(p["published_at"])
        if said and race.off and said > race.off:
            line.late += 1
            continue
        if p["horse_no"] not in race.runners:
            line.scratched += 1
            continue
        line.races.add(key)
        line.all.add(race, p["horse_no"])
        by_race[(p["source"], *key)].append(p)
    for (source, *key), made in by_race.items():
        ranked = [p for p in made if p["pick_rank"] == 1]
        top = ranked[0] if ranked else made[0] if len(made) == 1 else None
        if top is not None:
            lines[source].top.add(races[tuple(key)], top["horse_no"],
                                  with_fav=True)

    for q in quoted:
        key = (q["race_date"], q["race_no"])
        race = races.get(key)
        if race is None:
            continue
        line = lines[_QUOTED[q["source"]]]
        if q["horse_no"] not in race.runners:
            line.scratched += 1
            continue
        line.races.add(key)
        line.all.add(race, q["horse_no"])

    for s in screened:
        key = (s["race_date"], s["race_no"])
        race = races.get(key)
        if race is None or s["horse_no"] not in race.runners:
            continue
        line = lines["screen"]
        line.races.add(key)
        line.all.add(race, s["horse_no"])
        if s["rank"] == 1:
            line.top.add(race, s["horse_no"], with_fav=True)

    for key, race in races.items():
        if race.favourite is None:
            continue
        line = lines["favourite"]
        line.races.add(key)
        line.top.add(race, race.favourite)
        for no in race.by_price[:4]:
            line.all.add(race, no)

    out = []
    for key in [*LINES, *sorted(set(lines) - set(LINES))]:
        line = lines.get(key)
        if line is None or not (line.races or line.late or line.scratched):
            continue
        label, kind = LINES.get(key, (key, "pick"))
        top = line.top.as_dict(top=True)
        if top and key == "favourite":
            # Its own "same races" figure would be itself.
            top["fav_won"] = top["fav_placed"] = None
        out.append({
            "key": key, "label": label, "kind": kind,
            "meetings": len({d for d, _ in line.races}), "races": len(line.races),
            "top": top, "all": line.all.as_dict(top=False),
            "scratched": line.scratched, "late": line.late})
    return {"since": since, "until": until,
            "meetings": len({d for d, _ in races}), "races": len(races),
            "placed_is": f"a finish in the first {PLACED}",
            "stake": STAKE, "lines": out}
