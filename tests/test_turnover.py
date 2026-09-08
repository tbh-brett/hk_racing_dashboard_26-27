"""ingest/turnover — how much money each pool holds.

The test that matters most here is `test_a_pool_that_is_not_open_is_null_not
_zero`. HKJC reports null on a pool that has not opened and 0 on one that is
open and untouched, and a series that conflates them shows the whole pool
arriving the instant a card opens.

The fixture is the reply shape read off the live endpoint for 2026-09-09 HV,
where 88 pool rows came back with every amount null because the market had not
opened.
"""
from __future__ import annotations

import pytest

from hkrd.ingest import turnover
from hkrd.ingest.odds import OddsError
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db

DATE = "2026-09-09"
MID = "MTG_20260909_0001"
CAP = "2026-09-08T11:00:00"


def _pool(kind, races, investment, *, leg_no=1, pid=None, merged=None):
    return {"id": pid or f"{MID}{kind}{leg_no}", "oddsType": kind,
            "status": "START_SELL", "sellStatus": "START_SELL",
            "leg": {"number": leg_no, "races": races},
            "investment": investment, "mergedPoolId": merged,
            "lastUpdateTime": ""}


def parse(pools, total=None):
    return turnover.investment_rows(
        {"poolInvs": pools, "totalInvestment": total},
        date=DATE, venue="HV", expect_id=MID, captured_at=CAP)


def test_an_amount_arrives_as_a_string():
    """`investment` and `totalInvestment` are both quoted in the reply."""
    rows = parse([_pool("WIN", [1], "1481817")])
    assert rows[0]["turnover"] == pytest.approx(1_481_817)
    assert rows[0]["pool"] == "WIN" and rows[0]["race_no"] == 1


def test_a_pool_that_is_not_open_is_null_not_zero():
    """Observed live: every pool on a declared card reports null until it
    opens. Zero would claim an open pool nobody had bet into, which is a
    different and false statement — and it would make the first real capture
    look like the entire pool arriving at once."""
    rows = parse([_pool("WIN", [1], None), _pool("QIN", [1], "0")])
    by = {r["pool"]: r["turnover"] for r in rows}
    assert by["WIN"] is None
    assert by["QIN"] == 0.0


def test_a_cross_race_pool_is_filed_under_the_first_race_of_its_leg():
    """A double spans two races and HKJC's own turnover page shows it under the
    first. `leg.races` names them, so the choice is read rather than assumed."""
    rows = parse([_pool("DBL", [3, 4], "84916", leg_no=3)])
    assert rows[0]["race_no"] == 3 and rows[0]["pool"] == "DBL"


def test_the_meeting_total_is_not_filed_against_a_race():
    """Given a real race number it would double-count in any sum over the card."""
    rows = parse([_pool("WIN", [1], "100")], total="7204033")
    total = [r for r in rows if r["pool"] == turnover.TOTAL_POOL]
    assert len(total) == 1
    assert total[0]["race_no"] == 0
    assert total[0]["turnover"] == pytest.approx(7_204_033)


def test_a_merged_pool_records_what_it_merged_into():
    """Quartet and First 4 are one line on the page because the money is
    reported once. A sum that does not know which rows are merged will not
    reconcile with the published total."""
    rows = parse([_pool("FF", [1], None, merged=f"{MID}QTT1")])
    assert rows[0]["merged_into"] == f"{MID}QTT1"


def test_a_pool_from_another_meeting_is_refused():
    with pytest.raises(OddsError, match="does not belong"):
        parse([_pool("WIN", [1], "100", pid="MTG_20260912_0001WIN1")])


def test_a_reply_without_poolinvs_raises():
    """The alias is load-bearing: the whitelisted query names it `poolInvs`,
    and a reply without it is a shape change rather than a quiet meeting."""
    with pytest.raises(OddsError, match="poolInvs"):
        turnover.investment_rows({"totalInvestment": "0"}, date=DATE,
                                 venue="HV", expect_id=MID, captured_at=CAP)


def test_a_negative_amount_is_refused():
    with pytest.raises(OddsError, match="negative"):
        parse([_pool("WIN", [1], "-5")])


def test_the_query_is_verbatim_and_must_not_be_tidied():
    """The endpoint whitelists whole query texts: a trimmed but perfectly valid
    subset of this one comes back WHITELIST_ERROR. Reformatting it would break
    the capture with a message that names neither the cause nor the fix."""
    q = turnover.INVESTMENT_QUERY
    assert "poolInvs: pmPools(" in q
    assert "totalInvestment" in q and "investment" in q
    assert q.startswith("query racing($date: String, $venueCode: String,")


def test_rows_are_written_and_the_write_is_idempotent(tmp_path):
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    rows = parse([_pool("WIN", [1], "100"), _pool("QIN", [1], "50")],
                 total="150")
    assert upsert.upsert_pool_turnover(conn, rows) == 3
    upsert.upsert_pool_turnover(conn, rows)
    assert conn.execute(
        "SELECT count(*) FROM odds_pool_turnover").fetchone()[0] == 3
    conn.close()


def test_a_later_capture_is_a_new_row_not_a_correction(tmp_path):
    """Turnover only rises. Two captures are two facts, and the series is the
    point: one figure is a size, two are a rate."""
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    upsert.upsert_pool_turnover(conn, parse([_pool("WIN", [1], "100")]))
    upsert.upsert_pool_turnover(conn, turnover.investment_rows(
        {"poolInvs": [_pool("WIN", [1], "500")], "totalInvestment": None},
        date=DATE, venue="HV", expect_id=MID,
        captured_at="2026-09-08T12:00:00"))
    got = conn.execute(
        "SELECT turnover FROM odds_pool_turnover WHERE pool = 'WIN' "
        "ORDER BY captured_at").fetchall()
    assert [r["turnover"] for r in got] == [100.0, 500.0]
    conn.close()
