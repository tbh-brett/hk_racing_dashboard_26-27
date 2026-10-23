"""Every read and write of the race-shape tables (racesim-SPEC §3).

runner_trip, race_trip   derived from runner_comments and runners (jobs/rebuild_trip)
shape_models             the four shape models, by version (jobs/fit_shape)
runner_shape             what they say about each declared runner (jobs/score_shape)
sim_tables               the simulator's tables per card date (jobs/fit_sim_tables)
race_sim, runner_sim     the simulations: append-only, a record (jobs/sim_card)
"""
from __future__ import annotations

import gzip
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence

import pandas as pd

from .connect import Connection, transaction

__all__ = ["TRIP_COLUMNS", "trip_races", "trip_runners", "trip_comments", "write_trip", "load_runs",
           "card_runs", "speed_map", "save_shape_models", "latest_shape_version", "shape_models",
           "write_shape_scores", "shape_keys"]

TRIP_COLUMNS = ("has_comment", "lane_settle", "lane_mid", "lane_turn", "lane_straight",
                "nocover_settle", "nocover_mid", "nocover_turn", "nocover_straight",
                "stated", "wide3_pre", "nocover_pre", "rail_pre", "lane_max_pre", "wide_straight",
                "slow_bad", "slow_mild", "traffic_pre", "traffic_straight",
                "run_way", "run_from", "phases_json", "derive_version")


# ── the trip ──────────────────────────────────────────────────────────────────────────────────
def trip_races(conn: Connection, date: str | None = None) -> list[dict]:
    """Every race that has been run (a result stored), or one date's."""
    where, params = ("AND a.race_date = ?", [date]) if date else ("", [])
    return [dict(r) for r in conn.execute(f"""
        SELECT a.race_date, a.race_no, a.venue, a.surface, a.distance FROM races a
         WHERE a.distance IS NOT NULL {where}
           AND EXISTS (SELECT 1 FROM runners u WHERE u.race_date = a.race_date
                          AND u.race_no = a.race_no AND u.place IS NOT NULL)
         ORDER BY a.race_date, a.race_no""", params)]


def trip_runners(conn: Connection, date: str | None = None) -> dict[tuple[str, int], list[dict]]:
    """The horses that ran, by race: a price or a place. A withdrawn horse has neither."""
    where, params = ("AND race_date = ?", [date]) if date else ("", [])
    out: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in conn.execute(f"""
        SELECT race_date, race_no, horse_no, horse_name, running_positions FROM runners
         WHERE (win_odds > 0 OR place > 0) {where}
         ORDER BY race_date, race_no, horse_no""", params):
        out[(r["race_date"], r["race_no"])].append(dict(r))
    return out


def trip_comments(conn: Connection, date: str | None = None) -> dict[tuple[str, int, int, str], str]:
    """(race_date, race_no, horse_no, source) -> text; source 'corunning' or 'incident'."""
    where, params = ("WHERE race_date = ?", [date]) if date else ("", [])
    return {(r["race_date"], r["race_no"], r["horse_no"], r["source"]): r["comment_text"]
            for r in conn.execute(f"SELECT race_date, race_no, horse_no, source, comment_text "
                                  f"FROM runner_comments {where}", params)}


def write_trip(conn: Connection, runs: Sequence[tuple], races: Iterable[tuple], *,
               date: str | None = None) -> None:
    """Delete-then-insert, for one date or the whole archive: a run that should no longer have a
    row must lose it. `runs` are (race_date, race_no, horse_no, *TRIP_COLUMNS); `races` are
    (race_date, race_no, events_json, derive_version)."""
    cols = ("race_date", "race_no", "horse_no") + TRIP_COLUMNS
    with transaction(conn):
        for table in ("runner_trip", "race_trip"):
            if date:
                conn.execute(f"DELETE FROM {table} WHERE race_date = ?", (date,))
            else:
                conn.execute(f"DELETE FROM {table}")
        conn.executemany(f"INSERT INTO runner_trip ({', '.join(cols)}) "
                         f"VALUES ({', '.join('?' for _ in cols)})", runs)
        conn.executemany("INSERT INTO race_trip (race_date, race_no, events_json, derive_version) "
                         "VALUES (?,?,?,?)", list(races))


# ── the runs every shape model and table is fitted on ─────────────────────────────────────────
_RUNS_SQL = """
SELECT u.race_date, u.race_no, u.horse_no, u.horse_name, u.place, u.dead_heat, u.finish_time,
       u.lengths_behind, u.draw, u.jockey, u.trainer, u.actual_weight, u.gear, u.win_odds,
       u.section_times, u.running_positions,
       a.venue, a.course, a.surface, a.going, a.distance, a.race_class,
       t.early_dev AS tempo_dev, t.band AS tempo_band,
       {trip}
  FROM runners u JOIN races a USING (race_date, race_no)
  LEFT JOIN race_tempo t USING (race_date, race_no)
  LEFT JOIN runner_trip p USING (race_date, race_no, horse_no)
 WHERE u.win_odds > 0 AND u.race_date < ?
   AND EXISTS (SELECT 1 FROM runners x WHERE x.race_date = u.race_date AND x.race_no = u.race_no
                  AND x.place IS NOT NULL)
 ORDER BY u.race_date, u.race_no, u.horse_no
"""


def load_runs(conn: Connection, *, before: str) -> pd.DataFrame:
    """Every priced run of a race that has been run, before a date, with the race, its pace and
    the run's 2D trip. A card still to run is not history: priced the day before, it would read as
    every declared horse's last start."""
    trip = ", ".join(f"p.{c}" for c in TRIP_COLUMNS if c not in ("phases_json", "derive_version"))
    return pd.read_sql(_RUNS_SQL.format(trip=trip), conn, params=(before,))


def card_runs(conn: Connection, date: str) -> pd.DataFrame:
    """A stored card's declared runners as the shape models read them (a scratching is not in it)."""
    return pd.read_sql("""
        SELECT u.race_date, u.race_no, u.horse_no, u.horse_name, u.draw, u.jockey, u.trainer,
               u.gear, u.actual_weight, u.rating, a.venue, a.course, a.surface, a.going, a.distance,
               a.race_class, a.race_name, a.off_time
          FROM runners u JOIN races a USING (race_date, race_no)
         WHERE u.race_date = ? AND coalesce(u.place_code, '') NOT LIKE 'W%'
         ORDER BY u.race_no, u.horse_no""", conn, params=(date,))


def speed_map(conn: Connection) -> pd.DataFrame:
    """The Speed Map's projections, which the settle model is checked against."""
    return pd.read_sql("SELECT race_date, race_no, horse_no, settle AS prod_settle, field_size AS prod_field "
                       "FROM runner_projection", conn)


# ── shape_models, runner_shape ────────────────────────────────────────────────────────────────
_MODEL_COLS = ("version", "kind", "trained_through", "params_json", "model_gz", "record_json", "created_at")


def save_shape_models(conn: Connection, rows: Sequence[dict]) -> None:
    """One fit: a row per kind. An older fit of the same sort (a card fit, or a season's check
    fit) gives up its boosters -- its scores are derived and its record stays -- so the archive
    does not carry every fit's twenty."""
    conn.executemany(
        f"INSERT INTO shape_models ({', '.join(_MODEL_COLS)}) VALUES ({', '.join('?' * len(_MODEL_COLS))}) "
        "ON CONFLICT (version, kind) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in _MODEL_COLS if c not in ("version", "kind")),
        [(r["version"], r["kind"], r["trained_through"], json.dumps(r["params"], sort_keys=True),
          gzip.compress(json.dumps(r["model_text"]).encode("utf-8"), mtime=0), json.dumps(r["record"]),
          r["created_at"]) for r in rows])
    for version in {r["version"] for r in rows}:
        check = "+check-" in version
        conn.execute("UPDATE shape_models SET model_gz = ? WHERE version <> ? AND "
                     + ("version LIKE '%+check-%'" if check else "version NOT LIKE '%+check-%'"),
                     (gzip.compress(b"[]", mtime=0), version))


def latest_shape_version(conn: Connection) -> str | None:
    """The newest card fit's version, without its boosters."""
    r = conn.execute("SELECT version FROM shape_models WHERE version NOT LIKE '%+check-%' "
                     "ORDER BY created_at DESC, version DESC LIMIT 1").fetchone()
    return None if r is None else r[0]


def shape_models(conn: Connection, version: str | None = None, *, check: bool = False) -> dict[str, dict]:
    """One fit's four rows by kind, boosters included: the newest card fit (or, with `check`, the
    newest season check fit) when `version` is None. {} when there is none."""
    if version is None:
        r = conn.execute("SELECT version FROM shape_models WHERE version "
                         + ("LIKE" if check else "NOT LIKE") + " '%+check-%' "
                         "ORDER BY created_at DESC, version DESC LIMIT 1").fetchone()
        if r is None:
            return {}
        version = r[0]
    out = {}
    for r in conn.execute(f"SELECT {', '.join(_MODEL_COLS)} FROM shape_models WHERE version = ?", (version,)):
        d = dict(r)
        d["params"] = json.loads(d.pop("params_json"))
        d["model_text"] = json.loads(gzip.decompress(d.pop("model_gz")).decode("utf-8"))
        d["record"] = json.loads(d.pop("record_json"))
        out[d["kind"]] = d
    return out


_SHAPE_COLS = ("race_date", "race_no", "horse_no", "pred_early", "p_lead", "exp_lane", "p_nocover",
               "slow_rate", "n_prior", "model_version", "inputs_key", "scored_at")


def write_shape_scores(conn: Connection, date: str, race_nos: Sequence[int], rows: Sequence[dict]) -> int:
    """A card's scores for the races named: the whole answer for each, so a runner scratched since
    the last score leaves it."""
    with transaction(conn):
        conn.execute(f"DELETE FROM runner_shape WHERE race_date = ? AND race_no IN "
                     f"({', '.join('?' * len(race_nos))})", (date, *race_nos))
        conn.executemany(f"INSERT INTO runner_shape ({', '.join(_SHAPE_COLS)}) "
                         f"VALUES ({', '.join('?' * len(_SHAPE_COLS))})",
                         [tuple(r[c] for c in _SHAPE_COLS) for r in rows])
    return len(rows)


def shape_keys(conn: Connection, date: str) -> set[str]:
    """The inputs keys a card's shape scores were made from."""
    return {r[0] for r in conn.execute("SELECT DISTINCT inputs_key FROM runner_shape WHERE race_date = ?", (date,))}
