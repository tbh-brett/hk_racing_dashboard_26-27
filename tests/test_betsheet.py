"""The bet sheet — importing the spreadsheet the betting is planned in.

The HKJC statement is the bookie's record of Brett's account: it exists only
after the meeting, and settles. This is the other half: Kelvin's book, kept in a
spreadsheet, and the only record of his tickets there is — so it settles them
too, from its own Return and P/L.

The fixture is the owner's own sheet for 2026-09-06, unedited. Its four
tickets are the whole of the check, because each one states a total that this
project's combination arithmetic has to arrive at independently:

    bet 1  3x4 over two QQP legs and a WP leg     20 lines x $200 = $4,000
    bet 2  QQP banker with four                    8 lines x $300 = $2,400
    bet 3  QQP banker with four                    8 lines x $300 = $2,400
    bet 4  QQP banker with four                    8 lines x $200 = $1,600

Two failures matter more than the happy path and are pinned separately: a horse
whose name is not on the card must be reported rather than guessed at, and a
stake that disagrees with the structure must be reported rather than quietly
overwritten — the sheet's figure is what was actually staked, so it is stored,
but the disagreement means one of the two has the ticket wrong.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.ingest import betsheet
from hkrd.jobs import import_betsheet
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIXTURE = Path(__file__).parent / "fixtures" / "betsheet.csv"
DATE = "2026-09-06"

# The runners the sheet names, with the numbers the card actually gave them.
CARD = {
    5: {1: "ISLAND BUDDY", 3: "CAVA PIONEER", 7: "LIGHT YEARS GLORY",
        8: "NEXT FORTUNE", 10: "RIDING HIGH", 14: "JOLLY COMPANION"},
    8: {1: "JOYFUL JOY", 3: "ANOTHER WORLD", 4: "POSITIVE SMILE",
        6: "GRAND PATCH", 10: "GOLDENTRONICMIGHTY"},
    10: {1: "PUBLIC ATTENTION", 3: "AKASHVANI", 6: "GOLD PATCH",
         7: "LUCY IN THE SKY", 8: "SUPER STRONG KID", 12: "MASTER PAYMENT"},
}


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "sheet.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": r, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200}
            for r in CARD])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": r, "horse_no": no,
             "horse_name": name}
            for r, field in CARD.items() for no, name in field.items()])
    conn.close()
    return path


def _text() -> str:
    return FIXTURE.read_text(encoding="utf-8-sig")


# ─── reading the sheet ────────────────────────────────────────────────────────

def test_one_bet_is_several_rows():
    """An all-up carries one row per race, sharing a bet number, and the stake
    sits on the last of them."""
    parsed = betsheet.parse_text(_text(), source="sheet")
    assert [t["bet_no"] for t in parsed.tickets] == ["1", "2", "3", "4"]
    chain = parsed.tickets[0]
    assert [l["race_no"] for l in chain["legs"]] == [5, 8, 10]
    assert chain["structure"] == "3x4"
    assert (chain["unit_stake"], chain["total_stake"]) == (200.0, 4000.0)
    assert not parsed.skipped


def test_the_leg_columns_mean_two_different_things_and_the_row_says_which():
    """On a single-race row `Banker` is the banker and the Leg columns are what
    it combines with; on an all-up row the Leg columns are that leg's own
    selections. Nothing guesses — the row's Bet Type decides."""
    parsed = betsheet.parse_text(_text(), source="sheet")
    single = parsed.tickets[1]["legs"][0]
    assert single["banker"] == "ISLAND BUDDY"
    assert single["selections"] == ["LIGHT YEARS GLORY", "NEXT FORTUNE",
                                    "RIDING HIGH", "JOLLY COMPANION"]
    assert parsed.tickets[0]["legs"][0]["banker"] is None


def test_the_date_is_read_day_second():
    """9/6/2026 is the sixth of September. Read the other way it would file a
    whole card under a date with no runners — loud, but only afterwards."""
    parsed = betsheet.parse_text(_text(), source="sheet")
    assert {t["race_date"] for t in parsed.tickets} == {"2026-09-06"}


def test_a_file_that_is_not_a_sheet_is_refused_by_name():
    with pytest.raises(betsheet.SheetError, match="Bet no"):
        betsheet.parse_text("Account Records\nRef No.\n3593\n", source="stmt")


# ─── importing it ─────────────────────────────────────────────────────────────

def test_the_sheets_own_totals_are_reproduced_by_the_arithmetic(db):
    """Four tickets, no disagreements. If the combination arithmetic drifts,
    this is where it shows up — against money that actually moved."""
    report = import_betsheet.run(FIXTURE, db=db, account="brett")
    assert (report.tickets, report.new_tickets) == (4, 4)
    assert report.disagreements == []
    assert report.unmatched == [] and report.errors == []

    conn = get_conn(db)
    try:
        rows = {r["bet_type"]: r for r in conn.execute(
            "SELECT bet_type, race_no, stake, status, all_up_formula "
            "FROM bets WHERE source = 'bet_sheet'")}
    finally:
        conn.close()
    assert rows["ALLUP_3x4"]["stake"] == 4000.0
    assert rows["ALLUP_3x4"]["race_no"] is None       # a chain spans races
    assert rows["ALLUP_3x4"]["all_up_formula"] == "3x4"
    assert rows["QQP_BANKER"]["status"] == "open"


def test_horses_become_numbers_and_the_banker_stays_the_banker(db):
    import_betsheet.run(FIXTURE, db=db, account="brett")
    conn = get_conn(db)
    try:
        legs = [dict(r) for r in conn.execute(
            "SELECT s.race_no, s.horse_no, s.leg_no, s.is_banker "
            "FROM bet_selections s JOIN bets b USING (bet_id) "
            "WHERE b.bet_type = 'ALLUP_3x4' ORDER BY s.leg_no, s.horse_no")]
        banker = conn.execute(
            "SELECT s.horse_no FROM bet_selections s JOIN bets b USING (bet_id) "
            "WHERE b.race_no = 5 AND s.is_banker = 1").fetchone()
    finally:
        conn.close()
    assert [(l["race_no"], l["horse_no"], l["leg_no"]) for l in legs] == [
        (5, 1, 1), (5, 7, 1), (8, 1, 2), (8, 4, 2), (10, 8, 3)]
    assert banker["horse_no"] == 1                    # ISLAND BUDDY


def test_a_name_the_card_never_heard_of_is_reported_not_guessed(db):
    """A fuzzy match is how a bet ends up recorded against the wrong horse,
    and nothing downstream would ever show it. The horse is left out; the
    ticket is not, because its stake is money that moved."""
    text = _text().replace("SUPER STRONG KID", "SUPER STRONK KID")
    report = import_betsheet.run_text(text, name="sheet.csv", db=db)
    assert any("SUPER STRONK KID" in u for u in report.unmatched)
    assert report.tickets == 4, "a typo must not take a ticket out of the ledger"
    assert report.disagreements == [], "a short ticket is not a second fault"
    conn = get_conn(db)
    try:
        horses = {r["horse_no"] for r in conn.execute(
            "SELECT horse_no FROM bet_selections WHERE race_no = 10")}
        stake = conn.execute("SELECT stake FROM bets WHERE race_no = 10"
                             ).fetchone()["stake"]
    finally:
        conn.close()
    assert 8 not in horses                            # nothing guessed for it
    assert stake == 1600.0


def test_a_stake_that_disagrees_with_the_structure_is_named(db):
    """Stored at the sheet's figure — it is what was staked — and reported,
    because the disagreement means one of the two has the ticket wrong."""
    text = _text().replace(",200,4000", ",200,400")
    report = import_betsheet.run_text(text, name="sheet.csv", db=db)
    assert len(report.disagreements) == 1
    assert "$400" in report.disagreements[0] and "$4,000" in report.disagreements[0]

    conn = get_conn(db)
    try:
        stake = conn.execute("SELECT stake FROM bets WHERE bet_type = "
                             "'ALLUP_3x4'").fetchone()["stake"]
    finally:
        conn.close()
    assert stake == 400.0


def test_re_importing_a_corrected_sheet_updates_rather_than_duplicates(db):
    """A spreadsheet gets edited after the fact. The bet number and the date
    identify the ticket, so the second read is the same four bets."""
    import_betsheet.run(FIXTURE, db=db, account="brett")
    again = import_betsheet.run_text(
        _text().replace(",300,2400", ",300,2400"), name="betsheet.csv", db=db)
    assert again.new_tickets == 0
    conn = get_conn(db)
    try:
        assert conn.execute("SELECT count(*) FROM bets").fetchone()[0] == 4
    finally:
        conn.close()


def test_a_meeting_with_no_card_is_an_error_not_a_silent_drop(db):
    text = _text().replace("9/6/2026", "9/13/2026")
    report = import_betsheet.run_text(text, name="sheet.csv", db=db)
    assert report.tickets == 0
    assert any("no card stored" in e for e in report.errors)


# ─── whose book, and what it returned ────────────────────────────────────────

def _rows(db, sql: str, args: tuple = ()) -> list[dict]:
    conn = get_conn(db)
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def test_a_sheet_is_kelvins_book_when_nobody_says(db):
    """The file decides the ledger. 13 Nov 2026: eleven of Kelvin's tickets
    sat in Brett's because the page's filter chose for it."""
    report = import_betsheet.run_text(_text(), name="sheet.csv", db=db)
    assert report.account == "kelvin"
    assert {r["account"] for r in _rows(db, "SELECT account FROM bets")} \
        == {"kelvin"}


def test_re_importing_moves_a_ticket_filed_under_the_wrong_book(db):
    import_betsheet.run(FIXTURE, db=db, account="brett")
    again = import_betsheet.run(FIXTURE, db=db)
    assert again.new_tickets == 0
    assert {r["account"] for r in _rows(db, "SELECT account FROM bets")} \
        == {"kelvin"}


_SETTLED = _text().replace(
    "Stakes per bet,Total stakes", "Stakes per bet,Total stakes,Return,P/L"
).replace(",200,4000", ",200,4000,9150,5150"
).replace(",300,2400\n3,", ",300,2400,,-2400\n3,")


def test_the_sheets_return_settles_the_ticket(db):
    """No statement covers Kelvin's account; the sheet's own columns do."""
    report = import_betsheet.run_text(_SETTLED, name="sheet.csv", db=db)
    assert report.settled == 2 and report.disagreements == []
    rows = {r["bet_type"] + str(r["race_no"]): r for r in _rows(
        db, "SELECT bet_type, race_no, returned, pnl, status, hit, "
            "settle_method FROM bets")}
    chain = rows["ALLUP_3x4None"]
    assert (chain["returned"], chain["pnl"], chain["status"], chain["hit"]) \
        == (9150.0, 5150.0, "settled", 1)
    assert chain["settle_method"] == "bet_sheet"
    lost = rows["QQP_BANKER5"]                      # P/L only: a loss
    assert (lost["returned"], lost["pnl"], lost["hit"]) == (0.0, -2400.0, 0)
    assert rows["QQP_BANKER8"]["status"] == "open"  # neither column written


def test_a_result_some_other_source_settled_is_not_overwritten(db):
    import_betsheet.run_text(_text(), name="sheet.csv", db=db)
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE bets SET status = 'settled', returned = 77, "
                     "pnl = -3923, hit = 1, settle_method = 'manual' "
                     "WHERE bet_type = 'ALLUP_3x4'")
    conn.close()
    import_betsheet.run_text(_SETTLED, name="sheet.csv", db=db)
    row = _rows(db, "SELECT returned FROM bets WHERE bet_type = 'ALLUP_3x4'")
    assert row[0]["returned"] == 77.0


def _six_up_card(db, date: str) -> None:
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": date, "race_no": r, "venue": "HV", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200}
            for r in range(3, 9)])
        upsert.upsert_runners(conn, [
            {"race_date": date, "race_no": r, "horse_no": n,
             "horse_name": f"HORSE {r}{n}"}
            for r in range(3, 9) for n in range(1, 4)])
    conn.close()


def test_a_six_up_is_its_own_pool_and_costs_the_product_of_its_legs(db):
    _six_up_card(db, "2026-09-16")
    rows = ["Bet no,Date,Race,Bet Type,Banker,Leg 1,Leg 2,Leg 3,"
            "Stakes per bet,Total stakes,Return,P/L"]
    for r in range(3, 9):
        picks = [f"HORSE {r}{n}" for n in range(1, 4 if r < 5 else 2)]
        money = ",2,18,955.5,937.5" if r == 8 else ",,,,"
        rows.append(f"7,9/16/2026,{r},SIX UP,," + ",".join(
            picks + [""] * (3 - len(picks))) + money)
    report = import_betsheet.run_text("\n".join(rows), name="s.csv", db=db)
    assert report.disagreements == [] and report.skipped == []
    bet = _rows(db, "SELECT bet_type, race_no, stake, returned FROM bets")[0]
    # 3 x 3 x 1 x 1 x 1 x 1 = 9 lines at $2: the $18 the sheet says
    assert (bet["bet_type"], bet["race_no"], bet["stake"], bet["returned"]) \
        == ("SIXUP", None, 18.0, 955.5)
    legs = _rows(db, "SELECT DISTINCT leg_no FROM bet_selections")
    assert sorted(l["leg_no"] for l in legs) == [1, 2, 3, 4, 5, 6]


def test_a_ticket_with_no_race_numbers_keeps_its_money(db):
    """8 Nov 2026 was written with the Race column empty on every row."""
    text = _SETTLED.replace("1,9/6/2026,5,", "1,9/6/2026,,") \
                   .replace("1,9/6/2026,8,", "1,9/6/2026,,") \
                   .replace("1,9/6/2026,10,", "1,9/6/2026,,")
    report = import_betsheet.run_text(text, name="sheet.csv", db=db)
    assert sum("no race number" in s for s in report.skipped) == 3
    chain = _rows(db, "SELECT stake, returned FROM bets "
                      "WHERE bet_type = 'ALLUP_3x4'")
    assert chain == [{"stake": 4000.0, "returned": 9150.0}]


def test_a_leg_whose_pool_is_not_named_keeps_the_ticket(db):
    text = _text().replace("All-Up,3x4,WP", "All-Up,3x4,")
    report = import_betsheet.run_text(text, name="sheet.csv", db=db)
    assert any("not a pool this ledger knows" in s for s in report.skipped)
    assert report.tickets == 4


def test_the_upload_route_files_by_kind_and_ignores_the_pages_filter(
        db, monkeypatch):
    """The filter said BRETT; the file is Kelvin's sheet, so it is Kelvin's."""
    import json

    from hkrd.api.routes.jobs import import_bets_job

    monkeypatch.setenv("HKRD_DB", str(db))
    out = import_bets_job({"text": _text(), "name": "Bets with Kelvin.csv",
                           "account": "brett"})
    body = json.loads(out.body)
    assert (body["kind"], body["account"]) == ("bet sheet", "kelvin")
    assert {r["account"] for r in _rows(db, "SELECT account FROM bets")} \
        == {"kelvin"}


def test_q_on_the_sheet_is_the_quinella(db):
    """21 Oct 2026, bet 17: `Q` banker with four at $300 is $1,200."""
    text = _text().replace("4,9/6/2026,10,QQP,", "4,9/6/2026,10,Q,") \
                  .replace(",200,1600", ",300,1200")
    report = import_betsheet.run_text(text, name="sheet.csv", db=db)
    assert report.skipped == [] and report.disagreements == []
    assert _rows(db, "SELECT stake FROM bets WHERE bet_type = 'QIN_BANKER'") \
        == [{"stake": 1200.0}]
