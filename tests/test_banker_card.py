"""The banker scorecard, the brief and the reason taps (docs/banker-card.md).

Under test, each because getting it wrong would mislead the one decision the
owner asked for help with:

  * every fact is as at the race -- nothing from the race day or after;
  * a fact that was not kept then is unknown, never "no";
  * the scorecard reads his bankers, scores the rest of the field the same
    way, splits by his own evidence, and scores his taps;
  * deciding on the desk never re-reads the heavy half;
  * the facts are kept once per race and read back, not recomputed;
  * a brief marks each fact against his record on the same side of his
    evidence, and says when the record is too thin to mark;
  * a reason outside the vocabulary is refused rather than stored unscored.
"""
from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from hkrd.jobs import decide_ticket, record_tickets
from hkrd.query import banker_brief, banker_facts, banker_record, suggest
from hkrd.store.connect import get_conn, init_db, transaction

DAY = "2026-11-18"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "card.db"
    monkeypatch.setenv("HKRD_DB", str(path))
    banker_record._KEPT.clear()
    conn = get_conn(path)
    init_db(conn)
    yield conn
    conn.close()


def _race(conn, date, no, runners, *, venue="ST", surface="Turf", distance=1200,
          cls="4", off="20:00"):
    """runners: (horse_no, name, place, win_odds, draw, gear, jockey, lengths_behind)."""
    conn.execute("INSERT INTO races (race_date, race_no, venue, surface, distance, race_class, "
                 "off_time) VALUES (?,?,?,?,?,?,?)", (date, no, venue, surface, distance, cls, off))
    for h, name, place, odds, draw, gear, jockey, lb in runners:
        conn.execute("INSERT INTO runners (race_date, race_no, horse_no, horse_name, place, "
                     "win_odds, draw, gear, jockey, lengths_behind) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (date, no, h, name, place, odds, draw, gear, jockey, lb))


def _pace(conn, date, no, h, style):
    conn.execute("INSERT INTO runner_pace (race_date, race_no, horse_no, pace_style, "
                 "derive_version) VALUES (?,?,?,?, 'test')", (date, no, h, style))


def _field(names, *, places=None, odds=None):
    places = places or [None] * len(names)
    odds = odds or [None] * len(names)
    return [(i + 1, n, places[i], odds[i], i + 1, None, "J Rider", None)
            for i, n in enumerate(names)]


# ── the facts ────────────────────────────────────────────────────────────────

def _history(conn):
    """ALPHA: 2nd at ST turf 1200 in class 4, then 5th beaten 4 lengths, both
    on the pace; trialled after that; today down to class 5, drawn 2, blinkers
    first time, with a claiming apprentice. BRAVO has never run."""
    _race(conn, "2026-09-01", 1, [(1, "ALPHA", 2, 5.0, 4, None, "A Rider", 1.5),
                                  (2, "OTHER", 1, 2.0, 1, None, "B Rider", 0.0)])
    _race(conn, "2026-09-28", 1, [(1, "ALPHA", 5, 9.0, 7, None, "A Rider", 4.0),
                                  (2, "OTHER", 1, 2.0, 1, None, "B Rider", 0.0)],
          venue="HV", distance=1650)
    _pace(conn, "2026-09-01", 1, 1, "Leader")
    _pace(conn, "2026-09-28", 1, 1, "On-Pace")
    conn.execute("INSERT INTO trials (trial_date, trial_no, horse_name) VALUES ('2026-10-08', 1, 'ALPHA')")
    _race(conn, "2026-10-18", 1, [(1, "ALPHA", 3, 4.0, 2, "B1", "A Rider (-2)", None),
                                  (2, "BRAVO", 1, 2.0, 9, "TT", "B Rider", None),
                                  (3, "OTHER", 2, 8.0, 3, None, "B Rider", None)], cls="5")
    conn.execute("INSERT INTO tipster_selection (source, tipster, race_date, race_no, horse_no, "
                 "fetched_at) VALUES ('rtw_preview', 'x', '2026-10-18', 1, 2, 'now')")


def test_every_fact_is_read_as_at_the_race(db):
    with transaction(db):
        _history(db)
    runs = [{"race_date": "2026-10-18", "race_no": 1, "horse_no": h, "horse_name": n}
            for h, n in ((1, "ALPHA"), (2, "BRAVO"))]
    f = banker_facts.facts_for(db, runs)
    alpha, bravo = f[("2026-10-18", 1, 1)], f[("2026-10-18", 1, 2)]
    assert alpha == {
        "own": "none", "tier": None, "model": None, "course_trip": "yes",
        "market": "2nd-3rd", "last_start": "4th-6th", "close_last": "further back",
        "since_last": "21 days or less", "class": "down", "draw": "1-3",
        "style": "on the pace", "rider": "under 8% or few rides", "trial": "yes",
        "gear": "yes", "tips": "none"}
    assert bravo["last_start"] == bravo["close_last"] == bravo["since_last"] == "debut"
    assert bravo["course_trip"] == "no" and bravo["market"] == "favourite"
    assert bravo["tips"] == "1 source" and bravo["gear"] == "no" and bravo["style"] is None


def test_its_own_race_never_counts_as_history(db):
    # ALPHA placed 3rd in the race itself: that is not "placed before".
    with transaction(db):
        _race(db, "2026-10-18", 1, [(1, "ALPHA", 3, 4.0, 2, None, "A", None)])
    f = banker_facts.facts_for(db, [{"race_date": "2026-10-18", "race_no": 1, "horse_no": 1,
                                     "horse_name": "ALPHA"}])
    assert f[("2026-10-18", 1, 1)]["course_trip"] == "no"


def test_a_fact_not_kept_then_is_unknown_not_no(db):
    with transaction(db):
        _race(db, "2026-06-01", 1, _field(["ALPHA", "BRAVO"], odds=[2.0, 3.0]))
    f = banker_facts.facts_for(db, [{"race_date": "2026-06-01", "race_no": 1, "horse_no": 1,
                                     "horse_name": "ALPHA"}])[("2026-06-01", 1, 1)]
    assert f["tips"] is None and f["model"] is None


def test_the_model_and_the_live_market_are_read_when_given(db):
    with transaction(db):
        _race(db, DAY, 1, _field(["A", "B", "C", "D", "E"]))
        for h, p in ((1, 0.1), (2, 0.4), (3, 0.2), (4, 0.25), (5, 0.05)):
            db.execute("INSERT INTO runner_gbm (race_date, race_no, horse_no, stage, p_win, "
                       "model_version, derive_version, inputs_key, scored_at) VALUES "
                       "(?, 1, ?, 'latest', ?, 'm', 'd', 'k', 'now')", (DAY, h, p))
    runs = [{"race_date": DAY, "race_no": 1, "horse_no": h, "horse_name": n}
            for h, n in ((1, "A"), (3, "C"))]
    f = banker_facts.facts_for(db, runs, market={(DAY, 1, 1): 1, (DAY, 1, 3): 5})
    assert f[(DAY, 1, 1)]["model"] == "4th or worse" and f[(DAY, 1, 3)]["model"] == "top 3"
    assert f[(DAY, 1, 1)]["market"] == "favourite" and f[(DAY, 1, 3)]["market"] == "4th-6th"
    assert banker_facts.facts_for(db, runs)[(DAY, 1, 1)]["market"] is None   # no price yet


def test_whose_evidence_it_is(db):
    with transaction(db):
        _race(db, DAY, 1, _field(["MINE", "SYS", "NOTED", "NONE"]))
        for eid, name, origin in (("bb_1", "MINE", "owner"), ("bb_2", "SYS", "system")):
            db.execute("INSERT INTO blackbook (id, horse_name, added_date, status, origin) "
                       "VALUES (?, ?, '2026-11-01', 'active', ?)", (eid, name, origin))
        db.execute("INSERT INTO trial_notes (horse_name, trial_date, trial_no, note, written_at) "
                   "VALUES ('NOTED', '2026-11-10', 1, 'asked, found more', '2026-11-11T10:00:00+00:00')")
    runs = [{"race_date": DAY, "race_no": 1, "horse_no": h, "horse_name": n}
            for h, n in ((1, "MINE"), (2, "SYS"), (3, "NOTED"), (4, "NONE"))]
    own = {k[2]: v["own"] for k, v in banker_facts.facts_for(db, runs).items()}
    assert own == {1: "yours", 2: "system's only", 3: "yours", 4: "none"}


# ── the scorecard ────────────────────────────────────────────────────────────

def _bet(conn, bet_id, date, no, banker, partners, account="brett"):
    conn.execute("INSERT INTO bets (bet_id, account, race_date, race_no, bet_type, stake, "
                 "returned, status, source) VALUES (?, ?, ?, ?, 'QPL_BANKER', 30, 0, "
                 "'settled', 'statement')", (bet_id, account, date, no))
    for h in [banker, *partners]:
        conn.execute("INSERT INTO bet_selections (bet_id, race_no, horse_no, is_banker) "
                     "VALUES (?, ?, ?, ?)", (bet_id, no, h, int(h == banker)))


NAMES = ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8"]
ODDS = [2.5, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0, 40.0]


def _season(conn, n=4):
    """`n` settled races of eight. He banks #4 (4th in the market) in each; it
    places in the first two and runs 6th in the rest. Kelvin banks #1."""
    for i in range(n):
        date = f"2026-10-{10 + i:02d}"
        order = [4, 1, 2, 3, 5, 6, 7, 8] if i < 2 else [1, 2, 3, 5, 6, 4, 7, 8]
        places = [order.index(h) + 1 for h in range(1, 9)]
        _race(conn, date, 1, [(h, f"{NAMES[h - 1]}-{i}", places[h - 1], ODDS[h - 1], h,
                               None, "J", None) for h in range(1, 9)])
        _bet(conn, f"b{i}", date, 1, 4, [1, 2, 3])
        _bet(conn, f"k{i}", date, 1, 1, [2, 3], account="kelvin")


def test_the_scorecard_reads_his_bankers_and_the_field_the_same_way(db):
    with transaction(db):
        _season(db)
    rec = banker_record.record(conn=db)
    o = rec["overall"]
    assert o["bankers"] == 4 and o["placed"] == 2 and o["thin"]
    assert rec["races"] == 4 and rec["field"]["bankers"] == 28        # the other seven, x4
    market = next(f for f in rec["facts"] if f["key"] == "market")
    fourth = next(v for v in market["values"] if v["value"] == "4th-6th")
    assert fourth["bankers"] == 4 and fourth["placed"] == 2
    # Three of eight runners are 4th-6th in the market; all his bankers are.
    assert fourth["lean"] == pytest.approx(8 / 3, abs=0.01)
    assert fourth["without_own"]["bankers"] == 4 and fourth["with_own"]["bankers"] == 0
    assert rec["breakeven"] == pytest.approx(1.21)


def test_kelvins_book_is_not_read_as_brett(db):
    with transaction(db):
        _season(db)
    assert banker_record.record(conn=db)["overall"]["bankers"] == 4
    assert banker_record.record(account="kelvin", conn=db)["overall"]["bankers"] == 4


def test_a_decision_is_read_without_redoing_the_heavy_half(db, monkeypatch):
    with transaction(db):
        _season(db)
    banker_record.record(conn=db)
    calls = []
    real = banker_record._rows
    monkeypatch.setattr(banker_record, "_rows", lambda *a: calls.append(1) or real(*a))
    with transaction(db):
        # He passed #2 on the first day, before the off, saying it was the draw.
        db.execute("INSERT INTO ticket_decision (race_date, race_no, banker_no, decision, late, "
                   "seen_json, engine, decided_at) VALUES ('2026-10-10', 1, 2, 'rejected', 0, "
                   "'{}', 'e', 'now')")
        db.execute("INSERT INTO ticket_reason VALUES ('2026-10-10', 1, 2, 'draw')")
    rec = banker_record.record(conn=db)
    assert rec["desk"]["passed"]["bankers"] == 1 and rec["desk"]["passed"]["placed"] == 1
    draw = next(r for r in rec["reasons"] if r["key"] == "draw")
    assert draw["passed"]["bankers"] == 1 and draw["taken"]["bankers"] == 0
    # A decided race is already a race he banked in: nothing new to read.
    assert calls == []


def test_a_new_result_does_redo_it(db, monkeypatch):
    with transaction(db):
        _season(db)
    banker_record.record(conn=db)
    calls = []
    real = banker_record._rows
    monkeypatch.setattr(banker_record, "_rows", lambda *a: calls.append(1) or real(*a))
    with transaction(db):
        _race(db, "2026-10-20", 1, [(h, f"N{h}", h, ODDS[h - 1], h, None, "J", None)
                                    for h in range(1, 9)])
        _bet(db, "b9", "2026-10-20", 1, 1, [2, 3])
    assert banker_record.record(conn=db)["overall"]["bankers"] == 5 and calls == [1]


def test_kept_facts_are_read_back_and_never_recomputed(db, monkeypatch):
    with transaction(db):
        _season(db)
    todo = record_tickets.pending_facts(since="2026-10-01")
    assert len(todo) == 4
    report = record_tickets.record_facts(todo)
    assert sum(report.facts.values()) == 32 and "facts recorded" in report.render()
    assert sum(record_tickets.record_facts(todo).facts.values()) == 0     # written once
    assert record_tickets.pending_facts(since="2026-10-01") == []
    monkeypatch.setattr(banker_record, "facts_for",
                        lambda *a, **k: pytest.fail("facts were kept; nothing to compute"))
    assert banker_record.record(conn=db)["overall"]["bankers"] == 4


# ── the brief ────────────────────────────────────────────────────────────────

def _line(n, ae):
    return {"bankers": n, "placed": 0, "expected": 0.0, "ae": ae, "ae_lo": 0.0,
            "ae_hi": 2.0, "thin": n < banker_record.THIN, "clears": None}


def _record(with_own=1.30, without_own=1.07, overall=1.18, **lines):
    """A scorecard whose every value has 40 bankers at his usual on each side,
    with the lines given replacing (fact, value) -> (overall, with_own, without_own)."""
    facts = []
    # The evidence fact's own lines ARE the two sides, as in a real scorecard.
    lines.setdefault("own:yours", ((120, with_own), (120, with_own), (0, None)))
    lines.setdefault("own:none", ((126, without_own), (0, None), (126, without_own)))
    for key, label, values in banker_facts.FACTS:
        vals = []
        for v in values:
            o, w, wo = lines.get(f"{key}:{v}", ((80, overall), (40, with_own), (40, without_own)))
            vals.append({"value": v, **_line(*o), "lean": 1.0, "field": _line(100, 1.0),
                         "with_own": _line(*w), "without_own": _line(*wo)})
        facts.append({"key": key, "label": label, "values": vals})
    return {"facts": facts, "overall": _line(255, overall),
            "sides": {"with_own": _line(120, with_own), "without_own": _line(135, without_own)}}


def _facts(**given):
    facts = {k: None for k in banker_facts.LABEL}
    facts.update(given)
    return facts


def test_a_horse_he_wrote_about_is_not_marked_on_its_evidence_alone():
    # The first build marked against break-even, and every fact about a horse
    # with his evidence on it inherited 1.30: ten FORs of ten on a real card.
    rec = _record()
    b = banker_brief.brief(_facts(own="yours", draw="middle", style="back"), rec)
    rows = {r["key"]: r for r in b["rows"]}
    assert rows["draw"]["mark"] == rows["style"]["mark"] == "usual"
    assert rows["draw"]["paid"] == "breakeven"          # the money, kept apart from the mark
    assert b["usual"]["ae"] == 1.30 and b["usual_paid"] == "breakeven"
    assert (b["stronger"], b["weaker"]) == (1, 0)       # the evidence itself, against the other side


def test_a_fact_is_marked_against_his_usual_on_the_same_side_of_his_evidence():
    rec = _record(**{
        "course_trip:yes": ((165, 1.0), (68, 1.12), (97, 0.91)),
        "course_trip:no": ((90, 1.62), (52, 1.61), (38, 1.62)),
        "draw:1-3": ((107, 1.29), (50, 1.50), (12, 1.13)),
        "tips:none": ((10, 0.96), (5, 1.0), (5, 0.9))})
    rows = {r["key"]: r for r in banker_brief.brief(
        _facts(own="none", course_trip="yes", draw="1-3", tips="none"), rec)["rows"]}
    # No evidence of his, already placed here: 0.91 against his 1.07 without evidence.
    assert rows["course_trip"]["mark"] == "weaker" and rows["course_trip"]["basis"] == "same side"
    assert rows["course_trip"]["paid"] == "below"
    # Only 12 such bankers on his side: read over all his bankers, against all of them.
    assert rows["draw"]["basis"] == "all your bankers" and rows["draw"]["mark"] == "usual"
    assert rows["tips"]["mark"] == "thin"
    assert rows["model"]["mark"] == "unknown" and rows["model"]["line"] is None
    assert rows["own"]["mark"] == "weaker"              # 1.07 against 1.30 with evidence

    rows = {r["key"]: r for r in banker_brief.brief(
        _facts(own="yours", course_trip="no", draw="1-3"), rec)["rows"]}
    assert rows["course_trip"]["mark"] == "stronger"    # 1.61 against his 1.30
    assert rows["draw"]["mark"] == "stronger" and rows["draw"]["basis"] == "same side"
    assert rows["own"]["mark"] == "stronger"


# ── the taps, and the desk ──────────────────────────────────────────────────

def _card(conn):
    with transaction(conn):
        _race(conn, DAY, 1, _field(NAMES))
        for h in range(1, 9):
            conn.execute("INSERT INTO odds_snapshots VALUES (?, 1, ?, ?, ?, NULL)",
                         (DAY, h, f"{DAY}T19:50:00", ODDS[h - 1]))
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status, origin) "
                     "VALUES ('bb_1', 'H4', '2026-11-01', 'active', 'owner')")


def test_the_desk_carries_the_brief_the_field_and_the_taps(db):
    _card(db)
    m = suggest.meeting(DAY, conn=db)
    assert [r["key"] for r in m["reasons"]] == [k for k, _ in banker_record.REASONS]
    race = m["races"][0]
    assert [r["horse_no"] for r in race["runners"]] == list(range(1, 9))
    assert race["runners"][3]["tier"] == "QUIET" and race["runners"][0]["market_rank"] == 1
    cand = race["candidates"][0]
    assert cand["banker"]["horse_no"] == 4 and cand["brief"]["own"] is True
    market = next(r for r in cand["brief"]["rows"] if r["key"] == "market")
    assert market["value"] == "4th-6th"                     # from the live capture


def test_a_decision_keeps_its_taps_and_replaces_them_with_it(db):
    _card(db)
    out = decide_ticket.decide(DAY, 1, 4, decision="accept", reasons=["trial", "pace", "trial"],
                               reason="found more when asked")
    assert out["decision"]["reasons"] == ["pace", "trial"]
    assert out["decision"]["reason"] == "found more when asked"
    assert '"brief"' in db.execute("SELECT seen_json FROM ticket_decision").fetchone()[0]
    out = decide_ticket.decide(DAY, 1, 4, decision="reject", reasons=["market"])
    assert out["decision"]["reasons"] == ["market"]
    assert db.execute("SELECT count(*) FROM ticket_reason").fetchone()[0] == 1


def test_a_reason_the_desk_cannot_score_is_refused(db):
    _card(db)
    with pytest.raises(suggest.TicketError, match="not a reason"):
        decide_ticket.decide(DAY, 1, 4, decision="accept", reasons=["vibes"])
    assert db.execute("SELECT count(*) FROM ticket_decision").fetchone()[0] == 0


@pytest.fixture()
def client(db):
    _card(db)
    from fastapi.testclient import TestClient
    from hkrd.api.app import app
    return TestClient(app)


def test_the_routes(client):
    body = client.get("/api/tickets/scorecard").json()
    assert body["breakeven"] == banker_record.BREAKEVEN and body["facts"]
    assert client.get("/api/tickets/scorecard?account=kelvin").status_code == 200
    url = f"/api/tickets/{DAY}/1/4/decision"
    ok = client.post(url, json={"decision": "accept", "reasons": ["gut"]})
    assert ok.status_code == 200 and ok.json()["decision"]["reasons"] == ["gut"]
    assert client.post(url, json={"decision": "accept", "reasons": ["vibes"]}).status_code == 422
    t = client.get(f"/api/tickets/{DAY}/1/4").json()
    assert t["brief"]["own"] is True and t["decision"]["reasons"] == ["gut"]
