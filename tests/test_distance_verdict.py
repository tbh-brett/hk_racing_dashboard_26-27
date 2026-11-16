"""The new-horse profile's trip verdict: derive/distance_verdict reads it,
query/background puts it on a runner and scores it.

The sentences are real ones from HKJC's "Intro to New Horses" (names kept
where the name is the point). The meeting further down is made up and small
enough to score by hand.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.derive import distance_verdict
from hkrd.query import background as background_q
from hkrd.store import background, upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.mark.parametrize("about, verdict, ceiling", [
    # Lucky Balerion's own: a sprinter that may reach a mile.
    ("LUCKY BALERION should be able to handle sprint races and could reach up to mile "
     "in future.", "miler", 1600),
    # An open end is one step past the trip it names.
    ("The bay gelding should be most suited over 1400M or upper in distance and fine at "
     "Happy Valley.", "miler", 1600),
    ("The bay gelding should be able to handle mile or farther in distance while dirt is "
     "fine.", "stayer", 1800),
    ("JOYFIELD is projected to be best suited for distances beyond a mile with a proven "
     "affinity for affected ground.", "stayer", 1800),
    ("The bay gelding should be most suited for distances under 1600M and second dam by "
     "RED RANSOM, he is expected to handle the dirt as well.", "sprinter", 1400),
    ("As a son of DUBAWI and family member of European Horse of the Year GOLDIKOVA, the bay "
     "gelding should be most suited in middle distance and stayer events.", "stayer", 2000),
    # "The son of X" is the horse, not its sire.
    ("The son of DEEP FIELD should be most effective from 1200M to 1800M and fine on both "
     "turf and dirt.", "stayer", 1800),
    # "Looks like his dam's sire ... that should" is still the horse.
    ("The brown gelding looks like his sire BRAZEN BEAU and dam's sire LONHRO that should "
     "be most suited under mile in distance and fine at Happy Valley.", "sprinter", 1400),
    # The second half of "sprint and mile events" carries the first's judgement.
    ("The bay gelding should be most suited in sprint and mile events while soft going is "
     "fine.", "miler", 1600),
    # A horse's name is not a trip.
    ("LOVELY MILES is out of a winning mare. The bay gelding should be a precocious "
     "sprinter and no issue at Happy Valley.", "sprinter", 1200),
    # The last sentence that judges a trip is the verdict.
    ("He could handle 2000M as a young horse. The chestnut should now be a sprinter.",
     "sprinter", 1200),
    # No space after the full stop.
    ("Two of his full-brother and sister won from 1630M to 1800M.The chestnut should be most "
     "effective in mile or middle-distance events.", "stayer", 2000),
])
def test_the_profile_is_read_into_how_far_the_horse_should_get(about, verdict, ceiling):
    got = distance_verdict.read(about)
    assert got and (got["verdict"], got["ceiling_m"]) == (verdict, ceiling), got


@pytest.mark.parametrize("about", [
    # About the family, not the horse.
    "His dam PARDON collected 4 wins in middle distance events and her off-spring prefers "
    "sprint races.",
    "His dam BRIGHT PLANET has had 3 winning foals that could handle up to 1500M.",
    # A race already run is not a prospect.
    "The bay gelding won twice over 1487M at Beverley that should be fine at Happy Valley "
    "while soft going is fine.",
    "He trialled nicely and should be competitive.",
    "", None,
])
def test_a_profile_that_names_no_trip_for_the_horse_says_nothing(about):
    assert distance_verdict.read(about) is None


# ── on the card, and in the record ──────────────────────────────────────────
#
#   S  "1600M or farther" -> stayer.  M  "up to mile" -> miler.
#   N  a profile with no trip.         X  no profile.
#   1 Oct, 1400 m: S won at 4.0, M 2nd at 2.0, X 3rd; place paid 18 / 12 / 14.
#   20 Nov, 1600 m (after the test starts): M won, S 4th; no dividends stored.

ABOUT = {"S": "The bay gelding should be most suited over 1600M or farther.",
         "M": "The chestnut should be fine to handle up to mile.",
         "N": "He trialled nicely and should be competitive."}


@pytest.fixture()
def db(tmp_path) -> Path:
    path = tmp_path / "vd.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-10-01", "race_no": 1, "venue": "ST", "distance": 1400},
            {"race_date": "2026-11-20", "race_no": 1, "venue": "ST", "distance": 1600}])
        field = {"2026-10-01": [("S", "1", 4.0), ("M", "2", 2.0), ("X", "3", 3.0)],
                 "2026-11-20": [("S", "4", 5.0), ("M", "1", 2.5), ("X", "2", 2.0)]}
        upsert.upsert_runners(conn, [
            {"race_date": d, "race_no": 1, "horse_no": i + 1, "horse_name": h,
             "place": pl, "win_odds": o}
            for d, rows in field.items() for i, (h, pl, o) in enumerate(rows)])
        upsert.upsert_dividends(conn, [
            {"race_date": "2026-10-01", "race_no": 1, "pool": "PLACE",
             "combination": str(n), "dividend_per_10": v}
            for n, v in ((1, 18.0), (2, 12.0), (3, 14.0))])
        background.upsert_backgrounds(conn, [
            {"horse_name": h, "brand_no": "L1", "profiled_for": "2026-09-01",
             "path": f"/p/{h}", "url": f"https://x/{h}", "import_type": "PPG",
             "about": a, "fetched_at": "2026-09-02"} for h, a in ABOUT.items()])
    conn.close()
    background_q._CACHE.clear()
    return path


def verdicts(db, distance, before):
    conn = get_conn(db)
    try:
        return background_q.distance_verdicts(["S", "M", "N", "X"], distance=distance,
                                              before=before, conn=conn)
    finally:
        conn.close()


def test_the_verdict_shows_where_the_trip_bears_on_it(db):
    # 1600 m, after both ran at 1400: a step up for each.
    got = verdicts(db, 1600, "2026-11-20")
    assert set(got) == {"S", "M"}
    assert got["S"]["verdict"] == "stayer" and got["S"]["step_up"] and got["S"]["longest_m"] == 1400
    assert got["S"]["url"] == "https://x/S" and "1600M or farther" in got["S"]["sentence"]
    # 1400 m again: no further than before, and under 1600 m.
    assert verdicts(db, 1400, "2026-11-20") == {}
    # A first start: shown at 1600 m and over only.
    assert verdicts(db, 1200, "2026-10-01") == {}
    first = verdicts(db, 1650, "2026-10-01")
    assert set(first) == {"S", "M"} and not first["M"]["step_up"] and first["M"]["longest_m"] is None


def test_the_record_scores_the_verdict_by_run_date_win_and_place_apart(db):
    conn = get_conn(db)
    try:
        rec = background_q.record(conn=conn)["distance"]
    finally:
        conn.close()
    assert rec["test_from"] == background_q.DISTANCE_TEST_FROM
    eras = {e["key"]: {(x["verdict"], x["trip"]): x for x in e["lines"]} for e in rec["eras"]}
    short, long_ = "under 1600 m", "1600 m and over"
    s = eras["origin"][("stayer", short)]
    assert (s["horses"], s["runs"], s["won"], s["placed"]) == (1, 1, 1, 1)
    assert s["ae"] == round(1 / ((1 / 4.0) / (1 / 4 + 1 / 2 + 1 / 3)), 2)
    assert (s["paid_runs"], s["place_return"]) == (1, 1.8)
    assert eras["origin"][("miler", short)]["place_return"] == 1.2
    # 20 Nov is a test run; no dividends stored, so no place return rather than a loss.
    t = eras["test"][("stayer", long_)]
    assert (t["runs"], t["won"], t["paid_runs"], t["place_return"]) == (1, 0, 0, None)
    assert eras["test"][("miler", long_)]["won"] == 1
    # The horse with no profile is not scored; N never ran.
    assert all(x["horses"] <= 1 for e in eras.values() for x in e.values())


def test_the_record_endpoint_carries_the_verdict(db, monkeypatch):
    monkeypatch.setenv("HKRD_DB", str(db))
    from fastapi.testclient import TestClient
    from hkrd.api.app import app
    got = TestClient(app).get("/api/background/record")
    assert got.status_code == 200, got.text
    assert [e["key"] for e in got.json()["distance"]["eras"]] == ["test", "origin"]
