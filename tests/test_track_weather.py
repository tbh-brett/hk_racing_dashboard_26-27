"""HKJC's track weather and going readings: reading the replies, and writing them.

The JSON is not invented. Two fixtures are replies from the live endpoint:

  track_weather_live.json     `query Weather` at 23:34 HK on 23 Nov 2026, a
                              Monday with no meeting: both courses, four
                              anemometers and a station each, verbatim
  track_going_20261004.json   `query rw_Summary` for 4 Oct 2026, a Sha Tin
                              meeting on both tracks, with the S4 simulcast
                              the same date returns. Trimmed only of the
                              pools, suspensions and stewards nothing reads

What is worth pinning is what would fail quietly: a direction read the wrong
way round, a station-only column read as zero on an anemometer, a reply about
the wrong meeting stored under the asked one, and a query "tidied" into one
the endpoint refuses.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hkrd.ingest import _client, going, track_weather
from hkrd.ingest.track_weather import TrackWeatherError
from hkrd.store import coerce
from hkrd.store import track_weather as store
from hkrd.store.connect import get_conn, init_db, transaction

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURED = "2026-11-23T23:34:09"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _weather(body: dict | None = None):
    if body is None:
        body = _fixture("track_weather_live")
    return track_weather.weather_rows(body, captured_at=CAPTURED)


def _by_key(rows):
    return {(r["venue"], r["location"]): r for r in rows}


@pytest.fixture()
def conn(tmp_path):
    c = get_conn(tmp_path / "t.db")
    init_db(c)
    yield c
    c.close()


# ─── the queries ──────────────────────────────────────────────────────────────

def test_the_queries_are_the_sites_own_byte_for_byte():
    """The endpoint runs only whitelisted queries. On 23 Nov 2026 the Weather
    text with one unread field removed came back WHITELIST_ERROR. These are
    the digests of the two literals as the site's bundle carried them that
    day; a mismatch means the string was edited here, and it can only be put
    right by reading the bundle again, not by tidying."""
    assert hashlib.sha256(track_weather.WEATHER_QUERY.encode()).hexdigest() \
        == "f793cb5f7b61dc9172faee5c11df7805f75446c89439d79b9da8a625ea706d58"
    assert hashlib.sha256(going.SUMMARY_QUERY.encode()).hexdigest() \
        == "1dfde381dd90a72293e08a2bb48c52feac5b4fe78e02f1a665ad42c1d921cdc0"


def test_the_request_is_shaped_as_the_site_shapes_it(monkeypatch):
    """`{variables, query}` and no operationName, to the endpoint the page
    names: the summary with one date in its list, the weather with none."""
    sent = []

    def fake(url, payload, **kw):
        sent.append((url, payload))
        if payload["query"] == track_weather.WEATHER_QUERY:
            return _fixture("track_weather_live")
        return _fixture("track_going_20261004")

    monkeypatch.setattr(track_weather, "fetch_json", fake)
    track_weather.fetch_weather(captured_at=CAPTURED)
    going.fetch_going("2026-10-04")
    assert [url for url, _ in sent] == [_client.urls.graphql] * 2
    assert sent[0][1] == {"variables": None,
                          "query": track_weather.WEATHER_QUERY}
    assert sent[1][1] == {"variables": {"dates": ["2026-10-04"]},
                          "query": going.SUMMARY_QUERY}


def test_a_failed_fetch_is_a_track_weather_error(monkeypatch):
    """GraphQL answers 200 with an `errors` key; `fetch_json` raises on it,
    and the job catches one exception type for every way a capture fails."""
    def refuse(*a, **k):
        raise _client.FetchError("returned errors: WHITELIST_ERROR")

    monkeypatch.setattr(track_weather, "fetch_json", refuse)
    with pytest.raises(TrackWeatherError, match="WHITELIST_ERROR"):
        track_weather.fetch_weather()
    with pytest.raises(TrackWeatherError, match="WHITELIST_ERROR"):
        going.fetch_going("2026-10-04")


# ─── the live weather ─────────────────────────────────────────────────────────

def test_every_instrument_on_both_courses_is_read():
    rows, notes = _weather()
    assert notes == []
    assert sorted(_by_key(rows)) == [(v, loc) for v in ("HV", "ST")
                                     for loc in "ABCDE"]
    assert {r["captured_at"] for r in rows} == {CAPTURED}
    assert {r["is_valid"] for r in rows} == {1}


@pytest.mark.parametrize("text, degrees", [
    ("N60W", 300.0), ("N1E", 1.0), ("S61E", 119.0), ("S70E", 110.0),
    ("N23W", 337.0), ("S10W", 190.0), ("N0E", 0.0), ("N90W", 270.0),
    ("S17°E", 163.0), ("E", 90.0), ("NW", 315.0), ("WN", 315.0),
    ("n60w", 300.0), ("N 60 W", 300.0), ("", None), ("---", None), (None, None),
])
def test_a_bearing_is_read_the_way_the_page_reads_it(text, degrees):
    """Start at the first letter's cardinal and turn toward the last: the
    page's own reading (N=0, E=90, S=180, W=270; N turns +E/-W, S turns
    -E/+W). Reading S61E as 180+61 would put an easterly in the south-west."""
    assert track_weather.bearing(text) == degrees


def test_a_bearing_the_page_could_not_read_raises():
    with pytest.raises(TrackWeatherError, match="CALM"):
        track_weather.bearing("CALM")


def test_the_live_fixture_reads_as_published():
    """Spot values from the recorded reply, so a change in any reader shows
    against numbers HKJC actually sent."""
    by = _by_key(_weather()[0])
    assert by[("HV", "A")]["wind_dir"] == 300.0              # N60W
    assert by[("HV", "A")]["gust_dir"] == 293.0              # N67W
    assert by[("ST", "C")]["wind_dir"] == 119.0              # S61E
    assert by[("ST", "C")]["wind_speed"] == 5.0              # "5" km/h
    assert by[("ST", "C")]["gust_speed"] == 6.0


def test_the_instruments_own_time_is_kept_to_the_millisecond():
    """HKJC sends tenths ("23:32:53.2"); the key is the instrument's clock, so
    a re-read of the same reading lands on the same row."""
    by = _by_key(_weather()[0])
    assert by[("HV", "A")]["observed_at"] == "2026-11-23T23:32:53.200"
    assert by[("ST", "E")]["observed_at"] == "2026-11-23T23:30:58.300"


def test_the_stations_columns_are_null_on_an_anemometer_and_zero_is_zero():
    """An anemometer does not measure rain. NULL there says so; a station
    that measured none says 0.0, a different statement."""
    by = _by_key(_weather()[0])
    for loc in "ABCD":
        for col in track_weather.STATION_ONLY:
            assert by[("ST", loc)][col] is None
    st = by[("ST", track_weather.STATION)]
    assert (st["temperature"], st["humidity"], st["soil_water"]) == \
        (22.3, 95.2, 13.5)
    assert (st["rain_10min"], st["rain_day"]) == (0.0, 0.8)
    assert by[("HV", "E")]["rain_day"] == 0.0


def test_an_unreadable_value_costs_that_value_not_the_reading():
    """These readings exist nowhere else. A direction nobody can read must not
    throw away the speed beside it -- and must not pass in silence either."""
    body = _fixture("track_weather_live")
    body["data"]["weather"][0]["sectional"][0]["avgCorrectedWindDirection"] = "XYZ"
    body["data"]["weather"][0]["sectional"][1]["avgCorrectedWindSpeed"] = "-3"
    rows, notes = _weather(body)
    by = _by_key(rows)
    assert len(rows) == 10
    assert by[("HV", "A")]["wind_dir"] is None
    assert by[("HV", "A")]["wind_speed"] == 2.0
    assert by[("HV", "B")]["wind_speed"] is None
    assert by[("HV", "B")]["gust_speed"] == 3.0
    assert any("HV/A" in n and "XYZ" in n for n in notes)
    assert any("HV/B" in n and "negative" in n for n in notes)


def test_a_reading_without_a_time_is_not_stored_and_is_named():
    body = _fixture("track_weather_live")
    body["data"]["weather"][1]["sectional"][2]["time"] = ""
    rows, notes = _weather(body)
    assert ("ST", "C") not in _by_key(rows)
    assert len(rows) == 9
    assert any(n.startswith("ST/C") for n in notes)


def test_a_reading_hkjc_marks_invalid_is_kept_and_flagged():
    body = _fixture("track_weather_live")
    body["data"]["weather"][1]["weatherStation"]["isValid"] = False
    rows, notes = _weather(body)
    assert _by_key(rows)[("ST", "E")]["is_valid"] == 0
    assert any("ST/E" in n and "invalid" in n for n in notes)


def test_a_missing_instrument_is_named():
    body = _fixture("track_weather_live")
    del body["data"]["weather"][0]["sectional"][3]
    rows, notes = _weather(body)
    assert len(rows) == 9
    assert any("HV: 3 anemometers" in n for n in notes)


@pytest.mark.parametrize("body", [
    {"data": {"weather": None}}, {"data": {"weather": []}}, {"data": None}, {},
])
def test_a_reply_without_the_weather_list_raises(body):
    """HKJC lists both courses on every answer. None at all is a changed
    shape, and must not read as a quiet afternoon."""
    with pytest.raises(TrackWeatherError, match="weather list"):
        _weather(body)


def test_two_instruments_under_one_location_code_stop_the_capture():
    """A location names one instrument's series. Stored, two instruments under
    one code would interleave in the table beyond any later unpicking."""
    body = _fixture("track_weather_live")
    body["data"]["weather"][1]["weatherStation"]["location"] = "A"
    with pytest.raises(TrackWeatherError, match="share a location"):
        _weather(body)


def test_a_course_without_its_instrument_fields_raises():
    body = _fixture("track_weather_live")
    del body["data"]["weather"][0]["sectional"]
    with pytest.raises(TrackWeatherError, match="shape"):
        _weather(body)


# ─── the going readings ───────────────────────────────────────────────────────

def test_going_readings_are_read_per_instrument_and_the_simulcast_skipped():
    rows, notes = going.going_rows(_fixture("track_going_20261004"),
                                   dates=["2026-10-04"])
    assert notes == []
    assert {(r["venue"], r["instrument"], r["read_at"], r["reading"])
            for r in rows} == {
        ("ST", "penetrometer", "2026-10-04T11:30:00", 2.72),
        ("ST", "penetrometer", "2026-10-04T08:00:00", 2.72),
        ("ST", "hammer", "2026-10-04T11:30:00", 8.60),
        ("ST", "hammer", "2026-10-04T08:00:00", 8.23)}
    assert {r["race_date"] for r in rows} == {"2026-10-04"}


def test_a_meeting_not_asked_for_is_refused():
    """`raceMeetings(date:)` answers about the current meeting when asked for
    one HKJC does not have (ingest/odds). rw_Summary was not seen to, but a
    reading filed under the wrong meeting could never be found again."""
    with pytest.raises(TrackWeatherError, match="not asked for"):
        going.going_rows(_fixture("track_going_20261004"),
                         dates=["2026-10-05"])


def test_a_date_with_no_meeting_is_a_note_not_a_failure():
    """What rw_Summary answers for a date with no meeting, and for one older
    than it still serves: an empty list."""
    rows, notes = going.going_rows({"data": {"commonMeetings": []}},
                                   dates=["2026-09-20"])
    assert rows == []
    assert notes == ["2026-09-20: HKJC returned no Sha Tin or Happy Valley "
                     "meeting"]


def test_a_meeting_before_its_first_reading_says_so():
    """What 25 Nov 2026 HV looked like two days out: listed, both lists empty.
    A zero on the report line, and the reason beside it."""
    body = {"data": {"commonMeetings": [
        {"date": "2026-11-25", "venueCode": "HV", "penetrometerReadings": [],
         "hammerReadings": []}]}}
    rows, notes = going.going_rows(body, dates=["2026-11-25"])
    assert rows == []
    assert notes == ["2026-11-25 HV: no reading published yet"]


def test_a_missing_readings_list_is_a_changed_shape():
    """Present and empty is HKJC saying no reading was taken. Absent is the
    reply no longer carrying the field, and reading it as empty would record
    every meeting from then on as unread."""
    body = _fixture("track_going_20261004")
    del body["data"]["commonMeetings"][0]["hammerReadings"]
    with pytest.raises(TrackWeatherError, match="hammerReadings"):
        going.going_rows(body, dates=["2026-10-04"])


def test_a_reply_at_hkjcs_cap_says_so():
    """Asked for 56 dates at once on 23 Nov 2026, rw_Summary returned ten
    meetings and dropped the rest without a word."""
    sims = [{"date": "2026-10-03", "venueCode": f"S{i}",
             "penetrometerReadings": [], "hammerReadings": []}
            for i in range(going.REPLY_CAP)]
    _, notes = going.going_rows({"data": {"commonMeetings": sims}},
                                dates=["2026-10-03"])
    assert any("the most HKJC returns" in n for n in notes)


def test_a_reading_without_a_value_is_not_stored():
    body = _fixture("track_going_20261004")
    body["data"]["commonMeetings"][0]["hammerReadings"][0]["reading"] = ""
    rows, notes = going.going_rows(body, dates=["2026-10-04"])
    assert len(rows) == 3
    assert any("hammer" in n and "no value" in n for n in notes)


# ─── writing ──────────────────────────────────────────────────────────────────

def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_weather_is_written_once_however_often_it_is_read(conn):
    """Keyed on the instrument's clock: a capture that finds a sensor has not
    reported since the last one adds nothing, and the first capture to see a
    reading stays the one recorded as seeing it."""
    rows, _ = _weather()
    with transaction(conn):
        assert store.upsert_track_weather(conn, rows) == 10
    later = [{**r, "captured_at": "2026-11-23T23:39:09"} for r in rows]
    with transaction(conn):
        assert store.upsert_track_weather(conn, later) == 10
    assert _count(conn, "track_weather") == 10
    assert {r[0] for r in conn.execute(
        "SELECT captured_at FROM track_weather")} == {CAPTURED}


def test_a_later_sighting_fills_a_gap_and_never_blanks_a_value(conn):
    rows, _ = _weather()
    first = [{**r, "wind_dir": None} if r["location"] == "A" else r
             for r in rows]
    with transaction(conn):
        store.upsert_track_weather(conn, first)
        store.upsert_track_weather(conn, rows)
        store.upsert_track_weather(conn, first)
    got = conn.execute("SELECT wind_dir FROM track_weather WHERE venue = 'HV' "
                       "AND location = 'A'").fetchone()[0]
    assert got == 300.0


def test_values_are_stored_as_numbers_not_text(conn):
    """Coerced at write time: a speed is REAL, the flag INTEGER."""
    rows, _ = _weather()
    with transaction(conn):
        store.upsert_track_weather(conn, rows)
    kinds = conn.execute(
        "SELECT DISTINCT typeof(wind_speed), typeof(wind_dir), "
        "typeof(is_valid) FROM track_weather").fetchall()
    assert [tuple(k) for k in kinds] == [("real", "real", "integer")]
    station = conn.execute(
        "SELECT typeof(temperature), typeof(rain_day) FROM track_weather "
        "WHERE location = 'E'").fetchall()
    assert {tuple(s) for s in station} == {("real", "real")}


def test_going_readings_are_idempotent_and_a_reread_replaces(conn):
    rows, _ = going.going_rows(_fixture("track_going_20261004"),
                               dates=["2026-10-04"])
    with transaction(conn):
        assert store.upsert_going_readings(conn, rows) == 4
        assert store.upsert_going_readings(conn, rows) == 4
    assert _count(conn, "going_readings") == 4
    fixed = [{**r, "reading": 2.70} if r["instrument"] == "penetrometer"
             and r["read_at"].endswith("11:30:00") else r for r in rows]
    with transaction(conn):
        store.upsert_going_readings(conn, fixed)
    assert _count(conn, "going_readings") == 4
    assert conn.execute(
        "SELECT reading FROM going_readings WHERE instrument = 'penetrometer' "
        "AND read_at = '2026-10-04T11:30:00'").fetchone()[0] == 2.70


@pytest.mark.parametrize("change, message", [
    ({"venue": "S1"}, "venue"),
    ({"location": ""}, "location"),
    ({"observed_at": "23:32"}, "observed_at"),
    ({"wind_speed": "fast"}, "wind_speed"),
    ({"is_valid": 2}, "is_valid"),
])
def test_the_weather_writer_refuses_what_is_not_a_reading(conn, change,
                                                          message):
    row = {**_weather()[0][0], **change}
    with pytest.raises(coerce.CoerceError, match=message):
        store.upsert_track_weather(conn, [row])


@pytest.mark.parametrize("change, message", [
    ({"instrument": "anemometer"}, "instrument"),
    ({"reading": None}, "no value"),
    ({"venue": "S4"}, "venue"),
    ({"read_at": "11:30"}, "read_at"),
])
def test_the_going_writer_refuses_what_is_not_a_reading(conn, change, message):
    row = {"race_date": "2026-10-04", "venue": "ST",
           "instrument": "penetrometer", "read_at": "2026-10-04T11:30:00",
           "reading": 2.72, **change}
    with pytest.raises(coerce.CoerceError, match=message):
        store.upsert_going_readings(conn, [row])


@pytest.mark.parametrize("token, value", [
    ("2", 2.0), (0, 0.0), ("22.7", 22.7), (1.524, 1.524), ("", None),
    ("---", None), (None, None),
])
def test_to_real(token, value):
    assert coerce.to_real(token, field="x") == value


@pytest.mark.parametrize("token", ["N60W", True, "nan", "inf"])
def test_to_real_refuses_what_is_not_a_finite_number(token):
    with pytest.raises(coerce.CoerceError, match="x"):
        coerce.to_real(token, field="x")
