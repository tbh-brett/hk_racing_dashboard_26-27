"""Where each import came from: ingest/newhorse, jobs/scrape_background and
query/background.

`newhorse_profiles.json` is four real profiles from HKJC's "Intro to New
Horses" as the site rendered them on 28 Sep 2026, and a trimmed index. The
record's meeting further down is made up and small enough to score by hand.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hkrd.ingest import newhorse
from hkrd.jobs import scrape_background
from hkrd.query import background as background_q
from hkrd.store import background, upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def fx() -> dict:
    return json.loads((FIXTURES / "newhorse_profiles.json").read_text(encoding="utf-8"))


def entry(p: dict) -> dict:
    e = dict(p["entry"])
    return dict(e, horse_name=newhorse.clean_name(e["horse_name"]),
                race_no=int(e["race_no"]))


def parsed(fx: dict, name: str) -> dict:
    p = next(p for p in fx["profiles"] if p["entry"]["horse_name"].strip() == name)
    return newhorse.parse(p["html"], entry(p))


# ── reading a profile ───────────────────────────────────────────────────────

def test_the_horse_that_started_it(fx):
    got = parsed(fx, "CHIU CHOW GOLF")
    assert (got["import_type"], got["origin"], got["sire"]) == ("PPG", "New Zealand", "Ocean Park")
    assert got["prev_owner"] == "Price Bloodstock Management Ltd"
    assert got["prev_trainer"] == "Allan & Jason Williams"
    assert got["prev_country"] == "Australia"
    assert got["agents"] == ["Price Bloodstock"]
    assert (got["sale_kind"], got["sale_ccy"], got["sale_amount"]) == ("yearling", "NZD", 70000.0)
    # "finished 2nd in an 800M jump out": trialled, did not win.
    assert got["trial_won"] is False
    assert (got["sire_hk_starters"], got["sire_hk_winners"]) == (27, 13)
    assert got["url"].endswith("racedate=20260503&raceNo=2&brandNo=L216")


def test_two_agents_on_one_horse_are_both_kept(fx):
    got = parsed(fx, "STUNNING BUNDLE")
    assert got["agents"] == ["John Foote Bloodstock", "Price Bloodstock"]
    assert got["buyer"] == "John Foote Bloodstock"


def test_a_raced_import_keeps_its_old_name_and_record(fx):
    got = parsed(fx, "GLORYSPEED")
    assert (got["import_type"], got["prev_name"]) == ("PP", "ST EDWARD")
    assert (got["overseas_wins"], got["overseas_starts"]) == (1, 4)
    assert got["prev_trainer"] == "Mick Price & Michael Kent (Jnr)"
    # Trained in Australia: the dam's country earlier in the paragraph must
    # not be read as the stable's.
    assert got["prev_country"] == "Australia"
    assert got["trial_won"] is None          # raced: its trials are not scored
    assert got["sale_amount"] is None        # unsold at its reserve


def test_a_trial_won_is_read(fx):
    assert parsed(fx, "FAITHFUL WARRIOR")["trial_won"] is True


def test_a_page_without_a_profile_is_refused():
    with pytest.raises(newhorse.NewHorseError):
        newhorse.parse("<div>maintenance</div>", {"horse_name": "X", "brand_no": "L1",
                                                  "race_date": "2026-09-27",
                                                  "race_no": 1, "path": "p"})


@pytest.mark.parametrize("raw, want", [("STEADFAST FORT ( L286 )", "STEADFAST FORT"),
                                       ("PERFECT ONE ", "PERFECT ONE"),
                                       ("Chiu Chow Golf", "CHIU CHOW GOLF")])
def test_the_index_name_is_the_card_name(raw, want):
    assert newhorse.clean_name(raw) == want


def test_one_agent_is_one_name():
    assert newhorse.agents("purchased by David Price, owned by Price Bloodstock "
                           "Management Ltd and bought by John Foote at AUD$ 40,000") \
        == ["John Foote Bloodstock", "Price Bloodstock"]


def test_the_index_lists_every_profile_oldest_first(fx, monkeypatch):
    monkeypatch.setattr(newhorse, "fetch_json", lambda url, body, **k: {"data": fx["index"]})
    got = newhorse.index()
    assert [e["race_date"] for e in got] == sorted(e["race_date"] for e in got)
    assert all(e["path"].startswith("/sitecore/content/Sites/JCRW/Local/NewHorse/")
               for e in got)
    assert {e["race_date"] for e in got} == {"2025-09-07", "2026-09-27"}


# ── the job ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path) -> Path:
    path = tmp_path / "bg.db"
    conn = get_conn(path)
    init_db(conn)
    conn.close()
    return path


def fake_site(monkeypatch, fx, profiles):
    calls = []
    entries = [entry(p) for p in profiles]
    by_path = {p["entry"]["path"]: p["html"] for p in profiles}

    def profile(path, **k):
        calls.append(path)
        return by_path[path]
    monkeypatch.setattr(scrape_background.newhorse, "index", lambda **k: entries)
    monkeypatch.setattr(scrape_background.newhorse, "profile", profile)
    return calls


def test_a_profile_is_fetched_once(db, fx, monkeypatch):
    calls = fake_site(monkeypatch, fx, fx["profiles"])
    first = scrape_background.scrape(db)
    assert (first.listed, first.stored, first.already, first.errors) == (4, 4, 0, [])
    second = scrape_background.scrape(db)
    assert (second.stored, second.already) == (0, 4)
    assert len(calls) == 4


def test_a_horse_profiled_again_later_is_read_again(db, fx, monkeypatch):
    fake_site(monkeypatch, fx, fx["profiles"][:1])
    scrape_background.scrape(db)
    again = copy.deepcopy(fx["profiles"][0])
    again["entry"]["race_date"] = "2026-06-01"
    again["entry"]["path"] += "-again"
    calls = fake_site(monkeypatch, fx, [fx["profiles"][0], again])
    got = scrape_background.scrape(db)
    assert (got.stored, got.already, calls) == (1, 1, [again["entry"]["path"]])
    conn = get_conn(db)
    try:
        assert background.profiled(conn) == {"CHIU CHOW GOLF": "2026-06-01"}
    finally:
        conn.close()


def test_a_profile_that_will_not_parse_is_an_error_not_the_end(db, fx, monkeypatch):
    broken = copy.deepcopy(fx["profiles"][:2])
    broken[0]["html"] = "<p>nothing here</p>"
    fake_site(monkeypatch, fx, broken)
    got = scrape_background.scrape(db)
    assert got.stored == 1 and len(got.errors) == 1 and not got.ok


# ── the record ──────────────────────────────────────────────────────────────
#
# Three imports, one meeting each side of TEST_FROM:
#   A  Price Bloodstock, Australia, 2025/26: won at 4.0, rating 52 -> 64
#   B  no agent, New Zealand, 2025/26:       5th, rating 52 -> 50
#   C  Price Bloodstock, 2026/27:            2nd at 2.0 (favourite)

def bg_row(name, profiled, agents, country):
    return {"horse_name": name, "brand_no": "L1", "profiled_for": profiled,
            "path": f"/p/{name}", "url": "https://x", "import_type": "PPG",
            "agents": agents, "prev_country": country, "fetched_at": "2026-09-28"}


@pytest.fixture()
def record_db(db) -> Path:
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": 1, "venue": "ST", "distance": 1200}
            for d in ("2026-03-01", "2026-04-01", "2026-10-04")])
        field = {  # date: [(horse, place, odds, rating)]
            "2026-03-01": [("A", "5", 8.0, 52), ("B", "3", 5.0, 52), ("X", "1", 2.0, 60)],
            "2026-04-01": [("A", "1", 4.0, 64), ("B", "5", 3.0, 50), ("X", "2", 2.5, 61)],
            "2026-10-04": [("C", "2", 2.0, 52), ("X", "1", 3.0, 62)],
        }
        upsert.upsert_runners(conn, [
            {"race_date": d, "race_no": 1, "horse_no": i + 1, "horse_name": h,
             "place": pl, "win_odds": o, "rating": rt}
            for d, rows in field.items() for i, (h, pl, o, rt) in enumerate(rows)])
        background.upsert_backgrounds(conn, [
            bg_row("A", "2026-02-20", ["Price Bloodstock"], "Australia"),
            bg_row("B", "2026-02-20", [], "New Zealand"),
            bg_row("C", "2026-10-01", ["Price Bloodstock"], None)])
    conn.close()
    background_q._CACHE.clear()
    return db


def lines(rec, era):
    e = next(x for x in rec["eras"] if x["key"] == era)
    return {(x["dimension"], x["group"]): x for x in e["lines"]}


def test_the_record_keeps_the_test_apart_from_the_idea(record_db):
    conn = get_conn(record_db)
    try:
        rec = background_q.record(conn=conn)
    finally:
        conn.close()
    origin, test = lines(rec, "origin"), lines(rec, "test")
    price = origin[("agent", "Price Bloodstock")]
    # A: two runs, one win; rating 52 -> 64, a rise of ten or more.
    assert (price["horses"], price["runs"], price["won"]) == (1, 2, 1)
    assert (price["rating_change"], price["rose"]) == (12, 1)
    # One win, against the chances its two runs' closing prices gave it.
    assert price["ae"] == round(1 / ((1 / 8.0) / (1 / 8 + 1 / 5 + 1 / 2)
                                     + (1 / 4.0) / (1 / 4 + 1 / 3 + 1 / 2.5)), 2)
    assert origin[("agent", "no agent named")]["won"] == 0
    assert origin[("trained in", "New Zealand")]["horses"] == 1
    assert test[("agent", "Price Bloodstock")]["won"] == 0
    assert test[("trained in", "not stated")]["horses"] == 1


def test_a_card_shows_the_background_it_has(record_db):
    conn = get_conn(record_db)
    try:
        got = background_q.for_horses(["A", "Z"], conn=conn)
    finally:
        conn.close()
    assert list(got) == ["A"] and got["A"]["agents"] == ["Price Bloodstock"]


def test_the_endpoint_answers(record_db, monkeypatch):
    monkeypatch.setenv("HKRD_DB", str(record_db))
    from fastapi.testclient import TestClient
    from hkrd.api.app import app
    got = TestClient(app).get("/api/background/record")
    assert got.status_code == 200, got.text
    assert [e["key"] for e in got.json()["eras"]] == ["test", "origin"]
