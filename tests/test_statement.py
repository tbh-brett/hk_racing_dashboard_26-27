"""HKJC account statements — the only record of a bet that exists before
settlement.

The parser is a port of the legacy `parse_acct_statement.py`, which the handoff
lists among the best code in the old repo. These tests pin the behaviours that
made it worth porting rather than rewriting: a "Quinella - Quinella Place" line
is two bets on one debit, an All-Up is one ticket that must never be split, and
a block that will not read is REPORTED rather than dropped — a bet missing from
the ledger reads as a bet never placed, which the Blackbook would then call a
missed chance.
"""
from __future__ import annotations

import pytest

from hkrd.ingest import statement
from hkrd.jobs import import_statement
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

SEP = "*" * 40


def _stmt(*blocks: str) -> str:
    """A statement is blocks separated by a row of forty asterisks."""
    head = ("Account Records - Download version for reference only\n"
            "From 22/04/2026 To 22/04/2026\n"
            "Betting Account No.: 40067995\n"
            "Balance: $636.40\n")
    body = f"\n{SEP}\n".join(b.strip("\n") for b in blocks)
    return f"{head}{SEP}\n{body}\n{SEP}\n- End -\n"


QQP = """\
2209
22/04/2026 18:40
Happy Valley
Wednesday
Quinella - Quinella Place
Race 1
3 CONCORDE STAR +
4 NEBRASKAN +
8 TEAM HAPPY

$10


$60.00
$129.50"""

BANKER = """\
2217
22/04/2026 19:05
Happy Valley
Wednesday
Quinella - Quinella Place
Race 2
6 DOUBLE BINGO
Banker with
4 TELECOM POWER +
7 PODIUM +
8 VERBIER +
9 DOUBLE SHOW

$10


$80.00
$80.00"""

QUARTET_MB = """\
2300
26/04/2026 14:27
Sha Tin
Sunday
Quartet
Multi-Banker
Race 5
1 KA YING RISING
Banker with
2 SATONO REVE +
3 HELIOS EXPRESS +
5 RAGING BLIZZARD
Banker with
2 SATONO REVE +
4 FAST NETWORK +
5 RAGING BLIZZARD

$20


$280.00
$2082.00"""

ALL_UP = """\
2401
26/04/2026 13:00
Sha Tin
Sunday
All Up
Quinella - Quinella Place
3 x 4
Race 3
2 ALPHA HEDGE +
5 BETA CALL
Race 5
1 KA YING RISING
Banker with
2 SATONO REVE +
3 HELIOS EXPRESS

$12


$144.00
$0.00"""

FLEXI = """\
2500
26/04/2026 15:00
Sha Tin
Sunday
Quartet
Race 7
1 ONE +
2 TWO +
3 THREE +
4 FOUR

$1.5625/192


$300.00
"""

CASH = """\
2600
26/04/2026 12:00
Deposit
$5000.00
"""

GIBBERISH = """\
9999
not a date at all
whatever this is
some other line
and another one"""


# ── the shapes the statement actually contains ───────────────────────────────

def test_quinella_quinella_place_is_two_bets_on_one_debit():
    """One $60 line covers two pools. Recorded as $30 each, so the ledger's
    turnover is the money that actually left the account."""
    recs, report = statement.parse_text(_stmt(QQP))
    assert report.bets == 1 and len(recs) == 2
    assert [r["bet_type"] for r in recs] == ["QIN", "QPL"]
    assert [r["stake_hkd"] for r in recs] == [30.0, 30.0]
    assert sum(r["stake_hkd"] for r in recs) == 60.0
    # Both halves carry the same reference and the same selections.
    assert {r["bookie_ref"] for r in recs} == {"2209"}
    assert all(r["selections"] == [3, 4, 8] for r in recs)


def test_a_banker_is_kept_apart_from_the_horses_it_is_banked_with():
    recs, _ = statement.parse_text(_stmt(BANKER))
    assert [r["bet_type"] for r in recs] == ["QIN_BANKER", "QPL_BANKER"]
    assert all(r["banker"] == 6 for r in recs)
    assert all(r["selections"] == [4, 7, 8, 9] for r in recs)


def test_a_multi_banker_quartet_keeps_each_position_separately():
    """`legs` here means positions within ONE race, not races — the same key
    carries both meanings in the ledger and they must not be confused."""
    recs, _ = statement.parse_text(_stmt(QUARTET_MB))
    assert len(recs) == 1
    bet, = recs
    assert bet["bet_type"] == "QTT_MB"
    assert bet["race_number"] == 5
    assert bet["legs"] == [[1], [2, 3, 5], [2, 4, 5]]
    assert bet["stake_hkd"] == 280.0 and bet["total_credit"] == 2082.0


def test_an_all_up_is_one_ticket_and_is_never_split():
    """The legs settle together. Splitting it into per-race bets would invent a
    settlement HKJC never published — only per-leg dividends exist."""
    recs, _ = statement.parse_text(_stmt(ALL_UP))
    assert len(recs) == 1
    bet, = recs
    assert bet["bet_type"] == "ALLUP_QQP"
    assert bet["stake_hkd"] == 144.0
    assert bet["all_up_formula"] == "3X4"
    assert [leg["race_number"] for leg in bet["legs"]] == [3, 5]
    assert bet["legs"][0] == {"race_number": 3, "banker": None,
                              "selections": [2, 5]}
    assert bet["legs"][1] == {"race_number": 5, "banker": 1,
                              "selections": [2, 3]}
    # The flattened selections carry the banker too, so a horse on an all-up
    # can still be found by number.
    assert bet["selections"] == [2, 5, 1, 2, 3]


def test_a_flexi_unit_stake_does_not_become_the_ticket_cost():
    """'$1.5625/192' is the per-combination stake. The debit is the $300."""
    recs, _ = statement.parse_text(_stmt(FLEXI))
    assert len(recs) == 1 and recs[0]["stake_hkd"] == 300.0


def test_a_cash_movement_is_counted_but_is_not_a_bet():
    recs, report = statement.parse_text(_stmt(QQP, CASH))
    assert report.cash_movements == 1
    assert report.bets == 1 and len(recs) == 2


def test_a_block_that_will_not_read_is_reported_not_dropped():
    """A bet silently missing from the ledger reads as a bet never placed."""
    recs, report = statement.parse_text(_stmt(QQP, GIBBERISH))
    assert len(recs) == 2
    assert len(report.unparsed) == 1 and "9999" in report.unparsed[0]


def test_an_empty_statement_raises_rather_than_returning_nothing():
    with pytest.raises(statement.StatementError):
        statement.parse_text("   ")


def test_a_statement_of_only_unreadable_bets_raises():
    """Ten blocks in and nothing out means the format changed. Returning an
    empty list would look like a quiet meeting."""
    with pytest.raises(statement.StatementError, match="format may have changed"):
        statement.parse_text(_stmt(GIBBERISH))


def test_venue_and_date_come_back_normalised():
    recs, _ = statement.parse_text(_stmt(QQP))
    assert recs[0]["venue"] == "HV"
    assert recs[0]["meeting_date"] == "20260422"
    assert recs[0]["placed_at"] == "2026-04-22T18:40:00"
    assert statement.parse_text(_stmt(QUARTET_MB))[0][0]["venue"] == "ST"


# ── the real files, if they are still beside the repo ────────────────────────

def test_the_real_statements_parse_when_present():
    from pathlib import Path
    found = [p for p in (Path("../hk_race_dashboard/acctstmt (22 April).txt"),
                         Path("../hk_race_dashboard/acctstmt (26 April).txt"))
             if p.is_file()]
    if not found:
        pytest.skip("legacy statements not beside the repo")
    for path in found:
        recs, report = statement.parse(path)
        assert recs and not report.unparsed, f"{path}: {report.unparsed}"
        assert all(r["stake_hkd"] > 0 for r in recs)


# ── writing them to the ledger ───────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """The races the sample statement bets into, so the selections join."""
    path = tmp_path / "s.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-04-22", "race_no": n, "venue": "HV",
             "course": "C", "surface": "Turf", "going": "G", "distance": 1650}
            for n in (1, 2)])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-04-22", "race_no": 1, "horse_no": n,
             "horse_name": f"HORSE {n}", "place": str(n), "win_odds": 5.0,
             "draw": n} for n in range(1, 10)]
            + [{"race_date": "2026-04-22", "race_no": 2, "horse_no": n,
                "horse_name": f"OTHER {n}", "place": str(n), "win_odds": 5.0,
                "draw": n} for n in range(1, 10)])
    conn.close()
    return path


def test_the_two_halves_of_one_block_are_two_bets_not_one(tmp_path, db):
    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP), encoding="utf-8")
    report = import_statement.run(src, db=db)
    conn = get_conn(db)
    rows = conn.execute("SELECT bet_id, bet_type, stake FROM bets "
                        "ORDER BY bet_type").fetchall()
    conn.close()
    assert report.bets == 2
    # One bookie reference, two bets — so the id cannot be the reference alone.
    assert [r["bet_type"] for r in rows] == ["QIN", "QPL"]
    assert len({r["bet_id"] for r in rows}) == 2
    assert [r["stake"] for r in rows] == [30.0, 30.0]


def test_importing_the_same_statement_twice_adds_nothing(tmp_path, db):
    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP, BANKER), encoding="utf-8")
    first = import_statement.run(src, db=db)
    second = import_statement.run(src, db=db)
    conn = get_conn(db)
    n = conn.execute("SELECT count(*) FROM bets").fetchone()[0]
    sel = conn.execute("SELECT count(*) FROM bet_selections").fetchone()[0]
    conn.close()
    assert first.bets == 4 and second.new_bets == 0
    assert n == 4 and sel == first.selections


def test_settlement_comes_from_the_credit_on_the_statement(tmp_path, db):
    """HKJC publishes per-leg dividends, not per-ticket ones. The credit on the
    statement is the only figure that settles a ticket, so it is what is used —
    nothing is recomputed from dividends."""
    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP, BANKER), encoding="utf-8")
    import_statement.run(src, db=db)
    conn = get_conn(db)
    rows = {(r["bet_type"], r["stake"]): r for r in conn.execute(
        "SELECT bet_type, stake, returned, pnl, hit, settle_method FROM bets")}
    conn.close()
    # $129.50 credit on a $60 block that became two $30 bets. The statement
    # does not say how that split between the win pool and the place pool, so
    # half sits on each and neither claims to have hit.
    qin = rows[("QIN", 30.0)]
    assert qin["returned"] == pytest.approx(64.75)
    assert qin["pnl"] == pytest.approx(34.75)
    assert qin["hit"] is None
    total = sum(r["returned"] for r in rows.values() if r["stake"] == 30.0)
    assert total == pytest.approx(129.50)


def test_a_block_with_no_credit_settles_as_a_loss_not_as_unsettled(tmp_path, db):
    """A statement is written after the meeting. No credit means it lost."""
    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP.replace("$129.50", "")), encoding="utf-8")
    import_statement.run(src, db=db)
    conn = get_conn(db)
    row = conn.execute("SELECT returned, pnl, hit, status, settle_method "
                       "FROM bets WHERE bet_type = 'QIN'").fetchone()
    conn.close()
    assert row["returned"] == 0 and row["pnl"] == -30.0
    # A zero credit is not ambiguous: neither pool paid.
    assert row["hit"] == 0 and row["status"] == "settled"
    assert row["settle_method"] == "bookie_statement"


def test_selections_are_written_so_a_horse_can_be_found_by_number(tmp_path, db):
    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP), encoding="utf-8")
    import_statement.run(src, db=db)
    conn = get_conn(db)
    nos = sorted(r["horse_no"] for r in conn.execute(
        "SELECT DISTINCT horse_no FROM bet_selections"))
    conn.close()
    assert nos == [3, 4, 8]


def _legacy_pair(conn, *, qin=90.0, qpl=39.5):
    """Ref 2209 as the legacy log has it — both halves of the block, settled
    from their own dividends, summing to the $129.50 the statement paid."""
    with transaction(conn):
        for bid, btype, ret in (("legacy01", "QIN", qin),
                                ("legacy02", "QPL", qpl)):
            conn.execute(
                "INSERT INTO bets (bet_id, bookie_ref, race_date, race_no, "
                "bet_type, stake, returned, pnl, status, hit, settle_method, "
                "placed_at, source) VALUES (?,'2209','2026-04-22',1,?,30.0,"
                "?,?,'settled',1,'dividend','2026-04-28T02:00:53',"
                "'legacy_log')", (bid, btype, ret, ret - 30.0))


def test_a_bet_already_in_the_ledger_is_updated_not_written_twice(tmp_path, db):
    """The legacy log was itself written from these statements and carries
    their references under ids of its own. Matching on the reference stopped
    the 26 April meeting appearing twice — 49 bets, $2,596 staked, counted as
    $5,192."""
    conn = get_conn(db)
    _legacy_pair(conn)
    conn.close()

    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP), encoding="utf-8")
    report = import_statement.run(src, db=db)

    conn = get_conn(db)
    rows = conn.execute("SELECT bet_id, bet_type, returned, settle_method, "
                        "source FROM bets ORDER BY bet_type").fetchall()
    conn.close()
    assert report.bets == 2 and report.new_bets == 0   # both halves were there
    assert [r["bet_id"] for r in rows] == ["legacy01", "legacy02"]
    # The pools settled from their own dividends are not overwritten by halves
    # apportioned off one block credit.
    assert [r["returned"] for r in rows] == [90.0, 39.5]
    assert {r["settle_method"] for r in rows} == {"dividend"}


def test_an_apportioned_half_never_overwrites_a_settled_return(tmp_path, db):
    """The legacy log has ref 2209 as $90.00 win and $39.50 place. Both sum to
    the $129.50 the statement shows, which an apportionment can only halve —
    so the real split stands and the guess does not replace it."""
    conn = get_conn(db)
    _legacy_pair(conn)
    conn.close()

    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP), encoding="utf-8")
    import_statement.run(src, db=db)

    conn = get_conn(db)
    rows = {r["bet_type"]: r for r in conn.execute(
        "SELECT bet_type, returned, hit FROM bets")}
    conn.close()
    assert rows["QIN"]["returned"] == 90.0
    assert rows["QPL"]["returned"] == pytest.approx(39.5)
    assert rows["QIN"]["hit"] == 1 and rows["QPL"]["hit"] == 1


def test_the_statement_timestamp_replaces_the_log_s_writing_time(tmp_path, db):
    """The log had `_bookie_placed_at` stripped and fell back to when the row
    was written — 495 of its 1,078 bets are stamped days or weeks after the
    race. The statement's own timestamp lands even on a row whose settlement
    is protected, so the two are gated separately rather than as one row."""
    conn = get_conn(db)
    _legacy_pair(conn)
    conn.close()

    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP), encoding="utf-8")
    import_statement.run(src, db=db)

    conn = get_conn(db)
    row = conn.execute("SELECT placed_at, returned FROM bets "
                       "WHERE bet_id = 'legacy01'").fetchone()
    conn.close()
    assert row["placed_at"] == "2026-04-22T18:40:00"   # the wager
    assert row["returned"] == 90.0                     # still the real split


def test_a_ledger_block_that_does_not_add_up_is_corrected_by_the_statement(
        tmp_path, db):
    """"Already settled" cannot be assumed. The legacy log has refs 2217 and
    2218 at $0 returned while its own notes quote credits of $80 and $40 — the
    old settler simply missed two winning blocks. Where the ledger's block
    disagrees with the statement, the bookie's own record wins, and the $120 it
    is owed comes back."""
    conn = get_conn(db)
    _legacy_pair(conn, qin=0.0, qpl=0.0)      # the settler missed this block
    conn.close()

    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP), encoding="utf-8")   # ref 2209, $129.50 credit
    import_statement.run(src, db=db)

    conn = get_conn(db)
    rows = conn.execute("SELECT bet_id, returned, settle_method FROM bets "
                        "ORDER BY bet_id").fetchall()
    conn.close()
    assert sum(r["returned"] for r in rows) == pytest.approx(129.50)
    assert {r["settle_method"] for r in rows} == {"statement_apportioned"}


def test_which_bets_a_statement_was_actually_read_for_is_recorded(tmp_path, db):
    """A reference recovered out of the legacy log's notes is the log quoting a
    statement nobody imported. Without this table the reconciliation reported
    all 1,078 bets as reconciled when two statements had been read."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute(
            "INSERT INTO bets (bet_id, bookie_ref, race_date, race_no, "
            "bet_type, stake, returned, pnl, status, hit, source) "
            "VALUES ('quoted','9999','2026-04-22',1,'QIN',50.0,0.0,-50.0,"
            "'settled',0,'legacy_log')")
    conn.close()

    src = tmp_path / "s.txt"
    src.write_text(_stmt(QQP), encoding="utf-8")
    import_statement.run(src, db=db)

    conn = get_conn(db)
    seen = conn.execute(
        "SELECT bet_id, bookie_ref, source_file, stake, returned "
        "FROM bet_statement_rows ORDER BY bet_id").fetchall()
    conn.close()
    assert len(seen) == 2                            # the two halves of 2209
    assert {r["bookie_ref"] for r in seen} == {"2209"}
    assert "quoted" not in {r["bet_id"] for r in seen}
    # The BLOCK's figures, not the halves — comparing the halves to themselves
    # would prove nothing.
    assert {r["stake"] for r in seen} == {60.0}
    assert {r["returned"] for r in seen} == {129.5}
