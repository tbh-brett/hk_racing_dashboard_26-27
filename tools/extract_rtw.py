"""Racing To Win interviews — the connections, in their own words.

HKJC's own interview videos (playlist PLK8zYRjJwINk, `[Racing To Win
Interviews]: … | Meeting 04 | 16 Sep`) are English speech-to-text, so the
names in the TRANSCRIPT are unreliable. But the DESCRIPTION is an exact index,
typed by a person, and it names everything the join needs:

    Race 3
    INTERVIEW:  Karis Teetan ( 6. ROSEWOOD FLEETFOOT )

So identity never comes from the transcript. It comes from the index — race,
horse number, the horse's name as HKJC spells it, and who is talking — and the
dashboard checks that name against the runner at that number on its own card.
The transcript is only asked WHERE each interview is: the interviews run in
the index's order, and each opens with a question naming its horse ("Andrea,
you pick up the ride on Horse Power in race seven"), which speech-to-text gets
close enough to find. An interview whose horse cannot be found in its question
is held, never guessed at.

Within an interview the turns alternate at the `>>` speaker marks: question,
answer, question, answer (see `_answers` for when speech-to-text misses a
mark). Each ANSWER is one quote, linked to the second it
starts — "I've sat on him yesterday and he actually felt really nice" is
worth one click to hear said. The questions are not quotes; the presenter is
not the connection.

Measured on the four interviews of the 26/27 season so far (6, 9, 13 and 16
Sep): 14 interviews indexed, 14 found, every speaker matched to the runner's
jockey or trainer by surname — "Nichola Yuen" is H Y Yuen, "Vincent Ho" is
C Y Ho.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

SOURCE = "rtw_interview"
EXTRACTOR = "rule:rtw-interview-v1"
CONFIDENCE = 0.90          # identity from HKJC's own index, text from ASR
OPENS_VIDEO = 0.80         # found by being first, not by its name
FOUND_AT = 0.80            # how close the horse's name must come in the question
MIN_ANSWER = 25            # characters; "Yeah." is not a quote

_INDEX = re.compile(
    r"Race\s+(\d{1,2})\s*[\r\n]+\s*INTERVIEW\s*:\s*(.+?)\s*"
    r"\(\s*(\d{1,2})\s*\.\s*(.+?)\s*\)", re.I)


def _flat(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def index(description: str) -> list[dict[str, Any]]:
    """The interviews the description lists, in order."""
    return [{"race_no": int(r), "speaker": re.sub(r"\s+", " ", who).strip(),
             "horse_no": int(no), "horse_name": re.sub(r"\s+", " ", name)
             .strip().upper()}
            for r, who, no, name in _INDEX.findall(description or "")]


def _found(name: str, window: str) -> bool:
    """The horse's name in a stretch of transcript, allowing for the
    recogniser's spacing ("Horse Power") and a letter or two."""
    target, text = _flat(name), _flat(window)
    if not target:
        return False
    if target in text:
        return True
    n = len(target)
    return any(SequenceMatcher(None, target, text[i:i + n]).ratio() >= FOUND_AT
               for i in range(0, max(1, len(text) - n + 1)))


def _surname(name: str) -> str:
    """The family name, with an apprentice's claim taken off first: a race
    card writes the rider as "C L Chau (-2)", a results page as "C L Chau"."""
    parts = re.sub(r"\(.*?\)", " ", name or "").split()
    return parts[-1].upper() if parts else ""


def _role(speaker: str, runner: dict | None,
          everyone: list[dict]) -> str | None:
    """jockey or trainer, by surname: the card says 'H Y Yuen (-10)', the
    index says 'Nichola Yuen'. The runner's own connections first; then
    anyone at the meeting, for a jockey interviewed about a horse he has
    since lost."""
    surname = _surname(speaker)
    if not surname:
        return None
    for pool in ([runner] if runner else [], everyone):
        for role in ("jockey", "trainer"):
            if any(_surname(x.get(role) or "") == surname
                   for x in pool if x):
                return role
    return None


def _turns(segs: list[dict]) -> list[list[dict]]:
    turns: list[list[dict]] = []
    for s in segs:
        if s["text"].lstrip().startswith(">>") or not turns:
            turns.append([])
        turns[-1].append(s)
    return turns


# The presenter chains questions with "And …" / "As you say …"; not one of
# the answers in the four interviews measured opens that way. Speech-to-text
# sometimes misses a change of speaker, and a missed `>>` turns the strict
# alternation the wrong way round for the rest of the interview.
_QUESTION_OPENS = re.compile(r"^(and|as)\b", re.I)


def _answers(turns: list[list[dict]]) -> list[list[dict]]:
    """The turns that are the interviewee speaking.

    Question, answer, question, answer — except that a turn where an answer
    is due but which opens like a question IS a question, and the answer is
    then the turn after it.
    """
    out, answer_due = [], False
    for turn in turns:
        text = " ".join(s["text"] for s in turn).lstrip("> ").strip()
        if answer_due and not _QUESTION_OPENS.match(text):
            out.append(turn)
            answer_due = False
        else:
            answer_due = True
    return out


def interview_quotes(rec: dict, runners: list[dict], fetched_at: str
                     ) -> tuple[list[dict], list[dict]]:
    """(quotes, quarantine) for one interview video."""
    url = f"https://www.youtube.com/watch?v={rec['video_id']}"
    segs = rec.get("segments") or []
    listed = index(rec.get("description") or "")
    if not listed:
        return [], [{"source": SOURCE, "race_date": rec["race_date"],
                     "race_no": None, "reason": "unparsed", "url": url,
                     "raw": f"no interview index in the description of "
                            f"{rec.get('title')}", "fetched_at": fetched_at}]

    # Where each interview starts: the question naming its horse, searched
    # over each line and the next (a name can break across two), and moved
    # back to the `>>` that opens the question.
    starts: list[int | None] = []
    after = 0
    for item in listed:
        hit = next((i for i in range(after, len(segs))
                    if _found(item["horse_name"], " ".join(
                        s["text"] for s in segs[i:i + 2]))), None)
        if hit is not None:
            # Which line of the pair holds the name decides where to back up
            # from. Backing up from the first when the name is in the second
            # lands on the tail of the previous ANSWER, and every turn after
            # it is then read the wrong way round.
            if (not _found(item["horse_name"], segs[hit]["text"])
                    and hit + 1 < len(segs)):
                hit += 1
            while hit > after and not segs[hit]["text"].lstrip().startswith(">>"):
                hit -= 1
            after = hit + 1
        elif not starts and segs:
            # The first interview IS the start of the video. "Zac, … the
            # highest rated horse in the world, Ying Star" is KA YING RISING,
            # and no spelling rule will find it; its place in the index will.
            hit, after = 0, 1
            item["opens_video"] = True
        starts.append(hit)

    quotes, held = [], []
    found = [s for s in starts if s is not None]
    by_no = {(x["race_no"], x["horse_no"]): x for x in runners}
    for item, start in zip(listed, starts):
        label = (f"{item['speaker']} on #{item['horse_no']} "
                 f"{item['horse_name']} (race {item['race_no']})")
        if start is None:
            held.append({"source": SOURCE, "race_date": rec["race_date"],
                         "race_no": item["race_no"], "reason": "unparsed",
                         "url": url, "fetched_at": fetched_at,
                         "raw": f"{label}: the horse is not named in any "
                                f"question in the transcript"})
            continue
        end = next((s for s in found if s > start), len(segs))
        runner = by_no.get((item["race_no"], item["horse_no"]))
        role = _role(item["speaker"], runner, runners)
        if role is None:
            held.append({"source": SOURCE, "race_date": rec["race_date"],
                         "race_no": item["race_no"], "reason": "unparsed",
                         "url": url, "fetched_at": fetched_at,
                         "raw": f"{label}: speaker is no jockey or trainer "
                                f"on this card"})
            continue
        for turn in _answers(_turns(segs[start:end])):
            text = re.sub(r"\s+", " ", " ".join(
                s["text"] for s in turn)).lstrip("> ").strip()
            if len(text) < MIN_ANSWER:
                continue
            t = turn[0]["t"]
            quotes.append({
                "source": SOURCE, "video_id": rec["video_id"], "t_start": t,
                "url": f"{url}&t={int(t)}s", "race_no": item["race_no"],
                "horse_no": item["horse_no"],
                "horse_said": item["horse_name"], "speaker": item["speaker"],
                "role": role, "caption_kind": "asr", "quote": text,
                "quote_en": None, "topic": None, "stance": None,
                "confidence": (OPENS_VIDEO if item.get("opens_video")
                               else CONFIDENCE), "extracted_by": EXTRACTOR,
                "fetched_at": fetched_at})
    return quotes, held
