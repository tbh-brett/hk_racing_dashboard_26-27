"""Import a simulation the lab froze before a meeting, as that meeting's record (racesim-SPEC §9.3).

    python -m hkrd.jobs.import_sim_record --file ops/records/sim_2026-10-19.json.gz

Once, for 19 Oct 2026: the first meeting the simulation was scored on, simulated in the lab before the
job existed. The file bundles the lab's card, its per-race results and the frozen pre-race columns
(ops/records). Each race becomes a `race_sim` row dated when the lab froze it, with `runner_sim` from
the frozen columns. It names tables ('lab-v0') and an engine that production does not hold, so
`sim_card --verify` says it cannot be re-run -- which is true, and is why it is marked as the lab's.
Running it again changes nothing.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from hkrd.store import job_log
from hkrd.store import sim as store
from hkrd.store.connect import get_conn, init_db, transaction

__all__ = ["run"]

TABLES = "lab-v0"


def _f(v: str) -> float | None:
    return None if v in ("", None) else float(v)


def run(path: Path, db: Path | None = None) -> str:
    bundle = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    meta, card = bundle["meta"], bundle["card"]
    date = meta["card"]
    frozen = {(int(r["race"]), int(r["no"])): r for r in bundle["frozen"]}
    conn = get_conn(db)
    written = kept = 0
    try:
        init_db(conn)
        for race in bundle["races"]:
            no = int(race["id"][-2:])
            text = json.dumps(race, separators=(",", ":"))
            fp = "lab-" + hashlib.sha256(text.encode()).hexdigest()[:60]
            if fp in store.sim_keys(conn, date, no):
                kept += 1
                continue
            eng = {e["no"]: e for e in race["engine"]}
            rows = []
            for s in race["summary"]["runners"]:
                f, e = frozen[(no, s["no"])], eng[s["no"]]
                rows.append(dict(horse_no=s["no"], v2_win=_f(f["v2_win"]), v2_place=_f(f["v2_place"]),
                                 sim_win=_f(f["sim_win"]), sim_top3=_f(f["sim_top3"]), p_lead=_f(f["p_lead"]),
                                 first_call_pos=_f(f["first_call_pos"]), p_front_third=_f(f["p_front_third"]),
                                 p_back_third=_f(f["p_back_third"]), p_nocover=_f(f["p_nocover"]),
                                 p_wide3=_f(f["p_settle_3wide_plus"]), p_held=_f(f["p_held_up"]),
                                 trip_cost=_f(f["trip_cost_lengths"]), p_win_if_led=_f(f["p_win_if_led"]),
                                 engine_held=_f(f["engine_held"]), engine_nocover_most=_f(f["engine_nocover_most"]),
                                 engine_ground=_f(f["engine_ground_lengths"]), run_share=e.get("run_share"),
                                 round_share=e.get("round_share"), through_share=e.get("through_share"),
                                 coupled_win=e.get("win"), coupled_top3=e.get("top3")))
            row = dict(race_date=date, race_no=no, fingerprint=fp, simulated_at=meta["frozen_at"],
                       engine_version=f"lab-{date}", tables_as_of=date, tables_version=TABLES,
                       v2_version=meta["v2"], shape_version="lab-study-16oct", settings_json=meta["settings"],
                       cpu_seconds=0.0)
            inputs = dict(card=dict(meta=card["meta"], races=card["races"]), tables=[date, TABLES], race_no=no,
                          settings=meta["settings"], source=meta["source"])
            with transaction(conn):
                store.write_sim(conn, row, inputs, text, rows)
            written += 1
    finally:
        conn.close()
    return f"import_sim_record {date}: {written} races written, {kept} already held"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, required=True)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    with job_log.running("import_sim_record", a.db) as outcome:
        outcome["detail"] = run(a.file, a.db)
        outcome["ok"] = True
    print(outcome["detail"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
