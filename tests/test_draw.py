"""derive/draw — the barrier term, and the artefacts it exists to avoid.

The draw was wired into SARR end to end and fed 0.0 on every scored runner: the
parameter, the multiplier, the column and the page all existed and nothing
supplied a value. These tests pin the two things that had to be true before it
could be switched on -- that the score is normalised by field size, and that the
table is fitted only on data older than the race it touches -- plus the one that
had to be true after: that it actually reaches the database.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from hkrd.derive import draw as draw_d
from hkrd.jobs import rebuild_sarr
from hkrd.model import sarr
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction


def _runs(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["race_date", "race_no", "place", "draw",
                                       "venue", "distance", "field_size"])


def _linear(n_races=40, field=12, venue="ST", distance=1200, reverse=False):
    """Fields where finishing order IS draw order (or its reverse)."""
    rows = []
    for i in range(n_races):
        for g in range(1, field + 1):
            place = (field + 1 - g) if reverse else g
            rows.append((f"2026-01-{i % 28 + 1:02d}", i, place, g,
                         venue, distance, field))
    return _runs(rows)


# ── normalisation: the artefact the legacy term was made of ──────────────────

def test_a_gate_is_scored_by_its_share_of_the_field_not_its_number():
    """Gate 8 of 8 is the widest gate there is. Gate 8 of 14 is mid-field. The
    legacy term scored both against a fixed mid-place of 6.5, which is the
    median of a 14-runner field only, so every wide gate collected field-size
    artefact instead of effect."""
    assert draw_d.normalised_draw(8, 8) == 1.0
    assert draw_d.normalised_draw(1, 8) == 0.0
    assert draw_d.normalised_draw(8, 14) == pytest.approx(0.5384, abs=1e-4)


def test_a_draw_wider_than_the_field_is_the_widest_gate_not_an_outlier():
    """After scratchings a horse can be drawn 14 in a race that 12 contest.
    103 of 21,100 archived runs are. It is the widest of them, which is 1.0."""
    assert draw_d.normalised_draw(14, 12) == 1.0


def test_an_unknown_gate_normalises_to_nothing_rather_than_a_guess():
    assert draw_d.normalised_draw(None, 12) is None
    assert draw_d.normalised_draw(5, None) is None
    assert draw_d.normalised_draw(5, 1) is None       # no inside and outside


# ── the fit ──────────────────────────────────────────────────────────────────

def test_an_inside_advantage_fits_a_positive_slope():
    """npos rises with ndraw when the inside wins, and SARR is lower-is-better,
    so a positive slope means a wide gate is a penalty."""
    t = draw_d.draw_table(_linear())
    assert t.slope_for("ST", 1200) == pytest.approx(1.0, abs=0.05)


def test_a_reversed_cell_keeps_its_own_sign_against_the_global():
    """At ST 1000 the real effect reverses -- fitted slope -0.093 raw against a
    +0.114 global -- because the chute makes the run to the first turn short and
    inside runners get crowded. A table that averaged that away, as a venue-only
    table must, would penalise exactly the gate it should reward."""
    hist = pd.concat([_linear(n_races=200, distance=1200),
                      _linear(n_races=200, distance=1000, reverse=True)])
    t = draw_d.draw_table(hist)
    assert t.slope_for("ST", 1200) > 0.5
    assert t.slope_for("ST", 1000) < -0.5
    assert t.global_slope == pytest.approx(0.0, abs=0.2)


def test_a_thin_cell_is_shrunk_toward_the_global_slope():
    """A cell of 36 runs keeps only 36/(36+200) of its own slope. The thin cells
    are where a per-gate table would have invented an effect."""
    hist = pd.concat([_linear(n_races=200, distance=1200),
                      _linear(n_races=3, distance=2400, reverse=True)])
    t = draw_d.draw_table(hist)
    thin, fat = t.slope_for("ST", 2400), t.slope_for("ST", 1200)
    assert t.counts[("ST", 2400)] == 36
    assert t.raw_slopes[("ST", 2400)] < -0.5      # its own evidence says down
    assert thin > t.raw_slopes[("ST", 2400)]      # but it is pulled up
    assert abs(thin - t.global_slope) < abs(thin - fat) or thin < fat


def test_a_cell_too_thin_to_regress_takes_the_global_slope_outright():
    hist = pd.concat([_linear(n_races=200, distance=1200),
                      _linear(n_races=1, distance=2400, reverse=True)])
    t = draw_d.draw_table(hist)
    assert t.counts[("ST", 2400)] == 12           # below MIN_CELL_N
    assert t.slope_for("ST", 2400) == t.global_slope
    assert ("ST", 2400) not in t.raw_slopes


def test_a_cell_never_seen_falls_back_to_the_global_not_to_zero():
    """An unseen distance is far more likely to behave like Hong Kong racing in
    general than like a track with no draw effect at all."""
    t = draw_d.draw_table(_linear())
    assert t.slope_for("HV", 2400) == t.global_slope
    assert t.slope_for(None, None) == t.global_slope


def test_runs_without_a_gate_are_dropped_rather_than_read_as_gate_zero():
    """15 Jul 2026 carries results for all 107 runners and no gates at all. A
    fit that treated those as a draw of 0 would be poisoned by one bad scrape."""
    good = _linear()
    blank = good.copy()
    blank["draw"] = None
    t = draw_d.draw_table(pd.concat([good, blank]))
    assert t.n_runs == len(good)
    assert t.slope_for("ST", 1200) == pytest.approx(1.0, abs=0.05)


def test_an_empty_history_raises_rather_than_returning_a_neutral_table():
    """A silent empty table is a draw term that does nothing, which is the exact
    failure this module was written to end."""
    with pytest.raises(draw_d.DrawError):
        draw_d.draw_table(pd.DataFrame())


# ── the score ────────────────────────────────────────────────────────────────

def test_the_score_is_centred_so_it_reorders_a_field_without_shifting_it():
    """If the draw term could move a whole race's ratings up or down, it would
    corrupt every comparison SARR makes ACROSS races -- and SARR is a relative
    rating, so that is all of them."""
    t = draw_d.draw_table(_linear())
    field = 12
    total = sum(draw_d.draw_score(g, field, "ST", 1200, t)
                for g in range(1, field + 1))
    assert total == pytest.approx(0.0, abs=1e-12)


def test_the_widest_gate_is_penalised_and_the_innermost_rewarded():
    t = draw_d.draw_table(_linear())
    inner = draw_d.draw_score(1, 12, "ST", 1200, t)
    outer = draw_d.draw_score(12, 12, "ST", 1200, t)
    assert inner < 0 < outer          # lower is better, so outer is the penalty


def test_a_missing_gate_scores_zero_so_the_horse_keeps_its_other_eight_terms():
    """Deliberate degradation, not a claim. A NaN here would drop the horse out
    of the race entirely; the rebuild counts these instead so a meeting scored
    without gates is visible rather than merely quiet."""
    t = draw_d.draw_table(_linear())
    assert draw_d.draw_score(None, 12, "ST", 1200, t) == 0.0


# ── through the model, and into the database ─────────────────────────────────

def test_the_multiplier_is_the_one_that_was_fitted_not_the_legacy_guess():
    """0.3 was hand-chosen and carried a comment reading "conservative draw
    weight". 1.5 came from an out-of-sample sweep that turns over there."""
    assert sarr.DRAW_MULTIPLIER == 1.5
    assert sarr.COMPONENT_WEIGHTS["draw"] == sarr.DRAW_MULTIPLIER
    assert "f_draw" not in sarr.WEIGHTS      # not one of the eight OLS weights


def test_the_score_reaches_the_composite_through_the_multiplier():
    profile = {"fmrp": 0.0, "lsa": 0.0, "esz": 0.0, "avg_ssi": 0.0,
               "style": "Midfield", "rating": 60.0, "traj": 0.0,
               "place_rate": 0.0, "place_rate_band": None, "n_runs": 5}
    parts = sarr.contributions(profile, 1200, "ST", 60.0, draw_score=0.2)
    assert parts["draw"] == pytest.approx(0.2 * 1.5)
    assert sarr.score(profile, 1200, "ST", 60.0, draw_score=0.2) == \
        pytest.approx(sum(parts.values()))


@pytest.fixture()
def drawn_db(tmp_path):
    """A meeting series where the inside gate genuinely wins, so a draw term
    that reaches the database has something to say."""
    path = tmp_path / "d.db"
    conn = get_conn(path)
    init_db(conn)
    races, runners = [], []
    start = dt.date(2026, 1, 4)
    for d in range(16):
        date = (start + dt.timedelta(days=d * 7)).isoformat()
        races.append({"race_date": date, "race_no": 1, "venue": "ST",
                      "course": "A", "surface": "Turf", "going": "G",
                      "distance": 1200, "race_class": "4"})
        for h in range(10):
            gate = ((h + d) % 10) + 1          # gate rotates, place follows it
            runners.append({
                "race_date": date, "race_no": 1, "horse_no": h + 1,
                "horse_name": f"HORSE {h}", "place": str(gate),
                "finish_time": 100.0 + gate * 0.2,
                "lengths_behind": "-" if gate == 1 else f"{gate}-1/4",
                "draw": gate, "actual_weight": 120 + h, "rating": 60 - h,
                "win_odds": str(3.0 + h * 2), "running_positions": "1 2 3 4",
                "section_times": "24.9; 22.8; 23.6; 24.1",
            })
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)
    conn.close()
    rebuild_sarr.rebuild(path, min_prior=2)
    return path


def test_the_rebuild_writes_a_draw_contribution_that_is_not_zero(drawn_db):
    """The regression this whole change exists to fix: every part of the
    plumbing was correct and the stored value was 0.0 on all 17,376 rows."""
    conn = get_conn(drawn_db)
    n, mean_abs = conn.execute(
        "SELECT count(*), avg(abs(contribution)) FROM runner_sarr_component "
        "WHERE component = 'draw'").fetchone()
    conn.close()
    assert n > 0
    assert mean_abs > 0.0


def test_components_still_sum_to_the_stored_score(drawn_db):
    """The page's one invariant, re-asserted with a ninth live term in the sum."""
    conn = get_conn(drawn_db)
    bad = conn.execute("""
        SELECT count(*) FROM (
          SELECT s.sarr, sum(c.contribution) total
          FROM runner_sarr s
          JOIN runner_sarr_component c USING (race_date, race_no, horse_no)
          GROUP BY s.race_date, s.race_no, s.horse_no
          HAVING abs(total - s.sarr) > 1e-9)""").fetchone()[0]
    conn.close()
    assert bad == 0


def test_the_rebuild_counts_runners_it_scored_without_a_gate(tmp_path):
    """Without a count, a meeting whose racecard scrape failed scores with the
    draw term silently doing nothing -- which looks exactly like a meeting where
    the draw did not matter. 15 Jul 2026 is that meeting."""
    path = tmp_path / "g.db"
    conn = get_conn(path)
    init_db(conn)
    races, runners = [], []
    start = dt.date(2026, 1, 4)
    for d in range(6):
        date = (start + dt.timedelta(days=d * 7)).isoformat()
        races.append({"race_date": date, "race_no": 1, "venue": "ST",
                      "course": "A", "surface": "Turf", "going": "G",
                      "distance": 1200, "race_class": "4"})
        for h in range(8):
            runners.append({
                "race_date": date, "race_no": 1, "horse_no": h + 1,
                "horse_name": f"HORSE {h}", "place": str(((h + d) % 8) + 1),
                "finish_time": 100.0 + h * 0.2,
                "lengths_behind": "-" if h == 0 else f"{h}-1/4",
                # the last meeting is the one whose racecard never arrived
                "draw": None if d == 5 else h + 1,
                "actual_weight": 120 + h, "rating": 60 - h,
                "win_odds": str(3.0 + h * 2), "running_positions": "1 2 3 4",
                "section_times": "24.9; 22.8; 23.6; 24.1",
            })
    with transaction(conn):
        upsert.upsert_races(conn, races)
        upsert.upsert_runners(conn, runners)
    conn.close()
    report = rebuild_sarr.rebuild(path, min_prior=2)
    assert report.scored_without_draw == 8
    assert "scored, but no gate" in report.render()


# ── walk-forward, which is the whole guardrail ───────────────────────────────

def test_the_table_is_fitted_only_on_races_older_than_the_one_it_scores(drawn_db,
                                                                        monkeypatch):
    """A same-period fit is, in backtest.py's words, the single easiest way to
    manufacture a finding in this package. Every history frame the rebuild fits
    on must end strictly before the meeting it is about to score."""
    seen: list[str | None] = []
    real = draw_d.draw_table

    def spy(hist):
        seen.append(hist["race_date"].max() if len(hist) else None)
        return real(hist)

    monkeypatch.setattr(rebuild_sarr.draw_d, "draw_table", spy)
    rebuild_sarr.rebuild(drawn_db, min_prior=2)

    conn = get_conn(drawn_db)
    meetings = [r[0] for r in conn.execute(
        "SELECT DISTINCT race_date FROM runners ORDER BY race_date")]
    conn.close()

    # One fit per meeting, in meeting order, so the two lists pair up.
    assert len(seen) == len(meetings)
    assert seen[0] is None           # nothing precedes the first meeting
    for latest_in_fit, meeting in zip(seen[1:], meetings[1:]):
        assert latest_in_fit is not None
        assert latest_in_fit < meeting
