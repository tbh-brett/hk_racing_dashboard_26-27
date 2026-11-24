"""HKJC's going readings: the turf penetrometer and the all-weather Clegg hammer.

Taken on the morning of a meeting and again before racing (08:00 and 11:30 at
Sha Tin, 08:00 and 17:40 at Happy Valley, on the meetings read on 23 Nov 2026),
and published per meeting in the summary the site's results pages read. Unlike
the wind (`ingest/track_weather`), HKJC keeps these for a while, which is what
lets a first run recover the weeks before the capture existed.

THE SITE'S OWN QUERY, `query rw_Summary`, copied from the site's JavaScript
(`_next/static/chunks/*.js` under racing.hkjc.com/local/informationAsset/) for
the reason `ingest/track_weather` gives: the endpoint runs only whitelisted
queries, and a query missing one field it does not read comes back
WHITELIST_ERROR. Most of what it asks for -- pools, dividends, stewards -- is
not read here; it is the price of being allowed to ask.

ONE DATE PER REQUEST. rw_Summary takes a list of dates and answers with at
most TEN meetings, simulcasts counted, dropping the rest without a word: asked
for every date from 1 Oct to 25 Nov 2026 in one request, it returned ten
meetings, the last on 10 Oct. A date with no meeting answers an empty list --
not the current meeting, which is what `raceMeetings(date:)` does in
`ingest/odds` -- but a meeting that was not asked for is refused all the same.

HOW FAR BACK. On 23 Nov 2026 the oldest meeting it still served was 23 Sep,
sixty-one days earlier; 20 Sep and every date before it came back as no meeting
at all. About two months, then.

TWO INSTRUMENTS, TWO SCALES. On 4 Oct 2026 the penetrometer read 2.72 on the
turf and the hammer 8.60 on the all-weather track at the same 11:30 reading.
They are named apart and must never be averaged or compared.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from hkrd.ingest.track_weather import VENUES, TrackWeatherError, post, reading

__all__ = ["SUMMARY_QUERY", "INSTRUMENTS", "REPLY_CAP", "going_rows",
           "fetch_going"]

# Hong Kong: HKJC writes these times with +08:00, and they are kept without it,
# on the same wall clock as the race times and the wind readings.
_HK = timezone(timedelta(hours=8))

# The summary's two lists, and the instrument each one is.
INSTRUMENTS = {"penetrometerReadings": "penetrometer",
               "hammerReadings": "hammer"}

# The most meetings rw_Summary returns in one answer, measured 23 Nov 2026.
REPLY_CAP = 10

# Lifted from the site's bundle on 23 Nov 2026 and pinned by its SHA-256 in
# `tests/test_track_weather`.
SUMMARY_QUERY = """
query rw_Summary ($dates: [String!]) {
  commonMeetings(dates: $dates, venueCodes: ["ST", "HV", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"]) {
    date
    venueCode
    startTime
    totalNumberOfRace

    pmPools(oddsTypes: [WIN,PLA,QIN,QPL,CWA,CWB,CWC,IWN,FCT,TCE,TRI,FF,QTT,DBL,TBL,DT,TT,SixUP]) {
      id
      jackpot
      leg {
        number
        races
      }
      dividends {
        winComb
        type
        seq
        partialUnit
        guarantee
        div
        partial
        status
      }
      oddsType
      hasCombs
      estimatedDividend
      comingleStatus
      rebateRate
      sellStatus
      mergedPoolId
      futureJackpot {
        venCode
        meetingDate
        meetingID
        firstLegRaceNo
        mergedBetType
        poolOrder
        includeResFund
        jackpot
      }
    }
    isActive
    jkSuspensions {
      commencedOn
      desc {
        code
        english
        chinese
      }
      jockeyId
      jockeyNms {
        chinese
        code
        english
      }
      resumedOn
      spsnTyp
    }
    foPools {
      oddsType
      oddsTypeDes {
        chinese
        english
      }
      instNo
    }
    stewards {
      name_ch
      name_en
      role {
        english
        chinese
        code
      }
      sequenceNumber
    }
    venueDes {
      chinese
      english
      code
    }
    country {
      namech
      nameen
      seq
    }
    penetrometerReadings {
      reading
      readingTime
    }
    hammerReadings {
      readingTime
      reading
    }
    meetingTrack_ch
    meetingTrack_en
    meetingType
    meetingTrackCode
    status
    isSeasonLastMeeting
    course {
      chinese
      code
      english
    }
    races {
      no
      raceCourse {
        code
        displayCode
        description_en
        description_ch
      }
      raceTrack {
        code
        description_en
        description_ch
      }
      go_ch
      go_en
      goCode
      venueCode
    }
  }
}
"""


def _read_at(value: Any) -> str:
    """A reading's time, "2026-11-22T11:30:00+08:00", as HK local time."""
    try:
        when = datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        raise TrackWeatherError(f"unreadable reading time {value!r}") from None
    if when.tzinfo is not None:
        when = when.astimezone(_HK).replace(tzinfo=None)
    return when.isoformat(timespec="seconds")


def going_rows(body: dict[str, Any], *, dates: Iterable[str]
               ) -> tuple[list[dict[str, Any]], list[str]]:
    """The penetrometer and hammer readings in one `rw_Summary` reply, and notes.

    `dates` are the dates asked for. A Sha Tin or Happy Valley meeting on any
    other date means the filter was not applied, and the reply is refused
    rather than filed under a meeting it does not describe. A list missing
    from a meeting is a changed shape and raises; a list present and empty is
    HKJC saying no reading has been taken, which is an answer.
    """
    meetings = (body.get("data") or {}).get("commonMeetings")
    if not isinstance(meetings, list):
        raise TrackWeatherError(
            "the reply has no commonMeetings list; the shape has changed")
    asked = {str(d) for d in dates}
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    if len(meetings) >= REPLY_CAP:
        notes.append(f"the reply held {len(meetings)} meetings, the most HKJC "
                     f"returns in one answer: any beyond it were dropped")
    found: set[str] = set()
    for meeting in meetings:
        venue = str(meeting.get("venueCode") or "").strip().upper()
        if venue not in VENUES:
            continue                    # a simulcast: no Hong Kong track read
        date = str(meeting.get("date") or "")[:10]
        if date not in asked:
            raise TrackWeatherError(
                f"the reply carries {date} {venue}, which was not asked for "
                f"({', '.join(sorted(asked))}); refused rather than stored "
                f"under the wrong meeting")
        found.add(date)
        if not any(meeting.get(key) for key in INSTRUMENTS):
            notes.append(f"{date} {venue}: no reading published yet")
        for key, instrument in INSTRUMENTS.items():
            if key not in meeting:
                raise TrackWeatherError(
                    f"{date} {venue}: the reply has no {key}; the shape "
                    f"has changed")
            for entry in meeting.get(key) or []:
                where = f"{date} {venue} {instrument}"
                try:
                    read_at = _read_at(entry.get("readingTime"))
                    value = reading(entry.get("reading"))
                except TrackWeatherError as exc:
                    notes.append(f"{where}: {exc}, not stored")
                    continue
                if value is None:
                    notes.append(f"{where} at {read_at}: no value, not stored")
                    continue
                rows.append({"race_date": date, "venue": venue,
                             "instrument": instrument, "read_at": read_at,
                             "reading": value})
    for date in sorted(asked - found):
        notes.append(f"{date}: HKJC returned no Sha Tin or Happy Valley "
                     f"meeting")
    return rows, notes


def fetch_going(date: str, *, session=None
                ) -> tuple[list[dict[str, Any]], list[str]]:
    """One date's going readings, in one request of its own: see REPLY_CAP."""
    return going_rows(post(SUMMARY_QUERY, {"dates": [date]}, session=session),
                      dates=[date])
