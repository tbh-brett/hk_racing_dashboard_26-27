"""Chinese horse names — the zh-hk card, and pairing it with the English one.

Both cards are real: 2026-09-23 Happy Valley race 1, fetched on 2026-09-22 a
minute apart, in `racecard_zh.html` and `racecard_en_20260923_r1.html`.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from hkrd.ingest import racecard, racecard_zh
from hkrd.ingest._client import NotFound
from hkrd.ingest.racecard import RacecardError
from hkrd.jobs import sync_horse_names as sync
from hkrd.store import tips, upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIXTURES = Path(__file__).parent / "fixtures"
DATE = "2026-09-23"


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def zh_html() -> str:
    return text("racecard_zh.html")


@pytest.fixture(scope="module")
def en_rows() -> list[dict]:
    return racecard.parse_racecard(text("racecard_en_20260923_r1.html"), 1)


@pytest.fixture(scope="module")
def zh_rows(zh_html) -> list[dict]:
    return racecard_zh.parse_racecard_zh(zh_html, 1)


# ── the Chinese card ─────────────────────────────────────────────────────────

def test_the_chinese_card_gives_name_and_brand_per_runner(zh_rows):
    assert len(zh_rows) == 12
    by_no = {r["horse_no"]: r for r in zh_rows}
    assert by_no[3] == {"race_no": 1, "horse_no": 3, "name_zh": "神駒馬靈",
                        "brand_no": "J162"}


def test_the_no_information_panel_is_not_found(monkeypatch):
    """Past the end of a card, and before a card is published, HKJC answers
    200 with this panel — the site's 404, as on the English side."""
    monkeypatch.setattr(racecard_zh, "fetch_html",
                        lambda *a, **k: text("racecard_zh_no_information.html"))
    with pytest.raises(NotFound, match="沒有相關資料"):
        racecard_zh.fetch_race_zh(DATE, "HV", 12)


def test_a_renamed_column_raises_rather_than_guessing(zh_html):
    with pytest.raises(RacecardError, match="the layout has changed"):
        racecard_zh.parse_racecard_zh(zh_html.replace(">馬名<", ">名稱<"), 1)


def test_the_name_column_is_matched_exactly_not_by_substring(zh_html):
    """馬名, 馬齡, 馬主 and 馬匹編號 share a character. A substring match on
    one of them is how the name binds to the owner."""
    with pytest.raises(RacecardError):
        racecard_zh.parse_racecard_zh(zh_html.replace(">馬名<", ">馬<"), 1)


def test_a_shifted_column_is_caught_by_shape(zh_html):
    swapped = (zh_html.replace(">馬名<", ">@@<").replace(">烙號<", ">馬名<")
               .replace(">@@<", ">烙號<"))
    with pytest.raises(RacecardError, match="no Chinese name"):
        racecard_zh.parse_racecard_zh(swapped, 1)


# ── pairing the two ──────────────────────────────────────────────────────────

def test_the_two_cards_pair_on_brand_number(en_rows, zh_rows):
    pairs, skipped = sync.pair_names(en_rows, zh_rows, label="R1")
    assert skipped == [] and len(pairs) == 12
    assert {"horse_name": "SOARING BRONCO", "name_zh": "神駒馬靈",
            "brand_no": "J162"} in pairs


def test_cards_that_disagree_about_a_horse_write_nothing_for_it(en_rows,
                                                                zh_rows):
    stale = [dict(r, horse_no=99) if r["brand_no"] == "J162" else r
             for r in zh_rows]
    pairs, skipped = sync.pair_names(en_rows, stale, label="R1")
    assert len(pairs) == 11
    assert "SOARING BRONCO" not in {p["horse_name"] for p in pairs}
    assert any("the cards disagree" in s for s in skipped)


def test_a_horse_on_one_card_only_is_reported(en_rows, zh_rows):
    pairs, skipped = sync.pair_names(en_rows[:-1], zh_rows, label="R1")
    assert len(pairs) == 11
    assert any("on the Chinese card only" in s for s in skipped)


# ── the job ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path, en_rows, zh_rows, monkeypatch) -> Path:
    """Race 1 stored, and both cards served from the fixtures."""
    path = tmp_path / "names.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [{"race_date": DATE, "race_no": 1,
                                    "venue": "HV", "distance": 1650}])
        upsert.upsert_runners(conn, [
            {"race_date": DATE, "race_no": 1, "horse_no": r["horse_no"],
             "horse_name": r["horse_name"]} for r in en_rows])
    conn.close()
    monkeypatch.setattr(sync.racecard, "fetch_race",
                        lambda *a, **k: {"runners": en_rows})
    monkeypatch.setattr(sync.racecard_zh, "fetch_race_zh",
                        lambda *a, **k: zh_rows)
    return path


def names(db: Path) -> list[tuple]:
    conn = get_conn(db)
    try:
        return sorted(tuple(r) for r in conn.execute(
            "SELECT * FROM horse_name_zh"))
    finally:
        conn.close()


def test_sync_stores_every_pair_and_a_second_run_changes_nothing(db):
    first = sync.sync(DATE, db=db)
    assert first.ok and first.venue == "HV"
    assert (first.races, first.pairs) == (1, 12)
    stored = names(db)
    assert ("SOARING BRONCO", "神駒馬靈", "J162", "racecard_zh", DATE) in stored
    sync.sync(DATE, db=db)
    assert names(db) == stored


def test_sync_records_its_run_with_counts(db):
    sync.sync(DATE, db=db)
    conn = get_conn(db)
    try:
        row = conn.execute("SELECT ok, detail FROM job_runs "
                           "WHERE job = 'sync_horse_names'").fetchone()
    finally:
        conn.close()
    assert row["ok"] == 1 and "12 names" in row["detail"]


def test_pending_asks_only_about_cards_with_an_unnamed_horse(db):
    today = dt.date(2026, 9, 22)
    assert sync.pending(db=db, today=today) == [DATE]
    sync.sync(DATE, db=db)
    assert sync.pending(db=db, today=today) == []
    # A meeting already run is never asked about: its card is gone.
    assert sync.pending(db=db, today=dt.date(2026, 9, 24)) == []


def test_sync_without_a_stored_card_says_so(db):
    report = sync.sync("2026-09-27", db=db)
    assert not report.ok and "no card stored" in report.errors[0]


def test_a_name_last_seen_later_is_not_made_older(db):
    conn = get_conn(db)
    try:
        with transaction(conn):
            for seen in (DATE, "2026-09-01"):
                tips.upsert_horse_names(conn, [{
                    "horse_name": "SOARING BRONCO", "name_zh": "神駒馬靈",
                    "brand_no": "J162", "source": "racecard_zh",
                    "seen_at": seen}])
        assert conn.execute("SELECT seen_at FROM horse_name_zh").fetchone()[0] \
            == DATE
    finally:
        conn.close()
