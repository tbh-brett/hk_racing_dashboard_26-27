"""The odds capture path, tested against real recorded snapshots.

The browser half cannot run here, so it is faked: `fetch_race` takes a page
object and only ever calls four methods on it, which makes the whole extraction
and guard path testable without Playwright. What is NOT faked is the page text —
that comes from the shape the live site actually renders.

The two behaviours worth pinning are the ones that cost real work in the old
system:

  * a stale DOM must be refused, not stored. Races 3-9 latching onto race 1's
    runners is silent corruption of the one table that cannot be rebuilt.
  * a missing header must raise. An empty row list is indistinguishable from a
    race with no market, and that is the corunning lesson.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hkrd.ingest import odds as odds_ingest
from hkrd.jobs import scrape_odds
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

DATE = "2026-07-15"

PANEL = """\
Last Update: 15/07/2026 16:22
Race 3
15/07, WED, 16:45, Class 4, 1800m, TURF, TEST HANDICAP, "C" Course, GOOD
No. Banker Sel. Horse Name Win Place
1
        HARMONY GALAXY
16
5.2
2
        CASA ROCHESTER
4.9
1.9
10
        KYRUS TREASURE
3.0
1.4
F
Field
"""


class FakePage:
    """Only the four methods `fetch_race` uses."""

    def __init__(self, texts, matrices=None):
        self._texts = list(texts)
        self._matrices = matrices or []
        self.goto_calls = 0
        self.waits = 0

    def goto(self, url, **kw):
        self.goto_calls += 1

    def wait_for_selector(self, selector, **kw):
        return None

    def wait_for_timeout(self, ms):
        self.waits += 1

    def evaluate(self, script):
        return self._matrices

    def locator(self, what):
        page = self

        class _L:
            def inner_text(self, **kw):
                return page._texts[0] if len(page._texts) == 1 else page._texts.pop(0)
        return _L()


# ─── extraction ───────────────────────────────────────────────────────────────

def test_win_and_place_come_off_the_panel():
    page = FakePage([PANEL])
    snap = odds_ingest.fetch_race(page, DATE, "HV", 3)
    by_no = {r["no"]: r for r in snap["odds"]}
    assert by_no["10"]["horse"] == "KYRUS TREASURE"
    assert by_no["10"]["win"] == "3.0"
    assert by_no["10"]["place"] == "1.4"
    assert snap["n_runners"] == 3


def test_place_is_never_a_multiple_of_win():
    """The ratio varies runner to runner, which is why place must be scraped."""
    page = FakePage([PANEL])
    snap = odds_ingest.fetch_race(page, DATE, "HV", 3)
    ratios = {round(float(r["win"]) / float(r["place"]), 2) for r in snap["odds"]}
    assert len(ratios) > 1


def test_a_missing_header_raises_rather_than_returning_nothing():
    """The corunning lesson: a parser that cannot find its shape must say so."""
    page = FakePage(["Last Update: 15/07/2026 16:22\nsomething else entirely\n"])
    with pytest.raises(odds_ingest.OddsError, match="header not found"):
        odds_ingest.fetch_race(page, DATE, "HV", 3)


def test_a_wait_that_times_out_is_recorded_not_swallowed():
    class Timeouts(FakePage):
        def wait_for_selector(self, selector, **kw):
            raise TimeoutError(selector)

    out = odds_ingest.fetch_race(Timeouts([PANEL]), DATE, "HV", 3)
    assert any("did not render" in n for n in out["notes"])


def test_pair_matrices_are_carried_through():
    page = FakePage([PANEL], matrices=[
        {"label": "qin", "pairs": [{"a": "1", "b": "2", "odds": "9"}]},
        {"label": "qpl", "pairs": [{"a": "1", "b": "2", "odds": "4"}]},
    ])
    snap = odds_ingest.fetch_race(page, DATE, "HV", 3)
    assert snap["qin_odds"] == [{"a": "1", "b": "2", "odds": "9"}]
    assert snap["qpl_odds"] == [{"a": "1", "b": "2", "odds": "4"}]


# ─── the stale-DOM guard ──────────────────────────────────────────────────────

def test_an_unchanged_dom_is_flagged_stale():
    """Without this, a whole meeting records race 1's runners."""
    page = FakePage([PANEL] * 40)
    first = odds_ingest.fetch_race(page, DATE, "HV", 3)
    second = odds_ingest.fetch_race(page, DATE, "HV", 4,
                                    previous=first["_fingerprint"])
    assert second["stale_dom"] is True
    assert page.goto_calls == 3          # settle, settle, forced reload


def test_a_changed_dom_is_not_flagged():
    other = PANEL.replace("HARMONY GALAXY", "OCEAN IMPACT")
    page = FakePage([PANEL, other, other, other])
    first = odds_ingest.fetch_race(page, DATE, "HV", 3)
    second = odds_ingest.fetch_race(page, DATE, "HV", 4,
                                    previous=first["_fingerprint"])
    assert "stale_dom" not in second


# ─── the job ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "odds.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": n, "venue": "HV", "course": "C",
             "surface": "Turf", "going": "G", "distance": 1800}
            for n in (3, 4)])
    conn.close()
    return path


def test_the_job_stores_snapshots_and_reports_counts(db, monkeypatch):
    def fake_meeting(date, venue, races, **kw):
        page = FakePage([PANEL] * 4)
        return [odds_ingest.fetch_race(page, date, venue, n) for n in races]

    monkeypatch.setattr(odds_ingest, "fetch_meeting", fake_meeting)
    # turnover and doubles off: this is the win/place path, and leaving them on
    # sends a unit test to bet.hkjc.com in a real browser.
    report = scrape_odds.run(DATE, "HV", db=str(db),
                             turnover=False, doubles=False)
    assert report.races == 2
    assert report.win_place == 6            # 3 runners x 2 races
    assert "2 races" in report.line()

    conn = get_conn(db)
    try:
        assert conn.execute(
            "SELECT count(*) FROM odds_snapshots").fetchone()[0] == 6
    finally:
        conn.close()


def test_a_stale_race_is_skipped_rather_than_stored(db, monkeypatch):
    """Storing it would put race 3's prices under race 4's number, forever."""
    def fake_meeting(date, venue, races, **kw):
        page = FakePage([PANEL] * 40)
        out, prev = [], None
        for n in races:
            snap = odds_ingest.fetch_race(page, date, venue, n, previous=prev)
            prev = snap["_fingerprint"]
            out.append(snap)
        return out

    monkeypatch.setattr(odds_ingest, "fetch_meeting", fake_meeting)
    report = scrape_odds.run(DATE, "HV", db=str(db),
                             turnover=False, doubles=False)
    assert report.races == 1
    assert any("stale DOM" in s for s in report.skipped)

    conn = get_conn(db)
    try:
        races_stored = conn.execute(
            "SELECT DISTINCT race_no FROM odds_snapshots").fetchall()
    finally:
        conn.close()
    assert [r["race_no"] for r in races_stored] == [3]


def test_nothing_in_the_capture_path_deletes():
    """prune_old_snapshots is why only 17 meetings of a season survived.

    Checked against the parsed code rather than the file text, so the comments
    that explain WHY nothing deletes do not trip the test that enforces it.
    """
    import ast

    for module in (scrape_odds, odds_ingest):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value is ast.get_docstring(tree):
                    continue
                assert "DELETE FROM" not in node.value.upper(), (
                    f"{module.__name__} contains a DELETE")
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(
                    node.func, "id", None)
                assert name != "prune_old_snapshots", (
                    f"{module.__name__} calls prune_old_snapshots")


# ─── against the real archive ─────────────────────────────────────────────────

LEGACY = Path("/home/user/hk_race_dashboard/cache/live_odds")


@pytest.mark.skipif(not LEGACY.is_dir(), reason="legacy odds cache not present")
def test_recorded_snapshots_still_parse():
    """The 17 meetings that survived pruning are the regression corpus.

    `fetch_race` builds the same payload these files hold, so if they stop
    parsing, the shape the fetch produces has drifted from the one the store
    expects.
    """
    files = sorted(LEGACY.glob("*/*.json"))[:40]
    assert files, "expected recorded snapshots"
    priced = 0
    for f in files:
        parsed = odds_ingest.parse_snapshot(json.loads(f.read_text(encoding="utf-8")))
        rows = odds_ingest.snapshot_rows(parsed)
        priced += len(rows)
    assert priced > 0


# ─── safe to run every quarter hour ───────────────────────────────────────────

def test_a_day_with_no_meeting_is_not_an_error(db, monkeypatch):
    """Most days have no meeting. The job must no-op without opening a browser."""
    import datetime as dt

    def explode(*a, **kw):
        raise AssertionError("must not launch a browser with nothing to price")

    monkeypatch.setattr(odds_ingest, "fetch_meeting", explode)
    report = scrape_odds.run(db=str(db), today=dt.date(2026, 7, 20))
    assert report.races == 0
    assert report.attempted == 0
    assert "nothing to price" in report.line()


def test_an_explicit_date_with_no_meeting_raises(db):
    """Asking for a date by name and getting silence is a different mistake.

    Naming a venue must not paper over it — that was the flaw a passing test
    would have hidden: an unscraped card reported as "all races settled".
    """
    with pytest.raises(ValueError, match="scrape the card"):
        scrape_odds.run("2026-07-20", "HV", db=str(db))


def test_races_already_run_are_dropped():
    """A settled price cannot move, so re-capturing it adds rows and no facts."""
    import datetime as dt
    now = dt.datetime.fromisoformat("2026-07-15T17:30:00")
    assert scrape_odds._is_settled("2026-07-15", "16:45", now) is True
    assert scrape_odds._is_settled("2026-07-15", "17:15", now) is False


def test_an_unreadable_off_time_keeps_the_race():
    """Dropping a race because its time would not parse is a silent stop."""
    import datetime as dt
    now = dt.datetime.fromisoformat("2026-07-15T17:30:00")
    assert scrape_odds._is_settled("2026-07-15", "not a time", now) is False


# ─── turnover and doubles ─────────────────────────────────────────────────────

def _fake_meeting(date, venue, races, **kw):
    page = FakePage([PANEL] * 4)
    return [odds_ingest.fetch_race(page, date, venue, n) for n in races]


def test_a_leg_closes_when_its_FIRST_race_goes_off():
    """Leg N couples race N with race N+1, so it shuts when race N runs -- not
    when race N+1 does. Getting this backwards keeps paying for a page whose
    market has already closed."""
    assert scrape_odds.live_legs([1, 2, 3], 3) == [1, 2]
    assert scrape_odds.live_legs([3], 3) == []          # nothing left to pair
    assert scrape_odds.live_legs([2, 3], 3) == [2]


def test_the_last_race_has_no_leg():
    assert scrape_odds.live_legs([10], 10) == []
    assert scrape_odds.live_legs(list(range(1, 11)), 10) == list(range(1, 10))


def test_turnover_and_doubles_are_stored_and_counted(db, monkeypatch):
    monkeypatch.setattr(odds_ingest, "fetch_meeting", _fake_meeting)

    def fake_extras(date, venue, races, legs, **kw):
        turnover_rows = [
            {"race_date": date, "race_no": n, "pool": p,
             "captured_at": "2026-07-15T09:00:00", "turnover": amount}
            for n in races for p, amount in (("WIN", 100.0), ("QIN", 50.0))]
        double_rows = [
            {"race_date": date, "leg_no": leg, "horse_first": 1,
             "horse_second": 2, "captured_at": "2026-07-15T09:00:00",
             "odds": 12.0} for leg in legs]
        return turnover_rows, double_rows, ["a note that was kept"]

    monkeypatch.setattr(scrape_odds, "_capture_extras", fake_extras)
    report = scrape_odds.run(DATE, "HV", db=str(db))

    assert report.turnover == 4          # 2 races x 2 pools
    assert report.doubles == 1           # only leg 3, since race 4 is last
    assert report.legs == 1
    assert "turnover" in report.line() and "legs" in report.line()
    assert "a note that was kept" in report.notes

    conn = get_conn(db)
    try:
        assert conn.execute(
            "SELECT count(*) FROM odds_pool_turnover").fetchone()[0] == 4
        assert conn.execute(
            "SELECT leg_no FROM odds_doubles").fetchone()["leg_no"] == 3
    finally:
        conn.close()


def test_switching_the_extras_off_leaves_the_prices_alone(db, monkeypatch):
    """The two are separable on purpose: on a small machine the win and place
    prices are the ones that must not be missed."""
    monkeypatch.setattr(odds_ingest, "fetch_meeting", _fake_meeting)

    def explode(*a, **kw):
        raise AssertionError("extras were captured despite being switched off")

    monkeypatch.setattr(scrape_odds, "_capture_extras", explode)
    report = scrape_odds.run(DATE, "HV", db=str(db),
                             turnover=False, doubles=False)
    assert report.win_place == 6 and report.turnover == 0 and report.doubles == 0


def test_a_failed_doubles_grid_does_not_cost_the_meeting_its_prices(db, monkeypatch):
    """Prices are committed before the extras are attempted. A grid that will
    not render is a lost denominator, never a lost market."""
    monkeypatch.setattr(odds_ingest, "fetch_meeting", _fake_meeting)

    def half_broken(date, venue, races, legs, **kw):
        return ([], [], [f"leg {legs[0]}: DoublesError: no doubles grid"])

    monkeypatch.setattr(scrape_odds, "_capture_extras", half_broken)
    report = scrape_odds.run(DATE, "HV", db=str(db))
    assert report.win_place == 6
    assert report.doubles == 0
    assert any("no doubles grid" in n for n in report.notes)

    conn = get_conn(db)
    try:
        assert conn.execute(
            "SELECT count(*) FROM odds_snapshots").fetchone()[0] == 6
    finally:
        conn.close()


def test_a_browser_that_will_not_start_twice_still_records_the_run(db, monkeypatch):
    """Chromium wants ~512MB and the deployed machine has 1GB, so the second
    launch failing is a real outcome. The prices are already committed by then;
    raising here would skip the job_log write and report the whole capture as
    missing when the irreplaceable half of it landed."""
    monkeypatch.setattr(odds_ingest, "fetch_meeting", _fake_meeting)

    def no_browser(*a, **kw):
        raise odds_ingest.OddsError("playwright is installed but has no browser")

    monkeypatch.setattr(scrape_odds, "_capture_extras", no_browser)
    report = scrape_odds.run(DATE, "HV", db=str(db))
    assert report.win_place == 6
    assert report.races == 2
    assert any("did not run" in n for n in report.notes)

    conn = get_conn(db)
    try:
        assert conn.execute(
            "SELECT count(*) FROM odds_snapshots").fetchone()[0] == 6
        # The run is on the record, so the freshness strip does not report a
        # gap for a capture that actually stored its prices.
        assert conn.execute(
            "SELECT count(*) FROM job_runs WHERE job LIKE '%odds%'").fetchone()[0] >= 1
    finally:
        conn.close()
