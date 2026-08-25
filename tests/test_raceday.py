"""query/raceday — the card, assembled in one call."""
from __future__ import annotations

import pytest

from hkrd.query import raceday
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "rd.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-06-01", "race_no": 1, "venue": "HV",
             "course": "C", "surface": "Turf", "going": "G", "distance": 1650},
            {"race_date": "2026-07-15", "race_no": 1, "venue": "HV",
             "course": "C", "surface": "Turf", "going": "G", "distance": 1650,
             "race_class": "4"},
        ])
        for date, odds in (("2026-06-01", [4.0, 8.0, 12.0]),
                           ("2026-07-15", [3.0, 6.0, 20.0])):
            upsert.upsert_runners(conn, [
                {"race_date": date, "race_no": 1, "horse_no": i + 1,
                 "horse_name": f"HORSE {i}", "place": str(i + 1),
                 "finish_time": 100.0 + i, "lengths_behind": "-" if i == 0 else "1-1/4",
                 "draw": i + 1, "actual_weight": 120 + i, "win_odds": o,
                 "jockey": f"J{i}", "trainer": f"T{i}",
                 "running_positions": "1 1 1"}
                for i, o in enumerate(odds)])
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": "2026-07-15", "race_no": 1, "horse_no": i + 1,
             "captured_at": ts, "win_odds": o, "place_odds": o / 3}
            for ts, prices in (("2026-07-15T06:00:00", [6.0, 5.0, 20.0]),
                               ("2026-07-15T12:30:00", [3.0, 6.0, 20.0]))
            for i, o in enumerate(prices)])
        conn.executemany(
            "INSERT INTO runner_sarr (race_date, race_no, horse_no, sarr, "
            "sarr_rank, n_prior, derive_version) VALUES (?,?,?,?,?,?,?)",
            [("2026-07-15", 1, 1, 0.5, 3, 4, "t"),
             ("2026-07-15", 1, 2, 0.2, 1, 4, "t"),
             ("2026-07-15", 1, 3, 0.9, 2, 4, "t")])
    conn.close()
    return path


def test_card_carries_everything_the_page_needs(db):
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    assert card["field_size"] == 3
    assert card["concentration"]["value"] is not None
    r = card["runners"][0]
    for key in ("win_odds", "market_rank", "movement", "sarr_rank",
                "rank_delta", "last_run"):
        assert key in r


def test_market_rank_orders_by_price(db):
    """The price is the best ranking available -- AUC 0.785 against 0.727 for
    the best model here -- so it is the reference the models are read against."""
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    by_no = {r["horse_no"]: r for r in card["runners"]}
    assert by_no[1]["market_rank"] == 1      # 3.0
    assert by_no[3]["market_rank"] == 3      # 20.0


def test_rank_delta_makes_disagreement_explicit(db):
    """Where a model likes a horse more than the market does is the interesting
    thing on the screen, so it is computed rather than left to the eye."""
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    by_no = {r["horse_no"]: r for r in card["runners"]}
    assert by_no[2]["rank_delta"] == -1      # model 1, market 2
    assert by_no[1]["rank_delta"] == 2       # model 3, market 1


def test_last_run_is_the_previous_race_not_todays(db):
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    last = card["runners"][0]["last_run"]
    assert last["race_date"] == "2026-06-01"
    assert last["days_ago"] == 44


def test_routine_tags_never_reach_the_card(db):
    """A passed veterinary examination rendering like a real finding is how a
    badge becomes noise and gets ignored."""
    conn = get_conn(db)
    conn.executemany(
        "INSERT INTO runner_tags VALUES (?,?,?,?,?)",
        [("2026-06-01", 1, 1, "sampling", 0.9),
         ("2026-06-01", 1, 1, "vet_routine", 0.9),
         ("2026-06-01", 1, 1, "hampered", 0.9)])
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    tags = card["runners"][0]["last_run"]["tags"]
    assert tags == ["hampered"]


def test_a_missing_race_returns_empty_rather_than_raising(db):
    conn = get_conn(db)
    card = raceday.build_card("1999-01-01", 1, conn=conn)
    conn.close()
    assert card["runners"] == []


def test_meeting_summary_bands_every_race(db):
    conn = get_conn(db)
    summary = raceday.meeting_summary("2026-07-15", conn=conn)
    conn.close()
    assert len(summary["races"]) == 1
    race = summary["races"][0]
    assert race["field_size"] == 3
    assert race["band"] in {"weak", "moderate", "strong"}
    assert "stale" in race


# ── artboard support: sparklines, win %, head to head ────────────────────────

def test_spark_points_maps_a_series_to_a_polyline():
    pts, dot_x, dot_y = raceday.spark_points([10.0, 8.0, 4.0])
    coords = [p.split(",") for p in pts.split(" ")]
    assert len(coords) == 3
    # A shortening price is drawn rising, so money arriving reads as upward.
    ys = [float(c[1]) for c in coords]
    assert ys[0] > ys[-1]
    assert (dot_x, dot_y) == (float(coords[-1][0]), float(coords[-1][1]))


def test_spark_points_of_a_single_capture_is_not_a_spike():
    """One price has no shape. Inventing one would read as movement."""
    pts, _, _ = raceday.spark_points([5.0])
    assert len(pts.split(" ")) == 1


def test_spark_points_handles_an_empty_series():
    assert raceday.spark_points([]) == ("", 0.0, 9.0)


def test_win_percentages_are_the_devigged_market(db):
    """The market's own estimate, shown beside its price -- not a model's."""
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    pcts = [r["win_pct"] for r in card["runners"] if r["win_pct"] is not None]
    assert len(pcts) == 3
    assert sum(pcts) == pytest.approx(100.0, abs=0.5)


def test_overround_is_the_book_percentage_over_a_hundred(db):
    """Computed from whatever is priced, so a partial field underrounds.

    The fixture prices three runners at 3.0, 6.0 and 20.0, which sums to 0.55
    of a book and therefore reports -45%. That is arithmetically right and
    worth seeing: a negative overround means the field is not fully priced,
    which is itself information. On the real 15 July card, with every runner
    priced, it reads 22.1%.
    """
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    expected = 100 * ((1 / 3.0 + 1 / 6.0 + 1 / 20.0) - 1)
    assert card["overround"] == pytest.approx(expected, abs=0.05)


def test_place_ratio_range_is_measured_not_assumed(db):
    """Place odds cannot be derived from win odds -- the "one third" rule is
    structurally invalid, and showing the real spread keeps that obvious."""
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    # This fixture has no place odds stored on runners, so it reports nothing
    # rather than inventing a ratio.
    assert card["place_ratio_range"] is None


def test_head_to_head_pairs_are_sorted_by_weight_swing(db):
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    pairs = card["head_to_head"]
    swings = [p["swing"] or 0 for p in pairs]
    assert swings == sorted(swings, reverse=True)


def test_swing_tiers_escalate_at_four_six_and_eight_pounds(db):
    """Most pairs clear none of them, which is correct rather than a bug."""
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    for p in card["head_to_head"]:
        s = p["swing"]
        if s is None:
            continue
        expected = 3 if s >= 8 else 2 if s >= 6 else 1 if s >= 4 else 0
        assert p["swing_tier"] == expected


def test_a_trainer_change_is_measured_against_one_run_back(db):
    """Today versus the immediately preceding run is what signals a stable
    move -- not today versus some earlier trainer."""
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    for r in card["runners"]:
        assert r["trainer_changed"] is False    # same trainer in the fixture
        assert r["trainer_prev"] is None
