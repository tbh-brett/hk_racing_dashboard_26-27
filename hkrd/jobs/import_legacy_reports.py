"""Import the legacy reports/*.json archive into the database.

    python -m hkrd.jobs.import_legacy_reports --reports ../hk_race_dashboard/reports

662 JSON files were read from disk at 98 sites in the old dashboard. They belong
in tables: once here, the form guide, lookup and results read them through the
same query layer as everything else, and the directory becomes an archive.

Only the families with a stable contract are imported. Every one produced by a
dedicated scraper held one schema across its whole life -- results 1 schema over
56 files, dividends 1 over 56, incidents 1 over 87, trials 1 over 159. The one
family written by generated code, race_day_report, drifted to 3 schemas with 17
inconsistent keys, and is deliberately NOT imported.

comments_on_running inside the incidents files is also skipped. That parser was
broken for its entire life -- it indexed a four-column table as three, so all
10,690 records carry a horse number where the name belongs and no comment text
at all. There is nothing in them to import; use jobs/scrape_corunning instead.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.store import upsert
from hkrd.store.coerce import CoerceError, to_date
from hkrd.store.connect import db_path, get_conn, init_db, transaction


@dataclass
class ImportReport:
    files_read: int = 0
    comments: int = 0
    dividends: int = 0
    trials: int = 0
    skipped_corunning: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  files read         {self.files_read:>7,}",
            f"  runner_comments    {self.comments:>7,}",
            f"  dividends          {self.dividends:>7,}",
            f"  trials             {self.trials:>7,}",
            f"  corunning skipped  {self.skipped_corunning:>7,}   (parser was broken; rescrape)",
        ]
        if self.errors:
            lines.append(f"  ERRORS             {len(self.errors):>7,}")
            lines += [f"    {e}" for e in self.errors[:10]]
        return "\n".join(lines)


def _incident_rows(doc: dict) -> list[dict]:
    date = to_date(doc.get("date"))
    out = []
    for race in doc.get("races", []):
        race_no = race.get("race_number")
        for rec in race.get("incident_report", []):
            text = (rec.get("incident") or "").strip()
            if not text or rec.get("horse_no") in (None, ""):
                continue
            out.append({"race_date": date, "race_no": race_no,
                        "horse_no": rec["horse_no"], "comment_text": text,
                        "source": "incident"})
    return out


def _dividend_rows(doc: dict) -> list[dict]:
    date = to_date(doc.get("date"))
    return [{"race_date": date, "race_no": race.get("race_number"),
             "pool": d.get("pool"), "combination": d.get("combination"),
             "dividend_per_10": d.get("dividend_per_10")}
            for race in doc.get("races", [])
            for d in race.get("dividends", [])
            if d.get("pool") and d.get("combination") is not None]


def _trial_rows(doc: dict) -> list[dict]:
    date = to_date(doc.get("date"))
    out = []
    for batch in doc.get("batches", []):
        no = batch.get("batch_number")
        course = (batch.get("course") or "")
        surface = "AWT" if "ALL WEATHER" in course.upper() else "Turf"
        for h in batch.get("horses", []):
            positions = h.get("running_positions") or []
            out.append({
                "trial_date": date, "trial_no": no,
                "horse_name": h.get("horse_name"),
                # The scrape's own `result` field is empty on every row, so the
                # finishing position comes from the last running position --
                # which settles open question C2: RESULT was never populated at
                # source, not merely unwired in the interface.
                "place": positions[-1] if positions else None,
                "finish_time": h.get("time"),
                "section_times": "; ".join(batch.get("sectional_times") or []),
                "running_positions": " ".join(str(p) for p in positions),
                "venue": "ST" if "SHA TIN" in course.upper() else "HV",
                "surface": surface, "gear": (h.get("gear") or "").strip() or None,
                "comment_text": (h.get("comment") or "").strip() or None,
            })
    return out


def run(reports: Path, *, db: Path | None = None) -> ImportReport:
    report = ImportReport()
    conn = get_conn(db if db is not None else db_path())
    try:
        init_db(conn)
        families = (
            ("incidents_*.json", _incident_rows, upsert.upsert_comments, "comments"),
            ("dividends_*.json", _dividend_rows, upsert.upsert_dividends, "dividends"),
            ("trials_*.json", _trial_rows, upsert.upsert_trials, "trials"),
        )
        for pattern, extract, write, attr in families:
            for path in sorted(reports.glob(pattern)):
                report.files_read += 1
                try:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    report.errors.append(f"{path.name}: {e}")
                    continue
                if pattern.startswith("incidents"):
                    report.skipped_corunning += sum(
                        len(r.get("comments_on_running", []))
                        for r in doc.get("races", []))
                try:
                    rows = extract(doc)
                except (CoerceError, KeyError, TypeError) as e:
                    report.errors.append(f"{path.name}: {e}")
                    continue
                if not rows:
                    continue
                try:
                    with transaction(conn):
                        n = write(conn, rows)
                except CoerceError as e:
                    report.errors.append(f"{path.name}: {e}")
                    continue
                setattr(report, attr, getattr(report, attr) + n)
    finally:
        conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reports", type=Path, required=True)
    ap.add_argument("--db", type=Path, default=None)
    a = ap.parse_args(argv)
    if not a.reports.is_dir():
        print(f"not a directory: {a.reports}")
        return 1
    report = run(a.reports, db=a.db)
    print(report.render())
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
