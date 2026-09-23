"""Import a tips payload: validate it, check it against the card, store it.

    python -m hkrd.jobs.import_tips out/2026-09-23.tips.json

`POST /api/tips/import` calls `run`, and it is the only way tips reach the
database. The payload is built on the PC (`tools/extract_tips.py`), because
YouTube refuses the datacenter address this app runs on; what arrives here is
rows, never a database file, so a re-push can only ever converge.

NOTHING HERE GUESSES A RUNNER. The extractor resolves names to numbers; this
checks every number against the card that is actually stored and takes out
anything that does not hold — to `tips_quarantine`, with the reason. A quote
with no number at all is kept, at race level or unplaced, because an
unresolved quote is still true. A wrong number is not: a trainer's quote
under the wrong horse is worse than no row.

Fixed odds ride along for a bookmaker only the PC can reach. A price is kept
only where the bookmaker's number AND its name agree with the card, and a
price that does not is named in the report, not quarantined: it is not
anyone's opinion to review, just a runner the two sides disagree about.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hkrd.derive import names
from hkrd.ingest.tips_payload import PayloadError, parse
from hkrd.store import fixed_odds, job_log, tips
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["run", "ImportReport", "PayloadError", "MIN_CONFIDENCE"]

# Below this a quote is quarantined, whatever runner it names. SPEC §3.
MIN_CONFIDENCE = 0.55


@dataclass
class ImportReport:
    """What one push wrote. Never a bare "ok" — a zero has to be visible."""

    race_date: str = ""
    quotes: int = 0
    selections: int = 0
    quarantined: int = 0
    # Of `quotes`, how many are stored against no runner — race-level, or
    # not placed at all. A push where this is every quote is a resolver that
    # has stopped resolving, and would otherwise read as a healthy count.
    unplaced_quotes: int = 0
    # Rows an earlier push stored that this one no longer has — left out of
    # it, or quarantined by it. The latest push for a source replaces it.
    removed: int = 0
    reasons: Counter = field(default_factory=Counter)
    prices: int = 0
    prices_skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"race_date": self.race_date, "quotes": self.quotes,
                "selections": self.selections,
                "quarantined": self.quarantined,
                "quarantine_reasons": dict(sorted(self.reasons.items())),
                "unplaced_quotes": self.unplaced_quotes,
                "removed": self.removed, "prices": self.prices,
                "prices_skipped": self.prices_skipped}

    def render(self) -> str:
        lines = [f"  tips               {self.race_date}",
                 f"  quotes             {self.quotes:>6}   "
                 f"({self.unplaced_quotes} on no runner)",
                 f"  selections         {self.selections:>6}",
                 f"  quarantined        {self.quarantined:>6}"]
        lines += [f"    {reason:<18}{n:>4}"
                  for reason, n in sorted(self.reasons.items())]
        if self.removed:
            lines.append(f"  removed            {self.removed:>6}   "
                         f"(stored before, not in this push)")
        if self.prices or self.prices_skipped:
            lines.append(f"  fixed prices       {self.prices:>6}   "
                         f"({len(self.prices_skipped)} not stored)")
            lines += [f"    {s}" for s in self.prices_skipped[:12]]
        return "\n".join(lines)


@dataclass(frozen=True)
class _Card:
    """The meeting a payload is checked against: which races and runners
    exist, and every runner's name in both languages."""

    races: dict[int, dict[int, str]]
    names_zh: dict[tuple[int, int], str | None]

    def name_check(self, said: str, race_no: int, horse_no: int, *,
                   heard: bool = False) -> str | None:
        """The checksum. A published name that fits a DIFFERENT horse from
        the one at that number means the number or the name is wrong, and
        there is no telling which — so neither is stored.

        `heard`: the number was spoken and the name came through speech-to-
        text, so the name only has to point at this horse more than at any
        other in the race. See `derive.names.verdict_heard`."""
        if names.is_chinese(said):
            roster = self.names_zh
        else:
            roster = {(r, h): n for r, field_ in self.races.items()
                      for h, n in field_.items()}
        key = (race_no, horse_no)
        if heard:
            return names.verdict_heard(
                said, roster.get(key),
                (n for k, n in roster.items()
                 if k[0] == race_no and k != key))
        return names.verdict(
            said, roster.get(key),
            (n for k, n in roster.items() if k != key),
            complete=all(roster.values()))


def _check_runner(race_no: int | None, horse_no: int | None,
                  said: str | None, card: _Card, *,
                  heard: bool = False) -> str | None:
    """Why this runner reference cannot be stored, or None if it can."""
    if race_no is None:
        # A horse number with no race is not a reference to anything.
        return "no_race" if horse_no is not None else None
    if race_no not in card.races:
        return "no_race"
    if horse_no is None:
        return None
    if horse_no not in card.races[race_no]:
        return "no_runner"
    return (card.name_check(said, race_no, horse_no, heard=heard)
            if said else None)


def _quarantined(row: Mapping[str, Any], reason: str,
                 identity: str) -> dict[str, Any]:
    return {"source": row["source"], "race_date": row["race_date"],
            "race_no": row.get("race_no"), "reason": reason,
            "url": row.get("url"), "fetched_at": row["fetched_at"],
            "identity": identity,
            "raw": tips.canonical_raw(
                {k: v for k, v in row.items() if k != "fetched_at"})}


def _duplicates(keys: list[Any], what: str) -> list[str]:
    counts = Counter(keys)
    return [f"{what} {k!r} appears {n} times" for k, n in counts.items()
            if n > 1]


def run(body: object, *, db: Path | None = None) -> ImportReport:
    """Validate, resolve, store. Raises PayloadError for a payload that
    breaks the contract, having recorded the rejection where ops can see it."""
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        try:
            return _import(conn, body)
        except PayloadError as exc:
            with transaction(conn):
                job_log.record_source(conn, "import_tips", ok=False,
                                      detail=f"rejected: {exc}")
            raise
    finally:
        conn.close()


def _import(conn, body: object) -> ImportReport:
    payload = parse(body)
    report = ImportReport(race_date=payload.race_date)
    races = tips.meeting_card(conn, payload.race_date)
    if not races:
        raise PayloadError([
            f"no races stored for {payload.race_date} — a payload for a "
            f"meeting this database has never scraped is a mistake, not "
            f"data. Scrape the card first."])

    quote_ids = [tips.quote_id(q) for q in payload.quotes]
    sel_keys = [(s["source"], s["tipster"], s["race_date"], s["race_no"],
                 s["horse_no"]) for s in payload.selections]
    # Two rows on one key would store as one, and which survived would be
    # whichever came last. That is a bug in the extractor, so it is refused.
    dupes = (_duplicates(quote_ids, "quote_id")
             + _duplicates(sel_keys, "selection")
             + _duplicates([(f["bookmaker"], f["race_no"], f["horse_no"],
                             f["captured_at"]) for f in payload.fixed_odds],
                           "price"))
    if dupes:
        raise PayloadError(dupes)

    card = _Card(races, tips.meeting_names_zh(conn, payload.race_date))
    quotes, sels, kept = [], [], []
    held = list(payload.quarantine)

    for q, qid in zip(payload.quotes, quote_ids):
        reason = _check_runner(q["race_no"], q["horse_no"], q["horse_said"],
                               card)
        if reason is None and q["confidence"] is not None                 and q["confidence"] < MIN_CONFIDENCE:
            reason = "low_confidence"
        if reason:
            held.append(_quarantined(q, reason, f"quote:{qid}"))
            report.reasons[reason] += 1
        else:
            quotes.append(q)
            if q["horse_no"] is None:
                report.unplaced_quotes += 1

    for s, key in zip(payload.selections, sel_keys):
        reason = _check_runner(s["race_no"], s["horse_no"], s["name_seen"],
                               card, heard=s["caption_kind"] == "asr")
        if reason:
            held.append(_quarantined(
                s, reason, "selection:" + "|".join(str(k) for k in key)))
            report.reasons[reason] += 1
        else:
            sels.append(s)
            kept.append(key)

    for r in payload.quarantine:
        report.reasons[r["reason"]] += 1

    prices = []
    for f in payload.fixed_odds:
        ours = races.get(f["race_no"], {}).get(f["horse_no"])
        if ours is None or names.similarity(f["name_seen"], ours) \
                < names.SPELLED_ALIKE:
            report.prices_skipped.append(
                f"{f['bookmaker']} R{f['race_no']} #{f['horse_no']} "
                f"{f['name_seen']}: the card has {ours or 'no such runner'}")
        else:
            prices.append(f)

    with transaction(conn, immediate=True):
        report.quotes = tips.upsert_quotes(conn, quotes)
        report.selections = tips.upsert_selections(conn, sels)
        report.quarantined = tips.upsert_quarantine(conn, held)
        report.prices = fixed_odds.upsert_fixed_odds(conn, prices)
        # The latest push is the source's whole answer for this meeting.
        report.removed = tips.replace_absent(
            conn, payload.race_date, payload.sources,
            quote_ids=[tips.quote_id(q) for q in quotes],
            selection_keys=kept,
            quarantine_ids=[tips.quarantine_row_id(r) for r in held])
        job_log.record_source(
            conn, "import_tips", ok=True,
            detail=(f"{report.race_date} · {report.quotes} quotes · "
                    f"{report.selections} selections · "
                    f"{report.quarantined} quarantined · "
                    f"{report.removed} removed · {report.prices} prices"))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("payload", type=Path, help="a <race_date>.tips.json file")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    try:
        report = run(json.loads(a.payload.read_text(encoding="utf-8")),
                     db=a.db)
    except PayloadError as exc:
        print(f"  rejected — {exc}", file=sys.stderr)
        return 1
    print(report.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
