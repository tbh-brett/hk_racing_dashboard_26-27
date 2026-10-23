"""The vendored engine and simulator, and the node runner (racesim-SPEC §10, §11.1, §12).

The four files in web/assets/shape are the lab's, byte for byte, and both the engine and the
simulator are deterministic -- a rerun of 25 Oct race 7 matched the lab's batch to the last digit --
so every check here is EXACT equality with a file the lab wrote:

  the runner on 25 Oct race 7 at small settings   race-sim/out/sim_small_2026-10-25_r7.json
  the runner on the whole 25 Oct card, lab settings  race-sim/out/sim_2026-10-25.json (slow: HKRD_SLOW=1)
  the engine over the season's replays             race-shape-2d/out/test_core_out.json
  Brett's 19 Oct marks against the replay           race-shape-2d/out/marks_check_2026-10-19.json
  the engine's traffic against the comments        race-sim/out/engine_traffic_check.json

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

# The lab's files, by the sha256 they had when they were vendored (23 Oct, engine v3).
VENDORED = {
    "engine.js": "d4813257ad80650a98a331e761db39a391f290bf497c513a6f3b2e0adc203c12",
    "sectionals.js": "97a03a9dcc4676d24eef5f76688f32d9d414b2d1b23205e26407d415385e08dc",
    "sim-core.js": "f2e381c8bad646ad63e5b2af8595ae9040306c50795635fd584fcc06488c7d9b",
    "track-view.js": "0514a271b1fdc1fc03cb75726e8e62bc419eb3b5c4e598850c8e866756800be3",
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


def test_the_engine_over_the_seasons_replays_is_the_labs(tmp_path: Path) -> None:
    races, marks = _unzip("races_2026-10-14.json.gz", tmp_path), _unzip("marks_2026-10-23.json.gz", tmp_path)
    _helper("test_core.cjs", races, tmp_path / "out.json", env={"MARKS": str(marks)})
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8")) == _load("test_core_out.json")


def test_brett_s_19_oct_marks_against_the_replay_are_the_labs(tmp_path: Path) -> None:
    races, marks = _unzip("races_2026-10-19.json.gz", tmp_path), _unzip("marks_2026-10-23.json.gz", tmp_path)
    _helper("marks_check.cjs", races, marks, tmp_path / "out.json")
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8")) == _load("marks_check_2026-10-19.json")


def test_the_engines_traffic_against_the_comments_is_the_labs(tmp_path: Path) -> None:
    races = _unzip("races_2026-10-14.json.gz", tmp_path)
    _helper("engine_traffic_check.cjs", races, tmp_path / "out.json")
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8")) == _load("engine_traffic_check.json")
