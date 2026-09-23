"""Racing To Win's race previews — the pundit's selections, not the whole card.

`Race previews - Happy Valley 23/09/26` is forty minutes of Mark McNamara and
Paul Lally going through all nine races, with a chapter per race in the
description ([00:18:39] - Race 6). Brett, 2026-09-23: talk about every runner
on the card buries the picks. So what is kept is what the show SELECTS.

Every chapter closes the same way, measured on all nine races that night:
the selections read out as numbers, then the top pick named —

    "14612 … He's going to make it three in a row. Jumbo blessing for Paul."
    "Anyway, 93 8 and 12. King Miles the selection for Paul in race seven."

The numbers are the picks, in order. Speech-to-text runs them together
("14612"), so they are split against the race's own field — every number a
runner, none repeated, three to five of them — and where more than one
split fits, the one whose first number is the horse named on top wins. If
none does, the race is held, never guessed.

Each pick keeps only what was said about it in that closing stretch, a
sentence or two: "He goes on top shooting to top. I think he's ready to win."
The gear changes and the walk through the field are left out.
"""
from __future__ import annotations

import re

import _scan

SOURCE = "rtw_preview"
EXTRACTOR = "rule:rtw-preview-v2"
VERDICT_WINDOW = 90.0      # seconds before a chapter ends: where the picks are
MAX_LINES = 3              # sentences kept per pick
_PRESENTERS = re.compile(r"^(.+?)\s+preview", re.I)
_TOP_WORDS = re.compile(r"\bfor [A-Z][a-z]+\b|\bon top\b|\bselection\b|"
                        r"\bwinning\b")
_FOR_NAME = re.compile(r"\bfor ([A-Z][a-z]+)\b")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
          "twelve": 12, "thirteen": 13, "fourteen": 14}


def presenters(description: str) -> str:
    """"Mark McNamara and Paul Lally preview all nine races…"."""
    first = (description or "").strip().splitlines()[0] if description else ""
    m = _PRESENTERS.match(first.strip())
    return m.group(1).strip() if m else "Racing To Win"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(">>", " ")).strip()


def sentences(segs: list[dict]) -> list[dict]:
    """The chapter as whole sentences, each with the time it starts.

    Captions break mid-name — "Red / brick warrior wears a shadow roll" is
    two lines — so a line is the wrong unit both for finding a name and for
    quoting what was said about it.
    """
    text, at = "", []
    for seg in segs:
        if text and not text.endswith(" "):
            text += " "
        at.append((len(text), seg["t"]))
        text += seg["text"].strip()
    out = []
    pos = 0
    for part in re.split(r"(?<=[.!?])\s+", text):
        start = text.index(part, pos)
        pos = start + len(part)
        out.append({"t": max(t for p, t in at if p <= start),
                    "text": _clean(part)})
    return [s for s in out if s["text"]]


# ── the numbers ──────────────────────────────────────────────────────────────

def numbers_said(sentence: str) -> list[str]:
    """The run of numbers a sentence ENDS with: "So we ended up with 82 11
    and five." -> ["82", "11", "five"]. A number mid-sentence ("the 10 pound
    claim") is part of what is being said, not a selection."""
    run: list[str] = []
    for tok in reversed(re.findall(r"[A-Za-z]+|\d+", sentence)):
        low = tok.lower()
        if tok.isdigit() or low in _WORDS:
            run.append(tok)
        elif low == "and" and run:
            continue
        else:
            break
    return list(reversed(run))


def splits(tokens: list[str], field: set[int]) -> list[list[int]]:
    """Every way the spoken tokens read as distinct runners in this field.

    "14612" in a field of fourteen is 1-4-6-12, 14-6-12 or 14-6-1-2; "113"
    in a field of twelve can only be 11-3.
    """
    found: list[list[int]] = []

    def walk(i: int, acc: list[int]) -> None:
        if i == len(tokens):
            found.append(acc)
            return
        tok = tokens[i]
        if not tok.isdigit():
            n = _WORDS[tok.lower()]
            if n in field and n not in acc:
                walk(i + 1, acc + [n])
            return

        def digits(rest: str, acc2: list[int]) -> None:
            if not rest:
                walk(i + 1, acc2)
                return
            for size in (1, 2):
                if len(rest) >= size and rest[0] != "0":
                    n = int(rest[:size])
                    if n in field and n not in acc2:
                        digits(rest[size:], acc2 + [n])
        digits(tok, acc)

    walk(0, [])
    return [s for s in found if 3 <= len(s) <= 5]


def _window(lines: list[dict]) -> list[dict]:
    end = lines[-1]["t"] if lines else 0
    return [ln for ln in lines if end - ln["t"] <= VERDICT_WINDOW]


def _is_numbers_line(text: str) -> bool:
    tokens = numbers_said(text)
    return len(tokens) >= 2 or bool(tokens and len(tokens[0]) >= 3)


def picks_in(lines: list[dict], field: list[dict]
             ) -> tuple[list[int] | None, dict | None, str]:
    """(the picks in order, the runner named on top, why not if not)."""
    numbers = {x["horse_no"] for x in field}
    window = _window(lines)

    top = None
    for ln in window:
        named = _scan.english_mentions([ln], field)
        if len(named) == 1 and _TOP_WORDS.search(ln["text"]):
            top = named[0].runner

    for ln in reversed(window):
        if not _is_numbers_line(ln["text"]):
            continue
        tokens = numbers_said(ln["text"])
        # The run can start with the tail of a horse's NAME: "Uh Rainbow 7
        # 2483" is RAINBOW SEVEN then the picks 2-4-8-3. So readings with
        # leading tokens dropped are tried too, and the one agreeing with
        # the horse named on top is preferred.
        options = [o for k in range(len(tokens))
                   for o in splits(tokens[k:], numbers)]
        if top:
            options = [o for o in options if o[0] == top["horse_no"]] or options
        options = [o for o in options if len(o) == 4] or options
        if len({tuple(o) for o in options}) > 1:
            # Still more than one: the reading using the most of what was
            # said wins — dropping a token is the exception, not the rule.
            longest = max(len("".join(map(str, o))) for o in options)
            options = [o for o in options
                       if len("".join(map(str, o))) == longest]
        unique = {tuple(o) for o in options}
        if len(unique) == 1:
            return list(unique.pop()), top, ""
        if unique:
            return None, top, (f"the numbers {' '.join(tokens)!r} read more "
                               f"than one way: {sorted(unique)}")
    return None, top, "no selection numbers in the chapter's closing minute"


# ── the preview ──────────────────────────────────────────────────────────────

def preview(rec: dict, card, fetched_at: str
            ) -> tuple[list[dict], list[dict], list[dict]]:
    """(quotes, selections, quarantine) for one preview video."""
    url = f"https://www.youtube.com/watch?v={rec['video_id']}"
    chapters = rec.get("chapters") or []
    if not chapters:
        return [], [], [{"source": SOURCE, "race_date": rec["race_date"],
                         "race_no": None, "reason": "unparsed", "url": url,
                         "raw": f"no chapter list in the description of "
                                f"{rec.get('title')}", "fetched_at": fetched_at}]
    show = presenters(rec.get("description") or "")
    # Whose selections: named per race where a chapter closes "… for Paul",
    # and otherwise the pundit most chapters name — the show has one
    # selector ("Paul Lally here to provide all of the selections").
    by_chapter = [_pundit(sentences([s for s in rec["segments"]
                                     if c["start"] <= s["t"] < c["end"]]), show)
                  for c in chapters]
    named = [p for p in by_chapter if p != show]
    usual = max(set(named), key=named.count) if named else show
    quotes, picks, held = [], [], []
    for chapter in chapters:
        race_no = chapter["race_no"]
        field = card.race(race_no)
        lines = sentences([s for s in rec["segments"]
                           if chapter["start"] <= s["t"] < chapter["end"]])
        if not field or not lines:
            continue
        order, _top, why = picks_in(lines, field)
        if not order:
            held.append({"source": SOURCE, "race_date": rec["race_date"],
                         "race_no": race_no, "reason": "unparsed", "url": url,
                         "fetched_at": fetched_at,
                         "raw": f"race {race_no} selections: {why}"})
            continue
        tipster = _pundit(lines, show)
        if tipster == show:
            tipster = usual
        by_no = {x["horse_no"]: x for x in field}
        said = _closing_words(lines, field, order)
        for rank, no in enumerate(order, start=1):
            kept = said.get(no, [])
            first_t = kept[0]["t"] if kept else lines[-1]["t"]
            text = " ".join(ln["text"] for ln in kept) or None
            picks.append({
                "source": SOURCE, "tipster": tipster, "race_no": race_no,
                "horse_no": no, "pick_rank": rank,
                "name_seen": by_no[no]["horse_name"], "note": text,
                "caption_kind": None, "url": f"{url}&t={int(first_t)}s",
                "published_at": rec.get("published"), "fetched_at": fetched_at})
            if kept:
                quotes.append({
                    "source": SOURCE, "video_id": rec["video_id"],
                    "t_start": first_t, "url": f"{url}&t={int(first_t)}s",
                    "race_no": race_no, "horse_no": no,
                    "horse_said": kept[0]["said"], "speaker": tipster,
                    "role": "presenter", "caption_kind": "asr", "quote": text,
                    "quote_en": None, "topic": None, "stance": None,
                    "confidence": kept[0]["score"], "extracted_by": EXTRACTOR,
                    "fetched_at": fetched_at})
    return quotes, picks, held


def _pundit(lines: list[dict], show: str) -> str:
    """Whose selections these are: "… for Paul", matched against the
    presenters the description names, "Mark McNamara and Paul Lally"."""
    people = [p.strip() for p in re.split(r"\s+and\s+|,\s*", show) if p.strip()]
    for ln in reversed(lines):
        m = _FOR_NAME.search(ln["text"])
        if m:
            for person in people:
                if person.split()[0] == m.group(1):
                    return person
    return show


def _closing_words(lines: list[dict], field: list[dict],
                   order: list[int]) -> dict[int, list[dict]]:
    """What was said about each pick in the chapter's closing stretch: the
    sentence naming it, and the one after if that one names nobody."""
    picked = [x for x in field if x["horse_no"] in order]
    out: dict[int, list[dict]] = {}
    current = None
    for ln in _window(lines):
        if _is_numbers_line(ln["text"]):
            current = None
            continue
        named = _scan.english_mentions([ln], picked)
        if len(named) == 1:
            current = named[0].runner["horse_no"]
            kept = out.setdefault(current, [])
            if len(kept) < MAX_LINES:
                kept.append({"t": ln["t"], "text": ln["text"],
                             "said": named[0].said, "score": named[0].score})
            continue
        if len(named) > 1 or current is None:
            current = None
            continue
        kept = out.setdefault(current, [])
        if len(kept) < MAX_LINES and not _scan.english_mentions([ln], field):
            kept.append({"t": ln["t"], "text": ln["text"],
                         "said": kept[0]["said"] if kept else "",
                         "score": kept[0]["score"] if kept else 0.0})
        current = None
    return {no: v for no, v in out.items() if v}
