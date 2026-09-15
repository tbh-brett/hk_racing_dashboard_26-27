"""query/rating — why a runner has no SARR rank, and how much history it has.

A blank rank is two different things: too little history, which is a RULE and
fires on 65.2% of cards, or a card nobody scored, which is a FAULT. Rendered as
the same dash, the fault passes for a debutant and nobody looks -- which is the
state every upcoming card was in until `3b67054`.

The rule lived in `query/raceday` because Race Day answered this first. Model
Analysis needs the same answer for its blend footer, and a page reaching into
another page's module for it is how two pages start disagreeing about the same
horse on the same day. These tests moved with the code.
"""
from __future__ import annotations

from hkrd.query import rating
from hkrd.store.connect import get_conn, init_db, transaction


# ── why a runner has no SARR rank ────────────────────────────────────────────

def test_a_rated_runner_has_no_reason():
    assert rating.unrated_reason(3, prior=22) is None


def test_a_horse_short_of_history_is_named_as_a_rule():
    """SOLID STATE on 2026-09-13: one run, and the model needs two."""
    from hkrd.model import sarr
    one = rating.unrated_reason(None, prior=1)
    debut = rating.unrated_reason(None, prior=0)
    assert one["kind"] == debut["kind"] == "history"
    assert one["label"] == "1 RUN" and debut["label"] == "DEBUT"
    assert one["needs"] == sarr.MIN_PRIOR


def test_enough_history_and_no_rank_is_a_fault_not_a_debutant():
    """A horse with twenty runs and no rank is a card nobody scored. The
    nightly job left every upcoming card in that state, and as a dash it was
    indistinguishable from a horse having its first start."""
    why = rating.unrated_reason(None, prior=20)
    assert why["kind"] == "unscored"
    assert why["label"] == "NOT SCORED"


def test_the_threshold_on_the_page_is_the_one_the_rebuild_uses():
    """Three places decide "enough history" -- the rebuild, the speed map and
    this page. A page explaining a blank with a different number from the one
    that caused it explains nothing.

    The speed map is named in that sentence and was not checked by it, which is
    how `jobs/project_card` and `query/speedmap` each kept a literal 2 through
    the commit that removed the others."""
    import inspect
    from hkrd.jobs import project_card, rebuild_sarr
    from hkrd.model import evaluate, sarr
    from hkrd.query import speedmap
    assert inspect.signature(rebuild_sarr.rebuild).parameters[
        "min_prior"].default == sarr.MIN_PRIOR
    assert inspect.signature(rebuild_sarr.score_runners).parameters[
        "min_prior"].default == sarr.MIN_PRIOR
    assert inspect.signature(evaluate.score).parameters[
        "min_prior"].default == sarr.MIN_PRIOR
    # The job that writes the speed map's NULLs -- and its CLI default, which
    # is the value that actually runs: `ops/crontab` calls `project_card
    # --pending` and never passes the flag.
    assert inspect.signature(project_card.project).parameters[
        "min_prior"].default == sarr.MIN_PRIOR
    assert project_card._parser().get_default("min_prior") == sarr.MIN_PRIOR
    assert rating.unrated_reason(None, prior=sarr.MIN_PRIOR - 1)["kind"] == "history"
    assert rating.unrated_reason(None, prior=sarr.MIN_PRIOR)["kind"] == "unscored"
    # And the sentence the speed map writes under the ladder.
    short = speedmap._reason({"settle": None, "draw": 1,
                              "n_prior": sarr.MIN_PRIOR - 1})
    assert str(sarr.MIN_PRIOR) in short
    assert speedmap._reason({"settle": None, "draw": 1,
                             "n_prior": sarr.MIN_PRIOR}) != short

# ── how much history a horse brings ──────────────────────────────────────────

def _card(tmp_path, runs):
    """runs: {horse_name: [(race_date, finished)]}. The card itself is 2026-09-20."""
    path = tmp_path / "r.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        dates = sorted({d for hist in runs.values() for d, _ in hist})
        for i, date in enumerate(dates, start=1):
            conn.execute(
                "INSERT INTO races (race_date, race_no, venue, surface, going,"
                " distance) VALUES (?, 1, 'ST', 'Turf', 'G', 1200)", (date,))
        for no, (name, hist) in enumerate(runs.items(), start=1):
            for date, finished in hist:
                conn.execute(
                    "INSERT INTO runners (race_date, race_no, horse_no,"
                    " horse_name, finish_time) VALUES (?, 1, ?, ?, ?)",
                    (date, no, name, 69.5 if finished else None))
    return conn


def test_a_horse_with_no_prior_runs_is_absent_rather_than_zero(tmp_path):
    """A debutant produces no row, which is exactly the case this exists to
    name -- so callers read it with `.get(name, 0)` and never a KeyError."""
    conn = _card(tmp_path, {"DEBUTANT": [("2026-09-20", False)]})
    out = rating.prior_run_counts(conn, ["DEBUTANT"], before="2026-09-20")
    conn.close()
    assert out == {}
    assert out.get("DEBUTANT", 0) == 0
    assert rating.unrated_reason(None, out.get("DEBUTANT", 0))["label"] == "DEBUT"


def test_today_is_not_counted_as_prior_history(tmp_path):
    """The run being rated must never inform its own rating. `race_date < ?`
    is the whole of that guarantee here."""
    conn = _card(tmp_path, {"OLD HAND": [("2026-08-02", True), ("2026-08-16", True),
                                         ("2026-09-20", False)]})
    out = rating.prior_run_counts(conn, ["OLD HAND"], before="2026-09-20")
    conn.close()
    assert out == {"OLD HAND": 2}


def test_a_run_with_no_finishing_time_does_not_count(tmp_path):
    """A scratching, a withdrawal, or a meeting whose results never landed.
    None of them measured the horse, and the rebuild does not count them."""
    conn = _card(tmp_path, {"PATCHY": [("2026-08-02", True), ("2026-08-16", False),
                                       ("2026-09-06", True), ("2026-09-20", False)]})
    out = rating.prior_run_counts(conn, ["PATCHY"], before="2026-09-20")
    conn.close()
    assert out == {"PATCHY": 2}


def test_the_whole_field_is_counted_in_one_query(tmp_path):
    """Per runner it is a scan apiece. A per-race constant computed per row is
    the performance rule this project already wrote down."""
    conn = _card(tmp_path, {
        "A": [("2026-08-02", True), ("2026-09-20", False)],
        "B": [("2026-08-02", True), ("2026-09-06", True), ("2026-09-20", False)],
        "C": [("2026-09-20", False)],
    })
    conn.set_trace_callback(lambda sql: queries.append(sql))
    queries: list[str] = []
    out = rating.prior_run_counts(conn, ["A", "B", "C"], before="2026-09-20")
    conn.set_trace_callback(None)
    conn.close()
    assert out == {"A": 1, "B": 2}
    assert len(queries) == 1


def test_an_empty_field_asks_nothing(tmp_path):
    """A race with no declared runners must not build `IN ()`, which is not
    valid SQL."""
    conn = _card(tmp_path, {"A": [("2026-09-20", False)]})
    assert rating.prior_run_counts(conn, [], before="2026-09-20") == {}
    assert rating.prior_run_counts(conn, [None], before="2026-09-20") == {}
    conn.close()


def test_the_two_pages_get_the_same_count_from_the_same_call():
    """Race Day and Model Analysis both read this. They asked separately
    before -- and the one that asked in SQL carried the predicate inline, so
    the two could have drifted without anything failing."""
    import inspect
    from hkrd.query import model, raceday
    for module in (raceday, model):
        assert "rating_q.prior_run_counts(" in inspect.getsource(module), \
            module.__name__


def test_the_counting_predicate_is_written_down_once():
    """The query layer's copy of "a prior run". `jobs/rebuild_sarr` counts to a
    different rule over a pandas frame, which is recorded in
    `rating.PRIOR_RUN_RULE` and is a model change to reconcile -- but no second
    page may state this one."""
    from pathlib import Path
    fragment = "AND finish_time IS NOT NULL GROUP BY horse_name"
    holders = [f.name for f in sorted(Path("hkrd/query").glob("*.py"))
               if fragment in f.read_text()]
    assert holders == ["rating.py"]


# ── a card nobody scored, told apart from a card that scored nobody ──────────

def _race(tmp_path, *, sarr_rows):
    """One race of four. `sarr_rows`: {horse_no: sarr or None}, absent = no row."""
    path = tmp_path / "s.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        conn.execute(
            "INSERT INTO races (race_date, race_no, venue, surface, going,"
            " distance) VALUES ('2026-09-20', 1, 'ST', 'Turf', 'G', 1200)")
        for no in range(1, 5):
            conn.execute(
                "INSERT INTO runners (race_date, race_no, horse_no, horse_name)"
                " VALUES ('2026-09-20', 1, ?, ?)", (no, f"HORSE {no}"))
            if no in sarr_rows:
                conn.execute(
                    "INSERT INTO runner_sarr (race_date, race_no, horse_no,"
                    " sarr, sarr_rank, n_prior, derive_version)"
                    " VALUES ('2026-09-20', 1, ?, ?, ?, 6, 'sarr-1.1')",
                    (no, sarr_rows[no], 1 if sarr_rows[no] is not None else None))
    return conn


def test_a_race_with_no_rows_at_all_was_never_scored(tmp_path):
    conn = _race(tmp_path, sarr_rows={})
    assert rating.race_was_scored(conn, "2026-09-20", 1) is False
    conn.close()


def test_a_race_that_rated_nobody_was_still_scored(tmp_path):
    """The case that makes this answerable. A maiden field of first-starters
    produces no ratings at all, and while the table held rated runners only it
    was indistinguishable from a card nobody had looked at."""
    conn = _race(tmp_path, sarr_rows={1: None, 2: None, 3: None, 4: None})
    assert rating.race_was_scored(conn, "2026-09-20", 1) is True
    conn.close()


def test_an_unscored_card_outranks_a_runners_own_history(tmp_path):
    """True of the horse is not the useful thing to say. A debutant on a card
    nobody scored would read DEBUT, which tells the reader nothing is wrong."""
    debut = rating.unrated_reason(None, 0, card_scored=False)
    assert debut["kind"] == "no_card_score"
    assert debut["label"] == "CARD NOT SCORED"
    # And once the card IS scored, the same runner reads as the rule it is.
    assert rating.unrated_reason(None, 0)["label"] == "DEBUT"


def test_a_rated_runner_is_unaffected_by_the_card_level_signal():
    """The race-level fault cannot apply to a runner that has a rank, because
    having one is proof the card was scored."""
    assert rating.unrated_reason(2, 9, card_scored=False) is None
