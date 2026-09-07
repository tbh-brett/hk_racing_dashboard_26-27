"""The odds capture path, tested against recorded replies from the real endpoint.

The network half is faked: `fetch_meeting` makes exactly two POSTs through
`_client.fetch_json`, so stubbing that one function makes the whole extraction
and guard path testable offline. What is NOT faked is the JSON — the three
fixtures in `tests/fixtures/odds_*.json` are verbatim replies captured on
2026-09-04, trimmed to a few races and otherwise untouched:

  odds_meetings.json          the meetings probe: five meetings, one of them
                              still filed under an `MTG_...` id
  odds_pools_live.json        a market open for betting, including a six-runner
                              race with no QPL pool and a race with three
                              scratchings priced `SCR`
  odds_pools_predeclared.json a card declared but not yet selling — every pool
                              present, every one of them empty

The behaviours worth pinning are the ones that cost real work in the old
system, and the one that would have cost it here:

  * a reply about a DIFFERENT meeting must be refused, not stored. Asking for a
    date HKJC has no meeting on returns the current meeting instead, silently.
    That is this module's version of the stale DOM that used to make races 3-9
    record race 1's runners.
  * a shape the parser does not recognise must raise. An empty row list is
    indistinguishable from a race with no market, and that is the corunning
    lesson.
  * nothing on this path may ever delete a snapshot.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from hkrd.ingest import _client, odds as odds_ingest
from hkrd.jobs import scrape_odds
from hkrd.store import upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIXTURES = Path(__file__).parent / "fixtures"
DATE = "2026-09-05"
VENUE = "S1"                 # a simulcast meeting: the only market open for
                             # betting when the fixtures were captured, and
                             # the pools are shaped identically to Sha Tin's.
PRE_DATE, PRE_VENUE = "2026-09-06", "ST"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def endpoint(monkeypatch):
    """Answer both queries from the recorded replies.

    Returns the call log, so a test can assert a whole meeting still costs one
    request for the pools rather than one per race.
    """
    calls: list[dict] = []
    replies = {"racingChanges": _fixture("odds_meetings"),
               "racing": _fixture("odds_pools_live")}

    def fake(url, payload, **kw):
        calls.append(payload)
        return replies[payload["operationName"]]

    monkeypatch.setattr(odds_ingest, "fetch_json", fake)
    return calls, replies


# ─── the meeting probe ────────────────────────────────────────────────────────

def test_the_meeting_id_is_read_from_hkjc_not_built(endpoint):
    """Two id formats are in the recorded reply and neither is parsed.

    A meeting open for betting is `20260905S1`; one only declared is
    `MTG_20260906_0001`, which carries no venue at all. Constructing either
    from the date and venue would be right until it was not.
    """
    assert odds_ingest.meeting_id(DATE, VENUE) == "20260905S1"
    assert odds_ingest.meeting_id(PRE_DATE, PRE_VENUE) == "MTG_20260906_0001"


def test_a_meeting_hkjc_does_not_have_is_none_not_a_guess(endpoint):
    assert odds_ingest.meeting_id("2026-09-08", "ST") is None


def test_an_empty_meetings_list_raises(monkeypatch):
    """HKJC lists meetings even between seasons, so none is a changed shape."""
    monkeypatch.setattr(odds_ingest, "fetch_json",
                        lambda *a, **k: {"data": {"raceMeetings": []}})
    with pytest.raises(odds_ingest.OddsError, match="empty"):
        odds_ingest.meeting_id(DATE, VENUE)


# ─── extraction ───────────────────────────────────────────────────────────────

def test_a_whole_meeting_costs_one_request_for_the_pools(endpoint):
    """It used to be a page load per race through a browser."""
    calls, _ = endpoint
    snaps = odds_ingest.fetch_meeting(DATE, VENUE)
    assert [c["operationName"] for c in calls] == ["racingChanges", "racing"]
    assert len(snaps) == 3
    # And the race filter is applied here, not asked for: the request is the
    # same size either way.
    assert calls[1]["variables"].get("raceNo") is None


def test_win_and_place_land_on_the_same_runner(endpoint):
    snaps = {s["race_no"]: s for s in odds_ingest.fetch_meeting(DATE, VENUE)}
    by_no = {r["no"]: r for r in snaps[1]["odds"]}
    assert by_no["2"]["win"] == "2.8"
    assert by_no["2"]["place"] == "1.0"
    assert snaps[1]["n_runners"] == 9


def test_place_is_never_a_multiple_of_win(endpoint):
    """The ratio varies runner to runner, which is why place must be captured
    rather than derived from the win price."""
    snaps = {s["race_no"]: s for s in odds_ingest.fetch_meeting(DATE, VENUE)}
    ratios = {round(float(r["win"]) / float(r["place"]), 2)
              for r in snaps[1]["odds"]}
    assert len(ratios) > 1


def test_a_scratched_runner_is_none_and_not_zero(endpoint):
    """Race 4 of the recorded meeting has three runners priced `SCR`. Zero
    would read as a price of nothing, which is a different and impossible
    claim; dropping the row would lose that the horse was declared."""
    snaps = {s["race_no"]: s for s in odds_ingest.fetch_meeting(DATE, VENUE)}
    parsed = odds_ingest.parse_snapshot(snaps[4])
    rows = {r["horse_no"]: r for r in odds_ingest.snapshot_rows(parsed)}
    assert rows[3]["win_odds"] is None and rows[3]["place_odds"] is None
    assert rows[1]["win_odds"] == pytest.approx(4.3)
    assert len(rows) == 14              # the scratchings are still declared


def test_pairs_come_out_of_the_reply_already_paired(endpoint):
    """`combString` is '02,04'. The old capture rebuilt this from the bounding
    boxes of a triangular matrix rendered into a square table."""
    snaps = {s["race_no"]: s for s in odds_ingest.fetch_meeting(DATE, VENUE)}
    parsed = odds_ingest.parse_snapshot(snaps[1])
    pairs = {(p["pool"], p["horse_a"], p["horse_b"]): p["odds"]
             for p in odds_ingest.pair_rows(parsed)}
    assert pairs[("QIN", 2, 4)] == pytest.approx(8.0)
    assert pairs[("QPL", 2, 4)] == pytest.approx(3.3)
    # C(9,2) for a nine-runner field, in both pools and neither more.
    assert sum(1 for k in pairs if k[0] == "QIN") == 36
    assert sum(1 for k in pairs if k[0] == "QPL") == 36


def test_a_field_too_small_for_a_qpl_pool_is_not_reported_as_missing(endpoint):
    """HKJC runs no quinella place pool below seven declared starters. Race 2
    of the recorded meeting has six, and no QPL pool exists for it. That is a
    fact about the race, and calling it a failed capture would put a warning on
    the strip every fifteen minutes for a market that is behaving normally."""
    snaps = {s["race_no"]: s for s in odds_ingest.fetch_meeting(DATE, VENUE)}
    assert snaps[2]["n_runners"] == 6
    assert snaps[2]["qpl_odds"] == []
    assert snaps[2]["qin_odds"]
    assert snaps[2]["notes"] == []


def test_a_market_that_has_not_opened_says_so(endpoint, monkeypatch):
    """A declared card returns every pool with nothing in it. Silence would be
    indistinguishable from a capture that broke."""
    _, replies = endpoint
    replies["racing"] = _fixture("odds_pools_predeclared")
    snaps = odds_ingest.fetch_meeting(PRE_DATE, PRE_VENUE)
    assert snaps and all(s["n_runners"] == 0 for s in snaps)
    assert all(any("market not open" in n for n in s["notes"]) for s in snaps)


# ─── the guard that replaced the stale-DOM check ──────────────────────────────

def test_a_reply_about_another_meeting_is_refused(endpoint):
    """Measured against the live endpoint: `raceMeetings(date:, venueCode:)`
    does not answer "no such meeting". Asked on 2026-09-04 for 2026-09-08 ST
    it returned the 2026-09-05 S1 card, with nothing to say the filter had
    been ignored. Storing that files one meeting's prices under another's race
    numbers, in the table nothing prunes."""
    with pytest.raises(odds_ingest.OddsError, match="lists no meeting"):
        odds_ingest.fetch_meeting("2026-09-08", "ST")


def test_a_single_mismatched_pool_refuses_the_whole_capture(endpoint):
    """Keeping the races that did match would look like a complete capture."""
    _, replies = endpoint
    pools = replies["racing"]["data"]["raceMeetings"][0]["pmPools"]
    tampered = json.loads(json.dumps(replies["racing"]))
    tampered["data"]["raceMeetings"][0]["pmPools"][2]["id"] = "20260906S2WIN4"
    replies["racing"] = tampered
    with pytest.raises(odds_ingest.OddsError, match="different meeting"):
        odds_ingest.fetch_meeting(DATE, VENUE)
    assert pools          # the untampered fixture is still what it was


def test_a_multi_leg_pool_raises_rather_than_picking_a_race(endpoint):
    """A double spans two races and has no single race to file under. None is
    requested, so one arriving means the reply changed shape."""
    _, replies = endpoint
    tampered = json.loads(json.dumps(replies["racing"]))
    tampered["data"]["raceMeetings"][0]["pmPools"][0]["leg"]["races"] = [1, 2]
    replies["racing"] = tampered
    with pytest.raises(odds_ingest.OddsError, match="spans races"):
        odds_ingest.fetch_meeting(DATE, VENUE)


def test_a_reply_with_no_pools_key_raises(endpoint):
    """The corunning lesson: a parser that cannot find its shape must say so."""
    _, replies = endpoint
    replies["racing"] = {"data": {"raceMeetings": [{}]}}
    with pytest.raises(odds_ingest.OddsError, match="no pmPools"):
        odds_ingest.fetch_meeting(DATE, VENUE)


def test_graphql_errors_are_not_read_as_an_empty_market(monkeypatch):
    """The endpoint answers 200 with an `errors` key. A caller that checked
    only the status code would store a successful capture of nothing."""
    class Resp:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"errors": [{"message": "Internal server error - WHITELIST_ERROR"}]}

    monkeypatch.setattr(_client, "_throttle", lambda: None)
    monkeypatch.setattr(_client, "get_session",
                        lambda: type("S", (), {"post": lambda *a, **k: Resp()})())
    with pytest.raises(_client.FetchError, match="WHITELIST_ERROR"):
        _client.fetch_json("https://example.invalid", {})


def test_the_queries_are_sent_verbatim():
    """The endpoint whitelists queries: a valid one it has not seen is refused
    with WHITELIST_ERROR. Reformatting either of these — even dropping a field
    nothing reads — breaks the capture, so they are copies of what the site's
    own bundle sends and must stay that way."""
    for query in (odds_ingest._MEETINGS_QUERY, odds_ingest._POOLS_QUERY):
        assert query.startswith("query ")
        assert "\n  " in query            # two-space indent, as printed by the site
        assert "\t" not in query
    assert "bankerOdds" in odds_ingest._POOLS_QUERY   # unread, and still sent


# ─── the job ──────────────────────────────────────────────────────────────────
#
# `store/` accepts only Hong Kong venues, and the one market open for betting
# when these fixtures were captured was a simulcast. So the job tests relabel
# the recorded meeting S1 → ST: the venue code in the probe and the venue
# segment of every pool id, and nothing else. Every price below is the one
# HKJC published, and the relabel is applied to BOTH replies together, so the
# meeting-id check is still doing real work rather than being sidestepped.

HK_VENUE = "ST"


def _relabelled(reply: dict, *, name: str) -> dict:
    text = json.dumps(reply)
    if name == "odds_meetings":
        text = text.replace('"venueCode": "S1"', f'"venueCode": "{HK_VENUE}"')
    return json.loads(text.replace(f"{DATE.replace('-', '')}S1",
                                   f"{DATE.replace('-', '')}{HK_VENUE}"))


@pytest.fixture()
def hk_endpoint(monkeypatch):
    calls: list[dict] = []
    replies = {n: _relabelled(_fixture(n), name=n)
               for n in ("odds_meetings", "odds_pools_live")}
    by_op = {"racingChanges": "odds_meetings", "racing": "odds_pools_live"}

    def fake(url, payload, **kw):
        calls.append(payload)
        return replies[by_op[payload["operationName"]]]

    monkeypatch.setattr(odds_ingest, "fetch_json", fake)
    return calls, replies


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "odds.db"
    conn = get_conn(path)
    init_db(conn)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": n, "venue": HK_VENUE, "course": "A",
             "surface": "Turf", "going": "G", "distance": 1800}
            for n in (1, 2, 4)])
    conn.close()
    return path


def test_the_job_stores_snapshots_and_reports_counts(db, hk_endpoint):
    report = scrape_odds.run(DATE, HK_VENUE, db=str(db))
    assert report.races == 3
    assert report.win_place == 29           # 9 + 6 + 14 declared runners
    assert report.pairs == 36 + 36 + 15 + 91 + 91
    assert "3 races" in report.line()

    conn = get_conn(db)
    try:
        stored = conn.execute(
            "SELECT count(*) FROM odds_snapshots").fetchone()[0]
        priced = conn.execute(
            "SELECT count(*) FROM odds_snapshots "
            "WHERE win_odds IS NOT NULL").fetchone()[0]
    finally:
        conn.close()
    assert stored == 29 and priced == 26     # three scratchings, still declared


def test_one_capture_gives_the_whole_meeting_one_timestamp(db, hk_endpoint):
    """`latest_prices` reads max(captured_at) and then everything AT it. A
    timestamp per race would split one capture across three of them."""
    scrape_odds.run(DATE, HK_VENUE, db=str(db))
    conn = get_conn(db)
    try:
        stamps = [r[0] for r in conn.execute(
            "SELECT DISTINCT captured_at FROM odds_snapshots")]
    finally:
        conn.close()
    assert len(stamps) == 1


def test_a_refused_capture_stores_nothing_and_says_why(db, hk_endpoint):
    """Not a traceback: the next run is fifteen minutes away, and the strip
    has to be able to show the reason."""
    _, replies = hk_endpoint
    replies["odds_pools_live"]["data"]["raceMeetings"][0]["pmPools"][0]["id"] = (
        "20260906S2PLA1")

    report = scrape_odds.run(DATE, HK_VENUE, db=str(db))
    assert report.races == 0
    assert any("different meeting" in s for s in report.skipped)
    assert "different meeting" in report.line()

    conn = get_conn(db)
    try:
        assert conn.execute(
            "SELECT count(*) FROM odds_snapshots").fetchone()[0] == 0
        run = conn.execute("SELECT ok, detail FROM job_runs "
                           "WHERE job = 'scrape_odds'").fetchone()
    finally:
        conn.close()
    assert run["ok"] == 0 and "different meeting" in run["detail"]


def test_a_zero_never_reports_itself_as_a_bare_zero(db, hk_endpoint):
    """A row of zeros is the shape a silent failure and a quiet market share."""
    _, replies = hk_endpoint
    replies["odds_pools_live"] = _fixture("odds_pools_predeclared")
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": PRE_DATE, "race_no": n, "venue": PRE_VENUE,
             "course": "A", "surface": "Turf", "going": "G", "distance": 1200}
            for n in (1, 3)])
    conn.close()

    report = scrape_odds.run(PRE_DATE, PRE_VENUE, db=str(db))
    assert report.races == 0
    assert "market not open" in report.line()
    assert not report.skipped          # a market that has not opened is not a
                                       # skipped race, it is a quiet one


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


def test_the_capture_path_needs_no_browser():
    """A browser is the reason the cron line spent a season commented out: the
    deploy image carries none, so a capture that needs one cannot be automated.

    Checked against the imports rather than the file text, so the comment that
    explains why there is no browser does not trip the test that enforces it.
    """
    import ast

    banned = {"playwright", "selenium", "webdriver"}
    for module in (scrape_odds, odds_ingest):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0].lower() not in banned, (
                    f"{module.__name__} imports {name}")


# ─── against the real archive ─────────────────────────────────────────────────

LEGACY = Path("/home/user/hk_race_dashboard/cache/live_odds")


@pytest.mark.skipif(not LEGACY.is_dir(), reason="legacy odds cache not present")
def test_recorded_snapshots_still_parse():
    """The 17 meetings that survived pruning are the regression corpus.

    The capture builds the same payload these files hold — that shape survived
    the move off Playwright deliberately, so `jobs/import_legacy_odds` did not
    have to change and the archive stays readable.
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
    """Most days have no meeting. The job must no-op without a single request."""
    import datetime as dt

    def explode(*a, **kw):
        raise AssertionError("must not reach HKJC with nothing to price")

    monkeypatch.setattr(odds_ingest, "fetch_json", explode)
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


# ─── the cadence ladder ───────────────────────────────────────────────────────
#
# The money arrives in the last five to ten minutes. A flat schedule spends
# most of its rows recording that nothing happened overnight and then samples
# the only interesting window three times.

def test_the_ladder_tightens_towards_the_off():
    """Every band must be at least as fine as the one before it, or a race
    gets sampled less often as its price starts moving."""
    intervals = [scrape_odds._interval_for(t)
                 for t in (1440, 300, 120, 40, 20, 8, 0, -20)]
    assert intervals == [60, 60, 15, 15, 5, 1, 1, 1]
    assert intervals == sorted(intervals, reverse=True)


def test_the_final_ten_minutes_are_captured_every_minute():
    """The window the user named: late money does not show before it."""
    assert scrape_odds._interval_for(10.0) == 1
    assert scrape_odds._interval_for(0.0) == 1
    # And through the scheduled off, because a delayed start is real and the
    # price that settles the bet is the one at the ACTUAL off.
    assert scrape_odds._interval_for(-15.0) == 1


def test_pairs_are_sampled_coarsely_overnight_and_never_below_five_minutes():
    """A 14-runner field has 14 win prices and 182 pair prices. Measured on
    disk at 106 bytes a row, pairs on the win ladder would be 0.93 GB a season
    against a 1 GB volume, and nothing here ever deletes."""
    pair = scrape_odds.PAIR_CADENCE_MINUTES
    assert scrape_odds._interval_for(1440.0, pair) == 180      # not 60
    assert scrape_odds._interval_for(5.0, pair) == 5           # not 1
    # Never finer than the win ladder, at any distance.
    for t in (1440, 300, 120, 40, 20, 8, 0, -20):
        assert (scrape_odds._interval_for(float(t), pair)
                >= scrape_odds._interval_for(float(t)))


def test_a_card_with_no_off_time_falls_to_the_coarsest_band():
    """Not the finest. A card without times must not be priced every minute
    all day because a column happened to be empty."""
    assert scrape_odds._interval_for(None) == 60


def test_a_race_never_captured_is_always_due():
    now = __import__("datetime").datetime(2026, 9, 6, 12, 0)
    assert scrape_odds._due(None, 60, now) is True
    assert scrape_odds._due("not a timestamp", 60, now) is True


def test_a_recent_capture_holds_the_next_one_off():
    import datetime as dt
    now = dt.datetime(2026, 9, 6, 12, 0)
    assert scrape_odds._due("2026-09-06T11:59:00", 15, now) is False
    assert scrape_odds._due("2026-09-06T11:44:00", 15, now) is True
    # Cron fires on the minute and the last capture landed a second into it,
    # so an exact-interval gap must still count as due or every other tick is
    # silently skipped.
    assert scrape_odds._due("2026-09-06T11:44:58", 15, now) is True


def test_a_tick_with_nothing_due_never_reaches_hkjc(db, monkeypatch):
    """This is what makes a one-minute cron affordable: ~1,100 of 1,500 ticks
    over a meeting's cycle answer from the database alone."""
    import datetime as dt

    def explode(*a, **kw):
        raise AssertionError("must not reach HKJC with nothing due")

    # A capture a minute ago, and the races are hours from their off.
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": n, "horse_no": 1,
             "captured_at": "2026-09-05T09:59:00", "win_odds": 3.0}
            for n in (1, 2, 4)])
        upsert.upsert_odds_pairs(conn, [
            {"race_date": DATE, "race_no": n, "pool": "QIN", "horse_a": 1,
             "horse_b": 2, "captured_at": "2026-09-05T09:59:00", "odds": 9.0}
            for n in (1, 2, 4)])
        conn.execute("UPDATE races SET off_time = '18:00' WHERE race_date = ?",
                     (DATE,))
    conn.close()

    monkeypatch.setattr(odds_ingest, "fetch_json", explode)
    report = scrape_odds.run(db=str(db), today=dt.date(2026, 9, 5),
                             now=dt.datetime(2026, 9, 5, 10, 0))
    assert report.attempted == 0
    assert "none due a capture yet" in report.notes[0]
    # And it says which of the two quiet outcomes this is.
    assert "past their off time" not in report.notes[0]


def test_a_race_close_to_its_off_is_due_every_minute(db, hk_endpoint):
    """One request brings back the whole card; the ladder decides what to
    keep. A race a minute from the off writes a win price and, because pairs
    are floored at five minutes, may write no pair price."""
    import datetime as dt

    conn = get_conn(db)
    with transaction(conn):
        conn.execute("UPDATE races SET off_time = '10:05' WHERE race_date = ?",
                     (DATE,))
        upsert.upsert_odds_snapshots(conn, [
            {"race_date": DATE, "race_no": n, "horse_no": 1,
             "captured_at": "2026-09-05T09:59:00", "win_odds": 3.0}
            for n in (1, 2, 4)])
        upsert.upsert_odds_pairs(conn, [
            {"race_date": DATE, "race_no": n, "pool": "QIN", "horse_a": 1,
             "horse_b": 2, "captured_at": "2026-09-05T09:59:00", "odds": 9.0}
            for n in (1, 2, 4)])
    conn.close()

    report = scrape_odds.run(db=str(db), today=dt.date(2026, 9, 5),
                             now=dt.datetime(2026, 9, 5, 10, 0))
    assert report.win_place > 0          # a minute on, and due again
    assert report.pairs == 0             # one minute is inside the pair floor


def test_the_day_before_is_priced_once_todays_races_are_done(db, hk_endpoint):
    """HKJC opens a market at 13:00 the DAY BEFORE racing. Without this the
    first race-day tick at noon finds a price that has already been moving for
    23 hours, and race 1 goes off at 12:30."""
    import datetime as dt

    conn = get_conn(db)
    with transaction(conn):
        # Today has nothing; the meeting in the fixture is tomorrow.
        conn.execute("UPDATE races SET off_time = '12:30' WHERE race_date = ?",
                     (DATE,))
    conn.close()

    report = scrape_odds.run(db=str(db), today=dt.date(2026, 9, 4),
                             now=dt.datetime(2026, 9, 4, 14, 0))
    assert report.race_date == DATE      # tomorrow, priced today
    assert report.races == 3


def test_with_no_meeting_on_either_day_the_report_names_today(db):
    """The commonest line in the log must not be about a date nobody asked
    about."""
    import datetime as dt
    report = scrape_odds.run(db=str(db), today=dt.date(2026, 7, 20),
                             now=dt.datetime(2026, 7, 20, 14, 0))
    assert report.race_date == "2026-07-20"
    assert "nothing to price" in report.line()


def test_quiet_silences_only_the_idle_tick(tmp_path, db, endpoint, capsys):
    """The every-minute cron line would otherwise put ~1,400 lines a day of
    "nothing to price" into the log and bury the runs that captured something.

    What it must NOT silence is a zero with something attempted, or a skip.
    Silent success and silent failure looking the same is the failure this
    whole rebuild exists to remove.
    """
    # No meeting on any date: the shape of ~1,400 ticks a day.
    idle_db = tmp_path / "idle.db"
    assert scrape_odds.main(["--db", str(idle_db), "--quiet"]) == 0
    assert capsys.readouterr().out == ""          # nothing to do, nothing said
    # Without --quiet the same tick still explains itself, for a person.
    scrape_odds.main(["--db", str(idle_db)])
    assert "nothing to price" in capsys.readouterr().out

    # A refused capture has plenty to say, quiet or not.
    _, replies = endpoint
    tampered = json.loads(json.dumps(replies["racing"]))
    tampered["data"]["raceMeetings"][0]["pmPools"][0]["id"] = "20260906S2PLA1"
    replies["racing"] = tampered
    scrape_odds.main(["--db", str(db), "--date", DATE, "--venue", VENUE,
                      "--quiet"])
    said = capsys.readouterr().out
    assert "SKIPPED" in said and "different meeting" in said


# ─── stopping when the race is actually over ─────────────────────────────────
#
# The capture used to run on the clock alone: thirty minutes past the SCHEDULED
# off and then stop. That is ~30 dead win/place captures and ~6 dead pair
# captures per race — several thousand rows a meeting recording a market that
# could no longer move — and it is wrong in the other direction too, because a
# delayed start moves the real close and the clock does not know.
#
# HKJC's own `sellStatus` does know. These pin the three states apart, because
# conflating any two of them either fills the table or loses the late money.

def _stop_selling(reply: dict) -> None:
    for pool in reply["data"]["raceMeetings"][0]["pmPools"]:
        pool["status"] = pool["sellStatus"] = "STOP_SELL"


def _closed(db) -> dict[int, str]:
    conn = get_conn(db)
    try:
        return {r["race_no"]: r["status"] for r in conn.execute(
            "SELECT race_no, status FROM market_close")}
    finally:
        conn.close()


def test_a_selling_market_is_never_recorded_as_closed(db, hk_endpoint):
    report = scrape_odds.run(DATE, HK_VENUE, db=str(db))
    assert report.closed == []
    assert _closed(db) == {}


def test_the_pool_shutting_is_what_stops_the_capture(db, hk_endpoint):
    """Not the clock. The close is recorded the first tick that sees it."""
    _, replies = hk_endpoint
    scrape_odds.run(DATE, HK_VENUE, db=str(db))       # a normal capture first
    _stop_selling(replies["odds_pools_live"])

    report = scrape_odds.run(DATE, HK_VENUE, db=str(db))
    assert sorted(report.closed) == [1, 2, 4]
    assert _closed(db) == {1: "STOP_SELL", 2: "STOP_SELL", 4: "STOP_SELL"}
    assert "market shut on R1, R2, R4" in report.line()


def test_a_closed_race_is_not_asked_about_again(db, hk_endpoint):
    """The whole point: a meeting that finished at six costs nothing for the
    rest of the evening, rather than a request a minute per race."""
    calls, replies = hk_endpoint
    scrape_odds.run(DATE, HK_VENUE, db=str(db))
    _stop_selling(replies["odds_pools_live"])
    scrape_odds.run(DATE, HK_VENUE, db=str(db))
    before = len(calls)

    # Unattended, as cron runs it — no explicit date, so the ladder and the
    # closed-race list both apply.
    report = scrape_odds.run(db=str(db), today=dt.date.fromisoformat(DATE))
    assert report.attempted == 0
    assert len(calls) == before, "a closed meeting reached the network"


def test_a_market_that_has_not_opened_is_not_a_finished_race(db, hk_endpoint):
    """Both look identical in the pool status — DEFINED / STOP_SELL, no prices.

    The recorded pre-declared reply IS that state. Treating it as an ending
    would stop the capture before the market ever opened, which is the whole
    overnight move and the one thing here that cannot be reconstructed after
    the fact.
    """
    _, replies = hk_endpoint
    replies["odds_pools_live"] = _fixture("odds_pools_predeclared")
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": PRE_DATE, "race_no": n, "venue": PRE_VENUE,
             "course": "A", "surface": "Turf", "going": "G", "distance": 1200}
            for n in (1, 3)])
    conn.close()

    report = scrape_odds.run(PRE_DATE, PRE_VENUE, db=str(db))
    assert report.closed == []
    assert _closed(db) == {}


def test_a_pool_that_stops_before_the_off_is_a_suspension_not_an_ending(
        db, hk_endpoint):
    """A market withdrawn and restored mid-afternoon must not end the race.

    Dropping a race for good over a blip would lose exactly the window the
    ladder exists to sample.
    """
    _, replies = hk_endpoint
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": DATE, "race_no": n, "venue": HK_VENUE, "course": "A",
             "surface": "Turf", "going": "G", "distance": 1800,
             "off_time": "14:00"} for n in (1, 2, 4)])
    conn.close()

    scrape_odds.run(DATE, HK_VENUE, db=str(db),
                    now=dt.datetime.fromisoformat(f"{DATE}T13:30:00"))
    _stop_selling(replies["odds_pools_live"])
    report = scrape_odds.run(DATE, HK_VENUE, db=str(db),
                             now=dt.datetime.fromisoformat(f"{DATE}T13:30:00"))
    assert report.closed == []

    # Past the off, the same status means what it says.
    after = scrape_odds.run(DATE, HK_VENUE, db=str(db),
                            now=dt.datetime.fromisoformat(f"{DATE}T14:02:00"))
    assert sorted(after.closed) == [1, 2, 4]


def test_a_shut_pool_with_no_prices_is_not_reported_as_a_failed_capture(
        db, hk_endpoint):
    """A race that has been run comes back empty, and so does a broken scrape.

    They must not share a line: one is the expected end of the day and the
    other is the thing the freshness strip exists to go amber for.
    """
    _, replies = hk_endpoint
    scrape_odds.run(DATE, HK_VENUE, db=str(db))
    _stop_selling(replies["odds_pools_live"])
    for pool in replies["odds_pools_live"]["data"]["raceMeetings"][0]["pmPools"]:
        pool["oddsNodes"] = []

    report = scrape_odds.run(DATE, HK_VENUE, db=str(db))
    assert report.skipped == []
    assert sorted(report.closed) == [1, 2, 4]

    conn = get_conn(db)
    try:
        run = conn.execute("SELECT ok FROM job_runs WHERE job = 'scrape_odds' "
                           "ORDER BY rowid DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert run["ok"] == 1, "recording a close is an outcome, not a miss"
