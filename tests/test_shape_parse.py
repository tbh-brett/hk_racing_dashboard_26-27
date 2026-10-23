"""The comment parse and the trip table (racesim-SPEC §4, §12).

derive/lateral, derive/runway, derive/contacts, derive/trip and jobs/rebuild_trip. The rules of
§4.2 one by one, the placeholder rule of §4.3, and §11.2's equality with the lab: the reference
files are the lab's own races files (the season to 14 Oct, 19 Oct with the Comments on Running,
21 Oct with the stewards' report only), each runner carrying its texts and the reading the lab
made of them. On the 23 Oct pull the port also matches the lab's parser on every one of the
archive's 103,766 comments and reports; that check needs the pull, so it lives with the lab.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from hkrd.derive import contacts, lateral, runway, trip
from hkrd.jobs import rebuild_trip
from hkrd.store import shape as store
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIX = Path(__file__).parent / "fixtures" / "shape"
FILES = ("races_2026-10-14.json.gz", "races_2026-10-19.json.gz", "races_2026-10-21.json.gz")


def _races(name: str) -> list[dict]:
    with gzip.open(FIX / name, "rt", encoding="utf-8") as f:
        return json.load(f)["races"]


# ── §11.2: equal to the lab on its reference files ──────────────────────────────────────────────
@pytest.mark.parametrize("name", FILES)
def test_every_runners_reading_is_the_labs(name: str) -> None:
    for r in _races(name):
        D, Lh = r["distance"], lateral.straight_length(r["venue"], r["surface"])
        for h in r["runners"]:
            p = lateral.merge(lateral.rich(h["cor"], D, Lh), lateral.rich(h["inc"], D, Lh))
            if runway.runs_from_behind(h["pos"]):
                run = runway.run_of(h["cor"], D, Lh)
                if run:
                    p["run"] = run
            assert json.loads(json.dumps(p)) == h["p"], (r["id"], h["no"])


@pytest.mark.parametrize("name", FILES)
def test_every_races_contacts_are_the_labs(name: str) -> None:
    for r in _races(name):
        Lh = lateral.straight_length(r["venue"], r["surface"])
        assert json.loads(json.dumps(contacts.contacts(r["runners"], r["distance"], Lh))) == r["events"], r["id"]


# ── §4.2 rule 1: a lane is what the text says; not stated stays None, never the rail ───────────
def test_a_lane_not_stated_is_none_not_the_rail() -> None:
    p = lateral.parse("Began well, settled in midfield, one-paced in the straight.", 1200)
    assert [p[f"lane_{k}"] for k in ("settle", "mid", "turn", "straight")] == [None] * 4
    assert p["stated"] == 0
    assert lateral.summarise(p)["rail_pre"] == 0 and lateral.summarise(p)["lane_max_pre"] is None


def test_very_wide_is_four_wide_and_away_from_the_fence_is_not_the_rail() -> None:
    assert lateral.parse("Raced very wide throughout.", 1200)["lane_settle"] == 4
    assert lateral.parse("Raced 1 out 1 back.", 1200)["lane_settle"] == 2
    assert lateral.parse("Raced away from the inside fence.", 1200)["lane_settle"] == 2
    assert lateral.parse("Raced on the rail in midfield.", 1200)["lane_settle"] == 1


def test_a_lane_given_at_a_distance_holds_from_there_not_for_the_whole_phase() -> None:
    p = lateral.parse("Raced 2 wide, 3 wide from 650M.", 1400)
    assert p["lane_settle"] == 2 and p["at"] == [[650, 3]] and p["lane_turn"] == 3


def test_a_lane_into_the_straight_is_the_top_of_the_straight_not_the_turn() -> None:
    p = lateral.parse("Raced in rear on the outside, very wide into straight, finished off evenly.", 1200)
    assert p["lane_entry"] == 4 and p["lane_turn"] == 3


# ── rule 2: phases, and the straight's length by course ─────────────────────────────────────────
def test_the_straight_is_310_at_happy_valley_430_on_sha_tin_turf_380_on_the_awt() -> None:
    assert lateral.straight_length("HV", "Turf") == 310
    assert lateral.straight_length("ST", "Turf") == 430
    assert lateral.straight_length("ST", "AWT") == 380


def test_a_hold_up_before_the_430_at_sha_tin_is_in_the_straight() -> None:
    text = "Held up between the 450 Metres and the 200 Metres."
    assert lateral.parse(text, 1400, 430)["traffic_straight"] == 1
    assert lateral.parse(text, 1400)["traffic_pre"] == 1


# ── rule 3: cover is a horse in front to sit behind, not ground ─────────────────────────────────
def test_cover_is_not_ground() -> None:
    a = lateral.rich("Raced 3 wide with cover.", 1200)
    assert a["lane_settle"] == 3 and a["cover_settle"] == 1 and a["nocover_settle"] == 0
    b = lateral.rich("Raced one out without cover.", 1200)
    assert b["lane_settle"] == 2 and b["nocover_settle"] == 1 and b["cover_settle"] == 0


# ── rule 4: the run from the turn, from the Comments on Running only ───────────────────────────
RUNWAY = [  # the lab's own cases (turns/runway.py)
    ("Taken back from the widest draw, raced at tail, 7 lengths or so back, still last turning in, made no impression.", None),
    ("Raced in midfield on the inside, saved ground on turn, angled out 400M, no daylight run early in straight.", "through"),
    ("Raced towards rear on the inside, angled out 500M, made some progress early in straight.", "round"),
    ("Dropped out from widest draw, held up last, commenced move out wide 700m out, lacked pace to challenge.", "round"),
    ("Box seat, unable to secure clear run until 350m out, ran on well to edge ahead final 50m in tight finish.", None),
    ("Settled towards rear, switched out rounding the Turn, steadied near the 350M, finished off nicely.", "round"),
    ("Settled in midfield on the rail, checked on heels over 800 metres out, switched out for a run upon straightening, quickened well.", "through"),
    ("Taken back from a wide draw, raced at rear on the outside, very wide into straight, finished off evenly.", "round"),
    ("Wide in mid-field without cover, pushed along into home straight, no threat to leaders.", None),
    ("Showed great speed to lead, under pressure 400m out, headed 200m out, weakened final 100m.", None),
    ("Improved to challenge 300m out, ran on well to lead 150m out.", None),
    ("CLASS shifted out and bumped HERO near the 300m.", None),
    ("Raced midfield, found a gap between runners 200m out, ran on strongly.", "through"),
    ("Rail position in mid-field, under pressure 600m out, dropped away quickly in home straight.", None),
    ("Jumped awkwardly and lost ground, raced keenly towards the rear, swung wide for home but unable to finish off.", "round"),
    ("Raced 2 wide behind midfield, saved ground on turn, finished off evenly.", "through"),
    ("Moderately away, raced 3 wide slightly behind midfield, 3 lengths or so back turning in, showed some response.", None),
    ("Settled down in second half of the field, wide no cover from 700M, widest straightening with some work to do.", "round"),
    ("Jumped well from gate 13, taken back to race 2 wide behind midfield, angled out 450M, 5 lengths back turning in.", "through"),
    ("Slowly away, rail position in rear of mid-field, pushed along 700m out, never involved, eased 200m out.", None),
    ("Raced in midfield between runners throughout. Drifted slightly further back passing half-way.", None),
    ("Modest beginning, recovered to rear of midfield, tight between runners at around first turn, unable to quicken after straightening.", None),
    ("Raced in rear, went wide to challenge, never dangerous.", "round"),
    ("Saved ground on turn, angled out after passing 500M, finished off well.", "round"),
    ("Settled towards rear on rail, shifted out away from fading runners over 600M out, one-paced in home straight.", "round"),
    ("Raced 2 wide behind midfield, wider turning in, showed limited response early in straight.", "round"),
    ("Passing the 300 Metres, CLASS shifted out and bumped HERO.", None),
    ("Raced in rear, shifted out and keen 900m out, ridden along 500m out, no impression.", None),
]


@pytest.mark.parametrize("text,want", RUNWAY)
def test_which_way_the_run_was_made(text: str, want: str | None) -> None:
    got = runway.run_way(text, 1200 if "800 metres" not in text else 1400, 430)
    assert (got["how"] if got else None) == want


def test_the_run_is_read_only_for_a_horse_not_up_with_the_pace() -> None:
    text = "Raced in rear, went wide to challenge, never dangerous."
    up = trip.run_row(text, None, distance=1200, venue="ST", surface="Turf", running_positions="2 2 1")
    back = trip.run_row(text, None, distance=1200, venue="ST", surface="Turf", running_positions="9 8 3")
    assert up["run_way"] is None and back["run_way"] == "round"


def test_the_stewards_shifted_out_is_a_contact_not_a_run_round() -> None:
    row = trip.run_row(None, "Near the 400 Metres shifted out and bumped HERO.", distance=1200,
                       venue="ST", surface="Turf", running_positions="9 8 3")
    assert row["run_way"] is None


# ── rule 5: contacts placed where the report says, with who moved into whom (23 Oct, verbatim) ───
def _field(names: dict[int, str], no: int, text: str) -> list[dict]:
    return [dict(no=k, name=v, inc=text if k == no else None) for k, v in names.items()]


def test_shifted_out_and_away_from_a_then_contact_with_b_moved_into_b() -> None:
    names = {2: "BEAUTY SHOW", 4: "DOUBLE ALPHA", 8: "HARMONY FIRE"}
    ev = contacts.contacts(_field(names, 2, "Shortly after the start shifted out and away from HARMONY FIRE "
                                            "and made contact with DOUBLE ALPHA."), 1000, 310)
    assert [(e["d"], e["a"], e["b"], e["dir"]) for e in ev] == [(940, 2, 4, 1)]


def test_lay_in_and_away_from_x_moved_this_horse_not_x() -> None:
    names = {7: "NOT USUAL DOUBLE", 10: "DRAGON GENERATION"}
    ev = contacts.contacts(_field(names, 7, "When placed under pressure in the Home Straight raced greenly and "
                                            "near the 300 Metres lay in and away from DRAGON GENERATION."), 1200, 430)
    assert [(e["d"], e["a"], e["b"], e["dir"]) for e in ev] == [(300, 7, None, -1)]


def test_the_early_stages_of_the_home_straight_are_early_in_the_straight() -> None:
    names = {2: "THE HORSE"}
    text = ("When questioned regarding his riding out of the horse B Avdulla sated that when placed under "
            "pressure in the early stages of the Home Straight his mount lay out.")
    assert [e["d"] for e in contacts.contacts(_field(names, 2, text), 1200, 310)] == [270]


def test_bumped_by_x_which_shifted_in_moved_x() -> None:
    names = {3: "CHIU CHOW GOLF", 5: "COMIC"}
    ev = contacts.contacts(_field(names, 5, "On jumping was bumped by CHIU CHOW GOLF which shifted in."), 1400, 430)
    assert [(e["d"], e["a"], e["b"], e["dir"]) for e in ev] == [(1385, 3, 5, -1)]


# ── §4.3: HKJC's placeholder is not a comment ──────────────────────────────────────────────────
PLACEHOLDER = "No Comments on Running information for this horse."


def test_the_placeholder_is_no_comment() -> None:
    assert lateral.is_placeholder(PLACEHOLDER) and lateral.is_placeholder("No report.")
    assert not lateral.is_placeholder("Raced wide without cover.")
    a = trip.run_row(PLACEHOLDER, "No report.", distance=1200, venue="HV", surface="Turf", running_positions="3 3 3")
    b = trip.run_row(None, None, distance=1200, venue="HV", surface="Turf", running_positions="3 3 3")
    assert a == b and a["has_comment"] == 0


def test_store_and_derive_agree_on_the_columns() -> None:
    assert store.TRIP_COLUMNS == trip.COLUMNS


# ── §4.4: the job ────────────────────────────────────────────────────────────────────────────
def _seed(conn, comments: list[tuple]) -> None:
    with transaction(conn):
        upsert.upsert_races(conn, [dict(race_date="2026-10-21", race_no=1, venue="HV", surface="Turf",
                                        distance=1200, race_class="4", course="A")])
        upsert.upsert_runners(conn, [dict(race_date="2026-10-21", race_no=1, horse_no=n, horse_name=f"HORSE {n}",
                                          place=n, win_odds=5.0, running_positions=f"{n} {n} {n}")
                                     for n in (1, 2, 3)])
        upsert.upsert_comments(conn, [dict(race_date="2026-10-21", race_no=1, horse_no=h, source=s,
                                           comment_text=t) for h, s, t in comments])


def test_rebuild_replaces_a_meetings_rows_when_its_comments_land(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = get_conn(db)
    init_db(conn)
    _seed(conn, [(n, "corunning", PLACEHOLDER) for n in (1, 2, 3)])
    out = rebuild_trip.rebuild(db, date="2026-10-21")
    assert (out.rows_written, out.with_comment, out.races_written) == (3, 0, 1)
    _seed(conn, [(3, "corunning", "Raced 3 wide without cover, wide on the turn."),
                 (2, "incident", "Near the 600 Metres was bumped by HORSE 3 which shifted in.")])
    out = rebuild_trip.rebuild(db, date="2026-10-21")
    assert (out.rows_written, out.with_comment, out.contacts) == (3, 1, 1)
    rows = {r["horse_no"]: dict(r) for r in conn.execute("SELECT * FROM runner_trip")}
    assert rows[3]["wide3_pre"] == 1 and rows[3]["nocover_pre"] == 1 and rows[1]["lane_settle"] is None
    ev = json.loads(conn.execute("SELECT events_json FROM race_trip").fetchone()[0])
    assert [(e["a"], e["b"], e["d"]) for e in ev] == [(3, 2, 600)]
    conn.close()
