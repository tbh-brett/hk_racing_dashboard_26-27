"""Reads and writes for a meeting's own bias (model/day_bias, query/shape_day).

day_lanes        Brett's straight-lane reads of placed horses, entered on race day. Raw.
day_bias_record  whether a meeting's bias has ever carried over, fitted on the archive.
"""
from __future__ import annotations

import json
from typing import Any

from .connect import Connection
from .gbm import _UNRUN

__all__ = ["LANES", "save_lane", "clear_race", "lanes", "meeting", "race_sims", "save_record", "latest_record",
           "last_settled"]

LANES = ("rail", "middle", "outside")


def save_lane(conn: Connection, date: str, race_no: int, horse_no: int, lane: str | None, *, at: str) -> None:
    """One placed horse's lane in the straight; None removes it."""
    if lane is None:
        conn.execute("DELETE FROM day_lanes WHERE race_date = ? AND race_no = ? AND horse_no = ?",
                     (date, race_no, horse_no))
        return
    if lane not in LANES:
        raise ValueError(f"a lane in the straight is one of {', '.join(LANES)}, not {lane!r}")
    conn.execute("INSERT INTO day_lanes (race_date, race_no, horse_no, lane, entered_at) VALUES (?,?,?,?,?) "
                 "ON CONFLICT (race_date, race_no, horse_no) DO UPDATE SET lane = excluded.lane, "
                 "entered_at = excluded.entered_at", (date, race_no, horse_no, lane, at))


def clear_race(conn: Connection, date: str, race_no: int) -> int:
    """Every lane read of one race, removed. How many there were."""
    return conn.execute("DELETE FROM day_lanes WHERE race_date = ? AND race_no = ?", (date, race_no)).rowcount


def lanes(conn: Connection, date: str) -> dict[tuple[int, int], str]:
    return {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT race_no, horse_no, lane FROM day_lanes WHERE race_date = ?", (date,))}


def meeting(conn: Connection, date: str) -> dict[str, Any]:
    """A meeting's races -- gone off or not, result stored or not -- and every horse that ran or is
    declared (a scratching is not), with its closing price, draw, first call and place."""
    races = [dict(r) for r in conn.execute(f"""
        SELECT r.race_no, r.off_time, r.venue, r.surface, r.distance, NOT ({_UNRUN}) AS gone,
               EXISTS (SELECT 1 FROM runners x WHERE x.race_date = r.race_date AND x.race_no = r.race_no
                          AND x.place IS NOT NULL) AS has_result
          FROM races r WHERE r.race_date = ? ORDER BY r.race_no""", (date,))]
    runners = [dict(r) for r in conn.execute("""
        SELECT race_no, horse_no, horse_name, place, draw, win_odds, running_positions FROM runners
         WHERE race_date = ? AND coalesce(place_code, '') NOT LIKE 'W%' ORDER BY race_no, horse_no""", (date,))]
    return dict(races=races, runners=runners)


def race_sims(conn: Connection, date: str) -> dict[int, dict]:
    """Each race's newest simulation -- after its off, its record -- per runner: its win chance and
    its chance of being in the front third at the first call."""
    out: dict[int, dict] = {}
    for r in conn.execute("""
        SELECT s.race_no, s.horse_no, s.sim_win, s.p_front_third
          FROM runner_sim s JOIN race_sim x USING (race_date, race_no, fingerprint)
         WHERE s.race_date = ? AND x.simulated_at =
               (SELECT max(simulated_at) FROM race_sim y WHERE y.race_date = x.race_date AND y.race_no = x.race_no)""",
                          (date,)):
        out.setdefault(r["race_no"], {})[r["horse_no"]] = dict(sim_win=r["sim_win"], p_front_third=r["p_front_third"])
    return out


def save_record(conn: Connection, *, fitted_at: str, through: str, record: dict) -> None:
    conn.execute("INSERT INTO day_bias_record (fitted_at, through, record_json) VALUES (?,?,?) "
                 "ON CONFLICT (fitted_at) DO UPDATE SET through = excluded.through, record_json = excluded.record_json",
                 (fitted_at, through, json.dumps(record)))


def last_settled(conn: Connection, *, before: str) -> str | None:
    """The newest meeting before `before` with a result stored."""
    r = conn.execute("SELECT max(race_date) FROM runners WHERE place IS NOT NULL AND race_date < ?", (before,)).fetchone()
    return r[0]


def latest_record(conn: Connection) -> dict | None:
    r = conn.execute("SELECT fitted_at, through, record_json FROM day_bias_record "
                     "ORDER BY fitted_at DESC LIMIT 1").fetchone()
    return None if r is None else dict(fitted_at=r[0], through=r[1], **json.loads(r[2]))
