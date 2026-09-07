"""The bet sheet — importing the spreadsheet the betting is planned in.

The HKJC statement is the bookie's record: it exists only after the meeting,
covers one account, and settles. This is the other half. It is kept alongside
the betting, it covers both accounts, and it is the only place an all-up exists
before the ticket is struck.

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
    and nothing downstream would ever show it."""
    text = _text().replace("SUPER STRONG KID", "SUPER STRONK KID")
    report = import_betsheet.run_text(text, name="sheet.csv", db=db)
    assert any("SUPER STRONK KID" in u for u in report.unmatched)
    assert report.tickets == 2, "the two tickets naming it are not written"


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
