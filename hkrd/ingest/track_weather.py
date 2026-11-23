"""HKJC's own track weather: the in-track anemometers and the weather station.

Recorded because nothing else holds it. Asked on 23 Nov 2026 whether the wind
at the races matters, the archive could answer only for Sha Tin and only from
the Hong Kong Observatory's station near the course; no Observatory station
tracks the Happy Valley bowl. HKJC measures both, and keeps none of it: the
query its "Weather and Track Condition" page sends takes no date and answers
with the reading of the moment. A reading not captured while a meeting is on
is gone, which is why `jobs/scrape_track_weather` runs through every meeting.
The going readings, which HKJC does keep for a while, are `ingest/going`.

THE SITE'S OWN QUERY. The page reads the same endpoint as the odds,
`info.cld.hkjc.com/graphql/base/` (the QIDS_URI in racing.hkjc.com's
/local/informationAsset/config/globalConfig.js), and that endpoint runs only
queries it has whitelisted; a hand-written one comes back `WHITELIST_ERROR`.
`query Weather` is copied from the site's JavaScript (`_next/static/chunks/
*.js` under /local/informationAsset/, the chunk that also holds
`wt_WeatherMeeting`). Measured 23 Nov 2026: the text with its surrounding
whitespace stripped was still accepted, and the same text with one unread
field removed came back WHITELIST_ERROR. So the field list is what the
whitelist checks, and the string is kept here byte for byte anyway, fields
nothing reads included. Change it only by re-reading the bundle. The site sends
`{"variables": ..., "query": ...}` with no operationName, and so does this.

WHAT ONE REPLY HOLDS: four anemometers round each course (locations A to D)
and a weather station (E), for BOTH courses on every reply, race day or not.
The units are the page's own:

  wind and gust speed   km/h -- the page prints `{avgCorrectedWindSpeed} km/h`
  direction             a quadrant bearing: "N60W" is 60 degrees west of
                        north, 300. Read here the way the page reads it (N or
                        S, a number, E or W; a bare cardinal; a two-letter
                        intercardinal) into degrees clockwise from north.
  rainfall              mm, in the last ten minutes and since midnight
  soil water            %, volumetric; humidity % relative; temperature deg C

The station's reply carries more than is kept -- pressure, solar radiation,
sunshine, soil moisture loss and the day's highs and lows. What is kept is the
wind on every instrument and, from the station, temperature, humidity,
rainfall and soil water.

Parsing is separate from fetching, so the shape is tested against a reply
recorded from the live endpoint.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from hkrd.ingest._client import FetchError, fetch_json, urls

__all__ = ["TrackWeatherError", "WEATHER_QUERY", "VENUES", "STATION",
           "STATION_ONLY", "bearing", "reading", "weather_rows",
           "fetch_weather", "post"]

GRAPHQL_URL = urls.graphql


class TrackWeatherError(ValueError):
    """A reply could not be read. Names what was wrong."""


# HKJC's two courses. The Weather reply carries both on every capture.
VENUES = ("ST", "HV")

# The weather station's location code. A to D are the anemometers.
STATION = "E"

# Hong Kong, the clock every reading is written on: UTC+8 all year round.
_HK = timezone(timedelta(hours=8))

# Lifted from the site's bundle on 23 Nov 2026. `tests/test_track_weather` pins
# it by its SHA-256, so an edit, however tidy, fails a test before it fails on
# a race day.
WEATHER_QUERY = """
query Weather {
  weather {
    racecourse
    sectional {
      location
      date
      time
      avgCorrectedWindDirection
      avgCorrectedWindSpeed
      correctedGustDirection
      correctedGustSpeed
      gpsPosition
      supplyVoltage
      status
      isValid
    }
    weatherStation {
      location
      date
      time
      avgCorrectedWindDirection
      avgCorrectedWindSpeed
      correctedGustDirection
      correctedGustSpeed
      gpsPosition
      supplyVoltage
      relativeHumidity
      temperature
      solarRadiation
      sunshineHour
      pressure
      correctedGustSpeedDailyMax
      relativeHumidityDailyMax
      relativeHumidityDailyMin
      temperatureDailyMax
      temperatureDailyMin
      rainFallPrecipitation1Min
      rainFallPrecipitation10Min
      rainPrecipitationDailyTotal
      soilVolumeticWaterContent
      soilMoistureLoss1Min
      soilMoistureLossDailyTotal
      soilMoistureLoss24H
      status
      isValid
    }
  }
}

""" + "  "      # the bundle's literal ends in two spaces; kept, not trusted to an editor

# ── values ───────────────────────────────────────────────────────────────────

_CARDINAL = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}
# The page accepts the two letters in either order, and so does this.
_INTERCARDINAL = {"NE": 45.0, "EN": 45.0, "SE": 135.0, "ES": 135.0,
                  "SW": 225.0, "WS": 225.0, "NW": 315.0, "WN": 315.0}
_QUADRANT = re.compile(r"^([NS])(\d+(?:\.\d+)?)°?([EW])$")
_ABSENT = re.compile(r"^-*$")       # blank, or HKJC's run of dashes
_TIME = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d+))?$")


def bearing(text: Any) -> float | None:
    """HKJC's wind direction as degrees clockwise from north, or None for none.

    "N60W" -> 300.0, "N1E" -> 1.0, "S61E" -> 119.0, "S10W" -> 190.0, the way
    the page itself reads them: start at the first letter's cardinal and turn
    toward the last. Raises on a shape the page would not read either.
    """
    s = re.sub(r"\s+", "", str(text if text is not None else "")).upper()
    if _ABSENT.match(s):
        return None
    if s in _CARDINAL:
        return _CARDINAL[s]
    if s in _INTERCARDINAL:
        return _INTERCARDINAL[s]
    m = _QUADRANT.match(s)
    if not m:
        raise TrackWeatherError(f"unrecognised wind direction {text!r}")
    first, degrees, toward = m.groups()
    turn = float(degrees)
    if first == "N":
        value = turn if toward == "E" else -turn
    else:
        value = 180.0 - turn if toward == "E" else 180.0 + turn
    return value % 360.0


def reading(value: Any) -> float | None:
    """A measured value. Arrives as a string ("2", "22.7") or a JSON number
    (0, 1.3); blank or a run of dashes is no reading, which is not zero."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TrackWeatherError(f"a flag where a reading belongs: {value!r}")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        s = str(value).strip()
        if _ABSENT.match(s):
            return None
        try:
            number = float(s)
        except ValueError:
            raise TrackWeatherError(f"unrecognised reading {value!r}") from None
    if not math.isfinite(number):
        raise TrackWeatherError(f"not a finite reading: {value!r}")
    return number


def _flag(value: Any) -> int | None:
    """HKJC's `isValid`: a JSON boolean. Anything else is not an answer."""
    if isinstance(value, bool):
        return int(value)
    if value is None:
        return None
    raise TrackWeatherError(f"isValid is not true or false: {value!r}")


def _observed(date: Any, time: Any) -> str:
    """The instrument's own date and time as one HK timestamp, to the ms.

    "2026-11-23" and "23:32:53.2" -> "2026-11-23T23:32:53.200". Parsed by hand
    rather than by `fromisoformat`, which before Python 3.11 read only three or
    six digits of a fraction, and HKJC sends one.
    """
    m = _TIME.match(str(time or "").strip())
    try:
        day = datetime.strptime(str(date or "").strip(), "%Y-%m-%d")
        if not m:
            raise ValueError
        hh, mm, ss, frac = m.groups()
        when = day.replace(hour=int(hh), minute=int(mm), second=int(ss),
                           microsecond=int((frac or "0")[:6].ljust(6, "0")))
    except ValueError:
        raise TrackWeatherError(
            f"unreadable reading time {date!r} {time!r}") from None
    return when.isoformat(timespec="milliseconds")


# (column, HKJC's field, reader). HKJC's own spelling, `Volumetic`, is the
# field's name and must stay so.
_FIELDS = (
    ("wind_dir", "avgCorrectedWindDirection", bearing),
    ("wind_speed", "avgCorrectedWindSpeed", reading),
    ("gust_dir", "correctedGustDirection", bearing),
    ("gust_speed", "correctedGustSpeed", reading),
    ("temperature", "temperature", reading),
    ("humidity", "relativeHumidity", reading),
    ("rain_10min", "rainFallPrecipitation10Min", reading),
    ("rain_day", "rainPrecipitationDailyTotal", reading),
    ("soil_water", "soilVolumeticWaterContent", reading),
)
# Measured by the station alone. NULL on an anemometer, which carries none of
# them: "this instrument does not read that", not "the reading was zero".
STATION_ONLY = frozenset({"temperature", "humidity", "rain_10min", "rain_day",
                          "soil_water"})
# Amounts. Below zero is not a calm or a dry spell but a fault, and is kept
# out of the record the way an unreadable value is.
_AMOUNTS = frozenset({"wind_speed", "gust_speed", "rain_10min", "rain_day"})


# ── parsing ──────────────────────────────────────────────────────────────────

def _row(venue: str, entry: dict[str, Any], *, station: bool,
         captured_at: str, notes: list[str]) -> dict[str, Any] | None:
    """One instrument's reading as a row, or None when it cannot be keyed.

    A value that cannot be read costs that value, not the reading: the rest
    of it is stored and the value is named in the notes. These are the only
    copy there will ever be, so a strange direction must not throw away the
    speed beside it.
    """
    location = str(entry.get("location") or "").strip().upper()
    where = f"{venue}/{location or '?'}"
    if not location:
        notes.append(f"{venue}: a reading with no location, not stored")
        return None
    try:
        observed = _observed(entry.get("date"), entry.get("time"))
    except TrackWeatherError as exc:
        notes.append(f"{where}: {exc}, not stored")
        return None
    row: dict[str, Any] = {"venue": venue, "location": location,
                           "observed_at": observed, "captured_at": captured_at}
    for column, key, read in _FIELDS:
        if column in STATION_ONLY and not station:
            row[column] = None
            continue
        try:
            row[column] = read(entry.get(key))
            if column in _AMOUNTS and row[column] is not None \
                    and row[column] < 0:
                raise TrackWeatherError(f"negative amount {entry.get(key)!r}")
        except TrackWeatherError as exc:
            notes.append(f"{where} {key}: {exc}, stored as no reading")
            row[column] = None
    try:
        row["is_valid"] = _flag(entry.get("isValid"))
    except TrackWeatherError as exc:
        notes.append(f"{where}: {exc}")
        row["is_valid"] = None
    if row["is_valid"] == 0:
        notes.append(f"{where}: HKJC marks the {observed[11:19]} reading "
                     f"invalid (stored, flagged)")
    return row


def weather_rows(body: dict[str, Any], *, captured_at: str
                 ) -> tuple[list[dict[str, Any]], list[str]]:
    """Every instrument's reading in one `query Weather` reply, and notes.

    One row per reading: the four anemometers and the station, for each
    course. Raises when the reply is not the shape the page reads, because an
    empty list from a changed shape and an empty list from a quiet sensor must
    not look the same.
    """
    courses = (body.get("data") or {}).get("weather")
    if not isinstance(courses, list) or not courses:
        raise TrackWeatherError(
            "the reply has no weather list: HKJC lists both courses on every "
            "answer, so the shape has changed")
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    for course in courses:
        venue = str(course.get("racecourse") or "").strip().upper()
        if venue not in VENUES:
            notes.append(f"racecourse {venue!r} is not ST or HV, not stored")
            continue
        if "sectional" not in course or "weatherStation" not in course:
            raise TrackWeatherError(
                f"{venue}: the reply has no sectional or weatherStation "
                f"field; the shape has changed")
        sensors = [s for s in course.get("sectional") or [] if s]
        if len(sensors) != 4:
            notes.append(f"{venue}: {len(sensors)} anemometers in the reply, "
                         f"4 expected")
        station = course.get("weatherStation")
        if not station:
            notes.append(f"{venue}: no weather station reading in the reply")
        for entry, is_station in [*((s, False) for s in sensors),
                                  *([(station, True)] if station else [])]:
            row = _row(venue, entry, station=is_station,
                       captured_at=captured_at, notes=notes)
            if row is not None:
                rows.append(row)
    # A location is the name of one instrument's series. Two instruments under
    # one name would interleave two series in the table, which no later read
    # could pull apart, so it stops the capture rather than being stored.
    named = [(r["venue"], r["location"]) for r in rows]
    if len(set(named)) != len(named):
        raise TrackWeatherError(
            "two instruments on one course share a location code: the "
            "locations are no longer one per instrument")
    for venue in VENUES:
        if not any(r["venue"] == venue for r in rows):
            notes.append(f"{venue}: no reading stored from this reply")
    return rows, notes


# ── fetching ─────────────────────────────────────────────────────────────────

def post(query: str, variables: dict[str, Any] | None, *,
         session=None) -> dict[str, Any]:
    """One call, shaped as the site shapes it, through the shared client.
    `ingest/going` sends its query through here too."""
    try:
        return fetch_json(GRAPHQL_URL, {"variables": variables, "query": query},
                          session=session)
    except FetchError as exc:
        raise TrackWeatherError(f"track weather endpoint: {exc}") from exc


def fetch_weather(*, captured_at: str | None = None, session=None
                  ) -> tuple[list[dict[str, Any]], list[str]]:
    """Every instrument on both courses, now. One request, about 5 KB.

    `captured_at` is when the capture ran, HK local, to the second; stamped
    here when the caller does not give it.
    """
    stamp = captured_at or datetime.now(_HK).replace(tzinfo=None).isoformat(
        timespec="seconds")
    return weather_rows(post(WEATHER_QUERY, None, session=session),
                        captured_at=stamp)
