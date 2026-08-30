"""Build a working database from the old repo, in one command.

    python -m hkrd.jobs.bootstrap --legacy ../hk_race_dashboard

Runs every import and rebuild in dependency order and reports what landed at
each step. Safe to re-run: every job is idempotent, so this converges rather
than duplicating.

Order matters and is not alphabetical:
  migrate   raw races and runners, from hkjc.db
  reports   dividends, commentary, incidents, trials, from reports/*.json
  odds      surviving live-odds snapshots, from cache/live_odds
  bets      the settled bets ledger, from reports/user_bets_log.jsonl
  blackbook the 196 entries, from blackbook.json
  derive    pace, then ET, then SARR (which reads pace), then tags
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from pathlib import Path

from hkrd.store.connect import db_path

__all__ = ["run"]


@dataclass
class Step:
    name: str
    detail: str = ""
    seconds: float = 0.0
    skipped: str = ""


@dataclass
class BootstrapReport:
    steps: list[Step] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        for s in self.steps:
            if s.skipped:
                lines.append(f"  {s.name:<12} skipped — {s.skipped}")
            else:
                lines.append(f"  {s.name:<12} {s.detail:<44} {s.seconds:6.1f}s")
        if self.errors:
            lines.append("")
            lines.append(f"  {len(self.errors)} step(s) reported errors:")
            lines += [f"    {e}" for e in self.errors[:12]]
        return "\n".join(lines)


def run(legacy: Path, *, db: Path | None = None,
        skip_derive: bool = False) -> BootstrapReport:
    report = BootstrapReport()
    target = db if db is not None else db_path()

    def step(name: str, path: Path | None, fn) -> None:
        """Run one job, or say plainly why it did not run. A missing legacy
        file is a fact about the source, not a failure — but it is never
        silent, because a database quietly missing its odds looks identical to
        one that never had any."""
        if path is not None and not path.exists():
            report.steps.append(Step(name, skipped=f"not found: {path}"))
            return
        started = time.perf_counter()
        try:
            detail = fn()
        except Exception as exc:                      # noqa: BLE001 — reported
            report.steps.append(Step(name, detail=f"FAILED: {exc}",
                                     seconds=time.perf_counter() - started))
            report.errors.append(f"{name}: {exc}")
            return
        report.steps.append(Step(name, detail=detail,
                                 seconds=time.perf_counter() - started))

    def _migrate():
        from hkrd.jobs import migrate_legacy
        out = migrate_legacy.migrate(legacy / "hkjc.db", target)
        report.errors.extend(f"migrate: {e}" for e in out.errors[:3])
        return f"{out.races:,} races · {out.runners:,} runners"

    def _reports():
        from hkrd.jobs import import_legacy_reports
        out = import_legacy_reports.run(legacy / "reports", db=target)
        return (f"{out.comments:,} comments · {out.dividends:,} dividends · "
                f"{out.trials:,} trials")

    def _odds():
        from hkrd.jobs import import_legacy_odds
        out = import_legacy_odds.run(legacy / "cache" / "live_odds", db=target)
        return f"{out.snapshots:,} snapshots · {out.pair_rows:,} pairs"

    def _bets():
        from hkrd.jobs import import_bets
        out = import_bets.run(legacy / "reports" / "user_bets_log.jsonl", db=target)
        return f"{out.bets:,} bets · {out.selections:,} selections"

    def _blackbook():
        from hkrd.jobs import import_blackbook
        out = import_blackbook.run(legacy / "blackbook.json", db=target)
        return f"{out.entries:,} entries · {out.tags:,} tag links"

    def _derive():
        from hkrd.jobs import derive_all
        out = derive_all.run(target)
        written = " · ".join(f"{v:,} {k.replace('runner_', '')}"
                             for k, v in out.written.items())
        # A handful of old races have unreadable sectionals. That is a property
        # of the source, so it is counted and named in the derive job's own
        # output — not treated here as a failed bootstrap.
        if out.errors:
            written += f"  ({len(out.errors)} races with bad sectionals)"
        return written

    step("migrate", legacy / "hkjc.db", _migrate)
    step("reports", legacy / "reports", _reports)
    step("odds", legacy / "cache" / "live_odds", _odds)
    step("bets", legacy / "reports" / "user_bets_log.jsonl", _bets)
    step("blackbook", legacy / "blackbook.json", _blackbook)
    if not skip_derive:
        step("derive", None, _derive)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--legacy", type=Path, default=Path("../hk_race_dashboard"),
                    help="the old repo, which holds hkjc.db and reports/")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--skip-derive", action="store_true",
                    help="import only; leave pace/ET/SARR/tags alone")
    a = ap.parse_args(argv)

    legacy = a.legacy.expanduser().resolve()
    if not legacy.is_dir():
        print(f"legacy repo not found: {legacy}")
        print("pass --legacy /path/to/hk_race_dashboard")
        return 1

    target = (a.db or db_path()).resolve()
    print(f"legacy   {legacy}")
    print(f"database {target}\n")
    started = time.perf_counter()
    report = run(legacy, db=a.db, skip_derive=a.skip_derive)
    print(report.render())
    print(f"\n  total {time.perf_counter() - started:.1f}s")
    if not report.errors:
        print("\n  ready — start the dashboard with:  python -m hkrd.serve")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
