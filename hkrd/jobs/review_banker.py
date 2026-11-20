"""Writing the banker review -- its only write path.

Asked for on 2026-11-20, after the desk turned out to learn nothing from what
the owner writes: *I make mistakes picking bankers sometimes, and there are
times circumstances affected the outcome, and I want a function to keep track
of both, going back over bankers already run.* He chose what the review is for
(grading his reasoning), what would make it worth his time (knowing which
reasons to trust), and what he will give it (one sentence and a confidence tap
before the off; a bulk review about every two weeks). docs/banker-review.md.

One row per horse he backed to carry a ticket, in two halves:

THE CLAIM -- why he chose it, how sure he was, who picked it. A claim made
BEFORE THE OFF is a forecast, and the record's whole value rests on it not
being rewritten by the result, which is the rule `trial_notes.written_at` and
the desk's decisions are held to: once the race has gone it stands as written.
A claim first written AFTER the off is a recollection, and he asked to fill in
the past ones, so it is welcome and editable -- and marked `claim_late`, kept
apart from the forecasts in every figure. `claim_at` is the first save and
never moves.

THE VERDICT -- what decided it. Needs a result, and must agree with it: "read it
right, and it placed" cannot be said of a horse that ran fifth. The
circumstances he names are his; what the stewards' report says is read beside
them (`derive/circumstances`) and never written over them.

`picked_by` (me, Kelvin, someone else) can be set at any time: it is a fact
about the ticket, not a forecast. Kelvin's sheet carries legs other people
chose, and the record of HIS reasoning should not carry theirs.

The desk's TAKE or PASS writes its sentence and confidence through `claim`, so
a claim has one home whichever page it was made on.

THE KIND OF REASON is given by the fortnightly review (docs/banker-review.md),
read from the reasoning alone with the result out of sight, and applied here:

    python -m hkrd.jobs.review_banker --kinds kinds.json --db /data/hkrd.db

`kinds.json` is a list of {race_date, race_no, horse_no, kind}. A horse with
no review row is reported and left alone: a kind describes a reason, and there
is no reason on record to describe.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from hkrd.derive.circumstances import LABEL as CIRCUMSTANCE_LABEL
from hkrd.query.banker_review import CONFIDENCE, PICKED_BY, VERDICT_LABEL, VERDICTS
from hkrd.query.pools import places_paid
from hkrd.store import review as store
from hkrd.store.connect import Connection, get_conn, transaction
from hkrd.store.tips import races_gone_off

__all__ = ["save", "claim", "reflect", "apply_kinds", "VERDICTS", "VERDICT_LABEL",
           "CONFIDENCE", "PICKED_BY", "UNSET", "ReviewError", "NotInRace", "ClaimClosed",
           "NoResult"]

# The vocabulary is the query's (`query/banker_review`), so the form and the
# rules here cannot list different words.
_NEEDS = {k: needs for k, _, needs in VERDICTS}


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()          # a field the caller did not send: left as it is


class ReviewError(ValueError):
    """A value outside the vocabulary, or a verdict the result contradicts."""


class NotInRace(LookupError):
    """No such runner in that race."""


class ClaimClosed(RuntimeError):
    """A claim written before the off, asked to change after it."""


class NoResult(RuntimeError):
    """A verdict on a race with no result stored yet."""


def _choice(value: object, vocab: tuple[tuple, ...], name: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    v = str(value).strip().lower()
    known = [k[0] for k in vocab]
    if v not in known:
        raise ReviewError(f"{name} must be one of {', '.join(known)}: {value!r}")
    return v


def _words(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _stamp(now: dt.datetime | None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(
        dt.timezone.utc).isoformat(timespec="seconds")


def _runner(conn: Connection, date: str, race_no: int, horse_no: int) -> dict[str, Any]:
    """The runner and what its race's state allows: gone off, result in,
    placed or not."""
    row = conn.execute("SELECT horse_name, place, place_code FROM runners WHERE "
                       "race_date = ? AND race_no = ? AND horse_no = ?",
                       (date, race_no, horse_no)).fetchone()
    if row is None:
        raise NotInRace(f"no runner {horse_no} in {date} race {race_no}")
    field, result = conn.execute(
        "SELECT sum(coalesce(place_code, '') NOT LIKE 'W%'), max(place IS NOT NULL) "
        "FROM runners WHERE race_date = ? AND race_no = ?", (date, race_no)).fetchone()
    withdrawn = (row["place_code"] or "").upper().startswith("W")
    place = row["place"]
    return {"name": row["horse_name"], "withdrawn": withdrawn, "result": bool(result),
            "gone": race_no in races_gone_off(conn, date),
            "placed": (place is not None and place <= places_paid(int(field or 0)))}


def claim(conn: Connection, date: str, race_no: int, horse_no: int, *,
          reasoning: Any = UNSET, confidence: Any = UNSET, picked_by: Any = UNSET,
          now: dt.datetime | None = None) -> None:
    """The claim half, inside a transaction the caller already holds."""
    run = _runner(conn, date, race_no, horse_no)
    cur = store.effective_claim(store.review(conn, date, race_no, horse_no),
                                store.desk_claim(conn, date, race_no, horse_no))
    new = {"reasoning": cur["reasoning"] if reasoning is UNSET else _words(reasoning),
           "confidence": (cur["confidence"] if confidence is UNSET
                          else _choice(confidence, CONFIDENCE, "confidence")),
           "picked_by": (cur["picked_by"] if picked_by is UNSET
                         else _choice(picked_by, PICKED_BY, "picked_by"))}
    changed = (new["reasoning"], new["confidence"]) != (cur["reasoning"], cur["confidence"])
    if changed and cur["claim_at"] and not cur["claim_late"] and run["gone"]:
        raise ClaimClosed(
            f"{run['name']}'s claim was written before the off ({cur['claim_at']}) and "
            f"the race has gone: it stands as written. Say what happened in the verdict.")
    if not changed and new["picked_by"] == cur["picked_by"] and cur["stored"]:
        return
    stamp = _stamp(now)
    if not (new["reasoning"] or new["confidence"]):
        at, late, edited = None, None, None
    elif cur["claim_at"] is None:
        at, late, edited = stamp, run["gone"], None
    else:
        at, late = cur["claim_at"], cur["claim_late"]
        edited = stamp if changed else cur["claim_edited_at"]
    if not cur["stored"] and not (new["reasoning"] or new["confidence"] or new["picked_by"]):
        return                       # nothing to keep, and nothing kept before
    store.save_claim(conn, date, race_no, horse_no, {
        **new, "claim_at": at, "claim_late": late, "claim_edited_at": edited})


def _verdict(conn: Connection, date: str, race_no: int, horse_no: int, *,
             verdict: Any, note: Any, circumstances: Any, now: dt.datetime | None) -> None:
    run = _runner(conn, date, race_no, horse_no)
    if run["withdrawn"]:
        raise ReviewError(f"{run['name']} was withdrawn from {date} race {race_no}: "
                          "there is no run to judge")
    if not run["result"]:
        raise NoResult(f"no result is stored for {date} race {race_no} yet")
    cur = store.review(conn, date, race_no, horse_no) or {}
    new_verdict = (cur.get("verdict") if verdict is UNSET
                   else _choice(verdict, tuple((k,) for k, _, _ in VERDICTS), "verdict"))
    needs = _NEEDS.get(new_verdict) if new_verdict else None
    if needs is not None and needs != run["placed"]:
        raise ReviewError(
            f"{VERDICT_LABEL[new_verdict]!r} needs a horse that "
            f"{'placed' if needs else 'did not place'}, and {run['name']} "
            f"{'placed' if run['placed'] else 'did not place'}")
    new_note = cur.get("verdict_note") if note is UNSET else _words(note)
    if circumstances is UNSET:
        new_circ = sorted(cur.get("circumstances") or [])
    else:
        given = sorted({str(c).strip().lower() for c in circumstances or [] if str(c).strip()})
        unknown = [c for c in given if c not in CIRCUMSTANCE_LABEL]
        if unknown:
            raise ReviewError(f"not a circumstance the review knows: {', '.join(unknown)} "
                              f"(known: {', '.join(CIRCUMSTANCE_LABEL)})")
        new_circ = given
    before = (cur.get("verdict"), cur.get("verdict_note"), sorted(cur.get("circumstances") or []))
    if (new_verdict, new_note, new_circ) == before and cur:
        return
    blank = not (new_verdict or new_note or new_circ)
    store.save_verdict(conn, date, race_no, horse_no, {
        "verdict": new_verdict, "verdict_note": new_note,
        "verdict_at": None if blank else _stamp(now)}, new_circ)


def save(date: str, race_no: int, horse_no: int, *, reasoning: Any = UNSET,
         confidence: Any = UNSET, picked_by: Any = UNSET, verdict: Any = UNSET,
         verdict_note: Any = UNSET, circumstances: Any = UNSET,
         db: Path | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    """Write whichever fields were sent; a field not sent is left as it is,
    and an empty one clears it. Returns the review as stored. Raises
    `NotInRace`, `ClaimClosed`, `NoResult` or `ReviewError`."""
    conn = get_conn(db) if db else get_conn()
    try:
        with transaction(conn, immediate=True):
            if any(v is not UNSET for v in (reasoning, confidence, picked_by)):
                claim(conn, date, race_no, horse_no, reasoning=reasoning,
                      confidence=confidence, picked_by=picked_by, now=now)
            if any(v is not UNSET for v in (verdict, verdict_note, circumstances)):
                _verdict(conn, date, race_no, horse_no, verdict=verdict, note=verdict_note,
                         circumstances=circumstances, now=now)
        return store.review(conn, date, race_no, horse_no) or {}
    finally:
        conn.close()


def reflect(date: str, race_no: int, note: str | None, *, db: Path | None = None,
            now: dt.datetime | None = None) -> dict[str, Any] | None:
    """His note on one race, or on the night (race 0). The race must be on the
    card; an empty note removes it. Returns the note as stored, or None."""
    conn = get_conn(db) if db else get_conn()
    try:
        sql = "SELECT 1 FROM races WHERE race_date = ?" + (" AND race_no = ?" if race_no else "")
        args = (date, race_no) if race_no else (date,)
        if conn.execute(sql + " LIMIT 1", args).fetchone() is None:
            raise NotInRace(f"no card stored for {date}" + (f" race {race_no}" if race_no else ""))
        with transaction(conn, immediate=True):
            store.save_reflection(conn, date, race_no, note, _stamp(now))
        return store.reflections(conn, date).get(race_no)
    finally:
        conn.close()


def apply_kinds(entries: list[dict[str, Any]], *, db: Path | None = None) -> dict[str, Any]:
    """Write the fortnightly review's kinds. Returns what it did, row counts
    and the horses it could not apply, never silence."""
    applied, missing = 0, []
    conn = get_conn(db) if db else get_conn()
    try:
        with transaction(conn, immediate=True):
            for e in entries:
                kind = _words(e.get("kind"))
                n = store.save_claim_kind(conn, e["race_date"], e["race_no"], e["horse_no"], kind)
                if n:
                    applied += n
                else:
                    missing.append(f"{e['race_date']} R{e['race_no']} #{e['horse_no']}")
    finally:
        conn.close()
    return {"read": len(entries), "applied": applied, "no_review": missing}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kinds", type=Path, required=True,
                    help="JSON list of {race_date, race_no, horse_no, kind}")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    got = apply_kinds(json.loads(a.kinds.read_text(encoding="utf-8")), db=a.db)
    print(f"  kinds read     {got['read']:>5}")
    print(f"  applied        {got['applied']:>5}")
    print(f"  no review row  {len(got['no_review']):>5}")
    for m in got["no_review"]:
        print(f"    {m}")
    return 0 if not got["no_review"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
