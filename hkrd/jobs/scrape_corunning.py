"""Scrape comments on running for a meeting and store them.

    python -m hkrd.jobs.scrape_corunning --date 2026-07-15

Writes two things from one fetch: the comment text into runner_comments with
source='corunning', and lane descriptors into runner_tags namespaced 'lane:'.
Both are then available to the form guide, lookup and results through the normal
query layer -- no separate loader, no JSON on disk.

This replaces reading lane position from photographs. The comment states where
the horse travelled; the tag records that and nothing more.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import corunning
from hkrd.ingest._client import FetchError, NotFound
from hkrd.store import coerce, upsert
from hkrd.store.connect import db_path, get_conn, init_db, transaction


def published(rows: list[dict]) -> bool:
    """Has HKJC written this race up yet?

    The comments on running are published DAYS after a meeting. Until then the
    page is live, parses cleanly, and gives every horse `coerce.NO_COMMENT_
    PREFIX`'s sentence. Measured on 2026-09-21 against HKJC's own page: 6, 9 and
    13 September were written up by then and 16 September, five days old, was
    not.

    All or nothing per meeting -- a meeting that has its comments carries none
    of the placeholder and one that has not carries nothing else -- so one race
    answers for the card, which is what lets the scheduler ask with a single
    request instead of eleven.
    """
    return any(r.get("comment") and not coerce.is_no_comment(r["comment"])
               for r in rows)


@dataclass
class CoRunningReport:
    races: int = 0
    comments: int = 0
    lane_tags: int = 0
    # Races HKJC has not written up yet: every horse still reads the
    # placeholder. Not an error -- that is every meeting's first week -- but a
    # count, so a run that stored no real comment says why.
    pending: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"  races scraped    {self.races:>6}",
                 f"  comments stored  {self.comments:>6}",
                 f"  lane tags        {self.lane_tags:>6}",
                 f"  not yet written  {self.pending:>6}"]
        if self.errors:
            lines.append(f"  ERRORS           {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def scrape(date: str, *, db: Path | None = None,
           max_races: int = 11, session=None) -> CoRunningReport:
    report = CoRunningReport()
    try:
        meeting = corunning.fetch_meeting(date, max_races=max_races, session=session)
    except (FetchError, corunning.CoRunningError) as e:
        report.errors.append(str(e))
        return report

    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn):
            for race_no, rows in meeting.items():
                report.races += 1
                # The placeholder rows are still stored, as they always were --
                # see `coerce.NO_COMMENT_PREFIX` -- and the real text replaces
                # them on the same key when HKJC writes the race up. They are
                # just not COUNTED as comments: a run that stored eleven races
                # of "nothing yet" stored nothing.
                if not published(rows):
                    report.pending += 1
                    upsert.upsert_comments(
                        conn, corunning.comment_rows(date, race_no, rows))
                    continue
                report.comments += upsert.upsert_comments(
                    conn, corunning.comment_rows(date, race_no, rows))
                tags = corunning.lane_tag_rows(date, race_no, rows)
                if tags:
                    conn.executemany(
                        "INSERT INTO runner_tags (race_date, race_no, horse_no, tag, "
                        "confidence) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT (race_date, race_no, horse_no, tag) "
                        "DO UPDATE SET confidence = excluded.confidence",
                        [(t["race_date"], t["race_no"], t["horse_no"],
                          t["tag"], t["confidence"]) for t in tags])
                    report.lane_tags += len(tags)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--max-races", type=int, default=11)
    a = ap.parse_args(argv)

    report = scrape(a.date, db=a.db, max_races=a.max_races)
    print(report.render())
    if report.errors:
        print("\nno rows were written", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
