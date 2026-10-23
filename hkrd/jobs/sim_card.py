"""Simulate the stored cards, one race a run (racesim-SPEC §5).

    python -m hkrd.jobs.sim_card --next                  # the one race most in need; the crontab's line
    python -m hkrd.jobs.sim_card --date 2026-10-25 --race 7
    python -m hkrd.jobs.sim_card --verify 2026-10-25 7    # re-run the stored inputs; fail unless identical

ONE RACE A RUN. A race at these settings is ~64 s of CPU; Fly gives this machine 12.5% of one core
once its burst balance (500 s at most) is spent, and then everything on it slows -- the pages, the
minute-by-minute odds capture, race night. So `--next` does one thing and exits: a new card's tables
(jobs/fit_sim_tables) or one race's simulation, every fifteen minutes from cron at nice 19 -- about
7% of one core. Never inside a request; never a whole card in one burst.

WHICH RACE. Never one that has gone off (its pool shut or a result stored): its last row is the
record it is scored on, so a race is never simulated again after its off, and a simulation that
finishes after the off is not kept. Never one whose v2 or shape scores are incomplete. Of the rest,
the earliest whose fingerprint (query/shape_sim) has no row: a new card, a changed field or rider,
v2 or the shape models moved by 0.01, new tables, a new engine. And not at all when the machine has
been throttled since the last run (`nr_throttled` in the cgroup's cpu.stat): that tick says so.

THE ONE SUBPROCESS IN THE CODEBASE (§5.5; Brett, 23 Oct). node runs the vendored engine -- the same
files the pages run -- on one JSON document on stdin and writes one on stdout. A non-zero exit, a
timeout or output that is not one JSON document is an error carrying node's stderr, never a skip.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.model import sim_tables
from hkrd.query import shape_sim
from hkrd.jobs import fit_sim_tables
from hkrd.store import job_log
from hkrd.store import sim as store
from hkrd.store.connect import Connection, get_conn, init_db, transaction

__all__ = ["SETTINGS", "ENGINE_DIR", "engine_version", "run_node", "simulate", "next_tick", "verify", "main"]

# Production settings (§5.4): the lab's but for N_COUPLED, 600 -> 300.
SETTINGS = dict(N_SUM=20000, N_ANCHOR=10000, PASSES=40, N_ENGINE=300, N_COUPLED=300, SIGMA_MULT=1.045)
# The vendored engine, resolved the way api/app.py resolves web/.
ENGINE_DIR = Path(__file__).resolve().parents[2] / "web" / "assets" / "shape"
RUNNER = Path(__file__).resolve().with_name("sim_node") / "run_race.cjs"
ENGINE_FILES = ("engine.js", "sim-core.js", "sectionals.js", "track-view.js")
NODE = shutil.which("node") or "/usr/local/bin/node"
CPU_STAT = Path("/sys/fs/cgroup/cpu.stat")


class SimError(RuntimeError):
    """node failed, timed out, or wrote something that is not one JSON document."""


@dataclass
class SimReport:
    did: str = ""
    checks: list[str] = field(default_factory=list)
    throttle: str = ""
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  {self.did or 'nothing to simulate'}"] + [f"  check  {c}" for c in self.checks]
        if self.throttle:
            lines.append(f"  cpu    {self.throttle}")
        lines += [f"  ERROR  {e}" for e in self.errors]
        return "\n".join(lines)


def engine_version() -> str:
    """sha256 of the four vendored files, in order: a change to any re-simulates every race to run."""
    h = hashlib.sha256()
    for name in ENGINE_FILES:
        h.update((ENGINE_DIR / name).read_bytes())
    return h.hexdigest()


def run_node(doc: dict) -> tuple[str, float]:
    """One race through node: (exactly what it wrote, CPU seconds it used)."""
    before = os.times()
    t0 = time.monotonic()
    try:
        done = subprocess.run([NODE, str(RUNNER), str(ENGINE_DIR)], input=json.dumps(doc).encode("utf-8"),
                              capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise SimError(f"node timed out after {exc.timeout} s") from exc
    after = os.times()
    cpu = (after.children_user - before.children_user) + (after.children_system - before.children_system)
    if done.returncode != 0:
        raise SimError(f"node exited {done.returncode}: {done.stderr.decode('utf-8', 'replace').strip()[:1500]}")
    text = done.stdout.decode("utf-8")
    try:
        json.loads(text)
    except ValueError as exc:
        raise SimError(f"node wrote something that is not one JSON document: {text[:200]!r}") from exc
    return text, cpu or (time.monotonic() - t0)          # no child accounting off Linux: wall time


def _runner_rows(result: dict) -> list[dict]:
    rows = []
    for s, e in zip(result["summary"]["runners"], result["engine"]):
        rows.append(dict(horse_no=s["no"], v2_win=s["v2_win"], v2_place=s["v2_place"], sim_win=s["win"],
                         sim_top3=s["top3"], p_lead=s["lead"], first_call_pos=s["first_call_pos"],
                         p_front_third=s["front"], p_back_third=s["back"], p_nocover=s["nocover"],
                         p_wide3=s["wide3"], p_held=s["held"], trip_cost=s["trip_cost"], p_win_if_led=s["win_if_led"],
                         engine_held=e["held"], engine_nocover_most=e["nocover_most"], engine_ground=e["ground_lengths"],
                         run_share=e["run_share"], round_share=e["round_share"], through_share=e["through_share"],
                         coupled_win=e["win"], coupled_top3=e["top3"]))
    return rows


def _check(race_id: str, result: dict) -> str:
    """§11.3: over the coupled runs, each horse's win share against v2, against its Monte Carlo spread."""
    c = result["coupled_check"]
    verdict = "FAILED (over twice the spread)" if c["mean_win_gap_to_v2"] > 2 * c["mc_sd_mean"] else "ok"
    return (f"{race_id}: coupled win share against v2, mean gap {c['mean_win_gap_to_v2']:.3f} against a "
            f"spread of {c['mc_sd_mean']:.3f} -- {verdict}; anchor's largest miss {result['anchor']['max_err']:.4f}")


def _gone(conn: Connection, date: str, race_no: int) -> bool:
    return not any(r["race_no"] == race_no for r in store.unrun_races(conn, today=date) if r["race_date"] == date)


def simulate(conn: Connection, date: str, race_no: int, report: SimReport, *, doc: dict | None = None,
             settings: dict = SETTINGS) -> bool:
    """Simulate one race at its current inputs, unless that fingerprint is already stored. True if
    node ran -- a tick's whole allowance, whether or not the result could be kept."""
    doc = doc or shape_sim.card_inputs(conn, date)
    rc = next((r for r in doc["races"] if r["no"] == race_no), None)
    if rc is None:
        why = next((r["why"] for r in doc["incomplete"] if r["no"] == race_no), "not on the card")
        report.did = f"{date} R{race_no}: not simulated -- {why}"
        return False
    if not store.has_tables(conn, date, sim_tables.VERSION):
        report.did = f"{date} R{race_no}: no simulator tables for this card yet (jobs.fit_sim_tables)"
        return False
    versions = dict(engine=engine_version(), tables=[date, sim_tables.VERSION], **rc["versions"])
    fp = shape_sim.fingerprint(rc, versions=versions, settings=settings)
    if fp in store.sim_keys(conn, date, race_no):
        report.did = f"{date} R{race_no}: already simulated at these inputs ({fp[:12]})"
        return False
    tables = store.tables_row(conn, date, sim_tables.VERSION)          # 1 MB of JSON: only when it runs
    card = shape_sim.race_inputs(doc)
    text, cpu = run_node(dict(card=card, tables=tables["tables"], race_no=race_no, settings=settings))
    result = json.loads(text)
    if _gone(conn, date, race_no):
        report.did = f"{date} R{race_no}: went off while it was simulated; not kept (its record is the row before)"
        return True
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    row = dict(race_date=date, race_no=race_no, fingerprint=fp, simulated_at=now, engine_version=versions["engine"],
               tables_as_of=date, tables_version=sim_tables.VERSION, v2_version=rc["versions"]["v2"],
               shape_version=rc["versions"]["shape"], settings_json=settings, cpu_seconds=round(cpu, 1))
    with transaction(conn):
        store.write_sim(conn, row, dict(card=card, tables=[date, sim_tables.VERSION], race_no=race_no,
                                        settings=settings), text, _runner_rows(result))
    report.did = f"simulated {date} R{race_no} ({len(rc['runners'])} runners, fingerprint {fp[:12]}, {cpu:.0f} s CPU)"
    report.checks.append(_check(rc["id"], result))
    return True


def _throttled(conn: Connection) -> tuple[bool, str]:
    """Has the machine been throttled since the last run? (nr_throttled, kept in job_runs.)"""
    try:
        stat = dict(line.split() for line in CPU_STAT.read_text().splitlines() if line.strip())
    except OSError:
        return False, "no cgroup cpu.stat on this machine: not checked"
    now = stat.get("nr_throttled")
    if now is None:
        return False, "cpu.stat has no nr_throttled here: not checked"
    last = store.last_throttle_reading(conn)
    if last is not None and int(now) > last:
        return True, f"nr_throttled={now} (was {last} at the last run): throttled since, nothing run this tick"
    return False, f"nr_throttled={now}"


def next_tick(db: Path | None = None, *, today: str | None = None) -> SimReport:
    """The crontab's run: one heavy thing at most, then exit."""
    report = SimReport()
    today = today or dt.date.today().isoformat()
    conn = get_conn(db)
    try:
        init_db(conn)
        throttled, report.throttle = _throttled(conn)
        if throttled:
            report.did = "throttled since the last run: nothing simulated this tick"
            return report
        docs: dict[str, dict] = {}
        for r in store.unrun_races(conn, today=today):
            date = r["race_date"]
            if date not in docs:
                docs[date] = shape_sim.card_inputs(conn, date)
                # tables only for a card the shape models have scored: they are what the tick fits
                # first on a fresh deploy, and they must not be fitted before the trip table is built
                if docs[date]["races"] and not store.has_tables(conn, date, sim_tables.VERSION):
                    out = fit_sim_tables.fit(date, conn=conn)
                    report.did = out.render() + (" -- simulating from the next tick" if out.written else "")
                    return report
            if simulate(conn, date, r["race_no"], report, doc=docs[date]):
                return report
        report.did = "nothing to simulate: every race still to run is simulated at its current inputs"
        incomplete = [f"{d} R{x['no']} ({x['why']})" for d, doc in docs.items() for x in doc["incomplete"] if not x["gone"]]
        if incomplete:
            report.did += "; not simulated: " + ", ".join(incomplete)
    except SimError as exc:
        report.errors.append(str(exc))
    finally:
        conn.close()
    return report


def verify(date: str, race_no: int, db: Path | None = None) -> tuple[bool, str]:
    """Re-run a race's newest stored inputs: identical bytes, or a failure."""
    conn = get_conn(db)
    try:
        init_db(conn)
        sim = store.latest_sim(conn, date, race_no)
        if sim is None:
            return False, f"{date} R{race_no} has no stored simulation"
        inputs = dict(sim["inputs"])
        as_of, version = inputs["tables"]
        tables = store.tables_row(conn, as_of, version)
        if tables is None:
            return False, f"the tables it names ({as_of}, {version}) are gone"
        text, _ = run_node(dict(inputs, tables=tables["tables"]))
    finally:
        conn.close()
    same = text == sim["result_text"]
    return same, f"{date} R{race_no} ({sim['fingerprint'][:12]}): " + ("identical" if same else "DIFFERENT from the stored result")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--next", action="store_true", help="the one race most in need")
    which.add_argument("--date", help="YYYY-MM-DD, with --race")
    which.add_argument("--verify", nargs=2, metavar=("DATE", "RACE"))
    ap.add_argument("--race", type=int)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.verify:
        ok, msg = verify(a.verify[0], int(a.verify[1]), a.db)
        print(msg)
        return 0 if ok else 1
    if a.date and a.race is None:
        ap.error("--date needs --race")
    with job_log.running("sim_card", a.db) as outcome:
        if a.next:
            report = next_tick(a.db)
        else:
            report = SimReport()
            conn = get_conn(a.db)
            try:
                init_db(conn)
                simulate(conn, a.date, a.race, report)
            except SimError as exc:
                report.errors.append(str(exc))
            finally:
                conn.close()
        outcome["ok"] = not report.errors
        outcome["detail"] = "; ".join([report.did, *report.checks, report.throttle, *report.errors])
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
