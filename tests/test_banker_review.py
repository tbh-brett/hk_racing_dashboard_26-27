"""The banker review: his claim and his verdict on every horse a ticket could
not pay without. Under test, each because the easy version is wrong:

  * which horses: a banker, or the only horse in a leg -- never a box or a
    pair -- and one row per horse in its race, whatever tickets carried it;
  * a claim written before the off stands once the race has gone; one written
    after is welcome, editable, and marked as written after;
  * a verdict needs a result, and must agree with it;
  * what the stewards' report says is read beside his verdict, never over it;
  * the desk's TAKE or PASS writes its sentence and confidence as the claim,
    and a decision made after the off writes none;
  * his notes on a race and on the night keep the moment they were first written;
  * the desk page and the routes sit behind the Bets page's own password.
"""
from __future__ import annotations

import datetime as dt
import json
import warnings

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from hkrd.api import auth
from hkrd.derive import circumstances
from hkrd.jobs import decide_ticket, review_banker
from hkrd.query import banker_review, review_record, suggest
from hkrd.store import review as store
from hkrd.store import tickets
from hkrd.store.connect import get_conn, init_db, transaction

DAY = "2026-11-11"
FIELD = [(1, "ALPHA"), (2, "BRAVO"), (3, "CHARLIE"), (4, "DELTA"), (5, "ECHO"),
         (6, "FOXTROT"), (7, "GOLF"), (8, "HOTEL")]
WIN = {1: 2.5, 2: 4.0, 3: 6.0, 4: 8.0, 5: 10.0, 6: 15.0, 7: 20.0, 8: 30.0}
HELD_UP = "Over the concluding stages was held up behind ALPHA."


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "review.db"
    monkeypatch.setenv("HKRD_DB", str(path))
    conn = get_conn(path)
    init_db(conn)
    yield conn
    conn.close()


def _race(conn, race_no=1, date=DAY, off="20:00"):
    conn.execute("INSERT INTO races (race_date, race_no, venue, distance, off_time) "
                 "VALUES (?, ?, 'HV', 1200, ?)", (date, race_no, off))
    for no, name in FIELD:
        conn.execute("INSERT INTO runners (race_date, race_no, horse_no, horse_name, win_odds) "
                     "VALUES (?, ?, ?, ?, ?)", (date, race_no, no, f"{name} {race_no}", WIN[no]))


def _result(conn, order, race_no=1, date=DAY):
    for place, h in enumerate(order, start=1):
        conn.execute("UPDATE runners SET place = ?, lengths_behind = ? WHERE race_date = ? "
                     "AND race_no = ? AND horse_no = ?",
                     (place, 0.0 if place == 1 else place * 0.5, date, race_no, h))


def _bet(conn, bet_id, sels, *, account="brett", bet_type="QPL_BANKER", date=DAY,
         race_no=1, formula=None):
    """sels: (race_no, horse_no, leg_no, is_banker)."""
    conn.execute("INSERT INTO bets (bet_id, account, race_date, race_no, bet_type, all_up_formula, "
                 "stake, returned, status, source) VALUES (?, ?, ?, ?, ?, ?, 40, 0, 'settled', "
                 "'statement')", (bet_id, account, date, race_no, bet_type, formula))
    for r, h, leg, banker in sels:
        conn.execute("INSERT INTO bet_selections (bet_id, race_no, horse_no, leg_no, is_banker) "
                     "VALUES (?, ?, ?, ?, ?)", (bet_id, r, h, leg, banker))


def _comment(conn, horse_no, text, race_no=1, source="incident"):
    conn.execute("INSERT INTO runner_comments VALUES (?, ?, ?, ?, ?)",
                 (DAY, race_no, horse_no, text, source))


def _decision(conn, race_no, banker, *, decision="accepted", late=0, reason=None):
    conn.execute("INSERT INTO ticket_decision (race_date, race_no, banker_no, decision, account, "
                 "unit_stake, reason, late, seen_json, engine, decided_at) VALUES "
                 "(?, ?, ?, ?, 'brett', 10, ?, ?, '{}', 'test', '2026-11-11T09:00:00+00:00')",
                 (DAY, race_no, banker, decision, reason, late))


def _keys(conn, account=None):
    return sorted(banker_review.key_horses(conn, DAY, DAY, account=account))


# ── what the record says ────────────────────────────────────────────────────

def test_the_record_is_read_with_the_blackbooks_own_rules():
    got = circumstances.read(
        "Jumped awkwardly. In the middle stages raced keenly. " + HELD_UP, None)
    assert [x["key"] for x in got] == ["start", "traffic", "keen"]
    assert got[0]["quote"].startswith("Stewards: ")
    # Steadied while pulling hard is the horse's own doing, not traffic.
    assert [x["key"] for x in circumstances.read(
        "Raced keenly and was steadied off the heels of BRAVO.", None)] == ["keen"]
    # The book leaves "no clear running" out as a forecast; as a record it is traffic.
    assert [x["key"] for x in circumstances.read(
        "Was denied clear running in the straight.", None)] == ["traffic"]
    assert [x["key"] for x in circumstances.read("Bled from both nostrils.", None)] == ["health"]
    assert [x["key"] for x in circumstances.read(None, "His saddle slipped early.")] == ["health"]
    assert [x["key"] for x in circumstances.read(None, "Settled 3-wide no cover.")] == ["wide"]
    assert circumstances.read(None, "No Comments on Running information for this horse.") == []
    assert {k for k, _ in circumstances.CIRCUMSTANCES} >= circumstances.FROM_RECORD


# ── which horses ─────────────────────────────────────────────────────────────

def test_a_horse_is_here_when_its_ticket_cannot_pay_without_it(db):
    with transaction(db):
        _race(db, 1)
        _race(db, 2)
        _bet(db, "qpl", [(1, 3, 0, 1), (1, 1, 0, 0), (1, 2, 0, 0)])        # banker 3
        _bet(db, "pair", [(1, 1, 0, 0), (1, 2, 0, 0)], bet_type="QIN")     # a pair: none
        _bet(db, "win", [(1, 5, 0, 0)], bet_type="WIN")                    # one horse: 5
        _bet(db, "old", [(1, 6, 0, 1), (1, 7, 0, 0)], account="personal")  # the April log
        _bet(db, "k3x4", [(1, 3, 1, 1), (1, 4, 1, 0), (2, 4, 2, 0)], account="kelvin",
             bet_type="ALLUP_3x4", race_no=None, formula="3x4")            # banker 3, then 4 alone
    assert _keys(db) == [(DAY, 1, 3), (DAY, 1, 5), (DAY, 1, 6), (DAY, 2, 4)]
    assert _keys(db, "kelvin") == [(DAY, 1, 3), (DAY, 2, 4)]
    assert _keys(db, "brett") == [(DAY, 1, 3), (DAY, 1, 5), (DAY, 1, 6)]
    # One row for horse 3, carried by both books.
    row = next(r for r in banker_review.meeting(DAY, conn=db)["horses"] if r["horse_no"] == 3)
    assert row["accounts"] == ["brett", "kelvin"]
    assert {t["bet_type"] for t in row["tickets"]} == {"QPL_BANKER", "ALLUP_3x4"}


def test_a_desk_take_is_here_before_any_statement_and_a_pass_is_not(db):
    with transaction(db):
        _race(db, 1)
        _decision(db, 1, 4, reason="leads and holds")
        _decision(db, 1, 5, decision="rejected")
        _decision(db, 1, 6, late=1)
    assert _keys(db) == [(DAY, 1, 4)]
    row = banker_review.meeting(DAY, conn=db)["horses"][0]
    # A desk decision from before the review existed: its sentence is the claim.
    assert row["review"]["reasoning"] == "leads and holds"
    assert row["review"]["claim_timing"] == "before"
    assert row["review"]["claim_source"] == "desk"


# ── the claim ────────────────────────────────────────────────────────────────

def test_a_claim_written_after_the_race_is_kept_and_marked_as_written_after(db):
    with transaction(db):
        _race(db)
        _result(db, [1, 2, 3, 4, 5, 6, 7, 8])
    t1 = dt.datetime(2026, 11, 20, 4, 0, tzinfo=dt.timezone.utc)
    review_banker.save(DAY, 1, 3, reasoning="drops in class", confidence="fair", now=t1)
    first = store.review(db, DAY, 1, 3)
    assert first["claim_late"] == 1 and first["claim_at"] == "2026-11-20T04:00:00+00:00"
    review_banker.save(DAY, 1, 3, reasoning="drops in class, drawn 1", now=t1 + dt.timedelta(days=1))
    again = store.review(db, DAY, 1, 3)
    assert again["claim_at"] == first["claim_at"]                 # the first save never moves
    assert again["claim_edited_at"] == "2026-11-21T04:00:00+00:00"
    assert again["confidence"] == "fair"                          # a field not sent is kept
    row = banker_review.horse(DAY, 1, 3, conn=db)
    assert row["review"]["claim_timing"] == "after"


def test_a_claim_written_before_the_off_stands_once_the_race_has_gone(db):
    with transaction(db):
        _race(db)
    review_banker.save(DAY, 1, 3, reasoning="leads from 1", confidence="strong")
    assert store.review(db, DAY, 1, 3)["claim_late"] == 0
    review_banker.save(DAY, 1, 3, reasoning="leads from 1, nothing else wants it")   # still open
    with transaction(db):
        _result(db, [5, 1, 2, 3, 4, 6, 7, 8])
    with pytest.raises(review_banker.ClaimClosed, match="stands as written"):
        review_banker.save(DAY, 1, 3, reasoning="it was always going to be close")
    with pytest.raises(review_banker.ClaimClosed):
        review_banker.save(DAY, 1, 3, confidence="weak")
    assert store.review(db, DAY, 1, 3)["reasoning"] == "leads from 1, nothing else wants it"
    # Who picked it is a fact, not a forecast, and the verdict is what the race is for.
    review_banker.save(DAY, 1, 3, picked_by="kelvin", verdict="right_beaten")
    got = store.review(db, DAY, 1, 3)
    assert got["picked_by"] == "kelvin" and got["verdict"] == "right_beaten"


# ── the verdict ──────────────────────────────────────────────────────────────

def test_a_verdict_needs_a_result_and_must_agree_with_it(db):
    with transaction(db):
        _race(db)
    with pytest.raises(review_banker.NoResult):
        review_banker.save(DAY, 1, 1, verdict="unsure")
    with transaction(db):
        _result(db, [1, 2, 3, 4, 5, 6, 7])                        # 8 did not finish
        db.execute("UPDATE runners SET place_code = 'WV' WHERE race_date = ? AND race_no = 1 "
                   "AND horse_no = 8", (DAY,))
    with pytest.raises(review_banker.ReviewError, match="needs a horse that did not place"):
        review_banker.save(DAY, 1, 2, verdict="mistake")
    with pytest.raises(review_banker.ReviewError, match="needs a horse that placed"):
        review_banker.save(DAY, 1, 5, verdict="right_paid")
    with pytest.raises(review_banker.ReviewError, match="verdict must be one of"):
        review_banker.save(DAY, 1, 5, verdict="unlucky")
    with pytest.raises(review_banker.ReviewError, match="not a circumstance"):
        review_banker.save(DAY, 1, 5, circumstances=["luck"])
    with pytest.raises(review_banker.ReviewError, match="withdrawn"):
        review_banker.save(DAY, 1, 8, verdict="unsure")
    with pytest.raises(review_banker.NotInRace):
        review_banker.save(DAY, 1, 99, verdict="unsure")
    review_banker.save(DAY, 1, 2, verdict="lucky")
    review_banker.save(DAY, 1, 5, verdict="unsure")
    assert store.review(db, DAY, 1, 2)["verdict"] == "lucky"


def test_his_circumstances_are_replaced_and_the_record_is_read_beside_them(db):
    with transaction(db):
        _race(db)
        _result(db, [1, 2, 3, 4, 5, 6, 7, 8])
        _comment(db, 5, HELD_UP)
    review_banker.save(DAY, 1, 5, verdict="circumstances", circumstances=["traffic", "start"],
                       verdict_note="no run until the 100")
    review_banker.save(DAY, 1, 5, circumstances=["pace"])
    got = store.review(db, DAY, 1, 5)
    assert got["circumstances"] == ["pace"] and got["verdict"] == "circumstances"
    assert got["verdict_note"] == "no run until the 100"
    row = banker_review.horse(DAY, 1, 5, conn=db)
    assert [x["key"] for x in row["record"]] == ["traffic"]       # the report, unchanged by him


# ── the desk ─────────────────────────────────────────────────────────────────

def _priced(conn, at=f"{DAY}T19:50:00"):
    for h, o in WIN.items():
        conn.execute("INSERT INTO odds_snapshots VALUES (?, 1, ?, ?, ?, ?)",
                     (DAY, h, at, o, max(1.1, o / 3)))


def test_the_desk_writes_its_sentence_and_confidence_as_the_claim(db):
    with transaction(db):
        _race(db)
        _priced(db)
    decide_ticket.decide(DAY, 1, 3, decision="accept", reason="leads from 1",
                         confidence="strong")
    got = store.review(db, DAY, 1, 3)
    assert (got["reasoning"], got["confidence"], got["picked_by"], got["claim_late"]) == \
        ("leads from 1", "strong", "me", 0)
    assert tickets.decision(db, DAY, 1, 3)["confidence"] == "strong"
    first = got["claim_at"]
    decide_ticket.decide(DAY, 1, 3, decision="accept", reason="leads from 1", confidence="fair")
    assert store.review(db, DAY, 1, 3)["claim_at"] == first      # a changed mind keeps the moment
    with pytest.raises(suggest.TicketError, match="confidence"):
        decide_ticket.decide(DAY, 1, 3, decision="accept", confidence="certain")
    with transaction(db):
        _result(db, [1, 2, 3, 4, 5, 6, 7, 8])
    # Decided after the off: kept by the desk as late, and no claim written.
    decide_ticket.decide(DAY, 1, 6, decision="accept", reason="knew it", confidence="strong")
    assert store.review(db, DAY, 1, 6) is None


# ── what it adds up to ───────────────────────────────────────────────────────

def test_the_summary_sets_his_circumstances_beside_the_record(db):
    with transaction(db):
        _race(db)
        _result(db, [1, 2, 3, 4, 5, 6, 7, 8])
        _bet(db, "a", [(1, 4, 0, 1), (1, 1, 0, 0)])
        _bet(db, "b", [(1, 5, 0, 1), (1, 1, 0, 0)])
        _bet(db, "c", [(1, 2, 0, 1), (1, 1, 0, 0)])
        _comment(db, 4, HELD_UP)
    review_banker.save(DAY, 1, 4, reasoning="drawn to lead", confidence="strong",
                       verdict="circumstances", circumstances=["traffic"])
    review_banker.save(DAY, 1, 5, verdict="mistake")
    s = review_record.summary(conn=db)
    assert (s["horses"], s["run"], s["judged"]) == (3, 3, 2)
    sides = {x["verdict"]: (x["n"], x["record_names_one"]) for x in s["record"]["sides"]}
    assert sides["circumstances"] == (1, 1) and sides["mistake"] == (1, 0)
    named = {x["key"]: x for x in s["record"]["named"]}
    assert named["traffic"]["you"] == 1 and named["traffic"]["record_agrees"] == 1
    assert named["pace"]["record_agrees"] is None                 # only he can say that one
    strong = [c for c in s["confidence"] if c["confidence"] == "strong" and c["timing"] == "after"]
    assert strong[0]["n"] == 1 and strong[0]["placed"] == 0
    assert {v["key"]: v["n"] for v in s["verdicts"]}["mistake"] == 1


def test_the_fortnightly_kinds_land_only_on_a_horse_with_a_review(db, tmp_path):
    with transaction(db):
        _race(db)
        _result(db, [1, 2, 3, 4, 5, 6, 7, 8])
        _bet(db, "a", [(1, 4, 0, 1), (1, 1, 0, 0)])
    review_banker.save(DAY, 1, 4, reasoning="drawn to lead", confidence="strong")
    src = tmp_path / "kinds.json"
    src.write_text(json.dumps([
        {"race_date": DAY, "race_no": 1, "horse_no": 4, "kind": "pace"},
        {"race_date": DAY, "race_no": 1, "horse_no": 5, "kind": "trial"}]), encoding="utf-8")
    assert review_banker.main(["--kinds", str(src)]) == 1           # one had nothing to describe
    assert store.review(db, DAY, 1, 4)["claim_kind"] == "pace"
    assert store.review(db, DAY, 1, 5) is None
    kinds = review_record.summary(conn=db)["kinds"]
    assert [(k["kind"], k["n"]) for k in kinds] == [("pace", 1)]


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture()
def client(db):
    with transaction(db):
        _race(db)
        _result(db, [1, 2, 3, 4, 5, 6, 7, 8])
        _bet(db, "a", [(1, 4, 0, 1), (1, 1, 0, 0)])
    from fastapi.testclient import TestClient
    from hkrd.api.app import app
    return TestClient(app)


def test_the_routes(client):
    listed = client.get("/api/bets/review").json()
    assert listed["meetings"][0]["race_date"] == DAY and listed["vocabulary"]["verdicts"]
    assert client.get("/api/bets/review?account=kelvin").status_code == 404
    assert client.get("/api/bets/review/summary").json()["horses"] == 1
    assert client.get(f"/api/bets/review/{DAY}").json()["horses"][0]["horse_no"] == 4
    # A card with no banker on it yet is an empty night, not an error.
    assert client.get(f"/api/bets/review/{DAY}?account=kelvin").json()["horses"] == []
    assert client.get("/api/bets/review/2026-11-12").status_code == 404
    assert client.get("/api/bets/review/11-11-2026").status_code == 422
    url = f"/api/bets/review/{DAY}/1/4"
    ok = client.post(url, json={"reasoning": "drawn to lead", "verdict": "mistake",
                                "circumstances": []})
    assert ok.status_code == 200
    assert ok.json()["review"]["verdict"] == "mistake"
    assert ok.json()["review"]["claim_timing"] == "after"
    assert client.post(url, json={"verdict": "right_paid"}).status_code == 422
    assert client.post(url, json={"mood": "bad"}).status_code == 422
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"circumstances": "traffic"}).status_code == 422
    assert client.post(f"/api/bets/review/{DAY}/1/99", json={"verdict": "unsure"}).status_code == 404


def test_the_notes_keep_the_moment_they_were_first_written(client, db):
    url = f"/api/bets/review/{DAY}/note"
    first = client.post(url, json={"race_no": 1, "note": "should have banked BRAVO"}).json()["note"]
    assert first["note"] == "should have banked BRAVO" and first["edited_at"] is None
    t1 = dt.datetime(2026, 11, 21, 4, 0, tzinfo=dt.timezone.utc)
    review_banker.reflect(DAY, 1, "should have banked BRAVO, drawn 2", now=t1)
    again = store.reflections(db, DAY)[1]
    assert again["written_at"] == first["written_at"]
    assert again["edited_at"] == "2026-11-21T04:00:00+00:00"
    night = client.post(url, json={"race_no": 0, "note": "the multi was the mistake"})
    assert night.status_code == 200
    got = client.get(f"/api/bets/review/{DAY}").json()["notes"]
    assert set(got) == {"0", "1"}
    assert client.post(url, json={"race_no": 0, "note": ""}).json()["note"] is None
    assert client.post(url, json={"race_no": 9, "note": "x"}).status_code == 404
    assert client.post(url, json={"race_no": "R1", "note": "x"}).status_code == 422
    assert client.post(url, json={"race_no": 1}).status_code == 422


def test_the_review_is_behind_the_bets_password():
    assert auth._is_bets_path(f"/api/bets/review/{DAY}/1/4")
    assert auth._is_bets_path("/api/bets/review")
    # The desk left the Bets page for its own, and took the password with it.
    assert auth._is_bets_path("/pages/desk.html")
    assert auth._is_bets_path(f"/api/tickets/{DAY}/1/4/decision")
    assert not auth._is_bets_path("/api/ticketsx")
    assert json.dumps(banker_review.vocabulary())             # the form's words serialise
