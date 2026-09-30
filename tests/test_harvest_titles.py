"""tools/harvest_youtube.parse_title — a meeting's identity from a video title.

Every title here is verbatim from the channel or playlist, 28 Sep 2026. The
27 Sep Fact Check preview wrote its venue in full and its card as 8 turf and
3 all-weather races; the reader knew only 「9.23谷草」 and filed it as
"other", so it was never fetched and the meeting had no Fact Check at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from harvest_youtube import _LOOKS_DATED, parse_title       # noqa: E402

TAIL = " … CC中文字幕#賽馬FactCheck"


@pytest.mark.parametrize("title, date, venue, surface, races", [
    ("賽前Highlights｜9.23谷草9場夜馬 周日跑唔成拎保護權再報名！",
     "2026-09-23", "HV", "Turf", 9),
    ("賽前Highlights｜9.27沙田8草3泥11場日馬 沙田泥賽周日開打",
     "2026-09-27", "ST", None, 11),
    ("賽前Highlights｜9.6開鑼戰沙田10場 文家良自強不息打韓戰",
     "2026-09-06", "ST", None, 10),
    ("賽前Highlights｜9.9谷草開鑼戰 袁幸堯谷草初次登場",
     "2026-09-09", "HV", "Turf", None),
    ("賽前Highlights｜9.13田草10場日馬 五駒開鑼跑完熱身",
     "2026-09-13", "ST", "Turf", 10),
])
def test_every_fact_check_preview_form_is_dated(title, date, venue, surface,
                                                 races):
    got = parse_title(title + TAIL, season_hint=2026)
    assert got["kind"] == "preview_zh"
    assert got["race_date"] == date
    assert (got["venue"], got["surface"], got["races_billed"]) == \
        (venue, surface, races)
    assert got["manual_subs_claimed"] is True


def test_a_review_is_a_review_whatever_follows_the_date():
    got = parse_title("賽後Spotlights｜9.27上演慶典盃 廖康銘憑白鷺金剛蟬聯G3"
                      + TAIL, season_hint=2026)
    assert (got["kind"], got["race_date"], got["venue"]) == \
        ("review_zh", "2026-09-27", None)


def test_a_venue_word_later_in_the_title_is_not_the_venue():
    # Only the words joined to the date are read: 「沙田」 in the headline
    # further on says nothing about which course the meeting is at.
    got = parse_title("賽前Highlights｜9.23谷草9場夜馬 沙田泥賽周日開打",
                      season_hint=2026)
    assert got["venue"] == "HV"


@pytest.mark.parametrize("title, kind, date", [
    ("Race previews – Sha Tin 27/09/26", "preview", "2026-09-27"),
    ("Race previews – Shatin 13/09/26", "preview", "2026-09-13"),
    ("[Racing To Win Interviews]: Season 26/27 | Meeting 06 | 27 Sep",
     "interview", "2026-09-27"),
    ("[Racing To Win]: Season 26/27 | Meeting 06 | 27 Sep | Saffie Osborne "
     "interview", "show", "2026-09-27"),
])
def test_racing_to_win_titles(title, kind, date):
    got = parse_title(title, season_hint=2026)
    assert (got["kind"], got["race_date"]) == (kind, date)


def test_an_undatable_preview_title_is_flagged_not_filtered():
    title = "賽前Highlights｜開鑼戰 沙田10場"
    assert parse_title(title, season_hint=2026)["kind"] == "other"
    assert _LOOKS_DATED.search(title)
    assert not _LOOKS_DATED.search("兵馬檢閱｜告東尼擅長調教歐洲馬")


@pytest.mark.parametrize("parsed, published, want", [
    # Last season's July preview, read with this season's hint.
    ("2027-07-12", "2026-07-10T05:00:06-07:00", "2026-07-12"),
    # This season's: the hint was right and stays right.
    ("2026-09-27", "2026-09-25T05:00:09-07:00", "2026-09-27"),
    # A New Year meeting previewed in December.
    ("2026-01-01", "2026-12-30T05:00:00-08:00", "2027-01-01"),
    (None, "2026-09-25T05:00:09-07:00", None),
    ("2026-09-27", None, "2026-09-27"),
])
def test_the_year_comes_from_the_upload(parsed, published, want):
    from harvest_youtube import dated_near
    assert dated_near(parsed, published) == want


# ── a video read before its captions were ready ─────────────────────────────

def _stored(tmp_path, **rec):
    import json
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"published": "2026-09-28T05:00:08-07:00", **rec}),
                 encoding="utf-8")
    return p


def test_a_video_stored_without_captions_is_asked_again(tmp_path):
    # Fact Check's 10.1 preview, read at 20:31 on 28 Sep, 31 minutes after
    # it went up: the subtitle track was listed and served nothing.
    import datetime as dt
    from harvest_youtube import incomplete
    empty = _stored(tmp_path, segments=[], caption_kind="manual")
    assert incomplete(empty, today=dt.date(2026, 9, 30))
    # A week on it stops asking: some videos never get captions.
    assert not incomplete(empty, today=dt.date(2026, 10, 9))


def test_a_short_human_track_waits_for_its_speech_to_text_tail(tmp_path):
    import datetime as dt
    from harvest_youtube import incomplete
    seg = [{"t": 1.0, "text": "x"}]
    short = _stored(tmp_path, segments=seg, caption_kind="manual",
                    caption_coverage=0.62, asr_filled_segments=0)
    assert incomplete(short, today=dt.date(2026, 9, 30))
    done = _stored(tmp_path, segments=seg, caption_kind="manual",
                   caption_coverage=0.62, asr_filled_segments=36)
    assert not incomplete(done, today=dt.date(2026, 9, 30))
    whole = _stored(tmp_path, segments=seg, caption_kind="asr", caption_coverage=1.0)
    assert not incomplete(whole, today=dt.date(2026, 9, 30))
