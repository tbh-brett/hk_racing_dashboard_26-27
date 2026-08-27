"""Scrape one day of barrier trials and store it.

    python -m hkrd.jobs.scrape_trials --date 2026-08-21

Separate from `scrape_meeting` because trials are held on their own days, at
their own venues, and a trial day is not a meeting. Folding them into the
meeting job would mean either scraping trials on race days that have none, or
never scraping the days that do.

The rows written carry two fields the archive has never had: `distance`, which
HKJC publishes in the batch header and the legacy import dropped, and `going`.
An archived trial reads None for both, and that is a gap in the archive rather
than in the source.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.ingest import trials as trials_ingest
from hkrd.ingest._client import FetchError, NotFound
from hkrd.store import upsert
from hkrd.store.connect import db_path, get_conn, init_db, transaction

__all__ = ["scrape", "TrialScrapeReport"]


@dataclass
class TrialScrapeReport:
    """Row counts. Never a bare "done": a zero has to be visible."""

    date: str = ""
    batches: int = 0
    runners: int = 0
    with_distance: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.batches > 0

    def render(self) -> str:
        lines = [f"  trial day          {self.date}",
                 f"  batches            {self.batches:>6}",
                 f"  runners            {self.runners:>6}",
                 f"  with a distance    {self.with_distance:>6}"]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>6}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def scrape(date: str, *, db: Path | None = None,
           session=None) -> TrialScrapeReport:
    """Fetch one trial day and store it. Running it twice is idempotent."""
    report = TrialScrapeReport(date=date)
    try:
        batches = trials_ingest.fetch_day(date, session=session)
    except NotFound:
        report.errors.append(f"no trial page for {date}")
        return report
    except (FetchError, trials_ingest.TrialsError) as exc:
        report.errors.append(f"trials: {exc}")
        return report

    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        with transaction(conn):
            for batch in batches:
                report.batches += 1
                # The batch's conditions are repeated onto every runner: the
                # trials table is one row per RUN, and a reader asking what
                # going a horse trialled on should not have to join back to a
                # batch table that does not exist.
                rows = [{
                    "trial_date": batch.get("trial_date") or date,
                    "trial_no": batch.get("trial_no"),
                    "horse_name": r.get("horse_name"),
                    "place": r.get("place"),
                    "finish_time": r.get("finish_time"),
                    "section_times": "; ".join(batch.get("section_times") or []),
                    "running_positions": " ".join(
                        str(p) for p in (r.get("running_positions") or [])),
                    "venue": batch.get("venue"),
                    "course": batch.get("course"),
                    "surface": batch.get("surface"),
                    "distance": batch.get("distance"),
                    "going": batch.get("going"),
                    "jockey": r.get("jockey"),
                    "trainer": r.get("trainer"),
                    "draw": r.get("draw"),
                    "lengths_behind": r.get("lbw"),
                    "gear": r.get("gear"),
                    "comment_text": r.get("comment"),
                } for r in batch.get("runners", [])]
                report.runners += upsert.upsert_trials(conn, rows)
                if batch.get("distance"):
                    report.with_distance += len(rows)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    report = scrape(a.date, db=a.db)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
