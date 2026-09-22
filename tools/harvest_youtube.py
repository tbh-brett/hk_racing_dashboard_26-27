#!/usr/bin/env python3
"""Harvest HK racing YouTube transcripts — connections, pundits and tipsters.

Covers three sources, by playlist or by channel:

    賽馬Fact Check   UCNpEBQatm4NALFlS-HO1nzg   Chinese, HUMAN-WRITTEN subtitles
    Racing To Win    PLK8zYRjJwINk (26/27)      English ASR, previews + interviews
                     PLRyiXGt4yq8YzlS_FNVmbSr7glDAtUgwt (25/26)
    全方位Bryan      UCQAbEL38om9qqgVHGuK5BSg   Cantonese ASR, stable-led

EVERY CLAIM BELOW WAS TESTED LIVE, 2026-09-21 and 2026-09-22. None is inferred.

1.  THE WEB CAPTION URL IS DEAD. The `baseUrl` on `ytInitialPlayerResponse`
    returns ZERO bytes -- YouTube's Proof-of-Origin token requirement. Tested in
    a real browser, on a residential IP, signed in. Every library and tutorial
    built on that URL is already broken.

2.  THE ANDROID PLAYER ENDPOINT WORKS. POST /youtubei/v1/player with an ANDROID
    client context returns a caption `baseUrl` that serves the real thing:
    976,021 bytes and 2,561 timed events for a 44-minute preview. No browser, no
    API key, no OAuth. This is what `youtube-transcript-api` >= 1.1.0 does
    internally.

3.  HUMAN SUBTITLES BEAT ASR, AND SOMETIMES SIT RIGHT NEXT TO IT. 賽馬Fact Check
    uploads a real `zh-HK` track (its titles say so: `CC中文字幕`) alongside the
    `yue` auto track. Every horse, jockey and trainer name in it is spelled
    correctly -- which is exactly what ASR destroys. So track selection ranks
    MANUAL above ASR within a language; picking by language alone would have
    taken the auto track sitting beside it.

4.  A MANUAL TRACK CAN STOP BEFORE THE VIDEO DOES. Measured on Fact Check's 9.23
    preview: subtitles end at 370s of 601s, 62%. The uncaptioned tail is where
    the analyst's selections sit. `caption_coverage` reports this, and unless
    --no-fill-gaps is passed the ASR track supplies the tail, every filled
    segment tagged `src: "asr"` so nothing downstream mistakes it for the clean
    text.

5.  RACING TO WIN IS IN ENGLISH, and its previews carry a per-race CHAPTER MAP in
    the description:

        [00:00:00] - Race 1
        [00:02:34] - Race 2   …   [00:39:19] - Race 11

    So a 44-minute preview splits into per-race segments by parsing text. No
    model, no heuristic, no guessing where race 7 starts. `by_race` in the output.

6.  TITLES CARRY THE MEETING, in four grammars, all parsed and unit-tested:

        Race previews – Sha Tin 12/07/26              DD/MM/YY; `Shatin` also occurs
        [Racing To Win Interviews]: … | Meeting 87 | 12 Jul
        賽前Highlights｜9.23谷草9場夜馬 …              谷=HV 田=ST, 草=turf 泥=AWT
        兵馬檢閱｜告東尼 …                            per-stable, no meeting

WHAT THIS DOES NOT SOLVE, AND YOU SHOULD NOT PRETEND IT DOES

On the ASR sources, proper nouns break and they are the only words that carry the
join. Measured:

    English (Racing To Win)      "Chartin" -> Sha Tin, every occurrence
                                 "Hugh Oman" -> Hugh Bowman
                                 "Paul Ali" -> Paul Lally
                                 "18800 meters" -> 1800 metres
    Cantonese (全方位Bryan)       "三in,三in做得旺㗎啦喎" -> noise
                                 3,116 chars for 12 minutes; names badly mangled

The analysis around them survives intact -- "his best form is 1200 metres and
David Hall's dropped him back in distance", "there's not too much speed in the
race". So an ASR transcript is good for REASONING and bad for IDENTITY, which is
why this script stops at the transcript and never tries to name a horse.
Resolution belongs downstream, against the runners actually declared in that race:
a closed set of ten to fourteen, where "Dragon Sunris" is an easy match and
against 1,900 horses it is a coin flip. On the manual track that problem does not
arise -- the name is simply correct -- which makes an English/Chinese name bridge
load-bearing rather than decorative.

WHERE TO RUN IT

On your own machine, not on Fly.io. YouTube blocks most datacenter IP ranges,
the single most reported transcript-library failure of 2026. A residential
connection is the fix and costs nothing. Server-side, the same code works behind
a rotating RESIDENTIAL proxy (set HTTPS_PROXY) at roughly a dollar per thousand
transcripts; static residential, datacenter and free-tier proxies do not work.

USAGE

    # Fact Check — the cleanest source, Chinese, human subtitles first
    python harvest_youtube.py --channel UCNpEBQatm4NALFlS-HO1nzg \
           --lang zh-HK yue --season-year 2026 --out ./raw/factcheck

    # Racing To Win, this season and last
    python harvest_youtube.py --playlist PLK8zYRjJwINk --season-year 2026 --out ./raw/rtw
    python harvest_youtube.py --playlist PLRyiXGt4yq8YzlS_FNVmbSr7glDAtUgwt \
           --season-year 2025 --out ./raw/rtw

    # only the connections' interviews
    python harvest_youtube.py --playlist PLK8zYRjJwINk --kinds interview --out ./raw/rtw

    # Bryan — Cantonese, weakest; collect now, resolve last
    python harvest_youtube.py --channel UCQAbEL38om9qqgVHGuK5BSg \
           --lang yue zh-HK --season-year 2026 --out ./raw/bryan

Re-running is safe and cheap: a video already on disk is skipped.

OUTPUT  —  <out>/<video_id>.json

    {"video_id", "title", "kind", "race_date", "venue", "surface",
     "races_billed", "meeting_no", "manual_subs_claimed",
     "published", "duration_sec", "description",
     "caption_lang", "caption_kind", "caption_tracks", "caption_coverage",
     "asr_filled_segments",
     "chapters":  [{"race_no", "start", "end"}],
     "segments":  [{"t", "text", "src"?}],     # every timed caption event
     "by_race":   {"7": "...the text for race 7..."},
     "text":      "...the whole thing..."}

    <out>/manifest.jsonl — one line per video: kind, date, coverage, outcome
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

# ── the two endpoints ────────────────────────────────────────────────────────

INNERTUBE = "https://www.youtube.com/youtubei/v1/{path}?prettyPrint=false"

# Reproduced from a working call. The ANDROID client is the point: the WEB
# client's caption URLs need a PO token and return nothing.
ANDROID_CTX = {"client": {
    "clientName": "ANDROID", "clientVersion": "20.10.38",
    "androidSdkVersion": 30, "hl": "en", "gl": "HK",
}}
WEB_CTX = {"client": {
    "clientName": "WEB", "clientVersion": "2.20260918.00.00", "hl": "en", "gl": "HK",
}}

# One request every 1.2s, the same courtesy the dashboard's HKJC scraper extends.
MIN_INTERVAL = 1.2
_last = 0.0


def _throttle() -> None:
    global _last
    wait = MIN_INTERVAL - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()


def _post(path: str, body: dict[str, Any], *, session: requests.Session,
          retries: int = 3) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        _throttle()
        try:
            r = session.post(INNERTUBE.format(path=path), json=body, timeout=30)
        except requests.RequestException as e:
            last = e
        else:
            if r.status_code == 200:
                return r.json()
            # 403 here is almost always the datacenter-IP block, not a bad
            # request. Say so, because "403" on its own sends people editing
            # their payload for an hour.
            if r.status_code == 403:
                raise RuntimeError(
                    "YouTube answered 403. This is usually the datacenter-IP "
                    "block rather than anything wrong with the request — run "
                    "this from a home connection, or set HTTPS_PROXY to a "
                    "ROTATING RESIDENTIAL proxy.")
            last = RuntimeError(f"HTTP {r.status_code} from {path}")
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"{path} failed after {retries} attempts — {last}")


# ── enumerating a playlist ───────────────────────────────────────────────────

def _walk_items(node: Any, items: list[dict], conts: list[str]) -> None:
    """Collect video entries and continuation tokens from a browse response.

    Two shapes are handled because YouTube is mid-migration: `lockupViewModel`
    is what the playlist pages return today, `playlistVideoRenderer` is the
    older shape still served in some contexts. Reading both means a flip back
    does not silently return an empty playlist.
    """
    if not isinstance(node, (dict, list)):
        return
    if isinstance(node, list):
        for v in node:
            _walk_items(v, items, conts)
        return

    lv = node.get("lockupViewModel")
    if isinstance(lv, dict) and lv.get("contentId"):
        title = (((lv.get("metadata") or {}).get("lockupMetadataViewModel") or {})
                 .get("title") or {}).get("content")
        items.append({"id": lv["contentId"], "title": title})

    pv = node.get("playlistVideoRenderer") or node.get("videoRenderer")
    if isinstance(pv, dict) and pv.get("videoId"):
        title = pv.get("title") or {}
        text = title.get("simpleText") or (title.get("runs") or [{}])[0].get("text")
        items.append({"id": pv["videoId"], "title": text})

    tok = (((node.get("continuationItemRenderer") or {}).get("continuationEndpoint")
            or {}).get("continuationCommand") or {}).get("token")
    if tok:
        conts.append(tok)

    for v in node.values():
        _walk_items(v, items, conts)


# The Videos tab of a channel. This is the protobuf the site itself sends for
# that tab; it is opaque and constant, and is reproduced rather than built.
CHANNEL_VIDEOS_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"


def _enumerate(first_body: dict[str, Any], *, session: requests.Session,
               max_pages: int) -> list[dict]:
    body = first_body
    out: list[dict] = []
    seen: set[str] = set()

    for _ in range(max_pages):
        data = _post("browse", body, session=session)
        items: list[dict] = []
        conts: list[str] = []
        _walk_items(data, items, conts)
        fresh = [i for i in items if i["id"] not in seen]
        for i in fresh:
            seen.add(i["id"])
            out.append(i)
        if not conts or not fresh:
            break
        body = {"context": WEB_CTX, "continuation": conts[0]}

    return out


def enumerate_playlist(playlist_id: str, *, session: requests.Session,
                       max_pages: int = 40) -> list[dict]:
    """Every video in a playlist, in playlist order. No API key required."""
    return _enumerate({"context": WEB_CTX, "browseId": f"VL{playlist_id}"},
                      session=session, max_pages=max_pages)


def enumerate_channel(channel_id: str, *, session: requests.Session,
                      max_pages: int = 40) -> list[dict]:
    """Every upload on a channel's Videos tab, newest first.

    For a source that posts to a channel rather than curating a playlist —
    全方位Bryan (UCQAbEL38om9qqgVHGuK5BSg) being the case this was added for.
    """
    return _enumerate({"context": WEB_CTX, "browseId": channel_id,
                       "params": CHANNEL_VIDEOS_PARAMS},
                      session=session, max_pages=max_pages)


# ── titles, dates, chapters ──────────────────────────────────────────────────

VENUES = {"sha tin": "ST", "shatin": "ST", "happy valley": "HV",
          "happyvalley": "HV", "conghua": "CH"}

# 谷 = Happy Valley, 田 = Sha Tin, 從 = Conghua. 草 = turf, 泥 = all-weather.
# Kept as two lookups rather than one table of four strings because the venue
# and the surface are independent and the dashboard already treats ST's AWT as
# its own course category.
VENUES_ZH = {"谷": "HV", "田": "ST", "從": "CH"}
SURFACE_ZH = {"草": "Turf", "泥": "AWT"}

# 賽馬Fact Check's own taxonomy, from its channel listing. The title says which
# kind of video it is and, for the two race-linked kinds, the meeting:
#   賽前Highlights｜9.23谷草9場夜馬 …     pre-race
#   賽後Spotlights｜9.13田草10場日馬 …    post-race
#   兵馬檢閱｜告東尼 …                    one trainer's stable, no meeting
#   優質生力軍｜…                         newcomers
#   競馬專訪｜…                           interview
# `CC中文字幕` in a title is the channel's own flag that the video carries
# human-written subtitles rather than only the auto track -- worth reading,
# because it predicts whether the horse names will be correct.
_ZH_MEETING = re.compile(
    r"(?P<kind>賽前Highlights|賽後Spotlights)\s*[｜|]\s*"
    r"(?P<mon>\d{1,2})[.\-/](?P<day>\d{1,2})\s*"
    r"(?P<venue>[谷田從])(?P<surface>[草泥])\s*"
    r"(?:(?P<races>\d{1,2})\s*場)?")

_ZH_KINDS = (
    ("兵馬檢閱", "stable_zh"),
    ("優質生力軍", "griffin_zh"),
    ("競馬專訪", "interview_zh"),
    ("大倉猜情尋", "stable_tips_zh"),
)

_PREVIEW = re.compile(
    r"race\s*previews?\s*[-–—:]\s*(?P<venue>.+?)\s+"
    r"(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{2,4})", re.I)
# `[Racing To Win Interviews]: Season 25/26 | Meeting 87 | 12 Jul`
# One pipe between the season and the meeting, one between meeting and date.
# The trailing topic (`| Season finale`, `| Joao Moreira interview`) is
# optional and deliberately not captured — it is editorial, not identity.
_SHOW = re.compile(
    r"\[racing to win(?P<iv>\s+interviews)?\]\s*:?[^|]*\|\s*"
    r"meeting\s*(?P<no>\d+)\s*\|\s*(?P<d>\d{1,2})\s+(?P<mon>[A-Za-z]{3,9})", re.I)


def parse_title(title: str, *, season_hint: int | None = None) -> dict[str, Any]:
    """Meeting identity from the video title.

    Returns kind in {preview, show, interview, other}. `race_date` is ISO or
    None; the show/interview titles give a day and month but no year, so the
    year comes from `season_hint` (a HK season runs Sep–Jul, so Aug–Dec belongs
    to the opening calendar year and Jan–Jul to the next).
    """
    t = (title or "").strip()

    m = _PREVIEW.search(t)
    if m:
        y = int(m["y"])
        y += 2000 if y < 100 else 0
        venue = VENUES.get(re.sub(r"\s+", " ", m["venue"]).strip().lower())
        try:
            d = dt.date(y, int(m["m"]), int(m["d"])).isoformat()
        except ValueError:
            d = None
        return {"kind": "preview", "race_date": d, "venue": venue,
                "meeting_no": None}

    m = _SHOW.search(t)
    if m:
        kind = "interview" if m["iv"] else "show"
        month = None
        for fmt in ("%b", "%B"):
            try:
                month = dt.datetime.strptime(m["mon"][:9], fmt).month
                break
            except ValueError:
                continue
        d = None
        if month and season_hint:
            year = season_hint if month >= 8 else season_hint + 1
            try:
                d = dt.date(year, month, int(m["d"])).isoformat()
            except ValueError:
                d = None
        return {"kind": kind, "race_date": d, "venue": None,
                "meeting_no": int(m["no"])}

    m = _ZH_MEETING.search(t)
    if m:
        kind = "preview_zh" if m["kind"] == "賽前Highlights" else "review_zh"
        d = None
        if season_hint:
            month = int(m["mon"])
            year = season_hint if month >= 8 else season_hint + 1
            try:
                d = dt.date(year, month, int(m["day"])).isoformat()
            except ValueError:
                d = None
        return {"kind": kind, "race_date": d,
                "venue": VENUES_ZH.get(m["venue"]),
                "surface": SURFACE_ZH.get(m["surface"]),
                "races_billed": int(m["races"]) if m["races"] else None,
                "manual_subs_claimed": "CC中文字幕" in t,
                "meeting_no": None}

    for marker, kind in _ZH_KINDS:
        if marker in t:
            return {"kind": kind, "race_date": None, "venue": None,
                    "meeting_no": None,
                    "manual_subs_claimed": "CC中文字幕" in t}

    return {"kind": "other", "race_date": None, "venue": None, "meeting_no": None}


_CHAPTER = re.compile(
    r"\[?(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\]?\s*[-–—]?\s*"
    r"Race\s*(?P<race>\d{1,2})\b", re.I)


def parse_chapters(description: str, duration: int | None) -> list[dict]:
    """Race boundaries from the description's chapter list.

    HKJC writes `[00:02:34] - Race 2`. Accepts `2:34 Race 2` too, because a
    format that has changed once can change again and the cost of accepting
    both is one alternation.
    """
    marks: list[tuple[int, int]] = []
    for m in _CHAPTER.finditer(description or ""):
        h, mi, s = int(m["h"]), int(m["m"]), int(m["s"] or 0)
        # `[00:02:34]` is h:m:s; a bare `2:34` is m:s.
        start = h * 3600 + mi * 60 + s if m["s"] else h * 60 + mi
        marks.append((start, int(m["race"])))

    marks.sort()
    out = []
    for i, (start, race) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else (duration or 10 ** 9)
        out.append({"race_no": race, "start": start, "end": end})
    return out


# ── the transcript ───────────────────────────────────────────────────────────

def _with_fmt(url: str, fmt: str) -> str:
    """The caption URL with its format REPLACED, not appended.

    Measured 2026-09-22: the ANDROID baseUrl now arrives already carrying
    `fmt=srv3`, and a second `fmt=json3` tacked on the end is ignored — the
    first one wins. `fmt` is not among the signed `sparams`, so it can be
    swapped without invalidating the signature.
    """
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k != "fmt"] + [("fmt", fmt)]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _srv3_events(xml: str) -> list[dict]:
    """srv3 timed text: `<p t="21280" d="4200">text</p>`, times in ms.

    ASR tracks split a line into `<s>` word spans inside the `<p>`, so the
    text is every piece of it joined. Parsed rather than refused, because
    the day json3 stopped arriving this returned nothing for a transcript
    that was sitting right there — 7,313 bytes of human subtitles recorded
    as "no captions".
    """
    out = []
    for p in ET.fromstring(xml).iter("p"):
        text = "".join(p.itertext()).strip()
        if text:
            out.append({"t": round(int(p.get("t") or 0) / 1000, 2),
                        "text": text})
    return out


def _caption_events(track: dict, *, session: requests.Session) -> list[dict]:
    """One caption track -> timed events. Empty list on anything unexpected."""
    url = _with_fmt(track["baseUrl"], "json3")
    _throttle()
    r = session.get(url, timeout=60)
    if r.status_code != 200 or not r.text.strip():
        return []
    if r.text.lstrip().startswith("<"):
        try:
            return _srv3_events(r.text)
        except ET.ParseError:
            return []
    try:
        payload = r.json()
    except ValueError:
        # Neither JSON nor XML: the caption endpoint has changed shape again.
        # Recorded as no events rather than crashing the run; the manifest
        # will show it.
        return []
    out = []
    for e in (payload.get("events") or []):
        segs = e.get("segs")
        if not segs:
            continue
        text = "".join(x.get("utf8", "") for x in segs).strip()
        if text:
            out.append({"t": round(e.get("tStartMs", 0) / 1000, 2), "text": text})
    return out


def fetch_video(video_id: str, *, session: requests.Session,
                lang_prefer: tuple[str, ...] = ("en", "zh-HK", "zh-Hant", "yue"),
                fill_gaps: bool = True) -> dict:
    """Player metadata plus the timed transcript, via the ANDROID endpoint."""
    data = _post("player", {"videoId": video_id, "context": ANDROID_CTX},
                 session=session)

    status = ((data.get("playabilityStatus") or {}).get("status"))
    vd = data.get("videoDetails") or {}
    mf = ((data.get("microformat") or {}).get("playerMicroformatRenderer") or {})
    if not mf.get("publishDate"):
        # Measured 2026-09-22: the ANDROID reply carries no `microformat`, so
        # no publish date — and a Fact Check title says "9.27" with no year,
        # so the publish date is what keeps last season's 9.27 preview off
        # this season's card. The WEB client answers UNPLAYABLE for playback
        # but still returns the microformat, for one more request.
        web = _post("player", {"videoId": video_id, "context": WEB_CTX},
                    session=session)
        mf = ((web.get("microformat") or {})
              .get("playerMicroformatRenderer") or {})
    tracks = (((data.get("captions") or {})
               .get("playerCaptionsTracklistRenderer") or {})
              .get("captionTracks") or [])

    # Within a language, ALWAYS prefer a human-written track over the ASR one.
    # 賽馬Fact Check uploads real zh-HK subtitles (its titles say CC中文字幕) and
    # they are a different class of data: every horse, jockey and trainer name
    # is spelled correctly, which is exactly what ASR destroys. Picking by
    # language alone would have taken the `yue` auto track sitting right next
    # to it.
    def _rank(t: dict) -> tuple[int, int]:
        lang = t.get("languageCode")
        pos = lang_prefer.index(lang) if lang in lang_prefer else len(lang_prefer)
        return (pos, 1 if t.get("kind") == "asr" else 0)

    ranked = sorted(tracks, key=_rank) if tracks else []
    chosen = ranked[0] if ranked else None

    events = _caption_events(chosen, session=session) if chosen else []
    duration = int(vd.get("lengthSeconds") or 0) or None

    # A manual track can stop before the video does -- measured on
    # 賽馬Fact Check's 9.23 preview, the subtitles end at 370s of 601s, so the
    # last four minutes (where the analyst's selections live) are uncaptioned.
    # Reporting coverage makes that visible instead of silently truncating a
    # source. With fill_gaps, the ASR track supplies the tail, tagged so that
    # nothing downstream mistakes it for the clean text.
    covered = max((e["t"] for e in events), default=0.0)
    filled = 0
    if (fill_gaps and chosen and chosen.get("kind") != "asr" and duration
            and covered < duration * 0.9):
        asr = next((t for t in ranked if t.get("kind") == "asr"), None)
        if asr:
            tail = [e for e in _caption_events(asr, session=session)
                    if e["t"] > covered]
            for e in tail:
                e["src"] = "asr"
            events = events + tail
            filled = len(tail)

    return {
        "video_id": video_id,
        "playability": status,
        "title": vd.get("title"),
        "duration_sec": duration,
        "published": mf.get("publishDate"),
        "description": vd.get("shortDescription") or "",
        "caption_lang": chosen.get("languageCode") if chosen else None,
        "caption_kind": ("asr" if chosen.get("kind") == "asr" else "manual")
                        if chosen else None,
        "is_asr": (chosen.get("kind") == "asr") if chosen else None,
        "caption_tracks": [f"{t.get('languageCode')}:"
                           f"{'asr' if t.get('kind') == 'asr' else 'manual'}"
                           for t in tracks],
        "caption_coverage": (round(covered / duration, 3)
                             if duration and events else None),
        "asr_filled_segments": filled,
        "segments": events,
    }


def assemble(video: dict, meta: dict) -> dict:
    """Join the pieces into the record that goes on disk."""
    segs = video["segments"]
    text = " ".join(s["text"] for s in segs)
    text = re.sub(r"\s+", " ", text).strip()

    chapters = parse_chapters(video["description"], video["duration_sec"])
    by_race: dict[str, str] = {}
    for ch in chapters:
        part = [s["text"] for s in segs if ch["start"] <= s["t"] < ch["end"]]
        if part:
            by_race[str(ch["race_no"])] = re.sub(r"\s+", " ", " ".join(part)).strip()

    return {**meta, **{k: v for k, v in video.items() if k != "segments"},
            "chapters": chapters, "by_race": by_race,
            "segments": segs, "text": text}


# ── driver ───────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--playlist",
                     help="playlist id, e.g. PLK8zYRjJwINk (Racing To Win 26/27)")
    src.add_argument("--channel",
                     help="channel id, e.g. UCQAbEL38om9qqgVHGuK5BSg (全方位Bryan)")
    p.add_argument("--lang", nargs="*", default=["en", "yue", "zh-HK", "zh-Hant"],
                   help="caption languages in order of preference. Cantonese "
                        "channels want `--lang yue zh-HK`; the default tries "
                        "English first, which is right for Racing To Win.")
    p.add_argument("--out", type=Path, default=Path("./raw/rtw"))
    p.add_argument("--kinds", nargs="*",
                   choices=["preview", "show", "interview",
                            "preview_zh", "review_zh", "stable_zh",
                            "griffin_zh", "interview_zh", "stable_tips_zh",
                            "other"],
                   help="restrict to these (default: preview, show, interview)")
    p.add_argument("--season-year", type=int,
                   help="opening calendar year of the season, e.g. 2026 for "
                        "26/27. Only needed to date the show and interview "
                        "videos, whose titles carry no year.")
    p.add_argument("--limit", type=int, help="stop after N videos")
    p.add_argument("--pages", type=int, default=40,
                   help="how many pages of the listing to read, newest first. "
                        "A routine run only needs the first one or two; the "
                        "default reads a whole channel's history.")
    p.add_argument("--no-fill-gaps", action="store_true",
                   help="do not top up a short manual caption track from the "
                        "ASR one (default is to fill, tagged src=asr)")
    p.add_argument("--force", action="store_true", help="refetch existing")
    args = p.parse_args(argv)

    # A channel has no Racing To Win title convention, so kind is `other` for
    # nearly everything on one — do not filter it away by default.
    # A playlist is curated, so its own three kinds are the whole of it. A
    # channel is not, so nothing is filtered away by default there.
    kinds = set(args.kinds or (["preview", "show", "interview"] if args.playlist
                               else ["preview", "show", "interview",
                                     "preview_zh", "review_zh", "stable_zh",
                                     "griffin_zh", "interview_zh",
                                     "stable_tips_zh", "other"]))
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifest.jsonl"

    session = requests.Session()
    session.headers.update({
        "User-Agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip",
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    })

    if args.playlist:
        print(f"enumerating playlist {args.playlist} …")
        items = enumerate_playlist(args.playlist, session=session,
                                   max_pages=args.pages)
    else:
        print(f"enumerating channel {args.channel} …")
        items = enumerate_channel(args.channel, session=session,
                                  max_pages=args.pages)
    print(f"  {len(items)} videos\n")
    if not items:
        print("  nothing returned — the playlist may be private, or YouTube "
              "changed the browse response shape. Check one video by hand "
              "before assuming the playlist is empty.", file=sys.stderr)
        return 1

    tally = {"saved": 0, "skipped": 0, "no_captions": 0, "filtered": 0, "error": 0}
    done = 0

    with manifest.open("a", encoding="utf-8") as log:
        for it in items:
            meta = parse_title(it.get("title") or "", season_hint=args.season_year)
            if meta["kind"] not in kinds:
                tally["filtered"] += 1
                continue

            target = args.out / f"{it['id']}.json"
            if target.exists() and not args.force:
                tally["skipped"] += 1
                continue

            try:
                video = fetch_video(it["id"], session=session,
                                    lang_prefer=tuple(args.lang),
                                    fill_gaps=not args.no_fill_gaps)
            except Exception as exc:
                tally["error"] += 1
                print(f"  ! {it['id']}  {exc}", file=sys.stderr)
                log.write(json.dumps({"id": it["id"], "title": it.get("title"),
                                      "outcome": "error", "detail": str(exc)},
                                     ensure_ascii=False) + "\n")
                log.flush()
                continue

            record = assemble(video, {"source_id": args.playlist or args.channel,
                                      "source_title": it.get("title"), **meta})
            target.write_text(json.dumps(record, ensure_ascii=False, indent=1),
                              encoding="utf-8")

            n = len(record["segments"])
            if n == 0:
                tally["no_captions"] += 1
                outcome = "no_captions"
            else:
                tally["saved"] += 1
                outcome = "saved"

            log.write(json.dumps({
                "id": it["id"], "title": it.get("title"), "kind": meta["kind"],
                "race_date": meta["race_date"], "venue": meta["venue"],
                "meeting_no": meta["meeting_no"], "outcome": outcome,
                "segments": n, "races": sorted(record["by_race"]),
                "lang": record["caption_lang"], "asr": record["is_asr"],
                "fetched_at": dt.datetime.now(dt.timezone.utc)
                                .isoformat(timespec="seconds"),
            }, ensure_ascii=False) + "\n")
            log.flush()

            races = ",".join(sorted(record["by_race"], key=lambda x: int(x))) or "—"
            cov = record.get("caption_coverage")
            print(f"  · {meta['kind']:14s} {meta['race_date'] or '????-??-??'}  "
                  f"{str(record['caption_lang']):6s}"
                  f"{str(record['caption_kind'] or ''):7s}"
                  f"{n:4d} segs  cov {('%.0f%%' % (cov * 100)) if cov else '  —':>5s}  "
                  f"races [{races}]  {it['id']}")

            done += 1
            if args.limit and done >= args.limit:
                break

    print(f"\n  saved {tally['saved']}   skipped {tally['skipped']}   "
          f"no captions {tally['no_captions']}   "
          f"not this kind {tally['filtered']}   errors {tally['error']}")
    if tally["no_captions"]:
        print("  NOTE: 'no captions' means YouTube listed no caption track, or "
              "listed one and served nothing. The second case is the PO-token "
              "failure and would mean the ANDROID route has stopped working — "
              "check one of those videos in a browser before rerunning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
