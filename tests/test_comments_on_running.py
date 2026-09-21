"""The comments on running arrive days after a meeting, and must still arrive.

Every September 2026 meeting reached the pages with no running comment, no lane
note and no tag drawn from either -- 446 runners across four meetings. Three
faults lined up, and each has a test here that fails without its fix:

  1. The scheduler asked "does this race have ANY comment?". Once the stewards'
     incident report was scraped off the results page, the answer was yes
     within the hour of every race, so every meeting read as settled before
     HKJC had written its running comments.
  2. The retry window was four days. HKJC writes the comments up later than
     that -- measured on 2026-09-21, 6/9/13 September were published and 16
     September (five days old) was not -- so a meeting fell out of the window
     before there was anything to fetch.
  3. HKJC's "not written yet" sentence reached the Form Guide as the horse's
     running comment, because `query/race` did not drop it where
     `query/results` did.

And one gap in the vocabulary: the stewards' verdict that a run was
DISAPPOINTING had no tag, so a run whose only other note was the routine
examination that follows it read as a clean trip on every page.
"""
from __future__ import annotations

import datetime as dt

import pytest

from hkrd.derive import tags as tagger
from hkrd.ingest import corunning
from hkrd.jobs import nightly, scrape_corunning
from hkrd.jobs import scrape_meeting as scrape_job
from hkrd.query import race as race_q
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

PLACEHOLDER = "No Comments on Running information for this horse."
TODAY = dt.date(2026, 9, 21)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "comments.db"
    monkeypatch.setenv("HKRD_DB", str(path))
    conn = get_conn(path)
    init_db(conn)
    conn.close()
    return path


def _meeting(db, date: str, *, races: int = 2, results: bool = True,
             incident: bool = True, running: str | None = PLACEHOLDER) -> None:
    """A meeting as HKJC leaves it on race night: result, dividends, sectionals
    and the stewards' incident report all in, and the running comments either
    absent, the placeholder, or (once written up) real text."""
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": date, "race_no": n, "venue": "HV", "distance": 1650,
             "course": "B", "surface": "Turf", "going": "G", "race_class": "3"}
            for n in range(1, races + 1)])
        upsert.upsert_runners(conn, [
            {"race_date": date, "race_no": n, "horse_no": h,
             "horse_name": f"HORSE {n}{h}",
             "place": str(h) if results else None,
             "section_times": "24.13; 22.33; 22.13"}
            for n in range(1, races + 1) for h in (1, 2)])
        upsert.upsert_dividends(conn, [
            {"race_date": date, "race_no": n, "pool": "WIN",
             "combination": "1", "dividend": 25.0}
            for n in range(1, races + 1)])
        rows = []
        for n in range(1, races + 1):
            for h in (1, 2):
                if incident:
                    rows.append((date, n, h, "A veterinary inspection did not "
                                 "show any significant findings.", "incident"))
                if running is not None:
                    rows.append((date, n, h, running, "corunning"))
        conn.executemany("INSERT OR REPLACE INTO runner_comments (race_date, "
                         "race_no, horse_no, comment_text, source) "
                         "VALUES (?,?,?,?,?)", rows)
    conn.close()


def _plans(db, **kw) -> dict[str, nightly.Plan]:
    return {p.date: p for p in nightly.plan_window(db, today=TODAY, **kw)}


def _day(offset: int) -> str:
    return (TODAY - dt.timedelta(days=offset)).isoformat()


# ── 1. the stewards' report is not a running comment ─────────────────────────

def test_a_stewards_report_does_not_make_a_meeting_commented(db):
    """The fault in one test. Race night: every runner has its incident report
    and HKJC's placeholder. Before the fix this read as settled."""
    _meeting(db, _day(1))
    plan = _plans(db)[_day(1)]
    assert plan.act and plan.comments_only
    assert "without comments on running" in plan.reason


def test_nor_does_a_stewards_report_with_no_running_rows_at_all(db):
    _meeting(db, _day(1), running=None)
    assert _plans(db)[_day(1)].comments_only


def test_real_running_comments_settle_the_meeting(db):
    _meeting(db, _day(1), running="Raced wide without cover, weakened.")
    plan = _plans(db)[_day(1)]
    assert not plan.act and "settled" in plan.reason


def test_a_meeting_missing_its_results_is_still_a_full_scrape(db):
    """Only a meeting that is complete BUT for its comments is asked for them
    alone. Missing results are the full job, as before."""
    _meeting(db, _day(1), results=False)
    plan = _plans(db)[_day(1)]
    assert plan.act and not plan.comments_only


# ── 2. the window is as long as HKJC's lag ───────────────────────────────────

def test_comments_are_still_asked_for_past_the_four_day_window(db):
    """16 September was five days old and unwritten when this was measured. A
    four-day window had already let it go."""
    _meeting(db, _day(10))
    plan = _plans(db).get(_day(10))
    assert plan is not None and plan.comments_only


def test_only_the_comments_are_chased_that_far_back(db):
    """Past the main window a meeting missing its RESULTS is a repair, not a
    nightly job, and must not start being re-scraped."""
    _meeting(db, _day(10), results=False)
    assert _day(10) not in _plans(db)


def test_a_meeting_hkjc_never_writes_up_falls_out_on_its_own(db):
    _meeting(db, _day(nightly.COMMENTS_BACK + 1))
    assert _day(nightly.COMMENTS_BACK + 1) not in _plans(db)


def test_the_extended_window_does_not_list_every_quiet_day(db):
    """The printed window stays readable: past the main window a date appears
    only when it has comments outstanding."""
    plans = _plans(db)
    assert len(plans) == nightly.DEFAULT_BACK + nightly.DEFAULT_AHEAD + 1


# ── the retry: one request while waiting, then the comments, then the tags ──

def _no_full_scrape(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("a settled meeting was re-scraped to get its comments")
    monkeypatch.setattr(scrape_job, "scrape_meeting", explode)


def test_waiting_on_hkjc_costs_one_request_and_writes_nothing(db, monkeypatch):
    _meeting(db, _day(5))
    _no_full_scrape(monkeypatch)
    asked = []

    def fetch(date, race_no, **k):
        asked.append((date, race_no))
        return [{"horse_no": 1, "comment": PLACEHOLDER},
                {"horse_no": 2, "comment": PLACEHOLDER}]

    def fetch_meeting(*a, **k):
        raise AssertionError("walked the whole card for a meeting not written up")

    monkeypatch.setattr(corunning, "fetch", fetch)
    monkeypatch.setattr(corunning, "fetch_meeting", fetch_meeting)
    report = nightly.run(db, today=TODAY)
    assert asked == [(_day(5), 1)]
    assert report.ok and report.pending_comments == [_day(5)]
    assert not report.derived
    assert "waiting on HKJC" in report.one_line()


def test_once_written_up_the_comments_land_and_every_tag_follows(db, monkeypatch):
    """The whole chain: the comments replace the placeholder, the lane note is
    read, and the tags are rebuilt -- the tags being what every page reads."""
    _meeting(db, _day(5))
    _no_full_scrape(monkeypatch)
    written = "Raced three wide without cover, weakened in the straight."
    monkeypatch.setattr(corunning, "fetch", lambda date, n, **k: [
        {"horse_no": 1, "comment": written}, {"horse_no": 2, "comment": written}])
    monkeypatch.setattr(corunning, "fetch_meeting", lambda date, **k: {
        n: [{"horse_no": 1, "comment": written},
            {"horse_no": 2, "comment": written}] for n in (1, 2)})

    report = nightly.run(db, today=TODAY)
    assert report.ok, report.errors
    assert any("comments on running, 4 runners" in s for s in report.scraped)

    conn = get_conn(db)
    stored = {r[0] for r in conn.execute(
        "SELECT comment_text FROM runner_comments WHERE source = 'corunning'")}
    tags = {r[0] for r in conn.execute(
        "SELECT tag FROM runner_tags WHERE race_date = ? AND race_no = 1 "
        "AND horse_no = 1", (_day(5),))}
    conn.close()
    assert stored == {written}, "the placeholder was not replaced"
    assert {"wide", "without_cover", "weakened"} <= tags
    assert any(t.startswith("lane:") for t in tags), "no lane note was read"
    # And the meeting is settled now, so tomorrow asks nothing: past the main
    # window a settled meeting is not even listed.
    assert _day(5) not in _plans(db)


def test_a_race_not_yet_written_is_counted_not_stored_as_a_comment(db):
    rows = [{"horse_no": 1, "comment": PLACEHOLDER}]
    assert not scrape_corunning.published(rows)
    assert scrape_corunning.published(
        rows + [{"horse_no": 2, "comment": "Led, kept on."}])
    # Padded, as a scraped cell can be. Still the placeholder.
    assert not scrape_corunning.published(
        [{"horse_no": 1, "comment": "  " + PLACEHOLDER + " "}])


# ── 3. the placeholder is never shown as a running comment ───────────────────

def test_the_form_guide_never_reads_the_placeholder_as_a_comment(db):
    _meeting(db, _day(1))
    conn = get_conn(db)
    running, incident = race_q._comments(conn, _day(1), 1, 1)
    conn.close()
    assert running is None
    assert incident and "veterinary" in incident


# ── the vocabulary: a disappointing run is not a clean one ──────────────────

ROMANTIC_GLADIATOR = (
    "Y L Chung could offer no explanation for the horse's disappointing "
    "performance.  A veterinary inspection of the horse immediately following "
    "the race did not show any significant findings.")


def test_the_stewards_verdict_on_a_poor_run_is_a_tag():
    """ROMANTIC GLADIATOR, 2026-09-16 R6, ninth. The only tag this text had was
    the routine examination, which every page hides -- so the run the stewards
    stopped to question the rider about showed as nothing at all."""
    names = {t.name for t in tagger.tag_comment(ROMANTIC_GLADIATOR)}
    assert names == {"disappointing", "vet_routine"}


@pytest.mark.parametrize("text", [
    "When questioned regarding the disappointing performance K C Leung stated "
    "that his mount travelled only fairly.",
    "GUMMY GUMMY did not respond to his riding and was disappointing.",
    "The performance of LUCKY MAN was considered disappointing as compared to "
    "its previous race starts.",
])
def test_the_ways_the_stewards_say_it(text):
    assert "disappointing" in {t.name for t in tagger.tag_comment(text)}


def test_it_is_a_negative_note_on_the_run_and_not_routine():
    tag = next(t for t in tagger.tag_comment(ROMANTIC_GLADIATOR)
               if t.name == "disappointing")
    assert (tag.kind, tag.polarity) == ("trouble", -1)
