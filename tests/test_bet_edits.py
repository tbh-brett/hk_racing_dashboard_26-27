"""Correcting the ledger: edit a bet, delete one, put one back.

Until this existed the ledger was append-only from the page, and a bet entered
twice, filed to the wrong account or typed with a short stake could only be
fixed in the database itself.

Three properties are pinned, in order of how badly getting them wrong would
mislead:

  **A re-import must not undo a correction.** Every importer re-derives bets
  from a file each time it reads one. A deleted statement bet came straight
  back on the next statement import, and an edited field was overwritten by
  the file's — silently. That is the case these tests exist for.

  **A deleted bet must stop counting everywhere at once.** It is removed from
  the live tables rather than flagged in them, so the ledger, the summary, the
  analysis, the raceday ceiling and the account totals are all correct without
  each of them having to remember to skip it.

  **Nothing is lost.** A deletion archives the bet and every child row and can
  be restored exactly; an edit records what the field was.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hkrd.jobs import edit_bet as job
from hkrd.jobs import import_betsheet, import_statement
from hkrd.query import bets as bets_q, prebet
from hkrd.store import bet_edits, bets as bet_store, upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIXTURES = Path(__file__).parent / "fixtures"
DATE = "2026-09-06"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A card, and one manual bet on it with a banker and a leg."""
    path = tmp_path / "edits.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": r, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200}
            for r in (1, 4, 5, 7, 8, 9, 10)])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": r, "horse_no": n,
             "horse_name": f"R{r} HORSE {n}", "place": str(n)}
            for r in (1, 4, 5, 7, 8, 9, 10) for n in range(1, 15)])
    bet_store.insert_bet(conn, {
        "bet_id": "manual-1", "account": "brett", "race_date": DATE,
        "venue": "ST", "race_no": 1, "bet_type": "QIN_BANKER", "stake": 40.0,
    }, [{"race_no": 1, "horse_no": 2, "is_banker": True},
        {"race_no": 1, "horse_no": 4}, {"race_no": 1, "horse_no": 7}])
    conn.close()
    monkeypatch.setenv("HKRD_DB", str(path))
    return path


def _bet(db, bet_id):
    conn = get_conn(db)
    try:
        row = conn.execute("SELECT * FROM bets WHERE bet_id = ?",
                           (bet_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ─── editing ──────────────────────────────────────────────────────────────────

def test_the_account_can_be_moved(db):
    """The likeliest correction there is: a bet filed to the wrong book."""
    out = job.edit("manual-1", changes={"account": "kelvin"}, db=str(db))
    assert out["changed"] == ["account"]
    assert _bet(db, "manual-1")["account"] == "kelvin"


def test_settling_by_hand_derives_the_rest(db):
    """The owner says what came back; profit, hit and status follow, so they
    cannot be left disagreeing — an open bet with a profit is not a state the
    ledger can be put in."""
    job.edit("manual-1", changes={"returned": 171.0}, db=str(db))
    bet = _bet(db, "manual-1")
    assert (bet["returned"], bet["pnl"], bet["hit"], bet["status"]) == (
        171.0, 131.0, 1, "settled")
    assert bet["settle_method"] == "manual"

    job.edit("manual-1", changes={"returned": 0}, db=str(db))
    bet = _bet(db, "manual-1")
    assert (bet["pnl"], bet["hit"], bet["status"]) == (-40.0, 0, "settled")

    job.edit("manual-1", changes={"returned": None}, db=str(db))
    bet = _bet(db, "manual-1")
    assert (bet["pnl"], bet["hit"], bet["status"]) == (None, None, "open")


def test_void_is_a_refund_not_a_loss(db):
    """A scratching returns the stake. That is a real event and belongs in the
    analysis; it is not a deletion, and it is not a losing bet."""
    job.edit("manual-1", changes={"status": "void"}, db=str(db))
    bet = _bet(db, "manual-1")
    assert (bet["returned"], bet["pnl"], bet["hit"], bet["status"]) == (
        40.0, 0.0, None, "void")


def test_the_horses_backed_can_be_changed(db):
    job.edit("manual-1", selections=[
        {"race_no": 1, "horse_no": 2, "is_banker": True},
        {"race_no": 1, "horse_no": 9}], db=str(db))
    conn = get_conn(db)
    try:
        rows = conn.execute("SELECT horse_no, is_banker FROM bet_selections "
                            "WHERE bet_id = 'manual-1' ORDER BY horse_no").fetchall()
    finally:
        conn.close()
    assert [(r["horse_no"], r["is_banker"]) for r in rows] == [(2, 1), (9, 0)]


def test_a_horse_the_card_does_not_have_is_refused_by_number(db):
    """A selection is a join key into `runners`. One that matches nothing is a
    bet the Blackbook can never see and the ledger can never name."""
    with pytest.raises(job.BetEditError, match="no runner 22"):
        job.edit("manual-1", selections=[{"race_no": 1, "horse_no": 22}],
                 db=str(db))
    # And nothing was half-applied.
    conn = get_conn(db)
    try:
        assert conn.execute("SELECT count(*) FROM bet_selections "
                            "WHERE bet_id = 'manual-1'").fetchone()[0] == 3
    finally:
        conn.close()


def test_two_bankers_in_one_leg_is_refused(db):
    with pytest.raises(job.BetEditError, match="2 bankers"):
        job.edit("manual-1", selections=[
            {"race_no": 1, "horse_no": 2, "is_banker": True},
            {"race_no": 1, "horse_no": 4, "is_banker": True}], db=str(db))


@pytest.mark.parametrize("changes, message", [
    ({"stake": 0}, "stake cannot be 0"),
    ({"stake": -5}, "stake cannot be"),
    ({"account": "client"}, "unknown account"),
    ({"status": "deleted"}, "status must be"),
    ({"race_date": "6/9/2026"}, "YYYY-MM-DD"),
    ({"bet_id": "x"}, "not an editable field"),
    ({"pnl": 100}, "not an editable field"),
])
def test_a_correction_that_cannot_stand_is_refused_with_a_reason(db, changes, message):
    with pytest.raises(job.BetEditError, match=message):
        job.edit("manual-1", changes=changes, db=str(db))


def test_saving_an_unchanged_form_is_not_an_edit(db):
    """Otherwise every field on the form starts being protected from the next
    import, which the owner never asked for."""
    out = job.edit("manual-1", changes={"account": "brett", "stake": 40,
                                        "notes": None}, db=str(db))
    assert out["changed"] == []
    assert bets_q.history("manual-1") == []


def test_every_correction_is_recorded(db):
    job.edit("manual-1", changes={"stake": 60}, db=str(db))
    job.edit("manual-1", changes={"account": "kelvin"}, db=str(db))
    edits = bets_q.history("manual-1")
    assert [(e["field"], e["old"], e["new"]) for e in edits] == [
        ("stake", 40.0, 60.0), ("account", "brett", "kelvin")]


# ─── deleting ─────────────────────────────────────────────────────────────────

def test_a_deleted_bet_stops_counting_everywhere(db):
    """Removed from the live tables, not flagged in them — so every reader is
    correct without each having to remember to skip it."""
    before = prebet.raceday_total(DATE)["staked"]
    job.delete("manual-1", reason="entered twice", db=str(db))
    assert _bet(db, "manual-1") is None
    assert bets_q.ledger() == []
    assert prebet.raceday_total(DATE)["staked"] == before - 40.0
    conn = get_conn(db)
    try:
        assert conn.execute("SELECT count(*) FROM bet_selections "
                            "WHERE bet_id = 'manual-1'").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_deleted_bet_can_be_put_back_exactly(db):
    """Children and all — the selections, and the overrides and links nothing
    else could rebuild."""
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("INSERT INTO bet_overrides (bet_id, flag, detail, "
                     "overridden_at) VALUES ('manual-1', 'raceday_ceiling', "
                     "'over', '2026-09-06T12:00:00')")
    conn.close()
    original = _bet(db, "manual-1")

    job.delete("manual-1", db=str(db))
    listed = bets_q.deleted()
    assert [b["bet_id"] for b in listed] == ["manual-1"]
    assert listed[0]["stake"] == 40.0

    job.restore("manual-1", db=str(db))
    assert _bet(db, "manual-1") == original
    assert bets_q.deleted() == []
    conn = get_conn(db)
    try:
        assert conn.execute("SELECT count(*) FROM bet_selections "
                            "WHERE bet_id = 'manual-1'").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM bet_overrides "
                            "WHERE bet_id = 'manual-1'").fetchone()[0] == 1
    finally:
        conn.close()


def test_deleting_twice_or_restoring_nothing_says_so(db):
    job.delete("manual-1", db=str(db))
    with pytest.raises(job.BetEditError, match="already be deleted"):
        job.delete("manual-1", db=str(db))
    with pytest.raises(job.BetEditError, match="no deleted bet"):
        job.restore("never-existed", db=str(db))


# ─── the importers ────────────────────────────────────────────────────────────

def test_a_statement_import_does_not_bring_a_deleted_bet_back(db):
    """The case this whole module is for. The statement importer matches bets
    by reference; with the original gone it would mint a fresh id and write
    the deleted bet straight back under it."""
    stmt = FIXTURES / "statement_2026-09-06.txt"
    first = import_statement.run(stmt, db=db, account="brett")
    assert first.new_bets > 0
    conn = get_conn(db)
    try:
        victim = conn.execute(
            "SELECT bet_id, bookie_ref, bet_type FROM bets "
            "WHERE source = 'statement' ORDER BY bet_id LIMIT 1").fetchone()
    finally:
        conn.close()

    job.delete(victim["bet_id"], reason="not mine", db=str(db))
    again = import_statement.run(stmt, db=db, account="brett")
    assert again.left_deleted == 1
    assert again.new_bets == 0
    assert _bet(db, victim["bet_id"]) is None
    conn = get_conn(db)
    try:
        assert conn.execute(
            "SELECT count(*) FROM bets WHERE bookie_ref = ? AND bet_type = ?",
            (victim["bookie_ref"], victim["bet_type"])).fetchone()[0] == 0
    finally:
        conn.close()


def test_a_statement_import_does_not_overwrite_a_correction(db):
    """The owner's value stands for the fields they touched, and ONLY those: a
    bet whose account was moved still takes the statement's settlement."""
    stmt = FIXTURES / "statement_2026-09-06.txt"
    import_statement.run(stmt, db=db, account="brett")
    conn = get_conn(db)
    try:
        bet_id = conn.execute(
            "SELECT bet_id FROM bets WHERE source = 'statement' "
            "ORDER BY bet_id LIMIT 1").fetchone()[0]
    finally:
        conn.close()

    job.edit(bet_id, changes={"account": "kelvin", "notes": "shared ticket"},
             db=str(db))
    # Something the statement owns changes underneath, as a re-issued
    # statement would: the owner never touched `returned`.
    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE bets SET returned = -1 WHERE bet_id = ?", (bet_id,))
    conn.close()

    import_statement.run(stmt, db=db, account="brett")
    bet = _bet(db, bet_id)
    assert bet["account"] == "kelvin", "the import put the account back"
    assert bet["notes"] == "shared ticket"
    assert bet["returned"] != -1, "the statement's settlement should still land"


def test_a_bet_sheet_import_honours_both(db):
    """The sheet names horses, so the card has to carry the real names for the
    importer to resolve them — the same card `test_betsheet` uses."""
    from tests.test_betsheet import CARD

    conn = get_conn(db)
    with transaction(conn):
        conn.execute("DELETE FROM runners WHERE race_date = ? "
                     "AND race_no IN (5, 8, 10)", (DATE,))
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": r, "horse_no": no, "horse_name": name}
            for r, field in CARD.items() for no, name in field.items()])
    conn.close()

    sheet = FIXTURES / "betsheet.csv"
    import_betsheet.run(sheet, db=db, account="brett")
    conn = get_conn(db)
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT bet_id FROM bets WHERE source = 'bet_sheet' ORDER BY bet_id")]
    finally:
        conn.close()
    assert len(ids) == 4

    job.delete(ids[0], db=str(db))
    job.edit(ids[1], changes={"stake": 999}, db=str(db))
    again = import_betsheet.run(sheet, db=db, account="brett")
    assert again.left_deleted == 1
    assert _bet(db, ids[0]) is None
    assert _bet(db, ids[1])["stake"] == 999.0


def test_restoring_lets_the_next_import_see_it_again(db):
    """The tombstone goes with the restore, so a restored bet is an ordinary bet
    again — including to the importer."""
    stmt = FIXTURES / "statement_2026-09-06.txt"
    import_statement.run(stmt, db=db, account="brett")
    conn = get_conn(db)
    try:
        bet_id = conn.execute("SELECT bet_id FROM bets WHERE source = 'statement' "
                              "LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    job.delete(bet_id, db=str(db))
    job.restore(bet_id, db=str(db))
    again = import_statement.run(stmt, db=db, account="brett")
    assert again.left_deleted == 0 and _bet(db, bet_id) is not None


# ─── the routes ───────────────────────────────────────────────────────────────

def test_the_api_edits_deletes_and_restores(db):
    from fastapi.testclient import TestClient
    from hkrd.api.app import app

    client = TestClient(app)
    r = client.patch("/api/bets/manual-1", json={"changes": {"stake": 80}})
    assert r.status_code == 200 and r.json()["changed"] == ["stake"]

    r = client.patch("/api/bets/manual-1", json={"changes": {"stake": 0}})
    assert r.status_code == 422 and "stake cannot be" in r.json()["detail"]

    assert client.get("/api/bets/manual-1/history").json()["edits"][0]["new"] == 80.0

    r = client.delete("/api/bets/manual-1", params={"reason": "duplicate"})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.delete("/api/bets/manual-1").status_code == 404
    assert [b["bet_id"] for b in client.get("/api/bets/deleted").json()["bets"]] == [
        "manual-1"]

    r = client.post("/api/bets/manual-1/restore")
    assert r.status_code == 200
    assert client.post("/api/bets/manual-1/restore").status_code == 409
