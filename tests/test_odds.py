"""ingest/odds and jobs/import_legacy_odds.

odds_snapshots is the highest-value table in the schema and the only data here
that cannot be reconstructed after the fact. The rule this module exists to
enforce is that nothing ever deletes from it.
"""
from __future__ import annotations

import json

import pytest

from hkrd.ingest import odds
from hkrd.jobs import import_legacy_odds
from hkrd.store.connect import get_conn

PAYLOAD = {
    "scraped_at": "2026-07-15T09:00:00", "date": "2026-07-15", "venue": "HV",
    "race_no": 3, "n_runners": 3,
    "odds": [
        {"no": "10", "horse": "KYRUS TREASURE", "win": "3.7", "place": "1.5"},
        {"no": "9", "horse": "NOBLE PURSUIT", "win": "6.7", "place": "2.1"},
        {"no": "7", "horse": "FAST SPEED", "win": "---", "place": ""},
    ],
    "qin_odds": [{"a": "10", "b": "9", "odds": "14"},
                 {"a": "9", "b": "10", "odds": "14"}],
    "qpl_odds": [{"a": "10", "b": "9", "odds": "5.5"}],
}


def test_parse_snapshot_normalises_the_payload():
    s = odds.parse_snapshot(PAYLOAD)
    assert s["race_date"] == "2026-07-15" and s["race_no"] == 3
    assert s["captured_at"] == "2026-07-15T09:00:00"


def test_a_snapshot_without_a_timestamp_is_refused():
    """The entire value of this table is knowing WHEN a price was true."""
    with pytest.raises(odds.OddsError, match="scraped_at"):
        odds.parse_snapshot({**PAYLOAD, "scraped_at": ""})
    with pytest.raises(odds.OddsError, match="unparseable"):
        odds.parse_snapshot({**PAYLOAD, "scraped_at": "sometime tuesday"})


def test_no_price_offered_is_none_not_zero():
    """Scratched runners and pre-market races show '---'. Zero would read as a
    price of nothing, which is a different and impossible claim."""
    rows = odds.snapshot_rows(odds.parse_snapshot(PAYLOAD))
    scratched = next(r for r in rows if r["horse_no"] == 7)
    assert scratched["win_odds"] is None and scratched["place_odds"] is None


def test_win_and_place_are_both_captured():
    """Place odds cannot be derived from win odds -- there is no fixed
    relationship, it depends on how concentrated the market is. The common
    'a third of the win odds' rule is structurally invalid."""
    rows = odds.snapshot_rows(odds.parse_snapshot(PAYLOAD))
    fav = next(r for r in rows if r["horse_no"] == 10)
    assert fav["win_odds"] == pytest.approx(3.7)
    assert fav["place_odds"] == pytest.approx(1.5)


def test_pairs_are_stored_in_one_orientation():
    """A pair has one representation, so the two halves of the matrix cannot
    disagree with each other."""
    pairs = odds.pair_rows(odds.parse_snapshot(PAYLOAD))
    qin = [p for p in pairs if p["pool"] == "QIN"]
    assert all(p["horse_a"] < p["horse_b"] for p in pairs)
    assert {(p["horse_a"], p["horse_b"]) for p in qin} == {(9, 10)}


def test_self_pairs_are_dropped():
    payload = {**PAYLOAD, "qin_odds": [{"a": "5", "b": "5", "odds": "9"}]}
    assert not [p for p in odds.pair_rows(odds.parse_snapshot(payload))
                if p["pool"] == "QIN"]


# ── import ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def cache(tmp_path):
    d = tmp_path / "live_odds" / "20260715"
    d.mkdir(parents=True)
    (d / "HV_R03_090000.json").write_text(json.dumps(PAYLOAD))
    later = {**PAYLOAD, "scraped_at": "2026-07-15T16:20:00",
             "odds": [{"no": "10", "win": "3.0", "place": "1.4"},
                      {"no": "9", "win": "15.0", "place": "3.8"}]}
    (d / "HV_R03_162000.json").write_text(json.dumps(later))
    return tmp_path / "live_odds"


def test_import_stores_every_snapshot(cache, tmp_path):
    db = tmp_path / "o.db"
    report = import_legacy_odds.run(cache, db=db)
    assert report.files_read == 2 and report.meetings == 1
    assert report.snapshots == 5 and report.pair_rows > 0
    assert not report.errors


def test_snapshots_accumulate_rather_than_overwrite(cache, tmp_path):
    """Movement is the point. A later capture is a new row, never a replacement
    -- 44% of races see the favourite change between morning and post time."""
    db = tmp_path / "o.db"
    import_legacy_odds.run(cache, db=db)
    conn = get_conn(db)
    prices = [r["win_odds"] for r in conn.execute(
        "SELECT win_odds FROM odds_snapshots WHERE horse_no = 9 ORDER BY captured_at")]
    conn.close()
    assert prices == [6.7, 15.0]        # the drift is preserved


def test_import_is_idempotent(cache, tmp_path):
    db = tmp_path / "o.db"
    first = import_legacy_odds.run(cache, db=db)
    import_legacy_odds.run(cache, db=db)
    conn = get_conn(db)
    total = conn.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0]
    conn.close()
    assert total == first.snapshots


def test_a_corrupt_file_is_reported_and_the_rest_still_import(cache, tmp_path):
    (cache / "20260715" / "broken.json").write_text("{not json")
    db = tmp_path / "o.db"
    report = import_legacy_odds.run(cache, db=db)
    assert report.errors
    assert report.snapshots > 0        # the good files still landed


def test_nothing_in_this_module_deletes_a_snapshot():
    """prune_old_snapshots(keep=20) left 17 meetings alive out of a full
    season. That call must never be reintroduced anywhere."""
    from pathlib import Path
    for f in (Path("hkrd/ingest/odds.py"), Path("hkrd/jobs/import_legacy_odds.py")):
        src = f.read_text(encoding="utf-8")
        assert "prune" not in src.replace("prune_old_snapshots(keep=20)", "")
        assert "DELETE FROM odds" not in src
