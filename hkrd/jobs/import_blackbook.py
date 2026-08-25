"""Import the legacy blackbook.json.

    python -m hkrd.jobs.import_blackbook --src ../hk_race_dashboard/blackbook.json

196 entries with their own tag vocabulary, which is kept rather than forced
into the ten-tag taxonomy design brief 06 proposed. The real one has 19
definitions written against actual use and is what the entries are labelled
with; discarding it would lose the meaning of every existing row.

Two near-duplicates ARE merged, because they are the same tag typed twice:
improvement/improving and trial/barrier_trial.

The `performances` array is imported as hand-written notes, not as the record
of what happened. 171 of the 196 entries have none, because logging one relied
on remembering to. Runs since booking are derived from the runners table
instead -- see query/blackbook.py.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.store.coerce import CoerceError, to_date, to_int
from hkrd.store.connect import db_path, get_conn, init_db, transaction

# Same tag typed two ways. Everything else is kept as written.
TAG_ALIASES = {"improving": "improvement", "barrier_trial": "trial"}


@dataclass
class BlackbookReport:
    entries: int = 0
    tags: int = 0
    notes: int = 0
    definitions: int = 0
    merged_tags: int = 0
    undefined_tags: list[str] = field(default_factory=list)
    dates_recovered: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  entries            {self.entries:>6}",
                 f"  tag links          {self.tags:>6}",
                 f"  tag definitions    {self.definitions:>6}",
                 f"  hand-written notes {self.notes:>6}",
                 f"  aliases merged     {self.merged_tags:>6}",
                 f"  source dates found {self.dates_recovered:>6}"]
        if self.undefined_tags:
            # Not an error: a tag in use with no definition written for it. The
            # UI falls back to the tag name, but it is worth seeing.
            lines.append(f"  tags undefined     {len(self.undefined_tags):>6}"
                         f"  ({', '.join(sorted(self.undefined_tags))})")
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


# `source_race` is free text the user typed, not a scraped field, so it is
# scanned rather than parsed to a format. All five shapes present in the 196
# entries: '2026-04-01 R6', 'R2 1400m' (no date), '2026-03-25' (no race),
# 'Trial 2026-05-15 B3' (trial batch, no race), and empty.
_RACE_TOKEN = re.compile(r"^R(\d{1,2})$", re.IGNORECASE)
_DATE_TOKEN = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")


def _split_source(source: str | None) -> tuple[str | None, int | None]:
    """'2026-04-01 R6' -> ('2026-04-01', 6). Missing halves come back None.

    Unrecognised tokens are skipped, not raised on: this field is a hand-typed
    memo, and the raw string is stored verbatim alongside whatever is recovered.
    """
    date_out: str | None = None
    race_no: int | None = None
    for token in str(source or "").split():
        if date_out is None and _DATE_TOKEN.match(token):
            date_out = to_date(token)
            continue
        match = _RACE_TOKEN.match(token)
        if race_no is None and match:
            race_no = int(match.group(1))
    return date_out, race_no


# Nine entries name a race number and a distance but no date -- 'R7 1400m'. The
# horse's own runs supply the missing half, and the match is checked on three
# things at once (race number, distance, and a booking within a fortnight of the
# run) so a recovered date is a match rather than a guess. All nine legacy cases
# land 0-4 days before the booking with the distance agreeing exactly.
_MEMO_DISTANCE = re.compile(r"(\d{3,4})\s*m", re.IGNORECASE)
_RECOVERY_WINDOW_DAYS = 14


def _recover_source_dates(conn) -> int:
    """Fill source_date where the memo named a race but not a day."""
    pending = conn.execute(
        "SELECT id, horse_name, added_date, source_race, source_race_no "
        "FROM blackbook WHERE source_date IS NULL AND source_race_no IS NOT NULL"
    ).fetchall()

    found = []
    for row in pending:
        memo = _MEMO_DISTANCE.search(row["source_race"] or "")
        if not memo:
            continue                      # no distance to confirm against
        hit = conn.execute("""
            SELECT r.race_date, a.distance,
                   julianday(?) - julianday(r.race_date) AS gap
            FROM runners r
            JOIN races a ON a.race_date = r.race_date AND a.race_no = r.race_no
            WHERE r.horse_name = ? AND r.race_no = ? AND r.race_date <= ?
            ORDER BY r.race_date DESC LIMIT 1
        """, (row["added_date"], row["horse_name"], row["source_race_no"],
              row["added_date"])).fetchone()
        if (hit and hit["distance"] == int(memo.group(1))
                and hit["gap"] is not None and hit["gap"] <= _RECOVERY_WINDOW_DAYS):
            found.append((hit["race_date"], row["id"]))

    conn.executemany(
        "UPDATE blackbook SET source_date = ?, source_date_from = 'matched' "
        "WHERE id = ?", found)
    return len(found)


def run(src: Path, *, db: Path | None = None) -> BlackbookReport:
    report = BlackbookReport()
    doc = json.loads(src.read_text(encoding="utf-8"))
    entries = doc.get("entries", [])
    definitions = doc.get("tag_definitions", {}) or {}

    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn):
            conn.executemany(
                "INSERT INTO blackbook_tag_definitions (tag, definition) VALUES (?, ?) "
                "ON CONFLICT (tag) DO UPDATE SET definition = excluded.definition",
                [(TAG_ALIASES.get(k, k), v) for k, v in definitions.items()])
            report.definitions = len(definitions)

            rows, tag_rows, note_rows = [], [], []
            for e in entries:
                try:
                    added = to_date(e.get("added_date"))
                    if not e.get("id") or not e.get("horse_name") or not added:
                        report.errors.append(f"{e.get('id')}: missing id, horse or date")
                        continue
                    src_date, src_no = _split_source(e.get("source_race"))
                    cond = e.get("conditions") or {}
                    rows.append((
                        e["id"], e["horse_name"].strip().upper(), added,
                        to_date(e.get("expiry_date")), e.get("status") or "active",
                        e.get("reasoning"), e.get("confidence"),
                        e.get("source_race"), src_date, src_no,
                        "memo" if src_date else None,
                        ",".join(str(d) for d in (cond.get("preferred_distance") or [])) or None,
                        cond.get("preferred_surface"),
                        (cond.get("jockey_preference") or "").strip() or None,
                    ))
                    for tag in e.get("tags") or []:
                        canonical = TAG_ALIASES.get(tag, tag)
                        if canonical != tag:
                            report.merged_tags += 1
                        tag_rows.append((e["id"], canonical))
                    for p in e.get("performances") or []:
                        d = to_date(p.get("date"))
                        if not d:
                            continue
                        note_rows.append((
                            e["id"], d, to_int(p.get("race_number"), field="race_number"),
                            str(p.get("finish")) if p.get("finish") is not None else None,
                            to_int(p.get("model_rank"), field="model_rank"),
                            p.get("bb_verdict"), p.get("notes")))
                except CoerceError as exc:
                    report.errors.append(f"{e.get('id')}: {exc}")

            conn.executemany(
                "INSERT INTO blackbook (id, horse_name, added_date, expiry_date, "
                "status, reasoning, confidence, source_race, source_date, "
                "source_race_no, source_date_from, pref_distance, pref_surface, "
                "pref_jockey) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (id) DO UPDATE SET "
                "horse_name=excluded.horse_name, added_date=excluded.added_date, "
                "expiry_date=excluded.expiry_date, status=excluded.status, "
                "reasoning=excluded.reasoning, confidence=excluded.confidence, "
                "source_race=excluded.source_race, source_date=excluded.source_date, "
                "source_race_no=excluded.source_race_no, "
                "source_date_from=excluded.source_date_from", rows)
            conn.executemany(
                "INSERT INTO blackbook_tags (id, tag) VALUES (?, ?) "
                "ON CONFLICT (id, tag) DO NOTHING", tag_rows)
            conn.executemany(
                "INSERT INTO blackbook_notes (id, race_date, race_no, finish, "
                "model_rank, verdict, notes) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT (id, race_date, race_no) DO UPDATE SET "
                "finish=excluded.finish, verdict=excluded.verdict, "
                "notes=excluded.notes", note_rows)
            report.entries, report.tags, report.notes = (
                len(rows), len(tag_rows), len(note_rows))
            report.dates_recovered = _recover_source_dates(conn)
            report.undefined_tags = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT t.tag FROM blackbook_tags t "
                    "LEFT JOIN blackbook_tag_definitions d ON d.tag = t.tag "
                    "WHERE d.tag IS NULL ORDER BY t.tag")]
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    if not a.src.is_file():
        print(f"not found: {a.src}")
        return 1
    report = run(a.src, db=a.db)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
