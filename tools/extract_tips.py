#!/usr/bin/env python3
"""Turn harvested transcripts into a tips payload for one meeting.

    python tools/extract_tips.py --race-date 2026-09-23 --dry-run
    python tools/extract_tips.py --upcoming            # every meeting from today

Runs on this PC, beside the harvesters, and asks nothing of any model: every
runner is picked by rule from the card the DASHBOARD holds (`GET
/api/tips/roster/<date>`), which is the same card the import checks the
numbers against. Brett, 2026-09-22: no paid model calls.

Each source has its rules in a module of its own: 賽馬Fact Check's in
`extract_factcheck.py`, Racing To Win's interviews in `extract_rtw.py` and
previews in `extract_rtw_preview.py`, 全方位Bryan's in `extract_bryan.py` and
Horse Detective's in `extract_threads.py`. This file finds each source's
videos or posts for a meeting and puts what they say into one payload.

--dry-run prints what was found and against which runner, and writes nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
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
from extract_factcheck import (SOURCE, _heard, picks_from,  # noqa: E402,F401
                               quotes_from, sections_from)
from hkrd.derive import names                              # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HK = dt.timezone(dt.timedelta(hours=8))


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
            quotes += sections_from(rec, card, fetched)
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
        if q.get("topic") == "section":
            print(f"    section {t // 60}:{t % 60:02d}  names "
                  f"{q['horse_said'] or '—'}  「{q['quote'][:40]}…」")
            continue
        print(f"    quote {t // 60}:{t % 60:02d}  {q['speaker']} ({q['role']}) "
              f"on {q['horse_said']} -> {who(q['race_no'], q['horse_no'])}  "
              f"conf {q['confidence']}  「{q['quote'][:30]}…」")
    for s in payload["selections"]:
        x = card_by.get((s["race_no"], s["horse_no"]), {})
        # The name in the language it was given in: an English pick checked
        # against the Chinese card read "name_unknown" on every horse.
        lang = "name_zh" if names.is_chinese(s["name_seen"]) else "horse_name"
        race = [v.get(lang) for k, v in card_by.items()
                if k[0] == s["race_no"] and k[1] != s["horse_no"]]
        check = names.verdict_heard(s["name_seen"], x.get(lang), race)
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
