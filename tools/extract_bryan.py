"""全方位Bryan — Cantonese, and the weakest source here by a distance.

SPEC §5 says to collect him from day one because he is free, resolve him
last, quarantine liberally, and measure the hit rate on one card before
building anything on it. Measured on 【大倉猜情尋】第5集, published the evening
before the 23 Sep meeting: 194 lines of Cantonese speech-to-text, and two of
the card's 108 runners named in the whole video — 幸運同行, and 震撼人心 written
as 真撼人心. Most of the show is about which RACES are worth playing rather
than about horses.

So this is deliberately thin. A comment is kept only where a runner's name is
found the way `_card.Card` finds Chinese names — the whole name, or a run of
three or more characters no other runner shares — and confidence is capped at
0.6, which is where SPEC §5 puts him: above the 0.55 the import quarantines
below, and below everything else on the card.

His videos carry no meeting in the title (「今晚最後來料」, "tonight's last
word"), so the meeting is taken from the publish date: the video must have
been published on the meeting day or the day before, and then the names have
to agree with that card anyway.
"""
from __future__ import annotations

import datetime as dt
import re

SOURCE = "bryan"
EXTRACTOR = "rule:bryan-v1"
CONFIDENCE = 0.60          # SPEC §5: cap him here
MAX_COMMENT = 40.0         # seconds
MIN_QUOTE = 30             # characters
KINDS = ("stable_tips_zh", "other", "preview_zh")


def is_for(rec: dict, race_date: str) -> bool:
    """Whether this video is about that meeting: published the day before
    it, or on the day."""
    if rec.get("kind") not in KINDS:
        return False
    try:
        lead = (dt.date.fromisoformat(race_date)
                - dt.date.fromisoformat((rec.get("published") or "")[:10])).days
    except ValueError:
        return False
    return 0 <= lead <= 1


def bryan_quotes(rec: dict, card, race_date: str, fetched_at: str
                 ) -> tuple[list[dict], list[dict]]:
    """(quotes, quarantine) for one of Bryan's videos."""
    url = f"https://www.youtube.com/watch?v={rec['video_id']}"
    segs = rec.get("segments") or []
    quotes: list[dict] = []
    current: dict | None = None

    def close() -> None:
        nonlocal current
        if current:
            text = "，".join(current["lines"]) + "。"
            if len(text) >= MIN_QUOTE:
                t = current["t"]
                quotes.append({
                    "source": SOURCE, "video_id": rec["video_id"],
                    "t_start": t, "url": f"{url}&t={int(t)}s",
                    "race_no": current["x"]["race_no"],
                    "horse_no": current["x"]["horse_no"],
                    "horse_said": current["said"], "speaker": "全方位Bryan",
                    "role": "analyst", "caption_kind": "asr", "quote": text,
                    "quote_en": None, "topic": None, "stance": None,
                    "confidence": CONFIDENCE, "extracted_by": EXTRACTOR,
                    "fetched_at": fetched_at})
        current = None

    for seg in segs:
        found = card.mentions(seg["text"])
        # Two runners in a line is a comparison, not a comment on either.
        if len(found) > 1:
            close()
            continue
        if found:
            _pos, said, runner, _conf = found[0]
            if not current or current["x"] is not runner:
                close()
                current = {"x": runner, "said": said, "t": seg["t"],
                           "lines": [seg["text"].strip()]}
                continue
        # A line naming nobody carries on what is being said about the horse
        # already in hand — his lines are five seconds each, and the point is
        # rarely finished in the one that names it.
        if current and seg["t"] - current["t"] <= MAX_COMMENT:
            current["lines"].append(seg["text"].strip())
        else:
            close()
    close()
    return quotes, []


def clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "")
