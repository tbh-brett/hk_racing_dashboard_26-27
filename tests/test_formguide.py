"""query/formguide — two calls, not a pipeline."""
from __future__ import annotations

import datetime as dt
import time

import pytest

from hkrd.query import formguide
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fg.db"
    conn = get_conn(path)
    init_db(conn)
    races, runners = [], []
    start = dt.date(2026, 1, 4)
    for d in range(8):
        date = (start + dt.timedelta(days=d * 14)).isoformat()
        races.append({"race_date": date, "race_no": 1, "venue": "ST", "course": "A",
                      "surface": "Turf", "going": "G", "distance": 1800,
                      "race_class": "4"})
        for h in range(6):
            runners.append({
                "race_date": date, "race_no": 1, "horse_no": h + 1,
                "horse_name": f"HORSE {h}", "place": str(((h + d) % 6) + 1),
                "finish_time": 108.0 + ((h + d) % 6) * 0.3,
                "lengths_behind": "-" if (h + d) % 6 == 0 else "1-1/4",
                "draw": h + 1, "actual_weight": 120 + h, "win_odds": "5.0",
                "running_positions": "3 3 3 2",
            })
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)
    conn.close()
    return path


def _last_date(db) -> str:
    conn = get_conn(db)
    d = conn.execute("SELECT max(race_date) FROM races").fetchone()[0]
    conn.close()
    return d


def test_form_guide_returns_card_plus_history(db):
    conn = get_conn(db)
    fg = formguide.build_form_guide(_last_date(db), 1, conn=conn)
    conn.close()
    assert len(fg.race.runners) == 6
    assert set(fg.history) == {r.horse_name for r in fg.race.runners}
    assert all(len(v) > 0 for v in fg.history.values())


def test_history_is_strictly_before_todays_race(db):
    """A form guide showing today's result would be showing the answer."""
    date = _last_date(db)
    conn = get_conn(db)
    fg = formguide.build_form_guide(date, 1, conn=conn)
    conn.close()
    for runs in fg.history.values():
        assert all(r.race_date < date for r in runs)


def test_history_entries_are_the_same_type_as_the_card(db):
    """The architectural claim: a past run in the form guide and the same run in
    lookup are one object, so they cannot disagree about a figure."""
    conn = get_conn(db)
    fg = formguide.build_form_guide(_last_date(db), 1, conn=conn)
    conn.close()
    card = fg.race.runners[0]
    past = next(iter(fg.history.values()))[0]
    assert type(card) is type(past)


def test_form_guide_is_fast(db):
    """The version this replaces cost 15.33s per call, dominated by a
    read_excel of data already in the database."""
    conn = get_conn(db)
    start = time.perf_counter()
    formguide.build_form_guide(_last_date(db), 1, conn=conn)
    elapsed = time.perf_counter() - start
    conn.close()
    assert elapsed < 0.5, f"took {elapsed:.3f}s against a 500ms budget"


def test_form_guide_serialises_for_the_api(db):
    conn = get_conn(db)
    payload = formguide.build_form_guide(_last_date(db), 1, conn=conn).to_dict()
    conn.close()
    assert "race" in payload and "history" in payload
    run = payload["history"]["HORSE 0"][0]
    assert "figure_display" in run and "finish_time_display" in run


# ── race quality retrospective ───────────────────────────────────────────────

def test_race_quality_reports_what_each_finisher_did_next(db):
    conn = get_conn(db)
    first = conn.execute("SELECT min(race_date) FROM races").fetchone()[0]
    out = formguide.race_quality(first, 1, conn=conn)
    conn.close()
    assert len(out) == 5
    assert [x["place"] for x in out] == [1, 2, 3, 4, 5]
    assert any(x["next_place"] is not None for x in out)


def test_not_run_since_is_distinct_from_ran_and_was_unplaced(db):
    """Two different facts that must not render identically."""
    conn = get_conn(db)
    last = conn.execute("SELECT max(race_date) FROM races").fetchone()[0]
    out = formguide.race_quality(last, 1, conn=conn)
    conn.close()
    assert all(x["next_place"] is None and x["next_date"] is None for x in out)


# ── condition fit ────────────────────────────────────────────────────────────

def test_condition_fit_carries_the_sample_size(db):
    """Across 153 condition cells in prior analysis, 8 cleared significance
    where 7.0 were expected by chance. The count is part of the value."""
    conn = get_conn(db)
    cells = formguide.condition_fit("HORSE 0", distance=1800, course="A",
                                    going="G", conn=conn)
    conn.close()
    assert cells
    for c in cells:
        d = c.to_dict()
        assert d["starts"] >= 0
        assert "/" in d["win_display"] or d["win_display"] == "—"
        assert "is_thin" in d


def test_thin_cells_declare_themselves(db):
    conn = get_conn(db)
    cells = formguide.condition_fit("HORSE 0", distance=9999, conn=conn)
    conn.close()
    assert cells[0].starts == 0
    assert cells[0].is_thin


def test_condition_fit_respects_the_before_cutoff(db):
    date = _last_date(db)
    conn = get_conn(db)
    everything = formguide.condition_fit("HORSE 0", distance=1800, conn=conn)
    prior = formguide.condition_fit("HORSE 0", distance=1800, before=date, conn=conn)
    conn.close()
    assert prior[0].starts < everything[0].starts


# ── head to head ─────────────────────────────────────────────────────────────

def test_head_to_head_counts_who_finished_ahead(db):
    conn = get_conn(db)
    h2h = formguide.head_to_head("HORSE 0", "HORSE 1", conn=conn)
    conn.close()
    assert h2h["meetings"]
    assert h2h["record"]["a"] + h2h["record"]["b"] == len(h2h["meetings"])


def test_weight_swing_is_the_gap_between_them_not_each_weight():
    """Both horses going up 5lb have not changed relative to one another."""
    assert formguide.weight_swing(-5, -4) == 1        # the brief's worked example
    assert formguide.weight_swing(-5, -5) == 0        # both up 5lb: no swing
    assert formguide.weight_swing(2, -6) == 8         # clears the top badge tier
    assert formguide.weight_swing(None, -4) is None
