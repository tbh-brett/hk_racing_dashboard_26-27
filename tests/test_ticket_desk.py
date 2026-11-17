"""The ticket desk: the owner picks the banker, the engine picks the partners.

Under test, each because a measurement or a rule says the easy version is wrong:

  * partners are ranked by the POOL THAT PAYS them, and the win market only
    where no pair pool was captured;
  * the only bankers proposed are runners the owner has a tier on, and any
    other is flagged, never refused;
  * a decision is honest only before the off: a late one is kept apart, and
    one made in time cannot be rewritten once the pool has shut;
  * a decision writes no bet;
  * the paper ticket reads prices from five minutes before the close, is
    written once, and is not rewritten when the engine's rules change;
  * every ticket is scored at the final dividend.
"""
from __future__ import annotations

import datetime as dt
import warnings

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from hkrd.jobs import decide_ticket, record_tickets
from hkrd.query import suggest, ticket_build, ticket_ledger, ticket_record
from hkrd.store.connect import get_conn, init_db, transaction

DAY = "2026-11-18"                    # on or after suggest.ENGINE_FROM: a live race
OFF = "20:00"
FIELD = [(1, "ALPHA"), (2, "BRAVO"), (3, "NOTED"), (4, "DELTA"), (5, "ECHO"),
         (6, "FOXTROT"), (7, "GOLF"), (8, "HOTEL"), (9, "BOOKED"), (10, "JULIET")]
WIN = {1: 2.5, 2: 4.0, 3: 6.0, 4: 8.0, 5: 10.0, 6: 15.0, 7: 20.0, 8: 30.0, 9: 50.0,
       10: 99.0}
PLACE = {1: 1.3, 2: 1.7, 3: 2.2, 4: 2.8, 5: 3.2, 6: 4.5, 7: 5.5, 8: 8.0, 9: 12.0, 10: 20.0}


def _pool(scale: float, *, banker: int = 3, first: tuple[int, ...] = (5, 1, 2, 4)
          ) -> dict[tuple[int, int], float]:
    """A whole pair pool in which `first` are the banker's shortest partners,
    in that order -- deliberately NOT the win market's order (1, 2, 4, 5)."""
    nos = [n for n, _ in FIELD]
    odds = {(a, b): scale * (WIN[a] * WIN[b]) ** 0.5 for a in nos for b in nos if a < b}
    for i, h in enumerate(first):
        odds[(min(banker, h), max(banker, h))] = scale * (2.0 + i)
    return odds


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "desk.db"
    monkeypatch.setenv("HKRD_DB", str(path))
    conn = get_conn(path)
    init_db(conn)
    yield conn
    conn.close()


def _race(conn, date=DAY, race_no=1, field=FIELD, off=OFF):
    conn.execute("INSERT INTO races (race_date, race_no, venue, distance, off_time) "
                 "VALUES (?, ?, 'HV', 1200, ?)", (date, race_no, off))
    for no, name in field:
        conn.execute("INSERT INTO runners (race_date, race_no, horse_no, horse_name) "
                     "VALUES (?, ?, ?, ?)", (date, race_no, no, name))


def _capture(conn, at, *, date=DAY, race_no=1, win=WIN, place=PLACE, qin=None, qpl=None):
    for h, o in win.items():
        conn.execute("INSERT INTO odds_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                     (date, race_no, h, at, o, (place or {}).get(h)))
    for pool, book in (("QIN", qin), ("QPL", qpl)):
        for (a, b), o in (book or {}).items():
            conn.execute("INSERT INTO odds_pairs VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (date, race_no, pool, a, b, at, o))


def _evidence(conn, date=DAY):
    """NOTED has the owner's trial note, BOOKED his book entry, GOLF only the
    system's, and nothing else in the race has anything."""
    before = (dt.date.fromisoformat(date) - dt.timedelta(days=9)).isoformat()
    conn.execute("INSERT INTO trial_notes (horse_name, trial_date, trial_no, note, "
                 "written_at) VALUES ('NOTED', ?, 1, 'asked, reacted very well', ?)",
                 (before, f"{before}T10:00:00+00:00"))
    for eid, name, origin in (("bb_1", "BOOKED", "owner"), ("bb_2", "GOLF", "system")):
        conn.execute("INSERT INTO blackbook (id, horse_name, added_date, status, origin) "
                     "VALUES (?, ?, ?, 'active', ?)", (eid, name, before, origin))


def _result(conn, order, *, date=DAY, race_no=1, qin=None, qpl=None, close=None):
    for place, h in enumerate(order, start=1):
        conn.execute("UPDATE runners SET place = ? WHERE race_date = ? AND race_no = ? "
                     "AND horse_no = ?", (place, date, race_no, h))
    for pool, book in (("QIN", qin), ("QPL", qpl)):
        for (a, b), v in (book or {}).items():
            conn.execute("INSERT INTO dividends VALUES (?, ?, ?, ?, ?)",
                         (date, race_no, pool, f"{a},{b}", v))
    if close:
        conn.execute("INSERT INTO market_close VALUES (?, ?, ?, 'STOP_SELL')",
                     (date, race_no, close))


def _card(conn):
    with transaction(conn):
        _race(conn)
        _evidence(conn)
        _capture(conn, f"{DAY}T19:50:00", qin=_pool(4.0), qpl=_pool(1.5))


# ── the ticket itself ────────────────────────────────────────────────────────

def _field():
    return [{"horse_no": n, "horse_name": name, "draw": n, "jockey": "J"}
            for n, name in FIELD]


def test_partners_are_ranked_by_the_pool_that_pays_them():
    t = suggest.build(3, _field(), WIN, PLACE, _pool(4.0), _pool(1.5))
    assert t["partner_source"] == "QPL pool"
    assert t["engine_partners"] == [5, 1, 2, 4]          # the pair pool's order
    assert [p["horse_no"] for p in t["partners"]] == [5, 1, 2, 4]
    assert t["pools"] == ["QIN", "QPL"] and t["lines"] == 8 and t["cost"] == 80.0


def test_the_win_market_ranks_them_only_where_no_pair_pool_was_captured():
    t = suggest.build(3, _field(), WIN, PLACE)
    assert t["partner_source"] == "win market"
    assert t["engine_partners"] == [1, 2, 4, 5]
    # ... and the quinella pool stands in where there is no quinella place.
    assert suggest.build(3, _field(), WIN, PLACE, _pool(4.0))["partner_source"] == "QIN pool"


def test_a_field_under_seven_is_a_quinella_only():
    six = _field()[:6]
    t = suggest.build(3, six, {h: WIN[h] for h in range(1, 7)}, k=3)
    assert t["pools"] == ["QIN"] and t["lines"] == 3 and t["cost"] == 30.0
    assert "qin_only" in [f["code"] for f in t["flags"]]


def test_the_banker_chance_says_which_pool_it_came_from():
    t = suggest.build(3, _field(), WIN, PLACE, _pool(4.0), _pool(1.5))
    b = t["banker"]
    assert b["place_source"] == "place pool" and b["model_pct"] is not None
    assert b["market_rank"] == 3 and t["field_size"] == 10
    assert b["top2_source"] == "QIN pool" and 0 < b["top2_pct"] < b["place_pct"]
    assert suggest.build(3, _field(), WIN)["banker"]["place_source"] == "model"


def test_a_line_that_would_not_cover_the_ticket_is_counted():
    # $10 a line over 8 lines costs $80; a QPL pair at 2.0 returns $20.
    t = suggest.build(3, _field(), WIN, PLACE, _pool(40.0), _pool(1.0))
    flag = next(f for f in t["flags"] if f["code"] == "line_under_cost")
    assert flag["detail"].startswith("4 of 8 lines") and flag["basis"]
    assert t["partners"][0]["covers_cost"] == {"QIN": True, "QPL": False}


def test_his_partners_replace_the_engines_and_are_marked_edited():
    t = suggest.build(3, _field(), WIN, PLACE, _pool(4.0), _pool(1.5), partners=[1, 9])
    assert t["edited"] and t["engine_partners"] == [5, 1]
    assert [p["engine_rank"] for p in t["partners"]] == [2, 8]
    same = suggest.build(3, _field(), WIN, PLACE, _pool(4.0), _pool(1.5), partners=[1, 5])
    assert not same["edited"]                         # the same two, in any order


@pytest.mark.parametrize("kwargs, error", [
    ({"partners": [1, 1]}, "twice"), ({"partners": [3, 1]}, "own partner"),
    ({"partners": [77]}, "not a declared runner"), ({"k": 0}, "at least one"),
    ({"unit_stake": 0}, "unit stake")])
def test_a_ticket_that_describes_nothing_is_refused(kwargs, error):
    with pytest.raises(suggest.TicketError, match=error):
        suggest.build(3, _field(), WIN, PLACE, **kwargs)


def test_a_banker_not_in_the_race_is_not_on_the_card():
    with pytest.raises(suggest.NotOnCard):
        suggest.build(42, _field(), WIN)


def test_no_price_yet_degrades_and_says_so():
    t = suggest.build(3, _field(), {})
    assert t["partners"] == [] and t["partner_source"] is None
    assert [f["code"] for f in t["flags"]] == ["no_market"]


# ── the candidates ───────────────────────────────────────────────────────────

def test_only_the_owners_horses_are_proposed_his_own_evidence_first(db):
    _card(db)
    race = suggest.meeting(DAY, conn=db)["races"][0]
    got = [(c["banker"]["horse_name"], c["evidence"]["tier"], c["evidence"]["own"])
           for c in race["candidates"]]
    assert got == [("NOTED", "NOTE", True), ("BOOKED", "QUIET", True),
                   ("GOLF", "QUIET", False)]
    flags = {c["banker"]["horse_name"]: [f["code"] for f in c["flags"]]
             for c in race["candidates"]}
    assert "no_own_evidence" not in flags["NOTED"]
    assert flags["GOLF"][0] == "no_own_evidence"       # the system's book is not his


def test_a_races_chances_are_worked_out_once_not_once_a_candidate(db, monkeypatch):
    # Worked out per candidate, Harville-Henery ran again for every banker in a
    # race. One book a race keeps a priced card inside the budget: 53 tickets
    # over 11 races in 182 ms on production's copy of 17 Nov 2026.
    _card(db)
    calls, real = [], suggest.book
    monkeypatch.setattr(suggest, "book", lambda *a, **k: calls.append(1) or real(*a, **k))
    race = suggest.meeting(DAY, conn=db)["races"][0]
    assert len(race["candidates"]) == 3 and len(calls) == 1


def test_any_runner_can_be_nominated_and_is_flagged_not_refused(db):
    _card(db)
    t = suggest.ticket(DAY, 1, 1, conn=db)             # ALPHA: nothing of his on it
    assert t["evidence"] is None and t["flags"][0]["code"] == "no_own_evidence"
    assert t["partners"] and t["decision"] is None and t["gone"] is False


def test_a_scratched_runner_is_out_of_the_field(db):
    _card(db)
    db.execute("UPDATE runners SET place_code = 'WX' WHERE race_date = ? AND horse_no = 5",
               (DAY,))
    t = suggest.ticket(DAY, 1, 3, conn=db)
    assert 5 not in t["engine_partners"] and t["field_size"] == 9
    with pytest.raises(suggest.NotOnCard):
        suggest.ticket(DAY, 1, 5, conn=db)


def test_a_race_that_has_gone_off_proposes_nothing(db):
    _card(db)
    db.execute("INSERT INTO market_close VALUES (?, 1, ?, 'STOP_SELL')",
               (DAY, f"{DAY}T20:01:00"))
    race = suggest.meeting(DAY, conn=db)["races"][0]
    assert race["gone"] and race["candidates"] == []


# ── the decision ─────────────────────────────────────────────────────────────

def _decide(db, **kw):
    kw.setdefault("decision", "accept")
    return decide_ticket.decide(DAY, 1, kw.pop("banker", 3), db=None, **kw)


def test_accepting_keeps_the_engines_partners_and_the_ticket_he_saw(db):
    _card(db)
    out = _decide(db, account="brett")
    assert out["decision"]["decision"] == "accepted"
    assert out["decision"]["partners"] == [1, 2, 4, 5] and not out["decision"]["late"]
    seen = db.execute("SELECT seen_json FROM ticket_decision").fetchone()[0]
    assert '"engine_partners": [5, 1, 2, 4]' in seen


def test_partners_of_his_own_are_an_edit_and_the_same_ones_are_not(db):
    _card(db)
    assert _decide(db, partners=[1, 9])["decision"]["decision"] == "edited"
    # What he saw is the engine's ticket at the size he took: two partners.
    assert '"engine_partners": [5, 1]' in db.execute(
        "SELECT seen_json FROM ticket_decision").fetchone()[0]
    assert _decide(db, partners=[4, 5, 1, 2])["decision"]["decision"] == "accepted"


def test_he_may_change_his_mind_until_the_pool_shuts(db):
    _card(db)
    _decide(db)
    out = _decide(db, decision="reject", reason="drifting")
    assert out["decision"]["decision"] == "rejected" and out["decision"]["partners"] == []
    assert db.execute("SELECT count(*) FROM ticket_decision").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM ticket_decision_partner").fetchone()[0] == 0


def test_a_decision_made_in_time_cannot_be_rewritten_after_the_off(db):
    _card(db)
    _decide(db)
    db.execute("INSERT INTO market_close VALUES (?, 1, ?, 'STOP_SELL')",
               (DAY, f"{DAY}T20:01:00"))
    with pytest.raises(decide_ticket.DecisionClosed, match="stands"):
        _decide(db, decision="reject")
    assert db.execute("SELECT decision FROM ticket_decision").fetchone()[0] == "accepted"


def test_a_decision_after_the_off_is_kept_and_marked_late(db):
    _card(db)
    db.execute("INSERT INTO market_close VALUES (?, 1, ?, 'STOP_SELL')",
               (DAY, f"{DAY}T20:01:00"))
    assert _decide(db)["decision"]["late"] is True


def test_a_decision_writes_no_bet(db):
    _card(db)
    _decide(db)
    assert db.execute("SELECT count(*) FROM bets").fetchone()[0] == 0


def test_a_decision_that_describes_nothing_is_refused(db):
    _card(db)
    with pytest.raises(suggest.TicketError, match="decision must be"):
        _decide(db, decision="maybe")
    with pytest.raises(suggest.NotOnCard):
        _decide(db, banker=42)
    with pytest.raises(suggest.NotOnCard):
        _decide(db, partners=[77])
    assert db.execute("SELECT count(*) FROM ticket_decision").fetchone()[0] == 0


# ── the paper record ─────────────────────────────────────────────────────────

def _gone(conn, *, order=(3, 5, 1, 2, 4, 6, 7, 8, 9, 10)):
    """A card with an early capture, a different order at the close, the pool
    shut at 20:01 and a result."""
    with transaction(conn):
        _race(conn)
        _evidence(conn)
        _capture(conn, f"{DAY}T19:50:00", qin=_pool(4.0), qpl=_pool(1.5))
        # At the close the pair pool has turned round: 2 and 4 are now shortest.
        _capture(conn, f"{DAY}T20:01:00", qin=_pool(4.0, first=(2, 4, 1, 5)),
                 qpl=_pool(1.5, first=(2, 4, 1, 5)))
        _result(conn, order, close=f"{DAY}T20:01:00",
                qin={(3, 5): 300.0}, qpl={(3, 5): 90.0, (1, 3): 40.0, (1, 5): 60.0})


def test_the_paper_ticket_reads_prices_from_before_the_close(db):
    _gone(db)
    noted = next(t for t in suggest.paper(DAY, 1, conn=db) if t["banker"]["horse_no"] == 3)
    assert noted["captured"]["win"] == f"{DAY}T19:50:00"
    assert noted["engine_partners"] == [5, 1, 2, 4]     # not the close's 2, 4, 1, 5
    assert noted["lead_minutes"] == 11.0


def test_a_race_captured_only_at_the_close_says_how_late_it_was_read(db):
    with transaction(db):
        _race(db)
        _evidence(db)
        _capture(db, f"{DAY}T20:01:00", qpl=_pool(1.5))
        _result(db, (3, 5, 1), close=f"{DAY}T20:01:00")
    assert suggest.paper(DAY, 1, conn=db)[0]["lead_minutes"] == 0.0


def test_the_paper_record_is_written_once_and_never_rewritten(db, monkeypatch):
    _gone(db)
    todo = record_tickets.pending(today=dt.date(2026, 11, 19))
    assert todo == [(DAY, 1)]
    first = record_tickets.record(todo)
    assert first.recorded == {DAY: 3} and first.races == {DAY: 1}
    assert "3 bankers over 1 races" in first.render()
    assert record_tickets.pending(today=dt.date(2026, 11, 19)) == []
    # A later engine takes two partners; the race already judged keeps its four.
    monkeypatch.setattr(ticket_build, "PARTNERS", 2)
    assert record_tickets.record([(DAY, 1)]).recorded == {DAY: 0}
    assert [r[0] for r in db.execute(
        "SELECT horse_no FROM ticket_suggestion_partner WHERE banker_no = 3 "
        "ORDER BY rank")] == [5, 1, 2, 4]


def test_a_nominated_banker_he_decided_on_gets_a_paper_ticket_too(db):
    _card(db)
    _decide(db, banker=1)                               # ALPHA: no evidence
    with transaction(db):
        _result(db, (3, 5, 1), close=f"{DAY}T20:01:00")
    record_tickets.record([(DAY, 1)])
    assert [tuple(r) for r in db.execute(
        "SELECT banker_no, tier, own FROM ticket_suggestion ORDER BY banker_no")] == [
        (1, None, 0), (3, "NOTE", 1), (7, "QUIET", 0), (9, "QUIET", 1)]


def test_pending_never_reaches_back_before_the_engine_was_written(db):
    with transaction(db):
        _race(db, date="2026-11-11")
        _result(db, (3, 5, 1), date="2026-11-11")
    assert record_tickets.pending(today=dt.date(2026, 11, 19)) == []
    assert record_tickets.pending(since="2026-09-01") == [("2026-11-11", 1)]


def test_a_night_with_nothing_outstanding_says_so():
    assert "nothing outstanding" in record_tickets.RecordReport().render()


# ── the record ───────────────────────────────────────────────────────────────

def test_settle_pays_each_pool_at_its_final_dividend():
    div = {"QIN": {frozenset((3, 5)): 300.0},
           "QPL": {frozenset((3, 5)): 90.0, frozenset((1, 3)): 40.0}}
    assert ticket_record.settle(3, [5, 1, 2, 4], 10.0, ["QIN", "QPL"], div) == {
        "QIN": 300.0, "QPL": 130.0}
    assert ticket_record.settle(3, [5], 20.0, ["QIN"], div) == {"QIN": 600.0}


@pytest.mark.parametrize("banker, paid, want", [
    (9, {"QIN": 0.0, "QPL": 0.0}, "banker_out"),
    (3, {"QIN": 0.0, "QPL": 0.0}, "partner_out"),
    (3, {"QIN": 0.0, "QPL": 40.0}, "paid_qpl"),
    (3, {"QIN": 300.0, "QPL": 90.0}, "paid")])
def test_the_outcome_names_what_went_wrong(banker, paid, want):
    assert ticket_record.outcome(banker, ["QIN", "QPL"], {3, 5}, {3, 5, 1}, paid) == want


def test_a_quinella_only_ticket_needs_the_banker_in_the_first_two():
    assert ticket_record.outcome(1, ["QIN"], {3, 5}, {3, 5, 1}, {"QIN": 0.0}) == "banker_out"


def test_every_ticket_is_scored_and_the_swap_is_compared(db):
    _card(db)
    _decide(db, partners=[1, 2, 4, 9])                  # he drops 5 for his 9
    _decide(db, banker=9, decision="reject")
    with transaction(db):
        _capture(db, f"{DAY}T20:01:00", qin=_pool(4.0), qpl=_pool(1.5))
        _result(db, (3, 5, 1, 2, 4, 6, 7, 8, 9, 10), close=f"{DAY}T20:01:00",
                qin={(3, 5): 300.0}, qpl={(3, 5): 90.0, (1, 3): 40.0, (1, 5): 60.0})
    record_tickets.record([(DAY, 1)])
    rec = ticket_record.record(DAY, conn=db)
    rows = {r["banker"]["horse_no"]: r for r in rec["rows"]}

    noted = rows[3]
    assert noted["settled"] and noted["banker_placed"] and noted["frame"] == [3, 5, 1]
    # The engine's paper ticket held 5: the quinella and two quinella places.
    assert noted["paper"]["returned"] == 430.0 and noted["paper"]["outcome"] == "paid"
    # His held 1 and not 5: one quinella place, $40 on an $80 ticket.
    d = noted["decision"]
    assert d["decision"] == "edited" and d["returned"] == 40.0 and d["cost"] == 80.0
    assert d["outcome"] == "paid_qpl" and d["under_cost"] is True
    assert d["engine"]["returned"] == 430.0
    assert rec["swaps"]["tickets"] == 1 and rec["swaps"]["difference_pts"] == -487.5
    assert rec["swaps"]["thin"] is True

    booked = rows[9]                                     # passed, and it ran ninth
    assert booked["decision"]["decision"] == "rejected"
    assert booked["paper"]["outcome"] == "banker_out" and not booked["banker_placed"]

    slices = {s["label"]: s for s in rec["tickets"]}
    assert slices["PAPER · EVERY CANDIDATE"]["bets"] == 3
    assert slices["PAPER · THE ONES YOU PASSED"]["bets"] == 1
    assert slices["TAKEN · AS YOU STRUCK THEM"]["staked"] == 80.0
    own = next(g for g in rec["bankers"]["by_evidence"] if g["key"] == "own")
    assert own["bankers"] == 2 and own["placed"] == 1 and own["ae"] is not None
    assert rec["counts"] == {"tickets": 3, "settled": 3, "awaiting": 0, "decided": 2,
                             "late": 0, "struck_without_the_desk": 0}


def test_a_late_decision_is_listed_and_in_no_figure(db):
    _gone(db)
    _decide(db)                                          # after the close
    record_tickets.record([(DAY, 1)])
    rec = ticket_record.record(DAY, conn=db)
    assert rec["counts"]["late"] == 1 and rec["counts"]["decided"] == 0
    assert next(s for s in rec["tickets"] if s["label"].startswith("TAKEN"))["bets"] == 0


def test_a_race_without_its_dividends_is_awaiting_not_lost(db):
    with transaction(db):
        _race(db)
        _evidence(db)
        _capture(db, f"{DAY}T19:50:00", qpl=_pool(1.5))
        _result(db, (3, 5, 1), close=f"{DAY}T20:01:00")
    record_tickets.record([(DAY, 1)])
    rec = ticket_record.record(DAY, conn=db)
    assert {r["awaiting"] for r in rec["rows"]} == {"dividends"}
    assert rec["counts"]["awaiting"] == 3 and rec["tickets"][0]["bets"] == 0


def test_races_before_the_engine_was_written_are_kept_apart(db):
    early = "2026-11-11"
    with transaction(db):
        _race(db, date=early)
        _evidence(db, date=early)
        _capture(db, f"{early}T19:50:00", date=early, qpl=_pool(1.5))
        _result(db, (3, 5, 1), date=early, close=f"{early}T20:01:00",
                qin={(3, 5): 300.0}, qpl={(3, 5): 90.0})
    record_tickets.record([(early, 1)])
    assert ticket_record.record(early, conn=db)["rows"] == []
    back = ticket_record.record(early, scope="backfilled", conn=db)
    assert len(back["rows"]) == 3 and all(r["backfilled"] for r in back["rows"])
    with pytest.raises(ValueError, match="scope"):
        ticket_record.record(early, scope="everything", conn=db)


# ── the ledger ───────────────────────────────────────────────────────────────

def _bet(conn, bet_id, banker, partners, *, bet_type="QPL_BANKER", stake=40.0,
         returned=0.0, date=DAY, race_no=1, account="brett"):
    conn.execute("INSERT INTO bets (bet_id, account, race_date, race_no, bet_type, "
                 "stake, returned, status, source) VALUES (?, ?, ?, ?, ?, ?, ?, "
                 "'settled', 'statement')",
                 (bet_id, account, date, race_no, bet_type, stake, returned))
    for h in [banker, *partners]:
        conn.execute("INSERT INTO bet_selections (bet_id, race_no, horse_no, is_banker) "
                     "VALUES (?, ?, ?, ?)", (bet_id, race_no, h, int(h == banker)))


def test_the_ledger_is_matched_to_the_decision_at_read_time(db):
    _card(db)
    _decide(db, partners=[1, 2, 4, 9])
    with transaction(db):
        _result(db, (3, 5, 1), close=f"{DAY}T20:01:00", qin={(3, 5): 300.0},
                qpl={(3, 5): 90.0, (1, 3): 40.0})
        _bet(db, "s1", 3, [1, 2, 4, 9], returned=40.0)
        _bet(db, "s2", 2, [1, 3])                        # struck without the desk
    rec = ticket_record.record(DAY, conn=db)
    row = next(r for r in rec["rows"] if r["banker"]["horse_no"] == 3)
    assert row["ledger"] == {"struck": True, "same_partners": True, "bets": 1,
                             "staked": 40.0, "returned": 40.0, "bet_ids": ["s1"]}
    assert rec["counts"]["struck_without_the_desk"] == 1


def test_the_ledgers_bankers_are_read_by_the_evidence_he_had(db):
    _gone(db, order=(3, 5, 1, 2, 4, 6, 7, 8))            # 9 and 10 did not finish
    with transaction(db):
        for h in WIN:
            db.execute("UPDATE runners SET win_odds = ? WHERE race_date = ? AND horse_no = ?",
                       (WIN[h], DAY, h))
        _bet(db, "s1", 3, [5, 1])                        # his trial note: placed
        _bet(db, "s2", 3, [5, 1], bet_type="QIN_BANKER")  # the same banker, once
        _bet(db, "s3", 9, [1, 2])                        # his book: fell, did not place
        _bet(db, "s4", 1, [2, 4])                        # nothing of his: placed
        _bet(db, "s5", 7, [1, 2], account="kelvin")
    got = ticket_ledger.bankers(account="brett", conn=db)
    by = {g["key"]: g for g in got["groups"]}
    assert (by["all"]["bankers"], by["own"]["bankers"], by["none"]["bankers"]) == (3, 2, 1)
    assert by["own"]["placed"] == 1                      # the faller counts, as a miss
    assert by["own"]["from_pool"] == 2 and by["own"]["ae"] is not None
    assert [t["key"] for t in got["by_tier"]] == ["tier:NOTE", "tier:QUIET"]
    assert ticket_ledger.bankers(conn=db)["groups"][0]["bankers"] == 4


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture()
def client(db):
    _card(db)
    from fastapi.testclient import TestClient
    from hkrd.api.app import app
    return TestClient(app)


def test_the_card_route_lists_the_candidates(client):
    body = client.get(f"/api/tickets/{DAY}").json()
    assert body["engine"] == suggest.ENGINE
    assert [c["banker"]["horse_no"] for c in body["races"][0]["candidates"]] == [3, 9, 7]


def test_a_missing_card_is_404_and_a_bad_date_is_422(client):
    assert client.get("/api/tickets/2031-01-01").status_code == 404
    assert client.get("/api/tickets/yesterday").status_code == 422
    assert client.get(f"/api/tickets/{DAY}/1/42").status_code == 404
    assert client.get(f"/api/tickets/{DAY}/1/3?partners=1,1").status_code == 422


def test_the_ticket_route_takes_his_partners(client):
    body = client.get(f"/api/tickets/{DAY}/1/3?partners=1,9&unit_stake=20").json()
    assert body["edited"] and body["cost"] == 80.0 and body["unit_stake"] == 20.0


def test_the_decision_route_records_and_refuses_with_real_status_codes(client, db):
    url = f"/api/tickets/{DAY}/1/3/decision"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"decision": "maybe"}).status_code == 422
    ok = client.post(url, json={"decision": "accept", "account": "brett"})
    assert ok.status_code == 200 and ok.json()["decision"]["decision"] == "accepted"
    db.execute("INSERT INTO market_close VALUES (?, 1, ?, 'STOP_SELL')",
               (DAY, f"{DAY}T20:01:00"))
    assert client.post(url, json={"decision": "reject"}).status_code == 409


def test_the_record_and_banker_routes_are_not_read_as_a_card(client):
    rec = client.get(f"/api/tickets/record?date_from={DAY}&date_to={DAY}")
    assert rec.status_code == 200 and rec.json()["scope"] == "live"
    assert client.get("/api/tickets/record?scope=everything").status_code == 422
    assert client.get("/api/tickets/record?date_from=2026-12-01&date_to=2026-11-01"
                      ).status_code == 422
    assert client.get("/api/tickets/bankers").json()["groups"][0]["key"] == "all"
