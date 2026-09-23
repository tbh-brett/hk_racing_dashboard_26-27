#!/usr/bin/env python3
"""Send tips payloads to the dashboard.

    python tools/push_tips.py out/2026-09-23.tips.json
    python tools/push_tips.py --upcoming          # every out/*.tips.json from today

Each file is one meeting, and the dashboard treats it as the whole answer for
the sources it names: whatever those sources said about that meeting before
and no longer say is taken off the card. Sending the same file twice changes
nothing.

Signs in with HKRD_PASSWORD from the environment or the repo's `.env` — never
a flag, never printed. HKRD_BASE, or --base, says which dashboard.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _dashboard as dash                                  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HK = dt.timezone(dt.timedelta(hours=8))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--upcoming", action="store_true",
                    help="every out/<date>.tips.json dated today or later")
    ap.add_argument("--out", type=Path, default=REPO / "out")
    ap.add_argument("--base", help=f"dashboard URL (default {dash.DEFAULT_BASE}"
                                    f" or HKRD_BASE)")
    a = ap.parse_args(argv)

    files = list(a.files)
    if a.upcoming:
        today = dt.datetime.now(HK).date().isoformat()
        files += sorted(p for p in a.out.glob("*.tips.json")
                        if p.name[:10] >= today)
    if not files:
        print("  nothing to send")
        return 0

    base = dash.base_url(a.base)
    failed = 0
    try:
        session = dash.connect(base)
    except dash.DashboardError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1
    for path in files:
        try:
            got = dash.post(session, base, "/api/tips/import",
                            json.loads(path.read_text(encoding="utf-8")))
        except dash.DashboardError as exc:
            failed += 1
            print(f"  {path.name}: {exc}", file=sys.stderr)
            continue
        reasons = ", ".join(f"{k} {v}" for k, v in
                            got["quarantine_reasons"].items()) or "none"
        print(f"  {path.name} -> {base}\n"
              f"    quotes {got['quotes']} ({got['unplaced_quotes']} on no "
              f"runner) · picks {got['selections']} · quarantined "
              f"{got['quarantined']} ({reasons}) · removed {got['removed']}"
              + (f" · prices {got['prices']} ({len(got['prices_skipped'])} "
                 f"not stored)" if got.get("prices") or
                 got.get("prices_skipped") else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
