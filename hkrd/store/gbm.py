"""What the fundamental model reads out of the database (`derive/features`).

One query for the runs and one for their stewards' tags, both whole-archive.
The model's inputs are built from EVERY earlier run of every horse and every
ride of every rider, so there is no smaller question to ask; what keeps it
cheap is asking only for the columns the features read.

The rows come back in KEY ORDER, on purpose. `derive/features` ranks each
race's runners by price, and two runners on the same odds are told apart by
the order they arrive in. The lab read them in storage order, which for a
results-scraped race is finishing order and for a card-scraped one is
saddlecloth order, and which a VACUUM or a delete-and-reinsert repair can
change: the same archive could then give different features. Key order
cannot move. It differs from the lab only in how tied prices rank (the three
`beat_mkt` inputs, on about 5% of runs); fed the lab's order, the port matches
it on every column (gbm step-1 check, 29 Sep).
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from . import coerce
from .connect import Connection

__all__ = ["load_runs", "settled_dates", "save_model", "promote", "live_model",
           "model_row", "card_facts", "unrun_races", "unrun_dates", "scored_keys", "write_scores",
           "shown", "scored_dates", "TROUBLE_TAGS", "WIDE_TAGS", "VET_TAGS"]

# What each stewards' count in the model means. The tag names are
# `derive/tags`' vocabulary; a tag not listed here is not a model input.
TROUBLE_TAGS = ("checked", "crowded", "hampered", "short_of_room", "barred", "steadied",
                "bumped", "held_up", "awkwardly_away", "stumbled", "contact")
WIDE_TAGS = ("wide", "lane:wide", "lane:three_wide", "lane:four_wide",
             "without_cover", "lane:without_cover")
VET_TAGS = ("bled", "lame_fore", "lame_hind", "roarer", "mucus", "arrhythmia", "vet_finding")


def _in(tags: tuple[str, ...]) -> str:
    return ", ".join(f"'{t}'" for t in tags)


_RUNS_SQL = """
SELECT u.race_date, u.race_no, u.horse_no, u.horse_name, u.place, u.place_code,
       u.finish_time, u.lengths_behind, u.draw, u.jockey, u.trainer,
       u.actual_weight, u.declared_weight, u.rating, u.win_odds, u.running_positions,
       a.venue, a.surface, a.going, a.distance, a.race_class,
       p.early_dev, p.late_dev
  FROM runners u
  JOIN races a USING (race_date, race_no)
  LEFT JOIN runner_pace p USING (race_date, race_no, horse_no)
 WHERE u.race_date <= ?
 ORDER BY u.race_date, u.race_no, u.horse_no
"""

_TAGS_SQL = f"""
SELECT race_date, race_no, horse_no,
       sum(tag IN ({_in(TROUBLE_TAGS)})) AS n_trouble,
       sum(tag IN ({_in(WIDE_TAGS)})) AS n_wide,
       sum(tag IN ({_in(VET_TAGS)})) AS n_vet,
       max(tag = 'eased') AS eased, max(tag = 'weakened') AS weakened,
       max(tag = 'raced_keenly') AS keen
  FROM runner_tags
 WHERE race_date <= ?
 GROUP BY race_date, race_no, horse_no
"""

TAG_COLS = ["n_trouble", "n_wide", "n_vet", "eased", "weakened", "keen"]
_CHUNK = 10_000
_NUMERIC = ["place", "finish_time", "lengths_behind", "draw", "actual_weight",
            "declared_weight", "rating", "win_odds", "distance", "early_dev", "late_dev"]


def load_runs(conn: Connection, *, through: str = "9999-12-31") -> pd.DataFrame:
    """Every runner dated on or before `through`, with its race and its last
    run's stewards' counts. A runner with no tag rows has NaN counts, never 0:
    "the stewards said nothing" and "no report has been read" are the same row
    here, and the model was trained on that meaning."""
    # In chunks: read whole, the driver's rows cost three times the finished
    # frame before pandas packs them, and this runs beside the web server.
    runs = pd.concat(pd.read_sql(_RUNS_SQL, conn, params=(through,), chunksize=_CHUNK),
                     ignore_index=True)
    # A chunk where a column is all NULL (rating, before 2024) arrives as text,
    # and the concat makes the whole column text. Numbers are typed here.
    runs[_NUMERIC] = runs[_NUMERIC].astype(float)
    tags = pd.read_sql(_TAGS_SQL, conn, params=(through,))
    key = ["race_date", "race_no", "horse_no"]
    idx = pd.MultiIndex.from_frame(runs[key])
    t = tags.set_index(key).reindex(idx)
    for c in TAG_COLS:
        runs[c] = t[c].to_numpy(dtype=float)
    return runs


def settled_dates(conn: Connection, *, through: str = "9999-12-31",
                  today: str | None = None) -> list[str]:
    """Meetings with results, oldest first: every race placed, or -- for a day
    already past, where an abandoned race will never be -- any race placed."""
    rows = conn.execute("""
        SELECT r.race_date, count(*) AS races, sum(p.placed IS NOT NULL) AS placed
          FROM races r
          LEFT JOIN (SELECT DISTINCT race_date, race_no, 1 AS placed FROM runners
                      WHERE place IS NOT NULL) p USING (race_date, race_no)
         WHERE r.race_date <= ?
         GROUP BY r.race_date ORDER BY r.race_date""", (through,)).fetchall()
    return [r["race_date"] for r in rows
            if r["placed"] and (r["placed"] == r["races"] or (today and r["race_date"] < today))]


_MODEL_COLS = ("version", "kind", "trained_through", "features_version", "params_json",
               "rounds", "model_text", "record_json", "promoted", "promoted_at", "created_at")


def save_model(conn: Connection, row: dict[str, Any]) -> None:
    """One fit, written or rewritten whole (a re-run of the same fit replaces
    its row). Never changes which row is live: `promote` does that."""
    vals = (row["version"], row["kind"], coerce.to_date(row["trained_through"]),
            row["features_version"], json.dumps(row["params"], sort_keys=True),
            int(row["rounds"]), row["model_text"], json.dumps(row["record"]),
            0, None, row["created_at"])
    cols = ", ".join(_MODEL_COLS)
    keep = ("promoted", "promoted_at", "version")
    conn.execute(
        f"INSERT INTO gbm_models ({cols}) VALUES ({', '.join('?' * len(vals))}) "
        "ON CONFLICT (version) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in _MODEL_COLS if c not in keep), vals)


def promote(conn: Connection, version: str, *, at: str) -> None:
    """Make `version` the live model; the one before keeps its promoted_at.
    A superseded fit that was never live gives up its model text, which is
    kept only while it could still be promoted."""
    conn.execute("UPDATE gbm_models SET promoted = 0 WHERE promoted = 1 AND version <> ?",
                 (version,))
    conn.execute("UPDATE gbm_models SET promoted = 1, promoted_at = ? WHERE version = ?",
                 (at, version))
    conn.execute("UPDATE gbm_models SET model_text = '' "
                 "WHERE promoted_at IS NULL AND created_at < "
                 "(SELECT created_at FROM gbm_models WHERE version = ?)", (version,))


def _shape(r) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    d["params"] = json.loads(d.pop("params_json"))
    d["record"] = json.loads(d.pop("record_json"))
    return d


def live_model(conn: Connection) -> dict[str, Any] | None:
    """The row the pages read, or None before the first promotion."""
    return _shape(conn.execute(
        f"SELECT {', '.join(_MODEL_COLS)} FROM gbm_models WHERE promoted = 1").fetchone())


def model_row(conn: Connection, version: str | None = None) -> dict[str, Any] | None:
    """One fit by version, or the newest fit when `version` is None."""
    sql = f"SELECT {', '.join(_MODEL_COLS)} FROM gbm_models "
    r = (conn.execute(sql + "WHERE version = ?", (version,)) if version else
         conn.execute(sql + "ORDER BY created_at DESC LIMIT 1")).fetchone()
    return _shape(r)


# ─── runner_gbm ──────────────────────────────────────────────────────────────

# A race has gone when HKJC shut its pool (`market_close`, written by the odds
# capture the minute it sees a pool stop selling at the off) or a result is
# stored -- `store/tips.races_gone_off`'s rule. A result alone is not enough:
# results arrive at 19:00, and on 2026-10-04 the 13:01 run rescored race 1,
# off at 12:30, so 'latest' was no longer what the page showed at the off.
_UNRUN = ("NOT EXISTS (SELECT 1 FROM runners x WHERE x.race_date = r.race_date "
          "AND x.race_no = r.race_no AND x.place IS NOT NULL) "
          "AND NOT EXISTS (SELECT 1 FROM market_close m WHERE m.race_date = r.race_date "
          "AND m.race_no = r.race_no)")


def card_facts(conn: Connection, date: str) -> list[dict[str, Any]]:
    """Every runner still declared on the day's unrun races, with the race
    facts a score depends on: what `score_gbm` fingerprints before it builds
    anything. Scratched runners (W codes) are not in the field."""
    rows = conn.execute(f"""
        SELECT u.race_no, u.horse_no, u.horse_name, u.draw, u.jockey, u.trainer,
               u.actual_weight, u.declared_weight, u.rating,
               r.venue, r.surface, r.course, r.distance, r.race_class, r.going
          FROM runners u JOIN races r USING (race_date, race_no)
         WHERE u.race_date = ? AND coalesce(u.place_code, '') NOT LIKE 'W%' AND {_UNRUN}
         ORDER BY u.race_no, u.horse_no""", (date,)).fetchall()
    return [dict(x) for x in rows]


def unrun_races(conn: Connection, date: str) -> list[int]:
    """One meeting's races still to run: pool not shut, no result stored."""
    return [r[0] for r in conn.execute(
        f"SELECT r.race_no FROM races r WHERE r.race_date = ? AND {_UNRUN} "
        "ORDER BY r.race_no", (date,))]


def unrun_dates(conn: Connection, *, today: str) -> list[str]:
    """Meetings from today on with at least one race not yet run."""
    return [r[0] for r in conn.execute(f"""
        SELECT DISTINCT r.race_date FROM races r
         WHERE r.race_date >= ? AND {_UNRUN} ORDER BY r.race_date""", (today,))]


def scored_keys(conn: Connection, date: str) -> set[str]:
    """The inputs fingerprints the day's 'latest' scores were made from."""
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT inputs_key FROM runner_gbm WHERE race_date = ? AND stage = 'latest'",
        (date,))}


_SCORE_COLS = ("race_date", "race_no", "horse_no", "stage", "p_win", "p_place", "contrib_json",
               "facts_json", "model_version", "derive_version", "inputs_key", "scored_at")


def write_scores(conn: Connection, rows: list[dict[str, Any]]) -> dict[str, int]:
    """One meeting's scores for its unrun races. 'latest' is the whole answer
    for each race it names: a runner scratched since the last score leaves it.
    'card' is written once: a race already on the record keeps its first read."""
    out = {"latest": 0, "card": 0}
    if not rows:
        return out
    date = coerce.to_date(rows[0]["race_date"])
    races = sorted({int(r["race_no"]) for r in rows})
    conn.execute(f"DELETE FROM runner_gbm WHERE race_date = ? AND stage = 'latest' "
                 f"AND race_no IN ({', '.join('?' * len(races))})", (date, *races))
    sql = (f"INSERT INTO runner_gbm ({', '.join(_SCORE_COLS)}) "
           f"VALUES ({', '.join('?' * len(_SCORE_COLS))}) ON CONFLICT DO NOTHING")
    for stage in ("latest", "card"):
        before = conn.total_changes
        conn.executemany(sql, [(
            date, int(r["race_no"]), int(r["horse_no"]), stage, float(r["p_win"]),
            float(r["p_place"]), json.dumps(r["contrib"]), json.dumps(r["facts"]),
            r["model_version"], r["derive_version"], r["inputs_key"], r["scored_at"])
            for r in rows])
        out[stage] = conn.total_changes - before
    return out


def shown(conn: Connection, dates: list[str], *, stage: str = "latest") -> pd.DataFrame:
    """What the page showed for these meetings: the 'latest' score at the off,
    or with `stage='card'` the first read, when the card landed."""
    cols = ["race_date", "race_no", "horse_no", "p_win", "model_version"]
    if not dates:
        return pd.DataFrame(columns=cols)
    return pd.read_sql(
        f"SELECT {', '.join(cols)} FROM runner_gbm WHERE stage = ? "
        f"AND race_date IN ({', '.join('?' * len(dates))})", conn, params=[stage, *dates])


def scored_dates(conn: Connection) -> list[str]:
    """Every meeting the model has scored."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT race_date FROM runner_gbm ORDER BY race_date")]
