"""Reads and writes for the race simulation (racesim-SPEC §3, §5, §7).

sim_tables            the simulator's tables per card date. Never overwritten once a race_sim row
                      names it: a stored simulation must always re-run exactly.
race_sim, runner_sim  the simulations. Append-only, a RECORD: a race that has gone off is never
                      simulated again, so its last row is the one it is scored on.
"""
from __future__ import annotations

import gzip
import json
import re
from typing import Any

from .connect import Connection
from .gbm import _UNRUN

__all__ = ["standards", "tempo_races", "season_contacts", "tables_row", "has_tables", "last_steal_reading",
           "tables_named", "save_tables",
           "card", "gone", "unrun_races", "sim_keys", "write_sim", "latest_sim", "sim_rows", "SIM_RUNNER_COLUMNS"]


def _gz(obj: Any) -> bytes:
    return gzip.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"), mtime=0)


def _ungz(blob: bytes) -> Any:
    return json.loads(gzip.decompress(blob).decode("utf-8"))


# ── what the tables are built from ────────────────────────────────────────────────────────────
def standards(conn: Connection) -> dict[tuple, dict]:
    """HKJC's standard times, keyed (venue, surface, distance, class_key)."""
    return {(r["venue"], r["surface"], r["distance"], r["class_key"]): dict(r)
            for r in conn.execute("SELECT * FROM standard_times")}


def tempo_races(conn: Connection) -> dict[str, dict]:
    """Every race with a pace reading, by race id: the day's track speed and the race's course."""
    return {f"{r['race_date']}_{int(r['race_no']):02d}": dict(r) for r in conn.execute(
        "SELECT t.race_date, t.race_no, t.variant, a.venue, a.surface, a.distance, a.race_class "
        "FROM race_tempo t JOIN races a USING (race_date, race_no)")}


def season_contacts(conn: Connection, *, start: str, before: str) -> list[dict]:
    """This season's placed contacts, race by race, with how many ran."""
    return [dict(distance=r["distance"], venue=r["venue"], surface=r["surface"], runners=r["n"],
                 events=json.loads(r["events_json"])) for r in conn.execute("""
        SELECT a.distance, a.venue, a.surface, t.events_json,
               (SELECT count(*) FROM runner_trip p WHERE p.race_date = t.race_date AND p.race_no = t.race_no) n
          FROM race_trip t JOIN races a USING (race_date, race_no)
         WHERE t.race_date >= ? AND t.race_date < ? ORDER BY t.race_date, t.race_no""", (start, before))]


# ── sim_tables ────────────────────────────────────────────────────────────────────────────────
def tables_row(conn: Connection, as_of: str, version: str) -> dict | None:
    r = conn.execute("SELECT * FROM sim_tables WHERE as_of = ? AND version = ?", (as_of, version)).fetchone()
    if r is None:
        return None
    out = dict(r)
    out["tables"] = _ungz(out.pop("tables_gz"))
    return out


def has_tables(conn: Connection, as_of: str, version: str) -> bool:
    return conn.execute("SELECT 1 FROM sim_tables WHERE as_of = ? AND version = ?", (as_of, version)).fetchone() is not None


_STEAL = re.compile(r"steal_s=([0-9.]+) boot=([0-9]+)")


def last_steal_reading(conn: Connection) -> tuple[float, int] | None:
    """(CPU seconds stolen since boot, the boot time) as the last FINISHED sim_card run read them --
    a run's own row is open while it runs."""
    r = conn.execute("SELECT detail FROM job_runs WHERE job = 'sim_card' AND finished_at IS NOT NULL "
                     "AND detail LIKE '%steal_s=%' ORDER BY started_at DESC LIMIT 1").fetchone()
    m = _STEAL.search(r[0]) if r else None
    return (float(m.group(1)), int(m.group(2))) if m else None


def tables_named(conn: Connection, as_of: str, version: str) -> bool:
    """Does a stored simulation name these tables? Then they may never change."""
    return conn.execute("SELECT 1 FROM race_sim WHERE tables_as_of = ? AND tables_version = ? LIMIT 1",
                        (as_of, version)).fetchone() is not None


def save_tables(conn: Connection, *, as_of: str, version: str, trained_through: str, tables: dict,
                created_at: str) -> bool:
    """Store a card date's tables. False, and nothing written, when a simulation already names
    the row there."""
    if tables_named(conn, as_of, version):
        return False
    conn.execute("INSERT INTO sim_tables (as_of, version, trained_through, tables_gz, created_at) "
                 "VALUES (?,?,?,?,?) ON CONFLICT (as_of, version) DO UPDATE SET "
                 "trained_through = excluded.trained_through, tables_gz = excluded.tables_gz, "
                 "created_at = excluded.created_at", (as_of, version, trained_through, _gz(tables), created_at))
    return True


# ── the card the simulator reads ──────────────────────────────────────────────────────────────
def card(conn: Connection, date: str) -> dict:
    """One meeting's races -- each saying whether it has gone off (pool shut or result stored) --
    and every declared runner (a scratching is not in the field) with v2's 'latest' chances, the
    shape models' predictions and the Speed Map's style and band; NULL where none is stored."""
    races = [dict(r) for r in conn.execute(
        f"SELECT r.*, NOT ({_UNRUN}) AS gone FROM races r WHERE r.race_date = ? ORDER BY r.race_no", (date,))]
    runners = [dict(r) for r in conn.execute("""
        SELECT u.race_no, u.horse_no, u.horse_name, u.draw, u.jockey, u.trainer, u.actual_weight,
               u.rating, u.gear, g.p_win, g.p_place, g.model_version AS v2_version, g.scored_at AS v2_scored_at,
               s.pred_early, s.p_lead, s.exp_lane, s.p_nocover, s.slow_rate, s.n_prior,
               s.model_version AS shape_version, p.style AS prod_style, p.settle_band AS prod_band
          FROM runners u
          LEFT JOIN runner_gbm g ON g.race_date = u.race_date AND g.race_no = u.race_no
                                AND g.horse_no = u.horse_no AND g.stage = 'latest'
          LEFT JOIN runner_shape s USING (race_date, race_no, horse_no)
          LEFT JOIN runner_projection p USING (race_date, race_no, horse_no)
         WHERE u.race_date = ? AND coalesce(u.place_code, '') NOT LIKE 'W%'
         ORDER BY u.race_no, u.horse_no""", (date,))]
    return dict(races=races, runners=runners)


def gone(conn: Connection, date: str, race_no: int) -> bool:
    """Has the race gone off -- its pool shut, or a result stored?"""
    r = conn.execute(f"SELECT NOT ({_UNRUN}) FROM races r WHERE r.race_date = ? AND r.race_no = ?",
                     (date, race_no)).fetchone()
    return bool(r and r[0])


def unrun_races(conn: Connection, *, today: str) -> list[dict]:
    """Every stored race from today on that has not gone off -- pool not shut, no result -- earliest
    first."""
    return [dict(r) for r in conn.execute(f"""
        SELECT r.race_date, r.race_no, r.off_time FROM races r
         WHERE r.race_date >= ? AND {_UNRUN} ORDER BY r.race_date, r.off_time, r.race_no""", (today,))]


# ── race_sim, runner_sim ──────────────────────────────────────────────────────────────────────
SIM_RUNNER_COLUMNS = ("v2_win", "v2_place", "sim_win", "sim_top3", "p_lead", "first_call_pos",
                      "p_front_third", "p_back_third", "p_nocover", "p_wide3", "p_held", "trip_cost",
                      "p_win_if_led", "engine_held", "engine_nocover_most", "engine_ground", "run_share",
                      "round_share", "through_share", "coupled_win", "coupled_top3")
_SIM_COLS = ("race_date", "race_no", "fingerprint", "simulated_at", "engine_version", "tables_as_of",
             "tables_version", "v2_version", "shape_version", "settings_json", "inputs_gz", "result_gz",
             "cpu_seconds")


def sim_keys(conn: Connection, date: str, race_no: int) -> set[str]:
    """The fingerprints a race has been simulated at."""
    return {r[0] for r in conn.execute("SELECT fingerprint FROM race_sim WHERE race_date = ? AND race_no = ?",
                                       (date, race_no))}


def write_sim(conn: Connection, row: dict, inputs: dict, result_text: str,
              runners: list[dict]) -> None:
    """Append one simulation. `result_text` is exactly what node wrote, kept byte for byte."""
    vals = {**row, "settings_json": json.dumps(row["settings_json"], sort_keys=True),
            "inputs_gz": _gz(inputs), "result_gz": gzip.compress(result_text.encode("utf-8"), mtime=0)}
    conn.execute(f"INSERT INTO race_sim ({', '.join(_SIM_COLS)}) VALUES ({', '.join('?' * len(_SIM_COLS))})",
                 [vals[c] for c in _SIM_COLS])
    cols = ("race_date", "race_no", "fingerprint", "horse_no") + SIM_RUNNER_COLUMNS
    conn.executemany(f"INSERT INTO runner_sim ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     [tuple([row["race_date"], row["race_no"], row["fingerprint"], r["horse_no"]]
                            + [r[c] for c in SIM_RUNNER_COLUMNS]) for r in runners])


def _sim(r: Any, *, blobs: bool) -> dict:
    out = dict(r)
    out["settings"] = json.loads(out.pop("settings_json"))
    if blobs:
        out["inputs"] = _ungz(out.pop("inputs_gz"))
        out["result_text"] = gzip.decompress(out.pop("result_gz")).decode("utf-8")
    else:
        out.pop("inputs_gz", None), out.pop("result_gz", None)
    return out


def latest_sim(conn: Connection, date: str, race_no: int, *, blobs: bool = True) -> dict | None:
    """A race's newest simulation -- after its off, its record."""
    r = conn.execute("SELECT * FROM race_sim WHERE race_date = ? AND race_no = ? "
                     "ORDER BY simulated_at DESC LIMIT 1", (date, race_no)).fetchone()
    return None if r is None else _sim(r, blobs=blobs)


def sim_rows(conn: Connection, date: str) -> list[dict]:
    """Every race of a meeting's newest simulation, without the blobs."""
    return [_sim(r, blobs=False) for r in conn.execute("""
        SELECT s.* FROM race_sim s WHERE s.race_date = ? AND s.simulated_at =
               (SELECT max(simulated_at) FROM race_sim x WHERE x.race_date = s.race_date AND x.race_no = s.race_no)
         ORDER BY s.race_no""", (date,))]
