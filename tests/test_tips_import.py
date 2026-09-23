"""The tips import — resolving a payload against the card, and storing it.

The payload is `fixtures/tips_payload.json`. Its factcheck quotes are verbatim
subtitles, and every race and horse NUMBER in it is illustrative, so the card
it is checked against is seeded here as the fixture's own note asks: race 1 is
the real 23 Sep Happy Valley race 1, read off `racecard_en_20260923_r1.html`,
and races 2 to 9 are fields of fourteen. Nothing a tip says is invented here;
the tests only break the fixture one row at a time.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hkrd.ingest import racecard, racecard_zh
from hkrd.jobs import import_tips, repair_meeting, sync_horse_names
from hkrd.store import tips, upsert
from hkrd.store.connect import StoreError, get_conn, init_db, transaction

FIXTURES = Path(__file__).parent / "fixtures"
DATE = "2026-09-23"
RACES, FIELD = 9, 14
TABLES = ("connections_quote", "tipster_selection", "tips_quarantine")


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def payload() -> dict:
    return json.loads(fixture_text("tips_payload.json"))


@pytest.fixture()
def db(tmp_path) -> Path:
    path = tmp_path / "tips.db"
    real = racecard.parse_racecard(
        fixture_text("racecard_en_20260923_r1.html"), 1)
    races = [{"race_date": DATE, "race_no": n, "venue": "HV", "course": "C",
              "surface": "Turf", "distance": 1200}
             for n in range(1, RACES + 1)]
    runners = [{"race_date": DATE, "race_no": 1, "horse_no": r["horse_no"],
                "horse_name": r["horse_name"]} for r in real]
    # The fixture's one English quote names its horse, R7 #4, so the English
    # checksum has something to check it against: that horse is put there.
    runners += [{"race_date": DATE, "race_no": n, "horse_no": h,
                 "horse_name": ("MASSIVE SOVEREIGN" if (n, h) == (7, 4)
                                else f"HORSE {n}-{h}")}
                for n in range(2, RACES + 1) for h in range(1, FIELD + 1)]
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)
    conn.close()
    return path


@pytest.fixture()
def named(db) -> Path:
    """The same card, with race 1's Chinese names learned the way the sync
    job learns them: both real cards, paired on brand number."""
    pairs, _ = sync_horse_names.pair_names(
        racecard.parse_racecard(fixture_text("racecard_en_20260923_r1.html"), 1),
        racecard_zh.parse_racecard_zh(fixture_text("racecard_zh.html"), 1))
    conn = get_conn(db)
    with transaction(conn):
        tips.upsert_horse_names(conn, [{**p, "source": "racecard_zh",
                                        "seen_at": DATE} for p in pairs])
    conn.close()
    return db


def table(db: Path, name: str) -> list[tuple]:
    conn = get_conn(db)
    try:
        return sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {name}"))
    finally:
        conn.close()


def query(db: Path, sql: str, *args) -> list[tuple]:
    conn = get_conn(db)
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


# ── the fixture, as pushed ───────────────────────────────────────────────────

def test_the_fixture_imports_and_reports_what_it_wrote(db, payload):
    got = import_tips.run(payload, db=db).as_dict()
    assert got == {"race_date": DATE, "quotes": 7, "selections": 5,
                   "quarantined": 2,
                   "quarantine_reasons": {"name_unknown": 1, "unparsed": 1},
                   "unplaced_quotes": 1, "removed": 0,
                   "prices": 0, "prices_skipped": []}
    assert [len(table(db, t)) for t in TABLES] == [7, 5, 2]


def test_importing_the_same_payload_twice_changes_nothing(db, payload):
    """SPEC §3 acceptance: a re-push is safe because it converges."""
    first = import_tips.run(payload, db=db).as_dict()
    before = {t: table(db, t) for t in TABLES}
    second = import_tips.run(copy.deepcopy(payload), db=db).as_dict()
    assert second == first
    assert {t: len(v) for t, v in before.items()} == \
        {t: len(table(db, t)) for t in TABLES}
    assert {t: table(db, t) for t in TABLES} == before


def test_a_quote_is_keyed_on_its_video_and_second(db, payload):
    import_tips.run(payload, db=db)
    ids = {r[0] for r in query(db, "SELECT quote_id FROM connections_quote")}
    assert "-yNB49esPsI:108" in ids and "6pYy-AOsnh8:214" in ids


def test_a_quote_nobody_could_place_is_kept_with_no_runner(db, payload):
    """Unresolved is not wrong. 紅愛舍 is in the fixture with no numbers."""
    import_tips.run(payload, db=db)
    assert query(db, "SELECT race_no, horse_no FROM connections_quote "
                     "WHERE horse_said = '紅愛舍'") == [(None, None)]


def test_types_are_coerced_on_the_way_in(db, payload):
    import_tips.run(payload, db=db)
    assert query(db, "SELECT typeof(race_no), typeof(horse_no), "
                     "typeof(t_start), typeof(confidence) FROM "
                     "connections_quote WHERE quote_id = '-yNB49esPsI:108'") \
        == [("integer", "integer", "real", "real")]


# ── a bad number quarantines, and writes nothing live ────────────────────────

@pytest.mark.parametrize("kind, i, change, reason", [
    ("quotes", 0, {"race_no": 12}, "no_race"),        # a nine-race card
    ("quotes", 0, {"horse_no": 15}, "no_runner"),     # a field of fourteen
    ("quotes", 6, {"horse_no": 3}, "no_race"),        # a horse with no race
    ("selections", 0, {"race_no": 12}, "no_race"),
    ("selections", 0, {"horse_no": 13}, "no_runner"),  # race 1 has twelve
])
def test_a_number_the_card_does_not_have_is_quarantined(db, payload, kind, i,
                                                        change, reason):
    row = payload[kind][i]
    row.update(change)
    got = import_tips.run(payload, db=db)
    assert got.reasons[reason] == 1
    held = query(db, "SELECT raw, race_no FROM tips_quarantine "
                     "WHERE reason = ?", reason)
    assert len(held) == 1 and held[0][1] == row["race_no"]
    if kind == "quotes":
        assert got.quotes == 6
        assert not query(db, "SELECT 1 FROM connections_quote WHERE "
                             "quote_id = ?", tips.quote_id(row))
    else:
        assert got.selections == 4
        assert not query(db, "SELECT 1 FROM tipster_selection WHERE "
                             "tipster = ? AND race_no = ? AND horse_no = ?",
                         row["tipster"], row["race_no"], row["horse_no"])


def test_a_low_confidence_quote_is_quarantined(db, payload):
    payload["quotes"][5]["confidence"] = 0.5
    got = import_tips.run(payload, db=db)
    assert got.reasons["low_confidence"] == 1 and got.quotes == 6


def held_reason(db: Path, kind: str, row: dict) -> str | None:
    """Why this payload row was quarantined, or None if it was stored.
    Exactly one of the two must be true."""
    if kind == "quotes":
        stored = query(db, "SELECT 1 FROM connections_quote WHERE "
                           "quote_id = ?", tips.quote_id(row))
        identity = f"quote:{tips.quote_id(row)}"
    else:
        key = (row["source"], row["tipster"], DATE, row["race_no"],
               row["horse_no"])
        stored = query(db, "SELECT 1 FROM tipster_selection WHERE source = ? "
                           "AND tipster = ? AND race_date = ? AND race_no = ? "
                           "AND horse_no = ?", *key)
        identity = "selection:" + "|".join(str(k) for k in key)
    held = query(db, "SELECT reason FROM tips_quarantine WHERE "
                     "quarantine_id = ?", tips.quarantine_id(row["source"],
                                                             identity))
    assert bool(stored) != bool(held), (stored, held)
    return held[0][0] if held else None


def test_a_name_that_is_another_horse_is_quarantined(named, payload):
    """The checksum. 堅多福 is SUPER SICARIO, #1 in race 1 — not #4."""
    payload["selections"][0]["name_seen"] = "堅多福"
    import_tips.run(payload, db=named)
    assert held_reason(named, "selections", payload["selections"][0]) \
        == "name_mismatch"


def test_the_right_name_passes_the_checksum(named, payload):
    payload["selections"][0]["name_seen"] = "紅磚戰士"      # really #4
    import_tips.run(payload, db=named)
    assert held_reason(named, "selections", payload["selections"][0]) is None


@pytest.mark.parametrize("said", [
    "神駒馬靈",      # the name
    "馬靈",          # how 賽馬Fact Check's own subtitles say it
    "神駒馬零",      # one character wrong in four
])
def test_a_similar_name_passes(named, payload, said):
    """Brett, 2026-09-22: exact matching is too strict for names that came
    off YouTube. SOARING BRONCO is #3 in race 1."""
    payload["quotes"][0].update(race_no=1, horse_no=3, horse_said=said)
    import_tips.run(payload, db=named)
    assert held_reason(named, "quotes", payload["quotes"][0]) is None


def test_a_quote_naming_another_horse_is_quarantined(named, payload):
    payload["quotes"][0].update(race_no=1, horse_no=4, horse_said="馬靈")
    import_tips.run(payload, db=named)
    assert held_reason(named, "quotes", payload["quotes"][0]) \
        == "name_mismatch"


def test_a_name_that_is_nobody_on_a_named_card_is_quarantined(named, payload):
    """良駒好友 is the fixture's illustrative name for race 1 #4, and no
    horse in race 1 is called anything like it. Stored, it would have hung
    西門獨's top pick on RED BRICK WARRIOR."""
    import_tips.run(payload, db=named)
    assert held_reason(named, "selections", payload["selections"][0]) \
        == "name_mismatch"


def test_with_no_chinese_names_known_there_is_nothing_to_check(db, payload):
    """Before the sync has run, a Chinese name can neither pass nor fail —
    which is why the sync runs half an hour after the card is scraped."""
    import_tips.run(payload, db=db)
    assert held_reason(db, "selections", payload["selections"][0]) is None


def test_an_english_name_is_checked_the_same_way(db, payload):
    payload["quotes"][5].update(race_no=1, horse_no=4,
                                horse_said="Super Sicario")
    import_tips.run(payload, db=db)
    assert held_reason(db, "quotes", payload["quotes"][5]) == "name_mismatch"


def test_english_speech_to_text_close_enough_passes(db, payload):
    payload["quotes"][5].update(race_no=1, horse_no=1,
                                horse_said="Super Sicarrio")
    import_tips.run(payload, db=db)
    assert held_reason(db, "quotes", payload["quotes"][5]) is None


def test_an_english_name_that_is_nobody_is_quarantined(db, payload):
    """Every runner's English name is always known, so a name like none of
    them is no horse at this meeting."""
    payload["quotes"][5]["horse_said"] = "Dragon Sunrise"
    import_tips.run(payload, db=db)
    assert held_reason(db, "quotes", payload["quotes"][5]) == "name_unknown"


# ── the latest push is the answer ────────────────────────────────────────────

def test_a_push_that_quarantines_a_stored_quote_takes_it_out(db, payload):
    """Keeping the old row would keep exactly the guess the new push
    withdrew — a trainer's quote under a horse nobody can vouch for."""
    import_tips.run(payload, db=db)
    moved = copy.deepcopy(payload)
    moved["quotes"][0]["horse_no"] = 15
    got = import_tips.run(moved, db=db)
    assert got.removed == 1
    assert not query(db, "SELECT 1 FROM connections_quote WHERE quote_id = ?",
                     "-yNB49esPsI:108")


def test_a_fixed_row_leaves_quarantine(db, payload):
    """The same quote — same video, same second — placed correctly on the
    next push is no longer a failure, and the count falls."""
    broken = copy.deepcopy(payload)
    broken["quotes"][0]["race_no"] = 12
    import_tips.run(broken, db=db)
    assert len(table(db, "tips_quarantine")) == 3
    import_tips.run(payload, db=db)
    assert len(table(db, "tips_quarantine")) == 2


def test_a_corrected_checksum_leaves_quarantine(named, payload):
    # The fixture's other race-1 pick is illustrative too; give it #9's
    # real name so only the row under test is ever wrong.
    payload["selections"][1]["name_seen"] = "至高心得"
    wrong = copy.deepcopy(payload)
    wrong["selections"][0]["name_seen"] = "堅多福"
    import_tips.run(wrong, db=named)
    assert len(table(named, "tips_quarantine")) == 3
    payload["selections"][0]["name_seen"] = "紅磚戰士"
    import_tips.run(payload, db=named)
    assert len(table(named, "tips_quarantine")) == 2


def test_the_same_failure_re_extracted_is_one_quarantine_row(db, payload):
    """A new extraction run changes the timestamp and the confidence. The
    count on the ops page must not climb when nothing new has gone wrong."""
    payload["quotes"][0]["race_no"] = 12
    import_tips.run(payload, db=db)
    payload["generated_at"] = "2026-09-22T09:00:00Z"
    payload["quotes"][0]["confidence"] = 0.8
    import_tips.run(payload, db=db)
    assert len(table(db, "tips_quarantine")) == 3


# ── rejected outright ────────────────────────────────────────────────────────

def test_a_meeting_never_scraped_is_rejected_and_recorded(db, payload):
    payload["race_date"] = "2026-09-24"
    for row in payload["quarantine"]:
        row["race_date"] = "2026-09-24"
    with pytest.raises(import_tips.PayloadError, match="no races stored"):
        import_tips.run(payload, db=db)
    assert query(db, "SELECT ok FROM job_runs WHERE job = 'import_tips'") \
        == [(0,)]


def test_two_quotes_on_one_key_are_refused(db, payload):
    payload["quotes"][1]["t_start"] = payload["quotes"][0]["t_start"]
    with pytest.raises(import_tips.PayloadError, match="appears 2 times"):
        import_tips.run(payload, db=db)
    assert all(not table(db, t) for t in TABLES)


def test_the_database_refuses_a_runner_the_card_does_not_have(db, payload):
    """Behind the job's check, the foreign keys: a writer that skipped the
    resolver still cannot store a horse that is not in the race."""
    row = {**payload["quotes"][0], "race_date": DATE, "horse_no": 15,
           "fetched_at": payload["generated_at"]}
    conn = get_conn(db)
    try:
        with pytest.raises(StoreError):
            with transaction(conn):
                tips.upsert_quotes(conn, [row])
    finally:
        conn.close()


def test_repairing_a_meeting_takes_its_tips_with_it(db, payload):
    """A tip was resolved to a number on the card that was stored. When that
    card is removed as wrong, the tips resolved against it go too."""
    import_tips.run(payload, db=db)
    repair_meeting.repair(DATE, db=db)
    assert all(not table(db, t) for t in TABLES)


# ── through the endpoint ─────────────────────────────────────────────────────

@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setenv("HKRD_DB", str(db))
    from fastapi.testclient import TestClient
    from hkrd.api.app import app
    return TestClient(app)


def test_the_endpoint_returns_counts_and_a_second_post_changes_nothing(
        client, db, payload):
    first = client.post("/api/tips/import", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["quotes"] == 7
    before = {t: table(db, t) for t in TABLES}
    second = client.post("/api/tips/import", json=payload)
    assert second.json() == first.json()
    assert {t: table(db, t) for t in TABLES} == before


def test_the_endpoint_names_what_was_wrong_with_a_rejected_payload(
        client, payload):
    payload["payload_version"] = 2
    r = client.post("/api/tips/import", json=payload)
    assert r.status_code == 422
    assert "payload_version must be 1" in r.json()["detail"]


# ── the latest push for a source replaces it (Brett, 2026-09-22) ─────────────

def test_a_quote_left_out_of_the_next_push_is_removed(db, payload):
    import_tips.run(payload, db=db)
    again = copy.deepcopy(payload)
    gone = again["quotes"].pop(6)                  # the 紅愛舍 quote
    got = import_tips.run(again, db=db)
    assert got.removed == 1
    assert not query(db, "SELECT 1 FROM connections_quote WHERE quote_id = ?",
                     tips.quote_id(gone))


def test_a_source_the_next_push_does_not_carry_is_left_alone(db, payload):
    """A harvest that failed sends nothing for its source — and must not
    read as that source having withdrawn everything it said."""
    import_tips.run(payload, db=db)
    only_factcheck = copy.deepcopy(payload)
    only_factcheck["quotes"] = [q for q in payload["quotes"]
                                if q["source"] == "factcheck"]
    only_factcheck["selections"] = []
    only_factcheck["quarantine"] = [r for r in payload["quarantine"]
                                    if r["source"] == "factcheck"]
    assert import_tips.run(only_factcheck, db=db).removed == 0
    assert len(table(db, "tipster_selection")) == 5


def test_a_source_declared_with_nothing_is_cleared(db, payload):
    """How the extractor says "on.cc was read, and tips nothing now"."""
    import_tips.run(payload, db=db)
    again = copy.deepcopy(payload)
    again["selections"] = [s for s in again["selections"]
                           if s["source"] != "oncc"]
    again["sources"] = ["bryan", "factcheck", "oncc", "rtw_interview",
                        "threads"]
    assert import_tips.run(again, db=db).removed == 3
    assert not query(db, "SELECT 1 FROM tipster_selection "
                         "WHERE source = 'oncc'")


def test_a_corrected_number_leaves_no_stale_quarantine_row(db, payload):
    """A pick stored under a wrong number and later corrected is a
    different key. The old failure goes, because the source's latest push
    no longer produces it."""
    broken = copy.deepcopy(payload)
    broken["selections"][0]["horse_no"] = 13
    import_tips.run(broken, db=db)
    assert len(table(db, "tips_quarantine")) == 3
    import_tips.run(payload, db=db)
    assert len(table(db, "tips_quarantine")) == 2


def test_a_meeting_is_never_touched_by_another_meetings_push(db, payload):
    import_tips.run(payload, db=db)
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [{"race_date": "2026-09-27", "race_no": 1,
                                    "venue": "ST", "distance": 1200}])
    conn.close()
    other = {"payload_version": 1, "race_date": "2026-09-27",
             "generated_at": payload["generated_at"],
             "sources": ["factcheck"]}
    assert import_tips.run(other, db=db).removed == 0
    assert len(table(db, "connections_quote")) == 7


# ── picks heard through speech-to-text ───────────────────────────────────────

def heard(payload: dict, horse_no: int, name: str) -> dict:
    """The fixture's first factcheck quote's video, as a pick the analyst
    SAID: a race-1 number, and the name as speech-to-text wrote it."""
    q = payload["quotes"][0]
    pick = {"source": "factcheck", "tipster": "譚朗蔚", "race_no": 1,
            "horse_no": horse_no, "pick_rank": 1, "name_seen": name,
            "caption_kind": "asr", "url": q["url"]}
    payload["selections"].append(pick)
    return pick


def test_a_heard_pick_with_a_mangled_name_is_recorded_and_marked(named,
                                                                 payload):
    """紅磚戰士 is #4. 紅轉占士 shares one character with it and none with
    any other runner in the race: the spoken number carries it."""
    pick = heard(payload, 4, "紅轉占士")
    import_tips.run(payload, db=named)
    assert held_reason(named, "selections", pick) is None
    assert query(named, "SELECT caption_kind FROM tipster_selection WHERE "
                        "tipster = '譚朗蔚'") == [("asr",)]


def test_a_heard_pick_whose_name_is_another_horse_is_quarantined(named,
                                                                 payload):
    """A number misheard as another horse's is still caught: the name then
    points at the horse it really was."""
    pick = heard(payload, 4, "神駒馬零")               # #3's name
    import_tips.run(payload, db=named)
    assert held_reason(named, "selections", pick) == "name_mismatch"


# ── the roster the extractor reads ───────────────────────────────────────────

def test_the_roster_carries_every_runner_and_its_chinese_name(client, named):
    r = client.get(f"/api/tips/roster/{DATE}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["venue"], len(body["races"])) == ("HV", RACES)
    assert (body["runners"], body["named"]) == (12 + 8 * FIELD, 12)
    race1 = {x["horse_no"]: x for x in body["races"][0]["runners"]}
    assert race1[3]["horse_name"] == "SOARING BRONCO"
    assert race1[3]["name_zh"] == "神駒馬靈"


def test_the_roster_for_a_meeting_never_scraped_is_a_404(client):
    assert client.get("/api/tips/roster/2026-09-24").status_code == 404
