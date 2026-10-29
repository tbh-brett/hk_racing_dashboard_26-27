"""The vendored engine and simulator, and the node runner (racesim-SPEC §10, §11.1, §12).

The four files in web/assets/shape were the lab's, byte for byte, until 29 Oct, when engine.js and
track-view.js took Happy Valley's course from HKJC's map (the lab drew it as Sha Tin's shape made
smaller). Both the engine and the simulator are deterministic -- a rerun of 25 Oct race 7 matched the
lab's batch to the last digit -- so every check here is EXACT equality with a file written before:

  the runner on 25 Oct race 7 at small settings   race-sim/out/sim_small_2026-10-25_r7.json
  the runner on the whole 25 Oct card, lab settings  race-sim/out/sim_2026-10-25.json (slow: HKRD_SLOW=1)
  the engine over the season's Sha Tin replays     the lab's engine on the 64 Sha Tin of them (29 Oct)
  the engine over all the season's replays          this engine on all 107, 43 at Happy Valley (29 Oct)
  Brett's 19 Oct marks against the replay           race-shape-2d/out/marks_check_2026-10-19.json
  the engine's traffic against the comments        the lab's on the Sha Tin replays; this engine's on all

Sha Tin is the lab's to the byte: the Happy Valley course changes nothing on it.

Skipped where node is not installed, like test_js_parses.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "web" / "assets" / "shape"
RUNNER = ROOT / "hkrd" / "jobs" / "sim_node" / "run_race.cjs"
HELPERS = Path(__file__).parent / "shape_node"
FIX = Path(__file__).parent / "fixtures" / "shape"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# The files by sha256: the lab's as vendored (23 Oct, engine v3), but for engine.js and track-view.js,
# which since 29 Oct carry Happy Valley's course (the lab's were d4813257... and 0514a271...).
VENDORED = {
    "engine.js": "9b0c3f39997c5c1dc3bc5af99b83e16fdc391148cd6b65d3c2bc2696f3eea150",
    "sectionals.js": "97a03a9dcc4676d24eef5f76688f32d9d414b2d1b23205e26407d415385e08dc",
    "sim-core.js": "f2e381c8bad646ad63e5b2af8595ae9040306c50795635fd584fcc06488c7d9b",
    "track-view.js": "d469ecda28458ee0453044a4221d4e12c99ee4eb72471252779dfd4e7b9982ee",
}
SMALL = dict(N_SUM=2000, N_ANCHOR=2000, PASSES=40, N_ENGINE=20, N_COUPLED=20, SIGMA_MULT=1.045)
LAB = dict(N_SUM=20000, N_ANCHOR=10000, PASSES=40, N_ENGINE=300, N_COUPLED=600, SIGMA_MULT=1.045)


def _load(name: str):
    path = FIX / name
    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text(encoding="utf-8"))


def _unzip(name: str, dest: Path) -> Path:
    out = dest / name.removesuffix(".gz")
    with gzip.open(FIX / name, "rb") as a:
        out.write_bytes(a.read())
    return out


def _run(doc: dict | str) -> subprocess.CompletedProcess:
    data = doc if isinstance(doc, str) else json.dumps(doc)
    return subprocess.run([NODE, str(RUNNER), str(ENGINE)], input=data.encode("utf-8"),
                          capture_output=True, timeout=1800)


def _card_doc(race_no: int, settings: dict) -> dict:
    return dict(card=_load("card_inputs_2026-10-25.json.gz"), tables=_load("sim_tables_2026-10-25.json.gz"),
                race_no=race_no, settings=settings)


@pytest.mark.parametrize("name", sorted(VENDORED))
def test_the_vendored_files_are_the_labs_byte_for_byte(name: str) -> None:
    assert hashlib.sha256((ENGINE / name).read_bytes()).hexdigest() == VENDORED[name]


def test_one_race_reproduces_the_lab_exactly() -> None:
    done = _run(_card_doc(7, SMALL))
    assert done.returncode == 0, done.stderr.decode()
    out = json.loads(done.stdout)
    spread = out.pop("settle_spread")
    ref = _load("sim_small_2026-10-25_r7.json")
    assert out == ref["races"][0]
    assert (spread["sd"], spread["miss"]) == (ref["meta"]["settle_sd"], ref["meta"]["settle_miss"])


def test_identical_input_gives_identical_bytes() -> None:
    doc = json.dumps(_card_doc(7, dict(SMALL, N_SUM=300, N_ANCHOR=300, PASSES=5, N_ENGINE=3, N_COUPLED=3)))
    a, b = _run(doc), _run(doc)
    assert a.returncode == 0 and a.stdout == b.stdout


@pytest.mark.parametrize("doc,why", [
    ("not json", "not one JSON document"),
    (json.dumps({"card": {"races": []}}), "has no tables"),
    (json.dumps({"card": {"races": [{"no": 1, "runners": []}]}, "tables": {}, "race_no": 3,
                 "settings": SMALL}), "no race 3"),
])
def test_bad_input_exits_1_with_a_message(doc: str, why: str) -> None:
    done = _run(doc)
    assert done.returncode == 1 and why in done.stderr.decode() and not done.stdout


@pytest.mark.skipif(not os.environ.get("HKRD_SLOW"), reason="12 minutes of CPU: HKRD_SLOW=1 before a release")
def test_the_whole_card_at_lab_settings_reproduces_the_lab() -> None:
    ref = _load("sim_2026-10-25.json.gz")
    for race in ref["races"]:
        done = _run(_card_doc(int(race["id"][-2:]), LAB))
        assert done.returncode == 0, done.stderr.decode()
        out = json.loads(done.stdout)
        out.pop("settle_spread")
        assert out == race, race["id"]


def _helper(script: str, *args: Path, env: dict | None = None) -> None:
    done = subprocess.run([NODE, str(HELPERS / script), *map(str, args)], capture_output=True,
                          env={**os.environ, "ENGINE_DIR": str(ENGINE), **(env or {})}, timeout=900)
    assert done.returncode == 0, done.stderr.decode()


def _sha_tin_only(tmp_path: Path) -> Path:
    races = json.loads(_unzip("races_2026-10-14.json.gz", tmp_path).read_text(encoding="utf-8"))
    out = tmp_path / "races_st.json"
    out.write_text(json.dumps(dict(races, races=[r for r in races["races"] if r["venue"] == "ST"])), encoding="utf-8")
    return out


def test_the_engine_over_the_seasons_sha_tin_replays_is_the_labs(tmp_path: Path) -> None:
    marks = _unzip("marks_2026-10-23.json.gz", tmp_path)
    _helper("test_core.cjs", _sha_tin_only(tmp_path), tmp_path / "out.json", env={"MARKS": str(marks)})
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8")) == _load("test_core_out_st.json")


def test_the_engine_over_all_the_seasons_replays_is_as_recorded(tmp_path: Path) -> None:
    races, marks = _unzip("races_2026-10-14.json.gz", tmp_path), _unzip("marks_2026-10-23.json.gz", tmp_path)
    _helper("test_core.cjs", races, tmp_path / "out.json", env={"MARKS": str(marks)})
    got = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert got["section_marks_hit"] == "4638/4638" and got["backwards_steps"] == 0
    assert got == _load("test_core_out.json")


def test_brett_s_19_oct_marks_against_the_replay_are_the_labs(tmp_path: Path) -> None:
    races, marks = _unzip("races_2026-10-19.json.gz", tmp_path), _unzip("marks_2026-10-23.json.gz", tmp_path)
    _helper("marks_check.cjs", races, marks, tmp_path / "out.json")
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8")) == _load("marks_check_2026-10-19.json")


def test_the_engines_traffic_against_the_comments_is_the_labs_at_sha_tin_and_as_recorded_on_all(tmp_path: Path) -> None:
    _helper("engine_traffic_check.cjs", _sha_tin_only(tmp_path), tmp_path / "st.json")
    assert json.loads((tmp_path / "st.json").read_text(encoding="utf-8")) == _load("engine_traffic_check_st.json")
    _helper("engine_traffic_check.cjs", _unzip("races_2026-10-14.json.gz", tmp_path), tmp_path / "all.json")
    assert json.loads((tmp_path / "all.json").read_text(encoding="utf-8")) == _load("engine_traffic_check.json")


def test_brett_s_happy_valley_reads_against_the_replay(tmp_path: Path) -> None:
    """Brett's reads of eleven HV videos (29 Oct): the lab's engine agreed with 10 of 27, the HV course
    alone 11; the straight fan, the drift round the home bend and the later run start 13 or 14 (one race turns
    on a front-runner's own fan). The misses
    left are mostly settling lanes the comments don't give (the engine sends those to the rail)."""
    _helper("hv_reads_check.cjs", _unzip("races_2026-10-14.json.gz", tmp_path), FIX / "hv_reads_2026-10-29.json",
            tmp_path / "out.json")
    got = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert got["races"] == 11 and got["checks"] == 27 and got["agree"] >= 13
    ok = {(r["race"], r["horse"], r["d"]) for r in got["rows"] if r["ok"]}
    assert ("2026-10-07_04", "ABSOLUTE HONOUR", 100) in ok and ("2026-09-16_08", "LE ZONDA", 300) in ok


_HV = """
const E = require(process.env.ENGINE_DIR + "/engine.js"), T = require(process.env.ENGINE_DIR + "/track-view.js");
const out = {};
for (const D of [1000, 1200, 1650, 1800, 2200]) {
  const g = E.geometry({ venue: "HV", surface: "Turf", distance: D }), bs = E.bends(g, D);
  out[D] = { chute: g.chute, deg: bs.reduce((a, b) => a + (b.to - b.from) / b.R * 180 / Math.PI, 0),
             first: bs.length ? bs[bs.length - 1].name : null, lap: g.parts.reduce((a, p) => a + p.len, 0),
             turn: g.parts.reduce((a, p) => a + p.deg, 0) };
}
const g = E.geometry({ venue: "HV", surface: "Turf", distance: 1650 }), p0 = T.railPoint(g, 0, true), p1 = T.railPoint(g, 1450, true);
out.closes = Math.hypot(p1.x - p0.x, p1.y - p0.y);
out.straight_normal = [T.railPoint(g, 150, true).nx, T.railPoint(g, 150, true).ny];
const st = E.geometry({ venue: "ST", surface: "Turf", distance: 1200 });
out.st = { label: st.label, R: E.bends(st, 1200).map(b => b.R) };
process.stdout.write(JSON.stringify(out));
"""


def test_happy_valley_is_its_own_course_from_the_hkjc_map() -> None:
    done = subprocess.run([NODE, "-e", _HV], capture_output=True, env={**os.environ, "ENGINE_DIR": str(ENGINE)},
                          timeout=60)
    assert done.returncode == 0, done.stderr.decode()
    got = json.loads(done.stdout)
    assert got["1650"]["lap"] == pytest.approx(1450) and got["1650"]["turn"] == pytest.approx(360)
    assert got["1650"]["deg"] == pytest.approx(360, abs=0.5)            # one lap, every bend once
    assert got["1000"]["chute"] == {"from": 937.5, "to": 1000} and got["1000"]["deg"] == pytest.approx(162, abs=0.5)
    assert got["1200"]["chute"]["from"] == 1150 and got["1200"]["first"] == "left side"     # up the left side first
    assert got["1800"]["chute"] is None and got["2200"]["deg"] > 500
    assert got["closes"] < 0.01 and got["straight_normal"] == [0, 1]   # the rail meets itself; outward is down the page
    assert got["st"] == {"label": "Sha Tin turf", "R": [pytest.approx(519.5 / 3.141592653589793)]}   # the home bend only
