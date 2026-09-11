"""query/raceday — the card, assembled in one call."""
from __future__ import annotations

import pytest

from hkrd.query import race as race_q
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
        # Place odds are NOT a fixed fraction of the win price. The ratio
        # runs from about 0.16 to 0.62 across a real card, which is the whole
        # reason place has to be captured rather than derived, so a fixture
        # that used o/3 would make the test that checks this tautological.
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": "2026-07-15", "race_no": 1, "horse_no": i + 1,
             "captured_at": ts, "win_odds": o, "place_odds": pl}
            for ts, prices in (
                ("2026-07-15T06:00:00", [(6.0, 2.4), (5.0, 2.0), (20.0, 4.0)]),
                ("2026-07-15T12:30:00", [(3.0, 1.5), (6.0, 2.2), (20.0, 3.4)]))
            for i, (o, pl) in enumerate(prices)])
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
    structurally invalid, and showing the real spread keeps that obvious.

    The prices come from the latest CAPTURE, not from `runners`, which holds
    the starting price and is empty until the race has been run. This used to
    assert None: the fixture had place odds all along, in the snapshot, and
    nothing on the card was reading them.
    """
    conn = get_conn(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()
    # 1.5/3.0 = 0.50 at the short end, 3.4/20.0 = 0.17 at the long one.
    assert card["place_ratio_range"] == "0.17–0.50"


def test_a_card_with_no_capture_invents_no_ratio(db):
    """The 2026-06-01 race has no snapshot. Nothing is better than a third."""
    conn = get_conn(db)
    card = raceday.build_card("2026-06-01", 1, conn=conn)
    conn.close()
    assert card["place_ratio_range"] is None
    # And the card still builds: a race with no market is not an error.
    assert len(card["runners"]) == 3


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


def test_the_card_is_priced_from_the_latest_capture(db):
    """`runners.win_odds` is the STARTING price, written by the results scrape
    after the race. On a card that has not been run it is NULL for every
    runner, which is how this page came to show an empty price column at
    exactly the moment it exists for.

    The fixture's two captures disagree — horse 1 was 6.0 in the morning and
    3.0 at 12:30, horse 2 went the other way — so reading the wrong one is
    visible rather than a coincidence.
    """
    conn = get_conn(db)
    conn.execute("UPDATE runners SET win_odds = NULL "
                 "WHERE race_date = '2026-07-15'")
    conn.commit()
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()

    by_no = {r["horse_no"]: r for r in card["runners"]}
    assert by_no[1]["win_odds"] == pytest.approx(3.0)     # 12:30, not 06:00
    assert by_no[2]["win_odds"] == pytest.approx(6.0)
    assert by_no[1]["place_odds"] == pytest.approx(1.5)
    # And every figure derived from a price is populated with it.
    assert by_no[1]["market_rank"] == 1
    assert by_no[1]["win_pct"] is not None
    assert card["overround"] is not None


def test_a_scratching_on_an_unrun_card_stays_unpriced(db):
    """A runner the latest capture does not price must not acquire one from
    somewhere else, or a horse that has come out reads as live money."""
    conn = get_conn(db)
    conn.execute("UPDATE runners SET win_odds = NULL "
                 "WHERE race_date = '2026-07-15'")
    conn.execute("UPDATE odds_snapshots SET win_odds = NULL, place_odds = NULL "
                 "WHERE horse_no = 2 AND captured_at = '2026-07-15T12:30:00'")
    conn.commit()
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()

    by_no = {r["horse_no"]: r for r in card["runners"]}
    assert by_no[2]["win_odds"] is None
    assert by_no[2]["market_rank"] is None
    assert by_no[1]["win_odds"] == pytest.approx(3.0)      # the rest stand


def test_a_run_that_is_over_keeps_its_starting_price(db):
    """Only Race Day is repriced. The Form Guide, Results and every backtest
    are asking what a run actually paid, which is a different question."""
    from hkrd.query.race import get_race

    conn = get_conn(db)
    race = get_race("2026-07-15", 1, conn=conn)
    conn.close()
    assert [r.win_odds for r in race.runners] == [3.0, 6.0, 20.0]


def test_the_blackbook_band_carries_a_price_before_the_race(db):
    """`blackbook.declared_on` reads runners.win_odds — the starting price, NULL
    until the results scrape writes it. So the band showed a booked horse
    drifting 6% with no price beside it to drift FROM: a movement without a
    market, which is the one thing on that band you cannot act on."""
    conn = get_conn(db)
    conn.execute("UPDATE runners SET win_odds = NULL "
                 "WHERE race_date = '2026-07-15'")
    conn.execute(
        "INSERT INTO blackbook (id, horse_name, status, confidence, "
        "added_date, reasoning) VALUES ('bb1', 'HORSE 0', 'watch', 'high', "
        "'2026-06-01', 'travelled well')")
    conn.commit()

    band = raceday.meeting_blackbook("2026-07-15", conn=conn)
    conn.close()

    entry = next(e for e in band["entries"] if e["horse_name"] == "HORSE 0")
    assert entry["win_odds"] == pytest.approx(3.0)      # the 12:30 capture
    assert entry["place_odds"] == pytest.approx(1.5)


# ── the STYLE column: how the horse RUNS, not where it sat last start ────────

def _with_style_history(path):
    """Six prior runs for HORSE 0: five Closers and a Leader last start.

    Enough to make the two readings of "style" disagree, which is the whole
    point — read off the last run the card called this horse a Leader while the
    Speed Map beside it, which has always used the habit, drew it at the back.
    """
    conn = get_conn(path)
    styles = ["Closer", "Closer", "Closer", "Closer", "Closer", "Leader"]
    with transaction(conn):
        for i, style in enumerate(styles):
            date = f"2026-0{i + 1}-05"
            upsert.upsert_races(conn, [
                {"race_date": date, "race_no": 1, "venue": "HV", "course": "C",
                 "surface": "Turf", "going": "G", "distance": 1650,
                 "race_class": "4"}])
            upsert.upsert_runners(conn, [
                {"race_date": date, "race_no": 1, "horse_no": 1,
                 "horse_name": "HORSE 0", "place": "3", "finish_time": 100.0,
                 "lengths_behind": "1-1/4", "draw": 1, "actual_weight": 120,
                 "win_odds": 5.0, "jockey": "J0", "trainer": "T0",
                 "running_positions": "1 1 1"}])
            conn.execute(
                "INSERT INTO runner_pace (race_date, race_no, horse_no, "
                "pace_style, derive_version) VALUES (?, 1, 1, ?, 't')",
                (date, style))
    return conn


def test_the_style_column_is_the_habit_not_the_last_run(db):
    """A Closer ridden forward once is still a Closer.

    The column read `last_run.pace_style` — one observation, and the single
    worst estimator of the next one. `runner_projection.style`, which the Speed
    Map draws, has always been the habit, so the two pages gave two answers
    about the same horse in the same race.
    """
    conn = _with_style_history(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()

    row = next(r for r in card["runners"] if r["horse_name"] == "HORSE 0")
    assert row["last_run"]["pace_style"] == "Leader"       # what it did
    assert row["running_style"]["style"] == "Closer"       # what it is


def test_the_style_carries_the_tally_it_was_read_off(db):
    """Never a bare badge: the count, the whole tally and the last classified
    run travel with it, so a habit can be checked and a horse whose last start
    disagreed with it can be marked rather than quietly averaged away."""
    conn = _with_style_history(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()

    got = next(r for r in card["runners"]
               if r["horse_name"] == "HORSE 0")["running_style"]
    assert got["n"] == 6
    assert got["counts"] == {"Closer": 5, "Leader": 1}
    assert got["last"] == "Leader"


def test_a_horse_with_no_classified_run_has_no_style(db):
    """None, never an invented Midfield. A badge drawn from nothing renders in
    the same ink as a measured one."""
    conn = _with_style_history(db)
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()

    got = next(r for r in card["runners"]
               if r["horse_name"] == "HORSE 1")["running_style"]
    assert got["style"] is None
    assert got["n"] == 0


def test_the_style_never_reads_todays_own_run(db):
    """Today's run is the result. A card scoring a horse on the race it is
    previewing is showing the answer."""
    conn = _with_style_history(db)
    conn.execute(
        "INSERT INTO runner_pace (race_date, race_no, horse_no, pace_style, "
        "derive_version) VALUES ('2026-07-15', 1, 1, 'Midfield', 't')")
    conn.commit()
    card = raceday.build_card("2026-07-15", 1, conn=conn)
    conn.close()

    got = next(r for r in card["runners"]
               if r["horse_name"] == "HORSE 0")["running_style"]
    assert got["style"] == "Closer"
    assert "Midfield" not in got["counts"]


# ── where a searched horse should land ───────────────────────────────────────

def _seed_where(conn):
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-01-04", "race_no": 1, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200, "race_class": "4"},
            {"race_date": "2026-03-01", "race_no": 5, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200, "race_class": "4"}])
        upsert.upsert_runners(conn, [
            {"race_date": "2026-01-04", "race_no": 1, "horse_no": 1,
             "horse_name": "OLD RUNNER", "place": "1", "finish_time": "1:09.5"},
            {"race_date": "2026-01-04", "race_no": 1, "horse_no": 2,
             "horse_name": "TRIALLED SINCE", "place": "2", "finish_time": "1:09.7"},
            {"race_date": "2026-03-01", "race_no": 5, "horse_no": 3,
             "horse_name": "ON THE CARD", "place": None}])
        upsert.upsert_trials(conn, [
            {"trial_date": "2026-02-10", "trial_no": 3, "horse_name": "TRIALLED SINCE",
             "place": "1", "finish_time": "1:00.0", "venue": "ST", "draw": "1"},
            {"trial_date": "2026-02-11", "trial_no": 4, "horse_name": "ON THE CARD",
             "place": "1", "finish_time": "1:00.0", "venue": "ST", "draw": "2"},
            {"trial_date": "2025-12-01", "trial_no": 1, "horse_name": "NEVER RACED",
             "place": "1", "finish_time": "1:00.0", "venue": "ST", "draw": "3"}])


def test_a_horse_on_the_newest_card_goes_to_its_race(tmp_path):
    """And to the RACE it is in, not race 1. The palette put ?horse= on the URL
    and the Form Guide never read it, so every horse landed on race 1 of the
    newest meeting and had to be found by eye.

    This beats a more recent trial on purpose: ON THE CARD trialled on 11 Feb
    and runs on 1 March, and the race is what you opened the search for."""
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    _seed_where(conn)
    where = race_q.latest_appearance("ON THE CARD", conn=conn)
    conn.close()
    assert where["kind"] == "race"
    assert where["on_latest_card"] is True
    assert (where["race_date"], where["race_no"]) == ("2026-03-01", 5)


def test_a_horse_off_the_card_goes_to_whichever_ran_last(tmp_path):
    """TRIALLED SINCE raced on 4 Jan and trialled on 10 Feb, so the trial is
    the news. OLD RUNNER has no trial at all and goes to its last race."""
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    _seed_where(conn)
    trial = race_q.latest_appearance("TRIALLED SINCE", conn=conn)
    run = race_q.latest_appearance("OLD RUNNER", conn=conn)
    conn.close()
    assert trial["kind"] == "trial"
    assert (trial["trial_date"], trial["trial_no"]) == ("2026-02-10", 3)
    assert trial["on_latest_card"] is False
    assert run["kind"] == "race"
    assert (run["race_date"], run["race_no"]) == ("2026-01-04", 1)


def test_a_horse_that_has_only_trialled_still_has_somewhere_to_go(tmp_path):
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    _seed_where(conn)
    where = race_q.latest_appearance("NEVER RACED", conn=conn)
    conn.close()
    assert where["kind"] == "trial"
    assert where["trial_date"] == "2025-12-01"


def test_an_unknown_name_says_so_rather_than_sending_you_nowhere(tmp_path):
    """A destination that will be empty when it loads is worse than none: the
    palette can keep the reader where they are instead."""
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    _seed_where(conn)
    assert race_q.latest_appearance("NOBODY", conn=conn)["kind"] == "none"
    assert race_q.latest_appearance("", conn=conn)["kind"] == "none"
    conn.close()


# ── what the winner won by ───────────────────────────────────────────────────

def _seed_margins(conn):
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": "2026-02-01", "race_no": 1, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200, "race_class": "4"},
            {"race_date": "2026-02-08", "race_no": 1, "venue": "ST", "course": "A",
             "surface": "Turf", "going": "G", "distance": 1200, "race_class": "4"}])
        upsert.upsert_runners(conn, [
            # a clear win, runner-up beaten a length and a quarter
            {"race_date": "2026-02-01", "race_no": 1, "horse_no": 1,
             "horse_name": "WINNER", "place": "1", "finish_time": "1:09.50",
             "lengths_behind": "-"},
            {"race_date": "2026-02-01", "race_no": 1, "horse_no": 2,
             "horse_name": "SECOND", "place": "2", "finish_time": "1:09.70",
             "lengths_behind": "1-1/4"},
            {"race_date": "2026-02-01", "race_no": 1, "horse_no": 3,
             "horse_name": "THIRD", "place": "3", "finish_time": "1:10.10",
             "lengths_behind": "3"},
            # a dead heat: two winners, no second placing at all
            {"race_date": "2026-02-08", "race_no": 1, "horse_no": 1,
             "horse_name": "JOINT A", "place": "1 DH", "finish_time": "1:09.50",
             "lengths_behind": "-"},
            {"race_date": "2026-02-08", "race_no": 1, "horse_no": 2,
             "horse_name": "JOINT B", "place": "1 DH", "finish_time": "1:09.50",
             "lengths_behind": "-"},
            {"race_date": "2026-02-08", "race_no": 1, "horse_no": 3,
             "horse_name": "BEATEN", "place": "3", "finish_time": "1:10.00",
             "lengths_behind": "2-1/2"}])


def test_a_winner_carries_what_it_won_by(tmp_path):
    """A winner has no lengths-behind -- it is not behind anything -- so the
    margin column was blank on exactly the runs worth reading it on, and "won"
    said nothing about whether it was a nose or six lengths. The margin is the
    RUNNER-UP's beaten lengths, the same fact from the other side."""
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    _seed_margins(conn)
    runs = {r.horse_name: r for r in race_q.get_race("2026-02-01", 1, conn=conn).runners}
    conn.close()
    assert runs["WINNER"].lengths_behind is None
    assert runs["WINNER"].win_margin == pytest.approx(1.25)
    # and nobody else gets one: beaten lengths is the number to read there
    assert runs["SECOND"].win_margin is None
    assert runs["SECOND"].lengths_behind == pytest.approx(1.25)
    assert runs["THIRD"].win_margin is None


def test_a_dead_heat_won_by_nothing(tmp_path):
    """Two winners and no second placing, so the subquery has nothing to find.
    Zero is the answer by definition, not a missing value."""
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    _seed_margins(conn)
    runs = {r.horse_name: r for r in race_q.get_race("2026-02-08", 1, conn=conn).runners}
    conn.close()
    assert runs["JOINT A"].win_margin == 0.0
    assert runs["JOINT B"].win_margin == 0.0
    assert runs["BEATEN"].win_margin is None


def test_the_margin_survives_the_horse_form_query_too(tmp_path):
    """`_to_line` is fed by more than one SELECT. A column one of them does not
    carry must read as absent, not raise."""
    conn = get_conn(tmp_path / "t.db")
    init_db(conn)
    _seed_margins(conn)
    form = race_q.get_horse_form("WINNER", limit=5, conn=conn)
    conn.close()
    assert form[0].win_margin == pytest.approx(1.25)
