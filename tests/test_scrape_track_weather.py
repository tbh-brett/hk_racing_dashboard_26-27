"""jobs/scrape_track_weather: when it asks HKJC, what it stores, what it says.

The wind is live only, so the question that matters is the schedule's: a tick
inside a meeting must capture, and a tick on any other minute of the week must
not reach the network at all, because it runs every five minutes, all week.
The going readings are kept for about two months, so their question is the
backfill's: ask once per meeting date, ask again only where a reading may
still land, and never ask about a meeting already recorded.

The network is faked at `ingest.track_weather.fetch_json`, the one function
both queries go through. Replies are the recorded fixtures.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from hkrd.ingest import _client, track_weather
from hkrd.jobs import clock, scrape_track_weather as job
from hkrd.store import job_log, upsert
from hkrd.store.connect import get_conn, init_db, transaction

FIXTURES = Path(__file__).parent / "fixtures"
CRONTAB = Path(__file__).resolve().parent.parent / "ops" / "crontab"

# The recorded weather reply was taken at 23:34:09 on Monday 23 Nov 2026. A
# card for that date with races at 22:30 and 23:10 puts that moment inside
# its window (21:30 to 23:40), so the fixture's readings are minutes old.
DAY = "2026-11-23"
NOW = dt.datetime(2026, 11, 23, 23, 34, 9)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = get_conn(path)
    init_db(conn)
    conn.close()
    return path


def _card(db, date: str, offs, venue: str = "HV") -> None:
    conn = get_conn(db)
    with transaction(conn):
        upsert.upsert_races(conn, [
            {"race_date": date, "race_no": i, "venue": venue, "off_time": off}
            for i, off in enumerate(offs, 1)])
    conn.close()


def _meeting(date: str, venue: str = "HV", times=("08:00", "17:40")) -> dict:
    return {"date": date, "venueCode": venue, "hammerReadings": [],
            "penetrometerReadings": [
                {"reading": "2.72", "readingTime": f"{date}T{t}:00+08:00"}
                for t in times]}


@pytest.fixture()
def endpoint(monkeypatch):
    """Every request answered from recordings, and logged. The summary for a
    date answers from `summaries`, or with no meeting at all."""
    calls: list[dict] = []
    summaries: dict[str, list[dict]] = {
        "2026-10-04": _fixture("track_going_20261004")["data"]["commonMeetings"]}
    weather = {"body": _fixture("track_weather_live")}

    def fake(url, payload, **kw):
        calls.append(payload)
        if payload["query"] == track_weather.WEATHER_QUERY:
            return weather["body"]
        (date,) = payload["variables"]["dates"]
        return {"data": {"commonMeetings": summaries.get(date, [])}}

    monkeypatch.setattr(track_weather, "fetch_json", fake)
    return calls, summaries, weather


def _rows(db, table: str) -> int:
    conn = get_conn(db)
    try:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _last(db, name: str):
    conn = get_conn(db)
    try:
        return job_log.last_run(conn, name)
    finally:
        conn.close()


# ─── the window ───────────────────────────────────────────────────────────────

def test_the_window_runs_from_an_hour_before_race_one_to_half_an_hour_after_the_last():
    start, end = job.window(DAY, ["22:30", None, "23:10", "TBA"])
    assert start == dt.datetime(2026, 11, 23, 21, 30)
    assert end == dt.datetime(2026, 11, 23, 23, 40)
    assert job.window(DAY, [None, "", "TBA"]) is None


def test_a_day_with_no_card_never_reaches_hkjc(db, endpoint):
    """Most ticks of most days. One local query, no request, no job_runs row:
    nothing happened, and nothing is recorded as having happened."""
    calls, _, _ = endpoint
    report = job.capture(db, now=NOW)
    assert not report.attempted and calls == []
    assert report.idle == f"no meeting stored for {DAY}"
    assert _last(db, job.JOB) is None


@pytest.mark.parametrize("at, captured", [
    (dt.datetime(2026, 11, 23, 21, 29), False),
    (dt.datetime(2026, 11, 23, 21, 30), True),
    (dt.datetime(2026, 11, 23, 23, 40), True),
    (dt.datetime(2026, 11, 23, 23, 41), False),
])
def test_a_tick_captures_only_inside_the_meeting(db, endpoint, at, captured):
    calls, _, _ = endpoint
    _card(db, DAY, ["22:30", "23:10"])
    report = job.capture(db, now=at)
    assert report.attempted is captured
    assert len(calls) == int(captured)
    if not captured:
        assert "outside the 2026-11-23 HV meeting (21:30 to 23:40)" \
            in report.idle


def test_a_tick_inside_the_meeting_stores_both_courses_and_says_so(db,
                                                                    endpoint):
    _card(db, DAY, ["22:30", "23:10"])
    report = job.capture(db, now=NOW)
    assert report.ok and report.rows == 10
    assert report.by_venue == {"HV": 5, "ST": 5}
    assert report.line() == ("2026-11-23 23:34 track weather (HV meeting): "
                             "10 readings · HV 5 · ST 5")
    assert _rows(db, "track_weather") == 10
    last = _last(db, job.JOB)
    assert last["ok"] is True and "10 readings" in last["detail"]


def test_the_default_clock_is_hong_kong_whatever_the_machine_says(
        db, endpoint, monkeypatch):
    """15:34 UTC is 23:34 in Hong Kong: inside the meeting, not hours off it."""
    _card(db, DAY, ["22:30", "23:10"])
    monkeypatch.setattr(clock, "_clock", lambda: dt.datetime(
        2026, 11, 23, 15, 34, 9, tzinfo=dt.timezone.utc))
    report = job.capture(db)
    assert report.attempted and report.at == "2026-11-23T23:34:09"


def test_a_card_without_off_times_is_captured_rather_than_missed(db,
                                                                 endpoint):
    _card(db, DAY, [None, None])
    report = job.capture(db, now=dt.datetime(2026, 11, 23, 9, 0))
    assert report.attempted and report.rows == 10
    assert any("no off time readable" in n for n in report.notes)


def test_force_captures_with_no_meeting_at_all(db, endpoint):
    report = job.capture(db, now=NOW, force=True)
    assert report.ok and report.rows == 10
    assert report.line().startswith("2026-11-23 23:34 track weather: 10 ")


def test_a_capture_repeated_before_the_sensors_report_adds_nothing(db,
                                                                   endpoint):
    _card(db, DAY, ["22:30", "23:10"])
    job.capture(db, now=NOW)
    job.capture(db, now=NOW + dt.timedelta(minutes=5))
    assert _rows(db, "track_weather") == 10


def test_a_sensor_that_has_stopped_reporting_is_named(db, endpoint):
    """Ten rows look the same whether every sensor is live or one has been
    repeating a reading from half an hour ago."""
    _, _, weather = endpoint
    weather["body"]["data"]["weather"][0]["sectional"][3]["time"] = "23:02:58.2"
    _card(db, DAY, ["22:30", "23:10"])
    report = job.capture(db, now=NOW)
    stale = [n for n in report.notes if "min before this capture" in n]
    assert stale == ["HV/D last read at 2026-11-23 23:02, 31 min before this "
                     "capture"]


def test_a_failed_fetch_is_recorded_and_exits_non_zero(db, monkeypatch,
                                                       capsys):
    def refuse(*a, **k):
        raise _client.FetchError("returned errors: WHITELIST_ERROR")

    monkeypatch.setattr(track_weather, "fetch_json", refuse)
    monkeypatch.setattr(clock, "_clock", lambda: dt.datetime(
        2026, 11, 23, 15, 34, 9, tzinfo=dt.timezone.utc))
    _card(db, DAY, ["22:30", "23:10"])
    assert job.main(["--db", str(db), "--quiet"]) == 1
    out = capsys.readouterr().out
    assert "SKIPPED" in out and "WHITELIST_ERROR" in out
    last = _last(db, job.JOB)
    assert last["ok"] is False and "WHITELIST_ERROR" in last["detail"]
    assert _rows(db, "track_weather") == 0


def test_an_idle_quiet_tick_prints_nothing_and_succeeds(db, endpoint, capsys,
                                                         monkeypatch):
    monkeypatch.setattr(clock, "_clock", lambda: dt.datetime(
        2026, 11, 23, 15, 34, 9, tzinfo=dt.timezone.utc))
    assert job.main(["--db", str(db), "--quiet"]) == 0
    assert capsys.readouterr().out == ""
    assert job.main(["--db", str(db)]) == 0
    assert "no meeting stored" in capsys.readouterr().out


def test_date_goes_with_going_only(db):
    with pytest.raises(SystemExit):
        job.main(["--db", str(db), "--date", "2026-11-22"])
    with pytest.raises(SystemExit):
        job.main(["--db", str(db), "--going", "--force"])


# ─── the going readings ───────────────────────────────────────────────────────

TODAY = dt.date(2026, 11, 23)


def test_the_first_run_backfills_and_later_runs_ask_only_what_can_change(
        db, endpoint):
    """Stored meetings: one past the lookback, two inside it with nothing on
    record, yesterday's, and Wednesday's card not yet raced. The first run asks
    the three inside, newest first, one request each; the second asks only
    yesterday's, where a late reading could still land."""
    calls, summaries, _ = endpoint
    for date in ("2026-09-20", "2026-10-04", "2026-11-18", "2026-11-22",
                 "2026-11-25"):
        _card(db, date, ["19:00"], venue="ST" if date == "2026-10-04" else "HV")
    summaries["2026-11-18"] = [_meeting("2026-11-18")]
    summaries["2026-11-22"] = [_meeting("2026-11-22", "ST", ("08:00",))]

    first = job.going(db, today=TODAY)
    assert first.asked == ["2026-11-22", "2026-11-18", "2026-10-04"]
    assert [c["variables"] for c in calls] == [
        {"dates": ["2026-11-22"]}, {"dates": ["2026-11-18"]},
        {"dates": ["2026-10-04"]}]
    assert first.by_date == {"2026-11-22": 1, "2026-11-18": 2,
                             "2026-10-04": 4}
    assert first.ok and _rows(db, "going_readings") == 7

    summaries["2026-11-22"] = [_meeting("2026-11-22", "ST")]   # 11:30 landed
    calls.clear()
    second = job.going(db, today=TODAY)
    assert second.asked == ["2026-11-22"]
    assert len(calls) == 1
    assert _rows(db, "going_readings") == 8
    last = _last(db, job.GOING_JOB)
    assert last["ok"] is True and "2026-11-22 2" in last["detail"]


def test_a_meeting_hkjc_no_longer_serves_is_a_note_not_a_failure(db,
                                                                 endpoint):
    _card(db, "2026-09-23", ["19:00"])
    report = job.going(db, today=TODAY)
    assert report.asked == ["2026-09-23"] and report.ok and report.rows == 0
    assert report.notes == ["2026-09-23: HKJC returned no Sha Tin or Happy "
                            "Valley meeting"]
    assert report.line() == ("going readings: 0 from 1 meeting date · "
                             "2026-09-23 0")


def test_a_day_with_nothing_outstanding_asks_nothing(db, endpoint, capsys):
    calls, _, _ = endpoint
    report = job.going(db, today=TODAY)
    assert report.asked == [] and calls == [] and report.ok
    assert _last(db, job.GOING_JOB) is None
    assert job.main(["--db", str(db), "--going", "--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_named_dates_are_asked_whatever_is_stored(db, endpoint, capsys):
    calls, _, _ = endpoint
    assert job.main(["--db", str(db), "--going", "--date", "2026-10-04"]) == 0
    assert [c["variables"] for c in calls] == [{"dates": ["2026-10-04"]}]
    assert "going readings: 4 from 1 meeting date" in capsys.readouterr().out


def test_one_failed_date_does_not_lose_the_others(db, endpoint, monkeypatch):
    calls, summaries, _ = endpoint
    real = track_weather.fetch_json

    def flaky(url, payload, **kw):
        if payload["variables"] == {"dates": ["2026-11-18"]}:
            raise _client.FetchError("HTTP 502")
        return real(url, payload, **kw)

    monkeypatch.setattr(track_weather, "fetch_json", flaky)
    report = job.going(db, dates=["2026-11-18", "2026-10-04"])
    assert report.by_date == {"2026-10-04": 4}
    assert report.skipped and "2026-11-18" in report.skipped[0]
    assert not report.ok
    assert _last(db, job.GOING_JOB)["ok"] is False


# ─── the schedule ─────────────────────────────────────────────────────────────

def test_the_crontab_arms_both_halves_against_the_volume():
    """The wind every five minutes, the job deciding; the going readings three
    times a day. Both at nice 19 and both naming the database on the volume,
    which every line here does twice over (see the note atop the crontab)."""
    lines = [ln for ln in CRONTAB.read_text(encoding="utf-8").splitlines()
             if "hkrd.jobs.scrape_track_weather" in ln
             and not ln.lstrip().startswith("#")]
    assert len(lines) == 2
    tick = next(ln for ln in lines if "--going" not in ln)
    reads = next(ln for ln in lines if "--going" in ln)
    assert tick.split()[:5] == ["4-59/5", "*", "*", "*", "*"]
    assert reads.split()[:5] == ["35", "12,19,23", "*", "*", "*"]
    for line in lines:
        assert "nice -n 19" in line and "--db /data/hkrd.db" in line
        assert "--quiet" in line
