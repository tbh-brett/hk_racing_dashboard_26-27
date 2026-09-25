"""Find what earlier scrapes left behind, and fetch only that.

    python -m hkrd.jobs.repair              # what is damaged, fetch nothing
    python -m hkrd.jobs.repair --fix
    python -m hkrd.jobs.repair --fix --only comments

Three kinds of damage, three different remedies, and the point of this job is
that they are different. A blanket re-scrape would be two thousand requests
against a public site to fix things that mostly need no requests at all.

  pace      No network. runner_pace is derived, so a bug that dropped rows is
            repaired by recomputing them — see derive/pace, where ONE
            non-finisher used to void the pace figure for its entire field.

  comments  The narrowest possible fetch: the comments-on-running page only,
            about eleven requests per meeting. Not a full re-scrape, which
            would be thirty per meeting to re-fetch results that are already
            correct. Tags follow automatically, because rebuild_tags reads
            runner_comments.

  headers   A race whose row has no distance, class or name — the results
            landed but the race header did not. This one does need the meeting
            fetched properly, because that is where the header comes from.

  classes   A race whose class is missing, is not in the archive's one
            vocabulary (`coerce.RACE_CLASSES`), or has never been asked
            whether it was restricted. HKJC writes the class five ways and
            the parsers read one or two of them: every Group, 4-year-old and
            restricted race came through with none. ONE request per meeting --
            the all-results page carries every race's header.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.store.coerce import RACE_CLASSES
from hkrd.store.connect import db_path, get_conn, init_db

__all__ = ["Damage", "survey", "repair"]

KINDS = ("pace", "comments", "headers", "classes")


@dataclass
class Damage:
    """What is wrong, in the units the fix works in."""

    pace_races: list[tuple[str, int]] = field(default_factory=list)
    comment_dates: list[str] = field(default_factory=list)
    header_dates: list[str] = field(default_factory=list)
    class_dates: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        lines = [""]
        lines.append("  pace")
        if self.pace_races:
            lines.append(f"    {len(self.pace_races)} races, "
                         f"{self.counts.get('pace_runners', 0):,} runners have no pace figure")
            lines.append("    no requests needed — this is a recompute")
            for d, n in self.pace_races[:5]:
                lines.append(f"      {d} R{n}")
            if len(self.pace_races) > 5:
                lines.append(f"      ... and {len(self.pace_races) - 5} more")
        else:
            lines.append("    nothing missing")

        lines.append("  comments on running")
        if self.comment_dates:
            mins = len(self.comment_dates) * 11 * 1.2 / 60
            lines.append(f"    {len(self.comment_dates)} meetings, "
                         f"{self.counts.get('comment_runners', 0):,} runners with no comment")
            lines.append(f"    {self.comment_dates[0]} .. {self.comment_dates[-1]}")
            lines.append(f"    about {mins:.0f} minutes of fetching")
        else:
            lines.append("    nothing missing")

        lines.append("  race headers")
        if self.header_dates:
            lines.append(f"    {self.counts.get('header_races', 0)} races with no distance, "
                         f"across {len(self.header_dates)} meetings")
            lines.append(f"    {', '.join(self.header_dates)}")
        else:
            lines.append("    nothing missing")

        lines.append("  race classes")
        if self.class_dates:
            mins = len(self.class_dates) * 1.2 / 60
            lines.append(f"    {self.counts.get('class_wrong', 0):,} races with no class or "
                         f"one outside the vocabulary; "
                         f"{self.counts.get('class_unasked', 0):,} never asked whether "
                         "restricted")
            lines.append(f"    {len(self.class_dates)} meetings, one request each, "
                         f"about {mins:.0f} minutes")
        else:
            lines.append("    nothing missing")
        return "\n".join(lines)

    @property
    def anything(self) -> bool:
        return bool(self.pace_races or self.comment_dates or self.header_dates
                    or self.class_dates)


def survey(db: Path | None = None) -> Damage:
    """Read-only. Makes no requests."""
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        d = Damage()

        # Races where at least one runner has sectionals but no pace row. A
        # race nobody has sectionals for is not damage, it is a race HKJC did
        # not time.
        rows = conn.execute("""
            SELECT r.race_date, r.race_no, count(*) AS n
            FROM runners r
            LEFT JOIN runner_pace p
              ON p.race_date = r.race_date AND p.race_no = r.race_no
             AND p.horse_no = r.horse_no
            WHERE p.horse_no IS NULL
              AND r.section_times IS NOT NULL AND r.section_times <> ''
            GROUP BY r.race_date, r.race_no
            ORDER BY r.race_date, r.race_no""").fetchall()
        d.pace_races = [(r["race_date"], r["race_no"]) for r in rows]
        d.counts["pace_runners"] = sum(r["n"] for r in rows)

        rows = conn.execute("""
            SELECT r.race_date, count(*) AS n
            FROM runners r
            LEFT JOIN runner_comments c
              ON c.race_date = r.race_date AND c.race_no = r.race_no
             AND c.horse_no = r.horse_no
            WHERE c.horse_no IS NULL
            GROUP BY r.race_date ORDER BY r.race_date""").fetchall()
        d.comment_dates = [r["race_date"] for r in rows]
        d.counts["comment_runners"] = sum(r["n"] for r in rows)

        rows = conn.execute("""
            SELECT race_date, count(*) AS n FROM races
            WHERE distance IS NULL GROUP BY race_date ORDER BY race_date""").fetchall()
        d.header_dates = [r["race_date"] for r in rows]
        d.counts["header_races"] = sum(r["n"] for r in rows)

        # Settled meetings only: the all-results page has nothing to say about
        # a card that has not run, and the nightly card scrape reads that.
        known = ",".join(f"'{c}'" for c in RACE_CLASSES)
        rows = conn.execute(f"""
            SELECT a.race_date,
                   sum(a.race_class IS NULL OR a.race_class NOT IN ({known})) wrong,
                   sum(a.restricted IS NULL) unasked
              FROM races a
             WHERE EXISTS (SELECT 1 FROM runners r WHERE r.race_date = a.race_date
                              AND r.race_no = a.race_no AND r.place IS NOT NULL)
             GROUP BY a.race_date
            HAVING wrong > 0 OR unasked > 0
             ORDER BY a.race_date""").fetchall()
        d.class_dates = [r["race_date"] for r in rows]
        d.counts["class_wrong"] = sum(r["wrong"] for r in rows)
        d.counts["class_unasked"] = sum(r["unasked"] for r in rows)
        return d
    finally:
        conn.close()


def _repair_classes(db: Path | None, dates: list[str], *, session=None) -> list[str]:
    """Re-read each meeting's headers and write the class, as HKJC wrote it.

    Only races already stored are touched, and only the header fields: a
    race the archive does not have is not invented from a header line.
    """
    from hkrd.ingest._client import FetchError
    from hkrd.ingest.headers import fetch_meeting_headers
    from hkrd.store import upsert
    from hkrd.store.connect import transaction

    log: list[str] = []
    conn = get_conn(db if db is not None else db_path())
    try:
        changed = 0
        for date in dates:
            try:
                heads = fetch_meeting_headers(date, session=session)
            except FetchError as exc:
                log.append(f"classes {date}: FAILED — {exc}")
                continue
            have = {r[0] for r in conn.execute(
                "SELECT race_no FROM races WHERE race_date = ?", (date,))}
            rows = [{"race_date": date, **h} for h in heads if h["race_no"] in have]
            if not rows:
                log.append(f"classes {date}: no header lines read")
                continue
            before = dict(conn.execute(
                "SELECT race_no, race_class FROM races WHERE race_date = ?", (date,)).fetchall())
            with transaction(conn):
                upsert.upsert_races(conn, rows)
            after = dict(conn.execute(
                "SELECT race_no, race_class FROM races WHERE race_date = ?", (date,)).fetchall())
            moved = [f"R{n} {before.get(n)}→{after[n]}" for n in sorted(after)
                     if before.get(n) != after[n]]
            changed += len(moved)
            if moved:
                log.append(f"classes {date}: " + ", ".join(moved))
        log.append(f"classes: {len(dates)} meetings read, {changed} classes changed")
    finally:
        conn.close()
    return log


def repair(db: Path | None = None, *, only: tuple[str, ...] = KINDS,
           limit: int | None = None, session=None) -> list[str]:
    """Fix what `survey` found. Returns a line per action taken."""
    from hkrd.jobs import derive_all, scrape_corunning, scrape_meeting

    log: list[str] = []
    d = survey(db)

    # Headers first: pace needs the distance, and a race with no distance has
    # no section layout, so repairing it before the recompute is the difference
    # between those races getting a figure and being skipped again.
    if "headers" in only and d.header_dates:
        for date in d.header_dates[:limit]:
            for venue in ("ST", "HV"):
                got = scrape_meeting.scrape_meeting(date, venue, post_race=True,
                                                    db=db, session=session)
                if got.races:
                    log.append(f"header {date} {venue}: {got.races} races, "
                               f"{got.runners} runners re-read")
                    break

    if "classes" in only and d.class_dates:
        log.extend(_repair_classes(db, d.class_dates[:limit], session=session))

    if "comments" in only and d.comment_dates:
        for date in d.comment_dates[:limit]:
            out = scrape_corunning.scrape(date, db=db, session=session)
            if out.errors:
                log.append(f"comments {date}: FAILED — {out.errors[0]}")
            else:
                log.append(f"comments {date}: {out.comments} stored, "
                           f"{out.lane_tags} lane tags")

    # Recompute last, so it sees everything the fetches just wrote. Tags are in
    # here too: rebuild_tags reads runner_comments, so comments fetched above
    # become tags without a second pass.
    steps = ["pace", "tags"] if "comments" in only else ["pace"]
    out = derive_all.run(db, only=tuple(steps))
    log.append("recomputed: " + ", ".join(
        f"{k} {v:,}" for k, v in out.written.items()))
    for k, v in out.skipped.items():
        if v:
            log.append(f"  skipped, {k}: {v:,}")
    log.extend(f"  ERROR {e}" for e in out.errors[:5])
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--fix", action="store_true",
                    help="actually fetch and recompute; without it, report only")
    ap.add_argument("--only", default=None,
                    help=f"comma-separated subset of {', '.join(KINDS)}")
    ap.add_argument("--limit", type=int, default=None,
                    help="most meetings to fetch, for a first cautious run")
    a = ap.parse_args(argv)

    only = tuple(s.strip() for s in a.only.split(",")) if a.only else KINDS
    unknown = [s for s in only if s not in KINDS]
    if unknown:
        print(f"unknown kind(s): {', '.join(unknown)}")
        return 2

    d = survey(a.db)
    print(d.render())
    print()

    if not d.anything:
        print("  nothing to repair")
        return 0
    if not a.fix:
        print("  run again with --fix to repair this")
        return 0

    for line in repair(a.db, only=only, limit=a.limit):
        print(f"  {line}")
    print()
    print("  survey again:")
    print(survey(a.db).render())
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
