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
           "model_row", "TROUBLE_TAGS", "WIDE_TAGS", "VET_TAGS"]

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
