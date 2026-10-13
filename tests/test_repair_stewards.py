"""The repair job's stewards' reports, and comments on running looked for
by their own source.

Found 13 Oct 2026: the whole of 2024-25 had no stewards' report and no
comment on running, so a season of runs carried no trouble tags. Under test:
the survey finds a settled race with no report (and none before the first
meeting HKJC's page carries one, `STEWARDS_FROM`), the repair stores what the
page says exactly as a fresh scrape would, and a runner with a stewards'
report but no comment on running is still found as missing its comment.
"""
from __future__ import annotations

import pytest

from hkrd.jobs import repair
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "hkrd.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": d, "race_no": 1, "venue": "ST", "distance": 1200,
             "race_class": "Class 4"} for d in ("2023-07-16", "2024-12-01", "2025-09-07")])
        upsert.upsert_runners(conn, [
            {"race_date": d, "race_no": 1, "horse_no": n, "horse_name": f"H{n}",
             "place": str(n)}
            for d in ("2023-07-16", "2024-12-01", "2025-09-07") for n in (1, 2)])
        # 2025-09-07 has its stewards' report but no comment on running.
        upsert.upsert_comments(conn, [
            {"race_date": "2025-09-07", "race_no": 1, "horse_no": n,
             "comment_text": "Sent for sampling.", "source": "incident"} for n in (1, 2)])
    conn.close()
    return path


def test_the_survey_finds_the_races_with_no_report_from_the_first_one_hkjc_serves(db):
    d = repair.survey(db)
    assert d.steward_races == [("2024-12-01", 1, "ST")]   # 2023-07 is before STEWARDS_FROM
    assert repair.STEWARDS_FROM == "2023-10-04"


def test_a_stewards_report_does_not_hide_a_missing_comment_on_running(db):
    d = repair.survey(db)
    assert "2025-09-07" in d.comment_dates


def test_the_repair_stores_the_report_as_a_fresh_scrape_would(db, monkeypatch):
    from hkrd.ingest import results
    asked = []

    def fake(date, venue, race_no, **_):
        asked.append((date, venue, race_no))
        return [{"horse_no": "2", "comment": "Near the 400 Metres was held up."}]

    monkeypatch.setattr(results, "fetch_incidents", fake)
    log = repair._repair_stewards(db, repair.survey(db).steward_races)
    assert asked == [("2024-12-01", "ST", 1)]
    conn = get_conn(db)
    rows = conn.execute("SELECT race_date, horse_no, source, comment_text FROM runner_comments "
                        "WHERE race_date = '2024-12-01'").fetchall()
    conn.close()
    assert [tuple(r) for r in rows] == [("2024-12-01", 2, "incident",
                                         "Near the 400 Metres was held up.")]
    assert any("1 runners' reports stored" in line for line in log)
    assert repair.survey(db).steward_races == []


def test_a_race_with_nothing_published_says_so_and_is_not_an_error(db, monkeypatch):
    from hkrd.ingest import results
    monkeypatch.setattr(results, "fetch_incidents", lambda *a, **k: [])
    log = repair._repair_stewards(db, repair.survey(db).steward_races)
    assert log[-1].endswith("1 races with none published, 0 failed")
