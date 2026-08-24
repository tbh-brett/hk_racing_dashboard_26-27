"""Rebuild runner_tags from runner_comments.

    python -m hkrd.jobs.rebuild_tags

Derived and droppable: delete every row and rerun, and the table comes back
identical. Lane tags from corunning are namespaced 'lane:' and are NOT touched
here -- they come from jobs/scrape_corunning, and this job must not delete work
it did not produce.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.derive import tags as tagger
from hkrd.store.connect import db_path, get_conn, init_db, transaction


@dataclass
class TagReport:
    comments_read: int = 0
    tags_written: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    untagged: int = 0

    def render(self) -> str:
        pct = 100 * self.untagged / self.comments_read if self.comments_read else 0
        lines = [f"  comments read      {self.comments_read:>7,}",
                 f"  runner_tags rows   {self.tags_written:>7,}",
                 f"  untagged comments  {self.untagged:>7,}  ({pct:.1f}%)"]
        if self.by_kind:
            lines.append("  by kind:")
            lines += [f"    {k:<10} {v:>7,}" for k, v in sorted(
                self.by_kind.items(), key=lambda x: -x[1])]
        return "\n".join(lines)


def rebuild(db: Path | None = None) -> TagReport:
    report = TagReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        rows = conn.execute(
            "SELECT race_date, race_no, horse_no, comment_text FROM runner_comments "
            "WHERE comment_text IS NOT NULL AND trim(comment_text) <> ''").fetchall()
        report.comments_read = len(rows)

        out: list[tuple] = []
        for r in rows:
            found = tagger.tag_comment(r["comment_text"])
            if not found:
                report.untagged += 1
            for t in found:
                report.by_kind[t.kind] = report.by_kind.get(t.kind, 0) + 1
                out.append((r["race_date"], r["race_no"], r["horse_no"],
                            t.name, t.confidence))

        with transaction(conn):
            # Only our own tags. A 'lane:' row comes from the corunning scrape
            # and is not this job's to remove.
            conn.execute("DELETE FROM runner_tags WHERE tag NOT LIKE 'lane:%'")
            conn.executemany(
                "INSERT INTO runner_tags (race_date, race_no, horse_no, tag, confidence) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (race_date, race_no, horse_no, tag) "
                "DO UPDATE SET confidence = excluded.confidence", out)
        report.tags_written = len(out)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    print(rebuild(a.db).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
