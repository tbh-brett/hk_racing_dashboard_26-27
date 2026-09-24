"""Everything the Screen reads about one meeting, in a handful of queries.

One reader for two callers: the Briefing's `/api/screen/{date}` and
`jobs/fit_screen`, which walks every meeting in the archive through THIS
function to fit the weights. So the page is scored on exactly the inputs the
weights were fitted on — a second gatherer in the job would drift from this
one and neither would look wrong.

AS AT THE RACE. Every history read is strictly before the meeting's date: the
last run, the campaign, the rider's year, the running style, the trials. A
race already run carries its `place` and `win_odds` on the runner for the fit
and for the page's "how did it go" column; `model/screen` never reads either.

WHAT IT CANNOT SEE. A horse scratched after the card was scraped stays on it
until the results land -- the card stores no scratched flag -- so a
pre-race screen can list a runner that will not start. Race Day has the same
limit, for the same reason.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from hkrd.model.screen import JOCKEY_WINDOW_DAYS, JOCKEY_REFERENCE
from hkrd.query import trials as trials_q
from hkrd.query.race import habitual_styles
from hkrd.store.coerce import is_no_comment
from hkrd.store.connect import Connection, get_conn

__all__ = ["gather", "RAN"]

# A starter, as the rest of the archive defines one: not withdrawn.
RAN = "coalesce({t}.place_code, '') NOT LIKE 'W%'"

_CARD_SQL = f"""
WITH field AS (
  SELECT race_no, count(*) n FROM runners r
   WHERE r.race_date = ? AND {RAN.format(t='r')} GROUP BY race_no)
SELECT r.race_no, r.horse_no, r.horse_name, r.draw, r.jockey, r.trainer,
       r.actual_weight, r.rating, r.gear, r.place, r.lengths_behind, r.win_odds,
       a.venue, a.course, a.surface, a.going, a.distance, a.race_class,
       a.off_time, f.n field_size, s.sarr, s.sarr_rank
  FROM runners r
  JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
  JOIN field f ON f.race_no = r.race_no
  LEFT JOIN runner_sarr s ON s.race_date = r.race_date
                         AND s.race_no = r.race_no AND s.horse_no = r.horse_no
 WHERE r.race_date = ? AND {RAN.format(t='r')}
 ORDER BY r.race_no, r.horse_no
"""

# Field sizes grouped once per race, never a correlated count per row.
_PRIOR_SQL = f"""
WITH prior AS (
  SELECT r.horse_name, r.race_date, r.race_no, r.horse_no, r.place,
         r.lengths_behind, r.draw, r.jockey, r.trainer, r.rating,
         r.actual_weight
    FROM runners r
   WHERE r.horse_name IN ({{marks}}) AND r.race_date < ?
     AND {RAN.format(t='r')}),
fields AS (
  SELECT r.race_date, r.race_no, count(*) n FROM runners r
   WHERE (r.race_date, r.race_no) IN (SELECT race_date, race_no FROM prior)
     AND {RAN.format(t='r')}
   GROUP BY r.race_date, r.race_no)
SELECT p.*, f.n field_size, a.venue, a.race_class, a.distance
  FROM prior p
  JOIN fields f ON f.race_date = p.race_date AND f.race_no = p.race_no
  JOIN races a ON a.race_date = p.race_date AND a.race_no = p.race_no
 ORDER BY p.horse_name, p.race_date DESC, p.race_no DESC
"""


def _keys_cte(keys: Sequence[tuple[str, int, int]]) -> tuple[str, list[Any]]:
    values = ",".join("(?,?,?)" for _ in keys)
    params: list[Any] = [x for k in keys for x in k]
    return f"WITH k(race_date, race_no, horse_no) AS (VALUES {values})", params


def _last_start_notes(conn: Connection, keys: list[tuple[str, int, int]]
                      ) -> dict[tuple[str, int, int], dict[str, Any]]:
    """Tags and both comments for the given runs, keyed by run."""
    out: dict[tuple[str, int, int], dict[str, Any]] = {
        k: {"tags": set(), "running_comment": None, "incident_comment": None}
        for k in keys}
    if not keys:
        return out
    cte, params = _keys_cte(keys)
    for row in conn.execute(
            f"{cte} SELECT t.race_date, t.race_no, t.horse_no, t.tag "
            f"FROM runner_tags t JOIN k ON k.race_date = t.race_date "
            f"AND k.race_no = t.race_no AND k.horse_no = t.horse_no", params):
        out[(row["race_date"], row["race_no"], row["horse_no"])]["tags"].add(row["tag"])
    for row in conn.execute(
            f"{cte} SELECT c.race_date, c.race_no, c.horse_no, c.source, "
            f"c.comment_text FROM runner_comments c JOIN k "
            f"ON k.race_date = c.race_date AND k.race_no = c.race_no "
            f"AND k.horse_no = c.horse_no", params):
        if is_no_comment(row["comment_text"]):
            continue
        slot = ("running_comment" if row["source"] == "corunning"
                else "incident_comment")
        out[(row["race_date"], row["race_no"], row["horse_no"])][slot] = row["comment_text"]
    return out


def _jockey_year(conn: Connection, date: str) -> tuple[dict[str, tuple[int, int]], float]:
    """Every rider's wins and rides over the year before `date`, and the
    year's overall win rate the model shrinks toward."""
    lo = (dt.date.fromisoformat(date) - dt.timedelta(days=JOCKEY_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT jockey, sum(CASE WHEN place = 1 THEN 1 ELSE 0 END) wins, "
        "count(*) rides FROM runners WHERE race_date >= ? AND race_date < ? "
        "AND place IS NOT NULL AND jockey IS NOT NULL GROUP BY jockey",
        (lo, date)).fetchall()
    by = {r["jockey"]: (r["wins"], r["rides"]) for r in rows}
    wins = sum(w for w, _ in by.values())
    rides = sum(n for _, n in by.values())
    return by, (wins / rides if rides else JOCKEY_REFERENCE)


def gather(date: str, *, conn: Connection | None = None) -> list[dict[str, Any]]:
    """One meeting as the Screen's inputs: `[{"race": {...}, "runners": [...]}]`.

    Each runner is the card line plus `sarr`, `style`, the rider's year,
    `prior_dates` (newest first), `prev` (the last run with its tags and both
    comments, or None), `history` (every earlier run, for head-to-heads)
    and `trials` (newest first, each rated by `derive/trial_quality`).
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        card = conn.execute(_CARD_SQL, (date, date)).fetchall()
        if not card:
            return []
        names = sorted({r["horse_name"] for r in card})
        marks = ",".join("?" * len(names))
        # The whole career, not a window: a horse back from a year off is
        # first-up, and read through a window it would look like a debutant.
        # A career is a few dozen runs and the (horse_name, race_date) index
        # answers it directly.
        prior: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute(_PRIOR_SQL.format(marks=marks), [*names, date]):
            prior.setdefault(row["horse_name"], []).append(dict(row))
        last = {n: runs[0] for n, runs in prior.items()}
        notes = _last_start_notes(conn, sorted(
            (p["race_date"], p["race_no"], p["horse_no"]) for p in last.values()))
        styles = habitual_styles(names, before=date, conn=conn)
        trials = trials_q.for_horses(names, before=date, limit=3, conn=conn)
        jockeys, base = _jockey_year(conn, date)

        races: dict[int, dict[str, Any]] = {}
        for row in card:
            no = row["race_no"]
            race = races.setdefault(no, {"race": {
                "race_date": date, "race_no": no, "venue": row["venue"],
                "course": row["course"], "surface": row["surface"],
                "going": row["going"], "distance": row["distance"],
                "race_class": row["race_class"], "off_time": row["off_time"],
                "field_size": row["field_size"], "j_base": base,
                # Every rider's year, shared by the races: the head-to-head
                # compares riders who are not on today's card.
                "jockeys": jockeys}, "runners": []})
            name = row["horse_name"]
            prev = last.get(name)
            if prev is not None:
                prev = {**prev, **notes[(prev["race_date"], prev["race_no"],
                                         prev["horse_no"])]}
            wins, rides = jockeys.get(row["jockey"], (0, 0))
            style = styles.get(name) or {}
            race["runners"].append({
                **{k: row[k] for k in ("horse_no", "horse_name", "draw", "jockey",
                                       "trainer", "actual_weight", "rating", "gear",
                                       "sarr", "sarr_rank", "place",
                                       "lengths_behind", "win_odds")},
                "venue": row["venue"], "race_class": row["race_class"],
                "style": style.get("style"), "style_n": style.get("n", 0),
                "j_wins": wins, "j_rides": rides,
                "prior_dates": [p["race_date"] for p in prior.get(name, [])],
                "prev": prev,
                "history": prior.get(name, []),
                "trials": [{"trial_date": t["trial_date"], "trial_no": t["trial_no"],
                            "place": t["place"], "field_size": t["field_size"],
                            "band": t["quality_band"], "comment": t["comment"],
                            "jockey": t["jockey"], "gear": t["gear"],
                            "note": (t.get("note") or {}).get("note")}
                           for t in trials.get(name, [])],
            })
        return [races[k] for k in sorted(races)]
    finally:
        if own:
            conn.close()
