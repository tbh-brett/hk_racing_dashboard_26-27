"""A night that lands results derives what those results can move, and no
more (`jobs/derive_all.run_landed`). SARR is walk-forward: a result moves only
the races after it, so the whole-archive rescore it replaced -- 87s of ~100s
on the full archive, several times a race day -- rewrote numbers that could
not have changed."""
from __future__ import annotations

from hkrd.jobs import derive_all
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


def test_a_landed_night_derives_only_what_it_can_move(tmp_path, monkeypatch) -> None:
    db = tmp_path / "d.db"
    conn = get_conn(db)
    init_db(conn)
    with transaction(conn):
        for d in ("2026-09-13", "2026-09-16", "2026-09-23"):
            upsert.upsert_races(conn, [{"race_date": d, "race_no": 1, "venue": "HV",
                                        "distance": 1200}])
            upsert.upsert_runners(conn, [{"race_date": d, "race_no": 1, "horse_no": 1,
                                          "horse_name": "A", "place": "1"}])
    conn.close()

    calls = []
    monkeypatch.setattr(derive_all, "run", lambda db=None, *, date=None, only=():
                        calls.append((date, tuple(only))) or derive_all.DeriveReport())
    derive_all.run_landed(db, dates=["2026-09-16"])

    assert ("2026-09-16", ("pace", "tempo")) in calls
    assert ("2026-09-13", ("pace", "tempo")) not in calls
    # SARR from the landed date on -- the 16th and the 23rd, never the 13th.
    assert [d for d, only in calls if only == ("sarr",)] == ["2026-09-16", "2026-09-23"]
    assert calls[-1] == (None, ("et", "tags"))
