#!/usr/bin/env python3
"""Turn harvested transcripts into a tips payload for one meeting.

    python tools/extract_tips.py --race-date 2026-09-23 --dry-run
    python tools/extract_tips.py --upcoming            # every meeting from today

Runs on this PC, beside the harvesters, and asks nothing of any model: every
runner is picked by rule from the card the DASHBOARD holds (`GET
/api/tips/roster/<date>`), which is the same card the import checks the
numbers against. Brett, 2026-09-22: no paid model calls.

Two sources so far. Racing To Win's interviews are `extract_rtw.py`: HKJC
indexes each one in the video's description, so identity is exact and only
the answers are taken from the transcript. 賽馬Fact Check is below. Its
previews carry human-written subtitles, so the names in them are right and a
runner is found by its name. Two things come out of one video:

QUOTES, from the subtitles. A comment about a horse starts on a line that
opens with its name (「富心星四場頭馬…」) or ends with it (「游達榮馬房星期三有
紫荊傳令」), and runs until the next such line, a line naming two or more
runners (a list, not a comment), or a pause of five seconds or more, which is
where the video changes section. A horse named in the MIDDLE of a line is
being mentioned, not discussed — 「效法廐侶福進同舞林寶典」 is part of what is
said about 富心星 — and gets no quote of its own.

PICKS, from the tail. The analyst's selections come after the subtitles stop,
so they are speech-to-text: the race is said (「就系第二場」), then each pick
as a number and a mangled name (「7號嘅銀次勇士」 for 銀刺勇士). The number is
the identity and the name only a check; they are sent as `caption_kind: asr`
and the dashboard marks them. Brett, 2026-09-22, over SPEC §6.

Names are found in running text as WHOLE names, or as a run of three or more
characters that belongs to one runner only. Two-character fragments were
tried on the real 9.23 preview and were wrong three times out of three.

--dry-run prints what was found and against which runner, and writes nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _dashboard as dash                                  # noqa: E402
import extract_bryan as bryan                              # noqa: E402
import extract_threads as threads                          # noqa: E402
import extract_rtw as rtw                                  # noqa: E402
import extract_rtw_preview as preview                      # noqa: E402
from _card import Card                                     # noqa: E402
from hkrd.derive import names                              # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EXTRACTOR = "rule:factcheck-v1"
SOURCE = "factcheck"
HK = dt.timezone(dt.timedelta(hours=8))

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

def _opens(text: str, card: Card) -> tuple[str, dict, float] | None:
    """The runner this line opens a comment about, if it opens one."""
    found = card.mentions(text)
    if len(found) != 1:
        return None
    pos, said, x, conf = found[0]
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
        if current:
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


# ── one meeting ──────────────────────────────────────────────────────────────

# Where each source's harvest lands, and the kind of video in it that says
# something about a coming meeting.
FOLDERS = {"factcheck": ("factcheck", "preview_zh"),
           rtw.SOURCE: ("rtw", "interview"),
           preview.SOURCE: ("rtw", "preview")}


def _read(folder: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(folder.glob("*.json"))]


def transcripts_for(date: str, raw: Path, source: str = SOURCE) -> list[dict]:
    """This meeting's videos from one source. A title's date has no year
    (「9.23」, "16 Sep"), so a video must also have been PUBLISHED within ten
    days before the meeting — otherwise last season's 「9.27田草」 would land on
    this season's 27th."""
    folder, kind = FOLDERS[source]
    out = []
    for rec in _read(raw / folder):
        if rec.get("kind") != kind or rec.get("race_date") != date:
            continue
        try:
            # publishDate is in YouTube's own (Pacific) time; a day either
            # way is tolerated, a season is not.
            lead = (dt.date.fromisoformat(date) - dt.date.fromisoformat(
                (rec.get("published") or "")[:10])).days
        except ValueError:
            continue
        if -1 <= lead <= 10 and rec.get("segments"):
            out.append(rec)
    return out


def extract(date: str, raw: Path, roster: dict) -> dict | None:
    """One meeting's payload, or None if no source had anything to say.

    `sources` names only what was actually read: a source with no video on
    disk for this meeting sends nothing, and so has nothing taken off the
    card on its behalf.
    """
    card = Card(roster)
    fetched = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)         .isoformat().replace("+00:00", "Z")
    quotes, picks, held, sources = [], [], [], []

    fc = transcripts_for(date, raw, SOURCE)
    print(f"  {date}: {len(fc)} Fact Check preview(s) · card "
          f"{roster['runners']} runners, {roster['named']} with Chinese names")
    if fc and not card.named:
        print(f"  {date}: no Chinese names on the dashboard yet — the name "
              f"sync runs at 07:30 and 13:30. Fact Check not extracted.")
    elif fc:
        sources.append(SOURCE)
        for rec in fc:
            quotes += quotes_from(rec, card, fetched)
            p, h = picks_from(rec, card, fetched)
            picks += p
            held += h

    iv = transcripts_for(date, raw, rtw.SOURCE)
    print(f"  {date}: {len(iv)} Racing To Win interview video(s)")
    if iv:
        sources.append(rtw.SOURCE)
        for rec in iv:
            q, h = rtw.interview_quotes(rec, card.runners, fetched)
            quotes += q
            held += h

    pv = transcripts_for(date, raw, preview.SOURCE)
    print(f"  {date}: {len(pv)} Racing To Win race preview video(s)")
    if pv:
        sources.append(preview.SOURCE)
        for rec in pv:
            q, p, h = preview.preview(rec, card, fetched)
            quotes += q
            picks += p
            held += h

    # Bryan's titles carry no meeting, so his videos are matched by the day
    # they were published rather than by a date in the title.
    bry = [rec for rec in _read(raw / "bryan") if bryan.is_for(rec, date)]
    print(f"  {date}: {len(bry)} 全方位Bryan video(s)")
    if bry and card.named:
        sources.append(bryan.SOURCE)
        for rec in bry:
            q, h = bryan.bryan_quotes(rec, card, date, fetched)
            quotes += q
            held += h

    # Horse Detective's posts carry no meeting either; the same rule, by the
    # Hong Kong day they went up. Chinese names only, so the card's are needed.
    hd = [p for p in threads.read(raw / "threads") if threads.is_for(p, date)]
    print(f"  {date}: {len(hd)} Horse Detective analysis post(s)")
    if hd and card.named:
        sources.append(threads.SOURCE)
        picks += threads.selections(hd, date, fetched)

    if not sources:
        return None
    return {"payload_version": 1, "race_date": date, "generated_at": fetched,
            "extractor": "rule:tips-v1", "sources": sources, "quotes": quotes,
            "selections": picks, "quarantine": held}


def show(payload: dict, card_by: dict[tuple[int, int], dict]) -> None:
    def who(r, h):
        x = card_by.get((r, h), {})
        return f"R{r} #{h:<2} {x.get('horse_name', '?')} ({x.get('name_zh')})"
    for q in payload["quotes"]:
        t = int(q["t_start"])
        print(f"    quote {t // 60}:{t % 60:02d}  {q['speaker']} ({q['role']}) "
              f"on {q['horse_said']} -> {who(q['race_no'], q['horse_no'])}  "
              f"conf {q['confidence']}  「{q['quote'][:30]}…」")
    for s in payload["selections"]:
        x = card_by.get((s["race_no"], s["horse_no"]), {})
        race = [v.get("name_zh") for k, v in card_by.items()
                if k[0] == s["race_no"] and k[1] != s["horse_no"]]
        check = names.verdict_heard(s["name_seen"], x.get("name_zh"), race)
        print(f"    pick  {s['tipster']} #{s['pick_rank']}  heard "
              f"「{s['name_seen']}」 -> {who(s['race_no'], s['horse_no'])}  "
              f"[{s['caption_kind']}] {check or 'name agrees'}")
    for h in payload["quarantine"]:
        print(f"    held  {h['reason']}: {h['raw'][:60]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    when = ap.add_mutually_exclusive_group(required=True)
    when.add_argument("--race-date", help="YYYY-MM-DD")
    when.add_argument("--upcoming", action="store_true",
                      help="every meeting from today with a harvested preview")
    ap.add_argument("--raw", type=Path, default=REPO / "raw")
    ap.add_argument("--out", type=Path, default=REPO / "out")
    ap.add_argument("--base", help=f"dashboard URL (default {dash.DEFAULT_BASE}"
                                    f" or HKRD_BASE)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what was found; write nothing")
    a = ap.parse_args(argv)

    if a.upcoming:
        today = dt.datetime.now(HK).date().isoformat()
        dates = sorted({json.loads(p.read_text(encoding="utf-8"))
                        .get("race_date") or ""
                        for folder, _ in FOLDERS.values()
                        for p in (a.raw / folder).glob("*.json")})
        dates = [d for d in dates if d >= today]
    else:
        dates = [a.race_date]
    if not dates:
        print("  no upcoming meeting among the harvested previews")
        return 0

    base = dash.base_url(a.base)
    try:
        session = dash.connect(base)
        for date in dates:
            roster = dash.get(session, base, f"/api/tips/roster/{date}")
            if roster is None:
                print(f"  {date}: the dashboard has no card for this date yet")
                continue
            payload = extract(date, a.raw, roster)
            if payload is None:
                continue
            card_by = {(r["race_no"], x["horse_no"]): x
                       for r in roster["races"] for x in r["runners"]}
            show(payload, card_by)
            if a.dry_run:
                continue
            a.out.mkdir(parents=True, exist_ok=True)
            target = a.out / f"{date}.tips.json"
            target.write_text(json.dumps(payload, ensure_ascii=False,
                                         indent=1), encoding="utf-8")
            print(f"  wrote {target}")
    except dash.DashboardError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
