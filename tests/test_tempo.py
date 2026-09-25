"""Race pace, read against HKJC's own standard for the race.

derive/tempo, ingest/standards, jobs/rebuild_tempo, and every reader: the
Results and Form Guide pace (`query/pace`), and Lookup's RACE PACE filter.

The numbers below are Happy Valley, 23 Sep 2026, and HKJC's reference
sectionals as published on 25 Aug 2026. The old reading called R6 "Fast" --
it was run exactly to the Class 4 standard -- and R4 "Fast" though its leader
was slower than standard to the 800m.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from hkrd.derive import tempo as T
from hkrd.ingest.standards import StandardsError, parse_standards
from hkrd.jobs import rebuild_tempo
from hkrd.jobs.scrape_standards import refresh as real_refresh
from hkrd.query import lookup, pace as pace_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-09-23"
# HV turf, Class 4, as published.
STD = {("HV", "Turf", 1000, "4"): {"class_key": "4", "standard_time": 57.10,
                                   "sections": "12.50;21.35;23.25"},
       ("HV", "Turf", 1200, "4"): {"class_key": "4", "standard_time": 69.70,
                                   "sections": "23.55;22.65;23.50"},
       ("HV", "Turf", 1200, "3"): {"class_key": "3", "standard_time": 69.50,
                                   "sections": "23.50;22.50;23.50"},
       ("HV", "Turf", 1650, "4"): {"class_key": "4", "standard_time": 99.85,
                                   "sections": "27.95;23.65;24.25;24.00"}}


def _race(no, distance, cls="4"):
    return {"race_date": DATE, "race_no": no, "distance": distance, "race_class": cls}


def test_the_leader_is_the_fastest_to_each_mark() -> None:
    # A leads to the 800 and B passes it; the leader's splits follow whoever
    # was in front at each mark, as HKJC's race sectional does.
    lead = T.leader_splits([[23.0, 23.5, 24.0], [23.4, 22.8, 23.1], [23.2, 23.4]])
    assert [round(x, 2) for x in lead] == [23.0, 23.2, 23.1]


def test_a_race_run_to_its_standard_is_neutral() -> None:
    """R6, HV 1000m C4: 12.54 / 21.24 against 12.50 / 21.35. The old reading
    pooled it with Sha Tin's straight 1000m and called it Fast."""
    row = T.tempo(_race(6, 1000), [[12.54, 21.24, 23.16]],
                  STD[("HV", "Turf", 1000, "4")], True, -0.0032)
    assert row["early_to"] == 400
    assert abs(row["early_dev"]) < 0.1
    assert row["band"] == "Neutral"


def test_a_fast_first_800_reads_fast() -> None:
    """R5, HV 1650m C4: 50.65s to the 800m against a 51.60s standard."""
    row = T.tempo(_race(5, 1650), [[27.92, 22.73, 24.36, 24.52]],
                  STD[("HV", "Turf", 1650, "4")], True, -0.0032)
    assert row["early_dev"] == pytest.approx(-0.78, abs=0.01)
    assert row["band"] in ("Fast", "Very Fast")


def test_the_day_s_track_moves_the_standard() -> None:
    """A slow day (winners 1% over standard) makes the same split ordinary."""
    split = [[23.75, 22.74, 23.13]]
    dry = T.tempo(_race(7, 1200, "3"), split, STD[("HV", "Turf", 1200, "3")], True, 0.0)
    wet = T.tempo(_race(7, 1200, "3"), split, STD[("HV", "Turf", 1200, "3")], True, 0.01)
    assert dry["early_dev"] > wet["early_dev"]
    assert dry["band"] in ("Slow", "Very Slow") and wet["band"] == "Neutral"


def test_the_meeting_variant_needs_a_meeting() -> None:
    races = [{"winner_time": 70.0, "standard_time": 69.5}] * 2
    assert T.meeting_variant(races) is None
    assert T.meeting_variant(races + [{"winner_time": 69.5, "standard_time": 69.5}]) \
        == pytest.approx(70.0 / 69.5 - 1)


def test_a_blank_cell_takes_the_nearest_class_and_says_so() -> None:
    row, exact = T.pick_standard(STD, "HV", "Turf", 1200, "5")
    assert row["class_key"] == "4" and exact is False
    row, exact = T.pick_standard(STD, "HV", "Turf", 1200, "3")
    assert row["class_key"] == "3" and exact is True
    # Group, 4YO and the legacy "0" all read the Group row.
    assert T.standard_class("G3") == T.standard_class("4YO") == T.standard_class("0") == "G"
    assert T.pick_standard(STD, "ST", "Turf", 2400, "4") == (None, False)


def test_bands_run_fast_to_slow() -> None:
    assert [T.band(x) for x in (-0.5, -0.2, 0.0, 0.25, 0.6)] == list(T.BANDS)


# ── the standards page ─────────────────────────────────────────────────────

def _flight(cells: list[dict]) -> str:
    data = {"sectionalData": {"updateDate": {"dateValue": 1787587200000}, "children": [
        {"displayTitle": {"value": "Happy Valley  Turf Track"}, "children": [
            {"distance": {"value": "1200"}, "children": cells}]}]}}
    payload = "31:" + json.dumps(["$", "$L32", "1", data])
    return f"<script>self.__next_f.push({json.dumps([1, payload])})</script>"


def _cell(label, total, secs):
    keys = ("start2000M", "start201600M", "start161200M", "start12800M",
            "start8400M", "start400M")
    vals = ["", "", ""] + secs
    return {"class": {"targetItem": {"displayLabel": {"value": label}}},
            "standardTimes": {"value": total},
            **{k: {"value": v} for k, v in zip(keys, vals)}}


def test_the_standards_are_read_from_the_page_data() -> None:
    rows = parse_standards(_flight([
        _cell("Class 4", "1.09.70", ["23.55", "22.65", "23.50"]),
        _cell("Class 1", "-", ["-", "-", "-"]),           # HKJC's blank cell
        _cell("Group Race", "1.08.95", ["23.50", "22.25", "23.20"])]))
    assert [(r["class_key"], r["standard_time"], r["sections"]) for r in rows] == [
        ("4", 69.70, "23.55;22.65;23.50"), ("G", 68.95, "23.50;22.25;23.20")]
    assert rows[0]["venue"] == "HV" and rows[0]["updated"] == "2026-08-25"


def test_a_page_without_the_data_is_an_error_not_an_empty_table() -> None:
    with pytest.raises(StandardsError):
        parse_standards("<html>layout changed</html>")


def test_the_refresh_asks_once_a_week(tmp_path, monkeypatch) -> None:
    from hkrd.jobs import scrape_standards
    calls = []
    monkeypatch.setattr(scrape_standards, "fetch_standards",
                        lambda **k: calls.append(1) or [
                            {"venue": "HV", "surface": "Turf", "distance": 1200,
                             "class_key": "4", "standard_time": 69.7,
                             "sections": "23.55;22.65;23.50", "updated": "2026-08-25"}])
    db = tmp_path / "s.db"
    now = dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc)
    assert real_refresh(db, now=now) is not None
    assert real_refresh(db, now=now + dt.timedelta(days=3)) is None
    assert real_refresh(db, now=now + dt.timedelta(days=8)) is not None
    assert len(calls) == 2


# ── the job and the readers ────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = get_conn(path)
    init_db(conn)
    races = [(4, 1200, "Class 4", [[23.58, 23.03, 23.40], [23.7, 23.1, 23.3]]),
             (6, 1000, "Class 4", [[12.54, 21.24, 23.16], [12.6, 21.3, 23.1]]),
             (5, 1650, "Class 4", [[27.92, 22.73, 24.36, 24.52], [28.0, 22.9, 24.2, 24.5]])]
    with transaction(conn):
        upsert.upsert_standard_times(conn, [
            {"venue": v, "surface": s, "distance": d, "class_key": c, **row}
            for (v, s, d, c), row in STD.items()], fetched_at="2026-09-24T00:00:00")
        for no, dist, cls, secs in races:
            upsert.upsert_races(conn, [{"race_date": DATE, "race_no": no, "venue": "HV",
                                        "surface": "Turf", "course": "C",
                                        "distance": dist, "race_class": cls}])
            upsert.upsert_runners(conn, [{
                "race_date": DATE, "race_no": no, "horse_no": i + 1,
                "horse_name": f"H{no}{i}", "place": str(i + 1),
                "finish_time": round(sum(s), 2),
                "section_times": ";".join(str(x) for x in s)}
                for i, s in enumerate(secs)])
            # A runner quicker than its field, which the old Lookup filter
            # labelled "Very Slow".
            conn.execute("INSERT INTO runner_pace (race_date, race_no, horse_no, "
                         "early_dev, pace_style, derive_version) "
                         "VALUES (?, ?, 1, -1.2, 'Leader', 't')", (DATE, no))
    conn.close()
    return path


def test_the_job_writes_one_reading_per_race(db) -> None:
    out = rebuild_tempo.rebuild(db)
    assert out.rows_written == 3 and not out.errors
    conn = get_conn(db)
    bands = dict(conn.execute("SELECT race_no, band FROM race_tempo").fetchall())
    conn.close()
    assert bands[6] == "Neutral"
    # Rebuilding one date replaces that date's rows rather than adding to them.
    assert rebuild_tempo.rebuild(db, date=DATE).rows_written == 3


def test_no_standards_is_said_not_read_as_no_pace(tmp_path) -> None:
    """Skipped and counted, not an error: a card scrape before the first
    weekly refresh has nothing wrong with it."""
    path = tmp_path / "empty.db"
    out = rebuild_tempo.rebuild(path)
    assert out.rows_written == 0 and out.missing_standards and not out.errors


def test_the_pages_read_the_seconds_against_the_standard(db) -> None:
    rebuild_tempo.rebuild(db)
    conn = get_conn(db)
    p = pace_q.race_pace(DATE, 6, conn=conn)
    bulk = pace_q.race_pace_bulk([(DATE, 6), (DATE, 5)], conn=conn)
    conn.close()
    assert p["measured"] and p["band"] == "Neutral"
    assert "Class 4 standard" in p["note"]
    assert bulk[(DATE, 6)]["band"] == "Neutral" and set(bulk) == {(DATE, 6), (DATE, 5)}


def test_lookup_filters_on_the_race_s_pace_not_the_runner_s(db) -> None:
    rebuild_tempo.rebuild(db)
    conn = get_conn(db)
    band5 = conn.execute("SELECT band FROM race_tempo WHERE race_no = 5").fetchone()[0]
    runs = lookup.search_runs(race_pace=band5, conn=conn)
    slow = lookup.search_runs(race_pace="Very Slow", conn=conn)
    conn.close()
    assert {r.race_no for r in runs} == {5}
    assert slow == []
