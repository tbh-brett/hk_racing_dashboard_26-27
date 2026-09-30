#!/usr/bin/env python3
"""賽馬Fact Check: what a preview says, and which runners it says it about.

Split out of tools/extract_tips.py on 30 Sep 2026 at its 500 lines. The
previews carry human-written subtitles, so the names in them are right and a
runner is found by its name. Three things come out of one video:

SECTIONS, from the subtitles. A preview is a run of short segments separated
by a pause of five seconds or more — the 10.1 preview: the meeting, Badel back
from suspension with his rides for 羅富全, the National Day Cup, 伍鵬志's
run of six meetings, 希斯's day racing, then the tipster's picks. Every
segment is kept whole, with the runners it names, so what is said about the
jockeys, trainers and the meeting — about no one horse — still reaches the
page (Brett, 30 Sep: "whatever is discussed ... included on the dashboard").

QUOTES, from the subtitles. A comment about a horse starts on a line that
opens with its name (「富心星四場頭馬…」) or ends with it (「游達榮馬房星期三有
紫荊傳令」), and runs until the next such line, a line naming two or more
runners (a list, not a comment), or the section's end. A horse named in the
MIDDLE of a line is being mentioned, not discussed — 「效法廐侶福進同舞林寶典」
is part of what is said about 富心星 — and gets no quote of its own; nor does
one named as the horse another was compared with or beat (「只比廐侶飛鷹翱翔…
慢四線」 is GOOD FORTUNE's trial time, 「後上鬥贏錶之銀河」 another horse's
win). One line on its own is a mention — 「包括當時亞軍精算暴雪」 in the
opening survey — shown in its section but not counted as the horse featured.

PICKS, from the tail. The analyst's selections come after the subtitles stop,
so they are speech-to-text: the race is said (「就系第二場」), then each pick
as a number and a mangled name (「7號嘅銀次勇士」 for 銀刺勇士). The number is
the identity and the name only a check; they are sent as `caption_kind: asr`
and the dashboard marks them. Brett, 2026-09-22, over SPEC §6.

Names are found in running text as WHOLE names, or as a run of three or more
characters that belongs to one runner only. Two-character fragments were
tried on the real 9.23 preview and were wrong three times out of three.
"""
from __future__ import annotations

import re

from _card import Card
from hkrd.derive import names

EXTRACTOR = "rule:factcheck-v1"
SOURCE = "factcheck"

SECTION_GAP = 5.0          # seconds of silence that end a comment
MAX_COMMENT = 75.0         # and no comment runs longer than this
OPENS_WITHIN = 6           # a name this close to the start opens a comment

_ZH_DIGITS = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9, "十": 10}
_NUM = r"(\d{1,2}|[一二兩三四五六七八九十]{1,3})"
_RACE = re.compile(r"第\s*" + _NUM + r"\s*場")
# Up to four characters after 「N號(嘅)」, not "until a particle": the
# recogniser runs names into what follows (「六號追風大家」), and comparing
# with containment finds 追風 inside it where a particle rule found nothing.
_PICK = re.compile(_NUM + r"\s*號\s*嘅?\s*([㐀-鿿]{1,4})")
_TIPSTER = re.compile(r"分析師\s*([㐀-鿿]{2,4}?)\s*(?:精選|推介|貼士)")


def _number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token.startswith("十"):                     # 十一 .. 十四
        return 10 + _ZH_DIGITS.get(token[1:], 0)
    return _ZH_DIGITS.get(token) if len(token) == 1 else None


# ── quotes, from the human subtitles ─────────────────────────────────────────

# A name right after these is the horse another was measured against or
# beat, not the subject of the line: 「只比廐侶飛鷹翱翔在另一組」 (10.1, GOOD
# FORTUNE's trial time against its stablemate's), 「後上鬥贏錶之銀河」 (9.27).
# Measured on the 9.23, 9.27 and 10.1 previews: these two were the only
# lines of 36 that opened a comment on the wrong horse.
_OBJECT_OF = re.compile(r"(?:比|贏|勝|輸俾|輸畀|輸給|擊敗|力壓|壓倒)(?:廐侶|同廐)?$")


def _opens(text: str, card: Card) -> tuple[str, dict, float] | None:
    """The runner this line opens a comment about, if it opens one."""
    found = card.mentions(text)
    if len(found) != 1:
        return None
    pos, said, x, conf = found[0]
    if _OBJECT_OF.search(text[:pos]):
        return None
    if pos <= OPENS_WITHIN or text.rstrip().endswith(said):
        return said, x, conf
    return None


def quotes_from(rec: dict, card: Card, fetched_at: str) -> list[dict]:
    segs = [s for s in rec["segments"] if s.get("src") != "asr"]
    out: list[dict] = []
    current: dict | None = None
    last_t = None

    def close() -> None:
        nonlocal current
        # One line on its own is a mention, not a segment on the horse: it
        # stays in its section (`sections_from`) and is not counted as the
        # horse featured.
        if current and len(current["lines"]) > 1:
            text = "，".join(current["lines"]) + "。"
            t = current["t"]
            out.append({
                "source": SOURCE, "video_id": rec["video_id"], "t_start": t,
                "url": f"https://www.youtube.com/watch?v={rec['video_id']}"
                       f"&t={int(t)}s",
                "race_no": current["x"]["race_no"],
                "horse_no": current["x"]["horse_no"],
                "horse_said": current["said"], "speaker": "賽馬Fact Check",
                "role": "analyst", "caption_kind": "manual", "quote": text,
                "quote_en": None, "topic": None, "stance": None,
                "confidence": current["conf"], "extracted_by": EXTRACTOR,
                "fetched_at": fetched_at})
        current = None

    for seg in segs:
        t, text = seg["t"], seg["text"].strip()
        gap = last_t is not None and t - last_t >= SECTION_GAP
        last_t = t
        if gap:
            close()
        opened = _opens(text, card)
        if opened and current and opened[1] is current["x"]:
            current["lines"].append(text)          # the same horse again
            continue
        if opened:
            close()
            said, x, conf = opened
            current = {"t": t, "x": x, "said": said, "conf": conf,
                       "lines": [text]}
            continue
        if len(card.mentions(text)) >= 2:           # a list, not a comment
            close()
            continue
        if current and t - current["t"] <= MAX_COMMENT:
            current["lines"].append(text)
        else:
            close()
    close()
    return out


# ── sections, the whole of what is said ──────────────────────────────────────

def sections(rec: dict) -> list[list[dict]]:
    """The subtitles split where the video pauses. A section of one line is
    a heading said before a pause (「講番主題賽事國慶盃創辦於1999年」) and is
    joined to the section after it."""
    out: list[list[dict]] = []
    last_t = None
    for seg in rec["segments"]:
        if seg.get("src") == "asr" or not seg["text"].strip():
            continue
        if last_t is None or seg["t"] - last_t >= SECTION_GAP:
            out.append([])
        out[-1].append(seg)
        last_t = seg["t"]
    merged: list[list[dict]] = []
    carry: list[dict] = []
    for part in out:
        part = carry + part
        carry = part if len(part) == 1 else []
        if not carry:
            merged.append(part)
    if carry:
        merged.append(carry)
    return merged


def sections_from(rec: dict, card: Card, fetched_at: str) -> list[dict]:
    """Every section of the preview as a quote on no runner, `topic`
    'section', with the card's Chinese names of the runners it names in
    `horse_said` — the page shows the section and links each runner."""
    out = []
    for part in sections(rec):
        named: list[str] = []
        for seg in part:
            for _pos, _said, x, _conf in card.mentions(seg["text"]):
                if x["name_zh"] not in named:
                    named.append(x["name_zh"])
        t = part[0]["t"]
        out.append({
            "source": SOURCE, "video_id": rec["video_id"], "t_start": t,
            "url": f"https://www.youtube.com/watch?v={rec['video_id']}&t={int(t)}s",
            "race_no": None, "horse_no": None,
            "horse_said": "、".join(named) or None, "speaker": "賽馬Fact Check",
            "role": "analyst", "caption_kind": "manual",
            "quote": "，".join(s["text"].strip() for s in part) + "。",
            "quote_en": None, "topic": "section", "stance": None,
            "confidence": None, "extracted_by": EXTRACTOR,
            "fetched_at": fetched_at})
    return out


# ── picks, from the speech-to-text tail ──────────────────────────────────────
#
# Measured on four previews (9.09, 9.13, 9.16, 9.23). Speech-to-text keeps the
# picks — 「7號嘅夢兆發」, 「8號嘅紅海咁」 — and loses the race: 「第二場」 came
# through once, 「三場四班」 once, and twice there was only 「場1000米」. So the
# race is found two ways, and they must agree when both are there:
#
#   SAID      「第N場」, or 「N場M班」
#   INFERRED  the one race whose runners AT THE HEARD NUMBERS carry the heard
#             names — at least two picks agreeing, and more than in any
#             other race. A closed list of eight to eleven races, checked.
#
# Rank is NOT the order they are read out: 9.16 lists 7, 8, 9 in number order
# and then says 「最喜歡就係…富裕君子」, which is #8. Only the pick called the
# favourite is ranked 1; the others are recorded unranked.

_RACE_CLASS = re.compile(_NUM + r"\s*場\s*[一二三四五\d]\s*班")
_FAVOURITE = re.compile(r"最喜歡|最有信心|先揀")


def _heard(text: str) -> dict[int, dict]:
    """number -> the names heard beside it, and where it was first heard."""
    heard: dict[int, dict] = {}
    for m in _PICK.finditer(text):
        if text[:m.start()].rstrip().endswith("月"):     # 「7月1號」 is a date
            continue
        no = _number(m.group(1))
        name = m.group(2).split("嘅")[-1]                # 「二號嘅路路」
        if no is None or not 1 <= no <= 14 or "號" in name:
            continue
        got = heard.setdefault(no, {"names": [], "pos": m.start()})
        got["names"].append(name)
    return heard


def _best_name(got: dict, name_zh: str | None) -> str:
    if not name_zh:
        return max(got["names"], key=len)
    return max(got["names"], key=lambda n: (names.similarity(n, name_zh),
                                            len(n)))


def _agrees(got: dict, no: int, field: dict[int, str | None]) -> bool:
    own = field.get(no)
    if not own:
        return False
    name = _best_name(got, own)
    return (names.similarity(name, own) > 0 and names.verdict_heard(
        name, own, [v for k, v in field.items() if k != no]) is None)


def infer_race(heard: dict[int, dict], card: Card) -> tuple[int | None, dict]:
    """The race the heard picks belong to, and every race's agreeing count."""
    votes = {}
    for race_no in sorted({x["race_no"] for x in card.runners}):
        field = {x["horse_no"]: x["name_zh"] for x in card.race(race_no)}
        votes[race_no] = sum(_agrees(g, no, field) for no, g in heard.items())
    ranked = sorted(votes.values(), reverse=True)
    top = ranked[0] if ranked else 0
    if top >= 2 and (len(ranked) == 1 or top > ranked[1]):
        return next(r for r, v in votes.items() if v == top), votes
    return None, votes


def _favourite(text: str, heard: dict[int, dict],
               field: dict[int, str | None]) -> int | None:
    """The pick called the favourite: a number said just after 最喜歡 /
    最有信心 / 先揀, or failing that a picked horse whose name is there."""
    m = _FAVOURITE.search(text)
    if not m:
        return None
    window = text[m.end():m.end() + 16]
    num = re.search(_NUM + r"\s*號", window)
    if num and _number(num.group(1)) in heard:
        return _number(num.group(1))
    for no in sorted(heard, key=lambda n: heard[n]["pos"]):
        name = field.get(no) or ""
        if any(name[i:i + 2] in window for i in range(len(name) - 1)):
            return no
    return None


def picks_from(rec: dict, card: Card, fetched_at: str
               ) -> tuple[list[dict], list[dict]]:
    """(selections, quarantine). Joined without spaces: the recogniser splits
    lines mid-name (「7號嘅銀」 / 「次勇士啦」)."""
    tail = [s for s in rec["segments"] if s.get("src") == "asr"]
    if not tail:
        return [], []
    starts, text = [], ""
    for s in tail:
        starts.append((len(text), s["t"]))
        text += s["text"].strip()

    def time_at(pos: int) -> float:
        return max(t for p, t in starts if p <= pos)

    url = f"https://www.youtube.com/watch?v={rec['video_id']}"
    tipster = (m.group(1) if (m := _TIPSTER.search(rec.get("description")
                                                   or "")) else "賽馬Fact Check")
    heard = _heard(text)
    if not heard:
        return [], []

    said_m = _RACE.search(text) or _RACE_CLASS.search(text)
    said = _number(said_m.group(1)) if said_m else None
    inferred, votes = infer_race(heard, card)

    def hold(why: str) -> tuple[list, list]:
        return [], [{"source": SOURCE, "race_date": rec["race_date"],
                     "race_no": said or inferred, "reason": "unparsed",
                     "raw": f"{tipster}'s picks: {why} — heard "
                            + ", ".join(f"{n}號{g['names'][0]}"
                                        for n, g in sorted(heard.items())),
                     "url": url, "fetched_at": fetched_at}]

    if said and inferred and said != inferred:
        return hold(f"race {said} was said, the names point to race "
                    f"{inferred}")
    race_no = said or inferred
    if race_no is None:
        return hold("no race said, and no one race the names agree with")

    field = {x["horse_no"]: x["name_zh"] for x in card.race(race_no)}
    favourite = _favourite(text, heard, field)

    picks = []
    for no, got in sorted(heard.items(), key=lambda kv: kv[1]["pos"]):
        if no not in field:
            continue
        t = time_at(got["pos"])
        picks.append({
            "source": SOURCE, "tipster": tipster, "race_no": race_no,
            "horse_no": no, "pick_rank": 1 if no == favourite else None,
            "name_seen": _best_name(got, field.get(no)), "note": None,
            "caption_kind": "asr", "url": f"{url}&t={int(t)}s",
            "published_at": rec.get("published"), "fetched_at": fetched_at})
    return picks, []
